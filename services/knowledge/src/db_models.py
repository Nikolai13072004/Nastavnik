"""ORM models for the multi-user foundation.

Stage 1 introduces four tables — ``users``, ``workspaces``,
``workspace_members``, ``documents`` — but only ``users``/``workspaces``/
``workspace_members`` are actively used by Stage 1 endpoints. ``documents``
is created up front so Stage 2/3 don't need a second migration when they
start populating it.

Identifier policy: every primary/foreign key is a UUID stored as
``String(36)``. This keeps schemas portable between SQLite (dev/CI) and
Postgres (prod) without dialect-specific UUID types.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountDeletionJob(Base):
    """Durable cleanup receipt; deliberately survives the deleted user."""

    __tablename__ = "account_deletion_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    workspace_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    """A registered end-user. Stage 1 holds auth credentials only."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Course creation (Stage 30): only a user with this capability (or a
    # superuser) may create a course workspace. Default off - for the B2B
    # кафедра model a superuser grants it to teachers via the admin panel.
    can_create_courses: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Personal billing plan (Stage 12): ``free`` / ``pro``. This is the billing
    # subject for a personal workspace; an org-owned workspace will resolve its
    # plan from the organization instead (see design/monetization-and-b2b.md).
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    # Session revocation (Stage 13): JWTs whose ``iat`` predates this are
    # rejected by ``get_current_user``. Stamped on ban so a banned account can't
    # resume on its old cookie after being un-banned — it must log in fresh.
    tokens_valid_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Active workspace (Stage 15): the workspace currently selected in the course
    # switcher. Null = personal. Intentionally NOT a foreign key — it's a soft
    # pointer re-validated against membership on every resolve
    # (get_current_workspace_id), so a deleted course or a removed membership
    # falls back to the personal workspace instead of dangling.
    active_workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Reserved for a future email-verification flow (not implemented in Stage 1).
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    owned_workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    email_tokens: Mapped[list["EmailToken"]] = relationship(
        cascade="all, delete-orphan"
    )


class Workspace(Base):
    """An isolation unit for documents and the vector index.

    Stage 1 creates exactly one personal workspace per user at registration
    time. The data model supports shared workspaces (see
    :class:`WorkspaceMember`), but Stage 1 surfaces no UI/API for them.
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    # Courses (Stage 15): a course is just a shared workspace. ``kind`` separates
    # a user's personal space ("personal") from a course ("course");
    # ``join_code`` (+ ``join_enabled``) is how a student joins. Personal
    # workspaces keep kind="personal" and no join code. See
    # design/courses-and-roles.md.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")
    join_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    join_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Course lifecycle (Stage 22): an archived course is frozen/read-only — the
    # ``can()`` resolver allows only ``view``; it's hidden from the active
    # switcher and can't be joined. Owner can restore it. See
    # design/courses-and-roles.md.
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    owner: Mapped["User"] = relationship(back_populates="owned_workspaces")
    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    """Maps a user to a workspace with a role.

    Stage 1 only ever created one ``owner`` row per personal workspace. Stage 15
    activates the role column for courses: ``owner`` (creator) / ``teacher``
    (co-manager) / ``student`` (participant). The ``can(user, action, workspace)``
    resolver reads this role. (``viewer`` is a documented future addition.)
    """

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="owner")
    # Optional business unit shown only to course managers. It is deliberately
    # attached to a membership, not the user: one employee may belong to
    # different groups in different training programmes.
    group_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    workspace: Mapped["Workspace"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class ProdigyIntegration(Base):
    """A revocable service credential bound to one organization and course."""

    __tablename__ = "prodigy_integrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    course_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CourseInvitation(Base):
    """Address-bound, expiring invitation to a shared course workspace."""

    __tablename__ = "course_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student")
    group_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Document(Base):
    """A user-uploaded document. Created by Stage 1, populated in Stage 2/3.

    Stored here so Stage 1's Alembic migration already includes the table —
    no second initial migration is needed when document ingestion is wired
    up. Currently nothing writes rows into this table.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # ``processing`` / ``ready`` / ``error`` / ``hidden``.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    sections_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # True when text was extracted via OCR (scans / image-based slides); the UI
    # surfaces it so users verify formulas/diagrams against the original.
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="documents")


