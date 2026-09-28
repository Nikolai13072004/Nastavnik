"""Pydantic models for the Vedomo API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class MaterialInfo(BaseModel):
    # Stage 3c: ``id`` is the ``Document.id`` (UUID) and is the canonical
    # handle for delete/reindex/sections going forward. ``name`` stays as the
    # human-visible ``Document.original_name`` and remains the URL parameter
    # for the legacy ``/api/materials/{file_name}/*`` routes until the
    # frontend migrates. Empty default keeps the type backwards-compatible
    # for direct/test paths that have no Document row.
    id: str = ""
    name: str
    sections_count: int = 0
    quality_label: str = "ready"
    quality_reason: str = ""
    status: str = "ready"
    # True when extracted via OCR (scans / image-based slides) - the UI shows a
    # "verify formulas/diagrams against the original" hint (Stage 46).
    ocr_used: bool = False


class MaterialActionResponse(BaseModel):
    ok: bool = True
    message: str = ""
    material_name: str = ""


class MaterialFromUrlRequest(BaseModel):
    """Ingest a web page as a material by URL (Stage 56)."""

    url: str = Field(default="", max_length=2000)


class MaterialProgressResponse(BaseModel):
    # Чьё это состояние. Клиент workspace не передаёт и передавать не может -
    # он резолвится из авторизации, - но обязан знать, к какому пространству
    # относится полученный снимок. Иначе после переключения курса посреди
    # загрузки фронт принимает состояние НОВОГО пространства за своё: пустое
    # трактует как «работа ещё не началась» и ждёт вечно, а чужое «готово»
    # засчитывает как успех своего файла.
    workspace_id: str = ""
    active: bool = False
    operation: str = "idle"
    phase: str = ""
    message: str = ""
    progress: int = 0
    current_file: str = ""
    error: str = ""


class SystemStatus(BaseModel):
    llm_mode: str
    model: str
    embedding_model: str
    reranker_model: str
    chunk_size: int
    hyde_enabled: bool
    total_books: int = 0
    total_chunks: int = 0


class SummaryRequest(BaseModel):
    selected_file: str = Field(default="Все файлы", max_length=500)
    selected_section: str = Field(default="Все разделы", max_length=500)
    topic: str = Field(default="", max_length=2000)
    summary_type: str = Field(default="Средний", max_length=50)


class SummaryExportRequest(BaseModel):
    # ``text`` is the generated summary turned into a DOCX. Bounded (Stage 41)
    # so a request can't drive an unbounded DOCX build / disk write.
    text: str = Field(default="", max_length=500_000)
    selected_file: str = Field(default="Все файлы", max_length=500)
    selected_section: str = Field(default="Все разделы", max_length=500)
    summary_type: str = Field(default="Средний", max_length=50)


class SummaryResponse(BaseModel):
    text: str
    diagnostics: str = ""
    trace: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    # Shared by request (history) and response. Caps (Stage 41) are generous
    # enough for real LLM answers but bound a hostile request body.
    role: str = Field(max_length=32)
    content: str = Field(max_length=50_000)


class ChatSource(BaseModel):
    source_file: str = ""
    section: str = ""
    page_start: int | None = None
    page_end: int | None = None
    score: float = 0.0
    label: str = ""
    # The actual passage backing this source (Stage 56) - lets the UI show "the
    # real fragment", not just the file name. Keep the complete retrieved passage.
    snippet: str = ""


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=50_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=200)
    selected_file: str = Field(default="Все файлы", max_length=500)
    answer_mode: str = Field(default="Обычный", max_length=50)
    # Chat history (Stage 19): when present, the server loads this session's
    # messages as the LLM context (source of truth) and appends the new turn to
    # it. Absent → a new session is created and its id is returned.
    session_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    answer: str
    summary: str = ""
    confidence_label: str = ""
    followup_suggestions: list[str] = Field(default_factory=list)
    history: list[ChatMessage] = Field(default_factory=list)
    sources: list[ChatSource] = Field(default_factory=list)
    diagnostics: str = ""
    trace: dict[str, Any] | None = None
    # The session this turn was saved to (Stage 19); "" when persistence is off.
    session_id: str = ""


class DiagnosticsResponse(BaseModel):
    text: str
    trace: dict[str, Any] | None = None


class SectionsResponse(BaseModel):
    sections: list[str] = Field(default_factory=list)


class MaterialsResponse(BaseModel):
    materials: list[MaterialInfo] = Field(default_factory=list)


class AuditEventOut(BaseModel):
    """One audit-log row as exposed to the superuser admin screen (Stage 9b).

    Mirrors :class:`src.db_models.AuditEvent`. ``user_id`` / ``workspace_id``
    are nullable because anonymous or workspace-less events (e.g. a failed
    login) are still recorded.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    user_id: str | None = None
    workspace_id: str | None = None
    target: str = ""
    ip: str = ""
    created_at: datetime


class AuditLogResponse(BaseModel):
    events: list[AuditEventOut] = Field(default_factory=list)


