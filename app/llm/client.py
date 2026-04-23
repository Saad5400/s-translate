from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Callable, Iterable
from urllib.parse import urlparse

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings
from ..schemas import Segment
from ..utils.errors import LLMError
from .chunker import chunk_segments, mask_placeholders, unmask_placeholders
from .prompts import (
    CONTEXT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_context_user_message,
    build_user_message,
)

log = logging.getLogger(__name__)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def resolve_ollama_base(api_base: str | None) -> str | None:
    """Return an Ollama api_base that is reachable from this process.

    Inside Docker, the UI default of ``http://localhost:11434`` points at the
    container itself and the connection refuses. When ``OLLAMA_API_BASE`` is
    set (e.g. ``http://host.docker.internal:11434`` or ``http://ollama:11434``
    via docker-compose), we rewrite any loopback-pointing base to that value.
    Missing/empty api_base also picks up the env default so the user can leave
    the field blank in Docker deployments.
    """
    override = os.getenv("OLLAMA_API_BASE", "").strip() or None
    if not api_base:
        return override
    if not override:
        return api_base
    try:
        host = urlparse(api_base).hostname
    except ValueError:
        return api_base
    if host and host.lower() in _LOOPBACK_HOSTS:
        return override
    return api_base

# Allow test injection of a stub translator for offline tests.
_STUB_FN: Callable[[list[Segment], str, str | None], list[str]] | None = None


def set_stub_translator(fn: Callable[[list[Segment], str, str | None], list[str]] | None) -> None:
    global _STUB_FN
    _STUB_FN = fn


class LLMClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: str | None = None,
        temperature: float = 0.2,
        concurrency: int = settings.concurrent_chunks,
    ) -> None:
        self.model = model
        self.api_key = api_key
        if model.startswith("ollama/") or model.startswith("ollama_chat/"):
            api_base = resolve_ollama_base(api_base)
        self.api_base = api_base
        self.temperature = temperature
        self.semaphore = asyncio.Semaphore(concurrency)

    async def summarize_document(
        self,
        segments: list[Segment],
        target_lang: str,
        source_lang: str | None = None,
        max_chars: int = 6000,
    ) -> str:
        """Build a short domain-context brief from the document's own text.

        The brief is fed into every translate_segments call so the LLM can pick
        the correct domain-specific terminology instead of literal word-for-word
        translations. Samples up to ``max_chars`` from the start/middle/end of
        the extracted segments so very long documents still fit in one request.
        Returns an empty string on any failure — context is an enhancement, not
        a blocker: translation proceeds normally when it's missing.
        """
        if _STUB_FN is not None:
            return ""
        if not segments:
            return ""
        excerpt = _sample_segments_excerpt(segments, max_chars=max_chars)
        if not excerpt.strip():
            return ""
        try:
            import litellm

            messages = [
                {"role": "system", "content": CONTEXT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_context_user_message(excerpt, source_lang, target_lang),
                },
            ]
            kwargs: dict = dict(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                api_key=self.api_key,
            )
            if self.api_base:
                kwargs["api_base"] = self.api_base
            resp = await litellm.acompletion(**kwargs)
            content = (resp.choices[0].message.content or "").strip()
            # Guard: an over-verbose or empty response still returns safely — the
            # translator prompt just ignores an empty brief.
            if len(content) > 2000:
                content = content[:2000].rstrip() + "…"
            log.info("document context brief: %s", content.replace("\n", " "))
            return content
        except Exception as exc:
            log.warning("context brief generation failed (%s); proceeding without", exc)
            return ""

    async def translate_segments(
        self,
        segments: list[Segment],
        target_lang: str,
        source_lang: str | None = None,
        max_chunk_tokens: int = settings.default_chunk_tokens,
        progress: Callable[[float, str], None] | None = None,
        context: str | None = None,
    ) -> list[Segment]:
        """Translate segments in-place (sets .translated). Returns same list."""
        # Filter out empty segments (no translation needed).
        translatable = [s for s in segments if s.text and s.text.strip()]
        for s in segments:
            if not s.text or not s.text.strip():
                s.translated = s.text

        if not translatable:
            return segments

        # Use stub if configured (tests/offline).
        if _STUB_FN is not None:
            results = _STUB_FN(translatable, target_lang, source_lang)
            for seg, translated in zip(translatable, results, strict=True):
                seg.translated = translated
            return segments

        # Mask placeholders per segment.
        maskings: list[dict[str, str]] = []
        for seg in translatable:
            masked, mapping = mask_placeholders(seg.text)
            seg.meta["_original_text"] = seg.text
            seg.text = masked  # temporarily mutate for chunk input
            maskings.append(mapping)

        try:
            chunks = chunk_segments(translatable, max_tokens=max_chunk_tokens)
            total = len(chunks)
            done = 0
            in_flight = 0

            async def _run_chunk(idx: int, chunk: list[Segment]) -> None:
                nonlocal done, in_flight
                async with self.semaphore:
                    in_flight += 1
                    if progress:
                        progress(
                            done / total,
                            f"Translating chunk {done + in_flight}/{total}",
                        )
                    try:
                        await self._translate_chunk(
                            chunk, target_lang, source_lang, context=context,
                        )
                    finally:
                        in_flight -= 1
                done += 1
                if progress:
                    progress(done / total, f"Translated {done}/{total} chunks")

            await asyncio.gather(*[_run_chunk(i, c) for i, c in enumerate(chunks)])
        finally:
            # Restore originals + unmask translations.
            for seg, mapping in zip(translatable, maskings, strict=True):
                seg.text = seg.meta.pop("_original_text", seg.text)
                if seg.translated:
                    seg.translated = unmask_placeholders(seg.translated, mapping)

        return segments

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(LLMError),
    )
    async def _translate_chunk(
        self,
        chunk: list[Segment],
        target_lang: str,
        source_lang: str | None,
        context: str | None = None,
    ) -> None:
        import litellm

        payload = {seg.id: seg.text for seg in chunk}
        payload_json = json.dumps(payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_message(
                    payload_json, target_lang, source_lang, context=context,
                ),
            },
        ]
        kwargs: dict = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            api_key=self.api_key,
        )
        if self.api_base:
            kwargs["api_base"] = self.api_base
        # Request JSON response format on providers that support it.
        kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as exc:
            # Fallback without response_format (some providers reject it).
            if "response_format" in str(exc).lower() or "json_object" in str(exc).lower():
                kwargs.pop("response_format", None)
                try:
                    resp = await litellm.acompletion(**kwargs)
                except Exception as exc2:
                    raise LLMError(f"LLM call failed: {exc2}") from exc2
            else:
                raise LLMError(f"LLM call failed: {exc}") from exc

        content = resp.choices[0].message.content or ""
        parsed = _parse_json(content)
        if parsed is None:
            raise LLMError(f"Could not parse JSON response: {content[:200]}")

        # Match back by id. Missing keys -> fall back to original text.
        for seg in chunk:
            translated = parsed.get(seg.id)
            if isinstance(translated, str) and translated:
                seg.translated = _normalize_target_text(translated, target_lang)
            else:
                log.warning("segment %s missing in response; keeping original", seg.id)
                seg.translated = seg.meta.get("_original_text", seg.text)


