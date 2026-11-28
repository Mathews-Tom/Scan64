# Get started

## Install the desktop app

The unified desktop app opens Scan64 in one window, without a browser tab. It bundles Python and three engines: a fresh install analyses with Caissa and plays the bot levels with Arasan; an install from before keeps Stockfish ([which engines an install uses](engines.md#which-engines-an-install-uses)). No developer tools or separate engine are needed.

**Download the installer for your platform from the [latest published Scan64 release](https://github.com/Mathews-Tom/Scan64/releases/latest).** [Desktop CI](https://github.com/Mathews-Tom/Scan64/actions/workflows/desktop.yml) artifacts are test builds, not release installers.

**Upgrading from 0.6.1 or earlier:** the old macOS app cannot update itself. Delete it and install the current desktop release by hand. Its data is not migrated.

| Platform | Release asset | Install and open |
| --- | --- | --- |
| **macOS Apple silicon (arm64)** | `.dmg` | Open the disk image, drag **Scan64** into **Applications**, eject the image and launch Scan64 from Applications. |
| **Windows x64** | `-setup.exe` | Run the installer, then launch Scan64 from the Start menu. Installation is for your current user, without administrator rights. |
| **Linux x64 — AppImage** | `.AppImage` | Make the downloaded AppImage executable, then run it from your download folder. Requires FUSE support; otherwise use the Debian package. |
| **Linux x64 — Debian/Ubuntu** | `.deb` | [Install the downloaded package with `apt`](user-guide.md#install-on-your-os), then launch Scan64 from the application menu. |

See [Open an unsigned installer safely](#open-an-unsigned-installer-safely) before the first launch on macOS or Windows.

The desktop app includes signed in-app updates: **Settings → Updates** checks GitHub Releases, downloads in the background by default, and waits for **Restart to update**. It never restarts automatically. Linux `.deb` installs instead link to the release download. See the [updates guide](user-guide.md#updates).

## Open an unsigned installer safely

Scan64 is a free project with one developer, and it does not pay for Apple Developer ID or Windows code-signing certificates. The macOS app is ad-hoc signed and not notarized. The Windows installer has no Authenticode signature. Your operating system therefore cannot confirm who built the download and warns you on first launch. These warnings do not mean the app is harmful, but they also do not tell you it is safe. The steps below confirm that your file is exactly the one published on the Scan64 release, not a corrupted or swapped copy; whether to trust that release is still your decision.

**1. Download only from the [Scan64 releases page](https://github.com/Mathews-Tom/Scan64/releases/latest).** Other mirrors, Desktop CI artifacts and files sent to you by someone else are not release installers.

**2. Check the file's SHA-256 against the release.** GitHub lists a `sha256:` digest for every release file. With the [GitHub CLI](https://cli.github.com/), print the latest release's digests with `gh release view --repo Mathews-Tom/Scan64 --json assets --jq '.assets[] | [.digest, .name] | @tsv'`. Hash your download with the command below. Then compare its output with the hex after `sha256:` for the same file name. Every character must match; letter case does not matter.

| System | Command |
| --- | --- |
| macOS | `shasum -a 256 ~/Downloads/Scan64_*_aarch64.dmg` |
| Windows (PowerShell) | `Get-FileHash $HOME\Downloads\Scan64_*_x64-setup.exe -Algorithm SHA256 \| Format-List Path,Hash` |
| Linux AppImage | `sha256sum ~/Downloads/Scan64_*_amd64.AppImage` |
| Linux Debian/Ubuntu | `sha256sum ~/Downloads/Scan64_*_amd64.deb` |

If the values differ, delete the file and download it again. Do not open a file whose digest does not match.

**3. macOS: allow Scan64 once.**

1. Drag Scan64 to **Applications** and open it. macOS refuses because it cannot verify the developer. Choose **Done**, not **Move to Trash**.
2. Open **System Settings → Privacy & Security**. Under **Security**, select **Open Anyway** next to the Scan64 message. The button appears for about an hour after the blocked launch; open Scan64 again if it is missing.
3. Confirm **Open Anyway** and enter your login password. macOS remembers this exception for Scan64.

Since macOS 15, Control-click → **Open** no longer bypasses this check; use **Open Anyway**. If the **Open Anyway** button never appears, you can instead remove the download flag from this one app in Terminal: `xattr -dr com.apple.quarantine /Applications/Scan64.app`. On a Mac managed by your work or school, ask its administrator rather than working around their settings. Never turn Gatekeeper off for the whole Mac with `spctl --master-disable` or similar. **If macOS says Scan64 is damaged or will damage your computer, stop.** That is not the unsigned-developer warning. Delete the file and download it again, then check the digest.

**4. Windows: get past SmartScreen.**

- If **Windows protected your PC** appears, select **More info**, check that the file name is the one you verified, then select **Run anyway**.
- Alternatively, before running the installer, right-click it, choose **Properties**, tick **Unblock** and select **OK**. This removes the downloaded-from-the-internet mark from that one file.
- **Smart App Control** blocks unsigned apps that it has no reputation data for, and it offers no per-app exception. If it blocks the installer, the only way to run Scan64 is to turn Smart App Control off in **Windows Security → App & browser control → Smart App Control settings**. That removes its protection for every app, so decide whether the trade-off is acceptable for your PC before you change it.

**5. Linux needs no bypass.** AppImages and `.deb` packages carry no platform signature to check; verify the digest as in step 2.

**After installation, updates are verified for you.** On macOS, Windows and the AppImage, **Settings → Updates** installs an update only if its signature matches Scan64's update key (key id `A1BF33DC0D262D9E`, listed in [SECURITY.md](../SECURITY.md)). This is separate from operating-system code signing. The `.deb` package does not update itself. Whenever you install a version by downloading it yourself, including every `.deb` update, repeat steps 1, 2 and the step for your system.

## Build the macOS app (Apple silicon)

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

The build bundles Python, Stockfish 18, Caissa 2.0 and Arasan 26.1, their networks and their licences/source; runtime needs no developer tools or system Stockfish. It is **unsigned and not notarized** (ad-hoc signing only). If macOS blocks a trusted build, use **System Settings → Privacy & Security → Open Anyway**; never disable Gatekeeper globally.

This is an explicit development dry run: it skips the production public-key guard and cannot consume production updates. The published installers use the production updater key; never use the test-only updater overlay for a release.

Quit with **⌘Q**, the application menu or by closing the window; another launch focuses the existing window. Data and logs live in `~/Library/Application Support/org.scan64.desktop`. The fixed shell origin keeps your player signed in even when the loopback server port changes; retain the app's local storage as well as its database. Older desktop data is not migrated.

Use **Download puzzles** or **Choose a file…** in Puzzles or **Settings → Puzzle library**. Manage app updates in **Settings → Updates**. Browser and container installation below is unchanged. See the [user guide](user-guide.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).

## Docker

No local Python, Node or Stockfish needed:

```sh
docker build -t scan64 .
docker run --name scan64 -p 127.0.0.1:8001:8001 -v scan64-data:/data scan64
```

Open `http://127.0.0.1:8001`. The image supports Linux amd64 and arm64, includes checksum-verified Stockfish 18, runs as a non-root user and keeps its database in the `scan64-data` volume. Stop with Ctrl-C and resume with `docker start -a scan64`; your games survive as long as you reuse the volume.

## From source

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

## Your first session

1. Enter a display name and choose **Start learning**. Declare a starting level for an overridable bot suggestion—not an Elo estimate.
2. From **Today**, play a bot or use **Review → Import games** for a Lichess/Chess.com username or PGN. Let analysis finish, then inspect a key moment and retry it.
3. Open **Train** for a lesson or **Safe or blunder**, or start **Convert the win** from an eligible review. Profile shows game insights and patterns; sparse before/after samples say **Not enough games**.
4. Explore **Library** for repertoires and community packs. Use **Settings → Send feedback** to preview a GitHub issue draft; Scan64 sends nothing itself.

Puzzles need a one-time import of the free [Lichess puzzle database](https://database.lichess.org/#puzzles) (CC0). Nothing is downloaded automatically:

```sh
uv run scan64-cli puzzles import --download --json
```

The download is about 310 MB. See [Import a puzzle catalog](user-guide.md#import-a-puzzle-catalog) for local files, Docker and filters. In the desktop app, choose **Download puzzles** or **Choose a file…** in Puzzles or **Settings → Puzzle library**.

## Development

`scripts/run.sh` starts the development setup: the API on `http://127.0.0.1:8001` and the hot-reloading Vite client on `http://127.0.0.1:5173`. The API does not reload, so restart the script after Python changes. `scripts/check.sh` runs the full local gate (Ruff, mypy, pytest, the web build and tests, and Playwright); it needs `stockfish` on `PATH` and port 5173 free.

## Optional extras

- **Maia weights.** Scan64 never bundles, downloads or hosts Maia weights. If you have the right to use a checkpoint, follow the [Maia guide](maia.md).
- **Local wording.** Opt into an installed Ollama model in **Settings → Local explanation wording**. It can reword new explanations, not change engine verdicts; unavailable or rejected wording falls back to built-in templates. See the [privacy boundary](user-guide.md#optional-local-explanation-wording).
