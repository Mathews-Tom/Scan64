from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import chess
from chess_lesson_spec import Diagnosis, Explanation

from scan64.learning.evidence.models import Evidence


class ExplanationTemplateError(ValueError):
    """Raised when a diagnosis cannot be rendered from a grounded template.

    A missing template for a taxonomy code, missing evidence, or a required
    payload field that M33's evidence composer failed to populate is a defect
    to surface loudly, never a reason to render a generic fallback sentence.
    """


def _piece_name(symbol: str) -> str:
    return chess.piece_name(chess.Piece.from_symbol(symbol).piece_type)


def _field(payload: Mapping[str, Any], field: str, code: str) -> Any:
    value = payload.get(field)
    if value is None:
        raise ExplanationTemplateError(
            f"Evidence for taxonomy code {code!r} is missing required field {field!r}"
        )
    return value


def _target_list_description(targets: Sequence[Mapping[str, Any]]) -> str:
    return ", ".join(f"{_piece_name(target['piece'])} on {target['square']}" for target in targets)


def _hanging_piece_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    square = _field(payload, "hanging_square", code)
    piece = _piece_name(_field(payload, "hanging_piece", code))
    return (
        f"After {played_move}, your {piece} on {square} is left hanging: the "
        "opponent's principal line captures it for free. Scan every piece you "
        "touch for an undefended attacked square before continuing your plan."
    )


def _missed_check_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    best_move = _field(payload, "best_move", code)
    return (
        f"You played {played_move}, but the engine's principal line begins "
        f"with the check {best_move}, which you missed."
    )


def _missed_capture_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    best_move = _field(payload, "best_move", code)
    captured_square = _field(payload, "captured_square", code)
    captured_piece = _piece_name(_field(payload, "captured_piece", code))
    return (
        f"You played {played_move}, but the engine's principal line begins "
        f"with {best_move}, capturing the {captured_piece} on {captured_square}."
    )


def _missed_direct_threat_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    threat_move = _field(payload, "threat_move", code)
    threatened_square = _field(payload, "threatened_square", code)
    threatened_piece = _piece_name(_field(payload, "threatened_piece", code))
    return (
        f"After {played_move}, the opponent's principal line immediately plays "
        f"{threat_move}, capturing your {threatened_piece} on {threatened_square}."
    )


def _knight_fork_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    best_move = _field(payload, "best_move", code)
    fork_square = _field(payload, "fork_square", code)
    targets = _field(payload, "targets", code)
    description = _target_list_description(targets)
    return (
        f"Playing {best_move} forks two pieces on {fork_square}: it wins "
        f"{description}, but you played {played_move} instead."
    )


def _pin_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    pinning_move = _field(payload, "pinning_move", code)
    pinned_square = _field(payload, "pinned_square", code)
    pinned_piece = _piece_name(_field(payload, "pinned_piece", code))
    return (
        f"Playing {pinning_move} pins the opponent's {pinned_piece} on "
        f"{pinned_square}, but you played {played_move} instead."
    )


def _overloaded_defender_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    defender_square = _field(payload, "defender_square", code)
    defender_piece = _piece_name(_field(payload, "defender_piece", code))
    defended_targets = _field(payload, "defended_targets", code)
    description = _target_list_description(defended_targets)
    return (
        f"Your {defender_piece} on {defender_square} is overloaded defending "
        f"{description}; the opponent's principal line exploits it after "
        f"{played_move}."
    )


def _stopped_calculation_early_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    sequence_plies = _field(payload, "sequence_plies", code)
    focused_line = _field(payload, "focused_line", code)
    line = " ".join(focused_line)
    return (
        f"After {played_move}, the critical line runs {sequence_plies} plies "
        f"deep ({line}), and you stopped calculating before its conclusion."
    )


def _delayed_development_text(payload: Mapping[str, Any], code: str) -> str:
    pawn_moves = _field(payload, "pawn_moves", code)
    minor_pieces_developed = _field(payload, "minor_pieces_developed", code)
    tempo_loss = _field(payload, "tempo_loss", code)
    return (
        f"Your opening moved pawns {pawn_moves} times while developing only "
        f"{minor_pieces_developed} minor piece(s), costing {tempo_loss:.1f} "
        "tempo relative to a normal developing plan."
    )


def _king_safety_neglect_text(payload: Mapping[str, Any], code: str) -> str:
    played_move = _field(payload, "played_move", code)
    incoming_threat = _field(payload, "incoming_threat", code)
    return (
        f"After {played_move}, the opponent's principal line begins with the "
        f"incoming threat {incoming_threat}, exposing your king."
    )


_TEMPLATES: dict[str, Callable[[Mapping[str, Any], str], str]] = {
    "board_awareness.hanging_piece": _hanging_piece_text,
    "threat_processing.missed_check": _missed_check_text,
    "threat_processing.missed_capture": _missed_capture_text,
    "threat_processing.missed_direct_threat": _missed_direct_threat_text,
    "tactics.fork.knight": _knight_fork_text,
    "tactics.pin": _pin_text,
    "tactics.overloaded_defender": _overloaded_defender_text,
    "calculation.stopped_too_early": _stopped_calculation_early_text,
    "opening.delayed_development": _delayed_development_text,
    "positional.king_safety_neglect": _king_safety_neglect_text,
}


def _matched_payload(diagnosis: Diagnosis, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
    by_id = {item.evidence_id: item for item in evidence}
    for evidence_id in diagnosis.evidence_refs:
        matched = by_id.get(evidence_id)
        if matched is not None:
            return matched.payload
    raise ExplanationTemplateError(
        f"No evidence for taxonomy code {diagnosis.primary!r} matches its "
        f"evidence_refs {tuple(diagnosis.evidence_refs)!r}"
    )


class TemplateExplanationProvider:
    """Deterministic, evidence-grounded explanation floor for every seeded code.

    Every seeded taxonomy code has a template that interpolates only fields
    M33's evidence composer actually populates. A code without a registered
    template, or evidence missing a field its template requires, raises
    ``ExplanationTemplateError`` rather than rendering a generic sentence.
    """

    async def explain(self, diagnosis: Diagnosis, evidence: Sequence[Evidence]) -> Explanation:
        render = _TEMPLATES.get(diagnosis.primary)
        if render is None:
            raise ExplanationTemplateError(
                f"No grounded template is registered for taxonomy code {diagnosis.primary!r}"
            )
        payload = _matched_payload(diagnosis, evidence)
        text = render(payload, diagnosis.primary)
        return Explanation(text=text, visualizations=[])
