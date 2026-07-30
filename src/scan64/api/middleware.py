import hashlib
import json
from collections.abc import Awaitable, Callable, Generator
from typing import cast

from fastapi import Request, Response
from sqlmodel import Field, Session, SQLModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp


class IdempotencyRecord(SQLModel, table=True):
    idempotency_key: str = Field(primary_key=True)
    status_code: int
    response_body: str
    headers: str


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        get_session_callable: Callable[[], Generator[Session, None, None]],
    ) -> None:
        super().__init__(app)
        self.get_session = get_session_callable

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in ["POST", "PUT", "PATCH", "DELETE"]:
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get(
            "client_move_id"
        )
        if not idempotency_key:
            return await call_next(request)

        authorization = request.headers.get("Authorization")
        if authorization is None:
            return await call_next(request)

        principal = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
        record_key = f"{request.method}:{request.url.path}:{principal}:{idempotency_key}"

        session_generator = self.get_session()
        session = next(session_generator)

        try:
            record = session.get(IdempotencyRecord, record_key)
            if record:
                headers = json.loads(record.headers)
                return Response(
                    content=record.response_body, status_code=record.status_code, headers=headers
                )

            response = await call_next(request)

            # BaseHTTPMiddleware's call_next is typed as returning Response, but at
            # runtime it always returns a StreamingResponse; cast to access body_iterator.
            streaming_response = cast(StreamingResponse, response)
            body = b""
            async for chunk in streaming_response.body_iterator:
                if isinstance(chunk, str):
                    body += chunk.encode("utf-8")
                else:
                    body += bytes(chunk)

            skip_idempotency_cache = bool(
                getattr(request.state, "skip_idempotency_cache", False)
            )

            # Reconstruct response to return it.
            reconstructed_response = Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

            if 200 <= response.status_code < 300 and not skip_idempotency_cache:
                new_record = IdempotencyRecord(
                    idempotency_key=record_key,
                    status_code=response.status_code,
                    response_body=body.decode("utf-8"),
                    headers=json.dumps(dict(response.headers)),
                )
                session.add(new_record)
                session.commit()

            return reconstructed_response

        finally:
            session_generator.close()
