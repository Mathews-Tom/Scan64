"""Narrator attempts, accepts and fallbacks per pipeline, and their endpoint (#366)."""

from __future__ import annotations

import threading

import pytest
from chess_lesson_spec import ExplanationClaim
from fastapi.testclient import TestClient

from scan64.api.app import app
from scan64.explanations.assembly import (
    NARRATOR_PIPELINES,
    NarratorCounts,
    NarratorPipeline,
    narrator_counts,
    record_narrator_accepted,
    record_narrator_attempt,
    record_template_fallback,
    resolve_explanation,
    template_fallback_counts,
)
from scan64.explanations.board_lines import COMPARISON_EVIDENCE_ID, resolve_board_lines_explanation
from tests.unit.test_board_lines import _claim as _board_claim
from tests.unit.test_board_lines import _facts as _board_facts
from tests.unit.test_board_lines import _Provider as _BoardProvider
from tests.unit.test_claim_text_grounding import MISSED_CAPTURE, _diagnosis, _FixedProvider


def _delta(
    before: dict[NarratorPipeline, NarratorCounts], pipeline: NarratorPipeline
) -> tuple[int, int, dict[str, int]]:
    after = narrator_counts()[pipeline]
    old = before[pipeline]
    fallbacks = {
        reason: count - old.fallbacks.get(reason, 0)
        for reason, count in after.fallbacks.items()
        if count != old.fallbacks.get(reason, 0)
    }
    return after.attempts - old.attempts, after.accepted - old.accepted, fallbacks


def _unchanged(before: dict[NarratorPipeline, NarratorCounts], pipeline: NarratorPipeline) -> bool:
    return _delta(before, pipeline) == (0, 0, {})


async def _lesson(text: str | None) -> None:
    await resolve_explanation(
        _diagnosis(),
        [MISSED_CAPTURE.evidence],
        MISSED_CAPTURE.fen,
        local_provider=_FixedProvider(text),
        environment={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("You played {played}, but {capture:best}.", (1, 1, {})),
        ("Qh5 was the best move.", (1, 0, {"chess_token_outside_slot": 1})),
        (None, (1, 0, {"provider_error": 1})),
    ],
)
async def test_lesson_pipeline_counts_each_outcome(
    text: str | None, expected: tuple[int, int, dict[str, int]]
) -> None:
    before = narrator_counts()
    await _lesson(text)
    assert _delta(before, "lesson") == expected
    assert _unchanged(before, "board")


@pytest.mark.asyncio
async def test_template_only_resolution_counts_no_attempt() -> None:
    before = narrator_counts()
    await resolve_explanation(
        _diagnosis(), [MISSED_CAPTURE.evidence], MISSED_CAPTURE.fen, environment={}
    )
    await resolve_board_lines_explanation(_board_facts("start", 3), environment={})
    assert _unchanged(before, "lesson")
    assert _unchanged(before, "board")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        (
            (
                _board_claim("{first:line-1} is scored {eval:line-1}.", "line-1", ("e2e4",)),
                _board_claim(
                    "{first:line-2} scores {diff:line-2}.", COMPARISON_EVIDENCE_ID, ("g1f3",)
                ),
            ),
            (1, 1, {}),
        ),
        (
            (_board_claim("Line 1 wins a queen.", "line-1", ("e2e4",)),),
            (1, 0, {"chess_token_outside_slot": 1}),
        ),
        (None, (1, 0, {"provider_error": 1})),
    ],
)
async def test_board_pipeline_counts_each_outcome(
    claims: tuple[ExplanationClaim, ...] | None, expected: tuple[int, int, dict[str, int]]
) -> None:
    before = narrator_counts()
    resolved = await resolve_board_lines_explanation(
        _board_facts("start", 3), local_provider=_BoardProvider(claims), environment={}
    )
    assert resolved.source == ("narrator" if expected[1] else "template")
    assert _delta(before, "board") == expected
    assert _unchanged(before, "lesson")


def test_fallback_rate_is_fallbacks_per_attempt() -> None:
    assert NarratorCounts(0, 0, {}).fallback_rate is None
    counts = NarratorCounts(4, 1, {"provider_error": 1, "unknown_slot": 2})
    assert counts.fallback_total == 3
    assert counts.fallback_rate == 0.75


def test_template_fallback_counts_sum_both_pipelines() -> None:
    before = template_fallback_counts().get("provider_error", 0)
    record_template_fallback("provider_error", "lesson")
    record_template_fallback("provider_error", "board")
    assert template_fallback_counts()["provider_error"] == before + 2


def test_counters_are_thread_safe() -> None:
    before = narrator_counts()

    def work() -> None:
        for _ in range(500):
            record_narrator_attempt("board")
            record_narrator_accepted("board")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert _delta(before, "board") == (4000, 4000, {})


def _client(host: str) -> TestClient:
    return TestClient(app, client=(host, 50000))


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_endpoint_reports_counts_and_rate_per_pipeline(host: str) -> None:
    record_narrator_attempt("lesson")
    record_template_fallback("unknown_slot", "lesson")
    response = _client(host).get("/v1/diagnostics/narrator")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == set(NARRATOR_PIPELINES)
    counts = narrator_counts()
    for pipeline in NARRATOR_PIPELINES:
        expected = counts[pipeline]
        assert body[pipeline] == {
            "attempts": expected.attempts,
            "accepted": expected.accepted,
            "fallbacks": expected.fallback_total,
            "fallback_rate": expected.fallback_rate,
            "fallbacks_by_reason": expected.fallbacks,
            "sentences": expected.sentences,
            "sentence_fallbacks": expected.sentence_fallbacks,
            "facts_dropped": expected.facts_dropped,
        }
    assert body["lesson"]["fallbacks_by_reason"]["unknown_slot"] >= 1
    assert body["lesson"]["fallback_rate"] is not None


@pytest.mark.parametrize("host", ["192.168.1.20", "testclient"])
def test_endpoint_refuses_other_hosts(host: str) -> None:
    response = _client(host).get("/v1/diagnostics/narrator")
    assert response.status_code == 403
