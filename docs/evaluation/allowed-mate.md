# Allowed checkmate: implementation and evaluation boundaries

`threat_processing.allowed_mate` describes a move after which the opponent has an engine-scored forced mate in one, two or three moves, when the mover was not already facing a forced mate before playing it. Ignoring an existing **avoidable** mate threat qualifies; playing on in an already forced mate does not.

## Proof and precedence

The normal 10,000-node fast pass and its 150cp flag threshold are unchanged. For a flagged reply scored as mate in 1–3, game analysis and the CLI additionally search the **source** position at 1,000,000 nodes, MultiPV 4. Interactive coach analysis uses the same source budget within its existing timeout. The source score must be centipawns or a positive mate score for the mover; a negative or zero mate score is excluded. Missing, unscored or wrong-budget source evidence cannot establish the new diagnosis or suppression. The reply's complete PV must legally replay to checkmate in the scored number of moves. This is engine-derived evidence, not a mathematical proof of exhaustive search or an independent coach's judgment.

Evidence stores the fast reply, the source-depth score, source engine-analysis ID/config/MultiPV, and the mating PV. Source analyses are persisted alongside the other engine analyses, without a database schema change. A source search is reused by M47's sound-move lesson path and widened to eight lines only when necessary.

A confirmed allowed-mate event suppresses `board_awareness.hanging_piece`, `threat_processing.missed_direct_threat` and `threat_processing.missed_capture`. If the new code has not cleared the trust gate, the trusted pipeline abstains entirely for that move instead of substituting another label. The ordinary scored move remains in review. If trusted, the new event competes with remaining causes under the existing confidence rule and beats the three material labels. There are no secondary diagnoses.

The deterministic template independently revalidates the source-depth proof and legally replays the mating PV, showing it in SAN through `#`. Its lesson asks for a sound move from the pre-move position that does not allow mate. M47's probability tolerance is unchanged, but a negative-mate alternative cannot satisfy this additional objective, even when both win probabilities are close to zero. Generation and re-verification apply the same filter. UI review/import labels name it **Allowed checkmate**; profile/report names come from its taxonomy seed. Archive habit checks use the same canonical trusted-code allowlist as runtime habit creation.

## Why the source-depth correction is necessary

The initial development measurement on the original 40 M48 games emitted 15 allowed-mate events: 11 true positives and 4 false positives (precision 0.733). Every false positive had a pre-existing forced mate discovered by the million-node source reference but missed by the 10,000-node source search. Exact game IDs, plies, scores and lines are preserved in [the M48 report](real-game-diagnosis.md).

Checking the absence of a pre-existing forced mate only in the fast pass did not faithfully implement the definition. The owner approved the focused-source correction and material-label suppression on 2026-11-14. This is a definition-faithful correction, **not** a changed threshold or optimization against held-out labels. The original 40 games remain development data and cannot decide trust after this correction. A separately committed preregistration fixes exactly the next 80 qualifying source games, in file order, as the fresh trust sample. Precision must be at least 0.80 with support at least 10 on those 80 alone; insufficient support does not trigger another extension in this PR.

M77 v1 and pre-fix v2 remain frozen and reproducible. V3 uses the same frozen sample and v2 mapping, with separately preregistered source-depth recordings and the corrected pipeline. None of these Lichess-theme proxy agreements can promote trust.

## Practice mapping

No `THEME_SKILLS` entry is added. Existing mate puzzle themes describe delivering mate from the solver's position, not preventing a mate after one's own blunder. The broad `defensiveMove` theme also does not establish that its solution averts mate. Those puzzles may be related practice, but mapping this diagnosis to them would imply specificity not supported by the tags. This omission is independent of the M77 proxy mapping: agreement labels and pedagogical practice routing answer different questions.

## Recorded hand-built tests

`tests/fixtures/allowed_mate.json` records Stockfish 18, one thread, 16 MiB hash, reset before each request. It contains hand-built positions allowing mate in 1/2/3, an already forced mate, a parried threat, and a material-only loss; source-depth searches are recorded separately from fast searches and M47 source searches. Tests cover both player colours, complete legal mating claims, precedence, untrusted abstention, the template, and sound-move lesson generation/re-verification. Deliberately conflicting shallow test input checks that a recorded deep forced-mate score cannot be overruled by a shallow non-mate score.
