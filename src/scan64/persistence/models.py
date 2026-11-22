from __future__ import annotations


def load_models() -> None:
    from scan64.api import middleware, models
    from scan64.chess.analysis import credit_models
    from scan64.chess.analysis import models as analysis_models
    from scan64.chess.games import assistance, clock_models
    from scan64.chess.games import models as game_models
    from scan64.chess.positions import models as position_models
    from scan64.coach import models as coach_models
    from scan64.content import models as content_models
    from scan64.content import puzzle_mode_models, puzzle_practice_models, puzzle_runs
    from scan64.explanations import settings as explanation_settings
    from scan64.learning import habit_models, sparring
    from scan64.learning.evaluation import transfer_measurement
    from scan64.learning.evidence import models as evidence_models
    from scan64.learning.exercises import transfer
    from scan64.learning.profiling import models as profiling_models
    from scan64.learning.scheduling import spaced_repetition

    _ = (
        middleware,
        models,
        credit_models,
        analysis_models,
        assistance,
        clock_models,
        game_models,
        position_models,
        coach_models,
        content_models,
        puzzle_runs,
        puzzle_mode_models,
        puzzle_practice_models,
        explanation_settings,
        habit_models,
        sparring,
        transfer_measurement,
        evidence_models,
        transfer,
        profiling_models,
        spaced_repetition,
    )
