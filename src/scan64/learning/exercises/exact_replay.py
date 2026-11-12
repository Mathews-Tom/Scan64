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


async def generate_exact_replay_exercise(
    diagnosis: Diagnosis,
    fen: str,
    lesson_id: str,
    accepted_moves_san: list[str],
    hints: list[Hint] | None = None,
) -> LessonSpec:
    """
    Generate an exact-replay exercise from a diagnosed position.
    """
    if hints is None:
        hints = []

    source = Source(kind="player_game", fen=fen)
    objective = Objective(type="exact_replay", instruction="Find a sound move.")
    accepted_moves = [
        AcceptedMove(san=san, reason="Within the engine soundness tolerance")
        for san in accepted_moves_san
    ]
    interaction = Interaction(input="move", maximum_attempts=3, accepted_moves=accepted_moves)

    # Placeholder verification since it runs before verifier
    verification = Verification(status="unverified", engine="stockfish")

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
