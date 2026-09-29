"""Security response headers on the API (Stage 46).

The SecurityHeadersMiddleware stamps defense-in-depth headers so a deploy
without the Caddy HTTPS reverse proxy still ships them.
"""

import config


def test_static_security_headers_present(api_client):
    """Every response carries the static (HTTP-safe) security headers."""
    response = api_client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_hsts_absent_over_plain_http(api_client):
    """HSTS is meaningless over HTTP, so it must NOT be sent when the auth cookie
    is not in Secure mode (the default / non-HTTPS deploy)."""
    assert config.AUTH_COOKIE_SECURE is False  # test default
    response = api_client.get("/api/health")
    assert "strict-transport-security" not in response.headers


def test_hsts_present_when_secure_cookie(api_client, monkeypatch):
    """When the operator declares an HTTPS deploy (AUTH_COOKIE_SECURE=true) the
    backend emits HSTS too."""
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    response = api_client.get("/api/health")
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_headers_present_on_rejected_request(api_client):
    """Headers apply even to an early rejection (the middleware is outermost):
    a 401 from a protected endpoint still carries them."""
    response = api_client.get("/api/materials")
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
