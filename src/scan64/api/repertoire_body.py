"""Bound new repertoire JSON before Starlette/FastAPI buffers or decodes it."""

from __future__ import annotations

import re

from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scan64.learning.repertoire import MAX_PGN_BYTES

# An ASCII PGN byte may be represented as six JSON bytes (\uXXXX).
# Leave room for the envelope without restricting valid escaped PGN strings.
MAX_IMPORT_BODY_BYTES = 6 * MAX_PGN_BYTES + 1024
MAX_ATTEMPT_BODY_BYTES = 4096


class RepertoireBodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        path = get_route_path(scope).rstrip("/")
        if path == "/v1/repertoires":
            limit = MAX_IMPORT_BODY_BYTES
        elif re.fullmatch(r"/v1/repertoires/[^/]+/attempts", path):
            limit = MAX_ATTEMPT_BODY_BYTES
        else:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            # Check before copying the chunk; no trust in Content-Length.
            if len(chunk) > limit - len(body):
                response = JSONResponse(
                    {"detail": "Repertoire request body exceeds the supported byte limit"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        buffered: bytes | None = bytes(body)
        del body

        async def bounded_receive() -> Message:
            nonlocal buffered
            if buffered is not None:
                data, buffered = buffered, None
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
