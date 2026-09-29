"""Synthetic-only API entrypoint for the HTTP concurrency drill.

It keeps the real FastAPI, PostgreSQL, Chroma, auth, retrieval and persistence
paths, but replaces the paid LLM boundary with a slow deterministic stream.
This makes the drill repeatable and guarantees that it cannot spend provider
credits or send course material outside the isolated Docker network.
"""
from __future__ import annotations

import os
import sys
import time

if os.environ.get("VEDOMO_LOAD_DRILL") != "synthetic-only":
    raise RuntimeError("load drill entrypoint requires the synthetic-only marker")

EXPECTED_DB = "postgresql+psycopg://load:synthetic-only@db:5432/load"
if os.environ.get("DATABASE_URL") != EXPECTED_DB:
    raise RuntimeError("refusing to run the load fixture against a non-fixture database")

sys.path.insert(0, "/app")
from src import runtime


class DeterministicStreamingLLM:
    """Predictable provider substitute; intentionally not a quality benchmark."""

    _tokens = (
        "Согласно ", "руководству, ", "оборудование ", "работает ",
        "при напряжении ", "220 В. ", "Перед установкой ",
        "проверьте требования безопасности.",
    )

    def stream(self, prompt, temperature=None, max_tokens=None):
        fail = "LOAD_DRILL_PROVIDER_FAILURE" in prompt
        for index, token in enumerate(self._tokens):
            time.sleep(0.075)
            yield token
            if fail and index == 1:
                raise RuntimeError("synthetic provider interruption")

    def call(self, prompt, temperature=None, max_tokens=None):
        return "".join(self._tokens)


runtime._llm = DeterministicStreamingLLM()

from api_app import app  # noqa: E402  (fixture must patch runtime first)
