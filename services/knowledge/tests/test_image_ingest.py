"""Photo → Vision ingest guards (Stage 56). CI-safe: the vision call is
monkeypatched, so no real model/API runs and the indexing pipeline is never
reached (both guard paths return before ``start_upload_material_service``)."""

import src.llm_engine as llm_engine
from src import app_services


def test_image_ingest_reports_vision_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(llm_engine, "transcribe_image", boom)
    out = app_services.ingest_image_service("ws", "u", "board.jpg", b"x", "image/jpeg")
    assert out.ok is False
    assert "Не удалось распознать" in out.message


def test_image_ingest_rejects_empty_transcription(monkeypatch):
    monkeypatch.setattr(llm_engine, "transcribe_image", lambda *_a, **_k: "   ")
    out = app_services.ingest_image_service("ws", "u", "board.jpg", b"x", "image/jpeg")
    assert out.ok is False
    assert "не нашлось" in out.message.lower()


def test_image_ingest_rejects_non_image_mime():
    # Non-image must be rejected before any (paid) vision call.
    out = app_services.ingest_image_service("ws", "u", "doc.pdf", b"x" * 100, "application/pdf")
    assert out.ok is False
    assert "не изображение" in out.message.lower()


def test_image_ingest_rejects_oversized(monkeypatch):
    import config

    monkeypatch.setattr(config, "VISION_MAX_IMAGE_BYTES", 1000, raising=False)
    out = app_services.ingest_image_service("ws", "u", "big.jpg", b"x" * 2000, "image/jpeg")
    assert out.ok is False
    assert "слишком большое" in out.message.lower()
