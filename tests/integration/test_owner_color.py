import io
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.chess.analysis import jobs
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import CandidatePosition
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position


class ReferenceFastPass:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(
        self, moves: list[str], initial_fen: str | None = None
    ) -> list[CandidatePosition]:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        candidates = []
        for index, san in enumerate(moves):
            best = next(iter(board.legal_moves)).uci()
            before = EngineAnalysis(
                position_id=uuid4(),
                config={"nodes": 10000},
                raw_result=[{"pv": [best], "score_cp": 500}],
            )
            board.push_san(san)
            after = EngineAnalysis(
                position_id=uuid4(),
                config={"nodes": 10000},
                raw_result=[{"pv": [next(iter(board.legal_moves)).uci()], "score_cp": -500}],
            )
            candidates.append(
                CandidatePosition(
                    fen=board.fen(),
                    move_index=index,
                    before_analysis=before,
                    after_analysis=after,
                    swing_cp=1000,
                )
            )
        return candidates


class ReferenceFocusedPass:
    def __init__(self, *_: object) -> None:
        pass

    async def run_focused_pass(self, candidates: list[CandidatePosition]) -> list[EngineAnalysis]:
        return [
            EngineAnalysis(
                position_id=uuid4(),
                config={"nodes": 1000000, "multipv": 4},
                raw_result=candidate.after_analysis.raw_result,
            )
            for candidate in candidates
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_color", "black_to_move"), [("white", False), ("black", False), ("black", True)]
)
async def test_reference_game_lessons_and_observations_are_owner_only(
    owner_color: Literal["white", "black"],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    black_to_move: bool,
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", ReferenceFastPass)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", ReferenceFocusedPass)
    path = Path(__file__).parents[1] / "fixtures" / "reference_amateur_game.pgn"
    pgn = path.read_text()
    if black_to_move:
        board = chess.Board()
        board.push_uci("e2e4")
        pgn = f'[FEN "{board.fen()}"]\n[SetUp "1"]\n\n1... e5 2. Nf3 Nc6 3. Bb5 a6 *'
    parsed = chess.pgn.read_game(io.StringIO(pgn))
    assert parsed is not None and not parsed.errors
    game = Game(
        pgn=pgn,
        moves=[move.uci() for move in parsed.mainline_moves()],
        headers=dict(parsed.headers),
        owner_player_id="reference-player",
        owner_color=owner_color,
    )
    db_session.add(game)
    db_session.commit()
    from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
    from scan64.learning.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    register_seeded_detectors(registry)
    await jobs.run_analysis_for_game(game, db_session, registry)
    lessons = db_session.exec(
        select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.game_id == game.id)
    ).all()
    assert lessons
    for lesson in lessons:
        position = db_session.get(Position, lesson.source_position_id)
        assert position is not None
        assert chess.Board(position.fen).turn == (owner_color == "white")
    from scan64.learning.profiling.models import ProfileObservation

    observations = db_session.exec(select(ProfileObservation)).all()
    assert observations
    for observation in observations:
        position = db_session.get(Position, UUID(observation.position_id))
        assert position is not None
        # Observations refer to the position AFTER the owner's move.
        assert chess.Board(position.fen).turn != (owner_color == "white")


@pytest.mark.asyncio
async def test_legacy_game_analysis_explicitly_rejects_missing_color(db_session: Session) -> None:
    game = Game(pgn="", owner_player_id="legacy-player")
    with pytest.raises(ValueError, match="without owner_color"):
        await jobs.run_analysis_for_game(game, db_session)


def test_game_creation_requires_owner_color(client: TestClient) -> None:
    response = client.post("/v1/games", json={"pgn": "1. e4 *", "player_id": "player"})
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "owner_color"] for error in response.json()["detail"])


def test_legacy_game_remains_readable_but_analysis_api_reports_error(
    client: TestClient, db_session: Session
) -> None:
    response = client.post("/v1/players", json={"id": "legacy-api-player"})
    token = response.json()["access_token"]
    game = Game(pgn="", owner_player_id="legacy-api-player")
    db_session.add(game)
    db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/v1/games/{game.id}", headers=headers).status_code == 200
    response = client.post(f"/v1/games/{game.id}/analysis-jobs", headers=headers)
    assert response.status_code == 409
    assert "without owner_color" in response.json()["detail"]
