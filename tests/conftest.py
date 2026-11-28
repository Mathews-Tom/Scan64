"""Fixtures shared by every test directory."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from scan64.providers.engine.config import ENGINE_CONFIG_ENV, STOCKFISH_PATH_ENV

# The developer's engine selection. Without it every test starts from the
# default engine profile: Stockfish on PATH. A test that needs a profile or a
# Stockfish path sets it with monkeypatch. SCAN64_TEST_SECOND_ENGINE is a test
# input, not engine configuration, and stays.
DEVELOPER_ENGINE_ENV = (ENGINE_CONFIG_ENV, STOCKFISH_PATH_ENV)


@pytest.fixture(autouse=True)
def _no_developer_engine_config() -> Iterator[None]:
    # Its own MonkeyPatch, not the ``monkeypatch`` fixture: requesting that here
    # would set it up before every other fixture and so undo a test's patches
    # only after those fixtures' teardown (an app lifespan would then shut down
    # with a test's fake engine pool still installed).
    with pytest.MonkeyPatch.context() as patch:
        for name in DEVELOPER_ENGINE_ENV:
            patch.delenv(name, raising=False)
        yield
