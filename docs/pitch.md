# Scan64 — Project Brief

Scan64 is an open-source, local-first chess practice and learning application. It converts player-owned games into evidence-backed, verified training rather than stopping at an engine evaluation.

## The product

The implemented learning loop is:

```text
game -> Stockfish analysis -> persisted evidence -> diagnosis -> owned lesson -> verified answer -> profile update
```

The web application includes Today and first-run orientation; eight relative Stockfish levels with colour choice, take-back and three-step hints; PGN import and per-move game review with accuracy, coverage, key moments and mistake retries; Daily Training; imported puzzles with a separate rating, stable daily sets, theme practice and solve streaks; evidence-linked patterns, UTC daily goals and tracked practice targets; opening exploration, famous-game study, a player profile, transfer measurement and opt-in coach-mode interruptions.

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

Run locally with the [README quickstart](../README.md): a one-port source application, a locally built Docker image, or the unsigned Apple silicon macOS app. No hosted service, published container image or signed/notarized app is provided. Follow the [user guide](user-guide.md) to complete the play-to-training workflow. The local quality gate is:

```sh
scripts/check.sh
```

For the architecture and optional-provider boundaries, read the [system design](system-design.md) and [Maia operator provisioning guide](maia.md).
