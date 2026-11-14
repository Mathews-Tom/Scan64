# Real-game diagnosis measurement

## M79 missed-capture investigation and preregistration

The original40 and M78 fresh80 are now **development data for missed capture**,
not evidence for its next trust decision. Selection and the production detector fix
were committed before recording; the separately disclosed reference amendment was
committed before any new-set measurement. Historical measurements below and all
M77 v1–v3 fixtures/reports remain unchanged.

### Definition and false-positive attribution

The exact `taxonomy/seeds.py` requirement is **"Player fails to take a hanging or
underdefended opponent piece."** Its minimum evidence is "Missed +2.0 advantage
via capture."; confidence 1.0 requires "taking was the only winning line."
The composer instead requires a legal first fast-PV capture, a >=200 cp *swing*
(not an absolute +200 cp advantage), positive target-local exchange gain, and
positive full-PV material gain. The detector consumes that evidence at confidence
0.8; it does not assert uniqueness. Arbitration enforces the swing and evidence
requirements. Pawn gains and beneficial recaptures are included operationally.
The independent reference applies positive local and full-line material tests to
the first **million-node** source PV, not the 10,000-node fast PV. It does not test
the fast move separately, alternative winning captures, uniqueness, or whether
the recommended move was actually played. Thus disagreement is not automatically
a demonstrably bad capture or a human-coach false positive.

Every pre-fix disagreement is listed below. Ply is one-based. Categories are
mutually exclusive, applied in order: **I** = recommended capture actually played;
**R** = deep search chooses a different first move (including an even exchange);
**E** = same first capture, but its deep local exchange is non-positive;
**L** = same profitable local capture, but full deep-PV endpoint material gain is
non-positive. L can represent later compensation/counterplay or simply endpoint
and definition ambiguity; a finite PV is not proof the initial victim was safe.
R does not establish that the fast capture loses. These are traceable search/
label mechanisms, not invented coach judgements.

| Mechanism | Original40 FP | M78 fresh80 FP |
| --- | ---: | ---: |
| I: player already took the recommended piece | 0 | 3 |
| R: different deep root move | 3 | 8 |
| E: deep recapture/exchange removes local profit | 1 | 3 |
| L: local profit, no net gain at deep PV endpoint | 5 | 13 |
| **Total** | **9** | **27** |

| Corpus | Lichess game / ply | Fast capture → deep move | Cause |
| --- | --- | --- | --- |
| original40 | fiv37vzi / 43 | Rxh7 → Rxh7 | L |
| original40 | 15lzmn9d / 20 | bxc4 → bxc4 | L |
| original40 | 15lzmn9d / 36 | axb5 → axb5 | L |
| original40 | 7hvhc1t7 / 15 | cxb4 → Bxc8 | R |
| original40 | 7hvhc1t7 / 17 | Bxg4 → Bxg4 | E |
| original40 | 8dqmtc5l / 29 | Nxf6 → Bxf6 | R |
| original40 | 8dqmtc5l / 102 | Kxd6 → Qc3 | R |
| original40 | fwbxa5oz / 42 | Nxh5 → Nxh5 | L |
| original40 | m6c8eqiw / 80 | dxe4 → dxe4 | L |
| M78 fresh80 | grcc15yt / 98 | Kxa2 → Kc1 | R |
| M78 fresh80 | cnw6r6h7 / 37 | Rxd6 → Bxb5 | R |
| M78 fresh80 | 3qlyk55t / 13 | gxf5 → Nh3 | R |
| M78 fresh80 | 3qlyk55t / 14 | hxg3 → hxg3 | L |
| M78 fresh80 | vn6b4jr8 / 35 | Rfxd6+ → Rfxd6+ | L |
| M78 fresh80 | vwa8ajti / 66 | Qxc5 → Qxc5 | L |
| M78 fresh80 | vwa8ajti / 75 | Rxd6 → Rxd6 | L |
| M78 fresh80 | g0cm4yme / 49 | bxc3 → bxc3 | L |
| M78 fresh80 | g0cm4yme / 61 | gxf3 → gxf3 | L |
| M78 fresh80 | ancngao7 / 51 | Qxd5 → Qxd5 | E |
| M78 fresh80 | lvgg5cfk / 30 | Bxe4 → Bxe4 | L |
| M78 fresh80 | hgu2kah6 / 83 | Kxg5 → Ke4 | I |
| M78 fresh80 | 09zsd09k / 53 | Kxh8 → Nd4 | I |
| M78 fresh80 | q3p670dv / 21 | dxe6 → dxe6 | E |
| M78 fresh80 | ayxflpm0 / 29 | Bxb7 → Bxb7 | L |
| M78 fresh80 | pihdkfmj / 65 | Rxc5 → Rxc5 | E |
| M78 fresh80 | gdzmxd4k / 74 | Rxh3 → Rxh3 | L |
| M78 fresh80 | ut4hjj1c / 53 | Qxg7 → a4 | R |
| M78 fresh80 | 5v7aj9ul / 65 | bxc6 → bxc6 | L |
| M78 fresh80 | 5v7aj9ul / 66 | Qxf4+ → Re2 | R |
| M78 fresh80 | gtpziea4 / 22 | Nxf5 → Na6 | R |
| M78 fresh80 | 0wav2qbp / 16 | Rxa6 → Rxa6 | L |
| M78 fresh80 | hbzk28yq / 63 | axb5 → axb5 | L |
| M78 fresh80 | hbzk28yq / 64 | bxa4 → b4 | I |
| M78 fresh80 | hbzk28yq / 66 | gxh5 → a3 | R |
| M78 fresh80 | uoit0jvt / 48 | Rxa3 → Rxa3 | L |
| M78 fresh80 | kibzjl88 / 43 | dxc6 → d6 | R |

