"""Stage 14-1: streaming chat endpoint (/api/chat/stream).

Drives the endpoint through the TestClient (which collects the full streamed
body) and parses the NDJSON lines. KB/LLM are faked so no models load.
"""

from __future__ import annotations

import json
import pytest

import config
from src import app_services
from src.db import SessionLocal
from src.db_models import UsageEvent, User, Workspace


class _ChatKB:
    """Minimal KB for the chat pipeline: returns context + one source."""

    def find_section_in_query(self, message, workspace_id=None):
        return None

    def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
        return ("Текст документа про тему.", [{"source_file": "a.pdf", "section": "Глава 1", "score": 0.9}])


class _EmptyKB(_ChatKB):
    def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
        return ("", [])


class _StreamLLM:
    def __init__(self, tokens):
        self._tokens = tokens

    def stream(self, prompt, temperature=None, max_tokens=None):
        for token in self._tokens:
            yield token

    def call(self, prompt, temperature=None, max_tokens=None):
        return "".join(self._tokens)


def _events(response):
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_stream_greeting_emits_single_done_no_tokens(authed_client):
    resp = authed_client.post("/api/chat/stream", json={"message": "привет"})
    events = _events(resp)
    assert all(e["type"] != "token" for e in events)
    assert events[-1]["type"] == "done"
    assert "Привет" in events[-1]["answer"]


def test_stream_generate_streams_tokens_then_done(authed_client, monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["Это ", "ответ", "."]))

    resp = authed_client.post(
        "/api/chat/stream",
        json={"message": "Объясни тему", "selected_file": "Все материалы", "answer_mode": "Обычный"},
    )
    events = _events(resp)

    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "Это ответ."

    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "Это ответ."
    assert len(done["sources"]) == 1
    assert done["sources"][0]["source_file"] == "a.pdf"
    # The user turn + assistant answer are appended to history.
    assert done["history"][-1] == {"role": "assistant", "content": "Это ответ."}


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_chat_hides_internal_fragment_number(authed_client, monkeypatch, endpoint):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(
        app_services.runtime,
        "get_llm",
        lambda: _StreamLLM(["Согласно таблице из фрагмента 2: результат 8."]),
    )

    response = authed_client.post(endpoint, json={"message": "Чему равен результат?"})
    result = _events(response)[-1] if endpoint.endswith("stream") else response.json()

    assert result["answer"] == "Согласно таблице из документа: результат 8."
    assert result["sources"][0]["source_file"] == "a.pdf"


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_broad_context_refusal_retries_three_passages(authed_client, monkeypatch, endpoint):
    class KB(_ChatKB):
        def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
            context = "\n\n---\n\n".join(f"[Фрагмент {index}] Текст {index}." for index in range(1, 5))
            sources = [
                {"source_file": f"source-{index}.txt", "section": "", "score": 0.9, "text": f"Текст {index}."}
                for index in range(1, 5)
            ]
            return context, sources

    class LLM:
        def call(self, prompt, temperature=None, max_tokens=None):
            return "Нет информации" if "Фрагмент 4" in prompt else "Ответ из первого источника."

        def stream(self, prompt, temperature=None, max_tokens=None):
            yield self.call(prompt)

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: KB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: LLM())

    response = authed_client.post(endpoint, json={"message": "Что сказано в первом источнике?"})
    result = _events(response)[-1] if endpoint.endswith("stream") else response.json()

    assert result["answer"] == "Ответ из первого источника."
    assert [source["source_file"] for source in result["sources"]] == [
        "source-1.txt", "source-2.txt", "source-3.txt"
    ]


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_focused_retry_preserves_refusal_when_no_passage_answers(authed_client, monkeypatch, endpoint):
    class KB(_ChatKB):
        def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
            context = "\n\n---\n\n".join(f"[Фрагмент {index}] Другая тема." for index in range(1, 5))
            sources = [
                {"source_file": f"source-{index}.txt", "section": "", "score": 0.9, "text": "Другая тема."}
                for index in range(1, 5)
            ]
            return context, sources

    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: KB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["Нет информации"]))

    response = authed_client.post(endpoint, json={"message": "Что сказано о другой теме?"})
    result = _events(response)[-1] if endpoint.endswith("stream") else response.json()

    assert "не найдена" in result["answer"]
    assert result["sources"] == []


