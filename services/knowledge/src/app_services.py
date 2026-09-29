"""UI-neutral service functions for Vedomo.

Every service function takes ``workspace_id`` as its first argument;
``api_app`` resolves it from ``current_user.personal_workspace.id`` via the
``get_current_workspace_id`` dependency. As of Stage 6e there is no implicit
workspace fallback anywhere in the backend — ``KnowledgeBase`` /
``summary_engine`` require ``workspace_id`` explicitly at every call site.

Two pieces stay shared on purpose:

* ``runtime.get_kb()`` returns one ``KnowledgeBase`` instance for the whole
  process — Chroma scopes data by ``workspace_id`` metadata, not by separate
  collections, so a single client is correct.
* ``_material_job_lock`` is a single global lock. It serialises background
  uploads/deletes across the instance, which is fine for the current
  single-node deployment. Per-workspace progress *state* is kept separate
  (``_material_progress_states`` keyed by ``workspace_id``) so a caller can
  never see another workspace's filename, progress percent or error.

Summary generation calls ``src.summary_engine`` strategies directly with
``workspace_id`` threaded all the way through to ``KnowledgeBase`` (Stage 4),
without going through any UI entrypoint (Stage 6d).
"""

import io
import csv
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread

from fastapi import HTTPException, status
from sqlalchemy import func, select

import config
from src import auth_service, courses, document_service, email_service, knowledge_base, quota, runtime, storage
from src import summary_engine
from src.api_models import (
    AdminStats,
    AdminUserOut,
    AdminUsersResponse,
    AssignmentForTaking,
    AssignmentListItem,
    AssignmentListResponse,
    AssignmentQuestionPublic,
    AssignmentResults,
    AttemptResult,
    BillingMeResponse,
    ChatSessionDetail,
    ChatSessionMessageOut,
    ChatSessionOut,
    ChatSessionsResponse,
    CreateAssignmentRequest,
    QuestionStat,
    ReconcileResponse,
    StudentAttemptOut,
    WorkspaceOut,
    WorkspacesResponse,
    CourseDetail,
    CourseInviteOut,
    PilotDashboard,
    PilotMemberMetric,
    CourseMemberOut,
    JoinCodeResponse,
    StudyRequest,
    StudyResponse,
    Flashcard,
    QuizQuestion,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
    MaterialActionResponse,
    MaterialInfo,
    MaterialProgressResponse,
    MaterialsResponse,
    SectionsResponse,
    SummaryExportRequest,
    SummaryRequest,
    SummaryResponse,
    SystemStatus,
)
from src.db import SessionLocal
from src.db_models import (
    Assignment,
    AssignmentAttempt,
    ChatMessage as ChatMessageRow,
    ChatSession,
    CourseInvitation,
    Document,
    User,
    Workspace,
    WorkspaceMember,
    UsageEvent,
)
from src.chat_utils import (
    get_last_qa,
    history_to_context,
    is_correction,
    is_followup,
    is_greeting,
    is_provider_filter,
    is_refusal,
    strip_refusal_prefix,
    strip_internal_fragment_references,
)
from src.answer_guard import guard_answer
from src.diagnostics import (
    DiagnosticLLM,
    finish_trace,
    format_last_trace,
    get_last_trace,
    start_trace,
)
from src.export_utils import export_text_to_docx
from src.progress_store import InMemoryProgressStore
from src.model_scheduler import scheduler as model_scheduler

logger = logging.getLogger("vedomo.app_services")


ALL_FILES_LABELS = {"Все файлы", "Все материалы", "all", ""}

# Material progress + cooperative-cancel state lives behind a small store seam
# (Phase A-prep): today an in-process implementation - correct for the single
# uvicorn worker - and the only thing to swap for a shared backend (Redis) when
# going multi-instance. ``_material_job_lock`` is separate: it serializes the
# single-process indexing thread, not cross-instance state.
_progress_store = InMemoryProgressStore()
_material_job_lock = Lock()
_material_job_thread = None


def _request_cancel(workspace_id: str) -> None:
    _progress_store.request_cancel(workspace_id)


def _is_cancel_requested(workspace_id: str) -> bool:
    return _progress_store.is_cancel_requested(workspace_id)


def _clear_cancel(workspace_id: str) -> None:
    _progress_store.clear_cancel(workspace_id)


def _normalize_selected_file(selected_file):
    if selected_file is None:
        return "Все файлы"

    value = str(selected_file).strip()
    return "Все файлы" if value in ALL_FILES_LABELS else value


def _format_provider_filter_message():
    """Честный текст вместо отписки фильтра: см. chat_utils.is_provider_filter."""
    return (
        "Языковая модель отказалась отвечать по этому фрагменту - сработал "
        "контентный фильтр провайдера. Это не значит, что ответа нет в "
        "документе.\n\nПопробуйте:\n"
        "• сузить вопрос, указав нужный раздел или таблицу;\n"
        "• переформулировать вопрос;\n"
        "• при повторении обратитесь к администратору - возможно, стоит "
        "переключить модель."
    )


def _format_no_information_message(selected_file):
    """User-facing fallback when chat retrieval returned nothing.

    Lives in the service layer (Stage 6d) so chat_service stays UI-agnostic.
    """
    if selected_file and selected_file != "Все файлы":
        return (
            "Информация по данному вопросу не найдена в выбранном материале.\n\n"
            "Попробуйте:\n"
            "• выбрать другой файл;\n"
            "• выбрать «Все файлы»;\n"
            "• переформулировать вопрос."
        )

    return (
        "Информация по данному вопросу не найдена в загруженных материалах.\n\n"
        "Попробуйте:\n"
        "• загрузить дополнительные материалы;\n"
        "• выбрать другой файл;\n"
        "• переформулировать вопрос."
    )


def _format_refusal_answer(answer, selected_file):
    """Keep the reason for declining, especially limits on medical advice.

    A generic search hint must not overwrite a useful explanation already
    shown in the stream. Bare protocol markers still get a readable fallback.
    """
    explanation = strip_refusal_prefix((answer or "").strip())
    if explanation.lower().strip(" .!:;\n\r") in ("", "нет информации", "no information"):
        return _format_no_information_message(selected_file)
    # A marker may be the subject of the same sentence: removing it from
    # "НЕТ ИНФОРМАЦИИ о гарантии" would leave a sentence fragment.
    original = (answer or "").strip()
    if explanation != original:
        marker_tail = re.match(
            r"^(?:нет информации|no information)\s*([,;:])",
            original,
            flags=re.IGNORECASE,
        )
        if marker_tail:
            return _format_no_information_message(selected_file)
        # In "НЕТ ИНФОРМАЦИИ о гарантии" the marker is the grammatical
        # subject.  Keep the original sentence rather than returning "о...".
        if explanation[:1].islower():
            return original
    return explanation


def _build_answer_summary(answer):
    text = " ".join(str(answer or "").split())
    if not text:
        return ""

    for separator in [". ", "! ", "? "]:
        if separator in text:
            first_part = text.split(separator, 1)[0].strip()
            if first_part:
                return first_part[:220]

    return text[:220]


def _build_confidence_label(sources, trace_status="ok"):
    if trace_status != "ok":
        return "low"

    source_count = len(sources or [])
    if source_count >= 3:
        return "high"
    if source_count >= 1:
        return "medium"
    return "low"


def _build_followup_suggestions(message, answer_mode, has_sources):
    if answer_mode == "Только цитаты":
        suggestions = [
            "Теперь объясни это простыми словами.",
            "Собери краткий вывод по этим цитатам.",
            "Покажи, какой раздел документа важнее всего.",
        ]
    elif answer_mode == "Кратко":
        suggestions = [
            "Теперь раскрой это подробнее.",
            "Сравни это с близким понятием.",
            "Покажи подтверждающие цитаты из текста.",
        ]
    elif answer_mode == "Подробно":
        suggestions = [
            "Сожми это в 3-4 ключевых пункта.",
            "Какая здесь главная мысль?",
            "Покажи, на какие разделы ты опирался.",
        ]
    else:
        suggestions = [
            "Объясни это еще проще.",
            "Сравни это с похожим понятием.",
            "Приведи подтверждающие цитаты из материала.",
        ]

    if not has_sources:
        suggestions[-1] = "Попробуй ответить по-другому или сузить вопрос."

    cleaned = []
    normalized_message = str(message or "").strip().lower()
    for item in suggestions:
        if item.strip().lower() != normalized_message and item not in cleaned:
            cleaned.append(item)
    return cleaned[:3]


def get_system_status(workspace_id: str):
    kb = runtime.get_kb()
    stats = kb.stats(workspace_id=workspace_id)

    mode = config.LLM_MODE
    model = config.API_MODEL if mode == "api" else config.OLLAMA_MODEL

    return SystemStatus(
        llm_mode=mode,
        model=model,
        embedding_model=config.EMBEDDING_MODEL,
        reranker_model=config.RERANKER_MODEL,
        chunk_size=config.CHUNK_SIZE,
        hyde_enabled=config.USE_HYDE,
        total_books=stats.get("total_books", 0),
        total_chunks=stats.get("total_chunks", 0),
    )


def _ready_material_quality(sections_count, chunk_count):
    """Quality badge for a Document whose status is already ``ready``.

    Stage 3d invariant: a ready Document is **never** hidden from the
    materials list — ``Document.status`` is the source of truth for
    visibility. The badge below is purely informational: it tells the user
    whether the material has good structure, is plain text, or is
    degraded (indexed but produced no chunks).
    """
    if sections_count >= 3:
        return "ready", "Материал хорошо подходит для поиска, конспектов и ссылок на источники."
    if sections_count > 0:
        return "ready", "Структура короткая, но материал уже пригоден для поиска и опоры на разделы."
    if chunk_count > 0:
        return "plain_text", "Сплошной текст без явных разделов: подходит для чтения и диалога, слабее для навигации."
    # Document is marked ready but the indexer produced no chunks. Still
    # surfaced so the user can re-run /reindex from the UI; never hidden.
    return "weak", "Материал проиндексирован, но содержательных фрагментов выделить не удалось. Попробуйте переиндексировать."


def list_materials(workspace_id: str):
    """Stage 3c: enumerate materials from the ``Document`` table.

    Visibility is owned entirely by ``Document.status``. The KB profile is
    used only to set the informational quality label/sections_count — it
    never decides whether a row is shown. This protects against KB lookup
    misses (e.g. a stale ``source_file`` mismatch) silently hiding an
    indexed document from the user.
    """
    kb = runtime.get_kb()
    db = SessionLocal()
    try:
        documents = document_service.list_documents(db, workspace_id)
    finally:
        db.close()

    materials = []
    for doc in documents:
        if doc.status == document_service.STATUS_ERROR:
            materials.append(
                MaterialInfo(
                    id=doc.id,
                    name=doc.original_name,
                    sections_count=0,
                    quality_label="error",
                    quality_reason=doc.error_message
                    or "Материал не удалось проиндексировать. Попробуйте загрузить снова.",
                    status=doc.status,
                    ocr_used=doc.ocr_used,
                )
            )
            continue

        if doc.status == document_service.STATUS_PROCESSING:
            materials.append(
                MaterialInfo(
                    id=doc.id,
                    name=doc.original_name,
                    sections_count=0,
                    quality_label="processing",
                    quality_reason="Идёт индексация материала. Обновите список через минуту.",
                    status=doc.status,
                    ocr_used=doc.ocr_used,
                )
            )
            continue

        # Status is READY: surface the document, then ask the KB for a
        # quality badge. KB lookup failures degrade the badge, not the row.
        try:
            profile = kb.get_file_profile(doc.original_name, workspace_id=workspace_id)
        except Exception:
            profile = {}
        sections_count = int((profile or {}).get("sections_count", 0) or 0)
        chunk_count = int((profile or {}).get("chunk_count", 0) or 0)
        quality_label, quality_reason = _ready_material_quality(sections_count, chunk_count)

        materials.append(
            MaterialInfo(
                id=doc.id,
                name=doc.original_name,
                sections_count=sections_count,
                quality_label=quality_label,
                quality_reason=quality_reason,
                status=doc.status,
                ocr_used=doc.ocr_used,
            )
        )

    return MaterialsResponse(materials=materials)


def list_sections(workspace_id: str, file_filter: str = "all"):
    kb = runtime.get_kb()
    normalized_filter = _normalize_selected_file(file_filter)

    if normalized_filter == "Все файлы":
        sections = kb.get_available_sections(workspace_id=workspace_id)
    else:
        sections = kb.get_sections_for_file(normalized_filter, workspace_id=workspace_id)

    return SectionsResponse(sections=sections)


# ---------------------------------------------------------------------------
# Material upload / delete / reindex — per-workspace progress
# ---------------------------------------------------------------------------


def _get_workspace_progress_snapshot(workspace_id: str) -> dict:
    """Return a copy of ``workspace_id``'s progress state (idle if missing)."""
    return _progress_store.get(workspace_id)


def _set_material_progress(workspace_id: str, **updates) -> None:
    _progress_store.set(workspace_id, **updates)


def _start_material_progress(workspace_id, operation, message="", current_file=""):
    _set_material_progress(
        workspace_id,
        active=True,
        operation=operation,
        phase="starting",
        message=message,
        progress=1,
        current_file=current_file,
        error="",
    )


def _queue_material_progress(workspace_id, operation, message="", current_file=""):
    _set_material_progress(
        workspace_id,
        active=True,
        operation=operation,
        phase="queued",
        message=message,
        progress=0,
        current_file=current_file,
        error="",
    )


def _update_material_progress(workspace_id, phase="", progress=None, message=None, current_file=None):
    payload = {}
    if phase is not None:
        payload["phase"] = phase
    if progress is not None:
        payload["progress"] = max(0, min(int(progress), 100))
    if message is not None:
        payload["message"] = message
    if current_file is not None:
        payload["current_file"] = current_file
    _set_material_progress(workspace_id, **payload)


def _finish_material_progress(workspace_id, message="", current_file=""):
    _set_material_progress(
        workspace_id,
        active=False,
        operation="idle",
        phase="done",
        message=message,
        progress=100,
        current_file=current_file,
        error="",
    )


def _fail_material_progress(workspace_id, message="", current_file=""):
    _set_material_progress(
        workspace_id,
        active=False,
        operation="idle",
        phase="error",
        message=message,
        progress=100,
        current_file=current_file,
        error=message,
    )


