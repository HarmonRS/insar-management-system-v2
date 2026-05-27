# SBAS-InSAR Production Pipeline Design

Date: 2026-05-19

## Decision

SBAS-InSAR production becomes an independent production workflow and page. It must not depend on the existing coarse time-series pairing layer as its production authority.

The old time-series pairing code may remain temporarily for compatibility and candidate discovery, but the new SBAS-InSAR page and backend API should bypass it by default. Deletion should happen only after the new workflow can create, run, publish, and browse Gamma SBAS/IPTA products end to end.

The legacy ISCE2/MintPy time-series production chain is disabled by default. The `timeseries-production` backend code and old catalog pages may remain as compatibility code, but they are no longer exposed as production-management subpages. The active SBAS production route is `/api/sbas-insar-production` and the active UI view is `sbas_insar_production`.

Update 2026-05-25:

- The previous ad-hoc SBAS stage buttons are no longer the primary production design.
- The primary design follows the expert document: workspace directories, a run-level `manifest.json`, step scripts, `state/step_status.json`, and one Gamma SBAS workflow job.
- The already successful experiment is not discarded. Its verified Gamma commands are reused as the first bridge implementation while the scripts are moved toward expert-document templates.
- Old stage endpoints may remain temporarily for compatibility and for reading historical runs, but the UI should favor `Gamma SBAS Workflow`.

Primary runtime configuration is now read from `.env` through:

```text
GAMMA_SBAS_ENABLED
GAMMA_SBAS_RUNTIME_ID
GAMMA_SBAS_WSL_DISTRO
GAMMA_SBAS_PYTHON
GAMMA_SBAS_ENV_SCRIPT
GAMMA_SBAS_WORK_ROOT
GAMMA_SBAS_PRODUCT_ROOT
GAMMA_SBAS_SCRIPT_TEMPLATE_ROOT
GAMMA_SBAS_DEFAULT_RLKS
GAMMA_SBAS_DEFAULT_AZLKS
GAMMA_SBAS_DEFAULT_MB_MODE
GAMMA_SBAS_DEFAULT_REFERENCE_WINDOW
GAMMA_SBAS_STEP_TIMEOUT_SECONDS
GAMMA_SBAS_WORKFLOW_TIMEOUT_SECONDS
```

Startup self-check now reports the Gamma SBAS runtime, work root, product root, Python interpreter, and WSL distro. Database self-maintenance still runs through the existing `ensure_database_ready()` startup path; no new SBAS-only database table is required for this slice.

## Scope

Initial production target:

- Sensor: LT1 SLC
- Engine: Gamma
- Workflow: Gamma DIFF + IPTA `mb` + `ts_rate`
- Processor code: `gamma_ipta_sbas`
- Default display product: LOS velocity toward radar positive

Out of scope for the first implementation slice:

- custom SBAS inversion in application code
- direct reuse of the current PS/time-series pair graph as final Gamma `itab`
- full automatic stack approval without a Gamma baseline and quality audit
- cross-satellite LT1A/LT1B stack mixing

## Product Contract

Every successful SBAS-InSAR production run should publish:

- `product_summary.json`
- `stack_manifest.json`
- `gamma_command_manifest.json`
- `pair_network.json`
- `quality_summary.json`
- `los_rate_toward_mm_per_year.tif`
- `los_rate_toward_mm_per_year.geo_preview.png`
- `los_rate_toward_mm_per_year.rdc_preview.bmp`
- `los_rate_away_mm_per_year.tif`
- `los_rate_away_mm_per_year.rdc_preview.bmp`
- `los_sigma_mm_per_year.tif`
- `los_sigma_mm_per_year.geo_preview.png`
- `los_sigma_mm_per_year.rdc_preview.bmp`
- `ts_rate_rad_per_year.tif`
- `sigma_rate_rad_per_year.tif`
- monitoring-point time-series `png/csv/json`
- raw logs for each Gamma stage

The current trial product remains the reference implementation:

```text
backend/runtime/gamma_ipta_trials/lt1b_r114_e1312_n438_20240516_20251002
```

Preview rule:

- UI default map previews must be rendered from geocoded EPSG:4326 GeoTIFFs.
- RDC/RMLI BMP browse images are processing QA artifacts only.
- Product names should make coordinate state explicit: `geo_preview` for map previews and `rdc_preview` for radar-geometry previews.

## LOS Sign Convention

Gamma `ts_rate` outputs phase rate in `rad/year`. The system must store both sign conventions explicitly:

```text
los_rate_away_mm_per_year = phase_rate * wavelength / (4*pi) * 1000
los_rate_toward_mm_per_year = -phase_rate * wavelength / (4*pi) * 1000
```

Default UI display:

```text
LOS toward radar positive
```

This matches Gamma `dispmap` default `sflg=0`: motion away from radar is negative, motion toward radar is positive.

## Page Design

Add a separate production view:

```text
Production Management
  - D-InSAR Runs
  - SBAS-InSAR Production
  - D-InSAR Products
```

The SBAS page is operational, not a marketing landing page. First screen should show:

- runtime capability: Gamma install, WSL distro, workflow support
- available SBAS stacks or trial runs
- selected run summary
- LOS velocity geocoded preview
- product file list
- monitoring-point curve
- quality metrics
- stage checklist

The current old "time-series run" and "time-series products" views are hidden from the production workspace. Legacy route aliases such as `ps_production` and `ps_products` should redirect to the SBAS-InSAR production view rather than opening the old ISCE2/MintPy workflow.

## Backend API

Initial read-only API:

```text
GET /api/sbas-insar-production/capabilities
GET /api/sbas-insar-production/trial-runs
GET /api/sbas-insar-production/trial-runs/{trial_id}
GET /api/sbas-insar-production/trial-runs/{trial_id}/artifacts/{relative_path}
```

Primary Gamma SBAS workflow API:

```text
POST /api/sbas-insar-production/runs/{run_id}/workflow
POST /api/sbas-insar-production/runs/{run_id}/workflow/jobs
```

`workflow` prepares the expert-document workspace and writes:

```text
runs/{run_id}/workspace.json
runs/{run_id}/manifest.json
runs/{run_id}/state/step_status.json
runs/{run_id}/scripts/01_workspace_data.sh
runs/{run_id}/scripts/02_import_lt1_slc.sh
runs/{run_id}/scripts/03_reference_mli.sh
runs/{run_id}/scripts/04_dem_lookup.sh
runs/{run_id}/scripts/05_coreg_prep.sh
runs/{run_id}/scripts/06_coregister_scenes.sh
runs/{run_id}/scripts/07_rmli_average.sh
runs/{run_id}/scripts/08_diff_network.sh
runs/{run_id}/scripts/09_filter_unwrap.sh
runs/{run_id}/scripts/10_detrend_atm.sh
runs/{run_id}/scripts/11_sbas_inversion.sh
runs/{run_id}/scripts/12_outputs_points.sh
```

`workflow/jobs` submits one `SBAS_GAMMA_WORKFLOW` background job. The production workflow is now the twelve-section expert-document workflow. Several sections still reuse the already verified experiment scripts internally, but they are no longer hidden behind an eight-stage production view:

```text
01_workspace_data       -> expert workspace/data-layout check
02_import_lt1_slc       -> par_LT1_SLC / orbit correction
03_reference_mli        -> multi_look / reference MLI checks
04_dem_lookup           -> DEM import, lookup table, RDC height
05_coreg_prep           -> common-reference stack preparation
06_coregister_scenes    -> scene coregistration
07_rmli_average         -> RMLI tab/average intensity
08_diff_network         -> baseline network and differential phase
09_filter_unwrap        -> adaptive filtering, coherence and unwrap
10_detrend_atm          -> quad_fit / atm_mod_2d / sub_phase
11_sbas_inversion       -> mb / ts_rate, consuming DIFF_atmsub_tab
12_outputs_points       -> geocode, browse products, monitoring curve
```

Stack discovery and hard-constraint audit API:

```text
POST /api/sbas-insar-production/stacks/discover
POST /api/sbas-insar-production/stacks/{stack_id}/audit
```

`stacks/discover` scans LT1 source roots directly and groups scenes by:

- platform, for example `LT1A` or `LT1B`
- satellite mode, for example `MONO`
- receiving station
- relative orbit
- orbit direction
- imaging mode
- polarization
- center bucket, for example `E131.2_N43.8`

