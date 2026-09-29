"""Stage 15-3: active-workspace selection.

Two layers:
- ``get_current_workspace_id`` requires an explicit destination and
  re-validates membership every request, without fallback.
- ``GET /api/workspaces`` / ``POST /api/workspaces/{id}/activate`` drive the
  switcher, with server-side membership validation (403/404).
"""

# Importing api_app registers all models on Base.metadata before the db_session
# fixture runs create_all, and gives us the resolver under test.
import pytest
from fastapi import HTTPException
from api_app import get_current_workspace_id
from src import courses
from src.db import SessionLocal
from src.db_models import User, Workspace, WorkspaceMember


def _user(db, email):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.flush()
    return u


def _personal(db, user):
    ws = Workspace(name="Personal", owner_user_id=user.id)  # kind defaults to personal
    db.add(ws)
    db.flush()
    return ws


def _course(db, owner, name="Сети"):
    ws = Workspace(name=name, owner_user_id=owner.id, kind=courses.WORKSPACE_KIND_COURSE)
    db.add(ws)
    db.flush()
    return ws


# --- resolver (get_current_workspace_id) -----------------------------------

def test_resolver_requires_explicit_destination(db_session):
    user = _user(db_session, "u@x")
    personal = _personal(db_session, user)
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        get_current_workspace_id(user, db_session)
    assert error.value.status_code == 428
    assert get_current_workspace_id(user, db_session, personal.id) == personal.id


def test_resolver_honors_active_course_when_member(db_session):
    user = _user(db_session, "u@x")
    owner = _user(db_session, "o@x")
    _personal(db_session, user)
    course = _course(db_session, owner)
    db_session.add(WorkspaceMember(workspace_id=course.id, user_id=user.id, role=courses.ROLE_STUDENT))
    user.active_workspace_id = course.id
    db_session.commit()
    assert get_current_workspace_id(user, db_session, course.id) == course.id


def test_resolver_rejects_nonmember_without_fallback(db_session):
    user = _user(db_session, "u@x")
    owner = _user(db_session, "o@x")
    personal = _personal(db_session, user)
    course = _course(db_session, owner)
    user.active_workspace_id = course.id  # selected but never joined
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        get_current_workspace_id(user, db_session, course.id)
    assert error.value.status_code == 403


def test_resolver_rejects_missing_workspace_without_fallback(db_session):
    user = _user(db_session, "u@x")
    personal = _personal(db_session, user)
    user.active_workspace_id = "deleted-or-bogus-id"
    db_session.commit()
    with pytest.raises(HTTPException) as error:
        get_current_workspace_id(user, db_session, user.active_workspace_id)
    assert error.value.status_code == 403


# --- endpoints --------------------------------------------------------------

def _course_with_tester(role):
    """Create a course owned by a prof, add the authed tester with ``role``."""
    db = SessionLocal()
    try:
        owner = User(email="prof@example.com", password_hash="x")
        db.add(owner)
        db.flush()
        course = Workspace(
            name="Сети", owner_user_id=owner.id, kind=courses.WORKSPACE_KIND_COURSE, join_code="ABCD2345"
        )
        db.add(course)
        db.flush()
        tester = db.query(User).filter(User.email == "tester@example.com").one()
        db.add(WorkspaceMember(workspace_id=course.id, user_id=tester.id, role=role))
        db.commit()
        return course.id
    finally:
        db.close()


def test_list_workspaces_personal_only_for_fresh_user(authed_client):
    data = authed_client.get("/api/workspaces").json()
    assert len(data["workspaces"]) == 1
    only = data["workspaces"][0]
    assert only["kind"] == "personal"
    assert only["role"] == "owner"
    assert only["is_active"] is True
    assert data["active_workspace_id"] == only["id"]


def test_member_can_activate_course(authed_client):
    course_id = _course_with_tester(courses.ROLE_STUDENT)

    resp = authed_client.post(f"/api/workspaces/{course_id}/activate")
    assert resp.status_code == 200
    assert resp.json()["id"] == course_id
    assert resp.json()["role"] == "student"

    listing = authed_client.get("/api/workspaces").json()
    assert listing["active_workspace_id"] == course_id
    by_id = {w["id"]: w for w in listing["workspaces"]}
    assert by_id[course_id]["role"] == "student"
    assert by_id[course_id]["is_active"] is True


def test_non_member_cannot_activate(authed_client):
    db = SessionLocal()
    try:
        owner = User(email="prof2@example.com", password_hash="x")
        db.add(owner)
        db.flush()
        course = Workspace(name="Закрытый", owner_user_id=owner.id, kind=courses.WORKSPACE_KIND_COURSE)
        db.add(course)
        db.commit()
        course_id = course.id
    finally:
        db.close()

    assert authed_client.post(f"/api/workspaces/{course_id}/activate").status_code == 403


def test_activate_unknown_workspace_is_404(authed_client):
    assert authed_client.post("/api/workspaces/does-not-exist/activate").status_code == 404


def test_active_falls_back_after_membership_revoked(authed_client):
    course_id = _course_with_tester(courses.ROLE_STUDENT)
    authed_client.post(f"/api/workspaces/{course_id}/activate")

    db = SessionLocal()
    try:
        db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == course_id).delete()
        db.commit()
    finally:
        db.close()

    listing = authed_client.get("/api/workspaces").json()
    personal = next(w for w in listing["workspaces"] if w["kind"] == "personal")
    # The revoked course is gone from the list and active falls back to personal.
    assert listing["active_workspace_id"] == personal["id"]
    assert all(w["id"] != course_id for w in listing["workspaces"])
