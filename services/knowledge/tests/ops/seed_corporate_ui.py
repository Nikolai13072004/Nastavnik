"""Seed a disposable local UI review database. Never use on a real instance."""
import os
import sys
from pathlib import Path

if os.environ.get("VEDOMO_UI_FIXTURE") != "synthetic-only":
    raise RuntimeError("UI fixture requires the synthetic-only marker")
if "output/playwright/corporate-pilot.db" not in os.environ.get("DATABASE_URL", ""):
    raise RuntimeError("refusing to seed a non-fixture database")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import requests
from src.db import SessionLocal
from src.db_models import Assignment, AssignmentAttempt, UsageEvent, User

BASE = "http://127.0.0.1:8000"
PASSWORD = "password12345"
owner = requests.Session()
owner_body = owner.post(BASE + "/api/auth/register", json={
    "email": "owner@company.ru", "password": PASSWORD, "display_name": "Анна Воронова",
}).json()
with SessionLocal() as db:
    db.get(User, owner_body["user"]["id"]).can_create_courses = True
    db.commit()
course = owner.post(BASE + "/api/courses", json={
    "name": "Академия продукта · Осень 2026",
}).json()
course_id = course["id"]
join_code = owner.get(f"{BASE}/api/courses/{course_id}").json()["join_code"]

student = requests.Session()
student_body = student.post(BASE + "/api/auth/register", json={
    "email": "m.sokolov@company.ru", "password": PASSWORD, "display_name": "Михаил Соколов",
}).json()
student.post(BASE + "/api/courses/join", json={"code": join_code}).raise_for_status()
owner.post(f"{BASE}/api/courses/{course_id}/members/{student_body['user']['id']}/group",
           json={"group_name": "Региональные продажи"}).raise_for_status()
owner.post(f"{BASE}/api/courses/{course_id}/invitations", json={
    "email": "elena@company.ru", "role": "student", "group_name": "Сервис",
}).raise_for_status()

with SessionLocal() as db:
    assignment = Assignment(
        workspace_id=course_id, created_by_user_id=owner_body["user"]["id"],
        title="Знание линейки", questions=[{
            "question": "Q", "options": ["A", "B"], "correct_index": 0,
        }], is_published=True,
    )
    db.add(assignment)
    db.flush()
    db.add(AssignmentAttempt(
        assignment_id=assignment.id, user_id=student_body["user"]["id"],
        answers=[0], score=1, total=1,
    ))
    db.add(UsageEvent(
        workspace_id=course_id, user_id=student_body["user"]["id"], action="chat",
        units=1, billing_subject_type="user", billing_subject_id=student_body["user"]["id"],
    ))
    db.commit()
print(course_id)
