# Time-Series SBAS And SARscape Integration Design

## 1. Problem Statement

The current time-series route can find and run a scene stack, but the system does not yet treat SBAS as a first-class production input. The main gaps are:

- `find-ps-timeseries` returns scenes, not a durable SBAS network.
- `PsTaskBatch` is used as a production input even though it is a thin list of paths.
- Planning context is partly duplicated in `PsTaskItem.remark`.
- `copy-ps-stack` copies source folders, but does not create a stack-level production package.
- The managed time-series runner reconstructs input state at run time.
- SARscape is currently integrated only as a D-InSAR pair processor.

The design goal is to make one immutable stack manifest the source of truth for every SBAS run, then let ISCE2/MintPy and SARscape consume the same contract.

## 2. Target Workflow

```text
AOI + filters
  -> time-series stack search
  -> SBAS network plan
  -> user review and commit
  -> immutable stack package
  -> processor workflow
  -> publish bundle
  -> psinsar catalog
```

The stack plan and the production package are separate states. A plan is a previewable proposal; a package is a committed production input.

## 3. Planning Contract

### 3.1 Search API

Add or evolve the current `find-ps-timeseries` route toward:

```text
POST /timeseries/plans/search
```

Core request fields:

- AOI source: uploaded shapefile, region geometry, or GeoJSON.
- Scene compatibility filters: satellite, orbit direction, imaging mode, polarization, date range.
- Scene thresholds: `initial_overlap_threshold`, `final_overlap_threshold`.
- Network thresholds: `time_baseline_min`, `time_baseline_max`, `spatial_baseline_max_meters`, later `perpendicular_baseline_max_meters`.
- Network policy: `strategy`, `num_connections`, `reference_image_id`.
- Processor hint: optional `processor_target`, for example `isce2_stack_mintpy` or `sarscape_sbas`.

### 3.2 Plan Tables

Existing:

- `timeseries_stack_plans`
- `timeseries_stack_plan_items`

New:

- `timeseries_stack_plan_edges`

The edge table stores the selected SBAS graph:

- plan reference
- master/slave plan item references
- master/slave radar scene references
- optional `pairing_metric_cache` reference
- temporal baseline
- spatial/perpendicular baseline
- scene overlap ratio
- AOI pair overlap ratio
- selection reason and score
- enabled flag

This lets the system answer: which pairs were selected, why were they selected, and what graph was actually submitted.

## 4. Production Input Package

Committed production input is represented by a prepared stack manifest. In the
current backend this file is:

```text
backend/runtime/timeseries_work/<run_id>/input/selected_stack_manifest.json
```

This file is not the same thing as a `TimeseriesStackPlan`. The plan is the
candidate pool and audit graph. The prepared stack is the smaller frozen set
submitted to a processor.

Schema:

```json
{
  "schema": "insar.timeseries-stack/v1",
  "prepared_stack_schema": "insar.prepared-sbas-stack/v1",
  "manifest_role": "prepared_sbas_stack",
  "mode": "sbas",
  "plan_id": "tsp_...",
  "prepared_stack_id": "pss_...",
  "source_plan_id": "tsp_...",
  "source_batch_id": "...",
  "processor_code": "sarscape_sbas",
  "aoi": {},
  "candidate_pool_source": {},
  "selection_params": {},
  "scenes": [],
  "network_edges": [],
  "reference_date": "YYYYMMDD",
  "production_contract": {
    "input_policy": "prepared_stack_only",
    "catalog_scan_allowed_after_prepare": false,
    "scene_selection_frozen": true
  },
  "artifacts": {
    "selected_network_edges_path_windows": "..."
  },
  "prepared_stack_validation": {},
  "prepared_at_utc": "...",
  "manifest_checksum": "..."
}
```

Rules:

- A production run consumes the prepared manifest, not `PsTaskItem.remark` and
  not a fresh scan of the full radar catalog.
- The manifest is immutable after `prepare` completes, except for explicit
  retry/re-prepare workflows.
- Processor-specific materialization is recorded in a separate processor manifest.
- Source data copying must include the manifest and graph.

