"""Coalesced wakeups for committed analysis jobs in this process.

The database remains authoritative. Listeners are scoped to its engine and job,
registered before a fresh snapshot, and never retain job history.
"""

import asyncio
import threading
from uuid import UUID

from sqlalchemy import Engine

_lock = threading.Lock()
_listeners: dict[tuple[Engine, UUID], set["AnalysisJobSubscription"]] = {}


class AnalysisJobSubscription:
    def __init__(self, engine: Engine, job_id: UUID) -> None:
        self._key = (engine, job_id)
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self._pending = False
        self._active = True
        with _lock:
            _listeners.setdefault(self._key, set()).add(self)

    async def wait(self) -> bool:
        """Wait for a committed change, or return false on shutdown."""
        await self._event.wait()
        with _lock:
            self._event.clear()
            self._pending = False
            return self._active

    def _notify_locked(self) -> None:
        # At most one callback AND one event bit per listener, even if an offline
        # producer thread commits faster than the HTTP loop can drain updates.
        if not self._pending:
            self._pending = True
            try:
                self._loop.call_soon_threadsafe(self._event.set)
            except RuntimeError:
                # A closed event loop cannot own a live HTTP subscriber.
                self._active = False

    def close(self) -> None:
        with _lock:
            if not self._active:
                return
            self._active = False
            listeners = _listeners.get(self._key)
            if listeners is not None:
                listeners.discard(self)
                if not listeners:
                    del _listeners[self._key]
            self._notify_locked()


def notify_analysis_job(engine: Engine, job_id: UUID) -> None:
    """Call only after committing an authoritative status transition."""
    with _lock:
        listeners = _listeners.get((engine, job_id))
        if listeners is None:
            return
        for listener in tuple(listeners):
            listener._notify_locked()
            if not listener._active:
                listeners.discard(listener)
        if not listeners:
            del _listeners[(engine, job_id)]


def close_analysis_subscriptions(engine: Engine) -> None:
    """Wake and release this app's subscribers before stopping its producers."""
    with _lock:
        for key in tuple(_listeners):
            if key[0] is engine:
                for listener in _listeners.pop(key):
                    listener._active = False
                    listener._notify_locked()
