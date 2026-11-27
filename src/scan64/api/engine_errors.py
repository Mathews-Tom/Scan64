"""One response for every feature the active engine profile cannot serve.

``EngineCapabilityError`` means the configured engine lacks an option or a
protocol feature (MultiPV, Skill Level, more than one line over XBoard).
Retrying never helps, and Scan64 never substitutes a weaker search, so every
route answers 503 with a stable code and the engine's own reason.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from scan64.providers.engine import EngineCapabilityError

ENGINE_CAPABILITY_UNAVAILABLE = "engine_capability_unavailable"


async def engine_capability_response(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, EngineCapabilityError)
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": ENGINE_CAPABILITY_UNAVAILABLE,
                "message": "This feature is unavailable under the active engine profile.",
                "reason": str(error),
            }
        },
    )


def add_engine_capability_handler(app: FastAPI) -> None:
    app.add_exception_handler(EngineCapabilityError, engine_capability_response)
