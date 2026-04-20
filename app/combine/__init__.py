"""File combiners for output modes (vertical append, horizontal side-by-side)."""
from __future__ import annotations

from pathlib import Path

from ..schemas import DocFormat, OutputMode


def combine(
    src_path: Path,
    translated_path: Path,
    out_path: Path,
    fmt: DocFormat,
    mode: OutputMode,
    rtl: bool = False,
) -> Path:
    """Produce the combined file for the chosen output mode."""
    if mode is OutputMode.BOTH_VERTICAL:
        return _combine_vertical(src_path, translated_path, out_path, fmt)
    if mode is OutputMode.BOTH_HORIZONTAL:
        return _combine_horizontal(src_path, translated_path, out_path, fmt, rtl=rtl)
    raise ValueError(f"combine() should not be called for mode {mode}")


def _combine_vertical(src: Path, trans: Path, out: Path, fmt: DocFormat) -> Path:
    if fmt is DocFormat.PDF:
        from .pdf_combine import combine_vertical
        return combine_vertical(src, trans, out)
    if fmt is DocFormat.DOCX:
        from .docx_combine import combine_vertical
        return combine_vertical(src, trans, out)
    if fmt is DocFormat.PPTX:
        from .pptx_combine import combine_vertical
        return combine_vertical(src, trans, out)
    if fmt is DocFormat.XLSX:
        from .xlsx_combine import combine_vertical
        return combine_vertical(src, trans, out)
    if fmt is DocFormat.TXT:
        text_src = src.read_text(encoding="utf-8", errors="replace")
        text_tr = trans.read_text(encoding="utf-8", errors="replace")
        out.write_text(text_src + "\n\n\n---\n\n\n" + text_tr, encoding="utf-8")
        return out
    raise ValueError(f"vertical combine not implemented for {fmt}")


def _combine_horizontal(src: Path, trans: Path, out: Path, fmt: DocFormat, rtl: bool) -> Path:
    # DOCX/PPTX route via LibreOffice → PDF → PyMuPDF side-by-side; output becomes PDF.
    if fmt is DocFormat.PDF:
        from .pdf_combine import combine_horizontal
        return combine_horizontal(src, trans, out, rtl=rtl)
    if fmt in (DocFormat.DOCX, DocFormat.PPTX):
        from .libreoffice import to_pdf
        src_pdf = to_pdf(src, out.parent)
        trans_pdf = to_pdf(trans, out.parent, suffix="_translated_for_combine")
        from .pdf_combine import combine_horizontal
        # Change extension to .pdf for horizontal DOCX/PPTX output.
        pdf_out = out.with_suffix(".pdf")
        return combine_horizontal(src_pdf, trans_pdf, pdf_out, rtl=rtl)
    if fmt is DocFormat.XLSX:
        from .xlsx_combine import combine_horizontal
        return combine_horizontal(src, trans, out, rtl=rtl)
    if fmt is DocFormat.TXT:
        # Side-by-side for TXT doesn't make sense; fall back to vertical.
        text_src = src.read_text(encoding="utf-8", errors="replace")
        text_tr = trans.read_text(encoding="utf-8", errors="replace")
        out.write_text(text_src + "\n\n\n---\n\n\n" + text_tr, encoding="utf-8")
        return out
    raise ValueError(f"horizontal combine not implemented for {fmt}")
