"""Programmatic API for GF-3 water extraction."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import asdict
from pathlib import Path

from .config import WaterExtractionConfig
from .pipeline import run_from_args


def run_water_extraction(config: WaterExtractionConfig) -> int:
    """Run the processor from a typed config object.

    The return value matches the CLI process exit code. Outputs are written under
    ``config.out_dir`` and optional vector output paths.
    """
    data = asdict(config)
    for key, value in list(data.items()):
        if isinstance(value, Path):
            data[key] = value
        elif isinstance(value, list):
            data[key] = [Path(item) for item in value]
    return run_from_args(Namespace(**data))
