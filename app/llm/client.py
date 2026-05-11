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
from .chunker import (
    _approx_tokens,
    chunk_segments,
    mask_placeholders,
    unmask_placeholders,
)
from .prompts import (
    CONTEXT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_chunk_user_message,
    build_context_user_message,
    build_document_intro_message,
    build_user_message,  # noqa: F401  (legacy export, kept for callers)
)

log = logging.getLogger(__name__)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# Context-brief sizing. Starts large for big-context models; halves on
# provider-side "too long" errors down to the minimum before giving up.
_MIN_CONTEXT_EXCERPT_CHARS = 3000
_MAX_CONTEXT_BRIEF_CHARS = 12000

# Substrings that providers use in error messages when the prompt
# exceeds their context window. Matched case-insensitively.
_CONTEXT_LENGTH_ERROR_MARKERS = (
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
    "max_tokens",
    "too many tokens",
    "prompt is too long",
    "input is too long",
    "request too large",
    "string too long",
)


class _ContextTooLongError(Exception):
    """Raised internally when the provider rejects an excerpt as too long."""


def _is_context_length_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _CONTEXT_LENGTH_ERROR_MARKERS)


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
        max_chars: int = 60000,
    ) -> str:
        """Build a domain-context brief from the document's own text.

        The brief is fed into every translate_segments call so the LLM can pick
        the correct domain-specific terminology instead of literal word-for-word
        translations. Samples up to ``max_chars`` from the start/middle/end of
        the extracted segments so very long documents still fit in one request.
        Returns an empty string on any failure — context is an enhancement, not
        a blocker: translation proceeds normally when it's missing.

        Starts with a generous excerpt and halves it on context-length errors
        from the provider, down to ``_MIN_CONTEXT_EXCERPT_CHARS``. This lets us
        feed as much as the model can handle for big-context providers, and
        gracefully degrades on smaller-window models or when the prompt+excerpt
        blows the limit.
        """
        if _STUB_FN is not None:
            return ""
        if not segments:
            return ""

        current = max_chars
        while current >= _MIN_CONTEXT_EXCERPT_CHARS:
            excerpt = _sample_segments_excerpt(segments, max_chars=current)
            if not excerpt.strip():
                return ""
            try:
                return await self._request_context_brief(
                    excerpt, target_lang, source_lang,
                )
            except _ContextTooLongError as exc:
                next_size = current // 2
                if next_size < _MIN_CONTEXT_EXCERPT_CHARS:
                    log.warning(
                        "context brief skipped: provider rejected excerpt as too long "
                        "even at minimum size %d (%s)",
                        current, exc,
                    )
                    return ""
                log.info(
                    "context excerpt too long for provider at %d chars; retrying at %d",
                    current, next_size,
                )
                current = next_size
            except Exception as exc:
                log.warning("context brief generation failed (%s); proceeding without", exc)
                return ""
        return ""

    async def _request_context_brief(
        self,
        excerpt: str,
        target_lang: str,
        source_lang: str | None,
    ) -> str:
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
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "context request: model=%s temperature=%s api_base=%s excerpt_chars=%d",
                self.model, self.temperature, self.api_base, len(excerpt),
            )
            log.debug("context system prompt:\n%s", CONTEXT_SYSTEM_PROMPT)
            log.debug("context user message:\n%s", messages[1]["content"])
        try:
            resp = await litellm.acompletion(**kwargs)
        except Exception as exc:
            if _is_context_length_error(exc):
                raise _ContextTooLongError(str(exc)) from exc
            raise
        content = (resp.choices[0].message.content or "").strip()
        log.debug("context raw response:\n%s", content)
        if len(content) > _MAX_CONTEXT_BRIEF_CHARS:
            content = content[:_MAX_CONTEXT_BRIEF_CHARS].rstrip() + "…"
        log.info("document context brief (%d chars): %s",
                 len(content), content.replace("\n", " "))
        return content

    async def translate_segments(
        self,
        segments: list[Segment],
        target_lang: str,
        source_lang: str | None = None,
        max_chunk_tokens: int = settings.default_chunk_tokens,
        progress: Callable[[float, str], None] | None = None,
        context: str | None = None,
    ) -> list[Segment]:
        """Translate segments in-place (sets .translated). Returns same list.

        Chunks are processed SEQUENTIALLY in a single conversation per document
        so the model can reuse earlier terminology decisions in later chunks.
        Older chunk turns are evicted from history when the conversation grows
        past ``settings.max_history_tokens`` — the system prompt and the
        intro/context-brief turn stay pinned. The model is also asked to
        maintain a small running glossary which we re-inject into every chunk
        message so pinned terminology survives eviction.
        """
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

            # Pinned head of the conversation — never evicted.
            intro_content = build_document_intro_message(target_lang, source_lang, context)
            history: list[dict[str, str]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": intro_content},
                {
                    "role": "assistant",
                    "content": (
                        "Understood. I'll translate each chunk you send while keeping "
                        "terminology, register, and style consistent across the whole "
                        "document, and I'll return JSON keyed by your segment ids."
                    ),
                },
            ]
            glossary: dict[str, str] = {}

            for idx, chunk in enumerate(chunks):
                if progress:
                    progress(idx / total, f"Translating chunk {idx + 1}/{total}")
                await self._translate_chunk_in_conversation(
                    chunk, target_lang, history, glossary,
                )
                if progress:
                    progress((idx + 1) / total, f"Translated {idx + 1}/{total} chunks")
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
    async def _translate_chunk_in_conversation(
        self,
        chunk: list[Segment],
        target_lang: str,
        history: list[dict[str, str]],
        glossary: dict[str, str],
    ) -> None:
        """Send one chunk inside the document conversation, mutate history.

        On context-length errors from the provider, evict the oldest chunk
        turn pair (preserving the pinned intro) and retry. Tenacity retries
        the whole method on transient LLM errors; the user/assistant pair is
        only appended to ``history`` on success so retries don't accumulate
        partial turns.
        """
        payload = {seg.id: seg.text for seg in chunk}
        payload_json = json.dumps(payload, ensure_ascii=False)
        user_msg = {
            "role": "user",
            "content": build_chunk_user_message(payload_json, glossary),
        }

        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "translate chunk request: model=%s temperature=%s api_base=%s "
                "segments=%d ids=%s history_turns=%d glossary_entries=%d",
                self.model, self.temperature, self.api_base,
                len(chunk), [seg.id for seg in chunk],
                len(history), len(glossary),
            )
            log.debug("translate chunk user message:\n%s", user_msg["content"])

        while True:
            # Proactively evict if estimated tokens already over budget — saves a
            # round-trip vs. waiting for a provider rejection.
            self._enforce_history_budget(history, user_msg)
            try:
                resp = await self._call_llm(history + [user_msg])
                break
            except _ContextTooLongError as exc:
                if not self._evict_oldest_pair(history):
                    raise LLMError(f"context exhausted, nothing left to evict: {exc}") from exc
                log.info(
                    "context too long; evicted oldest chunk pair (remaining turns=%d)",
                    len(history),
                )
                continue
            except Exception as exc:
                if _is_context_length_error(exc):
                    if not self._evict_oldest_pair(history):
                        raise LLMError(f"context exhausted: {exc}") from exc
                    log.info(
                        "context error '%s'; evicted oldest chunk pair (remaining turns=%d)",
                        type(exc).__name__, len(history),
                    )
                    continue
                raise LLMError(f"LLM call failed: {exc}") from exc

        content = resp.choices[0].message.content or ""
        log.debug("translate raw response:\n%s", content)
        parsed = _parse_json(content)
        if parsed is None:
            raise LLMError(f"Could not parse JSON response: {content[:200]}")
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "translate parsed response: %s",
                json.dumps(parsed, ensure_ascii=False, indent=2),
            )

        # Top-level "_glossary" is a running document-wide glossary the model
        # may append to. We strip it before applying per-segment results so it
        # isn't mistaken for an unknown segment id.
        new_glossary = parsed.pop("_glossary", None)
        if isinstance(new_glossary, dict):
            for src, tgt in new_glossary.items():
                if isinstance(src, str) and isinstance(tgt, str) and src.strip():
                    glossary[src] = tgt

        _apply_response_to_chunk(chunk, parsed, target_lang)

        # Commit the turn to history only after success.
        history.append(user_msg)
        history.append({"role": "assistant", "content": content})

    async def _call_llm(self, messages: list[dict[str, str]]):
        """Single LiteLLM round-trip, with response_format fallback and
        context-length error translation. Lifts the shared call/fallback logic
        out of the per-chunk method so eviction can reuse it cleanly.
        """
        import litellm

        kwargs: dict = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            api_key=self.api_key,
        )
        if self.api_base:
            kwargs["api_base"] = self.api_base
        kwargs["response_format"] = {"type": "json_object"}

        try:
            return await litellm.acompletion(**kwargs)
        except Exception as exc:
            if "response_format" in str(exc).lower() or "json_object" in str(exc).lower():
                kwargs.pop("response_format", None)
                try:
                    return await litellm.acompletion(**kwargs)
                except Exception as exc2:
                    if _is_context_length_error(exc2):
                        raise _ContextTooLongError(str(exc2)) from exc2
                    raise
            if _is_context_length_error(exc):
                raise _ContextTooLongError(str(exc)) from exc
            raise

    def _enforce_history_budget(
        self,
        history: list[dict[str, str]],
        pending_user_msg: dict[str, str],
    ) -> None:
        """Evict the oldest chunk pair while the estimated tokens of
        ``history + pending_user_msg`` would exceed ``max_history_tokens``.

        Only the chunk turns (indices >= 3) are eligible. The pinned head —
        [system, intro_user, intro_assistant] — is preserved per requirement:
        "Never evict the system prompt or the document context brief."
        """
        budget = getattr(settings, "max_history_tokens", 96000)

        def _total() -> int:
            total = sum(_approx_tokens(m.get("content", "")) for m in history)
            total += _approx_tokens(pending_user_msg.get("content", ""))
            return total

        while _total() > budget:
            if not self._evict_oldest_pair(history):
                return  # can't shrink further

    @staticmethod
    def _evict_oldest_pair(history: list[dict[str, str]]) -> bool:
        """Drop the oldest user/assistant chunk-turn pair from history.

        The first 3 entries are pinned (system + intro_user + intro_assistant).
        Chunk turns alternate user/assistant starting at index 3. Returns True
        if a pair was removed, False if there's nothing evictable.
        """
        if len(history) < 5:
            return False
        if history[3].get("role") != "user" or history[4].get("role") != "assistant":
            # History shape unexpectedly diverged — bail out rather than
            # corrupt the alternation.
            return False
        del history[3:5]
        return True


