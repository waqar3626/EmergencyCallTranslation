"""Speech-recognition engines used by speech_service.

Whisper cannot recognise Pashto (published zero-shot word error rates are
above 100%, and it writes Urdu or Arabic script), so Pashto uses a model
fine-tuned on Pashto speech. It is a CTC model: it labels the audio frame by
frame instead of generating text word by word, so it is fast on a CPU and
cannot fall into Whisper's repetition loops.
"""
import gc
import os
import threading

import numpy as np

PASHTO_MODEL = os.getenv("PASHTO_ASR_MODEL", "ihanif/pashto-asr-v3")
SAMPLE_RATE = 16000
# Threads for model inference: one per physical core. Using hyper-threads as
# well made translation 3x slower on the test laptop (12.3 s vs 3.7 s).
CPU_THREADS = int(os.getenv("CPU_THREADS", "0")) or max(1, (os.cpu_count() or 2) // 2)
# Long recordings are recognised in windows of this length (seconds).
WINDOW_SECONDS = 20


class PashtoCTC:
    """Pashto speech recognition with a fine-tuned w2v-BERT 2.0 CTC model."""

    def __init__(self, model_name=PASHTO_MODEL):
        import torch
        from transformers import AutoProcessor, Wav2Vec2BertForCTC

        self._torch = torch
        torch.set_num_threads(CPU_THREADS)
        self.processor = AutoProcessor.from_pretrained(model_name)
        model = Wav2Vec2BertForCTC.from_pretrained(model_name)
        model.eval()
        # int8 weights for the linear layers: about twice as fast on a CPU and
        # a quarter of the memory. Free the float32 original straight away.
        self.model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        del model
        gc.collect()
        self.lock = threading.Lock()

    def transcribe(self, audio):
        """audio: float32 numpy array at 16 kHz. Returns Pashto text."""
        if audio is None or len(audio) < SAMPLE_RATE // 4:
            return ""
        window = WINDOW_SECONDS * SAMPLE_RATE
        parts = []
        with self.lock, self._torch.inference_mode():
            for start in range(0, len(audio), window):
                chunk = np.asarray(audio[start:start + window], dtype=np.float32)
                if len(chunk) < SAMPLE_RATE // 4:
                    continue
                inputs = self.processor(chunk, sampling_rate=SAMPLE_RATE, return_tensors="pt")
                logits = self.model(**inputs).logits
                ids = self._torch.argmax(logits, dim=-1)
                parts.append(self.processor.batch_decode(ids)[0].strip())
        return " ".join(p for p in parts if p)

    def transcribe_file(self, path):
        from faster_whisper.audio import decode_audio
        return self.transcribe(decode_audio(path, sampling_rate=SAMPLE_RATE))