### 4.1 Layered SBAS Input Model

The production model is now four layers:

1. Full radar inventory
   - The long-lived scene catalog and pairing metric cache.
   - It can be large and dirty/rebuilt over time.

2. Candidate time-series pool
   - `TimeseriesStackPlanORM`, plan items, and plan edges.
   - This is the large pool selected by AOI, date, orbit, baseline, overlap,
     and network policy.
   - It records why each scene and edge was selected.

3. Prepared SBAS stack
   - `selected_stack_manifest.json` with
     `prepared_stack_schema=insar.prepared-sbas-stack/v1`.
   - Contains only the frozen scenes for this run.
   - Writes `input/selected_network_edges.json` as a standalone artifact.
   - Records validation results for scene files, graph count/date consistency,
     DEM availability when required, and the no-catalog-scan production policy.

4. Processor execution
   - SARscape `wf_sbas` consumes the prepared scene stack.
   - System `network_edges` are mandatory as the planning/audit graph, but the
     native `wf_sbas` path may rebuild the executable graph internally.
   - When SARscape's actual graph can be extracted, it should be saved as
     `actual_network_edges.json` and compared with `selected_network_edges.json`.

Backend enforcement:

- `prepare_run()` creates the prepared stack contract and validates it.
- `build_sarscape_processor_preflight()` refuses non-prepared manifests.
- `run_sarscape_sbas()` refuses non-prepared manifests and missing
  `selected_network_edges.json`.
- `execute_template_workflow()` in the SARscape service has a second guard so
  lower-level execution cannot accidentally run from a candidate pool.

## 5. Processor Boundary

Introduce a time-series processor interface:

```text
TimeseriesProcessor
  check_available()
  preflight(manifest)
  build_workflow(run)
  prepare_inputs(run)
  execute_step(run, step_id)
  export_publish_bundle(run)
```

Processor codes:

- `isce2_stack_mintpy`
- `sarscape_sbas`

The existing `timeseries_service` can remain the orchestration service, but processor-specific logic should move behind this interface.

## 6. SARscape SBAS Processor

SARscape SBAS should be a stack-level processor, not an extension of the D-InSAR pair engine.

Suggested steps:

1. `sarscape_preflight`
   - Check ENVI, SARscape, taskengine, license, DEM, orbit pool, and output roots.
   - Enumerate available SARscape SBAS/E-SBAS task names via `envipyengine`.

2. `sarscape_import`
   - Import LT-1 scenes.
   - Write `sarscape_import_manifest.json`.

3. `sarscape_connection_graph`
   - Prefer the system-selected `network_edges`.
   - If SARscape internally rebuilds the graph, export the actual graph as `actual_network_edges.json`.

4. `sarscape_interferogram_generation`

5. `sarscape_inversion`
   - Generate time-series, velocity, coherence, and quality products.

6. `sarscape_geocode_export`

7. `export_publish_bundle`

8. `register_psinsar_product`

## 7. Result Contract

One SBAS run registers one `psinsar` product bundle.

Required bundle roles:

- stack manifest
- processor manifest
- selected network edges
- actual network edges if processor modified them
- velocity product
- time-series product
- temporal coherence or equivalent quality product
- geocoded rasters
- quicklooks
- logs
- processor reports
- product manifest

The catalog registers the publish manifest, not the transient work directory.

## 8. Delivery Phases

### Phase 1: Planning Boundary

- Stop auto-creating PS batches after search.
- Persist `TimeseriesStackPlanEdge`.
- Return edges from `/timeseries-plans/{plan_id}`.
- Add network thresholds to `PsRequest` with backward-compatible defaults.

### Phase 2: Manifest Boundary

- Add committed stack package creation.
- Generate immutable `stack_manifest.json`.
- Make the existing ISCE2/MintPy route consume the manifest.

### Phase 3: SARscape Discovery

- Add a SARscape SBAS task verifier script.
- Capture task names and required parameters per installed SARscape version.
- Add `sarscape_sbas` preflight endpoint.

Initial implementation points:

