"""
GF3 (GaoFen-3) L1A -> L2 processing service.

Pipeline: Extract archive -> Parse XML metadata -> Radiometric calibration -> RPC geometric correction.
Pure Python implementation using GDAL/numpy, no ENVI/SARscape dependency.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tarfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------

def _extract_archive(path: str, dest: str) -> str:
    """Extract .tar.gz or .zip archive to *dest*, return extracted directory path."""
    os.makedirs(dest, exist_ok=True)
    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as tf:
            # Security: prevent path traversal
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    raise ValueError(f"Unsafe path in archive: {member.name}")
            tf.extractall(dest)
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise ValueError(f"Unsafe path in archive: {info.filename}")
            zf.extractall(dest)
    else:
        raise ValueError(f"Unsupported archive format: {path}")
    return dest


# ---------------------------------------------------------------------------
# GF3 XML metadata parser
# ---------------------------------------------------------------------------

def _parse_gf3_meta(xml_path: str) -> Dict[str, Any]:
    """Parse GF3 product XML metadata.

    Returns dict with keys per polarization:
      polarizations: list of str (e.g. ["HH", "VV"])
      calibration: {pol: {"QualifyValue": float, "CalibrationConst": float}}
    """
    import defusedxml.ElementTree as ET

    tree = ET.parse(xml_path)
    root = tree.getroot()

    result: Dict[str, Any] = {"polarizations": [], "calibration": {}}

    # Find all imageinfo or channel elements
    # GF3 XML structure varies; search by tag name for robustness
    def _find_all_recursive(element, tag):
        found = []
        for child in element.iter():
            if tag.lower() in child.tag.lower():
                found.append(child)
        return found

    # Try to find QualifyValue and CalibrationConst
    # Typical GF3 XML has <imageinfo> -> <QualifyValue> and <CalibrationConst>
    # per polarization channel
    qualify_values = {}
    cal_consts = {}

    # Search for elements containing polarization-specific calibration info
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag  # strip namespace

        if tag == "QualifyValue" and elem.text:
            # Parent should indicate which polarization this belongs to
            parent = _find_parent(root, elem)
            pol = _extract_polarization(parent, elem)
            if pol:
                try:
                    qualify_values[pol] = float(elem.text.strip())
                except ValueError:
                    pass

        if tag == "CalibrationConst" and elem.text:
            parent = _find_parent(root, elem)
            pol = _extract_polarization(parent, elem)
            if pol:
                try:
                    cal_consts[pol] = float(elem.text.strip())
                except ValueError:
                    pass

    # If per-polarization search didn't work, try flat extraction
    if not qualify_values:
        # Fallback: find all QualifyValue elements in order
        qv_elems = [e for e in root.iter() if e.tag.split("}")[-1] == "QualifyValue" and e.text]
        cc_elems = [e for e in root.iter() if e.tag.split("}")[-1] == "CalibrationConst" and e.text]
        # Find polarization list
        pol_elems = [e for e in root.iter() if e.tag.split("}")[-1] == "Polarisation" and e.text]
        if not pol_elems:
            pol_elems = [e for e in root.iter() if e.tag.split("}")[-1] == "polarization" and e.text]

        pols = [e.text.strip().upper() for e in pol_elems]

        for i, pol in enumerate(pols):
            if i < len(qv_elems):
                try:
                    qualify_values[pol] = float(qv_elems[i].text.strip())
                except ValueError:
                    pass
            if i < len(cc_elems):
                try:
                    cal_consts[pol] = float(cc_elems[i].text.strip())
                except ValueError:
                    pass

    polarizations = sorted(set(list(qualify_values.keys()) + list(cal_consts.keys())))
    if not polarizations:
        # Last resort: guess from TIFF filenames in same directory
        xml_dir = os.path.dirname(xml_path)
        for f in os.listdir(xml_dir):
            fl = f.upper()
            for pol in ("HH", "HV", "VH", "VV"):
                if pol in fl and f.lower().endswith((".tif", ".tiff")):
                    if pol not in polarizations:
                        polarizations.append(pol)
        polarizations.sort()

    calibration = {}
    for pol in polarizations:
        calibration[pol] = {
            "QualifyValue": qualify_values.get(pol, 1.0),
            "CalibrationConst": cal_consts.get(pol, 0.0),
        }

    result["polarizations"] = polarizations
    result["calibration"] = calibration

    logger.info("[GF3] Parsed metadata: polarizations=%s, calibration=%s", polarizations, calibration)
    return result


def _find_parent(root, target):
    """Find the parent element of *target* in the tree."""
    for parent in root.iter():
        for child in parent:
            if child is target:
                return parent
    return None


def _extract_polarization(parent, elem) -> Optional[str]:
    """Try to extract polarization from context around an XML element."""
    if parent is None:
        return None
    # Check parent tag or sibling elements
    tag = parent.tag.split("}")[-1] if "}" in parent.tag else parent.tag
    for pol in ("HH", "HV", "VH", "VV"):
        if pol in tag.upper():
            return pol
    # Check sibling text
    for child in parent:
        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if "polaris" in child_tag.lower() and child.text:
            return child.text.strip().upper()
    return None


# ---------------------------------------------------------------------------
# RPC file parser
# ---------------------------------------------------------------------------

def _read_rpb(rpb_path: str) -> Dict[str, Any]:
    """Parse a .rpb (Rational Polynomial Coefficients) file into GDAL metadata dict."""
    rpc = {}
    current_key = None
    values = []

    with open(rpb_path, "r") as f:
        for line in f:
            line = line.strip().rstrip(";")
            if "=" in line:
                if current_key and values:
                    rpc[current_key] = values
                    values = []
                key, _, val = line.partition("=")
                current_key = key.strip()
                val = val.strip().strip("(").strip(")")
                if val:
                    for v in val.replace(",", " ").split():
                        try:
                            values.append(float(v))
                        except ValueError:
                            values.append(v)
            else:
                # continuation of values
                val = line.strip("()").strip()
                if val:
                    for v in val.replace(",", " ").split():
                        try:
                            values.append(float(v))
                        except ValueError:
                            values.append(v)

    if current_key and values:
        rpc[current_key] = values

    # Map to GDAL RPC metadata keys
    gdal_rpc = {}
    key_map = {
        "lineOffset": "LINE_OFF",
        "sampOffset": "SAMP_OFF",
        "latOffset": "LAT_OFF",
        "longOffset": "LONG_OFF",
        "heightOffset": "HEIGHT_OFF",
        "lineScale": "LINE_SCALE",
        "sampScale": "SAMP_SCALE",
        "latScale": "LAT_SCALE",
        "longScale": "LONG_SCALE",
        "heightScale": "HEIGHT_SCALE",
        "lineNumCoef": "LINE_NUM_COEFF",
        "lineDenCoef": "LINE_DEN_COEFF",
        "sampNumCoef": "SAMP_NUM_COEFF",
        "sampDenCoef": "SAMP_DEN_COEFF",
    }

    for rpb_key, gdal_key in key_map.items():
        if rpb_key in rpc:
            val = rpc[rpb_key]
            if isinstance(val, list):
                if len(val) == 1:
                    gdal_rpc[gdal_key] = str(val[0])
                else:
                    gdal_rpc[gdal_key] = " ".join(str(v) for v in val)
            else:
                gdal_rpc[gdal_key] = str(val)

    return gdal_rpc


# ---------------------------------------------------------------------------
# Radiometric calibration
# ---------------------------------------------------------------------------

def _radiometric_calibration(tiff_path: str, qv: float, cal: float, output_path: str) -> str:
    """L1A -> L1B single-polarization radiometric calibration.

    Formula: A = sqrt(I^2 + Q^2), dB = 20*log10(A * QV / 65535) - Cal
    For amplitude-only TIFF: dB = 20*log10(A * QV / 65535) - Cal
    """
    from osgeo import gdal

    ds = gdal.Open(tiff_path, gdal.GA_ReadOnly)
    if ds is None:
        raise FileNotFoundError(f"Cannot open TIFF: {tiff_path}")

    n_bands = ds.RasterCount
    width = ds.RasterXSize
    height = ds.RasterYSize

    if n_bands >= 2:
        # Complex I/Q data
        band_i = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
        band_q = ds.GetRasterBand(2).ReadAsArray().astype(np.float64)
        amplitude = np.sqrt(band_i ** 2 + band_q ** 2)
    else:
        # Amplitude only
        amplitude = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)

    # Avoid log of zero
    amplitude = np.where(amplitude > 0, amplitude, np.nan)
    db_values = 20.0 * np.log10(amplitude * qv / 65535.0) - cal
    db_values = np.where(np.isfinite(db_values), db_values, 0).astype(np.float32)

    # Write calibrated result preserving georeference
    driver = gdal.GetDriverByName("GTiff")
    out_ds = driver.Create(output_path, width, height, 1, gdal.GDT_Float32,
                           options=["COMPRESS=DEFLATE"])
    out_ds.SetGeoTransform(ds.GetGeoTransform())
    out_ds.SetProjection(ds.GetProjection())

    # Copy RPC metadata if present
    rpc_md = ds.GetMetadata("RPC")
    if rpc_md:
        out_ds.SetMetadata(rpc_md, "RPC")

    out_ds.GetRasterBand(1).WriteArray(db_values)
    out_ds.GetRasterBand(1).SetNoDataValue(0)
    out_ds.FlushCache()
    out_ds = None
    ds = None

    logger.info("[GF3] Calibration done: %s -> %s (QV=%.2f, Cal=%.2f)", tiff_path, output_path, qv, cal)
    return output_path


# ---------------------------------------------------------------------------
# Geometric correction
# ---------------------------------------------------------------------------

def _geometric_correction(
    l1b_path: str,
    rpb_path: Optional[str],
    output_path: str,
    resolution: float,
    dem_path: str,
) -> str:
    """L1B -> L2 RPC geometric correction using GDAL Warp."""
    from osgeo import gdal

    ds = gdal.Open(l1b_path, gdal.GA_ReadOnly)
    if ds is None:
        raise FileNotFoundError(f"Cannot open L1B: {l1b_path}")

    # If RPC not already in dataset, load from .rpb
    rpc_md = ds.GetMetadata("RPC")
    if not rpc_md and rpb_path and os.path.isfile(rpb_path):
        rpc_md = _read_rpb(rpb_path)
        ds.SetMetadata(rpc_md, "RPC")
        logger.info("[GF3] Loaded RPC from %s", rpb_path)

    warp_options = gdal.WarpOptions(
        dstSRS="EPSG:4326",
        format="GTiff",
        xRes=resolution,
        yRes=resolution,
        rpc=True,
        creationOptions=["COMPRESS=DEFLATE"],
    )

    # Use DEM if available
    if dem_path and os.path.isfile(dem_path):
        warp_options = gdal.WarpOptions(
            dstSRS="EPSG:4326",
            format="GTiff",
            xRes=resolution,
            yRes=resolution,
            rpc=True,
            transformerOptions=[f"RPC_DEM={dem_path}"],
            creationOptions=["COMPRESS=DEFLATE"],
        )

    result = gdal.Warp(output_path, ds, options=warp_options)
    ds = None

    if result is None:
        raise RuntimeError(f"GDAL Warp failed for {l1b_path}")
    result = None

    logger.info("[GF3] Geometric correction done: %s -> %s", l1b_path, output_path)
    return output_path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_gf3_l1a_to_l2(
    input_dir: str,
    output_dir: str,
    resolution: float = 0.0002,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run GF3 L1A -> L2 pipeline: extract -> calibrate -> geometric correction.

    Args:
        input_dir: Path to GF3 L1A product directory (or archive file)
        output_dir: Output directory for L2 products
        resolution: Output resolution in degrees (default 0.0002 ~ 20m)
        job_id: Optional job ID for progress tracking

    Returns:
        dict with keys: ok, l2_paths, polarizations, output_dir
    """
    from ..config import settings

    dem_path = settings.GF3_GEO_DEM_PATH
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Extract if archive
    work_dir = input_dir
    if os.path.isfile(input_dir):
        logger.info("[GF3] Extracting archive: %s", input_dir)
        extract_dir = os.path.join(output_dir, "_extracted")
        _extract_archive(input_dir, extract_dir)
        # Find actual data directory (may be nested)
        subdirs = [d for d in os.listdir(extract_dir)
                    if os.path.isdir(os.path.join(extract_dir, d))]
        work_dir = os.path.join(extract_dir, subdirs[0]) if subdirs else extract_dir

    # Step 2: Scan for XML + TIFF + RPB files
    xml_path = None
    tiff_files: Dict[str, str] = {}  # pol -> tiff path
    rpb_files: Dict[str, str] = {}   # pol -> rpb path

    for f in os.listdir(work_dir):
        fl = f.lower()
        fp = os.path.join(work_dir, f)
        if fl.endswith(".meta.xml") or (fl.endswith(".xml") and "meta" in fl):
            xml_path = fp
        elif fl.endswith(".xml") and xml_path is None:
            xml_path = fp
        elif fl.endswith((".tif", ".tiff")):
            for pol in ("HH", "HV", "VH", "VV"):
                if pol in f.upper():
                    tiff_files[pol] = fp
                    break
        elif fl.endswith(".rpb"):
            for pol in ("HH", "HV", "VH", "VV"):
                if pol in f.upper():
                    rpb_files[pol] = fp
                    break

    if not xml_path:
        # Try subdirectories
        for sub in os.listdir(work_dir):
            sub_path = os.path.join(work_dir, sub)
            if os.path.isdir(sub_path):
                for f in os.listdir(sub_path):
                    fl = f.lower()
                    fp = os.path.join(sub_path, f)
                    if fl.endswith(".xml") and not xml_path:
                        xml_path = fp
                    elif fl.endswith((".tif", ".tiff")):
                        for pol in ("HH", "HV", "VH", "VV"):
                            if pol in f.upper():
                                tiff_files[pol] = fp
                                break
                    elif fl.endswith(".rpb"):
                        for pol in ("HH", "HV", "VH", "VV"):
                            if pol in f.upper():
                                rpb_files[pol] = fp
                                break

    if not tiff_files:
        return {"ok": False, "error": f"No TIFF files found in {work_dir}"}

    # Step 3: Parse XML metadata
    meta = {"polarizations": list(tiff_files.keys()), "calibration": {}}
    if xml_path:
        try:
            meta = _parse_gf3_meta(xml_path)
        except Exception as e:
            logger.warning("[GF3] Failed to parse XML %s: %s, using defaults", xml_path, e)

    polarizations = meta.get("polarizations", list(tiff_files.keys()))
    calibration = meta.get("calibration", {})

    # Step 4-5: Process each polarization
    l2_paths = []
    for pol in polarizations:
        if pol not in tiff_files:
            logger.warning("[GF3] No TIFF found for polarization %s, skipping", pol)
            continue

        tiff_path = tiff_files[pol]
        cal_info = calibration.get(pol, {"QualifyValue": 1.0, "CalibrationConst": 0.0})
        qv = cal_info["QualifyValue"]
        cal = cal_info["CalibrationConst"]

        # L1A -> L1B (calibration)
        l1b_path = os.path.join(output_dir, f"{pol}_L1B.tif")
        logger.info("[GF3] Calibrating %s (QV=%.2f, Cal=%.2f)", pol, qv, cal)
        _radiometric_calibration(tiff_path, qv, cal, l1b_path)

        # L1B -> L2 (geometric correction)
        l2_path = os.path.join(output_dir, f"{pol}_L2.tif")
        rpb_path = rpb_files.get(pol)
        logger.info("[GF3] Geometric correction %s (resolution=%.6f)", pol, resolution)
        _geometric_correction(l1b_path, rpb_path, l2_path, resolution, dem_path)

        l2_paths.append(l2_path)

        # Clean up intermediate L1B
        try:
            os.remove(l1b_path)
        except OSError:
            pass

    if not l2_paths:
        return {"ok": False, "error": "No polarization channels processed successfully"}

    logger.info("[GF3] Pipeline complete: %d L2 products", len(l2_paths))
    return {
        "ok": True,
        "l2_paths": l2_paths,
        "polarizations": polarizations,
        "output_dir": output_dir,
        "input_dir_name": os.path.basename(input_dir),
    }


