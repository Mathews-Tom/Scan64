"""The desktop shell's outer security boundary; browser installs do not use it."""

import hmac

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SHELL_TOKEN_HEADER = "X-Scan64-Shell-Token"
SHELL_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


class ShellTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("SCAN64_SHELL_TOKEN must not be empty")
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        tokens = [
            value for name, value in scope["headers"] if name.lower() == b"x-scan64-shell-token"
        ]
        # Reject ambiguous duplicate headers, and compare bytes so arbitrary input
        # cannot cause compare_digest's non-ASCII string exception.
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self.token):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse({"detail": "Invalid shell token"}, status_code=401)
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def add_desktop_security(app: FastAPI, token: str | None) -> None:
    if token is None:
        return
    if not token:
        raise ValueError("SCAN64_SHELL_TOKEN must not be empty")
    app.add_middleware(ShellTokenMiddleware, token=token)
    # CORS must be outside the token gate: browsers do not send the secret in
    # preflights, and even a 401 must carry CORS headers for an allowed origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(SHELL_ORIGINS),
        allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            SHELL_TOKEN_HEADER,
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "client_move_id",
        ],
        allow_credentials=False,
    )
