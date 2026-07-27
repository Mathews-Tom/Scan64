from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.plugins.registry import PluginRegistry

_registry: PluginRegistry | None = None


def initialize_host_registry() -> PluginRegistry:
    global _registry
    if _registry is not None:
        raise RuntimeError("The host plugin registry is already initialized")
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    _registry = registry
    return registry


def get_host_registry() -> PluginRegistry:
    if _registry is None:
        raise RuntimeError("The host plugin registry is not initialized")
    return _registry


def clear_host_registry() -> None:
    global _registry
    _registry = None
