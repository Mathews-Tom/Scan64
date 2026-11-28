"""The fixed position set: slow-loss games with a root-cause theme or a turning point (#403).

``select`` finds the loser's moves in ``factor_calibration/games.json`` (20 public Lichess games,
CC0) that lost half a pawn or more by the Lichess scores and that have a root-cause theme, plus
one position for each game whose lesson has a turning point and no theme. ``build`` adds the
engine's best line and the line after the played move. ``positions.json`` stores the result, so
every run uses the same moves and lines. Themes and the turning point are computed from the game
at run time, so a run measures the current theme code.
"""

import hashlib
import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import chess.engine

from scan64.explanations.role_facts import RoleFact, numbered_san, role_facts
from scan64.explanations.themes import GameThemeFacts, game_theme_facts, trace_from_scores
from scan64.explanations.voice_pipeline import StoryFact, cause_facts, select_facts

HERE = Path(__file__).resolve().parent
POSITIONS = HERE / "positions.json"
GAMES = HERE.parents[1] / "factor_calibration" / "games.json"
MIN_LOSS_CP = 50
LINE_PLIES = 6
DEPTH = 18
HASH_MB = 64


@dataclass(frozen=True)
class Position:
    """One lesson: the mistake, the engine's lines, and the game's root-cause facts."""

    id: str
    fen: str
    played: str
    best_line: tuple[str, ...]
    played_line: tuple[str, ...]
    game: GameThemeFacts

    @property
    def board(self) -> chess.Board:
        return chess.Board(self.fen)

    @property
    def mover(self) -> chess.Color:
        return self.board.turn

    @property
    def played_san(self) -> str:
        return numbered_san(self.board, chess.Move.from_uci(self.played))

    def line_facts(self) -> tuple[RoleFact, ...]:
        return role_facts(
            self.fen,
            self.played,
            self.best_line[0],
            best_line=self.best_line,
            played_line=self.played_line,
        )

    def story(self) -> tuple[StoryFact, ...]:
        """Every fact the story tells, in order: cause, turning point, then the line."""
        return (*cause_facts(self.game, self.mover), *select_facts(self.line_facts())[0])

    def key_moves(self) -> tuple[str, ...]:
        """The SAN of the played move, the engine's best move and the engine's reply."""
        board = self.board
        sans = [board.san(chess.Move.from_uci(self.played))]
        sans.append(board.san(chess.Move.from_uci(self.best_line[0])))
        if self.played_line:
            board.push(chess.Move.from_uci(self.played))
            sans.append(board.san(chess.Move.from_uci(self.played_line[0])))
        return tuple(san.rstrip("+#") for san in sans)


def _games() -> dict[str, Any]:
    return {g["id"]: g for g in json.loads(GAMES.read_text(encoding="utf-8"))["games"]}


def _facts(game: dict[str, Any], ply: int) -> GameThemeFacts | None:
    # Lichess gives one score for each ply, after the move: the score of position i + 1.
    trace = trace_from_scores(None, game["moves"], [None, *game["lichess_white_cp"]])
    return game_theme_facts(trace, trace.boards[ply], trace.moves[ply])


def has_turn(facts: GameThemeFacts) -> bool:
    point = facts.turning_point
    return point is not None and point.ply <= facts.mistake_ply


def select() -> Iterator[tuple[str, int, chess.Board, chess.Move]]:
    """Each candidate (game id, ply of the position before the mistake, board, played move)."""
    for game_id, game in sorted(_games().items()):
        learner = chess.WHITE if game["winner"] == "black" else chess.BLACK
        sign = 1 if learner == chess.WHITE else -1
        trace = trace_from_scores(None, game["moves"], [None, *game["lichess_white_cp"]])
        turn_only: list[int] = []
        for ply in range(1, len(trace.moves)):
            before, after = trace.scores[ply], trace.scores[ply + 1]
            if trace.boards[ply].turn != learner or before is None or after is None:
                continue
            if sign * (before - after) < MIN_LOSS_CP:
                continue
            facts = game_theme_facts(trace, trace.boards[ply], trace.moves[ply])
            if facts is None:
                continue
            if facts.themes:
                yield game_id, ply, trace.boards[ply], trace.moves[ply]
            elif has_turn(facts):
                turn_only.append(ply)
        if turn_only:
            yield game_id, turn_only[0], trace.boards[turn_only[0]], trace.moves[turn_only[0]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(engine_path: str) -> dict[str, Any]:
    """The stored set. A move the engine itself chooses, or that loses little, is left out."""
    resolved = shutil.which(engine_path)
    if resolved is None:
        raise SystemExit(f"no engine at {engine_path}")
    limit = chess.engine.Limit(depth=DEPTH)
    rows, left_out = [], []
    with chess.engine.SimpleEngine.popen_uci(resolved) as engine:
        engine.configure({"Threads": 1, "Hash": HASH_MB})
        identity = {
            "name": engine.id["name"],
            "binary_sha256": _sha256(Path(resolved)),
            # Stockfish's networks are embedded in the binary; their names hold their digests.
            "networks": [
                engine.options[o].default
                for o in ("EvalFile", "EvalFileSmall")
                if o in engine.options
            ],
        }
        for game_id, ply, board, played in select():
            name = f"{game_id}-{ply}"
            best = engine.analyse(board, limit, game=object())
            after = board.copy(stack=False)
            after.push(played)
            reply = engine.analyse(after, limit, game=object())
            loss = best["score"].pov(board.turn).score(mate_score=10_000) - reply["score"].pov(
                board.turn
            ).score(mate_score=10_000)
            best_line = [move.uci() for move in best["pv"][:LINE_PLIES]]
            if best_line[0] == played.uci() or loss < MIN_LOSS_CP:
                left_out.append(name)
                continue
            rows.append(
                {
                    "id": name,
                    "game": game_id,
                    "ply": ply,
                    "fen": board.fen(),
                    "played": played.uci(),
                    "best_line": best_line,
                    "played_line": [move.uci() for move in reply.get("pv", [])[:LINE_PLIES]],
                }
            )
    return {
        "games_sha256": _sha256(GAMES),
        "engine": identity | {"depth": DEPTH, "threads": 1, "hash_mb": HASH_MB},
        "min_loss_cp": MIN_LOSS_CP,
        "left_out": left_out,
        "positions": rows,
    }


def load(path: Path = POSITIONS) -> list[Position]:
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored["games_sha256"] != _sha256(GAMES):
        raise SystemExit(f"{GAMES.name} is not the file the position set was built from")
    games = _games()
    out = []
    for row in stored["positions"]:
        facts = _facts(games[row["game"]], row["ply"])
        if facts is None:
            raise SystemExit(f"{row['id']}: the game holds no such position")
        out.append(
            Position(
                row["id"],
                row["fen"],
                row["played"],
                tuple(row["best_line"]),
                tuple(row["played_line"]),
                facts,
            )
        )
    return out
