from scan64.lessonspec.models import (
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
    best_move_san: str,
    hints: list[Hint] | None = None,
) -> LessonSpec:
    """
    Generate an exact-replay exercise from a diagnosed position.
    """
    if hints is None:
        hints = []

    source = Source(kind="player_game", fen=fen)
    objective = Objective(type="exact_replay", instruction="Find the best move.")
    accepted_moves = [AcceptedMove(san=best_move_san, reason="Best engine evaluation")]
    interaction = Interaction(input="move", maximum_attempts=3, accepted_moves=accepted_moves)

    # Placeholder verification since it runs before verifier
    verification = Verification(status="unverified", engine="stockfish")

    return LessonSpec(
        schema_version="0.1.0",
        lesson_id=lesson_id,
        source=source,
        diagnosis=diagnosis,
        objective=objective,
        interaction=interaction,
        hints=hints,
        verification=verification,
    )
