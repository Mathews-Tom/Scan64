# Linux engine builds (#397, #411)

The engines behind the Linux compatibility run
(`results/compat_20261127_202247.jsonl`, profiles in
`config/engines/compat/linux/`) were installed on linux-5090 on 2026-11-27:
Ubuntu, gcc 15.2.0, CUDA 13.2, RTX 5090 (sm_120), Ryzen Threadripper 9960X
(Zen 5, AVX-512). This is the build record for that run, copied from the
machine's engine manifest and build script. Every path is under
`~/.local/opt/engines` (below, `E`): sources in `E/src/<name>`, installed files
in `E/<name>`.

The binary SHA-256 below is the one each JSONL record carries as
`binary_sha256`. For Triumviratus, Rodent V, Maia-3 and both Lc0 profiles the
profile points at a small wrapper script, so the record carries the wrapper's
digest; the wrapped binary's digest and the wrapper text are listed too. Every build ran with at
most 8 jobs; `taskset -c …` only pinned the build to 8 CPUs.

## Engines

### Stockfish 18

- Source: https://github.com/official-stockfish/Stockfish, release 18. The
  binary was built before this record was kept (2026-11-25), so its tag,
  commit and build command were not recorded. Its `compiler` command reports
  g++ 15.2.0, architecture `x86-64-avx2`; its `id name` is `Stockfish 18`.
- Binary: `~/.local/bin/stockfish`,
  `e16866e09afd5be00990e69e3ac7b3c0794abcce133a2dd7b99f13570385b73d`.
- Networks: embedded `nn-c288c895ea92.nnue` and `nn-37f18f62d772.nnue`, the
  names the `Dockerfile` and `scripts/build-server.py` pin
  (https://tests.stockfishchess.org/api/nn/nn-c288c895ea92.nnue,
  `c288c895ea924429ea9092e3f36b2b3c1f00f2a3a4c759ff7e57e79e3b43e4a7`;
  https://tests.stockfishchess.org/api/nn/nn-37f18f62d772.nnue,
  `37f18f62d772f3107e1d6aaca3898c130c3c86f2ab63e6555fbbca20635a899d`).
  Stockfish names a network after the first 12 hex digits of its SHA-256;
  the digests of the embedded copies were not checked separately.

### Stockfish 11

- Source: https://github.com/official-stockfish/Stockfish, tag `sf_11`,
  commit `c3483fa9a7d7c0ffa9fcc32b467ca844cfb63790`.
- Build: `make build` in `E/src/stockfish11/src` with the 64-bit, POPCNT,
  SSE3, no-PEXT configuration that `E/logs/stockfish11.log` prints (the
  `x86-64-modern` architecture; the exact `ARCH=` argument was not kept);
  output copied to `E/stockfish11/stockfish11`.
- Binary: `4d3ae69a83fe356e9a20e33bbbebbec839e7f122e9048d4a182c0a21f1049474`.
- Network: none (classical evaluation).

### Alexandria 9.1.0

- Source: https://github.com/PGG106/Alexandria, tag `v9.1.0`, commit
  `f49c4c2d081988ca36ac18378a42250f7601fcb0`.
- Build: `make -j8 build=native EVALFILE=nn.net` in `E/src/alexandria`.
- Binary: `E/alexandria/Alexandria`,
  `874523b9ce4a1c6da161d50947481e2f901dba02a92e6d0d8518603484e08947`.
- Network (embedded):
  https://github.com/PGG106/Alexandria-networks/releases/download/net10/nn.net,
  `c7a2b6ca795818ee563a9d5b99ce194fbbdfabbe601eaeefe03a4f5fb4571486` (the
  `net-hash.txt` of v9.1.0).

### Minic 3.46

- Source: https://github.com/tryingsomestuff/Minic, tag `3.46`, commit
  `1e7c0ecb31e06e4bd80d14e529c3dcf01d120c2f`; Fathom submodule from
  https://github.com/jdart1/Fathom.git at
  `f08df228fcc62883a577ce66d4d72473f608aadd` (`.gitmodules` uses ssh).
- Build: `NOPROFILE=1 taskset -c 40-47 make build` in `E/src/minic`; output
  `Dist/Minic3/minic_dev_linux_x64` copied to `E/minic/minic`.
- Binary: `4a39dc0117de20f5a1e123692ae76d708f2c6b830871006cb2a9beb6968a0357`.
- Network (embedded, saved as `Tourney/nn.bin`):
  https://github.com/tryingsomestuff/NNUE-Nets/raw/9a32a4c1727a87ab05be4495f2baf8fc57b11e75/nihilist_nugget.bin,
  `cd9c69d0ad8598f495cd7ca1ba7040a06dd6a1f27bec614e115ef9c5e997e996`.

