# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.1.x | :white_check_mark: |
| 1.0.x | :white_check_mark: |
| 0.12.x | :white_check_mark: |
| 0.11.x | :white_check_mark: |
| 0.10.x | :white_check_mark: |
| 0.9.x | :white_check_mark: |
| < 0.9 | :x: |
| Main | :white_check_mark: |

Security fixes ship only as new releases from `main` through the in-app updater's single channel. Supported 0.9.x–1.0.x installations receive them by updating to the latest release; there are no maintenance branches for older lines.

## Reporting a Vulnerability

Do not disclose vulnerabilities, exploit details, or credentials in a public issue. Submit a private report through [GitHub Security Advisories](https://github.com/Mathews-Tom/Scan64/security/advisories/new).

Include the affected version, a minimal reproduction, impact, and any proposed mitigation. Maintainers will acknowledge the report and coordinate disclosure through the advisory.

## Desktop server boundary

The Tauri desktop server binds only to `127.0.0.1` and serves no web-client files.
The shell generates a fresh secret for each server launch and sends it in
`X-Scan64-Shell-Token`. Every actual request except `GET /health` requires an
exact, constant-time token match; health returns only `{"status":"ok"}`.
CORS preflights need no token, but only `tauri://localhost`,
`http://tauri.localhost` and `https://tauri.localhost` are allowed. There is no
wildcard origin. The separate player bearer-token ownership checks still apply.

The token prevents unauthenticated loopback requests, including requests from
unrelated browser pages. CORS is a browser restriction, not authentication or
an operating-system sandbox: another process that obtains the secret can use
the API, and software running as the same OS user may be able to inspect the
server environment or the app's memory. Do not log or disclose the shell token.
Browser/source and container servers do not install this desktop-only middleware.

On macOS/Linux the shell enforces owner-only (`0700`) data and log directories,
including directories left by an earlier launch. The server also enforces its
data-directory permissions when invoked directly. Windows uses the per-user
app-data directory's inherited access controls.

The shell retains the server's stdin pipe. EOF requests graceful shutdown and
arms an independent three-second hard-exit deadline, including during startup
or a hung engine shutdown. On macOS/Linux the server owns and kills its isolated
process group, including Stockfish. The shell's five-second hard-stop fallback
also kills the whole POSIX group, not just the server PID. On Windows the server
requires a kill-on-close Job Object containing itself and its descendants;
process exit closes the sole job handle and kills the engines. Job setup failure
stops startup. These mechanisms
do not depend on Stockfish cooperating with UCI shutdown. The five-second
shell-death requirement is subject to the three-OS lifecycle checks, not a claim
that the desktop shell is a security sandbox.

## Desktop update signing

The unified desktop updater accepts an update only after Tauri verifies its
signature against the public key compiled into `plugins.updater.pubkey`.
`plugins.updater.requireSignedVersion` is required to be `true` in production:
the signature's authenticated version must match the version announced in the
manifest. Missing signed versions and version mismatches fail verification, so
an older correctly signed bundle cannot be relabeled as a newer release.
The production endpoint is the HTTPS GitHub Releases `latest.json` for
`Mathews-Tom/Scan64`; TLS certificate and hostname validation remain enabled for
the manifest and download. This signing is separate from Apple notarization and
Windows code signing: installers remain unsigned by those platform authorities.

The owner generates and retains the production private key **offline**, with an
offline backup, and supplies the key and password only through the Actions secret
store (`TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`). Neither
belongs in source, artifacts or logs. Losing the signing key requires users to
reinstall manually; treat a compromise as a security incident, stop publication,
and coordinate replacement through the private reporting channel above.

The committed production public key identifies minisign key
`A1BF33DC0D262D9E`. Release builds validate it without the PR-only
`SCAN64_UPDATER_DRY_RUN` bypass. Replacing or losing this key requires **one
manual reinstall** of a build containing the new public key; the old installation
cannot authenticate the replacement key. Never silently rotate it.

The `Release` workflow builds on macOS arm64, Windows x64 and Linux x64 with
read-only repository permissions. Published-release signing jobs do not restore
shared uv, pnpm or Rust build caches. Only the Tauri build step receives the two
signing secrets; no step prints environment values or writes signing keys to
artifacts. Signed dry-run workflow artifacts expire after one day (14 days for
published-release workflow artifacts). A separate, secret-free runner uses pinned
minisign to verify every
update signature and its authenticated version against the committed public key,
then creates `latest.json`. On published releases it also requires the tag,
Python, Tauri, crate and crate-lock versions to match. Only the final upload job
has `contents: write`; it downloads the verified assets without executing
repository or artifact code and attaches them to the existing published release.
Pull-request runs (same-repository heads only) and manual dispatches build and
verify identically but **never upload to a release**. Review workflow changes
carefully: a same-repository author able to modify the signing step is inside
the Actions-secret trust boundary. Fork pull requests never receive this job.

The build guard rejects insecure transport,
disabled TLS certificate or hostname validation, an unexpected production
endpoint, or a missing signed-version requirement, including during dry runs.
The build guard accepts only the
allowlisted canonical updater keys: `pubkey`, `endpoints`,
`dangerousInsecureTransportProtocol`, `requireSignedVersion`, and `windows`.
When present, `windows` must be an object containing only the canonical
`installMode` key. Every other key at either level, including installer arguments,
TLS verification bypass flags, downgrade flags, and kebab-case aliases, is
rejected in every build mode. Insecure transport is permitted only within the
explicit loopback HTTP test boundary, never in normal
debug builds or PR dry runs. Test builds require the `updater-test`
Cargo feature and `SCAN64_UPDATER_TEST=1`; they use a per-run temporary test key
and a loopback-only HTTP overlay that also requires signed versions. Never
publish a test-configured build as a release.

Installation requires the user's explicit **Restart to update** action, and the
shell stops its server before replacing the app. On first startup under a newer
app version, a SQLite backup precedes any migrations; failure prevents startup.
The newest three snapshots are kept under the app data directory's `backups/`.
They contain private player data and need the same protection as the live
database. This is database protection, not automatic app-bundle rollback.
