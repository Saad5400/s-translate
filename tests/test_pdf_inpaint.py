"""Tests for rasterized-page text-replacement on OCR'd scans.

The old behaviour drew a solid-coloured rectangle on top of the page image for
each text bbox — which left a visible coloured block anywhere the background
wasn't uniformly that colour (photos, gradients, coloured stock backgrounds).
The new behaviour replaces the underlying image's pixels under each text bbox
with pixels sampled from the bands just above and below. These tests verify
the inpainting primitive and the end-to-end rasterized flow.
"""
from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from app.translators.pdf_translator import (
    _inpaint_pixels_vertical_gradient,
    _inpaint_rasterized_page,
    _is_rasterized_page,
)


def test_inpaint_uniform_background_stays_uniform():
    """A bbox drawn on a uniform coloured field is inpainted using samples from
    the bands above/below — the result must match the surrounding colour
    exactly, not leave any trace of the original text pixels."""
    arr = np.full((200, 200, 3), 120, dtype=np.uint8)
    # Simulate glyph ink inside the bbox.
    arr[80:120, 60:140, :] = 30
    _inpaint_pixels_vertical_gradient(arr, (60, 80, 140, 120), band=6)
    # Every pixel inside the bbox should now match the background (~120).
    inside = arr[80:120, 60:140, :]
    assert int(inside.min()) >= 110
    assert int(inside.max()) <= 130
    # Outside is untouched.
    assert int(arr[0, 0, 0]) == 120


def test_inpaint_vertical_gradient_interpolates():
    """A vertical gradient (top=0, bottom=255) should be reconstructed smoothly
    across the bbox — the top of the inpaint should stay dark and the bottom
    light. A solid-fill would instead paint one flat colour, destroying the
    gradient."""
    h, w = 200, 200
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    # Vertical gradient: row y has intensity y.
    for y in range(h):
        arr[y, :, :] = y
    # Overwrite a bbox with "ink" so we're really testing reconstruction.
    arr[80:120, 60:140, :] = 255
    _inpaint_pixels_vertical_gradient(arr, (60, 80, 140, 120), band=6)
    top_row_mean = arr[81, 70:130, 0].mean()
    bot_row_mean = arr[118, 70:130, 0].mean()
    # Gradient runs from ~80 at the top to ~120 at the bottom — the
    # reconstruction should respect that ordering (top < bottom).
    assert top_row_mean < bot_row_mean


def test_end_to_end_rasterized_inpaint_no_visible_rectangle():
    """Build a PDF whose page IS a full-page image with some fake "text" stamps
    burned in, then run `_inpaint_rasterized_page` over the stamp bboxes. The
    stamps must disappear: each bbox sampled from the final image should be
    close to the surrounding background, not the stamp colour.
    """
    # Build a solid coloured image (the "photo background").
    bg_colour = np.array([40, 80, 160], dtype=np.uint8)
    img = np.empty((400, 400, 3), dtype=np.uint8)
    img[:] = bg_colour
    # Stamp two "text" blocks with a distinct ink colour.
    stamps = [(60, 60, 260, 90), (60, 120, 260, 150)]
    ink = np.array([230, 230, 230], dtype=np.uint8)
    for (x0, y0, x1, y1) in stamps:
        img[y0:y1, x0:x1, :] = ink

    # Turn the array into a PDF with a single page whose only content is the
    # image, scaled to fill the page.
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    img_bytes = buf.getvalue()

    doc = pymupdf.open()
    page_rect = pymupdf.Rect(0, 0, 400, 400)
    page = doc.new_page(width=page_rect.width, height=page_rect.height)
    page.insert_image(page_rect, stream=img_bytes)
    # Sanity: the page must be classified as "rasterized" for the inpaint path
    # to apply in the real pipeline.
    assert _is_rasterized_page(page)

    # Fake segments matching each stamp's PDF bbox.
    class _FakeSeg:
        def __init__(self, bbox):
            self.meta = {"bbox": list(bbox), "font_size": 12.0}

    segs = [_FakeSeg(b) for b in stamps]
    assert _inpaint_rasterized_page(page, segs, doc) is True

    # Render the page to pixels and verify the stamps are gone.
    pix = page.get_pixmap(dpi=96, alpha=False)
    out = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    for (x0, y0, x1, y1) in stamps:
        # Map PDF coords to pixmap pixel coords (same scale because dpi=96 and
        # PDF units are points at 72dpi; scale 96/72).
        scale = pix.w / page_rect.width
        sx0 = int(x0 * scale) + 4
        sy0 = int(y0 * scale) + 4
        sx1 = int(x1 * scale) - 4
        sy1 = int(y1 * scale) - 4
        region = out[sy0:sy1, sx0:sx1, :]
        dist_to_bg = np.abs(region.astype(int) - bg_colour.astype(int)).mean()
        dist_to_ink = np.abs(region.astype(int) - ink.astype(int)).mean()
        # After inpaint, the bbox must look like the background, NOT like the
        # ink that used to be there.
        assert dist_to_bg < dist_to_ink
        # And the bbox area must not be a solid coloured rectangle of the
        # sampled "fill" (which would be the old behaviour) — for a uniform
        # background that does match here, so we only require closeness to bg.
        assert dist_to_bg < 15
    doc.close()
