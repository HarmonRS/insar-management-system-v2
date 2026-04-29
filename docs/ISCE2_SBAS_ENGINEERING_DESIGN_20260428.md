# ISCE2 + MintPy SBAS Engineering Design

Updated: 2026-04-28

## 1. Purpose

This document defines the engineering expansion plan for the current stack-based time-series InSAR route:

- `LT-1 stack batch -> ISCE2 stripmapStack -> MintPy SBAS -> publish bundle -> psinsar catalog`

The repository already has a working phase-1 skeleton. The goal of this document is not to restart the design from zero, but to align the next implementation round with the code that already exists in:

- `backend/app/services/timeseries_service.py`
- `backend/app/routers/timeseries_production.py`
- `backend/app/services/psinsar_catalog_service.py`
- `frontend/src/TimeseriesProductionPanel.jsx`
- `frontend/src/components/PsinsarCatalogPanel.jsx`

This document supersedes the "missing pieces" parts of `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md` for the current implementation phase.

## 2. Decisions

### 2.1 Primary processing route

Keep the current scientific split:

- ISCE2 is responsible for LT-1 stack preparation, stack geometry, co-registration, baseline generation, interferogram generation, and unwrap inputs.
- MintPy is responsible for SBAS inversion and time-series products.
- The system registers only publish-grade bundles, not raw MintPy work directories.

This means the production claim for the current phase is:

- `SBAS time-series production on top of ISCE2 + MintPy`

It is not:

- full PS-InSAR
- full StaMPS integration
- full commercial-grade atmospheric/error-correction stack

### 2.2 Keep the current business model

Use the current model already implemented in code:

- planning-layer stack snapshot:
  - `PsTaskBatchORM`
  - `PsTaskItemORM`
- business-facing production run:
  - `PsTimeseriesRunORM`
- step orchestration:
  - `WorkflowRunORM`
  - `WorkflowStepORM`
- publish/catalog registration:
  - `ResultProductORM`
  - `ResultAssetORM`
  - `ResultIssueORM`

Do not redesign the run model into a new engine abstraction in this round.

### 2.3 Keep naming stable for now

Current naming in the repository is mixed:

- product family shown to users: `timeseries`
- processing mode: `sbas`
- catalog namespace and package schema legacy: `psinsar`

For this round:

- keep `product_family = timeseries`
- keep `mode = sbas`
- keep `catalog_name = psinsar` for compatibility
- keep `psinsar.publish.v1` ingestion support working

Do not do a DB/API namespace rename and a pipeline hardening round at the same time.

## 2.4 Current implementation scope (2026-04-28)

This round is intentionally constrained to avoid impact on other production business:

- no DB schema migration
- no catalog namespace rename
- no workflow framework refactor
- no change to the system self-maintenance / self-check contract

The implementation landed in this round focuses on pipeline hardening around the existing `PsTimeseriesRunORM` path:

- preflight gating before SBAS run creation
- stronger runtime self-check visibility
- publish-bundle validation before catalog registration
- frontend visibility for preflight, runtime checks, and publish validation

This means the current engineering target is:

- make the existing ISCE2 + MintPy SBAS route operationally safer

not:

- redesign the overall architecture
- replace the existing result registration model
- introduce a second persistence path for timeseries products

### 2.5 Phase-2 scope (2026-04-28)

The second implementation round keeps the same production chain but upgrades the planning trace from ad-hoc JSON to first-class additive schema objects.

Additive schema only:

- new planning tables:
  - `TimeseriesStackPlanORM`
  - `TimeseriesStackPlanItemORM`
- nullable trace columns on existing objects:
  - `PsTaskBatchORM.plan_id`
  - `PsTaskBatchORM.plan_strategy`
  - `PsTaskItemORM.plan_item_ref_id`
  - `PsTimeseriesRunORM.plan_id`
  - `PsTimeseriesRunORM.plan_strategy`

Operational rules for phase 2:

- do not introduce a separate migration framework
- rely on the existing database self-maintenance path:
  - `Base.metadata.create_all()`
  - missing-column auto-add in `backend/app/db_maintenance.py`
- keep phase-1 `planning_context` / `remark` compatibility for old batches

The engineering target of phase 2 is:

- formalize `plan -> batch -> run -> publish bundle -> catalog product` traceability
- expose `plan_id` in frontend production and result views
- keep old batches runnable without backfilling or hard migration
- expose `GET /timeseries-plans/{plan_id}` for plan audit/detail lookup

## 3. Current Baseline In Code

The current code already implements the core production skeleton.

