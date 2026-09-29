"""Stage 17-1: study trainer generation (flashcards & quizzes).

Function-level tests drive ``generate_study_service`` with a faked KB + scripted
LLM (no models, no network); one endpoint test covers the daily quota (402).
"""

import config
from src import app_services
from src.api_models import StudyRequest


class _CtxKB:
    def __init__(self, chunks: int = 3):
        self._n = chunks

    def _chunks(self):
        return [{"text": f"факт {i}", "source_file": "m.pdf", "section": ""} for i in range(self._n)]

    def search_chunks_for_summary(self, query, file_filter="all", section_filter=None, top_k=None, workspace_id=None):
        return self._chunks()

    def get_file_chunks(self, file_filter="all", section_filter=None, workspace_id=None):
        return self._chunks()


class _EmptyKB(_CtxKB):
    def search_chunks_for_summary(self, *a, **k):
        return []

    def get_file_chunks(self, *a, **k):
        return []


class _ScriptLLM:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def call(self, prompt, max_tokens=None, temperature=None):
        self.calls += 1
        return self._replies.pop(0) if self._replies else ""


def _req(mode, topic=""):
    return StudyRequest(selected_file="Все материалы", topic=topic, mode=mode, count=3)


def test_flashcards_happy_and_normalizes_dashes(monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _CtxKB())
    monkeypatch.setattr(
        app_services.runtime,
        "get_llm",
        lambda: _ScriptLLM(['{"cards":[{"q":"Что?","a":"Ответ — тут"},{"q":"Где?","a":"Там"}]}']),
    )
    resp = app_services.generate_study_service("ws", _req("flashcards"))
    assert resp.ok and resp.mode == "flashcards"
    assert len(resp.cards) == 2
    assert resp.cards[0].question == "Что?"
    assert "—" not in resp.cards[0].answer  # dash normalized to hyphen


def test_quiz_happy_drops_bad_and_clamps_correct(monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _CtxKB())
    reply = (
        "```json\n"
        '{"questions":['
        '{"q":"2+2?","options":["3","4","5","6"],"correct":1,"explanation":"ок"},'
        '{"q":"oob","options":["x","y","z","w"],"correct":9,"explanation":""},'
        '{"q":"too few","options":["only"],"correct":0}'
        "]}\n```"
    )
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _ScriptLLM([reply]))
    resp = app_services.generate_study_service("ws", _req("quiz"))
    assert resp.ok and resp.mode == "quiz"
    # The 1-option item is dropped; the rest kept.
    assert len(resp.questions) == 2
    assert resp.questions[0].correct_index == 1
    assert resp.questions[1].correct_index == 0  # out-of-range clamped to 0


def test_retry_then_fallback(monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _CtxKB())
    llm = _ScriptLLM(["не json", "тоже не json"])
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: llm)
    resp = app_services.generate_study_service("ws", _req("flashcards"))
    assert resp.ok is False
    assert resp.cards == [] and resp.questions == []
    assert llm.calls == 2  # retried once before giving up


def test_retry_recovers_on_second_try(monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _CtxKB())
    llm = _ScriptLLM(["мусор без json", '{"cards":[{"q":"A","a":"B"}]}'])
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: llm)
    resp = app_services.generate_study_service("ws", _req("flashcards"))
    assert resp.ok and len(resp.cards) == 1
    assert llm.calls == 2


def test_empty_material_returns_not_ok(monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _EmptyKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _ScriptLLM([]))
    resp = app_services.generate_study_service("ws", _req("flashcards"))
    assert resp.ok is False


def test_study_quota_exceeded_returns_402(authed_client, monkeypatch):
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)

    from src.db import SessionLocal
    from src.db_models import UsageEvent, User, Workspace

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "tester@example.com").one()
        ws = db.query(Workspace).filter(Workspace.owner_user_id == user.id).one()
        for _ in range(config.PLAN_LIMITS["free"]["study_per_day"]):
            db.add(
                UsageEvent(
                    workspace_id=ws.id,
                    user_id=user.id,
                    action="study",
                    units=1,
                    billing_subject_type="user",
                    billing_subject_id=user.id,
                )
            )
        db.commit()
    finally:
        db.close()

    resp = authed_client.post("/api/study", json={"selected_file": "Все материалы", "mode": "flashcards"})
    assert resp.status_code == 402
    assert resp.json()["error"] == "quota_exceeded"
