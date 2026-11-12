"""Stage immutable per-occurrence fast-pass provenance without holding a write lock."""

from uuid import UUID

from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.boards import board_from
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.tolerance import review_probability

REVIEW_NODES = 10_000


def stage_position_analysis(
    game: Game,
    fen: str,
    analysis: EngineAnalysis,
    positions_by_fen: dict[str, Position],
    analyses: dict[UUID, EngineAnalysis],
) -> Position:
    position = positions_by_fen.get(fen)
    if position is None:
        position = ingest_fen(fen, game.id)
        positions_by_fen[fen] = position
    analysis.position_id = position.id
    analyses[analysis.id] = analysis
    return position


def stage_game_review(
    game: Game,
    evaluations: list[EngineAnalysis],
    positions_by_fen: dict[str, Position],
    analyses: dict[UUID, EngineAnalysis],
    existing_plies: set[int],
) -> list[GameMoveReview]:
    if len(evaluations) != len(game.moves) + 1:
        raise ValueError("Fast pass must evaluate every reached position")
    if all(ply in existing_plies for ply in range(1, len(game.moves) + 1)):
        return []
    board = board_from(game.headers.get("FEN"))
    positions = []
    probabilities = []
    terminals = []
    sans = []
    movers = []
    for index, analysis in enumerate(evaluations):
        positions.append(
            stage_position_analysis(game, board.fen(), analysis, positions_by_fen, analyses)
        )
        probability, terminal = review_probability(
            board, analysis.raw_result[0] if analysis.raw_result else {}
        )
        probabilities.append(probability)
        terminals.append(terminal)
        if index < len(game.moves):
            move = board.parse_uci(game.moves[index])
            sans.append(board.san(move))
            movers.append("white" if board.turn else "black")
            board.push(move)
    return [
        GameMoveReview(
            game_id=game.id,
            ply=index + 1,
            source_position_id=positions[index].id,
            reached_position_id=positions[index + 1].id,
            before_analysis_id=evaluations[index].id,
            after_analysis_id=evaluations[index + 1].id,
            move_uci=move,
            san=sans[index],
            mover=movers[index],
            before_probability=probabilities[index],
            after_probability=probabilities[index + 1],
            before_terminal=terminals[index],
            after_terminal=terminals[index + 1],
        )
        for index, move in enumerate(game.moves)
        if index + 1 not in existing_plies
    ]
