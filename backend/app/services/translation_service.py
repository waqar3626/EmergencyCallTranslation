import json
import os
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# Translation runs locally with Meta's NLLB-200 (1.3B, int8) through
# CTranslate2: no rate limits, no internet needed, and call text stays on the
# server. The free online services are only a fallback if NLLB is unavailable.
NLLB_MODEL = os.getenv("NLLB_MODEL", "OpenNMT/nllb-200-distilled-1.3B-ct2-int8")
USE_ONLINE_FALLBACK = os.getenv("ONLINE_TRANSLATION_FALLBACK", "1") == "1"

UNAVAILABLE_PREFIX = "[Translation unavailable] "

# Keep each request small enough for URL length and provider limits.
MAX_PIECE_CHARS = 400

NON_LATIN_LETTERS = re.compile(r"[؀-ۿݐ-ݿऀ-ॿ਀-੿]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")
GURMUKHI = re.compile(r"[਀-੿]")
SENTENCE_END = re.compile(r"(?<=[۔؟?!.،,\n])\s+")

# Successful translations only, so failed pieces are retried later.
_translation_cache = {}
MAX_CACHE_ENTRIES = 2048
_nllb = None
_nllb_failed_at = 0.0
_nllb_lock = threading.Lock()
# After Google answers "429 Too Many Requests" it is skipped for a while.
_google_blocked_until = 0.0
GOOGLE_BACKOFF_SECONDS = 600


def _nllb_code(source_language: str, text: str):
    if source_language == "Urdu":
        return "urd_Arab"
    if source_language == "Pashto":
        return "pbt_Arab"
    if source_language == "Punjabi":
        # Pakistani Punjabi is written in Shahmukhi (Arabic script).
        return "pan_Guru" if GURMUKHI.search(text) else "pnb_Arab"
    return None


def _online_code(source_language: str, text: str):
    if source_language == "Urdu":
        return "ur"
    if source_language == "Pashto":
        return "ps"
    if source_language == "Punjabi":
        # Plain "pa" makes the providers expect Gurmukhi and they only transliterate.
        return "pa" if GURMUKHI.search(text) else "pa-Arab"
    return None


def _is_real_translation(source: str, translated: str):
    if not translated or not translated.strip():
        return False
    if translated.strip() == source.strip():
        return False
    if "MYMEMORY WARNING" in translated or "QUERY LENGTH LIMIT" in translated:
        return False
    # An English result should be mostly Latin letters.
    non_latin = len(NON_LATIN_LETTERS.findall(translated))
    latin = len(LATIN_LETTERS.findall(translated))
    return latin > 0 and non_latin <= latin * 0.2


# ---------------------------------------------------------------- NLLB (local)
def _load_nllb():
    global _nllb, _nllb_failed_at
    with _nllb_lock:
        if _nllb is not None:
            return _nllb
        # Retry a failed load (e.g. no internet for the first download) later.
        if time.time() - _nllb_failed_at < 300:
            return None
        try:
            import ctranslate2
            from huggingface_hub import snapshot_download
            from transformers import AutoTokenizer

            print(f"Loading translation model: {NLLB_MODEL}", flush=True)
            path = NLLB_MODEL if os.path.isdir(NLLB_MODEL) else snapshot_download(NLLB_MODEL)
            from app.services.asr_engines import CPU_THREADS
            translator = ctranslate2.Translator(path, device="cpu", compute_type="int8",
                                                inter_threads=1, intra_threads=CPU_THREADS)
            tokenizer = AutoTokenizer.from_pretrained(path)
            _nllb = (translator, tokenizer, threading.Lock())
            print("Translation model loaded", flush=True)
        except Exception as error:
            print(f"Translation model load failed: {error}", flush=True)
            _nllb_failed_at = time.time()
        return _nllb


