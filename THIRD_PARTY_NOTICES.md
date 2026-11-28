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

## Caissa
- **Version**: Caissa 2.0, the official `2.0` tag (commit `bb725799e9b19ebdaa0d584f5433fc1c3019e349`), unmodified.
- **License**: MIT, code and network. Copyright (c) 2021 Michał Witanowski.
- **Usage**: Bundled analysis engine (#396). Its network is embedded in the executable.
- **Source**: https://github.com/Witek902/Caissa/tree/2.0, archive https://codeload.github.com/Witek902/Caissa/tar.gz/refs/tags/2.0, SHA-256 `c3ac41d0b8c2a9c9cd5259c2a50b63bd01fb470ffc85afef7691fe67a5b5e4c7`.
- **Network**: `eval-ml-8-152B-spsa.pnn` from the Caissa-Nets release https://github.com/Witek902/Caissa-Nets/releases/tag/eval-ml-8-152B-spsa, SHA-256 `ee9f6bbb0ef6cd1f38a13cf095340968c2ad18d0fa1359d493043e61acc829fa`. Licence: MIT, https://github.com/Witek902/Caissa-Nets/blob/8098255ee7b6608d9698ce0c725b743c74ab2f9b/LICENSE (SHA-256 `ae652b1e02d00d2392d416b83ad71397e5ea91117ffd22aa6829ff22b71a71a4`; same text and copyright as below).
- **Included code**: Fathom Syzygy tablebase probing (`src/backend/syzygy`, MIT; Copyright (c) 2013-2018 Ronald de Man, (c) 2015 basil00, (c) 2016-2020 Jon Dart; licence in `src/backend/syzygy/LICENSE`). `src/backend/incbin.h` is Dale Weiler's incbin, released upstream under the Unlicense (public domain); the copy carries no notice of its own.
- **Build**: the Dockerfile and `scripts/build-server.py` verify all three SHA-256 values, place the network at `data/neuralNets/eval-ml-8-152B-spsa.pnn` and run `make -C src sse4 CC=g++ VERSION=2.0 EVALFILE=../data/neuralNets/eval-ml-8-152B-spsa.pnn` (x86-64: Linux, and MinGW on Windows). On ARMv8 the target is `neon`. On macOS the compiler is `CC=clang++`, and `FLAGS` is the makefile's own without the GCC-only `-flto-partition=one` and `-fno-exceptions`, which clang rejects (Caissa's CMake build for clang uses neither). The source is unmodified on every platform.
- **Redistribution**: the Docker image has the executable at `/usr/local/bin/caissa` and the source archive, the network, `LICENSE`, `Fathom-LICENSE` and `Caissa-Nets-LICENSE` in `/usr/share/doc/caissa/`. The desktop apps carry the same files in the `caissa/` resource folder.

```text
MIT License

Copyright (c) 2021 Michał Witanowski

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

## Arasan
- **Version**: Arasan 26.1, the official `v26.1` tag (commit `0c0bebd04b5e5b99560cfd0998fd1cc48a97f8a9`), unmodified except two preprocessor guards in the Windows build (below).
- **License**: the Arasan licence, an MIT licence covering the `src`, `book`, `doc`, `prj`, `tests` and `network` directories. Copyright 1994-2026 by Jon Dart.
- **Usage**: Bundled bot-ladder engine (#396), through `UCI_LimitStrength`/`UCI_Elo`. No opening book is bundled.
- **Source**: https://github.com/jdart1/arasan-chess/tree/v26.1, archive https://codeload.github.com/jdart1/arasan-chess/tar.gz/refs/tags/v26.1, SHA-256 `7fd33137fc70a5ef27ef9022e941a2139da8a28b2a115657129be4acf9bbd7e1`.
- **Network**: `network/arasanv8-20261005.nnue` from that archive, SHA-256 `1f98bc6c1ac615646d66d409e138225c56765ad0c1065f1ce20a22a56c631927`, loaded at runtime from the executable's directory.
- **Included code**: the `src/syzygy` submodule, Fathom (https://github.com/jdart1/Fathom, commit `c9c6fef0dddc05d2e242c183acf5833149ab676d`, MIT; Copyright (c) 2013-2018 Ronald de Man, (c) 2015 basil00, (c) 2016-2025 Jon Dart). The tag archive has no submodules, so the build downloads https://codeload.github.com/jdart1/Fathom/tar.gz/c9c6fef0dddc05d2e242c183acf5833149ab676d (SHA-256 `ba4553045d796f0521a8554ef21bad44b20fd4a7657eb08ff85d17c9f27ff8ac`, shipped as `fathom-c9c6fef0.tar.gz`) and extracts it into `src/syzygy`.
- **Build**: the Dockerfile runs `make -C src -j2 CC=g++ BUILD_TYPE=modern LDFLAGS="-Ofast -fno-rtti -fno-enforce-eh-specs -DNDEBUG"` (`BUILD_TYPE=neon` on ARMv8); that `LDFLAGS` is the Makefile's own without `-fuse-ld=gold`. `scripts/build-server.py` compiles the Makefile's `ARASANX_SOURCES` with the same flags in one compiler call: g++ (MinGW g++ on Windows, linked statically) at the `modern` SSE4.1/POPCNT level, clang++ with the NEON flags on macOS. Arasan 26.1 supports Windows only with MSVC, so for MinGW the script changes two guards in the extracted source and nothing else: in `src/globals.cpp`, `#elif !defined(_MSC_VER)` before the POSIX headers becomes `#elif !defined(_WIN32)` (the `getrlimit` code that needs `<sys/resource.h>` is already `#ifndef _WIN32`); in `src/types.h`, the `_aligned_malloc`/`_aligned_free` branch is taken for `__MINGW32__` as well as `_MSC_VER`. The shipped `arasan-v26.1.tar.gz` is the unmodified upstream archive; the two edits are `ARASAN_MINGW_EDITS` in `scripts/build-server.py`.
- **Redistribution**: the Docker image has the executable and network in `/usr/local/lib/arasan/` and both source archives, `LICENSE` and `Fathom-LICENSE` in `/usr/share/doc/arasan/`. The desktop apps carry the same files in the `arasan/` resource folder.

