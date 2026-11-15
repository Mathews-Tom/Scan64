<p align="center">
  <img src="assets/banner.svg" alt="Scan64: play, review and practise from your own games" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Mathews-Tom/Scan64/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Mathews-Tom/Scan64?color=a1ffcb&label=release"></a>
  <a href="https://github.com/Mathews-Tom/Scan64/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Mathews-Tom/Scan64/ci.yml?branch=main&label=CI"></a>
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-a1ffcb"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-232323">
  <img alt="Desktop: macOS arm64, Windows x64 and Linux x64" src="https://img.shields.io/badge/desktop-macOS%20%C2%B7%20Windows%20%C2%B7%20Linux-232323">
</p>

<p align="center">
  <b>A free, open-source chess coach that runs on your own computer.</b><br>
  Play a bot at your level, ask for a hint when you're stuck, replay the moments that decided the game, and practise puzzles picked from your own mistakes.
</p>

<p align="center">
  <a href="#get-started">Get started</a> ·
  <a href="#see-it-in-action">See it in action</a> ·
  <a href="docs/user-guide.md">User guide</a> ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

---

## Why Scan64?

Most chess tools can tell you that a move was bad. Scan64 tries to help you see *why*, and then gives you something to practise. It is built for improving club players, roughly 600 to 1600, who keep losing to the same kinds of mistakes, but anyone who enjoys the game is welcome.

- **Your games are the curriculum.** Lessons, reviews and patterns come from the games you play or import, checked by Stockfish.
- **Hints that teach, not tell.** Hints come in three steps: a nudge, then the idea, then the move with arrows and a reason. You decide how much help you need.
- **Honest about what it knows.** Scan64 shows you the evidence behind every pattern and goal. It does not claim to raise your rating.
- **Private by default.** Everything runs locally. Your games and progress stay in a database on your machine, and you can export them or delete your player data whenever you like.

## See it in action

These recordings and screenshots are from the **published v0.7.0 macOS app window**, using demonstration games and a small sample of the Lichess CC0 puzzle catalog. The same client runs on all three desktop platforms.

### Play a bot and ask for a hint

Pick one of eight bot levels and a colour, take moves back while you learn, and step through the hint ladder when you're stuck.

<p align="center"><img src="assets/demo-play-hints.gif" alt="Playing a bot game in Scan64 and stepping through the three-step hint ladder until arrows and a reason appear" width="860"></p>

### Review the moments that decided the game

After a game, Scan64 analyses your moves with Stockfish and shows an accuracy figure, an evaluation graph and up to three key moments, each of which you can retry on the board.

<p align="center"><img src="assets/demo-review.gif" alt="Selecting a blunder on the review board, reading its evaluation graph and accuracy coverage, then inspecting the game's two verified key moments" width="860"></p>

### Practise puzzles

Solve multi-move puzzles from the free Lichess puzzle database. Scan64 keeps a separate puzzle rating, builds a daily set and lets you practise by theme or revisit your misses.

<p align="center"><img src="assets/demo-puzzles.gif" alt="Solving a multi-move puzzle in Scan64" width="860"></p>

<table>
  <tr>
    <td width="33%"><a href="assets/screenshot-today.png"><img src="assets/screenshot-today.png" alt="The v0.7.0 Today screen with Play, Train and Review actions"></a></td>
    <td width="33%"><a href="assets/screenshot-review.png"><img src="assets/screenshot-review.png" alt="The v0.7.0 review board with a selected blunder marked as a key moment"></a></td>
    <td width="33%"><a href="assets/screenshot-profile.png"><img src="assets/screenshot-profile.png" alt="The v0.7.0 Profile cropped to the declared starting level, suggested bot level and introductory daily-puzzle progress"></a></td>
  </tr>
  <tr>
    <td align="center"><b>Today</b>: what to do next</td>
    <td align="center"><b>Review</b>: where the game turned</td>
    <td align="center"><b>Profile</b>: your starting point</td>
  </tr>
</table>

## What you can do

