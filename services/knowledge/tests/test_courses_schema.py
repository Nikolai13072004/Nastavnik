"""Stage 15-1: courses & roles schema + join-code helper.

Schema-only stage — no endpoints yet. Verifies the new columns exist with the
right defaults (so personal workspaces are unaffected) and that the join-code
generator is safe + effectively unique.
"""

from src import courses
# Import the models at module load so they're registered on ``Base.metadata``
# before the ``db_session`` fixture runs ``create_all`` (otherwise the first
# test's fixture creates an empty schema).
from src.db_models import User, Workspace


def test_workspace_defaults_to_personal_kind(db_session):
    user = User(email="t@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()

    ws = Workspace(name="Personal", owner_user_id=user.id)
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    db_session.refresh(user)

    # New columns default so existing personal-workspace behavior is unchanged.
    assert ws.kind == courses.WORKSPACE_KIND_PERSONAL
    assert ws.join_code is None
    assert ws.join_enabled is True
    assert ws.is_archived is False
    assert user.active_workspace_id is None


def test_course_workspace_can_carry_a_join_code(db_session):
    teacher = User(email="teacher@example.com", password_hash="x")
    db_session.add(teacher)
    db_session.flush()

    code = courses.generate_join_code()
    course = Workspace(
        name="Сети",
        owner_user_id=teacher.id,
        kind=courses.WORKSPACE_KIND_COURSE,
        join_code=code,
    )
    db_session.add(course)
    db_session.commit()

    found = db_session.query(Workspace).filter(Workspace.join_code == code).one()
    assert found.kind == courses.WORKSPACE_KIND_COURSE
    assert found.join_enabled is True


def test_join_code_is_safe_alphabet_and_length():
    code = courses.generate_join_code()
    assert len(code) == courses.JOIN_CODE_LENGTH
    assert all(ch in courses.JOIN_CODE_ALPHABET for ch in code)
    # No ambiguous characters (0/O, 1/I/L).
    assert not (set(code) & set("0O1IL"))


def test_join_codes_are_effectively_unique():
    codes = {courses.generate_join_code() for _ in range(500)}
    # 8 chars over a 31-symbol alphabet → collisions are astronomically rare.
    assert len(codes) > 495
