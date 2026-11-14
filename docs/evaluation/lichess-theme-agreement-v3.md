# Lichess puzzle-theme agreement v3: source-depth correction

## Pre-registration

Frozen before any v3 diagnosis execution, cross-tabulation or agreement calculation.
This is informational theme agreement, not coach validation, an efficacy claim,
or a product trust gate. Independent blind coach review remains **PENDING**.
The [v1 report](lichess-theme-agreement.md), original engine recording and
[v2 pre-fix report](lichess-theme-agreement-v2.md) remain immutable historical
results. V2 used the faulty fast-source prior-mate check and trust-dependent
material suppression; its recorded results are not post-fix evidence.

### Frozen sample and mapping

Reuse exactly the existing **2,000** owner-side setup rows (the committed sample,
not a new 1,000-row subset), in their frozen source order. The setup mover is the
owner, opposite the puzzle solver. `tests/fixtures/m77/puzzles.csv` SHA256:
`fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35`.
The unchanged `tests/fixtures/m77/recorded.json` SHA256 is
`9a13d33ef51ea906f833e0d51092850652c59e988708d35225ccef79fbab71af`.
No before/after fast or focused reply analysis is regenerated. All v1 selection,
source-prefix provenance and ownership rules remain unchanged.

The mapping is **identical to v2**, without expansion:

| Code | Confirming theme set |
| --- | --- |
| `board_awareness.hanging_piece` | `{hangingPiece}` |
| `threat_processing.missed_capture` | unmapped |
| `threat_processing.missed_check` | unmapped |
| `threat_processing.missed_direct_threat` | `{hangingPiece}` |
| `threat_processing.allowed_mate` | `{mate, mateIn1, mateIn2, mateIn3, backRankMate}` |

The pinned source justification and label-provenance caveats in v2 apply without
change. Themes never enter analysis, evidence composition or arbitration.

### Fixed pipeline and additional recording

Keep the original 10,000-node, single-PV fast screening and 1,000,000-node,
MultiPV-4 focused reply recording. For each fast-pass candidate whose recorded
reply's best score is positive mate in 1–3, record **only** its source position at
1,000,000 nodes and MultiPV 4. No other source requests are permitted. Use the
same Stockfish identity as v1, Threads 1, Hash 16 MiB, and `ucinewgame` before every
request. Store source analyses separately in
`tests/fixtures/m77/recorded-source-v3.json`, with strict provenance tying them to
this signed preregistration, the original sample and recording hashes, exact
eligible puzzle IDs in sample order, source FENs and stable analysis UUIDs. Missing,
extra, reordered, duplicate, illegal-PV or configuration-mismatched records fail
closed. Preserve actual analysis configuration; never disguise a fast recording
as a deep request.

V3 calls the production composer with its required `mate_source_analysis` keyword:
new evidence is absent when this is None. Only the added deep source analysis can
establish no prior forced mate; the 10k source score cannot. A legal positive
reply mate in 1–3 and all remaining production checks still apply. Confirmed
allowed-mate evidence suppresses hanging piece, missed direct threat and missed
capture **even when allowed mate is untrusted**; allowed mate itself wins only
when separately trusted. Do not tune core thresholds, A8 bands, M77 mappings or
sample membership after observing these labels.

V1 passes `mate_source_analysis=None`, omits new evidence/detectors and uses its
frozen four-code trust and pre-fix arbitration semantics. V2 reconstructs only the
old allowed-mate evidence from the original recorded fast source/reply and focused
provenance inside the offline evaluator, retaining its original four-code trust
and pre-fix trust-dependent suppression. This is evaluation-only historical
replay, not a production compatibility shim. Old-code evidence composition is
unchanged. Both old reports must reproduce byte-for-byte, regardless of later
production trust decisions.

### Execution gate and metrics

Do not calculate v3 until the core owner explicitly confirms the source-depth
composer/arbitration pipeline is ready **and** the fresh, separate M48 trust
decision is available. M48 alone determines trust (precision >=0.80 and emitted
support >=10); M77 labels cannot promote a code or tune the detector. Record that
fresh decision and its independent report/commit references before the results.

For each code report emitted support, theme-positive denominator and overlap.
Mapped precision = overlap/emitted support; recall = overlap/theme-positive rows.
Report two-sided Wilson 95% intervals (z=1.959963984540054) for precision and
recall separately. Zero denominators and unmapped metrics are N/A, not measured
zero. Every row remains in the full-sample denominator, including not-flagged,
no-eligible-diagnosis and untrusted-only abstentions. Show all disposition counts
and non-trusted eligible-only winners. Report v3-minus-v1 and v3-minus-v2 support,
matches, precision and recall deltas, with allowed-mate v1 comparisons N/A because
v1 had no such mapping. Record all fixture hashes, preregistration identity and
reproducible offline `--version v1/v2/v3 --check` commands. Commit results separately
from this DCO-signed preregistration.

### Interpretation limits

Retain all v1/v2 limitations: algorithmically seeded/vote-refinable labels, unknown
per-row human edits, shared Stockfish ancestry, prefix selection and tactical
enrichment, non-independent rows, ownership mismatch for missed opportunities,
overlapping concepts and single-winner arbitration. Broad mate tags cannot prove
mate distance or the absence of prior forced mate. Wilson intervals quantify
binomial sampling uncertainty, not label quality or systematic sampling bias.
The source-depth correction addresses an observed engine-horizon bug, not label
agreement optimization. No claim of independent chess truth or improved learning
outcomes follows from these proxy numbers.
