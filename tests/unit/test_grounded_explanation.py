"""Unit coverage for resolve_explanation (M35 PR-4): the LLM path validated
before display, always falling back to the template floor on rejection.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from chess_lesson_spec import Diagnosis

from scan64.explanations.assembly import resolve_explanation
from scan64.learning.evidence.models import Evidence

_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _diagnosis_and_evidence() -> tuple[Diagnosis, list[Evidence]]:
    evidence = Evidence(
        evidence_id="ev_1",
        kind="missed_tactic",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="the fast-pass principal variation exposes a tactical opportunity",
        payload={
            "tactic_type": "knight_fork",
            "fork_square": "e7",
            "targets": [{"square": "d5", "piece": "q"}],
            "results_in_material_gain": True,
            "played_move": "e2e3",
            "best_move": "g1f3",
            "focused_line": ["g1f3"],
        },
    )
    diagnosis = Diagnosis(primary="tactics.fork.knight", confidence=0.9, evidence_refs=["ev_1"])
    return diagnosis, [evidence]


def _write_toml(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / "llm.toml"
    config_path.write_text(content)
    return config_path


def _mocked_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_default_disabled_llm_renders_the_template() -> None:
    diagnosis, evidence = _diagnosis_and_evidence()

    explanation = await resolve_explanation(diagnosis, evidence, _FEN, environment={})

    assert "e7" in explanation.text
    assert "d5" in explanation.text


@pytest.mark.asyncio
async def test_explicit_template_configuration_renders_the_template(tmp_path: Path) -> None:
    diagnosis, evidence = _diagnosis_and_evidence()
    config_path = _write_toml(tmp_path, '[llm]\nprovider = "template"\n')

    explanation = await resolve_explanation(
        diagnosis,
        evidence,
        _FEN,
        environment={"SCAN64_LLM_CONFIG": str(config_path)},
    )

    assert "e7" in explanation.text


@pytest.mark.asyncio
async def test_grounded_generation_replaces_the_template(tmp_path: Path) -> None:
    diagnosis, evidence = _diagnosis_and_evidence()
    config_path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "ollama"\nmodel = "local"\nbase_url = "http://localhost:11434"\n',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "claims": [
                                {
                                    "text": "A knight fork on e7 wins your queen on d5.",
                                    "evidence_ref": "ev_1",
                                    "line": ["g1f3"],
                                    "certainty": "observed",
                                    "disclosure_level": 1,
                                }
                            ]
                        }
                    )
                }
            },
        )

    async with _mocked_client(handler) as client:
        explanation = await resolve_explanation(
            diagnosis,
            evidence,
            _FEN,
            environment={"SCAN64_LLM_CONFIG": str(config_path)},
            client=client,
        )

    assert explanation.text == "A knight fork on e7 wins your queen on d5."
    assert explanation.claims[0].evidence_ref == "ev_1"


@pytest.mark.asyncio
async def test_ungrounded_generation_falls_back_to_the_template(tmp_path: Path) -> None:
    diagnosis, evidence = _diagnosis_and_evidence()
    config_path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "ollama"\nmodel = "local"\nbase_url = "http://localhost:11434"\n',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "claims": [
                                {
                                    "text": "Your queen on h4 was hanging.",
                                    "evidence_ref": "ev_unknown",
                                    "line": [],
                                    "certainty": "observed",
                                    "disclosure_level": 1,
                                }
                            ]
                        }
                    )
                }
            },
        )

    async with _mocked_client(handler) as client:
        explanation = await resolve_explanation(
            diagnosis,
            evidence,
            _FEN,
            environment={"SCAN64_LLM_CONFIG": str(config_path)},
            client=client,
        )

    assert "e7" in explanation.text
    assert "d5" in explanation.text
    assert explanation.text != "Your queen on h4 was hanging."


@pytest.mark.asyncio
async def test_a_malformed_generation_falls_back_to_the_template(tmp_path: Path) -> None:
    diagnosis, evidence = _diagnosis_and_evidence()
    config_path = _write_toml(
        tmp_path,
        '[llm]\nprovider = "ollama"\nmodel = "local"\nbase_url = "http://localhost:11434"\n',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json"}})

    async with _mocked_client(handler) as client:
        explanation = await resolve_explanation(
            diagnosis,
            evidence,
            _FEN,
            environment={"SCAN64_LLM_CONFIG": str(config_path)},
            client=client,
        )

    assert "e7" in explanation.text
