# ISCE2 SBAS Time-Series Experiments

This folder is the isolated sandbox for validating the SBAS/time-series route before wiring it into production code.

## Purpose

- verify LT-1 stack compatibility with ISCE2 stack tooling
- validate MintPy input and output expectations
- record runnable command templates
- collect conclusions that should later be promoted into `docs/` or backend services
- maintain the current SBAS product contract before backend embedding

## Structure

- `notes/`
  - experiment notes, pitfalls, conclusions
- `configs/`
  - sample templates, parameter files, manifest drafts
- `scripts/`
  - throwaway or semi-stable experiment scripts
- `scratch/`
  - local temporary workspace placeholder only

## Rules

- do not commit raw SAR scenes
- do not commit large DEM or orbit datasets
- do not commit large intermediate outputs
- keep production code changes out of this folder unless the goal is to prototype file layout or commands
- when an experiment becomes stable, move the result back into the formal backend or `docs/`

## Suggested first experiments

1. Check whether LT-1/LUTAN1 scenes can be ingested by official ISCE2 stack tooling.
2. Determine whether MintPy can consume the generated stack layout without extra conversion.
3. Record the minimal command chain needed for one small AOI smoke test.
4. Draft the first `psinsar` manifest and product directory convention.

## Current scripts

- `backend/app/isce2_pipeline/lt1_input_resolver.py`
  - shared LT-1 input helper reused by D-InSAR and stack experiments
  - centralizes DEM resolution, orbit-pool resolution, and LT-1 precise-orbit XML generation
- `scripts/scan_lt1_stack_candidates.py`
  - scan LT-1 single-scene folders and build a stack candidate manifest
- `scripts/build_lt1_stack_prep.py`
  - consume a selected stack manifest
  - resolve orbit pool and DEM
  - generate a dry-run `scratch/...` workspace for `stripmapStack --nofocus`
  - write the current adapter contract and a preflight run script
- `scripts/materialize_lt1_stack_scenes.py`
  - consume `scratch/.../stack_input_manifest.json`
  - materialize one or more LT-1 acquisitions into `SLC/YYYYMMDD/`
  - write `YYYYMMDD.slc`, `YYYYMMDD.slc.xml`, and `data`
- `scripts/install_isce2_stack_runtime_ubuntu2404.sh`
  - install known WSL `isce2` runtime dependencies
  - default pip mirror is Tsinghua
- `scripts/install_mintpy_runtime_ubuntu2404.sh`
  - create or update a dedicated WSL `mintpy` conda environment
  - default conda channels use Tsinghua mirror URLs
- `scripts/install_mintpy_into_cloned_isce2_env_ubuntu2404.sh`
  - clone the working WSL `isce2` env into a dedicated unified-env target such as `isce2_mintpy`
  - install MintPy into that clone with Tsinghua mirror channels
- `scripts/run_mintpy_unified_env_ubuntu2404.sh`
  - run MintPy commands directly inside the cloned unified env
- `scripts/run_mintpy_sbas_unified_env_smoketest_ubuntu2404.sh`
  - run the current LT-1 SBAS smoke test in the cloned unified env
  - reuses the same strict-mask and patched-launcher helpers as the bridge route
- `scripts/run_mintpy_with_isce_ubuntu2404.sh`
  - run MintPy commands in the dedicated `mintpy` env
  - bridge only the top-level WSL `isce` package into the `mintpy` env
  - avoids pulling conflicting `h5py` / numeric packages from the `isce2` env
- `scripts/create_mintpy_all_ifgram_mask.py`
  - build a strict `maskAllValid.h5` from `inputs/ifgramStack.h5`
  - keep only pixels valid in all interferograms before SBAS inversion
- `scripts/run_smallbaselineApp_patched.py`
  - repo-local launcher for MintPy `smallbaselineApp`
  - applies a local workaround for the MintPy `1.6.2` single-pixel partial-network inversion bug
- `scripts/run_mintpy_sbas_smoketest_ubuntu2404.sh`
  - run the current LT-1 SBAS smoke test in three steps:
    - `load_data`
    - strict-mask generation
    - `modify_network -> velocity`
- `scripts/export_mintpy_publish_products_ubuntu2404.sh`
  - geocode MintPy outputs into latitude/longitude grids
  - convert selected outputs into GeoTIFF
  - build a publish-style bundle with `manifest.json`, `assets/`, `preview/`, and `metadata/`
  - defaults to the bridge runner but now also supports `MINTPY_RUNNER=...` override
- `scripts/export_mintpy_publish_products_unified_env_ubuntu2404.sh`
  - run the same publish export logic through the cloned unified env runner
- `scripts/export_conda_env_snapshot_ubuntu2404.sh`
  - export one WSL conda environment into reproducible snapshot files
  - writes `no_builds.yml`, `explicit.txt`, `conda_list.txt`, and `runtime_versions.txt`
