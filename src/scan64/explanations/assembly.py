"""Resolve the client-visible explanation for a diagnosis.

The template floor (``explanations/templates/provider.py``) is always
computed and is the default explanation. When an operator has configured an
LLM provider (``SCAN64_LLM_CONFIG``, off by default), its output is routed
through ``explanations/validator.py`` and only trusted if every claim is
grounded in the diagnosis's own evidence; a rejected (ungrounded) generation
falls back to the template rather than surfacing an error or a partial
explanation. Rejection is expected behaviour, not a failure to suppress: each
fallback is logged with its reason code (never evidence contents) and counted
in-process, so the fallback-to-template rate is visible via
``template_fallback_counts``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import httpx
from chess_lesson_spec import Diagnosis, Explanation

from scan64.explanations.settings import OPERATOR_LLM_CONFIG
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    RejectionReason,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, LLMMessage, LLMProviderError
from scan64.providers.llm.adapters import LLMExplanationProvider
from scan64.providers.llm.config import LLMProviderConfig, create_llm_provider

logger = logging.getLogger(__name__)

TemplateFallbackReason = RejectionReason | Literal["provider_error"]

_template_fallbacks: Counter[str] = Counter()
_template_fallbacks_lock = threading.Lock()


def _record_template_fallback(reason: TemplateFallbackReason) -> None:
    with _template_fallbacks_lock:
        _template_fallbacks[reason] += 1
    logger.info(
        "Explanation generation fell back to the template: reason=%s",
        reason,
        extra={"event": "explanation_template_fallback", "reason": reason},
    )


def template_fallback_counts() -> dict[str, int]:
    """Template fallbacks by reason code since process start."""
    with _template_fallbacks_lock:
        return dict(_template_fallbacks)


# The explanation is displayed immediately, not gated behind a progressive
# hint ladder, so any claim's disclosure_level is acceptable.
_FULL_DISCLOSURE_HINT_LEVEL = 3

# Evidence payload fields M33's composer populates with a single verified UCI
# move (see learning/evidence/composer.py).
_SINGLE_MOVE_FIELDS = ("best_move", "threat_move", "pinning_move", "incoming_threat")


def _verified_lines_for(
    evidence: Sequence[Evidence],
) -> dict[str, tuple[tuple[str, ...], ...]]:
    lines: dict[str, tuple[tuple[str, ...], ...]] = {}
    for item in evidence:
        payload: dict[str, Any] = item.payload
        candidates: list[tuple[str, ...]] = []
        focused_line = payload.get("focused_line")
        if isinstance(focused_line, list) and focused_line:
            candidates.append(tuple(focused_line))
        for field in _SINGLE_MOVE_FIELDS:
            move = payload.get(field)
            if isinstance(move, str) and move:
                candidates.append((move,))
        if candidates:
            lines[item.evidence_id] = tuple(candidates)
    return lines


def _grounding_context(
    diagnosis: Diagnosis, evidence: Sequence[Evidence], fen: str
) -> GroundedExplanationContext:
    referenced = tuple(item for item in evidence if item.evidence_id in diagnosis.evidence_refs)
    return GroundedExplanationContext(
        fen=fen,
        evidence=referenced,
        verified_lines=_verified_lines_for(referenced),
        requested_hint_level=_FULL_DISCLOSURE_HINT_LEVEL,
    )


def _explanation_request(evidence: Sequence[Evidence]) -> ExplanationRequest:
    evidence_summary = "\n".join(
        f"- evidence_id={item.evidence_id!r} kind={item.kind!r} "
        f"claim={item.claim!r} payload={item.payload!r}"
        for item in evidence
    )
    return ExplanationRequest(
        messages=(
            LLMMessage(
                role="system",
                content=(
                    "You explain a chess mistake to a learner. State only facts "
                    "present in the supplied evidence: every claim must cite an "
                    "evidence_id from the list below, name only squares, pieces, "
                    "or moves that appear in that evidence's payload, and any "
                    "move sequence you cite must be one of that evidence's "
                    "verified lines. Never invent a fact absent from the "
                    "evidence."
                ),
            ),
            LLMMessage(role="user", content=f"Verified evidence:\n{evidence_summary}"),
        )
    )


async def _attempt_grounded_generation(
    provider: LLMExplanationProvider,
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    template_explanation: Explanation,
) -> Explanation:
    context = _grounding_context(diagnosis, evidence, fen)
    request = _explanation_request(context.evidence)
    try:
        generated = await provider.generate(request)
        return validate_generated_explanation(generated, context)
    except GroundedExplanationValidationError as error:
        _record_template_fallback(error.reason)
        return template_explanation
    except LLMProviderError:
        _record_template_fallback("provider_error")
        return template_explanation


async def resolve_explanation(
    diagnosis: Diagnosis,
    evidence: Sequence[Evidence],
    fen: str,
    *,
    local_provider: LLMExplanationProvider | None = None,
    environment: Mapping[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Explanation:
    """Resolve the client-visible explanation for a diagnosis.

    Always computes the deterministic template explanation first -- it is the
    floor every diagnosis falls back to. Explicit operator configuration takes
    precedence over the owner's installation-local model. Both are subject to
    the same grounding check; only a grounded response replaces the template.

    ``client`` lets a caller (tests, or a host that already owns a shared
    client) supply one; when omitted, a client is opened and closed for the
    duration of this call.
    """
    template_explanation = await TemplateExplanationProvider().explain(diagnosis, evidence)

    environment_values = os.environ if environment is None else environment
    raw_path = environment_values.get(OPERATOR_LLM_CONFIG)
    if raw_path is None:
        if local_provider is not None:
            return await _attempt_grounded_generation(
                local_provider,
                diagnosis,
                evidence,
                fen,
                template_explanation,
            )
        return template_explanation

    config = LLMProviderConfig.from_toml(Path(raw_path))
    if config.provider == "template":
        return template_explanation

    if client is not None:
        provider = create_llm_provider(config, client=client, environment=environment_values)
        if provider is None:
            return template_explanation
        return await _attempt_grounded_generation(
            provider, diagnosis, evidence, fen, template_explanation
        )

    async with httpx.AsyncClient() as owned_client:
        provider = create_llm_provider(config, client=owned_client, environment=environment_values)
        if provider is None:
            return template_explanation
        return await _attempt_grounded_generation(
            provider, diagnosis, evidence, fen, template_explanation
        )
