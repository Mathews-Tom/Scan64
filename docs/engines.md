# Analysis engines

Scan64 analyses with one UCI engine selected by configuration. Stockfish 18 is
the default and the only engine bundled by the Dockerfile and desktop build.
Any other open-source UCI engine can replace it for analysis, review, hints,
lessons and narrator evidence without a code change.

## Selecting an engine

| Variable | Default | Effect |
| --- | --- | --- |
| `SCAN64_ENGINE_CONFIG` | unset | Absolute path of an engine profile (TOML). When set, it fully defines the engine. |
| `SCAN64_STOCKFISH_PATH` | `stockfish` on `PATH` | Stockfish binary used when no profile is selected. Ignored when `SCAN64_ENGINE_CONFIG` is set. |

Without a profile the engine is Stockfish from `SCAN64_STOCKFISH_PATH`, one
thread, 16 MB hash, no extra options: the same identity as before profiles
existed, so saved analyses keep matching.

A profile:

```toml
[engine]
name = "ethereal"                                  # label for logs; not part of cache identity
binary_path = "~/.local/opt/engines/Ethereal.exe"  # absolute (~ expands) or a bare command on PATH
protocol = "uci"                                   # optional: "uci" (default) or "xboard"
threads = 1                                        # sent as Threads when the engine declares it
hash_mb = 16                                       # sent as Hash when the engine declares it
node_scale = 1.0                                   # optional, in (0, 1]: nodes sent = budget × node_scale

[engine.options]          # extra UCI options; each must be declared by the engine
UCI_ShowWDL = true

[limits.hint]             # optional per-use override (nodes and/or time_ms, multipv; no depth)
nodes = 200000
time_ms = 1500
multipv = 4
```

Examples: [`config/engines/`](../config/engines/) (`ethereal.toml`,
`berserk.toml`, `stockfish-wdl.toml`).

Rules (all enforced at load or engine start, failing with the file and key):

- Unknown tables/keys, a relative `binary_path` containing a directory, a
  missing or relative profile path, and non-positive numbers are rejected.
- `engine.options` may not set Scan64-managed options: `Threads`, `Hash`,
  `MultiPV`, `Ponder`, `UCI_AnalyseMode`, `UCI_Chess960`, `UCI_Variant`,
  `UCI_Opponent`, `UCI_RatingAdv`, `Skill Level`, `UCI_LimitStrength`, `UCI_Elo`.
- An option in `engine.options` that the engine does not declare stops engine
  start with `EngineConfigurationError`.
- `Threads`/`Hash` are sent only to engines that declare them (Lc0 has no `Hash`).
- The only per-use override today is `hint` (interactive hints; it still runs
  inside the three-second hint deadline). Review (10,000 nodes), focused passes,
  lesson verification and mate-source searches keep the budgets in their callers.
- `engine.node_scale` (default 1) scales every node budget Scan64 sends to this
  engine: game analysis, focused passes, hints, lesson verification, Analysis
  Board lines, the bot ladder and Skill Level play. The budgets are sized for an
  alpha-beta engine's nodes/second; an engine with slow neural nodes (Lc0,
  Maia-3) sets a value below 1, for example `0.002` so that a 1,000,000-node
  focused search sends 2,000 nodes. The engine receives at least 1 node. Stored
  rows keep the Scan64 budget in `nodes` and record `node_scale` when it is not
  1, so rows from different scales never reuse each other; rows at scale 1 keep
  their pre-existing reuse key.
- `engine.protocol = "xboard"` runs an XBoard (protover 2) engine. The engine
  must declare `ping` and `setboard`; one that sends `setboard=0` (Fairy-Max
  5.0b) fails at start with `EngineCapabilityError`, since every Scan64 search
  starts from a FEN. Scan64 sends `engine.threads` as `cores`
  and `engine.hash_mb` as `memory` when the engine declares `smp`/`memory`.
  XBoard has no MultiPV, so only single-line searches work: game review and
  single-line analysis run; a search for more lines (focused passes, Analysis
  Board lines, hints with MultiPV, the bot ladder) raises
  `EngineCapabilityError` naming the engine, never a single-line substitute.
  Stored rows record `protocol = "xboard"`; UCI rows omit the key.
- Any request the active engine cannot serve answers HTTP 503 with
  `{"detail": {"code": "engine_capability_unavailable", "message": ..., "reason": ...}}`;
  `reason` names the missing option and the engine. A background analysis job
  that hits it ends `failed` with that reason as its `error`.
- The profile file is re-read when its modification time or size changes; a
  running pool keeps the processes it started with.

## What every stored line contains