### Obsidian 16.0

- Source: https://github.com/gab8192/Obsidian, tag `v16.0`, commit
  `2838ce52c1a3f997cb1175b360cd8c6b2ae6791c`.
- Build: `make -j8 nopgo build=native` in `E/src/obsidian` (the default PGO
  target ends with `rm -rf obs_pgo`).
- Binary: `E/obsidian/Obsidian`,
  `708688fc47bd3ea72448e527bfdf352e60a623a7e50e42094e03ef60b123c221`.
- Network (embedded):
  https://github.com/gab8192/Obsidian-nets/releases/download/nets/net87perm.bin,
  `26cc34cc2f8df06d2c0c0cc0c2fade6cd354326c5c73e363fa6284d3e3362b16`.

### Triumviratus 8.1

- Source: https://github.com/Tors3/Triumviratus, tag `v8.1`, commit
  `bd309e0673a5a851941f0c19d15b01cbaba4baa5`.
- Build: `taskset -c 32-39 make -C source -j8 all` in `E/src/triumviratus`.
  With no network at the build root the makefile defines
  `-DNNUE_EMBEDDING_OFF`.
- Binary: `E/triumviratus/triumviratus`,
  `23567b1543ec810ad1d8dba3623db831d0ff631b08fc78d2f15e92826a72f448`.
  The profile starts `E/triumviratus/triumviratus-shm`,
  `ac682e4bcf054417b5bb174fcf1db8158479459382480ab895bb5cb84489d865`, which
  keeps the shared network file out of `/dev/shm`:

  ```sh
  #!/bin/sh
  # Scan64 #397/#411 wrapper: Triumviratus shares its network through /dev/shm
  # unless TRIUMV_SHM_DIR is set, and a Scan64 engine profile cannot set an
  # environment variable.
  D="$(dirname "$(readlink -f "$0")")"
  export TRIUMV_SHM_DIR="$D/shm"
  exec "$D/triumviratus" "$@"
  ```

- Network (external file next to the binary, the `EvalFile` default):
  https://github.com/Tors3/Triumviratus-Networks/releases/download/consilium-plebis/nn-consilium-plebis.nnue,
  `b069fc089421bdf2dfcc5d7ea4de2345d7af4795cd996373c07d5f5cf67e0670`.

### Fairy-Max 5.0b

- Source: Debian source package `fairymax` 5.0b-2 from
  https://deb.debian.org/debian/pool/main/f/fairymax/ (no upstream
  repository or commit):
  - `fairymax_5.0b.orig.tar.gz`,
    `3e36ba168aa10179225f8dd6953d1c39c4cd4526572765b2e9c77717efe3e52f`
  - `fairymax_5.0b-2.debian.tar.xz`,
    `8f5d42ca0dd8ac192ec6ac082a30a75fc794d837db29814dbc48ce3fd81c8068`
  - `fairymax_5.0b-2.dsc`,
    `ef15c880a03299377c5df259d19413b2a8f69deb02d9335678b47ccff40d8fff`
- Build: unpack both tarballs into `E/src/fairymax`, apply the patches in
  `debian/patches/series` (`fix-return.patch`, `makefile.patch`,
  `gcc14.patch`, `fairymax-uchar.patch`) with `patch -p1`, then
  `make fairymax fairydir="$HOME/.local/opt/engines/fairymax" CFLAGS="-O2 -s"`.
  The binary reads `fmax.ini` from that compiled-in directory.
- Binary: `E/fairymax/fairymax`,
  `264e8be5ae2a3fd4aefc9f1d659be4a388cc2ae7434acce5f08351ea7605aa39`.
- Network: none. Piece tables `E/fairymax/fmax.ini` (from the package's
  `data/`), `c4372907f0f6a58cc582dfc036eba7f558a28f4b5241d8039ea9408fdf10b62d`.

### Fairy-Stockfish 14

- Source: https://github.com/fairy-stockfish/Fairy-Stockfish, release
  `fairy_sf_14`, commit `f3e6969d11d1bec17eba26e7ae0e629ad4af71dd`. Not built:
  the release binary is used.
- Binary:
  https://github.com/fairy-stockfish/Fairy-Stockfish/releases/download/fairy_sf_14/fairy-stockfish_x86-64
  saved as `E/fairy-stockfish/fairy-stockfish_x86-64`,
  `ab6b85823152e78654092dc2fbb154956a559c6ef0455d728268544390ee150f`.