**Principled fix:** never emit missed-capture evidence when the played legal move
equals the proposed capture. The three I cases are unequivocal definition defects:
a shallow score swing flagged the very move being recommended. Regression tests
cover all three, with alternative-move positive controls. The production fix changes
no other detector, threshold, A8 band, fast-pass selection, historical reference label,
or recorded engine result. In particular, we do not force agreement with the deep first PV: that
would share the oracle's source conclusion, discard untested alternative captures,
and conceal rather than resolve the R/L ambiguity. The E cases illustrate genuine
shallow exchange artifacts; no arbitrary depth or material cutoff is fitted to
those four examples. Remaining search and label limitations are evaluated as-is.

The first development M77 replay detected that its frozen sample includes the same
identity defect. Its offline-only historical reconstruction now restores that
pre-M79 evidence, following the existing historical allowed-mate replay pattern;
all three committed Results suffixes reproduce byte-for-byte. Production has no
legacy switch. This repair preceded preregistration and new recording.

### Frozen M79 fresh80 protocol

Select **exactly qualifying ordinals 121–200** from the same January 2013 Lichess
CC0 compressed archive, in source order, after original40 and M78 fresh80.
Verify SHA256 `aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635`
before selection; verify both earlier game-ID prefixes. Filters remain error-free
PGN parsing, both Elo ratings 600–1600 inclusive, >=20 plies. No hand-selection,
extension, early stopping, or result filtering.

Run `uv run python scripts/record_diagnosis_reference.py
/tmp/m78-lichess-2013-01.pgn.zst --m79-fresh80` with `BROWSER=/usr/bin/true`.
Store separate `m79_fresh80_recorded.json` and `m79_fresh80_real_games.pgn`.
The recorder embeds the preregistration HEAD. Preserve all earlier corpus bytes.
Stockfish 19, one thread, 16 MB hash, 10,000-node fast pass and independently reset
1,000,000-node MultiPV-4 source/reply reference searches remain unchanged.

**Decision rule:** keep `threat_processing.missed_capture` trusted **only if**
its precision is >=0.80 and emitted support (TP+FP) >=10 on M79 fresh80 alone.
Otherwise remove it from `TRUSTED_CODES`, migrate current trust consumers and
practice mappings, and let the trusted pipeline abstain normally. Persisted records,
archives and immutable trust-at-award snapshots must remain readable/restorable.
Other trusted codes retain their existing original40 or M78 fresh80 gates.
Report all codes' TP/FP/FN/support/precision/gate and material-suppression checks.
No detector fix or threshold change is allowed after any new-set numbers are seen.
If another fix is needed, disclose it and stop rather than tuning this holdout.
Record chronology, restarts, preregistration/results SHAs and fixture checksums.

### Premeasurement reference amendment

A fresh-context method review, while raw recording was in progress and **before
any M79 metrics were computed or inspected**, found the same identity error in
the reference: it could call a profitable deep first capture "missed" when that
was the move played. M78 development game `0wav2qbp`, ply 17 (`exf6`) demonstrates
a trust-favouring false TP: the fast capture differs, but the deep capture was
played. Also, excluding played fast captures removed one previous TP as well as
the three I false positives. Under the unchanged historical reference, the corrected
detector's M78 development count is **84 TP / 24 FP = 0.778, support 108**,
versus **85 TP / 27 FP = 0.759, support 112** pre-fix. M79 uses the amended
reference below instead; these historical development counts do not decide trust.

