"""Tests for plans + quotas + metering (Stage 12).

Quotas are disabled by default in the suite (conftest sets ``QUOTAS_ENABLED``
off), so these tests flip ``config.QUOTAS_ENABLED`` on where they exercise
enforcement. ``check_quota`` runs *before* any KB/LLM work, so the endpoint
402 tests never load models.
"""

from __future__ import annotations

import uuid

import pytest

import config
from src import auth_service, billing, quota
from src.auth_models import UserCreate
from src.db import SessionLocal
from src.db_models import Document, UsageEvent, User, Workspace


def _make_user_workspace(db, email="u@example.com", plan="free"):
    user = auth_service.register_user(db, UserCreate(email=email, password="password12345"))
    if plan != "free":
        user.plan = plan
        db.commit()
    workspace = auth_service.get_personal_workspace(db, user)
    return user, workspace


def _seed_usage(db, workspace_id, subject_id, action, n):
    for _ in range(n):
        db.add(
            UsageEvent(
                workspace_id=workspace_id,
                user_id=subject_id,
                action=action,
                units=1,
                billing_subject_type="user",
                billing_subject_id=subject_id,
            )
        )
    db.commit()


def _seed_documents(db, workspace_id, owner_user_id, n):
    for i in range(n):
        db.add(
            Document(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                original_name=f"doc-{i}.pdf",
                stored_path=f"docs/{workspace_id}/doc-{i}.pdf",
                status="ready",
            )
        )
    db.commit()


# ---------------------------------------------------------------------------
# Plan + billing context
# ---------------------------------------------------------------------------


def test_new_user_defaults_to_free_plan(api_client):
    api_client.post(
        "/api/auth/register",
        json={"email": "fresh@example.com", "password": "password12345"},
    )
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "fresh@example.com").one()
        assert user.plan == "free"
    finally:
        db.close()


def test_get_billing_context_free_and_pro(db_session):
    _, ws_free = _make_user_workspace(db_session, email="free@example.com", plan="free")
    ctx = billing.get_billing_context(db_session, ws_free)
    assert ctx.plan == "free"
    assert ctx.billing_subject_type == "user"
    assert ctx.billing_subject_id == ws_free.owner_user_id
    assert ctx.limits.chat_per_day == config.PLAN_LIMITS["free"]["chat_per_day"]

    _, ws_pro = _make_user_workspace(db_session, email="pro@example.com", plan="pro")
    ctx_pro = billing.get_billing_context(db_session, ws_pro)
    assert ctx_pro.plan == "pro"
    assert ctx_pro.limits.chat_per_day == config.PLAN_LIMITS["pro"]["chat_per_day"]


def test_unknown_plan_falls_back_to_free(db_session):
    user, ws = _make_user_workspace(db_session, email="weird@example.com")
    user.plan = "enterprise-does-not-exist"
    db_session.commit()
    ctx = billing.get_billing_context(db_session, ws)
    assert ctx.plan == "free"
    assert ctx.limits.max_materials == config.PLAN_LIMITS["free"]["max_materials"]


# ---------------------------------------------------------------------------
# check_quota enforcement (unit)
# ---------------------------------------------------------------------------


def test_chat_quota_blocks_at_daily_limit(db_session, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)
    _, ws = _make_user_workspace(db_session, email="chat@example.com")
    limit = config.PLAN_LIMITS["free"]["chat_per_day"]

    _seed_usage(db_session, ws.id, ws.owner_user_id, "chat", limit - 1)
    quota.check_quota(ws.id, quota.ACTION_CHAT)  # still under — must not raise

    _seed_usage(db_session, ws.id, ws.owner_user_id, "chat", 1)  # now at the limit
    with pytest.raises(quota.QuotaExceeded) as exc:
        quota.check_quota(ws.id, quota.ACTION_CHAT)
    assert exc.value.action == "chat"
    assert exc.value.limit == limit
    assert exc.value.used == limit
    assert exc.value.plan == "free"


