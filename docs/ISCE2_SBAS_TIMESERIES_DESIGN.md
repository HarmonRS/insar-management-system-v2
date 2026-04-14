# ISCE2 SBAS Time-Series Production Design

Updated: 2026-04-06

## 1. Goal

Add stack-based time-series production under the current ISCE2-oriented architecture.

The current repository already has:

- image discovery and stack selection
- PS/time-series stack batch persistence
- single-pair D-InSAR production

The current repository does not yet have:

- a stack-level production entry
- time-series workflow orchestration
- SBAS inversion output registration
- PS-InSAR result/product pages beyond placeholders

This design focuses on the first deliverable:

- implement SBAS time-series production first
- keep true PS-InSAR or StaMPS as a later phase

Related product contract:

- `docs/ISCE2_SBAS_PRODUCT_SPEC.md`

## 2. Current State

### 2.1 What already exists

- `backend/app/routers/pairing.py`
  - `/find-ps-timeseries` can search image stacks for time-series use.
- `backend/app/routers/task_batches.py`
  - `/task-batches/ps` persists a selected stack into `PsTaskBatchORM` and `PsTaskItemORM`.
- `backend/app/models/orm.py`
  - `ps_task_batches` and `ps_task_items` already represent the planning-layer stack snapshot.
- `backend/app/models/orm.py`
  - `workflow_runs`, `workflow_steps`, and `workflow_artifacts` already exist and are suitable for multi-step orchestration.
- `backend/app/models/orm.py`
  - `result_products` already supports multiple catalogs through `catalog_name`.
- `frontend/src/App.jsx`
  - `ps_production`, `ps_products`, `psinsar_results`, and `psinsar_analysis` are reserved placeholders.

### 2.2 What is missing

- `backend/app/dinsar_engines/isce2_engine.py` is pair-oriented.
  - It runs a custom LT-1 `stripmapApp.py` flow for one pair or a pair-root directory.
  - It does not model stack/network/time-series execution.
- Current `SBAS` in pairing-related code and docs means a pairing strategy.
  - It does not mean completed SBAS inversion, velocity estimation, or time-series products.
- The current PS batch is not a production run.
  - It is only a stored stack selection.

### 2.3 Architectural implication

Time-series production is not a small extension of the current pair-based D-InSAR engine.

The processing object changes from:

- `pair -> one run -> one main displacement output`

to:

- `stack/network -> multi-step run -> multiple intermediate and final products`

Because of that, time-series production should not be forced into `DinsarEngine` as-is.

Still, the LT-1 input-preparation layer should be shared.

Recommended reuse point:

- keep DEM path resolution, orbit-pool resolution, and LT-1 precise-orbit XML generation in a shared helper
- current implementation anchor:
  - `backend/app/isce2_pipeline/lt1_input_resolver.py`

### 2.4 Experiment Status

Current LT-1 stack experiment status already de-risks the processing side of phase 1:

- one offline LT-1 sample stack has completed the generated `run_01` to `run_07` chain under `Ubuntu-24.04`
- the same LT-1 sample stack has also completed `run_08_igram`, producing filtered and unwrapped pair products under `Igrams/`
- the same LT-1 sample stack has now also completed the first MintPy SBAS smoke test through radar-coordinate `timeseries.h5` and `velocity.h5`
- current successful MintPy work directory:
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_v5`
- the same LT-1 sample stack has now also completed an experiment-layer geocode plus publish-bundle export
- current successful publish-style directory:
  - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_v5`
- local SAR scenes, local orbit data, and a local DEM were sufficient for this stage
- Earthdata `SWBD` was not treated as a hard dependency during experiments because `run_01_reference` can recover with a synthetic all-land `waterMask`
- current runtime split decision:
  - keep ISCE2 stack processing in the existing WSL `isce2` env
  - validate MintPy in a separate WSL `mintpy` env to avoid mutating the working processing env on the development machine
