# Changelog

All notable changes to Scan64 are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version in `pyproject.toml` is authoritative. Versioned sections are prepared in release pull requests; publication requires a separately authorized annotated tag and GitHub Release.

## [Unreleased]

## [0.6.0] - 2026-11-14

### Added

- Moves that allow a forced checkmate are diagnosed as **Allowed checkmate**, with the engine's mating line and a lesson to find a move that does not allow mate. The diagnosis became trusted only after a pre-registered test on fresh real games.
- An evaluation report comparing Scan64's diagnoses with Lichess puzzle themes, reproducible offline.

### Changed

- Moves that allow checkmate are no longer described as a hanging piece or a missed direct threat.
- Game analysis and coach interruptions skip a lesson whose accepted answers would include the move being diagnosed as the mistake.

### Fixed

- `scan64-cli analyse` reports a rejected candidate and continues when one source search fails, instead of stopping the run.

## [0.5.1] - 2026-11-14

### Fixed

- The macOS app's updater can read GitHub release information. In 0.5.0 every check failed with "Update failed", so 0.5.0 users must install 0.5.1 manually once.
- Release builds of the macOS app no longer fail on GitHub's anonymous API rate limit.

## [0.5.0] - 2026-11-14

### Added

- In-app updates for the macOS app: an optional daily background check and a **Check for updates** button, Ed25519-signed release verification, and **Restart to update**, which backs up the database, keeps the previous app and rolls back on failure. The browser shows a notice when an update is ready.
- **Import puzzles…** in the macOS app window, from a downloaded Lichess puzzle file or an explicit full download, with progress and cancellation.
- Release builds of the macOS app are produced and signed by GitHub Actions and attached to each release with a signed update manifest.

### Changed

- The browser tab, installed web app and macOS app use the Scan64 logo.
- Today names the latest game by its players and date instead of an internal identifier.
- The README now has a banner, demos and screenshots, and the user, system and contributor guides describe the current app.

### Upgrade notes

- Version 0.4.0 has no updater: install 0.5.0 manually once. Later versions can be installed from the app.

## [0.4.0] - 2026-11-13

### Added

- A Scan64 app for Apple silicon Macs (unsigned and not notarized; ad-hoc signature only), with bundled Python and Stockfish 18, a status window with Open and Quit, single-instance launch, and data stored in `~/Library/Application Support/Scan64`.
- `scripts/build-macos-app.sh` to build the app from source, compiling Stockfish 18 from its pinned, hash-checked source and including its source, licence and networks.

## [0.3.0] - 2026-11-13

### Added

- A Today dashboard, guided first-run setup, level-based bot suggestions, and separate puzzle and bot-strength trends.
- Eight computer-opponent levels, player colour selection, take-back, promotion choices, resignation, and durable game results.
- One-port source and container operation, with built-in web serving and `scan64-cli serve`.
- Three-step position hints with tactical explanations and board-grounded positional principles.
- Owned per-move game reviews with accuracy, coverage, evaluation graphs, key moments, and mistake retries.
- Explicit Lichess puzzle import, resumable multi-move puzzle solving, and source-verified LessonSpec 0.2.0 support.
- Puzzle ratings, stable daily puzzle sets, theme practice, solve streaks, and owned mistake practice.
- Evidence-linked recurring patterns, verified daily goals, calendar streaks, and tracked-pattern practice targets.
- Keyboard move entry, readable positions and legal choices, keyboard replay, and narrow-screen board controls.
- Positive profile credit for engine-supported, unassisted tactics found in owned games.

### Changed

- Game imports require the player's colour; lessons and profile observations use only that side's moves.
- Game lessons focus on at most five distinct costly mistakes and accept all engine-discovered sound alternatives.
- Diagnoses teach one trusted cause, with precision measured against recorded real amateur games.
- Learning streaks distinguish server-tracked puzzle hints from app-reported lesson hints; neither represents measured chess improvement.

### Fixed

- Pending and running analyses recover after restart without duplicating completed learning records.
- Reconnected games remain locked until canonical synchronization completes, and completed sessions no longer reappear as playable.
- New games use explicit opponent levels instead of misleading strength labels.
- Castling hints avoid premature answer reveals; repeated position hints reuse bounded engine results with orderly shutdown.

### Security

- Famous-game attempts require the submitted player's bearer token before changing attempts or mastery.

## [0.2.1] - 2026-08-10

### Changed

- Clarified the source-only local distribution model and current product boundaries in the release documentation.

### Security

- Added a private GitHub Security Advisories reporting route for vulnerability disclosures.

## [0.2.0] - 2026-07-31

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