```text
Copyright 1994-2026 by Jon Dart

 Permission is hereby granted, free of charge, to any person obtaining
 a copy of this software and associated documentation files (the
 "Software"), to deal in the Software without restriction, including
 without limitation the rights to use, copy, modify, merge, publish,
 distribute, sublicense, and/or sell copies of the Software, and to
 permit persons to whom the Software is furnished to do so, subject to
 the following conditions:

 The above copyright notice and this permission notice shall be
 included in all copies or substantial portions of the Software.

 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
 EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
 BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
 ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
```

## Fathom (Syzygy tablebase probing, in Caissa and Arasan)

```text
The MIT License (MIT)

Copyright (c) 2013-2018 Ronald de Man
Copyright (c) 2015 basil00
Copyright (c) 2016-2025 by Jon Dart

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

Caissa's copy (`src/backend/syzygy/LICENSE`) has the same text with "Copyright (c) 2016-2020 by Jon Dart". Each copy ships next to its engine as `Fathom-LICENSE`.

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

## Gemma 4 E4B narrator model (downloaded by the user, not bundled)
- **License**: Apache-2.0, as recorded in the file's GGUF metadata (`general.license`), which links Google's [Gemma 4 licence](https://ai.google.dev/gemma/docs/gemma_4_license).
- **Source**: `gemma-4-E4B-it-Q3_K_M.gguf` from https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF at revision `bfc15c382204943c3a8fff0c750b94ae2364d7a3`, SHA-256 `0980898d0e7d9c75cea05141bf4010393312101ef6cf5b31f8197e0de18655f4`. A Q3_K_M quantisation by Unsloth of [google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it).
- **Usage**: the default local narrator model (#429). The player downloads it with `scan64-cli narrator install` or by hand ([user guide](docs/user-guide.md#the-recommended-narrator-model)) and registers it with their own Ollama. No Scan64 image, app or repository contains the weights.

## Other software in the local Docker image

- **Scan64**: GPL-3.0-or-later; license and these notices are in `/usr/share/doc/scan64/`.
- **Python**: PSF license and included third-party notices, available under `/usr/local/lib/python3.12/`. The runtime uses the official Python 3.12 Debian Bookworm slim image.
- **Debian runtime packages**: Individual licenses and copyright notices are under `/usr/share/doc/<package>/copyright`; the C++ runtime of Stockfish, Caissa and Arasan is supplied by Debian's `libstdc++6` and `libgomp1` packages (GCC runtime exception).
- **Python dependencies**: Installed from `uv.lock`, including python-chess (above), FastAPI, Starlette, Pydantic, SQLModel, SQLAlchemy, Alembic, HTTPX, Uvicorn and zstandard; their licenses and notices are retained in `/app/.venv/lib/python3.12/site-packages/*dist-info/`. The bundled Zstandard library's BSD license is reproduced below.
- **Built web client**: Chessground (above), React/React DOM (MIT), chess.js (BSD-2-Clause), idb-keyval (Apache-2.0) and Workbox service-worker libraries (MIT). The Vite build emits bundled dependency licenses in `/app/web/THIRD_PARTY_LICENSES.md`; service-worker bundles also retain Workbox license comments. The exact dependency versions are recorded in `pnpm-lock.yaml`.
- Node, pnpm, uv and the engine compilers are build tools only, not installed into the runtime image. No Maia model weights are bundled.

## Other software in the macOS Apple silicon app

- **Stockfish 18**: the same unmodified `sf_18` source and SHA-256-verified networks as the container, built with `make -C src -j2 build ARCH=apple-silicon COMP=clang`. The executable, both original NNUE networks, corresponding `stockfish-sf_18.tar.gz` and upstream `Copying.txt` accompany it under `Contents/Resources/stockfish/`. To rebuild, extract the archive, copy both networks into `src/`, and run that make command with Xcode Command Line Tools.
- **Caissa 2.0 and Arasan 26.1**: the same SHA-256-verified sources, networks and licences as the container (see [Caissa](#caissa) and [Arasan](#arasan)), built with Xcode's clang++ (`make -C src neon CC=clang++ ...` for Caissa; the Arasan Makefile's sources and NEON flags for Arasan). They accompany the app under `Contents/Resources/caissa/` and `Contents/Resources/arasan/`; the Windows and Linux apps carry the same `caissa/` and `arasan/` resource folders.
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
