# Verified training pairs

Built 2026-11-28 by `scripts/build-training-pairs.py` from the annotated corpus (#463), issue #464. Settings: `scripts/training-pairs.toml`.
The pairs themselves are written outside the repository.

| Source | Games (held out) | Sentences | Kept | Trimmed | Dropped | Held-out pairs |
|---|---:|---:|---:|---:|---:|---:|
| Chess Fundamentals | 7 (0) | 234 | 0 | 0 | 234 | 0 |
| Chess Strategy (second edition, translated by J. du Mont) | 30 (2) | 878 | 1 | 0 | 877 | 0 |

Audit sample for the blind judge: 1 pairs (target 200, seed 464).

## Dropped and trimmed, by reason

| Reason | Chess Fundamentals | Chess Strategy (second edition, translated by J. du Mont) |
|---|---:|---:|
| no move before the comment | 0 | 29 |
| no chess content | 80 | 193 |
| descriptive square or piece | 19 | 193 |
| move not in the facts | 73 | 278 |
| move ambiguous | 11 | 53 |
| engine: level | 1 | 0 |
| unsupported term | 13 | 53 |
| unsupported judgement | 2 | 4 |
| unsupported piece or square | 1 | 10 |
| no move or square of the facts | 7 | 9 |
| more than one sentence | 1 | 3 |
| played move is not the game move | 4 | 2 |
| name or date | 17 | 25 |
| negation | 5 | 25 |

A trimmed sentence is counted under the reason its cut part failed.

## Unmatched chess terms

Terms a sentence names that no fact for its position backs, by number of sentences. This ranking feeds the next concepts in #459.

| Term | Sentences |
|---|---:|
| would | 134 |
| threat | 76 |
| develop | 74 |
| exchange | 66 |
| los | 58 |
| prevent | 47 |
| advantage | 43 |
| cannot | 42 |
| castl | 42 |
| should | 40 |
| defen | 37 |
| must | 34 |
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
| bad | 14 |
| winning | 13 |
| safe | 9 |
| idea | 9 |
| pin | 9 |
| powerful | 8 |
| mistake | 8 |
| escape | 8 |
| gambit | 6 |
| recapture | 6 |
| compensat | 5 |
| hope | 5 |
| initiative | 5 |
| stronger | 5 |
| simple | 5 |
| forced | 5 |
| promising | 4 |
| careful | 4 |
| material | 4 |
| brilliant | 4 |
| beautiful | 4 |
| worse | 3 |
| weaker | 3 |
| inferior | 3 |
| resign | 3 |
| subtle | 3 |
| wise | 3 |
| blunder | 2 |
| complicat | 2 |
| fall | 2 |
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
| wary | 1 |
| deadly | 1 |
