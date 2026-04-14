# Phase 4 Unified-Env Replay On Fresh Phase-2 Workspace

Updated: 2026-04-08

## Goal

Re-run the already validated unified-env SBAS route on the fresh workspace:

- `scratch/phase2_bridge_smoketest_20260406`

This checks that the current experiment does not rely on the older sample workspace only.

## Inputs

- WSL distro:
  - `Ubuntu-24.04`
- unified env:
  - `/home/administrator/miniconda3/envs/isce2_mintpy_v1`
- stack config:
  - `configs/phase2_bridge_smoketest_20260406_smallbaseline.cfg`
- stack workspace:
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406`
- MintPy work dir:
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/stack_work/mintpy_sbas_unified_phase2_20260407`
- publish dir:
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/publish/mintpy_sbas_unified_phase2_20260407`

## Successful Commands

Unified-env MintPy smoke test:

```text
wsl -d Ubuntu-24.04 env MINTPY_ENV=isce2_mintpy_v1 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_mintpy_sbas_unified_env_smoketest_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/configs/phase2_bridge_smoketest_20260406_smallbaseline.cfg /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/stack_work/mintpy_sbas_unified_phase2_20260407
```

Unified-env publish export:

```text
wsl -d Ubuntu-24.04 env MINTPY_ENV=isce2_mintpy_v1 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/export_mintpy_publish_products_unified_env_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/stack_work/mintpy_sbas_unified_phase2_20260407 /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/publish/mintpy_sbas_unified_phase2_20260407
```

## Result

The fresh workspace replay succeeded through:

- `load_data`
- strict `maskAllValid.h5` generation
- `modify_network`
- `reference_point`
- `quick_overview`
- `invert_network`
- `reference_date`
- `velocity`
- geocode/export
- publish-bundle generation

This confirms the current fresh workspace now reaches the same experiment boundary as the earlier baseline sample:

- radar-coordinate MintPy runtime products
- geocoded publish bundle

## Runtime Output Snapshot

Generated under `stack_work/mintpy_sbas_unified_phase2_20260407/`:

- `timeseries.h5`
  - `82579888` bytes
- `velocity.h5`
  - `83086408` bytes
- `temporalCoherence.h5`
  - `16632424` bytes
- `maskTempCoh.h5`
  - `4136848` bytes
- `maskAllValid.h5`
  - `4095215` bytes
- `avgSpatialCoh.h5`
  - `16631784` bytes
- `numTriNonzeroIntAmbiguity.h5`
  - `16632000` bytes
- `numTriNonzeroIntAmbiguity.png`
  - `232923` bytes

## Publish Bundle Snapshot

Generated under `publish/mintpy_sbas_unified_phase2_20260407/`:

- `manifest.json`
  - `32442` bytes
- `assets/geo_timeseries.h5`
  - `304443360` bytes
- `assets/geo_velocity.h5`
  - `305627744` bytes
- `assets/geo_temporalCoherence.h5`
  - `61141152` bytes
- `assets/geo_maskTempCoh.h5`
  - `15260536` bytes
- `assets/velocity.tif`
  - `60318026` bytes
- `assets/temporalCoherence.tif`
  - `60318026` bytes
- `assets/maskTempCoh.tif`
  - `15094802` bytes
- `preview/velocity_preview.png`
  - `813928` bytes
- `preview/numTriNonzeroIntAmbiguity.png`
  - `232923` bytes
- `metadata/smallbaselineApp.cfg`
  - `26419` bytes
- `metadata/source_quality_summary.json`
  - `403` bytes

## Quality Summary

From `metadata/source_quality_summary.json`:

- `maskAllValid`
  - `1219067 / 4076199`
  - `29.91%`
- `maskTempCoh`
  - `63092 / 4076199`
  - `1.55%`
- preview stretch:
  - `vmin = -0.2251889556646347`
  - `vmax = 0.2251889556646347`

## Findings

### 1. The fresh workspace replay is reproducible

The new workspace produced:

- the full 10-pair interferogram stack
- MintPy `timeseries.h5`
- MintPy `velocity.h5`
- publish-layer geocoded HDF5 / GeoTIFF / preview / `manifest.json`

This is the current strongest experiment proof that the SBAS route is not tied to the earlier historical scratch directory.

### 2. The same two MintPy experiment helpers are still required

The unified env still depends on:

- `create_mintpy_all_ifgram_mask.py`
- `run_smallbaselineApp_patched.py`

Current interpretation:

- unified env removes the temporary `isce` bridge
- it does not remove the current strict-mask or patched-launcher workarounds

### 3. Offline water-mask strategy remains valid

This replay consumed the synthetic all-land `waterMask.rdr` created earlier in the fresh workspace.

No Earthdata / `SWBD` download was needed for the MintPy or publish stages.

### 4. Geocode/export completed with a tolerable warning

`save_gdal.py` warned that no EPSG / UTM metadata was found and assumed:

- `EPSG:4326`

For this experiment chain, that is acceptable because the export was already driven by latitude / longitude lookup plus explicit `--lalo` sampling.

This warning should still be recorded for later production hardening.

### 5. `group_key` should come from system metadata

This host-side replay exported a valid publish bundle, and the bundle should carry:

- `"group_key": "LT1A|STRIP1|HH|DESCENDING|E123.3_N46.1"`

Reason:

- the bundle builder accepts `group_key` as a CLI argument
- the LT-1 group key contains pipe characters:
  - `LT1A|STRIP1|HH|DESCENDING|E123.3_N46.1`
- when invoked naively from Windows PowerShell into WSL, the value may be truncated or split by the host shell

Current judgment:

- this is not a blocker for the SBAS scientific chain
- future system embedding should write `group_key` from task/run metadata inside backend code instead of relying on manual shell arguments

## Current Judgment

The current fresh workspace now confirms the full experiment chain:

- `raw LT-1 -> stripmapStack -> MintPy SBAS -> geocode/export -> publish bundle`

At experiment level, the unified env remains the preferred SBAS runtime.

At production-safety level, the existing D-InSAR `isce2` environment should still remain untouched.
