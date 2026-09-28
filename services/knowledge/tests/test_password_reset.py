"""Tests for the password-reset flow (Stage 23).

Covers the service helpers (token mint/consume, single-use, expiry, no
enumeration, session revocation) and the HTTP endpoints (identical responses
for known/unknown emails, full forgot -> reset -> login round-trip).

The ``memory`` email backend (set in ``conftest``) collects sent mail into
``email_service.OUTBOX``; we read the reset link out of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src import auth_service, email_service
from src.auth_models import UserCreate
from src.db_models import EmailToken, User
from src.security import verify_password


def _register(db, email="user@example.com", password="originalpass1"):
    return auth_service.register_user(
        db, UserCreate(email=email, password=password, display_name="U")
    )


def _link_token(body: str) -> str:
    """Pull the raw token out of a reset email body."""
    return body.split("token=", 1)[1].splitlines()[0].strip()


# --- token helpers ---------------------------------------------------------


def test_create_and_consume_token_roundtrip(db_session):
    user = _register(db_session)
    raw = auth_service.create_email_token(db_session, user, purpose="reset", ttl_minutes=60)
    db_session.commit()

    # Only the hash is stored, never the raw token.
    stored = db_session.execute(select(EmailToken)).scalar_one()
    assert stored.token_hash != raw
    assert stored.used_at is None

    consumed = auth_service.consume_email_token(db_session, raw, purpose="reset")
    assert consumed is not None and consumed.id == user.id
    # Spending it stamps used_at. consume_email_token does not commit (the caller
    # does), so commit here as the real flow would before re-reading.
    db_session.commit()
    db_session.refresh(stored)
    assert stored.used_at is not None


def test_token_is_single_use(db_session):
    user = _register(db_session)
    raw = auth_service.create_email_token(db_session, user, purpose="reset", ttl_minutes=60)
    db_session.commit()

    assert auth_service.consume_email_token(db_session, raw, purpose="reset") is not None
    # Second use fails.
    assert auth_service.consume_email_token(db_session, raw, purpose="reset") is None


def test_token_rejects_wrong_purpose(db_session):
    user = _register(db_session)
    raw = auth_service.create_email_token(db_session, user, purpose="reset", ttl_minutes=60)
    db_session.commit()
    assert auth_service.consume_email_token(db_session, raw, purpose="verify") is None


def test_token_rejects_expired(db_session):
    user = _register(db_session)
    raw = auth_service.create_email_token(db_session, user, purpose="reset", ttl_minutes=60)
    token = db_session.execute(select(EmailToken)).scalar_one()
    token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert auth_service.consume_email_token(db_session, raw, purpose="reset") is None


def test_consume_rejects_garbage(db_session):
    assert auth_service.consume_email_token(db_session, "not-a-real-token", purpose="reset") is None


# --- request_password_reset (no enumeration) -------------------------------


def test_request_reset_sends_email_for_known_user(db_session):
    email_service.OUTBOX.clear()
    _register(db_session, email="known@example.com")
    auth_service.request_password_reset(db_session, "known@example.com")
    assert len(email_service.OUTBOX) == 1
    assert email_service.OUTBOX[0].to == "known@example.com"
    assert "token=" in email_service.OUTBOX[0].body


def test_request_reset_silent_for_unknown_email(db_session):
    email_service.OUTBOX.clear()
    auth_service.request_password_reset(db_session, "ghost@example.com")
    assert email_service.OUTBOX == []


def test_request_reset_silent_for_inactive_user(db_session):
    email_service.OUTBOX.clear()
    user = _register(db_session, email="banned@example.com")
    user.is_active = False
    db_session.commit()
    auth_service.request_password_reset(db_session, "banned@example.com")
    assert email_service.OUTBOX == []


# --- reset_password --------------------------------------------------------


def test_reset_password_changes_credentials(db_session):
    email_service.OUTBOX.clear()
    _register(db_session, email="rot@example.com", password="originalpass1")
    auth_service.request_password_reset(db_session, "rot@example.com")
    raw = _link_token(email_service.OUTBOX[0].body)

    auth_service.reset_password(db_session, raw, "brandnewpass2")

    user = db_session.execute(
        select(User).where(User.email == "rot@example.com")
    ).scalar_one()
    assert verify_password("brandnewpass2", user.password_hash)
    assert not verify_password("originalpass1", user.password_hash)


def test_reset_password_revokes_existing_sessions(db_session):
    _register(db_session, email="revoke@example.com")
    user = db_session.execute(
        select(User).where(User.email == "revoke@example.com")
    ).scalar_one()
    assert user.tokens_valid_after is None

    raw = auth_service.create_email_token(db_session, user, purpose="reset", ttl_minutes=60)
    db_session.commit()
    auth_service.reset_password(db_session, raw, "brandnewpass2")

    db_session.refresh(user)
    assert user.tokens_valid_after is not None


def test_reset_password_rejects_bad_token(db_session):
    with pytest.raises(HTTPException) as info:
        auth_service.reset_password(db_session, "garbage", "brandnewpass2")
    assert info.value.status_code == 400
    assert info.value.detail == "invalid_or_expired_token"


# --- HTTP endpoints --------------------------------------------------------


def test_forgot_endpoint_same_response_known_and_unknown(api_client):
    api_client.post(
        "/api/auth/register",
        json={"email": "real@example.com", "password": "originalpass1"},
    )
    # Clear the verification email register just sent, so we count only the
    # forgot-password mail below.
    email_service.OUTBOX.clear()

    r_known = api_client.post("/api/auth/password/forgot", json={"email": "real@example.com"})
    r_unknown = api_client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})

    assert r_known.status_code == 200
    assert r_unknown.status_code == 200
    # Identical body - no signal about which emails exist.
    assert r_known.json() == r_unknown.json()
    # Only the real account got an email.
    assert len(email_service.OUTBOX) == 1
    assert email_service.OUTBOX[0].to == "real@example.com"


def test_full_reset_flow_over_http(api_client):
    email_service.OUTBOX.clear()
    api_client.post(
        "/api/auth/register",
        json={"email": "flow@example.com", "password": "originalpass1"},
    )

    api_client.post("/api/auth/password/forgot", json={"email": "flow@example.com"})
    raw = _link_token(email_service.OUTBOX[-1].body)

    reset = api_client.post(
        "/api/auth/password/reset",
        json={"token": raw, "password": "brandnewpass2"},
    )
    assert reset.status_code == 200

    # Old password no longer works.
    bad = api_client.post(
        "/api/auth/login",
        json={"email": "flow@example.com", "password": "originalpass1"},
    )
    assert bad.status_code == 401

    # New password works.
    good = api_client.post(
        "/api/auth/login",
        json={"email": "flow@example.com", "password": "brandnewpass2"},
    )
    assert good.status_code == 200


def test_reset_endpoint_rejects_bad_token(api_client):
    r = api_client.post(
        "/api/auth/password/reset",
        json={"token": "totally-invalid", "password": "brandnewpass2"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_or_expired_token"
