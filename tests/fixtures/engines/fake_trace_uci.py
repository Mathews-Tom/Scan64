"""UCI engine double that replays a recorded ``eval`` session.

FAKE_TRACE_STDOUT names a recorded standard-output transcript (``uci``, then
``position``, ``eval`` and ``quit``). On ``uci`` the double prints the
transcript up to and including ``uciok``; on ``eval`` it prints the rest.
FAKE_TRACE_STDERR, when set, names a file printed to standard error on
``eval`` (Laser prints its table there). FAKE_TRACE_HANG set: ``eval`` never
answers. Every command is logged to FAKE_TRACE_LOG.
"""

import os
import sys
import time

transcript = open(os.environ["FAKE_TRACE_STDOUT"], encoding="utf-8").read().splitlines(True)
cut = next(i for i, line in enumerate(transcript) if line.strip() == "uciok") + 1
stderr_path = os.environ.get("FAKE_TRACE_STDERR")

with open(os.environ["FAKE_TRACE_LOG"], "a", encoding="utf-8") as log:
    for raw in sys.stdin:
        line = raw.strip()
        log.write(line + "\n")
        log.flush()
        if line == "uci":
            sys.stdout.write("".join(transcript[:cut]))
            sys.stdout.flush()
        elif line == "eval":
            if os.environ.get("FAKE_TRACE_HANG"):
                time.sleep(60)
            sys.stdout.write("".join(transcript[cut:]))
            sys.stdout.flush()
            if stderr_path:
                sys.stderr.write(open(stderr_path, encoding="utf-8").read())
                sys.stderr.flush()
        elif line == "quit":
            break
