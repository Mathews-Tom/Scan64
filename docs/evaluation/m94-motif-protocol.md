# M94 motif operational-reference preregistration

Status: **freeze-ready source phase; no fresh search, labels, verdict or promotion**.
The parent must review and commit these definitions and sources before recording.
M93's protocol, historical revisions and recordings are immutable and unchanged.

## Selection and engine contract

Use exactly qualifying ordinals **281–360**, the next 80 after original40,
M78, M79 and M93. Exposed ordinals 1–280 cannot support these new decisions.
Source: January 2013 rated standard Lichess PGN, CC0,
`https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst`.
Compressed SHA256:
`aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635`.
Qualify only error-free PGNs, both Elo 600–1600 inclusive, at least 20 plies.
Verify all four established Site prefixes before taking this fixed slice. Do not
replace, extend, resample, tune against or inspect this holdout before preregistration.

Stockfish 19, one thread, 16 MB hash; `ucinewgame` before every position.
Fast pass: 10,000 nodes, MultiPV 1, existing 150 cp candidate threshold.
Focused reply and separately searched deep source: 1,000,000 nodes, MultiPV 4.
Use the first source PV only; no best-of-MultiPV label selection. No paid calls,
new download or engine change. Production motifs require a >=200 cp move swing.

## Shared operational rules

Codes: `tactics.skewer`, `tactics.discovered_attack`, `tactics.back_rank`.
These are intentionally conservative operational subsets of chess motif names,
not a claim to recognize every thematic position. Each is a **missed source
opportunity**, not a motif newly allowed to the opponent. The root recommendation
must differ from the played legal move. Castling is excluded (two moving pieces).
Every PV move must be legal; any malformed or illegal suffix rejects the entire
proof, not just that suffix. Whole-PV material balance accounts for captures and
promotions (pawn 1, knight/bishop 3, rook 5, queen 9; king is not material).
Material motifs require net gain >=1 over the full PV **and** gain >=1 in the
consecutive target-square capture exchange starting on ply three. A later gain
elsewhere cannot turn an even named-target exchange into a supported diagnosis.

### Skewer — `m94-skewer-v1`

The root bishop, rook or queen move newly attacks an enemy front piece along a
ray; that front piece was not attacked by this slider from its original square.
The front is the king or is strictly more valuable than the rear. The rear is an
enemy knight, bishop, rook or queen on the same ray, with exactly the front piece
between it and the slider after the root. The opponent's immediate reply moves
that front piece away. On ply three the same slider captures the same rear piece,
with positive full-PV and target-local exchange gains. Pre-existing rays, pins,
equal-value front/rear pieces, pawn rears, unrelated captures and even exchanges
are not labels under this definition. King-front and queen-front skewers qualify;
a non-king front need not be in check.

### Discovered attack — `m94-discovered-attack-v1`

The root moves a friendly blocker off a stationary friendly bishop/rook/queen's
ray. Immediately afterward that stationary slider newly attacks an enemy
minor-or-higher piece or king: it did not attack that square before, and the
root's origin lay between slider and target. For a non-king target, on ply three
this stationary slider captures that same, unmoved target with both positive gain
requirements above. For a king target, the newly discovered check must have a
positive engine mate score of 1–3 and a complete legal mating PV of exactly
`2 * mate_in - 1` plies. This includes discovered and double checks when the
stationary slider's check is actually opened. Already open rays, attacks by the
moved slider itself, castling, pawn targets and unsupported geometric threats do
not qualify.

### Back rank — `m94-back-rank-v1`

The root rook or queen gives check from the defending king's home rank (rank 8
for Black, rank 1 for White). The first source score is mate in 1–3, and its
complete legal PV has exactly `2 * mate_in - 1` plies and ends in checkmate.
At mate the defending king is on its home rank, checked by a rook or queen along
that rank. Every on-board forward-neighbor square (the next rank, king file and
adjacent files) is occupied by a defending piece. Thus own-piece confinement,
not merely an engine score or attack map, is proved. Open-flight, diagonal-only,
non-home-rank, truncated, wrong-distance and unscored mating claims are excluded.
The checking root and final mating rook/queen may differ, allowing deflections
and sacrifices in a legal mate-in-two or mate-in-three line.

## Separately specified deep reference

`evidence/motifs.py` generates hypotheses from the **fast source** PV. Detectors
revalidate that raw source proof and its named fields; they do not trust a flag.
`evaluation/m94_reference.py` independently implements label decisions from the
**separately searched million-node source** PV. It imports no composer, evidence,
detector or operational predicate. It reconstructs legal positions, calculates
material independently, enumerates a skewer ray in coordinates, verifies blocker
removal for discoveries, and separately evaluates final mating confinement.
The raw deep source root must also differ from the played move. It never uses
fast evidence, detector flags, fast scores or reply PVs as source labels. Existing
`deep_reference.py` and the M93 oracle remain unchanged. New labels are evaluated
only on M94, never retroactively scored on exposed corpora.

