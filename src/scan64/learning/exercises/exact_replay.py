from chess_lesson_spec import (
    AcceptedMove,
    Diagnosis,
    Hint,
    Interaction,
    LessonSpec,
    Objective,
    Source,
    Verification,
)

from scan64.providers.engine.config import active_engine_profile


async def generate_exact_replay_exercise(
    diagnosis: Diagnosis,
    fen: str,
    lesson_id: str,
    accepted_moves_san: list[str],
    hints: list[Hint] | None = None,
    *,
    engine: str | None = None,
) -> LessonSpec:
    """
    Generate an exact-replay exercise from a diagnosed position.

    ``engine`` names the engine profile whose search the lesson is verified
    against; without it, the active engine profile.
    """
    if hints is None:
        hints = []

    source = Source(kind="player_game", fen=fen)
    objective = Objective(
        type="exact_replay",
        instruction=(
            "Find a sound move that does not allow mate."
            if diagnosis.primary == "threat_processing.allowed_mate"
            else "Find a sound move."
        ),
    )
    accepted_moves = [
        AcceptedMove(san=san, reason="Within the engine soundness tolerance")
        for san in accepted_moves_san
    ]
    interaction = Interaction(input="move", maximum_attempts=3, accepted_moves=accepted_moves)

    # Unverified until verify_lesson checks it against the engine's search.
    verification = Verification(
        status="unverified",
        engine=engine if engine is not None else active_engine_profile().name,
    )

    return LessonSpec(
        schema_version="0.2.0",
        lesson_id=lesson_id,
        source=source,
        diagnosis=diagnosis,
        objective=objective,
        interaction=interaction,
        hints=hints,
        verification=verification,
    )
