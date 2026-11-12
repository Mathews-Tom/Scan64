import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import chess
from sqlalchemy import update
from sqlmodel import Session, col

from scan64.chess.analysis.coach_interruption import CoachInterruption, prepare_coach_interruption
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.boards import uci_moves_to_san
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.participants import participants, player_color
from scan64.chess.games.pgn import CorruptGameError, build_pgn
from scan64.chess.opponents.protocols import OpponentContext, OpponentPolicy
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.providers.maia import MaiaConfig, MaiaConfigurationError, MaiaOpponentProvider
from scan64.providers.stockfish.pool import EnginePoolManager

logger = logging.getLogger(__name__)


class PlaySessionNotFound(ValueError):
    """No play session exists for the requested id."""


class PlaySessionNotActive(ValueError):
    """The play session has already reached a terminal state."""


class TakebackNotAllowed(ValueError):
    """The session has no undoable human move pair."""


@dataclass(frozen=True)
class PlayMoveResult:
    opponent_move: str | None
    interruption: CoachInterruption | None


class PlaySessionService:
    def __init__(
        self,
        db_session: Session,
        stockfish_provider: StockfishOpponentProvider,
        maia_config: MaiaConfig | None = None,
        maia_config_path: Path | None = None,
        engine_pool_manager: EnginePoolManager | None = None,
    ) -> None:
        self.db = db_session
        self.stockfish_provider = stockfish_provider
        self.maia_config = maia_config
        self.maia_config_path = maia_config_path
        self.engine_pool_manager = engine_pool_manager
        self.pending_analysis: list[tuple[str, UUID]] = []

    def opponent_provider_for(self, opponent_config: dict[str, str]) -> OpponentPolicy:
        provider_name = opponent_config.get("provider", "stockfish")
        if provider_name == "stockfish":
            return self.stockfish_provider
        if provider_name == "maia":
            return MaiaOpponentProvider(self.configured_maia())
        raise ValueError(f"Unsupported opponent provider: {provider_name}")

    def configured_maia(self) -> MaiaConfig:
        if self.maia_config is not None:
            return self.maia_config
        if self.maia_config_path is None:
            raise RuntimeError(
                "Maia is not configured. Set SCAN64_MAIA_CONFIG to an "
                "operator-provided config file."
            )
        try:
            self.maia_config = MaiaConfig.from_toml(self.maia_config_path)
        except MaiaConfigurationError as error:
            raise RuntimeError(
                "Maia configuration is invalid. Review SCAN64_MAIA_CONFIG."
            ) from error
        return self.maia_config

    def persist_maia_selection(self, play_session: PlaySession, strength_setting: int) -> None:
        if play_session.opponent_config.get("provider") != "maia":
            return
        if self.maia_config is None:
            return
        selection = self.maia_config.select(strength_setting)
        opponent_config = dict(play_session.opponent_config)
        opponent_config["maia_checkpoint"] = str(selection.checkpoint.rating)
        if selection.disclosure is None:
            opponent_config.pop("maia_coverage_disclosure", None)
        else:
            opponent_config["maia_coverage_disclosure"] = selection.disclosure
        play_session.opponent_config = opponent_config

    def human_color(self, play_session: PlaySession, game: Game) -> chess.Color:
        return player_color(
            game.headers.get("FEN"),
            game.moves,
            play_session.opponent_config.get("player_color") or game.owner_color,
        )

    async def play_opponent_move(
        self, play_session: PlaySession, game: Game, board: chess.Board
    ) -> str:
        """Apply one opponent move, shared by opening moves and paired replies."""
        config = play_session.opponent_config
        default_strength = 10 if config.get("provider") == "maia" else 0
        strength = int(config.get("strength", default_strength))
        clock = play_session.clock_config
        time_limit = clock.get("time_remaining_ms") if clock else None
        context = OpponentContext(
            strength_setting=strength,
            time_remaining_ms=int(time_limit) if time_limit else None,
            bot_level=int(config["bot_level"]) if "bot_level" in config else None,
            seed=int(config.get("seed", "0")),
            ply=len(game.moves),
        )
        position = Position(
            fen=board.fen(),
            side_to_move="w" if board.turn == chess.WHITE else "b",
            canonical_id=board.fen().split(" ")[0],
        )
        decision = await self.opponent_provider_for(config).choose_move(position, context)
        self.persist_maia_selection(play_session, strength)
        move = chess.Move.from_uci(decision.uci_move)
        if move not in board.legal_moves:
            raise RuntimeError(f"Opponent produced illegal move: {decision.uci_move}")
        board.push(move)
        game.moves = [*game.moves, decision.uci_move]
        return decision.uci_move

    def takeback(self, session_id: UUID) -> PlaySession:
        play_session = self.db.get(PlaySession, session_id)
        if play_session is None:
            raise PlaySessionNotFound("PlaySession not found")
        if play_session.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")
        if play_session.coach_mode:
            raise TakebackNotAllowed("Take-back is unavailable in coach mode")
        game = self.db.get(Game, play_session.game_id) if play_session.game_id else None
        initial_ply = int(play_session.opponent_config.get("initial_ply", "0"))
        if game is None or len(game.moves) - initial_ply < 2:
            raise TakebackNotAllowed("No human move pair to take back")
        # Keep the opening bot move (and any imported history) intact.
        game.moves = game.moves[:-2]
        game.pgn = build_pgn(game)
        self.db.add(game)
        self.db.commit()
        return play_session

    def complete_session(
        self, play_session: PlaySession, game: Game | None, result: str | None
    ) -> None:
        """The single terminal-state transition for a play session.

        The status move is a conditional write, so two concurrent terminal
        requests cannot both complete one session and enqueue duplicate work.
        """
        if (game is None) != (result is None):
            raise ValueError("game and result must be given together")

        completed = self.db.exec(
            update(PlaySession)
            .where(col(PlaySession.id) == play_session.id)
            .where(col(PlaySession.status) == "active")
            .values(status="completed")
        )
        if completed.rowcount == 0:
            self.db.rollback()
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")
        play_session.status = "completed"

        if game is not None and result is not None:
            game.result = result
            game.date = game.date or game.created_at.strftime("%Y.%m.%d")
            game.pgn = build_pgn(game)
            self.db.add(game)

            if game.owner_player_id is not None and game.moves:
                job = AnalysisJob(game_id=game.id)
                self.db.add(job)
                self.db.commit()
                self.db.refresh(job)
                self.pending_analysis.append((game.owner_player_id, job.id))
                return

        self.db.commit()

    def resign(self, session_id: UUID) -> PlaySession:
        """Concede the game, ending the session as a loss for the player."""
        play_session = self.db.get(PlaySession, session_id)
        if not play_session:
            raise PlaySessionNotFound("PlaySession not found")
        if play_session.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")

        if play_session.game_id is None:
            self.complete_session(play_session, None, None)
            return play_session

        game = self.db.get(Game, play_session.game_id)
        if game is None:
            raise CorruptGameError(f"Game {play_session.game_id} is missing")

        conceded = self.human_color(play_session, game)
        self.complete_session(play_session, game, "0-1" if conceded == chess.WHITE else "1-0")
        return play_session

    async def make_move(self, session_id: UUID, player_move: str) -> PlayMoveResult:
        """Process a player move and return its durable play outcome."""
        play_session = self.db.get(PlaySession, session_id)
        if not play_session:
            raise PlaySessionNotFound("PlaySession not found")

        if play_session.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")

        if not play_session.game_id:
            white, black = participants(play_session.player_id, play_session.opponent_config)
            game = Game(
                pgn="",
                moves=[],
                white=white,
                black=black,
                owner_player_id=play_session.player_id,
                owner_color=(
                    "black"
                    if play_session.opponent_config.get("player_color") == "black"
                    else "white"
                ),
            )
            game.pgn = build_pgn(game)
            self.db.add(game)
            self.db.commit()
            self.db.refresh(game)
            play_session.game_id = game.id
            self.db.add(play_session)
            self.db.commit()

        assert play_session.game_id is not None
        fetched = self.db.get(Game, play_session.game_id)
        if not fetched:
            raise ValueError("Game not found")

        initial_fen = fetched.headers.get("FEN") if fetched.headers else None
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        for recorded_move in fetched.moves:
            board.push_uci(recorded_move)
        if board.turn != self.human_color(play_session, fetched):
            raise ValueError("It is not the player's turn")
        before_board = board.copy()
        moves_before = list(fetched.moves)

        move = chess.Move.from_uci(player_move)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal move: {player_move}")

        board.push(move)
        player_position = board.copy()
        fetched.moves = [*moves_before, player_move]

        prepared_interruption = None
        if play_session.coach_mode and not player_position.is_game_over():
            try:
                prepared_interruption = await prepare_coach_interruption(
                    session=self.db,
                    game=fetched,
                    player_id=play_session.player_id,
                    before_board=before_board,
                    after_board=player_position,
                    played_move=before_board.san(move),
                    history_san=uci_moves_to_san([*moves_before, player_move], initial_fen),
                    pool_manager=self.engine_pool_manager,
                )
            except Exception:
                logger.exception(
                    "Coach diagnostic failed for play_session_id=%s move=%s; "
                    "committing the legal player move without an interruption",
                    play_session.id,
                    player_move,
                )

        if board.is_game_over():
            if prepared_interruption is not None:
                prepared_interruption.add_to(self.db)
            self.complete_session(play_session, fetched, board.result())
            return PlayMoveResult(
                opponent_move=None,
                interruption=(
                    prepared_interruption.interruption
                    if prepared_interruption is not None
                    else None
                ),
            )

        opponent_move = await self.play_opponent_move(play_session, fetched, board)

        if board.is_game_over():
            if prepared_interruption is not None:
                prepared_interruption.add_to(self.db)
            self.complete_session(play_session, fetched, board.result())
            return PlayMoveResult(
                opponent_move=opponent_move,
                interruption=(
                    prepared_interruption.interruption
                    if prepared_interruption is not None
                    else None
                ),
            )

        fetched.pgn = build_pgn(fetched)
        self.db.add(fetched)
        self.db.add(play_session)
        if prepared_interruption is not None:
            prepared_interruption.add_to(self.db)
        self.db.commit()

        return PlayMoveResult(
            opponent_move=opponent_move,
            interruption=(
                prepared_interruption.interruption if prepared_interruption is not None else None
            ),
        )