def _cancel_material_progress(workspace_id, message="", current_file=""):
    _set_material_progress(
        workspace_id,
        active=False,
        operation="idle",
        phase="cancelled",
        message=message,
        progress=100,
        current_file=current_file,
        error="",
    )


def get_material_progress(workspace_id: str) -> MaterialProgressResponse:
    snapshot = dict(_get_workspace_progress_snapshot(workspace_id))
    # Штамп пространства ставим здесь, а не в хранилище: в хранилище это ключ,
    # и для отсутствующей записи он бы потерялся - а именно пустой снимок и
    # вводил фронт в заблуждение (см. MaterialProgressResponse.workspace_id).
    snapshot["workspace_id"] = workspace_id
    return MaterialProgressResponse(**snapshot)


def reset_material_progress_for_tests():
    _progress_store.reset()


def _launch_material_job(workspace_id, operation, message, target, material_name=""):
    global _material_job_thread
    from src.operation_guard import operations

    with _material_job_lock:
        current = get_material_progress(workspace_id)
        if current.active:
            return MaterialActionResponse(
                ok=False,
                message="Сейчас уже выполняется другая операция с библиотекой. Дождитесь завершения.",
                material_name=material_name,
            )

        # Reserve before queuing: rejected jobs must not appear active.
        if not operations.enter(continuation=True):
            raise HTTPException(status_code=503, detail="cleanup_in_progress")

        # Clear any stale cancel flag so a new job doesn't inherit a cancel
        # request left over from a previous operation.
        _clear_cancel(workspace_id)

        _queue_material_progress(
            workspace_id,
            operation=operation,
            message=message,
            current_file=material_name,
        )

        def runner():
            try:
                target()
            except Exception:
                # Unexpected failure - handled errors (bad format, scan, zip
                # bomb) come back via doc.error_message, not here. Log the real
                # exception server-side; show the user clean copy, not a raw
                # Python error (Stage 42).
                logger.exception("material job failed for workspace %s", workspace_id)
                _fail_material_progress(
                    workspace_id,
                    "Не удалось обработать файл. Попробуйте другой файл или повторите позже.",
                    current_file=material_name,
                )

            finally:
                operations.leave()

        _material_job_thread = Thread(target=runner, daemon=True)
        try:
            _material_job_thread.start()
        except Exception:
            operations.leave()
            _fail_material_progress(workspace_id, "Не удалось запустить обработку.")
            raise

    return MaterialActionResponse(
        ok=True,
        message=message,
        material_name=material_name,
    )


def _normalize_material_name(file_name):
    return os.path.basename(str(file_name or "")).strip()


def upload_material_service(workspace_id: str, user_id: str, file_name, content):
    """Persist + index a freshly uploaded material via :mod:`document_service`.

    Validation (name present, extension allowed, size below
    ``MAX_UPLOAD_BYTES``) happens here so the user gets a synchronous error
    response without ever creating a Document row for clearly-rejected
    uploads.
    """
    normalized_name = _normalize_material_name(file_name)
    if not normalized_name:
        return MaterialActionResponse(ok=False, message="Не выбран файл для загрузки.")

    lower_name = normalized_name.lower()
    if not any(lower_name.endswith(ext) for ext in config.SUPPORTED_FORMATS):
        return MaterialActionResponse(
            ok=False,
            message=f"Формат не поддерживается. Разрешены: {', '.join(config.SUPPORTED_FORMATS)}",
            material_name=normalized_name,
        )

    max_bytes = getattr(config, "MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
    if len(content) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        return MaterialActionResponse(
            ok=False,
            message=f"Файл слишком большой. Максимальный размер: {limit_mb} МБ.",
            material_name=normalized_name,
        )

    _start_material_progress(
        workspace_id,
        operation="upload",
        message=f"Готовлю загрузку {normalized_name}",
        current_file=normalized_name,
    )

    db = SessionLocal()
    try:
        try:
            doc = document_service.create_document(
                db,
                workspace_id=workspace_id,
                owner_user_id=user_id,
                original_name=normalized_name,
                content=content,
                cancel_check=lambda: _is_cancel_requested(workspace_id),
                # Forward add_book's phase/percent reports to the polled
                # workspace progress so the bar moves smoothly instead of
                # jumping 1% -> 100%.
                progress_callback=lambda **payload: _update_material_progress(
                    workspace_id, **payload
                ),
            )
        except knowledge_base.IndexingCancelled:
            # Cooperative cancel (Stage 9a): create_document already rolled back
            # the partial document. Surface it as a cancelled (not failed) op.
            _clear_cancel(workspace_id)
            _cancel_material_progress(
                workspace_id,
                message=f"Загрузка {normalized_name} отменена.",
                current_file=normalized_name,
            )
            return MaterialActionResponse(
                ok=False,
                message=f"Загрузка {normalized_name} отменена.",
                material_name=normalized_name,
            )
        except Exception:
            # Unexpected failure: log detail, surface clean copy (Stage 42).
            logger.exception("upload failed for workspace %s", workspace_id)
            _fail_material_progress(
                workspace_id,
                "Не удалось обработать файл. Попробуйте другой файл или повторите позже.",
                current_file=normalized_name,
            )
            raise
    finally:
        db.close()

    if doc.status == document_service.STATUS_ERROR:
        message = doc.error_message or f"{normalized_name}: индексация не удалась."
        _fail_material_progress(workspace_id, message, current_file=normalized_name)
        return MaterialActionResponse(
            ok=False,
            message=message,
            material_name=normalized_name,
        )

    _finish_material_progress(
        workspace_id,
        message=f"{normalized_name} готов к поиску и конспектам",
        current_file=normalized_name,
    )
    # Meter the successful upload (Stage 12). The material-count quota itself is
    # checked at the route before the file is even read; this is the ledger.
    quota.record_usage(
        workspace_id,
        quota.ACTION_UPLOAD,
        user_id=user_id,
        meta={"size_bytes": len(content), "name": normalized_name},
    )
    return MaterialActionResponse(
        ok=True,
        message=f"✅ {normalized_name}: материал загружен и проиндексирован.",
        material_name=normalized_name,
    )


def start_upload_material_service(workspace_id: str, user_id: str, file_name, content):
    normalized_name = _normalize_material_name(file_name)
    return _launch_material_job(
        workspace_id,
        operation="upload",
        message=f"Запустил загрузку и индексацию {normalized_name}",
        material_name=normalized_name,
        target=lambda: upload_material_service(workspace_id, user_id, file_name, content),
    )


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _safe_material_filename(title: str, max_len: int = 80) -> str:
    """Turn a page title into a safe material filename (no path/illegal chars)."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", title or "").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].rstrip() or "Веб-страница"


_MAX_FETCH_BYTES = 5 * 1024 * 1024  # web pages are text; cap the download to avoid OOM
_MAX_REDIRECTS = 5


def _assert_public_host(url: str) -> None:
    """Raise ``ValueError`` if **any** address the host resolves to (IPv4 or
    IPv6) is private / loopback / link-local / reserved / multicast / unspecified
    (SSRF guard). Uses ``getaddrinfo`` so an internal IPv6-only host can't slip
    past an IPv4-only lookup."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if not host:
        raise ValueError("Некорректная ссылка.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("Не удалось разрешить адрес сайта.")
    for info in infos:
        ip = info[4][0].split("%", 1)[0]  # strip any IPv6 scope id (fe80::1%eth0)
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError("Не удалось разрешить адрес сайта.")
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise ValueError("Эта ссылка ведёт во внутреннюю сеть - так нельзя.")


def _safe_fetch(url: str) -> bytes:
    """GET a URL with the SSRF guard re-run on **every redirect hop** (so a
    redirect can't smuggle us to an internal host) and a hard body-size cap.
    Returns raw HTML bytes; raises ``ValueError`` on a bad/blocked/oversized one."""
    import requests
    from urllib.parse import urljoin

    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _assert_public_host(current)  # check each hop, not just the first
        try:
            resp = requests.get(
                current,
                timeout=15,
                allow_redirects=False,  # follow manually so we can re-check each hop
                stream=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Nastavnik/1.0)"},
            )
        except requests.RequestException as exc:
            raise ValueError(f"Не удалось загрузить страницу: {exc}") from exc

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise ValueError("Битый редирект на странице.")
            current = urljoin(current, location)
            continue

        if resp.status_code != 200:
            resp.close()
            raise ValueError(f"Страница недоступна (код {resp.status_code}).")

        ctype = resp.headers.get("Content-Type", "").lower()
        if "html" not in ctype and "text" not in ctype:
            resp.close()
            raise ValueError("По ссылке не веб-страница (нужен HTML).")

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(8192):
            total += len(chunk)
            if total > _MAX_FETCH_BYTES:
                resp.close()
                raise ValueError("Страница слишком большая.")
            chunks.append(chunk)
        resp.close()
        return b"".join(chunks)

    raise ValueError("Слишком много редиректов.")


def _fetch_url_text(url: str) -> tuple[str, str]:
    """Fetch a web page and extract its readable text → ``(title, text)``.

    Raises ``ValueError`` with a user-facing message on a bad URL, an internal-
    network target (SSRF guard, re-checked on every redirect), an oversized
    response, a failed fetch, or too little content."""
    from bs4 import BeautifulSoup

    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise ValueError("Ссылка должна начинаться с http:// или https://.")

    html = _safe_fetch(url)  # BeautifulSoup handles encoding detection from bytes

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "form"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    main = soup.find("article") or soup.find("main") or soup.body or soup
    raw = main.get_text(separator="\n")
    text = "\n".join(line.strip() for line in raw.splitlines() if line.strip())

    if len(text) < 200:
        raise ValueError("Не удалось извлечь текст со страницы (слишком мало содержимого).")
    return (title or url), text


_YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def _youtube_video_id(url: str) -> str | None:
    match = _YT_RE.search(url or "")
    return match.group(1) if match else None


def _youtube_title(url: str) -> str:
    """Best-effort video title via YouTube's keyless oembed endpoint."""
    import requests

    try:
        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=10,
        )
        if resp.ok:
            return (resp.json().get("title") or "").strip()
    except Exception:  # noqa: BLE001 - the title is optional, never fatal
        return ""
    return ""


def _fetch_youtube_text(url: str) -> tuple[str, str]:
    """Fetch a YouTube video's captions → ``(title, text)``. Raises ``ValueError``
    with a user-facing message when there's no usable transcript."""
    video_id = _youtube_video_id(url)
    if not video_id:
        raise ValueError("Не похоже на ссылку YouTube.")

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=["ru", "en"])
    except ImportError as exc:  # dependency missing - shouldn't happen in prod
        raise ValueError("Импорт субтитров YouTube недоступен на сервере.") from exc
    except Exception as exc:  # noqa: BLE001 - the lib raises many error subclasses
        raise ValueError(
            "Не удалось получить субтитры: у видео их нет, они закрыты или недоступны."
        ) from exc

    text = "\n".join((seg.get("text") or "").strip() for seg in segments)
    text = "\n".join(line for line in text.splitlines() if line)
    if len(text) < 100:
        raise ValueError("Субтитры пустые или слишком короткие.")

    title = _youtube_title(url) or f"YouTube {video_id}"
    return title, text


def ingest_url_service(workspace_id: str, user_id: str, url: str) -> MaterialActionResponse:
    """Ingest a link as a ``.txt`` material via the normal upload pipeline
    (Stage 56) - background-indexed like an upload. A YouTube link is auto-routed
    to its captions; anything else is fetched and scraped as a web page."""
    url = (url or "").strip()
    try:
        if _youtube_video_id(url):
            title, text = _fetch_youtube_text(url)
        else:
            title, text = _fetch_url_text(url)
    except ValueError as exc:
        return MaterialActionResponse(ok=False, message=str(exc))

    file_name = f"{_safe_material_filename(title)}.txt"
    return start_upload_material_service(workspace_id, user_id, file_name, text.encode("utf-8"))


def ingest_image_service(
    workspace_id: str, user_id: str, file_name, content: bytes, mime_type: str = "image/png"
) -> MaterialActionResponse:
    """Photo → Vision transcription → ``.txt`` material (Stage 56). Better than
    plain OCR for formulas/diagrams. The blocking vision call runs in the caller's
    threadpool; indexing then goes through the normal upload pipeline."""
    # Validate BEFORE the paid vision call: reject non-images and oversized files
    # so a bad upload can't burn an API call or pump a huge payload to the model.
    if not (mime_type or "").lower().startswith("image/"):
        return MaterialActionResponse(ok=False, message="Это не изображение - выберите фото (PNG/JPEG).")
    max_bytes = getattr(config, "VISION_MAX_IMAGE_BYTES", 12 * 1024 * 1024)
    if len(content) > max_bytes:
        return MaterialActionResponse(
            ok=False, message=f"Изображение слишком большое (макс {max_bytes // (1024 * 1024)} МБ)."
        )

    from src import llm_engine

    try:
        text = llm_engine.transcribe_image(content, mime_type or "image/png")
    except Exception as exc:  # noqa: BLE001 - surface any vision/API failure to the user
        return MaterialActionResponse(ok=False, message=f"Не удалось распознать фото: {exc}")

    if len((text or "").strip()) < 20:
        return MaterialActionResponse(ok=False, message="На фото не нашлось распознаваемого текста.")

    base = _safe_material_filename(os.path.splitext(file_name or "Фото")[0]) or "Фото"
    out_name = f"{base} (фото).txt"
    return start_upload_material_service(workspace_id, user_id, out_name, text.encode("utf-8"))


def cancel_material_service(workspace_id: str) -> MaterialActionResponse:
    """Request cooperative cancellation of the workspace's in-flight job.

    The running indexing loop checks the flag between batches and aborts,
    rolling back any partial data. No active operation → nothing to cancel.
    """
    if not get_material_progress(workspace_id).active:
        return MaterialActionResponse(ok=False, message="Сейчас нечего отменять.")

    _request_cancel(workspace_id)
    return MaterialActionResponse(ok=True, message="Отмена запрошена. Останавливаю операцию…")


