#!/usr/bin/env python3
"""Run MintPy smallbaselineApp with a local workaround for a single-pixel inversion bug."""

from __future__ import annotations

import sys

import numpy as np

import mintpy.ifgram_inversion as ifgram_inversion
from mintpy.cli.smallbaselineApp import main as mintpy_smallbaseline_main


_ORIGINAL_ESTIMATE_TIMESERIES = ifgram_inversion.estimate_timeseries


def _patched_estimate_timeseries(*args, **kwargs):
    ts, inv_quality, num_inv_obs = _ORIGINAL_ESTIMATE_TIMESERIES(*args, **kwargs)

    # MintPy 1.6.2 may return a shape-(1,) inversion quality array for the
    # single-pixel partial-network branch, while the caller expects a scalar.
    if isinstance(inv_quality, np.ndarray) and inv_quality.size == 1:
        inv_quality = np.asarray(inv_quality).reshape(-1)[0].item()

    if isinstance(num_inv_obs, np.ndarray) and num_inv_obs.size == 1:
        num_inv_obs = int(np.asarray(num_inv_obs).reshape(-1)[0])

    return ts, inv_quality, num_inv_obs


def main(argv: list[str] | None = None) -> int:
    ifgram_inversion.estimate_timeseries = _patched_estimate_timeseries
    print("Applied local MintPy estimate_timeseries single-pixel fix.")
    return mintpy_smallbaseline_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