- `scripts/export_phase4_env_snapshots_ubuntu2404.sh`
  - export both `isce2` and `isce2_mintpy_v1` snapshots for the current phase-4 record
- `scripts/build_mintpy_publish_bundle.py`
  - generate `preview/velocity_preview.png`
  - summarize quality masks
  - write the publish-style `manifest.json`
- `scripts/prepare_lt1_stack_dem.py`
  - clip a stack-local DEM window from the source DEM
  - store it under `scratch/.../inputs/dem/`
  - avoid global-DEM bbox problems during `createWaterMask`
- `scripts/run_generated_stack_runfile_ubuntu2404.sh`
  - execute one generated `run_XX_*` file under `Ubuntu-24.04`
  - standardize `PATH`, `PYTHONPATH`, and log output
- `scripts/create_synthetic_watermask.py`
  - create a local all-land `geom_reference/waterMask.rdr`
  - used only when `run_01_reference` cannot download `SWBD` from Earthdata

## Reproducible Flow

1. Generate or refresh the sample stack workspace with `build_lt1_stack_prep.py`.
2. Materialize the LT-1 acquisitions with `materialize_lt1_stack_scenes.py`.
3. Clip a stack-local DEM window with `prepare_lt1_stack_dem.py`.
4. In `Ubuntu-24.04`, run `scripts/install_isce2_stack_runtime_ubuntu2404.sh`.
5. Regenerate the stack workspace so it picks the local DEM.
6. Run the generated wrapper through the validated chain:
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_01_reference`
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_02_focus_split`
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_03_geo2rdr_coarseResamp`
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_04_refineSecondaryTiming`
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_05_invertMisreg`
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_06_fineResamp`
   - `scripts/run_generated_stack_runfile_ubuntu2404.sh <scratch_root_wsl> run_07_grid_baseline`
   - if Earthdata credentials are missing, the wrapper now auto-generates a synthetic all-land `waterMask.rdr` from `shadowMask.rdr` and treats `run_01_reference` as recovered
7. In `Ubuntu-24.04`, run `scripts/install_mintpy_runtime_ubuntu2404.sh` before the first MintPy validation.
8. In `Ubuntu-24.04`, run `scripts/run_mintpy_with_isce_ubuntu2404.sh prep_isce.py ...` for the first MintPy metadata preparation on stripmapStack outputs.
9. In `Ubuntu-24.04`, run:
   - `scripts/run_mintpy_sbas_smoketest_ubuntu2404.sh <cfg_wsl> <mintpy_work_dir_wsl>`
10. Review:
   - `notes/PHASE2_MINTPY_SBAS_SMOKETEST.md`
11. In `Ubuntu-24.04`, run:
   - `scripts/export_mintpy_publish_products_ubuntu2404.sh <mintpy_work_dir_wsl> <publish_dir_wsl>`
12. Review:
   - `notes/PHASE3_PUBLISH_EXPORT_SMOKETEST.md`
13. Record findings under `notes/` before promoting anything into backend code.

## Product Contract

Formal product guidance now lives in:

- `docs/ISCE2_SBAS_PRODUCT_SPEC.md`

Current practical rule:

- runtime success is proven by radar-coordinate `timeseries.h5` and `velocity.h5`
- publish success is proven by a geocoded bundle under `publish/.../`
- system embedding should treat `manifest.json` as the publish entrypoint
- true time-series capability should be judged against `assets/geo_timeseries.h5`, not only `assets/velocity.tif`

## Unified-Environment Track

The bridge route remains the validated baseline.

A separate unified-environment experiment is now staged in:

- `notes/PHASE4_UNIFIED_ENV_EXPERIMENT.md`
- `notes/PHASE4_UNIFIED_ENV_DECISION.md`

Current practical rule:

- the unified env has now completed the same SBAS smoke test chain and publish export in experiment scope
- the unified env is now the preferred SBAS experiment runtime
- do not replace or mutate the current `isce2` env used by D-InSAR production
- do not replace the bridge route as the default fallback until the comparison note is fully written
- current successful unified env:
  - `/home/administrator/miniconda3/envs/isce2_mintpy_v1`
- current successful unified SBAS work dir:
  - `scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_unified_v1`
- current successful unified publish dir:
  - `scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_unified_v1`

## Current Offline Assumption

- the LT-1 sample experiment already has local SAR scenes, local orbit XML, and a local DEM
- the remaining optional online dependency is the `SWBD` water mask normally fetched by `createWaterMask.py`
- for this experiment track, do not download `SWBD`
- continue with the local synthetic all-land `waterMask.rdr` fallback until the stack route is otherwise stable
- current validated MintPy boundary is radar-coordinate `timeseries.h5` plus `velocity.h5`
- current validated publish boundary is a geocoded experiment bundle under `publish/.../`