### 3.1 Run record and workflow

`backend/app/models/orm.py`

- `PsTimeseriesRunORM` already stores:
  - run identity
  - batch binding
  - processor/runtime metadata
  - work and publish roots
  - input/orbit/quality summaries
  - failure state
- `WorkflowRunORM` and `WorkflowStepORM` already support DAG execution and retry.
- `SystemJobORM` already supports queued worker execution per workflow step.

### 3.2 Current workflow steps

`backend/app/services/timeseries_service.py`

Current step chain is already eight steps:

1. `prepare`
2. `stack_prep_initial`
3. `materialize`
4. `stack_prep_refresh`
5. `run_isce2_stack`
6. `run_mintpy_sbas`
7. `export_publish_bundle`
8. `register_psinsar_product`

This is already the correct backbone for the managed SBAS route.

### 3.3 Current scientific execution boundary

The scientific boundary is still script-based, and that is acceptable for now:

- `experiments/isce2_sbas_timeseries/scripts/build_lt1_stack_prep.py`
- `experiments/isce2_sbas_timeseries/scripts/materialize_lt1_stack_scenes.py`
- `experiments/isce2_sbas_timeseries/scripts/prepare_lt1_stack_dem.py`
- `experiments/isce2_sbas_timeseries/scripts/run_generated_stack_runfile_ubuntu2404.sh`
- `experiments/isce2_sbas_timeseries/scripts/run_mintpy_sbas_unified_env_smoketest_ubuntu2404.sh`
- `experiments/isce2_sbas_timeseries/scripts/export_mintpy_publish_products_ubuntu2404.sh`

The current implementation should continue to wrap these scripts instead of rewriting the scientific logic prematurely.

### 3.4 Current frontend and ops surface

Already present:

- run submission and run detail:
  - `frontend/src/TimeseriesProductionPanel.jsx`
- product catalog panel:
  - `frontend/src/components/PsinsarCatalogPanel.jsx`
- health-check visibility:
  - `frontend/src/HealthCheckPanel.jsx`
- catalog rebuild API:
  - `backend/app/routers/ps_products.py`

So the next round is a hardening and extension round, not an empty scaffold round.

## 4. Main Gaps

The next engineering work should focus on the following gaps.

### 4.1 Self-check is present but still shallow

Current runtime check already validates:

- WSL distro
- Python path
- stack script path
- configured helper scripts
- MintPy import
- DEM/orbit/output root presence

What is still missing:

- write permission checks for work and publish roots
- DEM sidecar consistency checks
- runtime dependency checks for scientific imports used by ISCE2/MintPy
- batch-level readiness checks before a run is queued
- publish-bundle structural validation before catalog registration

### 4.2 Quality summary exists, but quality gating is weak

Current code validates:

- stack prep readiness
- required run files
- required ISCE2 output directories
- required MintPy outputs
- publish manifest existence

But it still does not promote enough scientific quality indicators into release gates, for example:

- interferogram count versus expected network count
- non-empty unwrap/correlation outputs
- valid-pixel ratio after `maskAllValid`
- temporal coherence thresholds
- reference point presence and stability summary

### 4.3 Frontend is functional but still operationally thin

Current frontend can:

- submit a run
- run WSL check
- list runs
- show workflow steps
- retry failed workflow steps
- browse catalog entries

Still missing:

- structured preflight diagnostics for the selected batch
- clearer phase summaries per run
- direct visibility into quality summaries and key artifacts
- better linkage between run detail and published product detail
- a richer product detail view closer to the D-InSAR catalog panel depth

### 4.4 Result management needs stricter contract enforcement

The catalog path is correct, but the following rules should be made explicit and enforced:

- `manifest.json` is the only registration entrypoint
- every published run must have a stable `publish_dir`
- required assets must exist before registration
- missing assets should generate catalog issues and possibly quarantine status
- every product should carry:
  - processor code
  - runtime id
  - native output trace
  - stack identity

## 5. Target Pipeline

### 5.1 Input contract

The run input must remain stack-based, not pair-based.

Source objects:

- one `ps_task_batch`
- many `ps_task_items`
- one selected stack manifest:
  - `input/selected_stack_manifest.json`
- one generated stack manifest:
  - `input/stack_input_manifest.json`

The selected manifest is the planning snapshot.

The generated stack manifest is the execution snapshot and must include:

- stack dates
- reference date
- stack key
- group key
- resolved DEM and orbit dependencies
- readiness flags
- blocking reasons
- generated ISCE2 command arguments

### 5.2 Runtime directory model

Keep the current directory split:

