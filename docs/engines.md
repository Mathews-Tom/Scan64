# Analysis engines

Scan64 analyses with one UCI engine selected by configuration. The Docker
image and the desktop app bundle Stockfish 18, Caissa 2.0 and Arasan 26.1
([Bundled engines](#bundled-engines)). A fresh install of either analyses with
Caissa and plays the bot ladder with Arasan; an install from before keeps
Stockfish ([Which engines an install uses](#which-engines-an-install-uses)). A
source checkout uses Stockfish on `PATH`. Any other open-source UCI engine can
replace them for analysis, review, hints, lessons and narrator evidence without a
code change.

## Bundled engines

Each engine is built from its pinned source archive. Every source archive,
network and downloaded licence has a SHA-256 in the `Dockerfile`, and
`scripts/build-server.py` reads the pins from there, so a desktop build accepts
nothing the image would not. Source archives and licence texts ship next to each
executable ([THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)).

| Engine | Role (#396) | Network | Docker image | Desktop resources |
| --- | --- | --- | --- | --- |
| Stockfish 18 (GPL-3.0) | installs from before the bundled engines | two NNUE files, embedded | `/usr/local/bin/stockfish` | `stockfish/stockfish[.exe]` |
| Caissa 2.0 (MIT) | analysis | `eval-ml-8-152B-spsa.pnn`, embedded through `EVALFILE` | `/usr/local/bin/caissa` | `caissa/caissa[.exe]` |
| Arasan 26.1 (MIT) | bot ladder | `arasanv8-20261005.nnue`, loaded from the executable's directory | `/usr/local/lib/arasan/arasan` | `arasan/arasan[.exe]` next to the `.nnue` |

Each package carries one profile for each permissive engine (sources in
[`config/engines/container/`](../config/engines/container/)): `caissa.toml`
(`UCI_ShowWDL` on) and `arasan.toml` (`OwnBook` off, the `elo` ladder of
`config/engines/arasan.toml`). The image has them in
`/usr/local/share/scan64/engines/`. The desktop build stages them in
`engines/` among its resources, with `binary_path` rewritten to the engine
beside them (`../caissa/caissa[.exe]`).

**Provisional score scale.** `caissa.toml` sets no `score_scale`, so a fresh
install reads Caissa's centipawns as Stockfish's. The committed measurement
([`caissa_positions_report.md`](../benchmarks/narrator/results/caissa_positions_report.md),
Evaluation) puts them at 0.71–0.75 of Stockfish's (1M-node searches pooled,
n = 37): win percentages and the 150 cp mistake threshold run on an
uncalibrated scale, and won positions read near 600–700 cp where Stockfish
reports 1,000 or more. The earlier #396 comparison (1 s searches, n = 31) found
the opposite, a slope of 1.19, so no single factor fits both samples yet. The
fit on the 50 narrator positions is open on #396.
Installs that keep Stockfish are unaffected.

### Which engines an install uses

A package names its profile folder in `SCAN64_BUNDLED_ENGINES_DIR`: the image
sets it, and the desktop shell passes it. On the first start with that folder
and no engine configured, the server records the install's engines in
`<database>.engines.toml` next to its database (`/data/scan64.db.engines.toml`
in the image, `scan64.db.engines.toml` in the desktop data folder):

| At first start | Recorded | Analysis | Bot ladder |
| --- | --- | --- | --- |
| no database yet (a fresh install) | `engines = "bundled"` | Caissa | Arasan |
| a database already there | `engines = "stockfish"` | Stockfish | Stockfish |

Every later start reads the record, so an update never changes an install's
engines: games under way, bot sessions (each plays only under the ladder it
started with) and stored analysis keep the engine they were made with.

To switch an install, stop it and edit the record (`"bundled"` or
`"stockfish"`). `SCAN64_ENGINE_CONFIG` or `SCAN64_LADDER_ENGINE_CONFIG` set
explicitly always win, and so does `SCAN64_STOCKFISH_PATH` in the image (the
desktop shell always passes its bundled Stockfish there, so it does not count in
the desktop app). Nothing is recorded on such a start. In the image:

```sh
docker run -e SCAN64_ENGINE_CONFIG=/usr/local/share/scan64/engines/caissa.toml \
  -e SCAN64_LADDER_ENGINE_CONFIG=/usr/local/share/scan64/engines/arasan.toml ...
```

Under the Caissa profile alone bot play is unavailable, since Caissa has no
strength options. Switching the ladder engine ends a bot session started under
the old one with 503 `bot_play_unavailable` on its next move.

Every desktop installer check (`scripts/desktop-package-smoke.py`) requires the
three engine folders and `engines/` with both profiles, checks every pinned file against its Dockerfile digest, and starts
each engine outside its own folder: it must report its `id name` and answer `uci`
and `isready`.

## Selecting an engine

| Variable | Default | Effect |
| --- | --- | --- |
| `SCAN64_ENGINE_CONFIG` | unset | Absolute path of an engine profile (TOML). When set, it fully defines the engine. |
| `SCAN64_STOCKFISH_PATH` | `stockfish` on `PATH` | Stockfish binary used when no profile is selected. Ignored when `SCAN64_ENGINE_CONFIG` is set. |
| `SCAN64_LADDER_ENGINE_CONFIG` | unset | Absolute path of a second profile that plays the bot ladder's moves. Unset, they use the analysis engine. |

Without a profile the engine is Stockfish from `SCAN64_STOCKFISH_PATH`, one
thread, 16 MB hash, no extra options: the same identity as before profiles
existed, so saved analyses keep matching.

A profile:

```toml
[engine]
name = "ethereal"                                  # label for logs; not part of cache identity
binary_path = "~/.local/opt/engines/Ethereal.exe"  # absolute (~ expands), relative to this file, or a bare command on PATH
protocol = "uci"                                   # optional: "uci" (default) or "xboard"
threads = 1                                        # sent as Threads when the engine declares it
hash_mb = 16                                       # sent as Hash when the engine declares it
node_scale = 1.0                                   # optional, in (0, 1]: nodes sent = budget × node_scale
search_deadline_s = 120                            # optional, > 0: wall-clock bound on node/depth-only searches
score_scale = 1.0                                  # optional, > 0: engine centipawns per Stockfish centipawn

[engine.options]          # extra UCI options; each must be declared by the engine
UCI_ShowWDL = true

[limits.hint]             # optional per-use override (nodes and/or time_ms, multipv; no depth)
nodes = 200000
time_ms = 1500
multipv = 4

[ladder]                  # optional: how the bot ladder weakens this engine
strength = "elo"          # "skill" (default) or "elo"
elo = [1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]  # UCI_Elo for levels 1-8, increasing
movetime_ms = 500         # fixed time per bot move; "elo" only
```

Examples: [`config/engines/`](../config/engines/) (`ethereal.toml`,
`berserk.toml`, `stockfish-wdl.toml`, `arasan.toml`).

Rules (all enforced at load or engine start, failing with the file and key):

- Unknown tables/keys, a missing or relative profile path, and non-positive
  numbers are rejected.
- A `binary_path` without a directory (`stockfish`) is looked up on `PATH`. A
  relative `binary_path` with a directory (`../caissa/caissa`) is resolved
  against the profile file's own folder.
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
- `engine.search_deadline_s` (default 120) bounds every analysis search that
  has no time limit of its own (fast pass, focused pass, lesson verification,
  Analysis Board lines). Past it the search is stopped and the request fails
  with `EngineCapabilityError` saying the engine did not finish its node or
  depth limit: an engine that ignores `go nodes` (Rodent V 1.2) reports
  `unavailable` instead of hanging. It is not part of the cache identity.
- A pooled engine whose process exits (`EngineTerminatedError`) fails that
  request and is replaced: the next checkout from the pool starts a new
  process.
- `engine.score_scale` (default 1) is the engine's centipawns per Stockfish
  centipawn. Scan64's win-probability curve (Lichess logistic) is fitted to
  Stockfish, so every win probability divides a line's `score_cp` by it
  first; stored `score_cp` stays the engine's own number. Each stored line and
  the row's identity record `score_scale` when it is not 1, so rows at
  different scales never reuse each other and rows at 1 keep their
  pre-existing reuse key. Other centipawn thresholds (the 150 cp fast-pass
  swing, narrator facts) still read raw `score_cp`.
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
- `[ladder]` selects the bot ladder's strength path. `skill` (the default, and
  the only path without a profile) searches every legal move at full strength
  and picks a move inside the level's error budget, seeded per session; it
  needs `MultiPV`, `Skill Level` and `UCI_LimitStrength`. `elo` sets
  `UCI_LimitStrength = true` and the level's `UCI_Elo`, then plays one move in
  `movetime_ms` with the engine's own randomness (not seeded; the session's
  node budget is unused). It needs `UCI_LimitStrength` and `UCI_Elo`; a level's
  `UCI_Elo` outside the range the engine declares fails the move with
  `EngineCapabilityError` (a 503 the player can act on, not a server error). The `elo` values are what Scan64 sends, not a
  measured playing strength. The analysis reset turns `UCI_LimitStrength` off
  again before every search. Sessions record the path in `bot_policy_version`:
  `stockfish-error-budget-v1` for `skill`,
  `uci-elo-v1:<engine name>:<elo list>:<movetime>ms` for `elo`. A session
  plays only under the policy it recorded: when the server's `[ladder]` or
  engine changes while a bot game is open, the next bot move fails with
  `EngineCapabilityError` naming both policies, so a game is never counted in
  one strength cohort and played under another. Sessions without a recorded
  policy predate the Elo path and play only under
  `stockfish-error-budget-v1`. The opponent's name comes from the recorded
  policy: `Arasan (level 1: First steps)` for an Elo session on the `arasan`
  profile, `Stockfish (...)` for error-budget and unstamped sessions.

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
  `EvalFileSmall`, `WeightsFile` or Arasan's `NNUE file` when those files
  exist. A relative path is resolved where its engine loads it: the working
  directory, then the executable's directory, for the first three; only the
  executable's directory for `NNUE file` (Arasan 26.1 `globals.cpp`).
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
| Bot ladder opponent (levels 1–8), `[ladder] strength = "skill"` | `MultiPV` plus Stockfish's `Skill Level`, `UCI_LimitStrength` | `EngineCapabilityError` naming the missing options; when the engine declares `UCI_Elo` the message names `[ladder] strength = "elo"`. |
| Bot ladder opponent, `[ladder] strength = "elo"` | `UCI_LimitStrength`, `UCI_Elo` | `EngineCapabilityError` naming the missing options. |
| Legacy Skill Level opponent (sessions without `bot_level`) | `Skill Level` | `EngineCapabilityError` naming `Skill Level`. |

The `skill` ladder stays Stockfish-only on purpose: its error budgets,
`SKILL_POLICY_VERSION = "stockfish-error-budget-v1"` and the strength estimate
cohorts (`api/strength.py`) are defined against Stockfish. An engine with
`UCI_Elo` but no `Skill Level` (Arasan 26.1) plays the ladder through
`[ladder] strength = "elo"`. Without `SCAN64_LADDER_ENGINE_CONFIG`, opponent
moves come from the same interactive pool as hints, under the analysis
profile's `[ladder]`. With it, ladder moves come from a pool of the ladder
profile's engine (sized like the interactive pool) under that profile's
`[ladder]`; the analysis profile's `[ladder]` is then unused, `scan64 serve`
checks the ladder profile's binary too, and the default batch pool shrinks by
the ladder pool's engine threads. Analysis, hints, review and legacy Skill Level
sessions (no `bot_level`) never use the ladder profile. A ladder profile with
the same engine and `[ladder]` as the analysis profile adds no pool. Setting or
changing it changes the server's ladder policy, so bot games started before
cannot continue (the policy check above). Example: Caissa for analysis, Arasan
for the ladder:

```sh
SCAN64_ENGINE_CONFIG=/path/to/caissa.toml SCAN64_LADDER_ENGINE_CONFIG=/path/to/arasan-ladder.toml scan64 serve
```

When the engine that plays bot moves (the ladder profile's, else the analysis
profile's) can play neither ladder, bot play is unavailable;
play never falls back to Stockfish. The play endpoints
(`POST /v1/play-sessions` when the bot moves first, `…/moves`, `…/resume` and
`…/opponent-move`) return 503 with
`detail = {code: "bot_play_unavailable", message, reason}`. `reason` is the
`EngineCapabilityError` text that names the missing options. The player's move
stays committed; a bot-first session is not created. The web client shows
that bot play is unavailable under the active engine profile.

## Chess variants (#398)

Scan64 reads variant positions with python-chess's variant boards: crazyhouse,
three-check (`3check`), atomic, antichess, king of the hill, horde and racing
kings. Searching one needs an engine that declares the variant. Which engines
do, how python-chess selects the variant, installing Fairy-Stockfish and its
probe results are in "Variants" below; this section is what Scan64 does with
them.

- **API.** `POST /v1/analysis/position` takes an optional `variant` (the names
  above; default `chess`). The FEN is parsed under that variant's rules: a FEN
  that is not a position of the variant is a 422 before any engine access. The
  reply names the variant it was searched under. The explanation endpoint
  covers standard chess only and rejects any other `variant` with 422.
- **Engine check.** Before any search (see "How the variant is selected"),
  Scan64 compares the board's variant with the engine's declared variants:
  the `UCI_Variant` choices over UCI, or the `variants` feature over XBoard,
  where antichess is `giveaway`. An undeclared variant is an
  `EngineCapabilityError`, answered as 503 `engine_capability_unavailable`
  whose `reason` names the variant and Fairy-Stockfish. Without the check
  python-chess raises a plain `EngineError`, which the endpoint reports as a
  generic analysis failure.
- **Analysis config.** A search of a variant board stores the variant in the
  analysis config (`variant`; absent for standard chess), so its scores are
  never read as another variant's (#506).
- **Hint cache.** The cache key includes the variant: the same FEN is a
  different position under another variant's rules.
- **Not covered.** Diagnosis, lessons, puzzles, the narrator's explanations,
  bot play and position health read standard chess only.
- **Tests.** The tests use the fake UCI engine
  (`tests/fixtures/engines/fake_uci.py`, `FAKE_UCI_VARIANTS`), which plays by
  python-chess; no test runs Fairy-Stockfish. The probe in "Variants" runs it
  directly, not through Scan64's endpoints.
- **Variant games.** A PGN with a supported `Variant` tag is stored with its
  variant. Game analysis, review and backfill refuse it with 409
  `variant_not_covered`; the web import says the game is saved and not
  analysed, and Your Games labels it "<variant>, not analysed".

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
| `Dockerfile`, `scripts/build-server.py`, `scan64-server.spec`, desktop shell | Bundle and pin Stockfish 18, Caissa 2.0 and Arasan 26.1 with their networks; the shell passes `SCAN64_STOCKFISH_PATH` and `SCAN64_BUNDLED_ENGINES_DIR` | A fresh install selects Caissa and Arasan ([Which engines an install uses](#which-engines-an-install-uses)); other engines are operator-provided. |
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
Ethereal (or `SCAN64_TEST_SECOND_ENGINE`). Outside CI it skips when no binary
exists.

Tests never read the developer's engine selection: an autouse fixture in
`tests/conftest.py` removes `SCAN64_ENGINE_CONFIG`, `SCAN64_LADDER_ENGINE_CONFIG`
and `SCAN64_STOCKFISH_PATH` before every test, so tests that run Stockfish use the `stockfish` on `PATH`.
A test that needs a profile or a Stockfish path sets it itself.
`SCAN64_TEST_SECOND_ENGINE` is a test input and is kept.

### How CI provides the second engine

CI does not build a second engine. The `Run repository quality gate` step in
`.github/workflows/ci.yml` sets `SCAN64_TEST_SECOND_ENGINE` to
[`tests/fixtures/scripted_uci_engine.py`](../tests/fixtures/scripted_uci_engine.py):

| Property | Value |
| --- | --- |
| `id name` | `Scan64 Scripted UCI Fixture` (does not start with `Stockfish`) |
| Declared options | `Threads`, `Hash`, `MultiPV` only (no `Skill Level`, no `UCI_Elo`) |
| Moves and scores | Legal moves only; two-ply material search; `mate 1` for a mating move |
| Each `info` line | `depth`, `seldepth`, `multipv`, `score`, `nodes`, `pv` |
| Engine identity | The `sha256:` digest of the script file, so its rows never match Stockfish rows |

The fixture checks the configuration path, the stored line fields and cache
separation. It does not check chess strength or the score scale of a real engine.

With `CI` set (GitHub Actions sets it), the tests do not skip. A missing second
engine or Stockfish fails both tests. A `SCAN64_TEST_SECOND_ENGINE` path that is
not a file fails the tests everywhere.

Why not Ethereal in CI: the Ethereal build above (commit `0e47e9b`, classical,
`gcc -O3 -DUSE_POPCNT`) passes both tests locally. On the GitHub `ubuntu-latest`
runner (AMD EPYC 9V74) the same build crashed with a segmentation fault on its
first `go`. The engine pool then did not shut down, and the job hung for 45 minutes.
An `-O1` ASan build did not crash there, so the cause is not found.

Run the test locally against a real engine:

```bash
commit=0e47e9b67f345c75eb965d9fb3e2493b6a11d09a
curl -fL -o ethereal.tar.gz "https://github.com/AndyGrant/Ethereal/archive/$commit.tar.gz"
echo "46ac749697f33271c24afb0d501092ab4f3b6986b37573f6c42cd728d34766bf  ethereal.tar.gz" | sha256sum --check
mkdir ethereal && tar -xzf ethereal.tar.gz -C ethereal --strip-components=1
(cd ethereal/src && gcc -std=gnu11 -O3 -DNDEBUG -DUSE_NNUE=0 -DUSE_POPCNT -mpopcnt *.c pyrrhic/tbprobe.c -lpthread -lm -o ../../Ethereal)
SCAN64_TEST_SECOND_ENGINE="$PWD/Ethereal" uv run pytest tests/integration/test_second_engine.py
```

Run it against the CI fixture:

```bash
SCAN64_TEST_SECOND_ENGINE="$PWD/tests/fixtures/scripted_uci_engine.py" uv run pytest tests/integration/test_second_engine.py
```

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

Every record carries its provenance: the Scan64 `commit`, `dirty` (uncommitted
changes under `src/` or `benchmarks/`), and the SHA-256 of the profile file and
of the resolved engine binary. A profile whose binary is missing records `not
installed`.

Scan64 downloads and bundles none of these engines. Each user installs the
engine and points a profile at it.

| Engine | Source | Licence | Behaviour it stresses |
| --- | --- | --- | --- |
| Alexandria | https://github.com/PGG106/Alexandria | GPL-3.0 | no MultiPV; unusual centipawn scale |
| Minic | https://github.com/tryingsomestuff/Minic | GPL-3.0 | MultiPV capped below 8; non-standard WDL option |
| Obsidian | https://github.com/gab8192/Obsidian | GPL-3.0 | truncated PVs |
| Triumviratus | https://github.com/Tors3/Triumviratus | GPL-3.0 | network from an external file |
| Fairy-Max | Debian package `fairymax` | public domain; also GPL or MIT (see below) | XBoard only; no MultiPV, Hash or Threads |
| Fairy-Stockfish | https://github.com/fairy-stockfish/Fairy-Stockfish | GPL-3.0 | several protocols; evaluation-term trace |
| Rodent V | https://github.com/nescitus/Rodent-V | GPL-3.0 | large option list; personality files |
| PZChessBot | https://github.com/kevlu8/PZChessBot | AGPL-3.0 | lower-case option names |
| Lc0 | https://github.com/LeelaChessZero/lc0 | GPL-3.0 (network licence not stated) | slow neural nodes; no Hash |
| Maia-3 | https://github.com/CSSLab/maia3 | AGPL-3.0 (weights licence not stated) | Python UCI wrapper; rating options |

Fairy-Max's licence is the notice at the top of `fairymax.c` in the Debian
source package `fairymax_5.0b.orig.tar.gz` (unchanged by the Debian patches):
"Fairy-Max 5.0 is free software, released in the public domain so that you have
my permission do with it whatever you want, whether it is commercial or not, at
your own risk. Those that are not comfortable with this, can also use or
redistribute it under the GNU Public License or the MIT License." The notice
names no GPL version. The package's `debian/copyright` gives upstream files the
custom licence `fairmyax-license` (sic), whose text is the older Fairy-Max 4.8
grant: "Fairy-Max 4.8 is free software, and you have my permission do with it
whatever you want, whether it is commercial or not." It does not mention the
GPL or MIT. Both texts add that configuring patented variants needs the patent
holders' permission and that there is no warranty. The results tables generated
before this change (`benchmarks/engines/results/compat_*.md`) still say "not
stated at source".

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
   which Ethereal 14.40 (PEXT) does not declare". The table was recorded before
   5732be7, when the reset also required `UCI_Elo`.
2. "Skill Level opponent play requires UCI option(s) Skill Level, which … does
   not declare".
3. "4 analysis lines need MultiPV, which FakeXBoard 1 cannot provide over
   XBoard" (lesson verification: 2 lines).
4. As 1, plus `MultiPV`.

Linux run (linux-5090, 2026-11-27, commit `edda2ed`, #411): the ten engines
above plus Stockfish 11, Lc0 with the BT4 network, Caissa, Arasan, Ethereal,
Berserk and Koivisto, from `benchmarks/engines/compat-linux.toml` with the
profiles in `config/engines/compat/linux/`. The states are copied from
`benchmarks/engines/results/compat_20261127_202247.jsonl`, except the two Lc0
rows, which come from `compat_20261127_233522.jsonl` (an Lc0-only rerun at
`747152f`, after the profiles moved to wrapper scripts; same states as
`202247`). The generated tables with the reason for every `unavailable` and
`blocked` cell, and the run notes, are the `.md` files next to them. Each engine's source, tag and
commit, build command, binary SHA-256 and network URL and SHA-256 are in
[`benchmarks/engines/linux-builds.md`](../benchmarks/engines/linux-builds.md);
the binary SHA-256 is also in every JSONL record. This table replaces nothing above.

| Engine | Version | Protocol | node_scale | game_analysis | focused_pass | hints | lesson_verification | analysis_board | bot_ladder | skill_level_opponent |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stockfish 18 | 18 | UCI | 1 | works | works | works | works | works | works | works |
| Alexandria | 9.1.0 | UCI | 1 | works | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| Minic | 3.46 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Obsidian | 16.0 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Triumviratus | 8.1 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Fairy-Max | 5.0b | XBoard | 1 | unavailable | blocked | unavailable | unavailable | unavailable | unavailable | unavailable |
| Fairy-Stockfish | 14 | UCI | 1 | works | works | works | works | works | works | works |
| Rodent V | 1.2 | UCI | 1 | unavailable | blocked | works | unavailable | works | unavailable | unavailable |
| PZChessBot | 8.0 | UCI | 1 | works | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| Lc0 | 0.32.1 (T1 net) | UCI | 0.12 | works | works | works | works | works | unavailable | unavailable |
| Lc0 (BT4) | 0.32.1 (BT4 net) | UCI | 0.013 | works | works | works | works | works | unavailable | unavailable |
| Maia-3 | 0.1.0 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Caissa | 2.0 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Arasan | 26.1 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Stockfish 11 | 11 | UCI | 1 | works | works | works | works | works | works | works |
| Ethereal | 14.40 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Berserk | 14 | UCI | 1 | works | works | works | works | works | unavailable | unavailable |
| Koivisto | 9.0 | UCI | 1 | works | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

- `unavailable` on the multi-line features (Alexandria, PZChessBot, Koivisto):
  "N analysis lines need the UCI option MultiPV, which … does not declare".
- `bot_ladder` and `skill_level_opponent` are `unavailable` on every engine
  without `Skill Level` (and, for the ladder, `UCI_LimitStrength`); Stockfish 18,
  Stockfish 11 and Fairy-Stockfish declare them.
- Fairy-Max: "does not declare the XBoard setboard feature", so no feature runs
  and `focused_pass` is `blocked`.
- Rodent V ignores `go nodes N`; with the profile's `search_deadline_s = 30`
  its node-only searches (`game_analysis`, `lesson_verification`) are
  `unavailable` after 30 s and `focused_pass` is `blocked`. Hints and the
  Analysis Board have a time limit and work.
- Arasan 26.1 stops after about 6k nodes for any N and Maia-3 ignores node
  limits, so their node-budget features `work` with less search than asked
  (see Follow-ups).
- Lc0's `node_scale` is measured nodes per second against Stockfish 18 (T1
  0.12, BT4 0.013); Maia-3 stays at 1.

## Follow-ups

- Separate the opponent engine from the analysis engine (app/play wiring) so a
  non-Stockfish analysis profile keeps Stockfish bot play.
- `exact_replay.py` should record the actual engine name in `Verification`.
- The bot ladder fails on engines with MultiPV but no strength controls
  (Ethereal): it requires the Stockfish reset options even where the engine is
  always at full strength.
- The pool-less legacy Skill Level opponent (`stockfish_opponent.py`) still
  starts the engine with `popen_uci` and sets `Hash` unconditionally.
- The bot ladder's node-limited MultiPV search and Skill Level play do not
  go through the search deadline yet; `[limits]` accepts only `hint`.
- The legacy Skill Level opponent (sessions without `bot_level`) has no
  `UCI_Elo` path; it still needs `Skill Level`.
- A bot game open when `[ladder]` or the engine changes cannot continue (see
  the ladder section); it stays in its recorded cohort, and the player starts
  a new game.
- An engine that stops far short of `go nodes N` (Arasan 26.1, about 6k nodes
  for any N) reports `works` with much less search than asked for; no state in
  the compatibility table shows it.
- Profiles have no working directory, environment variables or command-line
  arguments (Rodent V, Triumviratus and Maia-3 need wrapper scripts on Linux),
  and option values get no `~` expansion (both Lc0 profiles use a wrapper that
  passes `--weights` from `$HOME`).

## Variants

What Scan64 does with variants is in "Chess variants (#398)" above. This
section records which engines can serve them and how python-chess selects a
variant, measured with the variant probe below.

### Engines

| Variant | python-chess board | `UCI_Variant` | Fairy-Stockfish 14 | Standard-chess engines (Stockfish 18 probed) |
| --- | --- | --- | --- | --- |
| Crazyhouse | `CrazyhouseBoard` | `crazyhouse` | yes | no |
| Three-check | `ThreeCheckBoard` | `3check` | yes | no |
| Atomic | `AtomicBoard` | `atomic` | yes | no |
| Antichess | `AntichessBoard` | `antichess` | yes | no |
| King of the hill | `KingOfTheHillBoard` | `kingofthehill` | yes | no |
| Horde | `HordeBoard` | `horde` | yes | no |
| Racing kings | `RacingKingsBoard` | `racingkings` | yes | no |

"yes" means the engine lists the value in its `UCI_Variant` option and
analysed every probe position of that variant. Fairy-Stockfish 14 declares 64
`UCI_Variant` values in all; the seven above are those python-chess has
boards for. Stockfish 18 declares no `UCI_Variant` option (probe run below).
The other standard engines were not probed; they are standard-chess engines
and are not treated as variant engines.

Fairy-Stockfish (https://github.com/fairy-stockfish/Fairy-Stockfish) is
GPL-3.0. Scan64 does not download or bundle it: the user installs the release
binary and points a profile at it, for example
[`config/engines/linux/fairy-stockfish.toml`](../config/engines/linux/fairy-stockfish.toml)
(`fairy-stockfish_x86-64` in `~/.local/opt/engines/fairy-stockfish/`). #398
states that no permissively licensed engine supports crazyhouse and
three-check (not checked here), so Scan64 has no bundled variant engine.

### How the variant is selected

The board selects the variant. python-chess sends `UCI_Variant` before every
search from the type of the board it is given (`CrazyhouseBoard` sends
`crazyhouse`) and raises `EngineError: cannot set UCI_Variant which is
automatically managed` when a caller sets it. `UCI_Variant` is therefore one
of the Scan64-managed options a profile may not set (see the rules above);
a variant engine's profile is an ordinary profile. Before a search, read the
values the engine declares (`engine.options["UCI_Variant"].var`). Without
that check python-chess raises `EngineError` ("engine does not support
UCI_Variant" for an engine without the option, "invalid value for combo option
'UCI_Variant'" for a value it does not list), not a capability message.

### Probe results

[`benchmarks/engines/variants_probe.py`](../benchmarks/engines/variants_probe.py)
analyses the start position and two or three fixed positions per variant,
including crazyhouse positions with pieces in hand and three-check positions
after checks (one where the side to move has a check left that wins). Each
search: 300,000 nodes, MultiPV 3, one thread, 16 MB hash, a fresh game per
position. The probe never sets `UCI_Variant`; a variant the engine does not
list is recorded as `not declared` and not searched. Every move of each raw
`pv` the engine sent is replayed on the python-chess variant board; an empty
`pv` counts as not legal (python-chess itself drops a `pv` it cannot play, so
its parsed lines cannot show an illegal move).

linux-5090, 2026-11-28, commit `f10d6d6` (clean tree), Fairy-Stockfish 14
release binary `fairy-stockfish_x86-64` (SHA-256 `ab6b8582…e150f`, no
network file: `EvalFile` unset), python-chess 1.11.2, CPUs 32-39. Raw records:
`benchmarks/engines/results/variants_probe_20261128_002851.jsonl`; the
manifest and the per-position table (scores included) are the `.manifest.json`
and `.md` files next to it. One run on one machine; PV length and time are
not playing strength.

| Variant | Positions | Lines per position | PV lengths in plies, per position | All PV moves legal | Time per position |
| --- | --- | --- | --- | --- | --- |
| Crazyhouse | start, fried-liver, alekhine, scandinavian | 3 | 14-16-17 / 2-8-12 / 2-11-11 / 13-2-11 | yes | 0.354–0.383 s |
| Three-check | start, white-two-checks, black-two-checks | 3 | 2-2-13 / 2-2-2 / 1-2-12 | yes | 0.316–0.349 s |
| Atomic | start, knight-threat, pinned-knight | 3 | 6-2-11 / 1-3-3 / 3-5-2 | yes | 0.037–0.294 s |
| Antichess | start, forced-capture, middlegame | 3; 1 in forced-capture | 15-2-16 / 12 / 12-14-2 | yes | 0.309–0.357 s |
| King of the hill | start, king-walk, italian | 3 | 11-13-10 / 10-2-13 / 14-2-14 | yes | 0.354–0.410 s |
| Horde | start, opening, pawn-wedge | 3 | 15-13-12 / 13-13-2 / 15-14-15 | yes | 0.355–0.401 s |
| Racing kings | start, race, knights-out | 3 | 13-12-2 / 9-8-13 / 11-12-9 | yes | 0.337–0.352 s |

- The forced-capture antichess position has one legal move, so it returns one
  line.
- Mates end the line: black-two-checks (`...Bb4+`, the third check) and
  knight-threat (`Nxf7`, whose explosion takes the e8 king) are `mate 1` with a
  one-ply first line, and pinned-knight is `mate 2`. Knight-threat finished in
  0.037 s.
- Many lines are 2 plies long. The probe does not show why; standard-chess
  engines also return short MultiPV lines (Stockfish 18's 1-ply second line in
  the comparison above).

Stockfish 18 through the same probe (`variants_probe_20261128_002924.jsonl`,
commit `ee61d81`, profile `config/engines/compat/linux/stockfish-18.toml`):
every position is `not declared` ("Stockfish 18 declares no UCI_Variant
option") and no search starts.

Re-run:

```
taskset -c 32-39 uv run python -m benchmarks.engines.variants_probe benchmarks/engines/variants_probe_linux.toml
uv run python -m benchmarks.engines.variants_probe benchmarks/engines/variants_probe_linux_stockfish.toml
uv run python -m benchmarks.engines.variants_probe --summarise RESULTS.jsonl
```

A run writes `variants_probe_<UTC stamp>.jsonl`, its `.manifest.json` and a
`.md` summary under `benchmarks/engines/results/`; there is no resume. The
manifest records the commit and dirty flag (the whole tree, the results
directory excluded), the engine's name, executable SHA-256 and option values,
its declared `UCI_Variant` values, the config and profile SHA-256 and a config
digest over them and every position, and the python-chess version. Its digest,
`run_id`, is in every record. `--summarise` rebuilds the summary from the
manifest and records alone and re-checks every PV's legality from the
recorded FEN and moves. `tests/unit/test_variants_probe.py` covers the
`not declared` path and the legality check with a scripted UCI engine
(`tests/fixtures/engines/fake_variant_uci.py`).

### What Scan64 lacks for variants

Tracked in #398:

- Narration: the explanation endpoint refuses a variant with 422.
- The variant Analysis Board cannot load a FEN or an imported variant game;
  it starts from the variant's start position.
- Game analysis (`ingest_pgn` raises `NonStandardVariantError`), review,
  diagnosis, lessons, puzzles, bot play and position health stay standard
  chess; variant games are stored, labelled and refused with 409
  `variant_not_covered`.
