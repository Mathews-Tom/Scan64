from __future__ import annotations

import asyncio
import logging
from asyncio import sleep
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from uuid import UUID

import chess
from pydantic import ConfigDict
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, col, select

from scan64.chess.analysis.coach_interruption import (
    CoachInterruption,
    PreparedCoachInterruption,
    prepare_coach_interruption,
)
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.boards import uci_moves_to_san
from scan64.chess.games.assistance import PlaySnapshot, StalePlaySnapshot, claim_snapshot
from scan64.chess.games.clock_models import PlayClock, PlayClockRead
from scan64.chess.games.clocks import (
    ClockConflict,
    PlayClockRuntime,
    as_utc,
    claim_clock,
    is_expired,
    project_clock,
    thinking_target_ms,
    utc_now,
)
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.participants import participants, player_color
from scan64.chess.games.pgn import CorruptGameError, build_pgn
from scan64.chess.opponents.protocols import OpponentContext, OpponentPolicy
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.learning.sparring import complete_sparring
from scan64.providers.maia import MaiaConfig, MaiaConfigurationError, MaiaOpponentProvider
from scan64.providers.stockfish.pool import EnginePoolManager

logger = logging.getLogger(__name__)


class PlaySessionNotFound(ValueError):
    """No play session exists for the requested id."""


class PlaySessionNotActive(ValueError):
    """The play session has already reached a terminal state."""


class TakebackNotAllowed(ValueError):
    """The session has no undoable human move pair."""


class _CommittedClockRead(PlayClockRead):
    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True)
class PlayMoveResult:
    opponent_move: str | None
    interruption: CoachInterruption | None
    status: str
    clock: PlayClockRead | None = None


