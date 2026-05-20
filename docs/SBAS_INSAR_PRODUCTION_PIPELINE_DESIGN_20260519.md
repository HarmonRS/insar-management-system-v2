# SBAS-InSAR Production Pipeline Design

Date: 2026-05-19

## Decision

SBAS-InSAR production becomes an independent production workflow and page. It must not depend on the existing coarse time-series pairing layer as its production authority.

The old time-series pairing code may remain temporarily for compatibility and candidate discovery, but the new SBAS-InSAR page and backend API should bypass it by default. Deletion should happen only after the new workflow can create, run, publish, and browse Gamma SBAS/IPTA products end to end.

The legacy ISCE2/MintPy time-series production chain is disabled by default. The `timeseries-production` backend code and old catalog pages may remain as compatibility code, but they are no longer exposed as production-management subpages. The active SBAS production route is `/api/sbas-insar-production` and the active UI view is `sbas_insar_production`.

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

Current status before executing the queued job:

```text
status = COREGISTRATION_SCRIPT_READY
next_stage = execute_coregistration
common reference date = 20241007
```

The actual `SLC_coreg.py` execution is now wired as a background job endpoint and page action, but has not been production-tested in this pass. It consumes `itab_approved`, not the pre-audit or unapproved pair plan.

Expected post-job outputs:

```text
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/coregistration_summary.json
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/SLC_tab
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/RMLI_tab
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/rslc/*.rslc
backend/runtime/sbas_insar_production/runs/sbas_ab96afabead5/work/gamma/common_20241007/rmli/*.mli
```

After the job succeeds:

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
