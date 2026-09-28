"""FastAPI entrypoint for the Nastavnik knowledge service.

Authentication gates every non-public endpoint (Stage 3a). ``workspace_id``
is plumbed from ``current_user.personal_workspace.id`` through
``app_services`` to ``KnowledgeBase`` and ``summary_engine`` (Stages 3b/4).
As of Stage 6e there is no implicit fallback: KB / summary methods require
``workspace_id`` at every call site, and the legacy ``DEFAULT_WORKSPACE_ID``
constant is gone.

Endpoint access tiers:

* **Public** (no auth required): ``/api/health``, ``/api/auth/register``,
  ``/api/auth/login``.
* **Authenticated** (any logged-in user): every endpoint that uses the
  ``get_current_workspace_id`` dependency (transitively requires auth) plus
  ``/api/auth/me`` and ``/api/auth/logout``.
* **Superuser-only** (``is_superuser=True``): ``/api/diagnostics/*`` and
  ``/api/admin/*`` (audit log + system stats).
* **Service-only**: ``/api/integrations/prodigy/chat`` requires a separate
  revocable credential bound to one organization and one workspace.

``get_current_workspace_id`` validates the tab's X-Workspace-ID against the
authenticated user's membership. The identifier selects a destination; it
never grants access. Requests without this header fail with 428; clients must
send an explicit destination, including when using the personal workspace.
"""

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import config
from src import app_services as services
from src import audit_service
from src import auth_service
from src import courses
from src import quota
from src.auth_api import router as auth_router
from src.prodigy_integration import router as prodigy_router
from src.auth_service import get_current_user, require_superuser
from src.rate_limit import client_ip, limiter
from src.csrf import CsrfOriginMiddleware
from src.security_headers import SecurityHeadersMiddleware
from src.obs import RequestLogMiddleware, configure_logging
from src.db import get_db
from src.db_models import User, Workspace
from src.api_models import (
    AdminActiveUpdate,
    AdminCourseCreatorUpdate,
    AdminRoleUpdate,
    AdminStats,
    AdminUserOut,
    AdminUsersResponse,
    AuditEventOut,
    AuditLogResponse,
    BillingMeResponse,
    ReconcileResponse,
    ChatRequest,
    ChatResponse,
    MaterialActionResponse,
    MaterialFromUrlRequest,
    MaterialProgressResponse,
    MaterialsResponse,
    SectionsResponse,
    SummaryExportRequest,
    SummaryRequest,
    SummaryResponse,
    SystemStatus,
    WorkspaceOut,
    WorkspacesResponse,
    ArchiveCourseRequest,
    CreateCourseRequest,
    JoinCourseRequest,
    RenameCourseRequest,
    SetMemberRoleRequest,
    JoinCodeUpdate,
    JoinCodeResponse,
    CourseDetail,
    CourseInviteRequest,
    CourseInviteOut,
    AcceptCourseInviteRequest,
    MemberGroupUpdate,
    PilotDashboard,
    StudyRequest,
    StudyResponse,
    AssignmentForTaking,
    AssignmentListItem,
    AssignmentListResponse,
    AssignmentResults,
    AttemptResult,
    CreateAssignmentRequest,
    PublishAssignmentRequest,
    SubmitAttemptRequest,
    ChatSessionDetail,
    ChatSessionOut,
    ChatSessionsResponse,
    RenameChatRequest,
)


# Security fail-fast (Stage 41): refuse to start a production-like deployment
# with the public dev JWT secret. Runs at import, so it also fires under
# ``uvicorn api_app:app`` (bypassing run_api.py / validate_config).
config.assert_jwt_secret_safe()
from src.api_lifespan import lifespan
from src.operation_guard import CleanupDrainMiddleware

app = FastAPI(title="Наставник - сервис знаний", version="0.1.0", lifespan=lifespan)
app.add_middleware(CleanupDrainMiddleware)

