| Model | n | Faithful (±95%) | Faithful and lively | Lively | Clear | Jargon | Narrator source | Fallback sentences | Calls per explanation | p50 s | p95 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-e4b Q4_K_S | 100 | 99% ±2 | 89% | 1.90 | 2.99 | 0 | 100/100 | 0.4% | 6.2 | 0.82 | 1.08 |

| Position | Faithful | Faithful and lively |
|---|---|---|
| H1-missed-mate | 24/25 | 24/25 |
| H3-stalemate | 25/25 | 24/25 |
| H4-pin | 25/25 | 24/25 |
| H5-allowed-fork | 25/25 | 17/25 |

Errors:
- H1-missed-mate: "now snags the queen on g5": the knight only attacks the queen, not captures it
