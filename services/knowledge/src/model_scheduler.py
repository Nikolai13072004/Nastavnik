"""Priority scheduling for CPU-heavy local model work.

Indexing is throughput work; an interactive retrieval request is latency work.
Both use the same CPU and embedding model in the single-process pilot setup.
This scheduler lets indexing finish its current bounded batch/page, then gives
waiting chat retrieval priority before the next batch starts.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Condition


class ModelScheduler:
    def __init__(self, max_parallel_chats: int = 1):
        self.max_parallel_chats = max(1, int(max_parallel_chats))
        self._condition = Condition()
        self._active_chats = 0
        self._active_indexer = False
        self._waiting_chats = 0

    @contextmanager
    def chat(self):
        with self._condition:
            self._waiting_chats += 1
            try:
                self._condition.wait_for(
                    lambda: not self._active_indexer
                    and self._active_chats < self.max_parallel_chats
                )
                self._active_chats += 1
            finally:
                self._waiting_chats -= 1
        try:
            yield
        finally:
            with self._condition:
                self._active_chats -= 1
                self._condition.notify_all()

    @contextmanager
    def indexing(self):
        with self._condition:
            self._condition.wait_for(
                lambda: not self._active_indexer
                and self._active_chats == 0
                and self._waiting_chats == 0
            )
            self._active_indexer = True
        try:
            yield
        finally:
            with self._condition:
                self._active_indexer = False
                self._condition.notify_all()


import os

scheduler = ModelScheduler(
    max_parallel_chats=int(os.getenv("MODEL_CHAT_CONCURRENCY", "1"))
)
