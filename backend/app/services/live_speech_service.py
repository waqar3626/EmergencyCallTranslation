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

In automatic mode the language is not fixed by the first utterance (a short
greeting such as "Assalam alaikum" sounds Arabic). It is re-detected over all
speech so far until the evidence is strong and long enough; if the decision
changes, the sentences already shown are recognised again and replaced.

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
from app.services.speech_service import SAMPLE_RATE, detect_language_scored, language_code_for, transcribe
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
LID_LOCK_SECONDS = 5.0                        # strong decision on this much speech: keep it
LID_FORCE_LOCK_SECONDS = 15.0                 # keep the decision after this much speech anyway
LID_WINDOW_SECONDS = 30.0                     # Whisper only looks at 30 s
MIN_PARTIAL_LID_SECONDS = 1.5
PARTIAL_RECHECK_SECONDS = 4.0                 # long utterance with a provisional language: check it once
MIN_LID_UTTERANCE_SECONDS = 1.0               # shorter bursts are mostly noise: ignore them for detection
MIN_FINAL_SECONDS = 0.5                       # shorter utterances are not transcribed

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
        self._language_fixed = self.language_code is not None   # chosen by the operator or detected reliably
        self._lid_audio = []                  # speech so far, while the language is still provisional
        self._provisional = {}                # utterance id -> (audio, language used), while provisional
        self._rechecked = set()               # utterances whose partial text triggered a language check
        self.segments = {}                    # utterance id -> (original, translation), in order
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

    def _partial_language(self, utterance_id, audio):
        """Language for partial text: the current (possibly provisional) guess,
        checked again once while a long utterance is still being spoken."""
        seconds = len(audio) / SAMPLE_RATE
        if self.language_code is None and seconds >= MIN_PARTIAL_LID_SECONDS:
            code, evidence = detect_language_scored(audio)
            if evidence != "none":
                self.language_code = code
        elif (not self._language_fixed and seconds >= PARTIAL_RECHECK_SECONDS
                and utterance_id not in self._rechecked):
            self._rechecked.add(utterance_id)
            speech = np.concatenate(self._lid_audio + [audio])[-int(LID_WINDOW_SECONDS * SAMPLE_RATE):]
            code, evidence = detect_language_scored(speech)
            if evidence == "strong":
                self.language_code = code       # still provisional; the final result decides
        return self.language_code

    def _update_language(self, audio):
        """Re-detect the language over all speech so far until the decision is reliable.
        The language may change while it is provisional."""
        if self._language_fixed or len(audio) < MIN_LID_UTTERANCE_SECONDS * SAMPLE_RATE:
            if self.language_code is None:
                self.language_code = "ur"       # provisional default until there is evidence
            return
        self._lid_audio.append(audio)
        speech = np.concatenate(self._lid_audio)
        seconds = len(speech) / SAMPLE_RATE
        code, evidence = detect_language_scored(speech[-int(LID_WINDOW_SECONDS * SAMPLE_RATE):])
        if evidence == "none":
            # Noise or unclear audio says nothing about the language: keep the current guess.
            if self.language_code is None:
                self.language_code = code
            return
        if (evidence == "strong" and seconds >= LID_LOCK_SECONDS) or seconds >= LID_FORCE_LOCK_SECONDS:
            self._language_fixed = True
            self._lid_audio = []
            print(f"Live language fixed: {code} after {seconds:.1f}s of speech", flush=True)
        self.language_code = code

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
        language = self._partial_language(utterance_id, audio)
        if language is None:
            return          # too little speech to guess the language yet
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
        self._update_language(audio)
        # Sentences shown with a different provisional language are recognised again.
        stale = [(i, a) for i, (a, used) in self._provisional.items() if used != self.language_code]
        if stale:
            print(f"Live language is now {self.language_code}; re-recognising {len(stale)} earlier utterance(s)",
                  flush=True)
            for earlier_id, earlier_audio in stale:
                self._provisional[earlier_id] = (earlier_audio, self.language_code)
                self._process(earlier_id, earlier_audio, replace=True)
        if self._language_fixed:
            self._provisional.clear()
        else:
            self._provisional[utterance_id] = (audio, self.language_code)
        self._process(utterance_id, audio)

    def _process(self, utterance_id, audio, replace=False):
        started = time.monotonic()
        language = self.language_code
        text, language = (transcribe(audio, language, live=True)
                          if len(audio) >= MIN_FINAL_SECONDS * SAMPLE_RATE else ("", language))
        if not any(len(word) >= 2 for word in text.split()):
            text = ""       # single letters from noise ("س", "ک") are not speech
        recognised = time.monotonic()
        if not text:
            self.segments.pop(utterance_id, None)
            self.send({"type": "final", "id": utterance_id, "text": "", "translation": "", "replace": replace})
            return
        name = detect_language(text, language)
        translation = translate_text(text, name, live=True)
        print(f"Live utterance {utterance_id}: {len(audio) / SAMPLE_RATE:.1f}s audio, "
              f"recognition {recognised - started:.1f}s, translation {time.monotonic() - recognised:.1f}s", flush=True)
        self.segments[utterance_id] = (text, translation)
        all_original = " ".join(s[0] for s in self.segments.values())
        all_translation = " ".join(s[1] for s in self.segments.values())
        self.emergency_type = classify_emergency(all_translation, all_original)
        print(f"Live utterance {utterance_id} ({language}): {text} -> {translation}", flush=True)
        self.send({"type": "final", "id": utterance_id, "text": text, "translation": translation,
                   "detected_language": name, "emergency_type": self.emergency_type, "replace": replace})
