"""End-to-end isolation tests for two authenticated users (Stage 3d).

Each test registers Alice and Bob through the real ``/api/auth/register``
endpoint, drives further requests through the real FastAPI app, and asserts
that one user's materials / chat sources / progress are never visible to the
other. ``KnowledgeBase`` is replaced with a workspace-aware fake so the
tests run in the same time budget as the rest of the suite while still
exercising the full ``api_app → app_services → document_service`` chain.
"""

import os

from src import app_services, document_service
from src.db import SessionLocal


# Topic used by the summary-isolation tests below. Deliberately a single
# foreign word so summary_engine's history/topic heuristics route it through
# generate_direct_topic_summary (the simplest path: one search call, one LLM
# call) and so _filter_chunks_by_topic_anchors does not drop our synthetic
# chunks.
_SUMMARY_TOPIC = "Bluetooth"


# ---------------------------------------------------------------------------
# Workspace-aware FakeKB: per-call captures + per-workspace synthetic sources
# ---------------------------------------------------------------------------


class WorkspaceAwareFakeKB:
    """KB stub whose every method tags its output with ``workspace_id``.

    Lets the e2e tests assert that chat / search / list calls are scoped
    end-to-end: if Alice's request ever reached Bob's data, Bob's marker
    string would surface in Alice's response and the test would fail.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def _log(self, name, **payload):
        self.calls.append((name, payload))

    # --- read paths ---
    def stats(self, workspace_id=None):
        self._log("stats", workspace_id=workspace_id)
        return {
            "total_books": 0,
            "total_chunks": 0,
            "books": [],
            "sections": [],
        }

    def get_available_sections(self, workspace_id=None):
        self._log("get_available_sections", workspace_id=workspace_id)
        return [f"section-of-{workspace_id}"]

    def get_sections_for_file(self, file_name, workspace_id=None):
        self._log("get_sections_for_file", file_name=file_name, workspace_id=workspace_id)
        return [f"section-of-{workspace_id}"]

    def get_file_profile(self, file_name, workspace_id=None):
        self._log("get_file_profile", file_name=file_name, workspace_id=workspace_id)
        return {"chunk_count": 4, "sections_count": 2, "sections": ["A", "B"]}

    def get_available_files(self, workspace_id=None):
        self._log("get_available_files", workspace_id=workspace_id)
        return [f"{workspace_id}-marker.pdf"]

    def find_section_in_query(self, message, workspace_id=None):
        self._log("find_section_in_query", workspace_id=workspace_id)
        return None

    def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
        self._log("search_with_sources", workspace_id=workspace_id)
        # Source filename embeds ``workspace_id`` so the assertion below is
        # both "this is non-empty" and "this isn't the other user's data".
        return (
            f"контекст для {workspace_id}",
            [
                {
                    "source_file": f"{workspace_id}-doc.pdf",
                    "section": f"section-of-{workspace_id}",
                    "score": 0.95,
                }
            ],
        )

    # --- write paths ---
    def add_book(self, file_path, workspace_id=None, document_id=None, original_name=None, progress_callback=None, cancel_check=None):
        self._log(
            "add_book",
            file_path=file_path,
            workspace_id=workspace_id,
            document_id=document_id,
            original_name=original_name,
        )
        return f"✅ {file_path}: добавлено 3 фрагментов"

    def remove_chunks(self, workspace_id, document_id):
        self._log("remove_chunks", workspace_id=workspace_id, document_id=document_id)
        return f"🗑️ document_id={document_id}: удалено 1 фрагментов"

    def remove_book(self, file_name, workspace_id=None):
        self._log("remove_book", file_name=file_name, workspace_id=workspace_id)
        return f"🗑️ {file_name}: удалено"

    def clear(self, workspace_id=None):
        self._log("clear", workspace_id=workspace_id)
        return "✅ База очищена"


class _FakeLLM:
    def call(self, prompt, max_tokens=None, temperature=None):
        # Deterministic, workspace-agnostic so a chat assertion can compare
        # only the *sources* returned, not the LLM output.
        return "Готовый ответ"


# ---------------------------------------------------------------------------
# Helpers — register a user, upload, wait for the background job to settle
# ---------------------------------------------------------------------------


def _register(client, email: str, display_name: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "iso-password-12",
            "display_name": display_name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _whoami(client) -> dict:
    body = client.get("/api/auth/me").json()
    return body


def _upload(client, original_name: str, body: bytes) -> None:
    response = client.post(
        "/api/materials/upload",
        files={"file": (original_name, body, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    _wait_for_material_job()


def _delete(client, original_name: str) -> None:
    response = client.delete(f"/api/materials/{original_name}")
    assert response.status_code == 200, response.text
    _wait_for_material_job()


def _reindex(client, original_name: str) -> None:
    response = client.post(f"/api/materials/{original_name}/reindex")
    assert response.status_code == 200, response.text
    _wait_for_material_job()


def _wait_for_material_job() -> None:
    """Block until the most recently launched material job thread finishes.

    ``app_services.start_*`` queue a background ``Thread`` and return
    immediately; the e2e tests assert on the post-state (Document rows,
    files on disk) so we must wait for that thread before reading.
    """
    if app_services._material_job_thread is not None:
        app_services._material_job_thread.join(timeout=15)


def _list_materials(client) -> list[dict]:
    return client.get("/api/materials").json()["materials"]


# ---------------------------------------------------------------------------
# Materials list, upload, delete, reindex — two-user isolation
# ---------------------------------------------------------------------------


def test_each_user_sees_only_their_own_materials(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())

    # Alice
    _register(api_client, "alice@example.com", "Alice")
    _upload(api_client, "alpha.pdf", b"alice body")
    alice_materials = _list_materials(api_client)
    assert {m["name"] for m in alice_materials} == {"alpha.pdf"}

    # Bob — same logical file name, distinct workspace
    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    _upload(api_client, "beta.pdf", b"bob body")
    bob_materials = _list_materials(api_client)
    assert {m["name"] for m in bob_materials} == {"beta.pdf"}

    # Cross-check: Bob's view never includes Alice's document.
    bob_names = {m["name"] for m in bob_materials}
    bob_ids = {m["id"] for m in bob_materials}
    alice_ids = {m["id"] for m in alice_materials}
    assert "alpha.pdf" not in bob_names
    assert alice_ids.isdisjoint(bob_ids)


def test_same_filename_is_independent_per_user(api_client, monkeypatch, tmp_path):
    """Both users may have a ``shared.pdf`` — each backed by its own
    Document row, its own stored path, and its own ``document_id``."""
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"alice contents")

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"bob contents")

    db = SessionLocal()
    try:
        alice_docs = document_service.list_documents(db, alice_workspace)
        bob_docs = document_service.list_documents(db, bob_workspace)
    finally:
        db.close()
    assert len(alice_docs) == 1
    assert len(bob_docs) == 1
    assert alice_docs[0].id != bob_docs[0].id
    assert alice_docs[0].stored_path != bob_docs[0].stored_path
    assert os.path.exists(alice_docs[0].stored_path)
    assert os.path.exists(bob_docs[0].stored_path)
    with open(alice_docs[0].stored_path, "rb") as f:
        assert f.read() == b"alice contents"
    with open(bob_docs[0].stored_path, "rb") as f:
        assert f.read() == b"bob contents"


def test_bob_delete_does_not_touch_alice(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"alice contents")

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"bob contents")

    # Snapshot Alice's state.
    db = SessionLocal()
    try:
        alice_doc_before = document_service.list_documents(db, alice_workspace)[0]
    finally:
        db.close()
    assert os.path.exists(alice_doc_before.stored_path)

    # Bob (currently logged in) deletes — Alice's row + file stay.
    _delete(api_client, "shared.pdf")

    db = SessionLocal()
    try:
        alice_after = document_service.list_documents(db, alice_workspace)
        bob_after = document_service.list_documents(db, bob_workspace)
    finally:
        db.close()
    assert [d.id for d in alice_after] == [alice_doc_before.id]
    assert bob_after == []
    assert os.path.exists(alice_doc_before.stored_path)


def test_alice_delete_does_not_touch_bob(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())

    # Bob first.
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"bob contents")

    api_client.cookies.clear()
    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"alice contents")

    db = SessionLocal()
    try:
        bob_doc_before = document_service.list_documents(db, bob_workspace)[0]
    finally:
        db.close()
    assert os.path.exists(bob_doc_before.stored_path)

    # Alice deletes — Bob untouched.
    _delete(api_client, "shared.pdf")

    db = SessionLocal()
    try:
        alice_after = document_service.list_documents(db, alice_workspace)
        bob_after = document_service.list_documents(db, bob_workspace)
    finally:
        db.close()
    assert alice_after == []
    assert [d.id for d in bob_after] == [bob_doc_before.id]
    assert os.path.exists(bob_doc_before.stored_path)


def test_bob_reindex_does_not_touch_alice(api_client, monkeypatch, tmp_path):
    """Bob reindexing his own ``shared.pdf`` must not even *look at* Alice's
    document — and the kb.remove_chunks call must target Bob's document_id."""
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake_kb = WorkspaceAwareFakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake_kb)

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"alice contents")

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    _upload(api_client, "shared.pdf", b"bob contents")

    db = SessionLocal()
    try:
        alice_doc = document_service.list_documents(db, alice_workspace)[0]
        bob_doc = document_service.list_documents(db, bob_workspace)[0]
    finally:
        db.close()

    # Erase the call log before the reindex so the assertion below only sees
    # what the reindex itself did.
    fake_kb.calls.clear()
    _reindex(api_client, "shared.pdf")

    # Every Stage 3c-relevant KB call during the reindex must have been
    # scoped to Bob's workspace + Bob's document_id.
    scoped_calls = [
        (name, payload)
        for name, payload in fake_kb.calls
        if name in {"remove_chunks", "add_book"}
    ]
    assert scoped_calls, "expected remove_chunks + add_book during reindex"
    for name, payload in scoped_calls:
        assert payload["workspace_id"] == bob_workspace, (name, payload)
        if "document_id" in payload:
            assert payload["document_id"] == bob_doc.id, (name, payload)
        assert payload.get("workspace_id") != alice_workspace, (name, payload)
        assert payload.get("document_id") != alice_doc.id, (name, payload)

    # And Alice's stored state is still exactly as before the reindex.
    db = SessionLocal()
    try:
        alice_after = document_service.list_documents(db, alice_workspace)
    finally:
        db.close()
    assert [(d.id, d.stored_path) for d in alice_after] == [
        (alice_doc.id, alice_doc.stored_path)
    ]
    assert os.path.exists(alice_doc.stored_path)


