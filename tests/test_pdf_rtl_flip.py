"""Full-page RTL mirror regression tests.

When translating into an RTL language (Arabic, Hebrew…) the entire page
layout must flip horizontally — text that was on the left appears on the
right, images on the right move to the left — so the output reads naturally
for RTL speakers. The implementation wraps the page's existing content
stream in a `q -1 0 0 1 W 0 cm ... Q` transform and draws the translated
text at mirrored bbox coords.

These tests use the offline stub translator so they run without an LLM.
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
import tempfile

import numpy as np
import pymupdf
import pytest
from PIL import Image

from app.api import run_job
from app.llm.client import set_stub_translator
from app.schemas import OutputMode, TranslationJob
from tests.stub_llm import stub_translate


@pytest.fixture(autouse=True)
def _stub_llm():
    set_stub_translator(stub_translate)
    yield
    set_stub_translator(None)


def _build_split_page_pdf(tmp_path: Path) -> Path:
    """Two pieces of text: clearly left-aligned and clearly right-aligned."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_htmlbox(
        pymupdf.Rect(20, 40, 250, 100),
        "<p style='font-size:14pt'>Left side text</p>",
    )
    page.insert_htmlbox(
        pymupdf.Rect(350, 200, 580, 260),
        "<p style='font-size:14pt'>Right side text</p>",
    )
    p = tmp_path / "split.pdf"
    doc.save(str(p))
    doc.close()
    return p


def _build_page_with_image(tmp_path: Path) -> Path:
    """Page with a visible image in the upper-left quadrant plus some text."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    buf = io.BytesIO()
    # Distinctive red swatch so we can locate it by colour after render.
    img = np.zeros((100, 150, 3), dtype=np.uint8)
    img[:, :, 0] = 255  # pure red
    Image.fromarray(img).save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(20, 40, 170, 140), stream=buf.getvalue())
    page.insert_htmlbox(
        pymupdf.Rect(200, 300, 400, 360),
        "<p style='font-size:14pt'>Body text</p>",
    )
    p = tmp_path / "image.pdf"
    doc.save(str(p))
    doc.close()
    return p


def test_rtl_flip_mirrors_text_positions(tmp_path):
    src = _build_split_page_pdf(tmp_path)
    job = TranslationJob(
        src_path=src, target_lang="ar",
        provider="stub", model="stub/stub", api_key="stub",
        output_mode=OutputMode.TRANSLATED,
    )
    out = asyncio.run(run_job(job))
    tr = pymupdf.open(str(out))
    try:
        page = tr[0]
        page_w = page.rect.width
        blocks = [b for b in page.get_text("blocks") if "ar]" in b[4]]
        assert len(blocks) >= 2
        for x0, y0, x1, y1, text, *_ in blocks:
            mid = (x0 + x1) / 2
            is_left = mid < page_w / 2
            if "Left side text" in text:
                assert not is_left, (
                    "after RTL mirror, original LEFT text should be on the RIGHT"
                )
            elif "Right side text" in text:
                assert is_left, (
                    "after RTL mirror, original RIGHT text should be on the LEFT"
                )
        # Sanity: content stream contains the horizontal-flip matrix.
        assert b"-1 0 0 1" in page.read_contents()
    finally:
        tr.close()


def test_ltr_target_does_not_mirror(tmp_path):
    """Control: translating into Spanish must KEEP the original layout.
    Regression guard: the RTL mirror must not trigger on LTR targets."""
    src = _build_split_page_pdf(tmp_path)
    job = TranslationJob(
        src_path=src, target_lang="es",
        provider="stub", model="stub/stub", api_key="stub",
        output_mode=OutputMode.TRANSLATED,
    )
    out = asyncio.run(run_job(job))
    tr = pymupdf.open(str(out))
    try:
        page = tr[0]
        page_w = page.rect.width
        blocks = [b for b in page.get_text("blocks") if "es]" in b[4]]
        assert len(blocks) >= 2
        for x0, y0, x1, y1, text, *_ in blocks:
            mid = (x0 + x1) / 2
            is_left = mid < page_w / 2
            if "Left side text" in text:
                assert is_left, "LTR target must keep left text on the left"
            elif "Right side text" in text:
                assert not is_left, "LTR target must keep right text on the right"
        # No flip matrix in the content stream for LTR targets.
        assert b"-1 0 0 1" not in page.read_contents()
    finally:
        tr.close()


def test_rtl_flip_mirrors_images(tmp_path):
    """An image in the upper-LEFT of the source page must appear in the
    upper-RIGHT after RTL translation — not just text, the whole page flips."""
    src = _build_page_with_image(tmp_path)
    job = TranslationJob(
        src_path=src, target_lang="ar",
        provider="stub", model="stub/stub", api_key="stub",
        output_mode=OutputMode.TRANSLATED,
    )
    out = asyncio.run(run_job(job))
    tr = pymupdf.open(str(out))
    try:
        page = tr[0]
        pix = page.get_pixmap(dpi=72, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
        # Sum red-dominance in each half (R-high, G-low, B-low).
        red_mask = (
            (arr[:, :, 0] > 200) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80)
        )
        half = pix.w // 2
        left_red = int(red_mask[:, :half].sum())
        right_red = int(red_mask[:, half:].sum())
        assert right_red > left_red * 5, (
            "red image was on left in source; after RTL flip it must be on right"
            f" (left_red={left_red}, right_red={right_red})"
        )
    finally:
        tr.close()
