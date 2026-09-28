import re


KEYWORDS = {
    "Fire": [
        "fire", "burn", "smoke", "آگ", "اتش", "آتش", "ہور",
        "لگی", "لگے", "لګې", "سوځ", "دود",
    ],
    "Medical": ["heart", "injury", "blood", "hospital", "زړه", "وینه"],
    "Accident": ["accident", "crash", "vehicle", "حادثہ", "حادثے", "ټکر", "تصادم"],
    "Police": ["robbery", "gun", "fight", "crime", "غلا", "ټوپک"],
}


def classify_emergency(text: str, original_text: str = ""):
    combined_text = f"{text} {original_text}".lower()
    text_words = set(re.findall(r"[\w\u0600-\u06ff]+", combined_text))

    for category, keywords in KEYWORDS.items():
        if text_words & set(keywords):
            return category

    return "Unknown"