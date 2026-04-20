from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..config import settings
from ..utils.errors import TranslationError


def to_pdf(src: Path, outdir: Path, suffix: str | None = None) -> Path:
    """Convert DOCX/PPTX/XLSX to PDF via LibreOffice headless. Returns PDF path."""
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.libreoffice_bin,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(src),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TranslationError(
            f"LibreOffice binary '{settings.libreoffice_bin}' not found. "
            f"Install libreoffice or set LIBREOFFICE_BIN env var."
        ) from exc
    if result.returncode != 0:
        raise TranslationError(f"LibreOffice conversion failed: {result.stderr[:500]}")
    pdf_candidate = outdir / (src.stem + ".pdf")
    if not pdf_candidate.exists():
        raise TranslationError(f"LibreOffice did not produce {pdf_candidate}")
    if suffix:
        renamed = outdir / (src.stem + suffix + ".pdf")
        shutil.move(pdf_candidate, renamed)
        return renamed
    return pdf_candidate
