from __future__ import annotations

from pathlib import Path

from lxml import etree

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"


def _ensure_child(parent, tag: str):
    """Ensure a w:<tag> child exists on parent; return the child. Idempotent."""
    existing = parent.find(f"{_W}{tag}")
    if existing is not None:
        return existing
    el = etree.SubElement(parent, f"{_W}{tag}")
    return el


def _get_or_create_pPr(p_elem):
    pPr = p_elem.find(f"{_W}pPr")
    if pPr is None:
        pPr = etree.SubElement(p_elem, f"{_W}pPr")
        # pPr must be the first child of <w:p> to validate.
        p_elem.insert(0, pPr)
    return pPr


def _get_or_create_rPr(r_elem):
    rPr = r_elem.find(f"{_W}rPr")
    if rPr is None:
        rPr = etree.SubElement(r_elem, f"{_W}rPr")
        r_elem.insert(0, rPr)
    return rPr


def apply(out_path: Path) -> Path:
    """Inject w:bidi into every paragraph's pPr, and w:rtl into every run's rPr.

    Also set section-level w:bidi on sectPr for page direction flip.
    """
    from docx import Document

    doc = Document(str(out_path))
    body = doc.element.body

    # All <w:p> elements anywhere (body + tables + headers/footers).
    # We mutate via the XML tree, which covers everything.
    for p in body.iter(f"{_W}p"):
        pPr = _get_or_create_pPr(p)
        _ensure_child(pPr, "bidi")
        for r in p.findall(f"{_W}r"):
            rPr = _get_or_create_rPr(r)
            _ensure_child(rPr, "rtl")

    # Section direction
    for sectPr in body.iter(f"{_W}sectPr"):
        _ensure_child(sectPr, "bidi")

    # Also handle header/footer parts (separate XML parts in python-docx).
    for section in doc.sections:
        for part in (section.header, section.footer):
            try:
                root = part._element
            except AttributeError:
                continue
            for p in root.iter(f"{_W}p"):
                pPr = _get_or_create_pPr(p)
                _ensure_child(pPr, "bidi")
                for r in p.findall(f"{_W}r"):
                    rPr = _get_or_create_rPr(r)
                    _ensure_child(rPr, "rtl")

    doc.save(str(out_path))
    return out_path