def test_stream_normalizes_em_dash_to_hyphen(authed_client, monkeypatch):
    """Bot answers print a plain hyphen — live tokens and the final answer alike."""
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["Bluetooth ", "— это ", "стандарт.–конец"]))

    resp = authed_client.post("/api/chat/stream", json={"message": "Объясни тему"})
    events = _events(resp)

    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert all("—" not in t and "–" not in t for t in tokens)
    assert events[-1]["answer"] == "Bluetooth - это стандарт.-конец"


def test_stream_no_context_emits_done_without_tokens(authed_client, monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _EmptyKB())
    # get_llm must never be called on the no-context path.
    monkeypatch.setattr(
        app_services.runtime, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("LLM should not run"))
    )

    resp = authed_client.post("/api/chat/stream", json={"message": "Вопрос без контекста"})
    events = _events(resp)
    assert all(e["type"] != "token" for e in events)
    assert events[-1]["type"] == "done"
    assert "НЕТ ИНФОРМАЦИИ" in events[-1]["answer"]


def test_stream_quota_exceeded_returns_402_before_stream(authed_client, monkeypatch):
    """A hit quota surfaces as a plain 402 JSON, never a half-streamed answer."""
    monkeypatch.setattr(config, "QUOTAS_ENABLED", True)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "tester@example.com").one()
        ws = db.query(Workspace).filter(Workspace.owner_user_id == user.id).one()
        for _ in range(config.PLAN_LIMITS["free"]["chat_per_day"]):
            db.add(
                UsageEvent(
                    workspace_id=ws.id,
                    user_id=user.id,
                    action="chat",
                    units=1,
                    billing_subject_type="user",
                    billing_subject_id=user.id,
                )
            )
        db.commit()
    finally:
        db.close()

    resp = authed_client.post("/api/chat/stream", json={"message": "вопрос по теме"})
    assert resp.status_code == 402
    assert resp.json()["error"] == "quota_exceeded"


# --- Refusal must not ship sources -----------------------------------------
# A refusal means the retrieved passages did NOT support an answer. Listing them
# under "информация не найдена" reads as if something *was* found, which is the
# opposite of the grounding promise the product is sold on.


def test_stream_refusal_ships_no_sources(authed_client, monkeypatch):
    # KB returns context + a source, but the model says it can't answer from it.
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["НЕТ ИНФОРМАЦИИ"]))

    resp = authed_client.post(
        "/api/chat/stream",
        json={"message": "Как приготовить борщ?", "selected_file": "Все материалы"},
    )
    done = _events(resp)[-1]

    assert "не найдена" in done["answer"]
    assert done["sources"] == []


def test_sync_refusal_ships_no_sources(authed_client, monkeypatch):
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["НЕТ ИНФОРМАЦИИ"]))

    resp = authed_client.post(
        "/api/chat",
        json={"message": "Как приготовить борщ?", "selected_file": "Все материалы"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "не найдена" in body["answer"]
    assert body["sources"] == []


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_refusal_keeps_medical_limit_in_final_and_saved_answer(authed_client, monkeypatch, endpoint):
    explanation = "Дозировка в шаблоне не заполнена. Её должен определить лечащий врач."
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm",
                        lambda: _StreamLLM(["НЕТ ИНФОРМАЦИИ.\n\n", explanation]))
    response = authed_client.post(endpoint, json={"message": "Назначь дозу по шаблону"})
    assert response.status_code == 200
    answer = _events(response)[-1] if endpoint.endswith("stream") else response.json()
    assert answer["answer"] == explanation
    assert answer["sources"] == []
    saved = authed_client.get("/api/chat/sessions/" + answer["session_id"])
    assert saved.status_code == 200
    assert explanation in saved.text


def test_answered_question_still_ships_sources(authed_client, monkeypatch):
    # Guard the fix from over-reaching: a real answer keeps its citations.
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["По материалу: тема раскрыта."]))

    resp = authed_client.post(
        "/api/chat",
        json={"message": "Объясни тему", "selected_file": "Все материалы"},
    )
    assert resp.json()["sources"], "ответ по материалу обязан нести источники"


