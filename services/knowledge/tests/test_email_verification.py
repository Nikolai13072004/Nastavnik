"""Tests for the email-verification flow (Stage 23).

Registration mails a verification link and the account starts unverified;
confirming the single-use token flips ``email_verified``. The ``memory`` email
backend (conftest) captures the link.
"""

from __future__ import annotations

from src import auth_service, email_service
from src.auth_models import UserCreate


def _link_token(body: str) -> str:
    return body.split("token=", 1)[1].splitlines()[0].strip()


def test_register_sends_verification_and_starts_unverified(api_client):
    email_service.OUTBOX.clear()
    r = api_client.post(
        "/api/auth/register",
        json={"email": "v@example.com", "password": "originalpass1"},
    )
    assert r.status_code == 201
    assert r.json()["user"]["email_verified"] is False
    assert len(email_service.OUTBOX) == 1
    assert "verify-email?token=" in email_service.OUTBOX[0].body


def test_confirm_marks_verified(api_client):
    email_service.OUTBOX.clear()
    api_client.post(
        "/api/auth/register",
        json={"email": "v2@example.com", "password": "originalpass1"},
    )
    raw = _link_token(email_service.OUTBOX[-1].body)

    confirm = api_client.post("/api/auth/verify/confirm", json={"token": raw})
    assert confirm.status_code == 200

    me = api_client.get("/api/auth/me")
    assert me.json()["email_verified"] is True


def test_confirm_rejects_bad_token(api_client):
    api_client.post(
        "/api/auth/register",
        json={"email": "v3@example.com", "password": "originalpass1"},
    )
    r = api_client.post("/api/auth/verify/confirm", json={"token": "not-valid"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_or_expired_token"


def test_confirm_token_is_single_use(api_client):
    email_service.OUTBOX.clear()
    api_client.post(
        "/api/auth/register",
        json={"email": "v4@example.com", "password": "originalpass1"},
    )
    raw = _link_token(email_service.OUTBOX[-1].body)
    assert api_client.post("/api/auth/verify/confirm", json={"token": raw}).status_code == 200
    assert api_client.post("/api/auth/verify/confirm", json={"token": raw}).status_code == 400


def test_verify_request_resends_for_unverified(api_client):
    email_service.OUTBOX.clear()
    api_client.post(
        "/api/auth/register",
        json={"email": "v5@example.com", "password": "originalpass1"},
    )
    before = len(email_service.OUTBOX)
    r = api_client.post("/api/auth/verify/request")
    assert r.status_code == 200
    assert len(email_service.OUTBOX) == before + 1


def test_verify_request_is_noop_when_already_verified(api_client):
    email_service.OUTBOX.clear()
    api_client.post(
        "/api/auth/register",
        json={"email": "v6@example.com", "password": "originalpass1"},
    )
    raw = _link_token(email_service.OUTBOX[-1].body)
    api_client.post("/api/auth/verify/confirm", json={"token": raw})

    email_service.OUTBOX.clear()
    r = api_client.post("/api/auth/verify/request")
    assert r.status_code == 200
    assert email_service.OUTBOX == []  # already verified -> nothing sent


def test_verify_request_requires_auth(api_client):
    # No cookie on a fresh client.
    r = api_client.post("/api/auth/verify/request")
    assert r.status_code == 401


def test_confirm_email_service_sets_timestamp(db_session):
    user = auth_service.register_user(
        db_session, UserCreate(email="s@example.com", password="originalpass1")
    )
    assert user.email_verified_at is None

    raw = auth_service.create_email_token(db_session, user, purpose="verify", ttl_minutes=60)
    db_session.commit()
    auth_service.confirm_email(db_session, raw)

    db_session.refresh(user)
    assert user.email_verified_at is not None
