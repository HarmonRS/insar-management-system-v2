"""Registry for D-InSAR engine instances."""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import DinsarEngine

_registry: Dict[str, DinsarEngine] = {}


def register(engine: DinsarEngine) -> None:
    """Registers or replaces an engine instance."""

    _registry[engine.engine_code] = engine


def get_engine(engine_code: str) -> Optional[DinsarEngine]:
    """Returns a registered engine instance by code."""

    return _registry.get(engine_code)


def list_engines() -> List[DinsarEngine]:
    """Returns all registered engines in registration order."""

    return list(_registry.values())


def _bootstrap() -> None:
    """Imports and registers all built-in engines."""

    from .landsar_engine import LandsarEngine
    from .pyint_engine import PyintEngine
    from .sarscape_engine import SarscapeEngine

    for engine in (SarscapeEngine(), PyintEngine(), LandsarEngine()):
        register(engine)


_bootstrap()