`EngineAnalysis.raw_result` is one dict per MultiPV line, best first. Keys appear
only when the engine reported them (never `null`):

| Key | Meaning |
| --- | --- |
| `pv` | Full principal variation, UCI moves. |
| `score_cp` / `score_mate` | Score from the side to move (exactly one). |
| `wdl` | `[wins, draws, losses]` per mille from the side to move. |
| `depth`, `seldepth` | Nominal and selective search depth. |
| `nodes`, `nps` | Nodes searched and speed. `nps` depends on wall-clock time; never compare or key on it. |
| `multipv` | 1-based line index. |

Rows written before this extension contain only `pv` and a score. They load
unchanged; `EngineAnalysis.lines()` returns typed `EngineLine` values whose
extended fields are `None` for them. No migration is needed: the column is JSON.

Stockfish reports WDL only with `UCI_ShowWDL = true` (see
`config/engines/stockfish-wdl.toml`). That option is part of the engine
identity, so WDL-enabled rows never reuse WDL-free rows and vice versa.

## Cache identity

Batch reuse (`scan64/chess/analysis/reuse.py`) and the per-process hint cache
are keyed on the effective engine inputs:

- `engine_version`: SHA-256 of the executable (embedded networks included).
- `network`: SHA-256 of external network/weights files named by `EvalFile`,
  `EvalFileSmall` or `WeightsFile` when those files exist.
- `options`: every UCI option value the process holds after configuration,
  including profile options, with the full-strength overrides applied.
- `nodes`, `depth`, `time_ms`, `multipv` and the full FEN.

A different engine has a different executable digest and option set, so it
never matches a Stockfish row, even if it reports the same `id name`. This is
covered by `tests/unit/test_engine_config.py` (key separation) and
`tests/integration/test_second_engine.py` (Stockfish rows, then Ethereal rows
for the same owner and positions: no reuse). The profile `name` is not part of
the key. The hint cache key includes the profile fingerprint (name, binary,
threads, hash, options) and the effective hint limits.

## Feature requirements by UCI option

| Feature | Requires | Without it |
| --- | --- | --- |
| Fast pass, review, backfill | any UCI or XBoard engine | — |
| Focused pass, hints, Analysis Board lines, lesson verification (MultiPV 2–8) | `MultiPV` (UCI only) | `EngineCapabilityError` (API: 503 `engine_capability_unavailable`; job: `failed` with the reason). No single-line substitute. |
| WDL in stored lines | engine reports `wdl` (Berserk by default; Stockfish/Lc0 with `UCI_ShowWDL`) | `wdl` key absent. |
| Full-strength reset before analysis | `Skill Level`, `UCI_LimitStrength` where declared (`UCI_Elo` is not sent: it is ignored while `UCI_LimitStrength` is off) | Skipped: an engine without these controls is always at full strength. |
| Bot ladder opponent (levels 1–8) | `MultiPV` plus Stockfish's `Skill Level`, `UCI_LimitStrength` | `EngineCapabilityError` naming the missing options. |
| Legacy Skill Level opponent (sessions without `bot_level`) | `Skill Level` | `EngineCapabilityError` naming `Skill Level`. |

Opponent strength stays Stockfish-only on purpose: the ladder's error budgets,
`BOT_POLICY_VERSION = "stockfish-error-budget-v1"` and the strength estimate
cohorts (`api/strength.py`) are defined against Stockfish. Opponent moves come
from the same interactive pool as hints. Bot play is therefore unavailable under
a non-Stockfish profile. Play never falls back to Stockfish. The play endpoints
(`POST /v1/play-sessions` when the bot moves first, `…/moves`, `…/resume` and
`…/opponent-move`) return 503 with
`detail = {code: "bot_play_unavailable", message, reason}`. `reason` is the
`EngineCapabilityError` text that names the missing options. The player's move
stays committed; a bot-first session is not created. The web client shows
that bot play is unavailable under the active engine profile.

## Stockfish-specific assumptions outside the engine provider

Found with `grep -rn -i stockfish src/` plus review of score, limit and option
handling. "Profile-driven" means the code constructs `StockfishConfig()`, which
is now `EngineConfig` and reads the active profile.

