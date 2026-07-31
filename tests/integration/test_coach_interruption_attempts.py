from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from chess_lesson_spec import Explanation
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.coach_interruption as coach_interruption
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.protocols import MoveDecision, OpponentContext
from scan64.chess.positions.models import Position
from scan64.content.models import StudySession
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.providers.stockfish.pool import EnginePoolManager

CHECKMATE_FEN = "7k/8/5KQ1/8/8/8/8/8 w - - 0 1"

COACH_FEN = "4k3/8/8/2b5/8/8/4P3/4K3 w - - 0 1"


class DeterministicOpponent:
    async def choose_move(self, position: Position, context: OpponentContext) -> MoveDecision:
        return MoveDecision(uci_move="e8d7")


def _register(db_session: Session, player_id: str = "alice") -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def _coach_session(db_session: Session, *, coach_mode: bool, fen: str = COACH_FEN) -> PlaySession:
    game = Game(
        pgn="",
        headers={"FEN": fen},
        moves=[],
        white="alice",
        black="Stockfish (strength 1)",
        owner_player_id="alice",
    )
    play_session = PlaySession(
        player_id="alice",
        game_id=game.id,
        opponent_config={"strength": "1"},
        coach_mode=coach_mode,
    )
    db_session.add(game)
    db_session.add(play_session)
    db_session.commit()
    return play_session


def _analysis_for(fen: str) -> EngineAnalysis:
    before = fen == COACH_FEN
    return EngineAnalysis(
        id=uuid4(),
        position_id=uuid4(),
        config={},
        raw_result=[
            {
                "pv": ["e1d1"] if before else ["e8d7"],
                "score_cp": 0 if before else 300,
            }
        ],
    )


def _install_deterministic_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    async def analyze_interactive(
        _: EnginePoolManager,
        fen: str,
        *,
        nodes: int | None = None,
        depth: int | None = None,
        multipv: int = 1,
        time_ms: int | None = None,
    ) -> EngineAnalysis:
        assert nodes is not None
        assert depth is None
        assert multipv == 1
        assert time_ms is None
        return _analysis_for(fen)

    monkeypatch.setattr(EnginePoolManager, "analyze_interactive", analyze_interactive)
    monkeypatch.setattr(
        PlaySessionService,
        "opponent_provider_for",
        lambda _service, _config: DeterministicOpponent(),
    )


def test_coach_diagnostic_persists_attempt_context_before_move_response(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=True)
    _install_deterministic_diagnostic(monkeypatch)

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    opportunity = db_session.exec(select(PersistedLessonOpportunity)).one()
    schedule = db_session.get(ReviewSchedule, ("alice", str(opportunity.id)))
    study_session = db_session.exec(select(StudySession)).one()
    assert opportunity.player_id == "alice"
    assert opportunity.game_id == play_session.game_id
    assert opportunity.verification_status == "verified"
    assert schedule is not None
    assert schedule.skill_id == "board_awareness.hanging_piece"
    assert study_session.player_id == "alice"
    assert study_session.domain == f"coach_interruption:{play_session.game_id}"
    observation = db_session.exec(select(ProfileObservation)).one()
    assert observation.player_id == "alice"
    assert observation.skill_id == schedule.skill_id
    assert observation.position_id != str(opportunity.source_position_id)
    assert db_session.get(SkillState, ("alice", schedule.skill_id)) is not None
    engine_evidence = next(
        item for item in db_session.exec(select(Evidence)).all() if item.kind == "engine_analysis"
    )
    assert engine_evidence.claim == "bounded interactive analysis for the flagged position"
    assert engine_evidence.payload["analysis_depth"] == "interactive"


def test_coach_diagnostic_does_not_duplicate_a_revisited_source_position(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=True)
    _install_deterministic_diagnostic(monkeypatch)

    first_response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert first_response.status_code == 200, first_response.text
    db_session.rollback()
    game = db_session.get(Game, play_session.game_id)
    assert game is not None
    game.moves = []
    replay_session = PlaySession(
        player_id="alice",
        game_id=game.id,
        opponent_config={"strength": "1"},
        coach_mode=True,
    )
    db_session.add(replay_session)
    db_session.commit()

    second_response = client.post(
        f"/v1/play-sessions/{replay_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert second_response.status_code == 200, second_response.text
    db_session.rollback()
    assert len(db_session.exec(select(PersistedLessonOpportunity)).all()) == 1


def test_coach_diagnostic_uses_template_when_explanation_times_out(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=True)
    _install_deterministic_diagnostic(monkeypatch)

    async def slow_explanation(*_: object, **__: object) -> object:
        await asyncio.sleep(coach_interruption.COACH_DIAGNOSTIC_TIMEOUT_SECONDS * 2)
        raise AssertionError("The bounded explanation request should be cancelled first.")

    monkeypatch.setattr(coach_interruption, "resolve_explanation", slow_explanation)

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    db_session.rollback()
    assert db_session.exec(select(PersistedLessonOpportunity)).one()


def test_coach_diagnostic_persists_the_resolved_explanation(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=True)
    _install_deterministic_diagnostic(monkeypatch)

    async def resolved_explanation(*_: object, **__: object) -> Explanation:
        return Explanation(text="Resolved explanation")

    monkeypatch.setattr(coach_interruption, "resolve_explanation", resolved_explanation)

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    db_session.rollback()
    opportunity = db_session.exec(select(PersistedLessonOpportunity)).one()
    assert opportunity.lesson_spec["explanation"]["text"] == "Resolved explanation"


def test_ordinary_play_does_not_run_the_coach_diagnostic(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=False)
    diagnostic_calls: list[str] = []

    async def observe_diagnostic(
        _: EnginePoolManager,
        fen: str,
        **__: object,
    ) -> EngineAnalysis:
        diagnostic_calls.append(fen)
        return _analysis_for(fen)

    monkeypatch.setattr(EnginePoolManager, "analyze_interactive", observe_diagnostic)
    monkeypatch.setattr(
        PlaySessionService,
        "opponent_provider_for",
        lambda _service, _config: DeterministicOpponent(),
    )

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    assert diagnostic_calls == []
    assert db_session.exec(select(PersistedLessonOpportunity)).all() == []


def test_game_ending_coach_move_skips_terminal_diagnostic(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=True, fen=CHECKMATE_FEN)
    diagnostic_calls: list[str] = []

    async def observe_diagnostic(
        _: EnginePoolManager,
        fen: str,
        **__: object,
    ) -> EngineAnalysis:
        diagnostic_calls.append(fen)
        return _analysis_for(fen)

    monkeypatch.setattr(EnginePoolManager, "analyze_interactive", observe_diagnostic)

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "g6g7"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert diagnostic_calls == []
    assert db_session.exec(select(PersistedLessonOpportunity)).all() == []


def test_coach_diagnostic_failure_preserves_the_legal_move(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _register(db_session)
    play_session = _coach_session(db_session, coach_mode=True)

    async def fail_diagnostic(
        _: EnginePoolManager,
        __: str,
        **___: object,
    ) -> EngineAnalysis:
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(EnginePoolManager, "analyze_interactive", fail_diagnostic)
    monkeypatch.setattr(
        PlaySessionService,
        "opponent_provider_for",
        lambda _service, _config: DeterministicOpponent(),
    )

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "e2e3"},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    game = db_session.get(Game, play_session.game_id)
    assert game is not None
    assert game.moves == ["e2e3", "e8d7"]
    assert db_session.exec(select(PersistedLessonOpportunity)).all() == []
    assert db_session.exec(select(EngineAnalysis)).all() == []
    assert db_session.exec(select(Position)).all() == []
