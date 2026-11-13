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
