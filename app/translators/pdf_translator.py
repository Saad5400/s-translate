from __future__ import annotations

import html
import logging
import re
from pathlib import Path

from ..lang.rtl import font_for, is_rtl
from ..schemas import DocFormat, Segment
from .base import Translator

log = logging.getLogger(__name__)


# Leading bullet / numbering characters we preserve untouched. The text AFTER
# the bullet is what gets translated; the bullet glyph is re-applied on the
# correct side of the bbox after translation.
_BULLET_RE = re.compile(
    r"^\s*(?P<bul>[•●◦▪▸■‣⁃·∙\-–—*▶►]|\d{1,3}[\.\)])\s+",
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
    """Extract text blocks with coordinates, redact original glyphs in place
    (leaving images and vector drawings intact), then reinsert translated text
    with the original colors, bullets, and font size.

    For RTL targets (Arabic, Hebrew, Persian…) the ENTIRE PAGE is mirrored
    horizontally so the reading order flips: elements that were on the left
    in the source document appear on the right in the output. The mirror is
    achieved by wrapping the page's existing content stream in a `q -1 0 0 1
    W 0 cm … Q` PDF transform — vector graphics, raster images, and the
    redacted-text fills all flip together, staying vectors / images (no
    rasterisation). The translated text is then drawn UNFLIPPED into bboxes
    that were mirrored around the page's vertical centre, so each Arabic
    paragraph lands exactly where its LTR counterpart would be after a
    mirror. This is what users expect from a "proper" RTL page layout.
    """

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
                    lines = block.get("lines", [])
                    if not lines:
                        continue
                    # PyMuPDF occasionally packs visually-separate elements
                    # (e.g. two side-by-side badges on the same y-row) into a
                    # single block. The block bbox then spans text-start →
                    # text-end, missing the gap between the badges. When we
                    # redact that combined bbox and paint translated text back,
                    # all copy lands in the first badge and the second one is
                    # wiped blank. Detect horizontally-arranged lines by their
                    # overlapping y-ranges and emit one segment per group so
                    # each element keeps its own bbox. Vertically-stacked
                    # paragraphs still collapse into one segment so multi-line
                    # text is translated with full context.
                    groups = _group_lines_by_row(lines)
                    for group in groups:
                        seg = _segment_from_lines(group, page_i, block_i, idx)
                        if seg is not None:
                            segments.append(seg)
                            idx += 1
        finally:
            doc.close()
        # ocrmypdf produces ONE text block per OCR'd line even when multiple
        # lines clearly form a single paragraph. PyMuPDF's block-level grouping
        # can't see across blocks, so `_group_lines_by_row` above only merges
        # lines that are already in the same block. Run a second pass that
        # merges vertically-adjacent segments sharing left margin, line height
        # and font properties — this turns "two separate texts" back into a
        # single paragraph so the translator gets the full sentence context and
        # the translated text flows naturally (critical for RTL languages where
        # splitting a sentence mid-thought breaks word order).
        segments = _merge_vertical_paragraphs(segments)
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
        archive, css = _build_font_archive()
        dir_attr = "rtl" if rtl else "ltr"
        font_family = font_for(target_lang) if target_lang else "Noto Sans"

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
                # TWO RENDERING PATHS:
                #
                # (a) Rasterized page (OCR'd scan / slide deck / any page that
                #     is essentially a full-page image): render the whole page
                #     to a PIL image, fill each text bbox with its local
                #     background colour, and draw the translated text DIRECTLY
                #     on the image using PIL + a real TTF Arabic font +
                #     arabic_reshaper + python-bidi. This gives us:
                #       - zero "bg colour rectangle behind text" artefacts
                #         (we paint glyphs pixel-accurate, no HTML body bg)
                #       - true Arabic shaping + RTL (HTML rendering in pymupdf
                #         does NOT shape Arabic cluster-joining properly)
                #       - precise placement (no HTML layout drift)
                #       - reliable multi-line wrapping (we compute line breaks
                #         ourselves to fit the bbox)
                #     Then we replace the page's content with that single
                #     image. For RTL we flip the image horizontally and use
                #     mirrored bboxes when drawing text.
                #
                # (b) Native text page: the existing redact-then-htmlbox path
                #     which works fine for simple digital text PDFs.
                if _is_rasterized_page(page):
                    _render_page_via_pil(
                        page, page_segs, target_lang=target_lang, rtl=rtl,
                    )
                else:
                    fills = _sample_bbox_fills(page, page_segs)
                    for seg, fill in zip(page_segs, fills):
                        rect = pymupdf.Rect(seg.meta["bbox"])
                        page.add_redact_annot(rect, fill=fill)
                    page.apply_redactions(
                        images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                    )
                    page_w = float(page.rect.width)
                    if rtl and page_w > 0:
                        _apply_rtl_page_mirror(page, page_w)
                    for seg in page_segs:
                        bbox = seg.meta["bbox"]
                        if rtl and page_w > 0:
                            rect = pymupdf.Rect(
                                page_w - bbox[2],
                                bbox[1],
                                page_w - bbox[0],
                                bbox[3],
                            )
                        else:
                            rect = pymupdf.Rect(bbox)
                        _draw_segment(
                            page, rect, seg, font_family, dir_attr, archive, css,
                        )
            doc.save(str(out_path), **_SAVE_KW)
        finally:
            doc.close()
        return out_path


def _merge_vertical_paragraphs(segments: list[Segment]) -> list[Segment]:
    """Merge adjacent vertically-stacked segments on the same page that share
    left margin, font size, weight and style — they are almost always one
    OCR-split paragraph. Walks top-to-bottom per page; each seg either starts
    a new merged run or extends the previous one. Bullet items stay separate
    (a new segment starting with a bullet is never merged into its predecessor)
    but a bullet paragraph DOES absorb its own unbulleted continuation lines
    so multi-line bullets stay intact."""
    if len(segments) <= 1:
        return segments

    by_page: dict[int, list[Segment]] = {}
    for seg in segments:
        by_page.setdefault(seg.meta.get("page", 0), []).append(seg)

    merged_all: list[Segment] = []
    for _, page_segs in sorted(by_page.items()):
        page_segs.sort(
            key=lambda s: (
                s.meta.get("bbox", [0, 0, 0, 0])[1],
                s.meta.get("bbox", [0, 0, 0, 0])[0],
            ),
        )
        result: list[Segment] = []
        for seg in page_segs:
            if result and _should_merge_vertical(result[-1], seg):
                result[-1] = _merge_two_segments(result[-1], seg)
            else:
                result.append(seg)
        merged_all.extend(result)

    # Renumber IDs so the JSON payload sent to the LLM is compact and unique
    # after merges — the original b0..bN indexing has holes now.
    for i, seg in enumerate(merged_all):
        seg.id = f"b{i}"
    return merged_all


def _should_merge_vertical(prev: Segment, curr: Segment) -> bool:
    """True when ``curr`` is the visual continuation of ``prev`` — same column
    (matching left edge), tight vertical gap (≤ ~1.6 line heights), same font
    size/weight/style. Bullets and size jumps break a paragraph."""
    pb = prev.meta.get("bbox")
    cb = curr.meta.get("bbox")
    if not pb or not cb:
        return False
    if curr.meta.get("bullet"):
        return False
    p_size = float(prev.meta.get("font_size", 11.0) or 11.0)
    c_size = float(curr.meta.get("font_size", 11.0) or 11.0)
    avg_size = (p_size + c_size) / 2.0
    if abs(p_size - c_size) > max(1.0, 0.18 * avg_size):
        return False
    if bool(prev.meta.get("bold")) != bool(curr.meta.get("bold")):
        return False
    if bool(prev.meta.get("italic")) != bool(curr.meta.get("italic")):
        return False
    x_tol = max(3.0, 0.55 * avg_size)
    if abs(pb[0] - cb[0]) > x_tol:
        return False
    y_gap = cb[1] - pb[3]
    # Allow minor overlap for ascenders/descenders (-0.5 * size) and cap at
    # ~1.6 line heights so a full paragraph break (blank line) is preserved.
    if y_gap < -0.5 * avg_size or y_gap > 1.6 * avg_size:
        return False
    # Width should be in the same ballpark — a caption line under a body
    # paragraph tends to be dramatically narrower; keep them separate.
    prev_w = pb[2] - pb[0]
    curr_w = cb[2] - cb[0]
    if min(prev_w, curr_w) < 0.25 * max(prev_w, curr_w):
        return False
    return True


def _merge_two_segments(prev: Segment, curr: Segment) -> Segment:
    """Return a new Segment that is prev + curr as a single paragraph.
    bbox = union, text = prev + '\n' + curr, metadata taken from prev with
    weighted averages for font size/color."""
    pb = prev.meta["bbox"]
    cb = curr.meta["bbox"]
    union = [min(pb[0], cb[0]), min(pb[1], cb[1]), max(pb[2], cb[2]), max(pb[3], cb[3])]
    # Weight font size by each segment's text length so the merged size reflects
    # what most of the visible text was rendered at.
    p_len = max(1, len(prev.text))
    c_len = max(1, len(curr.text))
    p_size = float(prev.meta.get("font_size", 11.0) or 11.0)
    c_size = float(curr.meta.get("font_size", 11.0) or 11.0)
    merged_size = (p_size * p_len + c_size * c_len) / (p_len + c_len)
    merged_meta = dict(prev.meta)
    merged_meta["bbox"] = union
    merged_meta["font_size"] = merged_size
    # Keep prev's color/bullet/font_hint — same paragraph, prev's formatting wins.
    return Segment(
        id=prev.id,
        text=f"{prev.text}\n{curr.text}",
        meta=merged_meta,
    )


def _group_lines_by_row(lines: list[dict]) -> list[list[dict]]:
    """Split a block's lines into groups so each group is a vertical stack
    (a paragraph). Lines that share a y-row but sit side-by-side horizontally
    (badges, column headers) become separate groups, one per line, because
    they belong to visually distinct elements."""
    if len(lines) <= 1:
        return [list(lines)]

    indexed = sorted(
        enumerate(lines),
        key=lambda it: (
            it[1].get("bbox", (0, 0, 0, 0))[1],
            it[1].get("bbox", (0, 0, 0, 0))[0],
        ),
    )
    rows: list[list[dict]] = []
    for _, line in indexed:
        lb = line.get("bbox")
        if not lb:
            continue
        placed = False
        for row in rows:
            rb = row[0].get("bbox")
            if _y_overlap_fraction(lb, rb) >= 0.5:
                row.append(line)
                placed = True
                break
        if not placed:
            rows.append([line])

    groups: list[list[dict]] = []
    para_buffer: list[dict] = []
    for row in rows:
        if len(row) == 1:
            para_buffer.append(row[0])
        else:
            if para_buffer:
                groups.append(para_buffer)
                para_buffer = []
            row_sorted = sorted(row, key=lambda ln: ln.get("bbox", (0,))[0])
            for ln in row_sorted:
                groups.append([ln])
    if para_buffer:
        groups.append(para_buffer)
    return groups or [list(lines)]


def _y_overlap_fraction(a, b) -> float:
    if not a or not b:
        return 0.0
    ay0, ay1 = a[1], a[3]
    by0, by1 = b[1], b[3]
    inter = max(0.0, min(ay1, by1) - max(ay0, by0))
    shorter = max(1e-6, min(ay1 - ay0, by1 - by0))
    return inter / shorter


def _segment_from_lines(
    lines: list[dict], page_i: int, block_i: int, idx: int
) -> "Segment | None":
    """Build one Segment from a group of lines that form a single paragraph
    or a single side-by-side element. The bbox is the UNION of all line
    bboxes in the group, so it tightly matches the visual extent of this
    element even when the parent block lumped unrelated elements together."""
    text_parts: list[str] = []
    font_sizes: list[float] = []
    colors: list[int] = []
    fonts_seen: list[str] = []
    is_bold = False
    is_italic = False
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")

    for line in lines:
        lb = line.get("bbox")
        if lb:
            x0 = min(x0, lb[0])
            y0 = min(y0, lb[1])
            x1 = max(x1, lb[2])
            y1 = max(y1, lb[3])
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
            if flags & 16:
                is_bold = True
            if flags & 2:
                is_italic = True
        if line_parts:
            text_parts.append("".join(line_parts))

    text = "\n".join(text_parts).strip()
    if not text or x0 == float("inf"):
        return None

    bullet = ""
    m = _BULLET_RE.match(text)
    if m:
        bullet = m.group("bul")
        text = text[m.end():]

    avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else 11.0
    color_int = max(set(colors), key=colors.count) if colors else 0
    font_hint = fonts_seen[0] if fonts_seen else ""

    return Segment(
        id=f"b{idx}",
        text=text,
        meta={
            "page": page_i,
            "bbox": [x0, y0, x1, y1],
            "font_size": avg_size,
            "font_hint": font_hint,
            "bold": is_bold,
            "italic": is_italic,
            "color": color_int,
            "bullet": bullet,
            "block_idx": block_i,
        },
    )


def _sample_bbox_fills(
    page, segs, dpi: int = 96
) -> list[tuple[float, float, float]]:
    """Return a per-segment dominant background colour (0-1 float RGB) for
    each seg's bbox. Skips the full-page raster entirely for text-only pages
    (no images/drawings) because the sampled fill would just be white — huge
    win on large native PDFs where we'd otherwise rasterize every page for
    nothing. Renders the page once for image-bearing pages and crops from
    the PIL image for each bbox, so samples are taken before any redact
    annots exist on the page.

    Falls back to pure white for any bbox whose sampling fails.
    """
    default = (1.0, 1.0, 1.0)
    if not segs:
        return []
    if not _page_has_visual_content(page):
        return [default] * len(segs)
    try:
        from PIL import Image

        pix = page.get_pixmap(dpi=dpi, alpha=False, annots=False)
        page_img = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
        page_w = page.rect.width
        page_h = page.rect.height
        if page_w <= 0 or page_h <= 0:
            return [default] * len(segs)
        out: list[tuple[float, float, float]] = []
        for seg in segs:
            bbox = seg.meta.get("bbox")
            if not bbox:
                out.append(default)
                continue
            x0 = max(0, int(bbox[0] / page_w * page_img.width))
            y0 = max(0, int(bbox[1] / page_h * page_img.height))
            x1 = min(page_img.width, int(bbox[2] / page_w * page_img.width))
            y1 = min(page_img.height, int(bbox[3] / page_h * page_img.height))
            if x1 - x0 < 2 or y1 - y0 < 2:
                out.append(default)
                continue
            r, g, b = _dominant_bg_rgb(page_img, (x0, y0, x1, y1))
            out.append((r / 255.0, g / 255.0, b / 255.0))
        return out
    except Exception:
        return [default] * len(segs)


def _render_page_via_pil(page, segs, *, target_lang: str, rtl: bool, dpi: int = 200) -> None:
    """Render the page to a PIL image, paint translated text onto it using
    a real Arabic font + proper shaping, then REPLACE the page's content with
    that single image. This is the rasterized-page path — it completely
    sidesteps pymupdf's HTML renderer (which paints body backgrounds, ignores
    @font-face for Arabic, and mis-wraps multi-line text).

    Steps:
      1. Rasterize the whole page at ``dpi``.
      2. For every translated segment, fill its bbox with the local background
         colour (sampled from a thin band around the bbox that excludes every
         other text bbox on the page, so stacked words don't contaminate each
         other).
      3. For RTL targets, flip the resulting PIL image horizontally — every
         image, graphic, border, etc. mirrors around the page's vertical
         centre exactly the way a right-to-left reader expects.
      4. Draw each translated string with ``PIL.ImageDraw.text``, using a
         proper Arabic TTF font and arabic_reshaper + python-bidi so the
         letter-forms actually join correctly. Multi-line text is wrapped
         line-by-line to fit the bbox width; font size is shrunk if needed.
      5. Replace the page's /Contents with a single insert_image call that
         paints this final PIL image across the full page rect.
    """
    from io import BytesIO

    import numpy as np
    import pymupdf
    from PIL import Image, ImageDraw

    page_rect = page.rect
    page_w = float(page_rect.width)
    page_h = float(page_rect.height)
    if page_w <= 0 or page_h <= 0:
        return

    scale = dpi / 72.0
    pix = page.get_pixmap(dpi=dpi, alpha=False, annots=False)
    img = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
    arr = np.array(img, dtype=np.uint8)
    img_w, img_h = arr.shape[1], arr.shape[0]

    # Build mask of ALL text bboxes so the per-bbox fill doesn't sample
    # pixels that are actually another text region.
    text_mask = np.zeros((img_h, img_w), dtype=bool)
    scaled_boxes: list[tuple[Segment, tuple[int, int, int, int]]] = []
    for seg in segs:
        b = seg.meta.get("bbox")
        if not b:
            continue
        margin = max(1.0, float(seg.meta.get("font_size", 11.0)) * 0.15)
        ix0 = max(0, min(img_w, int(round((b[0] - margin) * scale))))
        iy0 = max(0, min(img_h, int(round((b[1] - margin) * scale))))
        ix1 = max(0, min(img_w, int(round((b[2] + margin) * scale))))
        iy1 = max(0, min(img_h, int(round((b[3] + margin) * scale))))
        if ix1 - ix0 < 2 or iy1 - iy0 < 2:
            continue
        scaled_boxes.append((seg, (ix0, iy0, ix1, iy1)))
        text_mask[iy0:iy1, ix0:ix1] = True

    # Fill each bbox with a colour sampled from its local, un-masked
    # surround (a 16-px ring outside the bbox, skipping pixels inside any
    # other text bbox). Falls back to the page-wide non-text median when
    # the local ring has no clean pixels.
    clean_pixels = arr[~text_mask]
    if clean_pixels.size:
        # Subsample for speed on huge rasters.
        if clean_pixels.shape[0] > 250_000:
            step = max(1, clean_pixels.shape[0] // 250_000)
            clean_pixels = clean_pixels[::step]
        page_bg = np.median(clean_pixels, axis=0).astype(np.uint8)
    else:
        page_bg = np.array([255, 255, 255], dtype=np.uint8)

    for _, (ix0, iy0, ix1, iy1) in scaled_boxes:
        local = _sample_local_bg(arr, text_mask, (ix0, iy0, ix1, iy1), ring=16)
        fill = local if local is not None else page_bg
        arr[iy0:iy1, ix0:ix1] = fill

    img = Image.fromarray(arr)

    # Full-page RTL mirror. After this step every image, graphic, stair
    # diagram etc. is flipped; we'll draw Arabic text at mirrored bboxes
    # so the translated words land where their LTR counterparts would after
    # a reading-order flip.
    if rtl:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    draw = ImageDraw.Draw(img)
    font_cache: dict[tuple[str, int, bool, bool], object] = {}
    for seg, (ix0, iy0, ix1, iy1) in scaled_boxes:
        text = (seg.translated or seg.text or "").strip()
        if not text:
            continue
        bullet = seg.meta.get("bullet", "")
        if bullet:
            text = f"{bullet} {text}"
        if rtl:
            # Mirror the bbox around the image's vertical centre line.
            mx0 = img_w - ix1
            mx1 = img_w - ix0
            ix0, ix1 = mx0, mx1

        color = _int_to_rgb_tuple(seg.meta.get("color", 0))
        font_size = max(8, int(round(float(seg.meta.get("font_size", 11.0)) * scale)))
        bold = bool(seg.meta.get("bold"))
        italic = bool(seg.meta.get("italic"))
        _draw_text_in_bbox(
            draw,
            text,
            (ix0, iy0, ix1, iy1),
            target_lang=target_lang,
            rtl=rtl,
            font_size=font_size,
            color=color,
            bold=bold,
            italic=italic,
            font_cache=font_cache,
        )

    # Re-encode and replace the page content with this single image. JPEG
    # for photo-like pages, PNG for line-art/slides, so the output PDF
    # stays reasonably sized.
    fmt = "JPEG" if _looks_like_photo(np.array(img)) else "PNG"
    buf = BytesIO()
    if fmt == "JPEG":
        img.save(buf, format="JPEG", quality=88, optimize=True, progressive=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    img_bytes = buf.getvalue()

    # Wipe existing content (removes the original image xrefs, the
    # invisible OCR text layer, any native glyphs) and paint the new image
    # across the whole page. Annotations are not on /Contents so they're
    # untouched; existing image xrefs become orphans cleaned up by
    # ``garbage=4`` at save time.
    try:
        doc = page.parent
        new_xref = doc.get_new_xref()
        doc.update_object(new_xref, "<<>>")
        doc.update_stream(new_xref, b"")
        page.set_contents(new_xref)
    except Exception as exc:
        log.warning("failed to clear page content on page %d: %s", page.number, exc)
    try:
        page.insert_image(page_rect, stream=img_bytes, keep_proportion=False)
    except Exception as exc:
        log.warning("insert_image failed on page %d: %s", page.number, exc)


def _int_to_rgb_tuple(color_int) -> tuple[int, int, int]:
    try:
        v = int(color_int)
    except Exception:
        v = 0
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def _sample_local_bg(arr, text_mask, box, ring: int = 16):
    """Median colour of pixels in a ``ring``-wide frame OUTSIDE ``box`` that
    are NOT inside any other text bbox. Returns a uint8 RGB array or None
    when no clean pixels are available."""
    import numpy as np

    h, w = arr.shape[:2]
    x0, y0, x1, y1 = box
    rx0 = max(0, x0 - ring)
    ry0 = max(0, y0 - ring)
    rx1 = min(w, x1 + ring)
    ry1 = min(h, y1 + ring)
    region = arr[ry0:ry1, rx0:rx1]
    mask = text_mask[ry0:ry1, rx0:rx1].copy()
    # Exclude the bbox itself from the ring so we're sampling OUTSIDE.
    ix0, iy0 = x0 - rx0, y0 - ry0
    ix1, iy1 = x1 - rx0, y1 - ry0
    mask[iy0:iy1, ix0:ix1] = True
    clean = region[~mask]
    if clean.size == 0:
        return None
    return np.median(clean, axis=0).astype(np.uint8)


def _load_pil_font(target_lang: str, size: int, bold: bool, italic: bool, cache: dict):
    """Load a TTF font suitable for ``target_lang`` at ``size`` pixels. Caches
    to avoid reloading per segment.

    Font priority for Arabic:
      1. Cairo (modern geometric sans — matches the look of contemporary
         Arabic UIs and paired Latin nicely)
      2. Noto Sans Arabic (clean modern Arabic sans, has a Bold variant)
      3. Noto Naskh Arabic (traditional Naskh fallback)
    """
    from PIL import ImageFont

    from ..config import settings
    from ..lang.rtl import normalize

    code = normalize(target_lang)
    arabic_codes = {"ar", "fa", "ur", "ckb", "ps", "sd"}
    hebrew_codes = {"he", "iw", "yi"}
    fonts_dir = settings.fonts_dir
    key = (code, size, bold, italic)
    if key in cache:
        return cache[key]
    candidates: list[str] = []
    if code in arabic_codes:
        if bold:
            candidates.extend([
                "NotoSansArabic-Bold.ttf",
                "Cairo-Variable.ttf",
                "NotoNaskhArabic-Bold.ttf",
            ])
        candidates.extend([
            "NotoSansArabic-Regular.ttf",
            "Cairo-Variable.ttf",
            "NotoNaskhArabic-Regular.ttf",
        ])
    elif code in hebrew_codes:
        candidates.append("NotoSansHebrew-Regular.ttf")
    candidates.append("NotoSans-Regular.ttf")
    for name in candidates:
        path = fonts_dir / name
        if path.exists():
            try:
                font = ImageFont.truetype(str(path), size)
                cache[key] = font
                return font
            except Exception:
                continue
    font = ImageFont.load_default()
    cache[key] = font
    return font


def _draw_text_in_bbox(
    draw,
    text: str,
    box: tuple[int, int, int, int],
    *,
    target_lang: str,
    rtl: bool,
    font_size: int,
    color: tuple[int, int, int],
    bold: bool,
    italic: bool,
    font_cache: dict,
) -> None:
    """Draw ``text`` into ``box`` using a proper language-appropriate font.

    For RTL targets we pass the RAW logical-order Unicode string straight
    into ``ImageDraw.text`` with ``direction="rtl"``. Modern Pillow ships
    with libraqm, which internally runs HarfBuzz + FriBiDi — the same
    pipeline that desktop browsers use — so Arabic letters join correctly
    and words appear in the right visual order. DO NOT pre-process the
    string with arabic_reshaper or python-bidi here: that would
    double-shape (presentation forms run through the shaper a second time)
    and double-reverse, producing the unreadable mangled output the user
    was seeing.
    """
    x0, y0, x1, y1 = box
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    direction = "rtl" if rtl else "ltr"
    features = ["kern", "liga"]
    lang = _raqm_lang_for(target_lang)

    forced_lines = text.split("\n")
    size_options = [font_size] + [max(8, font_size - step) for step in (2, 4, 6, 8, 10)]
    for size in size_options:
        font = _load_pil_font(target_lang, size, bold, italic, font_cache)
        wrapped_lines: list[str] = []
        fit = True
        for forced in forced_lines:
            segs = _wrap_line(draw, forced, font, box_w, direction, features, lang)
            if segs is None:
                fit = False
                break
            wrapped_lines.extend(segs)
        if not fit:
            continue
        line_h = _line_height(font)
        total_h = line_h * len(wrapped_lines)
        if total_h <= box_h or size == size_options[-1]:
            y = y0
            for line in wrapped_lines:
                tw = _text_width(draw, line, font, direction, features, lang)
                x = x1 - tw if rtl else x0
                try:
                    draw.text(
                        (x, y), line, font=font, fill=color,
                        direction=direction, features=features, language=lang,
                    )
                except Exception:
                    try:
                        draw.text((x, y), line, font=font, fill=color)
                    except Exception:
                        pass
                y += line_h
            return


def _raqm_lang_for(target_lang: str) -> str:
    """Map our language codes to BCP47 tags libraqm understands so Arabic
    glyph shaping picks the right regional forms."""
    from ..lang.rtl import normalize

    code = normalize(target_lang)
    return {
        "ar": "ar", "fa": "fa", "ur": "ur", "ps": "ps",
        "ckb": "ku", "sd": "sd",
        "he": "he", "iw": "he", "yi": "yi",
    }.get(code, code or "en")


def _line_height(font) -> int:
    try:
        ascent, descent = font.getmetrics()
        return int(round((ascent + descent) * 1.15))
    except Exception:
        return font.size if hasattr(font, "size") else 12


def _text_width(draw, text: str, font, direction: str, features, lang: str) -> int:
    try:
        bbox = draw.textbbox(
            (0, 0), text, font=font,
            direction=direction, features=features, language=lang,
        )
        return bbox[2] - bbox[0]
    except Exception:
        try:
            return int(font.getlength(text))
        except Exception:
            return len(text) * max(6, getattr(font, "size", 10) // 2)


def _wrap_line(draw, text: str, font, max_width: int, direction: str, features, lang: str):
    if not text.strip():
        return [""]
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = " ".join(cur + [word]) if cur else word
        if _text_width(draw, trial, font, direction, features, lang) <= max_width:
            cur.append(word)
            continue
        if not cur:
            return None
        lines.append(" ".join(cur))
        cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _apply_rtl_page_mirror(page, page_w: float) -> None:
    """Horizontally flip every PDF operator drawn on this page so far —
    images, graphics, redacted-text fills, inpainted raster patches — by
    wrapping the existing content stream in `q -1 0 0 1 W 0 cm ... Q`. The
    `-1 0 0 1 W 0` cm matrix is a reflection about x = W/2: each x-coord x
    becomes W − x, y-coords stay put. Because we wrap in save/restore (q/Q),
    content streams inserted AFTER this call — the translated text from
    `insert_htmlbox` below — execute in the un-flipped coordinate system, so
    Arabic text renders the right way up, not mirrored. The bbox coords we
    pass to `_draw_segment` for those inserts are themselves pre-mirrored so
    each translated paragraph lands where its LTR counterpart would sit after
    a full page flip. This gives a proper newspaper-style RTL layout without
    any rasterisation.

    Implementation note: PyMuPDF's public Page API does not expose a direct
    "prepend content stream" method, so we read the existing concatenated
    content, wrap it in q/Q + flip matrix, create a new object via
    ``get_new_xref`` + ``update_stream``, then point the page's /Contents at
    it via ``set_contents``. Subsequent ``insert_htmlbox`` calls simply
    append fresh content streams to /Contents, which execute after our Q.
    """
    try:
        page.wrap_contents()
    except Exception:
        pass
    try:
        existing = page.read_contents()
    except Exception as exc:
        log.warning("RTL page mirror: read_contents failed on page %d: %s", page.number, exc)
        return
    prefix = f"q\n-1 0 0 1 {page_w:.4f} 0 cm\n".encode()
    suffix = b"\nQ\n"
    new_content = prefix + (existing or b"") + suffix
    doc = page.parent
    try:
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, new_content)
        page.set_contents(xref)
    except Exception as exc:
        log.warning("RTL page mirror failed (page %d): %s", page.number, exc)


def _inpaint_rasterized_page(page, segs, doc) -> bool:
    """Rewrite every large background image on the page so each text bbox is
    filled in with pixels sampled from the surrounding background — not a
    visible coloured rectangle. Returns True when at least one image was
    updated; False when no usable image could be loaded (caller then falls
    back to solid-fill rects).

    Why inpaint EVERY large image: a typical OCR'd slide or scan has several
    overlapping full-page images (paper background + underlay + overlay), and
    the text glyphs are baked into whichever one sits on top in the PDF's
    painting order. We don't know which one ahead of time, so we inpaint all
    of them. The redundant work is cheap relative to the cost of leaving the
    original text showing through on whichever layer wasn't touched.

    Why the text-mask: in OCR output with tight line spacing the pixels just
    above/below one bbox are frequently INSIDE another bbox (i.e. ink from the
    next line of text). Sampling those as "background" pollutes the fill to a
    grey/black colour and produces the "visible bg-colour rectangle behind the
    text" the user was seeing. We build a union mask of every segment bbox on
    the page and force the per-bbox sampler to skip masked pixels so bands are
    drawn only from truly clean neighbourhoods, walking outward until enough
    unmasked pixels are found.
    """
    try:
        import pymupdf  # noqa: F401
        import numpy as np
        from PIL import Image
    except Exception:
        return False

    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return False
    if not infos:
        return False

    page_area = max(1.0, page.rect.width * page.rect.height)

    def _area(info):
        b = info.get("bbox") or (0, 0, 0, 0)
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    # Consider every image whose footprint covers at least 30% of the page —
    # that catches layered full-page slide backgrounds even when a smaller
    # "foreground image" sits on top. Skip stickers/icons whose text couldn't
    # be our target.
    targets = [
        info for info in sorted(infos, key=_area, reverse=True)
        if info.get("xref") and info.get("bbox") and _area(info) >= 0.30 * page_area
    ]
    if not targets:
        targets = [
            info for info in sorted(infos, key=_area, reverse=True)
            if info.get("xref") and info.get("bbox")
        ][:1]
    if not targets:
        return False

    any_painted = False
    for target in targets:
        xref = int(target["xref"])
        img_bbox = target["bbox"]
        ib_x0, ib_y0, ib_x1, ib_y1 = (float(v) for v in img_bbox)
        ib_w, ib_h = ib_x1 - ib_x0, ib_y1 - ib_y0
        if ib_w <= 0 or ib_h <= 0:
            continue

        try:
            pix = pymupdf.Pixmap(doc, xref)
        except Exception:
            continue
        try:
            if pix.alpha:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            elif pix.colorspace is None or pix.n < 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        except Exception:
            continue
        img_w, img_h = pix.w, pix.h
        if img_w <= 0 or img_h <= 0:
            continue
        try:
            img_pil = Image.frombytes("RGB", (img_w, img_h), pix.samples)
        except Exception:
            continue
        arr = np.array(img_pil, dtype=np.uint8)
        scale_x = img_w / ib_w
        scale_y = img_h / ib_h

        boxes: list[tuple[int, int, int, int]] = []
        text_mask = np.zeros((img_h, img_w), dtype=bool)
        for seg in segs:
            bbox = seg.meta.get("bbox")
            if not bbox:
                continue
            margin_px = max(1.0, float(seg.meta.get("font_size", 11.0)) * 0.15)
            bx0 = bbox[0] - margin_px
            by0 = bbox[1] - margin_px
            bx1 = bbox[2] + margin_px
            by1 = bbox[3] + margin_px
            ix0 = max(0, min(img_w, int(round((bx0 - ib_x0) * scale_x))))
            iy0 = max(0, min(img_h, int(round((by0 - ib_y0) * scale_y))))
            ix1 = max(0, min(img_w, int(round((bx1 - ib_x0) * scale_x))))
            iy1 = max(0, min(img_h, int(round((by1 - ib_y0) * scale_y))))
            if ix1 - ix0 < 2 or iy1 - iy0 < 2:
                continue
            boxes.append((ix0, iy0, ix1, iy1))
            text_mask[iy0:iy1, ix0:ix1] = True
        if not boxes:
            continue

        # Pre-compute the page's "clean background" colour — median of every
        # pixel that is NOT inside any text bbox. Used both as the skip test
        # (bboxes already matching this colour have no text to erase — e.g.
        # OCR'd PDFs where the text sits in a separate native layer, not in
        # the image) and as a last-resort fill when neighbouring bands turn
        # out to be dominated by surrounding graphics.
        clean_pixel_count = int((~text_mask).sum())
        if clean_pixel_count > 0:
            bg_sample = arr[~text_mask]
            # Subsample for speed on huge rasters.
            if bg_sample.shape[0] > 250_000:
                step = max(1, bg_sample.shape[0] // 250_000)
                bg_sample = bg_sample[::step]
            page_bg_median = np.median(bg_sample, axis=0).astype(np.uint8)
        else:
            page_bg_median = np.array([255, 255, 255], dtype=np.uint8)

        # Process top-to-bottom. After each bbox is inpainted the pixels inside
        # it are now CLEAN background, so we clear its entry in text_mask —
        # this lets a later, vertically-overlapping bbox (e.g. a stacked word
        # like "MODEL" under "WATERFALL") sample from the freshly-cleaned
        # region above it instead of walking further up into unrelated
        # coloured content and producing a greyish blob.
        boxes.sort(key=lambda b: (b[1], b[0]))
        for box in boxes:
            ix0, iy0, ix1, iy1 = box
            # SKIP if the bbox is already close to the page background —
            # nothing to inpaint. Avoids the "dark grey blob" regression where
            # we replaced already-clean pixels with a bad sampled fill.
            sample = arr[iy0:iy1, ix0:ix1, :3]
            if sample.size:
                delta = np.abs(
                    sample.astype(np.int16) - page_bg_median.astype(np.int16)
                ).mean()
                if delta < 12.0:
                    text_mask[iy0:iy1, ix0:ix1] = False
                    continue
            _inpaint_pixels_clean_band(
                arr, box, text_mask, fallback_color=page_bg_median,
            )
            text_mask[iy0:iy1, ix0:ix1] = False
        any_painted = True

        import io

        buf = io.BytesIO()
        fmt = "JPEG" if _looks_like_photo(arr) else "PNG"
        save_img = Image.fromarray(arr)
        try:
            if fmt == "JPEG":
                save_img.save(buf, format="JPEG", quality=85, optimize=True)
            else:
                save_img.save(buf, format="PNG", optimize=True)
        except Exception:
            continue
        new_bytes = buf.getvalue()
        if not new_bytes:
            continue
        try:
            page.replace_image(xref, stream=new_bytes)
        except Exception:
            continue

    return any_painted


def _inpaint_pixels_clean_band(
    arr,
    box,
    text_mask,
    band: int = 4,
    max_search_px: int = 40,
    fallback_color=None,
) -> None:
    """Inpaint pixels inside ``box`` by sampling truly CLEAN horizontal bands
    near the box — clean meaning outside the union of all text bboxes in
    ``text_mask``. Walks outward in both directions (up and down) until it
    collects at least ``band`` clean rows' worth of pixels per column. If the
    vertical search fails (box flush against image edge or text stacked all
    the way), falls back to horizontal clean bands to the left/right, then
    finally to the image's global background median. Never samples from
    inside another text bbox, which is the root-cause of the grey "bg colour"
    blob the caller previously saw behind translated text.
    """
    import numpy as np

    h, w = arr.shape[:2]
    x0, y0, x1, y1 = box
    x0 = max(0, min(w, x0))
    y0 = max(0, min(h, y0))
    x1 = max(0, min(w, x1))
    y1 = max(0, min(h, y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    box_h = y1 - y0
    box_w = x1 - x0
    slice_view = arr[y0:y1, x0:x1, :3]

    def _collect_clean(direction: int, y_start: int) -> "np.ndarray | None":
        """Walk pixel-by-pixel in ``direction`` (-1 = upward, +1 = downward)
        until we have gathered ``band`` clean rows PER COLUMN. Returns a
        (band, box_w, 3) float32 array of the collected pixels, or None if
        any column could not be filled within the search budget.

        Bailing on ANY unfilled column is critical: the previous "partial
        fill" strategy zeroed out missing columns which then pulled
        interpolated fills toward black for every row between clean and
        zeroed columns — producing the visible grey rectangle behind
        stacked words like WATERFALL / MODEL the user reported.
        """
        collected = np.zeros((band, box_w, 3), dtype=np.float32)
        filled = np.zeros(box_w, dtype=np.int32)
        steps = 0
        y = y_start
        while steps < max_search_px:
            if direction < 0:
                y -= 1
                if y < 0:
                    return None
            else:
                y += 1
                if y >= h:
                    return None
            row_mask = text_mask[y, x0:x1]
            row_pixels = arr[y, x0:x1, :3]
            open_cols = (~row_mask) & (filled < band)
            if open_cols.any():
                idx = np.where(open_cols)[0]
                slot = filled[idx]
                collected[slot, idx, :] = row_pixels[idx].astype(np.float32)
                filled[idx] = slot + 1
            if (filled >= band).all():
                return collected
            steps += 1
        return None

    top = _collect_clean(-1, y0)
    bot = _collect_clean(+1, y1 - 1)

    if top is not None and bot is not None:
        top_med = np.median(top, axis=0, keepdims=True)  # (1, box_w, 3)
        bot_med = np.median(bot, axis=0, keepdims=True)
        weights = np.linspace(0.0, 1.0, box_h, dtype=np.float32).reshape(-1, 1, 1)
        fill = top_med * (1.0 - weights) + bot_med * weights
        slice_view[...] = np.clip(fill, 0, 255).astype(np.uint8)
        return
    one_side = top if top is not None else bot
    if one_side is not None:
        med = np.median(one_side, axis=0, keepdims=True)
        slice_view[...] = np.clip(
            np.repeat(med, box_h, axis=0), 0, 255
        ).astype(np.uint8)
        return

    # Vertical search failed — try horizontal clean bands.
    def _collect_clean_h(direction: int, x_start: int) -> "np.ndarray | None":
        collected = np.zeros((box_h, band, 3), dtype=np.float32)
        filled = np.zeros(box_h, dtype=np.int32)
        steps = 0
        x = x_start
        while steps < max_search_px:
            if direction < 0:
                x -= 1
                if x < 0:
                    return None
            else:
                x += 1
                if x >= w:
                    return None
            col_mask = text_mask[y0:y1, x]
            col_pixels = arr[y0:y1, x, :3]
            open_rows = (~col_mask) & (filled < band)
            if open_rows.any():
                idx = np.where(open_rows)[0]
                slot = filled[idx]
                collected[idx, slot, :] = col_pixels[idx].astype(np.float32)
                filled[idx] = slot + 1
            if (filled >= band).all():
                return collected
            steps += 1
        if (filled >= max(1, band // 2)).mean() >= 0.5:
            return collected
        return None

    left = _collect_clean_h(-1, x0)
    right = _collect_clean_h(+1, x1 - 1)
    if left is not None and right is not None:
        left_med = np.median(left, axis=1, keepdims=True)
        right_med = np.median(right, axis=1, keepdims=True)
        weights = np.linspace(0.0, 1.0, box_w, dtype=np.float32).reshape(1, -1, 1)
        fill = left_med * (1.0 - weights) + right_med * weights
        slice_view[...] = np.clip(fill, 0, 255).astype(np.uint8)
        return
    one_side_h = left if left is not None else right
    if one_side_h is not None:
        med = np.median(one_side_h, axis=1, keepdims=True)
        slice_view[...] = np.clip(
            np.repeat(med, box_w, axis=1), 0, 255
        ).astype(np.uint8)
        return

    # Last resort: caller-supplied page background colour (cheap, consistent)
    # or, if that's missing, the global median over pixels NOT in any text
    # bbox. This keeps the fill close to the page's real background even when
    # the bbox is surrounded by heavy graphics / other text on every side.
    if fallback_color is not None:
        slice_view[...] = np.asarray(fallback_color, dtype=np.uint8)[None, None, :]
        return
    clean_pixels = arr[~text_mask][:, :3]
    if clean_pixels.size:
        slice_view[...] = np.median(clean_pixels, axis=0).astype(np.uint8)
    else:
        slice_view[...] = np.median(arr.reshape(-1, 3), axis=0).astype(np.uint8)


def _inpaint_pixels_vertical_gradient(arr, box, band: int = 6) -> None:
    """Replace pixels inside ``box`` (x0,y0,x1,y1) in-place on numpy RGB array
    ``arr``. Samples a thin horizontal band just ABOVE the box and another
    just BELOW, averages each band row-wise, then linearly interpolates between
    them across the box height. This preserves vertical gradients (common in
    OCR'd scans of photos, colored backgrounds, glossy brochures) instead of
    slapping down a single flat colour. When one side has no usable band
    (box flush with an image edge), the other side is repeated.
    """
    import numpy as np

    h, w = arr.shape[:2]
    x0, y0, x1, y1 = box
    x0 = max(0, min(w, x0))
    y0 = max(0, min(h, y0))
    x1 = max(0, min(w, x1))
    y1 = max(0, min(h, y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return

    top_y0 = max(0, y0 - band)
    bot_y1 = min(h, y1 + band)
    top_avail = y0 - top_y0
    bot_avail = bot_y1 - y1

    box_h = y1 - y0
    slice_view = arr[y0:y1, x0:x1, :3]

    def _strip_mean(src: "np.ndarray") -> "np.ndarray":
        # Median is more robust than mean against any residual glyph pixels in
        # the sampling band — one stray letter in the band doesn't taint the
        # fill toward grey.
        return np.median(src.astype(np.int16), axis=0, keepdims=True).astype(
            np.float32
        )

    if top_avail > 0 and bot_avail > 0:
        top_avg = _strip_mean(arr[top_y0:y0, x0:x1, :3])
        bot_avg = _strip_mean(arr[y1:bot_y1, x0:x1, :3])
        weights = np.linspace(0.0, 1.0, box_h, dtype=np.float32).reshape(-1, 1, 1)
        fill = top_avg * (1.0 - weights) + bot_avg * weights
        slice_view[...] = np.clip(fill, 0, 255).astype(np.uint8)
        return
    if top_avail > 0:
        top_avg = _strip_mean(arr[top_y0:y0, x0:x1, :3])
        slice_view[...] = np.clip(np.repeat(top_avg, box_h, axis=0), 0, 255).astype(
            np.uint8
        )
        return
    if bot_avail > 0:
        bot_avg = _strip_mean(arr[y1:bot_y1, x0:x1, :3])
        slice_view[...] = np.clip(np.repeat(bot_avg, box_h, axis=0), 0, 255).astype(
            np.uint8
        )
        return

    # Fallback: no top/bottom bands → use the image's left/right neighbours.
    left_x0 = max(0, x0 - band)
    right_x1 = min(w, x1 + band)
    left_avail = x0 - left_x0
    right_avail = right_x1 - x1
    box_w = x1 - x0
    if left_avail > 0 and right_avail > 0:
        left_avg = np.median(
            arr[y0:y1, left_x0:x0, :3].astype(np.int16), axis=1, keepdims=True
        ).astype(np.float32)
        right_avg = np.median(
            arr[y0:y1, x1:right_x1, :3].astype(np.int16), axis=1, keepdims=True
        ).astype(np.float32)
        weights = np.linspace(0.0, 1.0, box_w, dtype=np.float32).reshape(1, -1, 1)
        fill = left_avg * (1.0 - weights) + right_avg * weights
        slice_view[...] = np.clip(fill, 0, 255).astype(np.uint8)
        return
    # Last resort: uniform median of the entire image border band.
    slice_view[...] = np.median(arr.reshape(-1, 3), axis=0).astype(np.uint8)


def _looks_like_photo(arr) -> bool:
    """Rough detector: if the pixel palette is diverse enough, treat as a
    photo and prefer JPEG to keep the output PDF from ballooning. Line-art,
    charts, and screenshots stay PNG so edges don't get fuzzy."""
    try:
        import numpy as np

        h, w = arr.shape[:2]
        # Downsample to cap the cost on big rasters.
        step_y = max(1, h // 64)
        step_x = max(1, w // 64)
        sample = arr[::step_y, ::step_x].reshape(-1, 3)
        # Quantize and count unique tones.
        q = (sample // 16).astype(np.int32)
        keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
        unique = np.unique(keys).size
        return unique > 48
    except Exception:
        return False


def _is_rasterized_page(page) -> bool:
    """True when the page's visible content is essentially one full-page image
    (a force-OCR'd scan). Detected as a single image whose placement covers
    ≥90% of the page area. On such pages we overlay fill rects instead of
    redacting image pixels — the latter forces pymupdf to re-encode the page
    raster, producing multi-MB JPX output that stutters in browser viewers."""
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return False
    if not infos:
        return False
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    for info in infos:
        bbox = info.get("bbox")
        if not bbox:
            continue
        w = max(0.0, float(bbox[2]) - float(bbox[0]))
        h = max(0.0, float(bbox[3]) - float(bbox[1]))
        if w * h >= 0.9 * page_area:
            return True
    return False


def _page_has_visual_content(page) -> bool:
    """True when the page carries images or non-trivial vector drawings whose
    pixels might need sampling. Pure-text pages return False so the caller
    can skip the full-page raster."""
    try:
        if page.get_images(full=False):
            return True
    except Exception:
        pass
    try:
        if len(page.get_drawings()) > 3:
            return True
    except Exception:
        pass
    return False


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
    # text-align follows the direction so RTL text hugs the right edge of the
    # original bbox (matching the source's left-aligned LTR rendering).
    align = "right" if dir_attr == "rtl" else "left"
    # Force `background: transparent` on EVERY element the Story engine emits.
    # PyMuPDF's HTML renderer happily paints a white body background by
    # default, which was showing up as the "bg colour behind the text" the
    # user reported — especially obvious over photographed slide backgrounds.
    # Zeroing margins/padding also keeps the translated text visually aligned
    # with the original bbox instead of drifting inward by the renderer's
    # default 8px body margin.
    snippet = (
        f'<div dir="{dir_attr}" '
        f'style="color:{color_hex}; '
        f"background: transparent !important; "
        f"margin: 0; padding: 0; "
        f"font-family: '{font_family}', sans-serif; "
        f"font-size: {size:.1f}pt; "
        f"font-weight: {weight}; "
        f"font-style: {style}; "
        f"text-align: {align}; "
        f'line-height: 1.15;">'
        f"{escaped}</div>"
    )
    _safe_insert_htmlbox(
        page, rect, snippet, size, archive=archive, css=css, align=align,
    )


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
    # Global reset — forces every HTML snippet the Story engine renders to
    # have a transparent background and zero margins/padding. Without this
    # PyMuPDF paints a visible white rectangle behind each translated paragraph
    # (the HTML body's default background) on top of any coloured / photographic
    # page background we inpainted. The `!important` guards against inline
    # styles accidentally re-introducing a fill.
    reset_css = (
        "html, body, div, p, span, strong, em, b, i, br {"
        " background: transparent !important;"
        " background-color: transparent !important;"
        " margin: 0; padding: 0;"
        "}\n"
    )
    if not fonts_dir.exists():
        return None, reset_css
    archive = pymupdf.Archive()
    css_parts: list[str] = [reset_css]
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
    page, rect, html_snippet: str, size: float, archive=None, css: str = "",
    align: str = "left",
) -> None:
    """Insert HTML; on overflow, progressively shrink font-size, then widen the
    rect, then fall back to plain textbox. Returns silently either way.

    Widening direction is anchored to the text alignment: a left-aligned
    (LTR) box grows to the RIGHT so the translated text keeps its original
    left margin; a right-aligned (RTL) box grows to the LEFT so the Arabic
    keeps its original right margin. Symmetric widening (the previous
    behaviour) pulled text away from its original anchor and made
    translations look visibly shifted on the page.
    """
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
    # fall in here. Expand horizontally up to 2.5×, ANCHORING to the text
    # alignment edge so the translated text keeps its original margin.
    page_w = page.rect.width
    for factor in (1.4, 1.8, 2.5):
        new_w = min(page_w, (rect.x1 - rect.x0) * factor)
        if align == "right":
            # RTL: keep the right edge pinned; grow leftward.
            x1 = rect.x1
            x0 = max(0.0, x1 - new_w)
        else:
            # LTR: keep the left edge pinned; grow rightward.
            x0 = rect.x0
            x1 = min(page_w, x0 + new_w)
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
