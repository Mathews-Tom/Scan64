# Changelog

All notable changes to Scan64 are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version in `pyproject.toml` is authoritative. Versioned sections are prepared in release pull requests; publication requires a separately authorized annotated tag and GitHub Release.

## [Unreleased]

### Added

- **Settings → Send feedback** drafts a bug report, idea or question as a GitHub issue. You see the exact text first; the optional version line names only the Scan64 version, OS family and desktop or browser. Scan64 opens GitHub's prefilled form in your browser and sends nothing itself; no games, player data or tokens are attached.

### Changed

- The README now explains why Scan64's macOS and Windows installers are unsigned and how to check a download's SHA-256 digest against the release. It shows how to open the app once on macOS (**Open Anyway**) and Windows (SmartScreen **Run anyway** or **Unblock**), and that the only way past Smart App Control is turning it off for the whole PC.
- The sidebar's expand and collapse control is now a chevron icon button in both states instead of a full-width row labelled **Collapse**. Its accessible name and tooltip still read **Expand sidebar** or **Collapse sidebar**.

## [0.12.2] - 2026-11-23

### Changed

- Importing games now belongs to **Review**: **Review → Import games** and the empty Review screen lead to importing your public Lichess or Chess.com games by username, and the import screen keeps Review highlighted. Library's link is renamed **Import games** because it covers username import as well as PGN paste.

### Fixed

- Repeated analysis requests for the same game now wait their turn without holding analysis slots, so other games keep being analysed.

## [0.12.1] - 2026-11-23

### Fixed

- Restore older account exports, including v0.9.0, that contain habit credit from game-derived lessons whose evidence omitted the pre-move position. Restore still rejects evidence that conflicts with the saved game or engine analysis.
- Importing more than 15 online games at once no longer freezes the app for about 30 seconds per extra game or leaves those games unanalysed until restart. Analysis now runs at most four games at a time and queues the rest.
- With the sidebar expanded at the default 1200-pixel desktop window width, scrollable screens such as Today and Import no longer squeeze the navigation labels into the narrow sidebar column.

## [0.12.0] - 2026-11-22

### Added

- **Opening Explorer → My repertoires** imports standard-chess PGNs with variations. Browse saved lines, drill decisions for either side to move on a spaced review schedule, and see how many positions you have reviewed and what is due.
- **Compare imported games** ranks the first departures from a saved repertoire by frequency, identifies whose move departed, and opens a drill on the preceding saved position. Equivalent positions reached by a different move order do not count as departures.
- **Profile → Download coach packet** saves a local ZIP containing a pattern summary and annotated PGN evidence for a human coach. The summary keeps sample sizes, coverage and caveats; it is not proof that training caused improvement.
- **Settings → Local explanation wording** can opt into an installed Ollama model for new analysis and Coach explanations. Scan64 checks generated claims against verified evidence and uses the built-in wording when disabled, unavailable or rejected. Saved explanations do not change.

### Changed

