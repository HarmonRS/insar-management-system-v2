# Phase 1 Stack Generation Smoke Test

Updated: 2026-04-05

Follow-up note:

- MintPy SBAS continuation is now recorded separately in:
  - `notes/PHASE2_MINTPY_SBAS_SMOKETEST.md`

## Goal

Validate that one LT-1 stack can be transformed from:

- per-scene `tiff + meta.xml + orbit.xml`

into:

- `stripmapStack --nofocus` compatible acquisition directories
- a generated stripmap stack work plan

without changing production code yet.

## Sample

- group key:
  - `LT1A|STRIP1|HH|DESCENDING|E123.3_N46.1`
- dates:
  - `20250118`
  - `20250315`
  - `20250510`
  - `20250705`
  - `20250830`
- reference date:
  - `20250510`

## Commands Used

1. Build dry-run stack workspace:

```text
C:\Users\Administrator\.conda\envs\InSAR\python.exe experiments\isce2_sbas_timeseries\scripts\build_lt1_stack_prep.py --manifest-path experiments\isce2_sbas_timeseries\configs\sample_stack_e123p3_n46p1.json
```

2. Materialize LT-1 acquisitions inside `Ubuntu-24.04`:

```text
wsl -d Ubuntu-24.04 /home/administrator/miniconda3/bin/conda run -n isce2 python /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/materialize_lt1_stack_scenes.py --stack-manifest /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_input_manifest.json
```

3. Run generated wrapper:

```text
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/run_stripmap_stack_dryrun.sh
```

4. Execute the frozen stack step chain inside `Ubuntu-24.04`:

```text
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_01_reference
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_02_focus_split
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_03_geo2rdr_coarseResamp
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_04_refineSecondaryTiming
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_05_invertMisreg
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_06_fineResamp
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_07_grid_baseline
```

5. Regenerate the stack in `interferogram` workflow mode and execute the new pair-processing stage:

```text
C:\Users\Administrator\.conda\envs\InSAR\python.exe experiments\isce2_sbas_timeseries\scripts\build_lt1_stack_prep.py --manifest-path experiments\isce2_sbas_timeseries\configs\sample_stack_e123p3_n46p1.json --workflow interferogram
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/run_stripmap_stack_dryrun.sh
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_08_igram
```

6. Prepare MintPy metadata in the dedicated `mintpy` env while bridging the working `isce2` Python package:

```text
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/install_mintpy_runtime_ubuntu2404.sh
wsl -d Ubuntu-24.04 bash /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scripts/run_mintpy_with_isce_ubuntu2404.sh prep_isce.py -f "/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/Igrams/*/filt_*.unw" -m /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/Igrams/20250118_20250315/referenceShelve/data.dat -b /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/baselines -g /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/geom_reference
```

## Confirmed Results

- All 5 acquisitions were materialized into:
  - `scratch/lt1a_strip1_hh_descending_e123p3_n46p1/SLC/YYYYMMDD/`
- Each acquisition directory now contains:
  - `YYYYMMDD.slc`
  - `YYYYMMDD.slc.xml`
  - `YYYYMMDD.slc.vrt`
  - `data.dat/.dir/.bak`
- Example materialized output:
  - `20250510.slc`
  - size: `3262658784` bytes
- `stackStripMap.py --nofocus` ran successfully far enough to:
  - discover all 5 acquisitions
  - estimate stack baselines
  - select interferometric pairs
  - generate stack config files
  - generate run files

## Baseline Snapshot

Relative to reference date `20250510`, the generated stack reported:

- `20250118`
  - `-199.6873591838194`
- `20250315`
  - `-89.0330513325973`
- `20250705`
  - `-561.9056433404087`
- `20250830`
  - `215.98283570831154`

The generated network reported:

- minimum connection degree:
  - `4.0`
- number of pairs:
  - `10`

## Generated Stack Work Products

Under `scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/`:

- `baselines/`
- `configs/`
- `run_files/`
- `pairs.pdf`

Generated run files:

- `run_01_reference`
- `run_02_focus_split`
- `run_03_geo2rdr_coarseResamp`
- `run_04_refineSecondaryTiming`
- `run_05_invertMisreg`
- `run_06_fineResamp`
- `run_07_grid_baseline`

## Important Runtime Fixes

- `matplotlib` was missing from the `isce2` env.
  - fixed by installing it with `pip` inside the WSL `isce2` environment
- `stackStripMap.py` could not import `stripmapStack.Stack` by default.
  - fixed by exporting:
    - `PYTHONPATH=/home/administrator/miniconda3/envs/isce2/share/isce2`
    - `PATH=/home/administrator/miniconda3/envs/isce2/share/isce2/stripmapStack:$PATH`
- the generated run files now include these prefixes automatically
- `run_01_reference` reached `topo` successfully but `createWaterMask.py` failed without Earthdata credentials.
  - root cause:
    - `SWBD` download requires `~/.netrc` for `urs.earthdata.nasa.gov`
  - current judgment:
    - local DEM is already sufficient for this experiment
    - the only missing optional online input is the water-body mask download
  - experimental fallback:
    - `scripts/run_generated_stack_runfile_ubuntu2404.sh` now auto-generates a synthetic all-land `geom_reference/waterMask.rdr`
    - the helper script is `scripts/create_synthetic_watermask.py`
  - limitation:
    - this fallback preserves stack execution but does not provide a true coastline mask
