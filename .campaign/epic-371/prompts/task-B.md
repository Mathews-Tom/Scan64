Working on making the Scan64 analysis engine swappable by configuration, with full access to engine variations.
Repo: /home/syd/projects/Scan64 (you are in a worktree of it)
Branch: feat/360-swappable-engine (already checked out in your worktree; do not switch branches)

Issue: #360 — Make the analysis engine swappable by configuration, with full variation access
Read the issue body first: `gh issue view 360 -R Mathews-Tom/Scan64`.

Task:
- Map every Stockfish-specific assumption outside the engine provider: names, env vars, identity and cache keys (identity.py), UCI options (Threads, Hash, Skill Level and others used by opponents in src/scan64/chess/opponents), score handling, MultiPV, limits, pool. Use `grep -rn -i "stockfish" src/` as a start. Put the map in docs/engines.md.
- Add an engine configuration: engine name, binary path, UCI options, and per-use limits. Load it from a TOML file selected by an env var (for example SCAN64_ENGINE_CONFIG), and keep SCAN64_STOCKFISH_PATH working. Stockfish 18 stays the default with no config.
- Generalise the adapter and pool so any UCI engine works through that config. Keep the existing EngineAdapter protocol and class names working (aliases are fine).
- Extend EngineAnalysis.raw_result per line with: pv (full), score cp/mate, wdl when the engine reports it, depth, seldepth, nodes, nps, multipv index. Old stored rows without these fields must still load. Engine identity keys must not make existing caches return wrong data; a new engine must never hit a Stockfish cache entry.
- Opponent strength features (Skill Level, UCI_Elo) can stay Stockfish-only; document which features need which UCI options and degrade cleanly when an engine lacks them.
- Second engine: build one open-source UCI engine from source into ~/.local/opt/engines/ (for example Berserk https://github.com/jhonnold/berserk or Ethereal https://github.com/AndyGrant/Ethereal, both GPL-3.0). Run the full analysis path with it through config only. Add an integration test that skips when the binary is missing.
- docs/engines.md: also add a comparison table of open-source UCI engines (Stockfish, Lc0, Berserk, Ethereal, Obsidian, Koivisto or others you verify): licence, UCI support, WDL output, MultiPV, and observed PV length on 5 fixed positions at equal node budgets. Measure with a saved script under benchmarks/engines/ with a TOML config.
- Update docs/system-design.md (engine capacity and configuration table).

Target: the second engine analyses a stored game end to end through config only; all existing engine tests pass; new tests cover config loading, extended fields, legacy rows, and cache identity separation.
