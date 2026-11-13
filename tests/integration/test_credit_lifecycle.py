from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.requests import Request

from scan64.api.data_lifecycle import ExportArchive, import_player_data
from scan64.api.models import Player
from scan64.chess.analysis.credit import CHECK_SKILL, positive_move_proof
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.games.assistance import SessionHintReveal, prefix_hash
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.models import ContentItem
from scan64.learning.profiling.models import SkillState
from tests.integration.test_data_lifecycle_completeness import _authorization, _create_player_token


def _seed_credit_records(session: Session, player_id: str) -> None:
    moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
    game = Game(
        owner_player_id=player_id,
        owner_color="black",
        result="0-1",
        moves=moves,
        pgn="1. f3 e5 2. g4 Qh4# 0-1",
    )
    session.add(game)
    session.flush()
    play = PlaySession(
        player_id=player_id,
        game_id=game.id,
        status="completed",
        assistance_tracking_version=1,
        credit_analysis_status="completed",
        opponent_config={"player_color": "black"},
    )
    board = chess.Board()
    for move in moves[:-1]:
        board.push_uci(move)
    source = Position(
        game_id=game.id,
        fen=board.fen(),
        canonical_id=board.epd(),
        side_to_move="b",
    )
    board.push_uci(moves[-1])
    reached = Position(
        game_id=game.id,
        fen=board.fen(),
        canonical_id=board.epd(),
        side_to_move="w",
    )
    session.add_all([play, source, reached])
    session.flush()
    before = EngineAnalysis(position_id=source.id, raw_result=[{"pv": ["d8h4"], "score_mate": 1}])
    after = EngineAnalysis(position_id=reached.id)
    session.add_all([before, after])
    session.flush()
    row = GameMoveReview(
        game_id=game.id,
        ply=4,
        source_position_id=source.id,
        reached_position_id=reached.id,
        before_analysis_id=before.id,
        after_analysis_id=after.id,
        move_uci="d8h4",
        san="Qh4#",
        mover="black",
        before_probability=1.0,
        after_probability=0.0,
    )
    proof, reason = positive_move_proof(row, source, before)
    assert reason is None and proof is not None
    # A legal abandoned branch is retained after takeback, not restricted to
    # the completed game's final move list.
    hint_moves = ["g1f3", "g8f6"]
    hint_board = chess.Board()
    for move in hint_moves:
        hint_board.push_uci(move)
    session.add_all(
        [
            row,
            PositiveMoveCredit(
                player_id=player_id,
                game_id=game.id,
                canonical_id=source.canonical_id,
                skill_id=CHECK_SKILL,
                ply=4,
                source_analysis_id=before.id,
                reached_analysis_id=after.id,
                proof=proof,
            ),
            SessionHintReveal(
                session_id=play.id,
                player_id=player_id,
                initial_fen=chess.STARTING_FEN,
                moves=hint_moves,
                prefix_hash=prefix_hash(chess.STARTING_FEN, hint_moves),
                canonical_id=hint_board.epd(),
                ply=3,
                highest_step=3,
            ),
            SkillState(player_id=player_id, concept_code=CHECK_SKILL, alpha=3.0, beta=2.0),
        ]
    )
    session.commit()


@pytest.fixture
def credit_archive(client: TestClient, db_session: Session) -> tuple[dict[str, Any], str]:
    player_id = "credit-lifecycle-owner"
    token = _create_player_token(client, player_id)
    _seed_credit_records(db_session, player_id)
    response = client.post(
        "/v1/exports",
        json={"player_id": player_id},
        headers=_authorization(token),
    )
    assert response.status_code == 200
    archive: dict[str, Any] = response.json()
    dry_run = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": True},
        headers=_authorization(token),
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["affected_rows"]["session_hint_reveals"] == 1
    assert dry_run.json()["affected_rows"]["positive_move_credits"] == 1
    assert len(db_session.exec(select(PositiveMoveCredit)).all()) == 1
    deletion = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        headers=_authorization(token),
    )
    assert deletion.status_code == 200
    assert deletion.json()["affected_rows"] == dry_run.json()["affected_rows"]
    assert db_session.exec(select(SessionHintReveal)).all() == []
    assert db_session.exec(select(PositiveMoveCredit)).all() == []
    return archive, token