# ---------------------------------------------------------------------------
# Chat / search isolation
# ---------------------------------------------------------------------------


def test_chat_sources_never_cross_workspaces(api_client, monkeypatch, tmp_path):
    """Alice's ``/api/chat`` response must only contain sources whose
    ``source_file`` came from Alice's workspace — and vice versa."""
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _FakeLLM())

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    alice_response = api_client.post(
        "/api/chat",
        json={"message": "Что в материалах?", "answer_mode": "Обычный"},
    )
    assert alice_response.status_code == 200
    alice_sources = alice_response.json()["sources"]

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    bob_response = api_client.post(
        "/api/chat",
        json={"message": "Что в материалах?", "answer_mode": "Обычный"},
    )
    assert bob_response.status_code == 200
    bob_sources = bob_response.json()["sources"]

    # Per WorkspaceAwareFakeKB, each source carries its workspace_id in
    # source_file — Alice's response must contain only Alice's marker, Bob's
    # response must contain only Bob's, and the two sets never overlap.
    alice_files = {src["source_file"] for src in alice_sources}
    bob_files = {src["source_file"] for src in bob_sources}
    assert alice_files == {f"{alice_workspace}-doc.pdf"}
    assert bob_files == {f"{bob_workspace}-doc.pdf"}
    assert alice_files.isdisjoint(bob_files)


