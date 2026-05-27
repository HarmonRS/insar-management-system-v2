# SBAS-InSAR GeoJSON Coverage And Result Management Design

Date: 2026-05-27

## 1. Purpose

Gamma SBAS-InSAR production has completed an end-to-end twelve-node run. The next step is to make production and results usable by geography, not only by time sequence and run status.

This design defines:

- how production pages show approximate geographic coverage with GeoJSON/bbox;
- how users select administrative regions or AOI to discover and produce SBAS stacks on demand;
- how completed SBAS runs become result products with searchable geographic extent;
- how the results page should display LOS velocity, LOS sigma, and monitoring-point curves.

## 2. Current Facts

The current successful run already contains usable geographic metadata:

```text
run_id = sbas_7537cc71c998
stack center bucket = E129.2_N44.1
stack bbox = 128.7690438245, 43.7486321624, 129.6293024728, 44.3582486206
monitor point = 129.10207098755, 44.15041727515
```

Available metadata sources:

```text
stack_manifest.scenes[*].bbox
stack_manifest.scenes[*].center_lon / center_lat
stack_manifest.stack.center_bucket
rdc_dem_summary.dem_source.stack_bbox
monitor_points_summary.monitor_outputs[*].metadata.approx_lonlat
published GeoTIFF bounds from GDAL metadata
```

Existing platform capabilities:

```text
backend/geojson/全国行政区.geojson
backend/geojson/层级映射.json
GET /api/aoi/regions/children
GET /api/aoi/regions/{tree_id}/geometry
backend AOI helpers for region_tree_id / GeoJSON / uploaded AOI parsing
frontend Leaflet map and L.geoJSON support
frontend App.jsx existing source-scene footprint and AOI overlay patterns
```

The missing piece is productized SBAS-specific coverage and catalog behavior.

## 3. Design Principles

1. SBAS production is temporal, but SBAS result consumption is geographic.
2. Production UI should answer "am I processing the right place?" before a long workflow is submitted.
3. Results UI should answer "where is this product, what time range does it cover, and can I download the main outputs?"
4. One completed SBAS run is one result product bundle, not one product per GeoTIFF.
5. GeoJSON/bbox coverage is enough for the first production UX. Full raster map rendering can come later.
6. Administrative-region filtering should reuse existing AOI infrastructure rather than introduce a parallel region system.

## 4. Coverage Model

### 4.1 Stack Coverage

For stack discovery and production run display:

```json
{
  "center": {"lon": 129.199, "lat": 44.053},
  "bbox": {
    "min_lon": 128.769,
    "min_lat": 43.749,
    "max_lon": 129.629,
    "max_lat": 44.358
  },
  "bbox_geojson": {
    "type": "Feature",
    "properties": {"role": "sbas_stack_bbox"},
    "geometry": {
      "type": "Polygon",
      "coordinates": [[
        [128.769, 43.749],
        [129.629, 43.749],
        [129.629, 44.358],
        [128.769, 44.358],
        [128.769, 43.749]
      ]]
    }
  },
  "scene_bbox_count": 7,
  "scene_footprints_geojson": null
}
```

First implementation may use stack bbox only. Per-scene rectangles can be added when the map needs to show coverage stability.

### 4.2 Product Coverage

For completed results:

```json
{
  "coverage_source": "stack_manifest_bbox_union",
  "geotiff_bounds_verified": true,
  "bbox": {...},
  "center": {...},
  "footprint_geojson": {...},
  "administrative_hint": {
    "province": "黑龙江省",
    "city": null,
    "county": null,
    "method": "center_point_lookup"
  }
}
```

The first administrative hint can be center-point based. Later it should become intersection-based and return all intersected regions with approximate overlap area.

## 5. Administrative Region And AOI Production

### 5.1 User Workflow

Production page should support three AOI sources:

```text
1. Administrative region selection
   province -> city -> county, backed by /aoi/regions endpoints

2. Map-drawn rectangle
   converted to bbox GeoJSON

3. Uploaded GeoJSON/SHP
   reuse existing AOI parsing helpers
```

The user flow:

```text
select AOI / administrative region
discover SBAS stack candidates
show candidate time density + geographic coverage
select candidate
create production Run
submit Gamma SBAS workflow
```

### 5.2 Discovery Filter

Stack discovery should accept:

```json
{
  "region_tree_id": "230000",
  "aoi_geojson": {},
  "aoi_bbox": {
    "min_lon": 128.7,
    "min_lat": 43.7,
    "max_lon": 129.7,
    "max_lat": 44.4
  },
  "aoi_overlap_min": 0.0,
  "stable_stack_overlap_min": 0.3
}
```

