"""Score every position along a lesson's verified lines (#367).

Game analysis calls ``score_verified_lines`` for each lesson it explains,
before the narrator facts are built. Each position no stored search scored is
searched once with ``LINE_NODES`` nodes at MultiPV 1 through the job's batch
adapter (the batch pool, with its same-owner, same-engine reuse). The scores
are stored in the evidence payload as ``line_evaluations``, so the facts read
them like every other stored search and no search runs in the explanation path.

Scoring is best effort: a search that fails (``SEARCH_FAILURES``: timeout,
engine error, engine exit, missing capability) leaves its position unscored, so
the facts state its ``evaluation_after`` as ``unknown``, and the analysis goes
on. The failure is logged by reason code only, never with the evidence.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection, Sequence

import chess

from scan64.explanations.facts import (
    LINE_EVALUATIONS_FIELD,
    LINE_NODES,
    NarratorFactsError,
    line_evaluation_entry,
    parse_played_move,
    unscored_line_positions,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.engine.uci import SEARCH_FAILURES, search_failure_reason
from scan64.providers.stockfish.adapter import EngineAdapter

logger = logging.getLogger(__name__)


async def score_verified_lines(
    adapter: EngineAdapter,
    evidence: Sequence[Evidence],
    evidence_refs: Collection[str],
    fen_before: str,
    *,
    concurrency: int = 1,
    nodes: int = LINE_NODES,
    prefetch: Callable[[Sequence[str]], None] | None = None,
) -> int:
    """Add ``line_evaluations`` to each referenced evidence payload; return the searches run.

    A failed search is counted as run; its position gets no entry (unknown).

    Positions are deduplicated across the referenced items; each item stores
    the scores of the positions on its own lines. Evidence whose lines do not
    replay is left unchanged (the facts builder reports it). ``prefetch``
    receives the FENs before any search, so a caching adapter can load them.
    """
    referenced = [item for item in evidence if item.evidence_id in evidence_refs]
    if not referenced:
        return 0
    before = chess.Board(fen_before)
    try:
        played = parse_played_move(before, referenced[0].payload.get("played_move"))
        per_item = {
            item.evidence_id: unscored_line_positions(before, [item.payload], played)
            for item in referenced
        }
    except NarratorFactsError:
        return 0
    fens = list(dict.fromkeys(fen for item_fens in per_item.values() for fen in item_fens))
    if not fens:
        return 0
    if prefetch is not None:
        prefetch(fens)
    gate = asyncio.Semaphore(max(1, concurrency))
    failures: list[str] = []

    async def search(fen: str) -> dict[str, object] | None:
        async with gate:
            try:
                analysis = await adapter.analyze_position(fen, nodes=nodes, multipv=1)
            except SEARCH_FAILURES as error:
                # The pool has already discarded an engine that exited.
                failures.append(search_failure_reason(error))
                return None
        return line_evaluation_entry(fen, analysis)

    entries = dict(zip(fens, await asyncio.gather(*(search(fen) for fen in fens)), strict=True))
    if failures:
        logger.warning(
            "Line scoring left %d of %d positions unscored: %s",
            len(failures),
            len(fens),
            ",".join(sorted(set(failures))),
        )
    for item in referenced:
        own = [entry for fen in per_item[item.evidence_id] if (entry := entries[fen]) is not None]
        if own:
            # A new dict, so the JSON column records the change.
            item.payload = {**item.payload, LINE_EVALUATIONS_FIELD: own}
    return len(fens)
