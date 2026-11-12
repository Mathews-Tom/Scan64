import importlib
from unittest.mock import AsyncMock

import chess
import pytest

from scan64.chess.opponents.bot_policy import DEFAULT_BOT_NODES
from scan64.chess.opponents.protocols import MoveDecision, OpponentContext
from scan64.chess.positions.models import Position
from scan64.cli.bots import (
    CALIBRATION_OPENINGS,
    CALIBRATION_SEEDS,
    CalibrationGame,
    play_calibration_game,
)


def test_openings_and_seeds_define_twenty_colour_balanced_games() -> None:
    assert len(CALIBRATION_OPENINGS) == len(CALIBRATION_SEEDS) == 10
    assert len(set(CALIBRATION_SEEDS)) == 10
    for opening in CALIBRATION_OPENINGS:
        board = chess.Board()
        for uci in opening:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            board.push(move)
        assert not board.is_game_over()


class FoolsMateProvider:
    def __init__(self) -> None:
        self.contexts: list[OpponentContext] = []
        self.moves = iter(("f2f3", "e7e5", "g2g4", "d8h4"))

    async def choose_move(self, position: Position, context: OpponentContext) -> MoveDecision:
        self.contexts.append(context)
        move = next(self.moves)
        assert chess.Move.from_uci(move) in chess.Board(position.fen).legal_moves
        return MoveDecision(uci_move=move)


@pytest.mark.asyncio
async def test_full_game_uses_real_terminal_outcome_and_context() -> None:
    provider = FoolsMateProvider()
    game = await play_calibration_game(provider, 1, 2, 47, (), 6789)
    assert game.result == "0-1"
    assert game.moves == ("f2f3", "e7e5", "g2g4", "d8h4")
    assert game.higher_score() == 1
    assert [context.bot_level for context in provider.contexts] == [1, 2, 1, 2]
    assert [context.ply for context in provider.contexts] == [0, 1, 2, 3]
    assert all(context.seed == 47 and context.nodes == 6789 for context in provider.contexts)


@pytest.mark.asyncio
async def test_opening_is_preserved_and_ply_includes_it() -> None:
    provider = FoolsMateProvider()
    next(provider.moves)
    next(provider.moves)
    game = await play_calibration_game(provider, 2, 1, 71, ("f2f3", "e7e5"), 123)
    assert game.opening == ("f2f3", "e7e5")
    assert game.moves == ("g2g4", "d8h4")
    assert game.result == "0-1"
    assert game.higher_score() == 0
    assert [context.ply for context in provider.contexts] == [2, 3]


@pytest.mark.parametrize(
    "white, black, result, expected",
    [
        (1, 2, "1-0", 0),
        (1, 2, "0-1", 1),
        (2, 1, "1-0", 1),
        (2, 1, "0-1", 0),
        (1, 2, "1/2-1/2", 0.5),
    ],
)
def test_score_is_always_from_higher_level_perspective(
    white: int, black: int, result: str, expected: float
) -> None:
    game = CalibrationGame(white, black, 11, (), (), result)
    assert game.higher_score() == expected


def test_cli_routes_calibration_with_production_default_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = importlib.import_module("scan64.cli.main")
    calibrate = AsyncMock(return_value=0)
    monkeypatch.setattr(cli, "calibrate_command", calibrate)
    monkeypatch.setattr("sys.argv", ["scan64-cli", "bots", "calibrate"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 0
    calibrate.assert_awaited_once_with("stockfish", nodes=DEFAULT_BOT_NODES)


def test_cli_rejects_nonpositive_node_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = importlib.import_module("scan64.cli.main")
    calibrate = AsyncMock()
    monkeypatch.setattr(cli, "calibrate_command", calibrate)
    monkeypatch.setattr("sys.argv", ["scan64-cli", "bots", "calibrate", "--nodes", "0"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 2
    calibrate.assert_not_awaited()


class RepeatingKnightsProvider:
    async def choose_move(self, position: Position, context: OpponentContext) -> MoveDecision:
        moves = ("g1f3", "g8f6", "f3g1", "f6g8")
        return MoveDecision(uci_move=moves[context.ply % 4])


@pytest.mark.asyncio
async def test_claimable_repetition_is_a_real_draw() -> None:
    game = await play_calibration_game(RepeatingKnightsProvider(), 1, 2, 11, (), 123)
    board = chess.Board()
    for move in game.moves:
        board.push_uci(move)
    assert board.can_claim_threefold_repetition()
    assert game.result == "1/2-1/2"
    assert game.higher_score() == 0.5


@pytest.mark.asyncio
async def test_move_cap_adjudicates_draw_without_fabricating_a_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scan64.cli.bots.MAX_GAME_PLIES", 2)
    game = await play_calibration_game(FoolsMateProvider(), 1, 2, 11, (), 123)
    assert game.moves == ("f2f3", "e7e5")
    assert game.result == "1/2-1/2"
