# Scan64

**Play chess. Discover your blind spots. Train what you fail to see.**

Scan64 is an open-source, local-first chess playing and learning platform. Play against computer opponents, study famous openings and games, practise tactics and endgames, analyse your matches, and follow structured training sessions—all within one application.

What makes Scan64 different is its personalized learning engine. Instead of merely showing engine evaluations and best moves, it studies your games over time to identify the threats, patterns, calculations, opening ideas, and behavioural habits you repeatedly overlook. It then turns those weaknesses into verified interactive exercises, progressive visual hints, and an adaptive training curriculum.

Scan64 uses engines such as Stockfish for authoritative analysis and can use human-like opponents such as Maia for realistic practice. Its headless backend produces portable `LessonSpec` objects, allowing web, mobile, desktop, voice, and physical-board applications to build their own learning experiences on top of the same engine.

## Run locally

Prerequisites:

- [uv](https://docs.astral.sh/uv/) for the Python 3.12+ backend.
- [pnpm](https://pnpm.io/installation) for the React web client.
- Stockfish 18 or later, available as `stockfish` on `PATH`.

Clone and install both applications:

```sh
git clone https://github.com/Mathews-Tom/Scan64.git
cd Scan64
uv sync --locked
pnpm --dir apps/scan64-web install --frozen-lockfile
```

Start the API at `http://127.0.0.1:8001` and the web client at `http://127.0.0.1:5173`:

```sh
scripts/run.sh
```

Open `http://127.0.0.1:5173`, choose **Play Game**, enter a player ID, and start a game. The active player identity is then retained locally. To exercise player-owned PGN import, choose **Import PGN**, paste this game, and select **Import PGN**:

```pgn
[Event "Local game"]
[White "White"]
[Black "Black"]
[Result "*"]

1. e4 e5 *
```

`SCAN64_DATABASE_URL` accepts a SQLite database URL. The default is `sqlite:///database.db`, preserving the existing relative `database.db` location. Set it before running the script to store the database elsewhere:

```sh
SCAN64_DATABASE_URL="sqlite:////absolute/path/to/scan64.db" scripts/run.sh
```

## Maia model weights

Scan64 can use Maia only with checkpoints provisioned by the operator. It does not bundle, download, cache, redistribute, or host Maia model weights. The licence and redistribution terms for the separately released Maia checkpoint files remain unresolved; upstream clarification is tracked in [CSSLab/maia-chess#76](https://github.com/CSSLab/maia-chess/issues/76). Operators must confirm their right to obtain and use any checkpoint before configuring it. Follow the [operator provisioning guide](docs/maia.md) for the local, fail-closed setup.

Language models are optional and are used only to verbalize verified analysis and provide conversational coaching. Chess legality, exercise validation, player modelling, and learning progress remain deterministic, inspectable, and available for fully local use.