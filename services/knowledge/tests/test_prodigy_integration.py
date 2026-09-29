"""The integration key must never grant another organization's documents."""

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import config

from src import app_services, prodigy_integration, runtime
from src.api_models import ChatRequest, ChatResponse, ChatSource
from src.db import SessionLocal
from src.db_models import Document, ProdigyIntegration, Workspace
from scripts.provision_prodigy_integration import main as provision


PATH = "/api/integrations/prodigy/chat"
TOKEN_A = "a" * 64
TOKEN_B = "b" * 64
COURSE_A = "course-a"
COURSE_B = "course-b"


def _import(client, payload, token=TOKEN_A, org="org-a", course=COURSE_A):
    return client.post("/api/integrations/prodigy/documents/import", json=payload, headers={
        "Authorization": f"Bearer {token}", "X-Prodigy-Organization-ID": org,
        "X-Prodigy-Course-ID": course,
    })


@pytest.fixture()
def import_job(monkeypatch):
    monkeypatch.setattr(config, "PRODIGY_DOCUMENT_IMPORT_ENABLED", True)
    progress = SimpleNamespace(active=False, current_file="")
    calls = []
    monkeypatch.setattr(app_services, "get_material_progress", lambda _: progress)

    def start(workspace_id, owner_id, name, content):
        calls.append((workspace_id, owner_id, name, content))
        progress.active = True
        progress.current_file = name
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(app_services, "start_upload_material_service", start)
    return progress, calls


def test_import_checks_credential_scope_before_starting_job(authed_client, indexed_kb, import_job):
    _provision(authed_client, indexed_kb)
    payload = {"source_name": "rule.md", "content_text": "Approved text",
               "content_hash": hashlib.sha256(b"Approved text").hexdigest()}
    for options in [{"token": "x" * 64}, {"org": "org-b"}, {"course": COURSE_B}, {"token": TOKEN_B}]:
        assert _import(authed_client, payload, **options).status_code == 401
    assert authed_client.post("/api/integrations/prodigy/documents/import", json=payload).status_code == 401
    assert not import_job[1]
    with SessionLocal.begin() as db:
        db.query(ProdigyIntegration).filter_by(organization_id="org-a").one().is_active = False
    assert _import(authed_client, payload).status_code == 401


def test_import_exact_text_pending_and_retry_do_not_create_duplicate_jobs(authed_client, indexed_kb, import_job):
    first, _ = _provision(authed_client, indexed_kb)
    progress, calls = import_job
    text = "Проверенный текст\nБез смены переносов."
    digest = hashlib.sha256(text.encode()).hexdigest()
    payload = {"source_name": "rule.md", "content_text": text, "content_hash": digest}
    assert _import(authed_client, payload).json()["status"] == "PROCESSING"
    assert _import(authed_client, {**payload, "retry": True}).json()["status"] == "PROCESSING"
    assert len(calls) == 1
    assert calls[0][0] == first
    assert calls[0][3] == text.encode()
    with SessionLocal.begin() as db:
        document = Document(workspace_id=first, owner_user_id=calls[0][1], original_name=calls[0][2],
                            stored_path="/test/import.md", size_bytes=len(text.encode()),
                            content_hash=digest, status="error")
        db.add(document)
        db.flush()
        document_id = document.id
    progress.active = False
    assert _import(authed_client, payload).json()["status"] == "ERROR"
    assert len(calls) == 1
    assert _import(authed_client, {**payload, "retry": True}).json()["status"] == "PROCESSING"
    assert len(calls) == 2
    progress.active = False
    with SessionLocal.begin() as db:
        db.get(Document, document_id).status = "ready"
    response = _import(authed_client, payload)
    assert response.json() == {"status": "READY", "document_id": document_id, "document_hash": digest}
    assert len(calls) == 2


