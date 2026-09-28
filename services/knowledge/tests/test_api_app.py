"""End-to-end tests for the Vedomo API.

Stage 3a flipped every non-public endpoint to require authentication, so all
of the original "happy path" tests now go through the ``authed_client``
fixture (registers a regular user, cookie is auto-replayed on every
subsequent request). Public endpoints stay on the bare ``api_client``
fixture so we keep verifying they don't accidentally start demanding auth.

The ``test_auth_required_for_protected_endpoints`` matrix is the safety net
that catches a missing auth dependency on any future endpoint.
"""

from src.api_models import (
    ChatResponse,
    MaterialActionResponse,
    MaterialProgressResponse,
    MaterialsResponse,
    SectionsResponse,
    SummaryResponse,
    SystemStatus,
)

import api_app


# ---------------------------------------------------------------------------
# Public endpoints — must keep working without authentication
# ---------------------------------------------------------------------------


def test_health_endpoint_is_public(api_client):
    response = api_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_register_is_public(api_client):
    response = api_client.post(
        "/api/auth/register",
        json={
            "email": "newcomer@example.com",
            "password": "password12345",
            "display_name": "Newcomer",
        },
    )

    assert response.status_code == 201
    assert response.json()["user"]["email"] == "newcomer@example.com"


def test_auth_login_is_public(api_client):
    api_client.post(
        "/api/auth/register",
        json={
            "email": "returning@example.com",
            "password": "password12345",
        },
    )
    # Drop the registration cookie so the login call is genuinely unauthenticated.
    api_client.cookies.clear()

    response = api_client.post(
        "/api/auth/login",
        json={"email": "returning@example.com", "password": "password12345"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "returning@example.com"


# ---------------------------------------------------------------------------
# Unauthenticated callers must be rejected on protected endpoints
# ---------------------------------------------------------------------------


_PROTECTED_ENDPOINTS = [
    ("GET", "/api/system/status"),
    ("GET", "/api/materials"),
    ("POST", "/api/materials/upload"),
    ("GET", "/api/materials/progress"),
    ("GET", "/api/materials/book.pdf/sections"),
    ("POST", "/api/materials/reindex"),
    ("POST", "/api/materials/book.pdf/reindex"),
    ("DELETE", "/api/materials/book.pdf"),
    ("POST", "/api/summaries"),
    ("POST", "/api/chat"),
    ("POST", "/api/exports/summary"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
]


def test_protected_endpoints_reject_unauthenticated_requests(api_client):
    """Each protected endpoint should answer with 401 when no session cookie
    is present. JSON payloads/uploaded files are kept minimal — the assertion
    here is about the auth gate, not the business logic."""
    for method, path in _PROTECTED_ENDPOINTS:
        if method == "POST" and "upload" in path:
            response = api_client.post(
                path,
                files={"file": ("book.pdf", b"hello", "application/pdf")},
            )
        elif method == "POST":
            response = api_client.post(path, json={})
        elif method == "DELETE":
            response = api_client.delete(path)
        else:
            response = api_client.get(path)

        assert response.status_code == 401, (
            f"{method} {path} expected 401 without auth, got {response.status_code}"
        )


_ADMIN_ENDPOINTS = [
    "/api/diagnostics/latest",
    "/api/diagnostics/latest.json",
]


def test_admin_endpoints_reject_unauthenticated_requests(api_client):
    for path in _ADMIN_ENDPOINTS:
        response = api_client.get(path)
        assert response.status_code == 401, (
            f"GET {path} expected 401 without auth, got {response.status_code}"
        )


def test_admin_endpoints_reject_regular_users(authed_client):
    """A logged-in non-superuser should hit 403, not 401."""
    for path in _ADMIN_ENDPOINTS:
        response = authed_client.get(path)
        assert response.status_code == 403, (
            f"GET {path} expected 403 for non-superuser, got {response.status_code}"
        )
        assert response.json()["detail"] == "superuser_required"


def test_admin_endpoints_allow_superusers(superuser_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services, "get_latest_diagnostics_text", lambda: "trace dump"
    )
    monkeypatch.setattr(
        api_app.services, "get_latest_diagnostics_json", lambda: {"status": "ok"}
    )

    text_response = superuser_client.get("/api/diagnostics/latest")
    json_response = superuser_client.get("/api/diagnostics/latest.json")

    assert text_response.status_code == 200
    assert text_response.json() == {"text": "trace dump"}
    assert json_response.status_code == 200
    assert json_response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Authenticated happy-path tests (parity with the pre-Stage-3a coverage)
# ---------------------------------------------------------------------------


def test_system_status_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "get_system_status",
        lambda workspace_id: SystemStatus(
            llm_mode="ollama",
            model="qwen2.5:14b",
            embedding_model="BAAI/bge-m3",
            reranker_model="BAAI/bge-reranker-v2-m3",
            chunk_size=1200,
            hyde_enabled=False,
            total_books=1,
            total_chunks=2,
        ),
    )

    response = authed_client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["model"] == "qwen2.5:14b"


def test_materials_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "list_materials",
        lambda workspace_id: MaterialsResponse(materials=[]),
    )

    response = authed_client.get("/api/materials")

    assert response.status_code == 200
    assert response.json() == {"materials": []}


def test_material_upload_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "start_upload_material_service",
        lambda workspace_id, user_id, file_name, content: MaterialActionResponse(
            ok=True,
            message=f"uploaded:{file_name}:{len(content)}",
            material_name=file_name,
        ),
    )

    response = authed_client.post(
        "/api/materials/upload",
        files={"file": ("book.pdf", b"hello", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["material_name"] == "book.pdf"


def test_material_progress_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "get_material_progress",
        lambda workspace_id: MaterialProgressResponse(
            active=True,
            operation="upload",
            phase="indexing",
            message="Сохраняю фрагменты",
            progress=64,
            current_file="book.pdf",
            error="",
        ),
    )

    response = authed_client.get("/api/materials/progress")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["progress"] == 64


def test_sections_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "list_sections",
        lambda workspace_id, file_filter="all": SectionsResponse(sections=["Глава 1"]),
    )

    response = authed_client.get("/api/materials/a.pdf/sections")

    assert response.status_code == 200
    assert response.json() == {"sections": ["Глава 1"]}


def test_material_reindex_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "start_reindex_material_service",
        lambda workspace_id, file_name=None: MaterialActionResponse(
            ok=True,
            message=f"reindex:{file_name or 'all'}",
            material_name=file_name or "",
        ),
    )

    response = authed_client.post("/api/materials/a.pdf/reindex")

    assert response.status_code == 200
    assert response.json()["message"] == "reindex:a.pdf"


