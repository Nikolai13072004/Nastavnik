"""Five-user HTTP/NDJSON load probe for the isolated Compose fixture only."""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from sqlalchemy import func, select

if os.environ.get("VEDOMO_LOAD_DRILL") != "synthetic-only":
    raise RuntimeError("this probe requires the isolated synthetic load drill")
if os.environ.get("DATABASE_URL") != "postgresql+psycopg://load:synthetic-only@db:5432/load":
    raise RuntimeError("refusing any database other than the synthetic load fixture")

sys.path.insert(0, "/app")
from src.db import SessionLocal
from src.db_models import ChatSession, UsageEvent, User

BASE = "http://backend:8000"
PASSWORD = "Synthetic-load-password-42"
USERS = 5
ROUNDS = 4
STATE = Path("/load-state/state.json")
RESULT = Path("/results/http-load-result.json")


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of an empty sample")
    rank = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[rank]


def wait_healthy() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if requests.get(BASE + "/api/health", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("load drill API did not become healthy")


def api(client, method, path, expected=200, **kwargs):
    response = client.request(method, BASE + path, timeout=60, **kwargs)
    assert response.status_code == expected, (method, path, response.status_code, response.text[:500])
    return response.json()


def register(email: str):
    client = requests.Session()
    body = api(client, "POST", "/api/auth/register", expected=201,
               json={"email": email, "password": PASSWORD})
    client.headers["X-Workspace-ID"] = body["user"]["personal_workspace"]["id"]
    return client, body["user"]


def login(email: str):
    client = requests.Session()
    body = api(client, "POST", "/api/auth/login",
               json={"email": email, "password": PASSWORD})
    return client, body["user"]


def seed() -> None:
    teacher, teacher_user = register("load-teacher@example.com")
    students = [register(f"load-student-{i}@example.com") for i in range(USERS)]
    with SessionLocal() as db:
        db.get(User, teacher_user["id"]).can_create_courses = True
        db.commit()
    course = api(teacher, "POST", "/api/courses", json={"name": "Synthetic product training"})
    teacher.headers["X-Workspace-ID"] = course["id"]
    join_code = api(teacher, "GET", "/api/courses/" + course["id"])["join_code"]
    for client, _ in students:
        api(client, "POST", "/api/courses/join", json={"code": join_code})
    manual = (("Руководство по продукту. Номинальное напряжение оборудования 220 В. "
               "Перед установкой проверьте требования безопасности. ") * 30).encode("utf-8")
    uploaded = api(teacher, "POST", "/api/materials/upload",
                   files={"file": ("product-manual.txt", manual, "text/plain")})
    assert uploaded["ok"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        progress = api(teacher, "GET", "/api/materials/progress")
        if not progress["active"]:
            assert not progress.get("error"), progress
            break
        time.sleep(0.5)
    else:
        raise AssertionError("indexing timeout")
    STATE.write_text(json.dumps({"course_id": course["id"]}), encoding="utf-8")
    print("PASS: course, five students and one indexed synthetic manual are ready")


def one_request(user_index: int, round_index: int, course_id: str) -> dict:
    client, _ = login(f"load-student-{user_index}@example.com")
    client.headers["X-Workspace-ID"] = course_id
    started = time.perf_counter()
    first_token = None
    done = None
    errors = []
    token_count = 0
    response = client.post(
        BASE + "/api/chat/stream",
        json={"message": "Какое напряжение указано в руководстве?", "answer_mode": "Кратко"},
        headers={"X-Request-ID": f"load-u{user_index}-r{round_index}"},
        stream=True,
        timeout=60,
    )
    response.raise_for_status()
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        event = json.loads(line)
        if event["type"] == "token":
            token_count += 1
            first_token = first_token or time.perf_counter()
        elif event["type"] == "done":
            done = event
        elif event["type"] == "error":
            errors.append(event.get("message", "stream_error"))
    finished = time.perf_counter()
    assert response.headers.get("X-Request-ID") == f"load-u{user_index}-r{round_index}"
    assert first_token is not None and token_count > 0
    assert done is not None and not errors
    assert done["session_id"] and done["sources"]
    return {
        "user": user_index,
        "round": round_index,
        "status": response.status_code,
        "ttft_seconds": first_token - started,
        "total_seconds": finished - started,
        "tokens": token_count,
        "sources": len(done["sources"]),
    }


def run() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    work = [(user, round_) for round_ in range(ROUNDS) for user in range(USERS)]
    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=USERS) as pool:
        futures = [pool.submit(one_request, user, round_, state["course_id"]) for user, round_ in work]
        rows = [future.result() for future in as_completed(futures)]
    wall = time.perf_counter() - wall_started
    totals = [row["total_seconds"] for row in rows]
    ttfts = [row["ttft_seconds"] for row in rows]
    with SessionLocal() as db:
        metered = int(db.scalar(select(func.count()).select_from(UsageEvent).where(
            UsageEvent.workspace_id == state["course_id"], UsageEvent.action == "chat")) or 0)
    assert len(rows) == USERS * ROUNDS and metered == len(rows)
    assert all(row["status"] == 200 for row in rows)
    report = {
        "fixture": "synthetic-only",
        "scope": "FastAPI + auth + PostgreSQL + Chroma + retrieval + persistence; deterministic local LLM",
        "concurrency": USERS,
        "rounds": ROUNDS,
        "requests": len(rows),
        "successful": len(rows),
        "metered_chat_events": metered,
        "wall_seconds": wall,
        "throughput_requests_per_second": len(rows) / wall,
        "ttft_seconds": {"p50": statistics.median(ttfts), "p95": percentile(ttfts, .95), "max": max(ttfts)},
        "total_seconds": {"p50": statistics.median(totals), "p95": percentile(totals, .95), "max": max(totals)},
        "sum_request_seconds": sum(totals),
        "provider_cost_measured": False,
        "rows": sorted(rows, key=lambda row: (row["round"], row["user"])),
    }
    assert wall < sum(totals) * .7, "requests did not materially overlap"
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: 20/20 streamed requests, five-way overlap, sources, sessions and metering")


def failure_and_recovery() -> None:
    """A broken provider stream must not bill or persist a truncated turn."""
    course_id = json.loads(STATE.read_text(encoding="utf-8"))["course_id"]
    with SessionLocal() as db:
        usage_before = int(db.scalar(select(func.count()).select_from(UsageEvent).where(
            UsageEvent.workspace_id == course_id, UsageEvent.action == "chat")) or 0)
        sessions_before = int(db.scalar(select(func.count()).select_from(ChatSession).where(
            ChatSession.workspace_id == course_id)) or 0)
    client, _ = login("load-student-0@example.com")
    client.headers["X-Workspace-ID"] = course_id
    response = client.post(BASE + "/api/chat/stream", stream=True, timeout=60,
                           json={"message": "LOAD_DRILL_PROVIDER_FAILURE", "answer_mode": "Кратко"})
    response.raise_for_status()
    events = [json.loads(line) for line in response.iter_lines() if line]
    assert any(event["type"] == "token" for event in events)
    assert [event["type"] for event in events].count("error") == 1
    assert all(event["type"] != "done" for event in events)
    with SessionLocal() as db:
        assert int(db.scalar(select(func.count()).select_from(UsageEvent).where(
            UsageEvent.workspace_id == course_id, UsageEvent.action == "chat")) or 0) == usage_before
        assert int(db.scalar(select(func.count()).select_from(ChatSession).where(
            ChatSession.workspace_id == course_id)) or 0) == sessions_before
    recovered = one_request(0, 99, course_id)
    with SessionLocal() as db:
        assert int(db.scalar(select(func.count()).select_from(UsageEvent).where(
            UsageEvent.workspace_id == course_id, UsageEvent.action == "chat")) or 0) == usage_before + 1
    assert recovered["status"] == 200
    print("PASS: interrupted provider stream was not billed/persisted; retry recovered")


if __name__ == "__main__":
    wait_healthy()
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"seed": seed, "run": run, "failure": failure_and_recovery}[action]()
