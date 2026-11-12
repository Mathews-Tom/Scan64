import math

import pytest

from scan64.content.puzzle_practice_models import elo_after


def test_elo_examples_and_full_precision() -> None:
    assert elo_after(1200, 1200, 1) == 1216
    assert elo_after(1200, 1200, 0) == 1184
    assert elo_after(1200, 1600, 1) == pytest.approx(1229.090909090909)
    assert elo_after(1200, 1600, 0) == pytest.approx(1197.090909090909)
    second = elo_after(1216, 1200, 0)
    assert second == pytest.approx(1199.263693206478)
    assert second != round(second)
    assert math.isfinite(elo_after(-1e308, 1e308, 0))
