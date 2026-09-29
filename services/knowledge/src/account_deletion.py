"""Durable, retryable account cleanup for the single-process deployment."""
import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from src.db import SessionLocal
from src.db_models import AccountDeletionJob, User, Workspace
from src.operation_guard import operations

logger = logging.getLogger(__name__)
_worker_lock = Lock()


def job_for_user(db, user_id):
    return db.scalar(select(AccountDeletionJob).where(AccountDeletionJob.user_id == user_id))


def is_pending(db, user_id):
    job = job_for_user(db, user_id)
    return job is not None and job.status != "completed"


def receipt(job):
    if job is None:
        return {"status": "none", "attempts": 0, "error_code": ""}
    return {"status": job.status, "attempts": job.attempts, "error_code": job.error_code}


def request_deletion(db, user):
    from src.app_services import check_account_deletion_allowed
    existing = job_for_user(db, user.id)
    if existing is not None:
        return receipt(existing)  # duplicate DELETE never resets/reorders work
    check_account_deletion_allowed(db, user)
    job = AccountDeletionJob(
        user_id=user.id,
        workspace_ids=list(db.scalars(select(Workspace.id).where(Workspace.owner_user_id == user.id))),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = job_for_user(db, user.id)
        if existing is None:
            raise
        return receipt(existing)
    return receipt(job)


def process_one():
    """One restart-safe attempt. False = nothing due, or still draining work."""
    if not _worker_lock.acquire(blocking=False):
        return False
    acquired = False
    try:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            job = db.scalar(select(AccountDeletionJob).where(
                AccountDeletionJob.status != "completed",
                or_(AccountDeletionJob.next_attempt_at.is_(None), AccountDeletionJob.next_attempt_at <= now),
            ).order_by(AccountDeletionJob.created_at).limit(1))
            if job is None:
                operations.release_exclusive()
                return False
            if not operations.try_exclusive():
                return False
            acquired = True
            job.status = "processing"
            job.attempts += 1
            job.error_code = ""
            # Requests authenticated before DELETE may have created a space.
            # The drain is complete now: capture the final ownership set.
            job.workspace_ids = sorted(set(job.workspace_ids) | set(db.scalars(
                select(Workspace.id).where(Workspace.owner_user_id == job.user_id)
            )))
            job_id, user_id = job.id, job.user_id
            db.commit()  # receipt survives an abrupt stop during external IO
            try:
                from src.app_services import delete_account_service
                user = db.get(User, user_id)
                if user is None:
                    # User removal + completed receipt are one transaction;
                    # a missing user here signals out-of-band mutation.
                    raise RuntimeError("deletion_owner_missing")
                delete_account_service(db, user, deletion_job=job)
            except Exception:
                db.rollback()
                job = db.get(AccountDeletionJob, job_id)
                job.status = "failed"
                job.error_code = "cleanup_incomplete"
                job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts - 1, 7)))
                db.commit()
                logger.exception("Account cleanup attempt failed: job=%s", job_id)
            return True
    finally:
        if acquired:
            operations.release_exclusive()
        _worker_lock.release()


class DeletionWorker:
    def __init__(self):
        self.stop_event = Event()
        self.thread = Thread(target=self.run, name="account-cleanup", daemon=True)

    def run(self):
        try:
            while not self.stop_event.wait(1):
                try:
                    process_one()
                except Exception:
                    # DB failure must not kill the worker or report success.
                    operations.release_exclusive()
                    logger.exception("Account cleanup worker unavailable")
        finally:
            operations.release_exclusive()

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join()  # lifespan waits off the event loop; receipt is durable
