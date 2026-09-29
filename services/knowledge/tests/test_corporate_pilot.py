"""Corporate pilot invitations, grouping, privacy-safe metrics and export."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import api_app
from src import app_services, audit_service, email_service
from src.db import SessionLocal
from src.db_models import Assignment, AssignmentAttempt, CourseInvitation, UsageEvent, User


def _register(client, email, name="Employee"):
    response = client.post("/api/auth/register", json={
        "email": email, "password": "password12345", "display_name": name,
    })
    assert response.status_code == 201
    return response.json()["user"]


def _invite(owner, course_id, email, *, group="Продажи", role="student"):
    email_service.OUTBOX.clear()
    response = owner.post(f"/api/courses/{course_id}/invitations", json={
        "email": email, "role": role, "group_name": group,
    })
    assert response.status_code == 201
    assert response.json()["email"] == email
    assert len(email_service.OUTBOX) == 1
    token = re.search(r"[?&]invite=([^\s]+)", email_service.OUTBOX[0].body).group(1)
    assert token not in response.text
    return token, response.json()


def test_address_bound_invitation_accepts_once_and_sets_group(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Знание продукта"}).json()["id"]
    token, created = _invite(authed_client, course_id, "employee@example.com")
    detail = authed_client.get(f"/api/courses/{course_id}").json()
    assert detail["invitations"][0]["id"] == created["id"]
    audit = next(event for event in audit_service.list_recent(50)
                 if event.action == audit_service.ACTION_INVITE_COURSE_MEMBER)
    assert audit.target == created["id"] and "employee@" not in audit.target

    with TestClient(api_app.app) as wrong:
        _register(wrong, "wrong@example.com")
        assert wrong.post("/api/invitations/accept", json={"token": token}).status_code == 404

    with TestClient(api_app.app) as employee:
        user = _register(employee, "employee@example.com")
        accepted = employee.post("/api/invitations/accept", json={"token": token})
        assert accepted.status_code == 200
        assert accepted.json()["id"] == course_id
        assert employee.post("/api/invitations/accept", json={"token": token}).status_code == 404

    detail = authed_client.get(f"/api/courses/{course_id}").json()
    member = next(item for item in detail["members"] if item["user_id"] == user["id"])
    assert member["role"] == "student" and member["group_name"] == "Продажи"
    assert detail["invitations"] == []


def test_invitation_can_be_revoked_and_expired_invite_is_rejected(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Продукт"}).json()["id"]
    token, created = _invite(authed_client, course_id, "revoked@example.com")
    revoked = authed_client.delete(f"/api/courses/{course_id}/invitations/{created['id']}")
    assert revoked.status_code == 200 and revoked.json()["invitations"] == []
    with TestClient(api_app.app) as invited:
        _register(invited, "revoked@example.com")
        assert invited.post("/api/invitations/accept", json={"token": token}).status_code == 404

    expired_token, _ = _invite(authed_client, course_id, "expired@example.com")
    with SessionLocal() as db:
        row = db.query(CourseInvitation).filter(CourseInvitation.email == "expired@example.com").one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    with TestClient(api_app.app) as expired:
        _register(expired, "expired@example.com")
        assert expired.post("/api/invitations/accept", json={"token": expired_token}).status_code == 404


def test_invitation_reports_delivery_failure_without_losing_pending_record(authed_client, monkeypatch):
    course_id = authed_client.post("/api/courses", json={"name": "Продукт"}).json()["id"]
    monkeypatch.setattr(app_services.email_service, "send_email", lambda *args, **kwargs: False)
    response = authed_client.post(f"/api/courses/{course_id}/invitations", json={
        "email": "mail-failed@example.com", "role": "student", "group_name": "Сервис",
    })
    assert response.status_code == 201
    assert response.json()["delivery_status"] == "failed"
    detail = authed_client.get(f"/api/courses/{course_id}").json()
    assert detail["invitations"][0]["email"] == "mail-failed@example.com"


def test_student_cannot_invite_group_or_read_pilot_metrics(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Продукт"}).json()["id"]
    token, _ = _invite(authed_client, course_id, "student-private@example.com")
    with TestClient(api_app.app) as student:
        user = _register(student, "student-private@example.com")
        assert student.post("/api/invitations/accept", json={"token": token}).status_code == 200
        assert student.post(f"/api/courses/{course_id}/invitations", json={
            "email": "other@example.com", "role": "student",
        }).status_code == 403
        assert student.post(f"/api/courses/{course_id}/members/{user['id']}/group", json={
            "group_name": "Скрытая группа",
        }).status_code == 403
        assert student.get(f"/api/courses/{course_id}/pilot").status_code == 403
        assert student.get(f"/api/courses/{course_id}/pilot.csv").status_code == 403


def test_dashboard_aggregates_without_exposing_chat_content_and_csv_is_safe(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Продукт"}).json()["id"]
    token, _ = _invite(authed_client, course_id, "metric@example.com", group="Сервис")
    with TestClient(api_app.app) as employee:
        employee_user = _register(employee, "metric@example.com", '=HYPERLINK("bad")')
        employee.post("/api/invitations/accept", json={"token": token})

    with SessionLocal() as db:
        assignment = Assignment(
            workspace_id=course_id, created_by_user_id=authed_client.get("/api/auth/me").json()["id"],
            title="Проверка", questions=[{"question": "Q", "options": ["A", "B"], "correct_index": 0}],
            is_published=True,
        )
        db.add(assignment)
        db.flush()
        db.add(AssignmentAttempt(
            assignment_id=assignment.id, user_id=employee_user["id"], answers=[0], score=1, total=1,
        ))
        db.add(UsageEvent(
            workspace_id=course_id, user_id=employee_user["id"], action="chat", units=1,
            billing_subject_type="user", billing_subject_id=employee_user["id"],
            meta={"private_prompt": "must never be returned"},
        ))
        db.commit()

    response = authed_client.get(f"/api/courses/{course_id}/pilot")
    assert response.status_code == 200
    body = response.json()
    metric = next(row for row in body["members"] if row["user_id"] == employee_user["id"])
    assert metric["chat_requests"] == 1
    assert metric["assignments_completed"] == 1 and metric["average_score_pct"] == 100
    assert "private_prompt" not in response.text and "must never" not in response.text

    csv_response = authed_client.get(f"/api/courses/{course_id}/pilot.csv")
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert "'=HYPERLINK" in csv_response.content.decode("utf-8-sig")
    assert "must never" not in csv_response.content.decode("utf-8-sig")
