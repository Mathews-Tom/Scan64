# Scan64 — System Overview

Scan64 is a local-first chess practice and learning application. It gives a player one owned path from a game to a verified training result:

```text
play or import -> analyse -> persist evidence -> diagnose -> serve lesson -> verify answer -> update mastery and review
```

The application is currently released as `0.2.0`. Its release record is [CHANGELOG.md](../CHANGELOG.md).

## What works today

### Play, import, and review

- Play a computer game through the web client and resume an active session after navigation.
- Import a player-owned PGN, request analysis, and open the game through its history or analysis deep link.
- Read persisted engine evaluations and diagnosis markers at their durable source positions.
- Study opening-family missions and famous games.

### Personalized training

- Persist player-attributed positions, analyses, evidence, diagnoses, lesson opportunities, review schedules, and attempts.
- Compose Daily Training from active mastery, due review, verified attempt history, fatigue, and an exploration floor.
- Verify moves on the server before advancing the matching review schedule and active skill.
- Present transfer exercises after the implemented mastery threshold, then report their results separately from ordinary lesson attempts.
- Run opt-in coach mode: a diagnosed practice move can return a durable interruption only after its lesson opportunity, review schedule, and study session exist.

### Operational and privacy boundaries

- Interactive play and batch analysis use separate Stockfish pools.
- Analysis beyond a player’s daily admission quota is queued fairly instead of silently discarded.
- Player-reachable game, play-session, position, analysis-job, and attempt paths are authorized by bearer-token ownership.
- Missing, malformed, ownerless, and non-owned resources use the same not-found surface.
- Player-scoped export, import, and deletion cover learning, analysis, and study records.

## Architecture boundaries

| Boundary | Responsibility |
| --- | --- |
| React web application | Renders play, history, analysis, training, profile, content, and coach screens. |
| FastAPI application | Owns HTTP contracts, identity checks, lifecycle operations, and application orchestration. |
| SQLite and migrations | Persist player, game, analysis, lesson, review, attempt, and transfer records locally. |
| Stockfish | Supplies opponent moves and authoritative analysis. |
| LessonSpec | Carries a renderer-independent lesson from the learning engine to a board client. |
| Optional providers | Maia supplies an operator-provisioned human-like opponent; an optional LLM can verbalize already-verified analysis. |

The generated API documentation at `http://127.0.0.1:8001/docs` is the authoritative endpoint reference while the API is running.

## Evidence and limits

The local quality gate covers backend tests, strict typing, linting, license checks, production frontend build, browser unit tests, and pointer-driven Playwright tests. A live audit also completed the production learning loop through Stockfish and a real browser board.

That evidence proves the application path works; it does not prove that Scan64 improves chess performance. The following are outside the current delivered capability:

- Calibrated behavioural-habit detection and context-conditioned profiling.
- Keyboard board interaction and complete accessibility coverage.
- Hosted deployment and PostgreSQL production verification.
- Configurable retention or compaction for persisted engine payloads.

## Read next

- [User guide](user-guide.md)
- [System design](system-design.md)
- [Maia operator provisioning](maia.md)
- [Changelog](../CHANGELOG.md)
