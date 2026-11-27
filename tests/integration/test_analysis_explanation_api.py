"""POST /v1/analysis/position/explanation (#368) on recorded Stockfish 18 lines."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import chess
import httpx
import pytest
import uvicorn
from chess_lesson_spec import ExplanationClaim
from fastapi.testclient import TestClient
from sqlmodel import Session

import scan64.api.analysis_explanation as endpoint_module
import scan64.explanations.settings as settings_module
from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import (
    COMPARISON_EVIDENCE_ID,
    BoardLinesFacts,
    board_lines_grounding,
)
from scan64.explanations.settings import OPERATOR_LLM_CONFIG, OwnerExplanationSettings
from scan64.explanations.validator import validate_generated_explanation
from scan64.providers.llm import ExplanationRequest, LLMProviderError
from scan64.providers.llm.contracts import GeneratedExplanation
from scan64.providers.stockfish.pool import HINT_MULTIPV, HINT_NODES

ENDPOINT = "/v1/analysis/position/explanation"
PLAYER = "explain-owner"
RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/analysis_board_lines.json").read_text(encoding="utf-8")
)
POSITIONS: dict[str, dict[str, Any]] = RECORDING["positions"]


@pytest.fixture(autouse=True)
def _no_operator_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)


def register(session: Session, player_id: str = PLAYER) -> dict[str, str]:
    token, hashed = issue_player_token()
    session.add(Player(id=player_id))
    session.add(PlayerCredential(player_id=player_id, token_hash=hashed))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


def recorded(name: str) -> AsyncMock:
    entry = POSITIONS[name]
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    return AsyncMock(side_effect=lambda *args, **kwargs: analysis.model_copy(deep=True))


def body(name: str, **extra: Any) -> dict[str, Any]:
    return {"player_id": PLAYER, "fen": POSITIONS[name]["fen"], **extra}


class _Provider:
    def __init__(self, claims: tuple[ExplanationClaim, ...] | None) -> None:
        self.claims = claims
        self.requests: list[ExplanationRequest] = []

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.requests.append(request)
        if self.claims is None:
            raise LLMProviderError("provider unavailable")
        return GeneratedExplanation(claims=self.claims)


def _grant(provider: _Provider | None) -> Callable[..., Awaitable[_Provider | None]]:
    async def read(session: Session, player_id: str, owner_generation: int | None) -> Any:
        assert player_id == PLAYER
        return provider

    return read


@pytest.mark.parametrize("name", sorted(POSITIONS))
def test_template_explains_three_grounded_lines_for_any_position(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    headers = register(db_session)
    analyze = recorded(name)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(ENDPOINT, headers=headers, json=body(name))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "template"
    assert data["fallback_reason"] is None
    assert [line["line_id"] for line in data["lines"]] == ["line-1", "line-2", "line-3"]
    recorded_pvs = [entry["pv"] for entry in POSITIONS[name]["raw_result"][:3]]
    assert [[move["uci"] for move in line["moves"]] for line in data["lines"]] == recorded_pvs
    facts = BoardLinesFacts.model_validate(data["facts"])
    assert [list(line.uci) for line in facts.lines] == recorded_pvs
    claims = tuple(
        ExplanationClaim(
            text=claim["text"],
            evidence_ref=claim["evidence_ref"],
            line=tuple(claim["moves"]),
            disclosure_level=1,
        )
        for claim in data["claims"]
    )
    validate_generated_explanation(
        GeneratedExplanation(claims=claims), board_lines_grounding(facts)
    )
    assert {claim["line_id"] for claim in data["claims"]} == {"line-1", "line-2", "line-3"}
    for claim in data["claims"]:
        line = next(line for line in data["lines"] if line["line_id"] == claim["line_id"])
        uci = [move["uci"] for move in line["moves"]]
        assert claim["moves"] and uci[: len(claim["moves"])] == claim["moves"]
    assert sum(claim["evidence_ref"] == COMPARISON_EVIDENCE_ID for claim in data["claims"]) == 2
    analyze.assert_awaited_once_with(chess.Board(POSITIONS[name]["fen"]), nodes=None, depth=None)


def test_explained_lines_are_the_lines_the_board_shows(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("open_centre"))
    shown = client.post("/v1/analysis/position", headers=headers, json=body("open_centre"))
    explained = client.post(ENDPOINT, headers=headers, json=body("open_centre"))
    assert shown.status_code == explained.status_code == 200
    assert [
        {"score_cp": line["score_cp"], "mate": line["mate"], "moves": line["moves"]}
        for line in explained.json()["lines"]
    ] == shown.json()["lines"]


def test_black_to_move_scores_are_white_view_and_facts_are_side_to_move_view(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("italian_black"))
    data = client.post(ENDPOINT, headers=headers, json=body("italian_black")).json()
    assert data["lines"][0]["score_cp"] == -53
    assert data["facts"]["lines"][0]["evaluation"]["centipawns"] == 53
    assert data["claims"][0]["text"].startswith("Black to move.")


def test_grounded_provider_wording_is_returned_with_its_line_links(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("start"))
    provider = _Provider(
        (
            ExplanationClaim(
                text="The engine's first choice is 1.e4, scored +0.41.",
                evidence_ref="line-1",
                line=("e2e4",),
                disclosure_level=1,
            ),
            ExplanationClaim(
                text="1.d4 is 0.11 pawns behind it.",
                evidence_ref=COMPARISON_EVIDENCE_ID,
                line=("d2d4",),
                disclosure_level=1,
            ),
        )
    )
    monkeypatch.setattr(endpoint_module, "read_local_explanation_provider", _grant(provider))
    data = client.post(ENDPOINT, headers=headers, json=body("start")).json()
    assert data["source"] == "narrator"
    assert data["fallback_reason"] is None
    assert data["text"] == (
        "The engine's first choice is 1.e4, scored +0.41. 1.d4 is 0.11 pawns behind it."
    )
    assert [(claim["line_id"], claim["moves"]) for claim in data["claims"]] == [
        ("line-1", ["e2e4"]),
        ("line-3", ["d2d4"]),
    ]
    [request] = provider.requests
    # The response holds the full facts; the prompt holds them with move facts cut.
    assert BoardLinesFacts.model_validate_json(request.messages[1].content) == (
        BoardLinesFacts.model_validate(data["facts"]).for_prompt()
    )


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        (
            (
                ExplanationClaim(
                    text="Line 1 is worth +2.00.",
                    evidence_ref="line-1",
                    line=("e2e4",),
                    disclosure_level=1,
                ),
            ),
            "evaluation_not_supported",
        ),
        (
            (
                ExplanationClaim(
                    text="The second line drops a knight.",
                    evidence_ref="line-2",
                    line=("g1f3",),
                    disclosure_level=1,
                ),
            ),
            "material_not_supported",
        ),
        (
            (
                ExplanationClaim(
                    text="The lines are close.",
                    evidence_ref=COMPARISON_EVIDENCE_ID,
                    disclosure_level=1,
                ),
            ),
            "missing_line",
        ),
        (None, "provider_error"),
    ],
)
def test_rejected_provider_output_falls_back_to_the_template(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    claims: tuple[ExplanationClaim, ...] | None,
    reason: str,
) -> None:
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("start"))
    template = client.post(ENDPOINT, headers=headers, json=body("start")).json()
    monkeypatch.setattr(
        endpoint_module, "read_local_explanation_provider", _grant(_Provider(claims))
    )
    data = client.post(ENDPOINT, headers=headers, json=body("start")).json()
    assert data["source"] == "template"
    assert data["fallback_reason"] == reason
    assert data["claims"] == template["claims"]


def test_the_owner_grant_selects_the_local_provider(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    db_session.add(OwnerExplanationSettings(player_id=PLAYER, model="granted-model"))
    db_session.commit()
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("start"))
    models: list[str] = []

    def local_provider(model: str, still_authorized: Any) -> _Provider:
        models.append(model)
        return _Provider(None)

    monkeypatch.setattr(settings_module, "LocalOllamaExplanationProvider", local_provider)
    data = client.post(ENDPOINT, headers=headers, json=body("start")).json()
    assert models == ["granted-model"]
    assert data["source"] == "template"
    assert data["fallback_reason"] == "provider_error"


@pytest.mark.parametrize("multipv", [1, 2, 4])
def test_multipv_selects_how_many_ranked_lines_are_explained(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, multipv: int
) -> None:
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("king_pawn"))
    data = client.post(ENDPOINT, headers=headers, json=body("king_pawn", multipv=multipv)).json()
    assert len(data["lines"]) == multipv
    assert len(data["lines"][0]["moves"]) == len(POSITIONS["king_pawn"]["raw_result"][0]["pv"])


def test_narrower_node_and_depth_budgets_reach_the_hint_search(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    analyze = recorded("start")
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(ENDPOINT, headers=headers, json=body("start", nodes=50_000, depth=12))
    assert response.status_code == 200, response.text
    analyze.assert_awaited_once_with(chess.Board(), nodes=50_000, depth=12)


@pytest.mark.parametrize(
    "extra",
    [
        {"multipv": HINT_MULTIPV + 1},
        {"multipv": 0},
        {"nodes": HINT_NODES + 1},
        {"nodes": 0},
        {"depth": 0},
        {"depth": 246},
    ],
)
def test_budgets_beyond_the_analysis_limits_are_rejected_before_search(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, extra: dict[str, int]
) -> None:
    headers = register(db_session)
    analyze = recorded("start")
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = client.post(ENDPOINT, headers=headers, json=body("start", **extra))
    assert response.status_code == 422, response.text
    analyze.assert_not_awaited()


def test_authentication_position_and_game_over_precede_engine_access(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = register(db_session)
    other = register(db_session, "explain-other")
    analyze = recorded("start")
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    assert client.post(ENDPOINT, json=body("start")).status_code == 401
    assert client.post(ENDPOINT, headers=other, json=body("start")).status_code == 404
    invalid = client.post(ENDPOINT, headers=owner, json={"player_id": PLAYER, "fen": "not a FEN"})
    assert invalid.status_code == 422
    mated = client.post(
        ENDPOINT, headers=owner, json={"player_id": PLAYER, "fen": "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"}
    )
    assert mated.status_code == 409
    analyze.assert_not_awaited()


def test_busy_unavailable_and_unusable_engine_output_are_retryable_503(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    manager = app.state.engine_pool_manager
    monkeypatch.setattr(manager, "_hint_in_progress", True)
    busy = client.post(ENDPOINT, headers=headers, json=body("start"))
    assert busy.status_code == 503 and "busy" in busy.json()["detail"]
    monkeypatch.setattr(manager, "_hint_in_progress", False)
    unusable = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e2e5"], "score_cp": 3}]))
    monkeypatch.setattr(manager, "analyze_hint", unusable)
    invalidate = MagicMock()
    monkeypatch.setattr(manager, "invalidate_hint", invalidate)
    failed = client.post(ENDPOINT, headers=headers, json=body("start"))
    assert failed.status_code == 503 and "try again" in failed.json()["detail"]
    invalidate.assert_called_once_with(chess.Board(), nodes=None, depth=None)
    monkeypatch.setattr(manager.interactive_pool, "concurrency", 1)
    single = client.post(ENDPOINT, headers=headers, json=body("start"))
    assert single.status_code == 503 and "at least two" in single.json()["detail"]
    monkeypatch.setattr(app.state, "engine_pool_manager", None)
    absent = client.post(ENDPOINT, headers=headers, json=body("start"))
    assert absent.status_code == 503 and "interactive engine pool" in absent.json()["detail"]


def test_playwright_fixture_is_the_current_template_response(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("start"))
    response = client.post(ENDPOINT, headers=headers, json=body("start"))
    stored = Path(__file__).parents[2] / "apps/scan64-web/tests/e2e/fixtures"
    expected = json.loads((stored / "analysis-explanation-start.json").read_text(encoding="utf-8"))
    assert response.json() == expected, (
        "regenerate: uv run python tests/fixtures/write_analysis_explanation_e2e.py"
    )


class _FirstCallBlocks:
    """The first model call runs until it is cancelled; later calls fail at once."""

    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.calls += 1
        if self.calls > 1:
            raise LLMProviderError("second call")
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise LLMProviderError("never cancelled")


def _grant_player(provider: _FirstCallBlocks) -> Callable[..., Awaitable[Any]]:
    async def grant(session: Session, player_id: str, owner_generation: int | None) -> Any:
        return provider if player_id == PLAYER else None

    return grant


def test_a_second_explanation_for_the_same_player_cancels_the_first(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    other_headers = register(db_session, "explain-other")
    analyze = recorded("start")
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    provider = _FirstCallBlocks()
    monkeypatch.setattr(endpoint_module, "read_local_explanation_provider", _grant_player(provider))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, ENDPOINT, headers=headers, json=body("start"))
        assert provider.started.wait(10)
        # Calls are per player: another player runs while the first call runs.
        other = client.post(
            ENDPOINT, headers=other_headers, json={**body("start"), "player_id": "explain-other"}
        )
        assert not provider.cancelled.is_set()
        second = client.post(ENDPOINT, headers=headers, json=body("start"))
        replaced = first.result(10)
    assert provider.cancelled.is_set()
    assert replaced.status_code == 409
    assert replaced.json()["detail"] == endpoint_module.SUPERSEDED_DETAIL
    assert second.status_code == 200, second.text
    assert second.json()["fallback_reason"] == "provider_error"
    assert other.status_code == 200, other.text
    assert provider.calls == 2
    assert analyze.await_count == 3
    assert not app.state.analysis_explanation_calls.running(PLAYER)


def test_a_request_replaced_during_its_search_starts_no_model_call(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    analysis = recorded("start")
    searching = threading.Event()
    finish_search = threading.Event()

    async def analyze(*args: Any, **kwargs: Any) -> EngineAnalysis:
        if not searching.is_set():
            searching.set()
            while not finish_search.is_set():
                await asyncio.sleep(0.01)
        result: EngineAnalysis = await analysis(*args, **kwargs)
        return result

    provider = _Provider(None)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    monkeypatch.setattr(endpoint_module, "read_local_explanation_provider", _grant(provider))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, ENDPOINT, headers=headers, json=body("start"))
        assert searching.wait(10)
        second = client.post(ENDPOINT, headers=headers, json=body("start"))
        finish_search.set()
        replaced = first.result(10)
    assert second.status_code == 200, second.text
    assert replaced.status_code == 409
    assert replaced.json()["detail"] == endpoint_module.SUPERSEDED_DETAIL
    # Only the latest request reached the model.
    assert len(provider.requests) == 1


def test_a_failed_explanation_frees_the_player_slot(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    manager = app.state.engine_pool_manager
    monkeypatch.setattr(manager, "analyze_hint", AsyncMock(side_effect=TimeoutError))
    assert client.post(ENDPOINT, headers=headers, json=body("start")).status_code == 503
    monkeypatch.setattr(manager, "analyze_hint", recorded("start"))
    assert client.post(ENDPOINT, headers=headers, json=body("start")).status_code == 200


def test_the_request_session_is_released_before_search_and_generation(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(db_session)
    in_transaction: list[bool] = []
    analysis = recorded("start")

    async def analyze(*args: Any, **kwargs: Any) -> EngineAnalysis:
        in_transaction.append(db_session.in_transaction())
        result: EngineAnalysis = await analysis(*args, **kwargs)
        return result

    class _Checking(_Provider):
        async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
            in_transaction.append(db_session.in_transaction())
            return await super().generate(request)

    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    monkeypatch.setattr(endpoint_module, "read_local_explanation_provider", _grant(_Checking(None)))
    response = client.post(ENDPOINT, headers=headers, json=body("start"))
    assert response.status_code == 200, response.text
    assert in_transaction == [False, False]


@contextmanager
def _served(asgi_app: Any) -> Iterator[str]:
    """Serve ``asgi_app`` with uvicorn on an ephemeral port; the lifespan is the caller's."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(asgi_app, lifespan="off", log_level="warning", timeout_graceful_shutdown=5)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_through_uvicorn_a_new_request_cancels_the_abandoned_model_call(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real middleware stack: the client leaves, then explains again.

    ``Request.is_disconnected`` never sees the first client leave behind
    ``IdempotencyMiddleware``; the second request must cancel the first call.
    """
    headers = register(db_session)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", recorded("start"))
    provider = _FirstCallBlocks()
    monkeypatch.setattr(endpoint_module, "read_local_explanation_provider", _grant_player(provider))

    async def leave_then_explain_again(base_url: str) -> httpx.Response:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=20) as http:
            first = asyncio.ensure_future(http.post(ENDPOINT, json=body("start")))
            while not provider.started.is_set():
                assert not first.done(), first.result().text
                await asyncio.sleep(0.01)
            # The player moves on: the client aborts the first request.
            first.cancel()
            with suppress(asyncio.CancelledError):
                await first
            await asyncio.sleep(0.5)
            # The abandoned call still runs: the server did not see the disconnect.
            assert not provider.cancelled.is_set()
            return await http.post(ENDPOINT, json=body("start"))

    with _served(app) as base_url:
        second = asyncio.run(leave_then_explain_again(base_url))
        # Checked before the server stops, since its shutdown also cancels tasks.
        assert provider.cancelled.is_set()
    assert second.status_code == 200, second.text
    assert second.json()["fallback_reason"] == "provider_error"
    assert provider.calls == 2
