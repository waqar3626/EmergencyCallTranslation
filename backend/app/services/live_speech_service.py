import io

from app.services.speech_service import (
    concatenate_audio,
    decode_audio_file,
    language_code_for,
    transcribe_live_audio,
)
from app.services.language_service import detect_language
from app.services.translation_service import translate_text
from app.services.classification_service import classify_emergency


# Recent transcript passed to Whisper so words split across clips stay consistent.
CONTEXT_CHARS = 150


def decode_live_chunk(audio_bytes):
    """Decode one self-contained WebM clip from the browser into 16 kHz PCM."""
    return decode_audio_file(io.BytesIO(audio_bytes))


class LiveTranscriptionSession:
    """State for one live call: the language and the transcript so far.

    The full transcript is re-translated after every clip, so the translation
    has sentence context instead of being stitched from 5-second fragments.
    Earlier sentences come from the translation cache, so only the newest
    part is sent to the translation provider.
    """

    def __init__(self, source_language: str = "Auto"):
        self.language_code = language_code_for(source_language)
        self.language_locked = self.language_code is not None
        self.segments = []

    def result(self, segment_text=""):
        original_text = " ".join(self.segments)
        if not original_text:
            return {
                "original_text": "",
                "detected_language": "Unknown",
                "translated_text": "",
                "emergency_type": "Unknown",
                "segment_text": "",
            }
        detected_language = detect_language(original_text, self.language_code)
        translated_text = translate_text(original_text, detected_language)
        return {
            "original_text": original_text,
            "detected_language": detected_language,
            "translated_text": translated_text,
            "emergency_type": classify_emergency(translated_text, original_text),
            "segment_text": segment_text,
        }

    def process_audio(self, audio_chunks):
        audio = concatenate_audio(audio_chunks)
        context = " ".join(self.segments)[-CONTEXT_CHARS:]
        text, language_code = transcribe_live_audio(audio, self.language_code, context)
        print(f"Live segment ({language_code}): '{text}'", flush=True)

        if not text:
            return None

        if not self.language_locked:
            # Detecting per clip makes the language flip between clips; keep
            # the first detection for the rest of the call.
            self.language_code = language_code
            self.language_locked = True

        self.segments.append(text)
        return self.result(text)
