"""Observability: request-id + structured access logging (Stage 34).

A pure-ASGI middleware (deliberately *not* Starlette's ``BaseHTTPMiddleware``,
which buffers responses and would break the NDJSON chat stream). It:

* assigns each request a short id (honouring an inbound ``X-Request-ID`` so a
  proxy / client can correlate),
* logs one structured line per request - method, path, status, duration,
  request id - on the ``vedomo.request`` logger,
* echoes the id back in the ``X-Request-ID`` response header.

``configure_logging`` makes the ``vedomo.*`` loggers actually print (the app
loggers are otherwise below uvicorn's default level and get swallowed - the same
trap that hid the email link in Stage 23). It writes to stdout at ``LOG_LEVEL``
(default INFO) and is idempotent, so importing the app twice (tests) is safe.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid

import config

_REQUEST_LOGGER = "vedomo.request"


def configure_logging() -> None:
    """Attach a stdout handler to the ``vedomo`` logger namespace once."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger("vedomo")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
        # Don't double-emit through uvicorn's root handlers.
        logger.propagate = False
    logger.setLevel(level)


def _inbound_request_id(scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id" and value:
            return value.decode("latin-1")[:64]
    return uuid.uuid4().hex[:12]


def _client_ip(scope) -> str:
    """Real client IP for the access log (proxy-aware; mirrors rate_limit).

    Behind the prod proxy (Caddy -> Next -> backend) the raw peer is always the
    frontend container, so trust Caddy's ``X-Real-IP`` when ``TRUST_PROXY_IP``
    is set; otherwise use the real peer.
    """
    if config.TRUST_PROXY_IP:
        for name, value in scope.get("headers", []):
            if name == b"x-real-ip" and value:
                return value.decode("latin-1")
    client = scope.get("client")
    return client[0] if client else "-"


class RequestLogMiddleware:
    """Pure-ASGI request-id + access-log middleware (streaming-safe)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope)
        rid_bytes = request_id.encode("latin-1")
        start = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                # Append rather than replace so we don't clobber other headers.
                message.setdefault("headers", []).append((b"x-request-id", rid_bytes))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            # 5xx stand out as WARNING so they're easy to spot in the logs and to
            # alert on; everything else stays at INFO. The status is still in the
            # message, so scripts/alert_5xx.sh greps it level-agnostically.
            level = logging.WARNING if (status["code"] or 0) >= 500 else logging.INFO
            logging.getLogger(_REQUEST_LOGGER).log(
                level,
                "%s %s -> %s %sms ip=%s rid=%s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status["code"] or "-",
                duration_ms,
                _client_ip(scope),
                request_id,
            )
