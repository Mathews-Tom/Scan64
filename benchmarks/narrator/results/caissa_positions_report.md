# Caissa evidence on the 50 narrator benchmark positions (#389)

Engine: Caissa 2.0 BMI2 (`caissa-2.0`), binary sha256 `e6d59548829a81b720b9c45a5a7826da32d1433718850960a68e706ab526c551`, network `eval-ml-8-152B-spsa.pnn` sha256 `ee9f6bbb0ef6cd1f38a13cf095340968c2ad18d0fa1359d493043e61acc829fa`. Threads 1, Hash 64 MB.
Budgets: fast pass 10,000 nodes; focused (after the move) 1,000,000 nodes MultiPV 4; before the move (mate source) 1,000,000 nodes MultiPV 4. Reference: `benchmarks/narrator/positions.json` (recorded Stockfish searches, same budgets).
Output: `benchmarks/narrator/positions-caissa.json`, built by `benchmarks/narrator/build_positions_live.py`.

## Diagnosis

| Outcome | Positions |
| --- | --- |
| Flagged by the fast pass (swing > 150 cp) | 43 of 50 |
| Diagnosed as a mistake (trusted code) | 32 of 50 |
| Same primary code as Stockfish | 32 of 50 |
| NarratorFacts builds | 32 of 32 diagnosed |
| Written to positions-caissa.json | 32 of 50 |

Primary code per position class (Stockfish code: Caissa result):

| Stockfish code | Positions | Caissa same code | Other code | No diagnosis | Not flagged |
| --- | --- | --- | --- | --- | --- |
| `board_awareness.hanging_piece` | 9 | 8 | 0 | 0 | 1 |
| `tactics.fork.knight` | 5 | 5 | 0 | 0 | 0 |
| `threat_processing.allowed_mate` | 9 | 9 | 0 | 0 | 0 |
| `threat_processing.missed_capture` | 9 | 6 | 0 | 2 | 1 |
| `threat_processing.missed_check` | 9 | 1 | 0 | 4 | 4 |
| `threat_processing.missed_direct_threat` | 9 | 3 | 0 | 5 | 1 |

Positions without a Caissa diagnosis (shortfall):

