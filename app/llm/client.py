from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Callable, Iterable

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
from .prompts import SYSTEM_PROMPT, build_user_message

log = logging.getLogger(__name__)

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
        self.api_base = api_base
        self.temperature = temperature
        self.semaphore = asyncio.Semaphore(concurrency)

    async def translate_segments(
        self,
        segments: list[Segment],
        target_lang: str,
        source_lang: str | None = None,
        max_chunk_tokens: int = settings.default_chunk_tokens,
        progress: Callable[[float, str], None] | None = None,
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
                        await self._translate_chunk(chunk, target_lang, source_lang)
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
    ) -> None:
        import litellm

        payload = {seg.id: seg.text for seg in chunk}
        payload_json = json.dumps(payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(payload_json, target_lang, source_lang)},
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
                seg.translated = translated
            else:
                log.warning("segment %s missing in response; keeping original", seg.id)
                seg.translated = seg.meta.get("_original_text", seg.text)


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
