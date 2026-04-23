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
        # ORDER MATTERS: merge first, then drop noise. Otherwise a valid
        # caption like "Sprint I" loses its "I" token to the noise drop
        # (single char, low font_size estimate from OCR) before the
        # merger gets a chance to glue it onto the preceding "Sprint".
        # The lost token then leaves an un-translated Latin glyph in
        # the source bitmap that the un-flip pass later restores at the
        # mirrored position, producing a stray English letter next to
        # the Arabic translation.
        segments = _merge_horizontal_tokens(segments)
        segments = _drop_ocr_noise(segments)
        segments = _merge_vertical_paragraphs(segments)
        # Wordmarks rendered as two stacked OCR lines (think the
        # "WATERFALL\nMODEL" two-line bug logo on a slide) come out
        # of OCR as separate segments because their bboxes barely
        # overlap and the paragraph merger treats them as distinct
        # paragraphs. Translating them in isolation produces two
        # disconnected Arabic words that visually collide on the
        # output. Merge such stacked short headings into one segment
        # so the LLM sees the compound phrase and the renderer treats
        # the result as a single stacked-title block.
        segments = _merge_stacked_wordmarks(segments)

        # Detect rotated text on rasterized pages. ocrmypdf's default page
        # segmentation only picks up ONE dominant orientation per page, so
        # a page mixing horizontal body text with a vertical wordmark (e.g.
        # the "AGILE" label on the DevOps sprint diagram) loses every
        # non-horizontal string — or worse, mis-OCRs it as garbage
        # horizontal text ("SCRUM" → "INMYDS") that sits on the text layer
        # pretending to be real content. Re-open the source PDF, render
        # each rasterized page to an image, rotate it 90°/270°, OCR the
        # rotated raster, and add any high-confidence rotated hits as new
        # Segments. Then cull existing NON-rotated segments that overlap
        # a rotated detection (the horizontal version is almost certainly
        # the garbage from ocrmypdf's mis-orientation).
        doc2 = pymupdf.open(str(src_path))
        try:
            next_id = len(segments)
            for page_i, page in enumerate(doc2):
                if not _is_rasterized_page(page):
                    continue
                rotated = _detect_rotated_text_on_page(page, next_id)
                if rotated:
                    segments = _cull_overlapping_horizontal(segments, rotated)
                    segments.extend(rotated)
                    next_id += len(rotated)
        finally:
            doc2.close()
        # Re-assign sequential IDs so the LLM JSON stays tidy.
        for i, seg in enumerate(segments):
            seg.id = f"b{i}"
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
            # Segments the LLM flagged as OCR noise (empty translation,
            # prompt rule 12) must be dropped here — their bboxes are
            # covering pictogram/icon pixels and must NOT be filled
            # (filling would erase the icon and leave a blank rect).
            segments = [
                s for s in segments
                if not s.meta.get("_ocr_noise")
            ]
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


