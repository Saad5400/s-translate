"""Conditionally OCR PDFs so the rest of the pipeline sees extractable text.

Why `--force-ocr`: `--skip-text` and `--redo-ocr` both leave mixed pages'
image-baked text un-OCR'd — which is exactly what this project needs to
translate on brochures, posters and marketing PDFs. So OCR is run in force
mode whenever OCR is needed at all.

Why CONDITIONAL: force-OCR rasterises every page, and for native digital
PDFs that already have perfect text extraction this destroys the 1:1 input-
to-output visual match the user is looking for (fonts become JPEG noise,
vector art gets baked into the image, file size balloons). So before running
OCR we first check whether ANY page actually needs it — a page needs OCR when
it contains images whose combined area is meaningful relative to the page,
AND the native text extraction off the page is thin compared to that image
area (suggesting the image carries text the OCR needs to surface). If every
page looks like a clean native-text page, we skip OCR entirely and return the
input unchanged so the downstream pipeline works off the original PDF.

Returns the input path unchanged when OCR fails OR is skipped, so the caller
never needs to know whether OCR ran.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)


def maybe_ocr_pdf(src: Path, work_dir: Path) -> Path:
    """Force-OCR `src` when needed; return OCR'd path on success, else `src`.

    Skips OCR entirely when the document is already fully native text and has
    no image content that could hide text. This preserves 1:1 visual fidelity
    for standard native PDFs (the common case) while still OCR'ing scans and
    image-heavy brochures that need it.
    """
    if src.suffix.lower() != ".pdf":
        return src

    if not _pdf_needs_ocr(src):
        log.info("skipping OCR: %s has no image-text content", src.name)
        return src

    jobs = settings.pdf_ocr_jobs or os.cpu_count() or 1
    out = work_dir / f"{src.stem}.ocr.pdf"

    # Try `--optimize 3` first (JBIG2 + pngquant + JPEG re-encode, typically
    # 5–10× smaller output, critical when force-OCR rasterizes whole pages).
    # Fall back to `--optimize 1` if the extra binaries (jbig2enc, pngquant)
    # aren't installed.
    def _run(optimize: str) -> tuple[int, str]:
        cmd = [
            "ocrmypdf",
            "--force-ocr",
            "--optimize", optimize,
            "--jobs", str(jobs),
            "--quiet",
            "-l", settings.pdf_ocr_languages,
            str(src),
            str(out),
        ]
        log.info("running OCR: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        return proc.returncode, stderr

    try:
        rc, stderr = _run("3")
        if rc != 0:
            out.unlink(missing_ok=True)
            log.warning("ocrmypdf --optimize 3 failed (rc=%d): %s", rc, stderr[:500])
            rc, stderr = _run("1")
            if rc != 0:
                log.error("ocrmypdf --optimize 1 failed (rc=%d): %s", rc, stderr[:1000])
                return src
    except Exception as exc:
        log.error("ocrmypdf failed (%s): %s", type(exc).__name__, exc)
        return src
    if not out.exists() or out.stat().st_size == 0:
        log.error("ocrmypdf produced no output at %s", out)
        return src
    return out


def _pdf_needs_ocr(src: Path) -> bool:
    """True when at least one page has image content substantial enough that
    it may carry text the OCR pipeline needs to surface.

    Heuristic per page:
      * If the page has NO images, it cannot have image-baked text → skip.
      * If it has images but their combined area covers a negligible fraction
        of the page (< 3%), they're almost certainly decorative glyphs (icons,
        logos, dingbats) — skip.
      * If images cover ≥ 3% of the page area AND the page's extractable text
        is thin enough that those images likely carry additional text, return
        True.
      * A page with dense native text (> ~400 chars) AND small images also
        doesn't need OCR — the images are probably decorative.
    """
    try:
        import pymupdf
    except Exception:
        # If pymupdf isn't importable we can't inspect — err on the side of
        # OCR'ing (legacy behaviour) so image-bearing PDFs still get text.
        return True
    try:
        doc = pymupdf.open(str(src))
    except Exception:
        return True
    try:
        for page in doc:
            page_area = page.rect.width * page.rect.height
            if page_area <= 0:
                continue
            try:
                infos = page.get_image_info(xrefs=False)
            except Exception:
                infos = []
            if not infos:
                continue
            covered = 0.0
            for info in infos:
                bbox = info.get("bbox")
                if not bbox:
                    continue
                w = max(0.0, float(bbox[2]) - float(bbox[0]))
                h = max(0.0, float(bbox[3]) - float(bbox[1]))
                covered += w * h
            coverage = covered / page_area
            if coverage < 0.03:
                continue  # decorative; no meaningful image text possible
            try:
                native_text = page.get_text("text") or ""
            except Exception:
                native_text = ""
            # If image coverage dominates the page OR native text is thin
            # relative to the image footprint, treat the page as OCR-worthy.
            # Thresholds tuned so a typical page with a single banner image
            # above normal paragraphs does NOT trigger OCR, but a scan or an
            # image-heavy brochure does.
            if coverage >= 0.60:
                return True
            if len(native_text.strip()) < 200 and coverage >= 0.15:
                return True
    finally:
        doc.close()
    return False
