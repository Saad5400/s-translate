from __future__ import annotations

import html
import re
from pathlib import Path

from ..lang.rtl import font_for, is_rtl
from ..schemas import DocFormat, Segment
from .base import Translator


# Leading bullet / numbering characters we preserve untouched. The text AFTER
# the bullet is what gets translated; the bullet glyph is re-applied on the
# correct side of the bbox after translation.
_BULLET_RE = re.compile(
    r"^\s*(?P<bul>[\u2022\u25CF\u25E6\u25AA\u25B8\u25A0\u2023\u2043\u00B7\u2219\-–—*•◦▪▶►]|\d{1,3}[\.\)])\s+",
    re.UNICODE,
)

_SAVE_KW = dict(
    garbage=4,
    deflate=True,
    deflate_images=True,
    deflate_fonts=True,
    clean=True,
    linear=False,
)


class PdfTranslator(Translator):
    """Extract text blocks with coordinates, redact originals (without destroying
    backgrounds), reinsert translated text preserving colors, bullets and RTL flow."""

    fmt = DocFormat.PDF

    def extract(self, src_path: Path) -> list[Segment]:
        import pymupdf

        doc = pymupdf.open(str(src_path))
        segments: list[Segment] = []
        idx = 0
        try:
            for page_i, page in enumerate(doc):
                page_dict = page.get_text("dict")
                for block_i, block in enumerate(page_dict.get("blocks", [])):
                    if block.get("type", 0) != 0:  # skip image blocks
                        continue
                    bbox = block.get("bbox")
                    lines = block.get("lines", [])
                    if not lines or not bbox:
                        continue
                    text_parts: list[str] = []
                    font_sizes: list[float] = []
                    colors: list[int] = []
                    fonts_seen: list[str] = []
                    is_bold = False
                    is_italic = False
                    for line in lines:
                        line_parts: list[str] = []
                        for span in line.get("spans", []):
                            line_parts.append(span.get("text", ""))
                            if span.get("size") is not None:
                                font_sizes.append(float(span["size"]))
                            if span.get("color") is not None:
                                colors.append(int(span["color"]))
                            fname = span.get("font", "")
                            fonts_seen.append(fname)
                            flags = span.get("flags", 0) or 0
                            # Bit 4 bold, bit 1 italic (see PyMuPDF docs).
                            if flags & 16:
                                is_bold = True
                            if flags & 2:
                                is_italic = True
                        if line_parts:
                            text_parts.append("".join(line_parts))
                    text = "\n".join(text_parts).strip()
                    if not text:
                        continue

                    # Detect bullet prefix and strip it from translatable text.
                    bullet = ""
                    m = _BULLET_RE.match(text)
                    if m:
                        bullet = m.group("bul")
                        text = text[m.end():]

                    avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else 11.0
                    # Majority-color
                    color_int = max(set(colors), key=colors.count) if colors else 0
                    # Font family hint (lowercased, crude)
                    font_hint = fonts_seen[0] if fonts_seen else ""

                    segments.append(
                        Segment(
                            id=f"b{idx}",
                            text=text,
                            meta={
                                "page": page_i,
                                "bbox": list(bbox),
                                "font_size": avg_size,
                                "font_hint": font_hint,
                                "bold": is_bold,
                                "italic": is_italic,
                                "color": color_int,
                                "bullet": bullet,
                                "block_idx": block_i,
                            },
                        )
                    )
                    idx += 1

                # OCR embedded images and add image-text segments.
                ocr_segs, new_idx = _ocr_page_images(doc, page, page_i, idx)
                segments.extend(ocr_segs)
                idx = new_idx
        finally:
            doc.close()
        return segments

    def reinsert(
        self,
        src_path: Path,
        segments: list[Segment],
        out_path: Path,
        target_lang: str = "",
    ) -> Path:
        import pymupdf

        rtl = is_rtl(target_lang) if target_lang else False
        font_family = font_for(target_lang) if target_lang else "Noto Sans"
        archive, css = _build_font_archive()

        if rtl:
            return self._reinsert_rtl(
                src_path, segments, out_path, font_family, archive, css
            )
        return self._reinsert_ltr(
            src_path, segments, out_path, font_family, archive, css
        )

    def _reinsert_ltr(
        self, src_path, segments, out_path, font_family, archive, css,
    ) -> Path:
        """Straightforward in-place LTR reinsertion: redact text, draw translated."""
        import pymupdf

        doc = pymupdf.open(str(src_path))
        try:
            by_page: dict[int, list[Segment]] = {}
            for seg in segments:
                by_page.setdefault(seg.meta["page"], []).append(seg)

            for page_i in range(doc.page_count):
                page = doc[page_i]
                page_segs = by_page.get(page_i, [])
                if not page_segs:
                    continue
                text_segs = [s for s in page_segs if s.meta.get("source") != "image"]
                img_segs = [s for s in page_segs if s.meta.get("source") == "image"]
                for seg in text_segs:
                    rect = pymupdf.Rect(seg.meta["bbox"])
                    page.add_redact_annot(rect, fill=None)
                if text_segs:
                    page.apply_redactions(
                        images=pymupdf.PDF_REDACT_IMAGE_NONE,
                        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                    )
                for seg in text_segs:
                    rect = pymupdf.Rect(seg.meta["bbox"])
                    _draw_segment(
                        page, rect, seg, font_family, "ltr", archive, css
                    )
                for seg in img_segs:
                    rect = pymupdf.Rect(seg.meta["bbox"])
                    _draw_image_segment(
                        page, rect, seg, font_family, "ltr", archive, css
                    )
            doc.save(str(out_path), **_SAVE_KW)
        finally:
            doc.close()
        return out_path

    def _reinsert_rtl(
        self, src_path, segments, out_path, font_family, archive, css,
    ) -> Path:
        """Mirror the whole page layout for RTL targets.

        For each page, we build a NEW page in a fresh document:
          1. Stamp the original page onto the new page with a horizontal-flip
             transform. This mirrors ALL visual content (text, graphics, images,
             backgrounds) — every position is at (w - x).
          2. The mirrored original text is unreadable (reversed glyphs) AND will
             be covered by our translated overlay anyway.
          3. Extract each source image and re-insert it at the mirrored bbox
             WITHOUT flipping its content. This "un-flips" only the images.
          4. Draw translated text at the mirrored bbox of every text segment.
        """
        import pymupdf

        src_doc = pymupdf.open(str(src_path))
        try:
            new_doc = pymupdf.open()
            by_page: dict[int, list[Segment]] = {}
            for seg in segments:
                by_page.setdefault(seg.meta["page"], []).append(seg)

            for page_i in range(src_doc.page_count):
                src_page = src_doc[page_i]
                w = src_page.rect.width
                h = src_page.rect.height
                new_page = new_doc.new_page(width=w, height=h)
                rect = pymupdf.Rect(0, 0, w, h)

                # Step 1: flipped rasterized background preserves shapes/colors/
                # strips in mirrored orientation. Skipped for pure-text pages
                # (no drawings, few images) since the overlay covers everything
                # anyway — big file-size win on text-heavy documents.
                needs_bg = _page_has_non_text_graphics(src_page)
                page_segs_for_bg = [
                    s for s in by_page.get(page_i, [])
                    if s.meta.get("source") != "image"
                ] if needs_bg else []
                flipped_bg = (
                    _render_flipped_page(src_page, page_segs_for_bg)
                    if needs_bg
                    else None
                )
                if flipped_bg is not None:
                    new_page.insert_image(rect, stream=flipped_bg, keep_proportion=False)

                # Step 2: re-insert each image at the mirrored bbox, unflipped.
                # Use `extract_image` which returns the ORIGINAL compressed bytes
                # (JPEG/PNG/etc.) — inserting as a stream preserves the native
                # compression and avoids the ~100× bloat of raw Pixmap embedding.
                for img_info in src_page.get_images(full=True):
                    xref = img_info[0]
                    try:
                        bbox_list = src_page.get_image_rects(xref)
                    except Exception:
                        bbox_list = []
                    if not bbox_list:
                        continue
                    try:
                        extracted = src_doc.extract_image(xref)
                        img_bytes = extracted.get("image") if extracted else None
                    except Exception:
                        img_bytes = None
                    for bbox in bbox_list:
                        mirrored = pymupdf.Rect(
                            w - bbox.x1, bbox.y0, w - bbox.x0, bbox.y1
                        )
                        try:
                            if img_bytes:
                                new_page.insert_image(
                                    mirrored, stream=img_bytes, keep_proportion=False
                                )
                            else:
                                pix = pymupdf.Pixmap(src_doc, xref)
                                new_page.insert_image(mirrored, pixmap=pix)
                                pix = None
                        except Exception:
                            continue

                # Step 3: draw translated text at mirrored bboxes. Glyph ghosts
                # from the flipped bg have already been erased in-raster by
                # _render_flipped_page, so we rely on that instead of drawing
                # a per-bbox fill rectangle here — avoids the grey-strip
                # artefact that mean-based sampling used to produce on white
                # pages.
                page_segs = by_page.get(page_i, [])
                for seg in page_segs:
                    rect_orig = pymupdf.Rect(seg.meta["bbox"])
                    if seg.meta.get("source") == "image":
                        # The image is re-inserted un-flipped at its mirrored
                        # bbox (step 2). So the text overlay should be at the
                        # SAME relative offset inside the mirrored image bbox —
                        # NOT the fully-mirrored page position.
                        img_bbox = seg.meta.get("image_page_bbox")
                        if img_bbox:
                            mirrored_bbox = pymupdf.Rect(
                                (w - img_bbox[2]) + (rect_orig.x0 - img_bbox[0]),
                                rect_orig.y0,
                                (w - img_bbox[2]) + (rect_orig.x1 - img_bbox[0]),
                                rect_orig.y1,
                            )
                        else:
                            mirrored_bbox = pymupdf.Rect(
                                w - rect_orig.x1, rect_orig.y0,
                                w - rect_orig.x0, rect_orig.y1,
                            )
                        _draw_image_segment(
                            new_page, mirrored_bbox, seg, font_family, "rtl",
                            archive, css,
                        )
                        continue
                    mirrored_bbox = pymupdf.Rect(
                        w - rect_orig.x1,
                        rect_orig.y0,
                        w - rect_orig.x0,
                        rect_orig.y1,
                    )
                    _draw_segment(
                        new_page, mirrored_bbox, seg, font_family, "rtl", archive, css
                    )

            new_doc.save(str(out_path), **_SAVE_KW)
            new_doc.close()
        finally:
            src_doc.close()
        return out_path


