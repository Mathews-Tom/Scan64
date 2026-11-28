# Third-Party Notices

This project uses the following third-party software:

## python-chess
- **License**: GPL-3.0-or-later
- **Usage**: Backend chess logic and PGN parsing.

## FSRS
- **Version**: `fsrs==6.3.3`, pinned in `pyproject.toml` and `uv.lock`.
- **License**: MIT.
- **Source**: https://pypi.org/project/fsrs/6.3.3/.
- **Usage**: Local lesson and puzzle review scheduling. Only the runtime dependency is installed; no optimizer or PyTorch extra. The installed distribution retains its upstream licence, reproduced here:

```text
MIT License

Copyright (c) 2022 Open Spaced Repetition

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Stockfish
- **License**: GPL-3.0-or-later
- **Usage**: Chess engine analysis and computer opponents. The Docker image builds unmodified Stockfish 18 from the official `sf_18` tag (commit `cb3d4ee9b47d0c5aae855b12379378ea1439675c`) for generic x86-64 or ARMv8.
- **Source**: https://github.com/official-stockfish/Stockfish/tree/sf_18. The Dockerfile verifies the source archive's SHA-256 (`22a195567e3493e7c9ca8bf8fa2339f4ffc876384849ac8a417ff4b919607e7b`) and the full SHA-256 of each embedded Stockfish NNUE network before building.
- **Redistribution**: The image includes the corresponding source archive, `Copying.txt` and both original networks in `/usr/share/doc/stockfish/`, alongside the executable at `/usr/local/bin/stockfish`. The source archive contains the upstream build scripts; reproduce the binary with `make -C src build ARCH=x86-64 COMP=gcc` or `ARCH=armv8` after placing the included networks in `src/`. These are Stockfish networks, not Maia weights.

## Stockfish Evaluation Guide
- **Author**: hxim (with one commit by Martin Bennedik), https://github.com/hxim/Stockfish-Evaluation-Guide.
- **License**: GPL-3.0 (`Copying.txt` in the guide repository).
- **Version ported**: commit `63ff375bb5a8b7b2e4f965c3a3de7698fef7616c` (2024-12-07).
- **Usage**: `src/scan64/explanations/factors/` is a Python port of the guide's 125 evaluation functions (the Stockfish 12-era classical evaluation, in 13 groups). The port keeps the guide's function names, groups, tables and arithmetic. The parity harness `tests/helpers/factors/seg_harness.cjs` runs the guide's original JavaScript under Node to produce the committed reference values; the guide itself is not redistributed.

## Chessground
- **License**: GPL-3.0-or-later
- **Usage**: Frontend chessboard component.

## Host Grotesk
- **License**: SIL Open Font License 1.1 (OFL-1.1).
- **Copyright**: The Host Grotesk Project Authors; see the unmodified upstream notice in `apps/scan64-web/public/fonts/host-grotesk-OFL.txt`.
- **Source**: https://github.com/Element-Type/HostGrotesk (release 1.002); Google Fonts distribution https://fonts.google.com/specimen/Host+Grotesk.
- **Bundled files**: `apps/scan64-web/public/fonts/host-grotesk-latin.woff2` and `host-grotesk-latin-ext.woff2`, unmodified Google Fonts v5 variable WOFF2 subsets, upright weights 300–800. Downloaded from `https://fonts.gstatic.com/s/hostgrotesk/v5/co3BmWBnlCJ3U42vbbfdwMjZpFjm.woff2` and `https://fonts.gstatic.com/s/hostgrotesk/v5/co3BmWBnlCJ3U42vbbfdwMjZqljm0U0.woff2`.
- **Original license source**: https://github.com/google/fonts/blob/fa826bb2a3e26df8c1bee99351fc0d039aeed7d2/ofl/hostgrotesk/OFL.txt.
- **Usage**: Bundled interface typography, served locally and precached for offline use. No runtime requests to Google Fonts.

