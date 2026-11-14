# Real-game diagnosis measurement

## M78 source-depth correction: fresh80 preregistration

The original40 measurements below are **development, pre-fix** results, retained
unchanged. They must never support a new allowed-mate trust decision.

Before recording or measuring the corrected detector, freeze a separate holdout:
exactly the next 80 qualifying games after the original first 40, from the same
January 2013 compressed archive, in unchanged source order. Qualification remains
error-free parsing, both Elo ratings 600–1600 inclusive, and at least 20 plies.
Verify the recorder's published compressed-source SHA256 before selection. Preserve
the original corpus and PGN bytes; store fresh80 in separate recording and PGN files.
No hand-selection, outcome filtering, stopping early, or extension is permitted.

Use the same engine identity, 10,000-node fast pass, and independent 1,000,000-node
MultiPV-4 source and reply reference, with the same per-position reset policy.
Corrected composer callers supply the deep source analysis. The existing independent
deep reference definition is unchanged. Record every code's TP, FP, FN, support,
precision, and gate status, plus suppression checks and the combined `--check` result.

Allowed mate is promoted **only** if precision >= 0.80 and support >= 10 on fresh80
alone. Insufficient support or precision means untrusted, with no corpus extension.
The four existing trusted codes retain their original40 gate. Original40 pre-fix
measurements remain historical, not a new post-fix trust sample. No detector tuning,
M77 data, or threshold changes are permitted.

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

### Exact false-positive evidence

All PVs below are the exact first recorded UCI PV, not a reconstructed line or
an example. Ply is one-based; `move_index` is zero-based. Fast searches use
10,000 nodes and the reference searches use 1,000,000 nodes, MultiPV 4. An absent
mate score means that the recording has `score_cp` instead; it is not mate zero.
The fast payload therefore has `before_score_mate = null` in all four cases,
with `after_score_mate` and `mate_in` matching the positive reply score below;
`mating_line` is the fast reply PV.

#### `8dqmtc5l`, ply 126 (`move_index` 125), Black `63...Ke8`

Game: <https://lichess.org/8dqmtc5l>.
Before FEN: `3k4/6K1/4P3/8/4Q3/8/8/8 b - - 2 63`.

- Fast before: `score_mate` absent, `score_cp = -1455`.
  PV: `d8e7 e4g6 e7d6 g6f7 d6e5 e6e7 e5d4 f7f4 d4c5 f4f2 c5b5 e7e8q b5b4`.
- Fast after: `score_mate = +2`.
  PV / mating line: `e4a4 e8d8 a4d7`.
- Deep before: `score_mate = -4`.
  PV: `d8c8 e6e7 c8b8 e7e8q b8a7 e8a8 a7b6 e4c6`.
- Deep after: `score_mate = +2`.
  PV: `e4a4 e8d8 a4d7`.
- Deep allowed-mate label: **negative**. The reply proves mate in two, but
  Black was already forced-mated in four before `Ke8`. The fast source sees a
  losing centipawn score without resolving mate; the independent deeper source
  resolves the pre-existing forced mate, which the preregistered label excludes.

#### `iwdxjr8a`, ply 55 (`move_index` 54), White `28.Qxf7`

Game: <https://lichess.org/iwdxjr8a>.
Before FEN: `1k1r4/ppp2p1Q/3p4/3P4/4P3/P7/2q2Pr1/3RK3 w - - 0 28`.

- Fast before: `score_mate` absent, `score_cp = -1007`.
  PV: `d1d2 c2c1 e1e2 g2f2 e2f2 c1d2 f2g1 d2d4 g1h2`.
- Fast after: `score_mate = +1`.
  PV / mating line: `g2g1`.
- Deep before: `score_mate = -8`.
  PV: `d1d2 c2c4 d2d1 d8e8 a3a4 e8e4 h7e4 c4e4 e1d2 g2f2 d2c3 f2c2 c3b3 e4c4 b3a3 c2a2`.
- Deep after: `score_mate = +1`.
  PV: `g2g1`.
- Deep allowed-mate label: **negative**. `Qxf7` allows immediate checkmate,
  but White was already forced-mated in eight. Fast and deep agree on the
  immediate reply; they differ on whether the source position already has
  forced mate. That deep source exclusion makes the fast detection a false
  positive rather than evidence for a newly allowed avoidable mate.

#### `0sara2m9`, ply 26 (`move_index` 25), Black `13...Rb8`

Game: <https://lichess.org/0sara2m9>.
Before FEN: `r3kbnr/1Q3ppp/p1n1p3/8/2P5/2N1BN2/PP2PPPP/2KR1B1R b kq - 0 13`.

- Fast before: `score_mate` absent, `score_cp = -2176`.
  PV: `c6d4 b7a8 e8e7 f3d4 h7h6 a8a7 e7f6 c3d5 f6g6`.
- Fast after: `score_mate = +1`.
  PV / mating line: `b7d7`.
- Deep before: `score_mate = -6`.
  PV: `c6d4 b7a8 e8e7 d1d4 e6e5 a8d8 e7e6 d8d5 e6f5 d5e5 f5g6 e5g5`.
- Deep after: `score_mate = +1`.
  PV: `b7d7`.
- Deep allowed-mate label: **negative**. White has the same mate-in-one reply
  in both searches, but the source search independently proves Black already
  forced-mated in six. The fast source has only a severe centipawn loss; absence
  of its mate score does not establish that Black could previously avoid mate.

#### `0sara2m9`, ply 32 (`move_index` 31), Black `16...Ne7`

Game: <https://lichess.org/0sara2m9>.
Before FEN: `1Q3bnr/5ppp/p3pk2/8/2P5/2N1BN2/PP2PPPP/2KR1B1R b - - 0 16`.

- Fast before: `score_mate` absent, `score_cp = -2257`.
  PV: `h7h6 b8e5 f6g6 e5e4 f7f5 e4e6 g8f6 f3e5 g6h7 e6f5 h7g8 f5e6 g8h7 f2f4 f8b4 e6f5 h7g8`.
- Fast after: `score_mate = +2`.
  PV / mating line: `b8e5 f6g6 e5g5`.
- Deep before: `score_mate = -6`.
  PV: `f8d6 b8d6 h7h6 e3d4 f6g6 d6g3 g6h5 g3h4 h5g6 h4g4 g6h7 g4g7`.
- Deep after: `score_mate = +2`.
  PV: `b8e5 f6g6 e5g5`.
- Deep allowed-mate label: **negative**. `Ne7` shortens the mating sequence
  to two, but Black was already forced-mated in six before moving. The fast
  source again fails to resolve a mate while the reference does; shortening an
  already forced mate is outside the registered allowed-mate definition.
