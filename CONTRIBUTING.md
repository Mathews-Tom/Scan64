# Contributing to Scan64

We welcome contributions! To ensure compliance with our governance model, we require that all contributions are accompanied by a Developer Certificate of Origin (DCO) sign-off.

## Developer Certificate of Origin (DCO)

All commits must include a `Signed-off-by` line. This line certifies that you wrote the patch or otherwise have the right to pass it on as an open-source patch.

Example:
```
Signed-off-by: Random J Developer <random@developer.example.org>
```

You can automatically add this sign-off to your commits by using the `-s` or `--signoff` flag with `git commit`:
```
git commit -s -m "feat: your feature"
```

## Making Changes
- Follow the existing code style.
- Ensure all tests pass.
- Write tests for any new functionality.

## Local development

Use the [root quickstart](README.md) for the source prerequisites and one-port application. From the repository root, `scripts/run.sh` runs the API on `127.0.0.1:8001` and the hot-reload web client on port 5173. The [web client guide](apps/scan64-web/README.md) lists frontend commands and the browser-test contract; [system design](docs/system-design.md) explains the implementation boundaries.

Use disposable data when developing, especially for import, restore or deletion:

```sh
export SCAN64_DATABASE_URL=sqlite:////tmp/scan64-contributing.db
export SCAN64_DATA_DIR=/tmp/scan64-contributing-data
```

`SCAN64_DATA_DIR` selects the puzzle download directory, not the database. Keep browser local storage if you need to retain the player bearer token, and back up a stopped database before testing migrations on real data.

## Checks and documentation

Run the repository gate after code changes:

```sh
scripts/check.sh
```

It covers backend tests excluding `real_model`, Ruff, strict typing, recorded diagnosis precision, dependency licences, the production web build, frontend lint/unit tests and Playwright. The checkpoint-dependent [Maia operator gate](docs/maia.md#verify) and the web client's Lighthouse report are separate.

Keep board coverage based on real pointer interaction, alongside keyboard UCI/SAN, readable-position, promotion, focus and responsive controls; do not replace it with test-only move hooks. Human macOS Safari/VoiceOver acceptance is still pending owner observation, not an automated pass. Record actual versions and observations when completing that acceptance.

Update existing user and architecture documentation when behavior changes, and describe evidence limits without claiming measured chess improvement. Release changes belong in the versioned changelog when preparing a release.

## Local desktop builds

Follow the [macOS build steps](README.md#build-the-macos-app-apple-silicon).
`scripts/build-server.py` and `scan64-server.spec` stage the frozen server,
hash-checked Stockfish source and networks, and notices as Tauri resources.
The build uses managed Python so its exact component licences can be recovered.
There is no Tk prerequisite.

For source-server development, build a debug shell and set
`SCAN64_SERVER_COMMAND` to a JSON argument array whose executable is absolute,
for example `["/absolute/checkout/.venv/bin/python","-m","scan64.desktop_server"]`.
This override and the smoke environment are debug-only; release builds execute
only their bundled server. Never put the per-launch shell token in logs.
`SCAN64_DEBUG_PUZZLE_URL` selects a local download fixture for debug builds
using the frozen bundled server; the cleared release environment drops it.

`scripts/check.sh` also runs cargo format, strict Clippy and Rust tests.
The Desktop pull-request workflow proves the shell-origin request and child
lifecycle on macOS, Windows and Linux using a source server. Its installer jobs
also install/extract the packaged apps, exercise the frozen server and Stockfish,
and prove signed updates with per-run test keys. Local apps are unsigned/ad-hoc
signed, not notarized.

## Desktop release workflow

The `Release` workflow builds the macOS arm64 `.dmg` and `.app.tar.gz`, Windows
x64 NSIS `-setup.exe`, and Linux x64 `.AppImage` and `.deb`. Updater artifacts
(`.app.tar.gz`, NSIS installer, AppImage) carry `.sig` files. Linux `.deb`
installations use manual replacement rather than the in-app updater.

Changes to `.github/workflows/release.yml` or `scripts/release/**` trigger a
same-repository pull-request dry run. After the workflow is on the default
branch, `workflow_dispatch` also builds and verifies without publishing. Both
use the owner's Actions signing secrets and the committed production public
key, not the update end-to-end test overlay. The release build guard is not
bypassed. Before release preparation, dry runs use the current Tauri version
(`0.0.0` initially), even if the Python version differs; their generated download
URLs are evidence only, not published assets.

On `release: published`, the isolated verifier requires `v<version>` to match
`pyproject.toml`, `tauri.conf.json`, `Cargo.toml` and the desktop `Cargo.lock`.
It verifies every signature and its authenticated version, then creates
`latest.json` with three platform download URLs and inline signatures. The only
write-permission job attaches that manifest, all installers, update bundles and
signatures to the existing release. CI artifacts retain the verified files for
14 days. No dispatch or pull-request event can run that upload job.

Release preparation and publication require owner approval; this workflow does
not create tags or releases. Keep signing keys out of source and logs, and follow
the [signing and key-rotation policy](SECURITY.md#desktop-update-signing).
