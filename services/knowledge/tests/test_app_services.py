"""Service-layer tests.

Stage 3c rewires upload/list/delete/reindex through ``document_service`` so
this file now requests the ``db_session`` fixture (from ``conftest.py``)
wherever it touches those flows: the fixture creates the auth/document
schema in the shared SQLite temp DB before each test and drops it after,
which is the same DB ``app_services.SessionLocal()`` opens under the hood.

Tests that only exercise pure helpers (``chat_service``'s greeting branch,
``export_summary_docx_service``) still skip ``db_session``.
"""

import config
from src import app_services, document_service
from src.api_models import ChatRequest, SummaryExportRequest, SummaryRequest


WORKSPACE_ID = "test-workspace"
USER_ID = "test-user"


def setup_function():
    app_services.reset_material_progress_for_tests()


class FakeKB:
    """Test double for KnowledgeBase covering Stage 3c-relevant methods."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        # Per-(workspace_id, document_id) set of indexed file paths so tests
        # can assert that ``reindex_document`` actually wiped and rebuilt.
        self.indexed: set[tuple[str, str]] = set()

    def _log(self, name, **payload):
        self.calls.append((name, payload))

    def stats(self, workspace_id=None):
        self._log("stats", workspace_id=workspace_id)
        return {
            "total_books": 2,
            "total_chunks": 10,
            "books": ["a.pdf", "b.pdf"],
            "sections": ["Глава 1", "Глава 2"],
        }

    def get_available_sections(self, workspace_id=None):
        self._log("get_available_sections", workspace_id=workspace_id)
        return ["Глава 1", "Глава 2"]

    def get_sections_for_file(self, file_name, workspace_id=None):
        self._log("get_sections_for_file", file_name=file_name, workspace_id=workspace_id)
        mapping = {
            "a.pdf": ["Глава 1", "Глава 2"],
            "b.pdf": ["Введение"],
        }
        return mapping.get(file_name, [])

    def get_file_profile(self, file_name, workspace_id=None):
        self._log("get_file_profile", file_name=file_name, workspace_id=workspace_id)
        mapping = {
            "a.pdf": {"chunk_count": 6, "sections_count": 2, "sections": ["Глава 1", "Глава 2"]},
            "b.pdf": {"chunk_count": 3, "sections_count": 1, "sections": ["Введение"]},
            "book.pdf": {"chunk_count": 4, "sections_count": 2, "sections": ["A", "B"]},
        }
        return mapping.get(file_name, {"chunk_count": 0, "sections_count": 0, "sections": []})

    def remove_book(self, file_name, workspace_id=None):
        self._log("remove_book", file_name=file_name, workspace_id=workspace_id)
        return f"🗑️ {file_name}: удалено 3 фрагментов"

    def remove_chunks(self, workspace_id, document_id):
        self._log("remove_chunks", workspace_id=workspace_id, document_id=document_id)
        self.indexed.discard((workspace_id, document_id))
        return f"🗑️ document_id={document_id}: удалено 1 фрагментов"

    def add_book(self, file_path, workspace_id=None, document_id=None, original_name=None, progress_callback=None, cancel_check=None):
        self._log(
            "add_book",
            file_path=file_path,
            workspace_id=workspace_id,
            document_id=document_id,
            original_name=original_name,
        )
        self.indexed.add((workspace_id, document_id))
        return f"✅ {file_path}: добавлено 3 фрагментов"

    def clear(self, workspace_id=None):
        self._log("clear", workspace_id=workspace_id)
        return "✅ База очищена"

    def index_all_books(self, workspace_id=None, progress_callback=None):
        self._log("index_all_books", workspace_id=workspace_id)
        return "📚 Найдено файлов: 2"


# ---------------------------------------------------------------------------
# get_system_status / chat helpers — no DB needed
# ---------------------------------------------------------------------------


def test_get_system_status_uses_config_and_kb_stats(monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    status = app_services.get_system_status(WORKSPACE_ID)

    assert status.llm_mode == config.LLM_MODE
    assert status.embedding_model == config.EMBEDDING_MODEL
    assert status.total_books == 2
    assert status.total_chunks == 10
    assert any(name == "stats" and payload["workspace_id"] == WORKSPACE_ID
               for name, payload in fake.calls)


# ---------------------------------------------------------------------------
# list_materials reads from the Document table
# ---------------------------------------------------------------------------


def _insert_document(db, *, name="book.pdf", status="ready", workspace_id=WORKSPACE_ID, **kwargs):
    from src.db_models import Document

    doc = Document(
        workspace_id=workspace_id,
        owner_user_id=kwargs.pop("owner_user_id", USER_ID),
        original_name=name,
        stored_path=kwargs.pop("stored_path", f"/tmp/{name}"),
        size_bytes=kwargs.pop("size_bytes", 0),
        content_hash=kwargs.pop("content_hash", ""),
        status=status,
        sections_count=kwargs.pop("sections_count", 0),
        error_message=kwargs.pop("error_message", ""),
        **kwargs,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_list_materials_returns_documents_with_ids(db_session, monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    doc_a = _insert_document(db_session, name="a.pdf")
    doc_b = _insert_document(db_session, name="b.pdf")

    response = app_services.list_materials(WORKSPACE_ID)

    by_name = {m.name: m for m in response.materials}
    assert by_name["a.pdf"].id == doc_a.id
    assert by_name["b.pdf"].id == doc_b.id
    assert by_name["a.pdf"].quality_label == "ready"
    assert by_name["a.pdf"].sections_count == 2
    assert by_name["b.pdf"].sections_count == 1


def test_list_materials_hides_other_workspaces(db_session, monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    # FakeKB.get_file_profile only knows ``a.pdf``/``b.pdf``/``book.pdf`` —
    # unrecognised names get chunk_count=0 and ``list_materials`` filters them
    # out as ``quality_label="hidden"``. Use the known names so the test is
    # specifically about workspace scoping, not about FakeKB defaults.
    _insert_document(db_session, name="a.pdf", workspace_id=WORKSPACE_ID)
    _insert_document(db_session, name="b.pdf", workspace_id="another-workspace")

    response = app_services.list_materials(WORKSPACE_ID)

    names = {m.name for m in response.materials}
    assert "a.pdf" in names
    assert "b.pdf" not in names


def test_list_materials_never_hides_ready_document_with_zero_chunks(
    db_session, monkeypatch
):
    """Smoke-bug regression (Stage 3d fix).

    Before the fix, a tiny ``alice_doc.txt`` (1 chunk, 0 sections, status=ready)
    disappeared from ``/api/materials`` because ``kb.get_file_profile`` lookup
    missed and the old ``_material_quality`` returned ``"hidden"`` for any
    ``chunk_count <= 0``. ``Document.status`` is now the source of truth for
    visibility — a ready Document must appear regardless of KB profile.
    """

    class BlindKB(FakeKB):
        """Simulates the smoke-bug scenario: KB profile returns zeros."""

        def get_file_profile(self, file_name, workspace_id=None):
            self._log("get_file_profile", file_name=file_name, workspace_id=workspace_id)
            return {"chunk_count": 0, "sections_count": 0, "sections": []}

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: BlindKB())

    doc = _insert_document(db_session, name="alice_doc.txt", status="ready")

    response = app_services.list_materials(WORKSPACE_ID)
    by_name = {m.name: m for m in response.materials}

    assert "alice_doc.txt" in by_name, (
        "ready Document must surface even when KB returns chunk_count=0"
    )
    entry = by_name["alice_doc.txt"]
    assert entry.id == doc.id
    assert entry.status == "ready"
    assert entry.quality_label != "hidden"
    # The fallback for zero-chunk ready documents is the new "weak" badge.
    assert entry.quality_label == "weak"


def test_list_materials_survives_kb_profile_lookup_exception(
    db_session, monkeypatch
):
    """Defence-in-depth: a KB error must NOT take the document off the list."""

    class CrashingKB(FakeKB):
        def get_file_profile(self, file_name, workspace_id=None):
            raise RuntimeError("KB temporarily unavailable")

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: CrashingKB())

    _insert_document(db_session, name="alice_doc.txt", status="ready")

    response = app_services.list_materials(WORKSPACE_ID)
    names = {m.name for m in response.materials}
    assert "alice_doc.txt" in names


def test_list_materials_surfaces_processing_and_error_statuses(db_session, monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    _insert_document(db_session, name="loading.pdf", status="processing")
    _insert_document(
        db_session,
        name="broken.pdf",
        status="error",
        error_message="Не удалось извлечь текст",
    )
    _insert_document(db_session, name="a.pdf")

    response = app_services.list_materials(WORKSPACE_ID)
    by_name = {m.name: m for m in response.materials}

    assert by_name["loading.pdf"].quality_label == "processing"
    assert by_name["loading.pdf"].status == "processing"
    assert by_name["broken.pdf"].quality_label == "error"
    assert "извлечь" in by_name["broken.pdf"].quality_reason


# ---------------------------------------------------------------------------
# list_sections still queries the KB by source_file
# ---------------------------------------------------------------------------


def test_list_sections_returns_all_sections(monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    response = app_services.list_sections(WORKSPACE_ID, file_filter="all")

    assert response.sections == ["Глава 1", "Глава 2"]


def test_list_sections_returns_sections_for_specific_file(monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    response = app_services.list_sections(WORKSPACE_ID, file_filter="a.pdf")

    assert response.sections == ["Глава 1", "Глава 2"]


def test_list_sections_normalizes_all_materials_label(monkeypatch):
    file_filters = []

    class TrackingKB(FakeKB):
        def get_available_sections(self, file_filter="all", workspace_id=None):
            self._log("get_available_sections", workspace_id=workspace_id)
            file_filters.append(file_filter)
            return ["Глава 1"]

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: TrackingKB())

    response = app_services.list_sections(WORKSPACE_ID, file_filter="Все материалы")

    assert response.sections == ["Глава 1"]
    assert file_filters == ["all"]


# ---------------------------------------------------------------------------
# upload — validation rejects, happy path creates a Document
# ---------------------------------------------------------------------------


def test_upload_rejects_file_exceeding_size_limit(monkeypatch):
    monkeypatch.setattr(app_services.config, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: FakeKB())

    response = app_services.upload_material_service(
        WORKSPACE_ID, USER_ID, "book.pdf", b"x" * 11
    )

    assert response.ok is False
    assert "слишком большой" in response.message.lower()


def test_upload_rejects_unsupported_format(monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: FakeKB())

    response = app_services.upload_material_service(
        WORKSPACE_ID, USER_ID, "virus.exe", b"payload"
    )

    assert response.ok is False
    assert "формат" in response.message.lower()


def test_upload_material_service_creates_document_and_writes_path(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    response = app_services.upload_material_service(
        WORKSPACE_ID, USER_ID, "book.pdf", b"hello"
    )

    assert response.ok is True

    # Document row was created.
    docs = document_service.list_documents(db_session, WORKSPACE_ID)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.original_name == "book.pdf"
    assert doc.workspace_id == WORKSPACE_ID
    assert doc.owner_user_id == USER_ID
    assert doc.status == document_service.STATUS_READY
    assert doc.size_bytes == len(b"hello")

    # File on disk contains the document_id in its path.
    workspace_dir = tmp_path / "docs" / WORKSPACE_ID
    files = list(workspace_dir.iterdir())
    assert len(files) == 1
    stored = files[0]
    assert doc.id in stored.name
    assert stored.name.endswith("__book.pdf")
    assert stored.read_bytes() == b"hello"

    # KB was invoked with the explicit document_id.
    add_calls = [payload for name, payload in fake.calls if name == "add_book"]
    assert len(add_calls) == 1
    assert add_calls[0]["workspace_id"] == WORKSPACE_ID
    assert add_calls[0]["document_id"] == doc.id


def test_upload_replaces_existing_document_with_same_name(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    first = app_services.upload_material_service(WORKSPACE_ID, USER_ID, "book.pdf", b"old")
    assert first.ok is True
    first_doc = document_service.list_documents(db_session, WORKSPACE_ID)[0]
    first_id = first_doc.id

    second = app_services.upload_material_service(WORKSPACE_ID, USER_ID, "book.pdf", b"new content")
    assert second.ok is True

    docs = document_service.list_documents(db_session, WORKSPACE_ID)
    assert len(docs) == 1
    assert docs[0].id != first_id
    assert docs[0].size_bytes == len(b"new content")

    # The old chunks were removed before the new ones were indexed.
    assert any(
        name == "remove_chunks" and payload["document_id"] == first_id
        for name, payload in fake.calls
    )


def test_upload_marks_status_error_when_indexing_fails(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))

    class ErrorKB(FakeKB):
        def add_book(self, file_path, workspace_id=None, document_id=None, original_name=None, progress_callback=None, cancel_check=None):
            self._log("add_book", file_path=file_path, workspace_id=workspace_id, document_id=document_id)
            return "Формат не поддерживается: book.pdf"

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: ErrorKB())

    response = app_services.upload_material_service(
        WORKSPACE_ID, USER_ID, "book.pdf", b"hello"
    )

    assert response.ok is False
    docs = document_service.list_documents(db_session, WORKSPACE_ID)
    assert len(docs) == 1
    assert docs[0].status == document_service.STATUS_ERROR
    assert "не поддерживается" in docs[0].error_message.lower()
    # File was rolled back.
    workspace_dir = tmp_path / "docs" / WORKSPACE_ID
    if workspace_dir.exists():
        assert list(workspace_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# delete / reindex now resolve a Document by name
# ---------------------------------------------------------------------------


def test_delete_material_service_removes_document_chunks_and_file(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    # Upload first so we have a real on-disk file and a Document row.
    app_services.upload_material_service(WORKSPACE_ID, USER_ID, "book.pdf", b"hello")
    docs = document_service.list_documents(db_session, WORKSPACE_ID)
    assert len(docs) == 1
    doc_id = docs[0].id
    stored_path = docs[0].stored_path
    import os
    assert os.path.exists(stored_path)

    response = app_services.delete_material_service(WORKSPACE_ID, "book.pdf")

    assert response.ok is True
    assert document_service.list_documents(db_session, WORKSPACE_ID) == []
    assert not os.path.exists(stored_path)
    assert any(
        name == "remove_chunks" and payload["document_id"] == doc_id
        for name, payload in fake.calls
    )


def test_delete_material_service_reports_missing_material(db_session, monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    response = app_services.delete_material_service(WORKSPACE_ID, "ghost.pdf")

    assert response.ok is False
    assert "не найден" in response.message.lower()


def test_reindex_material_service_rebuilds_single_document(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    app_services.upload_material_service(WORKSPACE_ID, USER_ID, "book.pdf", b"hello")
    doc_id = document_service.list_documents(db_session, WORKSPACE_ID)[0].id
    fake.calls.clear()

    response = app_services.reindex_material_service(WORKSPACE_ID, "book.pdf")

    assert response.ok is True
    # Chunks for the same document_id are wiped and rebuilt.
    assert any(
        name == "remove_chunks" and payload["document_id"] == doc_id
        for name, payload in fake.calls
    )
    assert any(
        name == "add_book" and payload["document_id"] == doc_id
        for name, payload in fake.calls
    )


def test_reindex_material_service_rebuilds_full_library(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_services.config, "DOCS_DIR", str(tmp_path / "docs"))
    fake = FakeKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: fake)

    app_services.upload_material_service(WORKSPACE_ID, USER_ID, "a.pdf", b"AA")
    app_services.upload_material_service(WORKSPACE_ID, USER_ID, "b.pdf", b"BB")
    fake.calls.clear()

    response = app_services.reindex_material_service(WORKSPACE_ID)

    assert response.ok is True
    assert "Переиндексировано документов: 2" in response.message
    assert any(name == "clear" for name, _ in fake.calls)


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def test_material_progress_defaults_to_idle():
    progress = app_services.get_material_progress(WORKSPACE_ID)

    assert progress.active is False
    assert progress.operation == "idle"
    assert progress.progress >= 0


def test_material_progress_is_scoped_per_workspace():
    other_workspace = "another-workspace"
    app_services._set_material_progress(
        WORKSPACE_ID,
        active=True,
        operation="upload",
        current_file="secret.pdf",
        message="Загружаю secret.pdf",
        progress=42,
    )

    own = app_services.get_material_progress(WORKSPACE_ID)
    foreign = app_services.get_material_progress(other_workspace)

    assert own.active is True
    assert own.current_file == "secret.pdf"
    assert foreign.active is False
    assert foreign.current_file == ""


def test_start_reindex_material_service_queues_background_job(monkeypatch):
    calls = []

    monkeypatch.setattr(
        app_services,
        "reindex_material_service",
        lambda workspace_id, file_name=None: calls.append((workspace_id, file_name))
        or app_services.MaterialActionResponse(
            ok=True,
            message="done",
            material_name=file_name or "",
        ),
    )

    response = app_services.start_reindex_material_service(WORKSPACE_ID, "book.pdf")

    assert response.ok is True
    assert "Запустил" in response.message


# ---------------------------------------------------------------------------
# summary / chat / export — unchanged behaviours
# ---------------------------------------------------------------------------


def test_generate_summary_service_routes_section_to_selected_section_strategy(monkeypatch):
    """With ``selected_section`` set, the service must dispatch to
    ``summary_engine.generate_selected_section_summary`` and pass through
    the request fields + ``workspace_id``."""
    calls = []

    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: "llm")
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: "kb")
    monkeypatch.setattr(
        app_services.summary_engine,
        "generate_selected_section_summary",
        lambda **kwargs: calls.append(kwargs) or "summary text",
    )
    monkeypatch.setattr(app_services, "format_last_trace", lambda: "trace text")

    response = app_services.generate_summary_service(
        WORKSPACE_ID,
        SummaryRequest(
            selected_file="a.pdf",
            selected_section="Глава 1",
            topic="Bluetooth",
            summary_type="Средний",
        ),
    )

    assert response.text == "summary text"
    assert response.diagnostics == "trace text"
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["workspace_id"] == WORKSPACE_ID
    assert kwargs["selected_file"] == "a.pdf"
    assert kwargs["section_filter"] == "Глава 1"
    assert kwargs["topic"] == "Bluetooth"
    assert kwargs["summary_type"] == "Средний"


def test_generate_summary_service_normalizes_all_materials_label(monkeypatch):
    """``Все материалы`` collapses to ``Все файлы`` before reaching the
    strategy, and the strategy receives ``file_filter='all'``."""
    calls = []

    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: "llm")
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: "kb")
    # selected_section="Все разделы" + topic="Россия…" → no section_filter,
    # history-shaped topic → planned strategy.
    monkeypatch.setattr(
        app_services.summary_engine,
        "generate_planned_topic_summary",
        lambda **kwargs: calls.append(kwargs) or "summary text",
    )
    monkeypatch.setattr(app_services, "format_last_trace", lambda: "trace text")

    app_services.generate_summary_service(
        WORKSPACE_ID,
        SummaryRequest(
            selected_file="Все материалы",
            selected_section="Все разделы",
            topic="Россия после Николая II до 2000 года",
            summary_type="Средний",
        ),
    )

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["workspace_id"] == WORKSPACE_ID
    assert kwargs["file_filter"] == "all"
    assert kwargs["section_filter"] is None


def test_generate_summary_service_forwards_workspace_id(monkeypatch):
    """The caller's workspace_id must reach the chosen strategy.

    Regression guard for Stage 4: previously the service-layer accepted the
    argument but dropped it via ``del workspace_id``, which made the summary
    silently fall back on the legacy ``DEFAULT_WORKSPACE_ID`` ("dev-default").
    The constant itself is gone in Stage 6e; we keep the literal here to
    pin the historical value the bug used to fall back to.
    """
    calls = []

    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: "llm")
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: "kb")
    monkeypatch.setattr(
        app_services.summary_engine,
        "generate_selected_section_summary",
        lambda **kwargs: calls.append(kwargs) or "summary text",
    )
    monkeypatch.setattr(app_services, "format_last_trace", lambda: "trace text")

    custom_workspace = "ws-stage-4-isolated"

    app_services.generate_summary_service(
        custom_workspace,
        SummaryRequest(
            selected_file="a.pdf",
            selected_section="Глава 1",
            topic="Bluetooth",
            summary_type="Средний",
        ),
    )

    assert calls, "no strategy was called"
    kwargs = calls[0]
    assert kwargs["workspace_id"] == custom_workspace
    # Old fallback value the Stage 4 bug used to silently substitute. The
    # constant is gone in Stage 6e — the literal is kept here as a
    # regression guard.
    assert kwargs["workspace_id"] != "dev-default"


def test_generate_summary_service_returns_friendly_error_on_failure(monkeypatch):
    """Stage 42: an unexpected failure returns clean user copy (not a raw Python
    exception) and the failed run is not metered."""
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: "llm")
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: "kb")
    monkeypatch.setattr(app_services, "format_last_trace", lambda: "")

    def boom(**kwargs):
        raise RuntimeError("internal detail that must not leak to the user")

    monkeypatch.setattr(
        app_services.summary_engine, "generate_selected_section_summary", boom
    )

    metered = []
    monkeypatch.setattr(
        app_services.quota, "record_usage", lambda *a, **k: metered.append((a, k))
    )

    resp = app_services.generate_summary_service(
        "ws-x",
        SummaryRequest(selected_file="a.pdf", selected_section="Глава 1", topic="Bluetooth"),
    )

    assert "internal detail" not in resp.text  # raw exception must not leak
    assert "Не удалось" in resp.text  # clean, user-facing copy
    assert metered == []  # a failed run is not metered


def test_export_summary_docx_service_uses_export_utils(monkeypatch):
    calls = []

    monkeypatch.setattr(
        app_services,
        "export_text_to_docx",
        lambda **kwargs: calls.append(kwargs) or "exports/test.docx",
    )

    path = app_services.export_summary_docx_service(
        SummaryExportRequest(
            text="summary body",
            selected_file="Все материалы",
            selected_section="Все разделы",
            summary_type="Средний",
        )
    )

    assert path == "exports/test.docx"
    assert calls[0]["name_parts"][0] == "Все файлы"


def test_export_summary_docx_service_returns_none_for_empty_text():
    path = app_services.export_summary_docx_service(
        SummaryExportRequest(text="   "),
    )

    assert path is None


def test_chat_service_returns_greeting_without_runtime():
    response = app_services.chat_service(
        WORKSPACE_ID,
        ChatRequest(message="Привет"),
    )

    assert response.answer == "Привет! Задайте вопрос по загруженным текстам."
    assert response.history[-1].role == "assistant"
    assert response.summary == "Привет! Задайте вопрос по загруженным текстам."
    assert response.confidence_label == "system"


def test_chat_service_returns_structured_answer(monkeypatch):
    received = {}

    class FakeKBForChat:
        def find_section_in_query(self, message, workspace_id=None):
            received["find_section"] = workspace_id
            return None

        def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
            received["search"] = workspace_id
            return (
                "контекст",
                [{"source_file": "book.pdf", "section": "Глава 1", "score": 0.91}],
            )

    class FakeLLMForChat:
        def call(self, prompt, max_tokens=None, temperature=None):
            return "Готовый ответ"

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: FakeKBForChat())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: FakeLLMForChat())

    response = app_services.chat_service(
        WORKSPACE_ID,
        ChatRequest(
            message="Что такое Bluetooth?",
            selected_file="Все материалы",
            answer_mode="Обычный",
        ),
    )

    assert response.answer == "Готовый ответ"
    assert response.history[-1].content == "Готовый ответ"
    assert response.sources[0].label == "book.pdf -> Глава 1"
    assert response.summary == "Готовый ответ"
    assert response.confidence_label == "medium"
    assert len(response.followup_suggestions) == 3
    assert received["find_section"] == WORKSPACE_ID
    assert received["search"] == WORKSPACE_ID