# Observability (Stage 34): structured request logging + X-Request-ID header.
# Pure-ASGI, so it doesn't buffer the NDJSON chat stream.
configure_logging()
# CSRF Origin/Referer check (Stage 41) added before the request logger so that
# rejected (403) requests are still logged. Both are pure-ASGI (stream-safe).
app.add_middleware(CsrfOriginMiddleware)
app.add_middleware(RequestLogMiddleware)
# Security headers (Stage 46), added last so it's the outermost layer and stamps
# every response - including a 403 from CSRF or a 429 from the rate limiter.
# Pure-ASGI (stream-safe). Mirrors docker/Caddyfile so headers apply even when
# the backend is served without the Caddy HTTPS reverse proxy.
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting (Stage 9a): register the shared limiter + 429 handler. Per-route
# limits are applied with @limiter.limit on auth/chat/upload.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Quota enforcement (Stage 12): a plan limit hit surfaces as 402 Payment
# Required with a structured payload the frontend turns into an "upgrade" CTA.
async def _quota_exceeded_handler(request: Request, exc: quota.QuotaExceeded):
    del request
    return JSONResponse(
        status_code=402,
        content={
            "error": "quota_exceeded",
            "action": exc.action,
            "limit": exc.limit,
            "used": exc.used,
            "plan": exc.plan,
        },
    )


app.add_exception_handler(quota.QuotaExceeded, _quota_exceeded_handler)

# Stage 9a: warn loudly if a prod-like deployment (Postgres) serves the auth
# cookie without the Secure flag. Stays quiet for local dev/CI on SQLite.
if not config.AUTH_COOKIE_SECURE and config.DATABASE_URL.startswith("postgres"):
    logging.getLogger("vedomo.security").warning(
        "AUTH_COOKIE_SECURE is false but DATABASE_URL looks like production "
        "(Postgres). Set AUTH_COOKIE_SECURE=true when serving over HTTPS so the "
        "auth cookie is not sent over plain HTTP."
    )

app.include_router(auth_router)
app.include_router(prodigy_router)


def get_current_workspace_id(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    requested_workspace: Annotated[str | None, Header(alias="X-Workspace-ID", max_length=36)] = None,
) -> str:
    """Require and validate the explicit tab destination.

    Authentication and current membership are required on every request.
    Explicit invalid/archived/revoked destinations fail closed, never falling
    back to another workspace. Action-specific permissions are checked by
    require_permission after this dependency.
    """
    # Browser tabs name their own destination. Never fall back on a failed
    # explicit selection: a write must not land in a different workspace.
    if requested_workspace is not None:
        workspace = db.get(Workspace, requested_workspace)
        if (
            workspace is None
            or workspace.is_archived
            or courses.role_in_workspace(db, current_user.id, workspace) is None
        ):
            raise HTTPException(status_code=403, detail="workspace_unavailable")
        return workspace.id

    raise HTTPException(status_code=428, detail="workspace_required")


WorkspaceId = Annotated[str, Depends(get_current_workspace_id)]
_ADMIN = [Depends(require_superuser)]


