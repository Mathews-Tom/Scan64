"""Training examples and held-out positions from the verified pairs of #464 (issue #465).

    uv run python -m benchmarks.narrator.finetune.data --data-dir <dir> --out <outside the repo>

A pair maps all facts of a move to one human sentence. The narrator words one merged
fact per call, so each pair becomes one example on its closest fact: the merged fact with
the fewest failures of the app's own sentence check (#454). ``results/data.json`` records
those failures, the counts and the hashes. The examples stay outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess

from scan64.explanations.phrase_bank import role_problems
from scan64.explanations.role_facts import RoleFact, numbered_san, role_label
from scan64.explanations.voice_pipeline import (
    VOICE_EXAMPLES,
    select_facts,
    voice_prompt,
    voice_system_prompt,
)
from scan64.learning.training_pairs import EngineView, heldout, position_facts

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
Row = dict[str, Any]


def config() -> Row:
    return tomllib.loads((HERE / "finetune.toml").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outside(path: Path) -> Path:
    if path.resolve().is_relative_to(ROOT):
        raise SystemExit(f"{path} is inside the repository")
    return path


def read_jsonl(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def view(record: Row) -> EngineView:
    threat = record["threat_line"]
    return EngineView(
        best_line=tuple(record["best_line"]),
        played_line=tuple(record["played_line"]),
        threat_line=None if threat is None else tuple(threat),
        best_score_cp=record["best_score_cp"],
        played_score_cp=record["played_score_cp"],
        best_mate=record["best_mate"],
        played_mate=record["played_mate"],
    )


def facts_of(game: Row, ply: int, engine: dict[tuple[str, str], Row]) -> Row:
    """The app's role facts for the move at ``ply``, from the cached engine analysis."""
    board = chess.Board()
    for san in game["sans"][: ply - 1]:
        board.push_san(san)
    move = board.parse_san(game["sans"][ply - 1])
    record = engine[board.fen(), move.uci()]
    return {
        "fen": board.fen(),
        "played": move.uci(),
        "mover": board.turn,
        "numbered": numbered_san(board, move),
        "facts": position_facts(game["sans"], ply, view(record)).role,
        "record": record,
    }


def heldout_positions(data_dir: Path) -> Iterator[Row]:
    """Every commented move of the held-out games."""
    engine = {(r["fen"], r["move"]): r for r in read_jsonl(data_dir / "pairs" / "engine.jsonl")}
    settings = config()["data"]
    for game in read_jsonl(data_dir / "corpus" / "games.jsonl"):
        if game["source"] not in settings["sources"] or not heldout(
            game["source"], game["number"], settings["split_every"]
        ):
            continue
        for comment in game["comments"]:
            if comment["ply"] > 0:
                entry = facts_of(game, comment["ply"], engine)
                yield {
                    "test": f"{game['source']}-{game['number']}-{comment['ply']}",
                    **entry,
                }


def closest_fact(sentence: str, facts: tuple[RoleFact, ...]) -> tuple[RoleFact, list[str]]:
    """The merged fact with the fewest failed checks for the sentence, and those failures."""
    merged, _ = select_facts(facts)
    ranked = [(fact, role_problems(sentence, fact)) for fact in merged]
    return min(ranked, key=lambda item: len(item[1]))


def example(fact: RoleFact, sentence: str, mover: bool) -> Row:
    prompt = voice_prompt(role_label(fact.role, mover), fact.text)
    system = voice_system_prompt("White" if mover else "Black")
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": sentence},
        ]
    }


def build(data_dir: Path, out: Path) -> Row:
    settings = config()["data"]
    games = {(g["source"], g["number"]): g for g in read_jsonl(data_dir / "corpus/games.jsonl")}
    engine = {(r["fen"], r["move"]): r for r in read_jsonl(data_dir / "pairs" / "engine.jsonl")}
    pairs = read_jsonl(data_dir / "pairs" / "pairs.jsonl")
    counts = {"pairs": len(pairs), "heldout": 0, "licence": 0, "examples": 0, "pass_app_check": 0}
    examples, used = [], []
    for pair in pairs:
        entry = facts_of(games[pair["source"], pair["game"]], pair["ply"], engine)
        where = f"{pair['source']}-{pair['game']}-{pair['ply']}"
        if heldout(pair["source"], pair["game"], settings["split_every"]):
            counts["heldout"] += 1
            continue
        if pair["source"] not in settings["sources"]:
            counts["licence"] += 1
            continue
        fact, problems = closest_fact(pair["sentence"], entry["facts"])
        counts["examples"] += 1
        counts["pass_app_check"] += not problems
        used.append(
            {
                "pair": where,
                "sentence": pair["sentence"],
                "fact": fact.text,
                "failed_checks": problems,
            }
        )
        examples.append(example(fact, pair["sentence"], entry["mover"]))
    out.write_text("".join(json.dumps(e) + "\n" for e in examples), encoding="utf-8")
    files = ["corpus/games.jsonl", "pairs/engine.jsonl", "pairs/pairs.jsonl"]
    return {
        "counts": counts,
        "pairs": used,
        "sha256": {name: sha256(data_dir / name) for name in files} | {"train.jsonl": sha256(out)},
        "engine": json.loads((data_dir / "pairs/engine-settings.json").read_text())[
            "binary_sha256"
        ],
        "prompt_examples": len(VOICE_EXAMPLES),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.data_dir, outside(args.out))
    path = HERE / "results" / "data.json"
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
