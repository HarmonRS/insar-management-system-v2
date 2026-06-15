# UNC Source Archive and Local Materialize Design

## Decision

UNC/SMB storage is treated as the source archive pool. Production engines should not use UNC paths as their working input. D-InSAR, SBAS, Gamma/PyINT, LandSAR, and SARscape should consume local materialized task inputs.

This keeps the 20 TB storage useful for long-term source management while protecting production from SMB disconnects, credential scope, WSL path conversion, and external engine UNC compatibility.

## Current Implementation

- Source asset inventory now recognizes archive assets:
  - `S1_ZIP`
  - `LT1_ARCHIVE`
  - `GF3_ARCHIVE`
- Sentinel-1 ZIP manifest parsing already reads `manifest.safe` directly from the ZIP.
- LT-1 archive parsing reads `*.meta.xml` directly from `.zip`, `.tar`, `.tar.gz`, or `.tgz` and records contained TIFF members.
- GF3 archive parsing reads the first XML member directly from `.zip`, `.tar`, `.tar.gz`, or `.tgz` and records quicklook-like members when present.
- `GF3_ARCHIVE_SOURCE_DIRS` roots are included in asset inventory scans as source pools.
- Source asset listing and inventory counts now include archive assets instead of hiding `S1_ZIP`.
- A generic source materialize endpoint exists:
  - `POST /api/assets/sources/{asset_id}/materialize`
  - `S1_ZIP` uses the existing Sentinel-1 SAFE unpacker.
  - `LT1_ARCHIVE` and `GF3_ARCHIVE` extract to a local materialized directory.
  - Directory assets return `DIRECTORY_READY`.

Default local materialize root is:

```text
<PYINT_WORK_ROOT>\source_materialized\<source_format>
```

Callers may pass `target_root` to force a D-InSAR Task_Pool or SBAS run-specific input directory.

## Production Boundary

D-InSAR and SBAS should store source asset references in task/run manifests, then materialize selected inputs into the run directory before engine execution.

Required next integration points:

- D-InSAR Task_Pool publishing:
  - store `source_product_asset_id`, `archive_path`, `source_format`;
  - materialize master/slave archive assets into the task directory before engine dispatch.
- Gamma/PyINT:
  - always consume local materialized paths because WSL conversion rejects or cannot reliably map UNC paths.
- LandSAR and ENVI/SARscape:
  - prefer local materialized paths even when Windows can see UNC, to avoid external engine path and credential issues.
- SBAS:
  - stack discovery can use archive metadata;
  - selected scenes must be materialized into the SBAS `RAW`/input structure before Gamma commands such as `par_LT1_SLC`.

## GF3 Management

GF3 has two asset layers:

- `GF3_ARCHIVE`: original source archive, suitable for UNC source management and migration tracking.
- GF3 SARscape standardized L2: production result/analysis-ready layer, used for map footprint, preview, radar data management, and water extraction.

Do not replace standardized L2 management with raw archive management. Archive assets should link migration and production status; previews and water extraction should continue to consume standardized L2/analysis-ready products.

## Migration Guidance

1. Register UNC roots first and scan inventory.
2. Verify archive asset counts and parse status.
3. Keep existing local standardized results and D-InSAR/SBAS products in place.
4. Move source archives to UNC and update root configuration.
5. Only after inventory and materialize tests pass, switch D-InSAR/SBAS publishing to archive asset references.

Production safety rule: if a run cannot materialize every selected source asset locally, the run must fail before invoking the engine.

## Recommended UNC Layout

The current deployment uses two SMB shares:

```text
\\DESKTOP-N16HJ84\InSAR_Storage_1
\\DESKTOP-N16HJ84\InSAR_Storage_2
```

Recommended source archive layout:

