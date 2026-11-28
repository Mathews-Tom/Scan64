"""The blind set with the extended rubric, and the join of its judgements (#403).

The rubric is the one of #458 (``voice/blind.py``) plus three fields. The judge sees only the
position, the facts and the comment; the key stays outside the judge's folder.
"""

import random
from typing import Any

from benchmarks.narrator.voice import blind
from benchmarks.narrator.voice.render import render

# Each field is true or false; null when the item's facts hold nothing to ask about.
EXTRA = ("root_cause_named", "turning_point_named", "order")


def asks(record: dict[str, Any]) -> dict[str, bool]:
    """Which extra fields need a yes or no for this record."""
    theme, turn = record["theme"] is not None, record["turn"] is not None
    return {"root_cause_named": theme, "turning_point_named": turn, "order": theme or turn}


def build(records: list[dict[str, Any]], seed: int) -> tuple[list[dict], dict[str, dict]]:
    rows = [r for r in records if r["error"] is None]
    random.Random(seed).shuffle(rows)
    items, key = [], {}
    for i, row in enumerate(rows, 1):
        item_id = f"q{i:03d}"
        items.append(
            {
                "id": item_id,
                "position": render(row["fen"]),
                "facts": [f"{role}: {text}" for role, text in row["facts"]],
                "comment": row["text"],
            }
        )
        key[item_id] = {k: row[k] for k in ("model", "style", "test", "n")} | {"asks": asks(row)}
    return items, key


def join(key: dict[str, dict], judgements: list[dict]) -> list[dict]:
    """``blind.join`` (ids and the #458 fields) plus the extra fields."""
    joined = blind.join(key, judgements)
    for row in joined:
        for name in EXTRA:
            value = row.get(name)
            if row["asks"][name]:
                if not isinstance(value, bool):
                    raise ValueError(f"{row['id']}: {name} must be true or false, not {value!r}")
            elif value is not None:
                raise ValueError(f"{row['id']}: {name} must be null, not {value!r}")
    return joined