- Network: none (`EvalFile` empty, classical evaluation). `variants.ini` from
  the same release,
  `e0dc1700fccd9e00e623153ef70a337087bb7d1996bd0b4f010e525cc6f1290d`.

### Rodent V 1.2

- Source: https://github.com/nescitus/Rodent-V, tag `Rodent_v_1.2`, commit
  `b53ffaf670590932957cb63b7b6d871f6f33b7d8`; `golang.org/x/sys v0.47.0` from
  proxy.golang.org (zip
  `cdac013ddced0262926ec29ffcda645da39670e61c7e5b761e572b6b1809bb1b`).
- Build: `GOTOOLCHAIN=local GOAMD64=v3 go build -p 8 -ldflags="-s -w" -o rodent_v`
  in `E/src/rodent-v`, with the Go caches under `E/src/go`; the binary and
  `personalities/`, `books/` and `nets/` copied to `E/rodent-v`.
- Binary: `E/rodent-v/rodent_v`,
  `9987fb98c14ea21d8667a57f68175e9503048980af27d57f025ee9e00142e55f`. The
  profile starts `E/rodent-v/rodent_v-cwd`,
  `6e0447bb5a4016b95ba4c758a91b44d1f7ed2e05da73650c1be1f2ce17d53c9f`, because
  the engine resolves its data relative to the working directory:

  ```sh
  #!/bin/sh
  # Scan64 #397/#411 wrapper: Rodent V resolves personalities/, books/ and nets/
  # relative to its working directory, and a Scan64 engine profile cannot set one.
  D="$(dirname "$(readlink -f "$0")")"
  cd "$D" || exit 1
  exec "$D/rodent_v" "$@"
  ```

- Network: embedded `nets/rodent_4kb_768hl_8ob_v2.bin` from the repository,
  `c35a1abc1b8c1cb1d5f4221454d494c1a6da1ed9088fd51ab27038bfa74b5053`.

### PZChessBot 8.0

- Source: https://github.com/kevlu8/PZChessBot, tag `v8.0`, commit
  `ef6344889c0ca60a95e28033e99eeb34e16101e9`.
- Build: `taskset -c 32-39 make -j8 native` in `E/src/pzchessbot`.
- Binary: `E/pzchessbot/pzchessbot`,
  `bbaa376a6b802b7579b79f94508b927ba064000da2420c9f11e4fe397aa463a5`. Its
  `id name` reports `v20261119-ef63448-dev`, generated from git.
- Network (embedded, saved as `nnue.bin`):
  https://github.com/kevlu8/PZChessBot/releases/download/v8.0/pznet65.nnue,
  `56470c0583a36f5f2c9058a939d40cd1f7ba1813d10f5c19bc0431be3edcfd7e`.

### Lc0 0.32.1 (T1 and BT4 networks)

- Source: https://github.com/LeelaChessZero/lc0, tag `v0.32.1`, commit
  `fd71a2d921b689c5f479d3227c3806c8e272d9c5`. Meson wraps (hash-checked by
  meson): abseil-cpp 20240722.0
  (https://github.com/abseil/abseil-cpp/releases/download/20240722.0/abseil-cpp-20240722.0.tar.gz,
  `f50e5ac311a81382da7fa75b97310e4b9006474f9560ac46f54a9967f07d4ae3`), eigen
  3.4.0 (https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.bz2,
  `b4c198460eba6f28d34894e3a5710998818515104d6e74e5cc331ce31e46e626`) and its
  patch (https://wrapdb.mesonbuild.com/v2/eigen_3.4.0-2/get_patch,
  `cb764fd9fec02d94aaa2ec673d473793c0d05da4f4154c142f76ef923ea68178`). Meson
  1.12.1 and ninja 1.13.2 from `uv tool install`.
- Build, in `E/src/lc0`:

  ```sh
  meson setup build/release --buildtype release -Dgtest=false \
    -Dcc_cuda=120 -Dplain_cuda=true -Dcudnn=false -Dblas=true -Dopenblas=false -Donnx=false
  meson compile -C build/release -j 8
  ```

  The binary links the CUDA 13.2 cuBLAS and runtime dynamically; there is no
  cuDNN, so the backends are `cuda` and `cuda-fp16`.
- Binary: `E/lc0/lc0`,
  `bc6140f393cdb8a19cda4e90dbaf72875cad659772a821178eb3a4276e0cf5ff` (both
  profiles).
