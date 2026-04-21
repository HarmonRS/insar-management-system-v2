"""Abstract base types for D-InSAR engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class EngineProfile:
    """Describes a runnable engine profile."""

    code: str
    label: str
    description: str = ""
    params_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineAvailability:
    """Represents an engine availability check result."""

    engine_code: str
    status: str
    available: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""


@dataclass
class RunRequest:
    """Normalized D-InSAR run request passed to an engine."""

    engine_code: str
    profile: str
    root_dir: str
    job_id: str
    num_to_process: int = 0
    timeout_seconds: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None


@dataclass
class RunResult:
    """Normalized D-InSAR run result returned by an engine."""

    success: bool
    engine_code: str
    profile: str
    job_id: str
    pairs_processed: int = 0
    pairs_failed: int = 0
    output_dirs: List[str] = field(default_factory=list)
    error: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


class DinsarEngine(ABC):
    """Common interface implemented by all D-InSAR engines."""

    @property
    @abstractmethod
    def engine_code(self) -> str:
        """Returns the stable engine identifier."""

    @property
    @abstractmethod
    def engine_label(self) -> str:
        """Returns the display label shown in the UI."""

    @abstractmethod
    def check_available(self) -> EngineAvailability:
        """Runs engine-specific availability checks."""

    @abstractmethod
    def get_profiles(self) -> List[EngineProfile]:
        """Returns supported production profiles."""

    @abstractmethod
    def run(self, request: RunRequest) -> RunResult:
        """Executes a production run synchronously."""

    @property
    def default_timeout_seconds(self) -> Optional[int]:
        """Returns the engine's default timeout when the caller leaves it empty."""

        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the engine definition for API responses."""

        return {
            "engine_code": self.engine_code,
            "engine_label": self.engine_label,
            "default_timeout_seconds": self.default_timeout_seconds,
            "profiles": [
                {
                    "code": profile.code,
                    "label": profile.label,
                    "description": profile.description,
                    "params_schema": profile.params_schema,
                }
                for profile in self.get_profiles()
            ],
        }