def test_library_reindex_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "start_reindex_material_service",
        lambda workspace_id, file_name=None: MaterialActionResponse(
            ok=True,
            message="reindex:all",
            material_name="",
        ),
    )

    response = authed_client.post("/api/materials/reindex")

    assert response.status_code == 200
    assert response.json()["message"] == "reindex:all"


def test_material_delete_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "start_delete_material_service",
        lambda workspace_id, file_name: MaterialActionResponse(
            ok=True,
            message=f"deleted:{file_name}",
            material_name=file_name,
        ),
    )

    response = authed_client.delete("/api/materials/a.pdf")

    assert response.status_code == 200
    assert response.json()["message"] == "deleted:a.pdf"


def test_summary_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "generate_summary_service",
        lambda workspace_id, request: SummaryResponse(
            text=f"summary:{request.topic}",
            diagnostics="trace",
            trace={"status": "ok"},
        ),
    )

    response = authed_client.post(
        "/api/summaries",
        json={"topic": "Bluetooth", "summary_type": "Средний"},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "summary:Bluetooth"
    assert response.json()["diagnostics"] == "trace"
    assert response.json()["trace"] == {"status": "ok"}


def test_chat_endpoint(authed_client, monkeypatch):
    monkeypatch.setattr(
        api_app.services,
        "chat_service",
        lambda workspace_id, request, **kwargs: ChatResponse(
            answer=f"answer:{request.message}",
            summary="короткий итог",
            confidence_label="high",
            followup_suggestions=["Объясни подробнее", "Сравни с Wi-Fi"],
            history=[{"role": "user", "content": request.message}],
            sources=[],
            diagnostics="trace",
            trace={"status": "ok"},
        ),
    )

    response = authed_client.post(
        "/api/chat",
        json={"message": "Что такое Bluetooth?", "answer_mode": "Обычный"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "answer:Что такое Bluetooth?"
    assert response.json()["summary"] == "короткий итог"
    assert response.json()["confidence_label"] == "high"
    assert response.json()["diagnostics"] == "trace"


def test_export_summary_endpoint(authed_client, monkeypatch, tmp_path):
    # Use a disposable temp file, not __file__: the endpoint deletes the file
    # after streaming it (Stage 41 cleanup), so returning a real source file
    # here would delete it.
    fake_docx = tmp_path / "summary.docx"
    fake_docx.write_bytes(b"PK\x03\x04 fake docx")
    monkeypatch.setattr(
        api_app.services,
        "export_summary_docx_service",
        lambda request: str(fake_docx),
    )

    response = authed_client.post(
        "/api/exports/summary",
        json={"text": "hello", "summary_type": "Средний"},
    )

    assert response.status_code == 200
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in response.headers["content-type"]
    )
    # Cleanup ran: the file was removed after the response streamed.
    assert not fake_docx.exists()


def test_export_summary_filename_uses_basename_not_backslash_split(
    authed_client, monkeypatch, tmp_path
):
    """Ensure filename extraction works with forward-slash paths (Linux/macOS)."""
    fake_file = tmp_path / "my_summary.docx"
    fake_file.write_bytes(b"PK\x03\x04")
    forward_slash_path = str(fake_file).replace("\\", "/")

    monkeypatch.setattr(
        api_app.services,
        "export_summary_docx_service",
        lambda request: forward_slash_path,
    )

    response = authed_client.post(
        "/api/exports/summary",
        json={"text": "hello", "summary_type": "Средний"},
    )

    assert response.status_code == 200
    assert "my_summary.docx" in response.headers.get("content-disposition", "")


def test_export_summary_endpoint_returns_400_for_empty_summary(
    authed_client, monkeypatch
):
    monkeypatch.setattr(
        api_app.services,
        "export_summary_docx_service",
        lambda request: None,
    )

    response = authed_client.post(
        "/api/exports/summary",
        json={"text": ""},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "empty_summary"}


# ---------------------------------------------------------------------------
# Stage 3b: workspace_id sourced from the caller's session
# ---------------------------------------------------------------------------


def test_endpoints_pass_callers_personal_workspace_id(authed_client, monkeypatch):
    """Every service-layer call should receive the authenticated user's
    ``personal_workspace.id`` — never a value from the URL or request body.

    Spying on each service stub catches a future regression where someone
    accidentally hard-codes a legacy sentinel value (the
    ``DEFAULT_WORKSPACE_ID`` constant is gone in Stage 6e) or accepts a
    ``workspace_id`` from the request body.
    """
    expected = authed_client.get("/api/auth/me").json()["personal_workspace"]["id"]
    captured: dict[str, str] = {}

    def _capture(name):
        def stub(workspace_id, *args, **kwargs):
            captured[name] = workspace_id
            # start_upload_material_service has the (workspace_id, user_id, file_name, content)
            # signature; the others take (workspace_id, ...). Drop ``user_id``
            # before forwarding so the small stub factory below doesn't have
            # to special-case it.
            if name == "start_upload_material_service" and args:
                args = args[1:]
            return _stub_return_for(name, *args, **kwargs)
        return stub

    def _stub_return_for(name, *args, **kwargs):
        if name == "get_system_status":
            return SystemStatus(
                llm_mode="ollama",
                model="m",
                embedding_model="e",
                reranker_model="r",
                chunk_size=1,
                hyde_enabled=False,
                total_books=0,
                total_chunks=0,
            )
        if name == "list_materials":
            return MaterialsResponse(materials=[])
        if name == "list_sections":
            return SectionsResponse(sections=[])
        if name == "get_material_progress":
            return MaterialProgressResponse()
        if name in {
            "start_upload_material_service",
            "start_delete_material_service",
            "start_reindex_material_service",
        }:
            return MaterialActionResponse(ok=True, message="", material_name="")
        if name == "generate_summary_service":
            return SummaryResponse(text="", diagnostics="")
        if name == "chat_service":
            return ChatResponse(answer="")
        raise AssertionError(f"no stub for {name}")

    for service_name in [
        "get_system_status",
        "list_materials",
        "list_sections",
        "get_material_progress",
        "start_upload_material_service",
        "start_delete_material_service",
        "start_reindex_material_service",
        "generate_summary_service",
        "chat_service",
    ]:
        monkeypatch.setattr(api_app.services, service_name, _capture(service_name))

    authed_client.get("/api/system/status")
    authed_client.get("/api/materials")
    authed_client.get("/api/materials/a.pdf/sections")
    authed_client.get("/api/materials/progress")
    authed_client.post(
        "/api/materials/upload",
        files={"file": ("book.pdf", b"hello", "application/pdf")},
    )
    authed_client.delete("/api/materials/a.pdf")
    authed_client.post("/api/materials/reindex")
    authed_client.post(
        "/api/summaries",
        json={"topic": "Bluetooth", "summary_type": "Средний"},
    )
    authed_client.post(
        "/api/chat",
        json={"message": "hi", "answer_mode": "Обычный"},
    )

    for service_name, received_id in captured.items():
        assert received_id == expected, (
            f"{service_name} received workspace_id={received_id!r}, "
            f"expected {expected!r}"
        )


def test_material_progress_endpoint_does_not_leak_other_workspaces(api_client):
    """Stage 3b invariant: a user must never see another workspace's progress.

    Alice has an active upload; Bob logs in and queries the progress
    endpoint. The response must be the idle default — no filename, no
    percent, no error from Alice's job.
    """
    from src import app_services

    # 1. Register Alice and stash her workspace id.
    api_client.post(
        "/api/auth/register",
        json={
            "email": "alice-leak-test@example.com",
            "password": "alicepassword12",
            "display_name": "Alice",
        },
    )
    alice_workspace_id = (
        api_client.get("/api/auth/me").json()["personal_workspace"]["id"]
    )

    # 2. Pretend Alice has an active upload in progress.
    app_services._set_material_progress(
        alice_workspace_id,
        active=True,
        operation="upload",
        phase="indexing",
        message="Загружаю alice_secret.pdf",
        progress=42,
        current_file="alice_secret.pdf",
    )

    # 3. Drop Alice's cookie, register Bob, query progress as Bob.
    api_client.cookies.clear()
    api_client.post(
        "/api/auth/register",
        json={
            "email": "bob-leak-test@example.com",
            "password": "bobpassword12",
            "display_name": "Bob",
        },
    )

    response = api_client.get("/api/materials/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["current_file"] == ""
    assert body["message"] == ""
    assert body["progress"] == 0
    assert body["error"] == ""
    # And Alice's own snapshot is still intact server-side.
    alice_state = app_services._get_workspace_progress_snapshot(alice_workspace_id)
    assert alice_state["current_file"] == "alice_secret.pdf"

    app_services.reset_material_progress_for_tests()


# ---------------------------------------------------------------------------
# Stage 3c: Document table is the source of truth for materials
# ---------------------------------------------------------------------------


def test_delete_does_not_touch_another_users_document(api_client, monkeypatch, tmp_path):
    """Two users register, each uploads ``shared.pdf``; Bob deleting his copy
    must leave Alice's Document/file/chunks intact (and vice versa).

    The KB call surface is mocked with FakeKB to keep the test fast — what
    matters here is the SQL + filesystem isolation, not the actual indexer.
    """
    from src import app_services, document_service
    from src.db import SessionLocal
    from tests.test_app_services import FakeKB

    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: FakeKB())

    def register_and_upload(email: str, body: bytes) -> tuple[str, str]:
        api_client.cookies.clear()
        api_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": "passwordpassword12",
                "display_name": email.split("@", 1)[0],
            },
        )
        workspace_id = api_client.get("/api/auth/me").json()["personal_workspace"]["id"]
        upload_response = api_client.post(
            "/api/materials/upload",
            files={"file": ("shared.pdf", body, "application/pdf")},
        )
        assert upload_response.status_code == 200
        # /api/materials/upload returns as soon as the background indexing
        # thread is queued; join on it so the Document row is guaranteed
        # committed before the test asserts on it.
        if app_services._material_job_thread is not None:
            app_services._material_job_thread.join(timeout=10)
        return email, workspace_id

    alice_email, alice_workspace = register_and_upload("alice-doc@example.com", b"alice body")
    bob_email, bob_workspace = register_and_upload("bob-doc@example.com", b"bob body")

    db = SessionLocal()
    try:
        alice_docs = document_service.list_documents(db, alice_workspace)
        bob_docs = document_service.list_documents(db, bob_workspace)
    finally:
        db.close()
    assert len(alice_docs) == 1
    assert len(bob_docs) == 1
    alice_doc = alice_docs[0]
    bob_doc = bob_docs[0]

    # Bob (currently logged in) deletes "shared.pdf" — only Bob's row goes.
    delete_response = api_client.delete("/api/materials/shared.pdf")
    assert delete_response.status_code == 200
    if app_services._material_job_thread is not None:
        app_services._material_job_thread.join(timeout=10)

    db = SessionLocal()
    try:
        alice_after = document_service.list_documents(db, alice_workspace)
        bob_after = document_service.list_documents(db, bob_workspace)
    finally:
        db.close()
    assert [d.id for d in alice_after] == [alice_doc.id]
    assert bob_after == []

    # Files on disk: Alice's stays, Bob's is gone.
    import os
    assert os.path.exists(alice_doc.stored_path)
    assert not os.path.exists(bob_doc.stored_path)


