# LT-1 Geocoded GeoTIFF Production Update (2026-06-28)

## Decision

The LT-1 production button must produce a usable geocoded GeoTIFF, not only prepare LandSAR `Input_Data` for later D-InSAR work.

The current implemented production path is:

```text
LT-1 source asset
  -> radar_data record
  -> SAR_SCENE_PREPROCESS job
  -> lt_gamma single-scene pipeline
  -> SARSceneGeoORM.analysis_tif_path = analysis_ready.tif
```

This is the platform's real LT-1 single-scene backscatter image product for now. It performs the Gamma single-scene preprocessing chain: LT source product to Gamma SLC, multilook amplitude, geocode, speckle filtering in the linear-power domain, dB conversion, and analysis-ready GeoTIFF registration.

The chain must not silently fall back to unrelated DEM sources. The default production contract is SRTM-derived on the current server:

- `SAR_ANALYSIS_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84`
- `SAR_ANALYSIS_TARGET_GRID_SIZE_M=30.0`
- `SAR_ANALYSIS_DEM_RESOLUTION_M=30.0`
- `SAR_ANALYSIS_RANGE_LOOKS=6`
- `SAR_ANALYSIS_AZIMUTH_LOOKS=5`
- `SAR_ANALYSIS_SPECKLE_FILTER_ENABLED=true`
- `SAR_ANALYSIS_SPECKLE_FILTER_METHOD=lee`
- `SAR_ANALYSIS_SPECKLE_FILTER_SIZE=5`
- `PYINT_GEO_INTERP=1`

The runtime clips the configured SRTM-derived DEM to the scene footprint with a small margin, converts that clip to Gamma DEM format, runs `generate_rdc_dem.py`, geocodes the multilooked amplitude with `geocode_back`, exports a GeoTIFF with `data2geotiff`, applies a Lee speckle filter to the linear power raster, then converts power to dB. The output `pixel_size_m` stored in `sar_scene_geo` is derived from the GeoTIFF transform; WGS84 degree grids are converted to approximate meters before registration.

Multilook is already part of the Gamma preprocessing path through `generate_rdc_dem.py`; it is controlled by `SAR_ANALYSIS_RANGE_LOOKS` and `SAR_ANALYSIS_AZIMUTH_LOOKS`. Speckle filtering is a separate post-geocode raster operation before dB conversion. The filter manifest records the method, window size, domain, and equivalent number of looks used for the Lee weighting.

For SRTM-derived DEMs, the configured 30 m analysis grid must be translated into Gamma `dem_lat_ovr` and `dem_lon_ovr` from the actual converted `.dem.par` spacing. Do not assume that `SAR_ANALYSIS_DEM_RESOLUTION_M=30.0` alone changes the output GeoTIFF grid. A 3 arc-second source DEM with `dem_lat_ovr=1` and `dem_lon_ovr=1` will still produce about 90 m latitude spacing. The runner now reads `post_lat`, `post_lon`, and scene latitude from the converted Gamma DEM parameter file, then writes the derived oversampling into the template before `generate_rdc_dem.py`.

The previous `D:\DEM\HeiLongJiang10M_DEM.tif` default is not the production default. It is an interpolated regional DEM and must not be mixed silently with SRTM-derived products. If a future production profile intentionally uses it, the selected DEM and output grid must be recorded in the product manifest.

## What Changed

- `/api/landsar-lt1-production/run` queues `SAR_SCENE_PREPROCESS` jobs with `engine=lt_gamma`.
- Each selected LT-1 source asset resolves to a `radar_data` scene and produces one independent `SARSceneGeoORM` record.
- Batch mode means batch single-scene GeoTIFF production. It does not create a D-InSAR pair/stack product.
- The product list under `/api/landsar-lt1-production/products` reads from `sar_scene_geo`, not from `result_products.catalog_name=lt1_landsar`.
- Asset inventory and radar search mark LT-1 assets as produced only when `sar_scene_geo.status = DONE` and `analysis_tif_path` exists for the LT-1 `lt_gamma` profile.

## UI And Query Contract

- The LT-1 GeoTIFF production UI does not accept arbitrary LT-1 scene directories. Operators must select scanned source assets so each output can be linked back to `radar_data` and `sar_scene_geo`.
- The UI does not expose a satellite-mode/BIST selector. Acquisition mode should come from scanned source metadata, and the current Gamma single-scene pipeline does not require the operator to choose LandSAR import mode manually.
- The production candidate list reuses `/radar-data/search` with `satellite_family=LT1` and `source_format=LT1_ARCHIVE`, so operators can plan production by acquisition date, administrative AOI, orbit, polarization, and product name.
- `/assets/sources` remains the asset-led inventory view. It is not the main LT-1 production planning surface because it lacks the full image search/AOI workflow.
- Items that already have a completed LT-1 GeoTIFF product are shown as produced and are not selectable for a new production task.

## What Is Not A Finished Image Product

LandSAR `100016` and `100206` are still useful, but they only prepare/import LT-1 data:

```text
100016 -> LandSAR Input_Data
100206 -> precise-orbit injection for imported XML
```

Those outputs must not be displayed as "image production complete" and must not block reprocessing as if they were geocoded images.

## Open LandSAR Work

LandSAR single-scene geocoded image production may still be possible through `180044`, `180016`, `200046`, or `200016`, but the repository does not yet contain a verified parameter chain for those modules. Do not expose those proIDs in the formal UI until a real parameter file and sample run are verified.