class AdminUserOut(BaseModel):
    """A user as shown in the superuser user-management table (Stage 13)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    is_active: bool
    is_superuser: bool
    can_create_courses: bool = False
    # Computed (not stored): the configured ROOT_ADMIN_EMAIL is immune to
    # demote/ban. The UI disables its row actions.
    is_protected: bool = False
    plan: str
    created_at: datetime


class WorkspaceOut(BaseModel):
    """A workspace the caller belongs to (personal or course), with their role
    in it (Stage 15). ``is_active`` marks the one currently selected;
    ``is_archived`` (Stage 22) marks a frozen course shown in the «Архив»."""

    id: str
    name: str
    kind: str
    role: str
    is_active: bool = False
    is_archived: bool = False


class WorkspacesResponse(BaseModel):
    """The caller's spaces for the course switcher: personal + joined courses."""

    workspaces: list[WorkspaceOut] = Field(default_factory=list)
    active_workspace_id: str


# --- Courses (Stage 15-4) ---------------------------------------------------
class CreateCourseRequest(BaseModel):
    name: str = Field(max_length=200)


class JoinCourseRequest(BaseModel):
    code: str = Field(max_length=64)


class RenameCourseRequest(BaseModel):
    name: str = Field(max_length=200)


class ArchiveCourseRequest(BaseModel):
    """Archive (freeze) or restore a course — owner only (Stage 22)."""

    archived: bool = True


class SetMemberRoleRequest(BaseModel):
    role: str = Field(max_length=32)  # "teacher" | "student"


class JoinCodeUpdate(BaseModel):
    """Rotate the join code and/or toggle joining (managers only)."""

    enabled: bool | None = None
    rotate: bool = False


class JoinCodeResponse(BaseModel):
    join_code: str
    join_enabled: bool


class CourseMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    group_name: str = ""


class CourseDetail(BaseModel):
    """A course's management view (managers only): members + join settings."""

    id: str
    name: str
    join_code: str
    join_enabled: bool
    members: list[CourseMemberOut] = Field(default_factory=list)
    invitations: list["CourseInviteOut"] = Field(default_factory=list)


class CourseInviteRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="student", max_length=32)
    group_name: str = Field(default="", max_length=120)


class CourseInviteOut(BaseModel):
    id: str
    email: str
    role: str
    group_name: str = ""
    expires_at: datetime
    delivery_status: str = "sent"


class AcceptCourseInviteRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class MemberGroupUpdate(BaseModel):
    group_name: str = Field(default="", max_length=120)


class PilotMemberMetric(BaseModel):
    user_id: str
    display_name: str
    email: str
    role: str
    group_name: str = ""
    chat_requests: int = 0
    assignments_completed: int = 0
    assignments_total: int = 0
    average_score_pct: float | None = None
    last_activity_at: datetime | None = None


class PilotDashboard(BaseModel):
    members_total: int
    active_members: int
    chat_requests: int
    assignments_published: int
    assignments_completed: int
    completion_pct: float | None = None
    members: list[PilotMemberMetric] = Field(default_factory=list)


class AdminUsersResponse(BaseModel):
    users: list[AdminUserOut] = Field(default_factory=list)


class AdminRoleUpdate(BaseModel):
    """Promote/demote a user (set ``is_superuser``)."""

    is_superuser: bool


class AdminActiveUpdate(BaseModel):
    """Ban/unban a user (set ``is_active``)."""

    is_active: bool


class AdminCourseCreatorUpdate(BaseModel):
    """Grant/revoke the course-creation capability (set ``can_create_courses``)."""

    can_create_courses: bool


class AdminStats(BaseModel):
    """System-wide counts for the admin overview (Stage 9b).

    Not workspace-scoped — this is superuser-only data covering the whole
    instance.
    """

    users: int = 0
    workspaces: int = 0
    documents: int = 0
    audit_events: int = 0


class ReconcileWorkspaceResult(BaseModel):
    """Per-workspace outcome of a reconcile run (Stage 9c)."""

    workspace_id: str
    removed_chunks: int = 0
    removed_documents: int = 0


class ReconcileResponse(BaseModel):
    """Result of scrubbing orphan KB chunks instance-wide (Stage 9c)."""

    workspaces: list[ReconcileWorkspaceResult] = Field(default_factory=list)
    total_removed_chunks: int = 0
    total_removed_documents: int = 0


class UsageCounter(BaseModel):
    """Used vs. allowed for one metered action (Stage 12)."""

    used: int = 0
    limit: int = 0


class BillingUsage(BaseModel):
    materials: UsageCounter = Field(default_factory=UsageCounter)
    chat: UsageCounter = Field(default_factory=UsageCounter)
    summary: UsageCounter = Field(default_factory=UsageCounter)
    study: UsageCounter = Field(default_factory=UsageCounter)


class BillingMeResponse(BaseModel):
    """Current plan + usage for the caller's workspace (Stage 12)."""

    plan: str = "free"
    usage: BillingUsage = Field(default_factory=BillingUsage)