## JetBrains Mono
- **License**: SIL Open Font License 1.1 (OFL-1.1).
- **Copyright**: The JetBrains Mono Project Authors; see the unmodified upstream notice in `apps/scan64-web/public/fonts/jetbrains-mono-OFL.txt`.
- **Source**: https://github.com/JetBrains/JetBrainsMono/tree/19371302b95d218af43299bce79ddbddd0bc364d.
- **Bundled file**: `apps/scan64-web/public/fonts/jetbrains-mono.woff2`, the unmodified upstream `fonts/webfonts/JetBrainsMono[wght].woff2`, upright variable weights 100–800.
- **Original license source**: https://github.com/JetBrains/JetBrainsMono/blob/19371302b95d218af43299bce79ddbddd0bc364d/OFL.txt.
- **Usage**: Bundled notation, evaluations, ratings and clocks; served locally and precached for offline use.

## Lucide interface icons
- **License**: ISC; Feather-derived icons additionally retain the MIT notice.
- **Copyright**: (c) 2022 Lucide Contributors. The bundled upstream licence also retains its current (c) 2026 Lucide Icons and Contributors notice and the Feather (c) 2013-present Cole Bemis notice.
- **Source**: https://lucide.dev/license and https://github.com/lucide-icons/lucide.
- **Usage**: Inline interface paths in the web client's navigation; no package or runtime network dependency.
- **Full original notices**: `apps/scan64-web/public/lucide-LICENSE.txt`, served locally and linked from `licenses.html`.

