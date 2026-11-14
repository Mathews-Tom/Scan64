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

## Post-registration clarification — 2026-11-14

The frozen wording above describes an operational engine judgment, not proof of
absence. A 1,000,000-node source search that does not find a forced mate does
**not** prove that none exists; a deeper or different search may find one. “No
prior forced mate” means none was found under the recorded engine configuration
and budget. This dated clarification changes no frozen preregistration text,
pipeline, mapping or results.

## Source recording execution

Signed preregistration: `597627f`; versioned evaluator/recorder: `f1de5fa`.
The separate source fixture, committed as `c5db4bf`, contains exactly **635**
eligible source analyses, each requested at 1,000,000 nodes and MultiPV 4.
Its SHA256 is
`f691f24a1865a0b8ed5317fc9020684a58e9597e3291f3bbec923ca386fad64e`.
The recorder validated exact eligible-ID coverage/order, stable analysis UUIDs,
source FENs, engine/configuration identity, legal PVs and unique MultiPV roots
before writing. No original before/after/focused analysis was regenerated.

The initial capture completed its engine requests but failed before writing any
fixture because Python-mode Pydantic output retained UUID objects that JSON
cannot serialize. Commit `d56b35d` changed persistence to JSON-mode serialization
and added a regression test. The complete source capture was then repeated with
the same preregistered inputs/settings, producing the sole persisted fixture.
No v3 diagnosis execution, agreement calculation, label-based selection or core
tuning occurred during this recording retry.

## Execution gate

V3 was calculated only after the core owner explicitly confirmed the corrected
composer/arbitration was stable and the separate fresh M48 decision was settled.
Fresh-80 M48 preregistration `c4490fa`, results `4d1ea6f`
([real-game report](real-game-diagnosis.md)): exactly 80 games, 5,116 plies and
866 fast-pass candidates; allowed mate TP42, FP0, FN0, emitted support42,
precision1.000. This passes the preregistered precision >=0.80/support >=10 gate,
so allowed mate is **trusted**. The four prior trusted codes remain trusted.
The corrected original-40 run is development-only and did not authorize trust.
The M48 report's shared 1M-node source-analysis limitation remains applicable;
this is not blind coach validation or independent chess truth.

The first fresh-80 recording hit its tool deadline before final persistence.
Its unchanged preregistered sample/settings were retried without a deadline;
only the complete fresh-80 result authorized this v3 execution. No theme
agreement numbers were calculated before that decision, and none affected it.

## Results

Informational theme agreement only; no coach validation or efficacy claim.
Blind independent coach review remains **PENDING**.

V3 pre-registration commit: `597627f`.
Fresh M48 allowed-mate trust decision: **trusted**.
Trust comes solely from the separate post-fix M48 measurement, never these labels.
V1 and v2 below are frozen historical results; v2 is explicitly **pre-fix**.
Frozen owner-side sample: **2,000**. Fast-pass flagged: **1,842**.
Trusted emissions: **1,010**; abstentions: **990**.
Additional deep source recordings: **635**.

### Per-code agreement

| Code | Emitted support | Theme-positive rows | Matches | Precision | Precision Wilson 95% CI | Recall | Recall Wilson 95% CI |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| `board_awareness.hanging_piece` | 185 | 63 | 36 | 0.1946 | [0.1440, 0.2576] | 0.5714 | [0.4486, 0.6860] |
| `threat_processing.missed_capture` | 62 | N/A | N/A | N/A | N/A | N/A | N/A |
| `threat_processing.missed_check` | 37 | N/A | N/A | N/A | N/A | N/A | N/A |
| `threat_processing.missed_direct_threat` | 107 | 63 | 0 | 0.0000 | [0.0000, 0.0347] | 0.0000 | [0.0000, 0.0575] |
| `threat_processing.allowed_mate` | 619 | 769 | 618 | 0.9984 | [0.9909, 0.9997] | 0.8036 | [0.7741, 0.8302] |

