from __future__ import annotations

from pathlib import Path


def combine_vertical(src: Path, trans: Path, out: Path) -> Path:
    """Append translated DOCX after original with a page break between."""
    from docx import Document
    from docxcompose.composer import Composer

    master = Document(str(src))
    # Add page break before appending.
    master.add_page_break()
    composer = Composer(master)
    composer.append(Document(str(trans)))
    composer.save(str(out))
    return out
