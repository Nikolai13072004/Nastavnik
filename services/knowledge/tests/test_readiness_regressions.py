"""Regression coverage for the readiness audit. Synthetic data, no live models."""
import pytest
from fastapi import HTTPException
from src import app_services, auth_service, runtime, storage
from src.auth_models import UserCreate
from src.db_models import User, Workspace, WorkspaceMember
from src.db import SessionLocal


@pytest.fixture
def api_db(authed_client):
    with SessionLocal() as session:
        yield session


def test_second_tab_must_not_redirect_first_tabs_material_request(authed_client, monkeypatch):
    initial = authed_client.get("/api/workspaces").json()["active_workspace_id"]
    other = authed_client.post("/api/courses", json={"name": "Audit second tab"}).json()["id"]
    authed_client.post(f"/api/workspaces/{initial}/activate")
    # Tab A is displaying `initial`. Both tabs use the same browser session.
    authed_client.post(f"/api/workspaces/{other}/activate")
    monkeypatch.setattr(app_services, "list_materials", lambda workspace_id: {
        "materials": [{"name": workspace_id, "sections_count": 1,
                       "quality_label": "ready", "quality_reason": "probe"}]
    })
    # The updated browser sends the destination displayed by this tab.
    response = authed_client.get("/api/materials", headers={"X-Workspace-ID": initial})
    assert response.status_code == 200
    assert response.json()["materials"][0]["name"] == initial, (
        "Tab A's request was silently served from Tab B's workspace"
    )


def test_failed_vector_cleanup_must_not_report_account_deleted(db_session, monkeypatch, tmp_path):
    class UnavailableVectors:
        def remove_orphan_chunks(self, **kwargs):
            raise OSError("Synthetic vector storage unavailable")

    user = auth_service.register_user(db_session, UserCreate(
        email="deletion-probe@example.com", password="synthetic-password-123"
    ))
    user_id = user.id
    monkeypatch.setattr(runtime, "get_kb", lambda: UnavailableVectors())
    monkeypatch.setattr(storage, "workspace_docs_dir", lambda _id: str(tmp_path / "no-docs"))
    with pytest.raises(HTTPException) as info:
        app_services.delete_account_service(db_session, user)
    assert info.value.status_code == 503
    assert db_session.get(User, user_id) is not None, (
        "Account deletion succeeded even though vector cleanup failed; no retry owner remains"
    )


@pytest.mark.parametrize("destination", ["", "missing-workspace"])
def test_invalid_explicit_workspace_never_falls_back(authed_client, destination):
    response = authed_client.get("/api/materials", headers={"X-Workspace-ID": destination})
    assert response.status_code == 403
    assert response.json()["detail"] == "workspace_unavailable"


def test_explicit_workspace_rechecks_membership_and_write_role(authed_client, api_db, monkeypatch):
    db_session = api_db
    tester = db_session.query(User).filter_by(email="tester@example.com").one()
    owner = User(email="other-owner@example.com", password_hash="synthetic")
    db_session.add(owner)
    db_session.flush()
    course = Workspace(name="Private", owner_user_id=owner.id, kind="course")
    db_session.add(course)
    db_session.commit()
    header = {"X-Workspace-ID": course.id}
    assert authed_client.get("/api/materials", headers=header).status_code == 403
    member = WorkspaceMember(workspace_id=course.id, user_id=tester.id, role="student")
    db_session.add(member)
    db_session.commit()
    monkeypatch.setattr(app_services, "list_materials", lambda workspace_id: {"materials": []})
    assert authed_client.get("/api/materials", headers=header).status_code == 200
    assert authed_client.post("/api/materials/upload", headers=header,
                             files={"file": ("sample.txt", b"synthetic")}).status_code == 403
    db_session.delete(member)
    db_session.commit()
    assert authed_client.get("/api/materials", headers=header).status_code == 403


def test_archived_explicit_workspace_is_rejected(authed_client, api_db):
    db_session = api_db
    course_id = authed_client.post("/api/courses", json={"name": "Archive test"}).json()["id"]
    course = db_session.get(Workspace, course_id)
    course.is_archived = True
    db_session.commit()
    assert authed_client.get("/api/materials", headers={"X-Workspace-ID": course_id}).status_code == 403


def test_filesystem_cleanup_failure_retains_metadata_and_can_retry(db_session, monkeypatch, tmp_path):
    user = auth_service.register_user(db_session, UserCreate(
        email="cleanup-retry@example.com", password="synthetic-password-123"
    ))
    uid = user.id
    wid = user.owned_workspaces[0].id
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "sample.txt").write_text("synthetic")
    class Vectors:
        def remove_orphan_chunks(self, **kwargs):
            return {"removed_chunks": 0}
    monkeypatch.setattr(runtime, "get_kb", lambda: Vectors())
    monkeypatch.setattr(storage, "workspace_docs_dir", lambda _: str(docs))
    real_remove = app_services.shutil.rmtree
    def fail_remove(*args, **kwargs):
        raise PermissionError("synthetic lock")
    monkeypatch.setattr(app_services.shutil, "rmtree", fail_remove)
    with pytest.raises(HTTPException) as info:
        app_services.delete_account_service(db_session, user)
    assert info.value.status_code == 503
    assert db_session.get(User, uid) is not None
    assert db_session.get(Workspace, wid) is not None
    assert docs.exists()
    monkeypatch.setattr(app_services.shutil, "rmtree", real_remove)
    app_services.delete_account_service(db_session, user)
    assert db_session.get(User, uid) is None
    assert not docs.exists()


def test_cleanup_failure_does_not_clear_session(authed_client, monkeypatch):
    def unavailable():
        raise OSError("synthetic outage")
    monkeypatch.setattr(runtime, "get_kb", unavailable)
    response = authed_client.delete("/api/auth/me")
    assert response.status_code == 202
    from src.account_deletion import process_one
    assert process_one()
    assert authed_client.get("/api/auth/deletion").json()["status"] == "failed"
    assert authed_client.get("/api/auth/me").status_code == 200
