from __future__ import annotations

import re
from typing import Iterable

from ..schemas import Segment

# Placeholder masking: protect URLs, emails, numbers-with-formatting from translation.
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Numbers with 3+ digits and special formatting like phone numbers; keep small
# numbers translatable (dates, quantities) so output is natural.
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")


def mask_placeholders(text: str) -> tuple[str, dict[str, str]]:
    """Replace non-translatable tokens with ⟨Pn⟩ placeholders. Return (masked, mapping)."""
    mapping: dict[str, str] = {}
    counter = [0]

    def _sub(match: re.Match[str]) -> str:
        counter[0] += 1
        key = f"⟨P{counter[0]}⟩"
        mapping[key] = match.group(0)
        return key

    masked = text
    for pattern in (_URL_RE, _EMAIL_RE, _PHONE_RE):
        masked = pattern.sub(_sub, masked)
    return masked, mapping


def unmask_placeholders(text: str, mapping: dict[str, str]) -> str:
    out = text
    for key, original in mapping.items():
        out = out.replace(key, original)
    return out


def _approx_tokens(text: str) -> int:
    """Rough token count estimate. ~4 chars/token for many langs; use tiktoken if available."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def chunk_segments(
    segments: Iterable[Segment],
    max_tokens: int = 2500,
) -> list[list[Segment]]:
    """Pack segments into chunks respecting approximate token budget."""
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    current_tokens = 0
    # Each segment adds some JSON overhead per entry (~15 tokens).
    per_segment_overhead = 15

    for seg in segments:
        seg_tokens = _approx_tokens(seg.text) + per_segment_overhead
        if current and current_tokens + seg_tokens > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(seg)
        current_tokens += seg_tokens
    if current:
        chunks.append(current)
    return chunks
