"""Unit tests for ``src.document_service`` (Stage 3c).

Uses a FakeKB to keep these focused on persistence + path layout + status
transitions; KB internals are covered by ``test_knowledge_base_isolation``.
"""

import os
import re

import pytest

from src import document_service, runtime
from src.db_models import Document
from tests.test_app_services import FakeKB


WORKSPACE_A = "workspace-a"
WORKSPACE_B = "workspace-b"
USER_A = "user-a"
USER_B = "user-b"


@pytest.fixture()
def fake_kb(monkeypatch):
    fake = FakeKB()
    monkeypatch.setattr(runtime, "get_kb", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def isolated_docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("config.DOCS_DIR", str(tmp_path / "docs"))


# ---------------------------------------------------------------------------
# create_document
# ---------------------------------------------------------------------------


def test_create_document_persists_row(db_session, fake_kb):
    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"hello",
    )

    persisted = db_session.get(Document, doc.id)
    assert persisted is not None
    assert persisted.workspace_id == WORKSPACE_A
    assert persisted.owner_user_id == USER_A
    assert persisted.original_name == "book.pdf"
    assert persisted.status == document_service.STATUS_READY
    assert persisted.size_bytes == len(b"hello")
    assert persisted.content_hash  # sha256 is populated


def test_create_document_uses_document_id_in_stored_path(db_session, fake_kb):
    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"hello",
    )

    # Path layout: docs/<workspace_id>/<document_id>__<safe_name>
    parts = doc.stored_path.replace("\\", "/").split("/")
    assert WORKSPACE_A in parts
    filename = os.path.basename(doc.stored_path)
    assert filename == f"{doc.id}__book.pdf"
    assert os.path.exists(doc.stored_path)
    with open(doc.stored_path, "rb") as f:
        assert f.read() == b"hello"


def test_create_document_invokes_kb_with_document_id(db_session, fake_kb):
    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"hello",
    )

    add_calls = [payload for name, payload in fake_kb.calls if name == "add_book"]
    assert len(add_calls) == 1
    assert add_calls[0]["workspace_id"] == WORKSPACE_A
    assert add_calls[0]["document_id"] == doc.id
    # Stage 3d fix: original_name is propagated to the KB so chunk metadata
    # stores ``source_file = original_name`` (not the on-disk filename with
    # document_id prefix).
    assert add_calls[0]["original_name"] == "book.pdf"


def test_reindex_document_invokes_kb_with_original_name(db_session, fake_kb):
    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"hello",
    )
    fake_kb.calls.clear()

    document_service.reindex_document(db_session, WORKSPACE_A, doc.id)

    add_calls = [payload for name, payload in fake_kb.calls if name == "add_book"]
    assert add_calls
    assert add_calls[0]["original_name"] == "book.pdf"
    assert add_calls[0]["document_id"] == doc.id


def test_create_document_replaces_existing_with_same_original_name(db_session, fake_kb):
    first = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"old",
    )
    first_path = first.stored_path
    first_id = first.id

    second = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"new content",
    )

    # New row has a fresh id; old row was deleted (replace-on-conflict).
    assert second.id != first_id
    assert db_session.get(Document, first_id) is None
    docs = document_service.list_documents(db_session, WORKSPACE_A)
    assert [d.id for d in docs] == [second.id]
    assert not os.path.exists(first_path)

    # KB saw remove_chunks for the old document_id before add_book for new.
    remove_calls = [payload for name, payload in fake_kb.calls if name == "remove_chunks"]
    assert any(payload["document_id"] == first_id for payload in remove_calls)


def test_create_document_marks_error_when_indexer_returns_error_string(db_session, monkeypatch):
    class ErrorKB(FakeKB):
        def add_book(self, file_path, workspace_id=None, document_id=None, original_name=None, progress_callback=None, cancel_check=None):
            self._log("add_book", file_path=file_path, workspace_id=workspace_id, document_id=document_id)
            return "Файл пуст: book.pdf"

    monkeypatch.setattr(runtime, "get_kb", lambda: ErrorKB())

    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"",
    )

    assert doc.status == document_service.STATUS_ERROR
    assert "Файл пуст" in doc.error_message
    # File was rolled back.
    assert not os.path.exists(doc.stored_path)


