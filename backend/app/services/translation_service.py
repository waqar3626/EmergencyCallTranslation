import json
import re
import sys
import threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from transformers import MarianMTModel, MarianTokenizer


# Offline fallback models, used only when every online provider fails.
OFFLINE_MODEL_NAMES = {
    "Urdu": "Helsinki-NLP/opus-mt-ur-en",
    "Punjabi": "Helsinki-NLP/opus-mt-pa-en",
}

UNAVAILABLE_PREFIX = "[Translation unavailable] "

# Keep each request small enough for URL length and provider limits.
MAX_PIECE_CHARS = 400

NON_LATIN_LETTERS = re.compile(r"[؀-ۿݐ-ݿऀ-ॿ਀-੿]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")
GURMUKHI = re.compile(r"[਀-੿]")
SENTENCE_END = re.compile(r"(?<=[۔؟?!.،,\n])\s+")

_offline_models = {}
# Successful translations only, so failed pieces are retried later.
_translation_cache = {}
MAX_CACHE_ENTRIES = 2048
_offline_lock = threading.Lock()


def _source_code(source_language: str, text: str):
    if source_language == "Urdu":
        return "ur"
    if source_language == "Pashto":
        return "ps"
    if source_language == "Punjabi":
        # Pakistani Punjabi is written in Shahmukhi (Arabic script); plain "pa"
        # makes the providers expect Gurmukhi and they only transliterate it.
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


def _fetch_json(url: str):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _google_translate(text: str, source: str):
    query = urlencode({"client": "gtx", "sl": source, "tl": "en", "dt": "t", "q": text})
    payload = _fetch_json(f"https://translate.googleapis.com/translate_a/single?{query}")
    return "".join(part[0] for part in payload[0] if part and part[0]).strip()


def _mymemory_translate(text: str, source: str):
    source = source.split("-")[0]
    query = urlencode({"q": text, "langpair": f"{source}|en"})
    payload = _fetch_json(f"https://api.mymemory.translated.net/get?{query}")
    return payload["responseData"]["translatedText"].strip()


def _split_text(text: str):
    """Group sentences into pieces of at most MAX_PIECE_CHARS characters.

    Grouping is greedy from the start, so when live text grows only the last
    piece changes and earlier pieces are served from the cache.
    """
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


def _load_offline_model(model_name: str):
    with _offline_lock:
        if model_name not in _offline_models:
            try:
                print(f"Loading offline translation model: {model_name}", flush=True)
                tokenizer = MarianTokenizer.from_pretrained(model_name)
                offline_model = MarianMTModel.from_pretrained(model_name)
                offline_model.eval()
                _offline_models[model_name] = (tokenizer, offline_model)
            except Exception as error:
                print(f"Offline translation model load failed: {error}", file=sys.stderr)
                # Retry on a later request instead of disabling it for good.
                return None
        return _offline_models[model_name]


def _offline_translate(text: str, source_language: str):
    model_name = OFFLINE_MODEL_NAMES.get(source_language)
    if model_name is None:
        return None
    loaded = _load_offline_model(model_name)
    if loaded is None:
        return None
    tokenizer, offline_model = loaded
    inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
    output = offline_model.generate(**inputs, num_beams=5, max_length=512)
    return tokenizer.decode(output[0], skip_special_tokens=True)


def _translate_piece(piece: str, source_language: str):
    cache_key = (piece, source_language)
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    source = _source_code(source_language, piece)
    providers = (
        ("Google", lambda: _google_translate(piece, source)),
        ("Google (auto-detect)", lambda: _google_translate(piece, "auto")),
        ("MyMemory", lambda: _mymemory_translate(piece, source)),
        ("offline model", lambda: _offline_translate(piece, source_language)),
    )
    for name, provider in providers:
        try:
            translated = provider()
            if _is_real_translation(piece, translated):
                print(f"Translated {source_language} with {name}", flush=True)
                if len(_translation_cache) >= MAX_CACHE_ENTRIES:
                    _translation_cache.clear()
                _translation_cache[cache_key] = translated.strip()
                return translated.strip()
            print(f"{name} returned no usable translation", flush=True)
        except Exception as error:
            print(f"{name} translation failed: {error}", flush=True)
    return None


def translate_text(text: str, source_language: str):
    if not text or not text.strip() or source_language == "English":
        return text
    if source_language not in {"Urdu", "Pashto", "Punjabi"}:
        return text

    translated_pieces = []
    for piece in _split_text(text):
        translated = _translate_piece(piece, source_language)
        if translated is None:
            # Never invent a translation for an emergency call; show the
            # original so the operator knows this part is untranslated.
            translated = f"{UNAVAILABLE_PREFIX}{piece}"
        translated_pieces.append(translated)
    return " ".join(translated_pieces)