def test_import_rejects_invalid_bytes_names_sizes_and_extra_fields(authed_client, indexed_kb, import_job):
    _provision(authed_client, indexed_kb)
    payload = {"source_name": "rule.md", "content_text": "Approved text",
               "content_hash": hashlib.sha256(b"Approved text").hexdigest()}
    for patch in [{"source_name": "../rule.md"}, {"source_name": "rule.pdf"},
                  {"content_hash": "0" * 64}, {"retry": "true"}, {"organization_id": "org-b"},
                  {"content_text": ""}]:
        assert _import(authed_client, {**payload, **patch}).status_code == 400
    assert _import(authed_client, {**payload, "content_text": "я" * 17000}).status_code == 413
    assert _import(authed_client, {**payload, "content_text": "x" * 70000}).status_code == 413
    assert not import_job[1]
    monkey_disabled = config.PRODIGY_DOCUMENT_IMPORT_ENABLED
    try:
        config.PRODIGY_DOCUMENT_IMPORT_ENABLED = False
        assert _import(authed_client, payload).status_code == 404
    finally:
        config.PRODIGY_DOCUMENT_IMPORT_ENABLED = monkey_disabled


def test_import_busy_workspace_never_replaces_another_document(authed_client, indexed_kb, import_job):
    first, _ = _provision(authed_client, indexed_kb)
    progress, calls = import_job
    progress.active = True
    progress.current_file = "other.md"
    payload = {"source_name": "rule.md", "content_text": "Approved text",
               "content_hash": hashlib.sha256(b"Approved text").hexdigest()}
    assert _import(authed_client, payload).json()["status"] == "BUSY"
    assert not calls
    with SessionLocal() as db:
        assert db.query(Document).filter_by(workspace_id=first).count() == 1


def test_chat_filters_before_generation_and_rejects_unavailable_ids(authed_client, indexed_kb, monkeypatch):
    first, second = _provision(authed_client, indexed_kb)
    with SessionLocal() as db:
        own_id = db.query(Document).filter_by(workspace_id=first).one().id
        foreign_id = db.query(Document).filter_by(workspace_id=second).one().id
    seen = []

    def chat(workspace_id, request, *, allowed_document_ids):
        seen.append((workspace_id, allowed_document_ids))
        return ChatResponse(answer="Утверждает ответственный.", sources=[
            ChatSource(source_file="a.txt", snippet="Утверждает ответственный.")])

    monkeypatch.setattr(prodigy_integration, "training_chat", chat)
    headers = {"Authorization": f"Bearer {TOKEN_A}", "X-Prodigy-Organization-ID": "org-a",
               "X-Prodigy-Course-ID": COURSE_A}
    request = {"question": "Кто утверждает?", "document_ids": [own_id]}
    assert authed_client.post(PATH, json=request, headers=headers).status_code == 200
    assert seen == [(first, [own_id])]
    assert authed_client.post(PATH, json={**request, "document_ids": [foreign_id]}, headers=headers).status_code == 403
    assert authed_client.post(PATH, json={**request, "document_ids": []}, headers=headers).status_code == 422
    with SessionLocal.begin() as db:
        db.get(Document, own_id).status = "processing"
    assert authed_client.post(PATH, json=request, headers=headers).status_code == 403
    assert len(seen) == 1
    monkeypatch.setattr(config, "PRODIGY_DOCUMENT_IMPORT_ENABLED", True)
    assert authed_client.post(PATH, json={"question": "Кто утверждает?"}, headers=headers).status_code == 400


@pytest.fixture()
def indexed_kb(monkeypatch):
    class IndexedKB:
        def __init__(self):
            self.passages = {}

        def get_document_chunks(self, document_id, *, workspace_id):
            return self.passages.get((workspace_id, document_id), [])

    kb = IndexedKB()
    monkeypatch.setattr(runtime, "get_kb", lambda: kb)
    return kb