It also checks LT1 precise orbit TXT availability against `PYINT_ORBIT_POOL_TXT` / `ORBIT_POOL_ENVI`.

`stacks/{stack_id}/audit` writes a reproducible manifest under:

```text
backend/runtime/sbas_insar_production/stack_manifests/{stack_id}/
```

The manifest status is `READY_FOR_GAMMA_BASELINE_AUDIT` only after hard grouping, minimum scene count, precise orbit availability, and an initial adjacent temporal network are satisfied. Gamma `base_calc` remains the next required audit before final `itab` approval.

Writable production planning API:

```text
POST /api/sbas-insar-production/stacks/{stack_id}/runs
GET /api/sbas-insar-production/runs
GET /api/sbas-insar-production/runs/{run_id}
GET /api/sbas-insar-production/runs/{run_id}/artifacts/{relative_path}
```

The current `runs` submission is a dry-run planning submission. It writes:

```text
backend/runtime/sbas_insar_production/runs/{run_id}/run_manifest.json
backend/runtime/sbas_insar_production/runs/{run_id}/stack_manifest.json
backend/runtime/sbas_insar_production/runs/{run_id}/pair_network.json
backend/runtime/sbas_insar_production/runs/{run_id}/gamma_command_manifest.json
backend/runtime/sbas_insar_production/runs/{run_id}/monitor_points.json
```

The created run status is:

```text
PLANNED_GAMMA_BASELINE_AUDIT
```

This is intentionally not a Gamma execution trigger yet. The next runnable slice should add:

```text
POST /api/sbas-insar-production/runs/{run_id}/baseline-audit
POST /api/sbas-insar-production/runs/{run_id}/itab-decision
POST /api/sbas-insar-production/runs/{run_id}/coregistration
POST /api/sbas-insar-production/runs/{run_id}/coregistration/jobs
POST /api/sbas-insar-production/runs/{run_id}/monitor-points
POST /api/sbas-insar-production/runs/{run_id}/retry-stage
```

`baseline-audit` currently supports:

- script-only mode: generate/reparse `scripts/01_baseline_audit.sh` and existing outputs
- execution mode: run Gamma `par_LT1_SLC`, `LT1_precision_orbit.py`, `multi_look`, and `base_calc`
- output parsing: write `baseline_audit_summary.json` and `pair_network_baseline_audit.json`

`itab-decision` is the current production gate:

- `approve`: copies Gamma `work/gamma/diff/itab_adjacent` to `work/gamma/diff/itab_approved`, writes `itab_decision.json`, moves the run to `ITAB_APPROVED`, and makes `coregistration` the next stage
- `reject`: writes `itab_decision.json`, moves the run to `ITAB_REJECTED`, and blocks further Gamma stages until the pair network is revised

`coregistration` supports script generation. It writes:

```text
backend/runtime/sbas_insar_production/runs/{run_id}/scripts/02_coreg_common_ref.sh
backend/runtime/sbas_insar_production/runs/{run_id}/coregistration_plan.json
```

The generated script consumes `work/gamma/diff/itab_approved` as the approval gate, uses the stack reference date as common geometry, and prepares Gamma `SLC_coreg.py` calls for every non-reference date.

`coregistration/jobs` submits the generated script to the existing `SystemTask` + `SystemJob` background queue as job type `SBAS_COREGISTRATION`. The job runs Gamma `SLC_coreg.py`, writes `coregistration_summary.json`, updates the run manifest to `COREGISTRATION_READY` or `COREGISTRATION_FAILED`, and advances the next stage to `rdc_dem` only when all expected RSLC/RMLI outputs and common tab files exist.

## Backend Services

First slice:

```text
sbas_insar_production_service.py
  - discover local Gamma IPTA trial summaries
  - normalize products and artifact URLs
  - expose sign convention and product metadata
  - serve safe artifacts from trial roots
```

Second slice:

```text
gamma_ipta_stack_planner.py
  - hard group LT1 scenes by platform, relative orbit, direction, mode, polarization, center bucket
  - require precise orbit availability
  - emit stack_manifest.json

gamma_ipta_pair_planner.py
  - build initial temporal network
  - run Gamma baseline audit
  - emit pair_network.json and Gamma itab

gamma_ipta_job_runner.py
  - execute official Gamma commands stage by stage
  - write command manifests and logs

sbas_insar_product_publisher.py
  - publish GeoTIFF/BMP/CSV/JSON products
  - register products into unified result catalog
```

## Future Database Model

Use unified pipeline tables rather than adding many one-off SBAS tables:

```text
pipeline_runs
pipeline_stages
pipeline_products
pipeline_quality_metrics
pipeline_logs
```

Minimum fields for `pipeline_runs`:

- `run_id`
- `workflow_code = sbas_insar`
- `processor_code = gamma_ipta_sbas`
- `engine_code = gamma`
- `status`
- `stack_manifest_path`
- `work_root`
- `publish_root`
- `created_by`
- `created_at`
- `started_at`
- `ended_at`
- `summary_json`

For the first slice, use filesystem discovery only. Do not add migrations until the run submission workflow is ready.

## Gamma Stage Contract

The managed runner should preserve the successful trial chain:

1. `par_LT1_SLC`
2. `LT1_precision_orbit.py`
3. `multi_look`
4. `base_calc`
5. `SLC_coreg.py`
6. `gc_map1` / `geocode` / `gc_map_fine`
7. `phase_sim_orb`
8. `SLC_diff_intf`
9. `adf`
10. `mcf`
11. `mb`
12. `ts_rate`
13. `geocode_back`
14. `data2geotiff`
15. LOS sign conversion and preview generation
16. monitoring-point time-series extraction

The application is an orchestrator. Gamma remains the processing authority.

## Migration Plan

Phase 1: read-only SBAS production page

- add design document
- add backend API for existing Gamma trial discovery
- add page entry and product preview
- keep old time-series page available as legacy

Phase 2: managed run submission

- add stack discovery and audit endpoints
- add planned-run submission endpoint
- write production run manifest, command manifest, and monitor-point config
- add Gamma runner skeleton
- queue job with stage updates

Phase 3: unified pipeline management

- add generic pipeline tables
- move Gamma SBAS run records into pipeline tables
- register products through the unified product catalog

Phase 4: remove old SBAS/time-series pairing dependency

- hide old SBAS entry completely
- keep any reusable discovery functions as internal utilities
- delete obsolete UI and API routes after dependency audit

## Acceptance Criteria For Phase 1

- SBAS-InSAR production appears as its own production workspace view.
- Existing Gamma IPTA trial can be listed from the backend API.
- The selected trial shows LOS velocity preview, sigma/GeoTIFF products, monitor-point curve, and quality summary.
- Artifact serving is constrained to the trial root.
- No existing D-InSAR, flood, or legacy time-series routes are broken.

## Implementation Progress On 2026-05-19

Implemented:

- read-only SBAS-InSAR production page
- trial product browser for the local Gamma IPTA validation run
- artifact API constrained to published trial outputs
- LT1 filesystem stack discovery independent of the old time-series pairing layer
- hard grouping by platform, satellite mode, receiving station, relative orbit, orbit direction, imaging mode, polarization, and center bucket
- precise orbit TXT availability check against the configured Gamma/PyINT orbit pool
- stack manifest and initial adjacent pair-network JSON generation
- geocoded web previews generated from `los_rate_toward_mm_per_year.tif` and `los_sigma_mm_per_year.tif`
- planned SBAS production run creation from a READY stack manifest
- filesystem production run browser and artifact download API
- Gamma stage plan manifest with execution disabled until baseline audit runner is attached
- monitoring-point config contract with explicit placeholder status for `auto_low_sigma_high_rate`
- Gamma baseline audit script generation and output parser
- baseline audit result display in the SBAS production page
- itab approval/rejection API and page controls
- common-reference coregistration script generation and page summary
- queued `SBAS_COREGISTRATION` background job submission through the existing task/job queue
- coregistration execution summary parser and manifest status update to `COREGISTRATION_READY` / `COREGISTRATION_FAILED`

Local verification:

- scanned `1500` LT1 scene directories from the local data pool
- found READY candidates with all required precise orbit TXT files
- generated one manifest at:

```text
backend/runtime/sbas_insar_production/stack_manifests/sbas_2e6301f64a10/20260519T122146Z_stack_manifest.json
```