Amend the M79 reference only: withhold `missed_capture` if legally replaying the
deep first move produces the actual after-position. All other labels remain
identical; the original `reference_labels` and both older corpus reports retain
their frozen definitions. This independently implemented omission condition lives
in `m79_reference_labels`; it does not call the composer or detector. The same
>=0.80 precision / >=10 support decision rule now applies to that definition-faithful
M79 label. Regression tests cover the development false TP and both played/omitted
capture controls. This amendment is committed before any new-set measurement.
It cannot influence selection or raw engine output, so recording is not restarted.
No new-set numbers were available to motivate it.

Post-selection-preregistration evaluation support also pins M79's gate and freezes
M77 v3 trust independently of the live product allowlist; these offline-only edits
were made during recording, before measurement. They do not alter recorded searches,
the production identity fix, or any frozen M77 fixture/report bytes.

### Historical trust and immutable evidence

Before measurement, archive validation was separated from current trust: the
bounded `HISTORICALLY_TRUSTED_CODES` history retains all five previously trusted
codes, including missed capture, even if active trust later shrinks. Only archive
validation uses that history. New lessons, observations, positive credit, puzzle
weakness targeting, patterns and habit qualification still use current trust.
Never-trusted codes cannot manufacture historical habit eligibility.

An integration test constructs a genuine pre-demotion missed-capture archive,
then restores it under simulated active demotion. Lesson/skill records, credits,
trust snapshots, pattern snapshots, awards and their five supporting games restore
unchanged. Current patterns disappear and the old target reports "The tracked
diagnosis is no longer trusted.", while its earned award remains visible. Reads
do not rewrite the export. A never-trusted pin substituted into the same archive
is rejected. This is a read-time policy change, not a retrospective reward rewrite.

Also before measurement, the read-time visibility policy gained the same identity
exclusion for old capture evidence that records the proposed and played moves.
Even if capture remains trusted, an old lesson cannot recommend the very move
the player made. The comparison does not modify stored lesson/evidence bytes.
A taken-capture regression and an omitted-capture control cover that read path.
This read-time extension does not change recording or detector measurement.

## M79 fresh80 measured result and trust decision

Selection/production preregistration: `4afbe47d09138606ecd7d3dbabc4659e1ef2c6ed`.
Premeasurement reference amendment: `f0521e1cbd6772f52befdfc3b5bd3379cf0c8125`.
Historical-restoration safeguards: `10ed1d1`; read-time identity exclusion: `08bd25a`.
The raw recording embeds the selection preregistration, not the later reference
amendment, because recording contains engine output only.

The checksum-verified source yielded **exactly ordinals 121–200: 80 games,
4,946 plies, 798 candidate moves**. Stockfish 19, one thread, 16 MB hash,
10,000-node fast searches and separately reset 1,000,000-node MultiPV-4 source/
reply searches match both earlier corpora. The two earlier game-ID prefixes were
verified. No hand-selection, extension, early stopping or outcome filtering occurred.

Separate fixtures: `tests/fixtures/m48/m79_fresh80_recorded.json` and
`tests/fixtures/m48/m79_fresh80_real_games.pgn`. SHA256, respectively:
`36f4ab102c418ef5eeeedf8a8733638623d643321f4b5741143c98f0284167a7`;
`958ee8358bff5d271788225eb03b0977d80921b395cd41d0843fcce3e5e7cd16`.
All four earlier corpus/PGN checksums still match the M78 values below.

| Code | Precision | Support | TP | FP | FN | Meets numeric gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `board_awareness.hanging_piece` | 0.882 | 127 | 112 | 15 | 30 | yes |
| `threat_processing.missed_check` | 0.953 | 64 | 61 | 3 | 28 | yes |
| `threat_processing.missed_capture` | **0.839** | **93** | **78** | **15** | **29** | **yes** |
| `threat_processing.missed_direct_threat` | 0.826 | 195 | 161 | 34 | 65 | yes |
| `tactics.fork.knight` | 0.750 | 4 | 3 | 1 | 2 | no |
| `tactics.pin` | 0.046 | 65 | 3 | 62 | 1 | no |
| `tactics.overloaded_defender` | 0.020 | 152 | 3 | 149 | 0 | no |
| `calculation.stopped_too_early` | 0.300 | 407 | 122 | 285 | 81 | no |
| `opening.delayed_development` | 0.138 | 80 | 11 | 69 | 3 | no |
| `positional.king_safety_neglect` | 0.349 | 195 | 68 | 127 | 25 | no |
| `threat_processing.allowed_mate` | 1.000 | 36 | 36 | 0 | 0 | yes |

