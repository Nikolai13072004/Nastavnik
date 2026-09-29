"""GigaChat model fallback chain (no network - _do_request is faked).

Model availability on a freemium key *flaps*: on 01.09.2026 GigaChat-3-Ultra
answered, 404'd, and came back within the hour. The chain must therefore both
degrade gracefully AND return to the preferred model once it recovers.
"""

from __future__ import annotations

import time

import pytest

from src.llm_engine import _GigaChatBackend, _GigaChatModelUnavailable


class _FakeResponse:
    def __init__(self, model):
        self.model = model

    def json(self):
        return {"choices": [{"message": {"content": f"ответ от {self.model}"}}]}


def _backend(monkeypatch, *, chain, alive):
    """Backend whose _do_request answers only for models in ``alive``."""
    monkeypatch.setattr("config.GIGACHAT_AUTH_KEY", "fake", raising=False)
    monkeypatch.setattr("config.GIGACHAT_MODEL", chain[0], raising=False)
    monkeypatch.setattr("config.GIGACHAT_MODEL_FALLBACKS", chain[1:], raising=False)

    backend = _GigaChatBackend()
    calls = []

    def fake_do_request(payload, stream):
        name = payload["model"]
        calls.append(name)
        if name not in alive:
            raise _GigaChatModelUnavailable(name)
        return _FakeResponse(name)

    monkeypatch.setattr(backend, "_do_request", fake_do_request)
    return backend, calls


CHAIN = ["Model-A", "Model-B", "Model-C"]


def test_falls_through_to_the_first_live_model(monkeypatch):
    backend, calls = _backend(monkeypatch, chain=CHAIN, alive={"Model-B"})
    resp = backend._request_any_model({"model": "Model-A"}, stream=False)
    assert resp.model == "Model-B"
    assert calls == ["Model-A", "Model-B"]


def test_dead_model_is_skipped_on_the_next_call(monkeypatch):
    backend, calls = _backend(monkeypatch, chain=CHAIN, alive={"Model-B"})
    backend._request_any_model({"model": "Model-A"}, stream=False)
    calls.clear()

    backend._request_any_model({"model": "Model-A"}, stream=False)
    # Model-A is in cooldown, so the second call must not spend a request on it.
    assert calls == ["Model-B"]


def test_preferred_model_is_retried_after_cooldown(monkeypatch):
    """The point of a *temporary* quarantine: a recovered model gets used again."""
    backend, calls = _backend(monkeypatch, chain=CHAIN, alive={"Model-B"})
    backend._request_any_model({"model": "Model-A"}, stream=False)

    backend._model_cooldown["Model-A"] = time.time() - 1  # quarantine expired
    calls.clear()
    resp = backend._request_any_model({"model": "Model-A"}, stream=False)

    assert calls[0] == "Model-A", "истёкший карантин обязан дать модели второй шанс"
    assert resp.model == "Model-B"


def test_recovered_model_wins_back_the_traffic(monkeypatch):
    alive = {"Model-B"}
    backend, calls = _backend(monkeypatch, chain=CHAIN, alive=alive)
    backend._request_any_model({"model": "Model-A"}, stream=False)
    assert backend._model == "Model-B"

    alive.add("Model-A")  # Model-A вернулась
    backend._model_cooldown.clear()
    resp = backend._request_any_model({"model": "Model-A"}, stream=False)
    assert resp.model == "Model-A"
    assert backend._model == "Model-A"


def test_chain_is_never_permanently_pruned(monkeypatch):
    backend, _ = _backend(monkeypatch, chain=CHAIN, alive={"Model-C"})
    backend._request_any_model({"model": "Model-A"}, stream=False)
    assert backend._models == CHAIN


def test_all_models_dead_raises_with_a_usable_message(monkeypatch):
    backend, _ = _backend(monkeypatch, chain=CHAIN, alive=set())
    with pytest.raises(RuntimeError) as exc:
        backend._request_any_model({"model": "Model-A"}, stream=False)
    assert "GIGACHAT_MODEL" in str(exc.value)
