from __future__ import annotations

from pathlib import Path

from ..schemas import DocFormat
from ..utils.errors import UnsupportedFormatError
from .base import Translator
from .docx_translator import DocxTranslator
from .pdf_translator import PdfTranslator
from .pptx_translator import PptxTranslator
from .txt_translator import TxtTranslator
from .xlsx_translator import XlsxTranslator

_REGISTRY: dict[DocFormat, type[Translator]] = {
    DocFormat.TXT: TxtTranslator,
    DocFormat.XLSX: XlsxTranslator,
    DocFormat.DOCX: DocxTranslator,
    DocFormat.PPTX: PptxTranslator,
    DocFormat.PDF: PdfTranslator,
}


def get_translator(path: str | Path) -> Translator:
    try:
        fmt = DocFormat.from_path(path)
    except ValueError as exc:
        raise UnsupportedFormatError(str(exc)) from exc
    cls = _REGISTRY.get(fmt)
    if cls is None:
        raise UnsupportedFormatError(f"No translator for {fmt}")
    return cls()


def supported_extensions() -> list[str]:
    return [f".{f.value}" for f in _REGISTRY]
