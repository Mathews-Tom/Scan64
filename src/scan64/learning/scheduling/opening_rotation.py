from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import chess

from scan64.content.openings.models import OpeningFamilyPayload


@dataclass(frozen=True)
class OpeningRotationPlan:
    required_family_id: str | None
    ordered_family_ids: tuple[str, ...]
    familiar_family_id: str | None
    response_review_family_id: str | None


class OpeningRotationPlanner:
    def __init__(self, history_window: int = 5) -> None:
        if history_window < 1:
            raise ValueError("history_window must be at least one")
        self.history_window = history_window

    def plan(
        self,
        families: Sequence[OpeningFamilyPayload],
        recent_family_ids: Sequence[str],
    ) -> OpeningRotationPlan:
        family_by_id = _family_by_id(families)
        recent_history = tuple(recent_family_ids[-self.history_window :])
        unknown_family_ids = set(recent_history).difference(family_by_id)
        if unknown_family_ids:
            joined_ids = ", ".join(sorted(unknown_family_ids))
            raise ValueError(f"Unknown opening family IDs in history: {joined_ids}")

        familiar_family_id = recent_history[-1] if recent_history else None
        response_review_family_id = (
            familiar_family_id
            if familiar_family_id is not None
            and family_by_id[familiar_family_id].opponent_response_moves
            else None
        )
        required_family_id = self._required_contrast(family_by_id, recent_history)
        ordered_family_ids = _ordered_family_ids(
            family_by_id,
            familiar_family_id,
            required_family_id,
        )
        return OpeningRotationPlan(
            required_family_id=required_family_id,
            ordered_family_ids=ordered_family_ids,
            familiar_family_id=familiar_family_id,
            response_review_family_id=response_review_family_id,
        )

    def _required_contrast(
        self,
        family_by_id: dict[str, OpeningFamilyPayload],
        recent_history: tuple[str, ...],
    ) -> str | None:
        if len(recent_history) != self.history_window or len(set(recent_history)) != 1:
            return None

        familiar_family = family_by_id[recent_history[0]]
        contrasting_families = [
            family
            for family in family_by_id.values()
            if family.structure != familiar_family.structure
        ]
        if not contrasting_families:
            raise ValueError(
                f"No contrasting opening family exists for {familiar_family.family_id}"
            )

        contrasting_families.sort(
            key=lambda family: (
                family.learner_colour == familiar_family.learner_colour,
                family.family_id,
            )
        )
        return contrasting_families[0].family_id


def classify_opening_family(
    moves: Sequence[str], families: Sequence[OpeningFamilyPayload]
) -> str | None:
    """Return the uniquely matching curated family for a UCI move history."""
    matching_family_ids = [
        family.family_id
        for family in families
        if len(moves) >= len(family.moves)
        and tuple(moves[: len(family.moves)]) == _uci_moves(family)
    ]
    if len(matching_family_ids) > 1:
        joined_ids = ", ".join(sorted(matching_family_ids))
        raise ValueError(f"Ambiguous opening-family match: {joined_ids}")
    return matching_family_ids[0] if matching_family_ids else None


def _family_by_id(families: Sequence[OpeningFamilyPayload]) -> dict[str, OpeningFamilyPayload]:
    family_by_id = {family.family_id: family for family in families}
    if not family_by_id:
        raise ValueError("At least one opening family is required")
    if len(family_by_id) != len(families):
        raise ValueError("Opening family IDs must be unique")
    return family_by_id


def _ordered_family_ids(
    family_by_id: dict[str, OpeningFamilyPayload],
    familiar_family_id: str | None,
    required_family_id: str | None,
) -> tuple[str, ...]:
    remaining_family_ids = sorted(
        family_id
        for family_id in family_by_id
        if family_id not in {required_family_id, familiar_family_id}
    )
    ordered_family_ids = [
        family_id
        for family_id in (required_family_id, familiar_family_id)
        if family_id is not None
    ]
    return tuple(ordered_family_ids + remaining_family_ids)


def _uci_moves(family: OpeningFamilyPayload) -> tuple[str, ...]:
    board = chess.Board()
    uci_moves: list[str] = []
    for san_move in family.moves:
        try:
            move = board.parse_san(san_move)
        except ValueError as error:
            raise ValueError(
                f"Opening family {family.family_id} contains invalid move {san_move!r}"
            ) from error
        uci_moves.append(move.uci())
        board.push(move)
    return tuple(uci_moves)
