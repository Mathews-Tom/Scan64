# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS web
WORKDIR /app
RUN corepack enable
COPY pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/scan64-web/package.json apps/scan64-web/package.json
WORKDIR /app/apps/scan64-web
RUN pnpm install --frozen-lockfile
COPY apps/scan64-web .
RUN pnpm build

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS stockfish
ARG TARGETARCH
RUN apt-get update && apt-get install --yes --no-install-recommends \
    ca-certificates curl g++ make && rm -rf /var/lib/apt/lists/*
WORKDIR /stockfish
# Official sf_18 tag, commit cb3d4ee9b47d0c5aae855b12379378ea1439675c.
# Check both the source and embedded Stockfish NNUE networks before compiling.
RUN mkdir /out && \
    curl --fail --location --retry 3 \
      https://codeload.github.com/official-stockfish/Stockfish/tar.gz/refs/tags/sf_18 \
      --output /out/stockfish-sf_18.tar.gz && \
    echo '22a195567e3493e7c9ca8bf8fa2339f4ffc876384849ac8a417ff4b919607e7b  /out/stockfish-sf_18.tar.gz' | sha256sum --check && \
    tar --extract --gzip --file /out/stockfish-sf_18.tar.gz --strip-components=1 && \
    curl --fail --location --retry 3 \
      https://tests.stockfishchess.org/api/nn/nn-c288c895ea92.nnue \
      --output src/nn-c288c895ea92.nnue && \
    curl --fail --location --retry 3 \
      https://tests.stockfishchess.org/api/nn/nn-37f18f62d772.nnue \
      --output src/nn-37f18f62d772.nnue && \
    echo 'c288c895ea924429ea9092e3f36b2b3c1f00f2a3a4c759ff7e57e79e3b43e4a7  src/nn-c288c895ea92.nnue' | sha256sum --check && \
    echo '37f18f62d772f3107e1d6aaca3898c130c3c86f2ab63e6555fbbca20635a899d  src/nn-37f18f62d772.nnue' | sha256sum --check && \
    case "$TARGETARCH" in \
      amd64) arch=x86-64 ;; \
      arm64) arch=armv8 ;; \
      *) echo "Unsupported Stockfish architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac && \
    make -C src -j2 build ARCH="$arch" COMP=gcc && \
    strip src/stockfish && \
    cp src/stockfish /out/stockfish && \
    cp Copying.txt src/*.nnue /out/

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS caissa
ARG TARGETARCH
RUN apt-get update && apt-get install --yes --no-install-recommends \
    ca-certificates curl g++ make && rm -rf /var/lib/apt/lists/*
WORKDIR /caissa
# Official 2.0 tag, commit bb725799e9b19ebdaa0d584f5433fc1c3019e349 (MIT). The
# network is the Caissa-Nets release eval-ml-8-152B-spsa (MIT; LICENSE from
# Caissa-Nets commit 8098255ee7b6608d9698ce0c725b743c74ab2f9b). Check all three,
# then embed the network in the binary through EVALFILE (src/makefile, INCBIN).
RUN mkdir /out && \
    curl --fail --location --retry 3 \
      https://codeload.github.com/Witek902/Caissa/tar.gz/refs/tags/2.0 \
      --output /out/caissa-2.0.tar.gz && \
    echo 'c3ac41d0b8c2a9c9cd5259c2a50b63bd01fb470ffc85afef7691fe67a5b5e4c7  /out/caissa-2.0.tar.gz' | sha256sum --check && \
    tar --extract --gzip --file /out/caissa-2.0.tar.gz --strip-components=1 && \
    mkdir -p data/neuralNets && \
    curl --fail --location --retry 3 \
      https://github.com/Witek902/Caissa-Nets/releases/download/eval-ml-8-152B-spsa/eval-ml-8-152B-spsa.pnn \
      --output data/neuralNets/eval-ml-8-152B-spsa.pnn && \
    echo 'ee9f6bbb0ef6cd1f38a13cf095340968c2ad18d0fa1359d493043e61acc829fa  data/neuralNets/eval-ml-8-152B-spsa.pnn' | sha256sum --check && \
    curl --fail --location --retry 3 \
      https://raw.githubusercontent.com/Witek902/Caissa-Nets/8098255ee7b6608d9698ce0c725b743c74ab2f9b/LICENSE \
      --output /out/Caissa-Nets-LICENSE && \
    echo 'ae652b1e02d00d2392d416b83ad71397e5ea91117ffd22aa6829ff22b71a71a4  /out/Caissa-Nets-LICENSE' | sha256sum --check && \
    case "$TARGETARCH" in \
      amd64) target=sse4 ;; \
      arm64) target=neon ;; \
      *) echo "Unsupported Caissa architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac && \
    make -C src "$target" CC=g++ VERSION=2.0 EVALFILE=../data/neuralNets/eval-ml-8-152B-spsa.pnn && \
    strip --output-file=/out/caissa src/caissa-2.0-* && \
    cp LICENSE /out/LICENSE && \
    cp src/backend/syzygy/LICENSE /out/Fathom-LICENSE && \
    cp data/neuralNets/eval-ml-8-152B-spsa.pnn /out/

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS arasan
ARG TARGETARCH
RUN apt-get update && apt-get install --yes --no-install-recommends \
    bc ca-certificates curl g++ gawk make && rm -rf /var/lib/apt/lists/*
WORKDIR /arasan
# Official v26.1 tag, commit 0c0bebd04b5e5b99560cfd0998fd1cc48a97f8a9 (MIT,
# including network/). The archive has no submodules; the build needs src/syzygy,
# Fathom commit c9c6fef0dddc05d2e242c183acf5833149ab676d (MIT). Arasan loads
# network/arasanv8-20261005.nnue from its executable's directory at runtime.
RUN mkdir /out && \
    curl --fail --location --retry 3 \
      https://codeload.github.com/jdart1/arasan-chess/tar.gz/refs/tags/v26.1 \
      --output /out/arasan-v26.1.tar.gz && \
    echo '7fd33137fc70a5ef27ef9022e941a2139da8a28b2a115657129be4acf9bbd7e1  /out/arasan-v26.1.tar.gz' | sha256sum --check && \
    curl --fail --location --retry 3 \
      https://codeload.github.com/jdart1/Fathom/tar.gz/c9c6fef0dddc05d2e242c183acf5833149ab676d \
      --output /out/fathom-c9c6fef0.tar.gz && \
    echo 'ba4553045d796f0521a8554ef21bad44b20fd4a7657eb08ff85d17c9f27ff8ac  /out/fathom-c9c6fef0.tar.gz' | sha256sum --check && \
    tar --extract --gzip --file /out/arasan-v26.1.tar.gz --strip-components=1 && \
    mkdir -p src/syzygy && \
    tar --extract --gzip --file /out/fathom-c9c6fef0.tar.gz --directory src/syzygy --strip-components=1 && \
    echo '1f98bc6c1ac615646d66d409e138225c56765ad0c1065f1ce20a22a56c631927  network/arasanv8-20261005.nnue' | sha256sum --check && \
    case "$TARGETARCH" in \
      amd64) build=modern ;; \
      arm64) build=neon ;; \
      *) echo "Unsupported Arasan architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac && \
    make -C src -j2 CC=g++ BUILD_TYPE="$build" LDFLAGS="-Ofast -fno-rtti -fno-enforce-eh-specs -DNDEBUG" && \
    strip --output-file=/out/arasan bin/arasanx-64-"$build" && \
    cp LICENSE network/arasanv8-20261005.nnue /out/ && \
    cp src/syzygy/LICENSE /out/Fathom-LICENSE

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS python
COPY --from=ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
COPY packages/chess-lesson-spec packages/chess-lesson-spec
COPY src src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime
RUN apt-get update && apt-get install --yes --no-install-recommends libstdc++6 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 scan64 \
    && useradd --uid 10001 --gid scan64 --create-home scan64 \
    && mkdir /data && chown scan64:scan64 /data
WORKDIR /app
COPY --from=python /app/.venv /app/.venv
COPY --from=web /app/apps/scan64-web/dist /app/web
COPY --from=stockfish /out/stockfish /usr/local/bin/stockfish
# Corresponding engine source, license and networks accompany the binary.
COPY --from=stockfish /out/stockfish-sf_18.tar.gz /out/Copying.txt /out/*.nnue /usr/share/doc/stockfish/
COPY --from=caissa /out/caissa /usr/local/bin/caissa
COPY --from=caissa /out/caissa-2.0.tar.gz /out/eval-ml-8-152B-spsa.pnn /out/LICENSE \
    /out/Fathom-LICENSE /out/Caissa-Nets-LICENSE /usr/share/doc/caissa/
# Arasan resolves its network next to the executable, so both share a directory.
COPY --from=arasan /out/arasan /out/arasanv8-20261005.nnue /usr/local/lib/arasan/
COPY --from=arasan /out/arasan-v26.1.tar.gz /out/fathom-c9c6fef0.tar.gz /out/LICENSE \
    /out/Fathom-LICENSE /usr/share/doc/arasan/
COPY config/engines/container/caissa.toml config/engines/container/arasan.toml \
    /usr/local/share/scan64/engines/
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/scan64/
ENV PATH="/app/.venv/bin:$PATH" \
    SCAN64_WEB_DIST=/app/web \
    SCAN64_DATABASE_URL=sqlite:////data/scan64.db \
    SCAN64_BUNDLED_ENGINES_DIR=/usr/local/share/scan64/engines \
    PYTHONDONTWRITEBYTECODE=1
USER scan64
VOLUME ["/data"]
EXPOSE 8001
CMD ["scan64-cli", "serve", "--host", "0.0.0.0", "--port", "8001", "--no-browser"]
