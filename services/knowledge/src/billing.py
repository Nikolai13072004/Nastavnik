"""Billing context resolution (Stage 12).

The single seam every quota / limit decision goes through, so the code never
reads a plan field directly. Today the billing subject is the workspace owner
(personal ``User.plan``); when organizations land, this resolver gains one
branch (``workspace.organization`` → ``org`` plan) and nothing downstream
changes. See ``design/monetization-and-b2b.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

import config
from src.db_models import User, Workspace


@dataclass(frozen=True)
class PlanLimits:
    """Resolved limits for a plan (a typed view over ``config.PLAN_LIMITS``)."""

    max_materials: int
    chat_per_day: int
    summary_per_day: int
    study_per_day: int
    model: str


@dataclass(frozen=True)
class BillingContext:
    """Who the quota/invoice belongs to, their plan, and the limits.

    ``billing_subject_type`` is ``"user"`` now and ``"organization"`` later;
    quotas are counted per ``billing_subject_id`` so the org tier is a
    resolver-only change.
    """

    plan: str
    billing_subject_type: str
    billing_subject_id: str
    limits: PlanLimits


def limits_for_plan(plan: str) -> PlanLimits:
    """Typed limits for ``plan``, falling back to ``free`` for anything unknown."""
    raw = config.PLAN_LIMITS.get(plan) or config.PLAN_LIMITS["free"]
    return PlanLimits(
        max_materials=int(raw["max_materials"]),
        chat_per_day=int(raw["chat_per_day"]),
        summary_per_day=int(raw["summary_per_day"]),
        study_per_day=int(raw.get("study_per_day", raw["summary_per_day"])),
        model=str(raw.get("model", "")),
    )


def get_billing_context(db: Session, workspace: Workspace) -> BillingContext:
    """Resolve the billing subject + plan + limits for ``workspace``.

    Now: the subject is the workspace owner, plan = ``owner.plan``. Future: if
    ``workspace.organization_id`` is set, the subject becomes the organization
    and the plan comes from it — a one-branch addition here.
    """
    owner = db.get(User, workspace.owner_user_id)
    plan = (owner.plan if owner else "free") or "free"
    if plan not in config.PLAN_LIMITS:
        plan = "free"
    return BillingContext(
        plan=plan,
        billing_subject_type="user",
        billing_subject_id=workspace.owner_user_id,
        limits=limits_for_plan(plan),
    )
