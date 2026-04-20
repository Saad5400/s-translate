from __future__ import annotations

from pathlib import Path

from ..schemas import DocFormat, Segment
from .base import Translator


class TxtTranslator(Translator):
    fmt = DocFormat.TXT

    def extract(self, src_path: Path) -> list[Segment]:
        text = src_path.read_text(encoding="utf-8", errors="replace")
        # One segment per paragraph (blank-line separated). This gives the LLM
        # enough context to produce coherent translations.
        parts = text.split("\n\n")
        segments: list[Segment] = []
        for i, part in enumerate(parts):
            segments.append(Segment(id=f"p{i}", text=part, meta={"trailing_nl": i != len(parts) - 1}))
        return segments

    def reinsert(self, src_path: Path, segments: list[Segment], out_path: Path) -> Path:
        out = []
        for seg in segments:
            out.append(seg.translated if seg.translated is not None else seg.text)
        out_path.write_text("\n\n".join(out), encoding="utf-8")
        return out_path
