from __future__ import annotations


def detect_language(text: str) -> str:
    """Detect language code for given text. Returns ISO 639-1 code or empty string."""
    if not text or not text.strip():
        return ""
    sample = text.strip()[:4000].replace("\n", " ")
    try:
        from fast_langdetect import detect

        result = detect(sample, low_memory=True)
        return result.get("lang", "")
    except Exception:
        return ""


# Curated list of common languages (code, display name) shown first in UI.
COMMON_LANGS: list[tuple[str, str]] = [
    ("en", "English"),
    ("ar", "Arabic"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("zh", "Chinese (Simplified)"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("tr", "Turkish"),
    ("pl", "Polish"),
    ("sv", "Swedish"),
    ("he", "Hebrew"),
    ("fa", "Persian"),
    ("ur", "Urdu"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("id", "Indonesian"),
    ("vi", "Vietnamese"),
    ("th", "Thai"),
    ("cs", "Czech"),
    ("el", "Greek"),
    ("ro", "Romanian"),
    ("hu", "Hungarian"),
    ("fi", "Finnish"),
    ("da", "Danish"),
    ("no", "Norwegian"),
    ("uk", "Ukrainian"),
    ("ms", "Malay"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("ml", "Malayalam"),
    ("sw", "Swahili"),
]


def lang_display_name(code: str) -> str:
    for c, name in COMMON_LANGS:
        if c == code:
            return name
    return code
