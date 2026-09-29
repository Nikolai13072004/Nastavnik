"""Stage 41: export DOCX is streamed then deleted (no disk accumulation)."""

from pathlib import Path

import config


def _docx_files():
    export_dir = Path(config.DATA_DIR) / "exports"
    return set(export_dir.glob("*.docx")) if export_dir.exists() else set()


def test_export_file_deleted_after_send(authed_client):
    before = _docx_files()

    resp = authed_client.post(
        "/api/exports/summary",
        json={"text": "Конспект про телеграф и радио."},
    )

    assert resp.status_code == 200
    # The background task deletes the generated file after streaming, so there
    # is no net accumulation on disk.
    assert _docx_files() == before


def test_export_empty_text_is_rejected(authed_client):
    resp = authed_client.post("/api/exports/summary", json={"text": "   "})
    assert resp.status_code == 400
