"""CSRF defense for cookie auth (Stage 41).

A pure-ASGI Origin/Referer check (deliberately *not* ``BaseHTTPMiddleware``, so
it doesn't buffer the NDJSON chat stream). The auth cookie is HttpOnly +
SameSite=Lax, which already blocks classic cross-site form/fetch POST; this is
defense-in-depth.

Policy: for state-changing methods (POST/PUT/PATCH/DELETE) we look at the
``Origin`` header (falling back to ``Referer``). If it is present and does NOT
match an allowed origin, the request is rejected with 403. If neither header is
present we allow it - browsers always attach ``Origin`` to cross-origin
mutating requests, so a CSRF attacker cannot strip it, while non-browser
clients / tests / ``Authorization: Bearer`` scripts (which omit it) keep
working.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import config

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _origin_of(url: str) -> str | None:
    """Return ``scheme://host[:port]`` for a URL, or None if unparseable."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class CsrfOriginMiddleware:
    """Reject cross-origin state-changing requests by Origin/Referer."""

    def __init__(self, app):
        self.app = app
        # Normalise configured trusted origins to scheme://host form once.
        self.allowed = {
            normalized
            for normalized in (_origin_of(o) for o in config.TRUSTED_ORIGINS)
            if normalized
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin")
        referer = headers.get(b"referer")

        candidate: str | None = None
        if origin:
            candidate = origin.decode("latin-1")
        elif referer:
            candidate = _origin_of(referer.decode("latin-1"))

        if candidate is not None and candidate not in self.allowed:
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    async def _reject(self, send):
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail":"csrf_origin_rejected"}',
            }
        )
