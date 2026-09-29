"""Provider boundary mocks: no real Telegram/webhook requests."""
import io
import json
from unittest.mock import MagicMock

import pytest

from scripts import notify_delivery as delivery


@pytest.fixture
def transport(monkeypatch):
    for key in ("ALERT_TELEGRAM_BOT_TOKEN", "ALERT_TELEGRAM_CHAT_ID", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.read.return_value = b'{"ok":true}'
    opener = MagicMock()
    opener.open.return_value = response
    build = MagicMock(return_value=opener)
    monkeypatch.setattr(delivery.urllib.request, "build_opener", build)
    monkeypatch.setattr(delivery.sys, "stdin", io.StringIO("synthetic message"))
    return opener, response, build


@pytest.mark.parametrize("url", ["", "http://example.com", "file:///private", "https://user:secret@example.com"])
def test_invalid_channel_fails_without_network(transport, monkeypatch, capsys, url):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", url)
    assert delivery.main() == 1
    transport[0].open.assert_not_called()
    assert "alert_delivery_failed reason=ValueError" in capsys.readouterr().err


def test_webhook_uses_json_and_checked_tls(transport, monkeypatch):
    import ssl
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hook")
    assert delivery.main() == 0
    opener, _, build = transport
    request = opener.open.call_args.args[0]
    assert json.loads(request.data) == {"text": "synthetic message"}
    assert opener.open.call_args.kwargs["timeout"] == 10
    handlers = build.call_args.args
    assert isinstance(handlers[0], delivery.NoRedirect)
    assert handlers[0].redirect_request(None, None, 302, None, None, "https://other.invalid") is None
    assert handlers[1]._context.verify_mode == ssl.CERT_REQUIRED
    assert handlers[1]._context.check_hostname


@pytest.mark.parametrize("body,expected", [(b'{"ok":true}', 0), (b'{"ok":false}', 1),
                                         (b'not-json', 1), (b'[]', 1)])
def test_telegram_requires_provider_success(transport, monkeypatch, body, expected):
    monkeypatch.setenv("ALERT_TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("ALERT_TELEGRAM_CHAT_ID", "synthetic-chat")
    transport[1].read.return_value = body
    assert delivery.main() == expected


def test_delivery_error_does_not_log_secrets(transport, monkeypatch, capsys):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/private-token")
    transport[0].open.side_effect = OSError("private-token synthetic message")
    assert delivery.main() == 1
    output = capsys.readouterr()
    assert "private-token" not in output.err and "synthetic message" not in output.err
    assert "reason=OSError" in output.err


def test_partial_telegram_config_does_not_fall_back(transport, monkeypatch):
    monkeypatch.setenv("ALERT_TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hook")
    assert delivery.main() == 1
    transport[0].open.assert_not_called()


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_non_success_http_status_fails(transport, monkeypatch, status):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.com/hook")
    transport[1].status = status
    assert delivery.main() == 1


def test_bot_errors_uses_shared_watchdog(monkeypatch):
    from scripts import tg_bot
    run_script = MagicMock(return_value="synthetic report")
    monkeypatch.setattr(tg_bot, "_run_script", run_script)
    assert tg_bot.dispatch("/errors") == "synthetic report"
    run_script.assert_called_once_with("alert_5xx.sh", {"ALERT_WINDOW": "24h", "ALERT_ALWAYS_REPORT": "1"})


def test_bot_send_error_is_sanitized(monkeypatch, capsys):
    from scripts import tg_bot
    monkeypatch.setattr(tg_bot, "_api", MagicMock(side_effect=OSError("private-token")))
    tg_bot.send("synthetic message")
    assert capsys.readouterr().err.strip() == "send failed: OSError"
