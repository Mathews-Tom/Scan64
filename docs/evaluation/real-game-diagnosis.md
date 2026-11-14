# Real-game diagnosis measurement

## M78 preregistration

The existing method is described in `docs/user-guide.md` under **Measure diagnosis
precision offline**. The corpus is the first 40 qualifying games in source order
from the January 2013 Lichess CC0 archive, with error-free parsing, both Elo ratings
600–1600 inclusive, and at least 20 plies. The synthetic reference game is last in
`recorded.json` and is excluded from real-game precision.

The allowed-mate reference will use only the first PV and `score_mate` of the
separate 1,000,000-node, MultiPV-4 source and reply recordings. Scores are from the
side to move: the mover before the played move and the opponent afterward. A
positive reply mate score from 1 through 3, a legal reply PV ending with the mover
checkmated, and a source mate score absent or strictly positive constitute a
reference positive. Source mate scores zero or negative exclude an already forced
mate. An existing avoidable mating threat is not itself exclusionary. No composer
payload, fast analysis, detector predicate, M77 fixture, or human annotation is a
reference input. This is an independent engine-derived operational reference, not
independent coach review or proof of human causality.

Before implementing this reference, a read-only incidence count found 11 eligible
score pairs in the existing 40 real games. All 11 recorded first reply PVs replay
legally to checkmate. This count is not detector support or measured precision.

Every registered code is measured in isolation through the real detector and
arbitration path. Support is TP + FP; precision is TP / (TP + FP). Promotion requires
precision >= 0.80 and support >= 10. The existing declared trusted codes must retain
those gates; trust will not silently shrink. Allowed mate will remain untrusted if
either threshold fails. Record TP, FP, FN, precision, support, and the `--check`
result, including failures.

### Contingent extension rule, fixed before measurement

If initial allowed-mate detector support is below 10, extend **once** to the first
200 qualifying games total from the exact same checksum-verified archive, in
source order with the unchanged inclusion criteria above. Do not select games by
mate score, detector output, label, or outcome, and do not stop early when support
reaches 10. Keep all original 40 recordings byte-for-byte equivalent as JSON data;
insert the next 160 real-game recordings before the unchanged synthetic reference
record. Preserve original engine identity, node limits, MultiPV, thread/hash
settings, and per-position reset policy. Record the selected PGN checksum and
extension provenance. Increase the recording size cap explicitly if the fixed
extension requires it. If the verified source or matching engine is unavailable,
report that concrete blocker rather than substitute a curated source. Do not tune
the detector on these measurements or on tests, fixtures, or M77 data.
