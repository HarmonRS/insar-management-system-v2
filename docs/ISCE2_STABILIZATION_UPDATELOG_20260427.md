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

## Additional Update: DEM Sidecar Path Repair

After switching the managed DEM bundle to a copied `SRTMDEM_RSP_SARscape` dataset under
`D:\DEM`, an ISCE2 run failed in `topo` even though the outer pipeline XML pointed at the
new location. The root cause was that the copied DEM sidecar XML files still contained old
absolute `/mnt/...` paths in `file_name`, `metadata_location`, and `extra_file_name`.

Delivered adjustments:

- Added `backend/app/isce2_pipeline/repair_dem_sidecars.py` to audit and repair moved DEM
  sidecar XML files at directory scope.
- Added sidecar self-repair for the selected ISCE2 DEM during pipeline resolution so the
  managed run no longer depends on manually editing copied `.xml` files first.
- Documented the migration risk and the recommended repair command in `docs/DEPLOYMENT.md`.

## Additional Update: LT-1 Enhancement Alignment

The managed `ISCE2` `lt1_stripmap` production profile now enables the built-in
stripmap enhancement path by default:

- dense offsets
- range rubbersheeting
- azimuth rubbersheeting

This was done to bring the default ISCE2 LT-1 production semantics closer to the
existing SARscape `custom6` chain, which already includes non-trivial refinement
steps rather than shipping a bare minimum interferometric result.

The implementation now passes these parameters end-to-end through:

- `backend/app/dinsar_engines/isce2_engine.py`
- `deploy/wsl/runners/isce2_runner.py`
- `backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py`

The rationale and the SARscape / ISCE2 mapping are documented in:

- `docs/ISCE2_LT1_ENHANCEMENT_ALIGNMENT_20260427.md`

## Additional Update: Rubbersheet Runtime Dependency

The first enhanced LT-1 run reached ISCE2 `dense_offsets` successfully and then
failed at `rubber_sheet_range` with:

```text
ModuleNotFoundError: No module named 'astropy'
```

Root cause:

- ISCE2's `runRubbersheetRange.py` imports `astropy.convolution`.
- The shared WSL runtime `insar_wsl_v1` had ISCE2 and SciPy installed, but did
  not include `astropy`.

Delivered adjustments:

- Added `astropy` to `deploy/wsl/conda/insar_wsl_v1.environment.yml`.
- Added a runtime dependency preflight in the WSL runner and LT-1 pipeline so
  rubbersheeting fails immediately with a clear message instead of after the
  dense-offset stage has already run.
- Added `astropy.convolution` to the ISCE2 WSL availability check.

Required deployment action:

```bash
conda install -n insar_wsl_v1 -c conda-forge astropy
```

## Additional Update: Real Ionosphere Stage Integration

The managed `ISCE2` LT-1 stripmap workflow now runs the native stripmap
dispersive correction path instead of faking `PICKLE/ionosphere` state during
resume.

Delivered adjustments:

- Enabled `do split spectrum = True` and `do dispersive = True` in the generated
  `stripmapApp` XML.
- Changed stage-2 execution from a narrow `unwrap -> unwrap` run to a real
  `filter_low_band/unwrap/ionosphere` resume path.
- Changed stage-3 execution to resume from real `ionosphere` state when present,
  or from the low/high-band unwrap state when only stage-2 products exist.
- Extended the reduced geocode export list with:
  - `ionosphere/dispersive.bil.unwCor.filt`
  - `ionosphere/nondispersive.bil.unwCor.filt`
  - `ionosphere/mask.bil`
- Updated the export step to prefer geocoded
  `ionosphere/nondispersive.bil.unwCor.filt` when available.

Operational effect:

- `resume_from=unwrap` now resumes the complete stage-2 chain up to
  `ionosphere`
- `resume_from=geocode` now performs real `ionosphere -> geocode` continuation
  instead of relying on copied pickle files

Deployment note:

- The native ionosphere implementation imports `cv2` and `scipy`
- The WSL runner, pipeline preflight, and health check now verify those modules
  before production starts