def test_credit_restore_preserves_evidence_without_reapplying_alpha(
    client: TestClient,
    db_session: Session,
    credit_archive: tuple[dict[str, Any], str],
) -> None:
    archive, token = credit_archive
    player_id = archive["player"]["id"]
    global_item = ContentItem(
        id="unchanged-reference",
        domain="tactics",
        licence="CC0-1.0",
        provenance="curated",
        payload={"immutable": True},
    )
    db_session.add(global_item)
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert response.status_code == 200
    restored = client.post(
        "/v1/exports",
        json={"player_id": player_id},
        headers=_authorization(token),
    )
    assert restored.status_code == 200
    for field in ("positive_move_credits", "session_hint_reveals", "play_sessions", "skill_states"):
        assert restored.json()[field] == archive[field]
    state = db_session.get(SkillState, (player_id, CHECK_SKILL))
    assert state is not None and state.alpha == 3.0 and state.beta == 2.0
    assert db_session.get(ContentItem, global_item.id).payload == {"immutable": True}
    duplicate = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert duplicate.status_code == 409
    assert len(db_session.exec(select(PositiveMoveCredit)).all()) == 1
    assert state.alpha == 3.0


def test_old_archive_restores_unknown_assistance(
    client: TestClient,
    db_session: Session,
    credit_archive: tuple[dict[str, Any], str],
) -> None:
    archive, token = credit_archive
    archive.pop("positive_move_credits")
    archive.pop("session_hint_reveals")
    for play in archive["play_sessions"]:
        play.pop("assistance_tracking_version")
        play.pop("credit_analysis_status")
    response = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert response.status_code == 200
    restored = db_session.get(PlaySession, UUID(archive["play_sessions"][0]["id"]))
    assert restored is not None
    assert restored.assistance_tracking_version is None
    assert restored.credit_analysis_status is None
    assert db_session.exec(select(PositiveMoveCredit)).all() == []


@pytest.mark.parametrize(
    "field",
    [
        "player_id",
        "game_id",
        "canonical_id",
        "skill_id",
        "source_analysis_id",
        "reached_analysis_id",
    ],
)
def test_credit_import_rejects_foreign_or_unbound_provenance(
    client: TestClient,
    db_session: Session,
    credit_archive: tuple[dict[str, Any], str],
    field: str,
) -> None:
    archive, token = credit_archive
    archive["positive_move_credits"][0][field] = str(uuid4())
    response = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert response.status_code == 400
    assert db_session.get(Player, archive["player"]["id"]) is None
    assert db_session.exec(select(PositiveMoveCredit)).all() == []


@pytest.mark.parametrize(
    "mutation",
    [
        "proof",
        "duplicate_credit",
        "duplicate_reveal",
        "hinted_source",
        "unknown",
        "active",
        "coach",
        "custom_start",
        "early_analysis",
        "false_version",
        "wrong_ply",
        "forged_history",
        "hint_owner",
        "hint_session",
        "hint_hash",
        "hint_canonical",
        "hint_illegal_line",
        "hint_step",
    ],
)
def test_credit_import_preflights_entire_graph_before_writing(
    client: TestClient,
    db_session: Session,
    credit_archive: tuple[dict[str, Any], str],
    mutation: str,
) -> None:
    archive, token = credit_archive
    credit = archive["positive_move_credits"][0]
    reveal = archive["session_hint_reveals"][0]
    play = archive["play_sessions"][0]
    if mutation == "proof":
        credit["proof"]["material_gain"] = 100
    elif mutation == "duplicate_credit":
        archive["positive_move_credits"].append(deepcopy(credit))
    elif mutation == "duplicate_reveal":
        archive["session_hint_reveals"].append(deepcopy(reveal))
    elif mutation == "hinted_source":
        reveal["moves"] = archive["games"][0]["moves"][:3]
        reveal["ply"] = 4
        reveal["canonical_id"] = credit["canonical_id"]
        reveal["prefix_hash"] = prefix_hash(reveal["initial_fen"], reveal["moves"])
    elif mutation == "unknown":
        play["assistance_tracking_version"] = None
    elif mutation == "active":
        play["status"] = "active"
    elif mutation == "coach":
        play["coach_mode"] = True
    elif mutation == "custom_start":
        play["opponent_config"]["initial_fen"] = chess.STARTING_FEN
    elif mutation == "early_analysis":
        play["credit_analysis_status"] = "analysed before the game ended"
    elif mutation == "false_version":
        play["assistance_tracking_version"] = True
    elif mutation == "wrong_ply":
        credit["ply"] = 2
    elif mutation == "forged_history":
        archive["games"][0]["moves"][0] = "e2e4"
    elif mutation == "hint_owner":
        reveal["player_id"] = "another-player"
    elif mutation == "hint_session":
        reveal["session_id"] = str(uuid4())
    elif mutation == "hint_hash":
        reveal["prefix_hash"] = "forged"
    elif mutation == "hint_canonical":
        reveal["canonical_id"] = "forged"
    elif mutation == "hint_illegal_line":
        reveal["moves"] = ["e2e5", "e7e5"]
    elif mutation == "hint_step":
        reveal["highest_step"] = 4
    response = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert response.status_code == 400, mutation
    assert db_session.get(Player, archive["player"]["id"]) is None
    assert db_session.exec(select(SessionHintReveal)).all() == []
    assert db_session.exec(select(PositiveMoveCredit)).all() == []


