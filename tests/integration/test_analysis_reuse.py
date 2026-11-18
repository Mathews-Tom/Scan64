from __future__ import annotations

from copy import deepcopy
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from scan64.chess.analysis.models import EngineAnalysis, EngineAnalysisConfig
from scan64.chess.analysis.reuse import BatchAnalysisAdapter
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from tests.integration.test_data_lifecycle import authorization_header, create_player_token


class IdentifiedAdapter:
    def __init__(self) -> None:
        self.identity = EngineAnalysisConfig(
            engine_name="Stockfish 19",
            engine_version="sha256:" + "a" * 64,
            options={"Threads": 1, "Hash": 16, "Skill Level": 20},
        ).model_dump()
        self.calls = 0

    async def analysis_identity(self) -> dict[str, Any]:
        return deepcopy(self.identity)

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        self.calls += 1
        return EngineAnalysis(
            position_id=None,
            config=dict(self.identity, nodes=nodes, depth=depth, time_ms=time_ms, multipv=multipv),
            raw_result=[{"pv": [next(iter(chess.Board(fen).legal_moves)).uci()], "score_cp": 20}],
        )


async def saved_analysis(session: Session, owner: str) -> tuple[Game, Position, EngineAnalysis]:
    game = Game(pgn="", moves=["e2e4"], owner_player_id=owner, owner_color="white")
    position = ingest_fen(chess.STARTING_FEN, game.id)
    analysis = await IdentifiedAdapter().analyze_position(position.fen, nodes=10_000)
    analysis.position_id = position.id
    session.add_all([game, position, analysis])
    session.commit()
    session.refresh(game)
    session.refresh(position)
    session.refresh(analysis)
    return game, position, analysis


@pytest.mark.asyncio
async def test_hits_are_detached_fresh_occurrences_without_autoflush_or_n_plus_one(
    db_session: Session,
) -> None:
    source_game, position, source = await saved_analysis(db_session, "reuse-owner")
    snapshot = source.model_dump()
    target = Game(
        pgn="", moves=source_game.moves, owner_player_id="reuse-owner", owner_color="white"
    )
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)
    db_session.refresh(position)
    db_session.refresh(source)
    # A caller's pending changes must not be flushed by read-only lookup.
    pending = Game(pgn="pending", owner_player_id="reuse-owner", owner_color="white")
    db_session.add(pending)
    statements: list[str] = []

    def statement(_conn: Any, _cursor: Any, sql: str, *_: Any) -> None:
        statements.append(sql)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", statement)
    adapter = IdentifiedAdapter()
    try:
        batch = BatchAnalysisAdapter(adapter, target, db_session)
        first = await batch.analyze_position(position.fen, nodes=10_000)
        second = await batch.analyze_position(position.fen, nodes=10_000)
    finally:
        event.remove(bind, "before_cursor_execute", statement)
    assert len(statements) == 1
    assert statements[0].startswith("SELECT")
    assert pending in db_session.new
    assert adapter.calls == 0
    assert first.id != second.id and first.id != source.id
    assert first.position_id is None and second.position_id is None
    assert first not in db_session and second not in db_session
    assert first.config == source.config and first.raw_result == source.raw_result
    first.raw_result[0]["pv"].append("e7e5")
    first.config["options"]["Hash"] = 99
    assert second.raw_result == source.raw_result
    assert second.config == source.config
    assert source.model_dump() == snapshot
    db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "build",
        "threads",
        "hash",
        "default_option",
        "network",
        "nodes",
        "depth",
        "time",
        "multipv",
        "halfmove",
        "fullmove",
        "unknown",
        "legacy_options",
        "legacy_budget",
    ],
)
async def test_only_full_exact_keys_hit(db_session: Session, change: str) -> None:
    game, position, source = await saved_analysis(db_session, "exact-owner")
    adapter = IdentifiedAdapter()
    fen = position.fen
    limits: dict[str, int | None] = {"nodes": 10_000}
    if change == "build":
        adapter.identity["engine_version"] = "sha256:" + "b" * 64
    elif change in {"threads", "hash", "default_option"}:
        name = {"threads": "Threads", "hash": "Hash", "default_option": "Move Overhead"}[change]
        adapter.identity["options"][name] = 32
    elif change == "network":
        adapter.identity["network"] = "changed network content"
    elif change in {"nodes", "depth", "time", "multipv"}:
        name = {"nodes": "nodes", "depth": "depth", "time": "time_ms", "multipv": "multipv"}[change]
        limits[name] = 2
    elif change in {"halfmove", "fullmove"}:
        parts = fen.split()
        parts[4 if change == "halfmove" else 5] = "9"
        fen = " ".join(parts)
        # Include the changed full FEN in this game's preload, not only its canonical board.
        game.headers = {"FEN": fen}
    else:
        source.config = deepcopy(source.config)
        if change == "unknown":
            source.config["engine_version"] = "unknown"
        else:
            source.config.pop("options" if change == "legacy_options" else "time_ms")
        db_session.add(source)
        db_session.commit()
    batch = BatchAnalysisAdapter(adapter, game, db_session)
    await batch.analyze_position(
        fen,
        nodes=limits.get("nodes"),
        depth=limits.get("depth"),
        time_ms=limits.get("time_ms"),
        multipv=limits.get("multipv", 1) or 1,
    )
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_foreign_archive_cannot_seed_same_owner_reuse(
    client: TestClient, db_session: Session
) -> None:
    owner = "archive-reuse-owner"
    headers = authorization_header(create_player_token(client, owner, owner))
    foreign_game, position, source = await saved_analysis(db_session, "foreign-owner")
    snapshot = source.model_dump()
    target = Game(pgn="", moves=["e2e4"], owner_player_id=owner, owner_color="white")
    db_session.add(target)
    db_session.commit()
    response = client.post("/v1/exports", json={"player_id": owner}, headers=headers)
    assert response.status_code == 200
    forged = response.json()
    deleted = client.request(
        "DELETE",
        f"/v1/players/{owner}/data",
        json={"dry_run": False, "confirmation": f"delete-{owner}"},
        headers=headers,
    )
    assert deleted.status_code == 200
    db_session.refresh(foreign_game)
    db_session.refresh(position)
    db_session.refresh(source)
    forged_game = foreign_game.model_dump(mode="json")
    forged_game["owner_player_id"] = owner
    forged["games"].append(forged_game)
    forged["positions"].append(position.model_dump(mode="json"))
    forged["engine_analyses"].append(source.model_dump(mode="json"))
    imported = client.post("/v1/imports", json=forged, headers=headers)
    assert imported.status_code == 400
    assert "authorized player" in imported.json()["detail"]
    target = Game(pgn="", moves=["e2e4"], owner_player_id=owner, owner_color="white")
    db_session.add(target)
    db_session.commit()
    adapter = IdentifiedAdapter()
    batch = BatchAnalysisAdapter(adapter, target, db_session)
    await batch.analyze_position(position.fen, nodes=10_000)
    assert adapter.calls == 1
    db_session.refresh(source)
    assert source.model_dump() == snapshot
    assert db_session.exec(select(EngineAnalysis)).all() == [source]
