from langdetect import detect


LANGUAGE_MAP = {
    "ur": "Urdu",
    "en": "English",
    "pa": "Punjabi",
    "ps": "Pashto",
}

# Letters used in Pashto but not in Urdu (ټ ډ ړ ږ ښ ګ ځ څ ې ۍ ڼ).
PASHTO_LETTERS = set("ټډړږښګځڅېۍڼ")

PASHTO_LATIN_MARKERS = {
    "mong", "mung", "pakor", "peshawar", "peshawer", "kalbala",
    "daze", "daza", "ba", "ke", "kho", "sanga", "tsenga", "zama",
    "sta", "staso", "yama", "yar", "kor", "khoor", "ghware",
}


def detect_language(text: str, language_code: str = None):
    if not text or not text.strip():
        return "Unknown"

    if any(character in PASHTO_LETTERS for character in text):
        return "Pashto"

    if any("਀" <= character <= "੿" for character in text):
        return "Punjabi"

    if language_code in LANGUAGE_MAP:
        return LANGUAGE_MAP[language_code]

    if any("؀" <= character <= "ۿ" for character in text):
        # Short Arabic-script phrases are often too small for langdetect.
        return "Urdu"

    latin_words = {
        word.strip(".,!?'-").lower()
        for word in text.split()
    }
    if len(latin_words & PASHTO_LATIN_MARKERS) >= 2:
        return "Pashto"

    try:
        lang_code = detect(text)
        return LANGUAGE_MAP.get(lang_code, "Unknown")

    except Exception:
        return "Unknown"
