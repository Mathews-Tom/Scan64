"""Coalesced wakeups for committed analysis jobs in this process.

The database remains authoritative. Listeners are scoped to its engine and job,
registered before a fresh snapshot, and never retain job history.
"""

import asyncio
import threading
from uuid import UUID

from sqlalchemy import Engine

_MAX_SUBSCRIPTIONS = 64
_MAX_SUBSCRIPTIONS_PER_PLAYER = 8

_lock = threading.Lock()
_listeners: dict[tuple[Engine, UUID], set["AnalysisJobSubscription"]] = {}


class AnalysisSubscriptionLimit(Exception):
    """The process or this player has no free analysis stream slots."""


class AnalysisJobSubscription:
    def __init__(self, engine: Engine, job_id: UUID, player_id: str) -> None:
        self._key = (engine, job_id)
        self._player_id = player_id
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self._pending = False
        self._active = True
        with _lock:
            if sum(map(len, _listeners.values())) >= _MAX_SUBSCRIPTIONS:
                raise AnalysisSubscriptionLimit
            owned = sum(
                listener._player_id == player_id
                for key, listeners in _listeners.items()
                if key[0] is engine
                for listener in listeners
            )
            if owned >= _MAX_SUBSCRIPTIONS_PER_PLAYER:
                raise AnalysisSubscriptionLimit
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
            self._close_locked()

    def _close_locked(self) -> None:
        if not self._active:
            return
        self._active = False
        listeners = _listeners[self._key]
        listeners.remove(self)
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


def close_analysis_subscriptions(engine: Engine, *, player_id: str | None = None) -> None:
    """Wake an app's subscribers on shutdown, or only a deleted player's streams."""
    with _lock:
        for key in tuple(_listeners):
            if key[0] is engine:
                for listener in tuple(_listeners[key]):
                    if player_id is None or listener._player_id == player_id:
                        listener._close_locked()
