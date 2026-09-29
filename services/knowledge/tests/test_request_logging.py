"""Request-id middleware tests (Stage 34). CI-safe - no models, public endpoint."""

from __future__ import annotations

import asyncio
import logging

from src.obs import RequestLogMiddleware


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _log_records_for_status(status_code: int) -> list[logging.LogRecord]:
    """Drive the middleware around a fake app returning ``status_code`` and grab
    what it logged. Handler attached straight to the request logger so it works
    regardless of propagate=False set by configure_logging."""

    async def fake_app(scope, receive, send):
        await send({"type": "http.response.start", "status": status_code, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    scope = {"type": "http", "method": "GET", "path": "/x", "headers": [], "client": ("1.2.3.4", 0)}

    logger = logging.getLogger("vedomo.request")
    cap = _Capture()
    logger.addHandler(cap)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        asyncio.run(RequestLogMiddleware(fake_app)(scope, receive, send))
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    return cap.records


def test_5xx_request_logs_at_warning():
    records = _log_records_for_status(503)
    assert records and records[-1].levelno == logging.WARNING
    assert " -> 503 " in records[-1].getMessage()


def test_2xx_request_logs_at_info():
    records = _log_records_for_status(200)
    assert records and records[-1].levelno == logging.INFO


def test_response_carries_request_id(api_client):
    resp = api_client.get("/api/health")
    assert resp.status_code == 200
    rid = resp.headers.get("x-request-id")
    assert rid and len(rid) >= 8


def test_inbound_request_id_is_echoed(api_client):
    resp = api_client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers.get("x-request-id") == "trace-abc-123"


def test_each_request_gets_a_distinct_id(api_client):
    a = api_client.get("/api/health").headers.get("x-request-id")
    b = api_client.get("/api/health").headers.get("x-request-id")
    assert a and b and a != b
