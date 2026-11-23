# Scan64 — System Overview

Scan64 is a local-first chess practice and learning application. It gives a player one owned path from a game to a verified training result:

```text
play or import -> analyse -> persist evidence -> diagnose -> serve lesson -> verify answer -> update mastery and review
```

The application release prepared here is `0.12.2`. See the [changelog](../CHANGELOG.md) and [latest published release](https://github.com/Mathews-Tom/Scan64/releases/latest).

## What works today

### Play, import, and review

- Play Stockfish at eight relative bot levels, choose White, Black or Random, promote, resign, and resume an active session after reload or navigation. Untimed games allow pair takebacks; timed games use server-owned clocks and keep separate strength cohorts. Bot levels are not human Elo estimates.
- Reveal a three-step hint ladder: a question and square, an idea and piece, then sound candidate moves. Tactical explanations use engine lines; positional principles describe local board facts.
- In unrated Play, grade up to three candidate moves or turn on the blunder guard. Assisted results and conversion practice do not count as independent full-game strength results.
- Import a player-owned PGN with your playing colour or public standard-chess games by Lichess/Chess.com username. Replay every move with accuracy, coverage, a White-perspective probability graph, up to three trusted key moments, separate display-only good moments and shared-budget lesson retries.
- Study opening-family missions and famous games through Library.
- In Opening Explorer, import your own standard-chess repertoire PGN (up to 256 KiB, 16 records, fewer than 4096 moves, 256 plies per line and 32 nested variations). Every record retains a separate root and all alternatives. Browse saved variations and practise a saved decision for the side to move with an interactive board or keyboard.
- The same 256 KiB cap applies to each normalized stored record, so oversized normalization is rejected before any write; empty titles become “Repertoire”. FEN counters are bounded to 1,000,000 and stored node FENs to 120 characters. Incoming repertoire JSON is bounded while streaming (including chunked bodies), allowing worst-case JSON escaping; this does not impose a new total limit on legacy account archives.
- Repertoire coverage counts saved positions with continuations reviewed at least once, including Again; leaf positions are excluded. Due is separate and includes unreviewed positions. A legal move outside the saved continuations receives Again, while illegal or malformed moves do not create a review. One reusable FSRS schedule belongs to each exact repertoire/node decision, without ratings, awards, diagnosis or other learning effects.
- Opening Explorer refreshes due decisions at the next scheduled deadline and when its tab becomes visible again. Retry the same submission after an uncertain response; a definitive rejection unlocks practice and refreshes the saved list.
- Select a saved repertoire and choose **Compare imported games** to rank first departures by frequency. The report names whose move left the saved line and opens a drill on the preceding decision, not on the departure move. Equivalent positions reached by another move order do not introduce a departure. Games without a matched decision are counted separately; play-linked games are excluded because continuing in Play can change the imported game and its assigned side.

### Personalized training

- Persist player-attributed positions, analyses, evidence, diagnoses, lesson opportunities, review schedules, and attempts.
- Compose Daily Training from active mastery, due review, verified attempt history, fatigue, and an exploration floor.
- Verify moves on the server before advancing the matching review schedule and active skill.
- Schedule lesson, transfer and eligible puzzle reviews with FSRS. Use Safe or blunder for supported evidence from your own games, and start separate conversion practice from eligible review positions.
- Present transfer exercises after the implemented mastery threshold, then report their results separately from ordinary lesson attempts.
- Run opt-in coach mode: a diagnosed practice move can return a durable interruption only after its lesson opportunity, review schedule, and study session exist.
- Import a Lichess CC0 puzzle catalog explicitly, then solve resumable multi-move puzzles. Stable daily sets, a separate puzzle rating, theme practice, an untimed solve streak and owned-mistake retries are implemented.
- Inspect evidence-linked recurring diagnoses, set a next-day qualifying-solve goal, and track UTC calendar streaks and pattern practice targets. Lesson hints remain app-reported; these records do not establish independent solving or measured improvement.
- Use Today for daily actions and evidence, onboarding for an overridable starting-level suggestion, and Profile for named skills, puzzle-rating history and separate bot-result cohorts.
- Read Profile accuracy by phase, colour and opening, plus clock-pressure coverage where clocks exist. Compare trusted-pattern recurrence before and after first verified training in imported games, with game/move counts, intervals and explicit insufficient-data states; the comparison does not establish a training effect.
- Download a local coach packet from Profile: a pattern summary and annotated PGN of the same trusted evidence. Scan64 does not upload the packet.

### Operational and privacy boundaries

- Interactive play and batch analysis use separate Stockfish pools.
- Analysis beyond a player’s daily admission quota is queued fairly instead of silently discarded.
- Player-reachable game, play-session, position, analysis-job, and attempt paths are authorized by bearer-token ownership.
- Missing, malformed, ownerless, and non-owned resources use the same not-found surface.
- Player-scoped export, import, and deletion cover learning, analysis, and study records.
- Optional local wording is off by default. A player can authorise a fixed loopback Ollama service to reword new explanations; the consent is local to that installation and does not travel in account archives.
- Account archives retain repertoire IDs, nodes, attempts and schedules; restored attempts and observations are historical, not newly verified evidence. Reset Progress clears repertoire practice and schedules but keeps imported trees. Delete Data removes both.
- Repertoire and account export/import/delete/reset routes bypass generic HTTP response caching and always perform their own authorization. Drill retries use the persisted, payload-bound attempt ID; repeating an import intentionally creates independent content. Repertoire archive records are individually bounded before model expansion/replay. Creation/review timestamps must be ISO dates from 1970 to before 2100, no more than five minutes ahead of the restoring clock; due dates must be before 2200. Accepted prior review times anchor later decision timestamps during clock rollback, preserving replay order.
- These guards use decoded, route-relative paths for mounted deployments and URL-delimiter-bearing account IDs. Confirmed deletion and repertoire reviews share the player write fence and freshly authorize after waiting; deletion previews remain read-only. Restored timestamps preserve UTC instants, including mixed-offset archives. PGN imports preserve same-line setup tags and reject dropped or duplicate tags, conflicting Result metadata, and blank record separators inside a variation before any write.
- The additive repertoire migration refuses downgrade while imported content, attempts or repertoire schedule history remain. Before upgrade, keep the desktop pre-migration backup and an account export. Recovery means copying that backup to a separate disposable data directory and opening it with the matching older version, not deleting live rows to force downgrade; keep the current database/export intact. The migration tests exercise upgrade/downgrade/re-upgrade on an unused copy, refusal with imported/practised data, and recovery of the unchanged pre-migration backup copy.
- Run the built UI and API on one port with `scan64-cli serve`, build a local Docker image, or install the desktop app: one window, bundled Python/Stockfish, in-app puzzle import and signed in-app updates, with installers for macOS Apple silicon, Windows x64 and Linux x64 attached to each release. No hosted service, published container image or Developer ID/Authenticode-signed app is provided.

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