- `scripts/verify_sarscape_sbas_tasks.py`
- `POST /idl/inspect/sarscape-sbas`
- `POST /timeseries-production/sarscape-sbas/preflight`
- `python -m backend.app.services.envi_runner_cli --inspect-sarscape-sbas`

These entry points must stay read-only. They instantiate ENVI task definitions
and inspect parameters, but do not execute SBAS processing.

The time-series SARscape preflight endpoint builds a processor manifest from
the committed PS batch/stack plan context. It reports the selected network
edges, the SARscape task sequence, required publish roles, and current blockers.
At this phase it must return `ready_for_pipeline_design=true` when ENVI/SARscape
is discoverable, but `ready_for_execution=false` until a checked-in parameter
template and job handler are implemented.

Current implementation status:

- `sarscape_sbas` is a selectable time-series processor.
- The production UI defaults to `ENVI/SARscape SBAS` with `Preflight only`.
- `POST /timeseries-production/runs` accepts `processor_code` and
  `execution_mode`.
- SARscape runs use workflow `psinsar_sarscape_sbas_chain`.
- Preflight-only SARscape runs execute `prepare` plus
  `sarscape_processor_preflight`, then complete the task without launching the
  long SARscape stack execution.
- Full execution is gated by `SARSCAPE_SBAS_ALLOW_EXECUTION=true` and a
  `validated=true` parameter template at
  `SARSCAPE_SBAS_PARAMETER_TEMPLATE_PATH`.
- The checked-in template at
  `backend/templates/sarscape_sbas_parameter_template.example.json` is a
  placeholder contract and is intentionally not executable.

Observed on the target workstation:

- Lightweight `Engine.tasks()` discovery succeeds.
- Static `.task` extraction succeeds without starting taskengine. The extractor is:
  - `scripts/extract_sarscape_sbas_task_templates.py`
- The installed SARscape exposes native workflow metatasks:
  - `wf_sbas`
  - `wf_esbas`
- `wf_sbas` is an ENVI metatask at
  `C:\Program Files\Harris\ENVI56\user_custom_code\wf_sbas.task`.
  It is not listed by `Engine.tasks()` on this workstation, but
  `Engine("ENVI").task("wf_sbas")` can instantiate it successfully. Discovery
  therefore combines `Engine.tasks()` with static `.task` file detection.
  It contains an embedded 11-node DAG:
  - `SARscape_setting_output_folders`
  - `SARsLoadPreferences`
  - `SARsImportSarSelector`
  - `ENVIEXTRACTELEMENTSFROMARRAYTASK`
  - `SARscapeSuggestLooks`
  - `SARsInSARStackSBASGenerateConnectionGraph`
  - `SARsInSARStackSBASInterferogramGeneration`
  - `SARsInSARStackSBASInversionStep1`
  - `SARsInSARStackSBASInversionStep2`
  - `SARsInSARStackSBASGeocode`
  - `SARscapeEnviuriToShape`
- The static `wf_sbas.task` file contains 18 parameter entries including the
  embedded `DAG` default. Live taskengine `QueryTask` exposes 17 callable
  parameters; it does not require the caller to pass `DAG`.
- The core production inputs are:
  - `INPUT_FILE_LIST`
  - `SARSCAPE_PREFERENCE`
  - `DEM_SARSCAPEDATA`
  - `OUTPUT_FOLDER`
  - `GEOCODE_RG_GRID_SIZE`
  - `ESTIMATE_RESIDUAL_HEIGHT`
  - `DISPLACEMENT_MODEL_TYPE`
  - `OUTPUT_ENVI_CARTOGRAPHIC_SYSTEM`
- `wf_sbas` returns SBAS product handles:
  - `DISPLACEMENT_SARSCAPEDATA`
  - `DEM_OUT_SARSCAPEDATA`
  - `CORRECTION_H_SARSCAPEDATA`
  - `COHERENCE_SARSCAPEDATA`
  - `ALOS_SARSCAPEDATA`
  - `ILOS_SARSCAPEDATA`
  - `VELOCITY_SARSCAPEDATA`
  - `OUTPUT_SHAPES`
