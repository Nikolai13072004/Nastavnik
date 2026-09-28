"""FastAPI router for ``/api/auth/*`` endpoints.

Kept in its own module so :mod:`api_app` stays a thin composition root and
the auth surface is easy to test in isolation.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

import config
from src import audit_service, auth_service
from src.auth_models import (
    AccountDeletionOut,
    AuthResponse,
    AuthSessionResponse,
    EmailVerifyConfirmRequest,
    MessageResponse,
    PasswordForgotRequest,
    PasswordResetRequest,
    UserCreate,
    UserLogin,
    UserOut,
    WorkspaceOut,
)
from src.db import get_db
from src.db_models import User
from src.rate_limit import client_ip, limiter


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    # Proxy-aware (Stage 46): the real client IP behind Caddy. See
    # src.rate_limit.client_ip - falls back to the raw peer when not behind a
    # trusted proxy (TRUST_PROXY_IP off).
    return client_ip(request)


def _build_user_out(db: Session, user: User) -> UserOut:
    from src.account_deletion import job_for_user
    deletion = job_for_user(db, user.id)
    workspace = auth_service.get_personal_workspace(db, user)
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        can_create_courses=user.can_create_courses,
        email_verified=user.email_verified_at is not None,
        deletion_status=deletion.status if deletion else "none",
        created_at=user.created_at,
        personal_workspace=WorkspaceOut.model_validate(workspace),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(config.RATE_LIMIT_REGISTER, key_func=client_ip)
def register(
    payload: UserCreate,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    user = auth_service.register_user(db, payload)
    token = auth_service.issue_access_token(user)
    auth_service.set_auth_cookie(response, token)
    auth_service.send_verification_email(db, user)
    return AuthResponse(access_token=token, user=_build_user_out(db, user))


@router.post("/login", response_model=AuthResponse)
@limiter.limit(config.RATE_LIMIT_LOGIN, key_func=client_ip)
def login(
    payload: UserLogin,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    user = auth_service.authenticate_user(db, payload.email, payload.password)
    token = auth_service.issue_access_token(user)
    auth_service.set_auth_cookie(response, token)
    audit_service.record(
        audit_service.ACTION_LOGIN,
        user_id=user.id,
        ip=_client_ip(request),
    )
    return AuthResponse(access_token=token, user=_build_user_out(db, user))


@router.post("/logout", response_model=MessageResponse)
def logout(
    response: Response,
    current_user: Annotated[User, Depends(auth_service.get_current_user)],
) -> MessageResponse:
    """Clear the auth cookie. Requires a valid session to prevent CSRF-style
    cookie-clearing attacks on anonymous visitors and to give a clear
    semantic: "this caller had a session and now does not"."""
    del current_user  # only used for the auth gate
    auth_service.clear_auth_cookie(response)
    return MessageResponse(message="logged_out")


@router.get("/me", response_model=UserOut)
def me(
    current_user: Annotated[User, Depends(auth_service.get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    return _build_user_out(db, current_user)


@router.get("/session", response_model=AuthSessionResponse)
def session(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    cookie_token: Annotated[str | None, Cookie(alias=config.AUTH_COOKIE_NAME)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AuthSessionResponse:
    """Return the current browser session without treating anonymity as an error.

    The endpoint exposes no additional user data: when authenticated it returns
    the same ``UserOut`` as ``/me``; otherwise it returns ``{"user": null}``.
    Protected endpoints, including ``/me``, retain their strict 401 contract.
    """
    try:
        user = auth_service.get_current_user(
            db=db,
            cookie_token=cookie_token,
            authorization=authorization,
            request=request,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return AuthSessionResponse(user=None)
        raise
    return AuthSessionResponse(user=_build_user_out(db, user))


@router.post("/password/forgot", response_model=MessageResponse)
@limiter.limit(config.RATE_LIMIT_PASSWORD, key_func=client_ip)
def password_forgot(
    payload: PasswordForgotRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """Email a reset link if the account exists. Always 200 with the same body -
    the response must not reveal whether the email is registered."""
    auth_service.request_password_reset(db, payload.email)
    return MessageResponse(message="if_the_account_exists_an_email_was_sent")


@router.post("/password/reset", response_model=MessageResponse)
@limiter.limit(config.RATE_LIMIT_PASSWORD, key_func=client_ip)
def password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """Set a new password from a valid single-use reset token. 400 if the token
    is unknown / expired / already used. Existing sessions are revoked."""
    auth_service.reset_password(db, payload.token, payload.password)
    return MessageResponse(message="password_reset")


@router.post("/verify/request", response_model=MessageResponse)
def verify_request(
    current_user: Annotated[User, Depends(auth_service.get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """Resend the verification email to the signed-in user (no-op if already
    verified)."""
    auth_service.send_verification_email(db, current_user)
    return MessageResponse(message="verification_email_sent")


@router.post("/verify/confirm", response_model=MessageResponse)
def verify_confirm(
    payload: EmailVerifyConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """Mark the email verified from a single-use token. 400 if the token is
    unknown / expired / already used."""
    auth_service.confirm_email(db, payload.token)
    return MessageResponse(message="email_verified")


@router.delete("/me", response_model=AccountDeletionOut, status_code=202)
def delete_me(
    response: Response,
    current_user: Annotated[User, Depends(auth_service.get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountDeletionOut:
    """Queue durable cleanup; acceptance is explicitly NOT completion."""
    from src.account_deletion import request_deletion
    return AccountDeletionOut(**request_deletion(db, current_user))


@router.get("/deletion", response_model=AccountDeletionOut)
def deletion_status(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    cookie_token: Annotated[str | None, Cookie(alias=config.AUTH_COOKIE_NAME)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> AccountDeletionOut:
    from src.account_deletion import job_for_user, receipt
    from src.security import decode_access_token
    try:
        user = auth_service.get_current_user(db, cookie_token, authorization, request)
        job = job_for_user(db, user.id)
    except HTTPException as exc:
        if exc.detail != "user_not_found":
            raise
        # A valid, unexpired former owner's token may read only its completed
        # receipt, never any workspace or another user's deletion status.
        payload = decode_access_token(auth_service._resolve_token(cookie_token, authorization))
        job = job_for_user(db, payload["sub"]) if payload else None
        if job is None or job.status != "completed":
            raise
        auth_service.clear_auth_cookie(response)
    return AccountDeletionOut(**receipt(job))
