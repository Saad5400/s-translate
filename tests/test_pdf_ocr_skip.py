"""Verify OCR is skipped for native-text PDFs — critical for the 1:1 input-to-
output visual match. `--force-ocr` rasterises every page and destroys crisp
vector text, so we only want to run it when the document actually has image-
baked text that needs to be surfaced.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

from app.translators.pdf_ocr import _pdf_needs_ocr


def _write(doc, path: Path) -> Path:
    doc.save(str(path))
    doc.close()
    return path


def test_pure_native_text_pdf_does_not_need_ocr(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 750),
        "<h1>Report</h1><p>"
        + " ".join(["lorem ipsum dolor sit amet"] * 20)
        + "</p>",
    )
    out = _write(doc, tmp_path / "native.pdf")
    assert _pdf_needs_ocr(out) is False


def test_decorative_small_icon_does_not_trigger_ocr(tmp_path):
    """A tiny logo or icon in the corner should not force rasterisation of
    the whole document — we only want OCR for pages that plausibly carry
    image-baked text."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_htmlbox(
        pymupdf.Rect(40, 80, 555, 750),
        "<h1>Report</h1><p>"
        + " ".join(["lorem ipsum dolor sit amet consectetur adipiscing elit"] * 20)
        + "</p>",
    )
    # Small icon image in the top-left — 1% of page area or less.
    buf = io.BytesIO()
    Image.fromarray(
        np.full((32, 32, 3), 200, dtype=np.uint8)
    ).save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(40, 40, 72, 72), stream=buf.getvalue())
    out = _write(doc, tmp_path / "icon.pdf")
    assert _pdf_needs_ocr(out) is False


def test_full_page_image_triggers_ocr(tmp_path):
    """A page that IS effectively a full-page image (scan) always needs OCR."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    buf = io.BytesIO()
    Image.fromarray(
        np.full((400, 400, 3), 180, dtype=np.uint8)
    ).save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(0, 0, 400, 400), stream=buf.getvalue())
    out = _write(doc, tmp_path / "scan.pdf")
    assert _pdf_needs_ocr(out) is True


def test_large_image_with_sparse_text_triggers_ocr(tmp_path):
    """A brochure-style page where an image takes ~30% of the page and the
    rest of the page has very little native text must be OCR'd: the image
    probably carries headlines/taglines the translator needs to see."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    buf = io.BytesIO()
    Image.fromarray(
        np.full((200, 200, 3), 150, dtype=np.uint8)
    ).save(buf, format="PNG")
    # Image covers 200×200 = 25% of a 400×400 page. Page has very little
    # native text.
    page.insert_image(pymupdf.Rect(100, 100, 300, 300), stream=buf.getvalue())
    page.insert_htmlbox(
        pymupdf.Rect(10, 10, 390, 40), "<p>Title</p>"
    )
    out = _write(doc, tmp_path / "brochure.pdf")
    assert _pdf_needs_ocr(out) is True
