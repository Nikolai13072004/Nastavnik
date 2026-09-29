"""Local Ollama may run on the Docker host instead of inside the API container."""

import os
from types import SimpleNamespace

import config
from src.llm_engine import _OllamaBackend


def test_availability_uses_configured_ollama_host(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
    monkeypatch.setenv("NO_PROXY", "existing.local")
    monkeypatch.setenv("no_proxy", "existing.local")

    backend = _OllamaBackend()
    requested = []
    monkeypatch.setattr(backend._requests, "get", lambda url, timeout: (
        requested.append((url, timeout)) or SimpleNamespace(status_code=200)
    ))

    assert backend.is_available()
    assert requested == [("http://host.docker.internal:11434/api/tags", 3)]
    assert {"existing.local", "host.docker.internal"}.issubset(
        set(os.environ["NO_PROXY"].split(","))
    )
