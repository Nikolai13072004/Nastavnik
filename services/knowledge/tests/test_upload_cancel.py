"""Cooperative upload cancellation (Stage 9a upload-ux)."""

from __future__ import annotations

import os

import pytest


def test_cancel_service_returns_false_when_idle():
    from src import app_services

    app_services.reset_material_progress_for_tests()
    resp = app_services.cancel_material_service("ws-none")
    assert resp.ok is False


def test_cancel_rolls_back_partial_document(db_session, monkeypatch):
    """When add_book is cancelled mid-index, create_document removes the
    partial file and Document row — nothing orphaned is left behind."""
    from src import document_service, knowledge_base
    from src.db_models import Document, User, Workspace

    # Minimal workspace/owner so the FK-less write path has valid ids.
    user = User(email="c@example.com", password_hash="x", display_name="C")
    db_session.add(user)
    db_session.flush()
    ws = Workspace(name="C ws", owner_user_id=user.id, plan="free")
    db_session.add(ws)
    db_session.flush()

    class CancellingKB:
        def add_book(self, file_path, *, workspace_id=None, document_id=None,
                     original_name=None, progress_callback=None, cancel_check=None):
            # Simulate a cancel raised between batches.
            if cancel_check is not None and cancel_check():
                raise knowledge_base.IndexingCancelled(document_id)
            return f"✅ {original_name}: ok"

        def remove_chunks(self, workspace_id, document_id):
            return None

    monkeypatch.setattr(document_service.runtime, "get_kb", lambda: CancellingKB())

    with pytest.raises(knowledge_base.IndexingCancelled):
        document_service.create_document(
            db_session,
            workspace_id=ws.id,
            owner_user_id=user.id,
            original_name="cancel-me.txt",
            content=b"hello world",
            cancel_check=lambda: True,
        )

    # No surviving Document row, and the workspace docs dir has no leftover file.
    from src import storage

    remaining = db_session.query(Document).filter(Document.workspace_id == ws.id).all()
    assert remaining == []

    docs_dir = storage.workspace_docs_dir(ws.id)
    leftover = os.listdir(docs_dir) if os.path.isdir(docs_dir) else []
    assert leftover == []
