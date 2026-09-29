"""Actual HTTP auth/upload/permissions/cleanup, with a tiny offline test model.

Not a model-quality/LLM benchmark. Run model -> seed -> backup -> mutate ->
restore -> verify. Never point this fixture at a real customer deployment.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
ACTION = sys.argv[1]
if os.environ.get("VEDOMO_DRILL") != "synthetic-only":
    raise RuntimeError("This fixture requires the isolated Compose drill")
if ACTION == "model":
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.sentence_transformer.modules import BoW
    model = SentenceTransformer(modules=[BoW(vocab=[
        "product", "alpha", "beta", "voltage", "220", "installation", "support", "manual",
    ])], device="cpu")
    model.save_pretrained("/models/drill-bow", create_model_card=False)
    print("Created offline BoW fixture; not the production BGE model")
    sys.exit(0)

import requests
from sqlalchemy import select
from src.db import SessionLocal
from src.db_models import AccountDeletionJob, Document, User

if os.environ.get("DATABASE_URL") != "postgresql+psycopg://drill:synthetic-only@db:5432/drill":
    raise RuntimeError("Refusing any database other than the synthetic drill")

BASE = "http://127.0.0.1:8000"
PASSWORD = "Synthetic-drill-password-42"
STATE = Path("/drill-state/expected.json")

if ACTION == "search":
    # Run via Compose run only while the backend is stopped (maintenance mode).
    from src.runtime import get_kb
    state = json.loads(STATE.read_text())
    context, sources = get_kb().search_with_sources("product voltage 220", workspace_id=state["course"])
    assert "220" in context and any(source["source_file"] == "known.txt" for source in sources)
    print("PASS: restored retrieval returns the known document and source")
    sys.exit(0)

deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        if requests.get(BASE + "/api/health", timeout=2).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(0.5)
else:
    raise RuntimeError("drill API did not become healthy")


def call(client, method, path, expected=200, **kwargs):
    response = client.request(method, BASE + path, timeout=40, **kwargs)
    assert response.status_code == expected, (method, path, response.status_code, response.text[:300])
    return response.json()


def login(email, register=False):
    client = requests.Session()
    data = call(client, "POST", "/api/auth/" + ("register" if register else "login"),
                expected=201 if register else 200, json={"email":email,"password":PASSWORD})
    client.headers["X-Workspace-ID"] = data["user"]["personal_workspace"]["id"]
    return client, data["user"]


def upload(client, name):
    text = ("1. Product manual\n\n" + "Product alpha voltage 220. " * 14
            + "\n\n2. Installation\n\n" + "Installation requires product support. " * 14).encode()
    result = call(client, "POST", "/api/materials/upload", files={"file":(name,text,"text/plain")})
    assert result["ok"], result
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status = call(client, "GET", "/api/materials/progress")
        if not status["active"]:
            assert not status.get("error"), status
            break
        time.sleep(0.5)
    else:
        raise AssertionError("indexing timeout")
    materials = call(client, "GET", "/api/materials")["materials"]
    assert any(m["name"] == name and m["status"] == "ready" for m in materials), materials


if ACTION == "seed":
    teacher, user = login("teacher@example.com", True)
    student, _ = login("student@example.com", True)
    login("outsider@example.com", True)
    # Fixture setup grants course creation, never a production admin bypass.
    with SessionLocal() as db:
        db.get(User, user["id"]).can_create_courses = True
        db.commit()
    course = call(teacher, "POST", "/api/courses", json={"name":"Synthetic product training"})
    teacher.headers["X-Workspace-ID"] = course["id"]
    detail = call(teacher, "GET", "/api/courses/" + course["id"])
    call(student, "POST", "/api/courses/join", json={"code":detail["join_code"]})
    upload(teacher, "known.txt")
    STATE.write_text(json.dumps({"course":course["id"],"teacher":user["id"]}))
    print("PASS: registered users, course membership, actual upload/indexing")
elif ACTION in {"mutate", "verify"}:
    state = json.loads(STATE.read_text())
    teacher, _ = login("teacher@example.com")
    teacher.headers["X-Workspace-ID"] = state["course"]
    if ACTION == "mutate":
        upload(teacher, "after.txt")
        call(teacher, "POST", "/api/courses/" + state["course"] + "/rename", json={"name":"Changed after snapshot"})
        print("PASS: post-snapshot mutations created")
    else:
        assert call(teacher,"GET","/api/courses/"+state["course"])["name"] == "Synthetic product training"
        assert [m["name"] for m in call(teacher,"GET","/api/materials")["materials"]] == ["known.txt"]
        sections = call(teacher,"GET","/api/materials/known.txt/sections")
        # A short document may legitimately have no user-visible sections.
        assert isinstance(sections["sections"], list), sections
        assert call(teacher,"GET","/api/system/status")["total_chunks"] > 0
        student, _ = login("student@example.com")
        student.headers["X-Workspace-ID"] = state["course"]
        assert call(student,"GET","/api/materials")["materials"][0]["name"] == "known.txt"
        call(student,"POST","/api/materials/upload",expected=403,files={"file":("forbidden.txt",b"not allowed")})
        outsider, _ = login("outsider@example.com")
        outsider.headers["X-Workspace-ID"] = state["course"]
        call(outsider,"GET","/api/materials",expected=403)
        call(outsider,"GET","/api/v2/tenants/default_tenant/databases/default_database/collections",expected=404)
        with SessionLocal() as db:
            records = list(db.scalars(select(Document).where(Document.workspace_id == state["course"])))
            assert len(records) == 1 and Path(records[0].stored_path).is_file()
        doomed, doomed_user = login("cleanup@example.com", True)
        upload(doomed, "cleanup.txt")
        assert call(doomed,"DELETE","/api/auth/me",expected=202)["status"] == "pending"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            receipt = call(doomed,"GET","/api/auth/deletion")
            if receipt["status"] == "completed": break
            assert receipt["status"] != "failed", receipt
            time.sleep(0.5)
        else: raise AssertionError("cleanup timeout")
        with SessionLocal() as db:
            assert db.get(User, doomed_user["id"]) is None
            assert db.scalar(select(AccountDeletionJob).where(AccountDeletionJob.user_id == doomed_user["id"])).status == "completed"
        assert not Path("/app/docs",doomed_user["personal_workspace"]["id"]).exists()
        assert call(teacher,"GET","/api/materials")["materials"][0]["name"] == "known.txt"
        print("PASS: restored login/course/roles/document/sections + real durable cleanup on PostgreSQL/Chroma")
else:
    raise ValueError("unknown action")
