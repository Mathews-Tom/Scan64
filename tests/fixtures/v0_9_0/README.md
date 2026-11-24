# v0.9.0 compatibility fixtures

These are outputs of the unmodified `v0.9.0` application, commit `982d949`, at Alembic revision `20261117_18`, not old-looking rows made with current models. `provenance.json` records the full commit, Stockfish identification and executable SHA-256, and the committed PGN sources.

The generator starts that checkout with `uv run --locked`, its own lockfile and environment, a loopback ephemeral port, `--no-browser`, `BROWSER=/usr/bin/true`, and database/data directories under `/tmp`. It calls the shipped server coroutine with browser opening disabled; it does not build or exercise the web UI. It creates the player, imports three PGNs, completes real Stockfish analysis, submits one server-verified lesson answer, imports one synthetic legal opening-line puzzle through the shipped puzzle CLI, solves its two player moves through the API, and exports with `POST /v1/exports`. The synthetic puzzle tests progress persistence, not tactical puzzle quality. It shuts down its server process group, checkpoints SQLite's WAL, and copies the database before compressing it.

- `scan64.db.gz`: the historical SQLite database, gzip-compressed for repository size.
- `archive.json.gz`: the actual historical API export (sorted JSON keys; gzip mtime zero).
- `expected.json`: historical API reads plus exported attempts, skill state and schedules, which have no dedicated history endpoint. The test checks both current API reads and preserved due dates, including the legacy-to-FSRS initialization contract.
- `provenance.json`: generator inputs and engine identity.

The recorded run used Stockfish 19. It contains three completed analysis jobs, 98 reviewed plies, 13 lesson opportunities and schedules (one reviewed, interval 2.5 days), one successful verified lesson attempt with its profile update applied, one solved rated puzzle, and two non-empty skill states (`threat_processing.missed_capture` and `board_awareness.hanging_piece`). The compressed database is 111,787 bytes (1,179,648 bytes unpacked); the archive is 68,145 bytes, expected API values 122,432 bytes and provenance 422 bytes. The committed data totals 302,786 bytes before this README.

Reproducing the committed semantic values requires the recorded Stockfish 19 binary, identified by SHA-256 in `provenance.json`. The v0.9.0 desktop and Docker builds bundled Stockfish 18; that engine produces different analysis values. These fixtures prove compatibility with data produced by the v0.9.0 source release using the recorded engine, not equality with its bundled-engine analysis.

No raw bearer token is retained. The historical credential hashes are random test-player hashes, not credentials for a real installation. Each test generates a fresh token and substitutes only its hash before exercising the authenticated restore/read paths.

## Regeneration

Run from the current checkout. Choose new output directories; the generator refuses to overwrite an existing one. Stockfish must be installed locally; there are no puzzle or game downloads.

```sh
git worktree add --detach /tmp/scan64-m109-v090 v0.9.0
BROWSER=/usr/bin/true SCAN64_DATABASE_URL=sqlite:////tmp/scan64-m109-generator.db SCAN64_DATA_DIR=/tmp/scan64-m109-generator uv run --locked python scripts/compat/make_v090_fixture.py /tmp/scan64-m109-v090 /tmp/scan64-m109-fixture-run1 --stockfish /opt/homebrew/bin/stockfish
BROWSER=/usr/bin/true SCAN64_DATABASE_URL=sqlite:////tmp/scan64-m109-generator.db SCAN64_DATA_DIR=/tmp/scan64-m109-generator uv run --locked python scripts/compat/make_v090_fixture.py /tmp/scan64-m109-v090 /tmp/scan64-m109-fixture-run2 --stockfish /opt/homebrew/bin/stockfish
diff -qr /tmp/scan64-m109-fixture-run1 /tmp/scan64-m109-fixture-run2
git worktree remove /tmp/scan64-m109-v090
```

Binary byte identity is not the reproducibility contract: original UUIDs, evidence references containing those UUIDs, wall-clock timestamps/dates, credential hashes, temporary import-source paths, UUID-dependent list ordering and time-decayed skill values are retained, not rewritten. Consequently the raw SQLite pages and compressed archive differ between runs. Preserve the original fields to exercise real historical restore input rather than a hand-normalized substitute. Compare semantic progress (PGNs, completed analysis, verified lesson outcome, puzzle moves/result/rating, skill values and schedule intervals) separately; exact due dates from the chosen run must survive both upgrade and restore.

Two observed generations had byte-identical provenance, identical PGNs, all 98 ordered review-move projections exactly equal, three completed jobs, the same verified/applied lesson move (`d4e5`), the same solved puzzle moves and displayed rating (1208; one rated run), and the same multiset of skill/interval/ease/reviewed schedule values. The largest alpha/beta difference was 0.00002612684392921949 from elapsed-time decay; skill values agree to three decimals. `diff -qr` reports differences in the database, archive and expected JSON, as expected for the fields above. Run 2 sizes were 111,808 / 68,352 / 122,429 bytes for database / archive / expected JSON. Tests use exact run-1 values, with no relaxed float tolerances on upgrade or restore.

The compatibility test is engine-free and offline. It does not accept a published installer, prove old 0.6.x data migration, or claim that every intermediate application release was regenerated.
