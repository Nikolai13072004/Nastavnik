"""Shared pytest fixtures for the auth/db layer.

We force ``DATABASE_URL`` to a per-test SQLite file *before* anything imports
``config``/``src.db``, then recreate the schema for every test that asks for
the ``db_session`` or ``api_client`` fixture. This keeps the new auth tests
fully isolated from each other and from the developer's local ``data/app.db``.

Existing tests don't request any fixture from this file, so their behaviour
is unchanged — pytest only invokes a fixture when the test (or another
fixture) explicitly depends on it.
"""

from __future__ import annotations

import os
import tempfile
import logging
from collections.abc import Generator

import pytest

# Override the database URL BEFORE config / src.db are imported anywhere.
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(prefix="vedomo_test_", suffix=".db")
os.close(_TMP_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB_PATH}"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-only-for-pytest")
# Rate limiting off by default in tests so the suite isn't throttled; the
# dedicated rate-limit test toggles ``limiter.enabled`` on for its scope.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
# Quotas off by default too (Stage 12) so existing tests aren't blocked by plan
# limits; the billing/quota tests flip ``config.QUOTAS_ENABLED`` on for their scope.
os.environ.setdefault("QUOTAS_ENABLED", "false")
# Email (Stage 23): collect into the in-process outbox so tests can read the
# reset/verify link instead of sending anything.
os.environ["EMAIL_BACKEND"] = "memory"
os.environ["ACCOUNT_DELETION_WORKER"] = "false"
os.environ["API_PROCESS_LOCK_PATH"] = _TMP_DB_PATH + ".api.lock"


from fastapi.testclient import TestClient


@pytest.fixture()
def vedomo_caplog(caplog):
    """Capture application logs even after production logging is configured.

    ``configure_logging`` intentionally stops the ``vedomo`` namespace from
    propagating to the root logger, preventing duplicate lines under Uvicorn.
    Pytest's standard ``caplog`` handler lives on that root logger, so a full
    suite (which imports the ASGI app first) otherwise misses later email logs.
    Attach the existing capture handler directly without changing production
    logging behaviour.
    """
    logger = logging.getLogger("vedomo")
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


class WorkspaceClient(TestClient):
    """Emulate the web client's explicit selection, not a server fallback.

    Tests for missing headers remove it with client.headers.pop().
    Requests with a supplied header always take precedence over defaults.
    """
    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        path = str(url).split("?")[0]
        if response.is_success and method.upper() == "POST":
            if path in {"/api/auth/register", "/api/auth/login"}:
                self.headers["X-Workspace-ID"] = response.json()["user"]["personal_workspace"]["id"]
            elif path in {"/api/courses", "/api/courses/join"} or path.endswith(("/activate", "/copy")):
                self.headers["X-Workspace-ID"] = response.json()["id"]
        return response



@pytest.fixture()
def db_session() -> Generator:
    """Yield a fresh DB session backed by an empty schema."""
    from src.db import Base, SessionLocal, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def api_client() -> Generator:
    """FastAPI TestClient with an empty auth schema for each test."""
    from fastapi.testclient import TestClient

    from api_app import app
    from src.db import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


    with WorkspaceClient(app) as client:
        yield client

    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def authed_client(api_client):
    """``api_client`` pre-authenticated as a freshly-registered regular user.

    TestClient auto-replays cookies set by ``/api/auth/register``, so any
    request issued through this fixture carries a valid session cookie.
    """
    api_client.post(
        "/api/auth/register",
        json={
            "email": "tester@example.com",
            "password": "testpassword123",
            "display_name": "Tester",
        },
    )
    # Course-feature tests (courses / assignments / chat-history) create a course
    # through this client; course creation is gated on ``can_create_courses``
    # (Stage 30). Grant it here so those flows keep working - the gate itself is
    # covered by tests/test_course_permission.py with a plain (ungranted) user.
    from src.db import SessionLocal
    from src.db_models import User

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.email == "tester@example.com").one()
        user.can_create_courses = True
        session.commit()
    finally:
        session.close()
    return api_client


@pytest.fixture()
def superuser_client(api_client):
    """``api_client`` pre-authenticated as a superuser.

    Registers a normal user, then flips ``is_superuser=True`` directly via a
    session — there is intentionally no public API for promoting users.
    """
    from src.db import SessionLocal
    from src.db_models import User

    api_client.post(
        "/api/auth/register",
        json={
            "email": "admin@example.com",
            "password": "adminpassword123",
            "display_name": "Admin",
        },
    )

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.email == "admin@example.com").one()
        user.is_superuser = True
        session.commit()
    finally:
        session.close()

    return api_client


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Best-effort cleanup of the temporary SQLite file."""
    try:
        os.unlink(_TMP_DB_PATH)
    except OSError:
        pass
