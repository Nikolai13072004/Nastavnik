"""Tests for account deletion (Stage 23).

``DELETE /api/auth/me`` removes the user and everything they own. The KB is
stubbed so the test doesn't load the 2GB embedding models - deletion only calls
``remove_orphan_chunks``.
"""

from __future__ import annotations

import config
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src import app_services, auth_service, runtime
from src.auth_models import UserCreate
from src.db_models import ChatSession, EmailToken, User, Workspace, WorkspaceMember


class _FakeKB:
    def remove_orphan_chunks(self, *, workspace_id, valid_document_ids):
        return {"removed_chunks": 0, "removed_document_ids": []}


@pytest.fixture(autouse=True)
def _stub_kb(monkeypatch):
    """Keep deletion off the real (heavy) KnowledgeBase."""
    monkeypatch.setattr(runtime, "get_kb", lambda: _FakeKB())


def test_delete_removes_user_and_owned_data(db_session):
    user = auth_service.register_user(
        db_session, UserCreate(email="del@example.com", password="originalpass1")
    )
    ws = auth_service.get_personal_workspace(db_session, user)
    db_session.add(ChatSession(workspace_id=ws.id, user_id=user.id, title="hi"))
    auth_service.create_email_token(db_session, user, purpose="reset", ttl_minutes=60)
    db_session.commit()
    uid, wsid = user.id, ws.id

    app_services.delete_account_service(db_session, user)

    assert db_session.get(User, uid) is None
    assert db_session.get(Workspace, wsid) is None
    assert db_session.execute(
        select(ChatSession).where(ChatSession.user_id == uid)
    ).first() is None
    assert db_session.execute(
        select(EmailToken).where(EmailToken.user_id == uid)
    ).first() is None
    assert db_session.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == uid)
    ).first() is None


def test_delete_last_superuser_blocked(db_session):
    user = auth_service.register_user(
        db_session, UserCreate(email="su@example.com", password="originalpass1")
    )
    user.is_superuser = True
    db_session.commit()

    with pytest.raises(HTTPException) as info:
        app_services.delete_account_service(db_session, user)
    assert info.value.status_code == 400
    assert info.value.detail == "last_superuser"
    # User survived the blocked delete.
    assert db_session.get(User, user.id) is not None


def test_delete_root_admin_blocked(db_session, monkeypatch):
    user = auth_service.register_user(
        db_session, UserCreate(email="root@example.com", password="originalpass1")
    )
    monkeypatch.setattr(config, "ROOT_ADMIN_EMAIL", "root@example.com")

    with pytest.raises(HTTPException) as info:
        app_services.delete_account_service(db_session, user)
    assert info.value.status_code == 403
    assert info.value.detail == "cannot_delete_root_admin"


def test_delete_me_endpoint_ends_session(api_client):
    api_client.post(
        "/api/auth/register",
        json={"email": "e2e@example.com", "password": "originalpass1"},
    )

    r = api_client.delete("/api/auth/me")
    assert r.status_code == 202
    assert r.json()["status"] == "pending"
    assert api_client.get("/api/auth/me").status_code == 200
    from src.account_deletion import process_one
    assert process_one()
    assert api_client.get("/api/auth/deletion").json()["status"] == "completed"

    # Session is gone and the credentials no longer work.
    assert api_client.get("/api/auth/me").status_code == 401
    login = api_client.post(
        "/api/auth/login",
        json={"email": "e2e@example.com", "password": "originalpass1"},
    )
    assert login.status_code == 401
