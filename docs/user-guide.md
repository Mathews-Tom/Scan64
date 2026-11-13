# Using Scan64

Scan64 is a local-first chess practice and learning application. It records games you play or import, analyses them with Stockfish, turns diagnosed positions into owned lessons, and updates your profile from server-verified practice and supported tactical evidence.

## Start the application

Follow the [root quickstart](../README.md#run-locally). It starts the API at `http://127.0.0.1:8001` and the web application at `http://127.0.0.1:5173`.

On your first visit, enter a display name and select **Start learning**. Scan64 creates a player identity once; its bearer token stays in that browser's local storage. Use a separate browser profile for a separate player. Returning visits keep the stored identity.

**Today** offers Play, Train and Review actions, your latest game, the week's activity, weakest skills by name, and recurring patterns. The navigation groups game history under **Review**, daily lessons under **Train**, and Opening Explorer, Famous Games and Import PGN under **Library**. **Profile** shows named skills and evidence; **Coach** opens the existing linked-student dashboard. The shell follows your system's light or dark colour scheme.

## Choose a starting point

New registrations see **Your starting point** in Today and Profile. Existing players
are not automatically enrolled: choose **Set up my starting point** to opt in.
Declare Beginner, Developing, Club, Advanced or Not sure, then select **Save level
and show suggestion**. The server immediately suggests bot level **2, 3, 5, 6 or 1**
respectively (`declared-level-v1`); puzzles are not required to obtain it. These are
overridable starting choices, not measured strength, placement results or human Elo.

**Skip introduction** and **Continue later** keep your identity and any recorded
progress. Returning or reloading uses the same stored browser identity and server
record; no second registration is needed. A later declaration does not reset the
introduction's original start time or erase a manual bot choice.

After declaring, **Start/resume daily puzzles** opens the existing Puzzles screen:
resume the active run or use its Daily **Start** action. The introduction counts the
first five daily-slot-bound runs started after the server's introduction start time,
not a separate placement set. Pre-opt-in active runs and slots reserved before opting
in do not count. Each qualifying run closes once, whether solved (with or without hints),
failed or abandoned. The displayed count comes from server evidence, carries into later days,
and does not estimate playing strength. A short or empty daily catalog never blocks
**Play now**; practise another day rather than rerolling or manufacturing slots.

Play preselects your saved last manual bot choice, otherwise your declared-level
suggestion, otherwise level 1. Only explicitly changing **Bot level** saves a manual
choice. Suggestions never overwrite it and results never adjust the level
automatically. Analysis Board and Famous Games keep their level-1 defaults.

## Read your strength record

Profile separates your declared experience, starting bot suggestion, puzzle Elo
event history and Stockfish results. Coach shows the same separate values for
explicitly linked students, including provisional puzzle status; it does not show
the internal profile rating as a measured player rating.

The **puzzle** scale starts at **1200**, uses **K=32**, and is provisional until
**20 rated puzzles**. Events show before/after values and the recorded score. This
is not chess/FIDE/site Elo, a bot-level conversion, theme mastery or a demonstrated
improvement outcome. New skill states still use the provisional **(3, 2)** seed:
it is a prior, not observed ability. Measured skill evidence is preserved, as is the
internal **1500** profile rating; onboarding changes none of those values.

Bot result samples are separated by **Stockfish level and server-stamped policy
version**, not mixed into puzzle Elo. Each cohort shows all eligible wins/draws/losses,
latest-five and previous-five samples and their trend; fewer than five games in either
window means insufficient data. Ordering uses game creation time and id, not analysis
completion. Legacy tracked results use the separately disclosed `v1(unstamped)`
cohort; Maia is excluded. Seed, colour and arbitrary client config keys do not define
new cohorts.

Only owned, tracked, completed games with terminal results, a standard start, no
coach mode and **no recorded in-app hints** qualify. Imports, unknown assistance,
unfinished/unknown results and custom starts do not; resignations do. Analysis
credit status is not required. These filters do **not** certify absence of takebacks
or external help, and the windows describe results against that bot policy—not
human playing strength or causal improvement. Archives preserve onboarding and
existing history without replaying ratings; old archives without onboarding remain
unplaced and opt-in.


## Keyboard, touch and screen readers

- Use **Skip to content** to reach the main study area. Tab/Shift+Tab move between native controls; Enter/Space activate buttons. Navigation and browser Back/Forward move focus to the new study area and update the page title. Focus outlines follow the system light/dark theme.
- Boards have a readable position alternative listing square coordinates, piece colour/type, side to move, check/result and legal choices. It reflects the same position as the graphic, including selected review and preview positions; it does not disclose unrevealed hints, puzzle solutions or engine recommendations.
- Use the board's labelled move field for strict UCI (`e2e4`, `e1g1`, `e7e8n`) or canonical English SAN (`e4`, `Nf3`, `O-O`). UCI is trimmed and lowercased; SAN is trimmed but case-sensitive and must match the generated legal notation, including disambiguation, capture, check or mate suffixes. Ambiguous `Nd2` is rejected when `Nbd2` is required. Non-English SAN piece letters are not supported.
- Submit a legal move with Enter or the submit button. Illegal/ambiguous input announces an error without spending a server attempt. Promotion without a UCI piece suffix opens explicit queen/rook/bishop/knight choices; cancel without playing, then correct the entry. Input stays unavailable during opponent turns, pending requests, promotion and read-only replay/line previews. Pointer drag and source/destination taps remain available.
- Hints announce their revealed step. Candidate previews are study-only: return to the game position before moving. **Close hints** or Escape inside the hint panel clears the preview and restores the Hint control. Lesson hints and server feedback are announced separately from graphical annotations; arrows/highlights have coordinate-based text equivalents.
- In Review, use move buttons, replay controls or keyboard graph points (Enter/Space; Left/Right changes ply). The move list is the full-size touch/keyboard equivalent of the graph's small points; labelled bands and key moments do not rely on colour. **Retry this moment** opens a named region; **Close retry**, **Finish Review**, or Escape inside it returns focus to the opener (or review heading if attempts exhausted). Imported lesson review similarly returns to its Review button. Famous Game study keeps its board read-only; use its separate prediction field for UCI/SAN.
- These controls cover Play, Review/owned analysis, free Analysis, Daily Training, Puzzles, Opening Explorer and Famous Games. Today, Library, Import, Profile, Coach and onboarding use the same navigation, labels, focus and responsive shell. At narrow widths/zoom controls and text reflow rather than requiring horizontal scrolling in the document or main area; the two-dimensional board fits its container. Reduced-motion preference disables decorative animation/transitions.

**Acceptance limits:** WCAG 2.2 AA is the target across shipped routes, not a certification. The human macOS Safari/VoiceOver position, move, promotion, hint, review and puzzle pass is **pending owner observation**. Keyboard/role-name and emulated touch/reflow checks do not substitute for that pass or establish support for every screen reader, real iPhone or browser. Record actual OS/Safari/VoiceOver versions and results before treating assistive-technology acceptance as complete; no unavailable AT results are claimed.


## Play a game

1. Open **Play**.
2. Keep the player ID filled from your stored identity; no new name or ID is required.
3. Keep the preselected **Bot level**, or explicitly change it to save your manual preference, and choose **Play as** (White, Black or Random). As Black, the opponent makes the opening move before you play.
4. Select **Coach Mode** only for opt-in, post-move interruptions; start the game and use the labelled move field, source/destination taps or drag-and-drop. The SAN move list includes both sides' moves.
5. If a pawn reaches the last rank, choose **queen**, **rook**, **bishop** or **knight**; that exact promotion is played. Play, Daily Training and critical-moment lessons share this board.
6. Reload or return to **Play** to resume with your chosen colour and orientation from the server's latest state, including after browser Back or Forward. Offline moves stay queued until the connection returns. Moving, resigning and taking back stay disabled until the canonical game is loaded; if that refresh fails, use **Retry game refresh** before continuing. **Take back** undoes your last move and the bot's reply; it is unavailable in coach mode, after completion, while offline moves are queued, or before you have played a move pair. The bot's opening move is never removed.
7. Select **Resign**, or finish by checkmate or a draw. The result appears immediately. Once a game has moves, **Review game** links to its analysis, which Scan64 queues automatically on completion. Resigning before the first move creates no empty game and has no review link.

Play offers eight server-defined levels from `GET /v1/bots`; Analysis Board's play-from-here and Famous Games' continue action default to level 1. The API takes `opponent_config: {"bot_level": "1"}` for Stockfish and optional `player_color` (`white`, `black` or `random`). Omitted colour uses the position's side to move. All eight levels use the same session-seeded MultiPV candidate policy with decreasing error budgets. They are calibrated relative to each other, **not Elo-anchored**, and show no Elo figure. Stockfish's native reduced-strength move generator has no seed control, so it is not used for these levels. Maia's rating-based `strength` is unchanged. Stored older Stockfish sessions still resume using their original native skill setting.

A live opponent move and a background analysis use separate Stockfish pools. Analysis beyond the per-player daily admission quota remains queued; it is not silently discarded.

Run `uv run scan64-cli bots calibrate` to play 20 fixed-seed, node-limited games per adjacent level pair, with each opening from both colours. Live play and calibration share the default budget of 1,000 nodes per move. The command independently repeats every game and compares the full move records, then prints the Stockfish version and each higher level's score against the level below; it fails if a repeat differs or any score is below 60%. Repeat the command with the same Stockfish version for identical results. Calibration is a relative engine test, not a human-rating estimate.

### Hints during play

Select **Hint** beside the game board for a three-step ladder: first a highlighted square and a question; press again for the idea and the piece to consider; press a third time for up to three numbered sound moves and their arrows. Candidates label engine-line-backed reasons **Tactical** and local move facts **Principle**; **engine preference** means neither applies. Principles describe development, castling, centre occupation or added control, rook files, king escape squares against back-rank mate, or passed-pawn advances. They describe what a move does, not why it is best or a promise of winning. Only moves inside the engine tolerance receive reasons. The first two responses do not disclose moves or their destination squares.

Use **Preview candidate**, **Next step**, **Previous step**, or **Full line** to explore without submitting any moves. Select **Return to game position** before continuing play, or **Close hints** to clear the arrows. New game moves invalidate old hints. Hints require a connection and are unavailable while a move is pending, offline moves await synchronization, or a coach interruption is open.

Live hints are recorded on the server before they are revealed. Any hint step excludes good-move credit for that canonical position, including after reload, takeback or revisiting it with different move counters. Closing the panel does not erase assistance. If the position changes while a hint is being prepared, request a new hint; the stale hint is not disclosed. Standalone FEN study hints remain available without claiming live-game assistance history.

Run `uv run python -m scan64.learning.evaluation.hint_coverage` to report tactical, principle-only and fallback shares of candidate moves over M48's 40 recorded real games, without launching an engine. Each reached position uses its deepest available recording (deep source, focused reply, or fast search); at most three tolerance-qualified candidates count per position. Tactical takes precedence when a candidate also has a principle. These shares measure explanatory coverage, not correctness or learning outcomes, and have no pass threshold.

## Import and analyse a PGN

1. Open **Library**, then **Import PGN**.
2. Paste a game, choose **Your side** (White or Black), and import it as the active player. A matching PGN player id or display name pre-selects your side. Lessons and profile observations use only your moves; older games without a stored side cannot be analysed.
3. Open **Review**, select the imported game, then select **Analyse game** when it has not already been analysed.
4. Wait for the analysis state to complete.
5. Replay every move on the analysis board, including unflagged moves and the final position. Select a move, a graph point or a key-moment marker to keep the board and move list linked. Analysis keeps at most five lessons at distinct source positions, ranked by win-probability loss and diagnostic confidence, with a penalty for skills already represented by kept lessons. A deeper source search can discard a flagged move when the move actually played is sound; lower-ranked mistakes then fill the available slots without a skill penalty for discarded candidates. Only kept lessons have review schedules; evidence and profile observations still include all diagnosed owner opportunities.

For a deterministic manual diagnosis walkthrough, import this game:

```pgn
[Event "Manual diagnostic test"]
[White "Manual Tester"]
[Black "Opponent"]
[Result "0-1"]

1. e4 e5 2. Qh5 Nc6 3. Qxe5+ Nxe5 0-1
```

The analysis identifies the hanging queen as `board_awareness.hanging_piece`. Lesson answers depend on the engine analysis, not a hard-coded move.

### Good move credit

Review lists credited moves with a plain-language proof and a button selecting their ply on the board. Each owner move also shows credit or its exclusion reason. **Profile → Strengths & Weaknesses** reflects the credited skill after analysis; reopen Profile to load its latest state.

Credit is deliberately conservative: only a newly tracked, completed game's scored sound owner move that exactly matches the stored first engine line can qualify. Supported tactics are profitable captures (not while in check, not mere recaptures restoring material, with a known preceding move) and checks whose legal line proves material gain or a positive mate score. Capture takes precedence when both apply. Each player/game/canonical source/skill earns at most one +1 success update; repeated positions cannot farm credit. A player earns at most **two credits per skill per game**. When more qualify, proven mate comes first; otherwise higher proven net capture gain or checking-line material gain comes first, then the earliest ply. Other qualifying moves show **“per-game credit cap reached”**. Archives exceeding two credits for one player/game/skill are rejected rather than silently trimmed. Same-source lessons, attempts and failed observations exclude positive credit rather than changing existing failure or practice semantics.

“No in-app hint” means only that Scan64 recorded no hint at that source; external assistance cannot be established. Imported games, old archives and unknown assistance history do not establish eligibility. Coach mode, custom-FEN starts (“started from a studied position”), hinted sources and analysis performed before the game ended receive no positive credit. Review reads and review-only backfill never award it later. Other lesson practice and hinted half-credit are unchanged.

Not every good move earns credit. Unsupported, untrusted, retired or incomplete proofs earn none. Low-budget engine lines are coarse evidence, not measured improvement, a playing-strength rating or an independently validated positive-tactic precision claim.

### Back up before upgrading or rolling back

Before a database migration, save both a **player export archive** using the player data export API and a **database-file copy**. Stop Scan64 before copying SQLite so the backup is consistent; keep the copy outside the live data directory. The player archive preserves owned progress and credit/hint provenance, while the database copy also preserves shared data. Keep the matching application version and verify the recovery path on a disposable copy before touching your working database.

The default rollback keeps already awarded skill alpha: export the positive-credit ledger before downgrading, then use the copy-tested migration downgrade and matching application version. Do not guess a subtraction from decayed skill values. Restoring a pre-upgrade database backup instead requires an explicit owner choice and loses **all later progress**, including unrelated games and practice. Older player archives restore assistance history as unknown, not independently played.

### Measure diagnosis precision offline

Run `uv run python -m scan64.learning.evaluation.real_games` from the repository root. It prints precision, prediction support (TP + FP), true/false positives and missed reference labels for every skill code, without launching Stockfish. `--check`, also run by `scripts/check.sh`, rejects regressions of the frozen trusted-code allowlist: precision must be at least 0.80 with support of at least 10.

Game analysis and coach interruptions now show exactly one measured primary cause, with no secondary codes. Only the frozen trusted codes generate these lessons, explanations and diagnosis-derived profile updates. Untrusted detections remain stored evidence; existing skill rows are not deleted. Legacy game lessons without the current M48 fast-proof marker are hidden even when their code is trusted. Patterns, weekly summaries and opening error rates use the same visibility policy. Hidden rows remain stored but reserve neither lesson slots nor source positions. A hanging-piece lesson must replay a legal fast reply that wins its named piece in the target-local exchange, with whole-line profit only a secondary check. Calculation signals remain explicitly unvalidated hypotheses, not claims about where a human stopped thinking. Curated Opening Explorer, transfer positions and famous games use author-assigned teaching codes, not detector diagnoses, so their content is unchanged.

The committed `tests/fixtures/m48/real_games.pgn` contains the first 40 qualifying games from the January 2013 Lichess CC0 archive: both ratings 600–1600 and at least 20 plies. `recorded.json` records the source URL, published source checksum, selection rule, engine identity and fixed node limits. Reference labels use only the first principal variation from separate **1,000,000-node, MultiPV-4** source and reply searches. The hardened detectors use **10,000-node** signals instead. The oracle module does not import evidence composition or detector predicates. For a capture, only consecutive captures on its target square establish the named piece's exchange-local gain; profit elsewhere later in the line cannot justify calling an evenly traded piece hanging. Whole-line material gain is only a secondary check. Each detector is arbitrated separately for measurement, so competitors cannot conceal false positives. This remains an engine-derived operational reference, not independent coach review or proof of rating gains. Calculation is operationalised as material lost only later in a line; development as an engine move developing a home minor while at least three remain home in the first ten moves. These labels do not reveal where a person stopped thinking or prove that an opening choice caused a loss.

To reproduce recordings, download the compressed source named in provenance and run `uv run python scripts/record_diagnosis_reference.py SOURCE.pgn.zst` with Stockfish and `zstd` on PATH. The recorder verifies the published archive SHA256 before streaming decompression. `--augment-before` preserves existing output and records only missing deep source positions.


## Train from diagnosed positions

Open **Train** (Daily Training) after one or more owned games have generated eligible lessons.

- A lesson is served only with durable player-owned opportunity and study-session context.
- Move answers are checked on the server against all sound moves found by the lesson's source-position MultiPV search (up to eight lines). A move is sound when its win-probability loss against the best is at most 0.08, using the Lichess centipawn logistic curve and decisive probabilities for mates. A sound alternative to the first engine choice shows **Correct** and records a success.
- A verified attempt advances only its matching review schedule and active skill state.
- Lessons have three attempts. A first wrong answer draws the nudge and a second draws the idea. **Next hint** reveals the next step at any time without spending an attempt, so you can see the moves before your final try. Hint usage is recorded with each answer; a hinted success keeps half credit. A third wrong answer ends the lesson with its explanation and drawings. Critical-moment review uses the same ladder, including for older lessons upgraded on read from their existing source-position analysis (at most one bounded engine fallback per read).
- Opening Explorer missions are recorded separately and do not change this profile path.
- An empty training screen means Scan64 has no eligible owned lesson for the active player.

Open **Profile** to inspect active mastery, evidence, and recurring diagnoses after completing an attempt.

### The pattern with its evidence

Today and Profile show the same ranked recurring diagnoses as the weekly summary. A pattern needs retained trusted lessons in at least three distinct owned games with completed analysis and a known playing colour; an unfinished (`*`) result still counts for presence. Opponent moves, invalid lessons, retired lesson schedules and retired skills do not count. Reads do not run an engine or update learning records; later invalidation or retirement removes supporting evidence on the next read.

“Highest observed loss among scored retained moments” means the sum of stored A8 win-probability losses, not all mistakes in your games or a causal explanation of your play. Scored patterns rank before unscored patterns; ties use distinct-game support then diagnosis code. Coverage shows scored occurrences out of all supporting retained occurrences separately from game support. Missing scores are unavailable, never zero. The A3 retention cap (at most five lessons per game) biases this sample: these are descriptive recurring diagnoses, not statistically validated habits, population comparisons, or psychological causes.

Each supporting link opens your owned Review at that move. The pattern supplies its diagnosis template; only Review's existing top-three key moments supply a detailed explanation or retry there. Other supporting moves remain inspectable without expanding that limit. Linked coaches can see the pattern, observed cost and coverage, but receive no student Review or practice actions.

Pattern practice tries available same-code **Your mistakes** retries, then a mapped **Theme run** as unrated related practice, not diagnosis or mastery evidence. When related supply is unavailable or exhausted, the plain Daily link says **may be unrelated**. It does not select or reorder today's slots, regenerate the set, or extend retry budgets. Failure observations, practice results and capped positive game-move credit remain distinct.

## Daily goals and tracked pattern evidence

Today and Profile show a **daily qualifying-solve goal**, default **three**, on the
server's UTC calendar. **Qualifying solves per day** accepts a positive whole number;
**Save next-day goal** schedules every edit for the next UTC day. Today's goal and
past day goals do not change. Supply shows unplayed daily slots, eligible lesson
retries and whether extra puzzle modes are available. This is not a promise that
every retry will qualify: a prior attempt or repeated source can exclude it. Short
or exhausted supply never silently lowers the goal, grants a solve or buys a streak.
Use **Refresh habit record** after practice to fetch current server evidence.

A puzzle counts only after a terminal, server-unhinted success with its matching
persisted attempt. A lesson counts only when it is your visible, trusted,
non-retired `persisted_opportunity`, the server grades the opportunity's **first
recorded attempt** as successful, and that attempt reports zero hints. An earlier
wrong or hinted attempt disqualifies a later retry for this goal, even if the retry
is correct. Lesson hint usage is **app-reported**; lesson answers and hint data are
available to the app. This is not server-proven independent lesson solving and
cannot establish absence of external assistance. Existing hinted skill-credit
rules are unchanged.

The same puzzle or canonical source position (EPD, without move counters) counts
at most once across modes in a UTC day. Replaying an already credited result does
not create another credit. Wrong answers, starts, time spent, hint requests,
foreign or exhausted sources, ungraded attempts, transfer practice and opening
attempts do not qualify. A puzzle reserved yesterday but finished today counts
on the **server completion day's UTC date**, not the slot date, browser date or PGN
date. This habit record grants **no mastery, puzzle-rating or other rating bonus**.

The calendar-day streak is separate from Puzzles' consecutive **solve streak**.
Today stays **in progress until the displayed UTC reset boundary**, including after
reaching its goal. A missed day breaks the calendar streak only after that day
ends. Stored day goals, qualifying source keys and trust-at-award snapshots make
historical met days immutable. Later source withdrawal or retirement is annotated
in the day history, but does not un-meet a day or break its historical streak.
Ownership deletion is the exception: it removes that player's snapshots.

Habit evidence uses versioned policy **`habit-v1`** with a persisted effective
timestamp (cutover). Results before cutover receive no retroactive credit. Player
archives preserve policy/version/cutover, scheduled goals, daily source/trust
snapshots, targets, practice/window references, invalidity reasons and awards. Each
award keeps exactly five ordered game/analysis-job references with their server
timestamps, plus any separate analysis barrier that released the award. Restore
validates that saved evidence against the archive; it does not reconstruct a new
window from today's game ordering. Missing or inconsistent award evidence is
rejected. Restore rejects repeated game, position, engine-analysis, analysis-job,
lesson-opportunity, lesson-attempt and evidence IDs before writing, even without
positive-move credits. Restore uses the archived cutover, not the destination's
migration time, and never replays rewards or recalculates Elo. Keep both a player
archive and a stopped-server
database copy before migration or rollback; retain legitimate learning and rating
history rather than trying to subtract habit rewards.

### Track a pattern without claiming a cure

On a qualifying recurring-pattern card, choose **Track practice target**. The server
saves the pattern and tracking start; a different top-ranked pattern never switches
that target. Previously tracked patterns remain visible with their saved support,
coverage and position links. Tracking begins with **three qualifying,
first-recorded-attempt, same-code lesson successes at distinct source EPDs across
at least two games**, all after tracking starts. App-reported zero hints remain a
limitation. Theme runs, Beta priors and related puzzle activity cannot substitute;
too few eligible retries means **insufficient evidence**, not completion.

After the third success, the window considers five subsequent games in server
`Game.created_at`/id order. Only owned Scan64 play-session games with a known owner
colour, terminal win/draw/loss result, completed analysis, every owner move scored,
and at least **20 owner moves** qualify. Imported games and their self-reported PGN
dates do not count. Any visible retained target diagnosis **or uncapped non-retired
ProfileObservation** restarts the window; capped-out or verification-dropped
diagnoses still reset it. An earlier owned active or nonterminal play session
pauses the chronological scan, even before it reaches 20 owner moves; later
completed games cannot silently replace it. The target identifies the unfinished
game with **Resume or resign game**. Finish or resign it, then let analysis
complete; a completed game below 20 owner moves is excluded. The award
is minted in the analysis-completion transaction that resolves the last required
evidence, including an earlier pending game. If that game contains a recurrence,
the window restarts after it and five already-analysed clean games can qualify in
that same transaction. Incomplete or partially scored evidence never earns an
award. A page read or refresh never mints it.

The card says **Pattern practice target met**, separately from **not observed in
the last five eligible games**. Inspect its practice count, game count, window
coverage and saved evidence. The award's five-game evidence remains the same after
export and restore. Absence is bounded by the chosen games and the
**≥150cp fast-pass diagnosis swing threshold**; smaller or undetected errors can
remain. It is not a permanent cure, cleared weakness, strength estimate, causal
improvement result or mastery measurement.

Reading Today/Profile only projects current evidence and invalidity; it does not
persist invalidation, grade practice or mint awards. **Refresh tracked evidence**
is an explicit authenticated write that saves any current invalidity reason; the
next target write does so too. Withdrawn pattern evidence marks current status
invalid, while the award remains labelled historical and met calendar days stay
unchanged. These records belong to the player and are covered by owned
export/restore/deletion; shared catalogs are not removed with a player.

## Use coach mode

Coach mode is an explicit practice option, not an always-on analysis engine.

1. Enable **Coach Mode** before starting a game.
2. Make a clear tactical error, such as leaving a piece en prise.
3. When the server completes an eligible diagnosis within interactive capacity, Scan64 returns an interruption with an owned lesson and study session.
4. Complete the lesson on the board. The resulting attempt is verified and recorded exactly as for Daily Training.

An arbitrary mistake does not guarantee an interruption. Ordinary play, independent calculation, and completed-game review do not use the coach interruption path.

## Import a puzzle catalog

An operator must import puzzles before players can practise them. Nothing downloads at startup.
Use the application's database environment (`SCAN64_DATABASE_URL`) for these commands:

```sh
uv run scan64-cli puzzles import /path/to/lichess_db_puzzle.csv.zst --json
# Or explicitly download the complete database, then import it:
uv run scan64-cli puzzles import --download --json
# In an already running local Docker container:
docker exec <container> scan64-cli puzzles import --download --json
```

`--download` saves the **entire** compressed Lichess CC0 database, roughly **310 MB**
and growing, before importing any rows. It is not a small filtered download. Allow space for
the complete file and a temporary replacement when downloading again. Interrupted downloads
do not replace a previously saved file. The default destination is
`lichess_db_puzzle.csv.zst` beside the SQLite database (or `./data` for other databases);
`SCAN64_DATA_DIR` or `--data-dir /path` overrides that directory. `--output filename.csv.zst`
changes the name (an absolute path overrides the directory), and `--url URL` changes the
download source. Download-only options cannot be combined with a local input path.

Import streams CSV by header through the pinned BSD-licensed `zstandard` library; it does not
load the whole dataset into memory. Defaults select the first **50,000 qualifying unique
puzzles in file order**: rating **600–1800 inclusive**, rating deviation **≤100**,
popularity **≥80**, and plays **≥100**. Explicit overrides are `--limit`, `--min-rating`,
`--max-rating`, `--max-rating-deviation`, `--min-popularity`, and `--min-plays`.
An underfilled import reports its actual count and never relaxes these filters.

The text summary (or `--json`) includes filters, rows read, accepted/inserted/existing counts,
`metadata_changed`, and skipped counts by reason. It also records the complete compressed
file's own SHA256, byte size, local path, source URL and HTTP ETag/Last-Modified when supplied.
For a local file the unavailable URL/HTTP fields are explicitly null. The operator-computed
checksum supports reproducibility; it is **not upstream authentication**.

Reimport the saved file with the same filters to leave the catalog unchanged: qualifying
already-present puzzles count toward the cap, so reruns do not append another 50,000.
Duplicate ids do not consume the cap. Identity is PuzzleId plus the original FEN and complete
UCI Moves (including the opponent's setup); changed FEN/Moves are skipped as conflicts.
Changed rating/themes and other metadata are refreshed and counted as `metadata_changed`,
without replacing the immutable lesson, original provenance, import order or player runs.
The first UCI move is applied as setup; the remaining legal continuation becomes a
source-verified external lesson, not an engine diagnosis or a theme-mastery update.

## Practise imported puzzles

Open **Puzzles** after the operator imports a catalog into the running application's
database using the commands above. First-time players use the same display-name
onboarding as other screens. An empty or exhausted catalog shows the server's message
and an explicit local import command; the screen never downloads a catalog.

The board starts after the opponent's setup move, with your solving side facing you.
Play the complete source continuation, including choosing the exact promotion piece.
After each accepted move the server plays the next opponent move automatically. A
wrong move leaves the position unchanged. There are **three wrong submissions total
per run**, not three per turn; only the server changes this count.

**Show hint** reveals the next of three levels without spending a wrong attempt.
Previously requested hints remain visible for that turn, including after reload.
The third hint may disclose the source move and remaining line. Hints reset on the
next solving turn. These hints come from the imported puzzle, not the live game's
engine-hint endpoint.

Reloading or returning to Puzzles resumes the active server-owned run, including
across UTC midnight; no solution line or progress is saved in browser storage.
Solved and failed states disable the board; choose **Next puzzle** to continue.
**Abandon puzzle** ends the run without a content attempt. For Daily it closes
the slot and loses rating when it is a rated first exposure; other modes are unrated.

If another tab changes the run, the screen loads the conflict response's current
state without replaying your action. Other request failures do not spend attempts
locally: choose **Retry** to reload canonical state before acting again. Puzzle
mutations require a connection; they are not queued for offline submission.

### Daily selection and separate puzzle rating

Today shows your separate Scan64 puzzle rating, provisional status, daily progress
and overdue review count without starting a run. Puzzles shows the server's reason
and selection range. A stable set contains at most **10 puzzles per UTC day**,
targeting **4 due reviews, 5 related to weaknesses and 1 exploration**. Available
candidates can change the actual mix or leave a short set; the server reports it.
New puzzles start within ±200 of your puzzle rating, explicitly widening to ±400
and then the catalog when necessary. Refresh, Start and Next cannot regenerate
the set, skip an active run or duplicate slots. Complete days reset at UTC midnight;
an existing active run always resumes before current-day practice.

The rating starts at **1200**, with **K=32**, and remains provisional until **20
rated puzzles**. For player rating R and the item's rating snapshotted when the
daily set is created: `E = 1 / (1 + 10^((item_rating - R) / 400))`, then
`R' = R + 32 * (S - E)`. Precision is stored; only display is rounded.
First exposure is committed when its daily-slot run starts. Completing it without
any requested hints gives S=1; hinted completion, failure or abandonment gives
S=0. Wrong moves are allowed within the three-submission limit. Hints do not spend
wrong attempts, but prevent an independent rated win even after changing turns.
Due reviews, repeated puzzles and pre-existing legacy runs are unrated; historical
practice is not replayed into the rating.

Daily failed or assisted completions schedule spaced reviews. Independently solving a
Daily review retires it; independent first solves do not enter the review queue.
Abandonment, legacy runs and mode runs do not schedule reviews. Theme relationships are
versioned heuristics (`puzzle-theme-heuristic-v1`), not diagnoses or mastery;
the reverse hangingPiece relationship is not measured prevention of hanging pieces.
Unknown themes can still appear in reviews or exploration.

Imported Lichess CC0 puzzles are **source verified**: their continuation is legal,
not engine recognition of move quality. The `puzzle-elo-v1` practice rating is
not chess/FIDE strength or evidence of improvement and never changes playing
strength priors or theme mastery.

### Theme, Your mistakes and Solve streak

Opening Puzzles resumes an active run, but never starts a new rated Daily puzzle
automatically. Choose a mode beside **Daily**, then **Start**. Finish or abandon
any active puzzle before switching; reload resumes it first. **Theme** uses the
catalog's actual source tags.
Theme and **Solve streak** choose unseen, never-attempted puzzles nearest your current
puzzle rating, widening from ±200 to ±400 and then the catalog, without difficulty
escalation. The app reports when eligible supply is exhausted. Starting either mode
first reserves today's Daily set and excludes its unclosed puzzles; it never rerolls
Daily membership.

These mode runs are **unrated** and never create, update or retire spaced reviews.
Solves and failures still record ordinary content attempts. A puzzle used in a mode
permanently stops being a rated first exposure or an unseen weakness/exploration
candidate. This consumes future Daily supply and can cause earlier widening or a
shorter Daily set. Today's reservations protect only today's unclosed slots.

**Solve streak** is untimed. Only finished runs started in that mode count: an
unhinted solve adds one, while a hinted solve, failure or abandonment resets it.
Wrong submissions within the three-attempt run budget are allowed. Daily and Theme
neither add nor reset. Current/best and the last reset reason are derived from saved
finished runs, so reloads and repeated submissions cannot mint streak progress.

**Your mistakes** lists eligible owned, trusted, non-retired game lessons in pages,
with source game/ply and remaining lifetime attempts. Excluded lessons appear only
as reason counts, never hidden diagnosis text. **Start mistake at ply N** rechecks eligibility
and stored analysis and opens the existing lesson retry. It reuses the lifetime
attempt budget, hints and assisted-credit rules; it does not copy a catalog puzzle.
Lesson hints remain app-reported assistance, not externally verifiable proof that
no help was used.

Operators must back up player rating, daily membership, mode/theme bindings, progress
and the historical rating-event ledger together before migrations, restore or deletion. Use the
reviewed deletion dry-run, rollback and audit workflow; retain a recoverable backup
before destructive changes. Restore preserves awards rather than reapplying them.
A rollback must restore a matching database/schema backup, not silently recompute
historical awards under new policy or refreshed catalog ratings.

## Review games and content

- **Review** (Your Games) lists player-owned played and imported games. Every game has an analysis deep link.
- **Review → Analysis Board** distinguishes a game that has not been analysed, an analysis in progress or failed, and an analysed game with per-move review. A move can have a review score without a trusted lesson.
- **Library → Opening Explorer** supplies opening-family missions.
- **Library → Famous Games** provides guided historical-game study. Recording a prediction requires the studying player's bearer token; another player cannot submit attempts or change that player's skills.
- **Coach** opens the Coach Dashboard with linked-student information for coach identities with relationships.

### Move-by-move game review

The board, every-ply move list, White-perspective probability graph and key-moment markers share one selection. **Initial position**, **First**, **Previous**, **Next** and **Last** include the starting and final positions, even when they have no lesson. **Play from here** starts a new game from the selected position. The graph always shows White's win probability, even when you played Black. Missing scores appear as gaps and hollow points, not 50% or zero loss.

Scan64 scores only your stored owner-colour moves. Let `p_before` and `p_after` be the fast-pass win probabilities for the side to move before and after your move. The side to move switches, so your loss is:

`L = clamp(p_before - (1 - p_after), 0, 1)`

The Scan64 bands are **Sound** for `L ≤ 0.08`, **Inaccuracy** for `0.08 < L ≤ 0.15`, **Mistake** for `0.15 < L ≤ 0.30`, and **Blunder** above `0.30`. Missing either score makes an owner move **Unscored**, not sound. Opponent moves are labelled separately and never enter your accuracy.

**Scan64 accuracy** is `100 * (1 - mean(L))` over scored owner moves only. It is unavailable when no owner move is scored. Read it beside **Coverage** (scored owner moves / all owner moves) and the Sound, Inaccuracy, Mistake, Blunder and Unscored counts; these counts cover your moves, not your opponent's. Older games with unknown owner colour remain explicit: no inferred colour, owner bands, accuracy or learning attribution.

These are coarse **10,000-node fast-pass** estimates, not Elo estimates, Lichess accuracy, or a validated measure of improvement. Deeper lesson searches do not replace the fast-pass accuracy inputs. Exact checkmate and automatic draw outcomes can appear at the final position; a resignation or claimable draw does not replace the engine score with an exact result.

For an older completed analysis without move-review rows, select **Backfill review scores** explicitly. Merely opening the review never searches the engine. Backfill runs one same-budget review-only pass and writes only review rows and position/analysis provenance: no extra learning writes to evidence, diagnoses, observations, lessons or skill state. The subsequent review read may reverify lessons from stored analysis and create an owned retry study session, without an engine search or new lessons.

Up to three key moments explain one trusted cause each, rather than diagnosing every scored error. **Retry this moment** restores the position before that move and uses the same lesson attempt endpoint and three-step hint ladder as Daily Training. The displayed attempts used/remaining are lifetime counts shared with training; reopening a retry does not reset them. Hinted success keeps half credit. Exhausted moments show the explanation and accepted moves, with no further retry.


## Transfer measurement

When an active skill reaches the implemented mastery threshold, Scan64 can assign a separate transfer exercise from its production transfer catalog. A later training session presents it as a required lesson, and its result appears in the player transfer report.

This measures the product's transfer signal. It does not prove Scan64 improves over-the-board chess performance; that requires real learner studies.

## Data and privacy

All player-reachable game, play-session, position, analysis-job, and lesson-attempt paths are bearer-token owner-authorized. Missing, malformed, ownerless, and non-owned resources use the same not-found response.

The API supports player-scoped export, import, and deletion. Treat deletion as destructive. Use a disposable player and database before testing it.

The generated API reference at `http://127.0.0.1:8001/docs` documents the live request and response schemas. `http://127.0.0.1:8001/openapi.json` is the machine-readable contract.

## Manual acceptance walkthrough

1. Start Scan64 with a disposable database.
2. Create a player and play `e2` to `e4`; confirm the opponent reply arrives.
3. Navigate to **Review** and back; confirm the active session resumes.
4. Resign; confirm the game becomes visible in history and completes analysis.
5. Import the hanging-queen PGN above, analyse it, and confirm a diagnosis marker and engine evaluations appear.
6. Complete its Daily Training lesson with an accepted move; confirm **Correct**, recorded success, and updated profile mastery.
7. Start a coach-mode game, make a diagnosed tactical error, complete the interruption, and confirm the profile updates.
8. Run the full local gate:

```sh
scripts/check.sh
```