| Location | Assumption | Status |
| --- | --- | --- |
| `api/app.py:74` | Pools built from `StockfishConfig()` | Profile-driven. |
| `chess/analysis/jobs.py:131,470`, `api/learning.py:141`, `cli/analyse.py:46` | Per-call `StockfishAdapter(StockfishConfig())` | Profile-driven. |
| `chess/analysis/orchestration.py`, `reuse.py`, `learning/exercises/sound_moves.py` | Import `EngineAdapter` protocol from `providers.stockfish.adapter` | Engine-neutral protocol; also exported from `providers.engine.adapters`. |
| `providers/stockfish/identity.py` | `engine_name` fallback `"Stockfish"`; networks only `EvalFile`/`EvalFileSmall` | Fallback is now the executable stem; `WeightsFile` added. Stockfish identity unchanged. |
| `providers/stockfish/pool.py` (before this change) | `Skill Level`/`UCI_LimitStrength`/`UCI_Elo`/`Threads`/`Hash` sent unconditionally | Sent only when declared; profile options validated. |
| `chess/opponents/bot_policy.py` | Configures `Skill Level 20`, `UCI_LimitStrength`, `UCI_Elo 1320`; error text names Stockfish | Stockfish-only; pool converts the failure into `EngineCapabilityError`. |
| `chess/opponents/stockfish_opponent.py` | Legacy path clamps strength to 0–20 `Skill Level`; non-pool path calls `popen_uci` and sets Stockfish options directly | Stockfish-only. |
| `api/play.py:51,69–74,248–262`, `chess/games/play_session_service.py:100–162`, `chess/games/participants.py:28–39`, `api/data_lifecycle.py:1989`, `api/strength.py:29–122` | Opponent provider name `"stockfish"`, display names "Stockfish (level …)", strength cohorts | Opponent labels; unchanged (Stockfish-only opponents). |
| `chess/games/clocks.py:266–285` | Clocked opponent uses `StockfishOpponentProvider(StockfishConfig())` | Same as opponents. |
| `learning/tolerance.py:11` | `win_probability` uses the Lichess logistic on `score_cp` | Assumes Stockfish-scale centipawns. Ethereal and Berserk declare a `Normalize` option (default on); other engines' scales can differ. Not engine-adjusted. |
| `chess/analysis/orchestration.py:91–102`, `jobs.py` (swing 150 cp), `learning/diagnosis/arbitration.py` ("Eval drop >= 2.0") | Centipawn thresholds and mate-to-score mapping | Same score-scale assumption. |
| `chess/analysis/review.py:12` (`REVIEW_NODES = 10_000`), hint 200k nodes / 1.5 s, focused-pass nodes, `learning/evidence/composer.py` (mate source `nodes == 1_000_000`) | Node budgets sized for Stockfish's nodes/second | Hint budget overridable per profile; every budget scales by `engine.node_scale` for engines with slow nodes (Lc0, Maia-3). |
| `learning/exercises/exact_replay.py:42` | `Verification(engine="stockfish")` label | Label only; does not record the actual engine. Follow-up. |
| `learning/evaluation/m93_protocol.py:18`, `m94_protocol.py:30`, `lichess_themes.py:718` | Measurement protocols pinned to "Stockfish 19" | Intentional: frozen evaluation protocols. |
| `cli/serve.py` | Checks the active profile's binary: `stockfish` on `PATH` (or `SCAN64_STOCKFISH_PATH`) for the default profile, the profile's `binary_path` otherwise | Profile-driven; the error names the profile that needs the missing binary. |
| `cli/main.py:27`, `cli/bots.py` | `calibrate --stockfish` and calibration output | Calibration is Stockfish-only by design. |
| `Dockerfile`, `scripts/build-server.py`, `scan64-server.spec`, desktop shell | Bundle and pin Stockfish 18 + networks; pass `SCAN64_STOCKFISH_PATH` | Bundling unchanged; other engines are operator-provided. |
| `SCAN64_ENGINE_POOL_*` | Pool sizes | Engine-neutral. |

## Building a second engine

No C toolchain is assumed. The binaries used for the measurements below were
built on Windows 11 x64 with Zig 0.17.1's bundled clang
(`uv tool run --from ziglang python -m ziglang cc`, target
`x86_64-windows-gnu`, AVX2 flags) into `~/.local/opt/engines/`:

| Engine | Commit | Build |
| --- | --- | --- |
| Ethereal 14.40 | `0e47e9b` | `cc -std=gnu11 -O3 -DNDEBUG -DUSE_NNUE=0 -DUSE_POPCNT -DUSE_PEXT -DUSE_AVX2 -mavx2 -mfma -mbmi2 -mpopcnt src/*.c src/pyrrhic/tbprobe.c -lpthread -lm` (classical evaluation; no network download) |
| Berserk 20261114 | `1d2354d` | makefile `SRC` list with `-DEVALFILE="berserk-6fd12bbd95c8.nn"` (network downloaded from `jhonnold/berserk-networks`, SHA-256 prefix verified, embedded) |
| Obsidian dev-16.15 | `720614a` | `c++ -std=c++17 -O3 -DEvalFile="net89perm.bin" src/*.cpp` + `src/fathom/src/tbprobe.c` |
| Koivisto 9.2 | `6de225a` | `c++ -std=c++17 -O3 -DEVALFILE="../networks/default.net"` over `src_files/*.cpp`, networks submodule |

