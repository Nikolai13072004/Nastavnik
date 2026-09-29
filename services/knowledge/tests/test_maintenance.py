"""Stage 9c: tests for the orphan-chunk reconciler (`src.maintenance`).

Exercises the *real* ``KnowledgeBase`` against a throwaway ChromaDB collection
(same trick as ``test_knowledge_base_isolation``: build via ``__new__`` + a tiny
fake embedder so CI never loads the 2 GB BGE models), plus a real SQLite session
from the ``db_session`` fixture.

``Document`` is imported at module load so its table is registered on
``Base.metadata`` before the ``db_session`` fixture's ``create_all`` runs — even
when this file is collected before any app-importing test.
"""

import hashlib

import chromadb

import config
from src import maintenance
from src.db_models import Document
from src.knowledge_base import KnowledgeBase


class FakeEmbeddings:
    """Deterministic, dependency-free stand-in for the BGE-M3 embeddings."""

    def __init__(self, dim=16):
        self.dim = dim

    def _vec(self, text):
        digest = hashlib.md5(text.strip().encode("utf-8")).digest()
        repeats = (self.dim // len(digest)) + 1
        raw = (digest * repeats)[: self.dim]
        vec = [b / 255.0 for b in raw]
        norm = sum(v * v for v in vec) ** 0.5
        return vec if norm == 0 else [v / norm for v in vec]

    def embed_documents(self, texts):
        return [self._vec(text) for text in texts]

    def embed_query(self, text):
        return self._vec(text)


def make_kb(tmp_path):
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._log = lambda *args, **kwargs: None
    kb._llm = None
    kb._reranker = None
    kb._reranker_loaded = True
    kb._embeddings = FakeEmbeddings()
    client = chromadb.PersistentClient(path=str(tmp_path / "chromadb"))
    kb._client = client
    kb._col = client.create_collection(
        name=config.COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    return kb


def add_doc(kb, tmp_path, *, workspace_id, document_id, name):
    path = tmp_path / name
    path.write_text(f"Содержимое материала {name}. " * 6, encoding="utf-8")
    kb.add_book(
        str(path),
        workspace_id=workspace_id,
        document_id=document_id,
        original_name=name,
    )


def _document_row(*, document_id, workspace_id, name):
    return Document(
        id=document_id,
        workspace_id=workspace_id,
        owner_user_id="owner-1",
        original_name=name,
        stored_path=f"docs/{workspace_id}/{document_id}__{name}",
        status="ready",
    )


# ---------------------------------------------------------------------------
# KB-level scrub
# ---------------------------------------------------------------------------


def test_remove_orphan_chunks_drops_unbacked_and_spares_the_rest(tmp_path):
    kb = make_kb(tmp_path)
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-a1", name="a1.txt")
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-a2", name="a2.txt")
    add_doc(kb, tmp_path, workspace_id="ws-b", document_id="doc-b1", name="b1.txt")

    # ws-a keeps only doc-a1 -> doc-a2 is an orphan.
    result = kb.remove_orphan_chunks(workspace_id="ws-a", valid_document_ids={"doc-a1"})

    assert result["removed_chunks"] > 0
    assert result["removed_document_ids"] == ["doc-a2"]
    assert kb.list_document_ids(workspace_id="ws-a") == ["doc-a1"]
    # A different workspace is never touched by a ws-a reconcile.
    assert kb.stats(workspace_id="ws-b")["total_books"] == 1


def test_remove_orphan_chunks_is_idempotent(tmp_path):
    kb = make_kb(tmp_path)
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-keep", name="keep.txt")
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-gone", name="gone.txt")

    first = kb.remove_orphan_chunks(workspace_id="ws-a", valid_document_ids={"doc-keep"})
    assert first["removed_chunks"] > 0

    # Second run over the now-clean store finds nothing.
    second = kb.remove_orphan_chunks(workspace_id="ws-a", valid_document_ids={"doc-keep"})
    assert second == {"removed_chunks": 0, "removed_document_ids": []}


def test_list_workspace_ids_enumerates_from_chroma(tmp_path):
    kb = make_kb(tmp_path)
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-a", name="a.txt")
    add_doc(kb, tmp_path, workspace_id="ws-b", document_id="doc-b", name="b.txt")

    assert kb.list_workspace_ids() == ["ws-a", "ws-b"]


# ---------------------------------------------------------------------------
# Reconcile against the Document table
# ---------------------------------------------------------------------------


def test_reconcile_workspace_with_no_document_rows_clears_all(tmp_path, db_session):
    """The exact observed symptom: chunks present in Chroma, 0 Document rows."""
    kb = make_kb(tmp_path)
    add_doc(kb, tmp_path, workspace_id="ws-orphan", document_id="doc-x", name="x.txt")
    assert kb.stats(workspace_id="ws-orphan")["total_books"] == 1

    summary = maintenance.reconcile_workspace("ws-orphan", kb=kb, db=db_session)

    assert summary["removed_documents"] == 1
    assert summary["removed_chunks"] > 0
    assert kb.stats(workspace_id="ws-orphan")["total_books"] == 0


def test_reconcile_all_workspaces_removes_orphans_and_spares_backed(tmp_path, db_session):
    kb = make_kb(tmp_path)
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-keep", name="keep.txt")
    add_doc(kb, tmp_path, workspace_id="ws-a", document_id="doc-orphan", name="orphan.txt")
    add_doc(kb, tmp_path, workspace_id="ws-b", document_id="doc-b", name="b.txt")

    # Only doc-keep (ws-a) and doc-b (ws-b) have backing rows; doc-orphan does not.
    db_session.add(_document_row(document_id="doc-keep", workspace_id="ws-a", name="keep.txt"))
    db_session.add(_document_row(document_id="doc-b", workspace_id="ws-b", name="b.txt"))
    db_session.commit()

    summary = maintenance.reconcile_all_workspaces(kb=kb, db=db_session)

    assert summary["total_removed_documents"] == 1
    assert summary["total_removed_chunks"] > 0
    # ws-a lost only the orphan; ws-b (fully backed) is untouched.
    assert kb.list_document_ids(workspace_id="ws-a") == ["doc-keep"]
    assert kb.stats(workspace_id="ws-b")["total_books"] == 1

    # Idempotent at the orchestration level too.
    again = maintenance.reconcile_all_workspaces(kb=kb, db=db_session)
    assert again["total_removed_chunks"] == 0
    assert again["total_removed_documents"] == 0
