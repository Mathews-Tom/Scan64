# Scan64 web client

The Scan64 web client is a React and TypeScript application built with Vite. It renders Today and onboarding, computer play with bot levels/colour choice/take-back and three-step hints, PGN import, game history and per-move review with key-moment retries, Daily Training, Puzzles, Profile, Library (opening explorer and famous-game study), and the linked-student Coach dashboard. Today and Profile expose recurring patterns, daily goals, calendar streaks and tracked practice targets; Puzzles has its own rating, stable daily sets, themes and solve streak.

## Run locally

Follow the [root prerequisites](../../README.md) first, including Python/uv and Stockfish for the API, and Node.js/pnpm for the client. Install client dependencies from the repository root:

```sh
pnpm --dir apps/scan64-web install --frozen-lockfile
```

For the normal one-port application, build the client and serve it with the API:

```sh
pnpm --dir apps/scan64-web build
uv run scan64-cli serve --no-browser
```

Open `http://127.0.0.1:8001` manually. `--port PORT` changes the port; `SCAN64_WEB_DIST` can select a different build directory. Docker and the macOS app use the same built client.

For hot-reload development, start both servers with the root launcher:

```sh
scripts/run.sh
```

For client-only development, run:

```sh
pnpm --dir apps/scan64-web dev
```

Vite defaults to `http://localhost:5173` and proxies `/v1` requests to the API at `http://127.0.0.1:8001`; client-only development still needs that API. `/health` and the API reference at `/docs` are on the API origin, not the Vite proxy.

## Commands

Run these commands from the repository root:

```sh
pnpm --dir apps/scan64-web build
pnpm --dir apps/scan64-web lint
pnpm --dir apps/scan64-web test
pnpm --dir apps/scan64-web test:e2e
pnpm --dir apps/scan64-web lighthouse:pwa
```

`test:e2e` targets `http://localhost:5173`: Playwright starts Vite when needed and reuses an existing server outside CI. In CI it requires a free port. Stop an unrelated Vite server to avoid testing the wrong application.

The root gate runs the frontend install, build, lint, unit tests and Playwright tests alongside the backend checks. `lighthouse:pwa` is a separate report command, not part of that gate:

```sh
scripts/check.sh
```

## Browser-test contract

Playwright tests use real pointer input for board moves and lessons. Do not replace board interaction coverage with test-only move hooks; pointer input protects against the layout-boundary regression class that previously left a visually rendered board unable to accept moves.

Shared boards also expose readable coordinates, pieces, side to move and legal choices, strict UCI/canonical English SAN entry, and explicit promotion choices. Preserve keyboard, focus, live-announcement, reduced-motion and narrow-screen behavior when changing controls. Human Safari/VoiceOver acceptance remains pending owner observation; automated role/name and touch-emulation checks do not establish assistive-technology conformance.

## Identity system

`src/tokens.css` is the single source of palette, typography, radius and motion values. Host Grotesk and JetBrains Mono are bundled under `public/fonts` with their Open Font License texts; the client never requests a font CDN. Reading copy uses ink rather than the quieter metadata colour. Board annotations retain written descriptions alongside their visual cues.

Settings → Appearance selects System, Light or Dark and remembers the choice in this browser. Navigation uses a 208px sidebar at 1024px, an icon rail at 768px and a two-row bottom tab bar on smaller screens; every destination retains its existing name and route. Settings is available before creating a player.

On short screens, navigation scrolls below the fixed brand rather than underneath it. Every navigation layout reserves space for the complete keyboard focus outline. Filled-primary actions are explicit; being a form submit button does not assign visual priority. Generated live-run images in `artifacts/m81/` are ignored and belong on the separate evidence branch, not in the client source.

Play, Review/Analysis, Puzzles, Daily Training, Famous Games and Explorer share the ivory/sage board, mint selection and last-move highlights, and a board-first workspace. Controls sit beside the board from 900px and stack below it on narrower screens. Best-move, threat and alternative annotations name their kind in the written description and number each candidate once. Their kind stays tied to the original position while previewing a line; colour is never the only cue. The board also fits the usable viewport above mobile navigation, including short landscape screens.

## User documentation

Use the [root README](../../README.md) for installation and the [user guide](../../docs/user-guide.md) for workflows and manual acceptance testing.
