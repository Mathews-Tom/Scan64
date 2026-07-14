import math
from typing import Any


class SessionComposer:
    def __init__(
        self, mix_config: dict[str, float] | None = None, hard_exploration_floor: float = 0.1
    ):
        """
        mix_config defines the ideal proportion of each item type.
        e.g. {"due": 0.4, "mistakes": 0.3, "transfer": 0.2, "exploration": 0.1}
        hard_exploration_floor is the minimum proportion that must be exploration/fundamentals.
        """
        self.mix_config = mix_config or {
            "due": 0.4,
            "mistakes": 0.3,
            "transfer": 0.2,
            "exploration": 0.1,
        }
        self.hard_exploration_floor = hard_exploration_floor

    def compose_session(
        self, pool: list[dict[str, Any]], session_size: int = 15
    ) -> list[dict[str, Any]]:
        """
        Compose a session from a candidate pool of items.
        Each item in the pool should have a 'type' matching the mix_config keys,
        and ideally a 'priority' score.
        """
        if not pool or session_size <= 0:
            return []

        # Group candidates by type, sorted by priority (highest first)
        grouped_candidates: dict[str, list[dict[str, Any]]] = {}
        for item in pool:
            t = item.get("type", "exploration")  # Default to exploration if unknown
            grouped_candidates.setdefault(t, []).append(item)

        for t in grouped_candidates:
            grouped_candidates[t].sort(key=lambda x: x.get("priority", 0.0), reverse=True)

        session = []

        # Calculate target counts based on mix config
        target_counts = {
            t: int(math.ceil(session_size * proportion))
            for t, proportion in self.mix_config.items()
        }

        # Ensure hard floor for exploration
        exploration_min = int(math.ceil(session_size * self.hard_exploration_floor))
        target_counts["exploration"] = max(target_counts.get("exploration", 0), exploration_min)

        # We might over-allocate slightly due to ceil, but we'll truncate at the end

        for t, target in target_counts.items():
            candidates = grouped_candidates.get(t, [])
            selected = candidates[:target]
            session.extend(selected)

        # If we are short, fill with whatever is highest priority across remaining items
        if len(session) < session_size:
            # Re-collect all unused items
            used_ids = {id(item) for item in session}
            remaining = [item for item in pool if id(item) not in used_ids]
            remaining.sort(key=lambda x: x.get("priority", 0.0), reverse=True)

            # How many more do we need?
            needed = session_size - len(session)
            session.extend(remaining[:needed])

        # Still over capacity? We need to trim, but MUST respect exploration floor
        if len(session) > session_size:
            # We want to keep up to session_size items.
            # First, secure the exploration items
            exploration_items = [item for item in session if item.get("type") == "exploration"]
            other_items = [item for item in session if item.get("type") != "exploration"]

            # Sort other items by priority
            other_items.sort(key=lambda x: x.get("priority", 0.0), reverse=True)

            # How many exploration items to keep? At least exploration_min, up to available
            keep_exploration = min(len(exploration_items), exploration_min)

            # We might keep more exploration if we are short on other items,
            # but usually we trim the lowest priority non-exploration items.
            # Let's just do a simple selection

            final_session = []

            # Secure minimum exploration
            final_session.extend(exploration_items[:keep_exploration])
            remaining_exploration = exploration_items[keep_exploration:]

            # How many slots left?
            slots_left = session_size - len(final_session)

            # Pool remaining exploration with other items, sort by priority
            pool_for_remaining = remaining_exploration + other_items
            pool_for_remaining.sort(key=lambda x: x.get("priority", 0.0), reverse=True)

            final_session.extend(pool_for_remaining[:slots_left])
            session = final_session

        # Return the final composed session
        return session
