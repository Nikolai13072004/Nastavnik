"""Demo-seed script structure tests (Stage 32).

Exercises the DB-seeding path with ``ingest=False`` so no embedding model is
needed - the full material ingest is a local/dev-box step. The ``db_session``
fixture creates the schema on the shared engine that ``seed()`` also uses.
"""

from __future__ import annotations

# Registers all ORM models on Base so the ``db_session`` fixture's
# ``create_all`` actually builds the schema (seed_demo imports models lazily).
from src import db_models  # noqa: F401

from scripts.seed_demo import COURSE_NAME, STUDENT_EMAIL, TEACHER_EMAIL, seed


def test_seed_creates_teacher_course_and_student_membership(db_session):
    info = seed(ingest=False)

    assert info["course_name"] == COURSE_NAME
    assert info["join_code"]
    assert info["ingested"] is False

    from src.db import SessionLocal
    from src.db_models import User, Workspace, WorkspaceMember

    db = SessionLocal()
    try:
        teacher = db.query(User).filter(User.email == TEACHER_EMAIL).one()
        # The demo teacher is a real (non-superuser) course creator.
        assert teacher.is_superuser is False
        assert teacher.can_create_courses is True

        course = db.query(Workspace).filter(Workspace.id == info["workspace_id"]).one()
        assert course.owner_user_id == teacher.id
        assert course.kind == "course"

        student = db.query(User).filter(User.email == STUDENT_EMAIL).one()
        member = (
            db.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == course.id,
                WorkspaceMember.user_id == student.id,
            )
            .one()
        )
        assert member.role == "student"
    finally:
        db.close()


def test_seed_is_idempotent(db_session):
    first = seed(ingest=False)
    second = seed(ingest=False)
    # Re-running reuses the same course instead of creating a duplicate.
    assert first["workspace_id"] == second["workspace_id"]

    from src.db import SessionLocal
    from src.db_models import Workspace

    db = SessionLocal()
    try:
        courses = db.query(Workspace).filter(Workspace.name == COURSE_NAME).all()
        assert len(courses) == 1
    finally:
        db.close()
