import json

from fastapi import Request, Response
from sqlmodel import Field, SQLModel
from starlette.middleware.base import BaseHTTPMiddleware


class IdempotencyRecord(SQLModel, table=True):
    idempotency_key: str = Field(primary_key=True)
    status_code: int
    response_body: str
    headers: str


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, get_session_callable):
        super().__init__(app)
        self.get_session = get_session_callable

    async def dispatch(self, request: Request, call_next):
        if request.method not in ["POST", "PUT", "PATCH", "DELETE"]:
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get(
            "client_move_id"
        )
        if not idempotency_key:
            return await call_next(request)

        session_generator = self.get_session()
        session = next(session_generator)

        try:
            record = session.get(IdempotencyRecord, idempotency_key)
            if record:
                headers = json.loads(record.headers)
                return Response(
                    content=record.response_body, status_code=record.status_code, headers=headers
                )

            response = await call_next(request)

            # Read response body
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            # Reconstruct response to return it
            reconstructed_response = Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

            # Only save successful mutations
            if 200 <= response.status_code < 300:
                # Save to DB
                new_record = IdempotencyRecord(
                    idempotency_key=idempotency_key,
                    status_code=response.status_code,
                    response_body=body.decode("utf-8"),
                    headers=json.dumps(dict(response.headers)),
                )
                session.add(new_record)
                session.commit()

            return reconstructed_response

        finally:
            session_generator.close()