- The installed SARscape also exposes these stack tasks:
  - `SARsInSARStackSBASGenerateConnectionGraph`
  - `SARsInSARStackSBASInterferogramGeneration`
  - `SARsInSARStackSBASInversionStep1`
  - `SARsInSARStackSBASInversionStep2`
  - `SARsInSARStackSBASGeocode`
  - `SARsInSARStackSBASVariogram`
  - `SARsInSARStackESBASInterferogramGeneration`
  - `SARsInSARStackESBASInversion`
  - `SARsInSARStackESBASGeocode`
  - `SARsInSARConnectionGraphESBAS`
- Reading `.parameters` for stack SBAS tasks can hang taskengine. Parameter
  discovery must therefore be optional, subprocess-isolated, and timeout-bound.
  Processor implementation should use a checked-in task template or SARscape
  help/SML-derived parameter contract rather than relying on live parameter
  introspection at run time.
- Timeout cleanup must remove only taskengine processes spawned by the timed-out
  inspection subprocess. Existing user-launched ENVI/taskengine sessions should
  not be killed by name.
- SARscape/taskengine can create zero-byte `env_*.xyz` and `IDL*.tmp` files in
  the process current working directory. ENVI runner cwd and temp variables must
  point at `backend/runtime/idl_worker/envi_cwd`, not the repository root.
  Root-level `env_*.xyz` and `IDL*.tmp` are disposable taskengine leftovers.

### Phase 3.5: SARscape Native Workflow Strategy

The short-term production strategy is to integrate SARscape through `wf_sbas`.
This is the lowest-risk ENVI/SARscape path because SARscape already wires import,
connection graph generation, interferogram generation, inversion, geocoding, and
shape export in one metatask DAG.

The backend template contract now supports two execution strategies:

- `native_workflow_metatask`
  - Preferred first implementation.
  - Executes `wf_sbas` once with the committed stack manifest converted into
    `INPUT_FILE_LIST`, configured DEM, output folder, and basic SBAS options.
  - Does not directly consume the system-selected `network_edges`.
  - Requires post-run extraction of SARscape's actual connection graph for audit.

- `explicit_stack_tasks`
  - Future controllable implementation.
  - Executes `SARsInSARStackSBASGenerateConnectionGraph`,
    `InterferogramGeneration`, `InversionStep1`, `InversionStep2`, and
    `Geocode` as separate tasks.
  - May allow tighter control of graph settings, but direct injection of the
    system-selected edge list is not verified yet.

Current rule:

- `network_edges` remain mandatory in the stack manifest because they are the
  system planning decision and task-dispatch audit record.
- When using `wf_sbas`, SARscape may rebuild the graph internally. The output
  bundle must therefore contain both:
  - `selected_network_edges.json`
  - `actual_network_edges.json`, when it can be extracted from SARscape outputs

Current code points:

- `backend/app/services/envi_service.py`
  - Discovers `wf_sbas`, `wf_esbas`, support tasks, and stack tasks.
  - Cleans up only newly spawned `taskengine.exe` PIDs on timeout.
  - Runs subprocess and in-process envipyengine calls from
    `backend/runtime/idl_worker/envi_cwd` so taskengine temp files do not pollute
    the project root.
- `backend/app/services/sarscape_sbas_service.py`
  - Builds processor manifests with `execution_strategy`.
  - Reports both native and explicit strategy availability.
  - Requires `insar.prepared-sbas-stack/v1` before execution.
  - Executes `native_workflow_metatask` only when the template is validated and
    execution is explicitly enabled.
- `backend/app/services/timeseries_service.py`
  - Treats `TimeseriesStackPlan` as the candidate pool.
  - Creates `selected_stack_manifest.json` as the prepared stack in
    `prepare_run()`.
  - Writes `input/selected_network_edges.json` before SARscape preflight or
    execution.
  - Refuses SARscape preflight/execution when the prepared stack validation
    fails.
