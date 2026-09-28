"""Pitch-demo seed structure tests (Stage 56). ``ingest=False`` so no embedding
model is needed - the material ingest is a local/dev-box step."""

from __future__ import annotations

# Register ORM models on Base so the db_session fixture's create_all builds them.
from src import db_models  # noqa: F401

from scripts.seed_pitch_demo import VERTICALS, seed_pitch


def test_seed_pitch_creates_a_course_per_vertical(db_session):
    summary = seed_pitch(ingest=False)
    assert len(summary) == len(VERTICALS)
    assert {c["course_name"] for c in summary} == {name for name, _ in VERTICALS}
    for course in summary:
        assert course["join_code"]
        assert course["ingested"] is False


def test_seed_pitch_is_idempotent(db_session):
    first = seed_pitch(ingest=False)
    second = seed_pitch(ingest=False)
    assert [c["workspace_id"] for c in first] == [c["workspace_id"] for c in second]

    from src.db import SessionLocal
    from src.db_models import Workspace

    db = SessionLocal()
    try:
        for name, _ in VERTICALS:
            assert db.query(Workspace).filter(Workspace.name == name).count() == 1
    finally:
        db.close()