def test_pro_plan_has_higher_chat_limit(db_session, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)
    _, ws = _make_user_workspace(db_session, email="propower@example.com", plan="pro")
    # Seed well past the free limit; pro is much higher, so this must not raise.
    _seed_usage(db_session, ws.id, ws.owner_user_id, "chat", config.PLAN_LIMITS["free"]["chat_per_day"] + 5)
    quota.check_quota(ws.id, quota.ACTION_CHAT)


def test_summary_quota_blocks_at_daily_limit(db_session, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)
    _, ws = _make_user_workspace(db_session, email="summary@example.com")
    limit = config.PLAN_LIMITS["free"]["summary_per_day"]
    _seed_usage(db_session, ws.id, ws.owner_user_id, "summary", limit)
    with pytest.raises(quota.QuotaExceeded):
        quota.check_quota(ws.id, quota.ACTION_SUMMARY)


def test_upload_quota_blocks_when_materials_full(db_session, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)
    _, ws = _make_user_workspace(db_session, email="upload@example.com")
    limit = config.PLAN_LIMITS["free"]["max_materials"]

    _seed_documents(db_session, ws.id, ws.owner_user_id, limit - 1)
    quota.check_quota(ws.id, quota.ACTION_UPLOAD)  # one slot left — must not raise

    _seed_documents(db_session, ws.id, ws.owner_user_id, 1)  # now full
    with pytest.raises(quota.QuotaExceeded):
        quota.check_quota(ws.id, quota.ACTION_UPLOAD)


def test_quotas_disabled_never_blocks(db_session, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", False)
    _, ws = _make_user_workspace(db_session, email="nolimit@example.com")
    _seed_usage(db_session, ws.id, ws.owner_user_id, "chat", config.PLAN_LIMITS["free"]["chat_per_day"] + 50)
    quota.check_quota(ws.id, quota.ACTION_CHAT)  # disabled → never raises


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------


def test_record_usage_writes_event(db_session):
    _, ws = _make_user_workspace(db_session, email="meter@example.com")
    quota.record_usage(ws.id, quota.ACTION_CHAT, meta={"answer_mode": "Обычный"})

    events = db_session.query(UsageEvent).filter(UsageEvent.workspace_id == ws.id).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "chat"
    assert ev.billing_subject_type == "user"
    assert ev.billing_subject_id == ws.owner_user_id
    assert ev.meta == {"answer_mode": "Обычный"}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_billing_me_requires_auth(api_client):
    assert api_client.get("/api/billing/me").status_code == 401


def test_billing_me_reports_plan_and_usage(authed_client):
    resp = authed_client.get("/api/billing/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "free"
    assert body["usage"]["chat"]["limit"] == config.PLAN_LIMITS["free"]["chat_per_day"]
    assert body["usage"]["materials"]["limit"] == config.PLAN_LIMITS["free"]["max_materials"]
    assert body["usage"]["chat"]["used"] == 0


def _authed_subject():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "tester@example.com").one()
        ws = db.query(Workspace).filter(Workspace.owner_user_id == user.id).one()
        return user.id, ws.id
    finally:
        db.close()


def test_chat_endpoint_returns_402_when_over_quota(authed_client, monkeypatch):
    """check_quota is the first line of chat_service, so this 402s without ever
    touching the KB/LLM."""
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)
    user_id, ws_id = _authed_subject()

    db = SessionLocal()
    try:
        _seed_usage(db, ws_id, user_id, "chat", config.PLAN_LIMITS["free"]["chat_per_day"])
    finally:
        db.close()

    resp = authed_client.post("/api/chat", json={"message": "вопрос по материалу"})
    assert resp.status_code == 402
    body = resp.json()
    assert body["error"] == "quota_exceeded"
    assert body["action"] == "chat"
    assert body["plan"] == "free"


def test_upload_endpoint_returns_402_when_materials_full(authed_client, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)
    user_id, ws_id = _authed_subject()

    db = SessionLocal()
    try:
        _seed_documents(db, ws_id, user_id, config.PLAN_LIMITS["free"]["max_materials"])
    finally:
        db.close()

    resp = authed_client.post(
        "/api/materials/upload",
        files={"file": ("extra.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 402
    assert resp.json()["action"] == "upload"
