"""Vision (фото → текст): выбор бэкенда по LLM_MODE + уборка за собой.

Сеть не трогаем — ``requests`` подменяется. Проверяем контракт, а не качество
распознавания: какой провайдер вызван, что улетело в запрос и удаляется ли
загруженный файл (иначе каждое распознанное фото навсегда оседает в аккаунте
пользователя GigaChat).
"""

from __future__ import annotations

import pytest

import config
from src import llm_engine


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeRequests:
    """Пишет каждый вызов в ``calls``, отвечает по совпадению пути."""

    def __init__(self, *, upload_status=200, chat_status=200, answer="распознанный текст"):
        self.calls = []
        self._upload_status = upload_status
        self._chat_status = chat_status
        self._answer = answer

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/files"):
            return _Resp(self._upload_status, {"id": "file-42"}, "upload failed")
        if url.endswith("/delete"):
            return _Resp(200, {"id": "file-42", "deleted": True})
        if "chat/completions" in url:
            return _Resp(
                self._chat_status,
                {"choices": [{"message": {"content": self._answer}}]},
                "chat failed",
            )
        raise AssertionError(f"неожиданный вызов: {url}")

    def urls(self):
        return [u for u, _ in self.calls]


@pytest.fixture
def gigachat_mode(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODE", "gigachat")
    monkeypatch.setattr(config, "GIGACHAT_AUTH_KEY", "fake-key")
    monkeypatch.setattr(llm_engine._GigaChatBackend, "_get_token", lambda self, **_: "tok")


def _install(monkeypatch, fake):
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)


def test_gigachat_mode_uploads_then_asks_then_deletes(gigachat_mode, monkeypatch):
    fake = _FakeRequests()
    _install(monkeypatch, fake)

    assert llm_engine.transcribe_image(b"\x89PNG", "image/png") == "распознанный текст"

    urls = fake.urls()
    assert urls[0].endswith("/files"), "сначала загрузка файла"
    assert "chat/completions" in urls[1], "затем запрос с вложением"
    assert urls[2].endswith("/delete"), "и уборка загруженного файла"


def test_gigachat_request_attaches_the_uploaded_file(gigachat_mode, monkeypatch):
    fake = _FakeRequests()
    _install(monkeypatch, fake)
    llm_engine.transcribe_image(b"\x89PNG", "image/png")

    _, chat_kwargs = fake.calls[1]
    message = chat_kwargs["json"]["messages"][0]
    assert message["attachments"] == ["file-42"]
    assert chat_kwargs["json"]["model"] == config.GIGACHAT_VISION_MODEL


def test_uploaded_file_is_deleted_even_when_recognition_fails(gigachat_mode, monkeypatch):
    # Иначе неудачное распознавание оставляло бы мусор в аккаунте навсегда.
    fake = _FakeRequests(chat_status=500)
    _install(monkeypatch, fake)

    with pytest.raises(RuntimeError):
        llm_engine.transcribe_image(b"\x89PNG", "image/png")

    assert any(u.endswith("/delete") for u in fake.urls()), "файл должен убираться и на ошибке"


def test_failed_upload_reports_clearly(gigachat_mode, monkeypatch):
    fake = _FakeRequests(upload_status=413)
    _install(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="загрузка фото"):
        llm_engine.transcribe_image(b"\x89PNG", "image/png")


def test_ollama_mode_says_vision_is_unavailable(monkeypatch):
    # Раньше здесь говорилось "только в API-режиме (OpenRouter)" — после
    # появления GigaChat это стало неправдой.
    monkeypatch.setattr(config, "LLM_MODE", "ollama")
    with pytest.raises(RuntimeError, match="gigachat"):
        llm_engine.transcribe_image(b"\x89PNG", "image/png")


def test_api_mode_still_routes_to_openrouter(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODE", "api")
    called = {}

    def fake_openrouter(image_bytes, mime_type="image/png"):
        called["hit"] = mime_type
        return "openrouter ответ"

    monkeypatch.setattr(llm_engine, "_transcribe_via_openrouter", fake_openrouter)
    assert llm_engine.transcribe_image(b"\x89PNG", "image/jpeg") == "openrouter ответ"
    assert called["hit"] == "image/jpeg"


def test_gigachat_vision_uses_configured_endpoint_and_certificate(gigachat_mode, monkeypatch):
    monkeypatch.setattr(config, "GIGACHAT_BASE_URL", "https://api.giga.chat/v1")
    monkeypatch.setattr(config, "GIGACHAT_CA_BUNDLE", "/run/certs/gigachat-ca.pem")
    fake = _FakeRequests()
    _install(monkeypatch, fake)

    llm_engine.transcribe_image(b"\x89PNG", "image/png")

    assert all(url.startswith("https://api.giga.chat/v1/") for url in fake.urls())
    assert all(kwargs["verify"] == "/run/certs/gigachat-ca.pem" for _, kwargs in fake.calls)
