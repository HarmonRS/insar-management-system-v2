# DEM Production Source Contract (2026-06-28)

## Decision

New production should use `D:\DEM\SRTMDEM_RSP_SARscape` as the common DEM source family unless a task explicitly declares another DEM in its manifest. The system should not mix SRTM, COPDEM, GMTED, and the interpolated Heilongjiang 10 m DEM silently.

This does not mean every engine reads the same physical file. The same SRTM source is maintained in several engine-compatible forms:

| Role | Path | Format | Intended consumers |
| --- | --- | --- | --- |
| SARscape/ENVI source | `D:\DEM\SRTMDEM_RSP_SARscape` | ENVI/SARscape float32 binary with `.hdr` | `IDL_DINSAR_DEM_BASE_FILE`, `GF3_SARSCAPE_DEM_PATH` |
| WGS84 prepared source | `D:\DEM\SRTMDEM_RSP_SARscape.wgs84` | ENVI float32 binary/VRT-readable raster | `ISCE2_DEM_PATH`, `PYINT_PREPARED_DEM_PATH`, `SAR_ANALYSIS_DEM_PATH`, `GAMMA_SBAS_DEM_PATH`, `TIMESERIES_DEM_PATH` |
| Int16 GeoTIFF source | `D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif` | GeoTIFF int16 | `LANDSAR_DEM_PATH`, `LANDSAR_SBAS_DEM_PATH`, GDAL/RPC-style GeoTIFF consumers such as `GF3_GEO_DEM_PATH` |

The LT-1 single-scene production profile uses a 30 m analysis grid. Product manifests must still record the selected SRTM-derived source file, the cropped/converted DEM path, the actual Gamma DEM spacing, the derived `dem_lat_ovr`/`dem_lon_ovr`, and the configured target grid so reviewers can distinguish the source DEM family from the output grid.

The LT-1 single-scene profile also performs multilook and speckle filtering before registering the final analysis GeoTIFF. The production manifest must record `SAR_ANALYSIS_RANGE_LOOKS`, `SAR_ANALYSIS_AZIMUTH_LOOKS`, and the speckle filter method/window so reviewers can reproduce the output pixel statistics.

## Current Configuration

The current server should use:

```env
IDL_DINSAR_DEM_BASE_FILE=D:\DEM\SRTMDEM_RSP_SARscape
GF3_SARSCAPE_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape

ISCE2_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84
PYINT_PREPARED_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84
SAR_ANALYSIS_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84
SAR_ANALYSIS_DEM_RESOLUTION_M=30.0
SAR_ANALYSIS_TARGET_GRID_SIZE_M=30.0
SAR_ANALYSIS_RANGE_LOOKS=6
SAR_ANALYSIS_AZIMUTH_LOOKS=5
SAR_ANALYSIS_SPECKLE_FILTER_ENABLED=true
SAR_ANALYSIS_SPECKLE_FILTER_METHOD=lee
SAR_ANALYSIS_SPECKLE_FILTER_SIZE=5
GAMMA_SBAS_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84
TIMESERIES_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84

LANDSAR_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
LANDSAR_SBAS_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
GF3_GEO_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
```

## Non-Default DEMs

- `D:\DEM\HeiLongJiang10M_DEM.tif` is a regional interpolated DEM. It is not the default production DEM.
- `D:\DEM\landsar_prepared\HeiLongJiang10M_DEM_full_4326_int16.tif` is a LandSAR-compatible regional derivative of the interpolated DEM. It should only be used by an explicitly named regional/high-resolution experiment.
- `D:\DEM\COPDEM_GLO30_China_4326_DEM` remains a possible China-coverage fallback, but it is not the default after this contract.
- `D:\DEM\GMTED2010.jp2` is too coarse for production geocoding and should not be used as a production DEM default.

Any task that intentionally uses a non-default DEM must record the selected source path, derived/cropped path, DEM resolution, target output grid, and coverage decision in its manifest.