def _provision(client, indexed_kb):
    first = client.post("/api/courses", json={"name": "Prodigy A"}).json()["id"]
    second = client.post("/api/courses", json={"name": "Prodigy B"}).json()["id"]
    with SessionLocal.begin() as db:
        db.add_all([
            ProdigyIntegration(organization_id="org-a", course_id=COURSE_A, workspace_id=first,
                token_hash=hashlib.sha256(TOKEN_A.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
            ProdigyIntegration(organization_id="org-b", course_id=COURSE_B, workspace_id=second,
                token_hash=hashlib.sha256(TOKEN_B.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
        ])
        db.add_all([
            Document(workspace_id=first, owner_user_id=db.get(Workspace, first).owner_user_id,
                original_name="a.txt", stored_path="/test/a.txt", size_bytes=10,
                content_hash="hash-a", status="ready"),
            Document(workspace_id=second, owner_user_id=db.get(Workspace, second).owner_user_id,
                original_name="b.txt", stored_path="/test/b.txt", size_bytes=10,
                content_hash="hash-b", status="ready"),
        ])
    with SessionLocal() as db:
        documents = db.query(Document).all()
        for document in documents:
            text = (
                "Утверждает ответственный."
                if document.original_name == "a.txt"
                else "Секретное правило B"
            )
            indexed_kb.passages[(document.workspace_id, document.id)] = [{
                "text": text,
                "source_file": document.original_name,
                "section": "",
                "page_start": None,
                "page_end": None,
            }]
    return first, second


def _ask(client, token=TOKEN_A, org="org-a", course=COURSE_A):
    return client.post(PATH, json={"question": "Кто утверждает документ?"}, headers={
        "Authorization": f"Bearer {token}", "X-Prodigy-Organization-ID": org,
        "X-Prodigy-Course-ID": course,
    })


def _get_document(client, document_id, token=TOKEN_A, org="org-a", course=COURSE_A):
    return client.get(f"/api/integrations/prodigy/documents/{document_id}", headers={
        "Authorization": f"Bearer {token}", "X-Prodigy-Organization-ID": org,
        "X-Prodigy-Course-ID": course,
    })


def _find_document(client, document_hash, token=TOKEN_A, org="org-a", course=COURSE_A):
    return _get_document(client, f"by-hash/{document_hash}", token, org, course)


def test_hash_lookup_is_exact_and_never_searches_another_workspace(authed_client, indexed_kb):
    first, second = _provision(authed_client, indexed_kb)
    digest = hashlib.sha256(b"approved content").hexdigest()
    with SessionLocal.begin() as db:
        own = db.query(Document).filter_by(workspace_id=first).one()
        foreign = db.query(Document).filter_by(workspace_id=second).one()
        own.content_hash = digest
        foreign.content_hash = digest
        own_id = own.id

    response = _find_document(authed_client, digest)
    assert response.status_code == 200
    assert response.json() == {"document_id": own_id, "document_hash": digest, "title": "a.txt"}
    assert _find_document(authed_client, "0" * 64).status_code == 404
    assert _find_document(authed_client, digest, org="org-b").status_code == 401
    assert _find_document(authed_client, digest, course=COURSE_B).status_code == 401
    assert _find_document(authed_client, digest, token=TOKEN_B).status_code == 401
    assert authed_client.get(f"/api/integrations/prodigy/documents/by-hash/{digest}").status_code == 401
    assert _find_document(authed_client, "bad").status_code == 400

    with SessionLocal.begin() as db:
        db.get(Document, own_id).status = "hidden"
    assert _find_document(authed_client, digest).status_code == 404


def test_hash_lookup_refuses_duplicates_and_non_ready_sources(authed_client, indexed_kb):
    first, _ = _provision(authed_client, indexed_kb)
    digest = "a" * 64
    with SessionLocal.begin() as db:
        own = db.query(Document).filter_by(workspace_id=first).one()
        own.content_hash = digest
        db.add(Document(
            workspace_id=first, owner_user_id=own.owner_user_id,
            original_name="duplicate.md", stored_path="/test/duplicate.md", size_bytes=10,
            content_hash=digest, status="ready",
        ))
    assert _find_document(authed_client, digest).status_code == 409
    with SessionLocal.begin() as db:
        db.query(Document).filter_by(workspace_id=first, original_name="duplicate.md").one().status = "processing"
    assert _find_document(authed_client, digest).status_code == 200
    with SessionLocal.begin() as db:
        db.query(Document).filter_by(workspace_id=first, original_name="a.txt").one().status = "error"
    assert _find_document(authed_client, digest).status_code == 404


def test_document_lookup_is_bound_to_ready_document_in_workspace(authed_client, indexed_kb):
    first, second = _provision(authed_client, indexed_kb)
    with SessionLocal() as db:
        own = db.query(Document).filter_by(workspace_id=first).one()
        foreign = db.query(Document).filter_by(workspace_id=second).one()
        own_id = own.id
        foreign_id = foreign.id

    response = _get_document(authed_client, own_id)
    assert response.status_code == 200
    assert response.json() == {
        "document_id": own_id,
        "document_hash": "hash-a",
        "title": "a.txt",
    }
    assert _get_document(authed_client, foreign_id).status_code == 404
    assert _get_document(authed_client, own_id, org="org-b").status_code == 401
    assert _get_document(authed_client, own_id, token=TOKEN_B).status_code == 401
    assert authed_client.get(f"/api/integrations/prodigy/documents/{own_id}").status_code == 401

    with SessionLocal.begin() as db:
        db.get(Document, own_id).status = "hidden"
    assert _get_document(authed_client, own_id).status_code == 404


def test_cookie_does_not_authorize_service_endpoint(authed_client):
    response = authed_client.post(PATH, json={"question": "Кто утверждает документ?"})
    assert response.status_code == 401


def test_training_chat_uses_short_prompt_and_verified_context(monkeypatch):
    source = ChatSource(source_file="a.txt", snippet="Утверждает ответственный.")
    plan = SimpleNamespace(
        kind="generate",
        context="[Фрагмент 1 | a.txt]\nУтверждает ответственный.",
        grouped_sources=[source],
    )
    seen = []

    class FakeLLM:
        def call(self, prompt, *, max_tokens):
            seen.append((prompt, max_tokens))
            return "Утверждает ответственный."

    monkeypatch.setattr(app_services, "_prepare_chat", lambda *_: plan)
    monkeypatch.setattr(runtime, "get_llm", lambda: FakeLLM())

    result = prodigy_integration.training_chat(
        "workspace-a", ChatRequest(message="Кто утверждает документ?")
    )
    assert result.sources == [source]
    assert len(seen) == 1
    assert len(seen[0][0]) < 1000
    assert seen[0][1] == 256


def test_training_chat_does_not_cite_excluded_context(monkeypatch):
    first = ChatSource(source_file="a.txt", snippet="Первое правило.")
    second = ChatSource(source_file="b.txt", snippet="Второе правило.")
    plan = SimpleNamespace(
        kind="generate",
        context="[Фрагмент 1]\nПервое правило." + " x" * 2970
        + "\n\n---\n\n[Фрагмент 2]\nВторое правило.",
        grouped_sources=[first, second],
    )

    class FakeLLM:
        def call(self, prompt, *, max_tokens):
            return "Первое правило."

    monkeypatch.setattr(app_services, "_prepare_chat", lambda *_: plan)
    monkeypatch.setattr(runtime, "get_llm", lambda: FakeLLM())

    result = prodigy_integration.training_chat(
        "workspace-a", ChatRequest(message="Какое правило действует?")
    )
    assert result.sources == [first]


def test_token_is_bound_to_one_organization_and_workspace(authed_client, monkeypatch, indexed_kb):
    first, second = _provision(authed_client, indexed_kb)
    seen = []

    def fake_chat(workspace_id, request):
        seen.append(workspace_id)
        return ChatResponse(answer="Утверждает ответственный.", sources=[
            ChatSource(source_file="a.txt", snippet="Утверждает ответственный.")
        ])

    monkeypatch.setattr(prodigy_integration, "training_chat", fake_chat)
    response = _ask(authed_client)
    assert response.status_code == 200
    assert response.json()["sources"][0]["document_hash"] == "hash-a"
    assert seen == [first]
    assert _ask(authed_client, org="org-b").status_code == 401
    assert _ask(authed_client, course=COURSE_B).status_code == 401
    assert _ask(authed_client, token=TOKEN_B, org="org-a").status_code == 401
    assert _ask(authed_client, token="x" * 64).status_code == 401
    assert second not in seen


def test_foreign_or_missing_source_causes_refusal(authed_client, monkeypatch, indexed_kb):
    _provision(authed_client, indexed_kb)
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda workspace_id, request:
        ChatResponse(answer="Секретное правило B", sources=[
            ChatSource(source_file="b.txt", snippet="Секретное правило B")
        ]))
    response = _ask(authed_client)
    assert response.status_code == 200
    assert response.json() == {
        "answer": "В доступных документах нет подтверждённого ответа на этот вопрос.",
        "sources": [], "refused": True,
    }


@pytest.mark.parametrize("source", [
    ChatSource(source_file="a.txt", snippet="Придуманная цитата"),
    ChatSource(source_file="a.txt", snippet="Утверждает ответственный.", section="Не тот раздел"),
    ChatSource(source_file="a.txt", snippet="Утверждает ответственный.", page_start=1),
])
def test_unverified_passage_is_refused(authed_client, monkeypatch, indexed_kb, source):
    _provision(authed_client, indexed_kb)
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_:
        ChatResponse(answer="Ответ", sources=[source]))

    response = _ask(authed_client)
    assert response.status_code == 200
    assert response.json()["refused"] is True
    assert response.json()["sources"] == []


def test_missing_or_foreign_index_passage_is_refused(authed_client, monkeypatch, indexed_kb):
    first, second = _provision(authed_client, indexed_kb)
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_:
        ChatResponse(answer="Ответ", sources=[
            ChatSource(source_file="a.txt", snippet="Утверждает ответственный.")
        ]))
    with SessionLocal() as db:
        document = db.query(Document).filter_by(workspace_id=first).one()
        own_key = (first, document.id)

    own_passages = indexed_kb.passages.pop(own_key)
    assert _ask(authed_client).json()["refused"] is True

    indexed_kb.passages[(second, document.id)] = own_passages
    assert _ask(authed_client).json()["refused"] is True


def test_normalized_index_passage_is_accepted(authed_client, monkeypatch, indexed_kb):
    first, _ = _provision(authed_client, indexed_kb)
    with SessionLocal() as db:
        document = db.query(Document).filter_by(workspace_id=first).one()
        indexed_kb.passages[(first, document.id)] = [{
            "text": "[Правило]\nполо\u00ad\nжительность\nвсех корней",
            "source_file": "a.txt",
            "section": "Правило",
            "page_start": None,
            "page_end": None,
        }]
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_:
        ChatResponse(answer="Ответ", sources=[ChatSource(
            source_file="a.txt",
            section="Правило",
            snippet="[Правило] положительность всех корней",
        )]))

    response = _ask(authed_client)
    assert response.status_code == 200
    assert response.json()["refused"] is False


