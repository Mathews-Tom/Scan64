from __future__ import annotations

from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from scan64.api.repertoire_body import (
    MAX_ATTEMPT_BODY_BYTES,
    MAX_IMPORT_BODY_BYTES,
    RepertoireBodyLimitMiddleware,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,limit",
    [
        ("/v1/repertoires", MAX_IMPORT_BODY_BYTES),
        ("/v1/repertoires/owned-id/attempts", MAX_ATTEMPT_BODY_BYTES),
    ],
)
@pytest.mark.parametrize("root_path", ["", "/mounted"])
async def test_chunked_oversize_stops_receiving_before_decoder_or_endpoint(
    path: str,
    limit: int,
    root_path: str,
) -> None:
    called = False
    consumed = 0
    chunks = iter([b"x" * (limit - 1), b"xx", b"never-read"])
    sent: list[Message] = []

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    async def receive() -> Message:
        nonlocal consumed
        consumed += 1
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    async def send(message: Message) -> None:
        sent.append(message)

    # Content-Length cannot defeat the streaming counter, even if a proxy
    # supplies a contradictory header on an otherwise chunked ASGI request.
    scope: Any = {
        "type": "http",
        "method": "POST",
        "path": root_path + path,
        "root_path": root_path,
        "headers": [(b"transfer-encoding", b"chunked"), (b"content-length", b"1")],
    }
    await RepertoireBodyLimitMiddleware(endpoint)(scope, receive, send)
    assert consumed == 2
    assert not called
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_valid_chunked_json_is_delivered_unchanged_and_once() -> None:
    raw = b'{"pgn":"\\u0031. \\u00654 *"}'
    chunks = iter([raw[:8], raw[8:]])
    delivered: list[Message] = []

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        delivered.append(await receive())
        delivered.append(await receive())

    async def receive() -> Message:
        try:
            chunk = next(chunks)
        except StopIteration:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunk, "more_body": chunk == raw[:8]}

    async def send(message: Message) -> None:
        raise AssertionError("Successful middleware should not send its own response")

    scope: Any = {"type": "http", "method": "POST", "path": "/v1/repertoires", "headers": []}
    await RepertoireBodyLimitMiddleware(endpoint)(scope, receive, send)
    assert delivered == [
        {"type": "http.request", "body": raw, "more_body": False},
        {"type": "http.disconnect"},
    ]
