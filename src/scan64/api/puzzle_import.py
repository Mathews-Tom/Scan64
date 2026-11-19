"""Shell-token protected import controls, registered only by the desktop server."""

from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, model_validator
from starlette.types import Receive, Scope, Send

from scan64.puzzle_import_job import ImportJobState, JobAlreadyRunning, PuzzleImportJob

router = APIRouter(prefix="/v1/app/puzzle-import", tags=["app"])


class StartImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["download", "file"]
    path: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "StartImport":
        if (self.source == "file") != (self.path is not None):
            raise ValueError("A file import requires a path; a download must not supply one")
        return self


def _job(request: Request) -> PuzzleImportJob:
    job: PuzzleImportJob | None = getattr(request.app.state, "puzzle_import_job", None)
    if job is None:
        raise HTTPException(status_code=404, detail="Not found")
    return job


@router.get("", response_model=ImportJobState)
def import_state(request: Request) -> ImportJobState:
    return _job(request).state()


@router.get("/events")
async def import_events(request: Request) -> StreamingResponse:
    state, listener = _job(request).subscribe()

    async def events() -> AsyncIterator[str]:
        try:
            current = state
            while True:
                yield current.model_dump_json() + "\n"
                if current.status not in {"running", "cancelling"}:
                    return
                while (update := await listener.next_state()) is not None:
                    if update.revision > current.revision:
                        current = update
                        break
                else:
                    return
        finally:
            listener.close()

    class ImportEventStream(StreamingResponse):
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            try:
                await super().__call__(scope, receive, send)
            finally:
                listener.close()

    return ImportEventStream(
        events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
    )


@router.post("", response_model=ImportJobState, status_code=202)
def start_import(body: StartImport, request: Request) -> ImportJobState:
    try:
        return _job(request).start(body.source, body.path)
    except JobAlreadyRunning as error:
        raise HTTPException(status_code=409, detail="A puzzle import is already running") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("", response_model=ImportJobState)
def cancel_import(request: Request) -> ImportJobState:
    return _job(request).cancel()