def test_all_sources_are_checked_before_limiting_response(authed_client, monkeypatch, indexed_kb):
    _provision(authed_client, indexed_kb)
    source = ChatSource(source_file="a.txt", snippet="Утверждает ответственный.")
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_:
        ChatResponse(answer="Ответ", sources=[source] * 11))

    response = _ask(authed_client)
    assert response.json()["refused"] is False
    assert len(response.json()["sources"]) == 10

    forged = ChatSource(source_file="a.txt", snippet="Придуманная цитата")
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_:
        ChatResponse(answer="Ответ", sources=[source] * 10 + [forged]))
    assert _ask(authed_client).json()["refused"] is True


def test_replaced_document_cannot_use_old_passage(authed_client, monkeypatch, indexed_kb):
    first, _ = _provision(authed_client, indexed_kb)
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_:
        ChatResponse(answer="Ответ", sources=[
            ChatSource(source_file="a.txt", snippet="Утверждает ответственный.")
        ]))
    with SessionLocal.begin() as db:
        document = db.query(Document).filter_by(workspace_id=first).one()
        old_key = (first, document.id)
        db.delete(document)
        db.flush()
        db.add(Document(
            workspace_id=first,
            owner_user_id=db.get(Workspace, first).owner_user_id,
            original_name="a.txt",
            stored_path="/test/a-new.txt",
            size_bytes=10,
            content_hash="hash-new",
            status="ready",
        ))

    assert old_key in indexed_kb.passages
    assert _ask(authed_client).json()["refused"] is True


