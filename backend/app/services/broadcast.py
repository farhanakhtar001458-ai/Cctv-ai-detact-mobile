"""Thread-safe WebSocket broadcast hub.

Camera workers run in plain threads; FastAPI/WebSocket objects live on the
asyncio loop. The hub bridges the two with ``loop.call_soon_threadsafe`` and a
bounded per-client queue so a slow dashboard can never stall video processing.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any

log = logging.getLogger(__name__)

MAX_QUEUE = 120


class Client:
    __slots__ = ("queue", "topics", "username", "role")

    def __init__(self, topics: set[str], username: str = "", role: str = "viewer") -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=MAX_QUEUE)
        self.topics = topics
        self.username = username
        self.role = role


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=200)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- subscription ------------------------------------------------------
    def register(self, client: Client) -> None:
        with self._lock:
            self._clients.add(client)

    def unregister(self, client: Client) -> None:
        with self._lock:
            self._clients.discard(client)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _deliver(self, payload: dict[str, Any]) -> None:
        with self._lock:
            clients = list(self._clients)
        topic = payload.get("topic", "")
        for client in clients:
            if client.topics and topic not in client.topics and "*" not in client.topics:
                continue
            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest frame update rather than blocking the worker.
                try:
                    client.queue.get_nowait()
                    client.queue.put_nowait(payload)
                except Exception:  # noqa: BLE001
                    pass

    def publish_threadsafe(self, payload: dict[str, Any]) -> None:
        """Publish from any thread (camera workers, TTS thread)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, payload)
        except RuntimeError:  # loop shutting down
            pass
        if payload.get("topic") in ("event", "alert", "voice"):
            self._recent.append(payload)

    async def publish(self, payload: dict[str, Any]) -> None:
        self._deliver(payload)

    def recent(self) -> list[dict[str, Any]]:
        return list(self._recent)


broadcaster = Broadcaster()
