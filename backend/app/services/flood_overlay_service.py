"""Flood overlay and impact analysis service."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from geoalchemy2.functions import ST_Intersects
from geoalchemy2.shape import from_shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from ..config import settings
from ..models import FloodDetectionORM, FloodOverlayORM, HazardPointORM, ResultProductORM


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _hazard_point_to_dict(point: HazardPointORM, *, distance_m: float | None = None) -> dict[str, Any]:
    return {
        "id": point.id,
        "name": point.hazard_name,
        "type": point.hazard_type,
        "city": point.city,
        "county": point.county,
        "township": point.township,
        "longitude": _to_float(point.longitude),
        "latitude": _to_float(point.latitude),
        "distance_m": 0 if distance_m is None else round(float(distance_m), 2),
    }


def _dinsar_product_to_dict(product: ResultProductORM) -> dict[str, Any]:
    summary = product.summary_json if isinstance(product.summary_json, dict) else {}
    deformation = (
        summary.get("deformation_mm")
        or summary.get("max_deformation_mm")
        or summary.get("mean_deformation_mm")
        or summary.get("deformation")
    )
    return {
        "id": product.id,
        "product_id": product.product_id,
        "display_name": product.display_name,
        "engine": product.engine_code,
        "status": product.status,
        "deformation_mm": deformation,
        "ai_score": product.ai_score,
        "manifest_path": product.manifest_path,
        "preview_path": product.preview_path,
    }


def _open_raster(path: str):
    import rasterio

    normalized_path = path.replace("\\", "/")
    try:
        return rasterio.open(normalized_path)
    except Exception:
        pass
    for ext in (".bin", ".img", ".tif", ".tiff"):
        candidate = normalized_path + ext
        try:
            return rasterio.open(candidate)
        except Exception:
            pass
    raise FileNotFoundError(f"Raster file cannot be opened: {path}")


def _classified_flood_to_geojson(path: str) -> tuple[dict[str, Any], float | None, list[str]]:
    import rasterio.features
    from pyproj import CRS, Transformer
    from shapely.geometry import shape as shape_geojson
    from shapely.ops import transform

    warnings: list[str] = []
    with _open_raster(path) as src:
        data = src.read(1)
        mask = data == 2
        if not mask.any():
            return {"type": "FeatureCollection", "features": []}, 0.0, warnings

        polygons = []
        for geom, value in rasterio.features.shapes(data, mask=mask, transform=src.transform):
            if int(value) != 2:
                continue
            polygon = shape_geojson(geom)
            if not polygon.is_empty and polygon.is_valid:
                polygons.append(polygon)

        if not polygons:
            return {"type": "FeatureCollection", "features": []}, 0.0, warnings

        flood_geom = unary_union(polygons)
        source_crs = src.crs

    area_km2 = None
    output_geom = flood_geom
    if source_crs:
        try:
            crs = CRS.from_user_input(source_crs)
            if not crs.is_geographic:
                area_km2 = float(flood_geom.area) / 1_000_000.0
                transformer = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
                output_geom = transform(transformer.transform, flood_geom)
            else:
                centroid = flood_geom.centroid
                zone = int((centroid.x + 180) // 6) + 1
                epsg = 32600 + zone if centroid.y >= 0 else 32700 + zone
                transformer = Transformer.from_crs(crs, CRS.from_epsg(epsg), always_xy=True)
                projected = transform(transformer.transform, flood_geom)
                area_km2 = float(projected.area) / 1_000_000.0
        except Exception as exc:
            warnings.append(f"area calculation failed: {exc}")
    else:
        warnings.append("classified raster has no CRS; geometry is stored in source coordinates")

    feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"class": 2, "name": "flood"},
                "geometry": mapping(output_geom),
            }
        ],
    }
    return feature_collection, area_km2, warnings


def _write_geojson(detection_id: int, feature_collection: dict[str, Any]) -> str:
    out_dir = Path(settings.WATER_RESULTS_DIR or Path(settings.BACKEND_DIR) / "water_results") / "flood_overlays"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"flood_detection_{detection_id}_overlay.geojson"
    target.write_text(json.dumps(feature_collection, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def _read_geojson(path: str | None) -> dict[str, Any] | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _geometry_area_km2(geom) -> float:
    if geom.is_empty:
        return 0.0
    try:
        from pyproj import CRS, Transformer
        from shapely.ops import transform

        centroid = geom.centroid
        zone = int((centroid.x + 180) // 6) + 1
        epsg = 32600 + zone if centroid.y >= 0 else 32700 + zone
        transformer = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
        projected = transform(transformer.transform, geom)
        return float(projected.area) / 1_000_000.0
    except Exception:
        return 0.0


def _calculate_affected_aois(flood_geom, warnings: list[str], *, limit: int = 50) -> list[dict[str, Any]]:
    try:
        from ..routers import dependencies as deps

        deps._load_region_index()
        deps._load_region_geometry_index()
        region_by_id = deps._REGION_BY_ID_CACHE or {}
        geometry_by_id = deps._REGION_GEOMETRY_BY_ID_CACHE or {}
    except Exception as exc:
        warnings.append(f"AOI overlay unavailable: {exc}")
        return []

    affected: list[dict[str, Any]] = []
    for tree_id, features in geometry_by_id.items():
        node = region_by_id.get(tree_id) or {}
        level = node.get("level")
        if level in {"country", "province"}:
            continue
        try:
            geometries = [shape(feature["geometry"]) for feature in features if feature.get("geometry")]
            if not geometries:
                continue
            region_geom = unary_union(geometries)
            if region_geom.is_empty or not flood_geom.intersects(region_geom):
                continue
            intersection = flood_geom.intersection(region_geom)
            area_km2 = _geometry_area_km2(intersection)
            if area_km2 <= 0.0001:
                continue
            affected.append(
                {
                    "tree_id": tree_id,
                    "name": node.get("name") or tree_id,
                    "level": level,
                    "flood_area_km2": round(area_km2, 4),
                }
            )
        except Exception:
            continue

    affected.sort(key=lambda item: item["flood_area_km2"], reverse=True)
    return affected[:limit]


def _attach_overlay_payload(overlay: FloodOverlayORM) -> dict[str, Any]:
    payload = dict(overlay.summary_json) if isinstance(overlay.summary_json, dict) else {}
    payload["overlay_id"] = overlay.id
    payload["detection_id"] = overlay.detection_id
    payload["flood_vector_path"] = overlay.flood_vector_path
    payload["flood_vector_geojson"] = _read_geojson(overlay.flood_vector_path)
    return payload


async def run_overlay(detection_id: int, db: AsyncSession, *, near_threshold_m: float = 500.0) -> dict[str, Any]:
    detection = await db.get(FloodDetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail=f"Flood detection id={detection_id} not found")
    if not detection.classified_path:
        raise HTTPException(status_code=400, detail="Flood detection has no classified raster")
    path = detection.classified_path.replace("\\", "/")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Classified raster file does not exist")

    feature_collection, flood_area_km2, warnings = _classified_flood_to_geojson(path)
    flood_vector_path = _write_geojson(detection_id, feature_collection)
    impact = await _query_impact_from_geojson(
        detection_id=detection_id,
        feature_collection=feature_collection,
        db=db,
        near_threshold_m=near_threshold_m,
        warnings=warnings,
    )
    if flood_area_km2 is not None:
        impact["flood_area_km2"] = round(flood_area_km2, 4)
    impact["flood_vector_path"] = flood_vector_path

    overlay = FloodOverlayORM(
        detection_id=detection_id,
        flood_vector_path=flood_vector_path,
        hazard_points_hit=len(impact["hazard_points"]["inside_flood"]),
        hazard_points_near=len(impact["hazard_points"]["near_flood"]),
        hazard_points_total=impact["hazard_points"]["total_in_scene"],
        dinsar_products_intersecting=len(impact["dinsar_products"]),
        affected_area_km2=impact.get("flood_area_km2"),
        summary_json=impact,
    )
    db.add(overlay)
    await db.flush()
    impact["overlay_id"] = overlay.id
    overlay.summary_json = impact
    if flood_area_km2 is not None:
        detection.flood_area_km2 = round(flood_area_km2, 4)
    await db.commit()
    await db.refresh(overlay)

    return {
        "id": overlay.id,
        "detection_id": detection_id,
        "flood_vector_path": overlay.flood_vector_path,
        "flood_vector_geojson": feature_collection,
        "summary": _attach_overlay_payload(overlay),
    }


async def get_overlay_result(detection_id: int, db: AsyncSession) -> dict[str, Any]:
    overlay = (
        await db.execute(
            select(FloodOverlayORM)
            .where(FloodOverlayORM.detection_id == detection_id)
            .order_by(FloodOverlayORM.id.desc())
        )
    ).scalars().first()
    if overlay and isinstance(overlay.summary_json, dict):
        return _attach_overlay_payload(overlay)

    detection = await db.get(FloodDetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail=f"Flood detection id={detection_id} not found")
    return {
        "detection_id": detection_id,
        "flood_area_km2": detection.flood_area_km2,
        "hazard_points": {"inside_flood": [], "near_flood": [], "total_in_scene": 0},
        "dinsar_products": [],
        "affected_aois": [],
        "flood_vector_path": None,
        "flood_vector_geojson": None,
        "warnings": ["overlay has not been run"],
    }


async def _query_impact_from_geojson(
    *,
    detection_id: int,
    feature_collection: dict[str, Any],
    db: AsyncSession,
    near_threshold_m: float,
    warnings: list[str],
) -> dict[str, Any]:
    features = feature_collection.get("features") or []
    if not features:
        return {
            "detection_id": detection_id,
            "flood_area_km2": 0.0,
            "hazard_points": {"inside_flood": [], "near_flood": [], "total_in_scene": 0},
            "dinsar_products": [],
            "affected_aois": [],
            "warnings": warnings,
        }

    flood_geom = unary_union([shape(feature["geometry"]) for feature in features if feature.get("geometry")])
    if flood_geom.is_empty:
        warnings.append("flood geometry is empty")
    flood_wkt = flood_geom.wkt
    area_geom = func.ST_GeomFromText(flood_wkt, 4326)
    area_geog = func.Geography(area_geom)

    inside_points: list[dict[str, Any]] = []
    near_points: list[dict[str, Any]] = []
    dinsar_products: list[dict[str, Any]] = []
    affected_aois = _calculate_affected_aois(flood_geom, warnings)

    try:
        inside_rows = (
            await db.execute(
                select(HazardPointORM).where(ST_Intersects(HazardPointORM.geom, area_geom))
            )
        ).scalars().all()
        inside_ids = {point.id for point in inside_rows}
        inside_points = [_hazard_point_to_dict(point, distance_m=0) for point in inside_rows]

        near_rows = (
            await db.execute(
                select(
                    HazardPointORM,
                    func.ST_Distance(func.Geography(HazardPointORM.geom), area_geog).label("distance_m"),
                ).where(func.ST_DWithin(func.Geography(HazardPointORM.geom), area_geog, near_threshold_m))
            )
        ).all()
        for point, distance_m in near_rows:
            if point.id in inside_ids:
                continue
            near_points.append(_hazard_point_to_dict(point, distance_m=distance_m))
    except Exception as exc:
        warnings.append(f"hazard point overlay unavailable: {exc}")

    try:
        products = (
            await db.execute(
                select(ResultProductORM).where(
                    ResultProductORM.catalog_name == "dinsar",
                    ResultProductORM.status == "READY",
                    ST_Intersects(ResultProductORM.geom, area_geom),
                )
            )
        ).scalars().all()
        dinsar_products = [_dinsar_product_to_dict(product) for product in products]
    except Exception as exc:
        warnings.append(f"dinsar product overlay unavailable: {exc}")

    return {
        "detection_id": detection_id,
        "flood_area_km2": None,
        "hazard_points": {
            "inside_flood": inside_points,
            "near_flood": near_points,
            "total_in_scene": len(inside_points) + len(near_points),
        },
        "dinsar_products": dinsar_products,
        "affected_aois": affected_aois,
        "warnings": warnings,
    }