- LT-1 input preparation now has a shared helper:
  - `backend/app/isce2_pipeline/lt1_input_resolver.py`
  - purpose:
    - centralize DEM resolution
    - centralize orbit-pool resolution
    - centralize LT-1 precise-orbit XML reuse or generation
  - compatibility rule:
    - this is a refactor of shared input-prep logic
    - the original D-InSAR execution path was not removed
    - `run_lt1_dinsar_pipeline.py` still keeps its original public workflow entry and now calls the helper internally

## Latest Execution Status

- `run_01_reference`
  - `topo` completed successfully in `Ubuntu-24.04`
  - local `geom_reference/waterMask.rdr` was synthesized from `shadowMask.rdr`
- `run_02_focus_split`
  - completed successfully
  - generated configs were effectively no-op under the current `--nofocus` contract
- `run_03_geo2rdr_coarseResamp`
  - completed successfully in `Ubuntu-24.04`
  - generated `offsets/<date>/range.off` and `azimuth.off` for:
    - `20250118`
    - `20250315`
    - `20250705`
    - `20250830`
  - generated `coregSLC/Coarse/<date>/YYYYMMDD.slc` products for:
    - `20250118`
    - `20250315`
    - `20250705`
    - `20250830`
  - runtime observation:
    - this stage is long-running and mostly silent in the log file
    - progress is easier to confirm from product directories than from stdout
- `run_04_refineSecondaryTiming`
  - completed successfully in `Ubuntu-24.04`
  - generated pair-level `refineSecondaryTiming/pairs/<pair>/misreg.*` for all 10 pairs
  - log observation:
    - `Bad match at level 1` and `correlation error` appeared in the log
    - despite that noise, the stage exited `0` and downstream inversion succeeded
- `run_05_invertMisreg`
  - completed successfully in `Ubuntu-24.04`
  - generated date-level `refineSecondaryTiming/dates/<date>/misreg.*` for:
    - `20250118`
    - `20250315`
    - `20250510`
    - `20250705`
    - `20250830`
  - inversion observation:
    - design matrix was reported as full rank
    - RMSE in azimuth was `0.002341399255443996` pixels
    - RMSE in range was `0.0027480408593210303` pixels
- `run_06_fineResamp`
  - completed successfully in `Ubuntu-24.04`
  - generated fine coregistered `merged/SLC/<date>/YYYYMMDD.slc` for all 5 dates
  - each merged date directory now also includes:
    - `referenceShelve/`
    - `secondaryShelve/`
- `run_07_grid_baseline`
  - completed successfully in `Ubuntu-24.04`
  - generated `merged/baselines/<date>/` baseline grids for all 5 dates
  - each date-level baseline directory now includes:
    - raw baseline raster
    - `.xml`
    - `.vrt`
    - `.full.vrt`
- `build_lt1_stack_prep.py --workflow interferogram`
  - now regenerates the official stripmapStack command in `interferogram` mode instead of hard-coding `slc`
  - regenerated run files now include:
    - `run_08_igram`
- `run_08_igram`
  - completed successfully in `Ubuntu-24.04`
  - generated 10 pair directories under `stack_work/Igrams/`
  - each pair now includes:
    - wrapped interferogram `.int`
    - amplitude `.amp`
    - filtered interferogram `filt_*.int`
    - coherence `filt_*.cor`
    - unwrapped phase `filt_*_snaphu.unw`
    - connected components `*.unw.conncomp`
    - `referenceShelve/data.*`
- MintPy runtime bootstrap
  - `scripts/install_mintpy_runtime_ubuntu2404.sh` created a dedicated WSL env:
    - `mintpy`
  - verified commands:
    - `smallbaselineApp.py`
    - `prep_isce.py`
  - installed MintPy version:
    - `1.6.2`
- `prep_isce.py`
  - first run in the clean `mintpy` env failed because `mintpy.utils.isce_utils` imports `isce`
  - experimental resolution:
    - `scripts/run_mintpy_with_isce_ubuntu2404.sh` now bridges only the top-level `isce` package from the WSL `isce2` env into the `mintpy` env
  - result:
    - `prep_isce.py` completed successfully over the LT-1 `stripmapStack` outputs
    - geometry `.rsc` files were written under `stack_work/geom_reference/`
    - observation `.rsc` files were written for all 10 unwrapped interferograms under `stack_work/Igrams/*/`

## Current Boundary

This smoke test confirms:

- LT-1 scenes can be materialized into `stripmapStack` acquisition directories
- the official stripmap stack driver can build the stack work plan over those LT-1 products
- the generated `run_01` to `run_07` chain can complete offline in `Ubuntu-24.04` over the sample LT-1 stack
- local DEM plus local orbit data are sufficient for this stack-preparation stage
- Earthdata credentials are not a hard blocker for this experiment track because the wrapper can recover `run_01_reference` with a synthetic all-land `waterMask`
- the same LT-1 stack can be regenerated in `interferogram` workflow mode to produce 10 filtered and unwrapped pair products
- MintPy metadata preparation is now viable through the dedicated `mintpy` env plus the explicit ISCE bridge wrapper

This smoke test does not yet confirm:

- `smallbaselineApp.py` execution beyond `prep_isce.py`
- final time-series or velocity products

Follow-up status:

- both of the above were later confirmed in:
  - `notes/PHASE2_MINTPY_SBAS_SMOKETEST.md`

## Next Tasks

1. Keep this note focused on stack-generation findings only.
2. Use `notes/PHASE2_MINTPY_SBAS_SMOKETEST.md` for MintPy SBAS runtime conclusions.
3. Promote the stable runtime and artifact contract into backend workflow code and `docs/`.
