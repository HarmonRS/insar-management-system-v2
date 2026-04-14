# ISCE2 SBAS Product Specification

Updated: 2026-04-06

## 1. Goal

Define the stable product contract for the current phase-1 SBAS route:

- `ISCE2 stripmapStack -> MintPy SBAS -> geocode/export -> psinsar publish bundle`

This document answers three practical questions:

- what the final SBAS deliverables are
- which files are runtime-only vs publish-grade
- which file the future system should treat as the publish entrypoint

## 2. Product Layers

The current experiment has three artifact layers.

### 2.1 Processing-layer runtime artifacts

Generated under a MintPy work directory such as:

- `stack_work/mintpy_sbas_v5/`

These are processing outputs, not yet the final publish contract.

Key files:

- `timeseries.h5`
  - radar-coordinate time-series cube
  - core scientific output
- `velocity.h5`
  - radar-coordinate velocity solution
  - useful for quick inspection and later geocode
- `temporalCoherence.h5`
  - quality indicator after inversion
- `maskTempCoh.h5`
  - thresholded reliability mask
- `maskAllValid.h5`
  - strict runtime mask used before inversion
- `numTriNonzeroIntAmbiguity.h5`
  - unwrap/network diagnostic
- `numTriNonzeroIntAmbiguity.png`
  - browseable diagnostic preview

Current runtime boundary:

- the minimum validated SBAS runtime boundary is `timeseries.h5` plus `velocity.h5`

### 2.2 Publish-layer standardized artifacts

Generated under a publish directory such as:

- `publish/mintpy_sbas_v5/`

This is the current publish-grade boundary for system embedding.

Directory contract:

- `manifest.json`
- `assets/`
- `preview/`
- `metadata/`

### 2.3 Catalog-layer registration artifacts

The future backend should publish the standardized bundle into the `psinsar` catalog.

Current rule:

- the backend should not register raw MintPy runtime paths directly
- the backend should register the publish bundle rooted by `manifest.json`
- the backend should populate `manifest.json.group_key` from task/run metadata during publish registration

## 3. Canonical Publish Bundle

### 3.1 Entrypoint

The publish entrypoint is:

- `manifest.json`

Reason:

- it binds product identity, stack dates, reference info, artifact paths, and quality summary in one place
- it allows the backend to register one bundle without hard-coding per-file conventions
- it decouples future UI/backend integration from the temporary MintPy work directory layout

### 3.2 Primary data products

#### `assets/geo_timeseries.h5`

Role:

- primary time-series product

Why it matters:

- this is the file that proves the system has true time-series capability
- every valid pixel contains multi-date displacement values rather than one summary statistic

Recommended system use:

- archive as the canonical analysis cube
- expose as the main download for advanced analysis
- use as the source for future point-query / profile / date-slice APIs

#### `assets/geo_velocity.h5`

Role:

- geocoded structured velocity product

Recommended system use:

- archive together with the time-series cube
- keep for programmatic reading when HDF5-native access is preferred

#### `assets/velocity.tif`

Role:

- publish-grade browse/export raster

Why it matters:

- easiest file for GIS browsing, thumbnail generation, map services, and first-screen display

Recommended system use:

- default map layer for result browsing
- not the only final product
- should be treated as a summary view derived from the time-series solution

### 3.3 Quality products

#### `assets/geo_temporalCoherence.h5`

Role:

- structured quality field

#### `assets/temporalCoherence.tif`

Role:

- GIS-friendly quality raster

Recommended use:

- review where the inversion is reliable

#### `assets/geo_maskTempCoh.h5`

Role:

- structured reliable-pixel mask

#### `assets/maskTempCoh.tif`

Role:

- GIS-friendly publish mask

Recommended use:

- mask browsing products
- support frontend display filtering

### 3.4 Preview and diagnostics

#### `preview/velocity_preview.png`

Role:

- lightweight preview image for cards, list pages, or quick QA

#### `preview/numTriNonzeroIntAmbiguity.png`

Role:

- diagnostic browse image

#### `metadata/source_quality_summary.json`

Role:

- compact machine-readable quality summary

#### `metadata/smallbaselineApp.cfg`

Role:

- reproducibility record of the MintPy configuration

## 4. File Priorities By Use Case

If the goal is system publish or catalog registration:

- focus on `manifest.json`

If the goal is proving time-series capability:

- focus on `assets/geo_timeseries.h5`

If the goal is first-screen map display:

- focus on `assets/velocity.tif`

If the goal is quality control:

- focus on `assets/temporalCoherence.tif`
- focus on `assets/maskTempCoh.tif`

If the goal is full reproducibility:

- keep `manifest.json`
- keep `metadata/smallbaselineApp.cfg`
- keep runtime `maskAllValid.h5` as a workflow artifact even if it is not published as a primary product

## 5. Current Product Typing

Current artifact typing implemented by the experiment bundle builder:

- `timeseries_cube`
  - `assets/geo_timeseries.h5`
- `velocity_map`
  - `assets/geo_velocity.h5`
- `velocity_geotiff`
  - `assets/velocity.tif`
- `temporal_coherence`
  - `assets/geo_temporalCoherence.h5`
- `temporal_coherence_geotiff`
  - `assets/temporalCoherence.tif`
- `quality_mask`
  - `assets/geo_maskTempCoh.h5`
- `quality_mask_geotiff`
  - `assets/maskTempCoh.tif`
- `preview_png`
  - `preview/velocity_preview.png`
- `diagnostic_png`
  - `preview/numTriNonzeroIntAmbiguity.png`

## 6. Publish Rules

Current recommended rules:

- publish the geocoded bundle, not the radar-coordinate MintPy runtime directory
- treat `manifest.json` as the registration root
- treat `geo_timeseries.h5` as the canonical time-series data product
- treat `velocity.tif` as the default visualization product
- always publish at least one quality layer beside the velocity layer
- keep diagnostics and config snapshots for reproducibility even if the frontend does not display all of them

## 7. Current Open Items

Still to be formalized before production:

- final CRS contract beyond the current `EPSG:4326` experiment assumption
- whether the backend stores one product record per artifact or one bundle-root record plus child artifacts
- how the frontend will query time-series pixels from `geo_timeseries.h5`
- whether extra export formats such as COG, CSV point extraction, or JSON summaries should be added

## 8. Current Judgment

For the current phase-1 SBAS route, the final deliverable should be understood as:

- one manifest-driven publish bundle
- centered on `assets/geo_timeseries.h5`
- summarized by `assets/velocity.tif`
- constrained by `assets/temporalCoherence.tif` and `assets/maskTempCoh.tif`

That is the product contract the future system should embed first.