- `backend/templates/sarscape_sbas_parameter_template.example.json`
  - Records the `wf_sbas` parameter contract and DAG summary.
  - Keeps `validated=false` until a controlled run validates parameters and
    output capture.
- `scripts/extract_sarscape_sbas_task_templates.py`
  - Regenerates the static parameter report from installed `.task` files.

Open engineering items:

- Confirm `wf_sbas.INPUT_FILE_LIST` accepts the same LT-1 `*.meta.xml` list used
  by current SARscape import tasks.
- Confirm whether `DAG` must be passed explicitly or SARscape uses the embedded
  default from `wf_sbas.task`.
- Locate SARscape's written connection graph or auxiliary processing file and
  convert it into `actual_network_edges.json`.
- Map `VELOCITY_SARSCAPEDATA`, `DISPLACEMENT_SARSCAPEDATA`,
  `COHERENCE_SARSCAPEDATA`, and `OUTPUT_SHAPES` into the unified `psinsar`
  publish bundle.
- Decide later whether to invest in `explicit_stack_tasks` for strict graph
  injection, depending on whether SARscape exposes a supported graph import or
  connection-list parameter.

Smoke test on 2026-04-30:

- Applied the non-destructive `008_timeseries_stack_plan_edges.sql` migration.
- Backfilled two edges for test plan `tsp_d89bfc5bded744e6bf9b60c1` from
  `pairing_metric_cache` because the plan was created before the edge table
  existed.
- Ran SARscape SBAS preflight for batch
  `e240a63a-5941-4a86-8aae-182a6bc95dae`.
- Result:
  - `scene_count=3`
  - `network_edge_count=2`
  - `ready_for_pipeline_design=true`
  - `ready_for_execution=false`
  - `execution_strategy=native_workflow_metatask`
  - `missing_required_tasks=[]`
  - blockers are only `Template is not marked validated=true` and
    `SARSCAPE_SBAS_ALLOW_EXECUTION is false`.
- Created a `preflight_only` run
  `b7c2df45-a891-4ff7-b106-013e8d285fbd` and executed its `prepare` plus
  `sarscape_processor_preflight` steps. This wrote
  `selected_stack_manifest.json` and `sarscape_sbas_processor_manifest.json`
  without launching the full SARscape SBAS pipeline.
- Dispatch verification for workflow
  `eeaf1d82-7268-490c-9fb5-911a00a475c6` exposed a real workflow bug:
  `workflow_service.mark_step_completed()` advanced downstream steps to
  `READY`, but the database session has `autoflush=False`, so the immediate
  `enqueue_ready_steps()` query did not see the new `READY` status.
  `sarscape_processor_preflight` therefore stayed `READY` without a job.
- Fixed the dispatcher by flushing after `_advance_ready_steps()` and before
  `enqueue_ready_steps()`.
- Verified the dispatcher fix in a rollback-only two-step workflow regression
  check: completing step `a` immediately advanced step `b` to `RUNNING` and
  created its queued job.
- Re-ran the controlled dispatch path for only this workflow:
  - `TIMESERIES_PREPARE`: `COMPLETED`
  - `TIMESERIES_SARSCAPE_PREFLIGHT`: `COMPLETED`
  - workflow status: `COMPLETED`
  - task status: `COMPLETED`, progress `100`
  - run status: `PREPARED`
  - no `TIMESERIES_RUN_SARSCAPE_SBAS` job or `run_sarscape_sbas` step was
    created because execution mode was `preflight_only`.
- Root-level taskengine leftovers after the run:
  - `env_*.xyz`: `0`
  - `IDL*.tmp`: `0`
  ENVI status now reports runner cwd as
  `backend/runtime/idl_worker/envi_cwd`.

Parameter template validation on 2026-04-30:

- Initial live `Engine("ENVI").task("wf_sbas")` parameter inspection failed
  with `ENVITASK: No task matches: wf_sbas`, even though the static
  `wf_sbas.task` file was present.
- Root cause: SARscape installed `wf_sbas.task` under
  `C:\Program Files\Harris\ENVI56\user_custom_code`, while taskengine only
  auto-loads deployed custom tasks from `ENVI_CUSTOM_CODE`, the ENVI
  `custom_code` directory, the application user directory, or IDL packages.
