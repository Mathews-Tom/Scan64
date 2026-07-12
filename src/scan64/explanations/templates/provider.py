from scan64.lessonspec.models import Diagnosis, Explanation


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
            )
        }

    async def explain(self, diagnosis: Diagnosis) -> Explanation:
        """
        Provide a template-based explanation for a given diagnosis.
        """
        # Fallback template if not found
        text = self.templates.get(
            diagnosis.primary,
            "An error occurred. Always scan for forcing moves before continuing."
        )
        return Explanation(text=text, visualizations=[])
