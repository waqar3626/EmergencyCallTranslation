import os
import re
import threading

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


MODEL_NAME = os.getenv("WHISPER_MODEL", "medium")
LIVE_MODEL_NAME = os.getenv("LIVE_WHISPER_MODEL", "small")
SAMPLE_RATE = 16000

model = None
live_model = None
model_lock = threading.Lock()
live_model_lock = threading.Lock()

# UI language name -> Whisper language code
LANGUAGE_CODES = {
    "Urdu": "ur",
    "Pashto": "ps",
    "Punjabi": "pa",
    "English": "en",
}

# Whisper often confuses closely related languages. Spoken Urdu and Hindi are
# nearly identical (Hindi output is Devanagari, which then fails translation),
# and Pashto is frequently scored as Persian or Arabic. Fold those scores into
# the languages this platform supports.
LANGUAGE_GROUPS = {
    "ur": ("ur", "hi", "sd"),
    "ps": ("ps", "fa", "ar"),
    "pa": ("pa",),
    "en": ("en",),
}

# Short emergency call in each language, one sentence per emergency type.
# Whisper treats the prompt as preceding speech, which steers it to the right
# script and spelling of common emergency words. (A bare word list was tried
# and Whisper copied the list format into its output.)
LANGUAGE_PROMPTS = {
    "ur": "ہیلو، ایمرجنسی ہے۔ ہمارے گھر میں آگ لگ گئی ہے۔ یہاں حادثہ ہو گیا ہے، لوگ زخمی ہیں۔ ایمبولینس بھیجیں۔ ڈاکو آئے ہیں، پولیس بھیجیں۔ جلدی آ جائیں۔",
    "ps": "سلام، بیړنۍ پېښه ده. زموږ کور کې اور لګېدلی دی. دلته ټکر شوی دی، خلک ټپیان دي. امبولانس راولېږئ. غلو حمله کړې، پولیس راولېږئ. ژر راشئ.",
    "pa": "ہیلو، ایمرجنسی اے۔ ساڈے گھر اگ لگ گئی اے۔ ایتھے حادثہ ہو گیا اے، لوک زخمی نیں۔ ایمبولینس بھیجو۔ ڈاکو آ گئے نیں، پولیس بھیجو۔ چھیتی آؤ۔",
    "en": "Hello, this is an emergency. There is a fire in our house. There has been an accident and people are injured. Send an ambulance. Robbers are here, send the police. Please come quickly.",
}


def _load_model():
    global model
    if model is None:
        with model_lock:
            if model is None:
                print(f"Loading speech model: {MODEL_NAME}", flush=True)
                try:
                    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
                except Exception as error:
                    print(f"Speech model loading failed: {error}", flush=True)
                    raise
    return model


def _load_live_model():
    global live_model
    if live_model is None:
        with live_model_lock:
            if live_model is None:
                print(f"Loading live speech model: {LIVE_MODEL_NAME}", flush=True)
                try:
                    live_model = WhisperModel(LIVE_MODEL_NAME, device="cpu", compute_type="int8")
                except Exception as error:
                    print(f"Live speech model loading failed: {error}", flush=True)
                    raise
    return live_model


def language_code_for(language_name: str):
    return LANGUAGE_CODES.get(language_name)


def decode_audio_file(audio_path_or_file):
    return decode_audio(audio_path_or_file, sampling_rate=SAMPLE_RATE)


def _collapse_repetitions(text: str):
    """Remove Whisper's looping output, e.g. "help me help me help me ..."."""
    words = text.split()
    for size in range(1, 5):
        collapsed = []
        index = 0
        while index < len(words):
            phrase = words[index:index + size]
            repeats = 1
            while words[index + repeats * size:index + (repeats + 1) * size] == phrase:
                repeats += 1
            if repeats > 1:
                # Allow a phrase to be said twice ("jaldi jaldi"), drop the rest.
                collapsed.extend(phrase * 2)
                index += size * repeats
            else:
                # Advance one word so repeats starting mid-phrase are found.
                collapsed.append(words[index])
                index += 1
        words = collapsed
    return " ".join(words)


def _is_prompt_echo(text: str, prompt: str):
    # With little speech Whisper sometimes just reads the prompt back. Only
    # treat it as an echo when most of the prompt is repeated, so a caller who
    # says one of its sentences is still transcribed.
    normalize = lambda value: re.sub(r"[\W_]+", "", value)
    cleaned, prompt = normalize(text), normalize(prompt)
    return bool(cleaned) and len(cleaned) >= 0.6 * len(prompt) and cleaned in prompt


def _pick_language(whisper_model, audio):
    _, info = whisper_model.transcribe(
        audio,
        vad_filter=True,
        vad_parameters={"threshold": 0.5, "min_speech_duration_ms": 300},
    )
    probabilities = dict(info.all_language_probs or [])
    scores = {
        code: sum(probabilities.get(member, 0.0) for member in members)
        for code, members in LANGUAGE_GROUPS.items()
    }
    best = max(scores, key=scores.get)
    print(f"Whisper detected '{info.language}', using '{best}' (scores: {scores})", flush=True)
    return best


def _transcribe(whisper_model, audio, language_code=None, context="", beam_size=5):
    if isinstance(audio, str):
        audio = decode_audio_file(audio)
    if audio is None or len(audio) == 0:
        return "", language_code

    if language_code is None:
        language_code = _pick_language(whisper_model, audio)

    vocabulary = LANGUAGE_PROMPTS.get(language_code, "")
    prompt = f"{vocabulary} {context}".strip()

    segments, _ = whisper_model.transcribe(
        audio,
        language=language_code,
        initial_prompt=prompt or None,
        vad_filter=True,
        vad_parameters={"threshold": 0.5, "min_speech_duration_ms": 300},
        condition_on_previous_text=False,
        no_speech_threshold=0.45,
        # Retry with sampling when greedy decoding loops or is low confidence.
        temperature=[0.0, 0.2, 0.4],
        beam_size=beam_size,
    )

    text = " ".join(segment.text.strip() for segment in segments).strip()
    text = _collapse_repetitions(text)
    if _is_prompt_echo(text, vocabulary):
        print("Discarding transcription that only echoes the prompt", flush=True)
        return "", language_code
    return text, language_code


def transcribe_audio(audio_path: str, language_code: str = None):
    return _transcribe(_load_model(), audio_path, language_code, beam_size=5)


def transcribe_live_audio(audio, language_code: str = None, context: str = ""):
    return _transcribe(_load_live_model(), audio, language_code, context=context, beam_size=1)


def concatenate_audio(chunks):
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def speech_to_text(audio_path: str):
    text, _ = transcribe_audio(audio_path)
    return text
