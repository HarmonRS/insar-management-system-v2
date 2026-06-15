"""Backward-compatible processor facade.

New code should import from ``gf3_water.pipeline`` or ``gf3_water.cli``. This
module remains so existing scripts and integrations that import
``gf3_water.processor`` continue to work.
"""

from __future__ import annotations

from .cli import build_arg_parser, main
from .pipeline import run_from_args

__all__ = ["build_arg_parser", "main", "run_from_args"]

