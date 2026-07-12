import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class AdmissionConfig:
    daily_quota_games: int = 50


class AdmissionController:
    """
    Manages per-player-per-day quotas and fair-share queueing for batch tasks.
    Tasks within the daily quota execute immediately (subject to pool concurrency).
    Tasks exceeding the quota are queued and executed round-robin across players.
    """

    def __init__(self, config: AdmissionConfig = AdmissionConfig()):
        self.config = config

        # Track usage: player_id -> (date_str, count)
        self.usage: dict[str, tuple[str, int]] = {}

        # Fair-share queue: player_id -> deque of tasks
        self.queues: dict[str, deque[Callable[[], Awaitable[Any]]]] = defaultdict(deque)

        # Players currently in the queue, to enable round-robin
        self.active_players: deque[str] = deque()

        # Background worker task
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def _get_current_date(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _increment_usage(self, player_id: str):
        date_str = self._get_current_date()
        if player_id in self.usage:
            last_date, count = self.usage[player_id]
            if last_date == date_str:
                self.usage[player_id] = (date_str, count + 1)
            else:
                self.usage[player_id] = (date_str, 1)
        else:
            self.usage[player_id] = (date_str, 1)

    def _get_usage(self, player_id: str) -> int:
        date_str = self._get_current_date()
        if player_id in self.usage:
            last_date, count = self.usage[player_id]
            if last_date == date_str:
                return count
        return 0

    def submit(
        self, player_id: str, task_func: Callable[[], Awaitable[Any]]
    ) -> asyncio.Future[Any]:
        future: asyncio.Future[Any] = asyncio.Future()

        async def wrapped_task():
            try:
                result = await task_func()
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)

        usage = self._get_usage(player_id)
        if usage < self.config.daily_quota_games:
            # Within quota: execute "immediately" (meaning, schedule it on the event loop now)
            self._increment_usage(player_id)
            asyncio.create_task(wrapped_task())
        else:
            # Over quota: add to fair-share queue
            if player_id not in self.active_players:
                self.active_players.append(player_id)
            self.queues[player_id].append(wrapped_task)

            # Start worker if not running
            if self._worker_task is None or self._worker_task.done():
                self._stop_event.clear()
                self._worker_task = asyncio.create_task(self._fair_share_worker())

        return future

    async def _fair_share_worker(self):
        while not self._stop_event.is_set():
            if not self.active_players:
                # Sleep briefly if no players have queued tasks
                await asyncio.sleep(0.1)
                continue

            # Pop the next player in round-robin order
            player_id = self.active_players.popleft()

            if self.queues[player_id]:
                task_func = self.queues[player_id].popleft()
                # Run one task for this player
                # Await it here so the round-robin is serialized at the dispatcher level,
                # letting the pool naturally bound execution.
                try:
                    await task_func()
                except Exception:
                    pass  # Exception is handled and set on the future inside wrapped_task

            # If the player still has tasks, put them back at the end of the line
            if self.queues[player_id]:
                self.active_players.append(player_id)

    def stop(self):
        self._stop_event.set()
        if self._worker_task:
            self._worker_task.cancel()
