"""Reconcile ChromaDB vectors with the ``Document`` SQL table (Stage 9c).

The ``Document`` table is the source of truth for ownership (see ``PROJECT.md``).
KB chunks carry a matching ``workspace_id`` + ``document_id`` in their metadata,
but the two stores can drift apart:

* ``document_service.delete_document`` removes chunks best-effort
  (``except Exception: pass``) — a Chroma hiccup leaves orphan chunks behind
  while the SQL row is already gone.
* A dev DB reset (Alembic / fixtures / a manual ``app.db`` wipe) recreates the
  SQL schema while the on-disk Chroma store keeps its old vectors.

``reconcile_*`` scrubs those orphans: per workspace it diffs the ``document_id``s
present in Chroma against the live ``Document.id`` rows and drops the chunks
that have no backing row. Workspace-scoped throughout — it never crosses
tenants — and idempotent: a second run over a clean store removes nothing.

``kb`` / ``db`` are injectable so tests can drive a throwaway KB + SQLite
session without touching process-wide singletons.
"""

from __future__ import annotations

from sqlalchemy import select

from src import runtime
from src.db import SessionLocal
from src.db_models import Document


def _valid_document_ids(db, workspace_id: str) -> set[str]:
    """Live ``Document.id``s for one workspace — the set a chunk must match."""
    rows = db.execute(
        select(Document.id).where(Document.workspace_id == workspace_id)
    ).scalars()
    return {row for row in rows}


def reconcile_workspace(workspace_id: str, *, kb=None, db=None) -> dict:
    """Scrub orphan chunks for one workspace. Returns a per-workspace summary."""
    kb = kb or runtime.get_kb()
    own_db = db is None
    db = db or SessionLocal()
    try:
        valid = _valid_document_ids(db, workspace_id)
        result = kb.remove_orphan_chunks(
            workspace_id=workspace_id, valid_document_ids=valid
        )
        return {
            "workspace_id": workspace_id,
            "removed_chunks": result["removed_chunks"],
            "removed_documents": len(result["removed_document_ids"]),
        }
    finally:
        if own_db:
            db.close()


def reconcile_all_workspaces(*, kb=None, db=None) -> dict:
    """Scrub orphan chunks across every workspace present in the index.

    Workspaces are enumerated from Chroma metadata (not SQL) so a workspace
    whose SQL rows were wiped is still caught. Returns per-workspace results
    plus instance-wide totals.
    """
    kb = kb or runtime.get_kb()
    own_db = db is None
    db = db or SessionLocal()
    try:
        per_workspace = [
            reconcile_workspace(ws, kb=kb, db=db) for ws in kb.list_workspace_ids()
        ]
        return {
            "workspaces": per_workspace,
            "total_removed_chunks": sum(r["removed_chunks"] for r in per_workspace),
            "total_removed_documents": sum(r["removed_documents"] for r in per_workspace),
        }
    finally:
        if own_db:
            db.close()
