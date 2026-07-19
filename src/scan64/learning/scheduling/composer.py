from __future__ import annotations

import math
from collections.abc import Collection
from typing import Any


class SessionComposer:
    def __init__(
        self, mix_config: dict[str, float] | None = None, hard_exploration_floor: float = 0.1
    ) -> None:
        self.mix_config = mix_config or {
            "due": 0.4,
            "mistakes": 0.3,
            "transfer": 0.2,
            "exploration": 0.1,
        }
        self.hard_exploration_floor = hard_exploration_floor

    def compose_session(
        self,
        pool: list[dict[str, Any]],
        session_size: int = 15,
        required_item_ids: Collection[str] = (),
    ) -> list[dict[str, Any]]:
        """Compose a priority-ranked session while retaining required content."""
        if not pool or session_size <= 0:
            return []

        required_items = self._required_items(pool, required_item_ids, session_size)
        required_object_ids = {id(item) for item in required_items}
        grouped_candidates = self._group_candidates(pool, required_object_ids)
        exploration_min = int(math.ceil(session_size * self.hard_exploration_floor))
        target_counts = self._target_counts(session_size, required_items, exploration_min)

        session = list(required_items)
        for item_type, target in target_counts.items():
            session.extend(grouped_candidates.get(item_type, [])[:target])

        if len(session) < session_size:
            session.extend(self._remaining_items(pool, session)[: session_size - len(session)])

        if len(session) > session_size:
            session = self._trim_session(
                session,
                session_size,
                exploration_min,
                required_object_ids,
            )

        return session

    @staticmethod
    def _required_items(
        pool: list[dict[str, Any]],
        required_item_ids: Collection[str],
        session_size: int,
    ) -> list[dict[str, Any]]:
        unique_required_item_ids = tuple(dict.fromkeys(required_item_ids))
        required_items: list[dict[str, Any]] = []
        for item_id in unique_required_item_ids:
            matching_items = [item for item in pool if item.get("id") == item_id]
            if len(matching_items) != 1:
                raise ValueError(f"Required session item {item_id!r} is missing or ambiguous")
            required_items.append(matching_items[0])

        if len(required_items) > session_size:
            raise ValueError("Required session items exceed session size")
        return required_items

    @staticmethod
    def _group_candidates(
        pool: list[dict[str, Any]],
        excluded_object_ids: set[int],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped_candidates: dict[str, list[dict[str, Any]]] = {}
        for item in pool:
            if id(item) in excluded_object_ids:
                continue
            item_type = item.get("type", "exploration")
            grouped_candidates.setdefault(item_type, []).append(item)

        for candidates in grouped_candidates.values():
            candidates.sort(key=lambda item: item.get("priority", 0.0), reverse=True)
        return grouped_candidates

    def _target_counts(
        self,
        session_size: int,
        required_items: list[dict[str, Any]],
        exploration_min: int,
    ) -> dict[str, int]:
        target_counts = {
            item_type: int(math.ceil(session_size * proportion))
            for item_type, proportion in self.mix_config.items()
        }
        target_counts["exploration"] = max(
            target_counts.get("exploration", 0),
            exploration_min,
        )

        for item in required_items:
            item_type = item.get("type", "exploration")
            target_counts[item_type] = max(target_counts.get(item_type, 0) - 1, 0)
        return target_counts

    @staticmethod
    def _remaining_items(
        pool: list[dict[str, Any]],
        session: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        selected_object_ids = {id(item) for item in session}
        remaining_items = [
            item for item in pool if id(item) not in selected_object_ids
        ]
        remaining_items.sort(
            key=lambda item: (
                item.get("priority", 0.0),
                item.get("type") == "due",
            ),
            reverse=True,
        )
        return remaining_items

    @classmethod
    def _trim_session(
        cls,
        session: list[dict[str, Any]],
        session_size: int,
        exploration_min: int,
        required_object_ids: set[int],
    ) -> list[dict[str, Any]]:
        required_items = [item for item in session if id(item) in required_object_ids]
        optional_items = [item for item in session if id(item) not in required_object_ids]
        required_exploration_count = sum(
            item.get("type") == "exploration" for item in required_items
        )
        additional_exploration_needed = max(
            exploration_min - required_exploration_count,
            0,
        )
        available_exploration_slots = max(session_size - len(required_items), 0)
        exploration_items = sorted(
            (item for item in optional_items if item.get("type") == "exploration"),
            key=lambda item: item.get("priority", 0.0),
            reverse=True,
        )[:min(additional_exploration_needed, available_exploration_slots)]
        secured_object_ids = {id(item) for item in required_items + exploration_items}
        remaining_items = cls._remaining_items(optional_items, exploration_items)
        slots_left = max(session_size - len(required_items) - len(exploration_items), 0)
        return required_items + exploration_items + [
            item for item in remaining_items if id(item) not in secured_object_ids
        ][:slots_left]
