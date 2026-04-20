from __future__ import annotations

RTL_CODES: frozenset[str] = frozenset(
    {"ar", "he", "fa", "ur", "yi", "ps", "sd", "ckb", "dv", "iw"}
)

# Map language code -> preferred bundled Noto font family (CSS font-family).
# Falls back to Noto Sans for unknown scripts.
LANG_TO_FONT_FAMILY: dict[str, str] = {
    "ar": "Noto Naskh Arabic",
    "fa": "Noto Naskh Arabic",
    "ur": "Noto Naskh Arabic",
    "ckb": "Noto Naskh Arabic",
    "ps": "Noto Naskh Arabic",
    "sd": "Noto Naskh Arabic",
    "he": "Noto Sans Hebrew",
    "iw": "Noto Sans Hebrew",
    "yi": "Noto Sans Hebrew",
    "dv": "Noto Sans Thaana",
    "zh": "Noto Sans CJK",
    "ja": "Noto Sans CJK",
    "ko": "Noto Sans CJK",
    "hi": "Noto Sans Devanagari",
    "bn": "Noto Sans Bengali",
    "th": "Noto Sans Thai",
    "ta": "Noto Sans Tamil",
    "te": "Noto Sans Telugu",
}


def normalize(code: str) -> str:
    """Strip region/script subtags: 'ar-SA' -> 'ar', 'zh-Hans' -> 'zh'."""
    if not code:
        return ""
    return code.strip().lower().split("-")[0].split("_")[0]


def is_rtl(lang_code: str) -> bool:
    return normalize(lang_code) in RTL_CODES


def font_for(lang_code: str) -> str:
    return LANG_TO_FONT_FAMILY.get(normalize(lang_code), "Noto Sans")
