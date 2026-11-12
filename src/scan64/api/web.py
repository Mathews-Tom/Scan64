"""Serve the optional built client without taking over the API namespace."""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.routing import Match, Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

RESERVED_PATHS = frozenset({"v1", "health", "docs", "openapi.json", "redoc"})


def web_dist_directory() -> Path:
    configured = os.environ.get("SCAN64_WEB_DIST")
    if configured is not None:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "apps" / "scan64-web" / "dist"


class WebMount(Mount):
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        # Decline the match entirely so API method errors (405) survive too.
        if scope["path"].lstrip("/").split("/", 1)[0] in RESERVED_PATHS:
            return Match.NONE, {}
        return super().matches(scope)


def mount_web_client(app: FastAPI, directory: Path) -> None:
    app.router.routes.append(WebMount("/", app=SPAStaticFiles(directory=directory), name="web"))


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        if path.split("/", 1)[0] in RESERVED_PATHS:
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            # Missing assets must remain 404s, not HTML served as JS or CSS.
            if error.status_code != 404 or path.startswith("assets/") or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)