def _nllb_translate(text: str, source_language: str, beam_size: int):
    loaded = _load_nllb()
    code = _nllb_code(source_language, text)
    if loaded is None or code is None:
        return None
    translator, tokenizer, lock = loaded
    with lock:
        tokenizer.src_lang = code
        tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(text))
        result = translator.translate_batch(
            [tokens], target_prefix=[["eng_Latn"]], beam_size=beam_size,
            max_decoding_length=256, repetition_penalty=1.1)
        output = result[0].hypotheses[0][1:]          # drop the language token
        return tokenizer.decode(tokenizer.convert_tokens_to_ids(output), skip_special_tokens=True)


def preload():
    _load_nllb()


# ---------------------------------------------------------------- online fallback
def _fetch_json(url: str):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _google_translate(text: str, source: str):
    global _google_blocked_until
    if time.time() < _google_blocked_until:
        return None
    query = urlencode({"client": "gtx", "sl": source, "tl": "en", "dt": "t", "q": text})
    try:
        payload = _fetch_json(f"https://translate.googleapis.com/translate_a/single?{query}")
    except HTTPError as error:
        if error.code == 429:
            _google_blocked_until = time.time() + GOOGLE_BACKOFF_SECONDS
        raise
    return "".join(part[0] for part in payload[0] if part and part[0]).strip()


def _mymemory_translate(text: str, source: str):
    source = source.split("-")[0]
    query = urlencode({"q": text, "langpair": f"{source}|en"})
    payload = _fetch_json(f"https://api.mymemory.translated.net/get?{query}")
    return payload["responseData"]["translatedText"].strip()


# ---------------------------------------------------------------- pipeline
def _split_text(text: str):
    """Group sentences into pieces of at most MAX_PIECE_CHARS characters."""
    pieces = []
    current = ""
    for sentence in SENTENCE_END.split(text.strip()):
        while len(sentence) > MAX_PIECE_CHARS:
            cut = sentence.rfind(" ", 0, MAX_PIECE_CHARS)
            cut = cut if cut > 0 else MAX_PIECE_CHARS
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + len(sentence) + 1 > MAX_PIECE_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


# Beam search width: wider is slightly better but slower on a CPU.
BEAM_SIZES = {"full": 4, "live": 2, "fast": 1}


def _translate_piece(piece: str, source_language: str, quality: str):
    fast = quality == "fast"
    cache_key = (piece, source_language)
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    providers = [("NLLB", lambda: _nllb_translate(piece, source_language, BEAM_SIZES[quality]))]
    if USE_ONLINE_FALLBACK and not fast:
        source = _online_code(source_language, piece)
        providers += [
            ("Google", lambda: _google_translate(piece, source)),
            ("MyMemory", lambda: _mymemory_translate(piece, source)),
        ]
    for name, provider in providers:
        try:
            translated = provider()
            if _is_real_translation(piece, translated):
                if not fast:   # cache only full-quality translations
                    if len(_translation_cache) >= MAX_CACHE_ENTRIES:
                        _translation_cache.clear()
                    _translation_cache[cache_key] = translated.strip()
                return translated.strip()
            if translated is not None:
                print(f"{name} returned no usable translation", flush=True)
        except Exception as error:
            print(f"{name} translation failed: {error}", flush=True)
    return None


def translate_text(text: str, source_language: str, fast: bool = False, live: bool = False):
    """Translate to English.

    fast=True (live partial text) uses greedy decoding; live=True (finished
    live sentences) a narrower beam; otherwise full quality (uploads).
    """
    if not text or not text.strip() or source_language == "English":
        return text
    if source_language not in {"Urdu", "Pashto", "Punjabi"}:
        return text

    quality = "fast" if fast else ("live" if live else "full")
    translated_pieces = []
    for piece in _split_text(text):
        translated = _translate_piece(piece, source_language, quality)
        if translated is None:
            # Never invent a translation for an emergency call; show the
            # original so the operator knows this part is untranslated.
            translated = f"{UNAVAILABLE_PREFIX}{piece}"
        translated_pieces.append(translated)
    return " ".join(translated_pieces)
