"""Generate compatibility evidence using an unmodified v0.9.0 checkout's public API."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import chess
import httpx
import zstandard

REVISION = "982d949"
PLAYER = "v090-compat"


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def snapshot(client: httpx.Client, archive: dict[str, Any]) -> dict[str, Any]:
    """Record visible progress and durable facts with no read endpoint (attempts/schedules)."""
    games = []
    for game in archive["games"]:
        game_id = game["id"]
        games.append(
            {
                "id": game_id,
                "pgn": request(client, "GET", f"/v1/games/{game_id}")["pgn"],
                "analysis": request(client, "GET", f"/v1/games/{game_id}/analysis-status"),
                "review": request(client, "GET", f"/v1/games/{game_id}/review"),
            }
        )
    return {
        "player": PLAYER,
        "profile": request(client, "GET", f"/v1/players/{PLAYER}/profile"),
        "progress": request(client, "GET", f"/v1/players/{PLAYER}/progress"),
        "games": games,
        "lesson_attempts": archive["lesson_attempts"],
        "puzzle_runs": [
            request(client, "GET", f"/v1/puzzles/runs/{run['id']}")
            for run in archive["puzzle_runs"]
        ],
        "skill_states": archive["skill_states"],
        "review_schedules": archive["review_schedules"],
    }


def generate(checkout: Path, output: Path, stockfish: Path) -> None:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    if not revision.startswith(REVISION):
        raise ValueError(f"Expected v0.9.0 ({REVISION}), got {revision}")
    engine = subprocess.run(
        [str(stockfish)], input="quit\n", text=True, capture_output=True, check=True
    ).stdout.splitlines()[0]
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="scan64-v090-", dir="/tmp") as temporary:
        work = Path(temporary)
        database = work / "scan64.db"
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("SCAN64_")
        }
        environment.update(
            BROWSER="/usr/bin/true",
            SCAN64_DATABASE_URL=f"sqlite:///{database}",
            SCAN64_DATA_DIR=str(work / "data"),
            SCAN64_STOCKFISH_PATH=str(stockfish),
            SCAN64_ENGINE_POOL_BATCH_CONCURRENCY="1",
        )
        # The API-only proof needs no built web client. Use the shipped server
        # coroutine with browser launch disabled, without the CLI's static-file check.
        launcher = (
            "import sys,uvicorn; from scan64.cli.serve import _run_server; import asyncio; "
            "assert sys.argv[1] == '--no-browser'; "
            "asyncio.run(_run_server(uvicorn.Server(uvicorn.Config('scan64.api.app:app', "
            "host='127.0.0.1',port=int(sys.argv[2]))), '', False))"
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        if port in {8000, 5173}:
            raise ValueError("Reserved a prohibited port; retry generation")
        with (output / "server.log").open("w") as log:
            server = subprocess.Popen(
                ["uv", "run", "--locked", "python", "-c", launcher, "--no-browser", str(port)],
                cwd=checkout,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180) as client:
                    deadline = time.monotonic() + 180
                    while True:
                        if server.poll() is not None:
                            raise RuntimeError("Historical server exited; see server.log")
                        try:
                            request(client, "GET", "/health")
                            break
                        except httpx.TransportError:
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    "Historical server did not become ready"
                                ) from None
                            time.sleep(0.1)
                    registration = request(
                        client, "POST", "/v1/players", json={"id": PLAYER, "display_name": "Compat"}
                    )
                    client.headers["Authorization"] = f"Bearer {registration['access_token']}"
                    sources = [
                        "benchmarks/fixtures/game_1.pgn",
                        "benchmarks/fixtures/game_2.pgn",
                        "tests/fixtures/reference_amateur_game.pgn",
                    ]
                    lesson_count = 0
                    for source in sources:
                        game = request(
                            client,
                            "POST",
                            "/v1/games",
                            json={
                                "player_id": PLAYER,
                                "owner_color": "white",
                                "pgn": (checkout / source).read_text(),
                            },
                        )
                        job = request(client, "POST", f"/v1/games/{game['id']}/analysis-jobs")
                        deadline = time.monotonic() + 600
                        while job["status"] not in {"completed", "failed"}:
                            if time.monotonic() > deadline:
                                raise TimeoutError("Analysis did not finish")
                            time.sleep(0.2)
                            job = request(client, "GET", f"/v1/analysis-jobs/{job['id']}")
                        if job["status"] != "completed":
                            raise RuntimeError(f"Analysis failed for {source}")
                        served = request(
                            client,
                            "GET",
                            f"/v1/games/{game['id']}/learning-opportunities",
                            params={"player_id": PLAYER},
                        )
                        if served["lessons"] and not lesson_count:
                            lesson = served["lessons"][0]
                            accepted = lesson["interaction"]["accepted_moves"][0]
                            move = chess.Board(lesson["source"]["fen"]).parse_san(accepted["san"])
                            result = request(
                                client,
                                "POST",
                                "/v1/learning/lesson-attempts",
                                json={
                                    "session_id": served["session_id"],
                                    "lesson_id": lesson["lesson_id"],
                                    "source_kind": "persisted_opportunity",
                                    "submitted_move": move.uci(),
                                    "elapsed_ms": 4200,
                                    "hints_used": 0,
                                },
                            )
                            if (
                                result["success"] is not True
                                or result["grading_status"] != "verified"
                            ):
                                raise RuntimeError(f"Lesson not verified: {result}")
                            lesson_count += 1
                    if not lesson_count:
                        raise RuntimeError("Real analysis produced no playable lesson")
                    # Import a small, explicitly synthetic legal line through the tag's CLI.
                    csv = (
                        "PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,"
                        "GameUrl,OpeningTags\n"
                        f"compat1,{chess.STARTING_FEN},e2e4 e7e5 g1f3 b8c6,1000,100,80,100,"
                        "opening short,,\n"
                    )
                    puzzle_file = work / "puzzles.csv.zst"
                    puzzle_file.write_bytes(zstandard.ZstdCompressor().compress(csv.encode()))
                    imported = subprocess.run(
                        [
                            "uv",
                            "run",
                            "--locked",
                            "scan64-cli",
                            "puzzles",
                            "import",
                            str(puzzle_file),
                            "--json",
                        ],
                        cwd=checkout,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    print(imported.stdout.strip())
                    run = request(client, "POST", "/v1/puzzles/runs", json={})["run"]
                    if run is None:
                        raise RuntimeError("No puzzle selected")
                    for puzzle_move in ("e7e5", "b8c6"):
                        run = request(
                            client,
                            "POST",
                            f"/v1/puzzles/runs/{run['run_id']}/moves",
                            json={
                                "move": puzzle_move,
                                "expected_cursor": run["cursor"],
                                "expected_revision": run["revision"],
                            },
                        )
                    if run["state"] != "solved":
                        raise RuntimeError(f"Puzzle not solved: {run['state']}")
                    archive = request(client, "POST", "/v1/exports", json={"player_id": PLAYER})
                    expected = snapshot(client, archive)
                    (output / "archive.json.gz").write_bytes(
                        gzip.compress(json.dumps(archive, sort_keys=True).encode(), mtime=0)
                    )
                    (output / "expected.json").write_text(
                        json.dumps(expected, indent=2, sort_keys=True) + "\n"
                    )
            finally:
                if server.poll() is None:
                    os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            schema = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if schema != ("20261117_18",):
                raise RuntimeError(f"Unexpected historical schema: {schema}")
        (output / "scan64.db.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        provenance = {
            "tag": "v0.9.0",
            "commit": revision,
            "alembic": schema[0],
            "stockfish": engine,
            "stockfish_sha256": hashlib.sha256(stockfish.read_bytes()).hexdigest(),
            "pgn_sources": sources,
        }
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        # Logs contain random request IDs and are operational output, not fixtures.
        (output / "server.log").unlink()
    print(
        f"Generated {len(archive['games'])} analysed games, {lesson_count} verified lesson, "
        f"{len(archive['puzzle_runs'])} solved puzzle; "
        f"{len(archive['skill_states'])} skills, {len(archive['review_schedules'])} schedules"
    )
    for path in sorted(output.iterdir()):
        print(f"{path.name}: {path.stat().st_size} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("output", type=Path, help="New output directory (never overwritten)")
    parser.add_argument(
        "--stockfish", type=Path, default=Path(shutil.which("stockfish") or "stockfish")
    )
    args = parser.parse_args()
    generate(args.checkout.resolve(), args.output.resolve(), args.stockfish.resolve())


if __name__ == "__main__":
    main()
