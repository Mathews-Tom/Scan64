"""Engine-neutral UCI configuration and line handling.

Concrete adapters and pools live in ``scan64.providers.stockfish`` under their
historical names; ``scan64.providers.engine.adapters`` re-exports them under
engine-neutral names.
"""

from scan64.providers.engine.config import (
    ENGINE_CONFIG_ENV,
    STOCKFISH_PATH_ENV,
    EngineConfig,
    EngineConfigurationError,
    EngineProfile,
    SearchLimits,
    active_engine_profile,
    load_engine_profile,
)
from scan64.providers.engine.uci import EngineCapabilityError, line_from_info

__all__ = [
    "ENGINE_CONFIG_ENV",
    "STOCKFISH_PATH_ENV",
    "EngineCapabilityError",
    "EngineConfig",
    "EngineConfigurationError",
    "EngineProfile",
    "SearchLimits",
    "active_engine_profile",
    "line_from_info",
    "load_engine_profile",
]