- phase-4 unified-env update:
  - a recreated WSL env `isce2_mintpy_v1` has now also completed the same LT-1 SBAS smoke test and publish export without the `isce` bridge
  - current successful unified SBAS work directory:
    - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/mintpy_sbas_unified_v1`
  - current successful unified publish directory:
    - `/mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_unified_v1`
  - current judgment:
    - unified env is viable at experiment layer and is now the preferred SBAS experiment runtime
    - existing WSL `isce2` should remain untouched for current D-InSAR production safety
    - bridge env remains the fallback baseline until a fuller comparison is written
- current MintPy bridge decision:
  - keep `mintpy` isolated
  - bridge only the top-level `isce` package from the `isce2` env into the `mintpy` env, because MintPy's ISCE stripmap metadata path imports `isce`
  - do not bridge the full `isce2` `site-packages`, because that polluted the MintPy runtime with conflicting `h5py`
- current MintPy runtime workaround decision:
  - use a strict `maskAllValid.h5` before inversion to suppress unstable partial-network pixels
  - use a repo-local patched launcher for the current MintPy `1.6.2` single-pixel partial-network inversion bug
  - current implementation anchors:
    - `experiments/isce2_sbas_timeseries/scripts/create_mintpy_all_ifgram_mask.py`
    - `experiments/isce2_sbas_timeseries/scripts/run_smallbaselineApp_patched.py`
    - `experiments/isce2_sbas_timeseries/scripts/run_mintpy_sbas_smoketest_ubuntu2404.sh`
- current experiment-layer publish export decision:
  - keep geocode/export as a separate post-MintPy stage
  - current implementation anchors:
    - `experiments/isce2_sbas_timeseries/scripts/export_mintpy_publish_products_ubuntu2404.sh`
    - `experiments/isce2_sbas_timeseries/scripts/build_mintpy_publish_bundle.py`
- the shared helper refactor is already in place:
  - `backend/app/isce2_pipeline/lt1_input_resolver.py`
  - compatibility rule:
    - the original pair-oriented D-InSAR public entry was not removed
    - `backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py` still owns the existing workflow entry and now delegates shared input preparation to the helper

## 3. Recommendation

### 3.1 Deliver SBAS first

Recommended first path:

- `stack planning -> stack prep -> ISCE2 stack processing -> MintPy smallbaselineApp -> psinsar product publish -> result browsing`

Reasons:

- the repository already has stack-selection concepts
- SBAS outputs are easier to standardize into cataloged products
- MintPy is a practical downstream tool for small-baseline inversion over ISCE outputs
- the UI and backend risk are both lower than starting from full PS-InSAR

### 3.2 Do not start with StaMPS / true PS route

True PS-InSAR should be treated as phase 2 or later because it adds extra uncertainty in:

- candidate selection and amplitude stability logic
- stack export conventions
- result structure standardization
- point-level visualization and analysis UX

## 4. Recommended Technical Chain

Phase-1 recommended chain:

1. User creates or reuses a `PS stack batch`.
2. System creates a stack-level production run bound to that batch.
3. Workflow prepares a working directory and stack metadata.
4. Workflow runs ISCE2 stack processing in WSL.
5. Workflow runs MintPy SBAS inversion in a dedicated WSL MintPy runtime.
6. Workflow exports standardized products and manifest files.
7. Workflow publishes the outputs into a `psinsar` result catalog.
8. Frontend reads the catalog and workflow state for products and results.

Product interpretation rule:

- `manifest.json` is the publish entrypoint
- `assets/geo_timeseries.h5` is the canonical time-series data product
- `assets/velocity.tif` is the default browse layer, not the only final deliverable

## 5. Scope

### 5.1 In scope for phase 1

- SBAS time-series production entry
- stack-level workflow orchestration
- WSL runtime checks for stack and MintPy steps
- product extraction and catalog registration
- basic `ps_production` and `ps_products` pages
- basic `psinsar_results` browsing capability

### 5.2 Out of scope for phase 1

- full PS-InSAR / StaMPS route
- advanced point-based analysis UI
- cluster scheduling
- multi-sensor stack fusion
- automatic reference-point intelligence

## 6. Target Architecture

### 6.1 Domain split

Keep the system split into three layers:

- Planning layer
  - existing `find-ps-timeseries`
  - existing `ps_task_batches`
- Production layer
  - new stack-level time-series run submission and monitoring
- Result layer
  - `psinsar` products and business-facing browsing

This keeps planning data and production runs decoupled.

### 6.2 Orchestration model

Reuse the existing generic workflow subsystem:

- `workflow_runs`
- `workflow_steps`
- `workflow_artifacts`

Recommended rule:

- do not extend `DinsarEngine` for time-series phase 1
- use `workflow` as the orchestration primitive
- add a thin time-series metadata table that binds:
  - one `ps_task_batch`
  - one workflow run
  - one processing mode
  - one output root

### 6.3 Suggested new metadata table

Add a new business table such as `ps_timeseries_runs`.

Suggested fields:

- `run_id`
- `batch_id`
- `workflow_run_id`
- `mode`
  - `sbas`
- `engine_code`
  - `isce2`
- `processor_code`
  - `isce2_stack_mintpy`
- `direction`
- `status`
- `work_dir`
- `output_dir`
- `publish_dir`
- `reference_date`
- `reference_strategy`
- `params_json`
- `summary_json`
- `error_message`
- `created_by`
- `created_at`
- `updated_at`
- `started_at`
- `ended_at`

Notes:

- `PsTaskBatchORM` remains the planning snapshot.
- `WorkflowRunORM` remains the step-level orchestration record.
- `ps_timeseries_runs` becomes the business-facing production record.

### 6.4 Suggested workflow

Recommended workflow name:

- `isce2_sbas_mintpy_v1`

Recommended steps:

1. `prepare_stack_input`
   - validate stack size and metadata
   - build stack working directory
   - resolve DEM/orbit/output paths
2. `run_isce2_stack`
   - run stack pre-processing in WSL
   - collect stack intermediate artifacts
3. `run_mintpy_sbas`
   - run repo-controlled MintPy `smallbaselineApp`
   - generate strict inversion mask if required by the pinned MintPy runtime
   - generate time-series and velocity products
4. `export_standard_products`
   - copy or convert outputs into stable publish structure
   - generate manifest and preview assets
5. `publish_psinsar_products`
   - register catalog entries
   - update product status and coverage

### 6.5 Suggested job types

Recommended new job types:

- `TIMESERIES_PREP_STACK`
- `ISCE2_STACK_RUN`
- `MINTPY_SBAS_RUN`
- `EXPORT_PSINSAR_PRODUCTS`
- `PUBLISH_PSINSAR_PRODUCTS`

These fit naturally under the existing job queue and worker model.

## 7. Backend Design

### 7.1 New modules

Recommended backend additions:

- `backend/app/routers/timeseries_production.py`
- `backend/app/routers/ps_products.py`
- `backend/app/services/timeseries_service.py`
- `backend/app/services/timeseries_workflow_factory.py`
- `backend/app/services/isce2_stack_service.py`
- `backend/app/services/mintpy_service.py`
- `backend/app/services/psinsar_catalog_service.py`
- `backend/app/services/timeseries_paths.py`

### 7.2 API sketch

Recommended new APIs:

- `POST /timeseries-production/runs`
  - submit one SBAS run for one stored PS stack batch
- `GET /timeseries-production/runs`
  - list recent time-series runs
- `GET /timeseries-production/runs/{run_id}`
  - get business metadata + workflow state + artifacts
- `POST /timeseries-production/runs/{run_id}/retry-step`
  - retry failed step when allowed
- `POST /timeseries-production/wsl-check`
  - check stack + MintPy runtime environment
- `GET /ps-products`
  - list registered `psinsar` products
- `POST /ps-products/rebuild-catalog`
  - rebuild `psinsar` product catalog

### 7.3 Reuse of current tables and services

Recommended reuse:

- `ps_task_batches` / `ps_task_items`
  - keep as the source stack definition
- `workflow_runs` / `workflow_steps` / `workflow_artifacts`
  - keep as the run and step state model
- `result_products`
  - extend with `catalog_name = psinsar`
- current job queue / worker / task log chain
  - reuse for execution and monitoring

### 7.4 Product catalog strategy

Do not build a separate product table just for time-series phase 1.

Use `result_products` with:

- `catalog_name = psinsar`
- `engine_code = isce2`
- `product_type` values specific to time-series outputs

Recommended `product_type` values:

- `velocity_map`
- `timeseries_cube`
- `temporal_coherence`
- `ifgram_network`
- `point_series_csv`
- `preview_png`
- `summary_report`

Use the existing generic asset and issue models where possible.

### 7.5 Current Phase-1 Artifact Contract

The current validated LT-1 experiment supports the following publish contract:

- `timeseries_cube`
  - source file:
    - `assets/geo_timeseries.h5`
- `velocity_map`
  - source file:
    - `assets/geo_velocity.h5`
- `velocity_geotiff`
  - source file:
    - `assets/velocity.tif`
- `temporal_coherence`
  - source file:
    - `assets/geo_temporalCoherence.h5`
- `quality_mask`
  - source file:
    - `assets/geo_maskTempCoh.h5`
- `temporal_coherence_geotiff`
  - source file:
    - `assets/temporalCoherence.tif`
- `quality_mask_geotiff`
  - source file:
    - `assets/maskTempCoh.tif`
- `ifgram_network`
  - source file:
    - runtime diagnostic source:
      - `numTriNonzeroIntAmbiguity.h5`
- `preview_png`
  - source file:
    - `preview/velocity_preview.png`
- `diagnostic_png`
  - source file:
    - `preview/numTriNonzeroIntAmbiguity.png`
- retained runtime-only artifacts:
  - `maskAllValid.h5`
  - `avgSpatialCoh.h5`
  - `smallbaselineApp.cfg`

Current sample manifest draft:

- `experiments/isce2_sbas_timeseries/configs/sample_psinsar_manifest_lt1_e123p3_n46p1.json`

Current successful experiment publish bundle:

- `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_v5/manifest.json`

## 8. Directory Conventions

Recommended Windows roots:

- `PS_TIMESERIES_WORK_ROOT`
- `PS_TIMESERIES_OUTPUT_ROOT`
- `PSINSAR_PRODUCT_DIR`

Recommended run layout:

```text
<PS_TIMESERIES_WORK_ROOT>/<batch_id>/<run_id>/
  stack_input/
  isce2_stack/
  mintpy/
  export/
  logs/
