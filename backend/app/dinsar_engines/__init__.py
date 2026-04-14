"""D-InSAR 多引擎包。

使用方式：
    from app.dinsar_engines import registry
    engine = registry.get_engine("sarscape")
    avail  = engine.check_available()
"""
from . import registry
from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult

__all__ = [
    "registry",
    "DinsarEngine",
    "EngineAvailability",
    "EngineProfile",
    "RunRequest",
    "RunResult",
]
