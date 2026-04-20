from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Callable

from . import jobs as jobs_mod
from .combine import combine as do_combine
from .lang.detect import detect_language
from .lang.rtl import is_rtl
from .llm.client import LLMClient
from .rtl import apply_rtl
from .schemas import DocFormat, OutputMode, TranslationJob
from .translators.registry import get_translator
from .utils.errors import TranslationError
from .utils.io import job_workspace

log = logging.getLogger(__name__)


async def translate_document(
    job: TranslationJob,
    progress: Callable[[float, str], None] | None = None,
) -> tuple[Path, DocFormat]:
    raise RuntimeError("Use run_job() which manages workspace lifetime.")


async def run_job(
    job: TranslationJob,
    progress: Callable[[float, str], None] | None = None,
    job_id: str | None = None,
) -> Path:
    """Execute the job end-to-end and return a path to the deliverable.

    The returned path is OUTSIDE the temp workspace (moved to a stable location)
    so callers can stream it to the user without racing cleanup.
    """
    from .config import settings

    def _p(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    src = job.src_path
    fmt = DocFormat.from_path(src)
    translator = get_translator(src)

    with job_workspace() as wd:
        # 1. Detect source language (optional, purely for prompting context).
        _p(0.02, "Detecting source language")
        source_lang = job.source_lang
        if not source_lang:
            # Use a quick text snippet from the doc for detection.
            snippet = _sample_text(src, fmt)
            if snippet:
                source_lang = detect_language(snippet)
        log.info("source_lang=%s target_lang=%s", source_lang, job.target_lang)

        # 2. Extract segments.
        _p(0.08, "Extracting text segments")
        segments = translator.extract(src)
        log.info("extracted %d segments", len(segments))

        # 3. Call LLM to translate.
        client = LLMClient(
            model=job.model_string,
            api_key=job.api_key,
            api_base=job.api_base,
            temperature=job.temperature,
        )

        def _llm_progress(f: float, msg: str) -> None:
            _p(0.08 + f * 0.7, msg)

        await client.translate_segments(
            segments,
            target_lang=job.target_lang,
            source_lang=source_lang,
            max_chunk_tokens=job.max_chunk_tokens,
            progress=_llm_progress,
        )

        # 4. Reinsert into format-preserving output.
        _p(0.80, "Rebuilding document")
        translated_path = wd / f"translated{src.suffix}"
        if isinstance(translator, _import_pdf_cls()):
            # PdfTranslator.reinsert accepts target_lang for inline RTL handling.
            translator.reinsert(src, segments, translated_path, target_lang=job.target_lang)
        else:
            translator.reinsert(src, segments, translated_path)

        # 5. Apply RTL (non-PDF formats).
        if is_rtl(job.target_lang):
            _p(0.88, "Applying RTL direction")
            apply_rtl(translated_path, fmt)

        # 6. Combine if needed.
        mode = job.output_mode
        if mode is OutputMode.ORIGINAL:
            final = src
            ext = src.suffix
        elif mode is OutputMode.TRANSLATED:
            final = translated_path
            ext = src.suffix
        else:
            _p(0.93, f"Combining ({mode.value})")
            combined_suffix = src.suffix
            combined_path = wd / f"combined{combined_suffix}"
            final = do_combine(
                src_path=src,
                translated_path=translated_path,
                out_path=combined_path,
                fmt=fmt,
                mode=mode,
                rtl=is_rtl(job.target_lang),
            )
            ext = final.suffix  # may become .pdf for DOCX/PPTX horizontal

        # 7. Move deliverable to a stable location. If a job_id is provided,
        # persist to the job directory (so it survives workspace cleanup and
        # becomes retrievable via GET /api/jobs/{id}/download). Otherwise use
        # the generic out dir for backwards-compat callers.
        suffix = _mode_suffix(mode)
        filename = f"{src.stem}{suffix}{ext}"
        if job_id:
            stable_path = jobs_mod.output_path(job_id, filename)
        else:
            stable_out = settings.temp_dir / "out"
            stable_out.mkdir(parents=True, exist_ok=True)
            stable_path = stable_out / filename
        if stable_path.exists():
            stable_path.unlink()
        shutil.copy2(final, stable_path)
        _p(1.0, "Done")
        return stable_path


def _import_pdf_cls():
    from .translators.pdf_translator import PdfTranslator
    return PdfTranslator


def _sample_text(src: Path, fmt: DocFormat) -> str:
    """Grab a short text sample for language detection."""
    try:
        if fmt is DocFormat.TXT:
            return src.read_text(encoding="utf-8", errors="replace")[:2000]
        if fmt is DocFormat.DOCX:
            from docx import Document

            d = Document(str(src))
            return " ".join(p.text for p in d.paragraphs[:30])[:2000]
        if fmt is DocFormat.PPTX:
            from pptx import Presentation

            prs = Presentation(str(src))
            parts: list[str] = []
            for slide in prs.slides[:3]:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        parts.append(shape.text_frame.text)
            return " ".join(parts)[:2000]
        if fmt is DocFormat.XLSX:
            from openpyxl import load_workbook

            wb = load_workbook(str(src), read_only=True)
            parts: list[str] = []
            ws = wb.worksheets[0] if wb.worksheets else None
            if ws is None:
                return ""
            for row in ws.iter_rows(max_row=50, values_only=True):
                for v in row:
                    if isinstance(v, str):
                        parts.append(v)
            wb.close()
            return " ".join(parts)[:2000]
        if fmt is DocFormat.PDF:
            import pymupdf

            d = pymupdf.open(str(src))
            try:
                parts: list[str] = []
                for page in d:
                    parts.append(page.get_text("text"))
                    if sum(len(p) for p in parts) > 2000:
                        break
                return " ".join(parts)[:2000]
            finally:
                d.close()
    except Exception:
        return ""
    return ""


def _mode_suffix(mode: OutputMode) -> str:
    return {
        OutputMode.ORIGINAL: "_original",
        OutputMode.TRANSLATED: "_translated",
        OutputMode.BOTH_VERTICAL: "_both_v",
        OutputMode.BOTH_HORIZONTAL: "_both_h",
    }[mode]