def test_unverified_answer_and_hidden_document_are_refused(authed_client, monkeypatch, indexed_kb):
    _provision(authed_client, indexed_kb)
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda workspace_id, request:
        ChatResponse(answer="Правило без источника", sources=[]))
    assert _ask(authed_client).json()["refused"] is True
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda workspace_id, request:
        ChatResponse(answer="Ответ", sources=[ChatSource(source_file="a.txt", snippet="Точный фрагмент")]))
    with SessionLocal.begin() as db:
        db.query(Document).filter_by(original_name="a.txt").one().status = "hidden"
    assert _ask(authed_client).json()["refused"] is True


def test_client_cannot_choose_a_workspace_in_the_body(authed_client, indexed_kb):
    _provision(authed_client, indexed_kb)
    response = authed_client.post(PATH, json={"question": "Кто утверждает?", "workspace_id": "other"}, headers={
        "Authorization": f"Bearer {TOKEN_A}", "X-Prodigy-Organization-ID": "org-a",
        "X-Prodigy-Course-ID": COURSE_A,
    })
    assert response.status_code == 422


def test_revoked_and_expired_keys_fail_closed(authed_client, monkeypatch, indexed_kb):
    _provision(authed_client, indexed_kb)
    monkeypatch.setattr(prodigy_integration, "training_chat", lambda *_: (_ for _ in ()).throw(AssertionError("must not call")))
    with SessionLocal.begin() as db:
        record = db.query(ProdigyIntegration).filter_by(organization_id="org-a").one()
        record.is_active = False
    assert _ask(authed_client).status_code == 401
    with SessionLocal.begin() as db:
        record = db.query(ProdigyIntegration).filter_by(organization_id="org-a").one()
        record.is_active = True
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert _ask(authed_client).status_code == 401