Then point a profile at the binary (for example `config/engines/ethereal.toml`)
and set `SCAN64_ENGINE_CONFIG`. `tests/integration/test_second_engine.py` runs a
stored game through the production analysis job and the hint pool with
Ethereal (or `SCAN64_TEST_SECOND_ENGINE`) and skips when no binary exists.

## Comparison of open-source UCI engines

Measured with [`benchmarks/engines/compare_engines.py`](../benchmarks/engines/compare_engines.py)
and [`engines.toml`](../benchmarks/engines/engines.toml): five fixed positions,
1,000,000 nodes, MultiPV 3, one thread, 64 MB hash, `UCI_ShowWDL` on where
declared, fresh game per position. Raw lines:
`benchmarks/engines/results/results_20261125_224843.jsonl`. One run on one
machine; PV length depends on the node budget and on when each engine sends its
last `info` line (Ethereal and Berserk report the last completed iteration, so
their final `nodes` are 65–98 % of the budget). This is variation length and
detail only, not playing strength.

| Engine | Licence | UCI | MultiPV | WDL | Strength options | PV length per line: start / Ruy Lopez / Kiwipete / Lucena / mate-in-one | Median PV length | Median depth / seldepth |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stockfish 18 | GPL-3.0 | yes | yes | with `UCI_ShowWDL` | Skill Level, UCI_LimitStrength, UCI_Elo | 21-16-18 / 25-1-16 / 19-26-17 / 18-13-14 / 1-23-23 | 18 | 19 / 33 |
| Ethereal 14.40 (classical) | GPL-3.0 | yes | yes | no | none | 16-18-17 / 17-14-16 / 17-18-17 / 29-32-29 / 1-15-19 | 17 | 15 / 29 |
| Berserk 20261114 | GPL-3.0 | yes | yes | yes (default on) | none | 18-19-21 / 15-18-17 / 13-21-24 / 22-22-32 / 1-21-20 | 20 | 18 / 24 |
| Obsidian dev-16.15 | GPL-3.0 | yes | yes | no | none | 2-18-4 / 1-4-20 / 2-5-3 / 25-25-1 / 1-22-18 | 4 | 17 / not reported |
| Koivisto 9.2 | GPL-3.0 | yes | **no** | no | none | 17 / 17 / 27 / 33 / 1 | 17 | 19 / 27 |
| Lc0 | GPL-3.0 | yes | yes | with `UCI_ShowWDL` | none | not measured | — | — |

Notes:

- The mate-in-one first line is always one move (`Qxf7#`).
- Obsidian often truncates its PV to 1–5 moves; Scan64 lesson lines and
  narrator evidence get less continuation from it.
- Koivisto declares no `MultiPV`, so it cannot run focused passes, hints or
  lesson verification (see feature table).
