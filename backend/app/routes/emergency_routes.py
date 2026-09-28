import asyncio
import os

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.services.file_service import save_audio_file
from app.services.speech_service import language_code_for, transcribe_audio
from app.services.language_service import detect_language
from app.services.translation_service import translate_text
from app.services.classification_service import classify_emergency

router = APIRouter(
    prefix="/api/emergency",
    tags=["Emergency"]
)


def _process_file(file_path: str, source_language: str):
    # Telling Whisper the language up front gives far better transcripts than
    # letting it guess, especially for Pashto and for Urdu vs Hindi.
    original_text, language_code = transcribe_audio(file_path, language_code_for(source_language))
    print(f"[Emergency API] Speech to text result: {original_text}")

    detected_language = (
        source_language
        if language_code_for(source_language)
        else detect_language(original_text, language_code)
    )
    print(f"[Emergency API] Detected language: {detected_language}")

    translated_text = translate_text(original_text, detected_language)
    print(f"[Emergency API] Translated text: {translated_text}")

    emergency_type = classify_emergency(translated_text, original_text)
    print(f"[Emergency API] Emergency type: {emergency_type}")

    return {
        "original_text": original_text,
        "detected_language": detected_language if original_text else "Unknown",
        "translated_text": translated_text,
        "emergency_type": emergency_type
    }


@router.post("/process")
async def process_emergency(
    audio: UploadFile = File(...),
    source_language: str = Form("Auto"),
):

    print("[Emergency API] Received request to /api/emergency/process")

    file_path = await save_audio_file(audio)
    print(f"[Emergency API] Saved audio to: {file_path}")

    try:
        # Transcription takes seconds to minutes on CPU; run it in a worker
        # thread so live WebSocket sessions keep being served meanwhile.
        return await asyncio.to_thread(_process_file, file_path, source_language)
    except Exception as error:
        print(f"[Emergency API] Processing failed: {error}")
        raise HTTPException(status_code=422, detail="Could not process this audio file. Please check the format and try again.")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass
