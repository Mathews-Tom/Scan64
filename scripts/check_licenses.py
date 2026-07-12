#!/usr/bin/env python3
import os
import sys

REQUIRED_ENTRIES = ["python-chess", "Stockfish", "Chessground"]


def check_licenses() -> int:
    notices_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "THIRD_PARTY_NOTICES.md"
    )

    if not os.path.exists(notices_path):
        print(f"Error: {notices_path} not found.")
        return 1

    with open(notices_path, encoding="utf-8") as f:
        content = f.read()

    missing = []
    for entry in REQUIRED_ENTRIES:
        if entry not in content:
            missing.append(entry)

    if missing:
        print("Error: Missing required third-party notices for:")
        for m in missing:
            print(f"  - {m}")
        return 1

    print("License check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(check_licenses())
