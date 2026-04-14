# Phase 3 Publish Export Smoke Test

Updated: 2026-04-06

## Goal

Validate that the successful MintPy SBAS experiment can be converted into a publish-style artifact bundle without touching the main system.

## Inputs

Source MintPy work directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_v5`

Source stack:

- group key:
  - `LT1A|STRIP1|HH|DESCENDING|E123.3_N46.1`
- dates:
  - `20250118`
  - `20250315`
  - `20250510`
  - `20250705`
  - `20250830`

## Successful Command

```text
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/export_mintpy_publish_products_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_v5 /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_v5
```

## Current Export Scripts

- `scripts/export_mintpy_publish_products_ubuntu2404.sh`
  - geocode core MintPy outputs
  - convert selected outputs to GeoTIFF
  - copy preview and metadata files
  - build root `manifest.json`
- `scripts/build_mintpy_publish_bundle.py`
  - generate `preview/velocity_preview.png`
  - generate `metadata/source_quality_summary.json`
  - generate publish-style root `manifest.json`

## Geocode Contract

Current experiment settings:

- lookup source:
  - `inputs/geometryRadar.h5`
- output pixel size:
  - latitude step:
    - `-0.000185185`
  - longitude step:
    - `0.000185185`
- interpolation:
  - `nearest`

Observed geocoded grid:

- extent:
  - south:
    - `45.80391`
  - north:
    - `46.42206`
  - west:
    - `122.930244`
  - east:
    - `123.76654`
- shape:
  - rows:
    - `3338`
  - columns:
    - `4516`

## Generated Publish Bundle

Successful publish-style directory:

- `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_v5`

Main outputs:

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

## Current Interpretation

This confirms:

- the experiment now supports radar-coordinate MintPy inversion
- the experiment also supports geocoded HDF5 exports
- the experiment can produce publish-style GeoTIFF outputs
- the experiment can build a stable manifest-driven bundle outside the MintPy work directory

This does not yet confirm:

- direct backend catalog registration
- frontend rendering against the real system APIs
- whether `EPSG:4326` should remain the final publish CRS decision

## Important Notes

### 1. Current CRS assumption

`save_gdal.py` warned that no explicit `EPSG` metadata was found and assumed:

- `EPSG:4326`

Current judgment:

- acceptable for this experiment because the geocoded outputs are in latitude/longitude grids
- should still be checked when formalizing the production publish contract

### 2. Group key in the generated manifest

Because PowerShell treats `|` specially, passing group keys on the command line is awkward from Windows.

Current practical rule:

- the sample manifest stored in git remains the clean reference:
  - `configs/sample_psinsar_manifest_lt1_e123p3_n46p1.json`
- the generated publish bundle manifest can be post-filled or generated from backend metadata later

## System Embedding Implication

At this point the experiment-layer chain is split cleanly into three stages:

1. `stripmapStack` preprocessing
2. MintPy SBAS inversion
3. geocode + publish-bundle export

That means the future system workflow can wire them as separate workflow steps without changing the validated experiment logic first.
