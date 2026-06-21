# Frontend Navigation Architecture

Last updated: 2026-06-20

This document is the frontend navigation source of truth. It reflects the current product decision: the system manages data for LT-1, Sentinel-1, and GF-3, but production is organized only around D-InSAR and SBAS-InSAR. PS-InSAR and legacy time-series entries are compatibility code, not visible primary workflows.

## Current First-Level Groups

- `data`: 数据管理
- `production_management`: 生产管理
- `insar_analysis`: InSAR形变分析
- `flood_analysis`: 洪涝灾害分析
- `ops`: 运行维护

Definition files:

- `frontend/src/config/appConstants.js`
- `frontend/src/utils/appUiHelpers.js`
- `frontend/src/components/app/AppSidePanel.jsx`
- `frontend/src/ProductionWorkspace.jsx`

## Data Management

```text
数据管理
├─ 入库监控 (`ingest`)
├─ 资产库存 (`asset_inventory`)
├─ 数据列表 (`data`)
└─ 灾害点 (`hazard`)
```

Boundary:

- LT-1 and Sentinel-1 source data are managed as local compressed archives.
- Metadata, footprint, and preview are extracted from archives without full unpacking.
- Full materialization happens only when production preparation creates a task under the local Task_Pool.
- GF-3 registers copied native `_geo` production results and generates local WebP previews.

## Production Management

`production_management` is a workspace group. The left navigation exposes one entry; the workspace owns the internal production views.

```text
生产管理 (`production_management`)
├─ D-InSAR配对规划 (`dinsar_pairing`)
├─ D-InSAR任务规划 (`dinsar_pairs`)
├─ D-InSAR任务批次 (`dinsar_batches`)
├─ D-InSAR生产准备/分发 (`dinsar_prepare`)
├─ D-InSAR运行 (`dinsar_runs`)
├─ D-InSAR产物 (`dinsar_products`)
├─ SBAS-InSAR Production (`sbas_insar_production`)
├─ SBAS-InSAR结果 (`sbas_insar_products`)
├─ 陆探生产占位 (`lt1_production`)
├─ 哨兵生产占位 (`sentinel1_production`)
└─ 高分三结果登记 (`gf3_native_registration`)
```

Boundary:

- D-InSAR uses the sequence: pair planning -> selected pairs -> D-InSAR batch -> production preparation -> run -> product catalog.
- D-InSAR production preparation materializes archive sources into `DINSAR_TASK_POOL_ROOT` and must not use UNC paths.
- Data distribution is a separate D-InSAR mode that exports source archive bundles under `DATA_DISTRIBUTION_ROOT`; it is not the production runtime path.
- SBAS-InSAR uses the dedicated Gamma/LandSAR SBAS production page. It does not depend on the old D-InSAR pair list or PS candidate-stack page.
- GF-3 is not produced on this server. The server registers native `_geo` results copied into the configured GF-3 pool and builds WebP from the produced binary raster, not from quicklook TIFFs.

Compatibility route aliases:

- `pairing` -> `dinsar_pairing`
- `pairs` -> `dinsar_pairs`
- `ps_results` -> `sbas_insar_production`
- `batches` -> `dinsar_batches`
- `copier` -> `dinsar_prepare`
- `dinsar_production` -> `dinsar_runs`
- `dinsar_products` -> `dinsar_products`
- `ps_production` -> `sbas_insar_production`
- `ps_products` -> `sbas_insar_products`

These aliases exist so existing code paths can redirect into the workspace. They are not standalone left-navigation entries.

## InSAR Analysis

```text
InSAR形变分析
├─ D-InSAR
│  ├─ D-InSAR结果 (`dinsar_results`)
│  └─ D-InSAR分析 (`dinsar_analysis`)
│     ├─ AI质量评估
│     └─ D-InSAR诊断
└─ SBAS
   └─ SBAS-InSAR分析 (`psinsar_analysis`)
```

Boundary:

- Analysis pages consume registered result catalogs.
- They should not submit production jobs or materialize source archives.
- The standalone `AI分析` first-level page has been removed. D-InSAR quality assessment and D-InSAR diagnosis are owned by `dinsar_analysis`.
- D-InSAR diagnosis uses the `AI_DIAGNOSIS` task type and persists reports in the `ai_diagnosis` table. The older `AI_ANALYZE` endpoint/task is compatibility code only.
- `psinsar_analysis` remains the historical route key, but its user-facing meaning is SBAS-InSAR analysis.

## Deprecated Visible Workflows

The following workflows must not be shown as primary UI entries:

- PS-InSAR production
- PS candidate-stack distribution
- legacy ISCE2/MintPy time-series production
- source-folder distribution for unpacked LT-1 or Sentinel-1 folders
- standalone AI analysis first-level navigation
- remote-sensing vision AI placeholder pages

Backend compatibility code may remain until historical data models and catalog names are migrated.

## Navigation Update Rules

- Add production execution, preparation, product registration, and product catalog features inside `ProductionWorkspace`.
- Add source ingestion, archive scanning, orbit scanning, and storage inventory under `data`.
- Add result interpretation and map analysis under `insar_analysis`.
- Keep `production_management` as the only production first-level group.
- Do not reintroduce a separate `production_planning` first-level group.
- When changing navigation, update `appConstants.js`, `appUiHelpers.js`, `AppSidePanel.jsx`, `ProductionWorkspace.jsx`, and this document together.
