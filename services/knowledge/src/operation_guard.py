"""Drain API requests (including streams) and indexer threads before cleanup.

Single API process per storage volume, enforced by the application lifespan.
This is not a distributed lock or a multi-instance deployment mechanism.
"""
from threading import Lock
from starlette.responses import JSONResponse


class OperationGuard:
    def __init__(self):
        self._lock = Lock()
        self._active = 0
        self._draining = False
        self._exclusive = False

    def enter(self, *, continuation=False):
        with self._lock:
            if self._exclusive or (self._draining and not continuation):
                return False
            self._active += 1
            return True

    def leave(self):
        with self._lock:
            self._active -= 1
            assert self._active >= 0

    def try_exclusive(self):
        with self._lock:
            self._draining = True
            if self._active or self._exclusive:
                return False
            self._exclusive = True
            return True

    def release_exclusive(self):
        with self._lock:
            self._draining = False
            self._exclusive = False


operations = OperationGuard()


class CleanupDrainMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Health and the read-only receipt poll must work while draining.
        exempt = scope.get("method") == "GET" and scope.get("path") in {
            "/api/health", "/api/auth/deletion",
        }
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/") or exempt:
            return await self.app(scope, receive, send)
        if not operations.enter():
            return await JSONResponse(
                {"detail": "cleanup_in_progress"}, status_code=503,
                headers={"Retry-After": "2"},
            )(scope, receive, send)
        try:
            # Pure ASGI: release only after the stream/background response ends.
            await self.app(scope, receive, send)
        finally:
            operations.leave()
