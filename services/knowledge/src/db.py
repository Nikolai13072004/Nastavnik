"""SQLAlchemy engine + session factory for the multi-user foundation.

This module only sets up the connection plumbing. ORM models live in
``src.db_models`` and are registered against :data:`Base` defined here.

Design notes:
- ``DATABASE_URL`` is read from :mod:`config`, which in turn reads it from the
  environment. Tests override it via env var before importing this module
  (see ``tests/conftest.py``).
- For SQLite we pass ``check_same_thread=False`` so the FastAPI test client and
  background threads (used by ``src.app_services``) can share a connection
  without raising.
- The actual schema is created by Alembic migrations
  (``alembic upgrade head``). Tests use ``Base.metadata.create_all(engine)``
  directly through a fixture — Alembic is reserved for real deployments.
"""

from __future__ import annotations

import os
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config


def _build_engine(database_url: str):
    """Create a SQLAlchemy engine with sensible defaults per backend."""
    connect_args: dict = {}
    engine_kwargs: dict = {"future": True}
    if database_url.startswith("sqlite"):
        # FastAPI test client and background indexer threads need to share
        # the connection; this is the standard SQLite-with-threads recipe.
        connect_args["check_same_thread"] = False
    else:
        # Postgres (prod): the default QueuePool (5 + 10 overflow = 15) is too
        # small. Chat streams hold their request session for the whole answer,
        # background indexing opens its own session, and the SPA polls several
        # endpoints frequently - with a few concurrent users the pool exhausts
        # and requests fail with "QueuePool limit ... connection timed out"
        # (seen on the pilot box: a request hangs ~30s, then the SPA bounces the
        # user to /login). Give it real headroom and pre-ping/recycle so stale
        # connections left after a backend restart (e.g. the nightly backup) are
        # transparently replaced. Env-overridable for tuning without a redeploy.
        engine_kwargs.update(
            pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "40")),
            pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
            pool_pre_ping=True,
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
        )
    return create_engine(database_url, connect_args=connect_args, **engine_kwargs)


engine = _build_engine(config.DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models in :mod:`src.db_models`."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
