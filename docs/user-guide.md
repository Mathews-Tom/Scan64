# Using Scan64

Scan64 is a local-first chess practice and learning application. It records games you play or import, analyses them with Stockfish, turns diagnosed positions into owned lessons, and updates your profile only from server-verified answers.

## Start the application

Follow the [root quickstart](../README.md#run-locally). It starts the API at `http://127.0.0.1:8001` and the web application at `http://127.0.0.1:5173`.

On your first visit, enter a display name and select **Start learning**. Scan64 creates a player identity once; its bearer token stays in that browser's local storage. Use a separate browser profile for a separate player. Returning visits keep the stored identity.

**Today** offers Play, Train and Review actions, your latest game, the week's activity, weakest skills by name, and recurring patterns. The navigation groups game history under **Review**, daily lessons under **Train**, and Opening Explorer, Famous Games and Import PGN under **Library**. **Profile** shows named skills and evidence; **Coach** opens the existing linked-student dashboard. The shell follows your system's light or dark colour scheme.

## Play a game

1. Open **Play**.
2. Keep the player ID filled from your stored identity; no new name or ID is required.
3. Choose a named **Bot level** and **Your colour** (White, Black or Random). As Black, the opponent makes the opening move before you play.
4. Select **Coach Mode** only for opt-in, post-move interruptions; start the game and move by drag-and-drop. The SAN move list includes both sides' moves.
5. If a pawn reaches the last rank, choose **queen**, **rook**, **bishop** or **knight**; that exact promotion is played. Play, Daily Training and critical-moment lessons share this board.
6. Reload or return to **Play** to resume with your chosen colour and board orientation. Offline moves stay queued until the connection returns. **Take back** undoes your last move and the bot's reply; it is unavailable in coach mode, after completion, while offline moves are queued, or before you have played a move pair. The bot's opening move is never removed.
7. Select **Resign**, or finish by checkmate or a draw. The result appears immediately. Once a game has moves, **Review game** links to its analysis, which Scan64 queues automatically on completion. Resigning before the first move creates no empty game and has no review link.

Play offers eight server-defined levels from `GET /v1/bots`; Analysis Board's play-from-here and Famous Games' continue action default to level 1. The API takes `opponent_config: {"bot_level": "1"}` for Stockfish and optional `player_color` (`white`, `black` or `random`). Omitted colour uses the position's side to move. All eight levels use the same session-seeded MultiPV candidate policy with decreasing error budgets. They are calibrated relative to each other, **not Elo-anchored**, and show no Elo figure. Stockfish's native reduced-strength move generator has no seed control, so it is not used for these levels. Maia's rating-based `strength` is unchanged. Stored older Stockfish sessions still resume using their original native skill setting.

A live opponent move and a background analysis use separate Stockfish pools. Analysis beyond the per-player daily admission quota remains queued; it is not silently discarded.

Run `uv run scan64-cli bots calibrate` to play 20 fixed-seed, node-limited games per adjacent level pair, with each opening from both colours. It prints the Stockfish version and each higher level's score against the level below; the command fails if any score is below 60%. Repeat with the same Stockfish version for identical results. Calibration is a relative engine test, not a human-rating estimate.

## Import and analyse a PGN

1. Open **Library**, then **Import PGN**.
2. Paste a game, choose **Your side** (White or Black), and import it as the active player. A matching PGN player id or display name pre-selects your side. Lessons and profile observations use only your moves; older games without a stored side cannot be analysed.
3. Open **Review**, select the imported game, then select **Analyse game** when it has not already been analysed.
4. Wait for the analysis state to complete.
5. Review move-by-move engine evaluations and diagnosis markers on the analysis board.

For a deterministic manual diagnosis walkthrough, import this game:

```pgn
[Event "Manual diagnostic test"]
[White "Manual Tester"]
[Black "Opponent"]
[Result "0-1"]

1. e4 e5 2. Qh5 Nc6 3. Qxe5+ Nxe5 0-1
```

The analysis identifies the hanging queen as `board_awareness.hanging_piece`. The generated lesson accepts `Bc4` from the source position.

## Train from diagnosed positions

Open **Train** (Daily Training) after one or more owned games have generated eligible lessons.

- A lesson is served only with durable player-owned opportunity and study-session context.
- Move answers are checked on the server against the lesson objective.
- A verified attempt advances only its matching review schedule and active skill state.
- Opening Explorer missions are recorded separately and do not change this profile path.
- An empty training screen means Scan64 has no eligible owned lesson for the active player.

Open **Profile** to inspect active mastery, evidence, and recurring diagnoses after completing an attempt.

## Use coach mode

Coach mode is an explicit practice option, not an always-on analysis engine.

1. Enable **Coach Mode** before starting a game.
2. Make a clear tactical error, such as leaving a piece en prise.
3. When the server completes an eligible diagnosis within interactive capacity, Scan64 returns an interruption with an owned lesson and study session.
4. Complete the lesson on the board. The resulting attempt is verified and recorded exactly as for Daily Training.

An arbitrary mistake does not guarantee an interruption. Ordinary play, independent calculation, and completed-game review do not use the coach interruption path.

## Review games and content

- **Review** (Your Games) lists player-owned played and imported games. Every game has an analysis deep link.
- **Review → Analysis Board** distinguishes a game that has not been analysed, a completed analysis with no diagnosis, and an analysed game with persisted evaluations and diagnosis markers.
- **Library → Opening Explorer** supplies opening-family missions.
- **Library → Famous Games** provides guided historical-game study.
- **Coach** opens the Coach Dashboard with linked-student information for coach identities with relationships.

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
6. Complete its Daily Training lesson with `Bc4`; confirm the profile gains evidence and updated mastery.
7. Start a coach-mode game, make a diagnosed tactical error, complete the interruption, and confirm the profile updates.
8. Run the full local gate:

```sh
scripts/check.sh
```
