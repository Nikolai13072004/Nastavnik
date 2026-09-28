"""End-to-end tests for the ``/api/auth/*`` endpoints."""

from __future__ import annotations

import config


def _register(client, email="alice@example.com", password="hunter22hunter", display_name="Alice"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )


def test_register_creates_user_and_sets_cookie(api_client):
    response = _register(api_client)

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["display_name"] == "Alice"
    assert body["user"]["personal_workspace"]["id"]
    # HttpOnly cookie must be set so the browser auto-attaches it.
    assert config.AUTH_COOKIE_NAME in response.cookies


def test_register_returns_409_on_duplicate_email(api_client):
    _register(api_client)
    second = _register(api_client, display_name="Alice II")
    assert second.status_code == 409
    assert second.json()["detail"] == "email_already_registered"


def test_login_accepts_correct_password(api_client):
    _register(api_client, email="bob@example.com", password="correctpassword1")

    response = api_client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "correctpassword1"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "bob@example.com"
    assert config.AUTH_COOKIE_NAME in response.cookies


def test_login_rejects_wrong_password(api_client):
    _register(api_client, email="carol@example.com", password="correctpassword1")

    response = api_client.post(
        "/api/auth/login",
        json={"email": "carol@example.com", "password": "WRONGpassword"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"


def test_me_requires_authentication(api_client):
    response = api_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"


def test_session_probe_returns_anonymous_without_a_cookie(api_client):
    response = api_client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_session_probe_returns_the_authenticated_user(api_client):
    _register(api_client, email="session@example.com", display_name="Session User")
    response = api_client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "session@example.com"


def test_session_probe_treats_an_invalid_cookie_as_anonymous(api_client):
    api_client.cookies.set(config.AUTH_COOKIE_NAME, "definitely-not-a-jwt")
    response = api_client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_me_returns_user_when_logged_in_via_cookie(api_client):
    _register(api_client, email="dan@example.com", password="goodpassword1", display_name="Dan")
    # TestClient automatically replays the cookie set by /register.

    response = api_client.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "dan@example.com"
    assert body["display_name"] == "Dan"
    assert body["personal_workspace"]["plan"] == "free"


def test_me_works_with_bearer_token(api_client):
    register_response = _register(api_client, email="erin@example.com")
    token = register_response.json()["access_token"]

    # Drop cookies to force the dependency to fall back to the header.
    api_client.cookies.clear()
    response = api_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "erin@example.com"


def test_logout_clears_cookie_and_blocks_subsequent_me(api_client):
    _register(api_client, email="frank@example.com")

    logout_response = api_client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "logged_out"

    api_client.cookies.clear()
    me_response = api_client.get("/api/auth/me")
    assert me_response.status_code == 401


def test_register_creates_exactly_one_personal_workspace(api_client):
    response = _register(api_client, email="grace@example.com")
    workspace_id = response.json()["user"]["personal_workspace"]["id"]
    assert workspace_id

    # Logging in returns the same workspace, not a new one.
    login = api_client.post(
        "/api/auth/login",
        json={"email": "grace@example.com", "password": "hunter22hunter"},
    )
    assert login.json()["user"]["personal_workspace"]["id"] == workspace_id


def test_invalid_token_rejected(api_client):
    api_client.cookies.set(config.AUTH_COOKIE_NAME, "definitely-not-a-jwt")
    response = api_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_token"