- Wrappers: engine option values get no `~` expansion, so a `WeightsFile`
  option would need an absolute home path. Each profile instead starts a
  wrapper next to the binary that passes `--weights` built from `$HOME`. The
  Lc0 rows in `results/compat_20261127_150931`, `172455`, `201411` and
  `202247` ran the binary directly with an absolute `WeightsFile` (same
  binary, same networks).
  - `E/lc0/lc0-t1` (`lc0.toml`),
    `24d7403358d888f70eaf7a698b4385efa37ba1a6ff36d5996a65d593b9e9005b`:

    ```sh
    #!/bin/sh
    # Scan64 #411 wrapper: engine option values get no ~ expansion, so the network
    # path is built from $HOME here instead of a WeightsFile option in the profile.
    exec "$HOME/.local/opt/engines/lc0/lc0" --weights="$HOME/.local/opt/engines/lc0/nets/t1-256x10-distilled-swa-2432500.pb.gz" "$@"
    ```

  - `E/lc0/lc0-bt4` (`lc0-bt4.toml`),
    `6a187d861f2155d6d63b8287de9122588f6cba7ee9f1830f1cdd5b1749f42bed`:

    ```sh
    #!/bin/sh
    # Scan64 #411 wrapper: engine option values get no ~ expansion, so the network
    # path is built from $HOME here instead of a WeightsFile option in the profile.
    exec "$HOME/.local/opt/engines/lc0/lc0" --weights="$HOME/.local/opt/engines/lc0/nets/BT4-1024x15x32h-swa-6147500-policytune-332.pb.gz" "$@"
    ```

- Networks (in `E/lc0/nets/`, passed by the wrappers with `--weights`):
  - T1 (`lc0.toml`):
    https://storage.lczero.org/files/networks-contrib/t1-256x10-distilled-swa-2432500.pb.gz,
    `bc27a6cae8ad36f2b9a80a6ad9dabb0d6fda25b1e7f481a79bc359e14f563406`
  - BT4 (`lc0-bt4.toml`):
    https://storage.lczero.org/files/networks-contrib/BT4-1024x15x32h-swa-6147500-policytune-332.pb.gz,
    `e6ada9d6c4a769bfab3aa0848d82caeb809aa45f83e6c605fc58a31d21bdd618`

### Maia-3 0.1.0

- Source: https://github.com/CSSLab/maia3, commit
  `1e13597c42d4858b7cfd7cfdae01e297263364b2` (no release tag; `main` on
  2026-05-25).
- Build: a uv virtual environment with CPython 3.12.13, then torch 2.14.1+cu130
  from https://download.pytorch.org/whl/cu130 and `uv pip install` of the
  source tree; other dependencies from PyPI (huggingface-hub 2.5.0, chess
  1.11.2, numpy 2.5.5, …). Weights fetched with `maia3-cache --model maia3-5m`
  (and `maia3-79m`) into `E/maia3/hf-home`.
- Binary: the profile starts `E/maia3/maia3-uci-5m`,
  `418fba1d79005755861a57be12117a7371f6984baacd5b0513af2f3ecd7ebfb1`, which
  calls `E/maia3/maia3-uci`,
  `4d4dacf284d6e4cdbee589f5b426fe60287fd766874fdb04ce99c78119286daa`, which
  starts the virtual environment's `maia3-uci`:

  ```sh
  #!/bin/sh
  # Scan64 #397/#411 wrapper: a Scan64 engine profile's binary_path takes no
  # arguments, so the model and device are fixed here.
  D="$(dirname "$(readlink -f "$0")")"
  exec "$D/maia3-uci" --model 5m --device cuda "$@"
  ```

  ```sh
  #!/bin/sh
  # Wrapper: keeps the Hugging Face weight cache and other caches under the engine directory.
  D="$(dirname "$(readlink -f "$0")")"
  export HF_HOME="$D/hf-home"
  export XDG_CACHE_HOME="$D/cache"
  export HF_HUB_DISABLE_TELEMETRY=1
  exec "$D/venv/bin/maia3-uci" "$@"
  ```

- Network: https://huggingface.co/UofTCSSLab/Maia3-5M at revision
  `b6559de2398d7140b985f28fd2c19fb5e47ddabe`, `maia3-5m.pt`,
  `ba14208b2992d85502f5fb501934abf6aaaeb355e9f3fdf90e326911f562524f` (the run
  used 5M only; 79M, revision `a107d6ceb7b298cb04ae1da4edffe2939858b894`,
  `3fc6181d5db789b45a15305732148757ae74efa3e0028e81ba335b462dac45c2`, was
  fetched and checked but not run).

### Caissa 2.0

- Source: https://github.com/Witek902/Caissa, tag `2.0`, commit
  `bb725799e9b19ebdaa0d584f5433fc1c3019e349`.
