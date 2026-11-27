# Contributing to Scan64

We welcome contributions! To ensure compliance with our governance model, we require that all contributions are accompanied by a Developer Certificate of Origin (DCO) sign-off.

## Choosing what to work on

The [roadmap](docs/roadmap.md) lists the features Scan64 is expected to grow into, as a checklist grouped by area and ordered in waves. Items marked **Ready** have no unfinished dependency. Comment on the item's issue, or open one titled with its ID, before starting, and follow the roadmap's steps for picking up an item.

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

## Engineering guardrails

Each rule below comes from a real failure, review finding or repair round in Scan64's development. Check a design against them before you start and a pull request against them before you ask for review. The [roadmap](docs/roadmap.md#principles-every-item-keeps) lists the product principles every change keeps; these rules are how the code keeps them.

### Saved data and migrations

| # | Rule | What it prevents |
|---:|---|---|
| 1 | Decide at design time whether the change persists anything new. A change that needs a migration gets one; never hide new persisted state in a JSON column to avoid it. | State that export, reset and downgrade paths cannot see |
| 2 | Chain a new Alembic revision after the latest revision on `main` when the pull request merges, not the one that existed when the branch started, and re-chain if another migration lands first. Earlier revisions and historical test fixtures stay unchanged; never re-pin an old test to the new head. | Forked migration graphs and silently weakened upgrade tests |
| 3 | Every new persisted record joins account export and import, Reset Progress and Delete Data in the same pull request. Restored records are historical: never replay scheduling or award learning from them. | Lost progress after a restore, or learning recreated after a reset |
| 4 | A downgrade refuses while data depends on the revision. Recovery means opening the pre-migration backup with the older version, never deleting live rows to force a downgrade. | Data loss during rollback |
| 5 | Missing data is "unavailable", never zero. Unsupported saved input means missing coverage, never zero occurrences. | Statistics that report absent evidence as evidence of absence |
| 6 | Measurements are immutable. Reproduce an old result from its pinned data commit, never by rescoring it with corrected rules, and never rewrite historical trust. When the proof is weak, reject it; do not relax the gate. | A trust table that changes without a new measurement |
| 7 | Derived records are new rows linked to their source. Never mutate a source game's provenance. | Evidence whose origin can no longer be stated |

### Authoritative state and concurrency

| # | Rule | What it prevents |
|---:|---|---|
| 8 | The server decides anything that settles a result: answers, clocks, flag-fall and terminal states. The client displays them. | A client that can award itself progress or a win |
| 9 | Commit the accepted player action (move, clock debit, deadline) before awaiting engine or diagnostic work. | A bot failure or restart that loses the player's move |
| 10 | A mutation carries the generation or clock version it was computed against. Stale and ABA commits return 409; foreign resources return the same 404 as missing ones. | Moves committed against a board the player never saw |
| 11 | Build a response from one committed value snapshot, not from ORM reads after the commit. | A concurrent resignation landing between commit and response and producing a mixed reply |
| 12 | Waits derived from UTC deadlines are bounded and re-check wall-clock time on every wake. | Early or late flag-fall after the system clock changes |
| 13 | Deadlines are event-driven, persisted and recovered at startup. A GET never starts engine work; a pending bot turn resumes through an owned mutation. | Engine work triggered by reads, and clocks that stop on restart |
| 14 | Engine capacity is bounded. Reuse the interactive and batch Stockfish pools and surface busy (503) or rate-limited (429) states; never add a pool for one feature. | A feature starving play of engine time |

### Push, streams and clients

| # | Rule | What it prevents |
|---:|---|---|
| 15 | Push progress over authenticated fetch NDJSON with the bearer and shell-token headers. Never put credentials in URLs; never weaken authentication to use `EventSource`. | Credentials leaking through URLs and logs |
| 16 | Register a listener before reading the current snapshot; publish only committed transitions; bound and coalesce queues; release subscriptions on terminal state, disconnect and owner deletion; cap concurrent streams. No transaction or thread per waiting listener and no hidden server polling. | Missed terminal states, leaked listeners and resource exhaustion |
| 17 | Mutate shared client storage atomically: an IndexedDB read-modify-write is one readwrite transaction (`idb-keyval` `update`, as in `apps/scan64-web/src/api/offlineQueue.ts`). | Lost offline moves when two tabs write the queue at once |
| 18 | Split code at real route boundaries; a static import of a lazily loaded module defeats the split. | Bundles that silently stop splitting |
| 19 | Treat imported files as untrusted: bound them before parsing, reject rather than coerce, render text only, and re-verify every lesson with the local engine before it appears. | Third-party content becoming trusted evidence |
| 20 | Consent is per installation: it never travels in an archive and never enables another player. The frozen desktop build never reads developer configuration from the environment. | Opt-ins that spread beyond the person who gave them |

