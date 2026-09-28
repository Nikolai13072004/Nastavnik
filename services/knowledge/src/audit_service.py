"""Append-only security audit log (Stage 9a).

``record`` writes one :class:`~src.db_models.AuditEvent` per security-relevant
action (login / upload / delete / reindex). It opens its own short-lived
session and **never raises**: a failed audit write must not break the user
action it describes. Routes call it after the action succeeds.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.db import SessionLocal
from src.db_models import AuditEvent

logger = logging.getLogger("vedomo.audit")

# Allowed action names — keep the vocabulary small and queryable.
ACTION_LOGIN = "login"
ACTION_UPLOAD = "upload"
ACTION_DELETE = "delete"
ACTION_REINDEX = "reindex"
ACTION_RECONCILE = "reconcile"
# Superuser user-management (Stage 13).
ACTION_PROMOTE = "promote"
ACTION_DEMOTE = "demote"
ACTION_BAN = "ban"
ACTION_UNBAN = "unban"
ACTION_GRANT_COURSE_CREATE = "grant_course_create"
ACTION_REVOKE_COURSE_CREATE = "revoke_course_create"
# Course lifecycle (Stage 51).
ACTION_CREATE_COURSE = "create_course"
ACTION_INVITE_COURSE_MEMBER = "invite_course_member"
ACTION_REVOKE_COURSE_INVITE = "revoke_course_invite"
ACTION_ACCEPT_COURSE_INVITE = "accept_course_invite"
ACTION_UPDATE_MEMBER_GROUP = "update_member_group"


def record(
    action: str,
    *,
    user_id: str | None = None,
    workspace_id: str | None = None,
    target: str = "",
    ip: str = "",
) -> None:
    """Best-effort write of one audit event. Swallows all errors."""
    try:
        db = SessionLocal()
        try:
            db.add(
                AuditEvent(
                    action=action,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    target=(target or "")[:255],
                    ip=(ip or "")[:64],
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # pragma: no cover - audit must never break the request
        logger.warning("audit write failed for action=%s", action, exc_info=True)


# Read side of the audit log — surfaced only to superusers via /api/admin/audit.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def list_recent(limit: int = _DEFAULT_LIMIT) -> list[AuditEvent]:
    """Return the most recent audit events, newest first (Stage 9b).

    Opens its own short-lived session like :func:`record`. ``limit`` is clamped
    to ``[1, 500]`` so a hostile or buggy caller can't ask for an unbounded
    scan. Unlike ``record`` this is allowed to raise — the admin route surfaces
    a real error rather than silently showing an empty log.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    db = SessionLocal()
    try:
        return list(
            db.execute(
                select(AuditEvent)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit)
            ).scalars()
        )
    finally:
        db.close()