- Build, in `E/src/caissa`: `cmake -B build -DCMAKE_BUILD_TYPE=Final -DTARGET_ARCH=x64-bmi2`,
  then `cmake --build build -j 8 --target caissa` (the CUDA trainer target
  does not compile with gcc 15 and is not needed).
- Binary: `E/caissa/caissa`,
  `e6d59548829a81b720b9c45a5a7826da32d1433718850960a68e706ab526c551`.
- Network (external `EvalFile` next to the binary):
  https://github.com/Witek902/Caissa-Nets/releases/download/eval-ml-8-152B-spsa/eval-ml-8-152B-spsa.pnn,
  `ee9f6bbb0ef6cd1f38a13cf095340968c2ad18d0fa1359d493043e61acc829fa`.

### Arasan 26.1

- Source: https://github.com/jdart1/arasan-chess, tag `v26.1`, commit
  `0c0bebd04b5e5b99560cfd0998fd1cc48a97f8a9`; `src/syzygy` submodule
  (https://github.com/jdart1/Fathom) at
  `c9c6fef0dddc05d2e242c183acf5833149ab676d`.
- Build: `make -j8 BUILD_TYPE=avx2-bmi2 LDFLAGS="-Ofast -fno-rtti -fno-enforce-eh-specs -DNDEBUG"`
  in `E/src/arasan/src` (the override drops `-fuse-ld=gold`, which this
  machine lacks; a plain `make` gives an SSE-only build).
- Binary: `E/arasan/arasanx-64-avx2-bmi2`,
  `0d522d483ab51ac1896565f65cb6e1cf7c60cccde885498ffb8a71245a35617c`.
- Network: `network/arasanv8-20261005.nnue` from the repository at that
  commit, copied next to the binary,
  `1f98bc6c1ac615646d66d409e138225c56765ad0c1065f1ce20a22a56c631927`.

### Ethereal 14.40

- Source: https://github.com/AndyGrant/Ethereal, commit
  `0e47e9b67f345c75eb965d9fb3e2493b6a11d09a`.
- Build, in `E/src/ethereal`:

  ```sh
  cc -std=gnu11 -O3 -mmove-max=128 -mstore-max=128 -DNDEBUG -DUSE_NNUE=0 -DUSE_POPCNT -DUSE_PEXT \
     -DUSE_AVX2 -mavx2 -mfma -mbmi2 -mpopcnt src/*.c src/pyrrhic/tbprobe.c -lpthread -lm -o Ethereal-mm128
  ```

  Without `-mmove-max=128 -mstore-max=128`, gcc 15 emits a 32-byte aligned
  store into a table that `calloc` only aligns to 16 bytes, and `go`
  segfaults. Output copied to `E/ethereal/Ethereal`.
- Binary: `9b5dcf5dc73a1a4fade21984b3f452c1f7410e4805103ffe8dc88678d1544546`.
- Network: none (classical, `USE_NNUE=0`).

### Berserk 14

- Source: https://github.com/jhonnold/berserk, release `14`, commit
  `8ae895a6151695be4a50d4fb65b0c131659c513a`.
- Build: `taskset -c 32-39 make -j8 build ARCH=native CC=gcc` in
  `E/src/berserk/src`.
- Binary: `E/berserk/berserk`,
  `bc6df977b5c4105101cb4614195b3dd84f145838766c95a4586804e2a68872f5`.
- Network (embedded):
  https://github.com/jhonnold/berserk/releases/download/14/berserk-9b84c340af7e.nn,
  `9b84c340af7e45f6e07f0046235ccb327f4ae0840c8ee2c4b97b99121e5c5084`.

### Koivisto 9.0

- Source: https://github.com/Luecx/Koivisto, tag `v9.0`, commit
  `2895237d7a4ff267752fb19852ce9868bdccb753`; `networks` submodule
  (https://github.com/Luecx/KoivistoNetworks.git) at
  `8cae81230c6bf36733a292ee7633602dfbbe9c49`.
- Build: `taskset -c 40-47 make -j8 native` in `E/src/koivisto/src_files`
  (static, LTO); output `bin/Koivisto_9.0-linux-native` copied to
  `E/koivisto/koivisto`.
- Binary: `d65e7b858268df6cd58a76e10a5f520141b4d9a93a05d9c5ba7391de03680a45`.
- Network (embedded): `networks/default.net` from that submodule commit,
  `cbca457c7e0f1cb9b2ff9a84c2e733479aafed3349cc44eac4fa0dd6af17dab7`.
