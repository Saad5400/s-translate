"""Unit tests for the per-document conversation behavior in llm.client.

Covers:
  * Richer per-segment response schema (skip / merged_ids / merge_into / splits).
  * Continuous-conversation eviction: oldest user+assistant chunk turn pair
    drops first; system + intro stay pinned.
  * Running "_glossary" accumulates across chunks and survives eviction by
    being re-injected into every chunk user message.

These tests stub out the litellm call so they run offline and fast.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.llm import client as client_mod
from app.llm.client import (
    LLMClient,
    _apply_response_to_chunk,
)
from app.schemas import Segment


def _seg(sid: str, text: str = "hello") -> Segment:
    s = Segment(id=sid, text=text)
    s.meta["_original_text"] = text
    return s


# ---------- _apply_response_to_chunk schema handling -------------------------


def test_apply_plain_string_translation():
    seg = _seg("a", "hello")
    _apply_response_to_chunk([seg], {"a": "hola"}, "es")
    assert seg.translated == "hola"


def test_apply_empty_string_marks_ocr_noise():
    seg = _seg("a", "fh")
    _apply_response_to_chunk([seg], {"a": ""}, "es")
    assert seg.translated == ""
    assert seg.meta.get("_ocr_noise") is True


def test_apply_skip_echoes_source():
    seg = _seg("a", "https://example.com")
    _apply_response_to_chunk([seg], {"a": {"skip": True, "translation": "https://example.com"}}, "es")
    assert seg.translated == "https://example.com"
    assert seg.meta.get("_skipped") is True


def test_apply_skip_without_translation_uses_original():
    seg = _seg("a", "DevOps")
    _apply_response_to_chunk([seg], {"a": {"skip": True}}, "es")
    assert seg.translated == "DevOps"
    assert seg.meta.get("_skipped") is True


def test_apply_merged_ids_absorbs_siblings():
    a, b, c = _seg("a", "The quick"), _seg("b", "brown fox"), _seg("c", "jumps.")
    parsed = {
        "a": {"translation": "El rápido zorro marrón salta.", "merged_ids": ["b", "c"]},
        "b": {"merge_into": "a"},
        "c": {"merge_into": "a"},
    }
    _apply_response_to_chunk([a, b, c], parsed, "es")
    assert a.translated == "El rápido zorro marrón salta."
    assert b.translated == ""
    assert b.meta.get("_merged_into") == "a"
    assert b.meta.get("_ocr_noise") is True
    assert c.translated == ""
    assert c.meta.get("_merged_into") == "a"


def test_apply_splits_joins_sentences():
    seg = _seg("a", "One. Two.")
    _apply_response_to_chunk([seg], {"a": {"splits": ["Uno.", "Dos."]}}, "es")
    assert seg.translated == "Uno. Dos."
    assert seg.meta.get("_split") is True


def test_apply_missing_id_falls_back_to_original():
    seg = _seg("a", "hello")
    _apply_response_to_chunk([seg], {}, "es")
    assert seg.translated == "hello"


def test_apply_malformed_value_falls_back_to_original():
    seg = _seg("a", "hello")
    _apply_response_to_chunk([seg], {"a": {"foo": "bar"}}, "es")
    assert seg.translated == "hello"


# ---------- Conversation eviction -------------------------------------------


def _make_client() -> LLMClient:
    # api_key / model values are arbitrary — we stub out the call entirely.
    return LLMClient(model="stub/stub", api_key="x")


def test_evict_oldest_pair_drops_first_user_assistant_pair_after_intro():
    c = _make_client()
    history = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "intro"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "chunk1"},
        {"role": "assistant", "content": "resp1"},
        {"role": "user", "content": "chunk2"},
        {"role": "assistant", "content": "resp2"},
    ]
    assert c._evict_oldest_pair(history) is True
    # System + intro turn pinned.
    assert [m["content"] for m in history[:3]] == ["S", "intro", "ack"]
    # First chunk pair dropped, second chunk pair remains.
    assert history[3]["content"] == "chunk2"
    assert history[4]["content"] == "resp2"
    assert len(history) == 5


def test_evict_oldest_pair_refuses_when_only_intro_left():
    c = _make_client()
    history = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "intro"},
        {"role": "assistant", "content": "ack"},
    ]
    assert c._evict_oldest_pair(history) is False
    assert len(history) == 3  # untouched


# ---------- End-to-end conversation flow with mocked LLM ---------------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.mark.asyncio
async def test_continuous_conversation_carries_history_and_glossary(monkeypatch):
    """Two chunks: first establishes a glossary entry, second sees that entry
    re-injected into its user message AND the first turn's user/assistant
    survive in history (no eviction yet). Verifies that we send one
    continuing conversation, not two independent chats.
    """
    c = _make_client()
    sent_messages: list[list[dict]] = []

    async def fake_call(messages):
        # Record what we sent on each round-trip.
        sent_messages.append([dict(m) for m in messages])
        if len(sent_messages) == 1:
            return _FakeResp(json.dumps({
                "s1": "hola",
                "_glossary": {"sprint": "sprint"},
            }, ensure_ascii=False))
        return _FakeResp(json.dumps({"s2": "mundo"}, ensure_ascii=False))

    monkeypatch.setattr(c, "_call_llm", fake_call)
    # Force one segment per chunk so we get two chunks deterministically.
    monkeypatch.setattr(
        client_mod, "chunk_segments",
        lambda segs, max_tokens=2500: [[s] for s in segs],
    )
    # Make sure stub translator is OFF for this test path.
    client_mod.set_stub_translator(None)

    segs = [Segment(id="s1", text="hello"), Segment(id="s2", text="world")]
    await c.translate_segments(segs, target_lang="es", context="some brief")

    assert [s.translated for s in segs] == ["hola", "mundo"]

    # First call: 3 pinned turns + 1 new user = 4 messages, glossary not yet present.
    assert len(sent_messages[0]) == 4
    assert sent_messages[0][0]["role"] == "system"
    assert "context brief" in sent_messages[0][1]["content"].lower()
    assert sent_messages[0][3]["role"] == "user"
    assert "Carry-forward glossary" not in sent_messages[0][3]["content"]

    # Second call: prior user+assistant pair preserved (no eviction), and the
    # glossary is injected into the new user message.
    assert len(sent_messages[1]) == 6
    assert sent_messages[1][3]["role"] == "user"   # chunk1 user
    assert sent_messages[1][4]["role"] == "assistant"  # chunk1 reply
    assert sent_messages[1][5]["role"] == "user"   # chunk2 user
    assert "sprint" in sent_messages[1][5]["content"]
    assert "Carry-forward glossary" in sent_messages[1][5]["content"]


@pytest.mark.asyncio
async def test_eviction_under_tight_budget_preserves_intro(monkeypatch):
    """With ``max_history_tokens`` clamped very low, the oldest chunk pair
    must evict before the next chunk is sent, while the system + intro turn
    stay pinned and the glossary survives via re-injection.
    """
    from app import config as cfg

    c = _make_client()
    sent_messages: list[list[dict]] = []

    async def fake_call(messages):
        sent_messages.append([dict(m) for m in messages])
        n = len(sent_messages)
        return _FakeResp(json.dumps(
            {f"s{n}": f"t{n}", "_glossary": {f"k{n}": f"v{n}"}},
            ensure_ascii=False,
        ))

    monkeypatch.setattr(c, "_call_llm", fake_call)
    monkeypatch.setattr(
        client_mod, "chunk_segments",
        lambda segs, max_tokens=2500: [[s] for s in segs],
    )
    monkeypatch.setattr(cfg.settings, "max_history_tokens", 1)  # force eviction every turn
    client_mod.set_stub_translator(None)

    segs = [Segment(id=f"s{i}", text=f"t{i}") for i in range(1, 4)]
    await c.translate_segments(segs, target_lang="es", context="brief here")

    # Three round trips fired.
    assert len(sent_messages) == 3

    # On the 3rd call, the first two chunk pairs must have been evicted —
    # so we should see exactly the 3 pinned turns + 1 new user.
    last = sent_messages[2]
    assert len(last) == 4
    assert last[0]["role"] == "system"
    # Intro (with context brief) preserved.
    assert "brief here" in last[1]["content"]
    assert last[2]["role"] == "assistant"
    # The new user message carries the accumulated glossary from the
    # previous two assistant responses, even though their turns were evicted.
    new_user = last[3]["content"]
    assert "k1" in new_user and "k2" in new_user
