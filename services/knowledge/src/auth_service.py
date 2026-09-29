"""Service layer for registration, authentication and the current-user dependency.

Routing (``api_app``) imports from here; tests can import directly to bypass
HTTP. Errors are raised as :class:`fastapi.HTTPException` so both layers see
the same shape.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from src import email_service
from src.auth_models import UserCreate
from src.db import SessionLocal, get_db
from src.db_models import EmailToken, User, Workspace, WorkspaceMember
from src.security import create_access_token, hash_password, verify_password


# ---------------------------------------------------------------------------
# Registration / login primitives
# ---------------------------------------------------------------------------


def _normalize_email(email: str) -> str:
    return email.strip().lower()


# Precomputed bcrypt hash used to equalise login timing: when the email is
# unknown we still run one ``verify_password`` against this dummy hash, so the
# response time doesn't reveal whether an account exists (user enumeration).
_DUMMY_PASSWORD_HASH = hash_password("vedomo-timing-equalizer")


def register_user(db: Session, payload: UserCreate) -> User:
    """Create a user + their personal workspace atomically.

    Raises ``HTTPException(409)`` if the email is already registered.
    """
    email = _normalize_email(payload.email)
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email_already_registered",
        )

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or email.split("@", 1)[0],
    )
    db.add(user)
    db.flush()  # populate user.id before we reference it below

    _create_personal_workspace(db, user)

    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    """Return the user matching the credentials, or raise 401.

    Always runs exactly one ``verify_password`` — against the real hash if the
    email exists, otherwise against ``_DUMMY_PASSWORD_HASH`` — so the timing of
    "wrong password" and "no such email" is indistinguishable.
    """
    user = db.execute(
        select(User).where(User.email == _normalize_email(email))
    ).scalar_one_or_none()
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, password_hash)
    if user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_inactive",
        )
    return user


def _create_personal_workspace(db: Session, user: User) -> Workspace:
    """Create the user's personal workspace + owner membership.

    The name is a plain Russian label (not "<name>'s workspace" - a tester
    flagged the RU+EN mix). The UI labels personal spaces "Личное пространство"
    regardless of the stored name, so this is just the stored default.
    """
    workspace = Workspace(
        name="Личное пространство",
        owner_user_id=user.id,
        plan="free",
    )
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
        )
    )
    return workspace


def get_personal_workspace(db: Session, user: User) -> Workspace:
    """Return the user's personal workspace.

    Stage 1 invariant: exactly one *personal* workspace per user, the one
    created at registration. Since Stage 15 a teacher can also *own* courses
    (``kind="course"``), so we filter to ``kind="personal"`` rather than just
    "oldest owned" — otherwise a created course could shadow the personal
    space. Oldest is kept only as a defensive tiebreaker.
    """
    workspace = db.execute(
        select(Workspace)
        .where(Workspace.owner_user_id == user.id, Workspace.kind == "personal")
        .order_by(Workspace.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if workspace is None:
        # Self-heal: a registered user without a workspace is a data-integrity
        # bug, but recreating it costs nothing and avoids a hard 500.
        workspace = _create_personal_workspace(db, user)
        db.commit()
        db.refresh(workspace)
    return workspace


# ---------------------------------------------------------------------------
# Email tokens + password reset (Stage 23)
# ---------------------------------------------------------------------------


def _hash_token(raw_token: str) -> str:
    """sha256 of the raw token - what we store, so a DB leak isn't usable."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_email_token(db: Session, user: User, *, purpose: str, ttl_minutes: int) -> str:
    """Mint a single-use token for ``purpose`` and return the **raw** value.

    Only the hash is persisted; the raw token goes into the email link. The
    caller is responsible for committing (so token creation can be batched with
    the surrounding work).
    """
    raw_token = secrets.token_urlsafe(32)
    db.add(
        EmailToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )
    )
    db.flush()
    return raw_token


def consume_email_token(db: Session, raw_token: str, *, purpose: str) -> User | None:
    """Validate + spend a token. Returns the owning user, or ``None`` if the
    token is unknown / wrong-purpose / already used / expired.

    Marks ``used_at`` so the token can't be replayed. Does not commit.
    """
    if not raw_token:
        return None
    token = db.execute(
        select(EmailToken).where(
            EmailToken.token_hash == _hash_token(raw_token),
            EmailToken.purpose == purpose,
        )
    ).scalar_one_or_none()
    if token is None or token.used_at is not None:
        return None

    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    # SQLite reads timestamps back as naive; we always store UTC.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        return None

    token.used_at = now
    return db.get(User, token.user_id)