- work root:
  - `backend/runtime/timeseries_work/<run_id>/...`
- publish root:
  - `TIMESERIES_PRODUCT_DIR/<stack_key>/runs/<run_id>/...`

Recommended internal layout under the work root:

- `input/`
- `inputs/dem/`
- `stack_work/`
- `logs/`
- `mintpy/`

Recommended publish layout:

- `manifest.json`
- `assets/`
- `preview/`
- `metadata/`

### 5.3 Managed workflow

The current eight-step chain is the correct managed workflow and should be kept:

1. `prepare`
   - validate batch
   - resolve stack identity
   - choose reference date
   - write `selected_stack_manifest.json`
2. `stack_prep_initial`
   - generate execution-layer stack manifest
   - resolve DEM/orbits
   - tell the system whether materialization is the only blocker
3. `materialize`
   - materialize LT-1 scenes into stack input layout
   - materialize orbit XML and local dependencies
4. `stack_prep_refresh`
   - re-run readiness check after materialization
   - must reach ready state
5. `run_isce2_stack`
   - prepare local DEM sidecars
   - generate run files
   - run `run_01` to `run_08`
   - validate `geom_reference`, `baselines`, and `Igrams`
6. `run_mintpy_sbas`
   - write MintPy config
   - run controlled `smallbaselineApp`
   - validate core MintPy outputs
7. `export_publish_bundle`
   - geocode MintPy outputs
   - export GeoTIFF browse layers
   - generate preview and manifest
   - augment the manifest with canonical metadata
8. `register_psinsar_product`
   - register the publish bundle into catalog
   - mark the run as published

### 5.4 Current publish contract

Keep `docs/ISCE2_SBAS_PRODUCT_SPEC.md` as the publish contract source of truth.

Required publish assets for the managed SBAS route:

- `assets/geo_timeseries.h5`
- `assets/geo_velocity.h5`
- `assets/velocity.tif`
- `assets/geo_temporalCoherence.h5`
- `assets/geo_maskTempCoh.h5`
- `preview/velocity_preview.png`
- `metadata/smallbaselineApp.cfg`
- `manifest.json`

Optional but recommended:

- `preview/numTriNonzeroIntAmbiguity.png`
- extra quality JSON files

## 6. Self-Check Design

Self-check should exist at four levels.

### 6.1 Runtime preflight

Primary entry:

- `POST /timeseries-production/wsl-check`

Current checks should be kept and extended with:

- WSL distro reachable
- configured Python reachable
- ISCE2 stack script import/help check
- MintPy import check
- helper script existence checks
- DEM root existence
- orbit pool existence
- publish root existence
- work root existence
- write-test for work root
- write-test for publish root
- DEM sidecar consistency check
- optional import checks for:
  - `cv2`
  - `scipy`
  - `astropy`

Return structure should remain machine-readable so the frontend can render a diagnostic card instead of a plain message string.

### 6.2 Batch preflight

Add a run-specific preflight before or during `create_run`.

Minimum checks:

- scene count meets SBAS minimum
- all scene dates are valid
- scene dates are unique
- direction is consistent
- source files exist and are readable
- orbit coverage is complete or explicitly degraded
- `group_key` and `stack_key` are derivable
- publish path does not collide with another active run

Recommended surface:

- a new backend helper in `timeseries_service.py`
- frontend summary block in `TimeseriesProductionPanel.jsx`

### 6.3 In-run gates

Each workflow step should continue to fail fast when hard requirements are not met.

Required gates:

- `stack_prep_refresh` must report `ready_for_stackStripMap_nofocus = true`
- all expected run files must exist before stack execution
- `run_08_igram` output directories must exist
- MintPy required outputs must exist and be non-empty
- export must generate a manifest plus required assets
- registration must succeed against the catalog service

### 6.4 Post-publish health

Health is not only "the run finished".

The catalog and package checks must continue to validate:

- manifest exists
- publish dir exists
- processor code present
- runtime id present for WSL-native engines
- native output dir present
- canonical package schema valid
- manifest count versus DB count consistency

## 7. Frontend Design

### 7.1 Timeseries production panel

Keep `frontend/src/TimeseriesProductionPanel.jsx` as the main run workspace.

Planned enhancements:

- show structured runtime preflight results
- show batch preflight results before submission
- show phase-oriented run summary:
  - input prepared
  - stack ready
  - ISCE2 complete
  - MintPy complete
  - exported
  - published
- show key paths and quality summary blocks without forcing the operator to inspect raw JSON
- keep failed-step retry
- add clearer linkage to the published product once available