- `recorded:https://lichess.org/6x5nq6qd:57` (threat_processing.missed_direct_threat): not_flagged, Caissa swing None cp, Stockfish swing 369 cp; Caissa fast pass before/after -623 cp/668 cp
- `recorded:https://lichess.org/7rzcutsf:4` (threat_processing.missed_direct_threat): no_diagnosis, Caissa swing 269 cp, Stockfish swing 250 cp; Caissa fast pass before/after 69 cp/200 cp
- `recorded:https://lichess.org/7rzcutsf:5` (threat_processing.missed_direct_threat): no_diagnosis, Caissa swing 243 cp, Stockfish swing 201 cp; Caissa fast pass before/after 200 cp/43 cp
- `recorded:https://lichess.org/z2ncoii6:37` (board_awareness.hanging_piece): not_flagged, Caissa swing None cp, Stockfish swing 298 cp; Caissa fast pass before/after -668 cp/643 cp
- `recorded:https://lichess.org/mntamgdj:20` (threat_processing.missed_direct_threat): no_diagnosis, Caissa swing 192 cp, Stockfish swing 291 cp; Caissa fast pass before/after 366 cp/-174 cp
- `recorded:https://lichess.org/ufcqmfxx:34` (threat_processing.missed_check): no_diagnosis, Caissa swing 279 cp, Stockfish swing 402 cp; Caissa fast pass before/after 251 cp/28 cp
- `recorded:https://lichess.org/fiv37vzi:42` (threat_processing.missed_direct_threat): no_diagnosis, Caissa swing 199 cp, Stockfish swing 333 cp; Caissa fast pass before/after 588 cp/-389 cp
- `recorded:https://lichess.org/descpsmi:82` (threat_processing.missed_check): not_flagged, Caissa swing None cp, Stockfish swing 8859 cp; Caissa fast pass before/after 22846 cp/-22730 cp
- `recorded:https://lichess.org/61wsdfp4:30` (threat_processing.missed_direct_threat): no_diagnosis, Caissa swing 383 cp, Stockfish swing 240 cp; Caissa fast pass before/after -189 cp/572 cp
- `recorded:https://lichess.org/7hvhc1t7:16` (threat_processing.missed_capture): no_diagnosis, Caissa swing 345 cp, Stockfish swing 479 cp; Caissa fast pass before/after 214 cp/131 cp
- `recorded:https://lichess.org/y3m3quag:35` (threat_processing.missed_check): not_flagged, Caissa swing None cp, Stockfish swing 532 cp; Caissa fast pass before/after 665 cp/-663 cp
- `recorded:https://lichess.org/8dqmtc5l:26` (threat_processing.missed_check): no_diagnosis, Caissa swing 360 cp, Stockfish swing 439 cp; Caissa fast pass before/after 215 cp/145 cp
- `recorded:https://lichess.org/8dqmtc5l:28` (threat_processing.missed_capture): no_diagnosis, Caissa swing 249 cp, Stockfish swing 449 cp; Caissa fast pass before/after 183 cp/66 cp
- `recorded:https://lichess.org/ce12lh0d:32` (threat_processing.missed_capture): not_flagged, Caissa swing None cp, Stockfish swing 211 cp; Caissa fast pass before/after 682 cp/-589 cp
- `recorded:https://lichess.org/ui2g2idg:42` (threat_processing.missed_check): no_diagnosis, Caissa swing 456 cp, Stockfish swing 770 cp; Caissa fast pass before/after 206 cp/250 cp
- `recorded:https://lichess.org/ui2g2idg:59` (threat_processing.missed_check): no_diagnosis, Caissa swing 185 cp, Stockfish swing 587 cp; Caissa fast pass before/after 827 cp/-642 cp
- `recorded:https://lichess.org/r5urywfn:74` (threat_processing.missed_check): not_flagged, Caissa swing None cp, Stockfish swing 8931 cp; Caissa fast pass before/after 647 cp/-635 cp
- `recorded:https://lichess.org/r5urywfn:86` (threat_processing.missed_check): not_flagged, Caissa swing None cp, Stockfish swing 8718 cp; Caissa fast pass before/after 690 cp/-646 cp

## Best move

| Search | Same first move as Stockfish |
| --- | --- |
| Fast pass before the move (10,000 nodes) | 37 of 50 |
| Before the move (1,000,000 nodes, MultiPV 4) | 39 of 50 |
| After the move, the refutation (1,000,000 nodes, MultiPV 4) | 41 of 50 |

## Evaluation

Scores from the side to move, centipawns. Mate scores are excluded. Slopes use the pairs where Stockfish |cp| <= 400: OLS is least squares of Caissa cp on Stockfish cp; RMA (reduced major axis, sd ratio) treats both engines as noisy.

| Search | Pairs | Median abs diff (cp) | Share above 50 cp | Slope OLS | Slope RMA | r | Slope n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Fast pass before | 46 | 122 | 72% | 0.69 | 0.71 | 0.962 | 25 |
| Fast pass after | 41 | 97 | 66% | 0.79 | 0.83 | 0.952 | 18 |
| Before, 1M nodes | 45 | 122 | 64% | 0.72 | 0.78 | 0.925 | 20 |
| After, 1M nodes | 36 | 99 | 72% | 0.70 | 0.72 | 0.971 | 17 |
| 1M-node searches pooled | 81 | 114 | 68% | 0.71 | 0.75 | 0.938 | 37 |

Large scores (1M-node searches, Stockfish |cp| > 700, n = 20): Stockfish median |cp| 933, Caissa median |cp| 603. Caissa reports winning positions near 600-700 cp where Stockfish reports 1,000 cp and more, so a move that keeps a won position won shows a smaller swing. Caissa also reports some won positions as cp scores above 20,000 instead of a mate score.

The #396 comparison found slope 1.19 (n = 31, 1 s per search, the position before the move only). This sample uses fixed node budgets and both sides of each mistake; it gives a slope below 1. One score-scale factor does not fit both samples, so a per-engine scale needs a larger fit.

## NarratorFacts

Every diagnosed position (32) builds NarratorFacts (`grounding_context_for`) without an error.

## Reproduce

```
taskset -c 32-39 uv run python -m benchmarks.narrator.build_positions_live benchmarks/narrator/narrator.toml
```
