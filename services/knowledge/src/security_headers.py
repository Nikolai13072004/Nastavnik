"""Security response headers for the API (Stage 46).

Pure-ASGI middleware (like ``csrf.py`` / ``obs.py`` - deliberately *not*
``BaseHTTPMiddleware``, so it doesn't buffer the NDJSON chat stream) that stamps
defense-in-depth headers on every backend response.

Why at the app layer: the friend/self code review found the security headers
lived *only* in ``docker/Caddyfile`` (the HTTPS prod profile, which needs a
domain). A bare ``http://<ip>:8000`` backend - or any deploy without Caddy in
front - therefore answered with no headers at all. Setting them here means they
apply regardless of what (if anything) is in front.

Division of ownership (no duplicate headers in any deploy mode):
* **Static headers** (nosniff / frame-options / referrer-policy / CSP
  frame-ancestors) are set here for ``/api`` responses and in
  ``frontend/next.config.ts`` for the user-facing pages.
* **HSTS** is only meaningful over HTTPS, so it is emitted here only when
  ``AUTH_COOKIE_SECURE`` is on (i.e. the operator declared an HTTPS deploy);
  in the Caddy profile Caddy is the HTTPS terminator and owns HSTS.

``append-if-absent`` semantics: we never clobber a header an upstream already
set, so running behind Caddy doesn't produce conflicting duplicates.
"""

from __future__ import annotations

import config

# Static headers safe on every response, HTTP or HTTPS.
_STATIC_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
]

_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Add defense-in-depth security headers to every HTTP response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in _STATIC_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                # HSTS only over HTTPS (operator opted in via AUTH_COOKIE_SECURE).
                if config.AUTH_COOKIE_SECURE and _HSTS[0] not in present:
                    headers.append(_HSTS)
            await send(message)

        await self.app(scope, receive, send_wrapper)
