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
