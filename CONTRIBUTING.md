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

CI runs the repository gate on every pull request. Locally, run the checks that cover your change ([rule 9](#rules)). Run the whole gate yourself only before an epic merges into `main`, before a release, or when no narrower check covers the change:

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
one day on dry runs and 14 days on published-release runs. Published-release
signing jobs do not restore shared uv, pnpm or Rust build caches. No dispatch or
pull-request event can run the upload job.

Release preparation and publication require owner approval; this workflow does
not create tags or releases. Keep signing keys out of source and logs, and follow
the [signing and key-rotation policy](SECURITY.md#desktop-update-signing).

## Working across machines

Scan64 work runs on several machines at once. These rules keep two machines from taking the same ticket. The [Scan64 board](https://github.com/users/Mathews-Tom/projects/1) shows Status and Machine for every issue and PR, and `scripts/scan64_coord.py` applies the rules.

### Machines

Each machine has a `machine:<name>` label. The part of the name before the first `-` is its OS, which the `needs-test:<os>` labels use.

| Machine | Label | Preferred work |
|---|---|---|
| Linux, RTX 5090 | `machine:linux-5090` | LLM runs and benchmarks, engine runs and builds, long jobs |
| Windows laptop, RTX 4080 | `machine:windows-4080` | Code that needs no GPU or engine; Windows-only behaviour (paths, process handling, CRLF); Windows test runs |
| Mac, current macOS | `machine:mac` | macOS test runs, macOS desktop smoke, current Safari and WebKit checks |
| Mac, old macOS | `machine:mac-old` | Safari 14–16 and old WebKit checks; desktop smoke on older macOS |

If an LLM fits both GPUs, it runs on the 5090. If an engine runs on Linux and Windows, it runs on Linux. Each OS keeps a small smoke run for its own platform behaviour. Adding a machine means adding its `machine:<name>` label and a row here, plus a `needs-test:<os>` label for a new OS.

### Rules

1. **One ticket, one machine.** The `machine:<name>` label on an issue or PR is the claim, and the board's Machine field mirrors it.
2. **Claim before you start:** `scan64-coord claim <N>`. It refuses a ticket that another machine has claimed.
3. **Never push to a ticket that another machine claimed.** To hand over, agree in a comment on the ticket, then run `scan64-coord claim <N> --takeover`. For a share of a ticket another machine owns, `scan64-coord split <N> "<title>"` creates and claims a sub-issue.
4. **Release when you stop:** `scan64-coord release <N> --review` when your PR is open (the machine keeps the claim, and the ticket's open PRs get its label); `--done` when the work is merged; no flag to give the ticket back.
5. **Tests on another OS only when the change can behave differently there.** CI runs the full Python suite on Linux and the desktop smoke test and installer build on all three OSes. Add `needs-test:windows` or `needs-test:mac` only when the PR touches something CI can't check on that OS: code that branches on the platform, process, socket, signal or file-path handling, the desktop shell, or Safari and WebKit rendering. Say in a PR comment what to check. The machine with that OS runs those checks and the changed test files, and posts the results as a PR comment. It does not push to the PR. Both Macs see `needs-test:mac`; to reach one of them only, for example for a Safari 14–16 check, use `scan64-coord ask <N> mac-old "<request>"`.
6. **Requests between machines go through GitHub, not chat.** `scan64-coord ask <N> <machine> "<request>"` comments on issue or PR N and adds `needs:<machine>`. The other machine sees N under "Waiting on this machine" in its status at session start, acts, and replies with `scan64-coord answer <N> "<result>"`, which removes the label. Requests without a ticket go on the coordination issue #404. Requests for a person rather than an agent mention them in the comment as usual.
7. **Every change goes through a pull request.** This includes one-line fixes and changes to an epic branch. Never push directly to `main` or to an epic branch. Name branches `<type>/<issue>-<slug>`, for example `feat/397-engine-compat`. While an epic is open, branch from the epic branch and target it. Sign off every commit. Never merge your own pull request; the maintainer reviews every pull request before it reaches the epic branch or `main`.
8. **No status files in the repository.** Status lives on the board and in the issues. Local campaign notes stay local.
9. **Run only checks that can fail.** Run a check only if a plausible defect in the change could make it fail. For a pull request, that means its changed test files and the checks for the areas it touches (for example, frontend checks only when the frontend changed); CI covers the rest. Don't rerun a check on a commit that already has a result, and don't repeat the epic's full suite for each pull request stacked on it. State in the PR what you ran and what you left to CI.

### Setup on each machine (once)

From a Scan64 checkout, with `gh` logged in:

```sh
python3 scripts/scan64_coord.py setup <machine>   # for example: setup mac
```

On Windows, use `python` instead of `python3`. Setup writes the machine name to `~/.config/scan64/machine`, installs a copy of the tool in `~/.local/share/scan64/coord` and a `scan64-coord` command in `~/.local/bin`, and prints the current status. It changes nothing in the checkout. A machine that used the earlier standalone `scan64-coord` repo reruns `setup` and `install-hooks`: its hooks are pointed at the new copy, and the old clone in `~/.local/share/scan64-coord` can then be deleted.

With Claude Code, also run `scan64-coord install-hooks`, then open `/hooks` once or restart Claude Code. This adds two hooks to `~/.claude/settings.json` and keeps the existing ones:

- At session start, the agent gets `scan64-coord status` in its context, after the installed copy refreshes itself from `origin/main`, or from `origin/epic/371` while the tool is only on the epic branch.
- Before a `git push` or `gh pr create` in a Scan64 checkout, the hook blocks the command if the branch's ticket belongs to another machine.

Other agents and people run `scan64-coord status` at the start of a session and `scan64-coord check` before pushing.

### Commands

| Command | Effect |
|---|---|
| `scan64-coord status` | Claims per machine, PRs that need this OS, unclaimed and unblocked tickets |
| `scan64-coord claim <N> [--takeover]` | Claim issue or PR N for this machine |
| `scan64-coord split <N> "<title>"` | Create and claim a sub-issue for this machine's share of N |
| `scan64-coord release <N> [--review\|--done]` | Change or drop the claim |
| `scan64-coord ask <N> <machine> "<request>"` | Ask another machine to act on N: comment plus `needs:<machine>` |
| `scan64-coord answer <N> "<result>"` | Reply on N and remove `needs:<this machine>` |
| `scan64-coord check [branch]` | Exit 1 if another machine owns the branch's ticket |
| `scan64-coord setup <machine>` | One-time machine setup |
| `scan64-coord install-hooks` | Add the Claude Code hooks |