# --- Study trainer: flashcards & quizzes (Stage 17) ------------------------
class StudyRequest(BaseModel):
    selected_file: str = Field(default="Все материалы", max_length=500)
    topic: str = Field(default="", max_length=2000)
    mode: str = Field(default="flashcards", max_length=32)  # "flashcards" | "quiz"
    count: int | None = Field(default=None, ge=1, le=100)


class Flashcard(BaseModel):
    question: str
    answer: str


class QuizQuestion(BaseModel):
    # Shared by request (CreateAssignmentRequest) and responses; caps (Stage 41)
    # bound a hostile assignment body without affecting generated quizzes.
    question: str = Field(default="", max_length=4000)
    options: list[str] = Field(default_factory=list, max_length=20)
    correct_index: int = 0
    explanation: str = Field(default="", max_length=4000)


class StudyResponse(BaseModel):
    """Generated study material. ``ok=false`` + ``message`` when the model
    couldn't return usable JSON (the UI shows a retry hint, never crashes)."""

    mode: str
    ok: bool = True
    message: str = ""
    cards: list[Flashcard] = Field(default_factory=list)
    questions: list[QuizQuestion] = Field(default_factory=list)


# --- Assignments: teacher-assigned tests + results (Stage 18) ---------------
class CreateAssignmentRequest(BaseModel):
    """Save a curated quiz as a course assignment (manager). The questions come
    from the trainer's quiz generation, reviewed by the teacher."""

    title: str = Field(default="", max_length=300)
    source_label: str = Field(default="", max_length=300)
    questions: list[QuizQuestion] = Field(default_factory=list, max_length=200)


class PublishAssignmentRequest(BaseModel):
    published: bool = True


class AssignmentListItem(BaseModel):
    """One assignment in the list. Manager-only fields (attempt_count,
    avg_score_pct) and student-only fields (submitted, my_score) are mutually
    exclusive — populated per the caller's role, left ``None`` otherwise."""

    id: str
    title: str
    question_count: int = 0
    is_published: bool = False
    created_at: datetime
    # Manager view:
    attempt_count: int | None = None
    avg_score_pct: float | None = None
    # Student view:
    submitted: bool | None = None
    my_score: int | None = None
    my_total: int | None = None


class AssignmentListResponse(BaseModel):
    assignments: list[AssignmentListItem] = Field(default_factory=list)
    can_manage: bool = False


class AssignmentQuestionPublic(BaseModel):
    """A question as a student sees it *before* submitting — no answer leaked."""

    question: str
    options: list[str] = Field(default_factory=list)


class SubmitAttemptRequest(BaseModel):
    answers: list[int] = Field(default_factory=list, max_length=500)


class AttemptResult(BaseModel):
    """Returned after a student submits: server-computed score plus the full
    questions (correct answers + explanations) and the student's choices —
    revealed only now that the test is submitted."""

    score: int = 0
    total: int = 0
    answers: list[int] = Field(default_factory=list)
    questions: list[QuizQuestion] = Field(default_factory=list)


class AssignmentForTaking(BaseModel):
    """The take-view. Before submit: only public questions. After submit:
    ``result`` is filled (with answers revealed)."""

    id: str
    title: str
    question_count: int = 0
    questions: list[AssignmentQuestionPublic] = Field(default_factory=list)
    submitted: bool = False
    result: AttemptResult | None = None
    can_manage: bool = False


class StudentAttemptOut(BaseModel):
    user_id: str
    display_name: str = ""
    email: str = ""
    score: int = 0
    total: int = 0
    submitted_at: datetime


class QuestionStat(BaseModel):
    """How a single question performed across attempts (to find hard ones)."""

    index: int
    question: str
    correct_count: int = 0
    attempts: int = 0
    accuracy_pct: float = 0.0


class AssignmentResults(BaseModel):
    """Manager results view: per-student attempts + aggregate analytics."""

    id: str
    title: str
    question_count: int = 0
    is_published: bool = False
    students_total: int = 0
    attempts_count: int = 0
    avg_score_pct: float = 0.0
    attempts: list[StudentAttemptOut] = Field(default_factory=list)
    question_stats: list[QuestionStat] = Field(default_factory=list)
    questions: list[QuizQuestion] = Field(default_factory=list)


# --- Chat history: persistent sessions (Stage 19) --------------------------
class ChatSessionOut(BaseModel):
    """One chat in the list (the caller's own, in the active workspace)."""

    id: str
    title: str
    message_count: int = 0
    updated_at: datetime


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionOut] = Field(default_factory=list)


class ChatSessionMessageOut(BaseModel):
    """A stored turn. ``meta`` carries the assistant's sources snapshot +
    confidence + follow-ups so a reopened chat renders like the live answer."""

    role: str
    content: str = ""
    meta: dict[str, Any] | None = None


class ChatSessionDetail(BaseModel):
    id: str
    title: str
    messages: list[ChatSessionMessageOut] = Field(default_factory=list)


class RenameChatRequest(BaseModel):
    title: str = Field(max_length=300)
