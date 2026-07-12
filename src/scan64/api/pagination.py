import base64
import json
from typing import Any

from pydantic import BaseModel


class PaginatedResponse[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        result: dict[str, Any] = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return result
    except Exception:
        return {}
