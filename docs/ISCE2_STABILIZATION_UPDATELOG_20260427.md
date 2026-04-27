# ISCE2 Stabilization Update Log

Date: `2026-04-27`

## Scope

This update hardens the managed `ISCE2` LT-1 stripmap D-InSAR production path against
large raw DEM reuse, long geocode stalls, and incomplete recovered-run metadata.

## Delivered Changes

### 1. Managed ISCE2 pipeline hardening

- Added `--resume-from unwrap|geocode|export` support to the LT-1 pipeline.
- Added `--full-geocode` support. The default path now geocodes only the export-critical
  products instead of ISCE2's full default list.
- Added stage-aware logging with stdout flush to improve long-run observability.
- Added geocode DEM subset preparation so resumed geocode/export runs do not reprocess the
  full base DEM.
- Added a guard that blocks fresh runs when the selected DEM resolves to a very large raw
  base raster without a prepared `.wgs84` sibling.

### 2. Engine and runtime configuration fixes

- `ISCE2` now prefers an existing prepared `.wgs84` DEM over the raw base path.
- `PyINT` DEM resolution now follows the same prepared-first preference.
- The WSL ISCE2 runner now passes `resume_from` and `full_geocode` through to the pipeline.
- The WSL runner now sets both `PROJ_DATA` and `PROJ_LIB`.

### 3. Recovery and catalog self-healing

- Added a completion-file repair helper for managed ISCE2 runs.
- In-place ISCE2 publish/rebuild now repairs missing:
  - `execution_manifest.json`
  - `current/isce2__<profile>.json`
- This allows recovered runs to be reintroduced into the managed result catalog without
  hand-editing completion markers.

### 4. One-time DEM preparation tooling

- Added `backend/app/isce2_pipeline/prepare_isce2_base_dem.py`.
- The script resolves DEM paths from `.env`, validates existing prepared outputs, and can
  generate a reusable `WGS84` `.wgs84` DEM from an `EGM96` base DEM.
- The script also auto-configures `PROJ` paths for standalone execution.

### 5. Configuration guidance

- Updated `.env.example` comments to distinguish:
  - raw SARscape/ENVI DEM source path
  - prepared ISCE2/PyINT `.wgs84` path

## Validation

- Python syntax validation was run for the modified ISCE2 pipeline, engine, runtime, and
  result-catalog modules.
- The one-time full DEM preparation completed successfully and produced:
  - `.wgs84`
  - `.wgs84.xml`
  - `.wgs84.vrt`
- The prepared DEM metadata was verified to report `WGS84`.

## Required Local Follow-Up

These operational steps are intentionally not committed:

- Point local `.env` `ISCE2_DEM_PATH` to the prepared `.wgs84` file.
- Point local `.env` `PYINT_PREPARED_DEM_PATH` to the same prepared `.wgs84` file.
- Restart the backend so the running process reloads the updated `.env`.

## Additional Update: Strict Production Workflow Boundary

After reviewing the ISCE2 production semantics, the export path was tightened so the
default managed `ISCE2` D-InSAR product remains a strict pipeline result rather than an
implicitly corrected interpretation layer.

Delivered adjustments:

- Kept the reference-normalization helper only as an optional debug/export capability.
- Restored the default export behavior to `reference_mode=none`.
- Removed reference-normalization controls from the regular managed production profile so
  operators do not treat post-processing heuristics as part of the standard workflow.
- Revalidated the modified pipeline, engine, and WSL runner modules with `python3 -m py_compile`
  inside the target WSL runtime environment.
