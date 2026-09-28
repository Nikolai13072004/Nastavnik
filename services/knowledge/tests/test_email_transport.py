"""SMTP boundary checks; no external server or real recipient is used."""
import smtplib
import ssl
from unittest.mock import MagicMock

import pytest

import config
from src import email_service


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USER", "synthetic-user")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "synthetic-password")
    monkeypatch.setattr(config, "SMTP_USE_TLS", True)
    monkeypatch.setattr(config, "SMTP_USE_SSL", False)
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(smtplib, "SMTP", MagicMock(return_value=client))
    monkeypatch.setattr(smtplib, "SMTP_SSL", MagicMock(return_value=client))
    return client


@pytest.mark.parametrize("implicit", [False, True])
def test_tls_verifies_chain_and_hostname_before_login(smtp, monkeypatch, implicit):
    monkeypatch.setattr(config, "SMTP_USE_SSL", implicit)
    email_service.send_email("recipient@example.com", "Тема", "Текст")
    if implicit:
        context = smtplib.SMTP_SSL.call_args.kwargs["context"]
        smtp.starttls.assert_not_called()
        smtplib.SMTP.assert_not_called()
    else:
        context = smtp.starttls.call_args.kwargs["context"]
        names = [call[0] for call in smtp.mock_calls]
        assert names.index("starttls") < names.index("login") < names.index("send_message")
        smtplib.SMTP_SSL.assert_not_called()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    smtp.login.assert_called_once_with("synthetic-user", "synthetic-password")
    msg = smtp.send_message.call_args.args[0]
    assert msg["To"] == "recipient@example.com"
    assert msg.get_content().strip() == "Текст"


@pytest.mark.parametrize("error", [
    ssl.SSLCertVerificationError("secret-token recipient@example.com"),
    smtplib.SMTPNotSupportedError("secret-token recipient@example.com"),
])
def test_failed_starttls_never_falls_back_to_login(smtp, vedomo_caplog, error):
    smtp.starttls.side_effect = error
    email_service.send_email("recipient@example.com", "subject", "secret-token")
    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()
    assert "email_delivery_failed" in vedomo_caplog.text
    assert "secret-token" not in vedomo_caplog.text
    assert "recipient@example.com" not in vedomo_caplog.text
    assert all(record.exc_info is None for record in vedomo_caplog.records)


@pytest.mark.parametrize("stage", ["connect", "login", "send_message"])
def test_smtp_failure_is_sanitized_and_can_retry(smtp, stage, vedomo_caplog):
    failure = smtplib.SMTPException("private-password secret-token recipient@example.com")
    target = smtplib.SMTP if stage == "connect" else getattr(smtp, stage)
    target.side_effect = failure
    email_service.send_email("recipient@example.com", "subject", "secret-token")
    assert "email_delivery_failed reason=SMTPException" in vedomo_caplog.text
    for value in ["private-password", "secret-token", "recipient@example.com"]:
        assert value not in vedomo_caplog.text
    target.side_effect = None
    smtp.reset_mock()
    email_service.send_email("recipient@example.com", "subject", "retry")
    smtp.send_message.assert_called_once()


def test_invalid_headers_are_best_effort(smtp, monkeypatch, vedomo_caplog):
    monkeypatch.setattr(config, "EMAIL_FROM", "sender@example.com\nBcc: victim@example.com")
    email_service.send_email("recipient@example.com", "subject", "secret-token")
    smtplib.SMTP.assert_not_called()
    assert "email_delivery_failed reason=ValueError" in vedomo_caplog.text
    assert "victim@example.com" not in vedomo_caplog.text


def test_unknown_backend_does_not_print_reset_link(monkeypatch, capsys, vedomo_caplog):
    monkeypatch.setattr(config, "EMAIL_BACKEND", "smpt")
    email_service.send_email("recipient@example.com", "subject", "secret-token")
    assert capsys.readouterr().out == ""
    assert "reason=invalid_backend" in vedomo_caplog.text
    assert "secret-token" not in vedomo_caplog.text


def test_smtp_outage_preserves_http_contract_and_retry(api_client, smtp):
    smtplib.SMTP.side_effect = TimeoutError("synthetic outage")
    registered = api_client.post("/api/auth/register", json={
        "email": "outage@example.com", "password": "originalpass1",
    })
    assert registered.status_code == 201
    assert registered.json()["user"]["email_verified"] is False
    known = api_client.post("/api/auth/password/forgot", json={"email": "outage@example.com"})
    unknown = api_client.post("/api/auth/password/forgot", json={"email": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()  # response only, not timing equivalence
    assert api_client.post("/api/auth/login", json={
        "email": "outage@example.com", "password": "originalpass1",
    }).status_code == 200

    smtplib.SMTP.side_effect = None
    assert api_client.post("/api/auth/verify/request").status_code == 200
    token = smtp.send_message.call_args.args[0].get_content().split("token=", 1)[1].splitlines()[0]
    assert api_client.post("/api/auth/verify/confirm", json={"token": token}).status_code == 200
    assert api_client.get("/api/auth/me").json()["email_verified"] is True
    assert api_client.post("/api/auth/password/forgot", json={"email": "outage@example.com"}).status_code == 200
    token = smtp.send_message.call_args.args[0].get_content().split("token=", 1)[1].splitlines()[0]
    assert api_client.post("/api/auth/password/reset", json={"token": token, "password": "newpassword42"}).status_code == 200
    assert api_client.post("/api/auth/password/reset", json={"token": token, "password": "otherpassword42"}).status_code == 400
    assert api_client.post("/api/auth/login", json={
        "email": "outage@example.com", "password": "newpassword42",
    }).status_code == 200
