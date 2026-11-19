"""Drive an actual ASGI stream without HTTPX's response-body buffering."""

import asyncio

from starlette.types import ASGIApp, Message, Scope


async def disconnect_stream(
    app: ASGIApp, path: str, headers: dict[str, str], *, before_body: bool = False
) -> list[Message]:
    messages: list[Message] = []
    disconnected = asyncio.Event()
    requested = False

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)
        if before_body and message["type"] == "http.response.start":
            # Model cancellation while the header write is in flight: the
            # response body generator has not started and cannot clean itself.
            raise asyncio.CancelledError()
        if message["type"] == "http.response.body" and message.get("body"):
            disconnected.set()

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), 5)
    except asyncio.CancelledError:
        if not before_body:
            raise
    return messages
