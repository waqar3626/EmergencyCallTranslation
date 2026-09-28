from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import json
import asyncio

from app.services.live_speech_service import LiveTranscriptionSession, decode_live_chunk

router = APIRouter()

SUPPORTED_SOURCE_LANGUAGES = {"Auto", "Urdu", "Pashto", "Punjabi", "English"}


async def _receive_audio(websocket: WebSocket, queue: asyncio.Queue):
    try:
        while True:
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                break

            if data.get("text"):
                try:
                    msg = json.loads(data["text"])
                except json.JSONDecodeError:
                    print(f"Invalid JSON in text message: {data['text']}")
                    continue
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            audio_chunk = data.get("bytes")
            if not audio_chunk:
                continue
            try:
                audio = await asyncio.to_thread(decode_live_chunk, audio_chunk)
            except Exception as error:
                print(f"Could not decode audio chunk ({len(audio_chunk)} bytes): {error}")
                continue
            if len(audio):
                await queue.put(audio)
    except WebSocketDisconnect:
        pass
    finally:
        await queue.put(None)


async def _process_audio(websocket: WebSocket, queue: asyncio.Queue, session: LiveTranscriptionSession):
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        chunks = [chunk]
        # If transcription is slower than real time, clips pile up. Merge them
        # into one pass instead of falling further behind.
        finished = False
        while not queue.empty():
            queued = queue.get_nowait()
            if queued is None:
                finished = True
                break
            chunks.append(queued)
        if len(chunks) > 1:
            print(f"Merging {len(chunks)} queued audio clips")

        try:
            result = await asyncio.to_thread(session.process_audio, chunks)
            if result is not None:
                await websocket.send_json(result)
        except Exception as error:
            print(f"Error processing audio: {error}")

        if finished:
            return


@router.websocket("/ws/live-transcription")
async def live_transcription(websocket: WebSocket):
    await websocket.accept()

    source_language = websocket.query_params.get("language", "Auto")
    if source_language not in SUPPORTED_SOURCE_LANGUAGES:
        source_language = "Auto"
    print(f"Live transcription started (source language: {source_language})")

    session = LiveTranscriptionSession(source_language)
    queue = asyncio.Queue()

    try:
        await asyncio.gather(
            _receive_audio(websocket, queue),
            _process_audio(websocket, queue, session),
        )
    except Exception as error:
        print(f"WebSocket error: {error}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        print("WebSocket connection closed")