def delete_material_service(workspace_id: str, file_name):
    """Delete a material: drop its Document row, KB chunks and the on-disk file.

    Synchronous and progress-free (Stage 55): deletion is fast, so it runs inline
    and returns the result. It is intentionally NOT wrapped in the
    progress-tracking machinery - routing it through the upload-style progress
    job made the SPA poll a job that had already finished, leaving the progress
    panel stuck at "100%" until F5.
    """
    normalized_name = _normalize_material_name(file_name)
    if not normalized_name:
        return MaterialActionResponse(ok=False, message="Материал не указан.")

    db = SessionLocal()
    try:
        doc = document_service.find_document_by_name(db, workspace_id, normalized_name)
        if doc is None:
            return MaterialActionResponse(
                ok=False,
                message=f"{normalized_name} не найден в библиотеке.",
                material_name=normalized_name,
            )
        document_service.delete_document(db, workspace_id, doc.id)
    finally:
        db.close()

    return MaterialActionResponse(
        ok=True,
        message=f"🗑️ {normalized_name}: удалено. Файл убран из библиотеки.",
        material_name=normalized_name,
    )


def start_delete_material_service(workspace_id: str, file_name):
    """Delete a material **synchronously** (Stage 55).

    Runs inline and returns the result directly - NOT through
    ``_launch_material_job`` (the background-thread + progress-poll machinery
    built for slow uploads/indexing). That path made deletion feel slow AND left
    the client's progress panel stuck until F5. Inline here + a plain list
    refresh on the client fixes both #2 (slow) and #7 (stuck progress).
    """
    return delete_material_service(workspace_id, file_name)


def reindex_material_service(workspace_id: str, file_name=None):
    normalized_name = _normalize_material_name(file_name)

    # Single-file reindex: route through document_service so the existing
    # Document row is reused (and Chroma rebuilt by document_id).
    if normalized_name:
        _start_material_progress(
            workspace_id,
            operation="reindex_material",
            message=f"Переиндексирую {normalized_name}",
            current_file=normalized_name,
        )

        db = SessionLocal()
        try:
            doc = document_service.find_document_by_name(db, workspace_id, normalized_name)
            if doc is None:
                _fail_material_progress(
                    workspace_id,
                    f"{normalized_name} не найден в библиотеке.",
                    current_file=normalized_name,
                )
                return MaterialActionResponse(
                    ok=False,
                    message=f"{normalized_name} не найден в библиотеке.",
                    material_name=normalized_name,
                )
            refreshed = document_service.reindex_document(db, workspace_id, doc.id)
        finally:
            db.close()

        if refreshed is None or refreshed.status == document_service.STATUS_ERROR:
            message = (
                (refreshed.error_message if refreshed else "")
                or f"{normalized_name}: переиндексация не удалась."
            )
            _fail_material_progress(workspace_id, message, current_file=normalized_name)
            return MaterialActionResponse(
                ok=False,
                message=message,
                material_name=normalized_name,
            )

        _finish_material_progress(
            workspace_id,
            message=f"{normalized_name} переиндексирован",
            current_file=normalized_name,
        )
        return MaterialActionResponse(
            ok=True,
            message=f"✅ {normalized_name}: переиндексировано.",
            material_name=normalized_name,
        )

    # Full-library reindex: walk every Document, rebuild its chunks. The KB
    # is wiped per-workspace once at the start so orphaned chunks (left over
    # by a previous bug) are also cleared.
    kb = runtime.get_kb()
    _start_material_progress(
        workspace_id,
        operation="reindex_library",
        message="Полностью пересобираю библиотеку",
    )
    kb.clear(workspace_id=workspace_id)
    _update_material_progress(
        workspace_id,
        phase="cleanup",
        progress=5,
        message="Очищаю текущий индекс",
    )

    db = SessionLocal()
    try:
        documents = document_service.list_documents(db, workspace_id)
        for doc in documents:
            document_service.reindex_document(db, workspace_id, doc.id)
    finally:
        db.close()

    _finish_material_progress(workspace_id, message="Библиотека полностью переиндексирована")
    return MaterialActionResponse(
        ok=True,
        message=f"📚 Переиндексировано документов: {len(documents)}",
    )


def start_reindex_material_service(workspace_id: str, file_name=None):
    normalized_name = _normalize_material_name(file_name)
    operation = "reindex_material" if normalized_name else "reindex_library"
    title = (
        f"Запустил переиндексацию {normalized_name}"
        if normalized_name
        else "Запустил полную пересборку библиотеки"
    )
    return _launch_material_job(
        workspace_id,
        operation=operation,
        message=title,
        material_name=normalized_name,
        target=lambda: reindex_material_service(workspace_id, file_name),
    )


def list_user_workspaces_service(db, user) -> WorkspacesResponse:
    """The caller's spaces for the course switcher (Stage 15-3): the personal
    workspace plus every course they own or are a member of, each tagged with
    the caller's role. ``active_workspace_id`` is the user's selection, but it
    falls back to personal if the selection is no longer reachable (course
    deleted / membership revoked)."""
    personal = auth_service.get_personal_workspace(db, user)

    spaces: dict[str, tuple[Workspace, str]] = {}
    # Owned workspaces (personal + any courses created) → owner.
    for ws in db.scalars(select(Workspace).where(Workspace.owner_user_id == user.id)):
        spaces[ws.id] = (ws, courses.ROLE_OWNER)
    # Courses joined as teacher/student.
    for member in db.scalars(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)):
        if member.workspace_id in spaces:
            continue
        ws = db.get(Workspace, member.workspace_id)
        if ws is not None:
            spaces[ws.id] = (ws, member.role)

    # An archived course is out of the active rotation: a stale selection
    # pointing at one (or at a deleted/left course) falls back to personal.
    selected = spaces.get(user.active_workspace_id)
    active_id = (
        user.active_workspace_id
        if selected is not None and not selected[0].is_archived
        else personal.id
    )

    def _sort_key(item: tuple[Workspace, str]):
        ws, _role = item
        return (0 if ws.id == personal.id else 1, ws.name.lower())

    return WorkspacesResponse(
        workspaces=[
            WorkspaceOut(
                id=ws.id,
                name=ws.name,
                kind=ws.kind,
                role=role,
                is_active=(ws.id == active_id),
                is_archived=ws.is_archived,
            )
            for ws, role in sorted(spaces.values(), key=_sort_key)
        ],
        active_workspace_id=active_id,
    )


def activate_workspace_service(db, user, workspace_id: str) -> WorkspaceOut:
    """Set the caller's active workspace (Stage 15-3).

    The client names a target id, but it's honored only after **server-side
    membership validation** — the ``workspace_id``-from-auth invariant holds.
    Unknown workspace → 404; not a member → 403. The user row is re-fetched on
    ``db`` so the write persists regardless of which session ``user`` came from.
    """
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace_not_found")
    role = courses.role_in_workspace(db, user.id, workspace)
    if role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if workspace.is_archived:
        # Archived courses are out of the active rotation; restore first.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="course_archived")

    db_user = db.get(User, user.id)
    db_user.active_workspace_id = workspace.id
    db.commit()
    return WorkspaceOut(
        id=workspace.id, name=workspace.name, kind=workspace.kind, role=role, is_active=True
    )


# --- Courses (Stage 15-4): create / join / manage --------------------------
def _unique_join_code(db) -> str:
    """A join code not currently used by any workspace (retry a few times; the
    alphabet is large enough that a residual collision is astronomically rare)."""
    for _ in range(10):
        code = courses.generate_join_code()
        if db.scalar(select(Workspace).where(Workspace.join_code == code)) is None:
            return code
    return courses.generate_join_code()


def _load_course_for_action(db, user, course_id: str, action: str) -> Workspace:
    """Load a course and enforce ``can(user, action, course)``. Unknown course →
    404; insufficient role → 403."""
    course = db.get(Workspace, course_id)
    if course is None or course.kind != courses.WORKSPACE_KIND_COURSE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="course_not_found")
    if not courses.can(db, user, action, course):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return course


def _load_owned_course(db, user, course_id: str) -> Workspace:
    """Load a course for an **owner-only lifecycle op** (archive/restore/copy/
    delete). Checks ``owner_user_id`` directly — *not* ``can()`` — so it works
    even on an archived course (where ``can()`` denies everything but view).
    Unknown course → 404; not the owner → 403."""
    course = db.get(Workspace, course_id)
    if course is None or course.kind != courses.WORKSPACE_KIND_COURSE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="course_not_found")
    if course.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return course


def archive_course_service(db, user, course_id: str, archived: bool) -> dict:
    """Freeze (or restore) a course — owner only (Stage 22). On archive, anyone
    currently in it (owner included) is bumped back to their personal space."""
    course = _load_owned_course(db, user, course_id)
    course.is_archived = bool(archived)
    if archived:
        db.query(User).filter(User.active_workspace_id == course_id).update(
            {User.active_workspace_id: None}, synchronize_session=False
        )
    db.commit()
    return {"status": "archived" if archived else "active"}


def _copy_course_name(name: str) -> str:
    return f"{(name or 'Курс').strip()} (новый семестр)"[:120]


def copy_course_service(db, user, course_id: str) -> WorkspaceOut:
    """Clone a course into a fresh one for a new semester — owner only (Stage 22).

    Copies **materials** (Document rows + files + KB vectors, reusing embeddings)
    and **assignments** (as unpublished drafts). Does NOT copy members, attempts
    or chats. The new course gets a new join code and becomes the caller's active
    workspace. Fully isolated by its own ``workspace_id``.
    """
    source = _load_owned_course(db, user, course_id)

    new_course = Workspace(
        name=_copy_course_name(source.name),
        owner_user_id=user.id,
        kind=courses.WORKSPACE_KIND_COURSE,
        join_code=_unique_join_code(db),
        join_enabled=True,
    )
    db.add(new_course)
    db.flush()
    db.add(WorkspaceMember(workspace_id=new_course.id, user_id=user.id, role=courses.ROLE_OWNER))

    # Materials: copy ready Documents + their files; build the id/path maps the
    # KB needs to clone vectors into the new workspace.
    doc_id_map: dict[str, str] = {}
    path_map: dict[str, str] = {}
    os.makedirs(storage.workspace_docs_dir(new_course.id), exist_ok=True)
    for doc in db.scalars(
        select(Document).where(Document.workspace_id == source.id, Document.status == "ready")
    ):
        new_doc_id = str(uuid.uuid4())
        new_path = storage.document_stored_path(new_course.id, new_doc_id, doc.original_name)
        try:
            source_path = storage.resolve_stored_path(doc.stored_path, doc.workspace_id)
            if source_path and os.path.isfile(source_path):
                shutil.copy2(source_path, new_path)
        except Exception:
            logger.warning("copy_course: file copy failed for %s", doc.id, exc_info=True)
            continue  # skip a material we can't copy rather than half-create it
        db.add(
            Document(
                id=new_doc_id,
                workspace_id=new_course.id,
                owner_user_id=user.id,
                original_name=doc.original_name,
                stored_path=new_path,
                size_bytes=doc.size_bytes,
                content_hash=doc.content_hash,
                status="ready",
                sections_count=doc.sections_count,
            )
        )
        doc_id_map[doc.id] = new_doc_id
        path_map[doc.id] = new_path
    db.flush()

    # Vectors: clone chunks (no re-embed). Best-effort — a failure leaves the
    # course usable (materials present), reconciler/reindex can backfill.
    if doc_id_map:
        try:
            runtime.get_kb().copy_documents(
                src_workspace_id=source.id,
                dst_workspace_id=new_course.id,
                doc_id_map=doc_id_map,
                path_map=path_map,
            )
        except Exception:
            logger.warning("copy_course: KB copy failed %s -> %s", source.id, new_course.id, exc_info=True)

    # Assignments: copy as unpublished drafts (no attempts).
    for a in db.scalars(select(Assignment).where(Assignment.workspace_id == source.id)):
        db.add(
            Assignment(
                workspace_id=new_course.id,
                created_by_user_id=user.id,
                title=a.title,
                source_label=a.source_label,
                questions=list(a.questions or []),
                is_published=False,
            )
        )

    db_user = db.get(User, user.id)
    db_user.active_workspace_id = new_course.id  # drop the owner into the new course
    db.commit()
    return WorkspaceOut(
        id=new_course.id, name=new_course.name, kind=new_course.kind, role=courses.ROLE_OWNER, is_active=True
    )


def create_course_service(db, user, name: str) -> WorkspaceOut:
    """Create a course owned by ``user`` (becomes owner) and switch into it.

    Gated on the course-creation capability (Stage 30): a non-creator gets 403.
    """
    if not courses.can_create_courses(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="course_create_forbidden")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="course_name_required")
    course = Workspace(
        name=name,
        owner_user_id=user.id,
        kind=courses.WORKSPACE_KIND_COURSE,
        join_code=_unique_join_code(db),
        join_enabled=True,
    )
    db.add(course)
    db.flush()
    db.add(WorkspaceMember(workspace_id=course.id, user_id=user.id, role=courses.ROLE_OWNER))
    db_user = db.get(User, user.id)
    db_user.active_workspace_id = course.id  # drop the creator straight into it
    db.commit()
    return WorkspaceOut(
        id=course.id, name=course.name, kind=course.kind, role=courses.ROLE_OWNER, is_active=True
    )


def join_course_service(db, user, code: str) -> WorkspaceOut:
    """Join a course by code (idempotent) and switch into it. Bad/disabled code
    → 404."""
    code = (code or "").strip().upper()
    course = db.scalar(
        select(Workspace).where(
            Workspace.join_code == code, Workspace.kind == courses.WORKSPACE_KIND_COURSE
        )
    )
    from src.account_deletion import is_pending
    if course is None or not course.join_enabled or course.is_archived or is_pending(db, course.owner_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invalid_code")
    role = courses.role_in_workspace(db, user.id, course)
    if role is None:
        db.add(WorkspaceMember(workspace_id=course.id, user_id=user.id, role=courses.ROLE_STUDENT))
        role = courses.ROLE_STUDENT
    db_user = db.get(User, user.id)
    db_user.active_workspace_id = course.id
    db.commit()
    return WorkspaceOut(id=course.id, name=course.name, kind=course.kind, role=role, is_active=True)


def leave_course_service(db, user, course_id: str) -> dict:
    """A member leaves a course. The owner can't leave (they'd orphan it)."""
    course = db.get(Workspace, course_id)
    if course is None or course.kind != courses.WORKSPACE_KIND_COURSE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="course_not_found")
    if course.owner_user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_cannot_leave")
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == course.id, WorkspaceMember.user_id == user.id
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_a_member")
    db.delete(member)
    db_user = db.get(User, user.id)
    if db_user.active_workspace_id == course.id:
        db_user.active_workspace_id = None
    db.commit()
    return {"status": "left"}


