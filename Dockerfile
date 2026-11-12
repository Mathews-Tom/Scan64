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
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/scan64/
ENV PATH="/app/.venv/bin:$PATH" \
    SCAN64_WEB_DIST=/app/web \
    SCAN64_DATABASE_URL=sqlite:////data/scan64.db \
    PYTHONDONTWRITEBYTECODE=1
USER scan64
VOLUME ["/data"]
EXPOSE 8001
CMD ["scan64-cli", "serve", "--host", "0.0.0.0", "--port", "8001", "--no-browser"]