def test_provision_rotate_and_revoke(authed_client, monkeypatch, capsys):
    workspace_id = authed_client.post("/api/courses", json={"name": "Dedicated Prodigy"}).json()["id"]
    base = ["provision", "--organization-id", "org-new", "--course-id", "course-new", "--workspace-id", workspace_id]
    monkeypatch.setattr(sys, "argv", base)
    provision()
    first = capsys.readouterr().out.strip().removeprefix("PRODIGY_SERVICE_TOKEN=")
    assert len(first) >= 43
    with SessionLocal() as db:
        record = db.query(ProdigyIntegration).filter_by(organization_id="org-new").one()
        assert record.token_hash == hashlib.sha256(first.encode()).hexdigest()

    monkeypatch.setattr(sys, "argv", base + ["--rotate"])
    provision()
    second = capsys.readouterr().out.strip().removeprefix("PRODIGY_SERVICE_TOKEN=")
    assert second != first
    with SessionLocal() as db:
        record = db.query(ProdigyIntegration).filter_by(organization_id="org-new").one()
        assert record.token_hash == hashlib.sha256(second.encode()).hexdigest()

    monkeypatch.setattr(sys, "argv", base + ["--revoke"])
    provision()
    assert capsys.readouterr().out.strip() == "Prodigy integration revoked"
    with SessionLocal() as db:
        assert not db.query(ProdigyIntegration).filter_by(organization_id="org-new").one().is_active


def test_rebind_rotates_token_and_rejects_old_course(authed_client, indexed_kb, monkeypatch, capsys):
    workspace_id, _ = _provision(authed_client, indexed_kb)
    args = ["provision", "--organization-id", "org-a", "--course-id", "new-course",
            "--workspace-id", workspace_id, "--rotate"]
    for extra in [[], ["--rebind-from-course", "wrong-course"]]:
        monkeypatch.setattr(sys, "argv", args + extra)
        with pytest.raises(SystemExit):
            provision()
        with SessionLocal() as db:
            credential = db.query(ProdigyIntegration).filter_by(organization_id="org-a").one()
            assert credential.course_id == COURSE_A
            assert credential.token_hash == hashlib.sha256(TOKEN_A.encode()).hexdigest()

    monkeypatch.setattr(sys, "argv", args + ["--rebind-from-course", COURSE_A])
    provision()
    token = capsys.readouterr().out.strip().removeprefix("PRODIGY_SERVICE_TOKEN=")
    with SessionLocal() as db:
        credential = db.query(ProdigyIntegration).filter_by(organization_id="org-a").one()
        document_id = db.query(Document).filter_by(workspace_id=workspace_id).one().id
        assert credential.course_id == "new-course"
        assert credential.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert _get_document(authed_client, document_id, token=token, course="new-course").status_code == 200
    assert _get_document(authed_client, document_id, token=token).status_code == 401
    assert _get_document(authed_client, document_id, course="new-course").status_code == 401
    assert _get_document(authed_client, document_id, token=token, org="org-b", course="new-course").status_code == 401


@pytest.mark.parametrize("flags", [[], ["--revoke"], ["--rotate"]])
def test_rebind_rejects_missing_rotation_or_identical_course(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["provision", "--organization-id", "org-a",
        "--course-id", COURSE_A, "--workspace-id", "unused", "--rebind-from-course", COURSE_A, *flags])
    with pytest.raises(SystemExit):
        provision()
