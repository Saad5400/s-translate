"""RTL appliers — invoked after translation when target language is RTL."""
from __future__ import annotations

from pathlib import Path

from ..schemas import DocFormat


def apply_rtl(out_path: Path, fmt: DocFormat) -> Path:
    if fmt is DocFormat.DOCX:
        from .docx_rtl import apply as f
    elif fmt is DocFormat.PPTX:
        from .pptx_rtl import apply as f
    elif fmt is DocFormat.XLSX:
        from .xlsx_rtl import apply as f
    elif fmt is DocFormat.TXT:
        from .txt_rtl import apply as f
    elif fmt is DocFormat.PDF:
        # PDF is RTL-handled inline during reinsertion; nothing to do here.
        return out_path
    else:
        return out_path
    return f(out_path)
