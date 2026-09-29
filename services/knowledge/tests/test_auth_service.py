"""Unit tests for the auth service layer (no FastAPI involved)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src import auth_service
from src.auth_models import UserCreate
from src.db_models import User, Workspace, WorkspaceMember
from src.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong password", h) is False


def test_verify_password_handles_malformed_hash():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_access_token_roundtrip_carries_subject():
    token = create_access_token(subject="user-123", expires_minutes=5)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert "exp" in payload and "iat" in payload


def test_decode_returns_none_for_garbage_token():
    assert decode_access_token("not.a.token") is None


def test_register_user_creates_personal_workspace(db_session):
    user = auth_service.register_user(
        db_session,
        UserCreate(email="alice@example.com", password="hunter22hunter", display_name="Alice"),
    )

    assert user.id
    assert user.email == "alice@example.com"
    # Password must be stored only as a hash.
    assert user.password_hash != "hunter22hunter"
    assert verify_password("hunter22hunter", user.password_hash)

    workspaces = db_session.execute(
        select(Workspace).where(Workspace.owner_user_id == user.id)
    ).scalars().all()
    assert len(workspaces) == 1
    assert workspaces[0].plan == "free"
    # Personal workspaces get a plain Russian name (no "<name>'s workspace" mix).
    assert workspaces[0].name == "Личное пространство"

    memberships = db_session.execute(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    ).scalars().all()
    assert len(memberships) == 1
    assert memberships[0].role == "owner"
    assert memberships[0].workspace_id == workspaces[0].id


def test_register_user_rejects_duplicate_email(db_session):
    auth_service.register_user(
        db_session,
        UserCreate(email="bob@example.com", password="passwordpassword"),
    )

    with pytest.raises(HTTPException) as info:
        auth_service.register_user(
            db_session,
            UserCreate(email="BOB@example.com", password="anotherpassword"),
        )
    assert info.value.status_code == 409
    assert info.value.detail == "email_already_registered"


def test_authenticate_user_accepts_correct_password(db_session):
    auth_service.register_user(
        db_session,
        UserCreate(email="carol@example.com", password="rightpassword1"),
    )

    user = auth_service.authenticate_user(db_session, "carol@example.com", "rightpassword1")
    assert isinstance(user, User)
    assert user.email == "carol@example.com"


def test_authenticate_user_rejects_wrong_password(db_session):
    auth_service.register_user(
        db_session,
        UserCreate(email="dan@example.com", password="rightpassword1"),
    )

    with pytest.raises(HTTPException) as info:
        auth_service.authenticate_user(db_session, "dan@example.com", "WRONGpassword")
    assert info.value.status_code == 401
    assert info.value.detail == "invalid_credentials"


def test_authenticate_user_rejects_unknown_email(db_session):
    with pytest.raises(HTTPException) as info:
        auth_service.authenticate_user(db_session, "ghost@example.com", "whatever1234")
    assert info.value.status_code == 401


def test_authenticate_user_rejects_inactive_user(db_session):
    user = auth_service.register_user(
        db_session,
        UserCreate(email="eve@example.com", password="goodpassword1"),
    )
    user.is_active = False
    db_session.commit()

    with pytest.raises(HTTPException) as info:
        auth_service.authenticate_user(db_session, "eve@example.com", "goodpassword1")
    assert info.value.status_code == 403
    assert info.value.detail == "user_inactive"


def test_get_personal_workspace_returns_the_one_created_at_registration(db_session):
    user = auth_service.register_user(
        db_session,
        UserCreate(email="frank@example.com", password="goodpassword1"),
    )
    workspace = auth_service.get_personal_workspace(db_session, user)
    assert workspace.owner_user_id == user.id
