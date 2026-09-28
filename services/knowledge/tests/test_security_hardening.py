"""Stage 9a security-hardening tests: audit log, rate limiting, upload DoS
guard, and login timing (anti-enumeration)."""

from __future__ import annotations

import pytest


def _audit_rows(action: str | None = None):
    from src.db import SessionLocal
    from src.db_models import AuditEvent

    session = SessionLocal()
    try:
        query = session.query(AuditEvent)
        if action is not None:
            query = query.filter(AuditEvent.action == action)
        return query.all()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_audit_service_record_writes_row(api_client):
    """The audit writer persists an event with the given fields."""
    from src import audit_service

    audit_service.record(
        audit_service.ACTION_DELETE,
        user_id="u1",
        workspace_id="w1",
        target="notes.txt",
        ip="1.2.3.4",
    )

    rows = _audit_rows(audit_service.ACTION_DELETE)
    assert len(rows) == 1
    assert rows[0].user_id == "u1"
    assert rows[0].workspace_id == "w1"
    assert rows[0].target == "notes.txt"
    assert rows[0].ip == "1.2.3.4"


def test_login_writes_audit_event(api_client):
    api_client.post(
        "/api/auth/register",
        json={"email": "log@example.com", "password": "password123", "display_name": "Log"},
    )
    api_client.post(
        "/api/auth/login",
        json={"email": "log@example.com", "password": "password123"},
    )

    rows = _audit_rows("login")
    assert len(rows) >= 1
    assert all(r.user_id for r in rows)


def test_upload_writes_audit_event(authed_client, monkeypatch):
    """Upload route records an audit event. The heavy indexing service is
    stubbed so the test stays fast (no model load)."""
    from src.api_models import MaterialActionResponse

    monkeypatch.setattr(
        "src.app_services.start_upload_material_service",
        lambda *a, **k: MaterialActionResponse(ok=True, message="ok", material_name="x.txt"),
    )

    resp = authed_client.post(
        "/api/materials/upload",
        files={"file": ("x.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 200

    rows = _audit_rows("upload")
    assert len(rows) == 1
    assert rows[0].target == "x.txt"


# ---------------------------------------------------------------------------
# Upload DoS guard
# ---------------------------------------------------------------------------


def test_upload_too_large_rejected_with_413(authed_client, monkeypatch):
    """An upload over the size limit is rejected before the indexing service
    runs (early Content-Length / streamed cap)."""
    import api_app

    called = {"hit": False}

    def _should_not_run(*_a, **_k):
        called["hit"] = True
        raise AssertionError("indexing service must not run for an oversized upload")

    monkeypatch.setattr(api_app, "_MAX_UPLOAD_BYTES", 8)
    monkeypatch.setattr("src.app_services.start_upload_material_service", _should_not_run)

    resp = authed_client.post(
        "/api/materials/upload",
        files={"file": ("big.txt", b"this body is definitely longer than eight bytes", "text/plain")},
    )

    assert resp.status_code == 413
    assert called["hit"] is False


# ---------------------------------------------------------------------------
# Login timing — anti-enumeration
# ---------------------------------------------------------------------------


def test_authenticate_runs_verify_for_unknown_email(db_session, monkeypatch):
    """Even when the email doesn't exist, one bcrypt verify runs, so timing
    can't reveal whether an account exists."""
    from fastapi import HTTPException

    from src import auth_service

    calls = {"n": 0}
    real_verify = auth_service.verify_password

    def _counting_verify(password, password_hash):
        calls["n"] += 1
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_service, "verify_password", _counting_verify)

    with pytest.raises(HTTPException) as exc:
        auth_service.authenticate_user(db_session, "ghost@example.com", "whatever")

    assert exc.value.status_code == 401
    assert calls["n"] == 1  # verify ran despite the email being unknown


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_login_is_rate_limited(api_client):
    """Hammering /api/auth/login past the per-IP limit returns 429."""
    from src.rate_limit import limiter

    api_client.post(
        "/api/auth/register",
        json={"email": "rl@example.com", "password": "password123", "display_name": "RL"},
    )

    limiter.enabled = True
    try:
        statuses = [
            api_client.post(
                "/api/auth/login",
                json={"email": "rl@example.com", "password": "wrong-password"},
            ).status_code
            for _ in range(20)
        ]
    finally:
        limiter.enabled = False
        try:
            limiter.reset()
        except Exception:
            pass

    assert 429 in statuses
