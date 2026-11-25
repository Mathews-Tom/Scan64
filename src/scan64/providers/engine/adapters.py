"""Engine-neutral names for the UCI adapters and pools."""

from scan64.providers.stockfish.adapter import (
    EngineAdapter,
    IdentifiedEngineAdapter,
    UciEngineAdapter,
)
from scan64.providers.stockfish.pool import (
    EnginePool,
    EnginePoolManager,
    PoolBoundAdapter,
    PooledUciEngineAdapter,
)

__all__ = [
    "EngineAdapter",
    "EnginePool",
    "EnginePoolManager",
    "IdentifiedEngineAdapter",
    "PoolBoundAdapter",
    "PooledUciEngineAdapter",
    "UciEngineAdapter",
]
