# Phase 0 Repo Reader Findings

Updated: 2026-04-03

## Existing repo reader path

The current repository already has a stable LT-1 single-scene metadata ingestion path.

Main code path:

- `backend/app/services/data_service.py`
  - `scan_radar_data()`
- `backend/app/utils.py`
  - `parse_lt1_radar_filename()`
  - `find_xml_file()`
  - `parse_xml_metadata()`

## What the existing reader does

### 1. Folder-name parsing

`parse_lt1_radar_filename()` extracts from the directory name:

- `satellite`
- `satellite_mode`
- `receiving_station`
- `imaging_mode`
- `orbit_circle`
- `scene_center_lon`
- `scene_center_lat`
- `imaging_date`
- `acquisition_time_utc`
- `product_type`
- `polarization`
- `product_level`
- `product_unique_id`

Example supported name:

```text
LT1B_MONO_SYC_STRIP1_018153_E135.4_N48.3_20250701_SLC_HH_S2A_0000790171
```

### 2. XML discovery

`find_xml_file()` prefers:

- `*.meta.xml`

and falls back to:

- the only XML file in the directory, if there is just one

### 3. XML parsing

`parse_xml_metadata()` extracts:

- `orbit_direction`
- `imaging_mode`
- `polarization`
- `receiving_station`
- `satellite_mode`
- `orbit_circle` from `absOrbit`
- `scene_center_lon`
- `scene_center_lat`
- `acquisition_time_utc`
- `product_type`
- `product_level`
- `product_unique_id`
- `look_direction`
- corner coordinates and coverage polygon

### 4. Merge rule

`scan_radar_data()` merges:

- folder-name metadata
- XML metadata

with XML preferred for most fields, except `product_unique_id` where the folder-name value is preserved if present.

## Why this matters for SBAS experiments

This means the SBAS experiment should not invent a separate metadata interpretation unless absolutely necessary.

Recommended rule:

- reuse the same field semantics already used by `RadarDataORM`
- reuse the same `.meta.xml` discovery logic
- treat `scan_radar_data()` output as the canonical single-scene asset layer

## Data layout check against `F:\Insar_data_pool_1`

Sample scene directories under `F:\Insar_data_pool_1` are compatible with the current single-scene reader:

- one folder per scene
- directory name matches LT-1 parser expectations
- contains `*.meta.xml`
- contains `*.tiff`
- contains preview and auxiliary files

Example sample directory:

```text
F:\Insar_data_pool_1\LT1A_MONO_KSC_STRIP1_017030_E123.3_N46.1_20250315_SLC_HH_S2A_0000678238
```

Example files inside:

- `...meta.xml`
- `...tiff`
- `...rpc`
- `...browse.jpg`
- `...thumb.jpg`

## Practical implication

For phase 1 design and experiments:

- the current repo already knows how to ingest these LT-1 scene folders as `RadarData`
- stack preparation should build on top of this asset layer
- the real unknown is not scene metadata parsing
- the real unknown is how to transform these scene folders into a stack layout acceptable to `stripmapStack`
