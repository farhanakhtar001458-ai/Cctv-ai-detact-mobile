"""Small in-process rate limiter (sliding window) for sensitive endpoints.

Works for a single process / single container. For multi-worker deployments
point ``SCAI_RATE_LIMIT_BACKEND=redis`` at a shared store (see docs/DEPLOYMENT.md)
or run the API with a single uvicorn worker and scale vision workers separately.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import settings


def parse_limit(spec: str) -> tuple[int, float]:
    """Parse '10/minute' -> (10, 60.0)."""
    count_str, _, unit = spec.partition("/")
    count = int(count_str)
    unit = unit.strip().lower() or "minute"
    windows = {"second": 1.0, "minute": 60.0, "hour": 3600.0, "day": 86400.0}
    if unit not in windows:
        raise ValueError(f"Unsupported rate-limit unit: {unit}")
    return count, windows[unit]


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_s:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(request: Request, bucket: str, spec: str | None = None) -> None:
    """Raise HTTP 429 when the caller exceeded the bucket budget."""
    if not settings.rate_limit_enabled:
        return
    limit, window = parse_limit(spec or settings.write_rate_limit)
    key = f"{bucket}:{client_ip(request)}"
    if not limiter.check(key, limit, window):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please slow down",
            headers={"Retry-After": str(int(window))},
        )
