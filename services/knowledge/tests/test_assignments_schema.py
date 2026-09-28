"""Stage 18-2: assignments & attempts schema.

Verifies the two new tables exist with the right defaults, the one-attempt-per-
student unique constraint holds, and the parent cascades clean up children
(deleting an assignment drops its attempts; deleting a course drops its
assignments).
"""

import pytest
from sqlalchemy.exc import IntegrityError

# Import models at load so they're on Base.metadata before the fixture's create_all.
from src.db_models import Assignment, AssignmentAttempt, User, Workspace


def _user(db, email):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.flush()
    return u


def _course(db, owner):
    ws = Workspace(name="Сети", owner_user_id=owner.id, kind="course")
    db.add(ws)
    db.flush()
    return ws


def test_assignment_defaults_to_draft(db_session):
    owner = _user(db_session, "owner@x")
    ws = _course(db_session, owner)
    a = Assignment(
        workspace_id=ws.id,
        created_by_user_id=owner.id,
        title="Контрольная 1",
        questions=[{"question": "2+2?", "options": ["3", "4"], "correct_index": 1, "explanation": ""}],
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)

    assert a.is_published is False  # draft by default
    assert a.source_label == ""
    assert len(a.questions) == 1


def test_one_attempt_per_student(db_session):
    owner = _user(db_session, "owner@x")
    student = _user(db_session, "s@x")
    ws = _course(db_session, owner)
    a = Assignment(workspace_id=ws.id, created_by_user_id=owner.id, title="T", questions=[])
    db_session.add(a)
    db_session.flush()

    db_session.add(AssignmentAttempt(assignment_id=a.id, user_id=student.id, answers=[1], score=1, total=1))
    db_session.commit()

    db_session.add(AssignmentAttempt(assignment_id=a.id, user_id=student.id, answers=[0], score=0, total=1))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_deleting_assignment_cascades_attempts(db_session):
    owner = _user(db_session, "owner@x")
    student = _user(db_session, "s@x")
    ws = _course(db_session, owner)
    a = Assignment(workspace_id=ws.id, created_by_user_id=owner.id, title="T", questions=[])
    db_session.add(a)
    db_session.flush()
    db_session.add(AssignmentAttempt(assignment_id=a.id, user_id=student.id, answers=[], score=0, total=0))
    db_session.commit()

    db_session.delete(a)
    db_session.commit()

    assert db_session.query(AssignmentAttempt).count() == 0


def test_deleting_course_cascades_assignments(db_session):
    owner = _user(db_session, "owner@x")
    student = _user(db_session, "s@x")
    ws = _course(db_session, owner)
    a = Assignment(workspace_id=ws.id, created_by_user_id=owner.id, title="T", questions=[])
    db_session.add(a)
    db_session.flush()
    db_session.add(AssignmentAttempt(assignment_id=a.id, user_id=student.id, answers=[], score=0, total=0))
    db_session.commit()

    db_session.delete(ws)
    db_session.commit()

    assert db_session.query(Assignment).count() == 0
    assert db_session.query(AssignmentAttempt).count() == 0