class PlaySessionService:
    def __init__(
        self,
        db_session: Session,
        stockfish_provider: StockfishOpponentProvider,
        maia_config: MaiaConfig | None = None,
        maia_config_path: Path | None = None,
        engine_pool_manager: EnginePoolManager | None = None,
        clock_runtime: PlayClockRuntime | None = None,
    ) -> None:
        self.db = db_session
        self.stockfish_provider = stockfish_provider
        self.maia_config = maia_config
        self.maia_config_path = maia_config_path
        self.engine_pool_manager = engine_pool_manager
        self.pending_analysis: list[tuple[str, UUID]] = []
        self.clock_runtime = clock_runtime
        self._opponent_inflight: set[UUID] = set()

    def notify_clock(self) -> None:
        if self.clock_runtime is not None:
            self.clock_runtime.notify()

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
        self,
        play_session: PlaySession,
        game: Game,
        board: chess.Board,
        *,
        time_remaining_ms: int | None = None,
    ) -> str:
        """Apply one opponent move, shared by opening moves and paired replies."""
        config = play_session.opponent_config
        default_strength = 10 if config.get("provider") == "maia" else 0
        strength = int(config.get("strength", default_strength))
        # Legacy static clock_config values never constitute a running clock.
        context = OpponentContext(
            strength_setting=strength,
            time_remaining_ms=time_remaining_ms,
            bot_level=(
                int(config["bot_level"])
                if config.get("provider", "stockfish") == "stockfish" and "bot_level" in config
                else None
            ),
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

    def persist_moves(
        self,
        play_session: PlaySession,
        game: Game,
        moves_before: list[str],
        snapshot: PlaySnapshot,
    ) -> None:
        """Publish a move edit only while its active-session snapshot still matches."""
        config = dict(play_session.opponent_config)
        try:
            claimed_config = claim_snapshot(self.db, snapshot)
        except StalePlaySnapshot as error:
            raise PlaySessionNotActive(str(error)) from error
        play_session.opponent_config = {
            **config,
            "assistance_generation": claimed_config["assistance_generation"],
        }
        pgn = build_pgn(game)
        if snapshot.game_id is None:
            game.pgn = pgn
            self.db.add(game)
            self.db.flush()
            play_session.game_id = game.id
            self.db.add(play_session)
            return
        with self.db.no_autoflush:
            changed = self.db.exec(
                update(Game)
                .where(col(Game.id) == game.id)
                .where(col(Game.moves) == moves_before)
                .where(
                    col(Game.id).in_(
                        select(PlaySession.game_id)
                        .where(col(PlaySession.id) == play_session.id)
                        .where(col(PlaySession.status) == "active")
                    )
                )
                .values(moves=game.moves, pgn=pgn)
                .execution_options(synchronize_session=False)
            )
        if changed.rowcount == 0:
            self.db.rollback()
            raise PlaySessionNotActive("PlaySession changed while editing moves")
        set_committed_value(game, "moves", game.moves)
        set_committed_value(game, "pgn", pgn)

    def takeback(self, session_id: UUID) -> PlaySession:
        play_session = self.db.get(PlaySession, session_id)
        if play_session is None:
            raise PlaySessionNotFound("PlaySession not found")
        if self.db.get(PlayClock, session_id) is not None:
            raise TakebackNotAllowed("Take-back is unavailable in timed play")
        if play_session.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")
        if play_session.coach_mode:
            raise TakebackNotAllowed("Take-back is unavailable in coach mode")
        game = self.db.get(Game, play_session.game_id) if play_session.game_id else None
        initial_ply = int(play_session.opponent_config.get("initial_ply", "0"))
        if game is None or len(game.moves) <= initial_ply:
            raise TakebackNotAllowed("No human move pair to take back")
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        for move in game.moves:
            board.push_uci(move)
        # A reply may still be pending: undo that human move, not the prior bot move.
        undo_plies = 1 if board.turn != self.human_color(play_session, game) else 2
        if len(game.moves) - initial_ply < undo_plies:
            raise TakebackNotAllowed("No human move pair to take back")
        # Keep the opening bot move (and any imported history) intact.
        moves_before = list(game.moves)
        snapshot = PlaySnapshot.capture(play_session, game)
        game.moves = moves_before[:-undo_plies]
        self.persist_moves(play_session, game, moves_before, snapshot)
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
            .where(
                col(PlaySession.game_id).is_(None)
                if game is None
                else col(PlaySession.game_id) == game.id
            )
            .values(status="completed")
        )
        if completed.rowcount == 0:
            self.db.rollback()
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")
        play_session.status = "completed"

        if game is not None and result is not None:
            # The status write serialises against take-back; re-read the moves
            # so a take-back committed after `game` was loaded reaches the PGN.
            self.db.refresh(game, attribute_names=["moves"])
            game.result = result
            game.date = game.date or game.created_at.strftime("%Y.%m.%d")
            game.pgn = build_pgn(game)
            self.db.add(game)
            complete_sparring(self.db, play_session, game, result)

            if game.owner_player_id is not None and game.moves:
                job = AnalysisJob(game_id=game.id)
                self.db.add(job)
                self.db.commit()
                self.db.refresh(job)
                if (
                    self.clock_runtime is not None
                    and self.db.get(PlayClock, play_session.id) is not None
                ):
                    self.clock_runtime.admit_analysis(game.owner_player_id, job.id)
                else:
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

        clock = self.db.get(PlayClock, session_id)
        if clock is not None:
            now = utc_now()
            if is_expired(clock, now):
                self.expire_clock(session_id)
                return play_session
            snapshot = PlaySnapshot.capture(play_session, game)
            try:
                claim_snapshot(self.db, snapshot)
                claim_clock(self.db, clock, utc_now(), terminal_reason="resignation")
            except StalePlaySnapshot as error:
                raise ClockConflict(str(error)) from error
        conceded = self.human_color(play_session, game)
        self.complete_session(play_session, game, "0-1" if conceded == chess.WHITE else "1-0")
        self.notify_clock()
        return play_session

    async def make_move(
        self,
        session_id: UUID,
        player_move: str,
        expected_clock_version: int | None = None,
        *,
        expected_generation: int | None = None,
    ) -> PlayMoveResult:
        """Commit the legal human move before awaiting its opponent reply."""
        play_session = self.db.get(PlaySession, session_id)
        if play_session is None:
            raise PlaySessionNotFound("PlaySession not found")
        if play_session.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")

        clock = self.db.get(PlayClock, session_id)
        if clock is not None:
            return await self.make_timed_move(
                play_session,
                clock,
                player_move,
                expected_clock_version,
                expected_generation=expected_generation,
            )

        fetched = self.db.get(Game, play_session.game_id) if play_session.game_id else None
        if play_session.game_id is not None and fetched is None:
            raise ValueError("Game not found")
        snapshot = PlaySnapshot.capture(play_session, fetched)
        if expected_generation is not None and expected_generation != int(
            snapshot.config.get("assistance_generation", "0")
        ):
            raise StalePlaySnapshot("PlaySession changed; grade the current position again")
        if fetched is None:
            white, black = participants(play_session.player_id, play_session.opponent_config)
            fetched = Game(
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
        fetched.moves = [*moves_before, player_move]
        self.persist_moves(play_session, fetched, moves_before, snapshot)
        if board.is_game_over():
            self.complete_session(play_session, fetched, board.result())
            return PlayMoveResult(opponent_move=None, interruption=None, status="completed")

        # Freeze exactly the committed human prefix before commit expires ORM state.
        # Engine work must never reload a newer takeback/resignation into this reply.
        reply_snapshot = PlaySnapshot.capture(play_session, fetched)
        reply_play = PlaySession(**play_session.model_dump())
        reply_game = Game(**fetched.model_dump())
        self.db.commit()

        prepared_interruption = None
        if reply_play.coach_mode:
            try:
                with self.db.no_autoflush:
                    prepared_interruption = await prepare_coach_interruption(
                        session=self.db,
                        game=reply_game,
                        player_id=reply_play.player_id,
                        before_board=before_board,
                        after_board=board.copy(),
                        played_move=before_board.san(move),
                        history_san=uci_moves_to_san(reply_game.moves, initial_fen),
                        pool_manager=self.engine_pool_manager,
                    )
            except Exception:
                logger.exception(
                    "Coach diagnostic failed for play_session_id=%s move=%s; "
                    "retaining the legal player move without an interruption",
                    reply_play.id,
                    player_move,
                )
        # Coach preparation only reads/stages detached rows. Release its read
        # transaction before engine work so terminal edits can commit meanwhile.
        self.db.rollback()
        return await self._finish_bot_turn(
            reply_play, reply_game, board, reply_snapshot, prepared_interruption
        )

    async def resume_bot_turn(
        self, session_id: UUID, expected_clock_version: int | None = None
    ) -> PlayMoveResult:
        """Reply to the canonical durable human prefix without replaying/grading it."""
        play_session = self.db.get(PlaySession, session_id)
        if play_session is None:
            raise PlaySessionNotFound("PlaySession not found")
        if play_session.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play_session.status}")
        if self.db.get(PlayClock, session_id) is not None:
            with self._reserve_opponent(session_id):
                return await self._resume_opponent(session_id, expected_clock_version)
        game = self.db.get(Game, play_session.game_id) if play_session.game_id else None
        if game is None:
            raise ValueError("No opponent turn to resume")
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        for move in game.moves:
            board.push_uci(move)
        if not board.is_valid() or board.is_game_over():
            raise ValueError("No active legal opponent turn to resume")
        if board.turn == self.human_color(play_session, game):
            raise ValueError("It is not the opponent's turn")
        snapshot = PlaySnapshot.capture(play_session, game)
        reply_play = PlaySession(**play_session.model_dump())
        reply_game = Game(**game.model_dump())
        self.db.rollback()
        return await self._finish_bot_turn(reply_play, reply_game, board, snapshot)

    async def _finish_bot_turn(
        self,
        reply_play: PlaySession,
        reply_game: Game,
        board: chess.Board,
        snapshot: PlaySnapshot,
        prepared_interruption: PreparedCoachInterruption | None = None,
    ) -> PlayMoveResult:
        opponent_move = await self.play_opponent_move(reply_play, reply_game, board)
        # Only detached copies changed across the await. The complete source CAS
        # fences publication against other replies, takebacks and terminal edits.
        with self.db.no_autoflush:
            play_session = self.db.get(PlaySession, snapshot.session_id)
            game = self.db.get(Game, snapshot.game_id)
            if play_session is None or game is None:
                raise PlaySessionNotActive("PlaySession changed while awaiting the opponent")
            play_session.opponent_config = dict(reply_play.opponent_config)
            game.moves = list(reply_game.moves)
            self.persist_moves(play_session, game, snapshot.moves, snapshot)
        interruption = (
            prepared_interruption.add_to(self.db) if prepared_interruption is not None else None
        )
        if board.is_game_over():
            self.complete_session(play_session, game, board.result())
            status = "completed"
        else:
            self.db.commit()
            status = "active"
        return PlayMoveResult(opponent_move=opponent_move, interruption=interruption, status=status)

    def expire_clock(self, session_id: UUID) -> PlaySession:
        """Adjudicate a persisted deadline through the one terminal transition."""
        self.db.expire_all()
        play = self.db.get(PlaySession, session_id)
        clock = self.db.get(PlayClock, session_id)
        if play is None:
            raise PlaySessionNotFound("PlaySession not found")
        if play.status != "active":
            raise PlaySessionNotActive(f"PlaySession is {play.status}")
        now = utc_now()
        if clock is None or not is_expired(clock, now):
            raise ClockConflict("Clock has not expired")
        game = self.db.get(Game, play.game_id)
        if game is None:
            raise CorruptGameError("Timed play session has no Game")
        board = self.board_for(game)
        winner = clock.active_color != "white"
        result = (
            "1/2-1/2"
            if board.has_insufficient_material(winner)
            else "1-0"
            if winner == chess.WHITE
            else "0-1"
        )
        try:
            claim_snapshot(self.db, PlaySnapshot.capture(play, game))
            claim_clock(self.db, clock, now, terminal_reason="flag")
        except StalePlaySnapshot as error:
            raise ClockConflict(str(error)) from error
        self.complete_session(play, game, result)
        self.notify_clock()
        return play

    @staticmethod
    def board_for(game: Game) -> chess.Board:
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        for move in game.moves:
            board.push_uci(move)
        return board

    def check_timed_turn(
        self, play: PlaySession, clock: PlayClock, expected_version: int | None
    ) -> None:
        if play.status != "active" or clock.active_color is None or clock.deadline_at is None:
            raise ClockConflict("Timed play session is already completed")
        if is_expired(clock, utc_now()):
            self.expire_clock(play.id)
            raise ClockConflict("Clock deadline has passed")
        if expected_version is None or expected_version != clock.version:
            raise ClockConflict("Expected clock version does not match the current turn")

    def commit_timed_move(
        self,
        play: PlaySession,
        game: Game,
        clock: PlayClock,
        snapshot: PlaySnapshot,
        board: chess.Board,
        opponent_move: str | None,
    ) -> PlayMoveResult:
        self.persist_moves(play, game, snapshot.moves, snapshot)
        claim_clock(
            self.db,
            clock,
            utc_now(),
            moved=True,
            next_color=None if board.is_game_over() else "white" if board.turn else "black",
            terminal_reason="board" if board.is_game_over() else None,
        )
        # Freeze under the move/snapshot and clock write fences. Commit expires
        # ORM objects and may be followed immediately by another owned command.
        result = PlayMoveResult(
            opponent_move=opponent_move,
            interruption=None,
            status="completed" if board.is_game_over() else play.status,
            clock=_CommittedClockRead(**project_clock(clock).model_dump()),
        )
        if board.is_game_over():
            self.complete_session(play, game, board.result())
        else:
            self.db.add(play)
            self.db.commit()
        self.notify_clock()
        return result

    async def make_timed_move(
        self,
        play: PlaySession,
        clock: PlayClock,
        player_move: str,
        expected_version: int | None,
        *,
        expected_generation: int | None = None,
    ) -> PlayMoveResult:
        self.check_timed_turn(play, clock, expected_version)
        game = self.db.get(Game, play.game_id)
        if game is None:
            raise CorruptGameError("Timed play session has no Game")
        board = self.board_for(game)
        if board.turn != self.human_color(play, game):
            raise ClockConflict("It is not the player's turn")
        if clock.active_color != ("white" if board.turn else "black"):
            raise ClockConflict("Clock does not match the board turn")
        snapshot = PlaySnapshot.capture(play, game)
        if expected_generation is not None and expected_generation != int(
            snapshot.config.get("assistance_generation", "0")
        ):
            raise StalePlaySnapshot("PlaySession changed; grade the current position again")
        before_board = board.copy()
        move = chess.Move.from_uci(player_move)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal move: {player_move}")
        board.push(move)
        game.moves = [*snapshot.moves, player_move]
        session_id = play.id
        coach_mode = play.coach_mode
        coach_game = Game(**game.model_dump()) if coach_mode else None
        bot_version = clock.version + 1
        # Human move, increment, next deadline and snapshot fence are durable
        # before any engine or coaching await.
        result = self.commit_timed_move(play, game, clock, snapshot, board, None)
        if board.is_game_over():
            return result
        # Reserve the newly durable bot turn before coaching can yield to a
        # second owned command, and retain it until its lesson is persisted.
        with self._reserve_opponent(session_id):
            # Commit expires ORM state: a concurrent resignation/flag may have
            # stopped the freshly durable bot turn before these objects reload.
            self.check_timed_turn(play, clock, bot_version)
            if clock.deadline_at is None:
                raise ClockConflict("Opponent clock is already stopped")
            bot_deadline = as_utc(clock.deadline_at)
            player_id = snapshot.player_id
            self.db.rollback()
            prepared = None
            if coach_game is not None:
                coach_timeout = asyncio.timeout(max(0, (bot_deadline - utc_now()).total_seconds()))
                try:
                    # Preparation is read-only. AUTOCOMMIT prevents its historical
                    # lookups from holding a SQLite transaction over engine awaits.
                    bind = self.db.get_bind().execution_options(isolation_level="AUTOCOMMIT")
                    with Session(bind) as coach_db:
                        async with coach_timeout:
                            prepared = await prepare_coach_interruption(
                                session=coach_db,
                                game=coach_game,
                                player_id=player_id,
                                before_board=before_board,
                                after_board=board.copy(),
                                played_move=before_board.san(move),
                                history_san=uci_moves_to_san(
                                    coach_game.moves, coach_game.headers.get("FEN")
                                ),
                                pool_manager=self.engine_pool_manager,
                            )
                except TimeoutError:
                    if coach_timeout.expired():
                        self.expire_clock(session_id)
                        raise ClockConflict("Opponent clock expired during coaching") from None
                    logger.exception("Coach diagnostic timed out for timed session %s", session_id)
                except Exception:
                    logger.exception("Coach diagnostic failed for timed session %s", session_id)
            result = await self._resume_opponent(session_id, bot_version)
            if prepared is not None:
                interruption = prepared.add_to(self.db)
                self.db.commit()
                result = replace(result, interruption=interruption)
            return result

    async def resume_opponent(self, session_id: UUID, expected_version: int) -> PlayMoveResult:
        """Resume only the durable bot turn; retain its committed transition."""
        with self._reserve_opponent(session_id):
            return await self._resume_opponent(session_id, expected_version)

    @contextmanager
    def _reserve_opponent(self, session_id: UUID) -> Iterator[None]:
        if session_id in self._opponent_inflight:
            raise ClockConflict("Opponent move is already in progress")
        reservation = (
            self.clock_runtime.reserve(session_id) if self.clock_runtime else nullcontext()
        )
        with reservation:
            self._opponent_inflight.add(session_id)
            try:
                yield
            finally:
                self._opponent_inflight.discard(session_id)

    async def _resume_opponent(
        self, session_id: UUID, expected_version: int | None
    ) -> PlayMoveResult:
        self.db.expire_all()
        play = self.db.get(PlaySession, session_id)
        clock = self.db.get(PlayClock, session_id)
        if play is None:
            raise PlaySessionNotFound("PlaySession not found")
        if clock is None:
            raise ClockConflict("Opponent resume requires a timed session")
        self.check_timed_turn(play, clock, expected_version)
        game = self.db.get(Game, play.game_id)
        if game is None:
            raise CorruptGameError("Timed play session has no Game")
        board = self.board_for(game)
        if board.turn == self.human_color(play, game):
            raise ClockConflict("It is not the opponent's turn")
        if clock.active_color != ("white" if board.turn else "black"):
            raise ClockConflict("Clock does not match the board turn")
        snapshot = PlaySnapshot.capture(play, game)
        projected = project_clock(clock)
        remaining = projected.white_remaining_ms if board.turn else projected.black_remaining_ms
        target = thinking_target_ms(remaining, clock.increment_seconds)
        work_play, work_game = PlaySession(**play.model_dump()), Game(**game.model_dump())
        assert clock.deadline_at is not None
        bot_deadline = as_utc(clock.deadline_at)
        # Detached work only: neither a read transaction nor a dirty Game may
        # survive while the engine is running.
        self.db.rollback()
        bot_timeout = asyncio.timeout(max(0, (bot_deadline - utc_now()).total_seconds()))
        started = monotonic()
        try:
            async with bot_timeout:
                opponent_move = await self.play_opponent_move(
                    work_play, work_game, board, time_remaining_ms=remaining
                )
                elapsed = (monotonic() - started) * 1000
                # Pacing is discretionary: leave time to persist an already
                # returned move instead of sleeping through its hard deadline.
                live_budget = (bot_deadline - utc_now()).total_seconds()
                delay = min((target - elapsed) / 1000, max(0, live_budget - 0.010))
                if delay > 0:
                    await sleep(delay)
        except TimeoutError:
            if not bot_timeout.expired():
                raise
            self.expire_clock(session_id)
            raise ClockConflict("Opponent clock deadline has passed") from None
        self.db.expire_all()
        play = self.db.get(PlaySession, session_id)
        clock = self.db.get(PlayClock, session_id)
        if play is None or clock is None:
            raise ClockConflict("Timed play session was removed")
        self.check_timed_turn(play, clock, expected_version)
        game = self.db.get(Game, snapshot.game_id)
        if game is None:
            raise ClockConflict("Timed Game was removed")
        play.opponent_config = work_play.opponent_config
        game.moves = work_game.moves
        return self.commit_timed_move(play, game, clock, snapshot, board, opponent_move)