- created one dry-run production plan at:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/run_manifest.json
```

The dry-run production plan uses the local LT1B relOrbit `114` stack around `E129.2_N44.1`, with `7` scenes and `6` initial adjacent temporal pairs.

- baseline audit script:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/scripts/01_baseline_audit.sh
```

- baseline audit summary:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/baseline_audit_summary.json
```

The baseline audit ran Gamma 20240627 `par_LT1_SLC`, `LT1_precision_orbit.py`, `multi_look`, and `base_calc` against all 7 LT1B scenes. It completed with status `BASELINE_AUDIT_READY` after the outer terminal command timed out, because the WSL process continued to completion in the background.

Gamma `base_calc` adjacent-network result:

- all-pair count: `21`
- adjacent-pair count: `6`
- max absolute perpendicular baseline: `731.9957 m`
- max temporal gap: `224 days`

The current adjacent network is connected, but several Bperp values are large enough that a human baseline/quality review is still required before using this `itab` for the full SBAS inversion.

The current run has been approved for the next controlled trial step:

```text
status = ITAB_APPROVED
next_stage = coregistration
approved itab = backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/diff/itab_approved
decision record = backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/itab_decision.json
```

The common-reference co-registration script has been generated:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/scripts/02_coreg_common_ref.sh
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/coregistration_plan.json
```

The common-reference co-registration job was executed through the backend task/job queue:

```text
task_id = 047092bb-2c57-4639-8e8b-89ce925d3273
job_id = 15a66c9c-8814-4566-b299-12081f74ec09
task status = COMPLETED
job status = COMPLETED
last_error = None
```

Gamma `SLC_coreg.py` completed successfully for all expected secondary scenes. It consumed `itab_approved`, not the pre-audit or unapproved pair plan.

