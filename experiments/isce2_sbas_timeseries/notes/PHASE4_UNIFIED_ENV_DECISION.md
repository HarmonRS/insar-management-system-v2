# Phase 4 Unified-Environment Decision

Updated: 2026-04-06

## Decision

Current experiment preference:

- prefer the unified WSL environment for SBAS experiment work

Current production safety rule:

- do not replace or mutate the existing D-InSAR production environment
- keep pair-oriented D-InSAR on the existing WSL `isce2` env
- keep the current backend/public D-InSAR entry unchanged

Current environment split:

- D-InSAR production baseline:
  - `isce2`
- SBAS experiment preferred runtime:
  - `isce2_mintpy_v1`

## Why This Decision Is Reasonable

The unified env has now completed the full current experiment chain:

- MintPy command invocation without the `isce` bridge
- `load_data`
- strict-mask generation
- `modify_network -> velocity`
- publish export to geocoded HDF5, GeoTIFF, preview, and `manifest.json`

This makes the unified env a valid experiment baseline.

At the same time, the existing pair-oriented D-InSAR route is already working and should not be destabilized just to simplify the SBAS experiment runtime.

The safest rule is therefore:

- let SBAS experiments move forward in a separate unified env
- do not touch the current `isce2` production env used by D-InSAR

## Evidence

Successful unified env:

- `/home/administrator/miniconda3/envs/isce2_mintpy_v1`

Successful unified SBAS work directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_unified_v1`

Successful unified publish directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_unified_v1`

Matched quality indicators:

- `maskAllValid`:
  - `1219001 / 4076199`
  - `29.91%`
- `maskTempCoh`:
  - `62987 / 4076199`
  - `1.55%`

Key package difference:

- current `isce2` env does not provide the MintPy-side package set needed for this SBAS route
- current `isce2_mintpy_v1` env includes:
  - `mintpy`
  - `cartopy`
  - `pyaps3`
  - `pykml`
  - `cvxopt`

## What This Decision Does Not Mean

It does not mean:

- the backend should immediately switch to unified-env execution
- the bridge route must be deleted now
- the current D-InSAR runtime should be modified

It only means:

- for the next experiment steps, unified env is the preferred path
- for current production safety, `isce2` remains untouched

## Required Guardrails

For the next phase, keep these rules:

- do not install MintPy into the existing `isce2` env
- do not redirect current D-InSAR scripts to `isce2_mintpy_v1`
- do not remove the bridge-based helpers yet
- keep all SBAS work in experiment scripts, notes, and scratch directories

## Reproducibility

Environment snapshots should be exported and kept with the experiment record.

Current snapshot command:

```text
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/export_phase4_env_snapshots_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/configs/env_snapshots/20260406
```

## Next Stable Experiment Steps

Before any system integration work:

1. Keep the unified env as the default SBAS experiment runtime.
2. Preserve environment snapshots for both `isce2` and `isce2_mintpy_v1`.
3. Write one comparison note focused on:
   - runtime simplicity
   - reproducibility
   - remaining workarounds
   - risk to D-InSAR production
4. Optionally repeat the chain on one more LT-1 sample stack.
5. Only after the experiment is stable, design the system integration boundary.