def request_password_reset(db: Session, email: str) -> None:
    """Send a reset link **iff** the account exists and is active.

    Returns ``None`` either way - the endpoint must not reveal whether an email
    is registered (user enumeration). Commits the new token.
    """
    user = db.execute(
        select(User).where(User.email == _normalize_email(email))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return

    raw_token = create_email_token(
        db, user, purpose="reset", ttl_minutes=config.PASSWORD_RESET_TTL_MINUTES
    )
    db.commit()

    link = f"{config.APP_BASE_URL}/reset-password?token={raw_token}"
    email_service.send_email(
        to=user.email,
        subject="Сброс пароля - Наставник",
        body=(
            "Вы запросили сброс пароля для «Наставника».\n\n"
            f"Чтобы задать новый пароль, откройте ссылку (действует "
            f"{config.PASSWORD_RESET_TTL_MINUTES} мин):\n{link}\n\n"
            "Если это были не вы - просто проигнорируйте это письмо, "
            "пароль останется прежним."
        ),
    )


def reset_password(db: Session, raw_token: str, new_password: str) -> User:
    """Set a new password from a valid reset token.

    Bumps ``tokens_valid_after`` so every existing session (the attacker's, if
    the account was compromised) is revoked - the user logs in fresh with the
    new password. Raises ``HTTPException(400)`` for a bad/expired/used token.
    """
    user = consume_email_token(db, raw_token, purpose="reset")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_or_expired_token",
        )
    user.password_hash = hash_password(new_password)
    user.tokens_valid_after = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def send_verification_email(db: Session, user: User) -> None:
    """Mail a verification link to ``user`` (no-op if already verified).

    Best-effort: a delivery failure must not break registration. Commits the
    new token.
    """
    if user.email_verified_at is not None:
        return
    raw_token = create_email_token(
        db, user, purpose="verify", ttl_minutes=config.EMAIL_VERIFY_TTL_MINUTES
    )
    db.commit()

    link = f"{config.APP_BASE_URL}/verify-email?token={raw_token}"
    email_service.send_email(
        to=user.email,
        subject="Подтверждение почты - Наставник",
        body=(
            "Добро пожаловать в «Наставник»!\n\n"
            f"Подтвердите адрес почты, открыв ссылку:\n{link}\n\n"
            "Если вы не регистрировались - просто проигнорируйте это письмо."
        ),
    )


def confirm_email(db: Session, raw_token: str) -> User:
    """Mark the account's email verified from a valid ``verify`` token.

    Raises ``HTTPException(400)`` for a bad/expired/used token. Idempotent on an
    already-verified account (keeps the original timestamp).
    """
    user = consume_email_token(db, raw_token, purpose="verify")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_or_expired_token",
        )
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Cookie / FastAPI dependency
# ---------------------------------------------------------------------------


def set_auth_cookie(response: Response, token: str) -> None:
    """Attach the JWT to the response as an HttpOnly, SameSite=Lax cookie."""
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=token,
        max_age=config.JWT_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=config.AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=config.AUTH_COOKIE_NAME,
        path="/",
    )


def _resolve_token(
    cookie_token: str | None,
    authorization: str | None,
) -> str | None:
    """Prefer the cookie; fall back to ``Authorization: Bearer <token>``."""
    if cookie_token:
        return cookie_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    return None


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    cookie_token: Annotated[str | None, Cookie(alias=config.AUTH_COOKIE_NAME)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    request: Request = None,
) -> User:
    """FastAPI dependency that resolves the current user from a JWT.

    Accepts the token from either the HttpOnly cookie set by
    :func:`set_auth_cookie` (preferred for browsers) or an
    ``Authorization: Bearer <token>`` header (handy for scripts/tests).
    """
    from src.security import decode_access_token  # local import to keep cycles obvious

    token = _resolve_token(cookie_token, authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not_authenticated",
        )

    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
        )

    user = db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_not_found",
        )
    if not user.is_active:
        # Banned / deactivated: reject the *live* token too, not just at login,
        # so a ban takes effect immediately on already-issued cookies/JWTs.
        # Kept at 401 (not 403) so the frontend bounces to /login rather than
        # showing a dead-end; the detail stays coarse on purpose.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user_inactive",
        )

    # Session revocation (Stage 13): reject tokens issued before the user's
    # ``tokens_valid_after`` (stamped on ban). This is what forces a fresh login
    # after a ban → un-ban cycle instead of the old cookie silently resuming.
    issued_at = payload.get("iat")
    threshold = user.tokens_valid_after
    if threshold is not None and issued_at is not None:
        # SQLite reads the timestamp back as naive; we always store UTC, so
        # treat a naive value as UTC before comparing to the JWT's (UTC) iat.
        if threshold.tzinfo is None:
            threshold = threshold.replace(tzinfo=timezone.utc)
        if int(issued_at) < int(threshold.timestamp()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session_revoked",
            )
    from src.account_deletion import is_pending
    allowed = request is not None and (
        (request.url.path == "/api/auth/me" and request.method in {"GET", "DELETE"})
        or (request.url.path == "/api/auth/session" and request.method == "GET")
        or (request.url.path == "/api/auth/deletion" and request.method == "GET")
    )
    if not allowed and is_pending(db, user.id):
        raise HTTPException(status_code=403, detail="account_deletion_pending")
    return user


def require_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """FastAPI dependency that allows only superusers through.

    Used by admin-only endpoints (e.g. ``/api/diagnostics/*``). Regular users
    that pass authentication get a 403, which is more informative than 401
    and signals that the request was understood but the role is wrong.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="superuser_required",
        )
    return current_user


# ---------------------------------------------------------------------------
# Convenience helpers for non-FastAPI callers (tests, scripts)
# ---------------------------------------------------------------------------


def open_session() -> Session:
    """Create a standalone session — caller owns commit/close.

    Useful for one-off scripts and tests that don't go through FastAPI DI.
    """
    return SessionLocal()


def issue_access_token(user: User) -> str:
    return create_access_token(subject=user.id)
