"""Provider configuration checks without API calls or real credentials."""

import pytest

import config
from src.llm_engine import _GigaChatBackend


@pytest.fixture
def backend_settings(monkeypatch):
    monkeypatch.setattr(config, "GIGACHAT_AUTH_KEY", "test-key")
    monkeypatch.setattr(config, "GIGACHAT_BASE_URL", "https://api.giga.chat/v1")
    monkeypatch.setattr(config, "GIGACHAT_CA_BUNDLE", "")
    monkeypatch.setattr(config, "GIGACHAT_VERIFY_SSL", True)


def test_current_endpoint_and_tls(backend_settings):
    backend = _GigaChatBackend()
    assert backend._CHAT_URL == "https://api.giga.chat/v1/chat/completions"
    assert backend._verify_ssl is True


def test_explicit_legacy_endpoint_is_supported(backend_settings, monkeypatch):
    monkeypatch.setattr(config, "GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1/")
    backend = _GigaChatBackend()
    assert backend._CHAT_URL == "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"


def test_certificate_bundle_is_used(backend_settings, monkeypatch):
    monkeypatch.setattr(config, "GIGACHAT_CA_BUNDLE", "/run/certs/gigachat-ca.pem")
    assert _GigaChatBackend()._verify_ssl == "/run/certs/gigachat-ca.pem"


def test_oauth_and_chat_use_the_same_certificate_bundle(backend_settings, monkeypatch):
    import time

    monkeypatch.setattr(config, "GIGACHAT_CA_BUNDLE", "/run/certs/gigachat-ca.pem")
    backend = _GigaChatBackend()
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"access_token": "test-token", "expires_at": (time.time() + 3600) * 1000}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(backend._requests, "post", post)
    backend._get_token()
    backend._do_request({"model": backend._model}, stream=False)

    assert [url for url, _ in calls] == [backend._OAUTH_URL, backend._CHAT_URL]
    assert all(kwargs["verify"] == "/run/certs/gigachat-ca.pem" for _, kwargs in calls)


@pytest.mark.parametrize("endpoint", [
    "http://api.giga.chat/v1",
    "https://user:password@api.giga.chat/v1",
    "https://api.giga.chat/v1?token=secret",
    "https://api.giga.chat/v1#fragment",
    "https:///v1",
])
def test_invalid_endpoint_fails_before_authorization(backend_settings, monkeypatch, endpoint):
    monkeypatch.setattr(config, "GIGACHAT_BASE_URL", endpoint)
    with pytest.raises(ValueError, match="HTTPS API URL"):
        _GigaChatBackend()
