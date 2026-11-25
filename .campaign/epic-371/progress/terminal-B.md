---
terminal: B
title: "Engine"
campaign: epic-371
wave: 1
status: complete
branch: feat/360-swappable-engine
writes_to: "src/scan64/providers/stockfish/, src/scan64/providers/engine/ (new), src/scan64/chess/analysis/models.py, engine config, engine tests, benchmarks/engines/ (new), docs/system-design.md, docs/engines.md (new)"
issue_refs: "#360"
target: "Second engine runs full analysis via config only"
blocked_by: []
started: 2026-11-25T22:32:24Z
updated: 2026-11-25T23:10:26Z
---

# Terminal B — Engine

## Issues

| Issue | Task | Target | Status | Actual |
|-------|------|--------|--------|--------|
| #360 | Make the analysis engine swappable by configuration, with full variation access | Second engine runs full analysis via config only | done | 4 commits (6444d9b, facac5c, 16444a8, 56889d7): providers/engine (config, uci, adapters); generalised stockfish adapter/pool/identity; EngineLine + extended raw_result; config/engines/*.toml (3); benchmarks/engines (script, toml, 1 recorded run, 5 engines); docs/engines.md; docs/system-design.md. Tests: test_engine_config.py 26 pass, test_second_engine.py 2 pass (Ethereal). PR #377 |

## Results

Shipped (PR https://github.com/Mathews-Tom/Scan64/pull/377, base epic/371):
- Engine profile via SCAN64_ENGINE_CONFIG (TOML: name, binary_path, threads, hash_mb, [engine.options], [limits.hint]); SCAN64_STOCKFISH_PATH kept; Stockfish default identity unchanged.
- StockfishConfig = EngineConfig; UciEngineAdapter / PooledUciEngineAdapter aliases; declared-only Threads/Hash/strength reset; undeclared profile options rejected; EngineCapabilityError for ladder / Skill Level on non-Stockfish engines.
- raw_result lines add wdl, depth, seldepth, nodes, nps, multipv; EngineAnalysis.lines() -> EngineLine, legacy rows load with None.
- Ethereal 14.40, Berserk 20261114, Obsidian dev-16.15, Koivisto 9.2 built from source (zig cc) in ~/.local/opt/engines/.
- docs/engines.md (assumption map, profile, identity, feature/UCI table, comparison); docs/system-design.md engine table.

Test commands:
- uv run pytest tests/unit/test_engine_config.py tests/integration/test_second_engine.py -> 28 passed
- engine-area suites (bot_ladder, engine_pool, hint_pool, prerelease_defects, analysis_throughput, play_assistance*, stockfish_pipeline, engine_pool_isolation, analysis_reuse, position_analysis_api, opponent_provider) -> 210 passed
- full pytest -m "not real_model" in 8 shards -> 3589 passed, 13 failed, 3 skipped. All 13 pre-existing/environment: 8 M93/M94/real-game checksum tests (CRLF checkout, core.autocrlf=true); test_web_serving::test_real_assets_and_missing_assets, test_puzzle_import_job::test_cancel_interrupts_stalled_http_stream, test_maia_coverage_disclosure (Windows '/operator' not absolute), test_desktop_package_smoke interpreter — these 4 also fail on base 8f00711.
- ruff check, ruff format --check, mypy --strict src/, check_licenses: pass
- real_games --check: fails "M93 recording checksum differs" (CRLF, pre-existing)
- web: build, lint, 818 unit, 104 e2e: pass
- cargo fmt pass; cargo clippy/test: "scan64-desktop@1.0.0 requires rustc 1.95" (machine has 1.94.1), pre-existing

Follow-ups: separate opponent engine from analysis engine (app.py/play.py); cli/serve.py checks `stockfish` on PATH; exact_replay.py engine label; per-use budgets for review/focused/verification (Lc0); map EngineCapabilityError to 4xx/503.

## Log
- 2026-11-25T22:32:24Z started
- 2026-11-25T22:45:48Z 6444d9b feat(engine): config-selected UCI engine, extended lines, capability errors; Ethereal + Berserk built with zig cc into ~/.local/opt/engines; PR https://github.com/Mathews-Tom/Scan64/pull/377
- 2026-11-25T22:51:36Z facac5c benchmark (5 engines measured), 16444a8 docs/engines.md + system-design.md; running scripts/check.sh
- 2026-11-25T22:54:58Z 56889d7 fix(engine): hint limits reject depth at load. Gate so far: ruff/format/mypy pass, licences pass, web build/lint/818 unit/104 e2e pass. Pre-existing: real_games --check fails "M93 recording checksum differs" (core.autocrlf=true checks out CRLF; committed blob sha d4eb9a8e matches M93_RECORDING_SHA256, disk sha 0538477d); cargo clippy/test fail "scan64-desktop@1.0.0 requires rustc 1.95" (machine rustc 1.94.1). pytest sharded 8-way (full serial run exceeds 2 h with other terminals' suites on the machine).
- 2026-11-25T23:10:26Z complete: PR #377 body updated with gate results; issue #360 commented