_ARABIC_ROMAN_ORDINAL = {
    "I": "الأول", "II": "الثاني", "III": "الثالث", "IV": "الرابع",
    "V": "الخامس", "VI": "السادس", "VII": "السابع", "VIII": "الثامن",
    "IX": "التاسع", "X": "العاشر",
    # ocrmypdf frequently misreads "II" as "Il", "III" as "Ill" when
    # the second I is rendered with a slight serif. Map those too so
    # the post-translation normaliser still hits.
    "Il": "الثاني", "Ill": "الثالث",
}


def _normalize_target_text(text: str, target_lang: str) -> str:
    """Post-process LLM output for the target language.

    For Arabic translations, replace stray Latin Roman-numeral / single-
    letter ordinals that the LLM left in the middle of an Arabic string
    with their Arabic equivalents. The system prompt explicitly forbids
    this behaviour but providers sometimes still leak the original
    Latin token (especially when the source had OCR typos like ``Sprint
    Il`` instead of ``Sprint II``). Also collapses internal newlines in
    very short Arabic captions where the LLM split a phrase like
    ``Sprint II`` across two lines.
    """
    import re

    from ..lang.rtl import normalize as _norm_lang

    if not text:
        return text
    code = _norm_lang(target_lang)
    if code != "ar":
        return text

    out = text
    # Replace " I" / " II" / " Il" etc. when preceded by Arabic content.
    def _sub(match: "re.Match") -> str:
        token = match.group(2)
        replacement = _ARABIC_ROMAN_ORDINAL.get(token, token)
        return f"{match.group(1)} {replacement}"

    arabic_run = r"[؀-ۿݐ-ݿ]+"
    pattern = re.compile(
        rf"({arabic_run})[\s\n]+([IVX]+|Il|Ill|n)\b"
    )
    out = pattern.sub(_sub, out)

    # Collapse newlines in very short captions (≤ ~25 chars) — these
    # are almost always the LLM splitting a single short phrase across
    # two lines, and rendering them stacked produces a fragmented
    # caption. Multi-line bodies / wordmarks longer than this stay
    # untouched.
    if len(out) <= 25 and "\n" in out:
        out = " ".join(part.strip() for part in out.split("\n") if part.strip())
    return out


def _sample_segments_excerpt(segments: list[Segment], max_chars: int = 6000) -> str:
    """Return a representative text sample from ``segments`` no longer than
    ``max_chars`` chars. For long documents, blends text from the START, MIDDLE,
    and END so a context summariser sees terminology from each section rather
    than only the first few pages (title/TOC/preface). Short documents fall
    through and return all text verbatim.
    """
    texts = [s.text for s in segments if s.text and s.text.strip()]
    if not texts:
        return ""
    all_text = "\n".join(texts)
    if len(all_text) <= max_chars:
        return all_text

    n = len(texts)
    per_section = max_chars // 3

    def _take_from(start_idx: int, step: int) -> str:
        picked: list[str] = []
        total = 0
        i = start_idx
        while 0 <= i < n and total < per_section:
            t = texts[i]
            remaining = per_section - total
            snippet = t if len(t) <= remaining else t[:remaining]
            picked.append(snippet)
            total += len(snippet)
            i += step
        return "\n".join(picked)

    start_part = _take_from(0, 1)
    mid_part = _take_from(n // 2, 1)
    end_part = _take_from(n - 1, -1)
    joined = "\n\n---\n\n".join(
        p for p in (start_part, mid_part, end_part) if p.strip()
    )
    return joined[:max_chars]


def _parse_json(content: str) -> dict | None:
    """Try parsing JSON; strip code fences / leading prose if needed."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Strip fences
    if content.startswith("```"):
        first_nl = content.find("\n")
        if first_nl != -1:
            content = content[first_nl + 1 :]
        if content.endswith("```"):
            content = content[: -3]
        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
    # Find first { and last }
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None
