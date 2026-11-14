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

## M78 measured result

Preregistration commit: `334f70a` (DCO signed).

Command: `BROWSER=/usr/bin/true uv run python -m
scan64.learning.evaluation.real_games --check`.
The recorded-only measurement completed with exit status **0**: all four declared
trusted codes pass. No live engine was launched. The detector was measured once
after its implementation appeared; it was not tuned against these results.

| Code | Precision | Support | TP | FP | FN | Meets trust gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `board_awareness.hanging_piece` | 0.896 | 77 | 69 | 8 | 12 | yes |
| `threat_processing.missed_check` | 0.867 | 30 | 26 | 4 | 11 | yes |
| `threat_processing.missed_capture` | 0.862 | 65 | 56 | 9 | 25 | yes |
| `threat_processing.missed_direct_threat` | 0.893 | 121 | 108 | 13 | 25 | yes |
| `tactics.fork.knight` | 1.000 | 5 | 5 | 0 | 5 | no |
| `tactics.pin` | 0.056 | 36 | 2 | 34 | 0 | no |
| `tactics.overloaded_defender` | 0.048 | 84 | 4 | 80 | 0 | no |
| `calculation.stopped_too_early` | 0.342 | 225 | 77 | 148 | 36 | no |
| `opening.delayed_development` | 0.119 | 67 | 8 | 59 | 3 | no |
| `positional.king_safety_neglect` | 0.407 | 81 | 33 | 48 | 12 | no |
| `threat_processing.allowed_mate` | **0.733** | **15** | **11** | **4** | **0** | **no** |

**Decision: allowed mate remains untrusted.** Its support passes, but precision
11/15 falls below 0.80. `diagnosis/trust.py` remains unchanged. This result cannot
justify trusted lessons, profile credit, or trusted explanations for this code.
The contingent extension was not triggered because support is already >= 10;
therefore the original 40 games, their recordings, and the final synthetic game
remain unchanged. The existing recorder and report path need no corpus expansion.

Read-only attribution of the four disagreements found that the fast source
search missed a pre-existing forced mate which the deeper source search found.
Indices below are zero-based and scores are side-to-move:

| Game | Move index | Fast source / reply mate | Deep source / reply mate |
| --- | ---: | --- | --- |
| `https://lichess.org/8dqmtc5l` | 125 | absent / +2 | -4 / +2 |
| `https://lichess.org/iwdxjr8a` | 54 | absent / +1 | -8 / +1 |
| `https://lichess.org/0sara2m9` | 25 | absent / +1 | -6 / +1 |
| `https://lichess.org/0sara2m9` | 31 | absent / +2 | -6 / +2 |

These examples explain the rejection; they are not tuning inputs. No M77 fixtures
were read. Added oracle unit coverage has not been run here; the owning agent
performs verification.
