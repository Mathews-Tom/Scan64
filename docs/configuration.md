# Configuration

Scan64 has no settings file of its own. Learners change their preferences under **Settings** in the app. Operators configure a source or Docker server through environment variables, some of which point at TOML files. The desktop app starts its server with a cleared environment and sets only the variables in [Set by the desktop app](#set-by-the-desktop-app), so the others do not reach it. Every variable below is read by the code named in its row, and an invalid value is never replaced by a default. Most stop `scan64-cli serve` at start. `SCAN64_MAIA_CONFIG`, `SCAN64_LLM_CONFIG` and the `SCAN64_LOCAL_LLM_*` limits are checked on first use and fail that request. `SCAN64_ENGINE_POOL_ENABLED` treats any value other than `0`, `false`, `no` or `off` as on.

The desktop app sets its own server variables (marked *desktop*). Do not set those yourself.

## Server and data

| Variable | Default | Effect | Read in |
| --- | --- | --- | --- |
| `SCAN64_DATABASE_URL` | `sqlite:///database.db` in the working directory | SQLAlchemy URL of the database. Blank is an error. Example: `sqlite:////absolute/path/to/scan64.db`. The desktop app sets it to `scan64.db` in its data directory. | `persistence/database.py` |
| `SCAN64_DATA_DIR` | puzzles: the SQLite database's directory, else `./data`; narrator: `./data` | Where `scan64-cli puzzles import --download` saves the puzzle file, and where `scan64-cli narrator install` downloads the model (`<dir>/narrator`). The desktop app sets it to its data directory (*desktop*). | `cli/puzzles.py`, `cli/narrator.py`, `desktop_server.py` |
| `SCAN64_WEB_DIST` | `apps/scan64-web/dist` in the checkout | Directory of the built web client that `scan64-cli serve` serves. The Docker image sets it to `/app/web`. | `api/web.py` |
| `SCAN64_ANALYSIS_DAILY_QUOTA` | `50` | Games per player per day whose analysis starts at once. Later games queue round-robin across players; none are rejected. `0` queues every game. | `chess/analysis/admission.py` |

`scan64-cli serve` takes `--host` (default `127.0.0.1`), `--port` (default `8001`) and `--no-browser`. Scan64 is built for loopback use only; it is not a hardened LAN or hosted server.

Where the desktop app keeps its data on each system is listed in the [user guide](user-guide.md#desktop-first-run-and-data).

## Engines

| Variable | Default | Effect | Read in |
| --- | --- | --- | --- |
| `SCAN64_ENGINE_CONFIG` | unset | Absolute path of an engine profile (TOML) for analysis, hints and grading. | `providers/engine/config.py` |
| `SCAN64_LADDER_ENGINE_CONFIG` | unset | Absolute path of a second profile that plays the bot ladder's moves. Unset, the bots use the analysis engine. | `providers/engine/config.py` |
| `SCAN64_STOCKFISH_PATH` | `stockfish` on `PATH` | Stockfish binary used when no profile is selected. | `providers/engine/config.py` |
| `SCAN64_BUNDLED_ENGINES_DIR` | unset | Directory of bundled engine profiles. The desktop app and the Docker image set it; a new install then records which engines it uses. | `providers/engine/install_defaults.py` |
| `SCAN64_ENGINE_POOL_ENABLED` | on | `0`, `false`, `no` or `off` starts a fresh engine process for each call instead of reusing pooled engines. | `providers/stockfish/pool.py` |
| `SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY` | `2` | Engines kept for live bot moves and on-demand review. At least 1. | `providers/stockfish/pool.py` |
| `SCAN64_ENGINE_POOL_BATCH_CONCURRENCY` | CPUs − interactive engines − ladder threads − 1, at least 2 | Engines for game analysis. At least 1. | `providers/stockfish/pool.py` |

The profile format, the bundled engines and which engines a new install uses are in [Analysis engines](engines.md#selecting-an-engine).

## Opponents

| Variable | Default | Effect | Read in |
| --- | --- | --- | --- |
| `SCAN64_MAIA_CONFIG` | unset | Absolute path of a Maia configuration (TOML) naming `lc0` and operator-owned checkpoints. Unset or invalid, the server still starts and the first Maia bot move fails with HTTP 500. | `api/play.py`, `chess/games/play_session_service.py` |

The file format and the checks are in [Maia opponents](maia.md#configure-scan64).

## Explanation wording

Built-in explanations need no model. Local wording is turned on by the learner under **Settings → Local explanation wording** ([user guide](user-guide.md#optional-local-explanation-wording)); an operator can instead fix the provider for every player.

| Variable | Default | Effect | Read in |
| --- | --- | --- | --- |
| `SCAN64_LLM_CONFIG` | unset | Absolute path of an `[llm]` TOML file (`template`, `ollama` or `openai` provider). When set, it overrides every player's local grant. | `explanations/settings.py`, `explanations/assembly.py`, `providers/llm/config.py` |
| `SCAN64_LOCAL_LLM_TIMEOUT_SECONDS` | `180` | Deadline for one local explanation, model listing included; at most 600. | `providers/llm/local_ollama.py` |
| `SCAN64_LOCAL_LLM_MAX_REQUEST_BYTES` | `262144` | Largest chat request sent to the local model; 1 KiB to 16 MiB. | `providers/llm/local_ollama.py` |
| `SCAN64_LOCAL_LLM_MAX_RESPONSE_BYTES` | `262144` | Largest response body accepted; 1 KiB to 16 MiB. | `providers/llm/local_ollama.py` |
| `SCAN64_LOCAL_LLM_CONTEXT_TOKENS` | `4096` | Context size sent as `num_ctx`; 2048 to 131072, at least 4096 recommended. | `providers/llm/local_ollama.py` |

The `[llm]` format, the default narrator model and the memory each context size costs are in the [narrator pipeline](narrator-pipeline.md#local-ollama-limits). The models that were compared are in [Narrator models](evaluation/narrator-models.md).

## Set by the desktop app

The desktop shell starts the server with `SCAN64_PORT`, `SCAN64_SHELL_TOKEN`, `SCAN64_APP_VERSION`, `SCAN64_DATA_DIR`, `SCAN64_BUNDLED_ENGINES_DIR` and `SCAN64_STOCKFISH_PATH`. The server then sets `SCAN64_DATABASE_URL` and `SCAN64_DISTRIBUTION=desktop` itself (`desktop_server.py`). The other `SCAN64_*` names in the code (`SCAN64_SERVER_COMMAND`, `SCAN64_DEBUG_PUZZLE_URL`, `SCAN64_UPDATER_*`, `SCAN64_UPDATE_SMOKE_*`, `SCAN64_SMOKE_*`) are test and build switches, not settings.
