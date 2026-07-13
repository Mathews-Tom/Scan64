from uuid import UUID

import chess
from sqlmodel import Session

from scan64.chess.games.models import Game, PlaySession
from scan64.chess.opponents.protocols import OpponentContext, OpponentPolicy
from scan64.chess.positions.models import Position


class PlaySessionService:
    def __init__(self, db_session: Session, opponent_provider: OpponentPolicy):
        self.db = db_session
        self.opponent_provider = opponent_provider

    async def make_move(self, session_id: UUID, player_move: str) -> str | None:
        """
        Process a player move and return the opponent's response move (UCI), or None if game over.
        """
        play_session = self.db.get(PlaySession, session_id)
        if not play_session:
            raise ValueError("PlaySession not found")

        if play_session.status != "active":
            raise ValueError(f"PlaySession is {play_session.status}")

        if not play_session.game_id:
            # Initialize a new game
            game = Game(pgn="", moves=[], white="Player", black="Opponent")
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
        for m in fetched.moves:
            board.push_uci(m)

        # Apply player move
        move = chess.Move.from_uci(player_move)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal move: {player_move}")

        board.push(move)
        fetched.moves = fetched.moves + [player_move]

        # Check if game over
        if board.is_game_over():
            play_session.status = "completed"
            fetched.result = board.result()
            self.db.add(fetched)
            self.db.add(play_session)
            self.db.commit()
            return None

        # Determine opponent context
        strength = int(play_session.opponent_config.get("strength", 10))
        clock = play_session.clock_config
        time_limit = clock.get("time_remaining_ms") if clock else None

        context = OpponentContext(
            strength_setting=strength, time_remaining_ms=int(time_limit) if time_limit else None
        )

        position = Position(
            fen=board.fen(),
            side_to_move="w" if board.turn == chess.WHITE else "b",
            canonical_id=board.fen().split(" ")[0],
        )

        # Get opponent move
        decision = await self.opponent_provider.choose_move(position, context)

        opp_move = chess.Move.from_uci(decision.uci_move)
        if opp_move not in board.legal_moves:
            raise RuntimeError(f"Opponent produced illegal move: {decision.uci_move}")

        board.push(opp_move)
        fetched.moves = fetched.moves + [decision.uci_move]

        if board.is_game_over():
            play_session.status = "completed"
            fetched.result = board.result()

        self.db.add(fetched)
        self.db.add(play_session)
        self.db.commit()

        return decision.uci_move