def _course_detail(db, course: Workspace) -> CourseDetail:
    members: list[CourseMemberOut] = []
    membership_rows = {
        row.user_id: row
        for row in db.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == course.id))
    }
    owner = db.get(User, course.owner_user_id)
    if owner is not None:
        owner_membership = membership_rows.get(owner.id)
        members.append(
            CourseMemberOut(
                user_id=owner.id, email=owner.email, display_name=owner.display_name,
                role=courses.ROLE_OWNER,
                group_name=owner_membership.group_name if owner_membership else "",
            )
        )
    for m in membership_rows.values():
        if m.user_id == course.owner_user_id:
            continue  # owner already listed (authoritative via owner_user_id)
        u = db.get(User, m.user_id)
        if u is not None:
            members.append(
                CourseMemberOut(
                    user_id=u.id, email=u.email, display_name=u.display_name,
                    role=m.role, group_name=m.group_name,
                )
            )
    now = datetime.now(timezone.utc)
    invitations = [
        CourseInviteOut(
            id=invite.id, email=invite.email, role=invite.role,
            group_name=invite.group_name, expires_at=invite.expires_at,
        )
        for invite in db.scalars(
            select(CourseInvitation).where(
                CourseInvitation.workspace_id == course.id,
                CourseInvitation.accepted_at.is_(None),
                CourseInvitation.expires_at > now,
            ).order_by(CourseInvitation.created_at.desc())
        )
    ]
    return CourseDetail(
        id=course.id,
        name=course.name,
        join_code=course.join_code or "",
        join_enabled=course.join_enabled,
        members=members,
        invitations=invitations,
    )


def course_detail_service(db, user, course_id: str) -> CourseDetail:
    """Management view of a course (members + join settings) — managers only."""
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    return _course_detail(db, course)


def rename_course_service(db, user, course_id: str, name: str) -> CourseDetail:
    course = _load_course_for_action(db, user, course_id, courses.ACTION_RENAME_COURSE)
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="course_name_required")
    course.name = name
    db.commit()
    return _course_detail(db, course)


def set_join_code_service(db, user, course_id: str, enabled, rotate: bool) -> JoinCodeResponse:
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_JOIN_CODE)
    if rotate:
        course.join_code = _unique_join_code(db)
    if enabled is not None:
        course.join_enabled = bool(enabled)
    db.commit()
    return JoinCodeResponse(join_code=course.join_code or "", join_enabled=course.join_enabled)


def set_member_role_service(db, user, course_id: str, target_user_id: str, role: str) -> CourseDetail:
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    if role not in (courses.ROLE_TEACHER, courses.ROLE_STUDENT):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_role")
    if target_user_id == course.owner_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_change_owner")
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == course.id, WorkspaceMember.user_id == target_user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_a_member")
    member.role = role
    db.commit()
    return _course_detail(db, course)


def remove_member_service(db, user, course_id: str, target_user_id: str) -> CourseDetail:
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    if target_user_id == course.owner_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_remove_owner")
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == course.id, WorkspaceMember.user_id == target_user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_a_member")
    db.delete(member)
    target = db.get(User, target_user_id)
    if target is not None and target.active_workspace_id == course.id:
        target.active_workspace_id = None  # bounce them back to personal next request
    db.commit()
    return _course_detail(db, course)


def _invite_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_course_invitation_service(
    db, user, course_id: str, *, email: str, role: str, group_name: str
) -> CourseInviteOut:
    """Create an expiring, email-bound invite without exposing its token in API JSON."""
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    role = (role or "student").strip().lower()
    if role not in (courses.ROLE_TEACHER, courses.ROLE_STUDENT):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_role")
    email = email.strip().lower()
    group_name = (group_name or "").strip()[:120]
    existing_user = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing_user is not None and courses.role_in_workspace(db, existing_user.id, course) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_member")

    # Reissuing invalidates older unaccepted links for this address/course.
    db.query(CourseInvitation).filter(
        CourseInvitation.workspace_id == course.id,
        func.lower(CourseInvitation.email) == email,
        CourseInvitation.accepted_at.is_(None),
    ).delete(synchronize_session=False)
    raw_token = secrets.token_urlsafe(32)
    invite = CourseInvitation(
        workspace_id=course.id,
        email=email,
        role=role,
        group_name=group_name,
        token_hash=_invite_hash(raw_token),
        invited_by_user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    link = f"{config.APP_BASE_URL}/?invite={raw_token}"
    delivered = email_service.send_email(
        email,
        f"Приглашение в программу «{course.name}»",
        "Вас пригласили в учебную программу «Наставник».\n\n"
        f"Открыть приглашение: {link}\n\nСсылка действует 7 дней и предназначена только для {email}.",
    )
    return CourseInviteOut(
        id=invite.id, email=invite.email, role=invite.role,
        group_name=invite.group_name, expires_at=invite.expires_at,
        delivery_status="sent" if delivered else "failed",
    )


def revoke_course_invitation_service(db, user, course_id: str, invitation_id: str) -> CourseDetail:
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    invite = db.get(CourseInvitation, invitation_id)
    if invite is None or invite.workspace_id != course.id or invite.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invitation_not_found")
    db.delete(invite)
    db.commit()
    return _course_detail(db, course)


def accept_course_invitation_service(db, user, token: str) -> WorkspaceOut:
    # PostgreSQL serializes simultaneous clicks on the same one-time link.
    # SQLite ignores FOR UPDATE, which is fine for local development/tests.
    invite = db.scalar(
        select(CourseInvitation)
        .where(CourseInvitation.token_hash == _invite_hash(token))
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    expires = invite.expires_at if invite is not None else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if invite is None or invite.accepted_at is not None or expires <= now:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invalid_invitation")
    if invite.email.lower() != user.email.lower():
        # Same response as an invalid token: a logged-in outsider must not be
        # able to confirm that somebody else's invitation exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invalid_invitation")
    course = db.get(Workspace, invite.workspace_id)
    from src.account_deletion import is_pending
    if (
        course is None or course.kind != courses.WORKSPACE_KIND_COURSE
        or course.is_archived or is_pending(db, course.owner_user_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invalid_invitation")
    member = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == course.id, WorkspaceMember.user_id == user.id
    ))
    if member is None:
        member = WorkspaceMember(
            workspace_id=course.id, user_id=user.id,
            role=invite.role, group_name=invite.group_name,
        )
        db.add(member)
    # Existing membership remains authoritative; accepting a stale invite must
    # never silently demote/promote an account. The invite is still consumed.
    invite.accepted_at = now
    db.get(User, user.id).active_workspace_id = course.id
    db.commit()
    role = courses.role_in_workspace(db, user.id, course) or courses.ROLE_STUDENT
    return WorkspaceOut(id=course.id, name=course.name, kind=course.kind, role=role, is_active=True)


def set_member_group_service(
    db, user, course_id: str, target_user_id: str, group_name: str
) -> CourseDetail:
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    member = db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == course.id, WorkspaceMember.user_id == target_user_id
    ))
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_a_member")
    member.group_name = (group_name or "").strip()[:120]
    db.commit()
    return _course_detail(db, course)


def course_pilot_dashboard_service(db, user, course_id: str) -> PilotDashboard:
    """Aggregate manager metrics; deliberately never returns chat text/history."""
    course = _load_course_for_action(db, user, course_id, courses.ACTION_MANAGE_MEMBERS)
    detail = _course_detail(db, course)
    member_ids = [member.user_id for member in detail.members]
    published_ids = list(db.scalars(select(Assignment.id).where(
        Assignment.workspace_id == course.id, Assignment.is_published.is_(True)
    )))
    chat_rows = db.execute(
        select(UsageEvent.user_id, func.count(UsageEvent.id), func.max(UsageEvent.created_at)).where(
            UsageEvent.workspace_id == course.id,
            UsageEvent.action == quota.ACTION_CHAT,
            UsageEvent.user_id.in_(member_ids or [""]),
        ).group_by(UsageEvent.user_id)
    ).all()
    chat = {row[0]: (int(row[1]), row[2]) for row in chat_rows}
    attempts = list(db.scalars(select(AssignmentAttempt).where(
        AssignmentAttempt.assignment_id.in_(published_ids or [""]),
        AssignmentAttempt.user_id.in_(member_ids or [""]),
    )))
    attempts_by_user: dict[str, list[AssignmentAttempt]] = {}
    for attempt in attempts:
        attempts_by_user.setdefault(attempt.user_id, []).append(attempt)
    metrics = []
    for member in detail.members:
        own_attempts = attempts_by_user.get(member.user_id, [])
        score_values = [a.score / a.total * 100 for a in own_attempts if a.total]
        last_values = [value for value in [chat.get(member.user_id, (0, None))[1]] if value is not None]
        last_values.extend(a.updated_at for a in own_attempts)
        metrics.append(PilotMemberMetric(
            user_id=member.user_id, display_name=member.display_name, email=member.email,
            role=member.role, group_name=member.group_name,
            chat_requests=chat.get(member.user_id, (0, None))[0],
            assignments_completed=len(own_attempts), assignments_total=len(published_ids),
            average_score_pct=round(sum(score_values) / len(score_values), 1) if score_values else None,
            last_activity_at=max(last_values) if last_values else None,
        ))
    students = [metric for metric in metrics if metric.role == courses.ROLE_STUDENT]
    completed = sum(metric.assignments_completed for metric in students)
    possible = len(students) * len(published_ids)
    return PilotDashboard(
        members_total=len(metrics),
        active_members=sum(1 for metric in metrics if metric.last_activity_at is not None),
        chat_requests=sum(metric.chat_requests for metric in metrics),
        assignments_published=len(published_ids), assignments_completed=completed,
        completion_pct=round(completed / possible * 100, 1) if possible else None,
        members=metrics,
    )


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def course_pilot_report_csv_service(db, user, course_id: str) -> bytes:
    dashboard = course_pilot_dashboard_service(db, user, course_id)
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Сотрудник", "Почта", "Роль", "Подразделение", "Вопросы", "Задания", "Средний результат, %", "Последняя активность"])
    for member in dashboard.members:
        writer.writerow([_csv_cell(member.display_name), _csv_cell(member.email), member.role,
                         _csv_cell(member.group_name), member.chat_requests,
                         f"{member.assignments_completed}/{member.assignments_total}",
                         member.average_score_pct if member.average_score_pct is not None else "",
                         member.last_activity_at.isoformat() if member.last_activity_at else ""])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def delete_course_service(db, user, course_id: str) -> dict:
    """Delete a course (owner only) and scrub its data.

    Wipes the workspace's vector chunks (``kb.clear``) and on-disk files
    (``docs/<id>/``), resets ``active_workspace_id`` for anyone currently in it,
    then deletes the ``Workspace`` row — cascading its ``Document`` and
    ``WorkspaceMember`` rows. KB/file wipes are best-effort: a failure there
    leaves orphans the reconciler can scrub, but must not block the delete.
    """
    course = _load_owned_course(db, user, course_id)  # owner-only; works on archived too

    try:
        runtime.get_kb().clear(workspace_id=course_id)
    except Exception:
        logger.warning("delete_course: KB clear failed for %s", course_id, exc_info=True)
    try:
        docs_dir = storage.workspace_docs_dir(course_id)
        if os.path.isdir(docs_dir):
            shutil.rmtree(docs_dir, ignore_errors=True)
    except Exception:
        logger.warning("delete_course: file wipe failed for %s", course_id, exc_info=True)

    # Anyone whose active workspace was this course falls back to personal.
    db.query(User).filter(User.active_workspace_id == course_id).update(
        {User.active_workspace_id: None}, synchronize_session=False
    )
    db.delete(course)  # cascades Document + WorkspaceMember rows
    db.commit()
    return {"status": "deleted"}


def generate_summary_service(workspace_id: str, request: SummaryRequest):
    """Generate a summary scoped to ``workspace_id``.

    Routes the request to the matching ``summary_engine`` strategy and
    threads ``workspace_id`` through to every KB call. Replaces the
    pre-Stage-6d bridge that detoured through a separate UI entrypoint.
    """
    # Quota gate (Stage 12): summaries are the expensive LLM path, so the free
    # tier caps them tightly. Raised as 402 before any work when over the cap.
    quota.check_quota(workspace_id, quota.ACTION_SUMMARY)

    selected_file = _normalize_selected_file(request.selected_file)
    selected_section = request.selected_section
    topic = (request.topic or "").strip()
    summary_type = request.summary_type

    start_trace(
        kind="summary",
        request={
            "workspace_id": workspace_id,
            "selected_file": selected_file,
            "selected_section": selected_section,
            "topic": topic,
            "summary_type": summary_type,
        },
    )

    try:
        kb = runtime.get_kb()
        llm = DiagnosticLLM(runtime.get_llm())

        file_filter = "all" if selected_file == "Все файлы" else selected_file
        section_filter = None
        if selected_section and selected_section != "Все разделы":
            section_filter = selected_section

        if section_filter:
            text = summary_engine.generate_selected_section_summary(
                kb=kb,
                llm=llm,
                selected_file=selected_file,
                section_filter=section_filter,
                topic=topic,
                summary_type=summary_type,
                workspace_id=workspace_id,
            )
        elif topic:
            summary_type_low = str(summary_type or "").lower()
            if (
                not summary_engine._looks_like_history_topic(topic)
                and ("крат" in summary_type_low or "сред" in summary_type_low)
            ):
                text = summary_engine.generate_direct_topic_summary(
                    kb=kb,
                    llm=llm,
                    topic=topic,
                    summary_type=summary_type,
                    file_filter=file_filter,
                    section_filter=None,
                    workspace_id=workspace_id,
                )
            elif getattr(config, "PLANNED_SUMMARY_ENABLED", True):
                text = summary_engine.generate_planned_topic_summary(
                    kb=kb,
                    llm=llm,
                    topic=topic,
                    summary_type=summary_type,
                    file_filter=file_filter,
                    section_filter=None,
                    workspace_id=workspace_id,
                )
            else:
                text = summary_engine.generate_topic_summary(
                    kb=kb,
                    llm=llm,
                    topic=topic,
                    summary_type=summary_type,
                    file_filter=file_filter,
                    section_filter=None,
                    workspace_id=workspace_id,
                )
        else:
            # No topic, no section — generic map-reduce over the selected file.
            text = summary_engine.generate_full_file_summary(
                kb=kb,
                llm=llm,
                selected_file=selected_file,
                selected_section=selected_section,
                summary_type=summary_type,
                file_filter=file_filter,
                workspace_id=workspace_id,
            )

        finish_trace(output=text)
        summary_failed = False
    except Exception as error:
        # Unexpected failure: log the detail, show clean copy instead of a raw
        # Python exception (Stage 42).
        logger.exception("summary generation failed for workspace %s", workspace_id)
        text = (
            "Не удалось сгенерировать конспект. Попробуйте сузить тему "
            "или повторить запрос."
        )
        finish_trace(output=text, error=error)
        summary_failed = True

    # Meter only a real summary run — not the error fallback above.
    if not summary_failed:
        quota.record_usage(workspace_id, quota.ACTION_SUMMARY, meta={"summary_type": summary_type})

    # Same as chat: bot output uses a plain hyphen, not em/en dashes.
    text = _normalize_dashes(text)

    return SummaryResponse(
        text=text,
        diagnostics=format_last_trace(),
        trace=get_last_trace(),
    )


