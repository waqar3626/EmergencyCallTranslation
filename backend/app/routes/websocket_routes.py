"""Live translation over WebSocket.

Client -> server
  binary: 16 kHz mono 16-bit little-endian PCM, any frame size (e.g. 100 ms)
  text:   {"type": "stop"}  finish the current utterance
          {"type": "ping"}  keep-alive, answered with {"type": "pong"}
Server -> client (JSON)
  {"type": "status",  "state": "listening" | "speaking" | "processing"}
  {"type": "partial", "id", "text", "translation"?, "detected_language"}
  {"type": "final",   "id", "text", "translation", "detected_language", "emergency_type"}
  {"type": "error",   "message"}
"""
import asyncio
import json

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.live_speech_service import LiveSession

router = APIRouter()

SUPPORTED_SOURCE_LANGUAGES = {"Auto", "Urdu", "Pashto", "Punjabi", "English"}


@router.websocket("/ws/live-transcription")
async def live_transcription(websocket: WebSocket):
    await websocket.accept()
    source_language = websocket.query_params.get("language", "Auto")
    if source_language not in SUPPORTED_SOURCE_LANGUAGES:
        source_language = "Auto"
    print(f"Live session started (source language: {source_language})", flush=True)

    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue = asyncio.Queue()

    def send(message):
        # Called from the session's worker thread.
        loop.call_soon_threadsafe(outbox.put_nowait, message)

    session = LiveSession(source_language, send)

    async def sender():
        while True:
            message = await outbox.get()
            if message is None:
                return
            await websocket.send_json(message)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                break
            if data.get("bytes"):
                pcm = np.frombuffer(data["bytes"], dtype="<i2").astype(np.float32) / 32768.0
                session.feed(pcm)
            elif data.get("text"):
                try:
                    message = json.loads(data["text"])
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "ping":
                    send({"type": "pong"})
                elif message.get("type") == "stop":
                    session.flush()
    except WebSocketDisconnect:
        pass
    except Exception as error:
        print(f"WebSocket error: {error!r}", flush=True)
    finally:
        session.close()
        outbox.put_nowait(None)
        try:
            await asyncio.wait_for(sender_task, timeout=2)
        except Exception:
            sender_task.cancel()
        print("Live session closed", flush=True)
