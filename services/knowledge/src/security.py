"""Password hashing and JWT helpers.

Kept deliberately small so it can be re-used by the auth service and by tests
without dragging in FastAPI dependencies.

We use the ``bcrypt`` library directly rather than passlib because passlib
1.7.4 mis-detects bcrypt 4.1+/5.x versions (the ``bcrypt.__about__`` module
was removed) and falls into a broken fallback path. Direct use also keeps the
truncation behaviour explicit: bcrypt only hashes the first 72 bytes of the
input, so we truncate consistently in both hash and verify.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

import config


_BCRYPT_MAX_BYTES = 72


def _encode_password(password: str) -> bytes:
    """Encode and truncate to bcrypt's 72-byte input limit.

    bcrypt 5.x raises ValueError on longer inputs instead of silently
    truncating, so we do the truncation explicitly. This matches the standard
    workaround documented by the bcrypt project.
    """
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Return a bcrypt hash for ``password``."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(_encode_password(password), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check that ``password`` matches ``password_hash``."""
    try:
        return bcrypt.checkpw(
            _encode_password(password),
            password_hash.encode("ascii"),
        )
    except (ValueError, TypeError):
        # Malformed hash in the DB — treat as a failed verification rather
        # than propagating an opaque exception to the auth endpoint.
        return False


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """Issue a JWT whose ``sub`` claim is the user id.

    ``expires_minutes`` defaults to :data:`config.JWT_EXPIRE_MINUTES`. The
    token also carries ``iat`` (issued-at) so we can reason about clock skew
    later if needed.
    """
    if expires_minutes is None:
        expires_minutes = config.JWT_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the decoded payload, or ``None`` if the token is invalid/expired."""
    try:
        return jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
    except JWTError:
        return None
