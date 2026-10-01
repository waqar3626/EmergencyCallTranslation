"""Streaming live translation.

The browser streams raw 16 kHz mono PCM. A voice activity detector (Silero)
splits the stream into utterances:

  listening --speech starts--> speaking --0.7 s of silence--> final result
                                   |
                                   +-- every ~1.2 s: partial result (fast, greedy)

While the caller speaks, partial transcripts (and, when the CPU has time, a
quick translation) are sent so text appears as they talk. When they pause, the
utterance is transcribed again, translated with full quality and added to the
call. During silence nothing is processed, so the session simply waits.

All model work runs on one worker thread per call; final results always take
priority and stale partial results are dropped, so the delay cannot build up.
"""
import threading
import time
from collections import deque

import numpy as np
from faster_whisper.vad import get_vad_model

from app.services.classification_service import classify_emergency
from app.services.language_service import detect_language
from app.services.speech_service import SAMPLE_RATE, detect_language_code, language_code_for, transcribe
from app.services.translation_service import translate_text

FRAME = 512                                   # Silero VAD frame: 32 ms
FRAME_SECONDS = FRAME / SAMPLE_RATE
START_PROBABILITY = 0.5
END_PROBABILITY = 0.35
START_FRAMES = 3                              # ~0.1 s of speech starts an utterance
END_SILENCE_SECONDS = 0.7                     # pause that ends an utterance
PREROLL_FRAMES = 10                           # keep ~0.3 s before speech starts
MAX_UTTERANCE_SECONDS = 15.0
PARTIAL_INTERVAL_SECONDS = 1.2
PARTIAL_TRANSLATION_INTERVAL_SECONDS = 4.0
MIN_PARTIAL_SECONDS = 0.8
VAD_HISTORY_FRAMES = 16                       # context given to the VAD per call

LANGUAGE_NAMES = {"ur": "Urdu", "ps": "Pashto", "pa": "Punjabi", "en": "English"}

_vad_model = None
_vad_load_lock = threading.Lock()   # loading the model
_vad_lock = threading.Lock()        # running it (one call at a time)


def _vad():
    global _vad_model
    with _vad_load_lock:
        if _vad_model is None:
            _vad_model = get_vad_model()
        return _vad_model


