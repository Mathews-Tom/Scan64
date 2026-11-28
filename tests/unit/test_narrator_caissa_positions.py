"""The Caissa positions file loads in the narrator benchmark and builds facts (#389)."""

import json
import tomllib
from pathlib import Path

from benchmarks.narrator.run_benchmark import load_config, load_positions
from scan64.explanations.assembly import grounding_context_for

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "benchmarks/narrator/narrator.toml"


def test_default_positions_file_is_the_recorded_stockfish_file() -> None:
    assert load_config(CONFIG).positions_file == ROOT / "benchmarks/narrator/positions.json"


def test_positions_file_switch_selects_the_caissa_file(tmp_path: Path) -> None:
    text = CONFIG.read_text(encoding="utf-8").replace(
        '# positions_file = "benchmarks/narrator/positions-caissa.json"',
        'positions_file = "benchmarks/narrator/positions-caissa.json"',
    )
    switched = tmp_path / "narrator.toml"
    switched.write_text(text, encoding="utf-8")
    config = load_config(switched)
    assert config.positions_file == ROOT / "benchmarks/narrator/positions-caissa.json"


def test_caissa_positions_keep_reference_ids_and_build_facts() -> None:
    with CONFIG.open("rb") as stream:
        live = tomllib.load(stream)["positions"]["live"]
    path = ROOT / live["file"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    reference = json.loads((ROOT / raw["reference_positions"]).read_text(encoding="utf-8"))
    reference_ids = [item["position_id"] for item in reference["positions"]]
    assert [row["position_id"] for row in raw["comparison"]] == reference_ids
    assert raw["engine"]["threads"] == 1
    assert raw["engine"]["hash_mb"] == 64
    assert len(raw["engine"]["binary_sha256"]) == 64
    positions = load_positions(path)
    assert positions
    assert {position.position_id for position in positions} <= set(reference_ids)
    for position in positions:
        grounding_context_for(position.diagnosis, list(position.evidence), position.fen)
