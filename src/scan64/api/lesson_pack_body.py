"""Bound pack JSON before FastAPI or Starlette buffers and parses it."""

from __future__ import annotations

import re

from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scan64.lesson_packs.schema import MAX_PACK_BYTES


class LessonPackBodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        path = get_route_path(scope).rstrip("/")
        if path == "/v1/lesson-packs":
            limit = MAX_PACK_BYTES
        elif re.fullmatch(r"/v1/lesson-packs/[^/]+/lessons/[^/]+/check", path):
            limit = 4096
        else:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(chunk) > limit - len(body):
                await JSONResponse(
                    {"detail": "Lesson pack request exceeds the supported byte limit"},
                    status_code=413,
                )(scope, receive, send)
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
