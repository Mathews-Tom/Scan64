from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import cast

from fastapi import HTTPException, Request, Response
from sqlmodel import Field, Session, SQLModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp

from scan64.api.auth import require_authenticated_player
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.learning.progress import progress_generation

LEARNING_CACHE_PREFIXES = ("/v1/puzzles", "/v1/learning", "/v1/content")
LearningGeneration = tuple[str, str | None]


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

    @contextmanager
    def _cache_session(self) -> Generator[Session, None, None]:
        session_generator = self.get_session()
        session = next(session_generator)
        try:
            yield session
        finally:
            # Never retain a read transaction or an owner lock during call_next,
            # response streaming, or any engine await in the endpoint.
            session.rollback()
            session_generator.close()

    def _learning_generation(self, request: Request, session: Session) -> LearningGeneration | None:
        path = request.url.path
        if not any(
            path == prefix or path.startswith(f"{prefix}/") for prefix in LEARNING_CACHE_PREFIXES
        ):
            return None
        player_id = require_authenticated_player(request, session)
        # Cache lookup and publication serialize with the reset transaction.
        lock_puzzle_player(player_id, session)
        return player_id, progress_generation(player_id, session)

    @staticmethod
    def _generation_key(record_key: str, generation: LearningGeneration | None) -> str:
        if generation is None or generation[1] is None:
            return record_key
        return f"{record_key}:progress:{generation[1]}"

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

        try:
            with self._cache_session() as session:
                generation = self._learning_generation(request, session)
                record = session.get(
                    IdempotencyRecord, self._generation_key(record_key, generation)
                )
                cached = (
                    Response(
                        content=record.response_body,
                        status_code=record.status_code,
                        headers=json.loads(record.headers),
                    )
                    if record is not None
                    else None
                )
        except HTTPException:
            # Let the endpoint retain its own authentication error contract;
            # an invalid/revoked principal must never receive a cached success.
            return await call_next(request)
        if cached is not None:
            return cached

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

        skip_idempotency_cache = bool(getattr(request.state, "skip_idempotency_cache", False))
        reconstructed_response = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

        if 200 <= response.status_code < 300 and not skip_idempotency_cache:
            try:
                with self._cache_session() as session:
                    current_generation = self._learning_generation(request, session)
                    if current_generation == generation:
                        session.add(
                            IdempotencyRecord(
                                idempotency_key=self._generation_key(record_key, generation),
                                status_code=response.status_code,
                                response_body=body.decode("utf-8"),
                                headers=json.dumps(dict(response.headers)),
                            )
                        )
                        session.commit()
            except HTTPException:
                # Account removal during the endpoint likewise forbids caching.
                pass
        return reconstructed_response