def test_archive_cannot_attach_hints_to_another_players_session(
    client: TestClient,
    db_session: Session,
    credit_archive: tuple[dict[str, Any], str],
) -> None:
    archive, token = credit_archive
    foreign_id = "foreign-session-owner"
    _create_player_token(client, foreign_id)
    session_id = UUID(archive["play_sessions"][0]["id"])
    db_session.add(PlaySession(id=session_id, player_id=foreign_id))
    db_session.commit()
    response = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert response.status_code == 400
    assert db_session.get(PlaySession, session_id).player_id == foreign_id
    assert db_session.exec(select(SessionHintReveal)).all() == []


@pytest.mark.parametrize("legacy", [False, True])
def test_credit_archive_restores_into_a_fresh_database(
    tmp_path: Path,
    credit_archive: tuple[dict[str, Any], str],
    legacy: bool,
) -> None:
    archive, token = credit_archive
    if legacy:
        archive.pop("positive_move_credits")
        archive.pop("session_hint_reveals")
        for play in archive["play_sessions"]:
            play.pop("assistance_tracking_version")
            play.pop("credit_analysis_status")
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh-restore.db'}")
    SQLModel.metadata.create_all(engine)
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
        }
    )
    with Session(engine) as session:
        assert import_player_data(
            request,
            ExportArchive.model_validate(archive),
            session=session,
        ) == {"status": "imported"}
        assert len(session.exec(select(PositiveMoveCredit)).all()) == (0 if legacy else 1)
        assert len(session.exec(select(SessionHintReveal)).all()) == (0 if legacy else 1)
        play = session.get(PlaySession, UUID(archive["play_sessions"][0]["id"]))
        assert play is not None
        assert play.assistance_tracking_version == (None if legacy else 1)
        state = session.get(SkillState, (archive["player"]["id"], CHECK_SKILL))
        assert state is not None and state.alpha == 3.0
    engine.dispose()


def test_no_game_first_hint_roundtrips(
    client: TestClient,
    db_session: Session,
) -> None:
    player_id = "first-hint-archive-owner"
    token = _create_player_token(client, player_id)
    play = PlaySession(player_id=player_id, assistance_tracking_version=1)
    db_session.add(play)
    db_session.flush()
    db_session.add(
        SessionHintReveal(
            session_id=play.id,
            player_id=player_id,
            initial_fen=chess.STARTING_FEN,
            moves=[],
            prefix_hash=prefix_hash(chess.STARTING_FEN, []),
            canonical_id=chess.Board().epd(),
            ply=1,
            highest_step=2,
        )
    )
    db_session.commit()
    exported = client.post(
        "/v1/exports",
        json={"player_id": player_id},
        headers=_authorization(token),
    )
    assert exported.status_code == 200
    archive = exported.json()
    assert archive["games"] == []
    assert archive["positive_move_credits"] == []
    deleted = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        headers=_authorization(token),
    )
    assert deleted.status_code == 200
    assert deleted.json()["affected_rows"]["session_hint_reveals"] == 1
    response = client.post("/v1/imports", json=archive, headers=_authorization(token))
    assert response.status_code == 200
    reveals = db_session.exec(select(SessionHintReveal)).all()
    assert len(reveals) == 1 and reveals[0].highest_step == 2
    restored = db_session.get(PlaySession, reveals[0].session_id)
    assert restored is not None and restored.game_id is None
