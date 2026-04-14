# Phase 0 Practical TODO

## Immediate

- [x] Run `scripts/check_env_ubuntu2404.sh` inside `Ubuntu-24.04`.
- [x] Use `scripts/scan_lt1_stack_candidates.py` to keep one baseline sample stack manifest current.
- [x] Treat `E123.3_N46.1` as the first tile-level smoke-test sample unless a better sample appears.
- [x] Confirm ISCE2 stack-processing scripts are present.
- [x] Confirm the official helper scripts do not advertise LT-1/LUTAN1 stack prep.
- [x] Record required orbit, DEM, and metadata adaptations.
- [x] Run `scripts/build_lt1_stack_prep.py` to keep the dry-run stack workspace current.

## Before first end-to-end run

- [x] Implement an LT-1 scene materializer that creates `YYYYMMDD.slc`, `YYYYMMDD.slc.xml`, and `data`.
- [x] Materialize the remaining acquisitions for `E123.3_N46.1` under `scratch/.../SLC/`.
- [x] Smoke-test the materializer on the reference date `20250510`.
- [x] Run the generated `run_stripmap_stack_dryrun.sh` preflight and then `stackStripMap.py --nofocus`.
- [x] Inspect the produced `baseline/`, `configs/`, and `run_files/` outputs.
- [x] Prepare a stack-local DEM to avoid global-DEM bbox behavior during `createWaterMask`.
- [x] Add a reproducible synthetic `waterMask` fallback for `run_01_reference` when Earthdata credentials are unavailable.
  Working rule: DEM is already local and sufficient; do not download `SWBD` during this experiment stage.
- [x] Extract shared LT-1 input preparation helper for DEM/orbit resolution.
  Compatibility rule: original D-InSAR entry logic remains in place; only the duplicated input-prep internals were consolidated.
- [x] Decide MintPy installation strategy after stack generation is stable.
  Decision: default to a dedicated WSL conda env named `mintpy` so the working `isce2` processing env stays unchanged on the development machine.
- [x] Freeze the first smoke-test command chain.
  Frozen chain: `run_01_reference -> run_02_focus_split -> run_03_geo2rdr_coarseResamp -> run_04_refineSecondaryTiming -> run_05_invertMisreg -> run_06_fineResamp -> run_07_grid_baseline`
- [x] Execute `run_01_reference` through the WSL wrapper and verify the fallback-recovered geometry outputs.
- [x] Execute `run_02` to `run_07` and record LT-1-specific failures if they appear.
  Result: all stages exited `0` in `Ubuntu-24.04`. `run_04_refineSecondaryTiming` logs still contain `Bad match at level 1` and `correlation error`, but pair-level `misreg`, date-level `misreg`, merged SLC, and merged baseline products were all generated successfully.

## Next Focus

- [x] Run `scripts/install_mintpy_runtime_ubuntu2404.sh` in `Ubuntu-24.04` and verify the new env.
  Result: dedicated WSL env `mintpy` was created successfully and `smallbaselineApp.py` / `prep_isce.py` are available.
- [x] Validate MintPy ingestion against the current `stack_work/merged/` outputs.
  Result: `build_lt1_stack_prep.py --workflow interferogram` plus `run_08_igram` produced `Igrams/*/filt_*_snaphu.unw`, and `prep_isce.py` completed successfully after bridging the working `isce2` Python package into the `mintpy` env.
- [x] Draft the first `smallbaselineApp.cfg` for the LT-1 sample stack.
  Result: `configs/sample_smallbaseline_lt1_e123p3_n46p1.cfg` now records the first runnable LT-1 stripmapStack -> MintPy SBAS contract.
- [x] Execute the first MintPy workflow steps after `prep_isce.py`.
  Result: the repo-local smoke-test chain now reaches radar-coordinate `timeseries.h5` and `velocity.h5` in `stack_work/mintpy_sbas_v5/`.
  Current helper chain:
  - `scripts/run_mintpy_with_isce_ubuntu2404.sh`
  - `scripts/create_mintpy_all_ifgram_mask.py`
  - `scripts/run_smallbaselineApp_patched.py`
  - `scripts/run_mintpy_sbas_smoketest_ubuntu2404.sh`
- [x] Draft the first production-side SBAS artifact manifest and publish contract.
  Result:
  - `configs/sample_psinsar_manifest_lt1_e123p3_n46p1.json`
  - `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md`

## New Follow-up

- [ ] Decide whether production should keep the repo-local patched MintPy launcher or pin an upstream-fixed MintPy version.
- [x] Add the geocode/export stage needed for publishable SBAS rasters and previews.
  Result: experiment-layer publish export now succeeds into `publish/mintpy_sbas_v5/` with geocoded HDF5, GeoTIFF, preview PNG, and `manifest.json`.
- [ ] Wire the validated SBAS runtime chain into backend workflow submission and artifact publishing.
- [ ] Run a separate unified-environment experiment by cloning the current WSL `isce2` env and installing MintPy directly inside it.
