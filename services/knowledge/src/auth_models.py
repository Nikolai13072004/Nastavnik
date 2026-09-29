"""Pydantic schemas for the auth API.

These are intentionally separate from :mod:`src.db_models` (SQLAlchemy ORM)
so the wire format and the storage format can evolve independently.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """Payload for ``POST /api/auth/register``."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)


class UserLogin(BaseModel):
    """Payload for ``POST /api/auth/login``."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class WorkspaceOut(BaseModel):
    """A workspace as exposed to the API client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    plan: str
    created_at: datetime


class UserOut(BaseModel):
    """The currently-authenticated user, returned by ``GET /api/auth/me``.

    Includes the personal workspace so the client can render workspace-scoped
    UI without a second round-trip.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str
    is_active: bool
    is_superuser: bool
    can_create_courses: bool = False
    email_verified: bool = False
    deletion_status: str = "none"
    created_at: datetime
    personal_workspace: WorkspaceOut


class AuthResponse(BaseModel):
    """Returned by register/login. The token also lands in an httpOnly cookie."""

    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AuthSessionResponse(BaseModel):
    """Non-error bootstrap response for public pages.

    Protected endpoints keep returning 401 when authentication is required;
    this shape is only for discovering whether the browser already has a valid
    session before deciding between the landing page and the application.
    """

    user: UserOut | None = None


class MessageResponse(BaseModel):
    message: str


class AccountDeletionOut(BaseModel):
    status: str
    attempts: int
    error_code: str


class PasswordForgotRequest(BaseModel):
    """Payload for ``POST /api/auth/password/forgot``."""

    email: EmailStr


class PasswordResetRequest(BaseModel):
    """Payload for ``POST /api/auth/password/reset``."""

    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=8, max_length=128)


class EmailVerifyConfirmRequest(BaseModel):
    """Payload for ``POST /api/auth/verify/confirm``."""

    token: str = Field(min_length=1, max_length=512)
