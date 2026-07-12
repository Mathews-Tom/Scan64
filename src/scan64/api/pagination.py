import base64
import json
from typing import Generic, TypeVar

from pydantic import BaseModel

class PaginatedResponse[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return {}