```

Recommended publish layout:

```text
<PSINSAR_PRODUCT_DIR>/<product_id>/
  manifest.json
  preview/
  assets/
  metadata/
```

Rules:

- production should always have a stable publish directory
- intermediate work directory and final publish directory should stay separate
- raw imagery must not be copied into publish output unnecessarily

## 9. Frontend Design

### 9.1 `ps_production`

Phase-1 page content:

- select an existing PS stack batch
- choose processing mode
  - phase 1 only exposes `SBAS`
- show runtime environment status
  - WSL
  - ISCE2 stack runtime
  - MintPy runtime
- submit workflow run
- show step status, logs, and artifacts

### 9.2 `ps_products`

Phase-1 page content:

- list `psinsar` products
- filter by status, run, batch, direction, date
- publish and rebuild operations
- basic manifest view

### 9.3 `psinsar_results`

Phase-1 page content:

- basic product query
- map preview of velocity or deformation raster
- metadata drawer
- quick link to workflow run and source batch

### 9.4 `psinsar_analysis`

Keep this reserved in phase 1.

Later it can take:

- point time-series browsing
- rate classification
- hotspot statistics
- thematic reporting

## 10. Configuration

Recommended new or clarified settings:

- `PS_TIMESERIES_ENABLED`
- `PS_TIMESERIES_WORK_ROOT`
- `PS_TIMESERIES_OUTPUT_ROOT`
- `PSINSAR_PRODUCT_DIR`
- `MINTPY_PYTHON`
- `MINTPY_SMALLBASELINE_APP`
- `MINTPY_RUNNER`
- `MINTPY_PATCHED_SMALLBASELINE_APP`
- `MINTPY_TEMPLATE_DIR`
- `PS_TIMESERIES_AUTO_PUBLISH`

Where practical:

- reuse `ISCE2_WSL_DISTRO`
- reuse `ISCE2_PYTHON` if MintPy is installed in the same environment
- reuse current DEM and orbit path conventions

## 11. Key Risk: LT-1 Stack Compatibility

This is the main technical uncertainty.

The current repository already uses a custom LT-1 single-pair `stripmapApp.py` flow.
That does not prove official ISCE2 stack tooling will work directly for LT-1/LUTAN1.

Therefore phase 0 must verify:

- whether official ISCE2 stack tooling can ingest LT-1 metadata directly
- whether orbit formatting is already sufficient for stack mode
- whether extra stack-input conversion is required

If official stack tooling is not directly compatible:

- keep the overall architecture unchanged
- implement a custom stack builder or adapter layer before MintPy

Do not hard-code the design around official stack scripts until this experiment is confirmed.

## 12. Option Comparison

| Option | Fit for current repo | Technical risk | UI/ops complexity | Phase-1 recommendation |
|---|---|---|---|---|
| ISCE2 stack + MintPy SBAS | High | Medium | Medium | Yes |
| ISCE2 StackToStaMPS / PS route | Medium-Low | High | High | No |

Why SBAS wins first:

- closer to the current stack-planning model
- easier to standardize outputs
- easier to explain and operate
- lower uncertainty in first delivery

## 13. TODO List

### Phase 0. Validation

- [x] Verify whether LT-1/LUTAN1 can run through official ISCE2 stack tooling.
- [x] Decide whether MintPy shares the current ISCE2 environment or uses a separate env.
- [x] Finalize work/output/publish directory rules.
- [x] Decide the minimal product set for first release.

### Phase 1. Backend scaffold

- [ ] Add `ps_timeseries_runs` ORM and schema.
- [ ] Add `timeseries_production` router.
- [ ] Add `timeseries_workflow_factory` for `isce2_sbas_mintpy_v1`.
- [ ] Add new job handlers for stack prep, stack run, MintPy run, export, and publish.
- [ ] Bind workflow runs to `ps_task_batches`.
- [ ] Persist workflow artifacts for major intermediate and final outputs.

### Phase 2. Catalog and products

- [ ] Extend catalog publishing to support `catalog_name = psinsar`.
- [ ] Define manifest schema for SBAS products.
- [ ] Add coverage extraction and preview generation.
- [ ] Add rebuild and health-check operations for `psinsar` products.

### Phase 3. Frontend

- [ ] Replace the `ps_production` placeholder with a real production panel.
- [ ] Replace the `ps_products` placeholder with a product management panel.
- [ ] Add a basic `psinsar_results` result page.
- [ ] Keep `psinsar_analysis` reserved until point-series UX is clear.

### Phase 4. Validation and rollout

- [x] Run one small AOI stack end to end in the experimental workflow.
- [ ] Verify rerun, resume, and failure-recovery behavior.
- [ ] Verify published products can be queried and rendered.
- [ ] Add operator documentation and troubleshooting notes.

## 14. Experimental Environment Recommendation

Yes. A dedicated experiment folder is recommended.

Purpose:

- validate LT-1 stack compatibility without polluting production code
- collect command templates, notes, and sample manifests
- separate exploratory scripts from backend services

Recommended location:

- `experiments/isce2_sbas_timeseries/`

Rules:

- store only scripts, notes, configs, and tiny mock artifacts in git
- do not commit raw SAR scenes, DEM rasters, or large intermediate outputs
- once an experiment stabilizes, move the conclusion back into `docs/` and production code
