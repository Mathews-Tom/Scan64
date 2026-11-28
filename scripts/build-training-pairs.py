"""Build verified training pairs from the annotated corpus (issue #464).

For each comment of ``<data-dir>/corpus/games.jsonl`` (#463), the engine
analyses the game move, the live narrator's code computes the facts, and each
sentence of the comment is kept, trimmed or dropped
(``scan64.learning.training_pairs``). Writes to ``<data-dir>/pairs``:

- ``engine.jsonl``: one analysis per move, appended as it is made, so a
  stopped build resumes where it stopped;
- ``verdicts.jsonl``: every sentence with its outcome and reason;
- ``pairs.jsonl``: the kept and trimmed sentences, facts -> sentence, with the
  source, licence and split;
- ``audit-sample.jsonl``: the sample for the blind judge.

The report (counts per source and reason, unmatched terms by frequency) is
written to the path in ``scripts/training-pairs.toml``.

    uv run python scripts/build-training-pairs.py --data-dir D:/scan64-corpus
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import tomllib
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import chess.engine

from scan64.learning.training_pairs import (
    MATE_CP,
    REASONS,
    EngineView,
    heldout,
    judge,
    position_facts,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scripts" / "training-pairs.toml"
CORPUS_CONFIG = ROOT / "scripts" / "annotated-corpus.toml"
logger = logging.getLogger("build-training-pairs")


def _score(info: chess.engine.InfoDict, colour: chess.Color) -> tuple[int, bool]:
    score = info.get("score")
    if score is None:
        raise RuntimeError("the engine gave no score")
    pov = score.pov(colour)
    return pov.score(mate_score=MATE_CP), pov.is_mate()


def _pv(info: chess.engine.InfoDict) -> tuple[str, ...]:
    return tuple(move.uci() for move in info.get("pv", ()))


def analyse(
    engine: chess.engine.SimpleEngine, board: chess.Board, move: chess.Move, nodes: int
) -> dict[str, Any]:
    """The engine's view of ``move`` in ``board``, as a JSON record."""
    limit = chess.engine.Limit(nodes=nodes)
    mover = board.turn
    best = engine.analyse(board, limit)
    best_line = _pv(best)
    if not best_line:
        raise RuntimeError(f"no best line in {board.fen()}")
    best_score, best_mate = _score(best, mover)
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        played_line: tuple[str, ...] = ()
        played_score, played_mate = MATE_CP, True
    elif after.is_game_over(claim_draw=False):
        played_line, played_score, played_mate = (), 0, False
    else:
        reply = engine.analyse(after, limit)
        played_line = _pv(reply)
        played_score, played_mate = _score(reply, mover)
    threat_line: tuple[str, ...] | None = None
    if not board.is_check():
        # The position with the other side to move, sent as a FEN: a null move
        # in the history is not sent to a UCI engine.
        passed = chess.Board(board.fen())
        passed.turn = not passed.turn
        passed.ep_square = None
        if not passed.is_game_over(claim_draw=False):
            threat_line = _pv(engine.analyse(passed, limit))
    return {
        "fen": board.fen(),
        "move": move.uci(),
        "best_line": list(best_line),
        "played_line": list(played_line),
        "threat_line": None if threat_line is None else list(threat_line),
        "best_score_cp": best_score,
        "played_score_cp": played_score,
        "best_mate": best_mate,
        "played_mate": played_mate,
    }


def _view(record: dict[str, Any]) -> EngineView:
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


