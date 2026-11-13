# Scan64

**Play chess. Discover recurring mistakes. Train verified lessons.**

Scan64 is an open-source, local-first chess practice and learning application. It lets you play against computer opponents, import PGNs, analyse player-owned games with Stockfish, review diagnoses on an analysis board, and complete verified lessons derived from those diagnoses.

The learning loop is durable and player-scoped:

```text
play or import -> engine analysis -> evidence -> diagnosis -> lesson -> verified attempt -> profile and review update
```

Daily Training composes eligible lessons from active mastery, due review, verified attempt history, fatigue, and a deliberate exploration floor. Coach mode is a separate opt-in practice path: a diagnosed move can produce an interruption only after Scan64 persists its opportunity, review schedule, and study session.

## Current capabilities

- Play against Stockfish; optionally provision Maia for human-like opponent behavior.
- Import PGNs and analyse completed games.
- Browse player-owned game history and deep-link directly to an analysis board.
- Review persisted evaluations and diagnosis markers at their source positions.
- Train from Daily Training and game-review lessons on an interactive board oriented to the solver.
- Track active mastery, evidence, recurring diagnoses, scheduled review, and transfer measurements.
- Use opt-in coach-mode interruptions during practice.
- Export, import, and delete player-derived data.

Position hints keep move reveals until step 3, including castling. Repeated hints reuse a bounded, 128-position in-process engine-result cache keyed by the full FEN, search limits and engine configuration. Only public analysis is shared; authorization, hint levels and session provenance are checked on every request. The cache is discarded on shutdown, after background analysis tasks have been cancelled and awaited and engine pools closed.

Scan64 does **not** yet establish that it improves over-the-board chess performance. It also does not currently provide calibrated behavioural-habit detection, context-conditioned profiling, hosted deployment, or PostgreSQL production verification.