def test_create_document_error_message_is_truncated_to_1024_chars(db_session, monkeypatch):
    long_message = "x" * 5000

    class ChattyKB(FakeKB):
        def add_book(self, file_path, workspace_id=None, document_id=None, original_name=None, progress_callback=None, cancel_check=None):
            return long_message

    monkeypatch.setattr(runtime, "get_kb", lambda: ChattyKB())

    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="book.pdf",
        content=b"hello",
    )

    assert doc.status == document_service.STATUS_ERROR
    assert len(doc.error_message) == 1024


def test_create_document_marks_error_when_indexer_raises(db_session, monkeypatch):
    class CrashKB(FakeKB):
        def add_book(self, file_path, workspace_id=None, document_id=None, original_name=None, progress_callback=None, cancel_check=None):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(runtime, "get_kb", lambda: CrashKB())

    with pytest.raises(RuntimeError):
        document_service.create_document(
            db_session,
            workspace_id=WORKSPACE_A,
            owner_user_id=USER_A,
            original_name="book.pdf",
            content=b"hello",
        )

    docs = document_service.list_documents(db_session, WORKSPACE_A)
    assert len(docs) == 1
    assert docs[0].status == document_service.STATUS_ERROR
    assert docs[0].error_message == "kaboom"


# ---------------------------------------------------------------------------
# list / find / get
# ---------------------------------------------------------------------------


def test_list_documents_is_scoped_to_workspace(db_session, fake_kb):
    a = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "a.pdf", b"AA"
    )
    document_service.create_document(
        db_session, WORKSPACE_B, USER_B, "b.pdf", b"BB"
    )

    a_docs = document_service.list_documents(db_session, WORKSPACE_A)
    b_docs = document_service.list_documents(db_session, WORKSPACE_B)

    assert [d.id for d in a_docs] == [a.id]
    assert len(b_docs) == 1
    assert b_docs[0].workspace_id == WORKSPACE_B


def test_get_document_returns_none_for_foreign_workspace(db_session, fake_kb):
    """Security invariant: workspace A cannot resolve workspace B's document."""
    a_doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "a.pdf", b"AA"
    )

    # Caller authenticated as workspace B asking for A's UUID -> None.
    assert document_service.get_document(db_session, WORKSPACE_B, a_doc.id) is None
    # Caller authenticated as workspace A -> hit.
    assert document_service.get_document(db_session, WORKSPACE_A, a_doc.id) is not None


def test_find_document_by_name_normalizes_basename(db_session, fake_kb):
    doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "book.pdf", b"hello"
    )
    found = document_service.find_document_by_name(db_session, WORKSPACE_A, "subdir/book.pdf")
    assert found is not None
    assert found.id == doc.id


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


def test_delete_document_removes_row_file_and_chunks(db_session, fake_kb):
    doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "book.pdf", b"hello"
    )
    stored_path = doc.stored_path
    assert os.path.exists(stored_path)

    assert document_service.delete_document(db_session, WORKSPACE_A, doc.id) is True

    assert db_session.get(Document, doc.id) is None
    assert not os.path.exists(stored_path)
    assert any(
        name == "remove_chunks" and payload["document_id"] == doc.id
        for name, payload in fake_kb.calls
    )


def test_delete_document_returns_false_for_foreign_workspace(db_session, fake_kb):
    """Cross-workspace delete attempts must be a silent no-op (no leak of
    whether the UUID exists, no mutation)."""
    doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "book.pdf", b"hello"
    )

    assert document_service.delete_document(db_session, WORKSPACE_B, doc.id) is False
    assert db_session.get(Document, doc.id) is not None
    assert os.path.exists(doc.stored_path)


