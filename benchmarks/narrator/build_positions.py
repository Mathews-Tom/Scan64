"""Build the fixed narrator benchmark positions from recorded real-game corpora.

Usage:
    uv run python -m benchmarks.narrator.build_positions benchmarks/narrator/narrator.toml

Each position is a diagnosed mistake produced by Scan64's own pipeline on a
recorded game: ``FastPassOrchestrator`` -> ``compose_candidate_evidence`` ->
every registered pattern detector -> ``arbitrate_diagnoses`` (trusted codes
only). The recorded million-node searches in ``tests/fixtures/m48`` replace the
live engine (``RecordedAdapter``), so no engine runs and the output is
byte-for-byte reproducible. Composer evidence IDs are random UUIDs; they are
renamed to ``<position_id>_ev<N>`` so the file is stable. Engine analysis IDs
are random UUIDs too; each is replaced by a UUID5 of the position ID and its
order of first use (as ``build_positions_live`` does), so ``positions_sha256``
in a runs header is the same after every rebuild.

Selection is deterministic: corpora in config order, games and candidates in
recorded order, at most ``per_code_cap`` positions per trusted code and ``per_game_cap``
per game, until
``count`` positions are collected.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tomllib
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import (
    CandidatePosition,
    FastPassConfig,
    FastPassOrchestrator,
)
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evaluation.real_games import RecordedAdapter
from scan64.learning.evidence.composer import compose_candidate_evidence, needs_mate_source
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def stable_uuids(record: dict[str, Any], position_id: str) -> dict[str, Any]:
    """Replace every random UUID in ``record`` with a UUID5 of ``position_id``.

    The N-th distinct UUID becomes ``uuid5(NAMESPACE_URL, f"{position_id}#{N}")``,
    the same derivation ``build_positions_live`` uses for the Caissa file.
    """
    text = json.dumps(record, sort_keys=True)
    mapping: dict[str, str] = {}
    for found in UUID_PATTERN.findall(text):
        if found not in mapping:
            mapping[found] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{position_id}#{len(mapping)}"))
    result: dict[str, Any] = json.loads(UUID_PATTERN.sub(lambda m: mapping[m.group(0)], text))
    return result


def _detectors() -> tuple[PatternDetector, ...]:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    detectors: list[PatternDetector] = []
    for name in registry.names(kind=PluginKind.PATTERN_DETECTOR):
        detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name)
        if not isinstance(detector, PatternDetector):
            raise RuntimeError(f"Registered detector {name!r} violates the plugin contract")
        detectors.append(detector)
    return tuple(detectors)


async def diagnose_candidate(
    *,
    corpus_path: Path,
    game: dict[str, Any],
    fens: list[str],
    candidate: CandidatePosition,
    focused_analysis: EngineAnalysis,
    mate_source_analysis: EngineAnalysis | None,
    detectors: tuple[PatternDetector, ...],
) -> dict[str, Any] | None:
    """Compose evidence, detect and arbitrate one fast-pass candidate, as the job does.

    Returns the benchmark position record, or ``None`` without a trusted diagnosis.
    """
    index = candidate.move_index
    before = chess.Board(fens[index])
    evidence = compose_candidate_evidence(
        before_board=before,
        after_board=chess.Board(candidate.fen),
        history_san=game["moves"][: index + 1],
        initial_fen=None,
        position_id="recorded",
        fast_analysis=candidate.before_analysis,
        reply_analysis=candidate.after_analysis,
        focused_analysis=focused_analysis,
        mate_source_analysis=mate_source_analysis,
        played_move=game["moves"][index],
        swing_cp=candidate.swing_cp,
    )
    opportunity = LearningOpportunity(
        opportunity_id="recorded",
        position_id="recorded",
        player_id="recorded",
        played_move=game["moves"][index],
        engine_eval_before=0.0,
        engine_eval_after=-(candidate.swing_cp / 100.0),
    )
    detected = []
    for detector in detectors:
        detected.extend(
            await detector.detect(opportunity, evidence, PlayerContext(player_id="recorded"))
        )
    selection = arbitrate_diagnoses(detected, evidence)
    if selection is None:
        return None
    best, secondary = selection
    position_id = f"{corpus_path.stem}:{game['id']}:{index}"
    renamed = {
        item.evidence_id: f"{position_id}_ev{number}" for number, item in enumerate(evidence)
    }
    record = {
        "position_id": position_id,
        "corpus": corpus_path.relative_to(REPO_ROOT).as_posix(),
        "game_id": game["id"],
        "move_index": index,
        "fen": before.fen(),
        "played_move": game["moves"][index],
        "diagnosis": {
            "primary": best.skill_id,
            "secondary": [item.skill_id for item in secondary],
            "confidence": best.confidence,
            "evidence_refs": [renamed[ref] for ref in best.evidence_ids],
        },
        # Only referenced items: the explanation pipeline reads no others.
        "evidence": [
            {**item.model_dump(), "evidence_id": renamed[item.evidence_id]}
            for item in evidence
            if item.evidence_id in best.evidence_ids
        ],
    }
    return stable_uuids(record, position_id)


async def _corpus_positions(
    corpus_path: Path, detectors: tuple[PatternDetector, ...]
) -> list[dict[str, Any]]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    found: list[dict[str, Any]] = []
    for game in corpus["games"][: corpus["provenance"]["game_count"]]:
        adapter = RecordedAdapter(game)
        fast_pass = await FastPassOrchestrator(
            adapter, FastPassConfig(nodes=10_000, swing_threshold_cp=150)
        ).run_fast_pass(game["moves"])
        for candidate in fast_pass.candidates:
            index = candidate.move_index
            mate_source = None
            if needs_mate_source(candidate.after_analysis):
                mate_source = EngineAnalysis(
                    config={"nodes": 1_000_000, "multipv": 4},
                    raw_result=game["deep_before"][str(index)],
                )
            position = await diagnose_candidate(
                corpus_path=corpus_path,
                game=game,
                fens=adapter.fens,
                candidate=candidate,
                focused_analysis=EngineAnalysis(
                    config={"nodes": 1_000_000, "multipv": 4},
                    raw_result=game["focused"][str(index)],
                ),
                mate_source_analysis=mate_source,
                detectors=detectors,
            )
            if position is not None:
                found.append(position)
    return found


async def build(config_path: Path) -> Path:
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)
    spec = raw["positions"]
    count = int(spec["count"])
    cap = int(spec["per_code_cap"])
    game_cap = int(spec["per_game_cap"])
    output: Path = REPO_ROOT / str(spec["file"])
    detectors = _detectors()
    selected: list[dict[str, Any]] = []
    per_code: Counter[str] = Counter()
    per_game: Counter[str] = Counter()
    for corpus in spec["corpora"]:
        for position in await _corpus_positions(REPO_ROOT / corpus, detectors):
            code = position["diagnosis"]["primary"]
            game_key = f"{position['corpus']}:{position['game_id']}"
            if per_code[code] >= cap or per_game[game_key] >= game_cap:
                continue
            per_code[code] += 1
            per_game[game_key] += 1
            selected.append(position)
            if len(selected) == count:
                break
        if len(selected) == count:
            break
    if len(selected) != count:
        raise RuntimeError(
            f"Only {len(selected)} trusted diagnoses found; need {count} "
            f"(per code {dict(per_code)})"
        )
    output.write_text(
        json.dumps(
            {
                "source": "recorded real-game corpora, Scan64 diagnosis pipeline",
                "corpora": spec["corpora"],
                "per_code": dict(sorted(per_code.items())),
                "positions": selected,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    output = asyncio.run(build(args.config))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
