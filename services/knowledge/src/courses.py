"""Course primitives (Stage 15): roles, workspace kinds, and join codes.

A *course* is a shared :class:`~src.db_models.Workspace` (``kind="course"``)
that students join with a short code. This module is the home for the small,
dependency-free building blocks; the ``can(user, action, workspace)`` resolver
(Stage 15-2), the create/join endpoints (15-4) and the switcher (15-3) build on
top of these constants and helpers. See ``design/courses-and-roles.md``.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db_models import WorkspaceMember

# --- Workspace kinds -------------------------------------------------------
WORKSPACE_KIND_PERSONAL = "personal"
WORKSPACE_KIND_COURSE = "course"

# --- Course roles (WorkspaceMember.role) -----------------------------------
ROLE_OWNER = "owner"      # course creator — full rights incl. delete course
ROLE_TEACHER = "teacher"  # co-manager — manage materials + members
ROLE_STUDENT = "student"  # participant — chat / summary / read materials

COURSE_ROLES = frozenset({ROLE_OWNER, ROLE_TEACHER, ROLE_STUDENT})
# Roles that may manage a course (materials + members). ``student`` may not.
MANAGER_ROLES = frozenset({ROLE_OWNER, ROLE_TEACHER})

# --- Join codes ------------------------------------------------------------
# Ambiguity-free alphabet: no 0/O, 1/I/L — codes are easy to read aloud and
# type. Uppercase letters + digits only.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 8


def generate_join_code(length: int = JOIN_CODE_LENGTH) -> str:
    """Return a random, unguessable course join code.

    Uses :mod:`secrets` (CSPRNG) over an ambiguity-free alphabet. Uniqueness
    against the DB is enforced at course-creation time (retry on the rare
    collision); this only guarantees randomness + safe characters.
    """
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(length))


# --- Authorization: can(user, action, workspace) ---------------------------
# The single decider for "what's allowed". Every mutating endpoint routes
# through it (see api_app.require_permission) instead of ad-hoc role checks, so
# a missed check can't silently become a privilege escalation.
ACTION_VIEW = "view"                        # see the course / list materials
ACTION_CHAT = "chat"
ACTION_SUMMARY = "summary"
ACTION_STUDY = "study"                       # flashcards / quizzes (Stage 17)
ACTION_TAKE_ASSIGNMENT = "take_assignment"   # take a published test (Stage 18)
ACTION_UPLOAD = "upload"
ACTION_REINDEX = "reindex"
ACTION_DELETE_MATERIAL = "delete_material"
ACTION_MANAGE_MEMBERS = "manage_members"
ACTION_MANAGE_ASSIGNMENTS = "manage_assignments"  # create/publish/delete + results (Stage 18)
ACTION_RENAME_COURSE = "rename_course"
ACTION_MANAGE_JOIN_CODE = "manage_join_code"
ACTION_DELETE_COURSE = "delete_course"

# A participant may read + ask + take assigned tests; a manager (teacher/owner)
# adds material + member + assignment management; only the owner may delete the
# whole course.
_STUDENT_ACTIONS = frozenset(
    {ACTION_VIEW, ACTION_CHAT, ACTION_SUMMARY, ACTION_STUDY, ACTION_TAKE_ASSIGNMENT}
)
_MANAGER_ACTIONS = _STUDENT_ACTIONS | {
    ACTION_UPLOAD,
    ACTION_REINDEX,
    ACTION_DELETE_MATERIAL,
    ACTION_MANAGE_MEMBERS,
    ACTION_MANAGE_ASSIGNMENTS,
    ACTION_RENAME_COURSE,
    ACTION_MANAGE_JOIN_CODE,
}
_ALLOWED_ACTIONS = {
    ROLE_STUDENT: _STUDENT_ACTIONS,
    ROLE_TEACHER: _MANAGER_ACTIONS,
    ROLE_OWNER: _MANAGER_ACTIONS | {ACTION_DELETE_COURSE},
}


def role_in_workspace(db: Session, user_id: str, workspace) -> str | None:
    """The user's effective role in ``workspace`` (owner/teacher/student), or
    ``None`` if they aren't a member.

    The owner is authoritative via ``workspace.owner_user_id`` — a course
    creator can never be locked out by a missing or edited member row (the
    "owner must not lose access" invariant). Personal workspaces resolve to
    ``owner`` the same way, so existing single-user behavior is unchanged.
    """
    if workspace is None or not user_id:
        return None
    from src.account_deletion import is_pending
    if is_pending(db, user_id) or is_pending(db, workspace.owner_user_id):
        return None
    if getattr(workspace, "owner_user_id", None) == user_id:
        return ROLE_OWNER
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return member.role if member is not None else None


def can(db: Session, user, action: str, workspace) -> bool:
    """Single authorization decider: may ``user`` perform ``action`` in
    ``workspace``?

    Deny by default: a non-member — or a row with an unrecognized role — gets
    ``False``.

    An **archived** course is frozen/read-only (Stage 22): everyone, owner
    included, may only ``view`` it — no chat / summary / study / upload /
    manage / take. Archive/unarchive/copy/delete are owner-only lifecycle
    operations checked directly in the service layer (not here), so they still
    work on an archived course.
    """
    role = role_in_workspace(db, getattr(user, "id", None), workspace)
    if role is None:
        return False
    if getattr(workspace, "is_archived", False) and action != ACTION_VIEW:
        return False
    return action in _ALLOWED_ACTIONS.get(role, frozenset())


def can_create_courses(user) -> bool:
    """May ``user`` create a course (Stage 30)?

    A superuser always may; everyone else needs the ``can_create_courses``
    capability, granted by a superuser via the admin panel. Deny by default -
    in the B2B кафедра model only provisioned teachers create courses.
    """
    return bool(
        getattr(user, "is_superuser", False) or getattr(user, "can_create_courses", False)
    )