# --- Study trainer: flashcards & quizzes (Stage 17) ------------------------
_FLASHCARDS_PROMPT = """{system}

На основе ТОЛЬКО следующего материала составь {count} учебных карточек «вопрос-ответ» для повторения.
Вопросы — по сути материала; ответы короткие и точные, строго из материала.
Верни СТРОГО валидный JSON, без markdown и пояснений, ровно такого вида:
{{"cards": [{{"q": "вопрос", "a": "ответ"}}]}}

Материал:
{context}
"""

_QUIZ_PROMPT = """{system}

На основе ТОЛЬКО следующего материала составь тест из {count} вопросов с выбором ответа.
У каждого вопроса ровно 4 варианта и ровно один правильный. "correct" — индекс правильного варианта (0-3).
"explanation" — короткое пояснение, почему ответ верный, строго из материала.
Верни СТРОГО валидный JSON, без markdown и пояснений, ровно такого вида:
{{"questions": [{{"q": "вопрос", "options": ["A","B","C","D"], "correct": 0, "explanation": "пояснение"}}]}}

Материал:
{context}
"""


def _study_context(workspace_id: str, selected_file: str, topic: str) -> str:
    """Build the material context for the trainer, scoped to the workspace +
    selected file (topic → semantic search, else the file's first chunks)."""
    kb = runtime.get_kb()
    file_filter = "all" if selected_file in ALL_FILES_LABELS else selected_file
    topic = (topic or "").strip()
    if topic:
        chunks = kb.search_chunks_for_summary(
            query=topic,
            file_filter=file_filter,
            section_filter=None,
            top_k=config.STUDY_MAX_CHUNKS,
            workspace_id=workspace_id,
        )
    else:
        chunks = kb.get_file_chunks(file_filter=file_filter, workspace_id=workspace_id)[
            : config.STUDY_MAX_CHUNKS
        ]

    parts = []
    for chunk in chunks:
        section = chunk.get("section") or ""
        source = chunk.get("source_file", "")
        label = f"{source} | {section}" if section else source
        parts.append(f"[{label}]\n{chunk.get('text', '')}")
    return "\n\n---\n\n".join(parts)


def _coerce_study(parsed, mode: str):
    """Validate the parsed LLM JSON into typed cards/questions, dropping any
    malformed item (so one bad entry doesn't sink the whole set)."""
    cards: list[Flashcard] = []
    questions: list[QuizQuestion] = []
    if not isinstance(parsed, dict):
        return cards, questions

    if mode == "quiz":
        for item in parsed.get("questions") or []:
            if not isinstance(item, dict):
                continue
            q = _normalize_dashes(str(item.get("q") or item.get("question") or "").strip())
            options = [
                _normalize_dashes(str(o).strip()) for o in (item.get("options") or []) if str(o).strip()
            ]
            if not q or len(options) < 2:
                continue
            try:
                correct = int(item.get("correct", item.get("correct_index", 0)))
            except (TypeError, ValueError):
                correct = 0
            if correct < 0 or correct >= len(options):
                correct = 0
            questions.append(
                QuizQuestion(
                    question=q,
                    options=options,
                    correct_index=correct,
                    explanation=_normalize_dashes(str(item.get("explanation") or "").strip()),
                )
            )
    else:
        for item in parsed.get("cards") or []:
            if not isinstance(item, dict):
                continue
            q = _normalize_dashes(str(item.get("q") or item.get("question") or "").strip())
            a = _normalize_dashes(str(item.get("a") or item.get("answer") or "").strip())
            if q and a:
                cards.append(Flashcard(question=q, answer=a))
    return cards, questions


def _extract_json_object(text: str):
    """Pull the outermost JSON object out of an LLM reply (tolerating ```json
    fences and surrounding prose). Returns the parsed dict or None."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def generate_study_service(workspace_id: str, request: StudyRequest) -> StudyResponse:
    """Generate flashcards or a quiz from the selected material (Stage 17).

    Quota is checked first (402 over the daily cap). The LLM must return strict
    JSON; on a bad reply we retry once with a stricter instruction, then fall
    back to ``ok=false`` + a message — never an exception that breaks the UI.
    Usage is metered only on a successful, non-empty result.
    """
    quota.check_quota(workspace_id, quota.ACTION_STUDY)

    mode = (request.mode or "flashcards").strip().lower()
    if mode not in ("flashcards", "quiz"):
        mode = "flashcards"
    count = request.count or config.STUDY_DEFAULT_COUNT
    count = max(1, min(int(count), config.STUDY_MAX_COUNT))

    context = _study_context(workspace_id, request.selected_file, request.topic)
    if not context.strip():
        return StudyResponse(
            mode=mode, ok=False, message="В выбранном материале нет данных для тренажёра."
        )

    template = _QUIZ_PROMPT if mode == "quiz" else _FLASHCARDS_PROMPT
    base_prompt = template.format(system=config.SYSTEM_PROMPT, count=count, context=context)
    llm = runtime.get_llm()

    for attempt in range(2):
        prompt = base_prompt if attempt == 0 else base_prompt + "\n\nВерни ТОЛЬКО валидный JSON."
        try:
            raw = llm.call(prompt, max_tokens=2400)
        except Exception:
            logger.warning("study generation LLM call failed", exc_info=True)
            break
        cards, questions = _coerce_study(_extract_json_object(raw), mode)
        if cards or questions:
            quota.record_usage(workspace_id, quota.ACTION_STUDY, meta={"mode": mode, "count": count})
            return StudyResponse(mode=mode, ok=True, cards=cards, questions=questions)

    return StudyResponse(
        mode=mode,
        ok=False,
        message="Не удалось собрать тренажёр. Попробуйте ещё раз или уточните тему.",
    )


# --- Assignments: teacher-assigned tests + results (Stage 18) ---------------
# Authorization is enforced at the route via require_permission(...); these
# services additionally scope every assignment to ``workspace_id`` (the active
# course from auth) so an id from another course can't be reached, and re-check
# ``can(manage)`` where the *content* differs by role (list / take views).


def _questions_from_stored(stored) -> list[QuizQuestion]:
    """Stored question dicts → typed QuizQuestion (with correct answers)."""
    out: list[QuizQuestion] = []
    for q in stored or []:
        if not isinstance(q, dict):
            continue
        options = [str(o) for o in (q.get("options") or [])]
        try:
            ci = int(q.get("correct_index", 0))
        except (TypeError, ValueError):
            ci = 0
        if ci < 0 or ci >= len(options):
            ci = 0
        out.append(
            QuizQuestion(
                question=str(q.get("question") or ""),
                options=options,
                correct_index=ci,
                explanation=str(q.get("explanation") or ""),
            )
        )
    return out


def _public_questions(stored) -> list[AssignmentQuestionPublic]:
    """Stored question dicts → student-facing view (no correct answer/explanation)."""
    return [
        AssignmentQuestionPublic(
            question=str(q.get("question") or ""),
            options=[str(o) for o in (q.get("options") or [])],
        )
        for q in (stored or [])
        if isinstance(q, dict)
    ]


def _avg_score_pct(attempts) -> float | None:
    scored = [(a.score, a.total) for a in attempts if a.total]
    if not scored:
        return None
    return round(sum(s / t for s, t in scored) / len(scored) * 100, 1)


def _manager_list_item(a: Assignment) -> AssignmentListItem:
    return AssignmentListItem(
        id=a.id,
        title=a.title,
        question_count=len(a.questions or []),
        is_published=a.is_published,
        created_at=a.created_at,
        attempt_count=len(a.attempts),
        avg_score_pct=_avg_score_pct(a.attempts),
    )


def _load_assignment(db, workspace_id: str, assignment_id: str) -> Assignment:
    """Load an assignment, 404 unless it belongs to the active workspace."""
    a = db.get(Assignment, assignment_id)
    if a is None or a.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment_not_found")
    return a


def _sanitize_questions(questions) -> list[dict]:
    """Validate/clean incoming quiz questions into storable dicts. Drops items
    with no text or <2 options; clamps an out-of-range correct index to 0.
    Bot-authored text gets the same hyphen normalization as chat/summaries."""
    stored: list[dict] = []
    for q in questions or []:
        question = _normalize_dashes(str(getattr(q, "question", "")).strip())
        options = [_normalize_dashes(str(o).strip()) for o in (getattr(q, "options", []) or []) if str(o).strip()]
        if not question or len(options) < 2:
            continue
        try:
            ci = int(getattr(q, "correct_index", 0))
        except (TypeError, ValueError):
            ci = 0
        if ci < 0 or ci >= len(options):
            ci = 0
        stored.append(
            {
                "question": question,
                "options": options,
                "correct_index": ci,
                "explanation": _normalize_dashes(str(getattr(q, "explanation", "")).strip()),
            }
        )
    return stored


def create_assignment_service(
    db, workspace_id: str, user, request: CreateAssignmentRequest
) -> AssignmentListItem:
    """Save a curated quiz as a draft assignment (manager). Generation already
    happened (and was metered as ``study``) via the trainer; this only persists."""
    title = (request.title or "").strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title_required")
    stored = _sanitize_questions(request.questions)
    if not stored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no_questions")

    a = Assignment(
        workspace_id=workspace_id,
        created_by_user_id=user.id,
        title=title[:255],
        source_label=(request.source_label or "").strip()[:255],
        questions=stored,
        is_published=False,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _manager_list_item(a)


def list_assignments_service(db, workspace_id: str, user) -> AssignmentListResponse:
    """List assignments for the active workspace, role-aware: a manager sees all
    (draft + published) with stats; a student sees only published ones with their
    own attempt status."""
    workspace = db.get(Workspace, workspace_id)
    can_manage = workspace is not None and courses.can(
        db, user, courses.ACTION_MANAGE_ASSIGNMENTS, workspace
    )

    rows = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.workspace_id == workspace_id)
            .order_by(Assignment.created_at.desc())
        )
    )

    if can_manage:
        items = [_manager_list_item(a) for a in rows]
        return AssignmentListResponse(assignments=items, can_manage=True)

    published = [a for a in rows if a.is_published]
    my_attempts = {
        at.assignment_id: at
        for at in db.scalars(
            select(AssignmentAttempt).where(
                AssignmentAttempt.user_id == user.id,
                AssignmentAttempt.assignment_id.in_([a.id for a in published] or [""]),
            )
        )
    }
    items = []
    for a in published:
        attempt = my_attempts.get(a.id)
        items.append(
            AssignmentListItem(
                id=a.id,
                title=a.title,
                question_count=len(a.questions or []),
                is_published=True,
                created_at=a.created_at,
                submitted=attempt is not None,
                my_score=attempt.score if attempt else None,
                my_total=attempt.total if attempt else None,
            )
        )
    return AssignmentListResponse(assignments=items, can_manage=False)


def _attempt_for(db, assignment_id: str, user_id: str) -> AssignmentAttempt | None:
    return db.scalar(
        select(AssignmentAttempt).where(
            AssignmentAttempt.assignment_id == assignment_id,
            AssignmentAttempt.user_id == user_id,
        )
    )


def get_assignment_for_taking_service(
    db, workspace_id: str, user, assignment_id: str
) -> AssignmentForTaking:
    """The take-view. Students can't see a draft (404). Correct answers are
    withheld until the student has an attempt — then the full result is revealed."""
    a = _load_assignment(db, workspace_id, assignment_id)
    workspace = db.get(Workspace, workspace_id)
    can_manage = workspace is not None and courses.can(
        db, user, courses.ACTION_MANAGE_ASSIGNMENTS, workspace
    )
    if not a.is_published and not can_manage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment_not_found")

    attempt = _attempt_for(db, a.id, user.id)
    if attempt is not None:
        result = AttemptResult(
            score=attempt.score,
            total=attempt.total,
            answers=list(attempt.answers or []),
            questions=_questions_from_stored(a.questions),
        )
        return AssignmentForTaking(
            id=a.id,
            title=a.title,
            question_count=len(a.questions or []),
            submitted=True,
            result=result,
            can_manage=can_manage,
        )

    return AssignmentForTaking(
        id=a.id,
        title=a.title,
        question_count=len(a.questions or []),
        questions=_public_questions(a.questions),
        submitted=False,
        can_manage=can_manage,
    )


def submit_attempt_service(
    db, workspace_id: str, user, assignment_id: str, answers: list[int]
) -> AttemptResult:
    """Score a submission on the server and store it. One attempt only (Stage
    46): the first submit is graded and stored; any later submit is ignored and
    returns the original result unchanged - otherwise a student could submit
    blind, read the answers revealed on the result screen, then resubmit them for
    100%. Returns the result with correct answers revealed."""
    a = _load_assignment(db, workspace_id, assignment_id)
    workspace = db.get(Workspace, workspace_id)
    can_manage = workspace is not None and courses.can(
        db, user, courses.ACTION_MANAGE_ASSIGNMENTS, workspace
    )
    if not a.is_published and not can_manage:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment_not_found")

    stored = list(a.questions or [])
    total = len(stored)

    # One attempt only: a resubmit never re-scores or overwrites. Return the
    # original attempt so a double-click is harmless and the revealed answers
    # can't be replayed for a better score.
    existing = _attempt_for(db, a.id, user.id)
    if existing is not None:
        return AttemptResult(
            score=existing.score,
            total=existing.total,
            answers=list(existing.answers or []),
            questions=_questions_from_stored(stored),
        )

    # Normalize answers to exactly one entry per question (-1 = unanswered).
    answers = [int(x) if isinstance(x, int) else -1 for x in (answers or [])]
    answers = (answers + [-1] * total)[:total]

    score = 0
    for i, q in enumerate(stored):
        try:
            correct = int(q.get("correct_index", 0))
        except (TypeError, ValueError):
            correct = 0
        if answers[i] == correct:
            score += 1

    attempt = AssignmentAttempt(assignment_id=a.id, user_id=user.id)
    db.add(attempt)
    attempt.answers = answers
    attempt.score = score
    attempt.total = total
    db.commit()

    return AttemptResult(
        score=score,
        total=total,
        answers=answers,
        questions=_questions_from_stored(stored),
    )


def publish_assignment_service(
    db, workspace_id: str, user, assignment_id: str, published: bool
) -> AssignmentListItem:
    """Toggle a draft ↔ published (manager). Students only ever see published."""
    a = _load_assignment(db, workspace_id, assignment_id)
    a.is_published = bool(published)
    db.commit()
    db.refresh(a)
    return _manager_list_item(a)


def delete_assignment_service(db, workspace_id: str, user, assignment_id: str) -> dict:
    """Delete an assignment and its attempts (manager)."""
    a = _load_assignment(db, workspace_id, assignment_id)
    db.delete(a)
    db.commit()
    return {"status": "deleted"}


def assignment_results_service(db, workspace_id: str, user, assignment_id: str) -> AssignmentResults:
    """Per-student attempts + aggregate analytics for a manager."""
    a = _load_assignment(db, workspace_id, assignment_id)
    stored = list(a.questions or [])
    total_q = len(stored)
    attempts = list(a.attempts)

    students_total = int(
        db.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == courses.ROLE_STUDENT,
            )
        )
        or 0
    )

    # Per-student rows (resolve names in one query).
    user_ids = [at.user_id for at in attempts]
    users = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_(user_ids or [""])))
    }
    rows: list[StudentAttemptOut] = []
    for at in attempts:
        u = users.get(at.user_id)
        rows.append(
            StudentAttemptOut(
                user_id=at.user_id,
                display_name=(u.display_name if u else "") or (u.email if u else ""),
                email=u.email if u else "",
                score=at.score,
                total=at.total,
                submitted_at=at.updated_at,
            )
        )
    rows.sort(key=lambda r: (-r.score, r.display_name.lower()))

    # Per-question accuracy (denominator = attempts that covered the question).
    question_stats: list[QuestionStat] = []
    for i, q in enumerate(stored):
        try:
            correct = int(q.get("correct_index", 0))
        except (TypeError, ValueError):
            correct = 0
        covered = 0
        right = 0
        for at in attempts:
            ans = list(at.answers or [])
            if i < len(ans):
                covered += 1
                if ans[i] == correct:
                    right += 1
        accuracy = round(right / covered * 100, 1) if covered else 0.0
        question_stats.append(
            QuestionStat(
                index=i,
                question=str(q.get("question") or ""),
                correct_count=right,
                attempts=covered,
                accuracy_pct=accuracy,
            )
        )

    return AssignmentResults(
        id=a.id,
        title=a.title,
        question_count=total_q,
        is_published=a.is_published,
        students_total=students_total,
        attempts_count=len(attempts),
        avg_score_pct=_avg_score_pct(attempts) or 0.0,
        attempts=rows,
        question_stats=question_stats,
        questions=_questions_from_stored(stored),
    )


# Characters that make Excel/LibreOffice treat a CSV cell as a formula. A
# student controls their own display_name, so a name like ``=HYPERLINK(...)`` or
# a DDE payload would execute on the *teacher's* machine when they open the
# exported results in a spreadsheet (OWASP "CSV/formula injection").
_CSV_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe_cell(value):
    """Neutralize CSV/Excel formula injection: prefix an attacker-influenced cell
    that starts with a formula leader with an apostrophe so the spreadsheet shows
    it as literal text. Legitimate names/emails/scores never start with these."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_LEADERS:
        return "'" + value
    return value


