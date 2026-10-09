"""Async rate limiting primitives."""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Simple tokenless limiter that enforces a minimum interval."""

    def __init__(self, requests_per_second: float):
        self.requests_per_second = requests_per_second
        self.min_interval = 0.0 if requests_per_second <= 0 else 1.0 / requests_per_second
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last_request_at
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self._last_request_at = time.monotonic()