# ---------------------------------------------------------------------------
# Auto-register L2 result into radar_data table
# ---------------------------------------------------------------------------

async def register_l2_to_radar_data(l2_dir: str, input_dir_name: str, polarizations: List[str], db) -> Optional[int]:
    """Register a GF3 L2 output directory as a radar_data record.

    Args:
        l2_dir: Path to the L2 output directory
        input_dir_name: Original L1A input directory name (for metadata extraction)
        polarizations: List of polarization channels processed
        db: AsyncSession

    Returns:
        radar_data.id if successfully registered, else None
    """
    from ..utils import parse_gf3_l2_dirname
    from ..models import RadarDataORM
    from .data_service import extract_geotiff_bounds
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Polygon as ShapelyPolygon
    from sqlalchemy.future import select

    # Check if already registered (by file_path)
    result = await db.execute(
        select(RadarDataORM).where(RadarDataORM.file_path == l2_dir).limit(1)
    )
    if result.scalar_one_or_none():
        logger.info("[GF3] L2 dir already registered: %s", l2_dir)
        return None

    # Parse metadata from the input directory name
    meta = parse_gf3_l2_dirname(input_dir_name)
    if not meta:
        # Fallback: minimal metadata
        meta = {
            "satellite": "GF3",
            "imaging_date": None,
            "polarization": ",".join(polarizations) if polarizations else None,
        }

    # Try to extract polygon from the first L2 GeoTIFF
    polygon = None
    try:
        for f in os.listdir(l2_dir):
            if f.lower().endswith((".tif", ".tiff")) and "L2" in f:
                tiff_path = os.path.join(l2_dir, f)
                polygon = extract_geotiff_bounds(tiff_path)
                if polygon:
                    break
    except OSError:
        pass

    coverage_geom = None
    if polygon and len(polygon) >= 4:
        try:
            shp = ShapelyPolygon(polygon)
            if shp.is_valid:
                coverage_geom = from_shape(shp, srid=4326)
        except Exception:
            pass

    radar = RadarDataORM(
        file_path=l2_dir,
        satellite=meta.get("satellite", "GF3"),
        imaging_date=meta.get("imaging_date"),
        imaging_mode=meta.get("imaging_mode"),
        polarization=meta.get("polarization") or (",".join(polarizations) if polarizations else None),
        scene_center_lon=meta.get("scene_center_lon"),
        scene_center_lat=meta.get("scene_center_lat"),
        coverage_polygon=coverage_geom,
    )
    db.add(radar)
    await db.flush()
    radar_id = radar.id
    await db.commit()

    logger.info("[GF3] Registered L2 in radar_data: id=%s, path=%s", radar_id, l2_dir)
    return radar_id