- Lc0 was not built here (it needs a neural-network backend, CUDA/cuDNN or
  similar, and a weights file). Its licence (GPL-3.0) is from the upstream
  repository; MultiPV and `UCI_ShowWDL` are from the
  [Lc0 options wiki](https://github.com/LeelaChessZero/lc0/wiki/Lc0-options)
  and [WDL blog post](https://lczero.org/blog/2020/04/wdl-head/). Its nodes are
  far more expensive than alpha-beta nodes, so equal node budgets are not
  comparable and Scan64's fixed budgets would need per-use overrides.

Re-run: `uv run python -m benchmarks.engines.compare_engines benchmarks/engines/engines.toml`
(`--resume <results.jsonl>` continues an interrupted run). Engines whose binary
is missing are recorded as "not installed".

## Engine compatibility set

[`benchmarks/engines/compat.py`](../benchmarks/engines/compat.py) runs every
engine in [`compat.toml`](../benchmarks/engines/compat.toml) through Scan64's
own features, each at its production entry point and budget, and records one
of: `works`; `unavailable` (an `EngineCapabilityError`, with its exact reason,
which the API returns as 503 `engine_capability_unavailable`); `blocked` (the
focused pass, when game analysis did not work); `error` (any other exception:
a bug). Each engine is selected through its profile in
[`config/engines/compat/`](../config/engines/compat/), as a user selects it.

```
uv run python -m benchmarks.engines.compat benchmarks/engines/compat.toml
uv run python -m benchmarks.engines.compat benchmarks/engines/compat.toml --only Lc0
uv run python -m benchmarks.engines.compat CONFIG --resume RESULTS.jsonl
```

Features: `game_analysis` (fast pass, `REVIEW_NODES`), `focused_pass`
(1,000,000 nodes, MultiPV 4), `hints` (play assistance), `lesson_verification`
(MultiPV 2/4/8 widening), `analysis_board` (hint search, scored legal lines),
`bot_ladder`, `skill_level_opponent`.

Scan64 downloads and bundles none of these engines. Each user installs the
engine and points a profile at it.

| Engine | Source | Licence | Behaviour it stresses |
| --- | --- | --- | --- |
| Alexandria | https://github.com/PGG106/Alexandria | GPL-3.0 | no MultiPV; unusual centipawn scale |
| Minic | https://github.com/tryingsomestuff/Minic | GPL-3.0 | MultiPV capped below 8; non-standard WDL option |
| Obsidian | https://github.com/gab8192/Obsidian | GPL-3.0 | truncated PVs |
| Triumviratus | https://github.com/Tors3/Triumviratus | GPL-3.0 | network from an external file |
| Fairy-Max | Debian package `fairymax` | not stated at source | XBoard only; no MultiPV, Hash or Threads |
| Fairy-Stockfish | https://github.com/fairy-stockfish/Fairy-Stockfish | GPL-3.0 | several protocols; evaluation-term trace |
| Rodent V | https://github.com/nescitus/Rodent-V | GPL-3.0 | large option list; personality files |
| PZChessBot | https://github.com/kevlu8/PZChessBot | AGPL-3.0 | lower-case option names |
| Lc0 | https://github.com/LeelaChessZero/lc0 | GPL-3.0 (network licence not stated) | slow neural nodes; no Hash |
| Maia-3 | https://github.com/CSSLab/maia3 | AGPL-3.0 (weights licence not stated) | Python UCI wrapper; rating options |

### Results

Windows smoke (windows-4080, 2026-11-27): Stockfish 18 and Ethereal 14.40 from
the comparison above, and the XBoard test engine
`tests/fixtures/engines/fake_xboard.py`.

| Engine | game_analysis | focused_pass | hints | lesson_verification | analysis_board | bot_ladder | skill_level_opponent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Stockfish 18 | works | works | works | works | works | works | works |
| Ethereal 14.40 | works | works | works | works | works | unavailable¹ | unavailable² |
| XBoard test engine | works | unavailable³ | unavailable³ | unavailable³ | unavailable³ | unavailable⁴ | unavailable² |

1. "The bot ladder requires UCI option(s) Skill Level, UCI_LimitStrength,
   UCI_Elo, which Ethereal 14.40 (PEXT) does not declare".
2. "Skill Level opponent play requires UCI option(s) Skill Level, which … does
   not declare".
3. "4 analysis lines need MultiPV, which FakeXBoard 1 cannot provide over
   XBoard" (lesson verification: 2 lines).
4. As 1, plus `MultiPV`.

The ten-engine run is done on the Linux machine, where every engine in the set
builds and runs. Its table replaces nothing above; it is added here with the
date and the engine versions.

## Follow-ups

- Separate the opponent engine from the analysis engine (app/play wiring) so a
  non-Stockfish analysis profile keeps Stockfish bot play.
- `exact_replay.py` should record the actual engine name in `Verification`.
- The bot ladder fails on engines with MultiPV but no strength controls
  (Ethereal): it requires the Stockfish reset options even where the engine is
  always at full strength.
- The pool-less legacy Skill Level opponent (`stockfish_opponent.py`) still
  starts the engine with `popen_uci` and sets `Hash` unconditionally.
- The bot ladder and Skill Level opponent need a strength path through
  `UCI_LimitStrength`/`UCI_Elo` alone, with time limits, for engines without
  `Skill Level` (Arasan 26.1, Minic, Rodent V); see #396 and #393.
- Node-only searches (fast pass, focused pass, lesson verification) have no
  time limit or deadline, so an engine that ignores `go nodes` (Rodent V 1.2)
  hangs them. `[limits]` accepts only `hint`.
- The batch pool keeps an engine process that died and does not restart it:
  after one `EngineTerminatedError`, later searches fail with "engine process
  dead".
- An engine that stops far short of `go nodes N` (Arasan 26.1, about 6k nodes
  for any N) reports `works` with much less search than asked for; no state in
  the compatibility table shows it.
- Profiles have no working directory, environment variables or command-line
  arguments (Rodent V, Triumviratus and Maia-3 need wrapper scripts on Linux),
  and option values get no `~` expansion.
