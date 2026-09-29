"""Stage 19-3: chat history — sessions persist, owner-only, server source of truth.

Greeting turns persist without touching KB/LLM, so the basic session lifecycle
needs no stubs; the generate/stream paths use the same fakes as test_chat_stream.
The security-critical guarantees are asserted: a chat is owner-only (another user
— including a course teacher — gets 404), sessions are scoped to the active
workspace, and an aborted/failed stream saves nothing.
"""

import json

import api_app  # noqa: F401  (registers models before fixtures' create_all)
from conftest import WorkspaceClient as TestClient

from src import app_services
from src.api_models import ChatRequest


class _ChatKB:
    def find_section_in_query(self, message, workspace_id=None):
        return None

    def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
        return ("Текст документа про тему.", [{"source_file": "a.pdf", "section": "Глава 1", "score": 0.9}])


class _StreamLLM:
    def __init__(self, tokens):
        self._tokens = tokens

    def stream(self, prompt, temperature=None, max_tokens=None):
        for token in self._tokens:
            yield token

    def call(self, prompt, temperature=None, max_tokens=None):
        return "".join(self._tokens)


def _register(client, email):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": "password12345", "display_name": email.split("@")[0]},
    )


def _events(resp):
    assert resp.status_code == 200
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# --- Basic lifecycle (greeting path → no KB/LLM needed) --------------------
def test_new_chat_creates_session(authed_client):
    r = authed_client.post("/api/chat", json={"message": "привет"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert sid

    sessions = authed_client.get("/api/chat/sessions").json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["title"] == "привет"  # from the first user message


def test_continue_appends_to_same_session(authed_client):
    sid = authed_client.post("/api/chat", json={"message": "привет"}).json()["session_id"]
    again = authed_client.post("/api/chat", json={"message": "привет", "session_id": sid})
    assert again.json()["session_id"] == sid

    detail = authed_client.get(f"/api/chat/sessions/{sid}").json()
    assert len(detail["messages"]) == 4
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["role"] == "assistant"


def test_rename_and_delete(authed_client):
    sid = authed_client.post("/api/chat", json={"message": "привет"}).json()["session_id"]
    renamed = authed_client.post(f"/api/chat/sessions/{sid}/rename", json={"title": "Моя тема"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Моя тема"

    assert authed_client.delete(f"/api/chat/sessions/{sid}").status_code == 200
    assert authed_client.get("/api/chat/sessions").json()["sessions"] == []
    assert authed_client.get(f"/api/chat/sessions/{sid}").status_code == 404


# --- Ownership / privacy ----------------------------------------------------
def test_other_user_cannot_read_or_use_session(authed_client):
    sid = authed_client.post("/api/chat", json={"message": "привет"}).json()["session_id"]
    with TestClient(api_app.app) as other:
        _register(other, "other-ch@example.com")
        assert other.get(f"/api/chat/sessions/{sid}").status_code == 404
        # Posting with someone else's session_id is rejected, not silently reused.
        assert other.post("/api/chat", json={"message": "привет", "session_id": sid}).status_code == 404
        assert other.get("/api/chat/sessions").json()["sessions"] == []


def test_teacher_cannot_read_student_chat(authed_client):
    course_id = authed_client.post("/api/courses", json={"name": "Сети"}).json()["id"]
    code = authed_client.get(f"/api/courses/{course_id}").json()["join_code"]

    with TestClient(api_app.app) as student:
        _register(student, "stud-ch@example.com")
        student.post("/api/courses/join", json={"code": code})  # auto-active in the course
        sid = student.post("/api/chat", json={"message": "привет"}).json()["session_id"]

    # The teacher is active in the same course but sees only their own chats.
    sessions = authed_client.get("/api/chat/sessions").json()["sessions"]
    assert all(s["id"] != sid for s in sessions)
    # And a direct read of the student's chat is 404 (not the owner).
    assert authed_client.get(f"/api/chat/sessions/{sid}").status_code == 404


def test_sessions_scoped_to_active_workspace(authed_client):
    authed_client.post("/api/chat", json={"message": "привет"})  # in personal
    # Creating a course switches the active workspace to it.
    authed_client.post("/api/courses", json={"name": "К"})
    assert authed_client.get("/api/chat/sessions").json()["sessions"] == []  # personal chat not here

    authed_client.post("/api/chat", json={"message": "привет"})  # in the course
    assert len(authed_client.get("/api/chat/sessions").json()["sessions"]) == 1


# --- Generate / stream paths (with KB+LLM fakes) ---------------------------
def test_assistant_meta_snapshots_sources(authed_client, monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["ответ"]))

    sid = authed_client.post("/api/chat", json={"message": "Объясни тему"}).json()["session_id"]
    detail = authed_client.get(f"/api/chat/sessions/{sid}").json()
    assistant = next(m for m in detail["messages"] if m["role"] == "assistant")
    assert assistant["meta"]["sources"][0]["source_file"] == "a.pdf"


def test_stream_persists_and_returns_session_id(authed_client, monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["Это ", "ответ"]))

    done = _events(authed_client.post("/api/chat/stream", json={"message": "Объясни тему"}))[-1]
    assert done["type"] == "done"
    assert done["session_id"]

    detail = authed_client.get(f"/api/chat/sessions/{done['session_id']}").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][1]["content"] == "Это ответ"


def test_stream_error_does_not_persist(authed_client, monkeypatch):
    class _BoomLLM:
        def stream(self, prompt, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover - makes this a generator function

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _BoomLLM())

    events = _events(authed_client.post("/api/chat/stream", json={"message": "Объясни тему"}))
    assert events[-1]["type"] == "error"
    # A failed/aborted stream saves no truncated turn.
    assert authed_client.get("/api/chat/sessions").json()["sessions"] == []


def test_chat_service_without_db_is_backward_compatible(monkeypatch):
    """Old callers (no db/user) still work: no persistence, empty session_id."""
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["ответ"]))

    resp = app_services.chat_service("ws-x", ChatRequest(message="Объясни тему"))
    assert resp.answer
    assert resp.session_id == ""
