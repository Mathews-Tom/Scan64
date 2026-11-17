from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import scan64.api.games as games_api
import scan64.chess.analysis.jobs as jobs
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.admission import AdmissionConfig, AdmissionController
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.eligibility import session_exclusion
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.online_import import OnlineGames, Source, fetch_online_games

PATH = "/v1/games/import-online"
PGN = (
    '[Event "Online"]\n[Site "https://lichess.org/abcdefgh"]\n'
    '[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n'
    "1. e4 { [%clk 0:04:59] } e5 1-0\n\n"
)


def register(db_session: Session, player_id: str) -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def authorized(client: TestClient, db_session: Session) -> dict[str, str]:
    return register(db_session, "local-owner")


@pytest.fixture
def provider(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[httpx.Response | Exception], list[httpx.Request]]:
    responses: list[httpx.Response | Exception] = []
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert responses, "Unexpected upstream request"
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch(source: Source, username: str, max_games: int) -> OnlineGames:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            return await fetch_online_games(source, username, max_games, client=http)

    monkeypatch.setattr(games_api, "fetch_online_games", fetch)
    return responses, requests


@pytest.fixture(autouse=True)
def admission(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[UUID]]:
    controller = AdmissionController(AdmissionConfig(daily_quota_games=0))
    completed: list[UUID] = []

    async def execute(job_id: UUID, pool_manager: object = None) -> None:
        job = db_session.get(AnalysisJob, job_id)
        assert job is not None
        assert db_session.get(Game, job.game_id) is not None
        completed.append(job_id)

    monkeypatch.setattr(jobs, "admission_controller", controller)
    monkeypatch.setattr(jobs, "execute_analysis_job_async", execute)
    yield completed
    assert client.portal is not None
    client.portal.call(controller.stop)


def test_import_is_owned_atomic_and_queued_through_admission(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    admission: list[UUID],
) -> None:
    responses, requests = provider
    responses.append(httpx.Response(200, text=PGN))
    response = client.post(
        PATH,
        json={"source": "lichess", "username": "alice", "max_games": 1},
        headers=authorized,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"imported_count", "duplicate_count", "skipped_count", "games", "message"}
    assert data["imported_count"] == 1
    assert data["duplicate_count"] == data["skipped_count"] == 0
    assert data["message"] == "Imported 1 new game."
    read = data["games"][0]
    assert set(read) == {"id", "white", "black", "result", "owner_color", "analysis_job_id"}
    assert read["owner_color"] == "white"
    game = db_session.get(Game, UUID(read["id"]))
    assert game is not None
    assert game.pgn == PGN and game.owner_player_id == "local-owner"
    assert game.owner_color == "white" and game.moves == ["e2e4", "e7e5"]
    job = db_session.get(AnalysisJob, UUID(read["analysis_job_id"]))
    assert job is not None and job.game_id == game.id and job.status == "pending"
    assert client.portal is not None

    async def drain() -> None:
        for _ in range(100):
            if admission:
                return
            await asyncio.sleep(0)
        raise AssertionError("Admission queue did not execute the online analysis job")

    client.portal.call(drain)
    assert admission == [job.id]
    assert db_session.exec(select(PlaySession)).all() == []
    assert session_exclusion(game, None) == "unknown assistance (imported or historical game)"
    assert len(requests) == 1
    assert client.get(f"/v1/games/{game.id}", headers=authorized).status_code == 200
    other = register(db_session, "other")
    assert client.get(f"/v1/games/{game.id}", headers=other).status_code == 404


