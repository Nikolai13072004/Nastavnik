"""The manual API check uses synthetic input and never prints provider error bodies."""

import pytest

import config
from scripts import check_gigachat


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODE", "gigachat")
    monkeypatch.setattr(config, "GIGACHAT_AUTH_KEY", "test-key")
    monkeypatch.setattr(config, "GIGACHAT_VERIFY_SSL", True)
    monkeypatch.setattr(config, "GIGACHAT_CA_BUNDLE", "")


@pytest.mark.parametrize("name,value", [
    ("LLM_MODE", "ollama"),
    ("GIGACHAT_AUTH_KEY", ""),
    ("GIGACHAT_VERIFY_SSL", False),
])
def test_invalid_settings_fail_before_network(settings, monkeypatch, name, value):
    monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(check_gigachat, "_GigaChatBackend", lambda: pytest.fail("Network must not run"))
    with pytest.raises(ValueError):
        check_gigachat.check()


@pytest.mark.parametrize("available,answer,succeeds", [
    (True, "4", True),
    (False, "4", False),
    (True, "5", False),
    (True, "", False),
])
def test_checks_selected_model_and_synthetic_answer(settings, monkeypatch, available, answer, succeeds):
    from types import SimpleNamespace

    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "selected-model" if available else "fallback-model"}]}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    def generate(prompt, temperature, max_tokens):
        assert "2 + 2" in prompt
        assert max_tokens == 16
        assert backend._models == ["selected-model"]
        yield answer

    backend = SimpleNamespace(
        _model="selected-model",
        _models=["selected-model", "fallback-model"],
        _base_url="https://api.giga.chat/v1",
        _verify_ssl=True,
        _get_token=lambda: "private-token",
        _requests=SimpleNamespace(get=get),
        generate=generate,
    )
    monkeypatch.setattr(check_gigachat, "_GigaChatBackend", lambda: backend)
    if succeeds:
        assert check_gigachat.check()["model"] == "selected-model"
    else:
        with pytest.raises(ValueError):
            check_gigachat.check()
    assert calls[0][0] == "https://api.giga.chat/v1/models"
    assert calls[0][1]["verify"] is True


def test_error_output_does_not_reveal_private_data(monkeypatch, capsys):
    def fail():
        raise RuntimeError("Authorization: secret-key; access_token=private-token")

    monkeypatch.setattr(check_gigachat, "check", fail)
    assert check_gigachat.main() == 1
    output = capsys.readouterr().out
    assert "GIGACHAT_CHECK_FAILED" in output
    assert "secret-key" not in output
    assert "private-token" not in output
