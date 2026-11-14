# Lichess puzzle-theme agreement v2: allowed mate

## Pre-registration

Frozen before any v2 diagnosis execution, cross-tabulation or agreement numbers.
This extends the informational label proxy in [v1](lichess-theme-agreement.md),
not coach validation, an efficacy claim, or a product trust gate. The independent
blind coach review remains **PENDING**. Product trust is determined solely by the
separate M48 isolation measurement: precision >=0.80 with emitted support >=10.
No M77 result may tune the detector or promote a code.

### Frozen sample, engine evidence and ownership

Reuse exactly the v1 2,000-row sample and recording, without modification or new
engine requests. `tests/fixtures/m77/puzzles.csv` SHA256:
`fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35`.
All v1 selection filters, source-prefix provenance, setup-mover ownership and
fast/focused engine settings remain unchanged. Recorded before/after mate scores
and legal PVs supply the new evidence; themes never enter diagnosis evidence.
Do not change A8 bands, the fast-pass threshold, fixtures, or sample membership.

### Frozen mapping and source verification

Keep the four v1 mappings unchanged:

| Code | Confirming theme set |
| --- | --- |
| `board_awareness.hanging_piece` | `{hangingPiece}` |
| `threat_processing.missed_capture` | unmapped |
| `threat_processing.missed_check` | unmapped |
| `threat_processing.missed_direct_threat` | `{hangingPiece}` |
| `threat_processing.allowed_mate` | `{mate, mateIn1, mateIn2, mateIn3, backRankMate}` |