def test_repeat_and_within_batch_duplicates_create_no_extra_jobs(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
) -> None:
    responses, _ = provider
    responses.extend([httpx.Response(200, text=PGN + PGN), httpx.Response(200, text=PGN)])
    first = client.post(PATH, headers=authorized, json={"source": "lichess", "username": "Alice"})
    assert first.status_code == 200
    assert first.json()["imported_count"] == first.json()["duplicate_count"] == 1
    repeated = client.post(
        PATH, headers=authorized, json={"source": "lichess", "username": "Alice"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["imported_count"] == 0
    assert repeated.json()["duplicate_count"] == 1 and repeated.json()["games"] == []
    assert len(db_session.exec(select(Game)).all()) == 1
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1


@pytest.mark.parametrize("prefix", ["", "game/"])
def test_paste_import_identity_deduplicates_only_for_token_owner(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    prefix: str,
) -> None:
    responses, _ = provider
    other = register(db_session, "other")
    pasted = client.post(
        "/v1/games",
        headers=other,
        json={"player_id": "other", "owner_color": "white", "pgn": PGN},
    )
    assert pasted.status_code == 200
    online_pgn = PGN.replace("lichess.org/", f"lichess.org/{prefix}")
    responses.append(httpx.Response(200, text=online_pgn))
    imported = client.post(
        PATH, headers=authorized, json={"source": "lichess", "username": "Alice"}
    )
    assert imported.json()["imported_count"] == 1
    responses.append(httpx.Response(200, text=online_pgn))
    repeated = client.post(PATH, headers=other, json={"source": "lichess", "username": "Alice"})
    assert repeated.json()["duplicate_count"] == 1 and repeated.json()["imported_count"] == 0
    assert len(db_session.exec(select(Game)).all()) == 2
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1


def test_chesscom_prefers_specific_link_over_shared_site(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
) -> None:
    responses, _ = provider
    old = PGN.replace(
        '[Site "https://lichess.org/abcdefgh"]',
        '[Site "Chess.com"]\n[Link "https://www.chess.com/game/live/123"]',
    )
    new = (
        old.replace("/123", "/124")
        .replace('[White "Alice"]', '[White "Bob"]')
        .replace(
            '[Black "Bob"]',
            '[Black "ALICE"]',
        )
    )
    assert (
        client.post(
            "/v1/games",
            headers=authorized,
            json={"player_id": "local-owner", "owner_color": "white", "pgn": old},
        ).status_code
        == 200
    )
    responses.extend(
        [
            httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/Alice/games/2026/11",
                    ]
                },
            ),
            httpx.Response(
                200,
                json={
                    "games": [
                        {"pgn": old, "end_time": 1, "rules": "chess"},
                        {"pgn": new, "end_time": 2, "rules": "chess"},
                    ]
                },
            ),
        ]
    )
    response = client.post(
        PATH,
        headers=authorized,
        json={"source": "chesscom", "username": "Alice", "max_games": 2},
    )
    assert response.status_code == 200, response.text
    assert response.json()["duplicate_count"] == response.json()["imported_count"] == 1
    read = response.json()["games"][0]
    assert read["owner_color"] == "black"
    game = db_session.get(Game, UUID(read["id"]))
    assert game is not None and game.pgn == new
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"username": "https://evil.test"},
        {"username": "aa/bb"},
        {"username": "a.b"},
        {"username": "a\n"},
        {"username": "éé"},
        {"username": "x"},
        {"username": "x" * 31},
        {"max_games": 0},
        {"max_games": 51},
        {"max_games": True},
        {"max_games": "2"},
        {"max_games": 2.0},
        {"source": "other"},
        {"player_id": "other"},
    ],
)
def test_invalid_input_makes_no_network_or_database_changes(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    invalid: dict[str, object],
) -> None:
    response = client.post(
        PATH,
        headers=authorized,
        json={"source": "lichess", "username": "Alice", **invalid},
    )
    assert response.status_code == 422, response.text
    assert provider[1] == []
    assert db_session.exec(select(Game)).all() == []
    assert db_session.exec(select(AnalysisJob)).all() == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_authentication_precedes_upstream_requests(
    client: TestClient,
    db_session: Session,
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    headers: dict[str, str],
) -> None:
    response = client.post(PATH, headers=headers, json={"source": "lichess", "username": "Alice"})
    assert response.status_code == 401
    assert provider[1] == []
    assert db_session.exec(select(Game)).all() == []


@pytest.mark.parametrize(
    "invalid",
    [
        PGN.replace("e5", "e5 Nf6"),
        PGN.replace('[White "Alice"]', '[White "Bob"]'),
        PGN.replace('[Black "Bob"]', '[Black "alice"]'),
        PGN.replace("https://lichess.org/abcdefgh", "https://attacker.test/abcdefgh"),
    ],
)
def test_invalid_game_rejects_whole_batch_without_partial_rows(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    invalid: str,
) -> None:
    provider[0].append(httpx.Response(200, text=PGN + invalid))
    response = client.post(
        PATH, headers=authorized, json={"source": "lichess", "username": "Alice"}
    )
    assert response.status_code == 400, response.text
    assert db_session.exec(select(Game)).all() == []
    assert db_session.exec(select(AnalysisJob)).all() == []


@pytest.mark.parametrize(
    "payload,skipped",
    [
        ("", 0),
        (
            PGN.replace(
                '[Event "Online"]',
                '[Event "Online"]\n[Variant "Atomic"]',
            ),
            1,
        ),
    ],
)
def test_no_standard_games_reports_the_reason_without_rows(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    payload: str,
    skipped: int,
) -> None:
    provider[0].append(httpx.Response(200, text=payload))
    response = client.post(
        PATH, headers=authorized, json={"source": "lichess", "username": "Alice"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["imported_count"] == data["duplicate_count"] == 0
    assert data["skipped_count"] == skipped
    assert "No standard games" in data["message"]
    if skipped:
        assert "non-standard" in data["message"]
    assert db_session.exec(select(Game)).all() == []
    assert db_session.exec(select(AnalysisJob)).all() == []


@pytest.mark.parametrize(
    "upstream,expected,detail",
    [
        (httpx.Response(404), 404, "username was not found"),
        (httpx.Response(429), 429, "rate limit"),
        (httpx.Response(503), 502, "request failed"),
        (httpx.ConnectError("offline"), 502, "network request failed"),
    ],
)
def test_upstream_errors_have_actionable_details_and_create_no_rows(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    upstream: httpx.Response | Exception,
    expected: int,
    detail: str,
) -> None:
    provider[0].append(upstream)
    response = client.post(
        PATH, headers=authorized, json={"source": "lichess", "username": "Alice"}
    )
    assert response.status_code == expected
    assert detail in response.json()["detail"]
    assert db_session.exec(select(Game)).all() == []
    assert db_session.exec(select(AnalysisJob)).all() == []


def test_persistence_failure_rolls_back_both_game_and_job_before_admission(
    client: TestClient,
    db_session: Session,
    authorized: dict[str, str],
    provider: tuple[list[httpx.Response | Exception], list[httpx.Request]],
    admission: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider[0].append(httpx.Response(200, text=PGN))

    def fail_commit() -> None:
        db_session.flush()
        raise IntegrityError("injected persistence failure", {}, Exception("constraint"))

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(IntegrityError):
        client.post(PATH, headers=authorized, json={"source": "lichess", "username": "Alice"})
    assert db_session.exec(select(Game)).all() == []
    assert db_session.exec(select(AnalysisJob)).all() == []
    assert admission == []
