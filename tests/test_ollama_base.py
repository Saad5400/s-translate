"""Ollama api_base rewriting — lets the UI's localhost default work inside Docker.

The web UI defaults the Ollama api_base to http://localhost:11434, but from
inside the s-trans container loopback points at the container itself and the
connection refuses. resolve_ollama_base() honors OLLAMA_API_BASE as an override
for loopback/empty bases so docker-compose can redirect to the real Ollama.
"""
from __future__ import annotations

import pytest

from app.llm.client import LLMClient, resolve_ollama_base


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)


def test_no_override_returns_input():
    assert resolve_ollama_base("http://localhost:11434") == "http://localhost:11434"
    assert resolve_ollama_base(None) is None


def test_override_rewrites_loopback(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama:11434")
    assert resolve_ollama_base("http://localhost:11434") == "http://ollama:11434"
    assert resolve_ollama_base("http://127.0.0.1:11434") == "http://ollama:11434"
    assert resolve_ollama_base("http://0.0.0.0:11434") == "http://ollama:11434"


def test_override_fills_missing_base(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
    assert resolve_ollama_base(None) == "http://host.docker.internal:11434"
    assert resolve_ollama_base("") == "http://host.docker.internal:11434"


def test_override_does_not_touch_remote(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama:11434")
    assert resolve_ollama_base("http://my-ollama.internal:11434") == "http://my-ollama.internal:11434"


def test_client_applies_override_for_ollama_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama:11434")
    c = LLMClient(model="ollama/qwen2.5:7b", api_key="", api_base="http://localhost:11434")
    assert c.api_base == "http://ollama:11434"


def test_client_leaves_non_ollama_untouched(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://ollama:11434")
    c = LLMClient(
        model="openai/gpt-4o-mini",
        api_key="sk-x",
        api_base="http://localhost:11434",
    )
    assert c.api_base == "http://localhost:11434"
