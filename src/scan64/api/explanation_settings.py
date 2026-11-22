"""Manage the authenticated owner's explicit consent to the local Ollama service."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from scan64.api.auth import require_player_token
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.explanations.settings import OwnerExplanationSettings, operator_manages_explanations
from scan64.persistence.database import get_session
from scan64.providers.llm.local_ollama import detect_local_ollama

router = APIRouter(prefix="/v1/players/{player_id}/explanation-settings", tags=["settings"])
_MAX_UPDATE_BYTES = 1024


class ExplanationSettingsRead(BaseModel):
    mode: Literal["disabled", "local", "operator"]
    model: str | None = None


class ExplanationProbeRead(BaseModel):
    available: bool
    models: list[str]


class ExplanationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: str | None = Field(min_length=1, max_length=128)
    consent: bool


def _require_settings_control() -> None:
    if operator_manages_explanations():
        raise HTTPException(
            status_code=409, detail="Explanation wording is managed by the operator"
        )


@router.get("", response_model=ExplanationSettingsRead)
def get_explanation_settings(
    player_id: str, request: Request, response: Response, session: Session = Depends(get_session)
) -> ExplanationSettingsRead:
    require_player_token(request, player_id, session)
    response.headers["Cache-Control"] = "no-store"
    if operator_manages_explanations():
        return ExplanationSettingsRead(mode="operator")
    row = session.get(OwnerExplanationSettings, player_id)
    if row is None:
        return ExplanationSettingsRead(mode="disabled")
    return ExplanationSettingsRead(mode="local", model=row.model)


@router.post("/probe", response_model=ExplanationProbeRead)
async def probe_explanation_settings(
    player_id: str, request: Request, response: Response, session: Session = Depends(get_session)
) -> ExplanationProbeRead:
    require_player_token(request, player_id, session)
    _require_settings_control()
    session.rollback()  # Do not hold a database connection while awaiting the local daemon.
    models = await detect_local_ollama()
    response.headers["Cache-Control"] = "no-store"
    return ExplanationProbeRead(available=models is not None, models=models or [])


@router.put("", response_model=ExplanationSettingsRead)
async def set_explanation_settings(
    player_id: str, request: Request, response: Response, session: Session = Depends(get_session)
) -> ExplanationSettingsRead:
    require_player_token(request, player_id, session)
    _require_settings_control()
    session.rollback()
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(status_code=415, detail="Expected JSON explanation settings")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_UPDATE_BYTES:
            raise HTTPException(status_code=413, detail="Explanation settings request is too large")
        body.extend(chunk)
    try:
        update = ExplanationSettingsUpdate.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Invalid explanation settings") from error

    if update.consent != (update.model is not None):
        raise HTTPException(status_code=422, detail="Enabling a model requires explicit consent")
    if update.model is not None:
        models = await detect_local_ollama()
        if models is None:
            raise HTTPException(status_code=503, detail="Local Ollama is unavailable")
        if update.model not in models:
            raise HTTPException(status_code=422, detail="Choose an installed local Ollama model")

    # The probe can outlive the bearer; re-authorize under the owner write fence.
    lock_puzzle_player(player_id, session)
    require_player_token(request, player_id, session)
    row = session.get(OwnerExplanationSettings, player_id)
    if update.model is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(OwnerExplanationSettings(player_id=player_id, model=update.model))
    else:
        row.model = update.model
        row.revision = uuid4().hex
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    if update.model is None:
        return ExplanationSettingsRead(mode="disabled")
    return ExplanationSettingsRead(mode="local", model=update.model)