Chess workflows provide labelled strict UCI/canonical English SAN entry, readable positions and legal choices alongside pointer/tap boards, with keyboard replay, hint and practice controls. All shipped routes target WCAG 2.2 AA and narrow-screen reflow; this is not certification or universal assistive-technology support. The human Safari/VoiceOver acceptance pass is pending owner observation; implementation and automated checks alone do not establish conformance. See [keyboard use and accessibility limits](docs/user-guide.md#keyboard-touch-and-screen-readers).

## Distribution and support

Scan64 supports a source-install local application and a Dockerfile for building a local image. An unsigned Apple silicon desktop bundle can also be built from this repository; download availability depends on an owner-named release. No published image, signed/notarized desktop application or hosted service is provided. Run Scan64 only on a machine you operate.


## Run locally on one port

### macOS app (Apple silicon)

When a release includes `Scan64-<version>-macos-arm64.zip`, download it from this repository's GitHub Releases, unzip it, and drag **Scan64.app** to Applications. Python, uv, Node and Stockfish are included or unnecessary at runtime.

The app is **unsigned and not notarized** (its build has only an ad-hoc signature). On first launch macOS Gatekeeper may block it: attempt to open Scan64, then go to **System Settings → Privacy & Security → Open Anyway**, confirm **Open**, and authenticate if prompted. Only bypass the warning for a download whose origin and SHA-256 you have verified. Do not disable Gatekeeper globally.

Scan64 opens your default browser and a small status window. **Open Scan64** reopens the browser; **Quit Scan64**, closing the status window, or **⌘Q / Dock → Quit** stops the server and chess engines. Opening the app again while it runs only opens the existing server. It uses the first free loopback port from 8001 and saves its database and puzzle data under `~/Library/Application Support/Scan64`; replacing the app does not remove these files. Keep browser local storage (player tokens); a different port is a different browser origin, so free the original port to return to that identity, or use an exported archive to move it.

Build locally on macOS arm64 with Xcode Command Line Tools, uv, pnpm and the source prerequisites below:

```sh
scripts/build-macos-app.sh
```

The script verifies the Dockerfile's Stockfish source/network pins, builds Stockfish 18 and the client, freezes a one-folder `.app` with managed Python/Tk, and prints the zip path, SHA-256 and size under `dist/`. It does not publish or attach the zip to a release. Corresponding engine source, networks and licenses are inside the app; see [third-party notices](THIRD_PARTY_NOTICES.md).

### With Docker

From the repository root, with Docker installed (no local Python, Node or Stockfish needed):

```sh
docker build -t scan64 .
docker run --name scan64 -p 127.0.0.1:8001:8001 -v scan64-data:/data scan64
```

Open **http://127.0.0.1:8001**. The image supports Linux amd64 and arm64, includes checksum-verified Stockfish 18, runs as a non-root user, and stores its SQLite database in the named `scan64-data` volume. Stop with Ctrl-C and resume with `docker start -a scan64`; games survive container restarts and replacement when the same volume is reused. Do not delete the volume unless you intend to delete the saved data. No Maia weights are included. Engine source, networks and license are included under `/usr/share/doc/stockfish`; see [third-party notices](THIRD_PARTY_NOTICES.md).

### From a clone

Prerequisites: [uv](https://docs.astral.sh/uv/), Python 3.12+, Node.js 24+, [pnpm](https://pnpm.io/installation) 10.33.3, and Stockfish 18+ available as `stockfish` on `PATH`.

```sh
git clone https://github.com/Mathews-Tom/Scan64.git
cd Scan64
uv sync --locked
pnpm --dir apps/scan64-web install --frozen-lockfile
pnpm --dir apps/scan64-web build
uv run scan64-cli serve
```

The command checks for Stockfish and the client build, serves UI and API at **http://127.0.0.1:8001**, and opens your browser once the server is listening. Use `--no-browser` for a headless session, or `--port 8061` to select another port. It binds to loopback by default; it is not a hardened LAN or hosted deployment.

Enter a display name on your first visit and select **Start learning**. **Today** offers Play, Train and Review; **Library** groups the existing study and import screens. The active player identity and its access token stay in that browser's local storage: keep them to access your saved games, including after a container restart.

Client deep links such as `/games/<id>/analysis` work on reload; `/docs`, `/openapi.json`, `/health` and `/v1` remain API routes. `SCAN64_DATABASE_URL` overrides the clone's default SQLite URL, `sqlite:///database.db`:

```sh
SCAN64_DATABASE_URL="sqlite:////absolute/path/to/scan64.db" uv run scan64-cli serve
```

### Development servers

After installing the clone prerequisites and dependencies, `scripts/run.sh` remains the hot-reload development path: API on `http://127.0.0.1:8001`, Vite client on `http://127.0.0.1:5173`. The API also works without a client build.

## Documentation

### Use Scan64

- [User guide](docs/user-guide.md): play, import, analyse, train, coach mode, transfer measurement, data lifecycle, and manual acceptance walkthrough.
- [Maia operator provisioning](docs/maia.md): configure an operator-owned Maia installation.
- [Opening-family rationale](docs/content/opening-families.md): current curated opening content.

### Understand the system

- [System overview](docs/system-overview.md): product boundaries, current state, and known limitations.
- [System design](docs/system-design.md): implemented runtime, data, authorization, and learning-loop architecture.
- [Live API reference](http://127.0.0.1:8001/docs): generated request and response schemas while the API is running.
- [LessonSpec package](packages/chess-lesson-spec/README.md): renderer-independent lesson protocol.

### Develop Scan64

- [Web client guide](apps/scan64-web/README.md): frontend commands and browser-test workflow.
- [Changelog](CHANGELOG.md): released functionality.
- [Security policy](SECURITY.md): submit vulnerabilities through a private GitHub Security Advisory.

## Maia model weights

Scan64 does not bundle, download, cache, redistribute, or host Maia model weights. Operators must establish their right to obtain and use a checkpoint, provision it outside the repository, and configure it explicitly. See the [Maia operator provisioning guide](docs/maia.md).

## Optional LLM explanations

Deterministic, evidence-grounded template explanations work without an LLM. An operator can configure an explanation provider through `SCAN64_LLM_CONFIG`; generated claims are validated against the diagnosis evidence before Scan64 uses them.