```text
ISC License

Copyright (c) 2022 Lucide Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## Chess piece artwork
- **Usage**: Board piece graphics in the web client, selected under **Settings → Chessboard**. The artwork is separately licensed and is not relicensed under Scan64's GPL.
- **Full attribution and licence texts**: `apps/scan64-web/public/pieces/NOTICE.txt`, served locally and linked from `licenses.html`. It covers every bundled set: Staunton (cburnett), Merida, Chessnut, Spatial, Firi, California, Maestro, Staunty, mpchess and Sophia.

### mpchess
- **Author**: Maxime Chupin.
- **License**: GPL-3.0-or-later. Full text: `apps/scan64-web/public/pieces/mpchess/LICENSE.txt`.
- **Source**: https://github.com/lichess-org/lila/tree/47948db658a1678d14e97d41c1bcf82958e70ded/public/piece/mpchess (licence listed in lila `COPYING.md`); upstream https://github.com/chupinmaxime/mpchess-pieces at `720f6e050b4ee9b6e68f1ef5d89330a44850d54e`.
- **Bundled files**: `apps/scan64-web/public/pieces/mpchess/{w,b}{K,Q,R,B,N,P}.svg`, unmodified.

### Sophia (GNOME Chess "simple" pieces)
- **Authors**: the GNOME Chess authors, based on original work by Max Froumentin (`ChessSophia.svg`, W3C).
- **License**: W3C Software Notice and License (GPL-compatible). Copyright (c) 1994-2002 World Wide Web Consortium, (Massachusetts Institute of Technology, Institut National de Recherche en Informatique et en Automatique, Keio University). All Rights Reserved. http://www.w3.org/Consortium/Legal/. Full notice: `apps/scan64-web/public/pieces/sophia/COPYING.html`.
- **Source**: https://gitlab.gnome.org/GNOME/gnome-chess/-/tree/de63bdec5cf9f0770890086262e6a127d5c4a619/data/pieces/simple.
- **Bundled files**: `apps/scan64-web/public/pieces/sophia/{w,b}{K,Q,R,B,N,P}.svg`. SVG content unmodified; files renamed on 2026-11-25 from `whiteKing.svg` … `blackPawn.svg`.

## Material board textures
- **License**: CC0-1.0 (public domain dedication), https://creativecommons.org/publicdomain/zero/1.0/. Compatible with GPL-3.0-or-later.
- **Usage**: Wood and stone boards under **Settings → Chessboard**. Only albedo (colour) maps are used; no roughness, normal, specular or gloss maps, so the boards stay matte.
- **Build**: `uv run scripts/build-board-textures.py` downloads each source listed in `scripts/board-textures.toml`, verifies its SHA-256, and writes `apps/scan64-web/public/boards/{id}.avif` and `{id}.webp`. Each square is a separate crop, rotated and scaled to the board's colours; the source pixels are otherwise unmodified.
- **Bundled notice**: `apps/scan64-web/public/boards/NOTICE.txt`, linked from `licenses.html`.
- **Sources**:
  - Granite 002 A (ambientCG (Lennart Demes)), CC0-1.0: https://ambientcg.com/view?id=Granite002A (file https://ambientcg.com/get?file=Granite002A_2K-JPG.zip, colour map SHA-256 `20830cc40b04ef173e5ff176204711aa31d20f9503d9c04bed8758e9a1c90018`). Used in: Grey and blue granite.
  - Granite 005 A (ambientCG (Lennart Demes)), CC0-1.0: https://ambientcg.com/view?id=Granite005A (file https://ambientcg.com/get?file=Granite005A_2K-JPG.zip, colour map SHA-256 `39a19c22c82177ba7883d27ed4c810b089f679c36d88820c5aa65943f260f55c`). Used in: Grey and blue granite.
  - Marble 005 (ambientCG (Lennart Demes)), CC0-1.0: https://ambientcg.com/view?id=Marble005 (file https://ambientcg.com/get?file=Marble005_4K-JPG.zip, colour map SHA-256 `a14b9d20bcaa8beb7c4a40558338004626a062ac311dbb14c910899eb17bf826`). Used in: White and black marble.
  - Marble 006 (ambientCG (Lennart Demes)), CC0-1.0: https://ambientcg.com/view?id=Marble006 (file https://ambientcg.com/get?file=Marble006_4K-JPG.zip, colour map SHA-256 `ec05915901eb74f0cdffc8ba34154edede63bd7238363cfb965e7dcbd52891fe`). Used in: White and black marble.
  - Angli Veneer (Jenelle van Heerden), CC0-1.0: https://polyhaven.com/a/angli_veneer (file https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/angli_veneer/angli_veneer_diff_4k.jpg, colour map SHA-256 `94d8285a6d3230a6a90d30de7d5262dfbf01a163a2064b9fc734c37cd55159ea`). Used in: Rosewood and boxwood, Ebony and boxwood.
  - Flamed Black Veneer (Jenelle van Heerden), CC0-1.0: https://polyhaven.com/a/flamed_black_veneer (file https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/flamed_black_veneer/flamed_black_veneer_diff_4k.jpg, colour map SHA-256 `0fb467edb7d51b1dfb290d2b3d72608026c35c9a2fcac15246b7c039cf641e29`). Used in: Ebony and boxwood.
  - Rosewood Veneer1 (Jenelle van Heerden), CC0-1.0: https://polyhaven.com/a/rosewood_veneer1 (file https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/rosewood_veneer1/rosewood_veneer1_diff_4k.jpg, colour map SHA-256 `84518fafb00a903e80887f5df2df28c1386110ac692ff7f549b9470735d4da4b`). Used in: Rosewood and boxwood.
  - Sapele Veneer (Jenelle van Heerden), CC0-1.0: https://polyhaven.com/a/sapele_veneer (file https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/sapele_veneer/sapele_veneer_diff_4k.jpg, colour map SHA-256 `99869bfe49452608917cc649b4fe1962c2b70c682126e92a36b84cb0370738e3`). Used in: Mahogany and maple.
  - Walnut Veneer (Jenelle van Heerden), CC0-1.0: https://polyhaven.com/a/walnut_veneer (file https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/walnut_veneer/walnut_veneer_diff_4k.jpg, colour map SHA-256 `10e83615504c2d2a8f5a8665ab0d36f550b0ab2a60c6188dc9a317a65e7362ec`). Used in: Walnut and maple.
  - White Maple Veneer (Jenelle van Heerden), CC0-1.0: https://polyhaven.com/a/white_maple_veneer (file https://dl.polyhaven.org/file/ph-assets/Textures/jpg/4k/white_maple_veneer/white_maple_veneer_diff_4k.jpg, colour map SHA-256 `f5c5788ed517c5801999c62d4e5397765eaec04360155793694292dd1aa9af7c`). Used in: Walnut and maple, Mahogany and maple.

## Syzygy 3-piece tablebase data
- **Source**: `http://tablebase.sesse.net/syzygy/3-4-5/`, downloaded 2026-07-14.
- **Usage**: Offline WDL/DTZ verification of the initial curated endgame catalog.
- **Files**: `KQvK.rtbw` (`517667dff787162dbb1ed9d5d6484d30ee854e686ee0675c08d99ecf045d2d50`), `KQvK.rtbz` (`71ea9444fa5bd42897d781a0c356975ea6f23e0f65a4254e470897031c161c8c`), `KPvK.rtbw` (`02fd2bdcd92821c869458f019f63524def1c9e7a2862ca8d2b16dc7712b16183`), and `KPvK.rtbz` (`ad01af2076183c90ccee662ca6c5d74da3222ff1fee47c01b5d4e56ed01938de`).

