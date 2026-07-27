from datetime import UTC, datetime

from scan64.content.models import ContentAttempt, ContentItem
from scan64.learning.profiling.profile_update import apply_content_attempt


def test_rating_bands_seed_distinct_nonuniform_priors() -> None:
    item = ContentItem(
        id="lesson",
        domain="tactics",
        provenance="test",
        licence="CC0",
        skill_mapping={"tactics.fork": 1.0},
    )
    observed_at = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    lower = apply_content_attempt(
        ContentAttempt(
            item_id=item.id,
            player_id="lower-rated",
            success=False,
            completed_at=observed_at,
        ),
        item,
        [],
        rating=1200,
    )[0]
    higher = apply_content_attempt(
        ContentAttempt(
            item_id=item.id,
            player_id="higher-rated",
            success=False,
            completed_at=observed_at,
        ),
        item,
        [],
        rating=1900,
    )[0]

    assert (lower.prior_alpha, lower.prior_beta) == (2.0, 3.0)
    assert (higher.prior_alpha, higher.prior_beta) == (3.0, 2.0)
    assert (lower.prior_alpha, lower.prior_beta) != (1.0, 1.0)
    assert (higher.prior_alpha, higher.prior_beta) != (1.0, 1.0)
    assert lower.expected_mastery < higher.expected_mastery