def _apply_response_to_chunk(
    chunk: list[Segment],
    parsed: dict,
    target_lang: str,
) -> None:
    """Apply a parsed chunk response onto the chunk's segments.

    Handles all value forms documented in the system prompt:
      - plain string          -> translation (or "" -> drop as OCR noise)
      - {"translation": ...}  -> same as plain string
      - {"skip": true, ...}   -> echo source verbatim, mark _skipped
      - {"merged_ids": [...]} -> this id carries the combined translation;
                                  the listed sibling ids get emptied + marked
                                  _merged_into so renderers drop them.
      - {"merge_into": "<id>"}-> mark this segment as absorbed into <id>.
      - {"splits": [...]}     -> join translated sentences for re-insertion.
    """
    chunk_ids = {seg.id for seg in chunk}
    chunk_by_id = {seg.id: seg for seg in chunk}
    absorbed_into: dict[str, str] = {}  # absorbed_id -> host_id

    def _set_translation(seg: Segment, text: str) -> None:
        if text == "":
            seg.translated = ""
            seg.meta["_ocr_noise"] = True
        else:
            seg.translated = _normalize_target_text(text, target_lang)

    def _fallback_to_original(seg: Segment, reason: str) -> None:
        log.warning("segment %s: %s; keeping original", seg.id, reason)
        seg.translated = seg.meta.get("_original_text", seg.text)

    for seg in chunk:
        val = parsed.get(seg.id)
        if val is None:
            _fallback_to_original(seg, "missing in response")
            continue

        if isinstance(val, str):
            _set_translation(seg, val)
            continue

        if not isinstance(val, dict):
            _fallback_to_original(seg, f"non-string/object value ({type(val).__name__})")
            continue

        merge_into = val.get("merge_into")
        if isinstance(merge_into, str) and merge_into in chunk_ids and merge_into != seg.id:
            absorbed_into[seg.id] = merge_into
            continue

        if val.get("skip") is True:
            original = seg.meta.get("_original_text", seg.text)
            tr = val.get("translation")
            seg.translated = tr if isinstance(tr, str) and tr else original
            seg.meta["_skipped"] = True
            continue

        splits = val.get("splits")
        if isinstance(splits, list) and splits:
            pieces = [s for s in splits if isinstance(s, str) and s]
            if pieces:
                joined = " ".join(_normalize_target_text(p, target_lang) for p in pieces)
                seg.translated = joined
                seg.meta["_split"] = True
                continue
            # Empty list of non-strings — fall through to the generic path so
            # we don't silently lose the segment.

        tr = val.get("translation")
        if isinstance(tr, str):
            _set_translation(seg, tr)
            merged_ids = val.get("merged_ids")
            if isinstance(merged_ids, list):
                for mid in merged_ids:
                    if isinstance(mid, str) and mid in chunk_ids and mid != seg.id:
                        absorbed_into[mid] = seg.id
            continue

        _fallback_to_original(seg, "object value lacked translation/skip/splits/merge_into")

    # Empty out absorbed segments so renderers drop them (PDF) or render as
    # blank (DOCX/PPTX/XLSX). Done in a second pass so the host id's own
    # translation is already populated.
    for absorbed_id, host_id in absorbed_into.items():
        absorbed = chunk_by_id[absorbed_id]
        absorbed.translated = ""
        absorbed.meta["_ocr_noise"] = True
        absorbed.meta["_merged_into"] = host_id


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