N/A is undefined/unmapped, not a measured zero. Confirmed allowed-mate
evidence suppresses material diagnoses even if allowed mate is untrusted;
an eligible-only winner is not a trusted product emission.

### V3 minus v1

| Code | Support delta | Matches delta | Precision delta | Recall delta |
| --- | ---: | ---: | ---: | ---: |
| `board_awareness.hanging_piece` | -40 | -19 | -0.0498 | -0.3016 |
| `threat_processing.missed_capture` | -26 | N/A | N/A | N/A |
| `threat_processing.missed_check` | -30 | N/A | N/A | N/A |
| `threat_processing.missed_direct_threat` | -127 | +0 | +0.0000 | +0.0000 |
| `threat_processing.allowed_mate` | N/A | N/A | N/A | N/A |

### V3 minus v2 (pre-fix)

| Code | Support delta | Matches delta | Precision delta | Recall delta |
| --- | ---: | ---: | ---: | ---: |
| `board_awareness.hanging_piece` | -40 | -19 | -0.0498 | -0.3016 |
| `threat_processing.missed_capture` | -26 | N/A | N/A | N/A |
| `threat_processing.missed_check` | -30 | N/A | N/A | N/A |
| `threat_processing.missed_direct_threat` | -127 | +0 | +0.0000 | +0.0000 |
| `threat_processing.allowed_mate` | +619 | +618 | N/A | +0.8036 |

### Disposition and eligible-only distribution

| Disposition | Count | Rate over full sample |
| --- | ---: | ---: |
| `board_awareness.hanging_piece` | 185 | 9.25% |
| `threat_processing.missed_capture` | 62 | 3.10% |
| `threat_processing.missed_check` | 37 | 1.85% |
| `threat_processing.missed_direct_threat` | 107 | 5.35% |
| `threat_processing.allowed_mate` | 619 | 30.95% |
| `not_flagged` | 158 | 7.90% |
| `no_eligible_diagnosis` | 117 | 5.85% |
| `untrusted_only` | 715 | 35.75% |

| Non-trusted eligible-only winner | Count | Rate over full sample |
| --- | ---: | ---: |
| `tactics.fork.knight` | 1 | 0.05% |
| `tactics.pin` | 10 | 0.50% |
| `tactics.overloaded_defender` | 34 | 1.70% |
| `calculation.stopped_too_early` | 193 | 9.65% |
| `opening.delayed_development` | 0 | 0.00% |
| `positional.king_safety_neglect` | 477 | 23.85% |

### Frozen evidence and reproduction

- Original recording pre-registration: `8c02417`.
- Source recording pre-registration: `597627f`.
- Engine: Stockfish 19; Threads 1, Hash 16 MiB; ucinewgame before every request.
- Original fast before/after and focused reply requests are unchanged.
- Added source: 1,000,000 nodes, MultiPV 4, only eligible fast mate replies.
- Sample SHA256: `fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35`.
- Original recording SHA256: `9a13d33ef51ea906f833e0d51092850652c59e988708d35225ccef79fbab71af`.
- `recorded-source-v3.json` SHA256: `f691f24a1865a0b8ed5317fc9020684a58e9597e3291f3bbec923ca386fad64e`.

```sh
uv run python -m scan64.learning.evaluation.lichess_themes --version v1 --check
uv run python -m scan64.learning.evaluation.lichess_themes --version v2 --check
uv run python -m scan64.learning.evaluation.lichess_themes --version v3 --check
```

All three commands reproduce their complete Results suffixes offline,
without network, database or live engine access. The separate explicit
recording command is `uv run python scripts/record_lichess_theme_agreement.py
--version v3`; it refuses overwrite and never regenerates original analyses.

All pre-registered v1/v2/v3 limitations apply. Mate tags cannot establish
the absence of prior forced mate or the three-move bound. These proxy
numbers neither change product trust nor validate instructional efficacy.
No core tuning, mapping expansion or sample changes followed these results.