```text
\\DESKTOP-N16HJ84\InSAR_Storage_1
  └─ GaoFen-3
     ├─ 20260513
     │  └─ GF3_*.tar.gz
     └─ 20260514

\\DESKTOP-N16HJ84\InSAR_Storage_2
  ├─ LuTan-1
  │  └─ Archive
  │     ├─ 20260513
  │     │  └─ LT1*.tar.gz / LT1*.tgz / LT1*.zip / LT1*.tar
  │     └─ 20260514
  ├─ Sentinel-1
  │  └─ Archive
  │     ├─ 20260513
  │     │  └─ S1*.zip
  │     └─ 20260514
  └─ Orbit
     ├─ LuTan-1
     │  ├─ LT1A_GpsData_GAS_C_YYYYMMDD.txt
     │  └─ LT1B_GpsData_GAS_C_YYYYMMDD.txt
     └─ Sentinel-1
        └─ S1*.EOF
```

Date folders are optional for the scanner because source and orbit inventory recurse through configured roots. They are recommended for operator readability and migration checks.

## Current Local Configuration Example

The local `.env` should keep legacy local roots and UNC roots side by side during migration:

```text
SOURCE_PRODUCT_DIRS=D:\LuTan1_Image_Pool;D:\Sentinel1_Image_Pool_ZIP;\\DESKTOP-N16HJ84\InSAR_Storage_2\LuTan-1\Archive;\\DESKTOP-N16HJ84\InSAR_Storage_2\Sentinel-1\Archive
ORBIT_SOURCE_DIRS=D:\LT1_data_lsarorbit;D:\Sentinel1_EOF_Pool;\\DESKTOP-N16HJ84\InSAR_Storage_2\Orbit\LuTan-1;\\DESKTOP-N16HJ84\InSAR_Storage_2\Orbit\Sentinel-1
GF3_ARCHIVE_SOURCE_DIRS=\\DESKTOP-N16HJ84\InSAR_Storage_1\GaoFen-3
```

Do not store SMB credentials in `.env`. Credentials should be stored in Windows Credential Manager for the account that runs the backend/worker service.

## Orbit Pool Contract

There are two different orbit concepts:

- `ORBIT_SOURCE_DIRS`: source inventory roots. These can be UNC and may be date-organized or flat.
- `ORBIT_POOL_ENVI` / `PYINT_ORBIT_POOL_TXT`: local production orbit pools. These should remain local disk paths.

LT-1 local production orbit pool should support both flat and satellite-split layouts:

```text
D:\orbit_pools\envi
  ├─ LT1A
  │  └─ LT1A_GpsData_GAS_C_YYYYMMDD.txt
  ├─ LT1B
  │  └─ LT1B_GpsData_GAS_C_YYYYMMDD.txt
  └─ converted
     └─ envi
```

The `LT1A` and `LT1B` names are satellite names, not product levels. ENVI/Gamma/PyINT/SBAS should use local orbit files copied or synchronized from `ORBIT_SOURCE_DIRS`; they should not be required to read UNC directly.

Sentinel-1 EOF files can be indexed from UNC. Gamma/PyINT/SBAS execution should stage required EOF files locally with the selected scenes.

## Migration Phases

### Phase 1: Source archive migration

Move or copy source archives only:

