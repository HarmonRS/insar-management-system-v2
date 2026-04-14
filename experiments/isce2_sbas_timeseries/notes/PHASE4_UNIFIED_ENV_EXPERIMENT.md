# Phase 4 Unified-Environment Experiment

Updated: 2026-04-06

## Goal

Validate whether the current WSL `isce2` runtime can be cloned and extended with MintPy so that the SBAS experiment can run without the temporary `isce` bridge helper.

This is still an experiment-layer task.

Do not change the production backend yet.

## Why run this phase

The bridge-based route is already validated, but a unified environment may be cleaner because:

- many ISCE2 + MintPy users operate in one environment
- command invocation becomes simpler
- future worker deployment may be easier if one runtime is stable

The bridge-based route still remains the fallback baseline until this phase is verified.

## Current Known Starting Point

WSL distro:

- `Ubuntu-24.04`

Current environments observed on 2026-04-06:

- `isce2`
- `mintpy`

Observed package state:

- `isce2` env:
  - `isce2 2.6.4`
  - `h5py 3.15.1`
  - `mintpy` not installed
- dedicated `mintpy` env:
  - `mintpy 1.6.3`

## Initial Hypothesis

Expected best-case outcome:

- clone `isce2` into `isce2_mintpy`
- install `mintpy` directly into the clone
- reuse the same repo-local strict-mask and patched-launcher helpers
- run the same LT-1 smoke test without the `isce` bridge wrapper

Main risk areas:

- package solver may replace or downgrade key ISCE2-side numeric dependencies
- MintPy may still require the same repo-local runtime workaround even in a unified env
- GDAL / h5py / pyaps3 dependency changes may alter the known-good stack behavior

## Reproducible Commands

### 1. Bootstrap the unified env

```text
wsl -d Ubuntu-24.04 env TARGET_ENV=isce2_mintpy_v1 BOOTSTRAP_MODE=recreate USE_TUNA_MIRROR=1 MINTPY_SPEC=mintpy=1.6.3 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/install_mintpy_into_cloned_isce2_env_ubuntu2404.sh
```

Why `BOOTSTRAP_MODE=recreate`:

- direct `conda create --clone` was not stable enough for this machine
- it still followed source-package URLs and hit channel/TOS friction
- the successful path was:
  - export the current `isce2` dependency list
  - recreate the env through Tsinghua mirror channels
  - reinstall exported pip packages
  - install MintPy into the recreated env

Optional environment override:

```text
TARGET_ENV=isce2_mintpy_v1 MINTPY_SPEC='mintpy=1.6.3'
```

### 2. Run MintPy commands directly inside the unified env

```text
wsl -d Ubuntu-24.04 env MINTPY_ENV=isce2_mintpy_v1 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_mintpy_unified_env_ubuntu2404.sh prep_isce.py -h
```

### 3. Re-run the current LT-1 SBAS smoke test in the unified env

```text
wsl -d Ubuntu-24.04 env MINTPY_ENV=isce2_mintpy_v1 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_mintpy_sbas_unified_env_smoketest_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/configs/sample_smallbaseline_lt1_e123p3_n46p1.cfg /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_unified_v1
```

### 4. Export publish bundle in the unified env

```text
wsl -d Ubuntu-24.04 env MINTPY_ENV=isce2_mintpy_v1 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/export_mintpy_publish_products_unified_env_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_unified_v1 /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_unified_v1
```

## Comparison Checklist

When this phase is executed, compare it against the bridge route on:

- package versions after install
- whether `prep_isce.py` imports cleanly without bridge
- whether `load_data` succeeds
- whether the strict-mask step is still required
- whether the patched launcher is still required
- whether output files match the existing bridge-based artifact set
- whether geocode/export still succeeds from the unified env

## Current Status

Successful environment:

- `/home/administrator/miniconda3/envs/isce2_mintpy_v1`

Observed package/version state in the successful unified env:

- `conda list` shows:
  - `mintpy 1.6.3`
- runtime `mintpy.__version__` reports:
  - `1.6.2`
- `isce` import path:
  - `/home/administrator/miniconda3/envs/isce2_mintpy_v1/lib/python3.11/site-packages/isce/__init__.py`
- `mintpy` import path:
  - `/home/administrator/miniconda3/envs/isce2_mintpy_v1/lib/python3.11/site-packages/mintpy/__init__.py`
- `h5py`:
  - `3.15.1`

Successful unified-env SBAS work directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_unified_v1`

Successful unified-env publish directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_unified_v1`

Validated in the unified env:

- `prep_isce.py -h` works without the `isce` bridge
- `load_data` succeeded
- strict-mask generation succeeded
- `modify_network -> velocity` succeeded
- publish export succeeded through geocoded HDF5, GeoTIFF, preview, and `manifest.json`

Observed unified-env output set under `mintpy_sbas_unified_v1/`:

- `timeseries.h5`
- `velocity.h5`
- `temporalCoherence.h5`
- `maskTempCoh.h5`
- `maskAllValid.h5`
- `avgSpatialCoh.h5`
- `numTriNonzeroIntAmbiguity.h5`
- `numTriNonzeroIntAmbiguity.png`

Observed publish bundle under `publish/mintpy_sbas_unified_v1/`:

- `manifest.json`
- `assets/geo_timeseries.h5`
- `assets/geo_velocity.h5`
- `assets/geo_temporalCoherence.h5`
- `assets/geo_maskTempCoh.h5`
- `assets/velocity.tif`
- `assets/temporalCoherence.tif`
- `assets/maskTempCoh.tif`
- `preview/velocity_preview.png`
- `preview/numTriNonzeroIntAmbiguity.png`
- `metadata/smallbaselineApp.cfg`
- `metadata/source_quality_summary.json`

Quality summary matched the bridge-based route:

- `maskAllValid`:
  - `1219001 / 4076199`
  - `29.91%`
- `maskTempCoh`:
  - `62987 / 4076199`
  - `1.55%`

Still required in the unified env:

- strict `maskAllValid.h5` before inversion
- repo-local patched `smallbaselineApp` launcher

Prepared:

- unified-env bootstrap script
- unified-env MintPy runner
- unified-env SBAS smoke-test runner
- unified-env publish-export wrapper

Completed:

- actual clone + install execution
- actual smoke-test result capture
- actual publish-export capture

Pending:

- deeper comparison of output metadata against the bridge route
- decide whether unified env or bridge env should be the default production candidate
- decide whether to pin the runtime to the conda package label `1.6.3` or the internal MintPy version string `1.6.2`

## Current Judgment

At experiment level, the unified environment is now viable.

This phase confirms:

- the current LT-1 SBAS route does not fundamentally require the `isce` bridge
- a recreated `isce2 + mintpy` WSL env can complete:
  - MintPy load/inversion
  - geocode/export
  - publish-bundle generation

Current recommendation:

- keep the bridge route as the already-known baseline until a fuller diff is written
- but treat the unified env as a valid candidate for the future production runtime