def _report(
    counts: dict[str, Counter[str]],
    reasons: dict[str, Counter[str]],
    terms: Counter[str],
    titles: dict[str, str],
    totals: dict[str, int],
    split_games: dict[str, Counter[str]],
) -> str:
    lines = [
        "# Verified training pairs",
        "",
        f"Built {datetime.now(UTC).date().isoformat()} by `scripts/build-training-pairs.py` "
        "from the annotated corpus (#463), issue #464. Settings: `scripts/training-pairs.toml`.",
        "The pairs themselves are written outside the repository.",
        "",
        "| Source | Games (held out) | Sentences | Kept | Trimmed | Dropped | Held-out pairs |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for source, outcome in sorted(counts.items()):
        total = sum(outcome.values())
        games = split_games[source]
        lines.append(
            f"| {titles[source]} | {games['train'] + games['heldout']} ({games['heldout']}) | "
            f"{total} | {outcome['kept']} | {outcome['trimmed']} | "
            f"{outcome['dropped']} | {totals.get(source, 0)} |"
        )
    lines += ["", "## Dropped and trimmed, by reason", ""]
    lines += ["| Reason | " + " | ".join(titles[s] for s in sorted(reasons)) + " |"]
    lines += ["|---|" + "---:|" * len(reasons)]
    for reason in REASONS:
        row = [str(reasons[source][reason]) for source in sorted(reasons)]
        if any(value != "0" for value in row):
            lines.append(f"| {reason} | " + " | ".join(row) + " |")
    lines += [
        "",
        "A trimmed sentence is counted under the reason its cut part failed.",
        "",
        "## Unmatched chess terms",
        "",
        "Terms a sentence names that no fact for its position backs, by number of sentences. "
        "This ranking feeds the next concepts in #459.",
        "",
        "| Term | Sentences |",
        "|---|---:|",
    ]
    lines += [f"| {term} | {count} |" for term, count in terms.most_common()]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    sources = tomllib.loads(CORPUS_CONFIG.read_text(encoding="utf-8"))["sources"]
    games_path = args.data_dir / "corpus" / "games.jsonl"
    if not games_path.is_file():
        raise FileNotFoundError(f"{games_path}: build the corpus first (#463)")
    out_dir = args.data_dir / "pairs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(out_dir / f"build_training_pairs_{stamp}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    engine_path = out_dir / "engine.jsonl"
    cached: dict[tuple[str, str], dict[str, Any]] = {}
    if engine_path.is_file():
        for line in engine_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            cached[(record["fen"], record["move"])] = record
    logger.info("Resuming with %d cached analyses", len(cached))
    games = [json.loads(line) for line in games_path.read_text(encoding="utf-8").splitlines()]

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    terms: Counter[str] = Counter()
    heldout_pairs: Counter[str] = Counter()
    split_games: dict[str, Counter[str]] = defaultdict(Counter)
    pairs: list[dict[str, Any]] = []
    settings = config["engine"]
    with (
        chess.engine.SimpleEngine.popen_uci(settings["command"]) as engine,
        engine_path.open("a", encoding="utf-8") as engine_out,
        (out_dir / "verdicts.jsonl").open("w", encoding="utf-8") as verdicts_out,
    ):
        engine.configure({"Threads": settings["threads"], "Hash": settings["hash_mb"]})
        for game in games:
            source, number = game["source"], game["number"]
            split = "heldout" if heldout(source, number, config["split"]["every"]) else "train"
            split_games[source][split] += 1
            for comment in game["comments"]:
                ply = comment["ply"]
                if ply == 0:
                    for sentence in comment["sentences"]:
                        counts[source]["dropped"] += 1
                        reasons[source]["no move before the comment"] += 1
                    continue
                board = chess.Board()
                for san in game["sans"][: ply - 1]:
                    board.push_san(san)
                move = board.parse_san(game["sans"][ply - 1])
                key = (board.fen(), move.uci())
                if key not in cached:
                    cached[key] = analyse(engine, board, move, settings["nodes"])
                    engine_out.write(json.dumps(cached[key]) + "\n")
                    engine_out.flush()
                view = _view(cached[key])
                facts = position_facts(game["sans"], ply, view)
                for index, sentence in enumerate(comment["sentences"]):
                    verdict = judge(sentence, facts, view)
                    counts[source][verdict.outcome] += 1
                    if verdict.reason is not None:
                        reasons[source][verdict.reason] += 1
                    terms.update(set(verdict.unmatched_terms))
                    record = {
                        "source": source,
                        "game": number,
                        "ply": ply,
                        "sentence_index": index,
                        "outcome": verdict.outcome,
                        "reason": verdict.reason,
                        "original": sentence,
                        "text": verdict.text,
                    }
                    verdicts_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    if verdict.text is None:
                        continue
                    if split == "heldout":
                        heldout_pairs[source] += 1
                    pairs.append(
                        {
                            "split": split,
                            "source": source,
                            "licence": sources[source]["licence"],
                            "game": number,
                            "ply": ply,
                            "fen": facts.fen_before,
                            "played": facts.played,
                            "facts": [
                                {"role": fact.role, "text": fact.text} for fact in facts.role
                            ],
                            "concepts": [
                                {"concept": fact.concept, "text": fact.text}
                                for fact in facts.concepts
                            ],
                            "sentence": verdict.text,
                            "original": sentence,
                            "outcome": verdict.outcome,
                        }
                    )
            logger.info("%s game %s: %d pairs so far", source, number, len(pairs))

    with (out_dir / "pairs.jsonl").open("w", encoding="utf-8") as pairs_out:
        for pair in pairs:
            pairs_out.write(json.dumps(pair, ensure_ascii=False) + "\n")
    audit = config["audit"]
    sample = random.Random(audit["seed"]).sample(pairs, min(audit["sample"], len(pairs)))
    with (out_dir / "audit-sample.jsonl").open("w", encoding="utf-8") as audit_out:
        for pair in sample:
            audit_out.write(json.dumps(pair, ensure_ascii=False) + "\n")
    titles = {source: sources[source]["title"] for source in counts}
    report = ROOT / config["output"]["report"]
    audit_line = (
        f"Audit sample for the blind judge: {len(sample)} pairs "
        f"(target {audit['sample']}, seed {audit['seed']})."
    )
    body = _report(counts, reasons, terms, titles, heldout_pairs, split_games)
    marker = "\n## Dropped and trimmed"
    body = body.replace(marker, f"\n{audit_line}\n{marker}", 1)
    report.write_text(body, encoding="utf-8")
    logger.info("%d pairs; %d in the audit sample; report %s", len(pairs), len(sample), report)


if __name__ == "__main__":
    main()