**Decision: keep missed capture trusted**, exclusively from M79 precision
78/93 = 0.8387096774 and support 93 under the amended omission reference.
The active five-code allowlist is unchanged. Therefore the `hangingPiece`
practice mapping still includes missed capture, and current pattern, habit,
profile and positive-credit consumers retain it. No other code is promoted.
The other three established codes keep original40 gates; allowed mate keeps M78
fresh80. All other M79 rows are descriptive, not replacement trust samples.

**Chronology:** one uninterrupted recording attempt completed in 4,222.92 seconds
(about 70 minutes), without a tool deadline. There were **no recording restarts**.
Only game/ply/candidate progress counts were available during recording. No partial
M79 diagnosis counts, labels or precision were computed or inspected. The
reference amendment and both persistence safeguards above were committed while
raw recording ran, before the first complete-set measurement. No detector,
reference, threshold or sample change followed the new numbers.

The first recorded-only `real_games --check` completed with exit status **0**:
the three original40 gates, allowed-mate M78 gate and missed-capture M79 gate pass,
as do confirmed-mate material-suppression assertions across all three corpora.
The retained historical original40 capture count is 56/65; historical-label M78
development replay after the identity fix is 84/108, as disclosed above.

**Limits:** this is one finite sample against engine-derived operational labels,
not independent coach review or measured teaching efficacy. The remaining 15
capture disagreements are not concealed by arbitration: each code is still scored
in isolation. Deep first-move disagreement does not refute alternative captures,
and finite-PV endpoint material can disagree with the taxonomy's hanging-piece
language. Passing the preregistered point-estimate gate does not remove those
limitations. The new omission reference prevents the specific played-capture
false-TP mechanism without making the detector share its deep source conclusion.

### M79 final verification

Results/fixture commit: `d728843f443871b41b492db1ac9c2166e023070c`.
`BROWSER=/usr/bin/true bash scripts/check.sh` completed with exit status **0**
in 309.70 seconds: **1,815 Python tests passed** (2 real-model tests deselected),
ruff and format checks passed (355 files), strict mypy passed (179 source files),
M48's three-corpus gate/suppression check passed, dependency licenses passed,
frontend build/lint passed, **370 frontend unit tests** and **58 headless e2e
tests** passed. Port 5173 was checked free before launch; no listener was killed.
The run emitted deprecation/lint warnings and mocked-e2e proxy connection-refused
noise for the absent port-8001 API, but no gate failed. Final tail:

```text
  58 passed (33.9s)
All checks passed.
```

All three M77 commands, `lichess_themes --version v1 --check`, `--version v2
--check` and `--version v3 --check`, exited **0** and reproduced their frozen
Results suffixes. A scoped gitleaks scan of the five implementation/results
commits reported no leaks.