Initial filtering can use bbox intersection:

```text
candidate stack is valid when union_bbox intersects AOI bbox
```

Second-stage filtering should use polygon intersection:

```text
candidate_score += common_stack_area_intersection_ratio
candidate_score += scene_count / temporal_density
candidate_score -= sparse_time_gap_penalty
```

### 5.3 Production Guardrails

Before running workflow:

```text
show bbox and administrative hint
show date list and max temporal gap
show DEM coverage status
warn when DEM covers center but not full stack bbox
warn when AOI overlap is low
```

The current run demonstrated a real issue:

```text
DEM source covers stack center = true
DEM source covers full stack bbox = false
```

This must be visible in the production page before the user trusts the result.

## 6. Production Page UI

Add `Geographic Coverage` block to `SbasInsarProductionPanel`.

Minimum fields:

```text
Center: lon / lat
BBox: min_lon, min_lat, max_lon, max_lat
Scene footprints: count
DEM coverage: bbox / center
Administrative hint
Monitor points if generated
Actions: view on map, copy GeoJSON, zoom to footprint
```

Map preview options:

```text
Phase 1: small unframed Leaflet map with rectangle overlay
Phase 2: shared main map overlay using existing App.jsx layer mechanisms
Phase 3: per-scene footprints and monitor points overlay
```

The production page should not become the result browser. It only provides enough geographic context to avoid wrong-location production.

## 7. Result Management Module

### 7.1 Module Boundary

Create a separate SBAS-InSAR result management module:

```text
navigation: Results / SBAS-InSAR Results
backend catalog: catalog_name = sbas_insar
product type: sbas_insar_bundle
source: completed Gamma SBAS production Run
```

Do not merge SBAS result semantics into D-InSAR result pages. Reuse the common catalog tables but keep service, API, and frontend module separate.

### 7.2 Product Row

One completed run becomes one product row:

```text
result_products.catalog_name = sbas_insar
result_products.run_key = sbas run_id
result_products.engine_code = gamma
result_products.processor_code = gamma_ipta_sbas
result_products.display_name = platform / orbit / area / date span
```

Required product metadata:

```text
platform
relative_orbit
orbit_direction
polarization
reference_date
start_date
end_date
scene_count
pair_count
bbox_min_lon / bbox_min_lat / bbox_max_lon / bbox_max_lat
center_lon / center_lat
administrative_hint
status
health_status
source_run_id
```

### 7.3 Asset Roles

Primary assets from the expert document:

```text
primary_velocity_geotiff
  current: publish/geotiff/los_rate_toward_m_per_year.tif
  expert equivalent: geo_los_def_rate.tif

primary_velocity_preview
  current: publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png
  expert equivalent: los_def_rate.bmp / geo_los_def_rate.bmp

primary_velocity_rgb_geotiff
  current: publish/geotiff/los_rate_toward_m_per_year.hls.geo_rgb.tif
  expert equivalent: geo_los_def_rate_rgb.tif

quality_sigma_geotiff
  current: publish/geotiff/los_sigma_m_per_year.tif
  expert equivalent: diff.sigma_ts / geo_diff.sigma_ts

quality_sigma_preview
  current: publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png
  expert equivalent: diff.sigma_ts.masked.bmp with cc.cm

monitor_timeseries_csv
  current: publish/monitor_points/*_timeseries.csv
  expert equivalent: disp_prt_2d output table

monitor_timeseries_plot
  current: publish/monitor_points/*_timeseries.png

support_manifest
  run_manifest.json, workflow_summary.json, gamma_command_manifest.json
```

Default result display should foreground:

```text
LOS velocity preview
LOS sigma preview
monitor point curve if present
footprint map
```

Audit/support files should be grouped separately.

## 8. Backend Design

### 8.1 Service

Add:

```text
backend/app/services/sbas_insar_catalog_service.py
```

Responsibilities:

```text
scan completed SBAS run directories
validate product bundle readiness
derive bbox/center/footprint GeoJSON
extract or verify GeoTIFF bounds
upsert result_products/result_assets/result_issues
provide list/detail/download APIs
bootstrap self-maintenance on startup
```

### 8.2 API

Add:

```text
GET  /api/sbas-insar-products/catalog-status
POST /api/sbas-insar-products/rebuild-catalog
GET  /api/sbas-insar-products
GET  /api/sbas-insar-products/{product_id}
GET  /api/sbas-insar-products/{product_id}/assets/{asset_id}
```

