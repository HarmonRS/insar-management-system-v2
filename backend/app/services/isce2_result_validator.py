from __future__ import annotations

import os
from typing import Any, Dict, List, Sequence

def _normalize_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


def _dedupe_existing_files(paths: Sequence[Any]) -> List[str]:
    normalized_paths: List[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = _normalize_path(path)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_paths.append(normalized)
    return normalized_paths


def _inspect_raster(path: str) -> Dict[str, Any]:
    gdal_error: Exception | None = None
    try:
        from osgeo import gdal

        gdal.UseExceptions()
        dataset = gdal.Open(path, gdal.GA_ReadOnly)
        if dataset is None:
            raise RuntimeError("GDAL returned no dataset")

        band_count = int(dataset.RasterCount or 0)
        width = int(dataset.RasterXSize or 0)
        height = int(dataset.RasterYSize or 0)
        if band_count <= 0 or width <= 0 or height <= 0:
            raise ValueError(
                f"invalid raster geometry bands={band_count} width={width} height={height}"
            )

        return {
            "width": width,
            "height": height,
            "bands": band_count,
            "driver": dataset.GetDriver().ShortName if dataset.GetDriver() is not None else "",
            "projection_present": bool(dataset.GetProjection()),
            "geo_transform_present": bool(dataset.GetGeoTransform(can_return_null=True)),
            "reader": "gdal",
        }
    except Exception as exc:
        gdal_error = exc

    try:
        import rasterio

        with rasterio.open(path) as dataset:
            band_count = int(dataset.count or 0)
            width = int(dataset.width or 0)
            height = int(dataset.height or 0)
            if band_count <= 0 or width <= 0 or height <= 0:
                raise ValueError(
                    f"invalid raster geometry bands={band_count} width={width} height={height}"
                )

            return {
                "width": width,
                "height": height,
                "bands": band_count,
                "driver": str(dataset.driver or ""),
                "projection_present": bool(dataset.crs),
                "geo_transform_present": dataset.transform is not None,
                "reader": "rasterio",
            }
    except Exception as exc:
        if gdal_error is not None:
            raise RuntimeError(f"gdal={gdal_error}; rasterio={exc}") from exc
        raise


def validate_isce2_result_files(
    primary_file: Any,
    source_files: Sequence[Any] | None = None,
) -> Dict[str, Any]:
    normalized_primary = _normalize_path(primary_file)
    normalized_sources = _dedupe_existing_files(source_files or [])
    if normalized_primary and normalized_primary not in normalized_sources and os.path.isfile(normalized_primary):
        normalized_sources.insert(0, normalized_primary)

    issues: List[str] = []
    accepted_sources: List[str] = []
    metrics: Dict[str, Any] = {
        "primary_exists": False,
        "primary_non_empty": False,
        "primary_readable": False,
        "primary_size_bytes": 0,
        "source_file_count": len(normalized_sources),
        "coh_present": False,
    }

    primary_metadata: Dict[str, Any] = {}
    if not normalized_primary:
        issues.append("Primary ISCE2 displacement file path is empty.")
    elif not os.path.isfile(normalized_primary):
        issues.append(f"Primary ISCE2 displacement file not found: {normalized_primary}")
    else:
        metrics["primary_exists"] = True
        try:
            primary_size = int(os.path.getsize(normalized_primary))
        except OSError:
            primary_size = 0
        metrics["primary_size_bytes"] = primary_size
        if primary_size <= 0:
            issues.append(f"Primary ISCE2 displacement file is empty: {normalized_primary}")
        else:
            metrics["primary_non_empty"] = True
            try:
                primary_metadata = _inspect_raster(normalized_primary)
                metrics["primary_readable"] = True
                metrics["primary_raster"] = primary_metadata
                accepted_sources.append(normalized_primary)
            except Exception as exc:
                issues.append(
                    f"Primary ISCE2 displacement file is not a readable GeoTIFF: {normalized_primary}: {exc}"
                )

    coh_path = ""
    coh_metadata: Dict[str, Any] = {}
    for candidate in normalized_sources:
        if candidate == normalized_primary or not os.path.isfile(candidate):
            continue
        try:
            candidate_size = int(os.path.getsize(candidate))
        except OSError:
            candidate_size = 0
        if candidate_size <= 0:
            issues.append(f"Auxiliary ISCE2 output file is empty: {candidate}")
            continue
        try:
            coh_metadata = _inspect_raster(candidate)
            coh_path = candidate
            accepted_sources.append(candidate)
            break
        except Exception as exc:
            issues.append(
                f"Auxiliary ISCE2 output file is not a readable GeoTIFF: {candidate}: {exc}"
            )

    if coh_path:
        metrics["coh_present"] = True
        metrics["coh_raster"] = coh_metadata

    accepted = bool(metrics["primary_readable"])
    return {
        "accepted": accepted,
        "primary_file": normalized_primary if accepted else "",
        "source_files": accepted_sources if accepted_sources else ([normalized_primary] if accepted else []),
        "coh_file": coh_path,
        "issues": issues,
        "metrics": metrics,
    }
