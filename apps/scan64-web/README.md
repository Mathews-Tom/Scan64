# Scan64 web client

The Scan64 web client is a React and TypeScript application built with Vite. It renders the player-facing routes for computer play, PGN import, game history, analysis, Daily Training, profile, opening explorer, famous-game study, and coach dashboard.

## Run locally

Install dependencies from the repository root:

```sh
pnpm --dir apps/scan64-web install --frozen-lockfile
```

Start the complete application with the root launcher:

```sh
scripts/run.sh
```

For client-only development, run:

```sh
pnpm --dir apps/scan64-web dev
```

The Vite development server proxies `/v1` requests to the API at `http://127.0.0.1:8001`.

## Commands

Run these commands from the repository root:

```sh
pnpm --dir apps/scan64-web build
pnpm --dir apps/scan64-web lint
pnpm --dir apps/scan64-web test
pnpm --dir apps/scan64-web test:e2e
pnpm --dir apps/scan64-web lighthouse:pwa
```

`test:e2e` starts its own Vite server on port `5173`. Stop a manually started web server before running it.

The root gate runs every frontend command that is required for a change:

```sh
scripts/check.sh
```

## Browser-test contract

Playwright tests use real pointer input for board moves and lessons. Do not replace board interaction coverage with test-only move hooks; pointer input protects against the layout-boundary regression class that previously left a visually rendered board unable to accept moves.

## User documentation

Use the [root README](../../README.md) for installation and the [user guide](../../docs/user-guide.md) for workflows and manual acceptance testing.
