"""Durable cleanup, real process restart, and request/indexing race regressions."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
from filelock import FileLock
from sqlalchemy.orm import Session

import api_app
import config
from src import account_deletion, app_services, runtime, storage
from src.db import SessionLocal
from src.db_models import AccountDeletionJob, Document, User, Workspace
from src.operation_guard import CleanupDrainMiddleware, operations


class Vectors:
    def __init__(self):
        self.documents = []
    def remove_orphan_chunks(self, **kwargs):
        return {"removed_chunks": 0}
    def remove_chunks(self, *, workspace_id, document_id):
        self.documents.append(document_id)


@pytest.fixture(autouse=True)
def isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(runtime, "get_kb", lambda: Vectors())
    yield
    operations.release_exclusive()


def user_id(client):
    return client.get("/api/auth/me").json()["id"]


def test_missing_header_rejected_even_with_server_selection(authed_client):
    authed_client.post("/api/courses", json={"name":"Explicit only"})
    authed_client.headers.pop("X-Workspace-ID")
    assert authed_client.get("/api/materials").status_code == 428
    assert authed_client.post("/api/chat", json={"message":"test"}).status_code == 428
    # Discovery and account endpoints do not require a workspace destination.
    assert authed_client.get("/api/workspaces").status_code == 200


def test_acceptance_is_durable_idempotent_and_freezes_access(authed_client, monkeypatch):
    uid = user_id(authed_client)
    def no_models():
        pytest.fail("accepting a deletion request must not load models")
    monkeypatch.setattr(runtime, "get_kb", no_models)
    for _ in range(2):
        response = authed_client.delete("/api/auth/me")
        assert response.status_code == 202
        assert response.json() == {"status":"pending", "attempts":0, "error_code":""}
    with SessionLocal() as db:
        assert db.query(AccountDeletionJob).filter_by(user_id=uid).count() == 1
        assert db.get(User, uid) is not None
    assert authed_client.get("/api/materials").status_code == 403
    assert authed_client.post("/api/courses", json={"name":"Too late"}).status_code == 403
    assert authed_client.get("/api/auth/deletion").json()["status"] == "pending"
    assert authed_client.get("/api/auth/me").json()["deletion_status"] == "pending"


def test_waits_for_background_work_before_purge(authed_client):
    started, finish = Event(), Event()
    wid = authed_client.headers["X-Workspace-ID"]
    def target():
        started.set()
        assert finish.wait(10)
    app_services._launch_material_job(wid, "upload", "synthetic", target)
    assert started.wait(5)
    try:
        assert authed_client.delete("/api/auth/me").status_code == 202
        assert account_deletion.process_one() is False
        assert authed_client.get("/api/health").status_code == 200
        assert authed_client.get("/api/workspaces").status_code == 503
        assert authed_client.get("/api/auth/deletion").json()["status"] == "pending"
    finally:
        finish.set()
        app_services._material_job_thread.join(5)
    assert account_deletion.process_one()
    assert authed_client.get("/api/auth/deletion").json()["status"] == "completed"


def test_retry_after_cleanup_and_sql_commit_failure(authed_client, monkeypatch):
    uid = user_id(authed_client)
    wid = authed_client.headers["X-Workspace-ID"]
    directory = Path(storage.workspace_docs_dir(wid))
    directory.mkdir(parents=True)
    (directory / "synthetic.txt").write_text("synthetic")
    authed_client.delete("/api/auth/me")
    commit = Session.commit
    def fail_final_commit(db):
        if any(isinstance(item, User) for item in db.deleted):
            raise OSError("synthetic SQL commit failure")
        return commit(db)
    monkeypatch.setattr(Session, "commit", fail_final_commit)
    assert account_deletion.process_one()
    with SessionLocal() as db:
        assert db.get(User, uid) is not None
        job = account_deletion.job_for_user(db, uid)
        assert job.status == "failed" and job.attempts == 1
        assert account_deletion.process_one() is False  # respect backoff
        job.next_attempt_at = None
        db.commit()
    assert not directory.exists()  # external cleanup cannot be rolled back
    monkeypatch.setattr(Session, "commit", commit)
    assert account_deletion.process_one()
    with SessionLocal() as db:
        assert db.get(User, uid) is None
        assert account_deletion.job_for_user(db, uid).status == "completed"


def test_real_process_exit_mid_cleanup_resumes_from_receipt(authed_client):
    uid = user_id(authed_client)
    authed_client.delete("/api/auth/me")
    code = """
