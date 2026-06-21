# Local-Only Source Archive and Task_Pool Materialize Design

Current canonical operating contract: [THREE_SENSOR_LOCAL_PRODUCTION_CONTRACT_20260616.md](THREE_SENSOR_LOCAL_PRODUCTION_CONTRACT_20260616.md). This file remains the implementation detail for local archive metadata extraction and Task_Pool materialization.

## Decision

As of 2026-06-15, UNC/SMB storage is removed from the active production path.

The switch throughput is too low for production data movement. Large source archives, precise orbit files, GF3 native result pools, task staging, and engine inputs must all live on local disks. A network share may exist outside this system for manual backup or manual transfer, but it must not be configured in runtime environment variables used by backend scans, inventory, materialization, or production.

Production engines consume local Task_Pool inputs. If a selected LT-1 or Sentinel-1 source product is archived, it is extracted from a local source archive into `D:\Task_Pool` or a task-specific subdirectory before engine execution.

The local source archive remains the source of record. LT-1 and Sentinel-1 materialization must never delete the original archive after extraction.

## Current Implementation

- Source asset inventory recognizes archive assets:
  - `S1_ZIP`
  - `LT1_ARCHIVE`
  - `GF3_ARCHIVE`
- Sentinel-1 ZIP manifest parsing reads `manifest.safe` directly from the ZIP.
- LT-1 archive parsing reads `*.meta.xml` directly from `.zip`, `.tar`, `.tar.gz`, or `.tgz` and records contained TIFF members.
- `SOURCE_PRODUCT_DIRS` is the local LT-1/Sentinel-1 source archive inventory root.
- Source asset listing and inventory counts include archive assets instead of hiding `S1_ZIP`.
- A generic source materialize endpoint exists:
  - `POST /api/assets/sources/{asset_id}/materialize`
  - `S1_ZIP` uses the existing Sentinel-1 SAFE unpacker.
  - `LT1_ARCHIVE` and `GF3_ARCHIVE` extract to a local materialized directory.
  - Directory assets return `DIRECTORY_READY`.
- If no `target_root` is supplied, generic materialize defaults to `TASK_POOL_ROOT\source_materialized\<sensor>`.

Default source materialization is local and task-scoped. D-InSAR and SBAS callers should pass a Task_Pool target directory:

```text
D:\Task_Pool\DInSAR\<task>\master
D:\Task_Pool\DInSAR\<task>\slave
D:\Task_Pool\SBAS\<stack>\sources\<YYYYMMDD>
```

The generic materialize endpoint still accepts `target_root` for ad hoc checks. Production callers should provide a task-specific Task_Pool destination.

## Production Boundary

D-InSAR and SBAS store local source asset references in task/run manifests, then materialize selected inputs into the run directory before engine execution.

Required integration points:

- D-InSAR Task_Pool publishing:
  - store `source_product_asset_id`, `archive_path`, and `source_format`;
  - materialize master/slave archive assets into the task directory before engine dispatch.
- Gamma/PyINT:
  - consume local materialized paths because WSL conversion rejects or cannot reliably map network paths.
- LandSAR and ENVI/SARscape:
  - consume local materialized paths.
- SBAS:
  - stack discovery can use archive metadata;
  - selected scenes must be materialized into the SBAS `RAW`/input structure before Gamma commands such as `par_LT1_SLC`.

Production safety rule: if a run cannot materialize every selected source asset locally, the run must fail before invoking the engine.

## GF3 Management

GF3 now has a separate operational rule:

- GF3 SARscape production is not run on this management machine.
- Already-produced SARscape `_geo` ENVI binary results are stored locally under `GF3_SARSCAPE_NATIVE_DIRS`.
- The system registers those local `_geo` native results and their `.hdr/.sml` sidecars.
- `*_geo_ql.tif` is retained only as an auxiliary quicklook file.
- WebP preview cache is generated locally from the `_geo` ENVI binary, not from `*_geo_ql.tif`.
- Standard GeoTIFF conversion remains a separate explicit path; the monitor button used for GF3 registration is native-result registration only.

## Recommended Local Layout

```text
D:\
  ├─ LuTan1_Image_Pool_Zip
  │  └─ LT1*.tar.gz / LT1*.tgz / LT1*.zip / LT1*.tar
  ├─ Sentinel1_Image_Pool_ZIP
  │  └─ S1*.zip
  ├─ LuTan1_Image_Pool
  │  └─ LT1 unpacked scene directories
  ├─ Sentinel1_Image_Pool
  │  └─ S1*.SAFE directories
  ├─ LT1_data_lsarorbit
  │  └─ LT1*_GpsData_*.txt
  ├─ Sentinel1_EOF_Pool
  │  └─ S1*.EOF
  ├─ production_results
  │  └─ gf3
  │     ├─ sarscape_native
  │     │  └─ YYYYMMDD_geo
  │     │     └─ GF3_*
  │     │        ├─ *_geo
  │     │        ├─ *_geo.hdr
  │     │        ├─ *_geo.sml
  │     │        └─ *_geo_ql.tif
  │     └─ standard_l2
  └─ Task_Pool
D:\Task_Pool
  ├─ DInSAR
  │  └─ <pair_task>
  │     ├─ task_manifest.json
  │     ├─ .dinsar_pair.json
  │     ├─ master
  │     ├─ slave
  │     ├─ orbit
  │     ├─ work
  │     └─ publish
  └─ SBAS
     └─ <stack_task>
        ├─ task_manifest.json
        ├─ sbas_stack_manifest.json
        ├─ sources
        ├─ orbits
        ├─ work
        └─ publish
```