### Tests

| # | Rule | What it prevents |
|---:|---|---|
| 21 | Integration tests that involve background workers use the shared `db_session` fixture in `tests/integration/conftest.py`: a per-test file database built through the production engine factory. A shared in-memory `StaticPool` connection lets an independent worker session roll back a test's uncommitted rows. | Tests that fail or pass because a worker closed its session |
| 22 | Race tests inject the competing write at the exact boundary, after the read and before the compare-and-swap. No sleeps, retries or skips. | Race tests that pass by timing luck |
| 23 | Assert after the call, outside any production exception boundary. An assertion inside a callback that production code catches proves nothing. | Tests that cannot fail |
| 24 | Test watchdogs exceed the combined phase budgets. Cancel pending tasks and await them before closing the client. | Teardown races reported as product failures |
| 25 | Concurrency regressions use real components: two browser pages with independent IndexedDB connections, real Stockfish for pool contention. No mocked IndexedDB. | Mocks that serialise what production interleaves |

### Pull requests, CI and releases

| # | Rule | What it prevents |
|---:|---|---|
| 26 | A CI watcher's exit status is not a check result. Read each check's conclusion on the exact head commit. | Merging on a cancelled job |
| 27 | A hosted-runner capacity cancellation is a failed required check, not a source failure. Ask a maintainer to rerun the failed jobs; do not change source for it. | Source "fixes" for infrastructure failures |
| 28 | Do not request automated bot reviews; the configured reviewer runs on its own. A skipped, neutral or rate-limited automated review is not a review. | Treating "review skipped" as "review passed" |
| 29 | When a round of fixes ends with new review findings, take the approach back to the issue instead of starting another open-ended round. | Unbounded fix-and-review cycles |
| 30 | Feature pull requests never edit `CHANGELOG.md` or version files. A release is prepared once, after its changes merge. | Changelog conflicts and half-released trains |
| 31 | Release versions never use semver prereleases such as `1.0.0-rc.1`. Tauri writes the version verbatim into the `.deb` control file, where dpkg orders `1.0.0-rc.1` above `1.0.0`. | A final release that installs as a downgrade |

Propose a new rule in the pull request that fixes the failure it comes from.

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
5. **Tests on another OS only when the change can behave differently there.** CI runs the full Python suite on Linux, the Python suite and strict typing on macOS (`Python (macos-15)`), and the desktop smoke test and installer build on all three OSes. Add `needs-test:windows` or `needs-test:mac` only when the PR touches something CI can't check on that OS: Windows behaviour beyond the desktop smoke test (paths, process handling, CRLF), macOS versions newer than CI's, the installed desktop app, or Safari and WebKit rendering. Say in a PR comment what to check. The machine with that OS runs those checks and the changed test files, and posts the results as a PR comment. It does not push to the PR. Both Macs see `needs-test:mac`; to reach one of them only, for example for a Safari 14–16 check, use `scan64-coord ask <N> mac-old "<request>"`.
6. **Requests between machines go through GitHub, not chat.** `scan64-coord ask <N> <machine> "<request>"` comments on issue or PR N and adds `needs:<machine>`. The other machine sees N under "Waiting on this machine" in its status at session start, acts, and replies with `scan64-coord answer <N> "<result>"`, which removes the label. Requests without a ticket go on the coordination issue #404. Requests for a person rather than an agent mention them in the comment as usual.
7. **Every change goes through a pull request.** This includes one-line fixes and changes to an epic branch. Never push directly to `main` or to an epic branch. Name branches `<type>/<issue>-<slug>`, for example `feat/397-engine-compat`. While an epic is open, branch from the epic branch and target it. Sign off every commit. Anyone can merge any pull request, their own included, once someone other than its author has approved it and the required checks pass. A push after the approval needs a new approval. GitHub enforces this on `main` and on `epic/*` branches.
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
