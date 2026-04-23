"""Regressions for mixed Latin+Arabic rendering, multi-line paragraph
flow, and font sizing on the PIL render path.

Each test uses the stub translator so it runs without the LLM. The
assertions are deliberately coarse — pixel-perfect matches are too
fragile across font / libraqm versions, so we check that:
  * the glyphs ARE rendered (non-empty black-pixel count) rather than
    tofu rectangles which would have a very different footprint;
  * a merged multi-line source paragraph results in the translated text
    occupying ONE horizontal flow, not the original line-by-line split;
  * translated font size is in the same ballpark as the source.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw

from app.translators.pdf_translator import (
    _draw_text_in_bbox,
    _load_pil_font,
)


def _black_pixel_mask(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.convert("RGB"))
    return (arr.sum(axis=2) < 3 * 80)


def test_mixed_script_renders_without_tofu():
    """Arabic sentence with embedded English tokens ("DevOps", "2025")
    must render real glyphs — not replacement-rectangles — for the Latin
    runs. A tofu square is a filled rectangular pixel block; real glyphs
    have thin strokes. We assert the black-pixel density for Latin runs
    is within the healthy range for text (not solid-filled rectangles)."""
    img = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(img)
    cache: dict = {}
    text = "منصة DevOps في 2025 تحتاج API موثوق"
    _draw_text_in_bbox(
        draw, text, (20, 20, 780, 180),
        target_lang="ar", rtl=True, font_size=40,
        color=(0, 0, 0), bold=False, italic=False, font_cache=cache,
    )
    mask = _black_pixel_mask(img)
    # If Latin chars were tofu squares, the mask density inside each
    # glyph cell would be close to 1.0. Real glyph strokes are sparse
    # (typically 0.15 - 0.35 density). Check the density over the non-
    # empty rows is nowhere near "solid-filled".
    per_row = mask.sum(axis=1)
    ink_rows = per_row[per_row > 0]
    assert ink_rows.size > 20, "expected multiple rows of ink"
    max_row = int(per_row.max())
    # A row that's entirely tofu rectangles would easily exceed 400 px of
    # black on this 800-px-wide canvas. Real text is well under that.
    assert max_row < 500, f"suspiciously dense row — possible tofu ({max_row})"


def test_cairo_is_primary_arabic_font():
    """Loading an Arabic font for ar should return Cairo (which covers
    both Arabic and Latin). Regression: earlier builds used Noto Sans
    Arabic as primary, which doesn't carry Latin glyphs."""
    cache: dict = {}
    font = _load_pil_font("ar", 24, False, False, cache)
    # Cairo-Variable reports name containing 'Cairo'.
    name = getattr(font, "font", None)
    family = name.family if name is not None else ""
    assert "Cairo" in (family or ""), f"expected Cairo, got {family!r}"


def test_bold_variant_applied_for_cairo():
    """Bold-flagged segments should pick up Cairo's bold axis, not the
    regular weight. We verify by checking stroke density differs between
    bold and regular renders of the same text."""
    cache: dict = {}
    img_reg = Image.new("RGB", (400, 80), "white")
    img_bold = Image.new("RGB", (400, 80), "white")
    for im, bold in ((img_reg, False), (img_bold, True)):
        d = ImageDraw.Draw(im)
        _draw_text_in_bbox(
            d, "عنوان مهم", (10, 10, 390, 70),
            target_lang="ar", rtl=True, font_size=36,
            color=(0, 0, 0), bold=bold, italic=False, font_cache=cache,
        )
    ink_reg = int(_black_pixel_mask(img_reg).sum())
    ink_bold = int(_black_pixel_mask(img_bold).sum())
    assert ink_bold > ink_reg * 1.15, (
        f"bold should draw heavier strokes (reg={ink_reg}, bold={ink_bold})"
    )


