import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class PaginatedResponse[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        result = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Invalid cursor") from error
    if not isinstance(result, dict):
        raise ValueError("Invalid cursor")
    return result


def decode_timestamp_uuid_cursor(cursor: str) -> tuple[datetime, UUID]:
    data = decode_cursor(cursor)
    created_at = data.get("created_at")
    item_id = data.get("id")
    if not isinstance(created_at, str) or not isinstance(item_id, str):
        raise ValueError("Invalid cursor")
    try:
        return datetime.fromisoformat(created_at), UUID(item_id)
    except ValueError as error:
        raise ValueError("Invalid cursor") from error
