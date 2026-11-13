# Maia operator provisioning

## Boundary

Scan64 does not bundle, download, cache, redistribute, or host Maia checkpoints. This guide provisions software and checkpoint files outside the repository and release artifacts. It does not establish a right to obtain, use, or distribute a checkpoint.

The v1 Maia checkpoint licence and redistribution terms remain unresolved. Read [CSSLab/maia-chess#76](https://github.com/CSSLab/maia-chess/issues/76) and confirm the operator's rights before obtaining an asset. Do not add checkpoint files to the repository; `.gitignore` rejects `*.pb.gz`.

## Validated macOS setup

The previously recorded operator setup used Apple silicon macOS with Homebrew, Lc0 0.32.1, Stockfish 18, and the official Maia v1.0 `maia-1500.pb.gz` asset. These are validation versions, not pins in the Homebrew command. Other platforms require operator-provided Lc0 and Stockfish binaries. Maia configuration, Lc0 and checkpoint paths must be absolute; a missing selected checkpoint or Lc0 binary fails closed. Ordinary Stockfish operation instead resolves `stockfish` on `PATH`.

Install the required engine binaries:

```text
brew install lc0 stockfish
```

Confirm the executable paths before writing configuration:

```text
command -v lc0
command -v stockfish
```

The recorded Homebrew paths were `/opt/homebrew/bin/lc0` and `/opt/homebrew/bin/stockfish`; use the paths reported on your machine.

## Obtain an operator-owned checkpoint

After confirming rights, manually download `maia-1500.pb.gz` from the [official Maia v1.0 release](https://github.com/CSSLab/maia-chess/releases/tag/v1.0) through the operator's browser or approved artifact process. Store it outside the Scan64 checkout, for example:

```text
mkdir -p "$HOME/.local/share/scan64/maia"
```

Move the operator-provided file to:

```text
$HOME/.local/share/scan64/maia/maia-1500.pb.gz
```

The official v1.0 asset was 1,258,199 bytes when this guide was verified. Treat the size only as an identity sanity check: the release API did not publish a cryptographic digest.

## Configure Scan64

Create an operator-owned configuration file:

```text
mkdir -p "$HOME/.config/scan64"
```

```toml
# $HOME/.config/scan64/maia.toml
[maia]
binary_path = "/opt/homebrew/bin/lc0"
threads = 1

[maia.checkpoints]
1500 = "/Users/replace-with-your-user/.local/share/scan64/maia/maia-1500.pb.gz"
```

Replace `/Users/replace-with-your-user` with the operator's absolute home path. Relative configuration, binary, and checkpoint paths are rejected. Add additional operator-owned checkpoints using their numeric ratings as keys.

Prepare the Scan64 environment from the repository root:

```text
uv sync --locked
pnpm --dir apps/scan64-web install --frozen-lockfile
pnpm --dir apps/scan64-web build
```

Start the complete local application with the configuration:

```text
SCAN64_MAIA_CONFIG="$HOME/.config/scan64/maia.toml" uv run scan64-cli serve --no-browser
```

Open `http://127.0.0.1:8001` manually, or use the API reference at `/docs`. Create an authenticated play session through `POST /v1/play-sessions` with `opponent_config: {"provider": "maia", "strength": "1500"}`. The Play screen currently creates Stockfish sessions only; there is no Maia picker. Scan64 selects the nearest configured checkpoint. A request that differs from the selected checkpoint persists an approximate-100-Elo granularity disclosure; requests below or above configured coverage instead persist the coverage-gap disclosure. Use `provider = "stockfish"` with `bot_level` for the relative Stockfish ladder; Maia's checkpoint rating is not a Stockfish bot-level conversion.

## Verify

Run the ordinary repository gate. It intentionally excludes checkpoint-dependent tests:

```text
scripts/check.sh
```

Run the operator-only quality gate with absolute binary and checkpoint paths:

```text
SCAN64_MAIA_BINARY=/opt/homebrew/bin/lc0 SCAN64_MAIA_1500_WEIGHTS="$HOME/.local/share/scan64/maia/maia-1500.pb.gz" SCAN64_STOCKFISH_BINARY=/opt/homebrew/bin/stockfish uv run pytest -m real_model tests/integration/test_maia_real_model.py tests/integration/test_maia_realism.py
```

The first test drives a configured API session and verifies that Maia returns a legal move. The second compares Maia's root-policy distribution against the provenance-recorded human reference fixture and an Elo-1500 Stockfish baseline. Both tests have no network path; they use only the locally provisioned operator assets.
