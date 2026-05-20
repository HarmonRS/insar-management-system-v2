# Legacy Time-Series InSAR Deprecation Record

Date: 2026-05-21

## Background

The project previously kept an ISCE2 + MintPy time-series/SBAS production path under:

```text
experiments/isce2_sbas_timeseries
```

That experiment directory has been removed. The active SBAS-InSAR direction is now the independent Gamma production workflow documented in:

```text
docs/SBAS_INSAR_PRODUCTION_PIPELINE_DESIGN_20260519.md
docs/GAMMA_IPTA_LT1_SBAS_TRIAL_RUNBOOK_20260518.md
```

Before this cleanup, startup validation still generated warnings for missing legacy paths:

```text
TIMESERIES_EXPERIMENT_ROOT
TIMESERIES_STACK_PREP_SCRIPT
TIMESERIES_MATERIALIZE_SCRIPT
TIMESERIES_PREPARE_DEM_SCRIPT
TIMESERIES_STACK_RUNNER_SCRIPT
TIMESERIES_MINTPY_SBAS_SCRIPT
TIMESERIES_EXPORT_PUBLISH_SCRIPT
```

Those warnings were misleading because they referred to the abandoned ISCE2/MintPy line, not the current Gamma SBAS-InSAR production line.

## Decision

The ISCE2/MintPy time-series production chain is deprecated and disabled by default.

The current SBAS-InSAR production authority is:

```text
Frontend view: sbas_insar_production
Backend route: /api/sbas-insar-production
Engine: Gamma
Workflow: DIFF + IPTA SBAS
```

The legacy code is not physically deleted yet. It remains only as compatibility and historical-reference code until the Gamma SBAS workflow can be tested end to end and historical result access is confirmed.

## Changes Made

Backend configuration:

- `backend/app/config.py`
  - `TIMESERIES_ENABLED` default changed from `true` to `false`.
  - Legacy `TIMESERIES_*` experiment/script defaults are now populated only when `TIMESERIES_ENABLED=true`.
  - `ensure_dirs()` no longer creates `TIMESERIES_WORK_ROOT` unless the legacy chain is explicitly enabled.
  - Runtime validation now reports an info line instead of warning about missing legacy experiment scripts when the chain is disabled.

Environment example:

- `.env.example`
  - `TIMESERIES_ENABLED=false`
  - `TIMESERIES_EXPERIMENT_ROOT=` is blank.
  - `TIMESERIES_DEFAULT_PROCESSOR_CODE=legacy_isce2_stack_mintpy`

Frontend production management:

- `frontend/src/config/appConstants.js`
  - Removed production workspace views:
    - `timeseries_runs`
    - `timeseries_products`
  - Legacy route aliases now map to the Gamma SBAS page:
    - `ps_production -> sbas_insar_production`
    - `ps_products -> sbas_insar_production`

- `frontend/src/ProductionWorkspace.jsx`
  - Removed lazy imports and render branches for:
    - `TimeseriesProductionPanel`
    - production-management `PsinsarCatalogPanel`
  - Updated production workspace text to describe Gamma SBAS as an independent entry.

- `frontend/src/components/app/AppSidePanel.jsx`
  - Updated production-management description to state that the old ISCE2/MintPy time-series entry is disabled.

Documentation:

- `docs/SBAS_INSAR_PRODUCTION_PIPELINE_DESIGN_20260519.md`
  - Added the legacy-chain deprecation decision.

- `docs/FRONTEND_NAVIGATION_ARCHITECTURE.md`
  - Updated production-management internal views to:
    - `dinsar_runs`
    - `sbas_insar_production`
    - `dinsar_products`

## Current Behavior

After backend restart, deployment validation should no longer warn about the removed `experiments/isce2_sbas_timeseries` path.

Expected validation line:

```text
[INFO] Legacy ISCE2/MintPy timeseries pipeline is disabled; current SBAS-InSAR production uses the Gamma /sbas-insar-production workflow.
```

The production-management page should show:

```text
D-InSAR 运行
SBAS-InSAR Production
D-InSAR 产物
```

It should no longer expose:

```text
时序InSAR 运行
时序InSAR 产物
```

## Verification

Completed on 2026-05-21:

- Frontend build passed:

```text
npm run build
```

- Runtime configuration check passed:

```text
scripts/check_runtime_config.py
```

Observed output included:

```text
[INFO] Legacy ISCE2/MintPy timeseries pipeline is disabled; current SBAS-InSAR production uses the Gamma /sbas-insar-production workflow.
[OK] Deployment configuration check passed.
```

- Backend config syntax was checked with Python AST parsing.

`python -m py_compile backend/app/config.py` was not used as the final check because Windows denied replacement of an existing `__pycache__` file. This was a local cache-permission issue, not a syntax failure.

## Retained Compatibility Code

The following code is intentionally retained for now:

```text
backend/app/routers/timeseries_production.py
backend/app/services/timeseries_service.py
frontend/src/TimeseriesProductionPanel.jsx
frontend/src/api/timeseriesProduction.js
frontend/src/components/PsinsarCatalogPanel.jsx
```

Database tables and historical product catalog structures such as `ps_timeseries_runs` are also retained.

`PsinsarCatalogPanel` may still be useful outside production management, especially for analysis/result browsing. Do not delete it until those usages are audited.

## Re-Enabling Legacy Chain

Re-enabling the legacy ISCE2/MintPy chain is not part of the current production plan.

If it must be revived for a controlled comparison, the operator must explicitly set:

```text
TIMESERIES_ENABLED=true
```

and provide valid values for all legacy experiment/script paths. The removed `experiments/isce2_sbas_timeseries` directory is no longer assumed to exist.

## Follow-Up Cleanup Criteria

Physical deletion of the legacy chain should wait until all of the following are true:

- Gamma SBAS production has completed an end-to-end run from stack discovery to published LOS velocity/sigma products.
- Historical `ps_timeseries_runs` and old time-series product records have a clear migration or read-only archival plan.
- Frontend navigation, route aliases, and analysis pages have been audited for remaining dependencies.
- Backend callers of `/api/timeseries-production` have either been removed or explicitly marked as legacy-only.
- Test coverage or manual regression notes confirm that D-InSAR production, SBAS production, product browsing, and task monitoring still work.