def assignment_results_xlsx_service(db, workspace_id: str, user, assignment_id: str) -> bytes:
    """Assignment results as an .xlsx workbook (Stage 55): bold header, frozen
    header row and sensible column widths - so a teacher opens it in Excel with
    the «Студент»/«Email»/«Сдано» columns wide enough to read at a glance (the
    original gripe a CSV can't fix - it carries no column widths). Same data +
    authorization as the JSON results view; text cells are still neutralized
    against spreadsheet formula injection."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    results = assignment_results_service(db, workspace_id, user, assignment_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Результаты"

    ws.append(["Студент", "Email", "Балл", "Всего", "Процент", "Сдано"])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in results.attempts:
        pct = round(row.score / row.total * 100) if row.total else 0
        submitted = row.submitted_at.strftime("%d.%m.%Y %H:%M") if row.submitted_at else ""
        ws.append(
            [
                _csv_safe_cell(row.display_name or row.email),
                _csv_safe_cell(row.email),
                row.score,
                row.total,
                f"{pct}%",
                _csv_safe_cell(submitted),
            ]
        )

    # Column widths (in characters) - the room the CSV couldn't express.
    for column, width in zip("ABCDEF", (30, 32, 8, 8, 10, 18)):
        ws.column_dimensions[column].width = width
    ws.freeze_panes = "A2"  # keep the header visible while scrolling

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _clean_passage(text: str) -> str:
    """Tidy a raw PDF/OCR passage so the "Источники" block reads as prose.

    Extractors hand us the page's *visual* layout: every line break is real, and
    words hyphenated across lines keep a soft hyphen (U+00AD) plus the break —
    e.g. ``веществен\\xad ными``. Rendered as-is (the UI uses `whitespace-pre-wrap`)
    a passage arrives as a ragged column of fragments, and copying it out gives
    the reader a word-salad. This rejoins the split words and reflows the lines;
    it deliberately does *not* try to repair mid-word spaces the OCR invented
    (``Гурви ца``) — guessing there would corrupt correct text.
    """
    if not text:
        return ""

    cleaned = str(text)
    # Hyphenation: soft hyphen (with whatever break follows) and a hard hyphen
    # sitting at a line end both mean "this word continues on the next line".
    cleaned = re.sub(r"­\s*", "", cleaned)
    cleaned = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", cleaned)
    # Remaining newlines are layout, not meaning — reflow them into a paragraph.
    cleaned = re.sub(r"\s*\n\s*", " ", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _group_chat_sources(sources, selected_file, limit=None):
    # Every distinct retrieved passage can support a different claim, even on
    # the same page. Do not discard its tail or replace it with a representative
    # from the same section: users must be able to verify the whole answer.
    grouped = {}

    for src in sources or []:
        source_file = src.get("source_file", "")
        section = src.get("section", "")
        page_start = src.get("page_start")
        page_end = src.get("page_end")
        score = float(src.get("score", 0) or 0)
        text = (src.get("text") or "").strip()
        key = (source_file, section, page_start, page_end, text)
        if key not in grouped:
            grouped[key] = {
                "source_file": source_file,
                "section": section,
                "page_start": page_start,
                "page_end": page_end,
                "score": score,
                "text": text,
            }
        elif score > grouped[key]["score"]:
            # Deduplicate only identical passages, retaining their best score.
            grouped[key]["score"] = score
            grouped[key]["text"] = text

    sorted_sources = sorted(
        grouped.values(),
        key=lambda item: item["score"],
        reverse=True,
    )[:limit]

    result = []
    for item in sorted_sources:
        if selected_file != "Все файлы" and item["section"]:
            label = item["section"]
        elif item["section"]:
            label = f'{item["source_file"]} -> {item["section"]}'
        else:
            label = item["source_file"]

        page_start = item.get("page_start")
        page_end = item.get("page_end")
        if page_start:
            page_label = (
                f"стр. {page_start}–{page_end}"
                if page_end and page_end != page_start
                else f"стр. {page_start}"
            )
            label = f"{label} · {page_label}"

        snippet = _clean_passage(item.get("text", ""))
        result.append(
            ChatSource(
                source_file=item["source_file"],
                section=item["section"],
                page_start=page_start,
                page_end=page_end,
                score=round(item["score"], 3),
                label=label,
                snippet=snippet,
            )
        )

    return result


@dataclass
class _ChatPlan:
    """Output of :func:`_prepare_chat` — either a canned early answer or the
    params to generate one. Shared by the sync (`chat_service`) and streaming
    (`chat_stream_service`) paths so the RAG / context / source logic lives in
    exactly one place."""

    kind: str  # "empty" | "greeting" | "no_context" | "generate"
    message: str = ""
    selected_file: str = "Все файлы"
    answer: str = ""
    summary: str = ""
    confidence_label: str = ""
    followup_suggestions: list = field(default_factory=list)
    grouped_sources: list = field(default_factory=list)
    full_prompt: str = ""
    focused_prompt: str = ""
    focused_context: str = ""
    focused_sources: list = field(default_factory=list)
    history: list = field(default_factory=list)
    # Контекст, который реально ушёл в модель. Нужен снаружи (evals): без него
    # приходилось искать заново, а повторный поиск идёт БЕЗ отката по
    # _SECTION_FILTER_MIN_SOURCES и даёт другой контекст - и метрика recall
    # мерила не то, из чего получен ответ. Замер 04.09.2026 на СП 60: кейс с
    # верным ответом получил recall 0.00, потому что сравнивался с 428
    # символами из суженного поиска вместо настоящего контекста.
    context: str = ""


_GREETING_FOLLOWUPS = [
    "Объясни тему простыми словами.",
    "Сделай краткий ответ по разделу.",
    "Приведи только цитаты по теме.",
]
# Ниже этого числа найденных фрагментов сужение по разделу считаем промахом и
# ищем заново по всему материалу (см. _prepare_chat). Порог низкий намеренно:
# настоящий узкий раздел столько и даёт, а промах фильтра — один-два обрывка.
_SECTION_FILTER_MIN_SOURCES = 3

_NO_CONTEXT_FOLLOWUPS = [
    "Выбери другой материал.",
    "Сузь вопрос до конкретного раздела.",
    "Переформулируй тему короче.",
]


# --- Chat history: persistent sessions (Stage 19) --------------------------
# Authorization here is OWNERSHIP, not the course can() resolver: a chat is
# private to its (user, workspace) pair even inside a shared course, so a
# teacher can never read a student's chats.


def _chat_session_title(message: str) -> str:
    """A short title from the first user message (renameable later)."""
    text = " ".join((message or "").split())
    return text[:60] if text else "Новый чат"


def _load_owned_session(db, user, workspace_id: str, session_id: str) -> ChatSession:
    """Load a chat session, 404 unless it belongs to this user AND the active
    workspace. A ``session_id`` from a request is never trusted blindly."""
    session = db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id or session.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return session


def session_history_messages(session: ChatSession) -> list[ChatMessage]:
    """The session's last N messages as chat-pipeline history (the server is the
    source of truth for context, not the client)."""
    recent = session.messages[-config.CHAT_HISTORY_CONTEXT_MESSAGES :]
    return [ChatMessage(role=m.role, content=m.content) for m in recent]


def _message_count(db, session_id: str) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(ChatMessageRow).where(ChatMessageRow.session_id == session_id)
        )
        or 0
    )


def persist_chat_turn(
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    message: str,
    answer: str,
    meta: dict | None,
) -> str:
    """Append a (user, assistant) turn to a session, creating it on first use.

    Opens its own DB session (so it's safe to call from the streaming generator,
    where the request-scoped session may already be closed). Re-validates
    ownership of an existing ``session_id`` defensively. Returns the session id.
    """
    db = SessionLocal()
    try:
        session = None
        if session_id:
            session = db.get(ChatSession, session_id)
            if session is not None and (session.user_id != user_id or session.workspace_id != workspace_id):
                session = None  # don't write into someone else's / another ws's session
        if session is None:
            session = ChatSession(
                workspace_id=workspace_id, user_id=user_id, title=_chat_session_title(message)
            )
            db.add(session)
            db.flush()
        db.add(ChatMessageRow(session_id=session.id, role="user", content=message))
        db.add(ChatMessageRow(session_id=session.id, role="assistant", content=answer, meta=meta))
        session.updated_at = datetime.now(timezone.utc)  # bump for newest-first ordering
        db.commit()
        return session.id
    finally:
        db.close()


def _assistant_meta(*, summary, confidence_label, followups, sources) -> dict:
    """The snapshot stored on an assistant message so a reopened chat renders
    citations/confidence/follow-ups exactly as the live answer did."""
    return {
        "summary": summary or "",
        "confidence_label": confidence_label or "",
        "followup_suggestions": list(followups or []),
        "sources": [s.model_dump() for s in (sources or [])],
    }


def list_chat_sessions_service(db, user, workspace_id: str) -> ChatSessionsResponse:
    """The caller's own chats in the active workspace, newest-first."""
    rows = list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == user.id, ChatSession.workspace_id == workspace_id)
            .order_by(ChatSession.updated_at.desc())
        )
    )
    counts = dict(
        db.execute(
            select(ChatMessageRow.session_id, func.count())
            .where(ChatMessageRow.session_id.in_([s.id for s in rows] or [""]))
            .group_by(ChatMessageRow.session_id)
        ).all()
    )
    return ChatSessionsResponse(
        sessions=[
            ChatSessionOut(
                id=s.id, title=s.title, message_count=int(counts.get(s.id, 0)), updated_at=s.updated_at
            )
            for s in rows
        ]
    )


