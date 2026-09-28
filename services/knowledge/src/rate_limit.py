"""Shared rate limiter (Stage 9a; per-user keying added in Stage 13).

A single :class:`slowapi.Limiter` imported by both ``api_app`` and
``src.auth_api`` so auth/chat/upload routes share one limiter.

Keying (Stage 13): authenticated endpoints are limited **per user**, not per
IP. A university/dorm NAT shares one public IP, so per-IP keying would let one
noisy student throttle the whole group. Public auth endpoints (login/register)
stay **per-IP** (they pass ``key_func=get_remote_address`` explicitly) because
there's no user yet and that's the anti-brute-force boundary.

Storage backend is configurable (Phase A-prep): default ``memory://`` is correct
for a single backend process (the Docker deployment runs one uvicorn worker);
set ``RATE_LIMIT_STORAGE_URI=redis://...`` to share counters across instances
when scaling horizontally - the only change needed here for that.

Disabled via ``RATE_LIMIT_ENABLED=false`` so the test suite isn't throttled.
"""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

import config
from src.security import decode_access_token


def _enabled_from_env() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() != "false"


def _token_from_request(request) -> str | None:
    """Pull the JWT from the auth cookie or an ``Authorization: Bearer`` header."""
    token = request.cookies.get(config.AUTH_COOKIE_NAME)
    if token:
        return token
    authorization = request.headers.get("Authorization") or request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    return None


def client_ip(request) -> str:
    """Best-effort real client IP (also the per-IP rate-limit / audit-log key).

    Behind the prod reverse proxy (Caddy -> Next.js -> backend) the immediate
    peer is always the frontend container, so the raw peer address collapses to
    one value for everyone. When ``config.TRUST_PROXY_IP`` is set (prod only),
    trust the ``X-Real-IP`` header Caddy writes with the true remote host - Caddy
    overwrites any client-supplied value and the backend isn't directly
    reachable, so it can't be forged. Off by default, so a directly-exposed
    backend falls back to the real peer (``get_remote_address``) and a client
    can't spoof its IP via a header.
    """
    if config.TRUST_PROXY_IP:
        real = request.headers.get("X-Real-IP")
        if real:
            return real.strip()
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def user_or_ip(request) -> str:
    """Rate-limit key: ``user:<id>`` when authenticated, else ``ip:<addr>``.

    Cookie and Bearer for the same user yield the same key. A missing /
    malformed / expired token falls back to the client IP — best-effort, never
    raises on the hot path (``decode_access_token`` already returns ``None`` for
    bad tokens).
    """
    token = _token_from_request(request)
    if token:
        try:
            payload = decode_access_token(token)
        except Exception:  # pragma: no cover - belt-and-braces, must not 500
            payload = None
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    return f"ip:{client_ip(request)}"


limiter = Limiter(
    key_func=user_or_ip,
    enabled=_enabled_from_env(),
    storage_uri=config.RATE_LIMIT_STORAGE_URI,
)
