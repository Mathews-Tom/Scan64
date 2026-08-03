# Scan64 — Project Brief

Scan64 is an open-source, local-first chess practice and learning application. It converts player-owned games into evidence-backed, verified training rather than stopping at an engine evaluation.

## The product

Most chess software can identify a bad move. Scan64 implements a different loop:

```text
game -> Stockfish analysis -> persisted evidence -> diagnosis -> owned lesson -> verified answer -> profile update
```

The web application includes computer play, PGN import, game history, analysis boards, Daily Training, opening exploration, famous-game study, a player profile, transfer measurement, and opt-in coach-mode interruptions.

The learning engine publishes renderer-independent `LessonSpec` objects. The React board is one consumer of that contract rather than a private shortcut around it.

## What differentiates it

- A diagnosis is tied to persisted evidence and its source position, not only a centipawn-loss label.
- A lesson is owned by a player and served with durable opportunity, review, and study-session context.
- A submitted move is verified server-side before Scan64 changes mastery or review state.
- Game history, analysis, and attempts enforce bearer-token ownership boundaries.
- Interactive play and background analysis use separate Stockfish capacity.

## Current limits

Scan64 has a functioning transfer-measurement mechanism. It does not yet establish that its training improves chess performance in real learners.

The product also does not currently claim calibrated behavioural-habit detection, context-conditioned profiling, keyboard board interaction, hosted deployment, PostgreSQL production verification, or configurable engine-payload retention.

## Use and evaluate it

Run locally with the [README quickstart](../README.md#run-locally). Follow the [user guide](user-guide.md) to complete the play-to-training workflow. The local quality gate is:

```sh
scripts/check.sh
```

For the architecture and optional-provider boundaries, read the [system design](system-design.md) and [Maia operator provisioning guide](maia.md).
