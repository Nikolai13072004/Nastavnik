"""Quota enforcement + usage metering (Stage 12).

``check_quota`` is called before a billable action (chat / summary / upload)
and raises :class:`QuotaExceeded` (mapped to HTTP 402) when the billing subject
is over its plan's limit. ``record_usage`` appends to the :class:`UsageEvent`
ledger after a successful action — best-effort, never breaks the request.

Counting is keyed on ``billing_subject_id`` (from :func:`billing.get_billing_context`),
so when the org tier arrives the enforcement code is unchanged — only the
resolver gains a branch. ``chat`` / ``summary`` are rolling per-UTC-day caps;
``upload`` is a total cap on current materials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import config
from src.billing import get_billing_context, limits_for_plan
from src.db import SessionLocal
from src.db_models import Document, UsageEvent, Workspace

logger = logging.getLogger("vedomo.quota")

ACTION_CHAT = "chat"
ACTION_SUMMARY = "summary"
ACTION_UPLOAD = "upload"
ACTION_STUDY = "study"


class QuotaExceeded(Exception):
    """Raised when a billing subject is at/over its plan limit for an action."""

    def __init__(self, *, action: str, limit: int, used: int, plan: str):
        self.action = action
        self.limit = limit
        self.used = used
        self.plan = plan
        super().__init__(f"quota_exceeded: {action} {used}/{limit} (plan={plan})")


def _start_of_day_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _count_action_today(db: Session, billing_subject_id: str, action: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.billing_subject_id == billing_subject_id,
                UsageEvent.action == action,
                UsageEvent.created_at >= _start_of_day_utc(),
            )
        )
        or 0
    )


def _count_materials(db: Session, workspace_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.workspace_id == workspace_id)
        )
        or 0
    )


def check_quota(workspace_id: str, action: str) -> None:
    """Raise :class:`QuotaExceeded` if ``action`` is over the plan's limit.

    No-op when ``QUOTAS_ENABLED`` is false (dev / test). If the workspace can't
    be resolved we *don't* block — failing open is safer than locking a user out
    over a lookup miss.
    """
    if not config.QUOTAS_ENABLED:
        return

    db = SessionLocal()
    try:
        workspace = db.get(Workspace, workspace_id)
        if workspace is None:
            return
        ctx = get_billing_context(db, workspace)

        if action == ACTION_UPLOAD:
            used = _count_materials(db, workspace_id)
            limit = ctx.limits.max_materials
        elif action == ACTION_CHAT:
            used = _count_action_today(db, ctx.billing_subject_id, ACTION_CHAT)
            limit = ctx.limits.chat_per_day
        elif action == ACTION_SUMMARY:
            used = _count_action_today(db, ctx.billing_subject_id, ACTION_SUMMARY)
            limit = ctx.limits.summary_per_day
        elif action == ACTION_STUDY:
            used = _count_action_today(db, ctx.billing_subject_id, ACTION_STUDY)
            limit = ctx.limits.study_per_day
        else:
            return

        if used >= limit:
            raise QuotaExceeded(action=action, limit=limit, used=used, plan=ctx.plan)
    finally:
        db.close()


def record_usage(
    workspace_id: str,
    action: str,
    *,
    user_id: str | None = None,
    units: int = 1,
    meta: dict | None = None,
) -> None:
    """Append one :class:`UsageEvent`. Best-effort — never raises."""
    try:
        db = SessionLocal()
        try:
            workspace = db.get(Workspace, workspace_id)
            if workspace is None:
                return
            ctx = get_billing_context(db, workspace)
            db.add(
                UsageEvent(
                    workspace_id=workspace_id,
                    # Actor; for a personal workspace that's the owner == subject.
                    user_id=user_id or ctx.billing_subject_id,
                    action=action,
                    units=units,
                    billing_subject_type=ctx.billing_subject_type,
                    billing_subject_id=ctx.billing_subject_id,
                    meta=meta,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # pragma: no cover - metering must never break the action
        logger.warning("usage metering failed for action=%s", action, exc_info=True)


def usage_summary(workspace_id: str) -> dict:
    """Current plan + per-action usage/limits for ``GET /api/billing/me``."""
    db = SessionLocal()
    try:
        workspace = db.get(Workspace, workspace_id)
        if workspace is None:
            limits = limits_for_plan("free")
            return {
                "plan": "free",
                "usage": {
                    "materials": {"used": 0, "limit": limits.max_materials},
                    "chat": {"used": 0, "limit": limits.chat_per_day},
                    "summary": {"used": 0, "limit": limits.summary_per_day},
                    "study": {"used": 0, "limit": limits.study_per_day},
                },
            }

        ctx = get_billing_context(db, workspace)
        return {
            "plan": ctx.plan,
            "usage": {
                "materials": {
                    "used": _count_materials(db, workspace_id),
                    "limit": ctx.limits.max_materials,
                },
                "chat": {
                    "used": _count_action_today(db, ctx.billing_subject_id, ACTION_CHAT),
                    "limit": ctx.limits.chat_per_day,
                },
                "summary": {
                    "used": _count_action_today(db, ctx.billing_subject_id, ACTION_SUMMARY),
                    "limit": ctx.limits.summary_per_day,
                },
                "study": {
                    "used": _count_action_today(db, ctx.billing_subject_id, ACTION_STUDY),
                    "limit": ctx.limits.study_per_day,
                },
            },
        }
    finally:
        db.close()
