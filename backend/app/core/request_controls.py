from __future__ import annotations

import hashlib
import os
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from threading import Lock
from time import monotonic

from fastapi import Request, status

from backend.app.core.api_errors import ApiContractException


def _positive_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


class GenerationRequestController:
    """Process-local guard for bounded model usage.

    This deliberately protects a single backend process. A shared store is
    still required before running multiple replicas.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._recent: dict[str, deque[float]] = defaultdict(deque)
        self._active: dict[str, int] = defaultdict(int)

    def acquire(self, key: str) -> None:
        now = monotonic()
        rate_limit = _positive_int("BACKEND_RATE_LIMIT_PER_MINUTE", 20, 600)
        concurrency_limit = _positive_int(
            "BACKEND_MAX_CONCURRENT_GENERATIONS_PER_CLIENT", 2, 20
        )

        with self._lock:
            recent = self._recent[key]
            while recent and now - recent[0] >= 60:
                recent.popleft()

            if len(recent) >= rate_limit:
                retry_after = max(1, int(60 - (now - recent[0])))
                raise ApiContractException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    code="RATE_LIMITED",
                    message="Too many generation requests. Please try again shortly.",
                    retryable=True,
                    headers={"Retry-After": str(retry_after)},
                )

            if self._active[key] >= concurrency_limit:
                raise ApiContractException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    code="CONCURRENCY_LIMITED",
                    message="A generation request is already in progress. Please wait for it to finish.",
                    retryable=True,
                    headers={"Retry-After": "1"},
                )

            recent.append(now)
            self._active[key] += 1

    def release(self, key: str) -> None:
        with self._lock:
            self._active[key] = max(0, self._active[key] - 1)

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            self._active.clear()


generation_request_controller = GenerationRequestController()


def _client_fingerprint(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        return f"key:{digest[:24]}"
    host = request.client.host if request.client else "unknown"
    return f"client:{host}"


async def generation_request_guard(request: Request) -> AsyncIterator[None]:
    fingerprint = _client_fingerprint(request)
    generation_request_controller.acquire(fingerprint)
    try:
        yield
    finally:
        generation_request_controller.release(fingerprint)