Generated post-job outputs:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/coregistration_summary.json
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/SLC_tab
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/RMLI_tab
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/rslc/*.rslc
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/rmli/*.mli
```

The coregistration summary reports:

```text
scene_count = 7
expected_secondary_count = 6
ready_secondary_count = 6
missing_dates = []
missing_tabs = []
ready = true
```

Current run state:

```text
status = COREGISTRATION_READY
next_stage = rdc_dem
```

Open product-display decisions:

- the current Gamma `*.bmp` previews are RDC processing-geometry products; keep them visible only as QA artifacts
- the first UI map preview should use `los_rate_toward_mm_per_year.geo_preview.png`
- the first sigma preview should use `los_sigma_mm_per_year.geo_preview.png`
- the current monitoring-point curve is a single automatic sample point, not a monitoring network
- production monitoring curves need user-selected lon/lat points, imported monitoring points, or a quality-filtered automatic sampler before they can be treated as formal outputs

## Post-Coregistration Production Design

Date: 2026-05-21

The next production slice should not jump directly from `COREGISTRATION_READY` to final velocity maps. It should keep the same gated pattern used for baseline audit and coregistration:

```text
generate stage script/plan -> submit background job -> parse summary -> advance manifest status
```

The remaining chain is split into four testable stages:

```text
rdc_dem -> interferograms -> ipta_timeseries -> publish_products
```

Each stage gets its own script, summary JSON, task/job type, page action, and artifact entries. This keeps operator testing bounded and avoids hiding a multi-hour Gamma failure inside a single monolithic job.

### Stage 3: RDC DEM

Stage id:

```text
rdc_dem
```

Allowed input state:

```text
run.status = COREGISTRATION_READY
run.next_stage = rdc_dem
```

Primary inputs:

- `work/gamma/common_20241007/SLC_tab`
- `work/gamma/common_20241007/RMLI_tab`
- reference date: `20241007`
- reference MLI parameter file from the common `RMLI_tab`
- project DEM source resolved from the configured DEM strategy

Do not hardcode the DEM used by the earlier `E131.2/N43.8` trial. That trial script reused:

```text
backend/runtime/pyint_dem_cache/.../lt1_20230602_20230720...dem
```

The active production run is centered near `E129.2/N44.1`, so stage 3 must resolve or build a DEM for this stack explicitly.

DEM resolution policy:

1. Prefer an explicitly configured Gamma-compatible or GDAL-readable DEM source.
2. Record the selected DEM source in `rdc_dem_plan.json`.
3. Generate or copy a local run-scoped DEM into:

```text
work/gamma/dem/
```

4. Fail early if the DEM cannot be read or does not cover the stack/reference geometry.

Gamma commands:

- `replace_values` when source DEM nodata cleanup is needed
- `gc_map1`
- `geocode`
- `create_diff_par`
- `init_offsetm`
- `offset_pwrm`
- `offset_fitm`
- `gc_map_fine`
- `geocode`

Expected files:

```text
scripts/03_prepare_rdc_dem.sh
rdc_dem_plan.json
rdc_dem_summary.json
logs/20241007_rdc_dem.log
work/gamma/dem/20241007_8rlks.utm.dem
work/gamma/dem/20241007_8rlks.utm.dem.par
work/gamma/dem/20241007_8rlks.UTM_TO_RDC
work/gamma/dem/20241007_8rlks.rdc.dem
work/gamma/dem/20241007_8rlks.diff_par
```

Success state:

```text
status = RDC_DEM_READY
next_stage = interferograms
```

Failure state:

```text
status = RDC_DEM_FAILED
next_stage = fix_rdc_dem
```

Summary checks:

- reference date exists in the common tabs
- reference MLI dimensions are parsed
- DEM source path is recorded
- `rdc.dem`, `UTM_TO_RDC`, `utm.dem.par`, and `diff_par` exist and are non-empty
- Gamma log tail is captured

User test request after this stage is implemented:

1. Open the SBAS production page and select `sbas_ab96afabead5`.
2. Confirm it shows `COREGISTRATION_READY` and `next_stage = rdc_dem`.
3. Click `提交 RDC DEM 任务`.
4. Wait for the task to complete.
5. Expected page result: `RDC_DEM_READY`, next stage `interferograms`.
6. Expected artifact: `rdc_dem_summary.json`.
7. If it fails, download or inspect `logs/20241007_rdc_dem.log`.

### Stage 4: Differential Interferograms

Stage id:

```text
interferograms
```

Allowed input state:

```text
run.status = RDC_DEM_READY
run.next_stage = interferograms
```

Primary inputs:

- `work/gamma/common_20241007/SLC_tab`
- `work/gamma/common_20241007/RMLI_tab`
- `work/gamma/common_20241007/itab_approved`
- `work/gamma/dem/20241007_8rlks.rdc.dem`

Pair source:

Use the approved Gamma `itab` rows. Do not regenerate pair choices from the old time-series pairing layer at this stage.

The current approved adjacent network is:

```text
1 2 1 1
2 3 2 1
3 4 3 1
4 5 4 1
5 6 5 1
6 7 6 1
```

Gamma commands per pair:

- `create_offset`
- `phase_sim_orb`
- `SLC_diff_intf`
- `adf`
- `cc_wave`
- `rasmph_pwr`
- `rasdt_pwr`
- `rascc_mask`
- `mcf`

Expected files:

```text
scripts/04_diff_unwrap_common_ref.sh
interferogram_plan.json
interferogram_summary.json
work/gamma/common_20241007/DIFF_tab
work/gamma/common_20241007/itab_ipta
work/gamma/common_20241007/diff/{pair}/{pair}_8rlks.diff
work/gamma/common_20241007/diff/{pair}/{pair}_8rlks.diff_filt
work/gamma/common_20241007/diff/{pair}/{pair}_8rlks.diff_filt.cor
work/gamma/common_20241007/diff/{pair}/{pair}_8rlks.diff_filt.unw
```

Success state:

```text
status = INTERFEROGRAMS_READY
next_stage = ipta_timeseries
```

Failure state:

```text
status = INTERFEROGRAMS_FAILED
next_stage = fix_interferograms
```

Summary checks:

- expected pair count equals approved `itab` row count
- every pair has `diff`, filtered diff, coherence, mask, and unwrapped phase
- `DIFF_tab` line count matches `itab_ipta` row count
- coherence statistics are recorded per pair
- failed pairs are listed explicitly

User test request after this stage is implemented:

1. Confirm the run shows `RDC_DEM_READY`.
2. Click `提交差分干涉图任务`.
3. Wait for completion.
4. Expected page result: `INTERFEROGRAMS_READY`, next stage `ipta_timeseries`.
5. Expected summary: `interferogram_summary.json` with `ready_pair_count = 6`.
6. Review any low-coherence warnings before continuing.

### Stage 5: IPTA Time-Series

Stage id:

```text
ipta_timeseries
```

Allowed input state:

```text
run.status = INTERFEROGRAMS_READY
run.next_stage = ipta_timeseries
```

Primary inputs:

- `work/gamma/common_20241007/DIFF_tab`
- `work/gamma/common_20241007/RMLI_tab`
- `work/gamma/common_20241007/itab_ipta`
- reference geometry MLI parameter file

Gamma commands:

- `mb`
- `ts_rate`

The first production implementation should reuse the trial-proven image-based `mb -> ts_rate` invocation shape, but with run-specific paths and the approved `itab`. It should record the exact reference parameter files used by `mb`, because this is a quality-sensitive detail.

Expected files:

```text
scripts/05_mb_ts_rate.sh
ipta_timeseries_plan.json
ipta_timeseries_summary.json
work/gamma/common_20241007/timeseries/diff_ts.tab
work/gamma/common_20241007/timeseries/itab_ts
work/gamma/common_20241007/timeseries/sigma_ts
work/gamma/common_20241007/timeseries/hgt_correction
work/gamma/common_20241007/timeseries/ts_rate
work/gamma/common_20241007/timeseries/ts_const
work/gamma/common_20241007/timeseries/sigma_rate
```

Success state:

```text
status = IPTA_TIMESERIES_READY
next_stage = publish_products
```

Failure state:

```text
status = IPTA_TIMESERIES_FAILED
next_stage = fix_ipta_timeseries
```

User test request after this stage is implemented:

1. Confirm the run shows `INTERFEROGRAMS_READY`.
2. Click `提交 IPTA 时序反演任务`.
3. Wait for completion.
4. Expected page result: `IPTA_TIMESERIES_READY`, next stage `publish_products`.
5. Expected summary: `ipta_timeseries_summary.json` showing `ts_rate` and `sigma_rate` exist.

### Stage 6: Publish Products

Stage id:

```text
publish_products
```

Allowed input state:

```text
run.status = IPTA_TIMESERIES_READY
run.next_stage = publish_products
```

Primary inputs:

- `work/gamma/common_20241007/timeseries/ts_rate`
- `work/gamma/common_20241007/timeseries/sigma_rate`
- `work/gamma/common_20241007/timeseries/sigma_ts`
- `work/gamma/common_20241007/timeseries/hgt_correction`
- `work/gamma/dem/20241007_8rlks.UTM_TO_RDC`
- `work/gamma/dem/20241007_8rlks.utm.dem.par`
- reference SLC parameter file for wavelength

Processing:

1. Compute wavelength from `radar_frequency`.
2. Convert phase-rate to LOS rate:

```text
los_rate_away_mm_per_year = phase_rate * wavelength / (4*pi) * 1000
los_rate_toward_mm_per_year = -phase_rate * wavelength / (4*pi) * 1000
```

3. Use `geocode_back` and `data2geotiff` to export EPSG:4326 GeoTIFFs.
4. Generate web preview PNGs from the geocoded GeoTIFFs, not from RDC BMPs.
5. Write product and quality summaries.

Expected files:

```text
scripts/06_publish_products.sh
publish_product_plan.json
publish_product_summary.json
quality_summary.json
publish/geotiff/ts_rate_rad_per_year.tif
publish/geotiff/sigma_rate_rad_per_year.tif
publish/geotiff/los_rate_toward_mm_per_year.tif
publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png
publish/geotiff/los_rate_away_mm_per_year.tif
publish/geotiff/los_sigma_mm_per_year.tif
publish/geotiff/los_sigma_mm_per_year.geo_preview.png
```

Success state:

```text
status = PRODUCTS_READY
next_stage = monitor_points
```

Failure state:

```text
status = PUBLISH_PRODUCTS_FAILED
next_stage = fix_publish_products
```

User test request after this stage is implemented:

1. Confirm the run shows `IPTA_TIMESERIES_READY`.
2. Click `发布 LOS 速率产品`.
3. Wait for completion.
4. Expected page result: `PRODUCTS_READY`.
5. Verify the UI default velocity and sigma previews are geocoded PNGs.
6. Download or open the GeoTIFFs in GIS if needed.
7. Treat RDC BMPs as processing QA only.

### Stage 7: Monitor Points

This should not block the first full production result.

The current automatic sample point is only a debug placeholder. Formal monitoring curves require at least one of:

- user-provided lon/lat points
- imported monitoring-point layer
- explicitly approved quality-filtered automatic sampler

Until then, the UI should label monitor curves as sample/debug output, not formal business monitoring points.

### Implementation Order

Recommended next coding order:

1. Add backend `RDC_DEM` stage: plan generation, script generation, queued job, summary parser, manifest update.
2. Add frontend actions and status display for the `rdc_dem` stage.
3. Ask the user to test only `RDC DEM` on `sbas_ab96afabead5`.
4. After that succeeds, implement the interferogram stage.
5. After interferograms succeed, implement IPTA time-series.
6. After IPTA succeeds, implement product publishing and geocoded previews.

This staged order is intentionally conservative. It keeps each Gamma failure surface small enough to diagnose from one stage log and one summary JSON.

## 2026-05-22 RDC DEM Implementation Note

The formal SBAS production workflow now includes a managed `rdc_dem` stage.

Implemented code paths:

- Backend service methods:
  - `prepare_rdc_dem`
  - `execute_rdc_dem`
  - `_write_rdc_dem_script`
  - `_build_rdc_dem_summary`
  - `_refresh_command_manifest_after_rdc_dem`
- FastAPI endpoints:
  - `POST /api/sbas-insar-production/runs/{run_id}/rdc-dem`
  - `POST /api/sbas-insar-production/runs/{run_id}/rdc-dem/jobs`
- Background job type:
  - `SBAS_RDC_DEM`
- Frontend:
  - `生成 RDC DEM 脚本`
  - `提交 RDC DEM 任务`
  - `RDC DEM Plan` status card

The generated script is intentionally based on the successful trial script:

```text
gc_map1
geocode simulated SAR to RDC
create_diff_par
init_offsetm
offset_pwrm
offset_fitm
gc_map_fine
geocode DEM to RDC
```

The current `.env` DEM path points to the large SARscape/GDAL raster:

```text
D:\DEM\SRTMDEM_RSP_SARscape.wgs84
```

That file is not a Gamma `.dem + .dem.par` pair. For this implementation slice, the production stage selects an existing Gamma-format PyINT DEM cache when no explicit Gamma DEM source is configured. On the current machine, the selected LT1 cache covers the stack center and follows the same PyINT/Gamma DEM format that the successful experiment used.

Known limitation:

- The existing LT1 PyINT DEM cache covers the stack center but may not fully cover the union of every scene bbox south edge. The plan records both `covers_stack_center` and `covers_stack_bbox`. If the RDC DEM stage fails or creates edge voids, the next fix should generate a fresh Gamma DEM from `D:\DEM\SRTMDEM_RSP_SARscape.wgs84` over the full SBAS stack bbox plus margin, instead of relying on older pair-level DEM caches.

Local validation completed:

- Python AST parse passed for:
  - `backend/app/services/sbas_insar_production_service.py`
  - `backend/app/routers/sbas_insar_production.py`
  - `backend/app/services/job_handlers.py`
- `SBAS_RDC_DEM` job handler registration resolves successfully.
- Frontend `npm run build` passed after running with the permission needed for Vite/esbuild subprocess spawn.

Manual test order for the user:

1. Restart backend and frontend if they are already running.
2. Open the SBAS-InSAR production page.
3. Select run `sbas_ab96afabead5`.
4. Confirm status is `COREGISTRATION_READY` or `RDC_DEM_SCRIPT_READY`.
5. Click `生成 RDC DEM 脚本`.
6. Confirm `RDC DEM Plan` appears and points to `scripts/03_prepare_rdc_dem.sh`.
7. Click `提交 RDC DEM 任务`.
8. Watch the task until completion.
9. Expected success:

```text
run.status = RDC_DEM_READY
run.next_stage = interferograms
rdc_dem_summary.ready = true
```

Expected output files:

```text
work/gamma/dem/20241007_8rlks.utm.dem
work/gamma/dem/20241007_8rlks.utm.dem.par
work/gamma/dem/20241007_8rlks.UTM_TO_RDC
work/gamma/dem/20241007_8rlks.rdc.dem
work/gamma/dem/20241007_8rlks.diff_par
logs/20241007_rdc_dem.log
rdc_dem_summary.json
```

If the job fails, inspect:

```text
logs/20241007_rdc_dem.log
rdc_dem_summary.json
```

The next production coding stage after `RDC_DEM_READY` is differential interferogram generation.

## 2026-05-23 Interferogram Stage Implementation Note

The formal SBAS production workflow now includes a managed `interferograms` stage after `RDC_DEM_READY`.

Implemented code paths:

- Backend service methods:
  - `prepare_interferograms`
  - `execute_interferograms`
  - `_write_interferogram_script`
  - `_build_interferogram_summary`
  - `_build_interferogram_pair_plan`
  - `_refresh_command_manifest_after_interferograms`
- FastAPI endpoints:
  - `POST /api/sbas-insar-production/runs/{run_id}/interferograms`
  - `POST /api/sbas-insar-production/runs/{run_id}/interferograms/jobs`
- Background job type:
  - `SBAS_INTERFEROGRAMS`
- Frontend:
  - Adds an action to generate the interferogram script.
  - Adds an action to submit the interferogram background job.
  - Adds an `Interferogram Plan` status card.

This stage is intentionally derived from the successful Gamma trial script `08_diff_unwrap_common_ref.sh`. The production script keeps the same Gamma command chain:

```text
create_offset
phase_sim_orb
SLC_diff_intf
adf
cc_wave
rasmph_pwr
rasdt_pwr
rascc_mask
mcf
rasdt_pwr
```

The production differences from the trial are:

- It reads the approved production `itab_approved` instead of hardcoding a trial date list.
- It writes production `DIFF_tab` and `itab_common_ref` for the next `mb` and `ts_rate` stage.
- It records a JSON plan and JSON execution summary under the run directory.
- It keeps all SLCs/RMLIs in the common reference geometry prepared by the coregistration stage.

Generated files for the current test run:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/interferogram_plan.json
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/scripts/04_diff_unwrap_common_ref.sh
```

Current generated plan:

```text
run_id = sbas_ab96afabead5
status = INTERFEROGRAMS_SCRIPT_READY
next_stage = execute_interferograms
reference_date = 20241007
pair_count = 6
pairs =
  20240422_20240617
  20240617_20240812
  20240812_20241007
  20241007_20250519
  20250519_20250714
  20250714_20250908
```

Expected output interface after successful execution:

```text
work/gamma/common_20241007/DIFF_tab
work/gamma/common_20241007/itab_common_ref
work/gamma/common_20241007/diff/<pair>/<pair>_8rlks.diff_filt.unw
work/gamma/common_20241007/diff/<pair>/<pair>_8rlks.diff_filt.cor
interferogram_summary.json
```

Local validation completed:

- Python AST parse passed for:
  - `backend/app/services/sbas_insar_production_service.py`
  - `backend/app/routers/sbas_insar_production.py`
  - `backend/app/services/job_handlers.py`
- `SBAS_INTERFEROGRAMS` job handler registration resolves successfully.
- The generated `04_diff_unwrap_common_ref.sh` passes `bash -n` in WSL.
- Frontend `npm run build` passed after running with the permission needed for Vite/esbuild subprocess spawn.

Manual test order for the user:

1. Restart backend and frontend if they are already running.
2. Open the SBAS-InSAR production page.
3. Select run `sbas_ab96afabead5`.
4. Confirm status is `INTERFEROGRAMS_SCRIPT_READY`.
5. Confirm `Interferogram Plan` shows 6 pairs and `reference_date = 20241007`.
6. Click the interferogram background-job button.
7. Watch the task until completion.
8. Expected success:

```text
run.status = INTERFEROGRAMS_READY
run.next_stage = ipta_timeseries
interferogram_summary.ready = true
interferogram_summary.ready_pair_count = 6
```

If the job fails, inspect:

```text
logs/<pair>_diff_unwrap_common.log
interferogram_summary.json
work/gamma/common_20241007/DIFF_tab
work/gamma/common_20241007/itab_common_ref
```

Known risk to verify in the production test:

- The trial script proved the command chain with reference-star pairs. The formal production stage uses the approved adjacent `itab` network, including secondary-secondary pairs after common-reference coregistration. This is the right SBAS topology, but the next live test should confirm Gamma accepts the secondary-secondary pair geometry with the current `phase_sim_orb` inputs. If Gamma rejects that geometry, the next correction is to adapt the pair topology or DEM geometry handling while keeping the same managed stage boundary.

The next production coding stage after `INTERFEROGRAMS_READY` is IPTA time-series inversion with `mb` and `ts_rate`.

## 2026-05-24 Interferogram Result And IPTA Stage Implementation Note

The current production run `sbas_ab96afabead5` completed the managed interferogram stage successfully.

Observed result:

```text
run.status = INTERFEROGRAMS_READY
run.next_stage = ipta_timeseries
interferogram_summary.ready = true
interferogram_summary.ready_pair_count = 6
interferogram_summary.pair_count = 6
DIFF_tab rows = 6
itab_common_ref rows = 6
```

All six approved SBAS pairs produced the required Gamma outputs:

```text
<pair>_8rlks.off
<pair>.sim_unw
<pair>_8rlks.diff
<pair>_8rlks.diff_filt
<pair>_8rlks.diff_filt.cor
<pair>_8rlks.diff_filt.cor_mask.bmp
<pair>_8rlks.diff_filt.unw
```

This confirms that the production adjacent-pair SBAS topology works with the current common-reference geometry, not only the reference-star topology from the earlier experiment.

The formal SBAS production workflow now includes a managed `ipta_timeseries` stage.

Implemented code paths:

- Backend service methods:
  - `prepare_ipta_timeseries`
  - `execute_ipta_timeseries`
  - `_write_ipta_timeseries_script`
  - `_build_ipta_timeseries_summary`
  - `_select_ipta_mb_reference_mli`
  - `_refresh_command_manifest_after_ipta_timeseries`
- FastAPI endpoints:
  - `POST /api/sbas-insar-production/runs/{run_id}/ipta-timeseries`
  - `POST /api/sbas-insar-production/runs/{run_id}/ipta-timeseries/jobs`
- Background job type:
  - `SBAS_IPTA_TIMESERIES`
- Frontend:
  - Adds an action to generate the IPTA script.
  - Adds an action to submit the IPTA background job.
  - Adds an `IPTA Time-Series Plan` status card.

This stage is derived from the successful Gamma trial script `09_mb_ts_rate.sh`. The production script keeps the same Gamma command chain:

```text
mb
ts_rate
```

The production differences from the trial are:

- It uses run-specific `DIFF_tab`, `RMLI_tab`, and `itab_common_ref`.
- It records the two `mb` reference parameter files explicitly:
  - geometry reference MLI parameter file
  - nearest non-reference common-RMLI parameter file used as the `mb` reference parameter
- It writes `ipta_timeseries_plan.json` and `ipta_timeseries_summary.json`.
- It updates the command manifest stage plan and enables the next `publish_products` stage only after `IPTA_TIMESERIES_READY`.

Generated files for the current test run:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/ipta_timeseries_plan.json
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/scripts/05_mb_ts_rate.sh
```

Current generated plan:

```text
run_id = sbas_ab96afabead5
status = IPTA_TIMESERIES_SCRIPT_READY
next_stage = execute_ipta_timeseries
reference_date = 20241007
geometry_reference_mli_par = work/gamma/mli/20241007.mli.par
mb_reference_mli_par = work/gamma/common_20241007/rmli/20240812.mli.par
```

Expected output interface after successful execution:

```text
work/gamma/common_20241007/timeseries/diff_ts.tab
work/gamma/common_20241007/timeseries/itab_ts
work/gamma/common_20241007/timeseries/sigma_ts
work/gamma/common_20241007/timeseries/hgt_correction
work/gamma/common_20241007/timeseries/ts_rate
work/gamma/common_20241007/timeseries/ts_const
work/gamma/common_20241007/timeseries/sigma_rate
ipta_timeseries_summary.json
```

Local validation completed:

- Python AST parse passed for:
  - `backend/app/services/sbas_insar_production_service.py`
  - `backend/app/routers/sbas_insar_production.py`
  - `backend/app/services/job_handlers.py`
- `SBAS_IPTA_TIMESERIES` job handler registration resolves successfully.
- The generated `05_mb_ts_rate.sh` passes `bash -n` in WSL.
- Frontend `npm run build` passed after running with the permission needed for Vite/esbuild subprocess spawn.

Manual test order for the user:

1. Restart backend and frontend if they are already running.
2. Open the SBAS-InSAR production page.
3. Select run `sbas_ab96afabead5`.
4. Confirm status is `IPTA_TIMESERIES_SCRIPT_READY`.
5. Confirm `IPTA Time-Series Plan` shows `reference_date = 20241007`.
6. Click the IPTA background-job button.
7. Watch the task until completion.
8. Expected success:

```text
run.status = IPTA_TIMESERIES_READY
run.next_stage = publish_products
ipta_timeseries_summary.ready = true
```

If the job fails, inspect:

```text
logs/mb_ts_rate.log
ipta_timeseries_summary.json
work/gamma/common_20241007/timeseries/
```

The next production coding stage after `IPTA_TIMESERIES_READY` is product publishing: geocoding `ts_rate` and `sigma_rate`, LOS sign conversion, GeoTIFF generation, web previews, and monitoring-point curve extraction.

## 2026-05-25 IPTA Failure Triage And Reference-Region Fix

The first production IPTA run for `sbas_ab96afabead5` failed inside Gamma `mb`.

Observed result:

```text
run.status = IPTA_TIMESERIES_FAILED
run.next_stage = fix_ipta_timeseries
execution.returncode = 139
logs/mb_ts_rate.log = Segmentation fault in mb
```

This was not a system-side summary false negative. Gamma `mb` read the input tables successfully:

```text
DIFF_tab records = 6
itab_common_ref records = 6
RMLI_tab entries = 7
```

The failure happened after `mb` printed the reference region and before writing valid `diff_ts.tab` / `itab_ts`. The initial production script reused the experiment's center-pixel reference region:

```text
range = width / 2
azimuth = lines / 2
window = 16 x 16
```

That is fragile for real production stacks. On this run, the center reference window contained zero/invalid unwrapped pixels in multiple interferograms. Gamma `mb` did not emit a clean validation error; it segfaulted.

Implemented fix:

- `prepare_ipta_timeseries` now automatically scans the unwrapped interferograms listed in `DIFF_tab`.
- It selects a 16 x 16 reference window with valid nonzero unwrapped values across all pairs and high mean coherence.
- The selected region is written into `ipta_timeseries_plan.json` as `reference_region`.
- `05_mb_ts_rate.sh` now uses the selected `R_REF` / `A_REF` instead of the image center.
- The script removes stale IPTA outputs before running so a retry starts from clean stage outputs.

Selected reference region for the current run:

```text
strategy = auto_valid_unwrapped_high_coherence_window
range_pixel = 2152
azimuth_line = 1512
window = 16 x 16
pair_count = 6
min_valid_pixel_count = 256
total_valid_pixel_count = 1536
median_mean_coherence = 0.9953643755
valid_pixel_count_by_pair = [256, 256, 256, 256, 256, 256]
```

Current retry-ready state:

```text
run.status = IPTA_TIMESERIES_SCRIPT_READY
run.next_stage = execute_ipta_timeseries
scripts/05_mb_ts_rate.sh uses R_REF=2152 and A_REF=1512
```

Validation completed after the fix:

- Python AST parse passed.
- `SBAS_IPTA_TIMESERIES` job handler resolves successfully.
- Regenerated `05_mb_ts_rate.sh` passes WSL `bash -n`.
- Frontend `npm run build` passed.

User retry order:

1. Refresh the SBAS-InSAR production page.
2. Select run `sbas_ab96afabead5`.
3. Confirm status is `IPTA_TIMESERIES_SCRIPT_READY`.
4. Confirm `IPTA Time-Series Plan` contains reference region `2152,1512`.
5. Submit the IPTA task again.

If the retry still fails, inspect:

```text
logs/mb_ts_rate.log
ipta_timeseries_summary.json
work/gamma/common_20241007/timeseries/
```

## 2026-05-25 IPTA Mode-1 Failure Closure

The reference-region fix was necessary but not sufficient. The second production retry still failed inside Gamma `mb` with return code `139`.

The regenerated plan was correct:

```text
reference_region = 2152,1512
window = 16 x 16
valid_pixel_count_by_pair = [256, 256, 256, 256, 256, 256]
median_mean_coherence = 0.9953643755
```

So the remaining failure was not an invalid reference window. A focused Gamma diagnostic matrix was run against the exact production `DIFF_tab`, `RMLI_tab`, and `itab_common_ref` for run `sbas_ab96afabead5`.

Diagnostic result:

```text
full stack, mb mode=1 -> rc=139, segmentation fault
full stack, mb mode=2 -> rc=139, segmentation fault
full stack, mb mode=0 -> rc=0
reference-star subset, mb mode=1 -> rc=0
first-3 adjacent-chain subset, mb mode=1 -> rc=0
single-pair subsets -> non-production diagnostic only, not valid as full stack inversion
```

A production-equivalent diagnostic was then run with the same output flags as the experiment script:

```text
mb sim_flg=1 hgt_flg=1 mode=0
ts_rate
```

That completed successfully and produced:

```text
diff_ts.tab
itab_ts
sigma_ts
hgt_correction
ts_rate
ts_const
sigma_rate
```

The important conclusion is that the original Gamma trial was valid, but the formal production input is not identical to the trial. The trial used a smaller/reference-star style network that Gamma `mb mode=1` accepted. The production run uses the approved full adjacent SBAS chain with 7 dates and 6 interferograms. On the current Gamma 2023 IPTA binary, that full production chain crashes in `mode=1`, while `mode=0` completes.

Implemented closure:

- Production default `mb_mode` is now `0`.
- `ipta_timeseries_plan.json` records:

```text
mb_mode = 0
mb_mode_description = valid unwrapped phase values required in all layers
```

- `05_mb_ts_rate.sh` writes and uses:

```text
MB_MODE="0"
```

- `ipta_timeseries_summary.json` records the mode used by the completed run.
- Router and background job payloads accept `mb_mode`, defaulting to `0`. The UI does not expose this as a normal operator choice yet.
- Script generation now has a fallback path if an existing script file cannot be overwritten because of Windows/WSL ACL drift.

Current formal service execution result after the fix:

```text
run_id = sbas_ab96afabead5
run.status = IPTA_TIMESERIES_READY
run.next_stage = publish_products
execution.returncode = 0
summary.ready = true
summary.missing_outputs = []
summary.mb_mode = 0
diff_ts_row_count = 7
itab_ts_row_count = 7
```

Output size checks:

```text
expected_float32_bytes = 45921036
sigma_ts = 45921036
hgt_correction = 45921036
ts_rate = 45921036
ts_const = 45921036
sigma_rate = 45921036
```

Operational note:

During local Codex verification, a direct Windows Python subprocess call to `wsl.exe` returned `Wsl/Service/E_ACCESSDENIED` unless the command was run with external execution permission. That was an execution-context permission issue, not a Gamma processing failure. The same service method completed when WSL execution was allowed. If the production worker reports `Wsl/Service/E_ACCESSDENIED`, fix the worker account/Windows service permissions for WSL access before re-testing Gamma processing.

One legacy run-directory ACL issue was also fixed manually for `sbas_ab96afabead5`: older files had ACLs that let the current Windows user create new files but not overwrite existing manifest/script files. The current run directory was granted current-user full control so `run_manifest.json`, `ipta_timeseries_plan.json`, and generated scripts can be updated on retries.

Next stage after this closure is product publishing:

```text
publish_products
geocode_back ts_rate/sigma_rate
data2geotiff
LOS velocity/sigma products
monitoring point curves
```

## 2026-05-26 Product Publishing And Monitoring Point Integration

The expert-workflow bridge now implements the downstream output work that was previously planned-only. In the current twelve-node workflow these are represented by section 12:

```text
12_outputs_points
```

`10_detrend_atm` is now part of the new development workflow. It is no longer treated as an optional compatibility branch. New production runs should execute section 10 before section 11, and `11_sbas_inversion` should consume `DIFF_atmsub_tab`.

### 12_outputs_points publish phase

The stage generates and executes:

```text
runs/{run_id}/scripts/12_outputs_points.sh
```

It follows the experiment-proven path:

- compute wavelength from `radar_frequency`
- convert `ts_rate` / `sigma_rate` phase rates to LOS
- write both LOS sign conventions:

```text
los_rate_away_mm_per_year = phase_rate * wavelength / (4*pi) * 1000
los_rate_toward_mm_per_year = -phase_rate * wavelength / (4*pi) * 1000
```

- geocode with Gamma `geocode_back`
- export GeoTIFFs with Gamma `data2geotiff`
- create RDC QA browse BMPs with `rasdt_pwr`
- create UI map previews from geocoded GeoTIFFs, not from RDC BMPs

Expected output state:

```text
run.status = PRODUCTS_READY
run.next_stage = monitor_points
```

Expected files:

```text
publish_product_plan.json
publish_product_summary.json
product_summary.json
quality_summary.json
publish/geotiff/los_rate_toward_mm_per_year.tif
publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png
publish/geotiff/los_rate_away_mm_per_year.tif
publish/geotiff/los_sigma_mm_per_year.tif
publish/geotiff/los_sigma_mm_per_year.geo_preview.png
publish/geotiff/ts_rate_rad_per_year.tif
publish/geotiff/sigma_rate_rad_per_year.tif
```

The summary records nonzero finite pixel statistics for LOS velocity and sigma in RDC geometry so the operator can quickly detect blank or extreme outputs.

### 12_outputs_points monitoring-point phase

The wrapper also runs the monitoring-point extraction script when product publishing has completed:

```text
runs/{run_id}/scripts/08_point_timeseries.sh
```

It reuses the experiment logic in a parameterized WSL helper:

```text
deploy/wsl/runners/gamma_sbas_product_tools.py
```

Supported point modes in this slice:

- `auto_low_sigma_high_rate`: automatic non-edge sample point, high absolute LOS velocity and low sigma
- `manual_lonlat`: nearest lookup-table pixel from configured lon/lat points

The automatic sample remains a diagnostic/sample curve, not a formal monitoring network.

Expected output state:

```text
run.status = MONITOR_POINTS_READY
run.next_stage = review_publish_products
```

Expected files:

```text
monitor_points_plan.json
monitor_points_summary.json
publish/monitor_points/{point_id}_timeseries.png
publish/monitor_points/{point_id}_timeseries.csv
publish/monitor_points/{point_id}_metadata.json
```

### Workflow Result

After 10, 11, and 12 complete, the workflow can finish as:

```text
WORKFLOW_COMPLETED
```

instead of `WORKFLOW_PARTIAL`, provided no enabled stage failed.

Manual test order:

1. Select run `sbas_ab96afabead5`.
2. Submit Gamma SBAS Workflow with:

```text
from_step = 12_outputs_points
```

3. Expected first completion:

```text
PRODUCTS_READY
```

or full completion:

```text
MONITOR_POINTS_READY
WORKFLOW_COMPLETED
```

4. Confirm the production Run detail shows geocoded LOS velocity and sigma previews.
5. Confirm the monitoring curve is visible and its CSV is downloadable.

## 2026-05-26 Expert Document Step Index And Color Convention Update

The user correctly pointed out that the expert document is not an eight-step process. The document has twelve major sections, each with multiple Gamma commands. The production workflow now uses those twelve sections as first-class workflow nodes. The previous eight-stage execution view is retained only as an internal service implementation detail where an already verified experiment script covers more than one expert section.

The twelve expert sections now appear in `capabilities`, `manifest.json`, `gamma_command_manifest.json`, and the SBAS production page:

```text
1. Directory and LT1 data preparation
2. Import every LT1 SLC
3. Reference MLI and footprint checks
4. DEM import and lookup table
5. SLC coregistration preparation
6. Coregister every SLC to reference
7. RMLI stack and average intensity
8. Interferogram network and differential phase
9. Adaptive filtering, coherence mask and unwrap
10. Detrend and atmospheric phase removal
11. SBAS inversion
12. Output, geocode and point time-series
```

Each section records representative commands from `LT1_GAMMA_SBAS_逐命令处理流程.docx`, mapped workflow stages, and implementation status. Existing successful experiment logic is retained as `implemented_bridge` where it has already proven the same Gamma function, even if the file layout is not yet identical to the document.

Current acceptance status:

```text
1-4  implemented_bridge or implemented
5-9  implemented_bridge
10   implemented_bridge: quad_fit/quad_sub/atm_mod_2d/fill_gaps/atm_sim_2d/sub_phase now writes DIFF_atmsub_tab; needs live production validation
11   implemented_bridge: mb/ts_rate now prefers DIFF_atmsub_tab; the full expert multi-pass unw_to_cpx/unw_model refinement is still not fully migrated
12   implemented_bridge: publish and monitor outputs exist; Gamma expert browse color products are now added
```

Color and browse products were corrected toward the expert document conventions. Velocity browse products now prefer Gamma `rasdt_pwr` with `hls.cm` and the expert range `-0.08 0.08` m/year. Sigma/quality browse products now use `cc.cm`; because production currently displays LOS sigma-rate rather than `diff.sigma_ts.masked`, the range is adapted to `0.0 0.06` m/year while retaining the expert color table family. Phase, detrend, and atmospheric browse products should use `rmg.cm` with `-6.28 6.28` radians when section 10 is implemented.

New preferred publish outputs:

```text
publish/geotiff/los_rate_toward_m_per_year.hls.bmp
publish/geotiff/los_rate_toward_m_per_year.hls.geo_rgb.tif
publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png
publish/geotiff/los_sigma_m_per_year.cc.bmp
publish/geotiff/los_sigma_m_per_year.cc.geo_rgb.tif
publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png
publish/geotiff/los_rate_toward_m_per_year.tif
publish/geotiff/los_rate_away_m_per_year.tif
publish/geotiff/los_sigma_m_per_year.tif
```

Legacy millimeter-per-year products remain published for comparison with earlier experiments:

```text
publish/geotiff/los_rate_toward_mm_per_year.tif
publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png
publish/geotiff/los_sigma_mm_per_year.tif
publish/geotiff/los_sigma_mm_per_year.geo_preview.png
```

Next implementation target is the rest of section 11. The managed section 10 node now generates `unw.atmsub` products and `DIFF_atmsub_tab`; section 11 consumes that table. The remaining gap is the expert multi-pass `unw_to_cpx` / `unw_model` refinement path.

## 2026-05-26 Twelve-Node Production Workflow Correction

The production workflow has been corrected from the temporary `8 coarse stages + 12-section checklist` view to a twelve-node workflow:

```text
01_workspace_data
02_import_lt1_slc
03_reference_mli
04_dem_lookup
05_coreg_prep
06_coregister_scenes
07_rmli_average
08_diff_network
09_filter_unwrap
10_detrend_atm
11_sbas_inversion
12_outputs_points
```

The bridge still reuses verified scripts from the successful experiment where that is safer than rewriting Gamma command chains immediately:

```text
02-03 reuse the baseline-audit import/multilook/base_calc implementation.
05-07 reuse the common-reference coregistration implementation.
08-09 reuse the differential interferogram/filter/unwrap implementation.
10 uses the expert detrend/atmospheric-correction implementation.
11 uses mb/ts_rate over DIFF_atmsub_tab.
12 runs publish products followed by monitoring-point extraction.
```

Dependency handling is no longer a simple linear coarse-stage status check. Section 4 can run after baseline/reference MLI preparation; sections 8-9 require both the DEM stage execution and the coregistration stage execution to be recorded as completed in the current run manifest.

Old experiment outputs are not accepted as expert-path validation. Development runs may be deleted and regenerated; the twelve-node workflow should be validated from a clean run so that each node has a fresh execution record. Existing summary JSON files may remain as operator evidence, but the workflow runner must not use them to skip expert nodes.

## 2026-05-26 Expert Section 10 Detrend/ATM Stage

The formal workflow order is now:

```text
08_diff_network
09_filter_unwrap
10_detrend_atm
11_sbas_inversion
12_outputs_points
```

`10_detrend_atm` writes:

```text
detrend_atm_plan.json
detrend_atm_summary.json
work/gamma/common_<ref>/DIFF_atmsub_tab
work/gamma/common_<ref>/itab_atmsub
work/gamma/common_<ref>/detrend_atm/<pair>/<pair>_<rlks>rlks.diff_filt.unw.atmsub
```

The stage follows the expert section 10 command family:

```text
create_diff_par
quad_fit
quad_sub
rasdt_pwr ... rmg.cm
atm_mod_2d
fill_gaps
atm_sim_2d
sub_phase
rasdt_pwr ... rmg.cm
```

The `fill_gaps` width for `a0/a1` model grids is inferred from the generated coefficient-file size and the reference MLI aspect ratio. If inference or `fill_gaps` fails, the script falls back to the raw atmospheric coefficients and records the warning in the pair log; this keeps the production test actionable while preserving the expert command path.

`11_sbas_inversion` now requires `DETREND_ATM_READY` and uses:

```text
DIFF_TAB = work/gamma/common_<ref>/DIFF_atmsub_tab
ITAB     = work/gamma/common_<ref>/itab_atmsub
```

Production test expectation:

```text
run.status after 10 = DETREND_ATM_READY
run.next_stage after 10 = ipta_timeseries
run.status after 11 = IPTA_TIMESERIES_READY
```

## 2026-05-27 Runtime Cleanup And Strict Production Display

The previous development/test outputs were removed so the next SBAS-InSAR test starts from a clean runtime state:

```text
backend/runtime/sbas_insar_production/discoveries/*
backend/runtime/sbas_insar_production/runs/*
backend/runtime/sbas_insar_production/stack_manifests/*
backend/runtime/gamma_ipta_trials/*
backend/runtime/gamma_ipta_probe/*
```

The frontend SBAS-InSAR production page no longer lists or opens `trial-runs`. It now loads only managed production `runs`, and product links use `/api/sbas-insar-production/runs/{run_id}/artifacts/...`.

The workflow runner is intentionally strict after this cleanup. Old experiment summaries or sidecar `*_summary.json` files are not accepted as proof that an expert node has completed. A node can be skipped or advanced only when the current run manifest contains a completed execution record for the corresponding stage.

## 2026-05-27 Result Management And Geographic Coverage Design

The first clean Gamma SBAS workflow run completed all twelve expert nodes:

```text
run_id = sbas_7537cc71c998
status = WORKFLOW_COMPLETED
scene_count = 7
pair_count = 6
workflow_summary.completed_count = 12
workflow_summary.failed_count = 0
```

This means production execution is now viable enough to split the user experience into two modules:

```text
SBAS-InSAR Production
  - discover stack candidates
  - create production Run
  - submit Gamma SBAS workflow
  - inspect 12 expert nodes, scripts, logs, and retry state

SBAS-InSAR Results
  - browse stable products
  - inspect geographic footprint and map location
  - preview LOS velocity, LOS sigma, and monitoring curves
  - download GeoTIFF/PNG/CSV/supporting manifests
  - jump back to the source production Run for audit/debug
```

### Geographic Coverage Gap

SBAS is time-series processing, so temporal density is important, but result users primarily ask "where is this product?". The current production page does not make geographic location obvious even though the metadata already exists.

Available geographic sources in the current run:

```text
stack_manifest.scenes[*].bbox
stack_manifest.scenes[*].center_lon / center_lat
stack_manifest.stack.center_bucket
rdc_dem_summary.dem_source.stack_bbox
monitor_points_summary.monitor_outputs[*].metadata.approx_lonlat
published GeoTIFF bounds from GDAL metadata
```

Example from `sbas_7537cc71c998`:

```text
stack center bucket = E129.2_N44.1
stack bbox          = 128.7690438245, 43.7486321624, 129.6293024728, 44.3582486206
monitor point      = 129.10207098755, 44.15041727515
```

The production page should add a compact `Geographic Coverage` block near the selected Run summary:

```text
center lon/lat
stack bbox
scene footprint count
DEM coverage status: covers stack bbox / covers stack center
monitor point lon/lat if generated
open on map / zoom to footprint action
```

This block is operational context, not a replacement for a result browser. It helps the operator avoid running or reviewing the wrong location.

### Result Product Boundary

One completed SBAS workflow Run should register one result product bundle, not many separate products. This follows the existing D-InSAR/PsInSAR catalog pattern:

```text
result_products: one row per SBAS bundle
result_assets:   multiple files under the bundle
result_issues:   missing/invalid/geocoding/quality warnings
catalog_name:    sbas_insar
run_key:         source SBAS run_id
```

The product record should carry first-class query fields:

```text
platform / satellite
relative_orbit
orbit_direction
polarization
reference_date
start_date
end_date
scene_count
pair_count
bbox_min_lon / bbox_min_lat / bbox_max_lon / bbox_max_lat
center_lon / center_lat
status / health_status
primary_asset_role
quality_asset_role
source_run_id
```

Do not treat every GeoTIFF as a separate product row. The user-facing product is the SBAS result for one stack/run over one geographic footprint and time span.

### Important Assets From The Expert Document

The expert document makes section 12 outputs the formal product boundary. Important product roles:

```text
primary_velocity_geotiff
  expert source: geo_los_def_rate.tif
  current system: publish/geotiff/los_rate_toward_m_per_year.tif

primary_velocity_rgb_geotiff
  expert source: geo_los_def_rate_rgb.tif
  current system: publish/geotiff/los_rate_toward_m_per_year.hls.geo_rgb.tif

primary_velocity_preview
  expert source: los_def_rate.bmp / geo_los_def_rate.bmp
  current system: publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png

quality_sigma_geotiff
  expert source: diff.sigma_ts / geo_diff.sigma_ts
  current system: publish/geotiff/los_sigma_m_per_year.tif

quality_sigma_preview
  expert source: diff.sigma_ts.masked.bmp with cc.cm
  current system: publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png

monitor_timeseries_csv
  expert source: disp_prt_2d outputs
  current system: publish/monitor_points/*_timeseries.csv

monitor_timeseries_plot
  current system: publish/monitor_points/*_timeseries.png

support_manifest
  run_manifest.json, workflow_summary.json, gamma_command_manifest.json, stage summaries
```

The Results UI should foreground only the primary and quality products by default. Supporting manifests and stage summaries belong in an "Audit files" section.

### SBAS-InSAR Results UI

List view:

```text
left/top filters:
  time range
  geographic bbox / map AOI
  platform
  relative orbit
  direction
  status / health
  has monitor points

result row/card:
  product name
  footprint mini-map or bbox text
  start/end/reference dates
  scene count / pair count
  LOS velocity preview thumbnail
  sigma health indicator
  actions: open details, zoom to map, download primary GeoTIFF
```

Detail view:

```text
map footprint panel
LOS velocity preview
LOS sigma preview
monitoring point curve
key metadata table
asset table grouped by role
quality summary
link back to production Run and 12-node workflow
```

Map behavior:

```text
use stack bbox as initial footprint
prefer GeoTIFF bounds when parsed successfully
show monitor points as point overlays
allow "zoom to result"
allow AOI filter against bbox intersection
```

### Backend Work Items

1. Add `sbas_insar_catalog_service.py`.
2. Register completed SBAS runs into `result_products/result_assets` with `catalog_name = sbas_insar`.
3. Derive `bbox` and `center` from `stack_manifest.scenes[*].bbox`; verify or refine from GeoTIFF metadata when available.
4. Add startup self-maintenance similar to D-InSAR/PsInSAR catalog bootstrapping:

```text
scan completed SBAS run publish bundles
upsert missing catalog rows
check primary/quality assets exist and are non-empty
record issues for missing bbox, missing GeoTIFF, missing preview, or DEM coverage mismatch
```

5. Add API routes:

```text
GET  /api/sbas-insar-products/catalog-status
POST /api/sbas-insar-products/rebuild-catalog
GET  /api/sbas-insar-products
GET  /api/sbas-insar-products/{product_id}
GET  /api/sbas-insar-products/{product_id}/assets/{asset_id}
```

6. Extend production run detail to include explicit `geographic_coverage`:

```json
{
  "center": {"lon": 129.199, "lat": 44.053},
  "bbox": {"min_lon": 128.769, "min_lat": 43.749, "max_lon": 129.629, "max_lat": 44.358},
  "scene_bbox_count": 7,
  "dem_covers_stack_bbox": false,
  "dem_covers_stack_center": true,
  "monitor_points": [{"point_id": "...", "lon": 129.102, "lat": 44.150}]
}
```

### Frontend Work Items

1. Add geographic coverage block to `SbasInsarProductionPanel`.
2. Add new `SbasInsarProductsPanel`.
3. Add API client module for SBAS product catalog.
4. Add navigation entry under result management/result analysis, separate from production management.
5. Reuse existing map overlay patterns from D-InSAR where practical, but keep SBAS asset roles and product semantics independent.
