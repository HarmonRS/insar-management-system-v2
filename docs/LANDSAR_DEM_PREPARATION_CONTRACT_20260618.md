# LandSAR DEM Preparation Contract

Updated: 2026-06-18

## Decision

LandSAR D-InSAR and LandSAR SBAS must not run directly against the full global DEM.

The maintained flow is:

1. Convert the global DEM once into an uncompressed Int16 GeoTIFF.
2. Configure LandSAR with that global Int16 GeoTIFF as the DEM source.
3. Before each LandSAR task executes, crop a task-level DEM from the global Int16 source.
4. Write the task-level crop path into `200014.txt` or `280039.txt`.

The global Int16 file is a reusable source DEM. The actual LandSAR console input is the small crop stored under the run work directory.

## Paths

Prepared global DEM source:

```text
D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
```

Runtime task crop location:

```text
<run work root>\...\dem_crop\<task>_<bbox-hash>_dem.tif
```

Production configuration:

```text
LANDSAR_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
LANDSAR_SBAS_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
```

## Commands

One-time global conversion:

```powershell
Set-Location D:\Code\Insar_management_system_v2; & C:\ProgramData\anaconda3\envs\InSAR\python.exe scripts\prepare_landsar_dem_int16.py --source D:\DEM\SRTMDEM_RSP_SARscape.wgs84 --target D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif --block-size 1024 --overwrite
```

Optional manual crop test from the prepared global GeoTIFF:

```powershell
Set-Location D:\Code\Insar_management_system_v2; & C:\ProgramData\anaconda3\envs\InSAR\python.exe scripts\prepare_landsar_dem_int16.py --source D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif --crop-only --bbox 120,42,136,54 --target D:\DEM\landsar_prepared\SRTMDEM_RSP_SARscape_ne_china_int16.tif --block-size 2048 --overwrite
```

The manual crop command is for verification or emergency operation. Normal D-InSAR/SBAS production performs task-level cropping automatically.

## Guardrails

- `LANDSAR_DEM_PATH` and `LANDSAR_SBAS_DEM_PATH` point to the global prepared Int16 GeoTIFF source.
- D-InSAR derives the crop bbox from master/slave LT-1 XML corner coordinates.
- LandSAR SBAS derives the crop bbox from all selected `Input_Data` LT-1 XML corner coordinates.
- The crop bbox is expanded by a margin before writing the task DEM.
- The source DEM must be Int16; Float/ENVI sources are rejected at runtime.
- Each crop writes a JSON manifest next to the crop tif.
- Run metadata records both `dem_source_path` and the actual task `dem_path`.