def test_sections_endpoint_scopes_to_caller_workspace(api_client, monkeypatch, tmp_path):
    """``/api/materials/{name}/sections`` must call the KB with the caller's
    workspace — surfacing a different workspace's sections would leak both
    structure and presence of foreign documents."""
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    alice_sections = api_client.get("/api/materials/any.pdf/sections").json()["sections"]

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    bob_sections = api_client.get("/api/materials/any.pdf/sections").json()["sections"]

    assert alice_sections == [f"section-of-{alice_workspace}"]
    assert bob_sections == [f"section-of-{bob_workspace}"]


# ---------------------------------------------------------------------------
# Progress isolation
# ---------------------------------------------------------------------------


def test_alice_inflight_upload_is_invisible_to_bob(api_client, monkeypatch, tmp_path):
    """If Alice has an active upload in progress (filename + percent),
    Bob's ``/api/materials/progress`` must show idle defaults — no
    ``current_file``, ``message``, ``progress`` or ``error`` leak."""
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: WorkspaceAwareFakeKB())

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]

    # Prime Alice's progress state with a recognisable filename so a leak
    # would be obvious in Bob's response body.
    app_services._set_material_progress(
        alice_workspace,
        active=True,
        operation="upload",
        phase="indexing",
        message="Индексирую alice_secret_report.pdf",
        progress=73,
        current_file="alice_secret_report.pdf",
    )

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_progress = api_client.get("/api/materials/progress").json()

    assert bob_progress["active"] is False
    assert bob_progress["operation"] == "idle"
    assert bob_progress["progress"] == 0
    assert bob_progress["current_file"] == ""
    assert bob_progress["message"] == ""
    assert bob_progress["error"] == ""
    # Belt-and-braces: no substring of Alice's filename anywhere in Bob's
    # progress response.
    serialized = " ".join(str(v) for v in bob_progress.values())
    assert "alice_secret_report" not in serialized.lower()

    # Alice's snapshot is still intact server-side.
    alice_snapshot = app_services._get_workspace_progress_snapshot(alice_workspace)
    assert alice_snapshot["current_file"] == "alice_secret_report.pdf"

    app_services.reset_material_progress_for_tests()