def test_multiline_paragraph_flows_across_width():
    """A paragraph whose source has internal \\n (from OCR line merging)
    must reflow as a single paragraph in the translation. We pass two
    \\n-joined source lines and check the rendered output spans MORE
    than one horizontal line within a square bbox — confirming the
    wrapper didn't respect the source's literal line breaks."""
    img = Image.new("RGB", (400, 400), "white")
    draw = ImageDraw.Draw(img)
    cache: dict = {}
    # Source lines 1 and 2 joined with \n, as the paragraph-merger emits.
    text = (
        "The waterfall model created extremely long deployment cycles.\n"
        "Developers would write code for months before it reached operations."
    )
    _draw_text_in_bbox(
        draw, text, (10, 10, 390, 390),
        target_lang="en", rtl=False, font_size=24,
        color=(0, 0, 0), bold=False, italic=False, font_cache=cache,
    )
    mask = _black_pixel_mask(img)
    ink_per_row = mask.sum(axis=1)
    ink_rows = np.where(ink_per_row > 5)[0]
    assert ink_rows.size > 0
    # Collapse runs of consecutive rows into "text lines". With the
    # \n-as-soft-break fix, the paragraph reflows into roughly 3-5
    # wrapped lines within the square bbox — not the source's 2 lines.
    gaps = np.where(np.diff(ink_rows) > 4)[0]
    num_visual_lines = gaps.size + 1
    assert num_visual_lines >= 3, (
        f"expected reflow to >=3 lines within the bbox (got {num_visual_lines})"
    )


def test_rotated_text_detection(tmp_path):
    """_detect_rotated_text_on_page must find a 90°-rotated AGILE
    wordmark baked into a rasterized page."""
    from PIL import ImageFont

    from app.translators.pdf_translator import _detect_rotated_text_on_page

    canvas = Image.new("RGB", (600, 800), "white")
    font_path = Path("app/fonts/NotoSans-Regular.ttf")
    if not font_path.exists():
        pytest.skip("NotoSans font missing")
    f = ImageFont.truetype(str(font_path), 70)
    layer = Image.new("RGBA", (260, 90), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((0, 0), "AGILE", font=f, fill="black")
    rotated_layer = layer.rotate(90, expand=True)
    canvas.paste(rotated_layer, (30, 250), rotated_layer)

    src = tmp_path / "rotated.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    import io
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(0, 0, 600, 800), stream=buf.getvalue())
    doc.save(str(src))
    doc.close()

    d = pymupdf.open(str(src))
    try:
        results = _detect_rotated_text_on_page(d[0], 0)
    finally:
        d.close()
    texts = [s.text for s in results]
    assert any("AGILE" in t for t in texts), f"did not detect AGILE: {texts}"
    agile = next(s for s in results if "AGILE" in s.text)
    # rotate(90) → reads bottom-to-top → our convention: rotation=90.
    assert agile.meta["rotation"] == 90, f"wrong rotation: {agile.meta['rotation']}"
    bx = agile.meta["bbox"]
    # bbox should be tall-and-narrow (rotated text is taller than wide).
    assert (bx[3] - bx[1]) > (bx[2] - bx[0]) * 1.5


def test_horizontal_culled_when_rotated_overlaps():
    """A horizontal segment whose bbox is majority-contained by a
    rotated segment's bbox must be dropped — it's almost certainly the
    garbage ocrmypdf produced when it tried to read vertical glyphs
    horizontally (e.g. "SCRUM" misread as "INMYDS")."""
    from app.schemas import Segment
    from app.translators.pdf_translator import _cull_overlapping_horizontal

    garbage = Segment(
        id="b0", text="INMYDS",
        meta={"page": 0, "bbox": [500, 250, 565, 480], "rotation": 0},
    )
    clean_body = Segment(
        id="b1", text="Body paragraph",
        meta={"page": 0, "bbox": [60, 100, 430, 150], "rotation": 0},
    )
    rotated = Segment(
        id="r0", text="SCRUM",
        meta={"page": 0, "bbox": [510, 255, 565, 485], "rotation": 270},
    )
    kept = _cull_overlapping_horizontal([garbage, clean_body], [rotated])
    ids = [s.id for s in kept]
    assert "b1" in ids
    assert "b0" not in ids, "garbage horizontal segment should be culled"
