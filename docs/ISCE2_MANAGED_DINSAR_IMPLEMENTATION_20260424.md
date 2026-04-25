# ISCE2 Managed D-InSAR Implementation 2026-04-24

## Scope

This document records the backend implementation that moves ISCE2 D-InSAR onto the same managed production lifecycle already used by ENVI.

The goal of this round is not to redesign the scientific core of ISCE2. The goal is to make ISCE2 production operationally healthy:

- submitted from the same production-management entry
- tracked by the same run / item / execution tables
- published into the same managed result tree
- scanned by the same catalog rebuild path
- compatible with database self-maintenance and health checks

## Implemented changes

### 1. Submission path

`/dinsar-production/run` now sends both `sarscape` and `isce2` through `dinsar_production_service.create_run(...)`.

This means ISCE2 no longer bypasses production management when it is launched from the normal UI entry.

### 2. Production run lifecycle

`dinsar_production_service.create_run(...)` now supports:

- `sarscape`
- `isce2`

For ISCE2 it creates:

- `dinsar_production_runs`
- `dinsar_production_run_items`
- `dinsar_production_executions`
- workflow steps with job type `ISCE2_RUN`

No schema migration was required for this round.

### 3. Managed ISCE2 controller

`JOB_TYPE_ISCE2_RUN` now has two modes:

- legacy queued mode: no `production_run_id`, keep the old shared queued handler
- managed production mode: `production_run_id` present, use the new WSL production controller

The managed controller is responsible for:

- creating one execution row per selected task
- forcing a managed run directory for each task
- writing `execution_manifest.json`
- writing `current/isce2__<profile>.json`
- publishing successful run directories
- rebuilding the result catalog after publish

### 4. Managed result layout

Each successful ISCE2 task now lands in the same managed D-InSAR tree as ENVI:

```text
D:\production_results\dinsar\<pair_key>\
  current\
    isce2__lt1_stripmap.json
  runs\
    <run_key>\
      .dinsar_run.json
      execution_manifest.json
      manifest.json
      native\
        workflow\
          ...
        export\
          <task>_disp.tif
          <task>_coh.tif
      assets\
        disp\
          disp.tif
        coh\
          coh.tif
      preview\
        thumb.webp
```

Rules:

- `native/` keeps the raw ISCE2 work and export outputs
- `assets/` holds the standardized files consumed by the system
- `manifest.json` is still written by `publish_from_sources(...)`
- `result_products` rows are still created by `rebuild_catalog(...)`

### 5. ISCE2 layout normalization

`backend/app/services/dinsar_result_layout_service.py` now includes `normalize_isce2_run_layout(...)`.

Behavior:

- copy raw ISCE2 `*_disp.tif` into `assets/disp/disp.tif`
- copy raw ISCE2 `*_coh.tif` into `assets/coh/coh.tif`
- keep the raw files under `native/export`
- rewrite run metadata / execution manifest / current pointer / package manifest when those files already exist

## Completion semantics

`rerun_mode=unfinished_only` for ISCE2 now checks the managed pointer:

```text
<DINSAR_PRODUCT_DIR>\<pair_key>\current\isce2__<profile>.json
```

This is intentional.

Legacy raw ISCE2 folders without a managed current pointer are no longer treated as a completed production result. That keeps skip logic aligned with production management rather than with stray historical outputs.

## Database and self-check compatibility

This implementation keeps the existing operational contract:

- no new tables
- no destructive migration
- no change to `result_products` / `result_assets` / `result_issues` schema
- no change to `dinsar_production_runs` schema

Operational compatibility points:

- catalog state is still rooted at `settings.DINSAR_PRODUCT_DIR`
- health checks still use the same catalog-state and manifest-tree logic
- database self-maintenance still works because manifests remain canonical and catalog rebuild remains the only registration path

## Validation completed in this round

- syntax compilation in memory for the edited Python modules
- direct module import validation for:
  - `backend.app.dinsar_engines.isce2_engine`
  - `backend.app.services.dinsar_result_layout_service`
  - `backend.app.services.dinsar_production_service`
  - `backend.app.routers.dinsar_production`
  - `backend.app.services.job_handlers`

## Remaining work

- add hard cancellation for an in-flight WSL subprocess
- add Gamma D-InSAR on top of the same managed controller contract
- decide whether `lt1_stripmap` should stay as the public profile code or be renamed in a later migration round
