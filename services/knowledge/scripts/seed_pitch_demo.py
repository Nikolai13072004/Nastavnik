"""Seed vertical demo courses for a pitch (Stage 56).

One command spins up three synthetic product-training programs, each with a
short sample material indexed, under a demo manager account.
Reuses the real service layer through ``seed_demo``'s helpers.

    python -m scripts.seed_pitch_demo               # full: ingests the materials
    python -m scripts.seed_pitch_demo --no-ingest    # structure only (no model)

Idempotent. The materials are synthetic and contain no customer data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402
from sqlalchemy import select  # noqa: E402
from scripts.seed_demo import (  # noqa: E402
    _ensure_course,
    _ensure_material,
    _ensure_membership,
    _ensure_user,
)

TEACHER_EMAIL = "demo.pitch@example.com"
TEACHER_PASSWORD = "demo-pitch-1234"
STUDENT_EMAIL = "demo.pitch.student@example.com"
STUDENT_PASSWORD = "demo-pitch-1234"

_MATERIALS_DIR = _REPO_ROOT / "evals" / "datasets" / "materials"
VERTICALS: list[tuple[str, str]] = [
    ("Академия продукта · AlphaDrive X120", "corporate-alphadrive-x120.txt"),
    ("Академия продукта · AlphaDrive X220", "corporate-alphadrive-x220.txt"),
    ("Безопасность оборудования · SafeSense S7", "corporate-safesense-s7.txt"),
]

LEGACY_PROGRAM_NAMES = {
    "Демо: Юриспруденция": "Академия продукта · AlphaDrive X120",
    "Демо: Экономика": "Академия продукта · AlphaDrive X220",
    "Демо: Медицина": "Безопасность оборудования · SafeSense S7",
}

DEMO_ASSIGNMENT_TITLE = "Допуск к работе с AlphaDrive X120"
DEMO_ASSIGNMENT_QUESTIONS = [
    {
        "question": "К какой сети разрешено подключать AlphaDrive X120?",
        "options": ["Однофазная 220 В", "Трёхфазная 380 В", "Постоянная 24 В"],
        "correct_index": 0,
        "explanation": "X120 рассчитан на однофазное питание 220 В; подключение к 380 В запрещено.",
    },
    {
        "question": "Сколько нужно ждать после отключения питания перед монтажными работами?",
        "options": ["Не менее 2 минут", "Не менее 10 минут", "Не менее 15 минут"],
        "correct_index": 1,
        "explanation": "Перед монтажом необходимо выждать не менее 10 минут.",
    },
    {
        "question": "Какие минимальные вентиляционные зазоры нужны для X120?",
        "options": [
            "100 мм сверху и снизу, 20 мм по бокам",
            "150 мм сверху и снизу, 30 мм по бокам",
            "Зазоры не требуются",
        ],
        "correct_index": 0,
        "explanation": "Для X120 нужны зазоры 100 мм сверху и снизу и 20 мм по бокам.",
    },
]


def _ensure_demo_assignment(db, course, teacher) -> None:
    """Keep the pitch useful even when the external LLM is not configured."""
    from src.db_models import Assignment

    assignment = db.execute(
        select(Assignment).where(
            Assignment.workspace_id == course.id,
            Assignment.title == DEMO_ASSIGNMENT_TITLE,
        )
    ).scalar_one_or_none()
    if assignment is None:
        assignment = Assignment(
            workspace_id=course.id,
            created_by_user_id=teacher.id,
            title=DEMO_ASSIGNMENT_TITLE,
            source_label="corporate-alphadrive-x120.txt",
            questions=DEMO_ASSIGNMENT_QUESTIONS,
            is_published=True,
        )
        db.add(assignment)
    else:
        assignment.source_label = "corporate-alphadrive-x120.txt"
        assignment.questions = DEMO_ASSIGNMENT_QUESTIONS
        assignment.is_published = True
    db.commit()


def seed_pitch(*, ingest: bool = True) -> list[dict]:
    """Create (or reuse) one demo manager + employee and a program per product with
    its sample material. Returns a summary list; safe to call repeatedly."""
    from src.db import SessionLocal
    from src.db_models import Workspace

    db = SessionLocal()
    summary: list[dict] = []
    try:
        teacher = _ensure_user(
            db, TEACHER_EMAIL, TEACHER_PASSWORD, "Анна Воронова", can_create=True
        )
        student = _ensure_user(db, STUDENT_EMAIL, STUDENT_PASSWORD, "Михаил Соколов")

        for old_name, new_name in LEGACY_PROGRAM_NAMES.items():
            old_program = db.execute(
                select(Workspace).where(
                    Workspace.owner_user_id == teacher.id,
                    Workspace.name == old_name,
                    Workspace.kind == "course",
                )
            ).scalar_one_or_none()
            new_program = db.execute(
                select(Workspace).where(
                    Workspace.owner_user_id == teacher.id,
                    Workspace.name == new_name,
                    Workspace.kind == "course",
                )
            ).scalar_one_or_none()
            if old_program is not None and new_program is None:
                old_program.name = new_name
        db.commit()

        for course_name, material_file in VERTICALS:
            course = _ensure_course(db, teacher, course_name)
            ingested = _ensure_material(
                db, course, teacher, material_file, _MATERIALS_DIR / material_file, ingest=ingest
            )
            _ensure_membership(db, course, student)
            if material_file == "corporate-alphadrive-x120.txt":
                _ensure_demo_assignment(db, course, teacher)
            db.refresh(course)
            summary.append(
                {
                    "workspace_id": course.id,
                    "course_name": course.name,
                    "join_code": course.join_code,
                    "material": material_file,
                    "ingested": ingested,
                }
            )
        return summary
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed vertical demo courses for a pitch.")
    parser.add_argument(
        "--no-ingest", action="store_true", help="skip indexing the materials (no embedding model)"
    )
    parser.add_argument("--base-url", default=config.APP_BASE_URL)
    args = parser.parse_args(argv)

    if not args.no_ingest:
        missing = [m for _, m in VERTICALS if not (_MATERIALS_DIR / m).exists()]
        if missing:
            print(f"Demo materials missing: {missing}", file=sys.stderr)
            return 1

    courses = seed_pitch(ingest=not args.no_ingest)
    base = str(args.base_url).rstrip("/")

    print("=" * 60)
    print("Питч-демо готово")
    print("=" * 60)
    print(f"Руководитель: {TEACHER_EMAIL} / {TEACHER_PASSWORD}")
    print(f"Сотрудник:    {STUDENT_EMAIL} / {STUDENT_PASSWORD}")
    print("-" * 60)
    for course in courses:
        status = "проиндексирован" if course["ingested"] else "пропущен (--no-ingest или уже был)"
        print(course["course_name"])
        print(f"  код входа: {course['join_code']}")
        print(f"  ссылка:    {base}/?join={course['join_code']}")
        print(f"  материал:  {course['material']} ({status})")
    print("=" * 60)
    if any(course["ingested"] for course in courses):
        print(
            "ВАЖНО: если backend (run_api.py / контейнер) запущен - перезапусти его.\n"
            "ChromaDB не делит коллекцию между процессами, иначе новые материалы не видны."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