def _page_has_non_text_graphics(src_page) -> bool:
    """Return True if the page has drawings/shapes/images worth preserving via
    a rasterized bg layer. Pure-text pages return False so we skip the raster
    overhead entirely."""
    try:
        drawings = src_page.get_drawings()
        if len(drawings) > 3:
            return True
    except Exception:
        pass
    try:
        images = src_page.get_images(full=True)
        if images:
            return True
    except Exception:
        pass
    return False


def _render_flipped_page(src_page, segments=None, dpi: int = 96) -> bytes | None:
    """Render a source page to a horizontally-flipped JPEG used as the RTL
    background layer. Before flipping, we erase each text bbox in-raster by
    filling it with the sampled local background colour — that way the
    flipped bg has NO mirrored glyph ghosts, so the translated overlay can
    be drawn directly on top without needing an extra fill rectangle.
    """
    try:
        import io

        from PIL import Image, ImageDraw

        pix = src_page.get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
        page_w = src_page.rect.width
        page_h = src_page.rect.height

        if segments:
            draw = ImageDraw.Draw(img)
            pad = 2  # grow erase area slightly to cover anti-aliased edges
            for seg in segments:
                bbox = seg.meta.get("bbox")
                if not bbox:
                    continue
                x0 = int(bbox[0] / page_w * img.width) - pad
                y0 = int(bbox[1] / page_h * img.height) - pad
                x1 = int(bbox[2] / page_w * img.width) + pad
                y1 = int(bbox[3] / page_h * img.height) + pad
                x0 = max(0, x0); y0 = max(0, y0)
                x1 = min(img.width, x1); y1 = min(img.height, y1)
                if x1 <= x0 or y1 <= y0:
                    continue
                rgb = _dominant_bg_rgb(img, (x0, y0, x1, y1))
                draw.rectangle([x0, y0, x1, y1], fill=rgb)

        flipped = img.transpose(Image.FLIP_LEFT_RIGHT)
        buf = io.BytesIO()
        flipped.save(buf, format="JPEG", quality=75, optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def _dominant_bg_rgb(img, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Sample the DOMINANT (mode) colour inside `box` on `img`, ignoring dark
    glyph pixels. Much more robust than a pixel-mean, which gets pulled toward
    grey by anti-aliased text on white/coloured backgrounds."""
    from collections import Counter

    crop = img.crop(box)
    w, h = crop.size
    if w < 2 or h < 2:
        return (255, 255, 255)
    # Sample the borders (top/bottom edges + left/right edges). The centre
    # tends to be mostly ink; the borders are mostly background.
    border_px: list[tuple[int, int, int]] = []
    sample_w = crop.resize((min(64, w), min(16, h)))
    pixels = list(sample_w.getdata())
    sw, sh = sample_w.size
    for i, p in enumerate(pixels):
        y = i // sw
        x = i % sw
        on_edge = y == 0 or y == sh - 1 or x == 0 or x == sw - 1
        if on_edge:
            border_px.append(p)
    if not border_px:
        border_px = pixels
    # Quantize heavily (step 16) and take the mode.
    def q(p):
        return (p[0] // 16 * 16, p[1] // 16 * 16, p[2] // 16 * 16)
    counts = Counter(q(p) for p in border_px)
    (qr, qg, qb), _ = counts.most_common(1)[0]
    # Refine: average the border pixels that fall in this quantized bucket.
    matching = [p for p in border_px if q(p) == (qr, qg, qb)]
    if not matching:
        matching = border_px
    r = sum(p[0] for p in matching) // len(matching)
    g = sum(p[1] for p in matching) // len(matching)
    b = sum(p[2] for p in matching) // len(matching)
    return (r, g, b)


def _ocr_page_images(doc, page, page_i: int, start_idx: int):
    """OCR each embedded image on a page; return Segments (source=image) plus
    the new running index. Tiny/decorative images are skipped."""
    try:
        import io

        import pytesseract
        from PIL import Image
    except Exception:
        return [], start_idx

    segments: list[Segment] = []
    idx = start_idx
    try:
        imgs = page.get_images(full=True)
    except Exception:
        imgs = []
    for img_info in imgs:
        xref = img_info[0]
        try:
            bboxes = page.get_image_rects(xref)
        except Exception:
            bboxes = []
        if not bboxes:
            continue
        try:
            extracted = doc.extract_image(xref)
            img_bytes = extracted.get("image") if extracted else None
        except Exception:
            img_bytes = None
        if not img_bytes:
            continue
        try:
            pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception:
            continue
        if pil.width * pil.height < 20_000:
            continue  # skip icons / tiny decorative images
        # Upscale small images to improve OCR fidelity.
        if pil.width < 800:
            scale = 800 / pil.width
            pil = pil.resize(
                (int(pil.width * scale), int(pil.height * scale)),
                Image.LANCZOS,
            )
        try:
            data = pytesseract.image_to_data(
                pil, lang="eng",
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            continue

        # Collect word-level OCR entries.
        word_entries: list[dict] = []
        for i, word in enumerate(data.get("text", [])):
            if not word or not word.strip():
                continue
            try:
                conf = int(float(data["conf"][i]))
            except Exception:
                conf = -1
            if conf < 50:
                continue
            word_entries.append({
                "words": [word],
                "x0": int(data["left"][i]),
                "y0": int(data["top"][i]),
                "x1": int(data["left"][i]) + int(data["width"][i]),
                "y1": int(data["top"][i]) + int(data["height"][i]),
                "line_key": (
                    int(data["block_num"][i]),
                    int(data["par_num"][i]),
                    int(data["line_num"][i]),
                ),
            })

        # Merge neighbouring words only if they're on the same OCR line AND
        # their horizontal gap is small (< 1.5× median word height). Widely
        # spaced labels on the same visual Y (e.g. horizontal-flow diagrams)
        # stay as separate segments — keeps fill rects tight.
        word_entries.sort(key=lambda e: (e["line_key"], e["x0"]))
        lines_out: list[dict] = []
        for e in word_entries:
            if lines_out:
                prev = lines_out[-1]
                same_line = prev["line_key"] == e["line_key"]
                gap = e["x0"] - prev["x1"]
                avg_h = (
                    (prev["y1"] - prev["y0"]) + (e["y1"] - e["y0"])
                ) / 2
                if same_line and gap < 1.5 * avg_h:
                    prev["words"].extend(e["words"])
                    prev["x1"] = e["x1"]
                    prev["y0"] = min(prev["y0"], e["y0"])
                    prev["y1"] = max(prev["y1"], e["y1"])
                    continue
            lines_out.append(dict(e))

        # Skip OCR on images that look like diagrams (many scattered small
        # regions). The overlays on such images tend to degrade readability
        # rather than help: individual labels get translated word-by-word,
        # obscuring the original diagram without producing clean Arabic.
        if len(lines_out) > 5:
            continue
        lines = {i: e for i, e in enumerate(lines_out)}

        for bbox in bboxes:
            img_x0, img_y0, img_x1, img_y1 = bbox.x0, bbox.y0, bbox.x1, bbox.y1
            img_w = img_x1 - img_x0
            img_h = img_y1 - img_y0
            for entry in lines.values():
                text = " ".join(entry["words"]).strip()
                if not text or len(text) < 2:
                    continue
                rx0 = entry["x0"] / pil.width
                ry0 = entry["y0"] / pil.height
                rx1 = entry["x1"] / pil.width
                ry1 = entry["y1"] / pil.height
                abs_bbox = [
                    img_x0 + rx0 * img_w,
                    img_y0 + ry0 * img_h,
                    img_x0 + rx1 * img_w,
                    img_y0 + ry1 * img_h,
                ]
                # Font size in PDF pt: map height in pixels → page-point height.
                pt_height = (entry["y1"] - entry["y0"]) / pil.height * img_h
                font_size = max(6.0, pt_height * 0.72)
                # Sample bg and text color at this patch.
                bg_rgb = _dominant_bg_rgb(
                    pil,
                    (entry["x0"], entry["y0"], entry["x1"], entry["y1"]),
                )
                text_rgb = _dominant_ink_rgb(
                    pil,
                    (entry["x0"], entry["y0"], entry["x1"], entry["y1"]),
                    bg_rgb,
                )
                segments.append(
                    Segment(
                        id=f"img{idx}",
                        text=text,
                        meta={
                            "page": page_i,
                            "bbox": abs_bbox,
                            "font_size": font_size,
                            "bold": False,
                            "italic": False,
                            "color": (text_rgb[0] << 16) | (text_rgb[1] << 8) | text_rgb[2],
                            "bullet": "",
                            "source": "image",
                            "image_page_bbox": [img_x0, img_y0, img_x1, img_y1],
                            "image_bg_rgb": bg_rgb,
                        },
                    )
                )
                idx += 1
    return segments, idx


def _dominant_ink_rgb(img, box, bg_rgb):
    """Pick a representative INK (foreground) color inside `box` — i.e. the
    most common colour that is NOT close to the background. Falls back to
    black if no clear ink colour is detectable."""
    from collections import Counter

    crop = img.crop(box)
    w, h = crop.size
    if w < 2 or h < 2:
        return (0, 0, 0)
    small = crop.resize((min(32, w), min(32, h)))
    pixels = list(small.getdata())

    def q(p):
        return (p[0] // 32 * 32, p[1] // 32 * 32, p[2] // 32 * 32)

    bg_q = (bg_rgb[0] // 32 * 32, bg_rgb[1] // 32 * 32, bg_rgb[2] // 32 * 32)
    counts = Counter(q(p) for p in pixels if q(p) != bg_q)
    if not counts:
        return (0, 0, 0)
    (qr, qg, qb), _ = counts.most_common(1)[0]
    matching = [p for p in pixels if q(p) == (qr, qg, qb)]
    if not matching:
        return (0, 0, 0)
    r = sum(p[0] for p in matching) // len(matching)
    g = sum(p[1] for p in matching) // len(matching)
    b = sum(p[2] for p in matching) // len(matching)
    return (r, g, b)


def _draw_image_segment(page, rect, seg, font_family, dir_attr, archive, css) -> None:
    """Draw translated OCR'd text ON TOP of an image with a tight bg fill
    matching the sampled image background, so the original English word is
    covered without stamping a wider rectangle than the text occupies."""
    bg = seg.meta.get("image_bg_rgb", (255, 255, 255))
    try:
        page.draw_rect(
            rect,
            color=None,
            fill=(bg[0] / 255, bg[1] / 255, bg[2] / 255),
            overlay=True,
        )
    except Exception:
        pass
    _draw_segment(page, rect, seg, font_family, dir_attr, archive, css)


def _draw_segment(page, rect, seg, font_family, dir_attr, archive, css) -> None:
    """Render one translated segment as an HTML box into `rect` on `page`."""
    text = seg.translated or seg.text
    size = seg.meta.get("font_size", 11.0)
    color_hex = _int_to_hex(seg.meta.get("color", 0))
    weight = "bold" if seg.meta.get("bold") else "normal"
    style = "italic" if seg.meta.get("italic") else "normal"
    bullet = seg.meta.get("bullet", "")
    content_text = (bullet + " " + text) if bullet else text
    escaped = html.escape(content_text).replace("\n", "<br>")
    snippet = (
        f'<div dir="{dir_attr}" '
        f'style="color:{color_hex}; '
        f"font-family: '{font_family}', sans-serif; "
        f"font-size: {size:.1f}pt; "
        f"font-weight: {weight}; "
        f"font-style: {style}; "
        f'line-height: 1.15;">'
        f"{escaped}</div>"
    )
    _safe_insert_htmlbox(page, rect, snippet, size, archive=archive, css=css)


def _int_to_hex(color_int: int) -> str:
    """Convert PyMuPDF integer color (0xRRGGBB) to CSS hex."""
    try:
        v = int(color_int)
    except Exception:
        v = 0
    return f"#{v & 0xFFFFFF:06x}"


def _build_font_archive():
    import pymupdf

    from ..config import settings

    fonts_dir = settings.fonts_dir
    if not fonts_dir.exists():
        return None, ""
    archive = pymupdf.Archive()
    css_parts: list[str] = []
    mapping = [
        ("Noto Naskh Arabic", "NotoNaskhArabic-Regular.ttf", "400"),
        ("Noto Naskh Arabic", "NotoNaskhArabic-Bold.ttf", "700"),
        ("Noto Sans Hebrew", "NotoSansHebrew-Regular.ttf", "400"),
        ("Noto Sans", "NotoSans-Regular.ttf", "400"),
    ]
    for family, fname, weight in mapping:
        fpath = fonts_dir / fname
        if not fpath.exists():
            continue
        archive.add(str(fpath), fname)
        css_parts.append(
            f"@font-face {{ font-family: '{family}'; src: url({fname}); font-weight: {weight}; }}"
        )
    return archive, "\n".join(css_parts)


def _safe_insert_htmlbox(
    page, rect, html_snippet: str, size: float, archive=None, css: str = ""
) -> None:
    """Insert HTML; on overflow, progressively shrink font-size, then widen the
    rect, then fall back to plain textbox. Returns silently either way."""
    import pymupdf

    def _attempt(r, snippet) -> int:
        kwargs = {}
        if archive is not None:
            kwargs["archive"] = archive
        if css:
            kwargs["css"] = css
        rc, _ = page.insert_htmlbox(r, snippet, **kwargs)
        return rc

    # Try original size + progressive shrink (to 4pt floor).
    sizes = [size] + [max(4.0, size - s) for s in (1, 2, 3, 4, 5, 6)]
    for fs in sizes:
        snippet = html_snippet.replace(
            f"font-size: {size:.1f}pt", f"font-size: {fs:.1f}pt"
        )
        try:
            if _attempt(rect, snippet) >= 0:
                return
        except Exception:
            continue

    # Still overflowing — widen the rect. Tight badge bboxes (DEVOPS etc.)
    # fall in here. Expand horizontally up to 2.5×; keep the original
    # horizontal anchor so it still visually aligns with the source strip.
    page_w = page.rect.width
    for factor in (1.4, 1.8, 2.5):
        new_w = min(page_w, (rect.x1 - rect.x0) * factor)
        # Expand symmetrically but clamp to page bounds.
        cx = (rect.x0 + rect.x1) / 2
        x0 = max(0.0, cx - new_w / 2)
        x1 = min(page_w, cx + new_w / 2)
        widened = pymupdf.Rect(x0, rect.y0, x1, rect.y1)
        for fs in (size, max(4.0, size - 2)):
            snippet = html_snippet.replace(
                f"font-size: {size:.1f}pt", f"font-size: {fs:.1f}pt"
            )
            try:
                if _attempt(widened, snippet) >= 0:
                    return
            except Exception:
                continue

    # Last resort: raw textbox at min size.
    try:
        page.insert_textbox(rect, _strip_html(html_snippet), fontsize=5, align=0)
    except Exception:
        pass


def _strip_html(s: str) -> str:
    return (
        re.sub(r"<[^>]+>", "", s)
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
