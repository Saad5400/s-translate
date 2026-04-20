"""Deterministic stub translator used for offline end-to-end tests.

Wraps each segment's text in a recognizable marker so we can assert that the
translated text actually appears in the output file after the pipeline runs.
"""
from __future__ import annotations

import re

from app.schemas import Segment

_SENTINEL_RE = re.compile(r"⟦(\d+)⟧(.*?)⟦/\1⟧", re.DOTALL)


def stub_translate(segments: list[Segment], target_lang: str, source_lang: str | None) -> list[str]:
    out: list[str] = []
    for seg in segments:
        text = seg.text
        # If sentinels present, translate inside each; else wrap whole text.
        if _SENTINEL_RE.search(text):
            def repl(m: re.Match) -> str:
                return f"⟦{m.group(1)}⟧[{target_lang}]{m.group(2)}[/{target_lang}]⟦/{m.group(1)}⟧"

            out.append(_SENTINEL_RE.sub(repl, text))
        else:
            out.append(f"[{target_lang}]{text}[/{target_lang}]")
    return out


def reverse_translate(segments: list[Segment], target_lang: str, source_lang: str | None) -> list[str]:
    """Return reversed text (simulates RTL-ish transform for visible-diff testing)."""
    out: list[str] = []
    for seg in segments:
        text = seg.text
        if _SENTINEL_RE.search(text):
            def repl(m: re.Match) -> str:
                return f"⟦{m.group(1)}⟧{m.group(2)[::-1]}⟦/{m.group(1)}⟧"

            out.append(_SENTINEL_RE.sub(repl, text))
        else:
            out.append(text[::-1])
    return out
