"""Material-indexing progress + cooperative-cancel state behind a small seam
(Phase A-prep, Stage 56).

Today this is an **in-process** implementation - correct for the single uvicorn
worker the Docker deployment runs. The point of the seam is forward-looking:
``app_services`` talks only to this interface (get/set progress, request/check/
clear cancel), so going multi-instance later is a drop-in replacement - e.g. a
``RedisProgressStore`` with the same methods - instead of surgery across the
service layer. Nothing about current behaviour changes.
"""

from __future__ import annotations

from threading import Lock

# Default snapshot for a workspace with no in-flight material job.
IDLE_PROGRESS_STATE: dict = {
    "active": False,
    "operation": "idle",
    "phase": "",
    "message": "",
    "progress": 0,
    "current_file": "",
    "error": "",
}


class InMemoryProgressStore:
    """Per-workspace progress + cancel flags, guarded by one lock.

    A future shared-backend store (Redis) would expose the same public methods;
    this class is the reference (and only) implementation for one process.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._progress: dict[str, dict] = {}
        # Cancel flags kept separate from progress so they never leak into the
        # MaterialProgressResponse(**snapshot) the API returns.
        self._cancel: dict[str, bool] = {}

    # --- progress -------------------------------------------------------------
    def get(self, workspace_id: str) -> dict:
        """A copy of the workspace's progress snapshot (idle if none)."""
        with self._lock:
            return dict(self._progress.get(workspace_id, IDLE_PROGRESS_STATE))

    def set(self, workspace_id: str, **updates) -> None:
        with self._lock:
            state = self._progress.setdefault(workspace_id, dict(IDLE_PROGRESS_STATE))
            state.update(updates)

    # --- cooperative cancel ---------------------------------------------------
    def request_cancel(self, workspace_id: str) -> None:
        with self._lock:
            self._cancel[workspace_id] = True

    def is_cancel_requested(self, workspace_id: str) -> bool:
        with self._lock:
            return self._cancel.get(workspace_id, False)

    def clear_cancel(self, workspace_id: str) -> None:
        with self._lock:
            self._cancel.pop(workspace_id, None)

    def reset(self) -> None:
        """Drop all progress + cancel state (test helper)."""
        with self._lock:
            self._progress.clear()
            self._cancel.clear()
