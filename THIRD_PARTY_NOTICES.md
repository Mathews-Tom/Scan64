# Third-Party Notices

This project uses the following third-party software:

## python-chess
- **License**: GPL-3.0-or-later
- **Usage**: Backend chess logic and PGN parsing.

## Stockfish
- **License**: GPL-3.0-or-later
- **Usage**: Chess engine analysis and computer opponents. The Docker image builds unmodified Stockfish 18 from the official `sf_18` tag (commit `cb3d4ee9b47d0c5aae855b12379378ea1439675c`) for generic x86-64 or ARMv8.
- **Source**: https://github.com/official-stockfish/Stockfish/tree/sf_18. The Dockerfile verifies the source archive's SHA-256 (`22a195567e3493e7c9ca8bf8fa2339f4ffc876384849ac8a417ff4b919607e7b`) and the full SHA-256 of each embedded Stockfish NNUE network before building.
- **Redistribution**: The image includes the corresponding source archive, `Copying.txt` and both original networks in `/usr/share/doc/stockfish/`, alongside the executable at `/usr/local/bin/stockfish`. The source archive contains the upstream build scripts; reproduce the binary with `make -C src build ARCH=x86-64 COMP=gcc` or `ARCH=armv8` after placing the included networks in `src/`. These are Stockfish networks, not Maia weights.

## Chessground
- **License**: GPL-3.0-or-later
- **Usage**: Frontend chessboard component.

## Syzygy 3-piece tablebase data
- **Source**: `http://tablebase.sesse.net/syzygy/3-4-5/`, downloaded 2026-07-14.
- **Usage**: Offline WDL/DTZ verification of the initial curated endgame catalog.
- **Files**: `KQvK.rtbw` (`517667dff787162dbb1ed9d5d6484d30ee854e686ee0675c08d99ecf045d2d50`), `KQvK.rtbz` (`71ea9444fa5bd42897d781a0c356975ea6f23e0f65a4254e470897031c161c8c`), `KPvK.rtbw` (`02fd2bdcd92821c869458f019f63524def1c9e7a2862ca8d2b16dc7712b16183`), and `KPvK.rtbz` (`ad01af2076183c90ccee662ca6c5d74da3222ff1fee47c01b5d4e56ed01938de`).

## Other software in the local Docker image

- **Scan64**: GPL-3.0-or-later; license and these notices are in `/usr/share/doc/scan64/`.
- **Python**: PSF license and included third-party notices, available under `/usr/local/lib/python3.12/`. The runtime uses the official Python 3.12 Debian Bookworm slim image.
- **Debian runtime packages**: Individual licenses and copyright notices are under `/usr/share/doc/<package>/copyright`; the Stockfish C++ runtime is supplied by Debian's `libstdc++6` and `libgomp1` packages (GCC runtime exception).
- **Python dependencies**: Installed from `uv.lock`, including python-chess (above), FastAPI, Starlette, Pydantic, SQLModel, SQLAlchemy, Alembic, HTTPX and Uvicorn; their licenses and notices are retained in `/app/.venv/lib/python3.12/site-packages/*dist-info/`.
- **Built web client**: Chessground (above), React/React DOM (MIT), chess.js (BSD-2-Clause), idb-keyval (Apache-2.0) and Workbox service-worker libraries (MIT). The Vite build emits bundled dependency licenses in `/app/web/THIRD_PARTY_LICENSES.md`; service-worker bundles also retain Workbox license comments. The exact dependency versions are recorded in `pnpm-lock.yaml`.
- Node, pnpm, uv and the Stockfish compiler are build tools only, not installed into the runtime image. No Maia model weights are bundled.

## Lichess real-game diagnosis reference
- **License**: CC0 (public-domain dedication).
- **Source**: https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst
- **Published checksum**: `aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635`, from https://database.lichess.org/standard/sha256sums.txt; the downloaded archive was verified against it.
- **Usage**: `tests/fixtures/m48/real_games.pgn`: first 40 error-free games in source order with both player ratings 600–1600 inclusive and at least 20 plies. No selection by detection or reference label.
- **Recorded analysis**: `tests/fixtures/m48/recorded.json` contains engine identity, fixed node limits, legal principal variations and the selected-PGN checksum. The additional Scan64 reference amateur game is not included in real-game precision/support.

## Lichess puzzle database
- **License**: CC0-1.0 (public-domain dedication).
- **Source**: https://database.lichess.org/#puzzles and https://database.lichess.org/lichess_db_puzzle.csv.zst.
- **Usage**: Optional operator-imported puzzle catalog; no dataset is bundled or downloaded at startup. The explicit download saves the entire compressed database (roughly 310 MB, growing over time) before streaming local import.
- **Provenance**: Each imported item and import summary records the compressed file's operator-computed SHA256 and byte size, local path and download URL/HTTP ETag/Last-Modified when available. Local imports explicitly record unavailable HTTP fields as null. This checksum records reproducibility, not independently authenticated upstream integrity or engine certification.

## python-zstandard and Zstandard
- **Version**: Python package `zstandard==0.25.0`, pinned in `pyproject.toml` and `uv.lock`.
- **License**: python-zstandard is BSD-3-Clause; the bundled Zstandard library is dual-licensed BSD-3-Clause OR GPL-2.0-only.
- **Source**: https://github.com/indygreg/python-zstandard and https://github.com/facebook/zstd.
- **Usage**: Streaming decompression of explicitly imported puzzle CSV.zst files. Installed distribution licence files accompany the dependency.