- LT-1 compressed scenes to `\\DESKTOP-N16HJ84\InSAR_Storage_2\LuTan-1\Archive\<YYYYMMDD>\`.
- Sentinel-1 ZIP scenes to `\\DESKTOP-N16HJ84\InSAR_Storage_2\Sentinel-1\Archive\<YYYYMMDD>\`.
- GF3 raw archives to `\\DESKTOP-N16HJ84\InSAR_Storage_1\GaoFen-3\<YYYYMMDD>\`.

Keep current local unpacked scene directories in place until D-InSAR and SBAS archive materialization have been tested.

### Phase 2: Orbit source migration

Copy orbit source files to UNC:

- LT-1 TXT files to `\\DESKTOP-N16HJ84\InSAR_Storage_2\Orbit\LuTan-1\`.
- Sentinel-1 EOF files to `\\DESKTOP-N16HJ84\InSAR_Storage_2\Orbit\Sentinel-1\`.

Keep `ORBIT_POOL_ENVI` and `PYINT_ORBIT_POOL_TXT` local. Add a later sync/materialize step to populate local orbit pools from the indexed UNC source assets.

### Phase 3: Production cutover

After inventory scan verifies UNC assets:

1. D-InSAR Task_Pool stores source asset IDs and archive paths.
2. Task preparation materializes master/slave scenes and orbit files locally.
3. Engines run only against local Task_Pool paths.
4. Results register normally.
5. Local materialized inputs and intermediate products are eligible for cleanup after result registration.

### Phase 4: Retire old local source pools

Only after repeated D-InSAR/SBAS runs succeed from archive materialization:

- remove old local source roots from `SOURCE_PRODUCT_DIRS`;
- keep local work/result roots;
- keep standardized GF3 L2 products unless explicitly migrated and revalidated.

## Local Cleanup Design

After source archives are managed on UNC and production results are registered as assets, local disk can be treated as a cache/work area. Cleanup should be explicit and asset-aware.

### Keep Classes

Cleanup must never delete:

- configured UNC source archive roots;
- local or UNC orbit source roots;
- registered D-InSAR result assets;
- registered SBAS result assets;
- registered GF3 standardized L2 assets;
- `SAR_ANALYSIS_READY_ROOT` products and water extraction result assets;
- current pointers, manifests, previews, and catalog metadata needed to open results.

### Cleanup Classes

Cleanup may delete only these local classes after verification:

- materialized source inputs under `source_materialized`;
- D-InSAR Task_Pool copied inputs after every required engine run is registered;
- D-InSAR engine intermediate folders not listed in the result manifest;
- Gamma/PyINT temporary project work directories after result registration;
- SBAS `RAW`, `SLC`, `RSLC`, `MLI`, `DIFF`, `DIFF1`, script logs, and temporary staging after SBAS product registration;
- GF3 SARscape native intermediates only after standardized L2 registration and optional native-retention policy allows cleanup.

### Safety Contract

Every cleanup operation should run in two phases:

1. `preview`: enumerate candidate paths, classify each path, show size, last modified time, owning task/run/product, and keep/delete reason.
2. `execute`: delete only candidates from a persisted preview token or exact candidate list.

Deletion must require:

- path is inside an approved local work root;
- path is not inside any configured source archive root;
- path is not inside a result publish root unless the exact file is classified as intermediate;
- associated result or standardized asset is registered;
- candidate is older than a configurable minimum age;
- no active task references the path.

### Proposed API

```text
POST /api/maintenance/cleanup/preview
POST /api/maintenance/cleanup/execute
```

Preview request fields:

```json
{
  "scope": "dinsar|sbas|gf3|materialized|all",
  "root_ids": [],
  "older_than_hours": 24,
  "require_registered_result": true,
  "include_task_pool_inputs": false
}
```

Preview response should include:

```json
{
  "preview_id": "...",
  "total_bytes": 0,
  "candidates": [
    {
      "path": "D:\\production_runtime\\...",
      "class": "materialized_source",
      "owner": "task/run/product id",
      "size_bytes": 0,
      "eligible": true,
      "reason": "registered_result_exists"
    }
  ],
  "blocked": []
}
```

### Recommended Defaults

- `materialized`: delete after 24 hours if no active task references it.
- `dinsar`: delete engine intermediates after result registration; keep Task_Pool inputs until all selected engines are complete or user opts in.
- `sbas`: delete heavy Gamma working directories after SBAS catalog registration and product assets exist.
- `gf3`: keep standardized L2; clean SARscape native only when `GF3_SARSCAPE_CLEAN_AFTER_SUCCESS=true` and standardized registration is confirmed.

### Implementation Order

1. Add read-only cleanup preview service.
2. Add path classification and approved-root checks.
3. Add execute endpoint with preview token.
4. Add frontend maintenance panel.
5. Wire D-InSAR/SBAS/GF3 run pages to show cleanup eligibility after successful registration.