## Other software in the local Docker image

- **Scan64**: GPL-3.0-or-later; license and these notices are in `/usr/share/doc/scan64/`.
- **Python**: PSF license and included third-party notices, available under `/usr/local/lib/python3.12/`. The runtime uses the official Python 3.12 Debian Bookworm slim image.
- **Debian runtime packages**: Individual licenses and copyright notices are under `/usr/share/doc/<package>/copyright`; the Stockfish C++ runtime is supplied by Debian's `libstdc++6` and `libgomp1` packages (GCC runtime exception).
- **Python dependencies**: Installed from `uv.lock`, including python-chess (above), FastAPI, Starlette, Pydantic, SQLModel, SQLAlchemy, Alembic, HTTPX, Uvicorn and zstandard; their licenses and notices are retained in `/app/.venv/lib/python3.12/site-packages/*dist-info/`. The bundled Zstandard library's BSD license is reproduced below.
- **Built web client**: Chessground (above), React/React DOM (MIT), chess.js (BSD-2-Clause), idb-keyval (Apache-2.0) and Workbox service-worker libraries (MIT). The Vite build emits bundled dependency licenses in `/app/web/THIRD_PARTY_LICENSES.md`; service-worker bundles also retain Workbox license comments. The exact dependency versions are recorded in `pnpm-lock.yaml`.
- Node, pnpm, uv and the Stockfish compiler are build tools only, not installed into the runtime image. No Maia model weights are bundled.

## Other software in the macOS Apple silicon app

- **Stockfish 18**: the same unmodified `sf_18` source and SHA-256-verified networks as the container, built with `make -C src -j2 build ARCH=apple-silicon COMP=clang`. The executable, both original NNUE networks, corresponding `stockfish-sf_18.tar.gz` and upstream `Copying.txt` accompany it under `Contents/Resources/stockfish/`. To rebuild, extract the archive, copy both networks into `src/`, and run that make command with Xcode Command Line Tools.
- **Scan64**: GPL-3.0-or-later; `LICENSE` and these notices accompany the app under `Contents/Resources/notices/`.
- **Python**: the PSF licence and statically linked component notices are under `Contents/Resources/scan64-server/_internal/notices/python/`. The build reads the managed interpreter's `BUILD` marker, fetches that exact version's python-build-standalone full archive, verifies its published SHA-256, and copies its `python/licenses/` files. No Tk runtime is included.
- **Python dependencies**: versions come from `uv.lock`; installed distribution metadata and upstream licences accompany the frozen server. The Zstandard BSD notice below also applies.
- **Built web client**: the same dependencies as the container; generated licences accompany the app as `Contents/Resources/notices/WEB_THIRD_PARTY_LICENSES.md`, including bundled font notices.
- **PyInstaller**: GPL-2.0-or-later with its bootloader distribution exception; its metadata/licence accompanies the frozen server. uv, pnpm, Node, Xcode and make are build tools only. No Maia weights are included.

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
- **Usage**: Streaming decompression of explicitly imported puzzle CSV.zst files. The Python wrapper's BSD licence accompanies its installed distribution; the bundled Zstandard library is redistributed under the following BSD licence, reproduced from https://github.com/facebook/zstd/blob/v1.5.7/LICENSE:

```text
BSD License

For Zstandard software

Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

 * Neither the name Facebook, nor Meta, nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