def test_small_upload_remains_visible_in_materials_endpoint(
    api_client, monkeypatch, tmp_path
):
    """Smoke-bug regression: a tiny upload that produces zero/one chunk and
    no sections must still appear in ``/api/materials``. Replays the exact
    sequence the user hit on the manual smoke test (register → upload
    alice_doc.txt → GET /api/materials) with a KB whose profile lookup
    returns zero counts (the same shape as the real KB returned for the
    smoke-bug)."""
    from src import app_services
    from tests.test_app_services import FakeKB

    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))

    class BlindKB(FakeKB):
        def get_file_profile(self, file_name, workspace_id=None):
            return {"chunk_count": 0, "sections_count": 0, "sections": []}

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: BlindKB())

    api_client.post(
        "/api/auth/register",
        json={
            "email": "smoke-bug@example.com",
            "password": "smokepassword12",
            "display_name": "Smoke",
        },
    )
    upload_response = api_client.post(
        "/api/materials/upload",
        files={"file": ("alice_doc.txt", b"Tiny smoke body", "text/plain")},
    )
    assert upload_response.status_code == 200
    if app_services._material_job_thread is not None:
        app_services._material_job_thread.join(timeout=10)

    response = api_client.get("/api/materials")
    assert response.status_code == 200
    materials = response.json()["materials"]
    assert len(materials) == 1, materials
    entry = materials[0]
    assert entry["name"] == "alice_doc.txt"
    assert entry["status"] == "ready"
    assert isinstance(entry["id"], str) and len(entry["id"]) == 36
    assert entry["quality_label"] != "hidden"


def test_materials_response_includes_document_id(api_client, monkeypatch, tmp_path):
    """The /api/materials list must surface ``id`` so the frontend can move
    from name-based addressing to document_id-based addressing."""
    from src import app_services
    from tests.test_app_services import FakeKB

    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: FakeKB())

    api_client.post(
        "/api/auth/register",
        json={
            "email": "mat-id@example.com",
            "password": "passwordpassword12",
            "display_name": "Mat",
        },
    )
    api_client.post(
        "/api/materials/upload",
        files={"file": ("book.pdf", b"hello", "application/pdf")},
    )
    if app_services._material_job_thread is not None:
        app_services._material_job_thread.join(timeout=10)

    response = api_client.get("/api/materials")
    materials = response.json()["materials"]
    assert len(materials) == 1
    entry = materials[0]
    assert entry["name"] == "book.pdf"
    # Document.id is a UUID4 string of length 36.
    assert isinstance(entry["id"], str) and len(entry["id"]) == 36
    assert entry["status"] == "ready"
