"""Stage 19-2: chat history schema.

Verifies the two new tables exist with the right defaults, messages keep their
insertion order, and the parent cascades clean up children (deleting a session
drops its messages; deleting a course or a user drops their sessions).
"""

# Import models at load so they're on Base.metadata before the fixture's create_all.
from src.db_models import ChatMessage, ChatSession, User, Workspace


def _user(db, email):
    u = User(email=email, password_hash="x")
    db.add(u)
    db.flush()
    return u


def _session(db, user, ws, title="Чат"):
    s = ChatSession(workspace_id=ws.id, user_id=user.id, title=title)
    db.add(s)
    db.flush()
    return s


def test_session_and_messages_defaults_and_order(db_session):
    user = _user(db_session, "u@x")
    ws = Workspace(name="Personal", owner_user_id=user.id)
    db_session.add(ws)
    db_session.flush()

    s = _session(db_session, user, ws)
    db_session.add(ChatMessage(session_id=s.id, role="user", content="Вопрос?"))
    db_session.add(
        ChatMessage(session_id=s.id, role="assistant", content="Ответ", meta={"sources": []})
    )
    db_session.commit()
    db_session.refresh(s)

    assert len(s.messages) == 2
    # Ordered by created_at (relationship order_by).
    assert s.messages[0].role == "user"
    assert s.messages[1].role == "assistant"
    assert s.messages[1].meta == {"sources": []}


def test_deleting_session_cascades_messages(db_session):
    user = _user(db_session, "u@x")
    ws = Workspace(name="Personal", owner_user_id=user.id)
    db_session.add(ws)
    db_session.flush()
    s = _session(db_session, user, ws)
    db_session.add(ChatMessage(session_id=s.id, role="user", content="hi"))
    db_session.commit()

    db_session.delete(s)
    db_session.commit()
    assert db_session.query(ChatMessage).count() == 0


def test_deleting_workspace_cascades_sessions(db_session):
    user = _user(db_session, "u@x")
    ws = Workspace(name="Course", owner_user_id=user.id, kind="course")
    db_session.add(ws)
    db_session.flush()
    s = _session(db_session, user, ws)
    db_session.add(ChatMessage(session_id=s.id, role="user", content="hi"))
    db_session.commit()

    db_session.delete(ws)
    db_session.commit()
    assert db_session.query(ChatSession).count() == 0
    assert db_session.query(ChatMessage).count() == 0


def test_deleting_user_cascades_their_sessions(db_session):
    owner = _user(db_session, "owner@x")
    student = _user(db_session, "student@x")
    course = Workspace(name="Course", owner_user_id=owner.id, kind="course")
    db_session.add(course)
    db_session.flush()
    # A student's chat inside the teacher's course.
    s = _session(db_session, student, course, title="Студент")
    db_session.add(ChatMessage(session_id=s.id, role="user", content="hi"))
    db_session.commit()

    db_session.delete(student)
    db_session.commit()
    # The student's session (and its messages) are gone; the course remains.
    assert db_session.query(ChatSession).count() == 0
    assert db_session.query(ChatMessage).count() == 0
    assert db_session.query(Workspace).filter(Workspace.id == course.id).count() == 1
