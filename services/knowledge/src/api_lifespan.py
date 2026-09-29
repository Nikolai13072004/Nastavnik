"""Own the storage process lock and restartable cleanup worker."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import FileLock
from starlette.concurrency import run_in_threadpool

import config
from src.account_deletion import DeletionWorker
from src import document_service
from src.db import SessionLocal


def _recover_interrupted_indexing() -> int:
    db = SessionLocal()
    try:
        return document_service.recover_interrupted_documents(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app):
    path = os.getenv("API_PROCESS_LOCK_PATH", str(Path(config.DATA_DIR) / "api-process.lock"))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Processes sharing docs/Chroma MUST share DATA_DIR too. A second API
    # worker fails startup rather than bypassing the in-process drain.
    with FileLock(path, timeout=0, is_singleton=True, thread_local=False):
        # A background indexing thread cannot survive a process restart. Scrub
        # its partial chunks before the first request can search them.
        await run_in_threadpool(_recover_interrupted_indexing)
        worker = DeletionWorker()
        enabled = os.getenv("ACCOUNT_DELETION_WORKER", "true").lower() == "true"
        if enabled:
            worker.start()
        try:
            yield
        finally:
            if enabled:
                await run_in_threadpool(worker.stop)
