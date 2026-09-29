"""Course-creation capability gate (Stage 30).

A plain registered user can't create a course (403). A superuser always can,
and can grant the ``can_create_courses`` capability to others via the admin
endpoint - after which the granted user can create. Self-grant is blocked by
the admin self-guard.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api_app import app


def _register(client, email, password="testpassword123", name="U"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": name},
    )


def test_me_exposes_capability_default_false(api_client):
    _register(api_client, "plain@example.com")
    assert api_client.get("/api/auth/me").json()["can_create_courses"] is False


def test_plain_user_cannot_create_course(api_client):
    _register(api_client, "plain@example.com")
    assert api_client.post("/api/courses", json={"name": "Сети"}).status_code == 403


def test_superuser_can_create_course(superuser_client):
    assert superuser_client.post("/api/courses", json={"name": "Сети"}).status_code == 200


def test_admin_grants_capability_then_user_can_create(superuser_client):
    with TestClient(app) as teacher:
        _register(teacher, "teacher@example.com")
        # Before the grant: forbidden.
        assert teacher.post("/api/courses", json={"name": "Сети"}).status_code == 403

        users = superuser_client.get("/api/admin/users").json()["users"]
        target = next(u for u in users if u["email"] == "teacher@example.com")
        assert target["can_create_courses"] is False

        granted = superuser_client.post(
            f"/api/admin/users/{target['id']}/course-creator",
            json={"can_create_courses": True},
        )
        assert granted.status_code == 200
        assert granted.json()["can_create_courses"] is True

        # After the grant: the same user can now create a course.
        assert teacher.post("/api/courses", json={"name": "Сети"}).status_code == 200


def test_admin_can_revoke_capability(superuser_client):
    with TestClient(app) as teacher:
        _register(teacher, "teacher2@example.com")
        users = superuser_client.get("/api/admin/users").json()["users"]
        target = next(u for u in users if u["email"] == "teacher2@example.com")
        superuser_client.post(
            f"/api/admin/users/{target['id']}/course-creator",
            json={"can_create_courses": True},
        )
        assert teacher.post("/api/courses", json={"name": "А"}).status_code == 200

        superuser_client.post(
            f"/api/admin/users/{target['id']}/course-creator",
            json={"can_create_courses": False},
        )
        assert teacher.post("/api/courses", json={"name": "Б"}).status_code == 403


def test_admin_cannot_grant_to_self(superuser_client):
    me = superuser_client.get("/api/auth/me").json()
    resp = superuser_client.post(
        f"/api/admin/users/{me['id']}/course-creator",
        json={"can_create_courses": True},
    )
    assert resp.status_code == 400
