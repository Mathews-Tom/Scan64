# Scan64 — System Overview

Scan64 is a local-first chess practice and learning application. It gives a player one owned path from a game to a verified training result:

```text
play or import -> analyse -> persist evidence -> diagnose -> serve lesson -> verify answer -> update mastery and review
```

The application is currently released as `0.5.1`. Its release record is [CHANGELOG.md](../CHANGELOG.md).

## What works today

### Play, import, and review

- Play Stockfish at eight relative bot levels, choose White, Black or Random, promote, resign, take back a move pair, and resume an active session after reload or navigation. Levels are not human Elo estimates.
- Reveal a three-step hint ladder: a question and square, an idea and piece, then sound candidate moves. Tactical explanations use engine lines; positional principles describe local board facts.
- Import a player-owned PGN with your playing colour and request analysis. Replay every move with accuracy, coverage, a White-perspective probability graph, up to three trusted key moments and shared-budget lesson retries.
- Study opening-family missions and famous games through Library.

### Personalized training

- Persist player-attributed positions, analyses, evidence, diagnoses, lesson opportunities, review schedules, and attempts.
- Compose Daily Training from active mastery, due review, verified attempt history, fatigue, and an exploration floor.
- Verify moves on the server before advancing the matching review schedule and active skill.
- Present transfer exercises after the implemented mastery threshold, then report their results separately from ordinary lesson attempts.
- Run opt-in coach mode: a diagnosed practice move can return a durable interruption only after its lesson opportunity, review schedule, and study session exist.
- Import a Lichess CC0 puzzle catalog explicitly, then solve resumable multi-move puzzles. Stable daily sets, a separate puzzle rating, theme practice, an untimed solve streak and owned-mistake retries are implemented.
- Inspect evidence-linked recurring diagnoses, set a next-day qualifying-solve goal, and track UTC calendar streaks and pattern practice targets. Lesson hints remain app-reported; these records do not establish independent solving or measured improvement.
- Use Today for daily actions and evidence, onboarding for an overridable starting-level suggestion, and Profile for named skills, puzzle-rating history and separate bot-result cohorts.

### Operational and privacy boundaries

- Interactive play and batch analysis use separate Stockfish pools.
- Analysis beyond a player’s daily admission quota is queued fairly instead of silently discarded.
- Player-reachable game, play-session, position, analysis-job, and attempt paths are authorized by bearer-token ownership.
- Missing, malformed, ownerless, and non-owned resources use the same not-found surface.
- Player-scoped export, import, and deletion cover learning, analysis, and study records.
- Run the built UI and API on one port with `scan64-cli serve`, build a local Docker image, or use the unsigned Apple silicon macOS app with bundled Python and Stockfish. No hosted service, published container image or signed/notarized app is provided.

## Architecture boundaries

| Boundary | Responsibility |
| --- | --- |
| React web application | Renders Today, onboarding, play, review, training, puzzles, profile, Library and linked-student coach screens. |
| FastAPI application | Owns HTTP contracts, identity checks, lifecycle operations, and application orchestration; optionally serves the built web client. |
| SQLite and migrations | Persist player, game, analysis, lesson, review, puzzle, onboarding, goal, streak and pattern-target records locally. |
| Stockfish | Supplies opponent moves and authoritative analysis. |
| LessonSpec | Carries a renderer-independent lesson from the learning engine to a board client. |
| Optional providers | Maia supplies an operator-provisioned human-like opponent; an optional LLM can verbalize already-verified analysis. |

The generated API documentation at `/docs` on the running application's origin is the authoritative endpoint reference (by default `http://127.0.0.1:8001/docs`).

## Evidence and limits

The local quality gate covers backend tests excluding operator-provisioned real-model tests, strict typing, linting, recorded diagnosis precision, license checks, the production frontend build, browser unit tests and Playwright tests. See [contributing](../CONTRIBUTING.md) for the command and the separate Maia gate.

Passing those checks does not prove that Scan64 improves chess performance. The following are outside the current delivered capability:

- Calibrated behavioural-habit detection and context-conditioned profiling.
- Certified accessibility or universal assistive-technology compatibility. Keyboard UCI/canonical English SAN, readable positions/legal choices and replay/hint/practice controls are implemented alongside pointer boards. WCAG 2.2 AA is the all-route target; human Safari/VoiceOver acceptance is pending owner observation, not an observed pass. See [keyboard coverage and limits](user-guide.md#keyboard-touch-and-screen-readers).
- Hosted deployment and PostgreSQL production verification.
- Configurable retention or compaction for persisted engine payloads.

## Read next

- [User guide](user-guide.md)
- [System design](system-design.md)
- [Maia operator provisioning](maia.md)
- [Changelog](../CHANGELOG.md)