Date folders are optional for the LT-1/Sentinel-1 scanners because inventory recurses through configured roots. GF3 native pools should keep the SARscape `YYYYMMDD_geo/<scene>` convention.

## Current Local Configuration Example

The local `.env` should keep all runtime roots local:

```text
SOURCE_PRODUCT_DIRS=D:\LuTan1_Image_Pool_Zip;D:\Sentinel1_Image_Pool_ZIP
SENTINEL1_STORAGE_DIRS=
ORBIT_SOURCE_DIRS=D:\LT1_data_lsarorbit;D:\Sentinel1_EOF_Pool
MONITOR_ORBIT_DIR=D:\LT1_data_lsarorbit
GF3_TASK_POOL_ROOT=D:\GaoFen3_Task_Pool
GF3_ARCHIVE_SOURCE_DIRS=D:\GaoFen3_Image_Pool\archives
GF3_SARSCAPE_NATIVE_DIRS=D:\GaoFen3_Image_Pool\sarscape_native
GF3_STORAGE_DIRS=D:\GaoFen3_Image_Pool\standard_l2
TASK_POOL_ROOT=D:\Task_Pool
DINSAR_TASK_POOL_ROOT=D:\Task_Pool\DInSAR
SBAS_TASK_POOL_ROOT=D:\Task_Pool\SBAS
GAMMA_SBAS_WORK_ROOT=D:\Task_Pool\SBAS
```

Do not configure UNC paths in these variables.

## Orbit Pool Contract

There are two different orbit concepts:

- `ORBIT_SOURCE_DIRS`: local source inventory roots.
- `ORBIT_POOL_ENVI` / `PYINT_ORBIT_POOL_TXT`: local production orbit pools.

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

The `LT1A` and `LT1B` names are satellite names, not product levels. ENVI/Gamma/PyINT/SBAS should use local orbit files copied or synchronized from `ORBIT_SOURCE_DIRS`.

## Migration Phases

### Phase 1: Local source archive inventory

Keep production source archives local:

- LT-1 compressed scenes in `D:\LuTan1_Image_Pool_Zip`.
- Sentinel-1 ZIP scenes in `D:\Sentinel1_Image_Pool_ZIP`.
- Existing unpacked local scene directories are not active management pools; they should only be task materialization outputs.

### Phase 2: Local orbit deployment

Deploy orbit source files on this machine:

- LT-1 TXT files under `D:\LT1_data_lsarorbit`.
- Sentinel-1 EOF files under `D:\Sentinel1_EOF_Pool`.

Keep `ORBIT_POOL_ENVI` and `PYINT_ORBIT_POOL_TXT` local.

### Phase 3: GF3 native-result registration

Copy completed SARscape `_geo` result folders to the local GF3 native pool:

```text
D:\GaoFen3_Image_Pool\sarscape_native\YYYYMMDD_geo\<GF3 scene>\
```

Then run GF3 native-result registration and GF3 WebP generation. WebP generation reads the `_geo` ENVI binary and requires its `.hdr` sidecar.

### Phase 4: Task_Pool production

After inventory scan verifies local assets:

1. D-InSAR Task_Pool stores source asset IDs and archive paths.
2. Task preparation materializes master/slave scenes and orbit files under `D:\Task_Pool\DInSAR\<task>`.
3. Engines run only against local Task_Pool paths.
4. Results register normally.
5. Local materialized inputs and intermediate products are eligible for cleanup after result registration.

## Local Cleanup Design

After production results are registered as assets, Task_Pool materialized inputs and work folders can be treated as cleanup candidates. Local source archive roots are durable production inputs and must not be cleaned as cache.

### Keep Classes

Cleanup must never delete:

- configured local source archive roots;
- configured local orbit source roots;
- registered D-InSAR result assets;
- registered SBAS result assets;
- registered GF3 SARscape native result assets;
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
- GF3 SARscape runtime or temporary staging only after local native `_geo` registration and optional native-retention policy allows cleanup.

### Safety Contract

Every cleanup operation should run in two phases:

1. `preview`: enumerate candidate paths, classify each path, show size, last modified time, owning task/run/product, and keep/delete reason.
2. `execute`: delete only candidates from a persisted preview token or exact candidate list.

Deletion must require:

- path is inside an approved local work root;
- path is not inside any configured source archive root;
- path is not inside a result publish root unless the exact file is classified as intermediate;
- associated result or standardized asset is registered;
- cleanup policy explicitly allows the class;
- operator confirmation is present.
