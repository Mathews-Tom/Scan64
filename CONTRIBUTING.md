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

## macOS release automation

`Release macOS` builds the unsigned Apple silicon app on `macos-15` when a GitHub
Release is published. It does not sign or notarize the app with Apple: Ed25519
signatures authenticate the update manifest only. The existing CI workflow is
unchanged.

### Signing key (one-time maintainer setup)

Use the existing release key if one is already configured; do not generate a new
key for each release. To initialize a new signing identity, send the private seed
**straight into a secret store**, never a terminal, log, shell argument or source
file. For example, from an authenticated maintainer shell:

```sh
set -o pipefail
key_dir=$(mktemp -d)
uv run python scripts/sign-release.py keygen --public-out "$key_dir/release_signing_key.pub" |
  gh secret set SCAN64_RELEASE_SIGNING_KEY --repo Mathews-Tom/Scan64
```

`keygen` writes only the base64 public key file and emits the base64 32-byte private
seed once on stdout. The pipe stores it as the repository Actions secret
`SCAN64_RELEASE_SIGNING_KEY`. Keep a recovery copy in the operator's secret store,
not in Git. Commit the matching public key as
`src/scan64/release_signing_key.pub` through a reviewed PR before shipping in-app
updates. Existing public files are never overwritten by `keygen`; key rotation
requires a deliberate trust-anchor migration, not just replacing the secret.

### Publish or dry-run

1. Merge the release preparation (package version, release changelog and matching
   committed public key). The selected tag must contain the build/signing scripts
   and their locked dependencies.
2. Create a tag exactly equal to `v` plus `pyproject.toml`'s version, then publish
   its GitHub Release through the usual maintainer release process. Publishing
   triggers the build; it never creates a tag or a release itself.
3. Alternatively, select **Actions → Release macOS → Run workflow**, enter an
   existing tag and leave `dry_run` enabled (the default). This builds, signs and
   verifies without changing release assets. The signing secret is required even
   for a dry run. Disable `dry_run` only to attach assets to an existing release.
4. Read the run summary: tag, dry-run status, canonical manifest, checksum and
   signature verification source. A missing committed public key uses a
   seed-derived public key in-process and is explicitly marked **bootstrap only**;
   it is not proof of compatibility with an installed app's trust anchor.

Build and signing jobs have read-only repository permissions. Signing runs on a
fresh runner, with locked non-development Python dependencies, uv cache restore/save
disabled and no frontend or PyInstaller build code; it downloads only the zip and
checks out the exact build commit. The secret is injected only into its signing
step. Only the separate upload
job gets `contents: write`. It uploads with `gh release upload --clobber`, so a rerun
replaces the four assets for that tag. Dry runs retain the same verified assets as
an Actions artifact for seven days and skip the release upload job.

### Check the four release assets

Download these together from the release: `Scan64-<version>-macos-arm64.zip`,
`Scan64-<version>-macos-arm64.zip.sha256`, `update.json` and `update.json.sig`.
From the directory containing them, check the archive checksum:

```sh
shasum -a 256 -c Scan64-<version>-macos-arm64.zip.sha256
```

From a checkout containing the trusted public key, verify the exact manifest bytes:

```sh
uv run python scripts/sign-release.py verify /path/to/update.json \
  --public-key src/scan64/release_signing_key.pub
```

Verification also accepts the base64 public key directly. Compare the manifest's
`asset`, `version`, `platform`, `size` and `sha256` with the downloaded archive;
successful signature verification alone does not check the zip. To recreate the
canonical manifest without the private key:

```sh
uv run python scripts/sign-release.py manifest \
  /path/to/Scan64-<version>-macos-arm64.zip <version> --output /tmp/scan64-update-check.json
cmp /path/to/update.json /tmp/scan64-update-check.json
```

For local signing, inject `SCAN64_RELEASE_SIGNING_KEY` from the secret store into
only `uv run python scripts/sign-release.py sign /path/to/update.json`; never put
the seed in the command line or enable shell tracing.