import os
from src import runtime, account_deletion
class Interrupt:
    def remove_orphan_chunks(self, **kwargs):
        os._exit(23)
runtime.get_kb = lambda: Interrupt()
account_deletion.process_one()
"""
    result = subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), timeout=30)
    assert result.returncode == 23
    with SessionLocal() as db:
        job = account_deletion.job_for_user(db, uid)
        assert job.status == "processing" and job.attempts == 1
    assert account_deletion.process_one()
    with SessionLocal() as db:
        assert db.get(User, uid) is None
        job = account_deletion.job_for_user(db, uid)
        assert job.status == "completed" and job.attempts == 2


def test_cleanup_covers_owned_uploads_without_wiping_other_library(authed_client, monkeypatch):
    uid = user_id(authed_client)
    vectors = Vectors()
    monkeypatch.setattr(runtime, "get_kb", lambda: vectors)
    with SessionLocal() as db:
        owner = User(email="shared-owner@example.com", password_hash="synthetic")
        db.add(owner); db.flush()
        ws = Workspace(owner_user_id=owner.id, name="Shared", kind="course")
        db.add(ws); db.flush()
        directory = Path(storage.workspace_docs_dir(ws.id))
        directory.mkdir(parents=True)
        mine, theirs = directory / "mine.txt", directory / "theirs.txt"
        mine.write_text("synthetic"); theirs.write_text("keep")
        document = Document(workspace_id=ws.id, owner_user_id=uid, original_name="mine.txt", stored_path=str(mine))
        keep = Document(workspace_id=ws.id, owner_user_id=owner.id, original_name="theirs.txt", stored_path=str(theirs))
        db.add_all([document, keep]); db.commit()
        did, keep_id, wid = document.id, keep.id, ws.id
    authed_client.delete("/api/auth/me")
    assert account_deletion.process_one()
    assert not mine.exists() and theirs.exists()
    assert vectors.documents == [did]
    with SessionLocal() as db:
        assert db.get(Workspace, wid) is not None
        assert db.get(Document, keep_id) is not None
        assert db.get(Document, did) is None


def test_owned_workspace_frozen_for_other_members(authed_client, monkeypatch):
    from conftest import WorkspaceClient
    course = authed_client.post("/api/courses", json={"name":"Frozen"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course}").json()["join_code"]
    with WorkspaceClient(api_app.app) as other:
        other.post("/api/auth/register", json={"email":"member@example.com", "password":"synthetic1234"})
        other.post("/api/courses/join", json={"code":code})
        authed_client.delete("/api/auth/me")
        assert other.get("/api/materials").status_code == 403
        assert other.post("/api/courses/join", json={"code":code}).status_code == 404


def test_stream_keeps_guard_until_last_body():
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async def application(scope, receive, send):
            started.set()
            await finish.wait()
        async def unused(*args):
            pass
        task = asyncio.create_task(CleanupDrainMiddleware(application)(
            {"type":"http", "method":"POST", "path":"/api/chat/stream"}, unused, unused))
        await started.wait()
        assert operations.try_exclusive() is False
        assert operations.enter() is False
        finish.set()
        await task
        assert operations.try_exclusive() is True
    asyncio.run(scenario())


def test_second_process_cannot_open_same_storage(tmp_path):
    lock = str(tmp_path / "api.lock")
    code = """
import sys
from filelock import FileLock, Timeout
try:
    with FileLock(sys.argv[1], timeout=0, is_singleton=True, thread_local=False):
        sys.exit(1)
except Timeout:
    sys.exit(0)
"""
    with FileLock(lock, timeout=0, is_singleton=True, thread_local=False):
        result = subprocess.run([sys.executable, "-c", code, lock], timeout=15)
    assert result.returncode == 0
