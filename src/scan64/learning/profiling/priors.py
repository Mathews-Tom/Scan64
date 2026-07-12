# PROVISIONAL: These population priors are empirical-Bayes seeds and are
# subject to tuning once real usage data is collected.
# Maps rating band (e.g. "0-1000", "1000-1500", "1500-2000", "2000+")
# to a prior (alpha, beta) for a default skill.
POPULATION_PRIORS: dict[str, tuple[float, float]] = {
    "0-1000": (1.0, 4.0),
    "1000-1500": (2.0, 3.0),
    "1500-2000": (3.0, 2.0),
    "2000+": (4.0, 1.0),
}


def get_prior_for_rating(rating: int) -> tuple[float, float]:
    """Return the provisional prior (alpha, beta) for a given rating."""
    if rating < 1000:
        return POPULATION_PRIORS["0-1000"]
    elif rating < 1500:
        return POPULATION_PRIORS["1000-1500"]
    elif rating < 2000:
        return POPULATION_PRIORS["1500-2000"]
    else:
        return POPULATION_PRIORS["2000+"]