def _merge_stacked_wordmarks(segments: list[Segment]) -> list[Segment]:
    """Combine vertically-overlapping (or nearly-touching) short
    headings that share a font-size class into one segment with an
    internal newline. Two ≤2-word segments whose bboxes vertically
    overlap or sit within ~25% of a line-height of each other and use a
    font ≥1.5× the page's body size are almost always a multi-line
    wordmark (a logo or hero title typeset on two lines). Translating
    them as one merged segment lets the LLM produce a coherent
    compound phrase instead of two disconnected words, and lets the
    renderer's stacked-title path place them with consistent spacing
    instead of relying on the source's overlapping bboxes.
    """
    if len(segments) <= 1:
        return segments

    by_page: dict[int, list[Segment]] = {}
    for seg in segments:
        by_page.setdefault(seg.meta.get("page", 0), []).append(seg)

    merged_all: list[Segment] = []
    for _, page_segs in sorted(by_page.items()):
        # Median font size for this page — anything 1.5× this size and
        # short is treated as a wordmark candidate.
        sizes = [
            float(s.meta.get("font_size", 11.0) or 11.0) for s in page_segs
        ]
        sizes_sorted = sorted(sizes)
        median = sizes_sorted[len(sizes_sorted) // 2] if sizes_sorted else 11.0
        big_threshold = max(20.0, 1.5 * median)
        page_segs_sorted = sorted(
            page_segs,
            key=lambda s: (
                s.meta.get("bbox", [0, 0, 0, 0])[1],
                s.meta.get("bbox", [0, 0, 0, 0])[0],
            ),
        )
        used = [False] * len(page_segs_sorted)
        for i, s in enumerate(page_segs_sorted):
            if used[i]:
                continue
            if s.meta.get("rotation"):
                merged_all.append(s)
                used[i] = True
                continue
            sb = s.meta.get("bbox")
            if not sb or float(s.meta.get("font_size", 11.0) or 11.0) < big_threshold:
                merged_all.append(s)
                used[i] = True
                continue
            if len(s.text.split()) > 2:
                merged_all.append(s)
                used[i] = True
                continue
            cur = s
            used[i] = True
            for j in range(i + 1, len(page_segs_sorted)):
                if used[j]:
                    continue
                nxt = page_segs_sorted[j]
                if nxt.meta.get("rotation"):
                    continue
                if len(nxt.text.split()) > 2:
                    continue
                if float(nxt.meta.get("font_size", 11.0) or 11.0) < big_threshold:
                    continue
                cb = nxt.meta.get("bbox")
                if not cb:
                    continue
                cur_bb = cur.meta["bbox"]
                line_h = max(
                    float(cur.meta.get("font_size", 11.0) or 11.0),
                    float(nxt.meta.get("font_size", 11.0) or 11.0),
                )
                # Vertical proximity: overlap or gap <= 0.4 × line height
                vertical_gap = cb[1] - cur_bb[3]
                if vertical_gap > 0.4 * line_h:
                    # rows are sorted by y, so no later seg can be closer
                    if vertical_gap > line_h:
                        break
                    continue
                # Horizontal proximity: their x-ranges should overlap
                # (stacked, not side-by-side).
                ox = min(cur_bb[2], cb[2]) - max(cur_bb[0], cb[0])
                if ox <= 0:
                    continue
                # Font size match within 25%
                avg = (
                    float(cur.meta.get("font_size", 11.0) or 11.0)
                    + float(nxt.meta.get("font_size", 11.0) or 11.0)
                ) / 2.0
                if abs(
                    float(cur.meta.get("font_size", 11.0) or 11.0)
                    - float(nxt.meta.get("font_size", 11.0) or 11.0)
                ) > 0.25 * avg:
                    continue
                # Merge: union bbox, text joined with \n.
                union = [
                    min(cur_bb[0], cb[0]),
                    min(cur_bb[1], cb[1]),
                    max(cur_bb[2], cb[2]),
                    max(cur_bb[3], cb[3]),
                ]
                new_meta = dict(cur.meta)
                new_meta["bbox"] = union
                new_meta["font_size"] = avg
                cur = Segment(
                    id=cur.id,
                    text=f"{cur.text}\n{nxt.text}",
                    meta=new_meta,
                )
                used[j] = True
            merged_all.append(cur)
    # Renumber IDs.
    for i, seg in enumerate(merged_all):
        seg.id = f"b{i}"
    return merged_all


def _drop_ocr_noise(segments: list[Segment]) -> list[Segment]:
    """Remove segments that are clearly OCR noise — tiny fragments whose
    text is dominated by punctuation/symbols, typically tesseract
    hallucinating letters in photo pixels. Keeps segments whose text
    has at least 2 alphanumeric characters OR looks like a legitimate
    short token (all-uppercase abbreviation, single digit with unit…).
    """
    out: list[Segment] = []
    for seg in segments:
        raw = seg.text or ""
        stripped = raw.strip()
        if not stripped:
            continue
        alnum = sum(1 for c in stripped if c.isalnum())
        # One alnum is OK for single digits ("2", "5%"); zero is always noise.
        if alnum == 0:
            continue
        # Heavy-symbol segments: mostly non-letters/non-digits,
        # length <= 6 — tesseract garbage on photo pixels.
        if len(stripped) <= 6 and alnum / len(stripped) < 0.5:
            continue
        # Short fragments containing bracket-like glyphs ( [] {} () <> |
        # \/ ) are almost always tesseract misreading pictogram/icon
        # pixels (e.g. a checkbox icon read as "B[]", a chevron as
        # "R]"). Real short text-captions don't use these characters.
        if len(stripped) <= 5 and any(c in "[]{}()<>|\\/" for c in stripped):
            continue
        # Tiny mostly-non-ASCII fragments are almost always tesseract
        # hallucinating glyphs in photo pixels (e.g. ``للري`` / ``كرا``
        # appearing inside a stock photo of monitors). Drop them so they
        # don't get translated and rendered as floating text.
        if len(stripped) <= 6:
            ascii_alpha = sum(1 for c in stripped if c.isascii() and c.isalpha())
            if ascii_alpha == 0:
                continue
        # Single-letter segments at sub-readable font sizes are OCR
        # imagining a glyph in a graphic detail; drop.
        font_pt = float(seg.meta.get("font_size", 11.0) or 11.0)
        if len(stripped) <= 1 and font_pt < 8.0:
            continue
        out.append(seg)
    return out


def _merge_horizontal_tokens(segments: list[Segment]) -> list[Segment]:
    """Merge horizontally-adjacent tiny tokens on the same baseline into
    one segment. OCR frequently splits short captions like ``Sprint I``
    into two separate blocks (``Sprint`` + ``I``) which later get
    translated independently and land at subtly-different mirrored
    positions — producing the "floating الأول above the paragraph" bug
    the reviewer flagged on the agile-iteration page.

    A pair is merged when:
      * same page and same row (y-ranges overlap >=70%),
      * horizontal gap <= 0.6 × max font size (one-space worth),
      * matching font size/weight/style,
      * neither has a bullet leading marker.
    """
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
        used = [False] * len(page_segs)
        for i, s in enumerate(page_segs):
            if used[i]:
                continue
            sb = s.meta.get("bbox")
            if not sb:
                merged_all.append(s)
                used[i] = True
                continue
            # Try to absorb the next segment on the same row.
            cur = s
            used[i] = True
            for j in range(i + 1, len(page_segs)):
                if used[j]:
                    continue
                nxt = page_segs[j]
                nb = nxt.meta.get("bbox")
                if not nb:
                    continue
                if _y_overlap_fraction(sb, nb) < 0.7:
                    # rows are sorted by y, so once row changes we're done
                    if nb[1] > sb[3]:
                        break
                    continue
                avg_size = (
                    float(cur.meta.get("font_size", 11.0))
                    + float(nxt.meta.get("font_size", 11.0))
                ) / 2.0
                gap = nb[0] - sb[2]
                if gap < -2 or gap > max(6.0, 0.6 * avg_size):
                    continue
                if bool(cur.meta.get("bold")) != bool(nxt.meta.get("bold")):
                    continue
                if bool(cur.meta.get("italic")) != bool(nxt.meta.get("italic")):
                    continue
                if nxt.meta.get("bullet"):
                    continue
                # Skip the font-size match when EITHER side is a tiny
                # token (≤2 characters) — tesseract estimates font size
                # from glyph height, which is unreliable for thin
                # letters like "I" (7pt) next to "Sprint" (17pt). The
                # x-gap and same-row tests already guard against bogus
                # joins.
                if (
                    len(cur.text.strip()) > 2
                    and len(nxt.text.strip()) > 2
                    and abs(
                        float(cur.meta.get("font_size", 11.0))
                        - float(nxt.meta.get("font_size", 11.0))
                    ) > max(1.0, 0.18 * avg_size)
                ):
                    continue
                union = [
                    min(sb[0], nb[0]), min(sb[1], nb[1]),
                    max(sb[2], nb[2]), max(sb[3], nb[3]),
                ]
                new_meta = dict(cur.meta)
                new_meta["bbox"] = union
                cur = Segment(
                    id=cur.id,
                    text=f"{cur.text} {nxt.text}",
                    meta=new_meta,
                )
                sb = union
                used[j] = True
            merged_all.append(cur)
    return merged_all


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
    # Allow minor overlap for ascenders/descenders (-0.5 * size). The upper
    # cap separates three cases:
    #   * y_gap ≤ ~0.4 * size — standard line-spacing inside a paragraph;
    #     this is the ONLY case we want to merge. OCR routinely splits a
    #     single paragraph into per-line segments that sit this tight.
    #   * y_gap ≈ 0.6-1.1 * size — bulleted list items with one blank-line
    #     worth of separation. Merging these destroys the bullet list,
    #     producing a run-on paragraph with no structure. Must reject.
    #   * y_gap ≥ ~1.3 * size — paragraph break, clearly rejected.
    # A cutoff of 0.55 preserves paragraph continuation while keeping
    # bulleted items separate. We also reject when prev ends in a
    # sentence-terminator ('.', '!', '?', '؟', '۔') followed by a gap
    # ≥ 0.4 * size — that's a paragraph break even without a blank line.
    if y_gap < -0.5 * avg_size or y_gap > 0.55 * avg_size:
        return False
    prev_text = (prev.text or "").rstrip()
    if prev_text and prev_text[-1] in ".!?؟。۔" and y_gap > 0.4 * avg_size:
        return False
    # A paragraph's LAST line is routinely much narrower than the
    # preceding full-width lines — "users." trailing "reached
    # production…" is still the same paragraph, just a widow. Only
    # reject when the PREVIOUS segment is dramatically narrower than
    # the CURRENT one (prev is a caption, curr is a wide body about to
    # start a new paragraph block). Allowing curr << prev restores the
    # stray-last-word merge that showed up on p2 as a floating
    # horizontal strip between two Arabic paragraphs.
    prev_w = pb[2] - pb[0]
    curr_w = cb[2] - cb[0]
    if prev_w < 0.25 * curr_w:
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


def _cull_overlapping_horizontal(segments, rotated_segs):
    """Remove horizontal (rotation=0) segments whose bbox is majority-
    contained within any rotated segment's bbox. Rotated OCR passes
    stricter filters than ocrmypdf's default horizontal pass, so when
    both produce hits for the same region the rotated one is kept."""
    if not rotated_segs:
        return segments
    keep = []
    for seg in segments:
        if seg.meta.get("rotation"):
            keep.append(seg)
            continue
        b = seg.meta.get("bbox")
        if not b:
            keep.append(seg)
            continue
        b_area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
        dominated = False
        for r in rotated_segs:
            if r.meta.get("page") != seg.meta.get("page"):
                continue
            rb = r.meta["bbox"]
            ix0 = max(b[0], rb[0]); iy0 = max(b[1], rb[1])
            ix1 = min(b[2], rb[2]); iy1 = min(b[3], rb[3])
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            if inter / b_area > 0.5:
                dominated = True
                break
        if not dominated:
            keep.append(seg)
    return keep


def _detect_rotated_text_on_page(page, start_id: int) -> list:
    """OCR the page image at 90° and 270° rotations to catch vertical text
    that horizontal OCR missed (labels on diagrams, marginalia, tall brand
    marks). Returns new Segments with ``meta['rotation']`` set to 90 or 270
    and bbox in PDF point coords aligned to the source page orientation.

    90° means the source text is rotated 90° counter-clockwise from
    horizontal — i.e. reads bottom-to-top (the usual spine-direction label
    on Western book covers). 270° means clockwise — reads top-to-bottom.
    """
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return []

    page_w_pt = float(page.rect.width)
    page_h_pt = float(page.rect.height)
    if page_w_pt <= 0 or page_h_pt <= 0:
        return []
    dpi = 200
    scale = dpi / 72.0
    try:
        pix = page.get_pixmap(dpi=dpi, alpha=False, annots=False)
        base = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
    except Exception:
        return []
    W, H = base.size  # original image dimensions in pixels

    # Note: we deliberately do NOT filter against existing horizontal
    # segments here. ocrmypdf's default OCR pass runs with a single
    # horizontal orientation, so it often produces GARBAGE text for
    # rotated glyphs (e.g. "SCRUM" → "INMYDS") that sits in the same
    # bbox. Rejecting a rotated hit because that garbage exists would
    # discard the correct detection. Instead, the caller post-culls
    # non-rotated segments that overlap a rotated one.

    results: list[Segment] = []
    counter = start_id
    for angle in (90, 270):
        rotated = base.rotate(angle, expand=True, fillcolor=(255, 255, 255))
        try:
            data = pytesseract.image_to_data(
                rotated, config="--psm 11", output_type=pytesseract.Output.DICT,
            )
        except Exception:
            continue
        # Group word-level detections into line-level clusters so "AGILE"
        # spanning multiple word boxes still ends up as a single Segment.
        lines_map: dict[tuple, list[int]] = {}
        for i, txt in enumerate(data.get("text", [])):
            if not txt or not txt.strip():
                continue
            try:
                conf = float(data.get("conf", [-1])[i])
            except Exception:
                conf = -1.0
            if conf < 78:
                continue
            stripped = txt.strip()
            if len(stripped) < 3:
                continue
            alnum = sum(1 for c in stripped if c.isalnum())
            if alnum < 3 or alnum / len(stripped) < 0.7:
                continue
            key = (
                int(data["block_num"][i]),
                int(data["par_num"][i]),
                int(data["line_num"][i]),
            )
            lines_map.setdefault(key, []).append(i)
        for indices in lines_map.values():
            words: list[str] = []
            ru0 = rv0 = float("inf")
            ru1 = rv1 = float("-inf")
            for i in indices:
                words.append(data["text"][i].strip())
                ru_, rv_ = int(data["left"][i]), int(data["top"][i])
                rw_, rh_ = int(data["width"][i]), int(data["height"][i])
                ru0, rv0 = min(ru0, ru_), min(rv0, rv_)
                ru1, rv1 = max(ru1, ru_ + rw_), max(rv1, rv_ + rh_)
            if ru0 == float("inf"):
                continue
            text = " ".join(words).strip()
            if len(text) < 3:
                continue
            # Un-rotate bbox from rotated-image coords to base-image coords.
            # Rotation reference (empirically verified):
            #   90° CCW : (u,v) → (W-1-v, u)    (W = base width before rotate)
            #   270° CCW: (u,v) → (v, H-1-u)    (H = base height before rotate)
            if angle == 90:
                bx0 = W - rv1
                by0 = ru0
                bx1 = W - rv0
                by1 = ru1
            else:
                bx0 = rv0
                by0 = H - ru1
                bx1 = rv1
                by1 = H - ru0
            bx0 = max(0, bx0); by0 = max(0, by0)
            bx1 = min(W, bx1); by1 = min(H, by1)
            w_px = bx1 - bx0
            h_px = by1 - by0
            if w_px < 6 or h_px < 6:
                continue
            # Rotated 90°/270° text must be TALLER than wide in page
            # coordinates. Reject any hit whose bbox is horizontal —
            # those are almost always tesseract mis-parses of horizontal
            # content reached through the rotation path.
            if h_px < 1.3 * w_px:
                continue
            # Convert to PDF point coordinates.
            bbox_pt = [bx0 / scale, by0 / scale, bx1 / scale, by1 / scale]
            # Estimate source font size in points from the SHORT dimension
            # of the rotated text (which was the line height before
            # rotation). A generous 0.9× of that height approximates the
            # glyph size in points.
            short_px = min(by1 - by0, bx1 - bx0)
            font_pt = max(8.0, (short_px / scale) * 0.9)
            # Label convention: ``rotation`` is how far CCW the SOURCE text
            # is from horizontal. We found it by rotating the image by
            # ``angle`` CCW; the source orientation that un-rotates to
            # horizontal is the opposite — ``360 - angle``.
            source_rotation = (360 - angle) % 360
            results.append(Segment(
                id=f"b{counter}",
                text=text,
                meta={
                    "page": page.number,
                    "bbox": bbox_pt,
                    "font_size": font_pt,
                    "rotation": source_rotation,
                    "bullet": "",
                    "bold": False,
                    "italic": False,
                    "color": 0,
                    "block_idx": -1,
                },
            ))
            counter += 1
    return results


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

    box_fills: list[np.ndarray] = []
    for _, (ix0, iy0, ix1, iy1) in scaled_boxes:
        local = _sample_local_bg(arr, text_mask, (ix0, iy0, ix1, iy1), ring=16)
        fill = local if local is not None else page_bg
        arr[iy0:iy1, ix0:ix1] = fill
        box_fills.append(fill)

    # BEFORE flipping, capture every Latin-text-shaped region on the page
    # — both on plain background and inside illustrations/diagrams — so
    # that after the full-page flip we can paste these patches back
    # UN-FLIPPED at their mirrored position. Rationale:
    #   * The page flip is the right behaviour for image GEOMETRY: stair
    #     diagrams, infinity loops, gear illustrations mirror so visual
    #     flow matches right-to-left reading (the user wants this).
    #   * But Latin TEXT glyphs and brand LOGOS baked into those images
    #     must NOT mirror — "DEV" flipping to "VED", "Build" to "dliuB",
    #     "AGILE" to "ELIGA", or a docker/git/aws logo reversing left-to-
    #     right all destroy readability and brand recognition.
    # The solution is selective un-flip: whole-page flip for geometry,
    # then paste un-flipped text patches on top at the mirrored coord,
    # so each Latin label or logo stays readable in place while its
    # surrounding illustration mirrors around it.
    #
    # ``_collect_baked_latin_regions`` already runs OCR + a morphological
    # text-line detector and returns text-shaped rectangles anywhere on
    # the page. We do NOT pass image_regions as protected here — we WANT
    # to un-flip Latin text INSIDE images too.
    baked_latin: list[tuple[int, int, int, int, "Image.Image"]] = []
    if rtl:
        rotated_bboxes = [
            box for seg, box in scaled_boxes
            if int(seg.meta.get("rotation", 0) or 0)
        ]
        baked_latin = _collect_baked_latin_regions(
            arr, text_mask, protected_bboxes=rotated_bboxes,
        )

    img = Image.fromarray(arr)

    # Full-page RTL mirror. After this step every image, graphic, stair
    # diagram etc. is flipped; we'll draw Arabic text at mirrored bboxes
    # so the translated words land where their LTR counterparts would after
    # a reading-order flip.
    if rtl:
        # Full-page horizontal flip — images, graphics, and illustrations
        # all mirror for right-to-left visual flow.
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
        # Selective un-flip of Latin text/logos. The full-page flip
        # reversed every Latin glyph; paste the pre-flip patches back
        # un-flipped at their mirrored position so labels like "DEV",
        # "OPS", "Build", "Design", "AGILE", "docker", brand logos,
        # etc. stay readable on top of the mirrored illustration.
        for rx0, ry0, rx1, ry1, patch in baked_latin:
            mx0 = img_w - rx1
            img.paste(patch, (mx0, ry0))

    draw = ImageDraw.Draw(img)
    font_cache: dict[tuple[str, int, bool, bool], object] = {}
    for seg, (ix0, iy0, ix1, iy1) in scaled_boxes:
        text = (seg.translated or seg.text or "").strip()
        if not text:
            continue
        bullet = seg.meta.get("bullet", "")
        if bullet:
            text = f"{bullet} {text}"
        rotation = int(seg.meta.get("rotation", 0) or 0)
        if rtl:
            # Mirror the bbox around the image's vertical centre line. A
            # horizontal page flip also flips rotation direction: 90° CCW
            # becomes 270° CCW (and vice versa) when viewed in the mirror.
            mx0 = img_w - ix1
            mx1 = img_w - ix0
            ix0, ix1 = mx0, mx1
            if rotation == 90:
                rotation = 270
            elif rotation == 270:
                rotation = 90

        color = _int_to_rgb_tuple(seg.meta.get("color", 0))
        font_size = max(8, int(round(float(seg.meta.get("font_size", 11.0)) * scale)))
        bold = bool(seg.meta.get("bold"))
        italic = bool(seg.meta.get("italic"))
        if rotation in (90, 270):
            _draw_rotated_text_in_bbox(
                img, text, (ix0, iy0, ix1, iy1),
                target_lang=target_lang, rtl=rtl,
                font_size=font_size, color=color,
                bold=bold, italic=italic,
                font_cache=font_cache, rotation=rotation,
            )
            # Refresh Draw handle — pasting into img can invalidate the
            # internal draw reference on some Pillow versions.
            draw = ImageDraw.Draw(img)
        else:
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


def _collect_baked_latin_regions(arr, text_mask, protected_bboxes=None):
    """Return rectangles (pixel coords) that contain baked-in LAYOUT-TEXT
    (any text-shaped pixel blob) NOT already covered by an existing
    translated segment. Used by the PIL render path to preserve baked-in
    labels across an RTL page flip: we paste the UN-flipped crop back at
    the mirrored position so the label stays readable.

    Combines two detectors to maximise coverage:
      - tesseract OCR on the page — catches legible Latin words that
        ocrmypdf missed.
      - morphological "text-like blob" detection on an adaptive binary
        threshold of the page — catches stylised circle-interior labels
        (the "Build"/"Design"/"Analyze" labels inside sprint diagrams)
        that OCR cannot read but which are still text-shaped pixels that
        would appear as reversed glyphs after a page flip.

    The morphological pass is essential for diagram-heavy slides where
    labels are rendered in ornamental fonts over coloured backgrounds
    — OCR alone finds nothing, but the pixels are clearly text-shaped
    and look awful when mirrored.
    """
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        pytesseract = None
    import numpy as np
    try:
        import cv2
    except Exception:
        cv2 = None

    h, w = arr.shape[:2]
    candidates: list[tuple[int, int, int, int]] = []

    if pytesseract is not None:
        try:
            img = Image.fromarray(arr)
            data = pytesseract.image_to_data(
                img, lang="eng", output_type=pytesseract.Output.DICT,
            )
        except Exception:
            data = None
        if data is not None:
            for i, txt in enumerate(data.get("text", [])):
                if not txt or not txt.strip():
                    continue
                try:
                    conf = float(data.get("conf", [-1])[i])
                except Exception:
                    conf = -1.0
                if conf < 55:
                    continue
                stripped = txt.strip()
                alpha_ascii = sum(1 for c in stripped if c.isascii() and c.isalpha())
                if alpha_ascii < 2:
                    continue
                try:
                    x = int(data["left"][i]); y = int(data["top"][i])
                    rw = int(data["width"][i]); rh = int(data["height"][i])
                except Exception:
                    continue
                if rw < 6 or rh < 6:
                    continue
                candidates.append((x, y, x + rw, y + rh))

    if cv2 is not None:
        # Morphological text-line detection: adaptive binary threshold
        # then horizontal dilation merges letters into word-sized blobs
        # that we can filter by aspect ratio.
        try:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            th = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY_INV, 15, 5,
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (10, 2))
            dilated = cv2.dilate(th, kernel, iterations=2)
            n, _, stats, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)
            for i in range(1, n):
                x, y, rw, rh, _area = stats[i]
                if rw < 15 or rh < 8:
                    continue
                if rw > 400 or rh > 60:
                    continue
                ar = rw / max(1, rh)
                if ar < 1.0 or ar > 15:
                    continue
                candidates.append((x, y, x + rw, y + rh))
        except Exception:
            pass

    regions: list[tuple[int, int, int, int, "Image.Image"]] = []
    from PIL import Image as _Image
    seen: list[tuple[int, int, int, int]] = []
    for (x0, y0, x1, y1) in candidates:
        x0 = max(0, x0); y0 = max(0, y0)
        x1 = min(w, x1); y1 = min(h, y1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        sub = text_mask[y0:y1, x0:x1]
        if sub.size and float(sub.sum()) / sub.size >= 0.5:
            continue
        # Protected bboxes: rotated-text regions that will be redrawn
        # with translated Arabic. Never un-flip the source pixels
        # there, otherwise the Latin original shows through any
        # transparent area of the rotated-text alpha layer.
        skip_protected = False
        if protected_bboxes:
            for px0, py0, px1, py1 in protected_bboxes:
                ix0 = max(x0, px0); iy0 = max(y0, py0)
                ix1 = min(x1, px1); iy1 = min(y1, py1)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                inter = (ix1 - ix0) * (iy1 - iy0)
                area = (x1 - x0) * (y1 - y0)
                if inter / max(1, area) > 0.25:
                    skip_protected = True
                    break
        if skip_protected:
            continue
        # De-dupe overlapping candidates — OCR and morph often hit the
        # same blob.
        overlap = False
        for sx0, sy0, sx1, sy1 in seen:
            ix0 = max(x0, sx0); iy0 = max(y0, sy0)
            ix1 = min(x1, sx1); iy1 = min(y1, sy1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            a = (x1 - x0) * (y1 - y0)
            b = (sx1 - sx0) * (sy1 - sy0)
            if inter / min(a, b) > 0.6:
                overlap = True
                break
        if overlap:
            continue
        seen.append((x0, y0, x1, y1))
        pad = max(2, int((y1 - y0) * 0.15))
        x0p = max(0, x0 - pad); y0p = max(0, y0 - pad)
        x1p = min(w, x1 + pad); y1p = min(h, y1 + pad)
        patch = _Image.fromarray(arr[y0p:y1p, x0p:x1p].copy())
        regions.append((x0p, y0p, x1p, y1p, patch))
    return regions


def _collect_image_regions(page, arr, scale: float, text_mask=None):
    """Return pixel-space rectangles (and un-flipped pixel copies) for
    photo/diagram regions on the page — coloured raster content likely
    to hold baked-in Latin text (people, charts, diagrams, logos). Used
    by the PIL render path to paste these regions back UN-FLIPPED at
    their mirrored position after the page-wide RTL flip, so any
    English labels inside photos stay readable regardless of style.

    Detection strategy (in order of preference):
      1. Query the PDF for raster-image xobject bboxes. This is
         precise when available, but ocrmypdf and other PDF rewriters
         often collapse/flatten the xobject graph, returning empty or
         only-full-page results — useless for our purposes.
      2. Fall back to content-based detection on the rasterized page:
         large contiguous regions of coloured/textured pixels (i.e.
         not near-white page background) are flagged as "photo
         regions". Connected components of the saturation/chroma mask
         give us the bounding boxes. This works on any rendered page
         independent of PDF structure.

    Regions that cover >=85% of the page area are skipped (they are
    the page background, not a content image). Regions smaller than
    ~0.5in on either side are skipped (decorative icons — mirroring
    is acceptable and often correct for directional arrows).
    """
    import numpy as np
    try:
        import cv2
    except Exception:
        cv2 = None

    h, w = arr.shape[:2]
    page_area = max(1, w * h)
    min_side = max(40, int(round(0.5 * 72 * scale)))
    seen_boxes: list[tuple[int, int, int, int]] = []
    candidates: list[tuple[int, int, int, int]] = []

    # Strategy 1: use PDF image-xobject bboxes when available.
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        try:
            infos = page.get_image_info()
        except Exception:
            infos = []
    for info in infos or []:
        bbox = info.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        try:
            bx0, by0, bx1, by1 = (float(v) for v in bbox)
        except Exception:
            continue
        ix0 = max(0, int(round(bx0 * scale)))
        iy0 = max(0, int(round(by0 * scale)))
        ix1 = min(w, int(round(bx1 * scale)))
        iy1 = min(h, int(round(by1 * scale)))
        if ix1 - ix0 < min_side or iy1 - iy0 < min_side:
            continue
        area = (ix1 - ix0) * (iy1 - iy0)
        if area / page_area >= 0.85:
            continue
        candidates.append((ix0, iy0, ix1, iy1))

    # Strategy 2: content-based photo-region detection on arr. Run
    # even when strategy 1 returned candidates — ocrmypdf sometimes
    # preserves metadata for SOME images but drops others on the same
    # page, so we merge both result sets.
    if cv2 is not None:
        try:
            hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
            sat = hsv[:, :, 1]
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            # Photo mask: coloured pixels (sat >= 30) OR mid-grey
            # pixels (not near-white page bg AND not near-black text).
            # Near-white page bg is gray >= 230; near-black text is
            # gray <= 60. The middle band + coloured pixels = photo.
            photo_mask = (sat >= 30) | ((gray >= 60) & (gray <= 220))
            # Exclude translated-text-bbox regions so filled bg doesn't
            # get included as "photo".
            if text_mask is not None:
                photo_mask = photo_mask & (~text_mask)
            pm = photo_mask.astype(np.uint8) * 255
            # Dilate generously to merge nearby photo-like patches into
            # coherent image regions.
            k = max(12, int(round(0.25 * 72 * scale)))
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            pm = cv2.morphologyEx(pm, cv2.MORPH_CLOSE, kernel)
            # Drop small isolated blobs (text runs, icons).
            min_blob_side = max(min_side, int(round(0.8 * 72 * scale)))
            n, _, stats, _ = cv2.connectedComponentsWithStats(pm, connectivity=8)
            for i in range(1, n):
                x, y, rw, rh, _a = stats[i]
                if rw < min_blob_side or rh < min_blob_side:
                    continue
                area = rw * rh
                if area / page_area >= 0.85:
                    continue
                candidates.append((int(x), int(y), int(x + rw), int(y + rh)))
        except Exception:
            pass

    out: list = []
    for (ix0, iy0, ix1, iy1) in candidates:
        ix0 = max(0, ix0); iy0 = max(0, iy0)
        ix1 = min(w, ix1); iy1 = min(h, iy1)
        if ix1 - ix0 < min_side or iy1 - iy0 < min_side:
            continue
        # De-dupe — accept only candidates not overlapping an
        # already-accepted region by more than 60% of the smaller one.
        dup = False
        for sx0, sy0, sx1, sy1 in seen_boxes:
            jx0 = max(ix0, sx0); jy0 = max(iy0, sy0)
            jx1 = min(ix1, sx1); jy1 = min(iy1, sy1)
            if jx1 <= jx0 or jy1 <= jy0:
                continue
            inter = (jx1 - jx0) * (jy1 - jy0)
            a = (ix1 - ix0) * (iy1 - iy0)
            b = (sx1 - sx0) * (sy1 - sy0)
            if inter / max(1, min(a, b)) > 0.6:
                dup = True
                break
        if dup:
            continue
        patch = arr[iy0:iy1, ix0:ix1].copy()
        # Reject flat layout elements (callout backgrounds, dividers,
        # rounded banner rectangles) that the content-mask grabbed along
        # with actual photos. Photos/diagrams have either meaningful
        # colour saturation or high tonal variation; a uniform gray
        # rectangle has neither. Un-flipping those regions is what
        # creates phantom empty-box duplicates next to the real mirrored
        # callout. Keep the check cheap — we already ran opencv above.
        if cv2 is not None:
            try:
                patch_hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
                sat_mean = float(patch_hsv[:, :, 1].mean())
                patch_gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
                gray_std = float(patch_gray.std())
                if sat_mean < 12.0 and gray_std < 30.0:
                    continue
            except Exception:
                pass
        seen_boxes.append((ix0, iy0, ix1, iy1))
        out.append((ix0, iy0, ix1, iy1, patch))
    return out


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
      1. Cairo (variable font, modern geometric sans, covers BOTH Arabic AND
         Latin scripts in a single file — essential for rendering mixed
         Arabic+English text like "API" or "DevOps" without tofu squares).
      2. Noto Sans Arabic (Arabic-only — picks up any Arabic glyph Cairo
         happens to lack).
      3. Noto Sans (Latin fallback).
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
    if code in arabic_codes:
        cairo_path = fonts_dir / "Cairo-Variable.ttf"
        if cairo_path.exists():
            try:
                font = ImageFont.truetype(str(cairo_path), size)
                try:
                    font.set_variation_by_axes([700.0 if bold else 400.0])
                except Exception:
                    pass
                cache[key] = font
                return font
            except Exception:
                pass
        # Cairo missing — fall back to Arabic-only Noto fonts (may show tofu
        # on embedded Latin runs; that's the cost of the fallback path).
        candidates = [
            "NotoSansArabic-Bold.ttf" if bold else "NotoSansArabic-Regular.ttf",
            "NotoNaskhArabic-Bold.ttf" if bold else "NotoNaskhArabic-Regular.ttf",
        ]
    elif code in hebrew_codes:
        candidates = ["NotoSansHebrew-Regular.ttf"]
    else:
        candidates = []
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
    # The extracted bbox is sized to the source text's glyph extents
    # (often narrower than the surrounding container because the source
    # paragraph didn't fill its card/column to the edge). Arabic glyphs
    # tend to be wider than Latin at the same point size, and also carry
    # longer lexical equivalents, so wrapping into the exact source-width
    # frequently produces 1-2-word narrow columns in place of a natural
    # paragraph. Widen the effective wrap width by a small factor for RTL
    # targets so the paragraph breathes out to match its container.
    # Downstream logic still anchors text to the original bbox's
    # right-edge (RTL) or left-edge (LTR), so the text just flows a bit
    # further into the adjacent whitespace — it does not collide with
    # neighbouring segments (those have their own bboxes/redactions).
    if rtl:
        box_w = int(box_w * 1.25)
    x0, y0, x1, y1 = box
    direction = "rtl" if rtl else "ltr"
    features = ["kern", "liga"]
    lang = _raqm_lang_for(target_lang)

    # Multi-line handling. The OCR paragraph-merger joins visual lines
    # with ``\n`` so the LLM gets the whole paragraph as context. At
    # render time, ``\n`` in a long paragraph is a SOFT break (just the
    # source's physical line wrap) and must be re-flowed to the
    # translated bbox width; but in a SHORT stacked title (e.g. a
    # "WATERFALL\nMODEL" wordmark, where each line is 1-3 words) the
    # break is INTENTIONAL and must be preserved — flattening stacked
    # titles produces the "overlapping two-line blob" the reviewer
    # flagged on the Waterfall page.
    pieces = [p.strip() for p in text.split("\n") if p.strip()]
    if not pieces:
        return
    # Stack only when this is plausibly a TRUE multi-line wordmark / heading —
    # a short, visually-stacked title where the newline is intentional. Three
    # conditions must all hold:
    #   * exactly 2–3 pieces (a wordmark rarely stacks into more)
    #   * every piece is a real multi-word phrase (≥2 words, ≤3 words)
    #   * the TOTAL text is short (≤10 words across all pieces)
    # Otherwise the newlines are just OCR/LLM artefacts inside a flowing
    # body paragraph and MUST be flattened into one line that gets
    # re-wrapped at the bbox width. Without this check, a long Arabic
    # body translation returned as "إزالة الحواجز\nبين التطوير\n..." would
    # be rendered as a tall column of 2-word lines instead of a normal
    # paragraph — the "cards with narrow 1-word wrapping" bug.
    total_words = sum(len(p.split()) for p in pieces)
    multiword_short = (
        2 <= len(pieces) <= 3
        and all(2 <= len(p.split()) <= 3 for p in pieces)
        and total_words <= 10
    )
    if multiword_short:
        forced_lines: list[str] = pieces
    else:
        forced_lines = [" ".join(pieces)]

    # Size strategy: start at the segment's original font size. Only shrink
    # when a single word overflows the bbox width (``_wrap_line`` returns
    # None). NEVER shrink because the wrapped paragraph is taller than the
    # source bbox — Arabic translations are often 20-30% taller than the
    # English source and shrinking to fit produced the tiny unreadable text
    # the user reported. The overflow lands in the whitespace below the
    # source bbox, which was inpainted to background.
    size_options = [font_size] + [max(10, font_size - step) for step in (1, 2, 3, 4, 6, 8)]
    font = None
    wrapped_lines: list[str] | None = None
    box_h = max(1, y1 - y0)
    # Allow vertical overflow up to 1.4× the source bbox height — Arabic
    # at the same nominal pt size is often taller than Latin, and a
    # rigid box-fit produces unreadable shrunken text. Above 1.4× the
    # rendered text would crash into the next paragraph below, so we
    # progressively shrink the font until the wrapped paragraph fits
    # within that overflow budget.
    overflow_budget = box_h * 1.4
    for size in size_options:
        font = _load_pil_font(target_lang, size, bold, italic, font_cache)
        candidate: list[str] = []
        ok = True
        for forced in forced_lines:
            segs_w = _wrap_line(draw, forced, font, box_w, direction, features, lang)
            if segs_w is None:
                ok = False
                break
            candidate.extend(segs_w)
        if not ok:
            continue
        line_h_try = _line_height(font)
        if line_h_try * len(candidate) <= overflow_budget or size == size_options[-1]:
            wrapped_lines = candidate
            break
    if wrapped_lines is None:
        font = _load_pil_font(target_lang, max(10, size_options[-1]), bold, italic, font_cache)
        wrapped_lines = []
        for forced in forced_lines:
            wrapped_lines.extend(
                _char_wrap(draw, forced, font, box_w, direction, features, lang)
            )
    # Caption-style fallback. When the text was wrapped into multiple
    # lines AND the SOURCE had only one line worth of words (≤4 across
    # all forced_lines), prefer to render it as ONE line that overflows
    # horizontally. Stacking a 2-word translation like "السباق الأول"
    # into two short lines under a circle reads as a malformed pair of
    # words; the same content on one line that extends slightly past
    # the source bbox is the correct caption shape.
    total_words_in_source = sum(len(p.split()) for p in forced_lines)
    if (
        wrapped_lines is not None
        and len(wrapped_lines) > len(forced_lines)
        and total_words_in_source <= 4
    ):
        wrapped_lines = list(forced_lines)
    if not wrapped_lines:
        return
    line_h = _line_height(font)
    total_h = line_h * len(wrapped_lines)
    # Vertically centre the rendered text inside the source bbox.
    # If the wrapped content is taller than the bbox (common for Arabic
    # which is often taller at the same pt size as Latin), start above
    # the bbox top so overflow lands symmetrically above and below —
    # this halves the chance that a single-line Arabic title collides
    # with an adjacent title below it (the "الشلال over النموذج overlap"
    # the reviewer flagged on the Waterfall page).
    y = y0 + (box_h - total_h) // 2
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


def _draw_rotated_text_in_bbox(
    page_img,
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
    rotation: int,
) -> None:
    """Draw ``text`` into ``box`` ROTATED by ``rotation`` degrees CCW so it
    matches the source page's vertical/marginalia text.

    Implementation: draw the text horizontally into an off-screen
    transparent image sized to the rotated bbox's SHORT dimension × LONG
    dimension, then rotate that image by ``rotation`` (expand=True) and
    alpha-paste into the page image at the bbox origin.
    """
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = box
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    if rotation in (90, 270):
        # When rotated, the horizontal canvas we draw on has dimensions
        # swapped from the target bbox: it will be ROTATED into the bbox.
        canvas_w, canvas_h = bh, bw
    else:
        canvas_w, canvas_h = bw, bh
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    if rotation in (90, 270):
        # Wordmark-style direct draw. We need pixel-precise placement
        # so the rotated word fills the column without clipping or
        # floating in empty space; the generic ``_draw_text_in_bbox``
        # uses getmetrics-based line heights that over-estimate ink
        # extent and pushes the glyph above the canvas top, producing
        # the "disconnected/clipped letters" the reviewer kept seeing.
        # Always bold for vertical wordmarks — the source ``AGILE``-
        # style label is invariably set in heavy weight so the caps
        # read as a marquee, and a regular-weight Arabic translation
        # at the same point size visually disappears in the column.
        bold = True
        direction = "rtl" if rtl else "ltr"
        features = ["kern", "liga"]
        lang = _raqm_lang_for(target_lang)
        # Pick the largest font whose ACTUAL ink bbox fits both canvas
        # dimensions. textbbox is the right measure for single-line
        # placement; we'll position by that bbox so nothing clips.
        best_size = max(font_size, 8)
        best_bbox = None
        upper = max(font_size, int(canvas_h * 2))
        for trial in range(upper, 7, -2):
            test_font = _load_pil_font(target_lang, trial, bold, italic, font_cache)
            try:
                bb = layer_draw.textbbox(
                    (0, 0), text, font=test_font,
                    direction=direction, features=features, language=lang,
                )
            except Exception:
                continue
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            if tw <= canvas_w and th <= canvas_h:
                best_size = trial
                best_bbox = bb
                break
        chosen = _load_pil_font(target_lang, best_size, bold, italic, font_cache)
        if best_bbox is None:
            try:
                best_bbox = layer_draw.textbbox(
                    (0, 0), text, font=chosen,
                    direction=direction, features=features, language=lang,
                )
            except Exception:
                best_bbox = (0, 0, canvas_w, canvas_h)
        bb_left, bb_top, bb_right, bb_bottom = best_bbox
        tw = bb_right - bb_left
        th = bb_bottom - bb_top
        # Centre the visible glyph bbox within the canvas. The
        # ``draw.text`` API places the text such that ``(x, y)`` is the
        # TOP-LEFT of the bounding box's origin, NOT of the visible
        # glyphs — subtract bb_left / bb_top so the glyphs land where
        # we want them centred.
        x = (canvas_w - tw) // 2 - bb_left
        y = (canvas_h - th) // 2 - bb_top
        try:
            layer_draw.text(
                (x, y), text, font=chosen, fill=color,
                direction=direction, features=features, language=lang,
            )
        except Exception:
            try:
                layer_draw.text((x, y), text, font=chosen, fill=color)
            except Exception:
                pass
    else:
        _draw_text_in_bbox(
            layer_draw, text, (0, 0, canvas_w, canvas_h),
            target_lang=target_lang, rtl=rtl, font_size=font_size,
            color=color, bold=bold, italic=italic, font_cache=font_cache,
        )
    rotated = layer.rotate(rotation, expand=True, resample=Image.BICUBIC)
    # Centre the rotated layer on the bbox centre so small rotation-induced
    # size mismatches don't nudge text out of place.
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    rw, rh = rotated.size
    paste_x = cx - rw // 2
    paste_y = cy - rh // 2
    page_img.paste(rotated, (paste_x, paste_y), rotated)


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
        # Tight leading. A 1.15× multiplier made Arabic (already taller
        # than Latin at the same pt size) overflow adjacent bboxes; 1.0
        # matches visible glyph extent so stacked single-line titles no
        # longer collide.
        return int(round((ascent + descent) * 1.0))
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


def _char_wrap(draw, text: str, font, max_width: int, direction: str, features, lang: str):
    """Last-resort wrapper: break at any character when even a single word is
    wider than the bbox. Used for URLs / long tokens in narrow columns."""
    if not text:
        return [""]
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        if _text_width(draw, trial, font, direction, features, lang) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
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