The pinned [lichess-puzzler cook source at 8e24b6d](https://github.com/ornicar/lichess-puzzler/blob/8e24b6d5e2b9618e41581a327926e4ac67c610a2/tagger/cook.py)
was read directly before registration: `cook` calls `mate_in`, adds its mate-depth
tag and `mate`, and only within that mating branch adds `backRankMate` when
`back_rank_mate` succeeds. These tags concern the opponent's punishing mating
continuation, matching the setup mover's allowed-mate ownership. No other tags
are mapped: attack, advantage, crushing, check and length tags do not establish
mate. Specialized additional mate patterns are unnecessary because the same
branch already adds `mate`; excluding them avoids gratuitous mapping expansion.
The v1 tagger-persistence, static-theme policy, label provenance and voting caveats
continue to apply. A mate theme alone does not prove that the position was not
already forcibly lost before the setup move, nor that mate was within three
moves; the detector's recorded pre/post evidence must establish that distinction.

### Versioned replay and analysis

V1 must retain its original detector/composer behavior, original seed list and
four-code trust/mapping, and reproduce the existing report byte-for-byte despite
v2 precedence. V2 uses the live registered detector/composer and joint trusted
arbitration, including allowed-mate precedence over hanging piece, missed direct
threat and missed capture only when separately trusted by M48. Unflagged,
no-eligible-diagnosis and untrusted-only rows remain full-sample abstentions.
The new code is reported even if untrusted; it is not a trusted product emission.

Do not execute v2 until the core detector is stable and the M48 trust decision
is available. Run the frozen sample once without tuning core code on it. For
each mapped code report emitted support, theme-positive denominator, overlap,
precision = overlap/emitted support and recall = overlap/theme-positive rows.
Report two-sided 95% Wilson intervals (z=1.959963984540054) separately for both
precision and recall. Zero denominators are N/A; unmapped codes are N/A, not zero.
Report v2-minus-v1 support, matches, precision and recall deltas for every old
code, retaining N/A where a comparison is undefined. Record the M48 trust outcome
and reproducible commands with the results in a separate commit after this
DCO-signed registration. `--version v1 --check` and `--version v2 --check` must
reproduce their respective committed Results suffixes without network or engines.

### Interpretation limits

Retain all v1 limits: automatically seeded/vote-refinable labels, unknown per-row
human edits, shared Stockfish ancestry, prefix-selection and tactical enrichment,
non-independent rows, ownership mismatch for missed opportunities, overlapping
concepts and single-winner arbitration. Wilson intervals quantify binomial
sampling uncertainty, not label quality or systematic bias. Broad `mate` makes
the proxy insensitive to mate distance and prior forced mate. This is a mapped
label agreement experiment, not independent chess truth or a learning outcome.

## Execution gate

V2 replay began only after the core owner confirmed detector/composer/arbitration
stability and the independent M48 decision was available. M48 registration
`334f70a`, results `4a1ad4f` ([real-game report](real-game-diagnosis.md)) measured
allowed_mate TP11, FP4, FN0, support15, precision 0.733: **not trusted**.
Support was >=10, so no extension was triggered. The four existing codes remain
trusted. The v2 sample did not tune core behavior or affect that decision.

## Results

Informational theme agreement only; no coach validation or efficacy claim.
Blind independent coach review remains **PENDING**.

V2 pre-registration commit: `66eb859`.
M48 allowed-mate trust decision: **not trusted**.
Trust comes solely from the separate M48 isolation report, never these labels.
Frozen owner-side sample: **2,000**. Fast-pass flagged: **1,842**.
Trusted emissions: **614**; abstentions: **1,386**.

### Per-code agreement

| Code | Emitted support | Theme-positive rows | Matches | Precision | Precision Wilson 95% CI | Recall | Recall Wilson 95% CI |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| `board_awareness.hanging_piece` | 225 | 63 | 55 | 0.2444 | [0.1929, 0.3046] | 0.8730 | [0.7689, 0.9342] |
| `threat_processing.missed_capture` | 88 | N/A | N/A | N/A | N/A | N/A | N/A |
| `threat_processing.missed_check` | 67 | N/A | N/A | N/A | N/A | N/A | N/A |
| `threat_processing.missed_direct_threat` | 234 | 63 | 0 | 0.0000 | [0.0000, 0.0162] | 0.0000 | [0.0000, 0.0575] |
| `threat_processing.allowed_mate` | 0 | 769 | 0 | N/A | N/A | 0.0000 | [0.0000, 0.0050] |

N/A is undefined/unmapped, not a measured zero. An untrusted allowed-mate
eligible-only winner is reported below, not counted as a trusted emission.

### V2 minus v1: unchanged old-code mappings

| Code | Support delta | Matches delta | Precision delta | Recall delta |
| --- | ---: | ---: | ---: | ---: |
| `board_awareness.hanging_piece` | +0 | +0 | +0.0000 | +0.0000 |
| `threat_processing.missed_capture` | +0 | N/A | N/A | N/A |
| `threat_processing.missed_check` | +0 | N/A | N/A | N/A |
| `threat_processing.missed_direct_threat` | +0 | +0 | +0.0000 | +0.0000 |

### Disposition and eligible-only distribution

| Disposition | Count | Rate over full sample |
| --- | ---: | ---: |
| `board_awareness.hanging_piece` | 225 | 11.25% |
| `threat_processing.missed_capture` | 88 | 4.40% |
| `threat_processing.missed_check` | 67 | 3.35% |
| `threat_processing.missed_direct_threat` | 234 | 11.70% |
| `threat_processing.allowed_mate` | 0 | 0.00% |
| `not_flagged` | 158 | 7.90% |
| `no_eligible_diagnosis` | 117 | 5.85% |
| `untrusted_only` | 1111 | 55.55% |

| Non-trusted eligible-only winner | Count | Rate over full sample |
| --- | ---: | ---: |
| `tactics.fork.knight` | 1 | 0.05% |
| `tactics.pin` | 18 | 0.90% |
| `tactics.overloaded_defender` | 39 | 1.95% |
| `calculation.stopped_too_early` | 193 | 9.65% |
| `opening.delayed_development` | 0 | 0.00% |
| `positional.king_safety_neglect` | 465 | 23.25% |
| `threat_processing.allowed_mate` | 395 | 19.75% |

### Frozen evidence and reproduction

- Original recording pre-registration: `8c02417`.
- Engine: Stockfish 19; Threads 1, Hash 16 MiB; ucinewgame before every request.
- Fast nodes 10000; focused nodes 1000000, MultiPV 4. No new engine requests.
- Sample SHA256: `fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35`.
- Recording SHA256: `9a13d33ef51ea906f833e0d51092850652c59e988708d35225ccef79fbab71af`.

```sh
uv run python -m scan64.learning.evaluation.lichess_themes --version v1 --check
uv run python -m scan64.learning.evaluation.lichess_themes --version v2 --check
```

Both checks are offline and reproduce their respective Results suffixes.
All pre-registered limitations above and in v1 apply. Mate tags do not prove
the absence of prior forced mate or the detector's three-move bound.
No core tuning, label remapping or sample selection followed these results.
