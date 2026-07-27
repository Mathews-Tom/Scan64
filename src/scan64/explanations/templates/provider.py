from __future__ import annotations

from collections.abc import Sequence

from chess_lesson_spec import Diagnosis, Explanation

from scan64.learning.evidence.models import Evidence


class TemplateExplanationProvider:
    def __init__(self) -> None:
        self.templates = {
            "tactics.knight_fork": (
                "Your move allowed a knight fork. Before continuing your own plan, "
                "inspect forcing moves like checks and captures."
            ),
            "opponent_threats.forcing_moves.knight_fork": (
                "Your move allowed a knight fork. Before continuing your own plan, "
                "inspect forcing moves like checks and captures."
            ),
        }

    async def explain(self, diagnosis: Diagnosis, evidence: Sequence[Evidence]) -> Explanation:
        """
        Provide a template-based explanation for a given diagnosis.

        ``evidence`` is threaded through the interface so per-code templates can
        interpolate M33's provenance-bearing evidence payloads; this milestone's
        conformance test (``tests/conformance/test_explanation_coverage.py``)
        drives it against every seeded taxonomy code.
        """
        # Fallback template if not found
        text = self.templates.get(
            diagnosis.primary, "An error occurred. Always scan for forcing moves before continuing."
        )
        return Explanation(text=text, visualizations=[])