### 7.2 Product catalog panel

Keep `frontend/src/components/PsinsarCatalogPanel.jsx` as the catalog entry.

Planned enhancements:

- retain catalog status and rebuild actions
- enrich product detail with:
  - stack identity
  - processor/runtime identity
  - preview and primary assets
  - quality summary
  - asset list
  - issue list
  - coverage summary
- keep the publish bundle as the fact source

### 7.3 Health panel

Keep health visibility in `frontend/src/HealthCheckPanel.jsx`.

The timeseries section should continue to show:

- catalog status
- rebuild need
- manifest vs DB counts
- issue count

It should remain aligned with:

- `timeseries_result_catalog`
- `product_packages`
- `wsl_runtime`

## 8. Result Management and Registration

### 8.1 Registration rule

Register only from:

- `<publish_dir>/manifest.json`

Do not register from:

- MintPy work directories
- ISCE2 runtime directories
- ad hoc copied assets

### 8.2 Catalog model

Keep:

- `catalog_name = psinsar`
- `product_family = timeseries`

Current catalog service already derives:

- product id
- display name
- stack identity
- runtime and processor metadata
- bbox from asset summaries
- preview and primary asset paths

The next round should strengthen issue generation for missing assets and invalid package states.

### 8.3 Quarantine policy

Do not auto-delete broken publish packages.

If a rebuild finds broken packages, the preferred behavior is:

- keep package on disk
- create `ResultIssueORM` records
- downgrade `health_status`
- use quarantine status only when the package is structurally unusable

This keeps auditability intact.

## 9. Implementation Strategy

### 9.1 Do not over-refactor first

The old design expected many new modules. That is no longer necessary because the codebase already has the main modules.

For the next round:

- keep `timeseries_service.py` as the orchestration center
- keep `job_handlers.py` as worker entrypoints
- keep `psinsar_catalog_service.py` as catalog authority
- extract helper modules only when a block becomes independently reusable or too large

### 9.2 Recommended implementation phases

#### Phase A: contract and self-check hardening

Files likely involved:

- `backend/app/services/timeseries_service.py`
- `backend/app/services/health_service.py`
- `frontend/src/TimeseriesProductionPanel.jsx`

Target:

- stronger runtime report
- batch preflight
- clearer failure reasons

#### Phase B: quality summary and gating

Files likely involved:

- `backend/app/services/timeseries_service.py`
- `experiments/isce2_sbas_timeseries/scripts/build_mintpy_publish_bundle.py`
- `backend/app/services/psinsar_catalog_service.py`

Target:

- richer `quality_summary_json`
- richer manifest quality block
- stronger publish/register gates

#### Phase C: frontend run and catalog UX

Files likely involved:

- `frontend/src/TimeseriesProductionPanel.jsx`
- `frontend/src/components/PsinsarCatalogPanel.jsx`
- `frontend/src/api/timeseriesProduction.js`
- `frontend/src/api/psinsarProducts.js`

Target:

- better preflight display
- better run summary
- richer product detail

#### Phase D: validation and operator closure

Target:

- one small LT-1 AOI end-to-end validation
- one rerun-from-failure validation
- catalog rebuild validation
- deployment/ops notes update

## 10. Non-Goals For This Round

Do not include the following in the same implementation round:

- Gamma/PyINT timeseries integration
- full PS-InSAR or StaMPS
- atmospheric correction productization
- topographic residual correction productization
- large database namespace migration from `psinsar` to `timeseries`

These are valid future directions, but they should not be mixed into the current SBAS production hardening round.

## 11. Acceptance Criteria

The engineering expansion can be treated as complete for this round when all of the following are true:

1. An operator can run runtime preflight and understand failures before queuing a run.
2. A stored PS stack batch can be submitted as one managed SBAS run.
3. The run can execute through all eight workflow steps in the managed path.
4. Failure at any step produces a clear error and supports controlled retry.
5. The publish bundle is complete and canonical.
6. The product is registered into the `psinsar` catalog from `manifest.json`.
7. The frontend can show:
   - run state
   - workflow step state
   - published product linkage
   - product assets and quality summary
8. `GET /api/health` remains healthy for:
   - `timeseries_result_catalog`
   - `product_packages`
   - `wsl_runtime`

## 12. Related Documents

- `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md`
- `docs/ISCE2_SBAS_PRODUCT_SPEC.md`
- `docs/DEPLOYMENT.md`
- `docs/CURRENT_STATUS_20260425.md`
- `experiments/isce2_sbas_timeseries/README.md`
