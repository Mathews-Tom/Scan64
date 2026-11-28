"""The calibration positions record the code and engine that produced them (#410, rule 6)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from benchmarks.factor_calibration.record_positions import (
    REPO_ROOT,
    code_provenance,
    config_sha256,
    engine_manifest,
    search_config,
)

POSITIONS = REPO_ROOT / "benchmarks" / "factor_calibration" / "positions.json"
GAMES = REPO_ROOT / "benchmarks" / "factor_calibration" / "games.json"
SHA256 = re.compile(r"[0-9a-f]{64}")


def test_engine_manifest_names_embedded_and_file_networks() -> None:
    identity = {
        "engine_name": "Stockfish 18",
        "engine_version": "sha256:" + "a" * 64,
        "network": json.dumps({"EvalFileSmall": "b" * 64}),
        "options": {"EvalFile": "nn-big.nnue", "EvalFileSmall": "nn-small.nnue", "Hash": 16},
    }
    assert engine_manifest(identity) == {
        "binary_sha256": "a" * 64,
        "network": {
            "EvalFile": {"name": "nn-big.nnue", "sha256": "embedded"},
            "EvalFileSmall": {"name": "nn-small.nnue", "sha256": "b" * 64},
        },
    }


def test_engine_manifest_without_a_binary_digest_records_none() -> None:
    assert engine_manifest({"engine_version": None, "network": None, "options": {}}) == {
        "binary_sha256": None,
        "network": {},
    }


def test_code_provenance_records_commit_dirty_and_digests(tmp_path: Path) -> None:
    provenance = code_provenance(GAMES, tmp_path / "positions.json")
    assert re.fullmatch(r"[0-9a-f]{40}", provenance["commit"])
    assert isinstance(provenance["dirty"], bool)
    assert provenance["script"] == "benchmarks/factor_calibration/record_positions.py"
    for key in ("script_sha256", "games_sha256", "config_sha256"):
        assert SHA256.fullmatch(provenance[key])
    assert provenance["config_sha256"] == config_sha256(search_config())


def test_committed_positions_record_their_provenance() -> None:
    data = json.loads(POSITIONS.read_text(encoding="utf-8"))
    provenance = data["provenance"]
    assert re.fullmatch(r"[0-9a-f]{40}", provenance["commit"])
    # Recorded after the run: the dirty flag is unknown (null), never a guessed bool or text.
    assert provenance["dirty"] is None
    assert "not recorded at the time" in provenance["recorded"]
    # The search settings the file states are the ones its config digest covers.
    assert provenance["config_sha256"] == config_sha256(search_config())
    assert {key: data["engine"][key] for key in search_config()["engine"]} == (
        search_config()["engine"]
    )
    assert data["selection"] == search_config()["selection"]
    assert SHA256.fullmatch(data["engine"]["binary_sha256"])
    assert set(data["engine"]["network"]) == {"EvalFile", "EvalFileSmall"}
