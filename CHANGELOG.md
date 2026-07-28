# Changelog

All notable changes to Scan64 are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version in `pyproject.toml` is authoritative. Published release notes are moved from `[Unreleased]` to their matching versioned section only when an authorized publication creates the corresponding annotated tag.

## [Unreleased]

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