def get_chat_session_service(db, user, workspace_id: str, session_id: str) -> ChatSessionDetail:
    """Full messages of one of the caller's own chats (404 if not theirs)."""
    session = _load_owned_session(db, user, workspace_id, session_id)
    return ChatSessionDetail(
        id=session.id,
        title=session.title,
        messages=[
            ChatSessionMessageOut(role=m.role, content=m.content, meta=m.meta) for m in session.messages
        ],
    )


def rename_chat_session_service(db, user, workspace_id: str, session_id: str, title: str) -> ChatSessionOut:
    session = _load_owned_session(db, user, workspace_id, session_id)
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title_required")
    session.title = title[:255]
    db.commit()
    return ChatSessionOut(
        id=session.id,
        title=session.title,
        message_count=_message_count(db, session.id),
        updated_at=session.updated_at,
    )


def delete_chat_session_service(db, user, workspace_id: str, session_id: str) -> dict:
    session = _load_owned_session(db, user, workspace_id, session_id)
    db.delete(session)
    db.commit()
    return {"status": "deleted"}


def _prepare_chat(workspace_id: str, request: ChatRequest, *, allowed_document_ids=None) -> _ChatPlan:
    """Run the chat pipeline up to (but not including) generation.

    Free of tracing / LLM calls so the sync and streaming services reuse the
    message classification, retrieval and prompt building identically.
    """
    message = str(request.message or "").strip()
    selected_file = _normalize_selected_file(request.selected_file)
    history = [ChatMessage(role=item.role, content=item.content) for item in (request.history or [])]

    if not message:
        return _ChatPlan(kind="empty", message=message, selected_file=selected_file, history=history)

    if is_greeting(message):
        return _ChatPlan(
            kind="greeting",
            message=message,
            selected_file=selected_file,
            answer="Привет! Задайте вопрос по загруженным текстам.",
            summary="Привет! Задайте вопрос по загруженным текстам.",
            confidence_label="system",
            followup_suggestions=list(_GREETING_FOLLOWUPS),
            history=history,
        )

    kb = runtime.get_kb()
    file_filter = "all" if selected_file == "Все файлы" else selected_file

    if allowed_document_ids is not None:
        file_filter = allowed_document_ids

    is_corr = is_correction(message)
    is_fu = is_followup(message)
    search_query = message
    prev_question, prev_answer = "", ""
    if is_corr or is_fu:
        prev_question, prev_answer = get_last_qa([item.model_dump() for item in history])
        if prev_question:
            search_query = prev_question

    section_filter = kb.find_section_in_query(message, workspace_id=workspace_id)
    with model_scheduler.chat():
        context, raw_sources = kb.search_with_sources(
            search_query,
            file_filter=file_filter,
            section_filter=section_filter,
            workspace_id=workspace_id,
        )

    # Сужение по разделу иногда бьёт мимо, и хуже всего — на самой естественной
    # формулировке. Заголовок документа тоже попадает в список разделов, поэтому
    # вопрос «сколько дней отпуска по Трудовому кодексу» распознаётся как «дай мне
    # раздел "Трудовой кодекс РФ"» и поиск сжимается до титульной страницы: замер
    # 03.09.2026 на ТК РФ — 12 найденных фрагментов без фильтра против 1 с ним, и
    # дальше честное «информация не найдена». Пользователь при этом сделал ровно
    # то, что естественно, — назвал документ.
    #
    # Поэтому подозрительно узкий результат считаем промахом фильтра и повторяем
    # поиск по всему материалу. Цена ошибки несимметрична: лишний контекст модель
    # переживёт, а пустой ответ на осмысленный вопрос — нет. Тот же защитный
    # приём уже применён в конспектах (Stage 6g).
    if section_filter and len(raw_sources) < _SECTION_FILTER_MIN_SOURCES:
        with model_scheduler.chat():
            wider_context, wider_sources = kb.search_with_sources(
                search_query,
                file_filter=file_filter,
                section_filter=None,
                workspace_id=workspace_id,
            )
        if len(wider_sources) > len(raw_sources):
            logger.info(
                "section filter %r looked wrong (%d sources), retried without it (%d)",
                section_filter, len(raw_sources), len(wider_sources),
            )
            context, raw_sources = wider_context, wider_sources

    grouped_sources = _group_chat_sources(raw_sources, selected_file=selected_file)

    if not context:
        return _ChatPlan(
            kind="no_context",
            message=message,
            selected_file=selected_file,
            answer="НЕТ ИНФОРМАЦИИ - база пуста или файлы не проиндексированы.",
            summary="Ответ не найден в текущей базе знаний.",
            confidence_label="low",
            followup_suggestions=list(_NO_CONTEXT_FOLLOWUPS),
            # Nothing was retrieved, so there is nothing to cite - never show
            # sources under a "не найдено" answer.
            grouped_sources=[],
            history=history,
        )

    prompt_key = {
        "Обычный": "qa",
        "Кратко": "short_answer",
        "Подробно": "detailed_answer",
        "Только цитаты": "quotes_only",
    }.get(request.answer_mode, "qa")

    focused_prompt = ""
    focused_context = ""
    focused_sources = []
    if is_corr and prev_question and prev_answer:
        full_prompt = config.PROMPTS["correction"].format(
            system=config.SYSTEM_PROMPT,
            context=(
                "<document_context trust=\"untrusted\">\n"
                f"{context}\n</document_context>"
            ),
            prev_question=prev_question,
            prev_answer=prev_answer,
            correction=message,
        )
    else:
        history_ctx = history_to_context([item.model_dump() for item in history], n_last=3)
        if history_ctx:
            full_prompt = config.PROMPTS[prompt_key].format(
                system=config.SYSTEM_PROMPT,
                topic=message,
                context=(
                    f"ПРЕДЫДУЩИЙ ДИАЛОГ:\n{history_ctx}\n\n"
                    "<document_context trust=\"untrusted\">\n"
                    f"{context}\n</document_context>"
                ),
            )
        else:
            full_prompt = config.PROMPTS[prompt_key].format(
                system=config.SYSTEM_PROMPT,
                topic=message,
                context=(
                    "<document_context trust=\"untrusted\">\n"
                    f"{context}\n</document_context>"
                ),
            )

        if prompt_key == "qa" and len(raw_sources) > 3:
            # A broad context can bury a relevant first passage. Only retry a
            # refusal with the three leading passages; never discard the broad
            # answer or invent a response when the focused pass also refuses.
            focused_context = "\n\n---\n\n".join(context.split("\n\n---\n\n")[:3])
            focused_sources = _group_chat_sources(
                raw_sources[:3], selected_file=selected_file
            )
            focused_input = (
                "<document_context trust=\"untrusted\">\n"
                f"{focused_context}\n</document_context>"
            )
            if history_ctx:
                focused_input = f"ПРЕДЫДУЩИЙ ДИАЛОГ:\n{history_ctx}\n\n{focused_input}"
            focused_prompt = config.PROMPTS[prompt_key].format(
                system=config.SYSTEM_PROMPT,
                topic=message,
                context=focused_input,
            )

    return _ChatPlan(
        kind="generate",
        message=message,
        selected_file=selected_file,
        grouped_sources=grouped_sources,
        full_prompt=full_prompt,
        focused_prompt=focused_prompt,
        focused_context=focused_context,
        focused_sources=focused_sources,
        history=history,
        context=context,
    )


def _new_turn(history, message, answer):
    return history + [
        ChatMessage(role="user", content=message),
        ChatMessage(role="assistant", content=answer),
    ]


def _normalize_dashes(text: str) -> str:
    """Bot answers use a plain hyphen instead of em/en dashes (user preference)."""
    if not text:
        return text
    return text.replace("—", "-").replace("–", "-")


def _retry_refusal_with_focused_context(plan: _ChatPlan, llm):
    """Try the strongest passages once after an unsupported broad-context refusal."""
    if not plan.focused_prompt:
        return None
    try:
        answer = llm.call(plan.focused_prompt)
    except Exception:
        logger.exception("focused chat retry failed")
        return None
    guarded = guard_answer(
        question=plan.message,
        context=plan.focused_context,
        answer=answer,
    )
    if is_provider_filter(guarded.answer) or is_refusal(guarded.answer):
        return None
    sources = plan.focused_sources if guarded.allow_sources else []
    return guarded.answer, sources


def chat_service(
    workspace_id: str,
    request: ChatRequest,
    *,
    db=None,
    user_id: str | None = None,
    plan: "_ChatPlan | None" = None,
):
    """``plan`` - уже готовый результат :func:`_prepare_chat`.

    Нужен замерам: им требуется и контекст, ушедший в модель, и сам ответ, а
    поиск стоит дорого. Замер 04.09.2026 на СП 60: переранжирование 80
    кандидатов кросс-энкодером занимает 35 секунд, ответ модели - 2. Прогон,
    вызывавший _prepare_chat отдельно ради контекста, делал поиск дважды и
    тратил вдвое больше времени, ничего не измеряя точнее.

    В обычной работе не передаётся: маршруты вызывают chat_service как есть.
    """
    # Quota gate (Stage 12): once the daily chat cap is hit, the whole chat is
    # paywalled with a 402 — raised before any work / trace is opened. Cheap
    # paths (greeting / empty / no-context) never record usage.
    quota.check_quota(workspace_id, quota.ACTION_CHAT)

    # Chat history (Stage 19): persist only when the route supplied db + user.
    # An existing session_id is validated (404 if not the caller's / wrong ws)
    # and its messages become the LLM context — the server is the source of
    # truth, the client's history is ignored for an existing session.
    persist = db is not None and user_id is not None
    existing_session_id = request.session_id or None
    if persist and request.session_id:
        user = db.get(User, user_id)
        session = _load_owned_session(db, user, workspace_id, request.session_id)
        existing_session_id = session.id
        request = request.model_copy(update={"history": session_history_messages(session)})

    start_trace(
        kind="chat",
        request={
            "message": str(request.message or "").strip(),
            "selected_file": _normalize_selected_file(request.selected_file),
            "answer_mode": request.answer_mode,
            "history_len": str(len(request.history or [])),
        },
    )

    plan = plan if plan is not None else _prepare_chat(workspace_id, request)
    plan.answer = _normalize_dashes(plan.answer)

    if plan.kind == "empty":
        finish_trace(output="")
        return ChatResponse(
            answer="",
            history=plan.history,
            sources=[],
            diagnostics=format_last_trace(),
            trace=get_last_trace(),
            session_id=existing_session_id or "",
        )

    if plan.kind in ("greeting", "no_context"):
        finish_trace(output=plan.answer)
        session_id_out = ""
        if persist:
            meta = _assistant_meta(
                summary=plan.summary,
                confidence_label=plan.confidence_label,
                followups=plan.followup_suggestions,
                sources=plan.grouped_sources,
            )
            session_id_out = persist_chat_turn(
                workspace_id, user_id, existing_session_id, plan.message, plan.answer, meta
            )
        return ChatResponse(
            answer=plan.answer,
            summary=plan.summary,
            confidence_label=plan.confidence_label,
            followup_suggestions=plan.followup_suggestions,
            history=_new_turn(plan.history, plan.message, plan.answer),
            sources=plan.grouped_sources,
            diagnostics=format_last_trace(),
            trace=get_last_trace(),
            session_id=session_id_out,
        )

    llm = DiagnosticLLM(runtime.get_llm())
    answer = llm.call(plan.full_prompt)
    guarded = guard_answer(question=plan.message, context=plan.context, answer=answer)
    answer = guarded.answer
    if guarded.blocked or guarded.corrected:
        logger.warning(
            "answer guard action=%s workspace=%s",
            guarded.reason or "corrected",
            workspace_id,
        )
    # Show only sources used for the accepted answer. A refusal carries none.
    sources = plan.grouped_sources if guarded.allow_sources else []
    if is_refusal(answer):
        focused = _retry_refusal_with_focused_context(plan, llm)
        if focused is not None:
            answer, sources = focused
    if is_provider_filter(answer):
        # Ответ подменён фильтром провайдера (см. chat_utils). Показывать эту
        # отписку со ссылками на документ нельзя: человек примет реквизиты
        # своего файла за подтверждение текста, который к вопросу не относится.
        answer = _format_provider_filter_message()
        sources = []
    elif is_refusal(answer):
        answer = _format_refusal_answer(answer, plan.selected_file)
        sources = []
    else:
        # Ответ не отказ, но модель могла начать его словами «НЕТ ИНФОРМАЦИИ»
        # перед тем, как назвать фактическое (см. strip_refusal_prefix).
        answer = strip_refusal_prefix(answer)
    answer = _normalize_dashes(strip_internal_fragment_references(answer))

    quota.record_usage(workspace_id, quota.ACTION_CHAT, user_id=user_id, meta={"answer_mode": request.answer_mode})

    finish_trace(output=answer)
    trace = get_last_trace()
    summary = _build_answer_summary(answer)
    confidence_label = _build_confidence_label(
        sources, trace_status=(trace or {}).get("status", "ok")
    )
    followups = _build_followup_suggestions(
        message=plan.message,
        answer_mode=request.answer_mode,
        has_sources=bool(sources),
    )

    session_id_out = ""
    if persist:
        meta = _assistant_meta(
            summary=summary, confidence_label=confidence_label, followups=followups, sources=sources
        )
        session_id_out = persist_chat_turn(
            workspace_id, user_id, existing_session_id, plan.message, answer, meta
        )

    return ChatResponse(
        answer=answer,
        summary=summary,
        confidence_label=confidence_label,
        followup_suggestions=followups,
        history=_new_turn(plan.history, plan.message, answer),
        sources=sources,
        diagnostics=format_last_trace(),
        trace=trace,
        session_id=session_id_out,
    )


