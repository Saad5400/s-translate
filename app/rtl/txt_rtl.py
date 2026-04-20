from __future__ import annotations

from pathlib import Path

RLM = "\u200F"  # Right-to-Left Mark
BOM = "\ufeff"


def apply(out_path: Path) -> Path:
    text = out_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith(BOM):
        text = BOM + text
    # Prepend RLM after BOM so viewers pick up RTL context.
    if not text.startswith(BOM + RLM):
        text = BOM + RLM + text[len(BOM):]
    out_path.write_text(text, encoding="utf-8")
    return out_path
