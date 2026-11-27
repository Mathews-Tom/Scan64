"""Read-only, loopback-only process diagnostics (#366).

``GET /v1/diagnostics/narrator`` reports the narrator's attempts, accepted
generations and template fallbacks by reason code, per pipeline, since process
start. It holds counts only: no evidence, prompt, model output or player data.
"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from scan64.explanations.assembly import NarratorCounts, narrator_counts

router = APIRouter(prefix="/v1/diagnostics", tags=["diagnostics"])


class NarratorPipelineCounts(BaseModel):
    attempts: int
    accepted: int
    fallbacks: int
    # Fallbacks per attempt; null before the first attempt.
    fallback_rate: float | None
    fallbacks_by_reason: dict[str, int]


class NarratorDiagnosticsRead(BaseModel):
    lesson: NarratorPipelineCounts
    board: NarratorPipelineCounts


def _pipeline(counts: NarratorCounts) -> NarratorPipelineCounts:
    return NarratorPipelineCounts(
        attempts=counts.attempts,
        accepted=counts.accepted,
        fallbacks=counts.fallback_total,
        fallback_rate=counts.fallback_rate,
        fallbacks_by_reason=dict(sorted(counts.fallbacks.items())),
    )


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client is not None else None
    try:
        loopback = host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        raise HTTPException(
            status_code=403, detail="Diagnostics are available only on this computer"
        )


@router.get("/narrator", response_model=NarratorDiagnosticsRead)
def get_narrator_diagnostics(request: Request, response: Response) -> NarratorDiagnosticsRead:
    _require_loopback(request)
    response.headers["Cache-Control"] = "no-store"
    counts = narrator_counts()
    return NarratorDiagnosticsRead(
        lesson=_pipeline(counts["lesson"]), board=_pipeline(counts["board"])
    )