- Keep a pre-upgrade database backup and player export. Saved repertoire data or local wording consent can prevent a schema downgrade; use the matching pre-upgrade backup to roll back without deleting rows from the live database. See [backup and rollback guidance](docs/user-guide.md#back-up-before-upgrading-or-rolling-back).

### Security

- Local wording is off by default. Detection sends no chess evidence. Enabling it sends evidence only to the local Ollama service, which can log or forward what it receives. The choice is scoped to the player and installation and is not transferred through an account archive.

## [0.11.0] - 2026-11-21

### Added

- **Safe or blunder** in Train lets you choose between the move you played and a verified sound alternative from your own analysed game. Both drill modes share the same three-attempt budget.
- **Convert the win** opens eligible positions from your reviews as separate practice games. Finish or resume the attempt without changing the original game; completed results schedule a review and do not count as ordinary full-game strength results.
- In unrated Play, **Candidates** grades up to three legal proposals before you choose a move. An opt-in blunder guard warns before committing a costly move and leaves the choice to you. Assisted results are kept out of independent strength estimates.
- Play offers server-owned Fischer clocks for 1+0, 3+2, 5+0 and 10+5. Profile separates results by exact time control. Untimed Play remains the default; timed games do not support takebacks or offline move queuing.

### Changed

- FSRS schedules lesson, transfer and eligible puzzle reviews. Existing due dates, retirement and last-review times are preserved on upgrade; missing past grades are not invented.
- Screens load on first visit. Visited browser screens can reopen offline after their code is cached. PGN analysis, desktop puzzle imports and desktop updater status push progress instead of polling; interrupted puzzle progress can reconnect without restarting or cancelling the import again.
- Back up the database and export player data before upgrading. Retained review, conversion, assistance or timing state can prevent downgrading; use the matching pre-upgrade backup for rollback. See [back up before upgrading or rolling back](docs/user-guide.md#back-up-before-upgrading-or-rolling-back).

### Fixed

- Restore player archives that contain valid lesson evidence from the position after a played move, preserving earned credit and review history without replaying rewards.

## [0.10.0] - 2026-11-19

### Added

- Profile accuracy breakdowns by game phase, playing colour and opening family, with scored/eligible move counts. Matched real-time PGN clocks show the share of owner moves with at most 30 seconds remaining; missing scores or clocks stay unavailable.
- Review highlights up to three good moments, including in imported games. Selecting one shows the position after the move; these highlights do not award skill credit.
- Profile compares trusted-pattern recurrence in imported online games before and after the first completed, server-verified mapped training attempt, including incorrect attempts. Each side requires three fully analysed games with at least 20 owner moves each; counts, coverage and 95% Wilson intervals are shown. Actual PGN play times determine the periods, and reset clears the training anchor without deleting games. This descriptive comparison is not proof that training caused improvement.
- Knight forks enter trusted recurring patterns, lessons and related weak-skill puzzle selection after passing the preregistered precision/support gate. Measurement uses an engine-derived operational reference, not independent human-coach validation.

### Changed

- Batch analysis reuses saved engine evaluations only for the same owner, full position, engine build, effective options and exact search limits. Reused results create fresh occurrence records without changing source-game provenance or diagnosis budgets.
- Pin, overloaded-defender, skewer, discovered-attack and back-rank diagnoses remain hidden because their fresh reports did not meet the unchanged promotion gate. The frozen skewer/discovery/back-rank report retains a documented automatic-75-move-draw continuation limitation; its correction requires a future separately preregistered revision, not rescoring or another sample in this release.

### Fixed

- Recorded diagnosis replay validates exact input bytes, source identities, legal moves and supported saved evidence, failing closed on changed or malformed artifacts while preserving historical reports.

## [0.9.0] - 2026-11-18

### Added

- Import public standard-chess games from Lichess or Chess.com by username, preserving available clocks and skipping duplicates. Imported games enter your owned game library and analysis queue.
- Review → Analysis Board shows up to three legal Stockfish lines with contextual move notation and White-perspective evaluations. Select a line's move to play its prefix, retain alternative branches, and download a PGN containing those variations, including custom starting positions.
- Premoves during play, right-drag arrows and right-click circles, a **Flip board** control that reverses pieces and coordinates, plus synthesized move, capture, check and game-end sounds.

### Changed

- Fast and focused game analysis use bounded parallel Stockfish searches while preserving interactive play capacity.
- SQLite uses write-ahead logging, bounded lock waits and indexed puzzle-theme queries. Puzzle CSV imports, desktop library staging and archive restores update the theme index in the same transaction as the puzzles.

### Fixed

- Keep the next puzzle active when an explanation for the previous puzzle finishes at the same time, rather than restoring the previous round.

## [0.8.0] - 2026-11-16

### Added

- Six selectable chess-piece sets and five board palettes, saved independently of the app's light/dark appearance. Artwork attribution and licence restrictions are available in Settings; California, Maestro and Staunty are non-commercial only.
- Puzzle review with unlimited unscored retries after the three-attempt scored round. Review does not record another rating, streak or mastery result.
- On-demand, engine-verified move explanations with board annotations and step-through replies. Different source-line alternatives are not automatically labelled mistakes; explanations during an active scored round count as assistance.
- Settings can reset all learning progress after explicit typed confirmation, preserving the account, saved games and analyses, imported libraries and settings.

### Fixed

- Solved puzzles show a prominent result beside the final board.
- Legal moves outside a puzzle's source line remain visible for inspection until **Try again**, rather than immediately snapping back.
- Restore the native desktop title bar so the application window can be dragged normally.
- Keep session-only board choices when navigating away from Settings, and discard stale puzzle reviews when another tab advances the position.
- Keep learning requests responsive during database lock waits, and avoid offering unrecordable coach lessons from games predating a progress reset.

## [0.7.1] - 2026-11-16

### Changed

- Desktop windows stop at 960 logical pixels wide and keep navigation on the left, including at increased zoom. Expand/Collapse reveals labels; compact windows use a temporary navigation drawer, and wide windows remember the sidebar preference.
- Screens share bounded board/control columns, aligned overview grids and readable form widths. Puzzles shows full-width library setup when empty, a real board only during practice, compact progress summaries and expandable practice rules.

### Fixed

- Compact navigation skips hidden updater controls when wrapping keyboard focus.
- Puzzle Retry repeats a failed mode start instead of reloading an older completed run.
- Desktop game reviews keep long move lists scrollable beside the board.

## [0.7.0] - 2026-11-15

### Added

- Scan64 is now one desktop app for macOS (Apple silicon), Windows (x64) and Linux (x64). It opens a single window and needs neither Python nor a separately installed Stockfish, because both the server and Stockfish 18 are bundled. Installers: `.dmg` (macOS), `-setup.exe` (Windows, per-user), and `.AppImage` or `.deb` (Linux).
- In-app updates on macOS, Windows and the Linux AppImage. Scan64 checks 10 seconds after launch and then every 24 hours. Settings → Updates and the sidebar show progress. You can turn automatic downloads off, and an update installs only when you click **Restart to update**. Every update is signature-checked; a tampered download is rejected and the app stays as it was. The Linux `.deb`, read-only install locations and macOS App Translocation explain why updates are unavailable and link to the download page.
- Before a new version upgrades the database, Scan64 copies it into `backups/` in the app's data folder and keeps the newest three copies.
- Puzzles can be downloaded from database.lichess.org, or imported from a local `.csv.zst` file, inside the app from Puzzles or Settings → Puzzle library; progress stays visible in the sidebar. The import shows progress, speed and time left, can be cancelled, and removes downloaded files when it finishes.
- `scan64-cli puzzles import --download` shows download and import progress on stderr (`--quiet` turns it off).
- A new look across every screen: Scan64 identity, light, dark and system appearance in Settings, bundled fonts, and a board-centred layout for Play, Review, Puzzles, Daily Training, Famous Games and Explorer.

### Changed

- The app keeps its data in a new folder, `org.scan64.desktop`: under `~/Library/Application Support` on macOS, `%APPDATA%` on Windows and `${XDG_DATA_HOME:-~/.local/share}` on Linux. Data from 0.6.1 and earlier is not migrated.
- Release assets are built and verified by one workflow for all three systems. The update bundles (`Scan64.app.tar.gz`, `-setup.exe`, `.AppImage`) carry a signature that is checked against the public key compiled into the app. Installers are not code-signed: macOS builds are ad-hoc signed and not notarized, and Windows builds are not Authenticode-signed.

### Removed

- The old macOS app from 0.4.0–0.6.1 (a status window that opened Scan64 in the browser), its Python updater and its `update.json` release files. 0.6.1 cannot update itself to 0.7.0: its update check reports a failure. Delete it, install 0.7.0 by hand and, once you no longer need the old data, remove it with `rm -rf ~/Library/Application\ Support/Scan64`.

### Security

- Update signatures must also authenticate the announced version, so an older signed build cannot be served as a newer one. Downgrades and insecure update transport are rejected, and release builds refuse any non-canonical updater setting.
- In the desktop app, the bundled local server rejects API calls that lack the shell token, which the app generates anew on every launch. Any local process that obtains that token can still call the API.

## [0.6.1] - 2026-11-14

### Changed

- The macOS app retries an update check interrupted by a network error after a few seconds, then again within the hour if the connection is still failing, instead of waiting a day. Rejected updates (bad signature, checksum or version) are not retried.

### Fixed

- **Missed capture** is no longer reported when the player actually made the recommended capture. A pre-registered re-test on fresh real games kept the diagnosis trusted.

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