# ---------------------------------------------------------------------------
# Auth / admin matrix (the headline assertion is concentrated here too so a
# single test file confirms the Stage 3 access model end-to-end)
# ---------------------------------------------------------------------------


def test_anonymous_callers_are_blocked_from_protected_endpoints(api_client):
    api_client.cookies.clear()
    for method, path in [
        ("GET", "/api/materials"),
        ("GET", "/api/materials/progress"),
        ("GET", "/api/system/status"),
        ("POST", "/api/chat"),
        ("POST", "/api/summaries"),
    ]:
        if method == "GET":
            response = api_client.get(path)
        else:
            response = api_client.post(path, json={})
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"


def test_regular_user_blocked_from_diagnostics(api_client):
    _register(api_client, "regular@example.com", "Regular")
    response = api_client.get("/api/diagnostics/latest")
    assert response.status_code == 403
    assert response.json()["detail"] == "superuser_required"


# ---------------------------------------------------------------------------
# Summary isolation (Stage 4) — two users, same topic, distinct summaries
# ---------------------------------------------------------------------------


class _SummaryFakeKB:
    """KB stub whose ``search_chunks_for_summary`` returns chunks tagged with
    ``workspace_id``.

    Every chunk carries a workspace-specific marker string in its ``text``.
    Combined with ``_EchoLLM`` below, this lets the e2e test assert that
    Alice's summary contains only Alice's marker — proving that
    ``workspace_id`` was honoured all the way down to the KB layer.
    """

    def __init__(self):
        self.search_calls: list[dict] = []

    def search_chunks_for_summary(
        self,
        query,
        file_filter="all",
        section_filter=None,
        top_k=None,
        workspace_id=None,
    ):
        self.search_calls.append(
            {
                "query": query,
                "file_filter": file_filter,
                "section_filter": section_filter,
                "top_k": top_k,
                "workspace_id": workspace_id,
            }
        )
        # 5+ chunks in one dense, non-noisy section so the summary-engine
        # filters (_focus_chunks_on_primary_section, dense window) keep them.
        return [
            {
                "text": (
                    f"Маркер рабочей области: workspace-marker-{workspace_id}. "
                    f"Bluetooth используется в workspace {workspace_id}."
                ),
                "source_file": "vedomo.pdf",
                "section": "Раздел Bluetooth",
                "chunk_id": chunk_id,
                "score": 1.0,
            }
            for chunk_id in range(1, 6)
        ]

    # Minimal stubs so chat/materials code paths that may briefly touch the
    # KB while the test is running do not blow up. The summary tests never
    # exercise these, but ``runtime.get_kb`` returns the same instance for
    # every caller in the process.
    def stats(self, workspace_id=None):
        return {"total_books": 0, "total_chunks": 0, "books": [], "sections": []}

    def get_available_files(self, workspace_id=None):
        return []

    def get_file_profile(self, file_name, workspace_id=None):
        return {"chunk_count": 0, "sections_count": 0, "sections": []}


