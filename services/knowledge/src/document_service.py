"""CRUD + indexing orchestration for the ``Document`` table.

Stage 3c makes ``Document`` the source of truth for the user's library:

* ``app_services.upload_material_service`` delegates to :func:`create_document`,
  which writes the file to ``docs/<workspace_id>/<document_id>__<safe_filename>``,
  indexes it via :class:`KnowledgeBase` with the explicit ``document_id`` so
  Chroma chunks carry that id in their metadata, and persists a row.
* ``app_services.delete_material_service`` / ``reindex_material_service``
  resolve a Document by ``(workspace_id, original_name)`` and call
  :func:`delete_document` / :func:`reindex_document`, which always tear down /
  rebuild chunks via ``kb.remove_chunks(workspace_id, document_id)``.

Replace-on-conflict is enforced here: uploading a second file with the same
``original_name`` into the same workspace deletes the existing Document (chunks,
file on disk, SQL row) and creates a new one with a fresh ``document_id``.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import document_loader, knowledge_base, runtime, storage
from src.db_models import Document


# Document statuses
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_ERROR = "error"


def _normalize_name(name: str) -> str:
    return os.path.basename(str(name or "")).strip()


def _safe_error_message(error: BaseException) -> str:
    """Return a short string for ``Document.error_message``.

    Truncated to fit ``Document.error_message``'s ``String(1024)`` column.
    Does not include stack traces; the underlying exception message itself
    may include filesystem paths under ``docs/<workspace_id>/``, but those
    are not secrets — they're derived from the user's own upload.
    """
    text = str(error or "").strip()
    if not text:
        text = error.__class__.__name__
    return text[:1024]


def find_document_by_name(
    db: Session, workspace_id: str, original_name: str
) -> Document | None:
    """Look up a Document in ``workspace_id`` by its ``original_name``.

    Used by the legacy ``/api/materials/{file_name}/...`` endpoints so the
    frontend can keep addressing materials by name during the transition to
    ``document_id``-based URLs.
    """
    name = _normalize_name(original_name)
    if not name:
        return None
    return db.execute(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.original_name == name,
        )
    ).scalar_one_or_none()


def get_document(db: Session, workspace_id: str, document_id: str) -> Document | None:
    """Look up a Document by id, but only if it belongs to ``workspace_id``.

    Returning ``None`` for a cross-workspace request is the security invariant:
    a caller authenticated for workspace A must never be able to mutate a
    document owned by workspace B even with a guessed UUID.
    """
    doc = db.get(Document, document_id)
    if doc is None or doc.workspace_id != workspace_id:
        return None
    return doc


def list_documents(db: Session, workspace_id: str) -> list[Document]:
    """Return Documents in ``workspace_id`` ordered by creation time."""
    return list(
        db.execute(
            select(Document)
            .where(Document.workspace_id == workspace_id)
            .order_by(Document.created_at.asc())
        ).scalars()
    )


def recover_interrupted_documents(db: Session, *, kb=None) -> int:
    """Remove partial indexes left by a process restart and mark them failed.

    Indexing runs in a background thread. A process stop can leave the SQL row
    in ``processing`` and a subset of its chunks in Chroma. Startup invokes
    this before accepting requests so partial knowledge is never searchable as
    if the document were complete. The source file stays available for the
    user's explicit «Обновить поиск» retry.

    A Chroma cleanup failure is intentionally propagated: serving a known
    partial index is worse than a visible startup failure.
    """
    interrupted = list(
        db.execute(
            select(Document).where(Document.status == STATUS_PROCESSING)
        ).scalars()
    )
    if not interrupted:
        return 0

    active_kb = kb or runtime.get_kb()
    for doc in interrupted:
        active_kb.remove_chunks(doc.workspace_id, doc.id)
        doc.status = STATUS_ERROR
        doc.sections_count = 0
        doc.error_message = (
            "Обработка была прервана перезапуском сервера. "
            "Нажмите «Обновить поиск», чтобы построить индекс заново."
        )
    db.commit()
    return len(interrupted)


def create_document(
    db: Session,
    workspace_id: str,
    owner_user_id: str,
    original_name: str,
    content: bytes,
    cancel_check=None,
    progress_callback=None,
) -> Document:
    """Persist a Document, save the file, and index it.

    Behaviour:

    1. If a Document with the same ``(workspace_id, original_name)`` already
       exists, it is fully deleted first (chunks → file → row).
    2. A new Document is inserted in ``processing`` status so a concurrent
       ``list_documents`` call sees the in-flight upload.
    3. The file is written to ``docs/<workspace_id>/<document_id>__<safe_name>``.
    4. ``KnowledgeBase.add_book`` indexes the file with the explicit
       ``document_id``.
    5. ``status`` becomes ``ready`` and ``sections_count`` is refreshed, or
       ``error`` with a (truncated) message — never both.

    Best-effort cleanup: an exception after the file is written removes the
    file, marks the row ``error``, and re-raises only for unexpected runtime
    errors. Expected failures (validation, ``add_book`` returning an error
    string) are recorded on the row and do *not* raise.
    """
    name = _normalize_name(original_name)
    if not name:
        raise ValueError("original_name is required")

    # 1. Replace-on-conflict.
    existing = find_document_by_name(db, workspace_id, name)
    if existing is not None:
        delete_document(db, workspace_id, existing.id)

    # 2. Insert the new row in ``processing`` status.
    document_id = str(uuid.uuid4())
    stored_path = storage.document_stored_path(workspace_id, document_id, name)

    doc = Document(
        id=document_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        original_name=name,
        stored_path=stored_path,
        size_bytes=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
        status=STATUS_PROCESSING,
        sections_count=0,
        error_message="",
    )
    db.add(doc)
    db.flush()

    # 3. Write the file.
    os.makedirs(storage.workspace_docs_dir(workspace_id), exist_ok=True)
    try:
        with open(stored_path, "wb") as f:
            f.write(content)
    except Exception as error:
        doc.status = STATUS_ERROR
        doc.error_message = _safe_error_message(error)
        db.commit()
        raise

    # 4. Index the file.
    kb = runtime.get_kb()
    try:
        result = kb.add_book(
            stored_path,
            workspace_id=workspace_id,
            document_id=document_id,
            original_name=name,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
        )
    except knowledge_base.IndexingCancelled:
        # Cancelled mid-index (Stage 9a): fully roll back so nothing orphaned
        # is left behind — chunks added so far, the file, and the row.
        try:
            kb.remove_chunks(workspace_id, document_id)
        except Exception:
            pass
        _safe_unlink(stored_path)
        db.delete(doc)
        db.commit()
        raise
    except Exception as error:
        doc.status = STATUS_ERROR
        doc.error_message = _safe_error_message(error)
        # Roll back the file we just wrote — the row stays so the user can see
        # what failed and re-upload without a unique-name collision.
        _safe_unlink(stored_path)
        db.commit()
        raise

    # 5. Reconcile final state.
    if isinstance(result, str) and not (result.startswith("✅") or result.startswith("⏭️")):
        doc.status = STATUS_ERROR
        doc.error_message = result[:1024]
        _safe_unlink(stored_path)
    else:
        doc.status = STATUS_READY
        doc.sections_count = _sections_count_from_kb(kb, workspace_id, name)
        doc.error_message = ""
        doc.ocr_used = document_loader.last_load_used_ocr()

    db.commit()
    db.refresh(doc)
    return doc


def delete_document(db: Session, workspace_id: str, document_id: str) -> bool:
    """Remove a Document, its file, and all of its chunks.

    Returns ``True`` if a row was deleted, ``False`` if the document didn't
    exist (or belonged to a different workspace — the security check is
    silent so callers can't probe foreign UUIDs).
    """
    doc = get_document(db, workspace_id, document_id)
    if doc is None:
        return False

    # Chunks first: if the row is gone but the chunks remain, list_materials
    # would still hide it (we list from SQL), but search/chat could surface
    # orphaned fragments.
    kb = runtime.get_kb()
    try:
        kb.remove_chunks(workspace_id, document_id)
    except Exception:
        # Best-effort: don't block delete on a Chroma hiccup. Any orphan chunks
        # this leaves behind are reclaimed by the Stage 9c reconciler
        # (``src.maintenance.reconcile_*`` / superuser ``POST /api/admin/reconcile``).
        pass

    _safe_unlink(doc.stored_path)

    db.delete(doc)
    db.commit()
    return True


def reindex_document(
    db: Session, workspace_id: str, document_id: str
) -> Document | None:
    """Wipe and rebuild the chunks for an existing Document.

    The file on disk and the SQL row are left in place — only Chroma is
    rebuilt. Status flips back to ``processing`` for the duration so a
    concurrent ``list_materials`` accurately reports the state.
    """
    doc = get_document(db, workspace_id, document_id)
    if doc is None:
        return None

    # Путь в БД абсолютный и мог устареть (перенос проекта, Docker <-> локаль).
    # Файл при этом на месте - storage восстановит путь по текущему DOCS_DIR.
    source_path = storage.resolve_stored_path(doc.stored_path, workspace_id)
    if not os.path.exists(source_path):
        doc.status = STATUS_ERROR
        doc.error_message = "Файл материала отсутствует на диске."
        db.commit()
        return doc

    kb = runtime.get_kb()
    doc.status = STATUS_PROCESSING
    doc.error_message = ""
    db.commit()

    try:
        kb.remove_chunks(workspace_id, document_id)
        result = kb.add_book(
            source_path,
            workspace_id=workspace_id,
            document_id=document_id,
            original_name=doc.original_name,
        )
    except Exception as error:
        doc.status = STATUS_ERROR
        doc.error_message = _safe_error_message(error)
        db.commit()
        raise

    if isinstance(result, str) and not (result.startswith("✅") or result.startswith("⏭️")):
        doc.status = STATUS_ERROR
        doc.error_message = result[:1024]
    else:
        doc.status = STATUS_READY
        doc.sections_count = _sections_count_from_kb(kb, workspace_id, doc.original_name)
        doc.error_message = ""
        doc.ocr_used = document_loader.last_load_used_ocr()

    db.commit()
    db.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_unlink(path: str) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _sections_count_from_kb(kb, workspace_id: str, original_name: str) -> int:
    """Best-effort sections count for the freshly indexed file.

    ``kb.get_file_profile`` already filters by ``workspace_id`` + ``source_file``,
    so the count is naturally scoped. If the KB call fails we return 0 — the
    user-visible cost is a less informative quality badge, not a crash.
    """
    try:
        profile = kb.get_file_profile(original_name, workspace_id=workspace_id)
    except Exception:
        return 0
    if not isinstance(profile, dict):
        return 0
    try:
        return int(profile.get("sections_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def document_ids(documents: Iterable[Document]) -> list[str]:
    """Convenience: list of ids for tests/logging without importing Document."""
    return [doc.id for doc in documents]
