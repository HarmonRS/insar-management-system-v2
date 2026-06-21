# Precise Orbit Production Contract 2026-06-17

This document is the current contract for LT-1 and Sentinel-1 precise orbit management. UNC paths are not allowed in active source, orbit, task, or production paths.

## Two Layers

There are two different orbit layers.

`ORBIT_SOURCE_DIRS` is the source asset layer. It is scanned into `orbit_assets` and used for scene-orbit binding:

```env
ORBIT_SOURCE_DIRS=D:\LT1_data_lsarorbit;D:\Sentinel1_EOF_Pool
```

`ORBIT_POOL_ENVI` / `PYINT_ORBIT_POOL_TXT` / `GAMMA_SBAS_ORBIT_ROOTS` are LT-1 production orbit pools. They are local TXT pools consumed by ENVI/SARscape and Gamma/PyINT:

```env
ORBIT_POOL_ENVI=D:\orbit_pools\envi
PYINT_ORBIT_POOL_TXT=D:\orbit_pools\envi
GAMMA_SBAS_ORBIT_ROOTS=D:\orbit_pools\envi
```

Expected LT-1 production layout:

```text
D:\orbit_pools\envi
  LT1A\
    LT1A_GpsData_GAS_C_YYYYMMDD.txt
  LT1B\
    LT1B_GpsData_GAS_C_YYYYMMDD.txt
```

`LT1A` and `LT1B` are satellite names, not product levels.

## Scan Semantics

The active scan entry is `/assets/inventory/scan`.

- `inventory_types=["orbit_asset"], families=["LT1"]` scans LT-1 orbit TXT files.
- `inventory_types=["orbit_asset"], families=["S1"]` scans Sentinel-1 EOF files.
- `inventory_types=["orbit_asset"], families=["LT1","S1"]` scans both.

LT-1 scanning recognizes `LT1A_GpsData_GAS_C_YYYYMMDD.txt` and `LT1B_GpsData_GAS_C_YYYYMMDD.txt`.

Sentinel-1 scanning recognizes `S1*_OPER_AUX_*.EOF` and matches EOF validity windows to scene acquisition windows.

After an LT-1 orbit scan, the scanner also synchronizes the LT-1 production TXT pool under `ORBIT_POOL_ENVI`. This keeps Gamma/PyINT and Gamma SBAS able to find the same TXT files without relying on the old monitor scan.

Orbit asset scans are incremental. For already indexed TXT/EOF files, the scanner skips metadata parsing and database upsert when the file path, size, mtime, parser version, active flag, and `parse_status=OK` still match the database record. Missing files are still marked inactive by comparing the scanned `seen_paths` set with existing assets under the same managed root.

## Engine Consumers

ENVI/SARscape D-InSAR:

- Uses local LT-1 data prepared for the SARscape workflow.
- The LT-1 TXT production pool is `ORBIT_POOL_ENVI`.
- The pool must be split by satellite because ENVI-side tools expect stable satellite folders.

Gamma/PyINT D-InSAR:

- Reads LT-1 TXT orbit files from `PYINT_ORBIT_POOL_TXT`.
- Current default keeps `PYINT_ORBIT_POOL_TXT=ORBIT_POOL_ENVI`.
- For LT-1, input assets may stage TXT orbits into the task input manifest when the precise-orbit bridge is enabled.
- For Sentinel-1, EOF paths come from source/orbit asset binding or task `orbit` staging.

Gamma SBAS:

- For LT-1, reads TXT orbit roots from `GAMMA_SBAS_ORBIT_ROOTS`.
- For Sentinel-1, planning uses `ORBIT_SOURCE_DIRS` EOF roots; S1 SBAS execution is not enabled.
- LT-1 Gamma SBAS scripts use the orbit path recorded in scene discovery.

LandSAR:

- Current D-InSAR integration does not independently scan an orbit pool.
- It consumes already prepared LT-1 task input.
- `ORBIT_POOL_LANDSAR` is not an active synchronization target in current code.

ISCE2:

- ISCE2 is retired from the active D-InSAR production path.
- `ORBIT_POOL_ISCE2` is legacy only and should be empty unless `ISCE2_ENABLED=true`.
- When `ISCE2_ENABLED=false`, health and orbit status must not treat missing ISCE2 XML as a production error.

## Database State

`orbit_assets` records original orbit files from `ORBIT_SOURCE_DIRS`.

`scene_orbit_bindings` records candidate and selected scene-orbit matches.

`radar_data.selected_orbit_asset_id`, `radar_data.orbit_binding_status`, `radar_data.has_orbit_data`, and `radar_data.orbit_file_path` are compatibility fields for production and search.

`orbit_asset_derivatives` records production-pool derivatives. For current LT-1 TXT production, derivative records use:

```text
engine_code=lt1_txt_pool
derivative_format=LT1_TXT
derivative_role=production_orbit_txt
pool_path=D:\orbit_pools\envi\LT1A|LT1B\*.txt
```

## Current Defaults

```env
ISCE2_ENABLED=false
ORBIT_POOL_ISCE2=
ORBIT_POOL_LANDSAR=
```

The old `/monitor/run-now?target=orbit` path is legacy. It may still synchronize the LT-1 production pool for compatibility, but new UI should use asset inventory orbit scans.
