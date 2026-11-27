# Factor calibration (#410)

Status: measured on 2026-11-27, linux-5090, branch `bench/410-factor-calibration` (base `feat/eval-factors`). No LLM call was made. This report measures factor facts, not teaching quality.

The factor facts (#408, `docs/narrator-pipeline.md` section 16) state a change in the classical evaluation across a mistake. A change becomes a fact when (1) its size reaches a threshold and (2) its sign agrees with the engine swing. This report fits the thresholds of rule 1 from data, checks the facts against the diagnosis class and the engine swing, and records a hand check of 20 positions.

## Result

| Item | Value |
|---|---|
| Fitted thresholds | `FACTOR_CONFIG` in `src/scan64/explanations/factor_facts.py`; table below |
| Fit rule | smallest multiple of 0.05 pawns, at least 0.10, that at most 10% of null-move changes reach |
| Null moves with a fact | 65% with the #408 values (0.30 / 0.15), 38% fitted |
| Mistakes with a fact | 71% with the #408 values, 68% fitted (benchmark and slow sets) |
| Size-passing changes with the swing's sign | 33% to 62% per class: the sign rule removes about half |
| Hand check, 65 facts on 20 positions | 28 name the cause (43%), 25 are true but secondary (38%), 12 are noise (18%) |
| Hand check, positions with a fact that names the cause | slow losses 9 of 10, tactical benchmark positions 4 of 10 |
| Health scale (#416) | `HEALTH_SCALE` in `factor_facts.py`: per group, full at the 80th percentile of healthy values, empty at the 20th percentile in trouble, linear between; section "Health scale" |

## Position sets

| Set | Source | Positions | Engine |
|---|---|---|---|
| `benchmark` | the 50 narrator-benchmark positions, `benchmarks/narrator/positions.json` | 50 (49 measured; `ui2g2idg:59` has best move = played move) | recorded Stockfish 19 searches, 1M nodes, MultiPV 4 |
| `caissa` | same games and moves, `benchmarks/narrator/positions-caissa.json` | 32 | Caissa 2.0 (#389) |
| `slow` | mistakes from Lichess games lost slowly, `benchmarks/factor_calibration/positions.json` | 52 (51 measured) | Stockfish 18, 1M nodes, MultiPV 4, 1 thread, 16 MB hash |
| `null` | moves from the same games that Stockfish scores within 25 cp of its best move | 40 | as `slow` |

**Slow-loss games.** The recorded corpora in `tests/fixtures/m48` hold 600 to 1600 Elo games, decided mostly by blunders, with fast-pass searches at 10,000 nodes only. They have too few slow losses, so the games come from the Lichess API (no account; the per-user export needs one, the tournament export does not):

- `https://lichess.org/api/tournament/LI5xV8sv/games?evals=true&moves=true` (Weekly Classical Arena, 20+10);
- `https://lichess.org/api/tournament/URrnVI6A/games?evals=true&moves=true` (Daily Rapid Arena, 10+0).

`benchmarks/factor_calibration/select_games.py` read 3297 games, of which 700 have the Lichess server analysis. A game is "lost slowly" when it is decisive, has at least 50 plies, and the loser's Lichess eval falls from -60 cp or better to -300 cp or worse over at least 16 plies with no loser move losing more than 200 cp. 20 games match (15 classical, 5 rapid; ratings 1215 to 2476). Their URLs, ratings, moves and evals are in `benchmarks/factor_calibration/games.json` (no user names). Lichess game data is CC0.

Candidates are the loser's moves in the decline that lose at least 40 cp by the Lichess evals (at most 3 per game). Null candidates are moves of either side that lose at most 10 cp while the eval is within ±200 cp (12 per game, seeded shuffle). `record_positions.py` searches each candidate before and after the move with Stockfish 18, then runs Scan64's pipeline as the analysis job does: `compose_candidate_evidence`, every registered detector and `arbitrate_diagnoses`. A slow candidate is kept when the Stockfish swing is at least 40 cp. A null candidate is kept when the swing is within ±25 cp and the played move is not Stockfish's best move. No slow position gets a trusted diagnosis: the detectors are tactical. These positions form the class `slow_loss (no tactical diagnosis)`. A rerun writes the same bytes.

Measured points per set (`build_narrator_facts` picks the lines and the quiet points, as in production):

| Set | Measured | Played point quiet | Best point quiet | Swing known |
|---|---|---|---|---|
| benchmark | 49 | 38 | 43 | 49 |
| slow | 51 | 50 | 49 | 51 |
| caissa | 32 | 22 | 28 | 32 |
| null | 40 | 40 | 39 | 37 |

## Fit method

A null move changes the engine score by at most 25 cp. A factor change at a null move is therefore not a cause of a score change: it is the noise a fact must exceed. Both comparisons of a null move (against `before` and against `best`) give one sample each: 79 samples per group and part.

1. For each group, the threshold is the smallest multiple of 0.05 pawns, at least 0.10, that at most a share `1 - q` of the absolute null changes reach. The size rule passes `|change| >= threshold`, so an exceedance count fits it exactly; an interpolated quantile does not for parts whose null changes are mostly zero.
2. All parts are pooled for the part default. A part whose own value is above the default gets its own threshold.
3. `q` is chosen from 0.8, 0.85, 0.9, 0.95 and 0.975 by the largest separation: the share of mistakes (benchmark and slow) with at least one fact minus the share of null moves with one.
4. The floor 0.10 was compared with 0.15, 0.20 and 0.25 (each with its own best `q`, 0.9 in every case; `floor_table` in `calibrate.py`). 0.10 gives the largest separation and the highest class match:

| Floor (pawns) | Mistakes with a fact | Null moves with a fact | Separation | Class match (positions with a fact) |
|---|---|---|---|---|
| 0.10 | 68% | 38% | +0.31 | 72% |
| 0.15 | 63% | 38% | +0.26 | 68% |
| 0.20 | 54% | 28% | +0.27 | 63% |
| 0.25 | 52% | 22% | +0.30 | 60% |

| Null quantile | Mistakes with a fact | Null moves with a fact | Separation | King safety | Threats | Term default |
|---|---|---|---|---|---|---|
| 0.8 | 75% | 60% | +0.15 | 0.2 | 0.45 | 0.1 |
| 0.85 | 73% | 55% | +0.18 | 0.4 | 0.5 | 0.1 |
| 0.9 | 68% | 38% | +0.31 | 0.75 | 0.6 | 0.1 |
| 0.95 | 50% | 28% | +0.22 | 1.65 | 0.7 | 0.15 |
| 0.975 | 30% | 8% | +0.22 | 4.05 | 1.25 | 0.25 |

`q = 0.9` gives the largest separation. The thresholds are written to `FACTOR_CONFIG` as defaults (`thresholds`, 0.10 for groups and parts) and `overrides` (by guide name). `uv run python -m benchmarks.factor_calibration.calibrate --check` refits and exits 1 when `FACTOR_CONFIG` differs; `tests/unit/test_factor_calibration.py` runs the same check.

## Fitted values

Pawns, learner's view. "Null 90th percentile" is the interpolated quantile, for reference. Parts that never change at a null move are omitted when they keep the default.

| Group or term | Kind | Null samples | Null median | Null 90th percentile | Fitted | #408 |
|---|---|---|---|---|---|---|
| King (king safety) | group | 79 | 0.05 | 0.54 | 0.75 | 0.30 |
| Mobility (mobility) | group | 79 | 0.12 | 0.31 | 0.35 | 0.30 |
| Passed pawns (passed pawns) | group | 79 | 0.00 | 0.08 | 0.10 | 0.30 |
| Pawns (pawn structure) | group | 79 | 0.00 | 0.14 | 0.25 | 0.30 |
| Pieces (piece placement) | group | 79 | 0.09 | 0.33 | 0.35 | 0.30 |
| Space (space) | group | 79 | 0.00 | 0.04 | 0.10 | 0.30 |
| Threats (threats) | group | 79 | 0.13 | 0.58 | 0.60 | 0.30 |
| Backward (backward pawns) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Bishop on king ring (bishop aimed at the king) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Bishop pawns (pawns on the bishop's colour) | term | 79 | 0.00 | 0.12 | 0.15 | 0.15 |
| Bishop xray pawns (bishop x-ray on pawns) | term | 79 | 0.00 | 0.02 | 0.10 (term default) | 0.15 |
| Blocked (blocked pawns) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Connected bonus (connected pawns) | term | 79 | 0.00 | 0.13 | 0.15 | 0.15 |
| Doubled (doubled pawns) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Flank attack (flank attack) | term | 79 | 0.04 | 0.13 | 0.20 | 0.15 |
| Hanging (hanging pieces) | term | 79 | 0.00 | 0.31 | 0.35 | 0.15 |
| Isolated (isolated pawns) | term | 79 | 0.00 | 0.02 | 0.10 (term default) | 0.15 |
| King danger (king attack) | term | 79 | 0.00 | 0.36 | 0.45 | 0.15 |
| King pawn distance (king distance to pawns) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| King protector (king protectors) | term | 79 | 0.04 | 0.12 | 0.15 | 0.15 |
| King proximity (king proximity to passed pawns) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| King threat (king threats) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Knight on queen (knight on the queen) | term | 79 | 0.00 | 0.07 | 0.10 (term default) | 0.15 |
| Long diagonal bishop (long diagonal bishop) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Minor behind pawn (minor pieces behind pawns) | term | 79 | 0.00 | 0.09 | 0.10 (term default) | 0.15 |
| Minor threat (minor piece threats) | term | 79 | 0.00 | 0.28 | 0.30 | 0.15 |
| Outpost total (outposts) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Passed block (passed pawn blockade) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Passed file (passed pawn file) | term | 79 | 0.00 | 0.04 | 0.15 | 0.15 |
| Passed rank (passed pawn rank) | term | 79 | 0.00 | 0.06 | 0.10 (term default) | 0.15 |
| Pawn push threat (pawn push threats) | term | 79 | 0.00 | 0.23 | 0.25 | 0.15 |
| Queen infiltration (queen infiltration) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Restricted (restricted pieces) | term | 79 | 0.03 | 0.10 | 0.15 | 0.15 |
| Rook on file (rook on an open file) | term | 79 | 0.00 | 0.10 | 0.15 | 0.15 |
| Rook on king ring (rook aimed at the king) | term | 79 | 0.00 | 0.03 | 0.10 (term default) | 0.15 |
| Rook on queen file (rook on the queen's file) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Rook threat (rook threats) | term | 79 | 0.00 | 0.10 | 0.15 | 0.15 |
| Shelter storm (pawn storm) | term | 79 | 0.00 | 0.03 | 0.10 (term default) | 0.15 |
| Shelter strength (pawn shelter) | term | 79 | 0.00 | 0.06 | 0.10 (term default) | 0.15 |
| Slider on queen (slider on the queen) | term | 79 | 0.00 | 0.29 | 0.30 | 0.15 |
| Threat safe pawn (safe pawn threats) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Trapped rook (trapped rook) | term | 79 | 0.00 | 0.18 | 0.20 | 0.15 |
| Weak queen (weak queen) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |
| Weak queen protection (weak queen protection) | term | 79 | 0.00 | 0.07 | 0.10 (term default) | 0.15 |
| Weak unopposed pawn (weak unopposed pawns) | term | 79 | 0.00 | 0.00 | 0.10 (term default) | 0.15 |

What changed from the #408 starting values (0.30 per group, 0.15 per part):

- King safety rises to 0.75 and king attack to 0.45. King danger is quadratic in the attack count, so it moves by large steps at quiet null moves.
- Threats rise to 0.60, hanging pieces to 0.35, minor piece threats and slider on the queen to 0.30.
- Pawn structure falls to 0.25, passed pawns and space to 0.10, and most pawn and king-shelter parts to 0.10. These rarely move at null moves, so a small change is already out of the noise.

## Fire rates

Positions with at least one fact:

| Set | #408 thresholds | Fitted thresholds |
|---|---|---|
| benchmark | 37/49 (76%) | 33/49 (67%) |
| slow | 34/51 (67%) | 35/51 (69%) |
| caissa | 21/32 (66%) | 20/32 (62%) |
| null | 26/40 (65%) | 15/40 (38%) |

Facts per position (both comparisons; at most 3 each):

| Set | 0 | 1 | 2 | 3 | 4 | 5 | 6 | Mean |
|---|---|---|---|---|---|---|---|---|
| benchmark | 16 | 6 | 6 | 4 | 5 | 2 | 10 | 2.45 |
| slow | 16 | 9 | 10 | 3 | 2 | 4 | 7 | 2.12 |
| caissa | 12 | 3 | 5 | 1 | 1 | 2 | 8 | 2.44 |
| null | 25 | 2 | 5 | 4 | 1 | 1 | 2 | 1.12 |

Most frequent facts:

| Set | Fact name | Facts |
|---|---|---|
| benchmark | king attack | 18 |
| benchmark | king safety | 13 |
| benchmark | pawn shelter | 10 |
| benchmark | threats | 8 |
| benchmark | pawn storm | 8 |
| benchmark | hanging pieces | 8 |
| benchmark | mobility | 6 |
| benchmark | safe pawn threats | 5 |
| slow | pawn shelter | 14 |
| slow | minor piece threats | 12 |
| slow | threats | 6 |
| slow | space | 6 |
| slow | outposts | 6 |
| slow | king attack | 6 |
| slow | connected pawns | 6 |
| slow | piece placement | 5 |

## Per diagnosis class

"Class match": a fired fact belongs to the groups the class names. Threat classes expect Threats; mate and check classes expect King safety or the `King threat` part; the slow-loss class expects a positional group (pawn structure, piece placement, mobility, passed pawns, space or king safety). "Size-rule changes whose sign agrees": of all changes that pass the size rule before the sign rule (both comparisons, all groups and parts), the share whose sign agrees with the engine swing.

| Class | Positions | With a fact | Class match (any fact) | Class match (largest fact) | Size-rule changes whose sign agrees with the swing |
|---|---|---|---|---|---|
| `threat_processing.allowed_mate` | 9 | 0/9 (0%) | — | — | — |
| `threat_processing.missed_check` | 8 | 4/8 (50%) | 4/4 (100%) | 4/4 (100%) | 13/39 (33%) |
| `threat_processing.missed_capture` | 9 | 7/9 (78%) | 3/7 (43%) | 3/7 (43%) | 28/71 (39%) |
| `board_awareness.hanging_piece` | 9 | 9/9 (100%) | 7/9 (78%) | 6/9 (67%) | 43/86 (50%) |
| `threat_processing.missed_direct_threat` | 9 | 9/9 (100%) | 2/9 (22%) | 1/9 (11%) | 45/72 (62%) |
| `tactics.fork.knight` | 5 | 4/5 (80%) | 3/4 (75%) | 3/4 (75%) | 24/39 (62%) |
| `slow_loss (no tactical diagnosis)` | 51 | 35/51 (69%) | 30/35 (86%) | 25/35 (71%) | 132/248 (53%) |

Reading the table:

- `allowed_mate` has no fact: in 9 of 9 the `played` point is not quiet (the mating line is checks to its end), so it is not measured.
- Threat classes match in 22% to 78%. Their engine reason is mostly material, and the factor facts exclude material (the capture and material slots state it). The facts that fire are mostly king safety and king attack: true changes in the line, not the class's reason.
- `missed_check` matches in 4 of 4 positions with a fact.
- Slow losses match in 30 of 35 positions with a fact (86%).
- Sign agreement is near one half (33% to 62%). Without the sign rule, about half of the size-passing changes would state a factor that moved against the engine's verdict. The sign rule (#408) is needed.

## Hand check

20 positions: every tenth position with a fact, in position-id order, from the benchmark set (10) and the slow set (10); `uv run python -m benchmarks.factor_calibration.hand_check` prints them. For each fact, the engine lines, the board at the measured points and the per-side term values were read. A Stockfish 18 search (2M nodes) checked two positions where the line was unclear. Verdicts:

- **cause**: the fact names the reason the engine's line shows;
- **secondary**: the change is true on the board, but the engine's reason is another one (mostly material);
- **noise**: the change does not explain the line (an artefact of the measured point).

| Position | Class | Swing (cp) | Facts (comparison, pawns) | Verdict |
|---|---|---|---|---|
| `1ronesu1:56` | fork.knight | -9042 | hanging pieces (before, -0.56); pawn storm (before, -0.13) | noise, noise: the cause is the missed Nxe6+ (queen, material). Bxa6 removed one hanging target; Stockfish still gives the learner +7.05 at the played point |
| `15lzmn9d:7` | missed_direct_threat | -574 | hanging pieces (before -0.67, best -0.67); king safety (-1.41, -1.38); king attack (-1.10, -1.07) | cause ×2: Qxd5 attacks the undefended rook on a8; secondary ×4: the queen also stands near the king |
| `55a83jiu:49` | missed_direct_threat | -432 | passed pawns (-0.23, -0.23); passed pawn rank (-0.27, -0.27); king distance to pawns (-0.10, -0.10) | cause ×4: after the queen trade the opponent has passed kingside pawns; secondary ×2 |
| `6x5nq6qd:57` | missed_direct_threat | -369 | king attack (before -0.58, best -0.49) | cause ×2: Qxe5+ wins a pawn with check next to the king |
| `7rzcutsf:5` | missed_direct_threat | -201 | piece placement (-0.42, -0.40); weak queen (-0.27, -0.27); pawn storm (-0.15, -0.15) | noise ×6: Stockfish scores the measured point +0.38 for the opponent, not -2.01; the point does not show the loss |
| `8ok6jtv5:32` | hanging_piece | -308 | mobility (before, -0.36) | secondary: the strong bishop is traded; the engine's reason is the attack on h2 |
| `a301qp92:21` | missed_capture | -557 | king safety (best, -2.05); king attack (best, -1.93) | secondary ×2: after Nxd4 the learner's pieces aim at the king, but the reason is the free knight |
| `fl7asfa0:23` | hanging_piece | -302 | threats (before -0.75, best -1.38); slider on the queen (-0.61, -0.61); piece placement (before, -0.37); safe pawn threats (best, -0.83) | secondary ×6: the active rook is given for a knight; the reason is the material |
| `mntamgdj:7` | hanging_piece | -466 | pawn storm (-0.28, -0.28); knight on the queen (-0.15, -0.15); pawn push threats (best, -0.47) | secondary ×3 (Nf3 will hit the queen on g5; c6 prepares d5), noise ×2 (pawn storm); the reason is a piece for two pawns |
| `y3m3quag:35` | missed_check | -532 | king safety (before, -2.83); king attack (before, -2.68); pawn shelter (before, -0.25) | cause ×2: after Qf6+ Ke2 the attack on the open king is gone; secondary ×1 |
| `6GO42oZu:22` | slow loss | -68 | king safety (best, -4.20); king attack (best, -4.26); passed pawns (before, -0.48); passed pawn blockade (before, -0.29); king proximity to passed pawns (before -0.22, best -0.22) | cause ×2: Qd3 trades queens, Qb5 keeps the attack on the uncastled king; secondary ×4 (the d6 pawn) |
| `8scydVYc:37` | slow loss | -77 | rook on an open file (best, -0.16) | cause: Rac8 contests the c-file held by the opponent's rook on c7 |
| `JS7wagnf:31` | slow loss | -78 | pawn shelter (before -0.37, best -0.37); minor piece threats (best, -0.37) | cause ×2: f5 weakens the pawn cover of the king on e8; secondary ×1 |
| `N3F2hq58:35` | slow loss | -42 | outposts (best, -0.12) | cause: Nc5 reaches an outpost; a6 does nothing |
| `UQLHfBx3:24` | slow loss | -69 | space (best, -0.10); pawn storm (best, -0.10) | cause ×2: g4 gains kingside space |
| `Vetlg2yk:57` | slow loss | -100 | pawn shelter (before -0.10, best -0.10) | noise ×2: an endgame king walk; the reason is the missed Bc2 |
| `ZzASDeMz:23` | slow loss | -112 | minor piece threats (best, -0.37) | cause: b4 axb4 Nxb4 puts a knight on b4 that hits d3 and c2 |
| `e1dRPYZ5:33` | slow loss | -87 | minor piece threats (best, -0.47) | cause: Ba3 attacks the rook on c1 |
| `gxeABQRi:39` | slow loss | -198 | threats (best, -0.65); knight on the queen (before -0.15, best -0.15) | cause ×3: Qf6 puts the queen where the knight on g5 gains tempo |
| `hFKp2oGC:40` | slow loss | -63 | piece placement (before -0.79, best -0.67); pawn shelter (-0.37, -0.37); king protectors (before, -0.24); pawns on the bishop's colour (best, -0.21) | cause ×5: Bxf1 Kxf1 loses castling, shuts in the rook on h1 and moves the king off its shelter; secondary ×1 |

Totals: benchmark 39 facts (10 cause, 19 secondary, 10 noise); slow 26 facts (18 cause, 6 secondary, 2 noise). Every fact is a measured change of the stated size at the stated point; the verdicts judge whether it is the reason.

## Findings

1. The fitted thresholds cut null-move facts from 65% to 38% of null moves and keep mistakes with a fact at 68% (71% before).
2. For slow positional losses, factor facts name a plausible cause in 9 of 10 hand-checked positions, and the class match is 86%.
3. For tactical mistakes, the reason is mostly material, which factor facts exclude by design. Their facts are mostly true secondary changes (king safety near the line), and the class match is low for missed captures and missed direct threats.
4. Half of the size-passing changes disagree in sign with the engine. The sign rule is needed; it does not make a fact the reason.
5. A measured point can miss the engine's reason (`7rzcutsf:5`: Stockfish scores the quiet point +0.38, not -2.01). The point is the first quiet position of the stored line, and the stored line can end before the loss shows.
6. Fact sizes do not scale with the swing: `6GO42oZu:22` states a 4.2-pawn king-safety change for a 0.68-pawn swing. The slot value states the factor change only, so it stays true, but it is not a share of the swing.

## Benchmark metric

`benchmarks/narrator/run_benchmark.py` reports `factor_named_accepted`: the share of accepted explanations that use at least one listed `{factor:...}` slot. `factor_named_where_available` divides by the accepted explanations of positions whose slot list holds a factor slot (33 of the 50 benchmark positions with the fitted thresholds). The summary table shows both as "Names a factor (accepted / where available)". Records from before the slot path give `—`. The LLM benchmark was not run for this report.

## Limits

- The null set is 40 moves from 20 games (79 samples per factor). The 90% exceedance threshold of a factor that moves in few null moves rests on a handful of nonzero samples.
- The slow set has one engine (Stockfish 18) and no trusted diagnosis; its class is the selection rule, not a detector.
- The class map (`EXPECTED` in `calibrate.py`) is a stated expectation, not a measurement.
- The hand check has one reviewer and 20 positions.
- A part name can admit a chess word to the vocabulary rule (section 13 of `docs/narrator-pipeline.md`), for example `rook` from `rook threats`. With a 0.10 threshold, `6x5nq6qd:57` fired `rook threats` at 0.10 and admitted "The learner is up a rook." The exceedance fit raised `Rook threat` to 0.15, so the adversarial suite passes, but the rule admits piece words from any passed part name. Follow-up: limit the words a factor name admits.

## Health scale

The position health panel (#416) shows one meter per factor group at one position. The thresholds above say when a change across a move becomes a fact; they give no scale for a value at a single position. This section fits that scale. `HEALTH_SCALE` in `src/scan64/explanations/factor_facts.py`, next to `FACTOR_CONFIG`, holds it; `calibrate.py` refits it and `--check` fails when the table differs from the fit.

**Value.** A meter reads `health_values(report, side)`: the group's tapered value of the side minus the opponent's, in pawns, from that side's view (the same values the factor facts compare). `Overall` is the guide's main evaluation without tempo, which flips sign every move. The value is antisymmetric: the opponent's value is the learner's value negated, so one row serves both meters.

**Samples.** Quiet positions, each labelled from one side's view:

| Label | Source | Samples |
|---|---|---|
| healthy | `null` set, every quiet measured point (`before`, `played`, `best`), the mover's view | 119 |
| healthy | slow-loss games (`games.json`), quiet positions from ply 10 where that side's Lichess eval is within ±100 cp; both sides of a level position count | 586 |
| trouble | `slow` set, the `played` point of each mistake, the loser's view | 50 |
| trouble | slow-loss games, quiet positions where that side's Lichess eval is -300 cp or less | 402 |

A game position is quiet by the rule of `quiet_ply`, with the game's next move as the line: no check, and the next move is not a capture or a check. The Lichess eval of a position is the eval after the previous ply. The positions before a slow mistake are not used: they lie inside the decline (the loser at -60 to -300 cp), so they are neither level nor lost.

**Fit.** `full` is the 80th percentile of the healthy values, `empty` the 20th percentile of the values in trouble, each rounded outward to 0.05 and at least 0.10 from 0 (so a group that is mostly zero, passed pawns and space, still has a range).

**Curve.** Linear between the two, clamped: `health = (value - empty) / (full - empty)`, 0 at and below `empty`, 1 at and above `full` (`HealthScale.health`). A logistic was not fitted: the healthy and trouble distributions overlap for most groups (AUC below), so a fitted slope would rest on the overlap, and two stated quantiles are easier to refit and to read.

| Meter | Healthy median | Healthy 80th pct | Trouble median | Trouble 20th pct | Full | Empty | Start reads | Healthy reads >= 2/3 | Trouble reads < 1/3 | AUC |
|---|---|---|---|---|---|---|---|---|---|---|
| King | +0.00 | +0.28 | -0.12 | -0.54 | +0.30 | -0.55 | 0.65 | 46% | 34% | 0.60 |
| Material | -0.00 | +0.46 | -1.96 | -5.13 | +0.50 | -5.15 | 0.91 | 100% | 36% | 0.89 |
| Threats | +0.01 | +0.24 | -0.09 | -0.38 | +0.25 | -0.40 | 0.62 | 48% | 38% | 0.58 |
| Pieces | +0.00 | +0.22 | +0.01 | -0.20 | +0.25 | -0.25 | 0.50 | 35% | 35% | 0.49 |
| Mobility | +0.00 | +0.40 | -0.31 | -0.61 | +0.45 | -0.65 | 0.59 | 44% | 54% | 0.70 |
| Pawns | +0.00 | +0.14 | -0.04 | -0.24 | +0.15 | -0.25 | 0.62 | 44% | 38% | 0.58 |
| Passed pawns | -0.00 | +0.00 | -0.00 | -0.43 | +0.10 | -0.45 | 0.82 | 98% | 25% | 0.62 |
| Space | +0.00 | +0.12 | -0.00 | +0.00 | +0.15 | -0.10 | 0.40 | 26% | 12% | 0.54 |
| Overall | +0.00 | +0.84 | -2.80 | -5.64 | +0.85 | -5.65 | 0.87 | 92% | 42% | 0.93 |

Pawns, side's view. "Start reads": the meter at the starting position (value 0). "AUC": the chance that a healthy value is above a value in trouble (0.5: no separation).

**Reading the table.**

- Material (AUC 0.89) and Overall (0.93) separate healthy positions from positions in trouble. Mobility (0.70) separates them partly.
- King safety, threats, pawn structure and passed pawns separate weakly (0.58 to 0.62). Piece placement (0.49) and space (0.54) do not: their meters show the size of the factor, not whether the side is in trouble.
- Space is 0 in most lost positions because the guide counts space only with many pieces on the board (lost positions are mostly endgames). Its `empty` is the 0.10 minimum.
- The positional groups read 0.40 to 0.65 at the start, not full: a level value is as common at a lost position as at a level one.

**Side to move.** A check, not used in the fit: split by whether the side is to move.

| Meter | Full, to move | Empty, to move | Full, waiting | Empty, waiting |
|---|---|---|---|---|
| King | +0.25 | -0.65 | +0.35 | -0.40 |
| Material | +0.30 | -5.60 | +0.55 | -4.75 |
| Threats | +0.20 | -0.50 | +0.35 | -0.25 |
| Pieces | +0.25 | -0.25 | +0.25 | -0.20 |
| Mobility | +0.35 | -0.70 | +0.50 | -0.60 |
| Pawns | +0.15 | -0.30 | +0.20 | -0.25 |
| Passed pawns | +0.10 | -0.50 | +0.10 | -0.35 |
| Space | +0.10 | -0.10 | +0.15 | -0.10 |
| Overall | +0.55 | -6.50 | +1.20 | -4.55 |

In trouble, the side to move reads lower king safety, threats and overall values than the side waiting. One pooled row is kept: a scale that changes with the side to move would move every meter on every ply, as tempo would.

**Limits.**

- 20 games (1215 to 2476), one source of labels (the Lichess server analysis), and one selection rule (slow losses). The trouble samples are mostly late, lost endgames; trouble in a middlegame is underrepresented.
- The labels come from the engine score, not from the group: a group's meter is fitted on positions where the side is in trouble for any reason, so for weakly separating groups the meter does not say that this group is the trouble.
- The game positions are consecutive plies of the same games, so the samples are not independent; the effective sample size is smaller than the counts.
- The quantiles (80th and 20th) and the level and trouble bounds (±100 cp, -300 cp) are stated choices, not fitted.

## Reproduce

```
curl -H "Accept: application/x-ndjson" "https://lichess.org/api/tournament/LI5xV8sv/games?evals=true&moves=true" > LI5xV8sv.ndjson
curl -H "Accept: application/x-ndjson" "https://lichess.org/api/tournament/URrnVI6A/games?evals=true&moves=true" > URrnVI6A.ndjson
uv run python -m benchmarks.factor_calibration.select_games LI5xV8sv.ndjson URrnVI6A.ndjson \
    --tournament LI5xV8sv URrnVI6A --out benchmarks/factor_calibration/games.json --max-games 40
uv run python -m benchmarks.factor_calibration.record_positions \
    benchmarks/factor_calibration/games.json benchmarks/factor_calibration/positions.json
uv run python -m benchmarks.factor_calibration.calibrate          # tables in this report
uv run python -m benchmarks.factor_calibration.calibrate --check  # FACTOR_CONFIG and HEALTH_SCALE equal the fit
uv run python -m benchmarks.factor_calibration.hand_check         # the 20 hand-check positions
```

The tournament exports change while a tournament runs; the committed `games.json` is the snapshot of 2026-11-27. `record_positions` and `calibrate` read only committed files.
