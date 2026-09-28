"""Tests for the superuser-only admin endpoints (Stage 9b).

Covers access control (401 anonymous / 403 regular user / 200 superuser) plus
the shape and content of ``/api/admin/stats`` and ``/api/admin/audit``.
"""

from __future__ import annotations

import pytest


ADMIN_PATHS = ["/api/admin/stats", "/api/admin/audit", "/api/admin/users"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_requires_authentication(api_client, path):
    """Anonymous callers get 401 on every admin endpoint."""
    resp = api_client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_forbidden_for_regular_user(authed_client, path):
    """A logged-in non-superuser gets 403 — the admin surface is fully gated."""
    resp = authed_client.get(path)
    assert resp.status_code == 403


class _StubKB:
    """Stand-in for ``runtime.get_kb()`` so the reconcile endpoint test never
    instantiates the real KnowledgeBase (which would load the 2 GB BGE models)."""

    def list_workspace_ids(self):
        return []

    def remove_orphan_chunks(self, **kwargs):
        return {"removed_chunks": 0, "removed_document_ids": []}


def test_admin_reconcile_requires_authentication(api_client):
    assert api_client.post("/api/admin/reconcile").status_code == 401


def test_admin_reconcile_forbidden_for_regular_user(authed_client):
    assert authed_client.post("/api/admin/reconcile").status_code == 403


def test_admin_reconcile_runs_for_superuser(superuser_client, monkeypatch):
    """Superuser can run reconcile; response carries the totals shape.

    ``runtime.get_kb`` is stubbed so the test exercises the endpoint wiring
    (auth gate → service → audit → response model) without loading models.
    """
    from src import runtime

    monkeypatch.setattr(runtime, "get_kb", lambda: _StubKB())

    resp = superuser_client.post("/api/admin/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"workspaces", "total_removed_chunks", "total_removed_documents"}
    assert body["workspaces"] == []
    assert body["total_removed_chunks"] == 0
    assert body["total_removed_documents"] == 0


def test_admin_stats_counts_whole_instance(superuser_client):
    """Stats count every user/workspace in the instance, not just the caller's."""
    from src import auth_service
    from src.auth_models import UserCreate
    from src.db import SessionLocal

    # Seed a second user directly via the service (not through the HTTP client,
    # which would replace the superuser's session cookie with the new user's).
    db = SessionLocal()
    try:
        auth_service.register_user(
            db, UserCreate(email="second@example.com", password="password12345")
        )
    finally:
        db.close()

    resp = superuser_client.get("/api/admin/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"users", "workspaces", "documents", "audit_events"}
    # admin (from the fixture) + the second user we just seeded.
    assert body["users"] == 2
    assert body["workspaces"] == 2
    assert body["documents"] == 0
    assert body["audit_events"] >= 0


def test_admin_audit_lists_recent_events_newest_first(superuser_client):
    """The audit endpoint surfaces recorded events newest-first with full fields."""
    from src import audit_service

    audit_service.record(audit_service.ACTION_UPLOAD, target="first.pdf", ip="1.1.1.1")
    audit_service.record(audit_service.ACTION_DELETE, target="second.pdf", ip="2.2.2.2")

    resp = superuser_client.get("/api/admin/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) >= 2

    # Newest first: the DELETE we recorded last leads.
    top = events[0]
    assert top["action"] == audit_service.ACTION_DELETE
    assert top["target"] == "second.pdf"
    assert top["ip"] == "2.2.2.2"
    assert events[1]["action"] == audit_service.ACTION_UPLOAD
    assert set(top) >= {
        "id",
        "action",
        "user_id",
        "workspace_id",
        "target",
        "ip",
        "created_at",
    }


def test_admin_audit_limit_is_clamped(superuser_client):
    """``limit`` below 1 clamps up to 1; a huge limit just returns everything."""
    from src import audit_service

    for i in range(5):
        audit_service.record(audit_service.ACTION_LOGIN, target=f"event-{i}")

    resp = superuser_client.get("/api/admin/audit", params={"limit": 0})
    assert resp.status_code == 200
    assert len(resp.json()["events"]) == 1

    resp = superuser_client.get("/api/admin/audit", params={"limit": 100_000})
    assert resp.status_code == 200
    assert len(resp.json()["events"]) >= 5


# ---------------------------------------------------------------------------
# User management (Stage 13-2)
# ---------------------------------------------------------------------------


def _seed_user(email="target@example.com"):
    """Create a regular user directly (no HTTP client → no cookie clobber)."""
    from src import auth_service
    from src.auth_models import UserCreate
    from src.db import SessionLocal

    db = SessionLocal()
    try:
        return auth_service.register_user(
            db, UserCreate(email=email, password="password12345")
        ).id
    finally:
        db.close()


def _user_field(email, field):
    from src.db import SessionLocal
    from src.db_models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        return getattr(user, field)
    finally:
        db.close()


def test_admin_users_lists_everyone(superuser_client):
    _seed_user("target@example.com")
    resp = superuser_client.get("/api/admin/users")
    assert resp.status_code == 200
    users = resp.json()["users"]
    emails = {u["email"] for u in users}
    assert {"admin@example.com", "target@example.com"} <= emails
    sample = next(u for u in users if u["email"] == "target@example.com")
    assert set(sample) >= {"id", "email", "is_active", "is_superuser", "plan", "created_at"}


def test_admin_user_mutations_forbidden_for_regular_user(authed_client):
    target = _seed_user("victim@example.com")
    assert authed_client.post(f"/api/admin/users/{target}/role", json={"is_superuser": True}).status_code == 403
    assert authed_client.post(f"/api/admin/users/{target}/active", json={"is_active": False}).status_code == 403


def test_admin_promote_and_demote(superuser_client):
    target = _seed_user("promoteme@example.com")

    resp = superuser_client.post(f"/api/admin/users/{target}/role", json={"is_superuser": True})
    assert resp.status_code == 200
    assert resp.json()["is_superuser"] is True
    assert _user_field("promoteme@example.com", "is_superuser") is True

    resp = superuser_client.post(f"/api/admin/users/{target}/role", json={"is_superuser": False})
    assert resp.status_code == 200
    assert _user_field("promoteme@example.com", "is_superuser") is False


def test_admin_ban_and_unban(superuser_client):
    target = _seed_user("banme@example.com")

    resp = superuser_client.post(f"/api/admin/users/{target}/active", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert _user_field("banme@example.com", "is_active") is False

    resp = superuser_client.post(f"/api/admin/users/{target}/active", json={"is_active": True})
    assert resp.status_code == 200
    assert _user_field("banme@example.com", "is_active") is True


def test_ban_stamps_tokens_valid_after(superuser_client):
    """Banning records tokens_valid_after (revokes existing sessions); un-ban
    leaves it set so the old token stays dead and a fresh login is required."""
    target = _seed_user("revoke@example.com")

    superuser_client.post(f"/api/admin/users/{target}/active", json={"is_active": False})
    assert _user_field("revoke@example.com", "tokens_valid_after") is not None

    superuser_client.post(f"/api/admin/users/{target}/active", json={"is_active": True})
    # Un-ban does NOT clear it — old tokens (iat < stamp) remain revoked.
    assert _user_field("revoke@example.com", "tokens_valid_after") is not None


def test_admin_cannot_modify_self(superuser_client):
    admin_id = _user_field("admin@example.com", "id")
    assert superuser_client.post(f"/api/admin/users/{admin_id}/role", json={"is_superuser": False}).status_code == 400
    assert superuser_client.post(f"/api/admin/users/{admin_id}/active", json={"is_active": False}).status_code == 400


def test_admin_unknown_user_is_404(superuser_client):
    assert superuser_client.post("/api/admin/users/nope/role", json={"is_superuser": True}).status_code == 404


def test_last_superuser_guard_blocks_demote_and_ban(superuser_client):
    """Service-level: the sole active superuser can't be demoted/banned even by a
    different actor. (Unreachable via the gated endpoint thanks to the
    self-guard, so tested directly — defense in depth.)"""
    from fastapi import HTTPException

    from src import app_services

    admin_id = _user_field("admin@example.com", "id")  # the only superuser

    with pytest.raises(HTTPException) as demote:
        app_services.admin_set_user_role(actor_id="someone-else", target_id=admin_id, is_superuser=False)
    assert demote.value.status_code == 400
    assert demote.value.detail == "last_superuser"

    with pytest.raises(HTTPException) as ban:
        app_services.admin_set_user_active(actor_id="someone-else", target_id=admin_id, is_active=False)
    assert ban.value.status_code == 400
    assert ban.value.detail == "last_superuser"


def test_protected_root_admin_cannot_be_demoted_or_banned(superuser_client, monkeypatch):
    """The configured ROOT_ADMIN_EMAIL is immune to demote/ban even from another
    superuser — 'no one can touch the root'."""
    import config

    target = _seed_user("root@example.com")
    monkeypatch.setattr(config, "ROOT_ADMIN_EMAIL", "root@example.com")
    superuser_client.post(f"/api/admin/users/{target}/role", json={"is_superuser": True})

    demote = superuser_client.post(f"/api/admin/users/{target}/role", json={"is_superuser": False})
    assert demote.status_code == 400
    assert demote.json()["detail"] == "protected_admin"

    ban = superuser_client.post(f"/api/admin/users/{target}/active", json={"is_active": False})
    assert ban.status_code == 400
    assert ban.json()["detail"] == "protected_admin"


def test_admin_users_marks_protected_root(superuser_client, monkeypatch):
    import config

    _seed_user("root@example.com")
    monkeypatch.setattr(config, "ROOT_ADMIN_EMAIL", "root@example.com")

    users = superuser_client.get("/api/admin/users").json()["users"]
    by_email = {u["email"]: u for u in users}
    assert by_email["root@example.com"]["is_protected"] is True
    assert by_email["admin@example.com"]["is_protected"] is False