This is an **engine-derived operational reference**, not independent human-coach
validation. Detector and reference searches share engine/evaluation assumptions.
Finite PVs, shallow/deep disagreement and deliberately narrow operational coverage
can produce disagreement; neither a passing gate nor geometry proves coaching
causality.

## Freeze and one-shot recording

`tests/fixtures/m94/source_revisions.json` freezes exact SHA256 source revisions,
including selection/recorder, existing analysis dependencies, taxonomy,
arbitration, composer, new proofs/detectors, independent oracle, replay, support,
explanations, synthetic fixtures/tests and this definition. The protocol module
and revision manifest are additionally frozen by the preregistration commit
(they are not self-hashed). All must be tracked and unchanged against HEAD before
recording. Recheck/regenerate the proposed source digests after any review fix,
then commit once; no amend/rebase after measurement. The source phase makes no
preregistration-commit or fresh-fixture assertion yet.

After parent authorization, run once at that commit with disposable runtime data:

```sh
BROWSER=/usr/bin/true uv run python scripts/record_diagnosis_reference.py \
  /tmp/m78-lichess-2013-01.pgn.zst --m94-fresh80
```

The recorder uses exclusive creation for separate `m94_fresh80_real_games.pgn`,
`m94_fresh80_recorded.json` and `m94_fresh80_manifest.json` under `tests/fixtures/m48`.
Either existing artifact consumes the holdout; it must not restart or overwrite a
partial measurement. **Before exclusive PGN creation**, require Threads 1/Hash 16,
then obtain the adapter's `analysis_identity()` and require Stockfish 19. This UCI
identity handshake performs no position search; a wrong engine/configuration fails
without consuming the holdout. The same adapter is reused, with identity checks
also retained on actual position analyses; all exits close it.

Record-time proof uses Git: all frozen sources, protocol and revision manifest
must be tracked, committed at HEAD and byte-matching before selection/search.
The manifest records that preregistration SHA, protocol/source-revision digests,
recording digest and PGN digest. This is the proof that the measured detector,
oracle and selection were fixed before labels; replay does not reconstruct it from
the caller's local Git object database.

Replay is history-free, engine-free and fail-closed. The data-phase commit pins a
`MeasurementIdentity` in `real_games.py`: exact preregistration SHA and SHA256 of
recording, PGN, manifest, protocol and source-revision manifest. Input artifacts
cannot supply their own expected identities. No identity is pinned in this
unmeasured source phase, so attempted M94 replay fails explicitly. After pinning,
all three artifacts must match those external expected digests; the complete
protocol/revisions envelope, top-level metadata, 80 distinct unexposed identities,
PGN qualifiers and identical recorded moves are still checked. Consistently
rewriting corpus and sibling manifest cannot redefine the accepted measurement.
An ordinary shallow or source-only checkout needs neither a preregistration Git
object nor any optional Git fallback. Publish these immutable observed digests in
the continuation; no fake measurement identities exist in this source phase.

## Gate and visibility

For **each code**, independently report TP, FP, FN, support=TP+FP, precision and
PASS/FAIL. Gate: **precision >=0.80 AND support >=10** on M94 alone. Use the
existing `Score.trusted` gate, all detectors arbitrated in isolation with trust
filtering off. `real_games --check` keeps the original40 three-code, M78 allowed
mate, M79 missed capture and M93 promoted-motif gates on their explicit code sets;
a separate M94 gate checks only promoted M94 codes. Unknown trusted codes fail.
All codes, including hidden ones, remain reportable. Below-gate outcomes are valid:
never tune the detectors, oracle, sample, thresholds or search to force promotion.

Historical Lichess v2/v3 eligibility, outcome validation and output rows use the
literal eleven-code pre-M94 universe. New live detectors cannot participate in
their untrusted-only arbitration or alter their committed result tables.

No M94 code is currently in current trust, historical archive trust or puzzle-theme
weighting. Following measurement, promote **only observed passing codes** in all
three consumers and verify actual patterns/lessons/puzzle behavior and a promoted
archive round trip. If none passes, all remain hidden. Final recording, replay,
artifact pinning, measured promotion and actual-product proofs are unexecuted in
this phase, not waived.

## Saved-input capability (not trust)

The global `m48-fast-local-v1` marker is unchanged. Every newly composed candidate
payload records `diagnosis_capabilities` with one exact per-code revision above,
including negative evaluations. `supports_saved_diagnosis(code, payload)` accepts
only that code's exact marker, independently of the other two markers. Legacy m48
alone cannot claim these new motifs. Unknown codes fail closed. Existing eleven
m48-covered codes retain their established capability and visibility. M95 consumes
only this helper and never the marker layout; whichever lane merges second needs
a focused integration proof. No recurrence/API/web or habit/award behavior changes.
