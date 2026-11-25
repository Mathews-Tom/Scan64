# Scan64 — Project Brief

Scan64 is an open-source, local-first chess practice and learning application. It converts player-owned games into evidence-backed, verified training rather than stopping at an engine evaluation.

## The product

The implemented learning loop is:

```text
game -> Stockfish analysis -> persisted evidence -> diagnosis -> owned lesson -> verified answer -> profile update
```

The unified desktop app runs the same React client on macOS Apple silicon, Windows x64 and Linux x64. It includes Today and first-run orientation; eight relative Stockfish levels with colour choice, take-back and three-step hints; PGN import and per-move game review with accuracy, coverage, key moments and mistake retries; Daily Training; imported puzzles with a separate rating, stable daily sets, theme practice and solve streaks; evidence-linked patterns, UTC daily goals and tracked practice targets; opening exploration, famous-game study, a player profile, transfer measurement and opt-in coach-mode interruptions. Light, dark and system appearance share one board-centred interface.

The learning engine publishes renderer-independent `LessonSpec` objects. The React board is one consumer of that contract rather than a private shortcut around it.

## What differentiates it

- A diagnosis is tied to persisted evidence and its source position, not only a centipawn-loss label.
- A lesson is owned by a player and served with durable opportunity, review, and study-session context.
- A submitted move is verified server-side before Scan64 changes mastery or review state.
- Game history, analysis, and attempts enforce bearer-token ownership boundaries.
- Interactive play and background analysis use separate Stockfish capacity.
- Puzzle continuations are source-verified rather than engine diagnoses, and puzzle rating remains separate from descriptive results against bot levels.
- Daily goals and pattern targets retain their supporting evidence and coverage limits. Lesson hint usage is app-reported, not proof of independent solving.

## Current limits

Scan64 has a functioning transfer-measurement mechanism. It does not yet establish that its training improves chess performance in real learners.

The product also does not currently claim calibrated behavioural-habit detection, context-conditioned profiling, hosted deployment, PostgreSQL production verification, or configurable engine-payload retention.

Keyboard move entry (strict UCI or canonical English SAN), readable chess positions, legal choices and keyboard replay/hints/practice accompany the pointer boards. WCAG 2.2 AA is the all-route design target, not a certification or universal screen-reader claim. Human Safari/VoiceOver acceptance remains pending owner observation; see the [user guide](user-guide.md#keyboard-touch-and-screen-readers) for coverage and limits.

## Use and evaluate it

Download the [latest published installers](https://github.com/Mathews-Tom/Scan64/releases/latest): macOS `.dmg`, Windows `-setup.exe`, or Linux `.AppImage` / `.deb`. The desktop app opens one window and bundles Python and Stockfish; no developer tools or separate engine installation are needed. It offers puzzle download/import with progress, signature-checked updates on macOS, Windows and AppImage, and an explicit **Restart to update** action. Debian packages link to the release download instead of self-updating.

Follow the [README install steps and client demos](../README.md#get-started), or run the one-port source application or a locally built Docker image. Installers are not operating-system signed; macOS builds are ad-hoc signed and not notarized. Version 0.6.1 and earlier cannot update to the unified desktop app automatically, and their data is not migrated. No hosted service or published container image is provided. The [user guide](user-guide.md) covers the play-to-training workflow, OS warnings, updates and local data. The local quality gate is:

```sh
scripts/check.sh
```

For the architecture and optional-provider boundaries, read the [system design](system-design.md) and [Maia operator provisioning guide](maia.md).
