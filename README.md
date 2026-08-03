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
- Train from Daily Training and game-review lessons on an interactive board.
- Track active mastery, evidence, recurring diagnoses, scheduled review, and transfer measurements.
- Use opt-in coach-mode interruptions during practice.
- Export, import, and delete player-derived data.

Scan64 does **not** yet establish that it improves over-the-board chess performance. It also does not currently provide calibrated behavioural-habit detection, context-conditioned profiling, keyboard board interaction, hosted deployment, or PostgreSQL production verification.

## Run locally

### Prerequisites

- [uv](https://docs.astral.sh/uv/) for the Python 3.12+ backend.
- [pnpm](https://pnpm.io/installation) for the React web client.
- Stockfish 18 or later, available as `stockfish` on `PATH`.

### Install and start

```sh
git clone https://github.com/Mathews-Tom/Scan64.git
cd Scan64
uv sync --locked
pnpm --dir apps/scan64-web install --frozen-lockfile
scripts/run.sh
```

The API listens at `http://127.0.0.1:8001`; the web client is at `http://127.0.0.1:5173`.

Open the web client, select **Play Game**, enter a player ID, and start a game. The active player identity stays in that browser's local storage.

`SCAN64_DATABASE_URL` overrides the default SQLite URL, `sqlite:///database.db`:

```sh
SCAN64_DATABASE_URL="sqlite:////absolute/path/to/scan64.db" scripts/run.sh
```

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

## Maia model weights

Scan64 does not bundle, download, cache, redistribute, or host Maia model weights. Operators must establish their right to obtain and use a checkpoint, provision it outside the repository, and configure it explicitly. See the [Maia operator provisioning guide](docs/maia.md).

## Optional LLM explanations

Deterministic, evidence-grounded template explanations work without an LLM. An operator can configure an explanation provider through `SCAN64_LLM_CONFIG`; generated claims are validated against the diagnosis evidence before Scan64 uses them.