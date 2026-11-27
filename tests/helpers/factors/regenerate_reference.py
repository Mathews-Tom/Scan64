"""Regenerate the guide reference outputs used by the factor parity tests.

The tests compare every ported term with these outputs, so CI needs no Node.

    git clone https://github.com/hxim/Stockfish-Evaluation-Guide /tmp/seg
    git -C /tmp/seg checkout 63ff375bb5a8b7b2e4f965c3a3de7698fef7616c
    uv run python tests/helpers/factors/regenerate_reference.py /tmp/seg/index.html

Keep the guide checkout outside the repository tree. The script runs the
guide's JavaScript only under Node, through seg_harness.cjs, in parallel shards.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HARNESS = Path(__file__).with_name("seg_harness.cjs")
POSITIONS = ROOT / "tests" / "fixtures" / "factors" / "positions.json"
OUT = ROOT / "tests" / "fixtures" / "factors" / "seg_reference.json.gz"
GUIDE_COMMIT = "63ff375bb5a8b7b2e4f965c3a3de7698fef7616c"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("index_html", type=Path, help="guide index.html at the pinned commit")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = parser.parse_args()

    count = len(json.loads(POSITIONS.read_text())["positions"])
    shards = [(i, min(i + 4, count)) for i in range(0, count, 4)]
    with tempfile.TemporaryDirectory() as tmp:

        def run(shard: tuple[int, int]) -> Path:
            out = Path(tmp) / f"{shard[0]:05d}.json"
            subprocess.run(
                [
                    "node",
                    str(HARNESS),
                    str(args.index_html),
                    str(POSITIONS),
                    str(out),
                    str(shard[0]),
                    str(shard[1]),
                ],
                check=True,
                cwd=tmp,
            )
            return out

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            parts = list(pool.map(run, shards))
        merged: dict[str, object] = {"guide_commit": GUIDE_COMMIT, "results": []}
        for part in parts:
            data = json.loads(part.read_text())
            merged["terms"] = data["terms"]
            results = merged["results"]
            assert isinstance(results, list)
            results.extend(data["results"])
    payload = json.dumps(merged, separators=(",", ":"), sort_keys=True).encode()
    OUT.write_bytes(gzip.compress(payload, mtime=0))
    print(f"{count} positions -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
