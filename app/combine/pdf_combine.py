from __future__ import annotations

from pathlib import Path


_SAVE_KW = dict(
    garbage=4,
    deflate=True,
    deflate_images=True,
    deflate_fonts=True,
    clean=True,
    linear=False,
)


def combine_vertical(src: Path, trans: Path, out: Path) -> Path:
    """Append translated PDF pages after original."""
    import pymupdf

    src_doc = pymupdf.open(str(src))
    trans_doc = pymupdf.open(str(trans))
    try:
        new_doc = pymupdf.open()
        new_doc.insert_pdf(src_doc)
        new_doc.insert_pdf(trans_doc)
        new_doc.save(str(out), **_SAVE_KW)
        new_doc.close()
    finally:
        src_doc.close()
        trans_doc.close()
    return out


def combine_horizontal(src: Path, trans: Path, out: Path, rtl: bool = False) -> Path:
    """Side-by-side per page: ALWAYS input on left, translated on right.

    The `rtl` flag is kept for API compatibility but no longer swaps sides —
    users expect input→output reading order regardless of target language.
    """
    import pymupdf

    src_doc = pymupdf.open(str(src))
    trans_doc = pymupdf.open(str(trans))
    try:
        new_doc = pymupdf.open()
        n = max(src_doc.page_count, trans_doc.page_count)
        for i in range(n):
            ref_page = src_doc[i] if i < src_doc.page_count else trans_doc[i]
            w = ref_page.rect.width
            h = ref_page.rect.height
            new_page = new_doc.new_page(width=w * 2, height=h)
            left_rect = pymupdf.Rect(0, 0, w, h)
            right_rect = pymupdf.Rect(w, 0, w * 2, h)

            if i < src_doc.page_count:
                new_page.show_pdf_page(left_rect, src_doc, i)
            if i < trans_doc.page_count:
                new_page.show_pdf_page(right_rect, trans_doc, i)

        new_doc.save(str(out), **_SAVE_KW)
        new_doc.close()
    finally:
        src_doc.close()
        trans_doc.close()
    return out
