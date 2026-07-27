"""Interim per-player bound on concurrent analysis work.

This is a temporary guard so one player's finished games cannot occupy the
engine indefinitely; it bounds concurrency per player, not globally. The queue
lives in this process only, so work queued when the process dies stays
``pending`` with nothing to resume it. Both limitations are deliberate for the
interim: M41's admission controller owns quotas, fair-share scheduling, and
durability, and removes this module. A drain also occupies the request
threadpool thread that scheduled it for as long as that player's queue runs,
which is another reason it is interim. Keep it to one submission call site.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from uuid import UUID

from scan64.chess.analysis.jobs import execute_analysis_job

DEFAULT_IN_FLIGHT_CAP = 2

logger = logging.getLogger(__name__)


def in_flight_cap() -> int:
    raw_cap = os.environ.get("SCAN64_ANALYSIS_IN_FLIGHT_CAP")
    if raw_cap is None:
        return DEFAULT_IN_FLIGHT_CAP
    try:
        cap = int(raw_cap)
    except ValueError as error:
        raise ValueError(
            f"SCAN64_ANALYSIS_IN_FLIGHT_CAP must be an integer, got {raw_cap!r}"
        ) from error
    if cap < 1:
        raise ValueError("SCAN64_ANALYSIS_IN_FLIGHT_CAP must be at least 1")
    return cap


class InFlightAnalysisLimiter:
    """Run at most ``cap`` analysis jobs per player, queueing the remainder."""

    def __init__(self, cap: int, runner: Callable[[UUID], None] = execute_analysis_job) -> None:
        if cap < 1:
            raise ValueError("cap must be at least 1")
        self.cap = cap
        self._runner = runner
        self._lock = threading.Lock()
        self._in_flight: dict[str, int] = defaultdict(int)
        self._queued: dict[str, deque[UUID]] = defaultdict(deque)

    def queue_depth(self, player_id: str) -> int:
        with self._lock:
            queued = self._queued.get(player_id)
            return len(queued) if queued else 0

    def in_flight(self, player_id: str) -> int:
        with self._lock:
            return self._in_flight.get(player_id, 0)

    def submit(self, player_id: str, job_id: UUID) -> None:
        """Run ``job_id`` now, or queue it behind this player's running work."""
        with self._lock:
            if self._in_flight[player_id] >= self.cap:
                self._queued[player_id].append(job_id)
                return
            self._in_flight[player_id] += 1

        self._drain(player_id, job_id)

    def _drain(self, player_id: str, job_id: UUID) -> None:
        next_job: UUID | None = job_id
        while next_job is not None:
            running = next_job
            try:
                self._runner(running)
            except Exception:
                # execute_analysis_job records its own failures on the job row;
                # anything reaching here is infrastructure-level, so log it and
                # keep draining rather than stranding the queue behind it.
                logger.exception("Analysis job %s failed", running)
            except BaseException:
                # Shutdown: give the slot back and leave the queue intact, so
                # the next submit for this player drains it.
                self._release(player_id, hand_off=False)
                raise
            next_job = self._release(player_id)

    def _release(self, player_id: str, *, hand_off: bool = True) -> UUID | None:
        with self._lock:
            queued = self._queued.get(player_id)
            if hand_off and queued:
                return queued.popleft()
            if not queued:
                self._queued.pop(player_id, None)
            remaining = self._in_flight[player_id] - 1
            if remaining > 0:
                self._in_flight[player_id] = remaining
            else:
                self._in_flight.pop(player_id, None)
            return None


analysis_limiter = InFlightAnalysisLimiter(in_flight_cap())
