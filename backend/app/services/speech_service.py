"""Speech-to-text: language detection and per-language recognition models.

Each language uses the most accurate model that runs on a CPU:
  * Urdu    - Whisper large-v3-turbo fine-tuned on Urdu (uploads and recordings)
  * Pashto  - w2v-BERT 2.0 fine-tuned on Pashto (Whisper cannot recognise Pashto)
  * Punjabi, English - the general Whisper model
Live translation uses a smaller, faster Whisper for Urdu, Punjabi and English.
All model names can be changed with environment variables.
"""
import os
import threading

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from app.services.asr_engines import CPU_THREADS


SAMPLE_RATE = 16000
GENERAL_MODEL = os.getenv("WHISPER_MODEL", "medium")                 # Punjabi, English
URDU_MODEL = os.getenv("URDU_ASR_MODEL", "kingabzpro/whisper-large-v3-urdu-ct2")
LIVE_MODEL = os.getenv("LIVE_WHISPER_MODEL", "small")               # live Urdu/Punjabi/English
# Partial (interim) text while the caller is still talking: speed over accuracy.
LIVE_PARTIAL_MODEL = os.getenv("LIVE_PARTIAL_MODEL", "base")
LID_MODEL = os.getenv("LID_WHISPER_MODEL", LIVE_MODEL)              # language detection

# UI language name -> Whisper language code
LANGUAGE_CODES = {
    "Urdu": "ur",
    "Pashto": "ps",
    "Punjabi": "pa",
    "English": "en",
}

# Whisper's language scores are summed over related languages: spoken Urdu is
# usually labelled Hindi, and Pashto is often labelled Persian, Arabic or even
# Marathi. On FLEURS, Urdu always scored >= 0.75 for the Urdu group, while
# Pashto never scored more than 0.6 for it.
LANGUAGE_GROUPS = {
    "ur": ("ur", "hi", "sd"),
    "ps": ("ps", "fa", "ar"),
    "pa": ("pa",),
    "en": ("en",),
}
URDU_THRESHOLD = 0.7
CONFIDENT = 0.5

_models = {}
_models_lock = threading.Lock()


def _whisper(name):
    with _models_lock:
        if name not in _models:
            print(f"Loading Whisper model: {name}", flush=True)
            _models[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=CPU_THREADS)
        return _models[name]


def _pashto():
    with _models_lock:
        if "pashto" not in _models:
            from app.services.asr_engines import PashtoCTC
            print("Loading Pashto speech model", flush=True)
            _models["pashto"] = PashtoCTC()
        return _models["pashto"]


def preload(live=False):
    """Load the models up front so the first request is not slow."""
    _whisper(LID_MODEL)
    _pashto()
    if live:
        _whisper(LIVE_MODEL)
        _whisper(LIVE_PARTIAL_MODEL)
    else:
        _whisper(URDU_MODEL)


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


def detect_language_code(audio):
    """Spoken language of the audio: 'ur', 'ps', 'pa' or 'en'."""
    _, info = _whisper(LID_MODEL).transcribe(
        audio, vad_filter=True, vad_parameters={"min_speech_duration_ms": 250})
    probabilities = dict(info.all_language_probs or [])
    scores = {code: sum(probabilities.get(m, 0.0) for m in members)
              for code, members in LANGUAGE_GROUPS.items()}
    if scores["en"] >= CONFIDENT:
        best = "en"
    elif scores["ur"] >= URDU_THRESHOLD:
        best = "ur"
    elif scores["pa"] >= CONFIDENT:
        best = "pa"
    else:
        best = "ps"
    print(f"Whisper detected '{info.language}', using '{best}' "
          f"(scores: { {k: round(v, 3) for k, v in scores.items()} })", flush=True)
    return best


def _whisper_transcribe(model, audio, language_code, beam_size):
    segments, _ = model.transcribe(
        audio,
        language=language_code,
        vad_filter=True,
        vad_parameters={"min_speech_duration_ms": 250, "min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        # One retry with sampling when greedy decoding loops or is unsure.
        temperature=[0.0, 0.3],
        beam_size=beam_size,
        without_timestamps=True,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return _collapse_repetitions(text)


def transcribe(audio, language_code=None, live=False, partial=False):
    """Recognise speech. audio: path or 16 kHz float32 array.

    Returns (text, language_code). live=True uses the faster live models;
    partial=True (interim live text) uses the fastest one.
    """
    if isinstance(audio, str):
        audio = decode_audio_file(audio)
    if audio is None or len(audio) < SAMPLE_RATE // 4:
        return "", language_code
    audio = np.asarray(audio, dtype=np.float32)

    if language_code is None:
        language_code = detect_language_code(audio)

    if language_code == "ps":
        return _pashto().transcribe(audio), language_code
    if live:
        model = LIVE_PARTIAL_MODEL if partial else LIVE_MODEL
        return _whisper_transcribe(_whisper(model), audio, language_code, beam_size=1), language_code
    model = URDU_MODEL if language_code == "ur" else GENERAL_MODEL
    return _whisper_transcribe(_whisper(model), audio, language_code, beam_size=5), language_code


def transcribe_audio(audio_path: str, language_code: str = None):
    return transcribe(audio_path, language_code)


def speech_to_text(audio_path: str):
    text, _ = transcribe_audio(audio_path)
    return text