def _ndjson(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _chat_done_event(*, answer, summary, confidence_label, followups, history, sources, session_id="") -> str:
    return _ndjson(
        {
            "type": "done",
            "answer": answer,
            "summary": summary,
            "confidence_label": confidence_label,
            "followup_suggestions": followups,
            "history": [m.model_dump() for m in history],
            "sources": [s.model_dump() for s in sources],
            "session_id": session_id,
        }
    )


def chat_stream_service(
    workspace_id: str,
    request: ChatRequest,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    plan: _ChatPlan | None = None,
):
    """NDJSON token stream for the assistant (Stage 14).

    Quota is checked at the *route* before the stream starts (so a 402 is a
    normal JSON response, never a half-printed answer). Usage is metered only
    after a full successful generation — an aborted/failed stream doesn't
    charge, so the frontend's fallback to ``/api/chat`` can't double-charge.
    Short paths (empty / greeting / no-context) emit a single ``done`` event.

    Chat history (Stage 19): the route validates ``session_id`` ownership and
    injects the session's messages into ``request.history`` *before* calling
    this. The turn is persisted only when ``user_id`` is set and **after** the
    full answer — so an aborted stream (generator closed before this point)
    saves nothing (no truncated assistant turn). The created/updated session id
    rides the final ``done`` event.
    """
    persist = user_id is not None

    plan = plan or _prepare_chat(workspace_id, request)
    plan.answer = _normalize_dashes(plan.answer)

    if plan.kind == "empty":
        yield _chat_done_event(
            answer="", summary="", confidence_label="", followups=[], history=plan.history,
            sources=[], session_id=session_id or "",
        )
        return

    if plan.kind in ("greeting", "no_context"):
        session_id_out = ""
        if persist:
            meta = _assistant_meta(
                summary=plan.summary,
                confidence_label=plan.confidence_label,
                followups=plan.followup_suggestions,
                sources=plan.grouped_sources,
            )
            session_id_out = persist_chat_turn(
                workspace_id, user_id, session_id, plan.message, plan.answer, meta
            )
        yield _chat_done_event(
            answer=plan.answer,
            summary=plan.summary,
            confidence_label=plan.confidence_label,
            followups=plan.followup_suggestions,
            history=_new_turn(plan.history, plan.message, plan.answer),
            sources=plan.grouped_sources,
            session_id=session_id_out,
        )
        return

    llm = runtime.get_llm()
    parts: list[str] = []
    try:
        for token in llm.stream(plan.full_prompt):
            if token:
                token = _normalize_dashes(token)
                parts.append(token)
    except Exception:  # generation failed mid-stream — no metering, no save
        logger.exception("chat stream failed for workspace %s", workspace_id)
        yield _ndjson(
            {
                "type": "error",
                "message": "Не удалось получить ответ. Попробуйте повторить запрос.",
            }
        )
        return

    answer = "".join(parts).strip()
    guarded = guard_answer(question=plan.message, context=plan.context, answer=answer)
    answer = guarded.answer
    if guarded.blocked or guarded.corrected:
        logger.warning(
            "answer guard action=%s workspace=%s",
            guarded.reason or "corrected",
            workspace_id,
        )
    sources = plan.grouped_sources if guarded.allow_sources else []
    if is_refusal(answer):
        focused = _retry_refusal_with_focused_context(plan, llm)
        if focused is not None:
            answer, sources = focused
    # Same rule as the sync path: a refusal ships no sources (see chat_service).
    if is_provider_filter(answer):
        # Ответ подменён фильтром провайдера (см. chat_utils). Показывать эту
        # отписку со ссылками на документ нельзя: человек примет реквизиты
        # своего файла за подтверждение текста, который к вопросу не относится.
        answer = _format_provider_filter_message()
        sources = []
    elif is_refusal(answer):
        answer = _format_refusal_answer(answer, plan.selected_file)
        sources = []
    else:
        # Ответ не отказ, но модель могла начать его словами «НЕТ ИНФОРМАЦИИ»
        # перед тем, как назвать фактическое (см. strip_refusal_prefix).
        answer = strip_refusal_prefix(answer)
    answer = _normalize_dashes(strip_internal_fragment_references(answer))

    # Do not expose unvalidated or unformatted model tokens.
    if answer:
        yield _ndjson({"type": "token", "text": answer})

    # Meter only after the full answer is in hand (skipped on abort/disconnect,
    # since the generator is closed before reaching here).
    quota.record_usage(workspace_id, quota.ACTION_CHAT, user_id=user_id, meta={"answer_mode": request.answer_mode})

    summary = _build_answer_summary(answer)
    confidence_label = _build_confidence_label(sources, trace_status="ok")
    followups = _build_followup_suggestions(
        message=plan.message, answer_mode=request.answer_mode, has_sources=bool(sources)
    )

    # Persist only the *complete* turn (same point as metering — never on abort).
    session_id_out = ""
    if persist:
        meta = _assistant_meta(
            summary=summary, confidence_label=confidence_label, followups=followups, sources=sources
        )
        session_id_out = persist_chat_turn(workspace_id, user_id, session_id, plan.message, answer, meta)

    yield _chat_done_event(
        answer=answer,
        summary=summary,
        confidence_label=confidence_label,
        followups=followups,
        history=_new_turn(plan.history, plan.message, answer),
        sources=sources,
        session_id=session_id_out,
    )


def export_summary_docx_service(request: SummaryExportRequest):
    text = str(request.text or "").strip()
    if not text:
        return None

    return export_text_to_docx(
        title="Наставник - конспект",
        content=text,
        prefix="nastavnik_summary",
        name_parts=[
            _normalize_selected_file(request.selected_file),
            request.selected_section,
            request.summary_type,
        ],
    )


def get_latest_diagnostics_text():
    return format_last_trace()


def get_latest_diagnostics_json():
    return get_last_trace()


def get_billing_me(workspace_id: str) -> BillingMeResponse:
    """Current plan + per-action usage for the caller's workspace (Stage 12)."""
    return BillingMeResponse(**quota.usage_summary(workspace_id))


def _is_protected_admin(email: str) -> bool:
    """The configured root admin (Stage 13) — immune to demote/ban via the API."""
    root = config.ROOT_ADMIN_EMAIL
    return bool(root) and (email or "").strip().lower() == root


def _admin_user_out(user) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        can_create_courses=user.can_create_courses,
        is_protected=_is_protected_admin(user.email),
        plan=user.plan,
        created_at=user.created_at,
    )


def list_admin_users() -> AdminUsersResponse:
    """All users for the superuser user-management table (Stage 13)."""
    from src.db_models import User

    db = SessionLocal()
    try:
        users = db.execute(select(User).order_by(User.created_at.asc())).scalars().all()
        return AdminUsersResponse(users=[_admin_user_out(u) for u in users])
    finally:
        db.close()


def _active_superuser_count(db) -> int:
    from src.db_models import User

    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.is_superuser.is_(True), User.is_active.is_(True))
        )
        or 0
    )


def _load_admin_target(db, actor_id: str, target_id: str):
    """Resolve the target user for an admin action, enforcing the self-guard.

    A superuser may manage *other* users only — never themselves (can't
    self-ban / self-demote, which is the lockout footgun).
    """
    from src.db_models import User

    if target_id == actor_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_modify_self")
    target = db.get(User, target_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    from src.account_deletion import is_pending
    if is_pending(db, target.id):
        raise HTTPException(status_code=409, detail="account_deletion_pending")
    return target


def check_account_deletion_allowed(db, user) -> None:
    if config.ROOT_ADMIN_EMAIL and user.email == config.ROOT_ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="cannot_delete_root_admin")
    if user.is_superuser and _active_superuser_count(db) <= 1:
        raise HTTPException(status_code=400, detail="last_superuser")
    if user.is_superuser:
        # Prevent two admins queuing deletion of all remaining admin accounts.
        raise HTTPException(status_code=403, detail="demote_before_deletion")


def delete_account_service(db, user, *, deletion_job=None) -> None:
    """Permanently delete ``user`` and all data they own (Stage 23).

    Per owned workspace (personal + any courses they own) wipes the vector
    chunks (Chroma) and the files directory on disk, then ORM-deletes the user
    - cascading to memberships, owned workspaces (their documents / assignments
    / chat sessions / members), the user's own chat sessions, and email tokens.
    Assignment attempts the user made in *other* people's courses are removed
    explicitly (no relationship cascade reaches them).

    Append-only ledgers (audit / usage events) are intentionally kept for
    accounting/security history. They can contain identifying metadata; their
    retention and backup policy is separate from this cleanup operation.

    Guards: the protected root admin and the last active superuser can't be
    deleted (that would lock everyone out of admin).
    """
    from src.db_models import AssignmentAttempt, Document

    check_account_deletion_allowed(db, user)

    owned_workspace_ids = [ws.id for ws in user.owned_workspaces]
    if deletion_job is not None:
        owned_workspace_ids = sorted(set(owned_workspace_ids) | set(deletion_job.workspace_ids))
    try:
        kb = runtime.get_kb()
        # SQL also cascades uploaded Documents in other owners' workspaces.
        # Clean only this user's documents there, not the surrounding library.
        other_documents = list(db.scalars(select(Document).where(
            Document.owner_user_id == user.id,
            Document.workspace_id.not_in(owned_workspace_ids),
        )))
        for doc in other_documents:
            path = storage.resolve_stored_path(doc.stored_path, doc.workspace_id)
            if not storage.is_workspace_library_path(path, doc.workspace_id):
                raise ValueError("unsafe_document_path")
            library_root = os.path.realpath(storage.workspace_docs_dir(doc.workspace_id))
            expected_root = os.path.join(os.path.realpath(config.DOCS_DIR), storage.sanitize_filename(doc.workspace_id))
            if os.path.normcase(library_root) != os.path.normcase(expected_root):
                raise ValueError("unsafe_workspace_path")
            if os.path.commonpath([library_root, os.path.realpath(path)]) != library_root:
                raise ValueError("unsafe_document_path")
            kb.remove_chunks(workspace_id=doc.workspace_id, document_id=doc.id)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            db.delete(doc)
        for ws_id in owned_workspace_ids:
            # Cleanup is repeatable. Keep SQL ownership until *all* external
            # stores confirm success, including on retry after partial cleanup.
            kb.remove_orphan_chunks(workspace_id=ws_id, valid_document_ids=[])
            docs_dir = storage.workspace_docs_dir(ws_id)
            try:
                shutil.rmtree(docs_dir)
            except FileNotFoundError:
                # A previous attempt may already have removed this directory.
                if os.path.lexists(docs_dir):
                    raise
    except Exception as exc:
        db.rollback()
        logging.getLogger(__name__).exception("account deletion: external cleanup incomplete")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="account_cleanup_incomplete",
        ) from exc

    # Attempts in courses the user doesn't own - not reachable via cascade.
    db.query(AssignmentAttempt).filter(AssignmentAttempt.user_id == user.id).delete(
        synchronize_session=False
    )

    db.delete(user)
    if deletion_job is not None:
        from datetime import datetime, timezone
        deletion_job.status = "completed"
        deletion_job.error_code = ""
        deletion_job.next_attempt_at = None
        deletion_job.completed_at = datetime.now(timezone.utc)
    # User removal and completed receipt succeed or roll back together.
    db.commit()


def admin_set_user_role(*, actor_id: str, target_id: str, is_superuser: bool) -> AdminUserOut:
    """Promote/demote a user. Guards: self (400) + last active superuser (400)."""
    db = SessionLocal()
    try:
        target = _load_admin_target(db, actor_id, target_id)
        # The configured root admin can't be demoted by anyone.
        if _is_protected_admin(target.email) and not is_superuser:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="protected_admin")
        # Don't demote the last active superuser — never leave the system without
        # an admin (belt-and-braces on top of the self-guard).
        if target.is_superuser and target.is_active and not is_superuser:
            if _active_superuser_count(db) <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="last_superuser")
        target.is_superuser = is_superuser
        db.commit()
        db.refresh(target)
        return _admin_user_out(target)
    finally:
        db.close()


def admin_set_user_active(*, actor_id: str, target_id: str, is_active: bool) -> AdminUserOut:
    """Ban/unban a user. Guards: self (400) + last active superuser (400)."""
    db = SessionLocal()
    try:
        target = _load_admin_target(db, actor_id, target_id)
        # The configured root admin can't be banned by anyone.
        if _is_protected_admin(target.email) and not is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="protected_admin")
        # Don't ban the last active superuser.
        if target.is_superuser and target.is_active and not is_active:
            if _active_superuser_count(db) <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="last_superuser")
        target.is_active = is_active
        if not is_active:
            # Revoke existing sessions so the old cookie can't resume after an
            # un-ban — the user must log in fresh (Stage 13).
            from datetime import datetime, timezone

            target.tokens_valid_after = datetime.now(timezone.utc)
        db.commit()
        db.refresh(target)
        return _admin_user_out(target)
    finally:
        db.close()


def admin_set_user_can_create_courses(
    *, actor_id: str, target_id: str, can_create_courses: bool
) -> AdminUserOut:
    """Grant/revoke the course-creation capability (Stage 30).

    Self-guard via ``_load_admin_target``; no last-superuser concern (a
    superuser can create courses regardless of this flag).
    """
    db = SessionLocal()
    try:
        target = _load_admin_target(db, actor_id, target_id)
        target.can_create_courses = can_create_courses
        db.commit()
        db.refresh(target)
        return _admin_user_out(target)
    finally:
        db.close()


def get_admin_stats() -> AdminStats:
    """System-wide counts for the superuser admin overview (Stage 9b).

    Deliberately *not* workspace-scoped: this is superuser-only data covering
    every user, workspace, document and audit event in the instance. Uses
    ``COUNT(*)`` rather than loading rows so the numbers stay cheap as the
    tables grow. Opens its own short-lived session.
    """
    # Imported lazily to keep the module's top-level imports focused on the
    # RAG/summary path; the admin stats are a small side feature.
    from src.db_models import AuditEvent, Document, User, Workspace

    db = SessionLocal()
    try:
        def _count(model) -> int:
            return int(db.scalar(select(func.count()).select_from(model)) or 0)

        return AdminStats(
            users=_count(User),
            workspaces=_count(Workspace),
            documents=_count(Document),
            audit_events=_count(AuditEvent),
        )
    finally:
        db.close()


def reconcile_database_service() -> ReconcileResponse:
    """Instance-wide reconcile of KB vectors against the ``Document`` table.

    Superuser-only and deliberately *not* workspace-scoped — like
    ``get_admin_stats`` it spans every workspace present in the index, scrubbing
    orphan chunks left by a failed best-effort delete or a dev DB reset.
    """
    # Imported lazily to keep the module's top-level imports focused on the
    # RAG/summary path; reconcile is a rare admin maintenance action.
    from src import maintenance

    summary = maintenance.reconcile_all_workspaces()
    return ReconcileResponse(**summary)