class AuditEvent(Base):
    """Append-only audit trail for security-relevant actions (Stage 9a).

    Records ``login`` / ``upload`` / ``delete`` / ``reindex``. Deliberately has
    NO foreign keys: an audit record must survive deletion of the user or
    workspace it refers to, so ``user_id`` / ``workspace_id`` are plain columns.
    Writes are best-effort and must never break the action they describe.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Actor / workspace are nullable so anonymous or workspace-less events
    # (e.g. a failed login) can still be recorded.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )


class UsageEvent(Base):
    """Append-only metering ledger for billable actions (Stage 12).

    Records one row per ``chat`` / ``summary`` / ``upload``. Powers both quota
    enforcement (count a billing subject's actions in a window) and future cost
    measurement (``meta`` can carry model / token / cost details).

    Like :class:`AuditEvent` it has NO foreign keys: the ledger must survive
    deletion of the user / workspace it refers to, for accounting. The billing
    subject is the *user* today and an *organization* later (see
    ``design/monetization-and-b2b.md``); recording ``billing_subject_*`` now
    means quota counting never has to be rewritten for the org tier.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    billing_subject_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    billing_subject_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    # Best-effort details (model, char/token counts, cost, error). JSON works on
    # both SQLite (dev/CI) and Postgres (prod).
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Assignment(Base):
    """A teacher-assigned test inside a course (Stage 18).

    A fixed set of multiple-choice questions, generated from a course material
    via the trainer pipeline and saved so every student takes the *same* test.
    ``questions`` is a JSON snapshot (``[{question, options[], correct_index,
    explanation}, …]``), so deleting the source material later doesn't break the
    assignment. A draft (``is_published=False``) is invisible to students until
    published. See ``design/assignments-and-analytics.md``.
    """

    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="assignments")
    attempts: Mapped[list["AssignmentAttempt"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class AssignmentAttempt(Base):
    """One student's submission to an assignment (Stage 18).

    One row per (assignment, user) — re-submitting overwrites it (the v1
    "overwrite, not lockout" choice). ``answers`` is the selected option index
    per question (``-1`` = unanswered); ``score`` / ``total`` are computed on the
    server from the assignment's stored ``correct_index`` — the client never
    sends a score.
    """

    __tablename__ = "assignment_attempts"
    __table_args__ = (
        UniqueConstraint("assignment_id", "user_id", name="uq_attempt_per_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    answers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    assignment: Mapped["Assignment"] = relationship(back_populates="attempts")


class ChatSession(Base):
    """One saved assistant conversation (Stage 19).

    Belongs to a **(user, workspace)** pair — access is owner-only; a teacher
    cannot read a student's chats. ``title`` is derived from the first user
    message and is renameable. See ``design/chat-history.md``.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="chat_sessions")
    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    """One turn in a :class:`ChatSession` (Stage 19).

    ``meta`` carries the assistant turn's sources snapshot + confidence +
    follow-ups, so a reopened chat renders citations exactly as when answered.
    """

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


class EmailToken(Base):
    """A single-use, expiring token for an email flow (Stage 23).

    Backs password reset (``purpose="reset"``) and email verification
    (``purpose="verify"``). Only the **sha256 hash** of the token is stored, so
    a DB leak doesn't hand out working links - the raw token lives only in the
    email. A token is spent when ``used_at`` is set; re-using or expiring it
    fails the lookup. Tokens are high-entropy (``secrets.token_urlsafe(32)``),
    so an unsalted hash is sufficient (unlike passwords).
    """

    __tablename__ = "email_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ``reset`` (password reset) or ``verify`` (email verification).
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
