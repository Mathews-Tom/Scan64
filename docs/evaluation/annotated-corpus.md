# Annotated-game corpus

Built 2026-11-28 by `scripts/build-annotated-corpus.py` from `scripts/annotated-corpus.toml` (issue #463).
The corpus itself (`games.jsonl`, `rejections.jsonl`) is written outside the repository.

| Source | Games found | Replayed | Rejected | Comments | Sentences | Moves | Decided by missing "ch" |
|---|---:|---:|---:|---:|---:|---:|---:|
| Chess Fundamentals (1921) | 14 | 7 | 7 (50%) | 110 | 240 | 523 | 1 |
| Chess Strategy (second edition, translated by J. du Mont) (1915) | 34 | 23 | 11 (32%) | 362 | 709 | 1495 | 7 |
| Lasker's Manual of Chess (1927) | pending | | | | | | |
| My System (English translation) (1929) | pending | | | | | | |
| The Modern Chess Instructor (1889) | pending | | | | | | |

## Rejections by reason

- `capablanca_chess_fundamentals`: ambiguous 6, move number 1.
- `lasker_chess_strategy`: ambiguous 2, move number 4, no legal move fits 3, not a descriptive move 2.
- `em_lasker_manual`: pending. No public-domain plain text found; scans only. Needs a transcription source.
- `nimzowitsch_my_system`: pending. No public-domain plain text found; scans only. Needs a transcription source.
- `steinitz_modern_chess_instructor`: pending. No public-domain plain text found; scans only. Needs a transcription source.

A move converts only when exactly one legal move fits its descriptive text. When a
text fits both a checking and a non-checking move and carries no "ch", the
non-checking move is taken; the last column counts those moves. Any other ambiguity
rejects the whole game.
