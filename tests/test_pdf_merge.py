"""Unit tests for PDF extraction post-processing — ensures OCR'd PDFs that
land as one-text-block-per-line get merged back into full paragraphs before
translation. Without this the LLM sees each line as a standalone "text" and
loses sentence context, producing choppy and often wrong translations
(especially for RTL languages where word order depends on the full sentence).
"""
from __future__ import annotations

from app.schemas import Segment
from app.translators.pdf_translator import _merge_vertical_paragraphs


def _seg(sid, y0, y1, text, bullet="", size=11.0, x0=50, x1=500, page=0, bold=False):
    return Segment(
        id=sid,
        text=text,
        meta={
            "page": page,
            "bbox": [x0, y0, x1, y1],
            "font_size": size,
            "bullet": bullet,
            "bold": bold,
            "italic": False,
            "color": 0,
            "block_idx": 0,
        },
    )


def test_merge_two_stacked_lines_same_paragraph():
    segs = [
        _seg("b0", 100, 112, "This is the first line"),
        _seg("b1", 113, 125, "of a continuing paragraph."),
    ]
    merged = _merge_vertical_paragraphs(segs)
    assert len(merged) == 1
    assert merged[0].text == "This is the first line\nof a continuing paragraph."


def test_separate_bullets_stay_separate():
    segs = [
        _seg("b0", 100, 112, "First bullet text", bullet="•"),
        _seg("b1", 113, 125, "Second bullet text", bullet="•"),
    ]
    assert len(_merge_vertical_paragraphs(segs)) == 2


def test_paragraph_break_preserved():
    segs = [
        _seg("b0", 100, 112, "Paragraph one ends here."),
        _seg("b1", 150, 162, "Paragraph two starts here."),
    ]
    assert len(_merge_vertical_paragraphs(segs)) == 2


def test_different_columns_preserved():
    segs = [
        _seg("b0", 100, 112, "Left column line", x0=50, x1=300),
        _seg("b1", 113, 125, "Right column line", x0=350, x1=550),
    ]
    assert len(_merge_vertical_paragraphs(segs)) == 2


def test_heading_and_body_not_merged():
    segs = [
        _seg("b0", 100, 120, "Heading", size=18.0),
        _seg("b1", 125, 137, "Body paragraph.", size=11.0),
    ]
    assert len(_merge_vertical_paragraphs(segs)) == 2


def test_multiline_bullet_merged():
    segs = [
        _seg("b0", 100, 112, "Bullet item that wraps", bullet="•"),
        _seg("b1", 113, 125, "onto a second line."),
    ]
    merged = _merge_vertical_paragraphs(segs)
    assert len(merged) == 1
    assert merged[0].meta["bullet"] == "•"
    assert "wraps\nonto" in merged[0].text


def test_three_line_paragraph_merged():
    segs = [
        _seg("b0", 100, 112, "Line one"),
        _seg("b1", 113, 125, "Line two"),
        _seg("b2", 126, 138, "Line three"),
    ]
    merged = _merge_vertical_paragraphs(segs)
    assert len(merged) == 1
    assert merged[0].text == "Line one\nLine two\nLine three"


def test_merged_bbox_is_union():
    segs = [
        _seg("b0", 100, 112, "First", x0=50, x1=200),
        _seg("b1", 113, 125, "Second", x0=50, x1=300),
    ]
    merged = _merge_vertical_paragraphs(segs)
    assert merged[0].meta["bbox"] == [50, 100, 300, 125]


def test_merge_renumbers_ids():
    """After merging, the remaining segments get new sequential b0..bN ids so
    the JSON payload sent to the LLM stays compact and unique."""
    segs = [
        _seg("b0", 100, 112, "Para A line 1"),
        _seg("b1", 113, 125, "Para A line 2"),
        _seg("b2", 200, 212, "Para B line 1"),
        _seg("b3", 213, 225, "Para B line 2"),
    ]
    merged = _merge_vertical_paragraphs(segs)
    assert len(merged) == 2
    assert [s.id for s in merged] == ["b0", "b1"]


def test_cross_page_not_merged():
    """Two segments on different pages — even with identical bboxes — stay
    separate; merging across page boundaries would corrupt per-page layout."""
    segs = [
        _seg("b0", 700, 712, "End of page 1", page=0),
        _seg("b1", 100, 112, "Start of page 2", page=1),
    ]
    assert len(_merge_vertical_paragraphs(segs)) == 2
