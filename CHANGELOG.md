# Changelog

All notable changes to Scan64 are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version in `pyproject.toml` is authoritative. Published release notes are moved from `[Unreleased]` to their matching versioned section only when an authorized publication creates the corresponding annotated tag.

## [Unreleased]

### Added

- Measured learner-state composition with adaptive session priority, fatigue, and guaranteed exploration.
- Durable application navigation for owned games, reload-safe play-session resumption, and directly linkable analysis boards.
- Analysis boards render persisted per-position engine evaluations, mark diagnoses at their durable source position, offer analysis for unanalysed owned games, and state explicitly when a completed analysis found nothing.
- Lifespan-managed Stockfish pools with separate interactive and batch capacity, plus fair per-player analysis-job admission.
- Player-scoped export, import, and deletion for persisted learning, analysis, and study data.
- Transfer-position catalog and verified transfer-exercise measurement on mastery completion.
- Configurable database location through `SCAN64_DATABASE_URL`, with the existing database path retained by default.
- Clean-clone startup guidance and pointer-driven end-to-end coverage for play, lessons, and analysis.
- Explicitly opted-in coach-mode interruptions with server-persisted opportunities, review schedules, study sessions, and verified answers.

### Changed

- Lesson answers are confirmed against their objective by engine or tablebase; previously persisted lessons are re-verified on read and excluded from sessions when invalid, while retaining their provenance and failure reason.

### Security

- Authorize every player-reachable play-session, game, game-collection, position, and analysis-job read or write against its owner, returning an undisclosing not-found result for missing, malformed, ownerless, and non-owned resources.

## [0.1.0] - 2026-07-28

### Added

- Persisted, player-attributed analysis artifacts, including positions, engine evaluations, evidence, generated lesson opportunities, and automatic analysis of completed games.
- Production detector orchestration with focused engine analysis, evidence-grounded explanations, profile mastery updates, and scheduled review.
- Player reports based on persisted diagnoses and games, including recurring patterns, opening-family outcomes, and active-mastery snapshots.
- Interactive Chessground lesson boards for Daily Training, critical-moment review, and Opening Explorer missions.
- Durable study sessions and lesson attempts, with server-verified answers advancing the exact owned review schedule and active skill state.

### Security

- Require a valid player bearer token before serving or recording training and game-learning attempts.
- Preserve lesson-attempt failure records while preventing cross-player session and opportunity access.