List filters:

```text
date_from
date_to
reference_date
platform
relative_orbit
orbit_direction
status
health_status
region_tree_id
aoi_bbox
aoi_geojson
intersects_bbox
has_monitor_points
limit / offset
```

### 8.3 Run Detail Extension

Extend production run detail:

```json
{
  "geographic_coverage": {
    "center": {"lon": 129.199, "lat": 44.053},
    "bbox": {"min_lon": 128.769, "min_lat": 43.749, "max_lon": 129.629, "max_lat": 44.358},
    "bbox_geojson": {},
    "scene_bbox_count": 7,
    "dem_covers_stack_bbox": false,
    "dem_covers_stack_center": true,
    "monitor_points": [{"point_id": "auto_low_sigma_high_rate", "lon": 129.102, "lat": 44.150}]
  }
}
```

### 8.4 Startup Self-Maintenance

On startup:

```text
ensure result catalog tables exist through existing maintenance
bootstrap sbas_insar catalog state
scan completed SBAS run publish bundles
upsert missing product rows
record issues for:
  missing bbox
  missing primary velocity GeoTIFF
  missing primary preview
  missing sigma GeoTIFF
  missing sigma preview
  missing monitor files when monitor summary says ready
  DEM coverage mismatch
```

This matches the current self-maintenance direction used by D-InSAR and PsInSAR catalogs.

## 9. Frontend Design

### 9.1 Production Page

Add:

```text
GeographicCoveragePanel
  bbox text
  center text
  administrative hint
  DEM coverage status
  mini map rectangle
  copy GeoJSON
  zoom/open on main map
```

Candidate stack cards should show:

```text
date span
scene count
center bucket
center lon/lat
bbox short text
AOI overlap indicator when AOI is selected
```

### 9.2 Result Page

Add:

```text
SbasInsarProductsPanel
```

List page:

```text
filters: date range, administrative region, map AOI, platform, orbit, status
cards/table: product name, date span, bbox/admin hint, scene/pair count, preview thumbnail, health badge
actions: open detail, zoom to map, download primary GeoTIFF
```

Detail page:

```text
footprint map
LOS velocity preview
LOS sigma preview
monitor curve
key metadata table
asset table grouped by role
quality and issue summary
link to production Run
```

### 9.3 Map Layer Strategy

Use GeoJSON for the first implementation:

```text
bbox polygon for stack/product footprint
administrative region boundary layer from existing AOI endpoints
monitor point markers
optional per-scene footprints
```

Do not display RDC BMP as map layer. Only geocoded preview PNG/GeoTIFF-derived bounds should be used for map-oriented display.

## 10. Implementation Sequence

Recommended order:

```text
1. Backend geographic_coverage in SBAS run detail.
2. Production page coverage block and bbox GeoJSON mini-map.
3. Add AOI/region filters to SBAS stack discovery request/response.
4. Add sbas_insar_catalog_service and catalog rebuild API.
5. Register current completed run as first SBAS result product.
6. Build SbasInsarProductsPanel list/detail.
7. Add map AOI filtering and administrative-region filtering to result page.
8. Add startup bootstrap/self-check output.
```

The first user-visible win is step 1-2: the operator can immediately see whether a Run covers the intended location.

## 11. Validation

Use `sbas_7537cc71c998` as the first validation run.

Checks:

```text
geographic_coverage.bbox is present
bbox_geojson draws a rectangle in Leaflet
admin hint is present or explicitly unknown
DEM coverage warning is visible when bbox is not fully covered
result product row exists after catalog rebuild
result detail opens velocity/sigma previews
asset downloads work
map zoom to footprint works
AOI filter returns this product when AOI intersects bbox
AOI filter excludes this product when AOI is far away
```

## 12. Open Questions

1. Administrative-region naming should start with center-point lookup or intersection lookup?
   Recommendation: center-point lookup first, intersection later.

2. Should stack discovery require AOI overlap, or only rank by AOI overlap?
   Recommendation: default to rank/filter by bbox intersection, expose minimum overlap later.

3. Should results use `sbas_insar` or `psinsar` catalog name?
   Recommendation: use `sbas_insar`. The current Gamma product is SBAS-InSAR, and old `psinsar` catalog semantics should not be overloaded.

4. Should GeoTIFF raster be rendered on map immediately?
   Recommendation: not in the first slice. Start with footprint GeoJSON and preview images; raster tile rendering can be added after catalog registration is stable.
