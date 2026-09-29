"""Stage 41: CSRF Origin/Referer middleware."""

import asyncio

from src.csrf import CsrfOriginMiddleware


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _drive(method, headers):
    """Drive the middleware directly (no HTTP) and return the status code."""
    mw = CsrfOriginMiddleware(_ok_app)
    scope = {"type": "http", "method": method, "headers": headers}
    captured = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]

    asyncio.run(mw(scope, receive, send))
    return captured["status"]


def test_safe_method_bypasses_csrf():
    # GET is never blocked, even with a foreign Origin.
    assert _drive("GET", [(b"origin", b"https://evil.example")]) == 200


def test_no_origin_allowed():
    # Non-browser clients / tests omit Origin - allowed.
    assert _drive("POST", []) == 200


def test_allowed_origin_passes():
    assert _drive("POST", [(b"origin", b"http://localhost:3000")]) == 200


def test_foreign_origin_rejected():
    assert _drive("POST", [(b"origin", b"https://evil.example")]) == 403


def test_foreign_referer_rejected():
    # Falls back to Referer when Origin is absent.
    assert _drive("POST", [(b"referer", b"https://evil.example/page")]) == 403


def test_integration_register_blocks_foreign_origin(api_client):
    resp = api_client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "password": "password123", "display_name": "X"},
        headers={"origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "csrf_origin_rejected"


def test_integration_register_allows_trusted_origin(api_client):
    resp = api_client.post(
        "/api/auth/register",
        json={"email": "y@example.com", "password": "password123", "display_name": "Y"},
        headers={"origin": "http://localhost:3000"},
    )
    assert resp.status_code != 403
