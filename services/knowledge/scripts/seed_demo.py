"""Seed a ready-to-show demo course (Stage 32 / golden scenario).

One command spins up the whole happy path so the product can be demoed without
manual setup: a teacher account that owns a course, a sample material indexed
into it, and a student account already joined. It reuses the real service layer
(``register_user`` / ``create_course_service`` / ``upload_material_service``),
so the demo data is created exactly the way the app creates it.

    python -m scripts.seed_demo                # full: ingests the sample material
    python -m scripts.seed_demo --no-ingest    # structure only (no embedding model)

Idempotent: re-running reuses the existing demo users / course / material
instead of duplicating them. Ingest needs the embedding model (``BAAI/bge-m3``)
and the same ``.env`` the API uses; ``--no-ingest`` skips it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow ``python scripts/seed_demo.py`` (direct) as well as ``-m scripts.seed_demo``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402

import config  # noqa: E402

TEACHER_EMAIL = "demo.teacher@example.com"
TEACHER_PASSWORD = "demo-teacher-1234"
STUDENT_EMAIL = "demo.student@example.com"
STUDENT_PASSWORD = "demo-student-1234"
COURSE_NAME = "Демо-курс: Биология"
MATERIAL_NAME = "bio-photosynthesis.txt"
MATERIAL_PATH = _REPO_ROOT / "evals" / "datasets" / "materials" / MATERIAL_NAME


def _ensure_user(db, email: str, password: str, display_name: str, *, can_create: bool = False):
    from src import auth_service
    from src.auth_models import UserCreate
    from src.db_models import User

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = auth_service.register_user(
            db, UserCreate(email=email, password=password, display_name=display_name)
        )
    changed = False
    if user.display_name != display_name:
        user.display_name = display_name
        changed = True
    if can_create and not user.can_create_courses:
        user.can_create_courses = True
        changed = True
    if changed:
        db.commit()
        db.refresh(user)
    return user


def _ensure_course(db, teacher, course_name: str = COURSE_NAME):
    from src import app_services, courses
    from src.db_models import Workspace

    course = db.execute(
        select(Workspace).where(
            Workspace.owner_user_id == teacher.id,
            Workspace.name == course_name,
            Workspace.kind == courses.WORKSPACE_KIND_COURSE,
        )
    ).scalar_one_or_none()
    if course is None:
        out = app_services.create_course_service(db, teacher, course_name)
        course = db.get(Workspace, out.id)
    return course


def _ensure_material(
    db, course, teacher, material_name: str = MATERIAL_NAME, material_path=MATERIAL_PATH, *, ingest: bool
) -> bool:
    """Index the sample material into the course if it isn't there yet. Returns
    True if it ran an ingest, False if skipped (already present or ``--no-ingest``)."""
    from src.db_models import Document

    existing = db.execute(
        select(Document).where(
            Document.workspace_id == course.id, Document.original_name == material_name
        )
    ).scalar_one_or_none()
    if existing is not None or not ingest:
        return False

    from src import app_services

    content = material_path.read_bytes()
    resp = app_services.upload_material_service(course.id, teacher.id, material_name, content)
    if not resp.ok:
        raise RuntimeError(f"material ingest failed: {resp.message}")
    return True


def _ensure_membership(db, course, student) -> None:
    from src import courses
    from src.db_models import WorkspaceMember

    if courses.role_in_workspace(db, student.id, course) is None:
        db.add(
            WorkspaceMember(workspace_id=course.id, user_id=student.id, role=courses.ROLE_STUDENT)
        )
        student.active_workspace_id = course.id
        db.commit()


def seed(*, ingest: bool = True) -> dict:
    """Create (or reuse) the demo teacher, course, material and student.
    Returns a summary dict; safe to call repeatedly."""
    from src.db import SessionLocal

    db = SessionLocal()
    try:
        teacher = _ensure_user(
            db, TEACHER_EMAIL, TEACHER_PASSWORD, "Демо Преподаватель", can_create=True
        )
        course = _ensure_course(db, teacher)
        ingested = _ensure_material(db, course, teacher, ingest=ingest)
        student = _ensure_user(db, STUDENT_EMAIL, STUDENT_PASSWORD, "Демо Студент")
        _ensure_membership(db, course, student)
        db.refresh(course)
        return {
            "workspace_id": course.id,
            "course_name": course.name,
            "join_code": course.join_code,
            "teacher": (TEACHER_EMAIL, TEACHER_PASSWORD),
            "student": (STUDENT_EMAIL, STUDENT_PASSWORD),
            "ingested": ingested,
        }
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a demo course for showing Vedomo.")
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="skip indexing the sample material (no embedding model needed)",
    )
    parser.add_argument(
        "--base-url", default=config.APP_BASE_URL, help="base URL for the printed invite link"
    )
    args = parser.parse_args(argv)

    if not args.no_ingest and not MATERIAL_PATH.exists():
        print(f"Sample material not found: {MATERIAL_PATH}", file=sys.stderr)
        return 1

    info = seed(ingest=not args.no_ingest)
    base = str(args.base_url).rstrip("/")
    invite = f"{base}/?join={info['join_code']}"

    print("=" * 60)
    print("Демо-данные готовы")
    print("=" * 60)
    print(f"Курс:        {info['course_name']}")
    print(f"Код входа:   {info['join_code']}")
    print(f"Ссылка:      {invite}")
    print(f"Материал:    {'проиндексирован' if info['ingested'] else 'пропущен (--no-ingest или уже был)'}")
    print("-" * 60)
    print(f"Преподаватель: {info['teacher'][0]} / {info['teacher'][1]}")
    print(f"Студент:       {info['student'][0]} / {info['student'][1]}")
    print("=" * 60)
    if info["ingested"]:
        print(
            "ВАЖНО: если бэкенд (run_api.py) уже запущен - перезапусти его.\n"
            "ChromaDB не делит коллекцию между процессами, поэтому работающий\n"
            "сервер не увидит новый материал, пока его не перезапустить."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