**Live retained-trust check:** a real API on port 8019 served the built frontend
against a fresh disposable SQLite database. A new player imported the position
before original40 game [6x5nq6qd](https://lichess.org/6x5nq6qd), ply 26, as Black,
and played `13...b5` instead of `bxc5`. Real Stockfish analysis completed.
Headless Chromium rendered **Your move · Blunder**, coverage **1/1**, accuracy
**42.1%**, and **one trusted key moment** headed
`threat processing / missed capture`. Its explanation was:
"You played b5, but the engine's principal line begins with b6c5, capturing the
queen on c5." The retry control showed three attempts remaining. No skill credit
was claimed for this imported game's unknown assistance. The browser and API
were stopped after acceptance; the owner's real browser and port 8000 were not used.

**Fresh-context review:** one reviewer verified all 36 development FP attributions,
selection against the checksum-verified archive, fixture/provenance checksums,
the complete M79 table, unchanged frozen artifacts, and the keep-trust decision.
Its initial reference-identity P2 was fixed and re-reviewed before measurement.
Changed-only reviews of the persistence safeguards and final results found
**no P1/P2 issues**. No detector or reference was tuned after fresh numbers.

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

Original corpus preservation checksums (SHA256, before fresh recording):
`recorded.json`: `c7dc5fd62964b3401bccba6087703154ae9872182b3c0c9f49af0e90f68b255d`;
`real_games.pgn`: `746f4daa743872e697b8780b3ff8681f51f4b06b730c8498cc3d6b33dd88ef05`.

### Corrected original40 development replay (not trust evidence)

After source-depth correction, recorded-only replay reports allowed mate TP 11,
FP 0, FN 0 (precision 1.000, support 11). This is development evidence only and
does not promote the code. All four existing trusted codes retain their gates:
hanging piece 69/77; missed check 26/30; missed capture 56/65; missed direct threat
106/118. Confirmed-mate material suppression assertions pass during this replay.
The historical pre-fix table and four deep-source false-positive reasons below
remain unchanged.

**Shared-source limitation:** corrected detector evidence and the M48 reference
share the same recorded 1,000,000-node source search. Fresh80 precision therefore
cannot reveal source-side forced-mate search errors, including the mechanism behind
the original four false positives. It measures reply-side agreement under that
shared-source operational definition, not independent source-proof correctness.
The reference does not call the composer or detector, but its source search is not
independent evidence of the detector's source-side conclusion.

**Recording chronology:** no fresh80 diagnosis metrics, including partial-corpus
metrics, were computed or inspected before the final recording attempt. The first
attempt exposed an existing recorder API mismatch (`FastPassResult` is not iterable);
the recorder was corrected to use `.candidates`, without changing analysis or selection.
The subsequent capture reached 63/80 games before a tool-imposed 3,600-second cutoff,
before its final recording write. It reported game/ply/candidate progress counts only,
not allowed-mate detections or reference labels. The identical capture was restarted
without a tool deadline: same eighty games, source/filter, nodes, resets, and detector.
No detector changes or fresh-result-driven tuning occurred between attempts.

## M78 fresh80 measured result and trust decision

Preregistration: `c4490fa` (DCO signed), before fresh recording or measurement.
The checksum-verified official archive yielded exactly qualifying ordinals 41–120:
80 games, 5,116 plies, 866 candidate moves. No extension or early stopping occurred.
Stockfish 19 used one thread, 16 MB hash, 10,000-node fast searches and separate
1,000,000-node MultiPV-4 source/reply searches, reset before each position.

Separate fixtures: `tests/fixtures/m48/fresh80_recorded.json` and
`tests/fixtures/m48/fresh80_real_games.pgn`. Recording SHA256:
`3bb73c22eca1906da3570a27f119e3948254987af53ec7328d5cfd30792a3539`;
selected PGN SHA256:
`858de447e6494b2618711953c77f5319a05246f4c26629de203c454b8c20eb8b`.
Original fixtures were not rewritten.

| Code | Precision | Support | TP | FP | FN | Meets numeric gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `board_awareness.hanging_piece` | 0.914 | 140 | 128 | 12 | 27 | yes |
| `threat_processing.missed_check` | 0.907 | 75 | 68 | 7 | 21 | yes |
| `threat_processing.missed_capture` | 0.759 | 112 | 85 | 27 | 45 | no |
| `threat_processing.missed_direct_threat` | 0.871 | 225 | 196 | 29 | 55 | yes |
| `tactics.fork.knight` | 0.857 | 7 | 6 | 1 | 6 | no |
| `tactics.pin` | 0.036 | 84 | 3 | 81 | 0 | no |
| `tactics.overloaded_defender` | 0.000 | 140 | 0 | 140 | 0 | no |
| `calculation.stopped_too_early` | 0.309 | 459 | 142 | 317 | 74 | no |
| `opening.delayed_development` | 0.167 | 84 | 14 | 70 | 0 | no |
| `positional.king_safety_neglect` | 0.466 | 189 | 88 | 101 | 36 | no |
| `threat_processing.allowed_mate` | **1.000** | **42** | **42** | **0** | **0** | **yes** |

**Decision: promote allowed mate**, based exclusively on fresh80 precision 42/42
and support 42. No fresh allowed-mate false positives exist to attribute.
The original four false positives remain documented below: their 10,000-node
source searches missed forced mates found at 1,000,000 nodes (-4, -8, -6, -6).
The corrected source exclusion removes those development disagreements; that does
not remove the shared-source limitation stated above.

The other fresh80 code rows are descriptive, not new trust samples. In particular,
missed capture's 0.759 fresh80 precision is disclosed, not hidden or used to shrink
the existing four-code allowlist; the preregistered gate for those four is original40.
No other code is promoted.

`BROWSER=/usr/bin/true uv run python -m scan64.learning.evaluation.real_games --check`
completed with exit status **0**, including after promotion: the original four
declared codes pass their original40 gates; allowed mate passes its fresh80 gate;
confirmed-mate material suppression assertions pass across both corpora.
The report is recorded-only; no live engine runs during measurement. Unit tests,
builds, linters, and formatters were not run by this worker.

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

## M78 original40 development result (pre-fix, historical)

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
