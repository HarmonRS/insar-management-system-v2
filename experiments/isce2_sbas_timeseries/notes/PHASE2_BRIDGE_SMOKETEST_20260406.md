# Phase 2 Bridge Smoketest

Date: 2026-04-06

## Scope

Validate the current SBAS bridge chain with the previously verified LT-1 sample stack:

1. `build_lt1_stack_prep.py`
2. `materialize_lt1_stack_scenes.py`
3. `build_lt1_stack_prep.py` refresh

This run validates the current bridge boundary only:

- raw LT-1 scenes
- local precise orbit pool
- local prepared DEM
- fresh scratch workspace

It does not run:

- `stripmapStack`
- MintPy
- geocode/export/publish

## Inputs

- sample manifest:
  - `experiments/isce2_sbas_timeseries/configs/sample_stack_e123p3_n46p1.json`
- orbit pool:
  - `/mnt/d/orbit_pools/isce2`
- DEM:
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/inputs/dem/stack_dem_window.wgs84`

## Workspace

- scratch root:
  - `experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406`

## Result

- final readiness: `True`
- scene count: `5`
- reference date: `20250510`
- all orbits resolved: `True`
- all `.slc/.slc.xml` present: `True`
- all `data` shelves present: `True`

## Materialization Summary

- dates:
  - `20250118`
  - `20250315`
  - `20250510`
  - `20250705`
  - `20250830`
- status counts:
  - `materialized: 5`
- total bytes written:
  - `16313641344`

## Artifacts

- selected manifest used for this WSL run:
  - `experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/selected_stack_manifest_wsl.json`
- generated stack manifest:
  - `experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/stack_input_manifest.json`
- materialization summary:
  - `experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/materialization_summary.json`
- generated stack dry-run wrapper:
  - `experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/run_stripmap_stack_dryrun.sh`
- synthetic water-mask recovery report:
  - `experiments/isce2_sbas_timeseries/scratch/phase2_bridge_smoketest_20260406/stack_work/logs/run_01_reference.synthetic_watermask.json`

## Finding

`build_lt1_stack_prep.py` currently reads `scene["tiff_path"]` and `scene["meta_path"]` directly.
When the script is run inside WSL against the sample manifest, the original `F:\...` Windows paths are not readable as Linux paths.

For this smoketest, a temporary WSL-path manifest copy was generated and used:

- `selected_stack_manifest_wsl.json`

This is an experiment-side workaround only.
No production/system logic was changed for this run.

## Update 2026-04-07

The same fresh workspace was then continued through the stripmap stack run files.

### Additional Result

- `run_01_reference`
  - reached `createWaterMask`
  - failed on remote `SWBD` retrieval from:
    - `https://e4ftl01.cr.usgs.gov/MEASURES/SRTMSWBD.003/...`
  - recovered with a local synthetic all-land `waterMask.rdr`
- `run_02_focus_split`
  - completed
- `run_03_geo2rdr_coarseResamp`
  - completed
- `run_04_refineSecondaryTiming`
  - completed
- `run_05_invertMisreg`
  - completed
- `run_06_fineResamp`
  - completed
- `run_07_grid_baseline`
  - completed
- `run_08_igram`
  - completed through interferogram generation, filtering, coherence, and `snaphu` unwrapping

### Interferogram Snapshot

- pair count on disk:
  - `10`
- verified pair folders:
  - `20250118_20250315`
  - `20250118_20250510`
  - `20250118_20250705`
  - `20250118_20250830`
  - `20250315_20250510`
  - `20250315_20250705`
  - `20250315_20250830`
  - `20250510_20250705`
  - `20250510_20250830`
  - `20250705_20250830`
- verified key files in every pair directory:
  - `filt_<date12>.int`
  - `filt_<date12>.cor`
  - `filt_<date12>_snaphu.unw`
  - `filt_<date12>_snaphu.unw.conncomp`

### Finding Update

The original `run_generated_stack_runfile_ubuntu2404.sh` fallback only matched the `.netrc` credential failure text.
This workspace showed a second offline failure mode:

- `createWaterMask` started normally
- ISCE2 `DataRetriever` failed during `SWBD` file retrieval
- the wrapper therefore did not auto-recover on the first attempt

The experiment helper has now been widened to recognize both:

- missing Earthdata credential text
- direct `SWBD` retrieval failure text from `createWaterMask`

No production/system runtime was changed by this fix.

## Recommended Next Step

Use this fresh workspace to continue with:

1. unified-env MintPy smoketest using:
   - `configs/phase2_bridge_smoketest_20260406_smallbaseline.cfg`
2. publish-style geocode/export if MintPy succeeds
3. compare this fresh replay with the earlier baseline workspace