| Play | Learn | Track |
| --- | --- | --- |
| Eight bot levels, either colour, take-backs and resignation | Three-step hints with tactical reasons and positional principles | Today dashboard with daily goals and a calendar streak |
| Import your own PGN games | Per-move game review with key moments and retries | Your costliest recurring pattern, with the positions behind it |
| Keyboard move entry and narrow-screen play | Lessons from your mistakes that accept the sound alternatives the engine verifies | Separate puzzle rating and bot-level trends |
| Optional human-like opponents with [Maia](docs/maia.md) | Daily puzzle sets, theme practice, solve streaks | Player-scoped [export, import and deletion](docs/user-guide.md#data-and-privacy) |

## How it works

```mermaid
flowchart LR
  A[Play a bot or import a PGN] --> B[Stockfish analyses your moves]
  B --> C[Mistakes become evidence]
  C --> D[One diagnosed cause per mistake]
  D --> E[Lessons, reviews and puzzles]
  E --> F[Verified attempts update your profile]
  F --> A
```

Every lesson answer is checked by the engine or a tablebase before it can change your profile. Patterns only count evidence from your own moves, and the coverage behind each figure is always shown. See the [system overview](docs/system-overview.md) for details.

## Get started

### Install the desktop app

The unified desktop app opens Scan64 in one window, without a browser tab. It bundles Python and Stockfish; no developer tools or separate engine are needed.

**Download [Scan64 v0.7.0](https://github.com/Mathews-Tom/Scan64/releases/tag/v0.7.0)** for your OS below. These are published release installers, not temporary [Desktop CI](https://github.com/Mathews-Tom/Scan64/actions/workflows/desktop.yml) artifacts.

**Upgrading from 0.6.1 or earlier:** the old macOS app cannot update itself. Delete it and install v0.7.0 by hand. Its data is not migrated.

| Platform | Download | Install and open |
| --- | --- | --- |
| **macOS Apple silicon (arm64)** | [`.dmg`](https://github.com/Mathews-Tom/Scan64/releases/download/v0.7.0/Scan64_0.7.0_aarch64.dmg) | Open the disk image, drag **Scan64** into **Applications**, eject the image and launch Scan64 from Applications. |
| **Windows x64** | [`-setup.exe`](https://github.com/Mathews-Tom/Scan64/releases/download/v0.7.0/Scan64_0.7.0_x64-setup.exe) | Run the installer, then launch Scan64 from the Start menu. Installation is for your current user, without administrator rights. |
| **Linux x64 — AppImage** | [`.AppImage`](https://github.com/Mathews-Tom/Scan64/releases/download/v0.7.0/Scan64_0.7.0_amd64.AppImage) | Run `chmod +x Scan64_0.7.0_amd64.AppImage`, then `./Scan64_0.7.0_amd64.AppImage` from your download folder. Requires FUSE support; otherwise use the Debian package. |
| **Linux x64 — Debian/Ubuntu** | [`.deb`](https://github.com/Mathews-Tom/Scan64/releases/download/v0.7.0/Scan64_0.7.0_amd64.deb) | From your download folder, run `sudo apt install ./Scan64_0.7.0_amd64.deb`, then launch Scan64 from the application menu. |

**All builds are unsigned.** macOS builds are ad-hoc signed, not Developer ID signed or notarized: for a trusted download blocked by Gatekeeper, use **System Settings → Privacy & Security → Open Anyway → Open**. On Windows, SmartScreen may require **More info → Run anyway**. Only bypass warnings for an artifact you trust; do not disable Gatekeeper or other system protections globally.

The desktop app includes signed in-app updates: **Settings → Updates** checks GitHub Releases, downloads in the background by default, and waits for **Restart to update**. It never restarts automatically. Linux `.deb` installs instead link to the release download. See the [updates guide](docs/user-guide.md#updates).

### Build the macOS app (Apple silicon)

You can also build the unified desktop app locally.
It opens one window with the whole client, not a browser tab or a status window.
You need Xcode Command Line Tools, Rust, uv with managed Python 3.12+, Node.js 24+ and pnpm 10.33.3:

```sh
uv sync --locked --group build --python-preference only-managed
pnpm install --frozen-lockfile
cargo install cargo-about --version 0.8.2 --locked
pnpm --dir apps/scan64-web build
uv run --no-sync python scripts/build-server.py
SCAN64_UPDATER_DRY_RUN=1 pnpm --dir apps/scan64-desktop tauri build --config src-tauri/tauri.bundle.conf.json --bundles app
open apps/scan64-desktop/src-tauri/target/release/bundle/macos/Scan64.app
```

The build bundles Python, Stockfish 18, both networks and their licences/source; runtime needs no developer tools or system Stockfish. It is **unsigned and not notarized** (ad-hoc signing only). If macOS blocks a trusted build, use **System Settings → Privacy & Security → Open Anyway → Open**; never disable Gatekeeper globally.

This is an explicit development dry run: it skips the production public-key guard and cannot consume production updates. The published installers use the production updater key; never use the test-only updater overlay for a release.

Quit with **⌘Q**, the application menu or by closing the window; another launch focuses the existing window. Data and logs live in `~/Library/Application Support/org.scan64.desktop`. The fixed shell origin keeps your player signed in even when the loopback server port changes; retain the app's local storage as well as its database. Older desktop data is not migrated.

Use **Download puzzles** or **Choose a file…** in Puzzles or **Settings → Puzzle library**. Manage app updates in **Settings → Updates**. Browser and container installation below is unchanged. See the [user guide](docs/user-guide.md) and [third-party notices](THIRD_PARTY_NOTICES.md).

### Docker

No local Python, Node or Stockfish needed:

```sh
docker build -t scan64 .
docker run --name scan64 -p 127.0.0.1:8001:8001 -v scan64-data:/data scan64
```

Open `http://127.0.0.1:8001`. The image supports Linux amd64 and arm64, includes checksum-verified Stockfish 18, runs as a non-root user and keeps its database in the `scan64-data` volume. Stop with Ctrl-C and resume with `docker start -a scan64`; your games survive as long as you reuse the volume.

### From source

You need [uv](https://docs.astral.sh/uv/), Python 3.12+, Node.js 24+, [pnpm](https://pnpm.io/installation) 10.33.3, and Stockfish 18+ available as `stockfish` on your `PATH`.

```sh
git clone https://github.com/Mathews-Tom/Scan64.git
cd Scan64
uv sync --locked
pnpm --dir apps/scan64-web install --frozen-lockfile
pnpm --dir apps/scan64-web build
uv run scan64-cli serve
```

This serves the app and API at `http://127.0.0.1:8001` and opens your browser. Use `--no-browser` to skip that, or `--port 8061` for another port. Scan64 binds to loopback only; it is not a hardened LAN or hosted deployment. To keep the database somewhere specific:

```sh
SCAN64_DATABASE_URL="sqlite:////absolute/path/to/scan64.db" uv run scan64-cli serve
```

### Your first session

Enter a display name and choose **Start learning**. Tell Scan64 roughly how strong you are and it suggests a bot level. From **Today** you can Play, Train or Review; **Library** holds the study and import screens.

Puzzles need a one-time import of the free [Lichess puzzle database](https://database.lichess.org/#puzzles) (CC0). Nothing is downloaded automatically:

```sh
uv run scan64-cli puzzles import --download --json
```

The download is about 310 MB. See [Import a puzzle catalog](docs/user-guide.md#import-a-puzzle-catalog) for local files, Docker and filters. In the desktop app, choose **Download puzzles** or **Choose a file…** in Puzzles or **Settings → Puzzle library**.

## What Scan64 does not claim

- It has not been shown to improve over-the-board results or ratings. Bot levels have no Elo figure.
- Lesson hints are reported by the app rather than proven by the server, and the user guide says so.
- Accessibility targets WCAG 2.2 AA on every route, but the hands-on Safari/VoiceOver pass is still pending. See [keyboard use and accessibility](docs/user-guide.md#keyboard-touch-and-screen-readers).
- There is no hosted service, published container image, PyPI package or signed desktop app.

## Documentation

| Use Scan64 | Understand it | Build on it |
| --- | --- | --- |
| [User guide](docs/user-guide.md) | [System overview](docs/system-overview.md) | [Contributing](CONTRIBUTING.md) |
| [Maia opponents](docs/maia.md) | [System design](docs/system-design.md) | [Web client guide](apps/scan64-web/README.md) |
| [Opening families](docs/content/opening-families.md) | [LessonSpec package](packages/chess-lesson-spec/README.md) | [Changelog](CHANGELOG.md) |
| Local API reference: `http://127.0.0.1:8001/docs` (while running) | [Project brief](docs/pitch.md) | [Security policy](SECURITY.md) |

### Development

`scripts/run.sh` starts the development setup: the API on `http://127.0.0.1:8001` and the hot-reloading Vite client on `http://127.0.0.1:5173`. The API does not reload, so restart the script after Python changes. `scripts/check.sh` runs the full local gate (Ruff, mypy, pytest, the web build and tests, and Playwright); it needs `stockfish` on `PATH` and port 5173 free.

### Optional extras

- **Maia weights.** Scan64 never bundles, downloads or hosts Maia weights. If you have the right to use a checkpoint, follow the [Maia guide](docs/maia.md).
- **LLM wording.** Explanations are deterministic templates by default. An operator can configure a provider through `SCAN64_LLM_CONFIG`; generated text is validated against the diagnosis evidence before use.

## Contributing

Bug reports, ideas and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) to set up and run the checks. Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License and credits

Scan64 is licensed under the [GNU General Public License v3.0](LICENSE).

It builds on [Stockfish](https://stockfishchess.org/), [python-chess](https://github.com/niklasf/python-chess) and [Chessground](https://github.com/lichess-org/chessground) (all GPL-3.0-or-later), and imports the [Lichess puzzle database](https://database.lichess.org/#puzzles) (CC0). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full list.

The Scan64 [mark](assets/logo.svg) and [social preview](assets/social-preview.png) are available in `assets/`.