class LiveSession:
    def __init__(self, source_language, send):
        """send(message: dict) is called from the worker thread; it must be thread-safe."""
        self.send = send
        self.language_code = language_code_for(source_language)
        self.segments = []                    # finished utterances: (original, translation)
        self.emergency_type = "Unknown"

        self._pending = np.zeros(0, dtype=np.float32)
        self._history = deque(maxlen=VAD_HISTORY_FRAMES)
        self._preroll = deque(maxlen=PREROLL_FRAMES)
        self._utterance = []
        self._speaking = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._utterance_id = 0
        self._last_partial = 0.0
        self._last_partial_translation = 0.0
        self._state = None

        self._jobs = deque()                  # final jobs, in order
        self._partial = None                  # latest partial job only
        self._finished = set()
        self._cond = threading.Condition()
        self._closed = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self._set_state("listening")

    # ------------------------------------------------------------ audio input
    def feed(self, pcm: np.ndarray):
        """pcm: float32 samples in [-1, 1] at 16 kHz."""
        self._pending = np.concatenate([self._pending, pcm.astype(np.float32)])
        count = len(self._pending) // FRAME
        if count == 0:
            return
        frames = self._pending[:count * FRAME].reshape(count, FRAME)
        self._pending = self._pending[count * FRAME:]

        # Run the VAD on the new frames plus some history for context.
        history = list(self._history)
        batch = np.concatenate(history + [frames.reshape(-1)]) if history else frames.reshape(-1)
        model = _vad()
        with _vad_lock:
            probs = model(batch).reshape(-1)[-count:]
        for frame, prob in zip(frames, probs):
            self._history.append(frame)
            self._on_frame(frame, float(prob))

        if self._speaking:
            seconds = len(self._utterance) * FRAME_SECONDS
            now = time.monotonic()
            if seconds >= MIN_PARTIAL_SECONDS and now - self._last_partial >= PARTIAL_INTERVAL_SECONDS:
                self._last_partial = now
                self._submit_partial()

    def _on_frame(self, frame, prob):
        if not self._speaking:
            self._preroll.append(frame)
            self._speech_frames = self._speech_frames + 1 if prob >= START_PROBABILITY else 0
            if self._speech_frames >= START_FRAMES:
                self._speaking = True
                self._silence_frames = 0
                self._utterance = list(self._preroll)
                self._preroll.clear()
                self._utterance_id += 1
                self._last_partial = time.monotonic()
                self._set_state("speaking")
            return

        self._utterance.append(frame)
        self._silence_frames = self._silence_frames + 1 if prob < END_PROBABILITY else 0
        if self._silence_frames * FRAME_SECONDS >= END_SILENCE_SECONDS:
            # Keep a little of the trailing silence, drop the rest.
            keep = len(self._utterance) - self._silence_frames + 5
            self._finish_utterance(self._utterance[:max(keep, 1)])
        elif len(self._utterance) * FRAME_SECONDS >= MAX_UTTERANCE_SECONDS:
            self._finish_utterance(self._utterance)
            # Long speech without a pause: carry on as a new utterance.
            self._speaking = True
            self._utterance = []
            self._utterance_id += 1
            self._last_partial = time.monotonic()

    def _finish_utterance(self, frames):
        audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
        self._speaking = False
        self._speech_frames = 0
        self._utterance = []
        with self._cond:
            self._jobs.append((self._utterance_id, audio))
            self._partial = None
            self._cond.notify()
        self._set_state("processing")

    def _submit_partial(self):
        audio = np.concatenate(self._utterance)
        with self._cond:
            self._partial = (self._utterance_id, audio)
            self._cond.notify()

    def flush(self):
        """The caller pressed stop: finish what was being said."""
        if self._speaking and len(self._utterance) * FRAME_SECONDS >= 0.4:
            self._finish_utterance(self._utterance)
        self._speaking = False

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify()

    # ------------------------------------------------------------ worker
    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.send({"type": "status", "state": state})

    def _language(self, audio):
        if self.language_code is None and len(audio) >= SAMPLE_RATE:
            # Detect once, on the first utterance long enough, then keep it.
            self.language_code = detect_language_code(audio)
        return self.language_code

    def _run(self):
        while True:
            with self._cond:
                while not self._closed and not self._jobs and self._partial is None:
                    self._cond.wait()
                if self._closed and not self._jobs:
                    return
                if self._jobs:
                    kind, (utterance_id, audio) = "final", self._jobs.popleft()
                else:
                    kind, (utterance_id, audio) = "partial", self._partial
                    self._partial = None
            try:
                if kind == "final":
                    self._final(utterance_id, audio)
                elif utterance_id not in self._finished:
                    self._partial_result(utterance_id, audio)
            except Exception as error:
                print(f"Live processing error: {error!r}", flush=True)
                self.send({"type": "error", "message": "Could not process part of the audio."})
            if not self._jobs and not self._speaking and self._partial is None:
                self._set_state("listening")

    def _partial_result(self, utterance_id, audio):
        language = self._language(audio)
        text, language = transcribe(audio, language, live=True, partial=True)
        if not text or utterance_id in self._finished:
            return
        message = {"type": "partial", "id": utterance_id, "text": text,
                   "detected_language": LANGUAGE_NAMES.get(language, "Unknown")}
        self.send(message)
        # A quick translation of the partial text, at most every few seconds and
        # only if no finished sentence is waiting (those always come first).
        with self._cond:
            busy = bool(self._jobs)
        now = time.monotonic()
        if (not busy and language != "en" and len(text.split()) >= 3
                and now - self._last_partial_translation >= PARTIAL_TRANSLATION_INTERVAL_SECONDS):
            self._last_partial_translation = now
            translation = translate_text(text, LANGUAGE_NAMES[language], fast=True)
            if utterance_id not in self._finished:
                self.send({**message, "translation": translation})

    def _final(self, utterance_id, audio):
        self._finished.add(utterance_id)
        started = time.monotonic()
        language = self._language(audio)
        text, language = transcribe(audio, language, live=True) if len(audio) >= SAMPLE_RATE // 3 else ("", language)
        recognised = time.monotonic()
        if not text:
            self.send({"type": "final", "id": utterance_id, "text": "", "translation": ""})
            return
        name = detect_language(text, language)
        translation = translate_text(text, name, live=True)
        print(f"Live utterance {utterance_id}: {len(audio) / SAMPLE_RATE:.1f}s audio, "
              f"recognition {recognised - started:.1f}s, translation {time.monotonic() - recognised:.1f}s", flush=True)
        self.segments.append((text, translation))
        all_original = " ".join(s[0] for s in self.segments)
        all_translation = " ".join(s[1] for s in self.segments)
        self.emergency_type = classify_emergency(all_translation, all_original)
        print(f"Live utterance {utterance_id} ({language}): {text} -> {translation}", flush=True)
        self.send({"type": "final", "id": utterance_id, "text": text, "translation": translation,
                   "detected_language": name, "emergency_type": self.emergency_type})
