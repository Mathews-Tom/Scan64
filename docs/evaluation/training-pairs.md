# Verified training pairs

Built 2026-11-28 by `scripts/build-training-pairs.py` from the annotated corpus (#463), issue #464. Settings: `scripts/training-pairs.toml`.
The pairs themselves are written outside the repository.

| Source | Games (held out) | Sentences | Kept | Trimmed | Dropped | Held-out pairs |
|---|---:|---:|---:|---:|---:|---:|
| Chess Fundamentals | 7 (0) | 234 | 0 | 0 | 234 | 0 |
| Chess Strategy (second edition, translated by J. du Mont) | 30 (2) | 878 | 1 | 1 | 876 | 0 |

Audit sample for the blind judge: 2 pairs (target 200, seed 464).

## Dropped and trimmed, by reason

| Reason | Chess Fundamentals | Chess Strategy (second edition, translated by J. du Mont) |
|---|---:|---:|
| no move before the comment | 0 | 29 |
| no chess content | 81 | 248 |
| descriptive square or piece | 16 | 113 |
| move not in the facts | 63 | 253 |
| move ambiguous | 17 | 71 |
| engine: level | 1 | 0 |
| unsupported term | 27 | 93 |
| unsupported judgement | 9 | 13 |
| unsupported piece or square | 8 | 27 |
| capture without a capture fact | 0 | 1 |
| no move or square of the facts | 9 | 25 |
| more than one sentence | 3 | 4 |

A trimmed sentence is counted under the reason its cut part failed.

## Unmatched chess terms

Terms a sentence names that no fact for its position backs, by number of sentences. This ranking feeds the next concepts in #459.

| Term | Sentences |
|---|---:|
| threat | 76 |
| develop | 74 |
| exchange | 66 |
| prevent | 47 |
| advantage | 43 |
| castl | 42 |
| defen | 37 |
| better | 32 |
| chance | 31 |
| centre | 30 |
| opening | 29 |
| weak | 28 |
| attack | 25 |
| variation | 24 |
| best | 23 |
| plan | 22 |
| draw | 21 |
| diagonal | 21 |
| sacrific | 20 |
| file | 20 |
| mate | 19 |
| avoid | 16 |
| superior | 16 |
| strong | 15 |
| winning | 13 |
| safe | 9 |
| idea | 9 |
| pin | 9 |
| powerful | 8 |
| mistake | 8 |
| escape | 8 |
| gambit | 6 |
| compensat | 5 |
| hope | 5 |
| initiative | 5 |
| stronger | 5 |
| simple | 5 |
| forced | 5 |
| promising | 4 |
| material | 4 |
| brilliant | 4 |
| beautiful | 4 |
| worse | 3 |
| weaker | 3 |
| inferior | 3 |
| subtle | 3 |
| wise | 3 |
| blunder | 2 |
| complicat | 2 |
| dangerous | 2 |
| losing | 2 |
| elegant | 2 |
| decisive | 2 |
| quicker | 1 |
| bold | 1 |
| error | 1 |
| promot | 1 |
| passive | 1 |
| aggressive | 1 |
| sharp | 1 |
| fork | 1 |
| only move | 1 |
| transpos | 1 |
| footing | 1 |
| deadly | 1 |