# ---------------------------------------------------------------------------
# reindex_document
# ---------------------------------------------------------------------------


def test_reindex_document_wipes_and_rebuilds_chunks(db_session, fake_kb):
    doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "book.pdf", b"hello"
    )
    fake_kb.calls.clear()

    refreshed = document_service.reindex_document(db_session, WORKSPACE_A, doc.id)

    assert refreshed is not None
    assert refreshed.status == document_service.STATUS_READY
    # remove_chunks must run BEFORE add_book.
    op_sequence = [name for name, _ in fake_kb.calls if name in {"remove_chunks", "add_book"}]
    assert op_sequence == ["remove_chunks", "add_book"]
    add_payload = next(payload for name, payload in fake_kb.calls if name == "add_book")
    assert add_payload["document_id"] == doc.id


def test_reindex_document_returns_none_for_foreign_workspace(db_session, fake_kb):
    doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "book.pdf", b"hello"
    )

    assert document_service.reindex_document(db_session, WORKSPACE_B, doc.id) is None


def test_reindex_document_marks_error_when_file_missing(db_session, fake_kb):
    doc = document_service.create_document(
        db_session, WORKSPACE_A, USER_A, "book.pdf", b"hello"
    )
    os.remove(doc.stored_path)

    refreshed = document_service.reindex_document(db_session, WORKSPACE_A, doc.id)

    assert refreshed is not None
    assert refreshed.status == document_service.STATUS_ERROR
    assert "файл" in refreshed.error_message.lower()


# ---------------------------------------------------------------------------
# path sanity
# ---------------------------------------------------------------------------


def test_stored_path_is_safe_against_path_traversal(db_session, fake_kb, tmp_path):
    """A malicious ``original_name`` must not escape the workspace directory."""
    doc = document_service.create_document(
        db_session,
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="../../etc/passwd",
        content=b"hello",
    )

    workspace_root = os.path.abspath(str(tmp_path / "docs" / WORKSPACE_A))
    assert os.path.abspath(doc.stored_path).startswith(workspace_root)
    assert re.search(r"[\\/]\.\.[\\/]", doc.stored_path) is None


# ---------------------------------------------------------------------------
# startup recovery
# ---------------------------------------------------------------------------


def test_recover_interrupted_documents_removes_partial_chunks_and_marks_error(
    db_session, fake_kb, tmp_path
):
    interrupted = Document(
        workspace_id=WORKSPACE_A,
        owner_user_id=USER_A,
        original_name="interrupted.pdf",
        stored_path=str(tmp_path / "interrupted.pdf"),
        size_bytes=100,
        content_hash="a" * 64,
        status=document_service.STATUS_PROCESSING,
    )
    ready = Document(
        workspace_id=WORKSPACE_B,
        owner_user_id=USER_B,
        original_name="ready.pdf",
        stored_path=str(tmp_path / "ready.pdf"),
        size_bytes=100,
        content_hash="b" * 64,
        status=document_service.STATUS_READY,
    )
    db_session.add_all([interrupted, ready])
    db_session.commit()

    recovered = document_service.recover_interrupted_documents(
        db_session, kb=fake_kb
    )

    db_session.refresh(interrupted)
    db_session.refresh(ready)
    remove_calls = [
        payload for name, payload in fake_kb.calls if name == "remove_chunks"
    ]
    assert recovered == 1
    assert remove_calls == [{"workspace_id": WORKSPACE_A, "document_id": interrupted.id}]
    assert interrupted.status == document_service.STATUS_ERROR
    assert "перезапуском сервера" in interrupted.error_message
    assert ready.status == document_service.STATUS_READY


def test_recover_interrupted_documents_is_idempotent(db_session, fake_kb):
    assert document_service.recover_interrupted_documents(db_session, kb=fake_kb) == 0
    assert document_service.recover_interrupted_documents(db_session, kb=fake_kb) == 0
    assert [name for name, _payload in fake_kb.calls if name == "remove_chunks"] == []
