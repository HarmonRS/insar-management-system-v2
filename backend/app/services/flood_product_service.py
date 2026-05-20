"""Flood product listing and manifest helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import FloodDetectionORM, FloodOverlayORM, FloodProductORM, SARSceneGeoORM


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _product_to_dict(product: FloodProductORM) -> dict[str, Any]:
    summary = product.summary_json if isinstance(product.summary_json, dict) else {}
    detection = product.detection
    overlay = product.overlay
    return {
        "id": product.id,
        "product_id": product.product_id,
        "detection_id": product.detection_id,
        "overlay_id": product.overlay_id,
        "display_name": product.display_name,
        "status": product.status,
        "publish_dir": product.publish_dir,
        "manifest_path": product.manifest_path,
        "summary": summary,
        "created_at": _iso(product.created_at),
        "flood_area_km2": getattr(detection, "flood_area_km2", None),
        "affected_area_km2": getattr(overlay, "affected_area_km2", None),
    }


async def list_flood_products(
    *,
    db: AsyncSession,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
) -> dict[str, Any]:
    count_query = select(func.count()).select_from(FloodProductORM)
    query = (
        select(FloodProductORM)
        .options(
            selectinload(FloodProductORM.detection),
            selectinload(FloodProductORM.overlay),
        )
        .order_by(FloodProductORM.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        count_query = count_query.where(FloodProductORM.status == status)
        query = query.where(FloodProductORM.status == status)

    total = (await db.execute(count_query)).scalar_one()
    rows = (await db.execute(query)).scalars().all()
    return {"items": [_product_to_dict(row) for row in rows], "total": total}


async def get_flood_product(product_id_or_pk: str, db: AsyncSession) -> dict[str, Any]:
    product = await _get_product(product_id_or_pk, db)
    return _product_to_dict(product)


async def get_flood_product_manifest(product_id_or_pk: str, db: AsyncSession) -> dict[str, Any]:
    product = await _get_product(product_id_or_pk, db)
    if product.manifest_path and os.path.isfile(product.manifest_path):
        try:
            with open(product.manifest_path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read manifest: {exc}") from exc

    return _build_manifest_from_db(product)


async def create_flood_product_for_detection(detection_id: int, db: AsyncSession) -> dict[str, Any]:
    detection = await db.get(FloodDetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail=f"Flood detection id={detection_id} not found")
    if detection.status != "DONE":
        raise HTTPException(status_code=400, detail=f"Flood detection is not DONE: {detection.status}")

    existing = (
        await db.execute(
            select(FloodProductORM)
            .options(
                selectinload(FloodProductORM.detection),
                selectinload(FloodProductORM.overlay),
            )
            .where(FloodProductORM.detection_id == detection_id)
            .order_by(FloodProductORM.id.desc())
        )
    ).scalars().first()
    if existing:
        return _product_to_dict(existing)

    overlay = (
        await db.execute(
            select(FloodOverlayORM)
            .where(FloodOverlayORM.detection_id == detection_id)
            .order_by(FloodOverlayORM.id.desc())
        )
    ).scalars().first()
    product = FloodProductORM(
        product_id=f"FLOOD-{detection_id:06d}",
        detection_id=detection_id,
        overlay_id=overlay.id if overlay else None,
        display_name=f"Flood detection #{detection_id}",
        status="READY",
        publish_dir=detection.output_dir,
        manifest_path=None,
        summary_json={
            "created_from": "flood_detection",
            "flood_area_km2": detection.flood_area_km2,
            "stable_water_area_km2": detection.stable_water_area_km2,
            "classified_path": detection.classified_path,
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    db.add(product)
    await db.commit()
    return await get_flood_product(str(product.id), db)


async def _get_product(product_id_or_pk: str, db: AsyncSession) -> FloodProductORM:
    value = str(product_id_or_pk).strip()
    query = select(FloodProductORM).options(
        selectinload(FloodProductORM.detection).selectinload(FloodDetectionORM.pre_scene).selectinload(SARSceneGeoORM.radar_data),
        selectinload(FloodProductORM.detection).selectinload(FloodDetectionORM.post_scene).selectinload(SARSceneGeoORM.radar_data),
        selectinload(FloodProductORM.overlay),
    )
    if value.isdigit():
        query = query.where(FloodProductORM.id == int(value))
    else:
        query = query.where(FloodProductORM.product_id == value)
    product = (await db.execute(query)).scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Flood product {product_id_or_pk} not found")
    return product


def _build_manifest_from_db(product: FloodProductORM) -> dict[str, Any]:
    detection = product.detection
    overlay = product.overlay
    pre_scene = detection.pre_scene if detection else None
    post_scene = detection.post_scene if detection else None
    pre_radar = pre_scene.radar_data if pre_scene else None
    post_radar = post_scene.radar_data if post_scene else None
    return {
        "schema": "flood_product_manifest.v1",
        "product": _product_to_dict(product),
        "detection": {
            "id": detection.id if detection else None,
            "status": detection.status if detection else None,
            "classified_path": detection.classified_path if detection else None,
            "flood_area_km2": detection.flood_area_km2 if detection else None,
            "stable_water_area_km2": detection.stable_water_area_km2 if detection else None,
            "pre_scene": {
                "id": pre_scene.id if pre_scene else None,
                "radar_data_id": pre_scene.radar_data_id if pre_scene else None,
                "satellite": pre_radar.satellite if pre_radar else None,
                "imaging_date": pre_radar.imaging_date if pre_radar else None,
                "geo_path": pre_scene.geo_path if pre_scene else None,
                "analysis_tif_path": pre_scene.analysis_tif_path if pre_scene else None,
                "analysis_engine": pre_scene.analysis_engine if pre_scene else None,
                "analysis_profile": pre_scene.analysis_profile if pre_scene else None,
            },
            "post_scene": {
                "id": post_scene.id if post_scene else None,
                "radar_data_id": post_scene.radar_data_id if post_scene else None,
                "satellite": post_radar.satellite if post_radar else None,
                "imaging_date": post_radar.imaging_date if post_radar else None,
                "geo_path": post_scene.geo_path if post_scene else None,
                "analysis_tif_path": post_scene.analysis_tif_path if post_scene else None,
                "analysis_engine": post_scene.analysis_engine if post_scene else None,
                "analysis_profile": post_scene.analysis_profile if post_scene else None,
            },
        },
        "overlay": {
            "id": overlay.id if overlay else None,
            "flood_vector_path": overlay.flood_vector_path if overlay else None,
            "affected_area_km2": overlay.affected_area_km2 if overlay else None,
            "hazard_points_hit": overlay.hazard_points_hit if overlay else 0,
            "hazard_points_near": overlay.hazard_points_near if overlay else 0,
            "dinsar_products_intersecting": overlay.dinsar_products_intersecting if overlay else 0,
            "summary": overlay.summary_json if overlay else None,
        },
    }
