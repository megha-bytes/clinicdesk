"""In-process pub/sub for live events, with a short history per channel for reconnects.

Channels: `thread:<thread_id>` (one patient conversation) and `ops:<clinic_id>` (staff dashboard).
Publishers are worker threads running the graph; subscribers are asyncio tasks (SSE / WebSocket).
Each channel numbers its events (`cursor`) so a client can reconnect with "everything after N".
Single-process by design (one Render instance); swap for Redis pub/sub to scale out.
"""
from __future__ import annotations

import asyncio
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass(eq=False)
class Subscriber:
    channel: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))


class Hub:
    def __init__(self, history: int = 200) -> None:
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=history))
        self._cursor: dict[str, int] = defaultdict(int)
        self._subs: dict[str, set[Subscriber]] = defaultdict(set)
        self._lock = threading.Lock()

    def publish(self, channel: str, event: dict) -> dict:
        with self._lock:
            self._cursor[channel] += 1
            ev = {**event, "cursor": self._cursor[channel]}
            self._history[channel].append(ev)
            subs = list(self._subs[channel])
        for s in subs:
            try:
                s.loop.call_soon_threadsafe(_put, s.queue, ev)
            except RuntimeError:          # subscriber's loop is closed
                self.unsubscribe(s)
        return ev

    def subscribe(self, channel: str, after: int | None = None) -> tuple[Subscriber, list[dict]]:
        sub = Subscriber(channel, asyncio.get_running_loop())
        with self._lock:
            self._subs[channel].add(sub)
            backlog = [e for e in self._history[channel] if after is not None and e["cursor"] > after]
        return sub, backlog

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs[sub.channel].discard(sub)

    def history(self, channel: str) -> list[dict]:
        with self._lock:
            return list(self._history[channel])

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._cursor.clear()


def _put(q: asyncio.Queue, ev: dict) -> None:
    if q.full():                          # a stuck client must not block everyone else
        q.get_nowait()
    q.put_nowait(ev)


hub = Hub()


def thread_channel(thread_id: str) -> str:
    return f"thread:{thread_id}"


def ops_channel(clinic_id: str) -> str:
    return f"ops:{clinic_id}"