class _EchoLLM:
    """LLM stub that echoes the prompt back as its answer.

    The summary prompt embeds the chunks' text verbatim, and chunks carry
    the per-workspace marker, so the marker is guaranteed to appear in the
    response text. Deterministic and Ollama-free.
    """

    def __init__(self):
        self.prompts: list[str] = []

    def call(self, prompt, max_tokens=None, temperature=None):
        self.prompts.append(prompt)
        return f"[ECHO]\n{prompt}"


def _summary_request_body():
    return {
        "selected_file": "Все файлы",
        "selected_section": "Все разделы",
        "topic": _SUMMARY_TOPIC,
        "summary_type": "Средний",
    }


def test_summary_is_scoped_to_caller_workspace(api_client, monkeypatch, tmp_path):
    """Alice and Bob POST ``/api/summaries`` with the same topic; the
    response Alice sees must only quote Alice's chunks (workspace-marker)
    and never Bob's, and vice versa.

    Regression guard for the full Stage 4 chain (post-Stage-6d):
    ``api_app → app_services.generate_summary_service →
     summary_engine.generate_direct_topic_summary → kb.search_chunks_for_summary``.
    A bug anywhere in that chain that dropped ``workspace_id`` would cause
    the wrong marker to surface in the response.
    """
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake_kb = _SummaryFakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake_kb)
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _EchoLLM())

    # --- Alice ---
    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    alice_resp = api_client.post("/api/summaries", json=_summary_request_body())
    assert alice_resp.status_code == 200, alice_resp.text
    alice_text = alice_resp.json()["text"]

    # --- Bob ---
    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    bob_resp = api_client.post("/api/summaries", json=_summary_request_body())
    assert bob_resp.status_code == 200, bob_resp.text
    bob_text = bob_resp.json()["text"]

    # Different workspaces → different markers → no overlap.
    assert alice_workspace != bob_workspace

    alice_marker = f"workspace-marker-{alice_workspace}"
    bob_marker = f"workspace-marker-{bob_workspace}"

    assert alice_marker in alice_text
    assert bob_marker not in alice_text

    assert bob_marker in bob_text
    assert alice_marker not in bob_text


def test_summary_search_call_carries_caller_workspace_id(api_client, monkeypatch, tmp_path):
    """Belt-and-braces: assert at the KB boundary that
    ``search_chunks_for_summary`` was invoked with the caller's workspace_id
    (and never with the legacy "dev-default" sentinel — the
    DEFAULT_WORKSPACE_ID constant itself is gone in Stage 6e).
    """
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake_kb = _SummaryFakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake_kb)
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _EchoLLM())

    _register(api_client, "alice@example.com", "Alice")
    alice_workspace = _whoami(api_client)["personal_workspace"]["id"]
    assert api_client.post("/api/summaries", json=_summary_request_body()).status_code == 200

    api_client.cookies.clear()
    _register(api_client, "bob@example.com", "Bob")
    bob_workspace = _whoami(api_client)["personal_workspace"]["id"]
    assert api_client.post("/api/summaries", json=_summary_request_body()).status_code == 200

    workspace_ids_seen = [call["workspace_id"] for call in fake_kb.search_calls]
    # generate_direct_topic_summary does exactly one search per call.
    assert workspace_ids_seen == [alice_workspace, bob_workspace]
    # Old legacy sentinel from the early dev era - regression guard.
    assert "dev-default" not in workspace_ids_seen


def test_superuser_reaches_diagnostics(api_client, monkeypatch):
    """Promotes a freshly-registered user to ``is_superuser=True`` via a
    direct DB session (intentional — there is no public promote endpoint)."""
    from src.db_models import User

    monkeypatch.setattr(
        app_services, "get_latest_diagnostics_text", lambda: "trace snapshot"
    )

    _register(api_client, "admin@example.com", "Admin")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "admin@example.com").one()
        user.is_superuser = True
        db.commit()
    finally:
        db.close()

    response = api_client.get("/api/diagnostics/latest")
    assert response.status_code == 200
    assert response.json() == {"text": "trace snapshot"}