# --- Промах сужения по разделу ---------------------------------------------
# Заголовок документа попадает в список разделов, поэтому «сколько дней отпуска
# по Трудовому кодексу» распознавалось как просьба дать раздел «Трудовой кодекс
# РФ», и поиск сжимался до титульной страницы. Замер 03.09.2026: 12 фрагментов
# без фильтра против 1 с ним — и отказ на осмысленный вопрос.


class _NarrowSectionKB:
    """KB, где сужение по разделу даёт куда меньше, чем поиск по всему файлу."""

    def __init__(self):
        self.calls = []

    def find_section_in_query(self, message, workspace_id=None):
        return "ТРУДОВОЙ КОДЕКС РОССИЙСКОЙ ФЕДЕРАЦИИ"

    def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
        self.calls.append(section_filter)
        if section_filter:
            # титульная страница - формально найдено, а ответить нечем
            return ("ТРУДОВОЙ КОДЕКС РОССИЙСКОЙ ФЕДЕРАЦИИ", [
                {"source_file": "tk.txt", "section": "Титул", "score": 0.4, "text": "заголовок"},
            ])
        return ("Ежегодный основной оплачиваемый отпуск - 28 календарных дней.", [
            {"source_file": "tk.txt", "section": f"Статья {n}", "score": 0.9 - n / 100, "text": f"текст {n}"}
            for n in range(115, 121)
        ])


def test_narrow_section_filter_falls_back_to_the_whole_file(authed_client, monkeypatch):
    kb = _NarrowSectionKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: kb)
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["28 календарных дней."]))

    resp = authed_client.post(
        "/api/chat",
        json={"message": "Сколько дней отпуска по Трудовому кодексу?", "selected_file": "tk.txt"},
    )
    assert resp.status_code == 200

    # Поиск выполнен дважды: сперва с фильтром, затем без него.
    assert kb.calls == ["ТРУДОВОЙ КОДЕКС РОССИЙСКОЙ ФЕДЕРАЦИИ", None]
    # И в ответ ушли источники широкого поиска, а не одинокий титульник.
    assert len(resp.json()["sources"]) > 1


def test_section_filter_is_kept_when_it_finds_enough(authed_client, monkeypatch):
    """Обратная защита: осмысленное сужение отменять нельзя."""

    class _GoodSectionKB(_NarrowSectionKB):
        def search_with_sources(self, query, file_filter="all", section_filter=None, workspace_id=None):
            self.calls.append(section_filter)
            return ("Текст раздела.", [
                {"source_file": "tk.txt", "section": "Глава 19", "score": 0.9, "text": f"фрагмент {n}"}
                for n in range(4)
            ])

    kb = _GoodSectionKB()
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: kb)
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM(["Ответ по разделу."]))

    resp = authed_client.post("/api/chat", json={"message": "Что в главе 19?", "selected_file": "tk.txt"})
    assert resp.status_code == 200
    # Повторного поиска не было - сужение сработало нормально.
    assert kb.calls == ["ТРУДОВОЙ КОДЕКС РОССИЙСКОЙ ФЕДЕРАЦИИ"]


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_refusal_prefix_keeps_its_sentence_subject(authed_client, monkeypatch, endpoint):
    explanation = "НЕТ ИНФОРМАЦИИ о гарантии результата. Документ описывает только снижение риска."
    monkeypatch.setattr(app_services.runtime, "get_kb", lambda: _ChatKB())
    monkeypatch.setattr(app_services.runtime, "get_llm", lambda: _StreamLLM([explanation]))
    response = authed_client.post(endpoint, json={"message": "Есть ли гарантия?"})
    answer = _events(response)[-1] if endpoint.endswith("stream") else response.json()
    assert answer["answer"] == explanation
