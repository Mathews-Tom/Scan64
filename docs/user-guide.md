# Using Scan64

Scan64 is a local-first chess practice and learning application. It records games you play or import, analyses them with Stockfish, turns diagnosed positions into owned lessons, and updates your profile only from server-verified answers.

## Start the application

Follow the [root quickstart](../README.md#run-locally). It starts the API at `http://127.0.0.1:8001` and the web application at `http://127.0.0.1:5173`.

The web client creates a player identity when you start a game. Its bearer token stays in that browser's local storage. Use a separate browser profile for a separate player.

## Play a game

1. Open **Play Game**.
2. Enter a player ID and optional display name.
3. Select **Coach Mode** only when you want opt-in, post-move interruption during practice.
4. Start a game and move pieces by drag-and-drop.
5. Select **Your Games** and reopen an active game to resume it.
6. Resign or finish the game. Scan64 queues analysis automatically for completed play sessions.

A live opponent move and a background analysis use separate Stockfish pools. Analysis beyond the per-player daily admission quota remains queued; it is not silently discarded.

## Import and analyse a PGN

1. Open **Import PGN**.
2. Paste a game and import it as the active player.
3. Open **Your Games**, select the imported game, then select **Analyse game** when it has not already been analysed.
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

Open **Daily Training** after one or more owned games have generated eligible lessons.

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

- **Your Games** lists player-owned played and imported games. Every game has an analysis deep link.
- **Analysis Board** distinguishes a game that has not been analysed, a completed analysis with no diagnosis, and an analysed game with persisted evaluations and diagnosis markers.
- **Opening Explorer** supplies opening-family missions.
- **Famous Games** provides guided historical-game study.
- **Coach Dashboard** displays linked-student information for coach identities with relationships.

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
3. Navigate to **Your Games** and back; confirm the active session resumes.
4. Resign; confirm the game becomes visible in history and completes analysis.
5. Import the hanging-queen PGN above, analyse it, and confirm a diagnosis marker and engine evaluations appear.
6. Complete its Daily Training lesson with `Bc4`; confirm the profile gains evidence and updated mastery.
7. Start a coach-mode game, make a diagnosed tactical error, complete the interruption, and confirm the profile updates.
8. Run the full local gate:

```sh
scripts/check.sh
```