- Backend runner now sets `ENVI_CUSTOM_CODE` to the discovered SARscape
  `user_custom_code` directory. This is process-local to the runner and does
  not modify the machine-level environment.
- After the fix, live `wf_sbas` parameter inspection succeeds:
  - `available=true`
  - `parameter_count=17`
  - required inputs: `INPUT_FILE_LIST`
  - outputs: `OUTPUT_SHAPES`, `DISPLACEMENT_SARSCAPEDATA`,
    `DEM_OUT_SARSCAPEDATA`, `CORRECTION_H_SARSCAPEDATA`,
    `COHERENCE_SARSCAPEDATA`, `ALOS_SARSCAPEDATA`,
    `ILOS_SARSCAPEDATA`, `VELOCITY_SARSCAPEDATA`
- Added repeatable validation script:
  `scripts/validate_sarscape_sbas_template.py`.
- Validation report:
  `backend/runtime/sarscape_sbas_template_validation_latest.json`.
- Current 3-scene validation result:
  - `ok=true`
  - validation scope: template contract only, no `task.execute()`
  - manifest scene count: `3`
  - network edge count: `2`
  - `INPUT_FILE_LIST_count=3`
  - scene `meta_path`, `tiff_path`, and folders all exist
  - DEM base, `.sml`, and `.hdr` all exist
  - remaining execution gate issue: checked-in template is still
    `validated=false`

Prepared stack boundary implementation on 2026-04-30:

- Added `prepared_stack_schema=insar.prepared-sbas-stack/v1` to
  `selected_stack_manifest.json`.
- Added `prepared_stack_id`, `source_plan_id`, `source_batch_id`,
  `candidate_pool_source`, and `production_contract`.
- Added `input/selected_network_edges.json` as the frozen planning/audit graph
  artifact.
- Added prepared stack validation for:
  - scene count and dates
  - required scene folder, TIFF, and metadata XML paths
  - zero-size source files
  - network edge count and edge date consistency
  - SARscape DEM dependency when SARscape is the selected processor
  - missing `selected_network_edges.json`
- SARscape processor preflight and execution now reject manifests that are not
  prepared stacks. The lower-level SARscape executor repeats this guard before
  calling any ENVI task.

Prepared stack UI/API update on 2026-04-30:

- Added read-only backend summary endpoint:
  `GET /timeseries-production/runs/{run_id}/prepared-stack`.
- The endpoint reads only existing run artifacts and does not trigger catalog
  scans, preflight, or SARscape execution.
- The summary reports:
  - prepared stack state
  - `prepared_stack_id`
  - manifest and selected network edge artifact paths
  - scene count and network edge count
  - prepared stack validation result
  - SARscape processor manifest readiness and blockers
- `TimeseriesProductionPanel` now shows a dedicated `Prepared SBAS Stack`
  section in run details.
- The SARscape preflight card now states that batch preflight is against the
  candidate pool, while production freezes a prepared stack before processor
  execution.
- `usePairingLogic` now marks created PS batches as candidate time-series pools
  in the planning context and logs that production will freeze a prepared SBAS
  stack during `prepare`.

### Phase 4: SARscape Execution

- Implement the SARscape SBAS processor steps.
- Serialize taskengine execution through the existing ENVI lock.
- Persist step manifests and logs.

Initial execution skeleton is in place:

- `TIMESERIES_SARSCAPE_PREFLIGHT`
- `TIMESERIES_RUN_SARSCAPE_SBAS`
- `backend/app/services/sarscape_sbas_service.py`

The execution handler resolves template macros and calls `execute_envi_task`
only after the template is readable, structurally valid, marked
`validated=true`, required tasks are discoverable, and execution is explicitly
enabled.

### Phase 5: Unified Result Management

- Normalize ISCE2/MintPy and SARscape outputs into the same publish bundle roles.
- Keep processor-specific files as secondary assets.
- Show products by role in the UI, not by processor-specific filenames.
