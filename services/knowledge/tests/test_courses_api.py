"""Stage 15-4: course lifecycle + membership management endpoints.

Drives create / join / manage through the API as a real user, plus a second
registered user for the teacher↔student interactions. KB/LLM are never touched
(these endpoints are pure DB + authz).
"""

# Importing api_app registers models before db_session-based fixtures; here we
# use api_client and a direct session for the second user.
import api_app  # noqa: F401
from src import app_services, audit_service, courses
from src.db import SessionLocal
from src.db_models import User, Workspace, WorkspaceMember


class _NoopKB:
    """KB stub so delete-course tests don't load embedding models."""

    def clear(self, *, workspace_id):
        return None


def _register(client, email):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": "password12345", "display_name": email.split("@")[0]},
    )


def test_create_course_makes_owner_and_returns_active(authed_client):
    resp = authed_client.post("/api/courses", json={"name": "Сети"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "course"
    assert body["role"] == "owner"
    assert body["is_active"] is True

    # It shows up in the switcher and is active.
    listing = authed_client.get("/api/workspaces").json()
    assert listing["active_workspace_id"] == body["id"]


def test_create_course_requires_name(authed_client):
    assert authed_client.post("/api/courses", json={"name": "   "}).status_code == 400


def test_create_course_writes_audit_event(authed_client):
    body = authed_client.post("/api/courses", json={"name": "Аудируемый курс"}).json()
    events = audit_service.list_recent(limit=50)
    match = [
        e
        for e in events
        if e.action == audit_service.ACTION_CREATE_COURSE and e.workspace_id == body["id"]
    ]
    assert match, "course creation must write a create_course audit event"
    assert match[0].target == "Аудируемый курс"


def test_owner_sees_join_code_and_student_joins(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    detail = authed_client.get(f"/api/courses/{course_id}").json()
    code = detail["join_code"]
    assert len(code) == courses.JOIN_CODE_LENGTH
    assert detail["join_enabled"] is True
    # Owner is listed as a member with role owner.
    assert any(m["role"] == "owner" for m in detail["members"])

    # A second user joins with the code → becomes a student, switched in.
    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as student:
        _register(student, "student@example.com")
        joined = student.post("/api/courses/join", json={"code": code})
        assert joined.status_code == 200
        assert joined.json()["id"] == course_id
        assert joined.json()["role"] == "student"
        # Joining again is idempotent (still student, no error).
        again = student.post("/api/courses/join", json={"code": code})
        assert again.status_code == 200
        assert again.json()["role"] == "student"

    # Owner now sees the student in the member list.
    members = authed_client.get(f"/api/courses/{course_id}").json()["members"]
    roles = {m["email"]: m["role"] for m in members}
    assert roles.get("student@example.com") == "student"


def test_join_with_bad_or_disabled_code_is_404(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]

    assert authed_client.post("/api/courses/join", json={"code": "NOPENOPE"}).status_code == 404

    # Disable joining → even the right code is rejected.
    detail = authed_client.get(f"/api/courses/{course_id}").json()
    authed_client.post(f"/api/courses/{course_id}/join-code", json={"enabled": False})
    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as other:
        _register(other, "late@example.com")
        assert other.post("/api/courses/join", json={"code": detail["join_code"]}).status_code == 404


def test_rotate_join_code_invalidates_old(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    old = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]
    rotated = authed_client.post(f"/api/courses/{course_id}/join-code", json={"rotate": True}).json()
    assert rotated["join_code"] != old

    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as student:
        _register(student, "s2@example.com")
        assert student.post("/api/courses/join", json={"code": old}).status_code == 404
        assert student.post("/api/courses/join", json={"code": rotated["join_code"]}).status_code == 200


def _join_second_user(client_app, course_code, email="member@example.com"):
    from fastapi.testclient import TestClient

    with TestClient(client_app) as c:
        _register(c, email)
        c.post("/api/courses/join", json={"code": course_code})
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one().id
    finally:
        db.close()


def test_manager_can_promote_and_remove_member(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]
    member_id = _join_second_user(api_app.app, code)

    # Promote student → teacher.
    promoted = authed_client.post(
        f"/api/courses/{course_id}/members/{member_id}/role", json={"role": "teacher"}
    )
    assert promoted.status_code == 200
    roles = {m["user_id"]: m["role"] for m in promoted.json()["members"]}
    assert roles[member_id] == "teacher"

    # Remove them.
    removed = authed_client.delete(f"/api/courses/{course_id}/members/{member_id}")
    assert removed.status_code == 200
    assert all(m["user_id"] != member_id for m in removed.json()["members"])


def test_owner_cannot_be_removed_or_demoted(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    db = SessionLocal()
    try:
        owner_id = db.query(Workspace).filter(Workspace.id == course_id).one().owner_user_id
    finally:
        db.close()

    assert authed_client.delete(f"/api/courses/{course_id}/members/{owner_id}").status_code == 400
    assert (
        authed_client.post(
            f"/api/courses/{course_id}/members/{owner_id}/role", json={"role": "student"}
        ).status_code
        == 400
    )


def test_student_cannot_manage_members(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]

    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as student:
        _register(student, "stud@example.com")
        student.post("/api/courses/join", json={"code": code})
        # A student may not view the management detail or rotate the code.
        assert student.get(f"/api/courses/{course_id}").status_code == 403
        assert student.post(f"/api/courses/{course_id}/join-code", json={"rotate": True}).status_code == 403


def test_owner_can_delete_course_others_cannot(authed_client, monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _NoopKB())
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]

    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as student:
        _register(student, "del-stud@example.com")
        student.post("/api/courses/join", json={"code": code})
        # A student can't delete the course.
        assert student.delete(f"/api/courses/{course_id}").status_code == 403

    # Owner deletes it.
    assert authed_client.delete(f"/api/courses/{course_id}").status_code == 200

    # Gone from the switcher and no longer reachable.
    listing = authed_client.get("/api/workspaces").json()
    assert all(w["id"] != course_id for w in listing["workspaces"])
    assert authed_client.get(f"/api/courses/{course_id}").status_code == 404


def test_member_can_leave_but_owner_cannot(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]

    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as student:
        _register(student, "leaver@example.com")
        student.post("/api/courses/join", json={"code": code})
        assert student.post(f"/api/courses/{course_id}/leave").status_code == 200

    # Owner can't leave their own course.
    assert authed_client.post(f"/api/courses/{course_id}/leave").status_code == 400


# --- Course lifecycle: archive + copy (Stage 22) ---------------------------
_QS = [{"question": "2+2?", "options": ["3", "4"], "correct_index": 1, "explanation": ""}]


def test_archive_hides_course_blocks_join_and_restores(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]

    assert authed_client.post(f"/api/courses/{course_id}/archive", json={"archived": True}).status_code == 200

    listing = authed_client.get("/api/workspaces").json()
    assert listing["active_workspace_id"] != course_id  # archived → bumped to personal
    item = next(w for w in listing["workspaces"] if w["id"] == course_id)
    assert item["is_archived"] is True
    # Can't activate an archived course.
    assert authed_client.post(f"/api/workspaces/{course_id}/activate").status_code == 400

    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as late:
        _register(late, "late-arch@example.com")
        assert late.post("/api/courses/join", json={"code": code}).status_code == 404  # archived not joinable

    # Restore.
    assert authed_client.post(f"/api/courses/{course_id}/archive", json={"archived": False}).status_code == 200
    restored = next(
        w for w in authed_client.get("/api/workspaces").json()["workspaces"] if w["id"] == course_id
    )
    assert restored["is_archived"] is False


def test_non_owner_cannot_archive_or_copy(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]

    from fastapi.testclient import TestClient

    with TestClient(api_app.app) as member:
        _register(member, "life-member@example.com")
        member.post("/api/courses/join", json={"code": code})
        assert member.post(f"/api/courses/{course_id}/archive", json={"archived": True}).status_code == 403
        assert member.post(f"/api/courses/{course_id}/copy").status_code == 403


def test_copy_course_clones_assignments_as_drafts(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    old_code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]
    # An assignment in the source (owner is active in the new course right after create).
    aid = authed_client.post("/api/assignments", json={"title": "К1", "questions": _QS}).json()["id"]
    authed_client.post(f"/api/assignments/{aid}/publish", json={"published": True})

    copied = authed_client.post(f"/api/courses/{course_id}/copy")
    assert copied.status_code == 200
    new = copied.json()
    assert new["id"] != course_id
    assert new["role"] == "owner" and new["is_active"] is True

    # Active workspace is now the new course → its assignments are drafts.
    listing = authed_client.get("/api/assignments").json()
    assert listing["can_manage"] is True
    assert len(listing["assignments"]) == 1
    assert listing["assignments"][0]["is_published"] is False

    # New course: fresh join code, only the owner as a member.
    detail = authed_client.get(f"/api/courses/{new['id']}").json()
    assert detail["join_code"] != old_code
    assert len(detail["members"]) == 1