def require_permission(action: str):
    """Dependency factory: gate an endpoint behind ``can(user, action, workspace)``.

    Resolves the active workspace (today the personal workspace; Stage 15-3
    makes it selectable) and the caller, then enforces the single authorization
    decider (``src.courses.can``). A non-member or insufficient role gets 403.
    Returns the validated ``workspace_id`` so the endpoint reads like
    ``WorkspaceId`` — only the permitted action differs.

    Inert today (a personal workspace's owner may do everything) but correct:
    when 15-3 makes a course selectable, a ``student`` is already blocked from
    upload/delete/manage at the backend, not just by hidden buttons.
    """

    def _dep(
        workspace_id: WorkspaceId,
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> str:
        workspace = db.get(Workspace, workspace_id)
        if workspace is None or not courses.can(db, current_user, action, workspace):
            raise HTTPException(status_code=403, detail="forbidden")
        return workspace_id

    return _dep


# Permission-gated workspace dependencies (Stage 15-2): each enforces its action
# via ``can()`` before the endpoint body runs.
WorkspaceView = Annotated[str, Depends(require_permission(courses.ACTION_VIEW))]
WorkspaceChat = Annotated[str, Depends(require_permission(courses.ACTION_CHAT))]
WorkspaceSummary = Annotated[str, Depends(require_permission(courses.ACTION_SUMMARY))]
WorkspaceStudy = Annotated[str, Depends(require_permission(courses.ACTION_STUDY))]
WorkspaceUpload = Annotated[str, Depends(require_permission(courses.ACTION_UPLOAD))]
WorkspaceReindex = Annotated[str, Depends(require_permission(courses.ACTION_REINDEX))]
WorkspaceDeleteMaterial = Annotated[str, Depends(require_permission(courses.ACTION_DELETE_MATERIAL))]
WorkspaceTakeAssignment = Annotated[str, Depends(require_permission(courses.ACTION_TAKE_ASSIGNMENT))]
WorkspaceManageAssignments = Annotated[
    str, Depends(require_permission(courses.ACTION_MANAGE_ASSIGNMENTS))
]


def _client_ip(request: Request) -> str:
    # Proxy-aware (Stage 46): the real client IP behind Caddy. See
    # src.rate_limit.client_ip - falls back to the raw peer when not behind a
    # trusted proxy (TRUST_PROXY_IP off).
    return client_ip(request)


_MAX_UPLOAD_BYTES = getattr(config, "MAX_UPLOAD_BYTES", 50 * 1024 * 1024)


async def _read_upload_within_limit(request: Request, file: UploadFile) -> bytes:
    """Read an upload without letting a huge file OOM the server (Stage 9a).

    Two layers: a fast Content-Length reject before touching the body, then a
    streamed read with a hard cap so a missing/lying Content-Length still can't
    buffer more than the limit. Oversized uploads raise 413; the per-file size
    check in the service layer stays as defence-in-depth.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="file_too_large")
        except ValueError:
            pass  # malformed header — fall through to the streamed cap

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB at a time
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="file_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@app.get("/api/health")
def health():
    """Public liveness probe — used by uptime monitoring."""
    return {"status": "ok"}


@app.get("/api/system/status", response_model=SystemStatus)
def system_status(workspace_id: WorkspaceView):
    return services.get_system_status(workspace_id)


@app.get("/api/billing/me", response_model=BillingMeResponse)
def billing_me(workspace_id: WorkspaceView):
    """The caller's plan + per-action usage/limits (Stage 12) for the usage UI."""
    return services.get_billing_me(workspace_id)


@app.get("/api/materials", response_model=MaterialsResponse)
def materials(workspace_id: WorkspaceView):
    return services.list_materials(workspace_id)


@app.post("/api/materials/upload", response_model=MaterialActionResponse)
@limiter.limit(config.RATE_LIMIT_UPLOAD)
async def material_upload(
    workspace_id: WorkspaceUpload,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    file: UploadFile = File(...),
):
    """Upload requires both ``workspace_id`` (where the file is indexed) and
    ``user.id`` (recorded as ``Document.owner_user_id``). ``workspace_id`` is
    transitively derived from ``current_user`` via ``get_current_workspace_id``,
    but we keep ``current_user`` as a separate dependency so the owner id is
    explicit at the call site."""
    # Quota gate (Stage 12): reject before reading the (possibly 50 MB) body or
    # queuing the expensive indexing job when the material cap is already hit.
    quota.check_quota(workspace_id, quota.ACTION_UPLOAD)
    content = await _read_upload_within_limit(request, file)
    result = services.start_upload_material_service(
        workspace_id, current_user.id, file.filename, content
    )
    audit_service.record(
        audit_service.ACTION_UPLOAD,
        user_id=current_user.id,
        workspace_id=workspace_id,
        target=file.filename or "",
        ip=_client_ip(request),
    )
    return result


@app.post("/api/materials/from-url", response_model=MaterialActionResponse)
@limiter.limit(config.RATE_LIMIT_UPLOAD)
def material_from_url(
    workspace_id: WorkspaceUpload,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    payload: MaterialFromUrlRequest,
):
    """Ingest a web page by URL as a material (Stage 56). Same upload permission,
    quota and audit as a file upload; the page is fetched, its text extracted and
    indexed through the normal pipeline. Sync route - the blocking fetch runs in
    FastAPI's threadpool, not the event loop."""
    quota.check_quota(workspace_id, quota.ACTION_UPLOAD)
    result = services.ingest_url_service(workspace_id, current_user.id, payload.url)
    audit_service.record(
        audit_service.ACTION_UPLOAD,
        user_id=current_user.id,
        workspace_id=workspace_id,
        target=result.material_name or payload.url[:200],
        ip=_client_ip(request),
    )
    return result


@app.post("/api/materials/from-image", response_model=MaterialActionResponse)
@limiter.limit(config.RATE_LIMIT_UPLOAD)
async def material_from_image(
    workspace_id: WorkspaceUpload,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    file: UploadFile = File(...),
):
    """Photo → Vision → material (Stage 56). Same upload permission, quota and
    audit as a file upload; the blocking vision transcription runs in a threadpool
    so it never stalls the event loop, then indexing goes through the normal
    pipeline. Better than plain OCR for formulas/diagrams."""
    quota.check_quota(workspace_id, quota.ACTION_UPLOAD)
    content = await _read_upload_within_limit(request, file)
    result = await run_in_threadpool(
        services.ingest_image_service,
        workspace_id,
        current_user.id,
        file.filename,
        content,
        file.content_type,
    )
    audit_service.record(
        audit_service.ACTION_UPLOAD,
        user_id=current_user.id,
        workspace_id=workspace_id,
        target=result.material_name or (file.filename or ""),
        ip=_client_ip(request),
    )
    return result


@app.get("/api/materials/progress", response_model=MaterialProgressResponse)
def material_progress(workspace_id: WorkspaceView):
    return services.get_material_progress(workspace_id)


@app.post("/api/materials/cancel", response_model=MaterialActionResponse)
def material_cancel(workspace_id: WorkspaceUpload):
    """Request cancellation of the workspace's in-flight material job."""
    return services.cancel_material_service(workspace_id)


@app.get(
    "/api/materials/{file_name}/sections",
    response_model=SectionsResponse,
)
def material_sections(workspace_id: WorkspaceView, file_name: str):
    return services.list_sections(workspace_id, file_filter=file_name)


@app.post("/api/materials/reindex", response_model=MaterialActionResponse)
def materials_reindex(
    workspace_id: WorkspaceReindex,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
):
    result = services.start_reindex_material_service(workspace_id)
    audit_service.record(
        audit_service.ACTION_REINDEX,
        user_id=current_user.id,
        workspace_id=workspace_id,
        target="*",
        ip=_client_ip(request),
    )
    return result


@app.post(
    "/api/materials/{file_name}/reindex",
    response_model=MaterialActionResponse,
)
def material_reindex(
    workspace_id: WorkspaceReindex,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    file_name: str,
):
    result = services.start_reindex_material_service(workspace_id, file_name=file_name)
    audit_service.record(
        audit_service.ACTION_REINDEX,
        user_id=current_user.id,
        workspace_id=workspace_id,
        target=file_name,
        ip=_client_ip(request),
    )
    return result


@app.delete(
    "/api/materials/{file_name}",
    response_model=MaterialActionResponse,
)
def material_delete(
    workspace_id: WorkspaceDeleteMaterial,
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
    file_name: str,
):
    result = services.start_delete_material_service(workspace_id, file_name)
    audit_service.record(
        audit_service.ACTION_DELETE,
        user_id=current_user.id,
        workspace_id=workspace_id,
        target=file_name,
        ip=_client_ip(request),
    )
    return result


@app.post("/api/summaries", response_model=SummaryResponse)
def summaries(workspace_id: WorkspaceSummary, request: SummaryRequest):
    return services.generate_summary_service(workspace_id, request)


@app.post("/api/study", response_model=StudyResponse)
def study(workspace_id: WorkspaceStudy, request: StudyRequest):
    """Flashcards / quiz from the selected material (Stage 17). Quota (402) is
    enforced in the service; a bad-JSON model reply returns ok=false, not 500."""
    return services.generate_study_service(workspace_id, request)


# --- Assignments (Stage 18): teacher-assigned tests + results ---------------
@app.post("/api/assignments", response_model=AssignmentListItem)
def create_assignment(
    workspace_id: WorkspaceManageAssignments,
    payload: CreateAssignmentRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Save a curated quiz as a draft assignment (manager). Generation already
    cost a ``study`` unit via the trainer; saving is free."""
    return services.create_assignment_service(db, workspace_id, current_user, payload)


@app.get("/api/assignments", response_model=AssignmentListResponse)
def list_assignments(
    workspace_id: WorkspaceView,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """List assignments for the active course — role-aware (manager: all + stats;
    student: published + own status)."""
    return services.list_assignments_service(db, workspace_id, current_user)


@app.get("/api/assignments/{assignment_id}", response_model=AssignmentForTaking)
def get_assignment(
    workspace_id: WorkspaceTakeAssignment,
    assignment_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Get one assignment to take. Correct answers are withheld until the student
    has submitted (then the full result is returned)."""
    return services.get_assignment_for_taking_service(db, workspace_id, current_user, assignment_id)


@app.post("/api/assignments/{assignment_id}/submit", response_model=AttemptResult)
def submit_assignment(
    workspace_id: WorkspaceTakeAssignment,
    assignment_id: str,
    payload: SubmitAttemptRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Submit answers → the server scores them (never the client) and stores the
    attempt (one per student, re-submit overwrites). Returns the review."""
    return services.submit_attempt_service(db, workspace_id, current_user, assignment_id, payload.answers)


@app.post("/api/assignments/{assignment_id}/publish", response_model=AssignmentListItem)
def publish_assignment(
    workspace_id: WorkspaceManageAssignments,
    assignment_id: str,
    payload: PublishAssignmentRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Publish / unpublish an assignment (manager). Students see only published."""
    return services.publish_assignment_service(
        db, workspace_id, current_user, assignment_id, payload.published
    )


@app.delete("/api/assignments/{assignment_id}")
def delete_assignment(
    workspace_id: WorkspaceManageAssignments,
    assignment_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Delete an assignment and its attempts (manager)."""
    return services.delete_assignment_service(db, workspace_id, current_user, assignment_id)


@app.get("/api/assignments/{assignment_id}/results", response_model=AssignmentResults)
def assignment_results(
    workspace_id: WorkspaceManageAssignments,
    assignment_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Per-student attempts + analytics (manager only)."""
    return services.assignment_results_service(db, workspace_id, current_user, assignment_id)


@app.get("/api/assignments/{assignment_id}/results.xlsx")
def assignment_results_xlsx(
    workspace_id: WorkspaceManageAssignments,
    assignment_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Results as an .xlsx download (Stage 55, manager only): proper column widths
    Excel can't get from a CSV, bold + frozen header."""
    data = services.assignment_results_xlsx_service(db, workspace_id, current_user, assignment_id)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="results-{assignment_id[:8]}.xlsx"'},
    )


@app.post("/api/chat", response_model=ChatResponse)
@limiter.limit(config.RATE_LIMIT_CHAT)
def chat(
    workspace_id: WorkspaceChat,
    request: Request,
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.chat_service(workspace_id, payload, db=db, user_id=current_user.id)


@app.post("/api/chat/stream")
@limiter.limit(config.RATE_LIMIT_CHAT)
def chat_stream(
    workspace_id: WorkspaceChat,
    request: Request,
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Streaming variant (Stage 14): NDJSON ``token`` events then a final
    ``done`` with sources/confidence/followups/history. Quota is checked here
    *before* the stream so a 402 is a normal JSON response, not a half-printed
    answer; usage is metered inside the generator only after a full answer.

    Chat history (Stage 19): an existing ``session_id`` is validated for
    ownership here (404 before any streaming, never mid-stream) and its messages
    are injected as context; the new turn is persisted by the generator only
    after the full answer (so an aborted stream saves nothing)."""
    quota.check_quota(workspace_id, quota.ACTION_CHAT)
    session_id = None
    if payload.session_id:
        session = services._load_owned_session(db, current_user, workspace_id, payload.session_id)
        session_id = session.id
        payload = payload.model_copy(update={"history": services.session_history_messages(session)})
    return StreamingResponse(
        services.chat_stream_service(
            workspace_id, payload, user_id=current_user.id, session_id=session_id
        ),
        media_type="application/x-ndjson",
    )


@app.get("/api/chat/sessions", response_model=ChatSessionsResponse)
def list_chat_sessions(
    workspace_id: WorkspaceChat,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """The caller's own chats in the active workspace (owner-only, never another
    user's — a teacher can't read a student's chats)."""
    return services.list_chat_sessions_service(db, current_user, workspace_id)


@app.get("/api/chat/sessions/{session_id}", response_model=ChatSessionDetail)
def get_chat_session(
    workspace_id: WorkspaceChat,
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Full messages of one of the caller's chats; 404 if it isn't theirs."""
    return services.get_chat_session_service(db, current_user, workspace_id, session_id)


@app.post("/api/chat/sessions/{session_id}/rename", response_model=ChatSessionOut)
def rename_chat_session(
    workspace_id: WorkspaceChat,
    session_id: str,
    payload: RenameChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.rename_chat_session_service(db, current_user, workspace_id, session_id, payload.title)


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(
    workspace_id: WorkspaceChat,
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.delete_chat_session_service(db, current_user, workspace_id, session_id)


@app.post("/api/exports/summary")
def export_summary(workspace_id: WorkspaceId, request: SummaryExportRequest):
    # Stage 3b: workspace_id is accepted at the endpoint to keep the auth
    # gate in place even though DOCX assembly is workspace-agnostic.
    del workspace_id
    path = services.export_summary_docx_service(request)
    if not path:
        return JSONResponse(status_code=400, content={"error": "empty_summary"})

    # Delete the generated file once it has been streamed (Stage 41) so exports
    # don't accumulate on disk.
    def _cleanup(p: str = path):
        try:
            os.remove(p)
        except OSError:
            pass

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=Path(path).name,
        background=BackgroundTask(_cleanup),
    )


@app.get("/api/workspaces", response_model=WorkspacesResponse)
def list_workspaces(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """The caller's spaces for the course switcher (Stage 15-3): the personal
    workspace plus every course they own or joined, each with the caller's
    role, and which one is currently active."""
    return services.list_user_workspaces_service(db, current_user)


@app.post("/api/workspaces/{workspace_id}/activate", response_model=WorkspaceOut)
def activate_workspace(
    workspace_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Switch the caller's active workspace (Stage 15-3). The target id is
    validated against membership server-side — unknown → 404, non-member → 403
    — so ``workspace_id`` is never trusted blindly from the client."""
    return services.activate_workspace_service(db, current_user, workspace_id)


# --- Courses (Stage 15-4): create / join / manage --------------------------
@app.post("/api/courses", response_model=WorkspaceOut)
def create_course(
    payload: CreateCourseRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Create a course (any authenticated user); the creator becomes owner and
    is switched into it."""
    out = services.create_course_service(db, current_user, payload.name)
    audit_service.record(
        audit_service.ACTION_CREATE_COURSE,
        user_id=current_user.id,
        workspace_id=out.id,
        target=out.name,
        ip=_client_ip(request),
    )
    return out


@app.post("/api/courses/join", response_model=WorkspaceOut)
def join_course(
    payload: JoinCourseRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Join a course by code (idempotent) and switch into it."""
    return services.join_course_service(db, current_user, payload.code)


@app.post("/api/courses/{course_id}/leave")
def leave_course(
    course_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Leave a course (any member except the owner)."""
    return services.leave_course_service(db, current_user, course_id)


@app.get("/api/courses/{course_id}", response_model=CourseDetail)
def course_detail(
    course_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Course management view (members + join settings) — managers only."""
    return services.course_detail_service(db, current_user, course_id)


@app.delete("/api/courses/{course_id}")
def delete_course(
    course_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Delete a course and scrub its materials/vectors (owner only)."""
    return services.delete_course_service(db, current_user, course_id)


@app.post("/api/courses/{course_id}/archive")
def archive_course(
    course_id: str,
    payload: ArchiveCourseRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Freeze (or restore) a course — owner only (Stage 22). An archived course
    is read-only and out of the active switcher."""
    return services.archive_course_service(db, current_user, course_id, payload.archived)


@app.post("/api/courses/{course_id}/copy", response_model=WorkspaceOut)
def copy_course(
    course_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Copy a course into a fresh one for a new semester (owner only): materials
    + assignments-as-drafts, no members/attempts/chats. Activates the new one."""
    return services.copy_course_service(db, current_user, course_id)


@app.post("/api/courses/{course_id}/rename", response_model=CourseDetail)
def rename_course(
    course_id: str,
    payload: RenameCourseRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.rename_course_service(db, current_user, course_id, payload.name)


@app.post("/api/courses/{course_id}/join-code", response_model=JoinCodeResponse)
def update_join_code(
    course_id: str,
    payload: JoinCodeUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Rotate the join code and/or enable/disable joining (managers only)."""
    return services.set_join_code_service(db, current_user, course_id, payload.enabled, payload.rotate)


@app.post("/api/courses/{course_id}/members/{user_id}/role", response_model=CourseDetail)
def set_member_role(
    course_id: str,
    user_id: str,
    payload: SetMemberRoleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.set_member_role_service(db, current_user, course_id, user_id, payload.role)


@app.delete("/api/courses/{course_id}/members/{user_id}", response_model=CourseDetail)
def remove_member(
    course_id: str,
    user_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.remove_member_service(db, current_user, course_id, user_id)


@app.post("/api/courses/{course_id}/invitations", response_model=CourseInviteOut, status_code=201)
def create_course_invitation(
    course_id: str,
    payload: CourseInviteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    result = services.create_course_invitation_service(
        db, current_user, course_id, email=str(payload.email),
        role=payload.role, group_name=payload.group_name,
    )
    audit_service.record(
        audit_service.ACTION_INVITE_COURSE_MEMBER, user_id=current_user.id,
        # Keep the append-only audit useful without retaining an email address
        # in its free-text target field.
        workspace_id=course_id, target=result.id, ip=_client_ip(request),
    )
    return result


@app.delete("/api/courses/{course_id}/invitations/{invitation_id}", response_model=CourseDetail)
def revoke_course_invitation(
    course_id: str,
    invitation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    result = services.revoke_course_invitation_service(
        db, current_user, course_id, invitation_id
    )
    audit_service.record(
        audit_service.ACTION_REVOKE_COURSE_INVITE, user_id=current_user.id,
        workspace_id=course_id, target=invitation_id, ip=_client_ip(request),
    )
    return result


@app.post("/api/invitations/accept", response_model=WorkspaceOut)
def accept_course_invitation(
    payload: AcceptCourseInviteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    result = services.accept_course_invitation_service(db, current_user, payload.token)
    audit_service.record(
        audit_service.ACTION_ACCEPT_COURSE_INVITE, user_id=current_user.id,
        workspace_id=result.id, target="", ip=_client_ip(request),
    )
    return result


@app.post("/api/courses/{course_id}/members/{user_id}/group", response_model=CourseDetail)
def set_member_group(
    course_id: str,
    user_id: str,
    payload: MemberGroupUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    result = services.set_member_group_service(
        db, current_user, course_id, user_id, payload.group_name
    )
    audit_service.record(
        audit_service.ACTION_UPDATE_MEMBER_GROUP, user_id=current_user.id,
        workspace_id=course_id, target=user_id, ip=_client_ip(request),
    )
    return result


@app.get("/api/courses/{course_id}/pilot", response_model=PilotDashboard)
def course_pilot_dashboard(
    course_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return services.course_pilot_dashboard_service(db, current_user, course_id)


@app.get("/api/courses/{course_id}/pilot.csv")
def course_pilot_report_csv(
    course_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    data = services.course_pilot_report_csv_service(db, current_user, course_id)
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pilot-{course_id[:8]}.csv"'},
    )


@app.get("/api/diagnostics/latest", dependencies=_ADMIN)
def diagnostics_latest():
    return {"text": services.get_latest_diagnostics_text()}


@app.get("/api/diagnostics/latest.json", dependencies=_ADMIN)
def diagnostics_latest_json():
    return services.get_latest_diagnostics_json() or {}


# ---------------------------------------------------------------------------
# Admin (superuser-only). Audit log + system stats for the admin screen
# (Stage 9b). Both gated by ``_ADMIN`` (``require_superuser``): a regular
# authenticated user gets 403, an anonymous caller gets 401.
# ---------------------------------------------------------------------------


@app.get("/api/admin/audit", response_model=AuditLogResponse, dependencies=_ADMIN)
def admin_audit(limit: int = 50):
    """Most recent audit events, newest first. ``limit`` is clamped server-side
    (see ``audit_service.list_recent``); no pagination yet (Stage 9b scope)."""
    events = audit_service.list_recent(limit)
    return AuditLogResponse(events=[AuditEventOut.model_validate(e) for e in events])


@app.get("/api/admin/stats", response_model=AdminStats, dependencies=_ADMIN)
def admin_stats():
    """Instance-wide counts (users / workspaces / documents / audit events)."""
    return services.get_admin_stats()


@app.get("/api/admin/users", response_model=AdminUsersResponse, dependencies=_ADMIN)
def admin_users():
    """All users for the superuser user-management table (Stage 13)."""
    return services.list_admin_users()


@app.post("/api/admin/users/{user_id}/role", response_model=AdminUserOut, dependencies=_ADMIN)
def admin_user_role(
    user_id: str,
    payload: AdminRoleUpdate,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Promote/demote a user. Self-guard + last-superuser guard live in the
    service (400). 401 anon / 403 non-superuser via _ADMIN."""
    result = services.admin_set_user_role(
        actor_id=current_user.id, target_id=user_id, is_superuser=payload.is_superuser
    )
    audit_service.record(
        audit_service.ACTION_PROMOTE if payload.is_superuser else audit_service.ACTION_DEMOTE,
        user_id=current_user.id,
        target=user_id,
        ip=_client_ip(request),
    )
    return result


@app.post("/api/admin/users/{user_id}/active", response_model=AdminUserOut, dependencies=_ADMIN)
def admin_user_active(
    user_id: str,
    payload: AdminActiveUpdate,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Ban/unban a user (immediate — a ban kills the live session via
    get_current_user). Same guards as the role endpoint."""
    result = services.admin_set_user_active(
        actor_id=current_user.id, target_id=user_id, is_active=payload.is_active
    )
    audit_service.record(
        audit_service.ACTION_UNBAN if payload.is_active else audit_service.ACTION_BAN,
        user_id=current_user.id,
        target=user_id,
        ip=_client_ip(request),
    )
    return result


@app.post(
    "/api/admin/users/{user_id}/course-creator",
    response_model=AdminUserOut,
    dependencies=_ADMIN,
)
def admin_user_course_creator(
    user_id: str,
    payload: AdminCourseCreatorUpdate,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Grant/revoke the course-creation capability (Stage 30). Self-guard in the
    service (400); 401 anon / 403 non-superuser via _ADMIN."""
    result = services.admin_set_user_can_create_courses(
        actor_id=current_user.id,
        target_id=user_id,
        can_create_courses=payload.can_create_courses,
    )
    audit_service.record(
        audit_service.ACTION_GRANT_COURSE_CREATE
        if payload.can_create_courses
        else audit_service.ACTION_REVOKE_COURSE_CREATE,
        user_id=current_user.id,
        target=user_id,
        ip=_client_ip(request),
    )
    return result


@app.post("/api/admin/reconcile", response_model=ReconcileResponse, dependencies=_ADMIN)
def admin_reconcile(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Scrub orphan KB chunks instance-wide (Stage 9c).

    Reconciles ChromaDB against the ``Document`` table for every workspace,
    removing chunks whose ``document_id`` has no backing row. Instance-wide by
    design (like `/api/admin/stats`), so it never takes a workspace id from the
    client. Idempotent."""
    result = services.reconcile_database_service()
    audit_service.record(
        audit_service.ACTION_RECONCILE,
        user_id=current_user.id,
        target="*",
        ip=_client_ip(request),
    )
    return result
