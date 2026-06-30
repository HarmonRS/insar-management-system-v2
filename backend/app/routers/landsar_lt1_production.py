from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM, RadarDataORM, SARSceneGeoORM
from ..services.job_handlers import JOB_TYPE_SAR_SCENE_PREPROCESS
from ..services.job_queue_service import job_queue_service
from ..services.landsar_lt1_production_service import landsar_lt1_production_service
from ..services.task_service import task_service
from ..utils import normalize_satellite_family
from .dependencies import _add_operation_audit_log, _get_current_user, _require_admin


router = APIRouter()
STATIC_ASSET_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


class LandsarLt1ImageProductionRequest(BaseModel):
    source_asset_ids: List[int] = Field(default_factory=list)
    radar_data_ids: List[int] = Field(default_factory=list)
    mode: str = "scene"
    task_name: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value):
        mode = str(value or "scene").strip().lower()
        if mode == "stack":
            mode = "batch"
        if mode not in {"scene", "batch"}:
            raise ValueError("mode must be scene or batch")
        return mode


def _dedupe_positive_ids(values: List[int]) -> List[int]:
    result: List[int] = []
    for value in values or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def _scene_product_marker(scene: SARSceneGeoORM) -> Dict[str, Any]:
    return {
        "scene_id": scene.id,
        "radar_data_id": scene.radar_data_id,
        "product_id": f"sar_scene_geo:{scene.id}",
        "product_family": "lt1_analysis_ready_geotiff",
        "engine_code": scene.analysis_engine,
        "profile_code": scene.analysis_profile,
        "analysis_tif_path": scene.analysis_tif_path,
        "analysis_dir": scene.analysis_dir,
        "analysis_preview_path": scene.analysis_preview_path,
        "status": scene.status,
        "published_at": scene.updated_at.isoformat() if scene.updated_at else None,
    }


def _scene_asset_items(scene: SARSceneGeoORM) -> List[Dict[str, Any]]:
    candidates = [
        (1, "analysis_tif", "analysis_ready.tif", scene.analysis_tif_path, "image/tiff", True),
        (2, "preview", "preview.png", scene.analysis_preview_path, "image/png", False),
    ]
    metadata = scene.analysis_metadata_json if isinstance(scene.analysis_metadata_json, dict) else {}
    manifest_path = str(metadata.get("manifest_path") or "").strip()
    if manifest_path:
        candidates.append((3, "manifest", "manifest.json", manifest_path, "application/json", False))
    if scene.analysis_dir:
        quality_path = os.path.join(scene.analysis_dir, "quality.json")
        candidates.append((4, "quality", "quality.json", quality_path, "application/json", False))
    assets: List[Dict[str, Any]] = []
    for asset_id, role, name, path, media_type, primary in candidates:
        if not path:
            continue
        assets.append(
            {
                "id": asset_id,
                "role": role,
                "name": name,
                "relative_path": os.path.basename(path),
                "absolute_path": path,
                "format": os.path.splitext(path)[1].lower().lstrip(".") or None,
                "media_type": media_type,
                "is_required": primary,
                "is_primary": primary,
                "exists": os.path.isfile(path),
                "file_size": os.path.getsize(path) if os.path.isfile(path) else None,
            }
        )
    return assets


async def _resolve_lt1_radars_for_request(
    db: AsyncSession,
    request: LandsarLt1ImageProductionRequest,
) -> List[RadarDataORM]:
    source_asset_ids = _dedupe_positive_ids(request.source_asset_ids)
    radar_data_ids = _dedupe_positive_ids(request.radar_data_ids)
    filters = []
    if radar_data_ids:
        filters.append(RadarDataORM.id.in_(radar_data_ids))
    if source_asset_ids:
        filters.append(RadarDataORM.source_product_ref_id.in_(source_asset_ids))
    if not filters:
        return []
    result = await db.execute(select(RadarDataORM).where(*([filters[0]] if len(filters) == 1 else [filters[0] | filters[1]])))
    radars = list(result.scalars().all())
    unique: Dict[int, RadarDataORM] = {}
    for radar in radars:
        if not radar.id:
            continue
        family = normalize_satellite_family(radar.satellite_family or radar.satellite)
        if str(family or "").upper() != "LT1":
            continue
        unique[int(radar.id)] = radar
    return [unique[key] for key in sorted(unique.keys())]


async def _produced_radars_for_request(
    db: AsyncSession,
    request: LandsarLt1ImageProductionRequest,
) -> Dict[int, dict]:
    radars = await _resolve_lt1_radars_for_request(db, request)
    radar_ids = [int(item.id) for item in radars if item.id]
    if not radar_ids:
        return {}
    result = await db.execute(
        select(SARSceneGeoORM).where(
            SARSceneGeoORM.radar_data_id.in_(radar_ids),
            SARSceneGeoORM.status == "DONE",
            SARSceneGeoORM.analysis_tif_path.isnot(None),
            SARSceneGeoORM.analysis_engine == "lt_gamma",
            SARSceneGeoORM.analysis_profile == "lt1_gamma_geocoded_mli",
        )
    )
    return {int(scene.radar_data_id): _scene_product_marker(scene) for scene in result.scalars().all()}


async def _active_radars_for_request(
    db: AsyncSession,
    request: LandsarLt1ImageProductionRequest,
) -> Dict[int, dict]:
    radars = await _resolve_lt1_radars_for_request(db, request)
    radar_ids = [int(item.id) for item in radars if item.id]
    if not radar_ids:
        return {}
    result = await db.execute(
        select(SARSceneGeoORM).where(
            SARSceneGeoORM.radar_data_id.in_(radar_ids),
            SARSceneGeoORM.status.in_(["PENDING", "RUNNING"]),
        )
    )
    return {int(scene.radar_data_id): _scene_product_marker(scene) for scene in result.scalars().all()}


def _already_produced_blocker(produced: Dict[int, dict]) -> str:
    first_id = sorted(produced.keys())[0]
    marker = produced[first_id] or {}
    product_id = marker.get("product_id") or "unknown"
    return f"Radar data {first_id} already has an analysis-ready GeoTIFF: {product_id}"


def _active_blocker(active: Dict[int, dict]) -> str:
    first_id = sorted(active.keys())[0]
    marker = active[first_id] or {}
    return f"Radar data {first_id} already has an active GeoTIFF production task (scene_id={marker.get('scene_id')})."


@router.get("/landsar-lt1-production/capabilities")
async def get_landsar_lt1_capabilities(
    current_user: AuthUserORM = Depends(_get_current_user),
):
    _ = current_user
    legacy = landsar_lt1_production_service.check_capabilities()
    return {
        "catalog_name": "sar_scene_geo",
        "supported_profiles": ["lt1_gamma_geocoded_mli"],
        "engine": "lt_gamma",
        "available": True,
        "status": "configured",
        "message": "LT-1 image production uses the existing Gamma single-scene pipeline: multilook, geocode, and analysis-ready GeoTIFF registration.",
        "legacy_landsar_import": legacy,
    }


@router.post("/landsar-lt1-production/preview")
async def preview_landsar_lt1_production(
    request: LandsarLt1ImageProductionRequest,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    blockers: List[str] = []
    warnings: List[str] = []
    radars = await _resolve_lt1_radars_for_request(db, request)
    if not radars:
        blockers.append("No LT-1 radar records were resolved from the selected source assets.")
    if request.mode == "scene" and len(radars) != 1:
        blockers.append("Scene mode requires exactly one LT-1 source asset.")
    if request.mode == "batch" and len(radars) < 1:
        blockers.append("Batch mode requires at least one LT-1 source asset.")
    produced = await _produced_radars_for_request(db, request)
    if produced:
        blockers.append(_already_produced_blocker(produced))
    active = await _active_radars_for_request(db, request)
    if active:
        blockers.append(_active_blocker(active))
    if request.mode == "batch":
        warnings.append("Batch mode submits one independent geocoded GeoTIFF task per scene; it does not build a D-InSAR stack.")
    preview = {
        "allow_submit": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "mode": request.mode,
        "profile_code": "lt1_gamma_geocoded_mli",
        "engine": "lt_gamma",
        "scene_count": len(radars),
        "source_asset_count": len(_dedupe_positive_ids(request.source_asset_ids)),
        "radar_data_count": len(radars),
        "produced_radars": produced,
        "active_radars": active,
        "scenes": [
            {
                "radar_data_id": radar.id,
                "source_asset_id": radar.source_product_ref_id,
                "satellite": radar.satellite,
                "imaging_date": radar.imaging_date,
                "imaging_mode": radar.imaging_mode,
                "polarization": radar.polarization,
                "file_path": radar.file_path,
            }
            for radar in radars
        ],
    }
    return preview


@router.post("/landsar-lt1-production/run", status_code=202)
async def queue_landsar_lt1_production(
    request: LandsarLt1ImageProductionRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    preview = await preview_landsar_lt1_production(request, current_user=admin_user, db=db)
    if preview.get("blockers"):
        raise HTTPException(status_code=400, detail={"blockers": preview.get("blockers")})

    queued: List[Dict[str, Any]] = []
    radars = await _resolve_lt1_radars_for_request(db, request)
    for radar in radars:
        result = await db.execute(
            select(SARSceneGeoORM)
            .where(SARSceneGeoORM.radar_data_id == int(radar.id))
            .with_for_update(skip_locked=True)
        )
        scene = result.scalar_one_or_none()
        if scene and scene.status in ("PENDING", "RUNNING"):
            raise HTTPException(status_code=409, detail=f"Radar data {radar.id} already has an active GeoTIFF production task.")
        if scene and scene.status == "DONE" and scene.analysis_tif_path:
            raise HTTPException(status_code=409, detail=f"Radar data {radar.id} already has an analysis-ready GeoTIFF.")
        if not scene:
            scene = SARSceneGeoORM(radar_data_id=int(radar.id), status="PENDING")
            db.add(scene)
            await db.flush()
        else:
            scene.status = "PENDING"
            scene.error_msg = None
        await db.flush()
        scene_id = int(scene.id)
        await db.commit()
        payload = {
            "scene_id": scene_id,
            "radar_data_id": int(radar.id),
            "engine": "lt_gamma",
            "source_asset_id": radar.source_product_ref_id,
            "requested_from": "landsar_lt1_production",
        }
        task_label = request.task_name or radar.product_unique_id or radar.unique_id or f"radar_id={radar.id}"
        task_type = f"LT1_SCENE_GEOTIFF_{scene_id}"
        try:
            task_id = await task_service.create_task(
                task_type,
                f"LT-1 geocoded GeoTIFF: {task_label}",
                params=payload,
            )
            job_id = await job_queue_service.create_job(
                JOB_TYPE_SAR_SCENE_PREPROCESS,
                payload=payload,
                task_id=task_id,
                max_attempts=3,
            )
        except Exception as exc:
            failed_scene = await db.get(SARSceneGeoORM, scene_id)
            if failed_scene and failed_scene.status == "PENDING":
                failed_scene.status = "FAILED"
                failed_scene.error_msg = "Job queue failed"
                await db.commit()
            raise HTTPException(status_code=409 if "conflict" in str(exc).lower() else 400, detail=str(exc)) from exc
        queued.append(
            {
                "task_id": task_id,
                "job_id": job_id,
                "scene_id": scene_id,
                "radar_data_id": int(radar.id),
                "source_asset_id": radar.source_product_ref_id,
            }
        )
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="lt1_geotiff_production_queued",
        resource="landsar-lt1-production/run",
        detail={
            "queued": queued,
            "mode": request.mode,
            "scene_count": len(queued),
        },
    )
    await db.commit()
    return {
        "message": "LT-1 geocoded GeoTIFF production job queued.",
        "task_id": queued[0]["task_id"] if len(queued) == 1 else None,
        "job_id": queued[0]["job_id"] if len(queued) == 1 else None,
        "queued": queued,
        "preview": preview,
    }


@router.get("/landsar-lt1-production/products")
async def list_landsar_lt1_products(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    query: Optional[str] = None,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    safe_limit = max(1, min(500, int(limit or 100)))
    safe_offset = max(0, int(offset or 0))
    filters = [
        SARSceneGeoORM.analysis_engine == "lt_gamma",
        SARSceneGeoORM.analysis_profile == "lt1_gamma_geocoded_mli",
    ]
    if status:
        filters.append(SARSceneGeoORM.status == str(status).strip().upper())
    if query:
        like = f"%{str(query).strip()}%"
        filters.append(RadarDataORM.product_unique_id.ilike(like) | RadarDataORM.unique_id.ilike(like) | RadarDataORM.file_path.ilike(like))
    total_result = await db.execute(
        select(func.count(SARSceneGeoORM.id))
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*filters)
    )
    total = int(total_result.scalar_one() or 0)
    result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*filters)
        .order_by(SARSceneGeoORM.updated_at.desc().nullslast(), SARSceneGeoORM.id.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
    items = []
    for scene, radar in result.all():
        marker = _scene_product_marker(scene)
        items.append(
            {
                "id": scene.id,
                "product_id": marker["product_id"],
                "catalog_name": "sar_scene_geo",
                "product_family": "lt1_analysis_ready_geotiff",
                "product_type": "analysis_ready_geotiff",
                "display_name": radar.product_unique_id or radar.unique_id or f"radar_id={radar.id}",
                "task_name": "",
                "profile_code": scene.analysis_profile,
                "engine_code": scene.analysis_engine,
                "status": scene.status,
                "health_status": "OK" if scene.status == "DONE" and scene.analysis_tif_path else "PENDING",
                "publish_dir": scene.analysis_dir,
                "manifest_path": (scene.analysis_metadata_json or {}).get("manifest_path") if isinstance(scene.analysis_metadata_json, dict) else None,
                "native_output_dir": scene.analysis_dir,
                "primary_asset_path": scene.analysis_tif_path,
                "summary": {
                    "scene_count": 1,
                    "radar_data_id": radar.id,
                    "source_asset_ids": [radar.source_product_ref_id] if radar.source_product_ref_id else [],
                    "imaging_date": radar.imaging_date,
                    "polarization": radar.polarization,
                    "pixel_size_m": scene.pixel_size_m,
                    "backscatter_unit": scene.analysis_backscatter_unit,
                },
                "tags": {"engine": scene.analysis_engine, "profile": scene.analysis_profile},
                "produced_at": scene.updated_at.isoformat() if scene.updated_at else None,
                "published_at": scene.updated_at.isoformat() if scene.updated_at else None,
                "registered_at": scene.created_at.isoformat() if scene.created_at else None,
            }
        )
    return {"total": total, "limit": safe_limit, "offset": safe_offset, "items": items}


@router.get("/landsar-lt1-production/products/{product_db_id}")
async def get_landsar_lt1_product_detail(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(
            SARSceneGeoORM.id == product_db_id,
            SARSceneGeoORM.analysis_engine == "lt_gamma",
            SARSceneGeoORM.analysis_profile == "lt1_gamma_geocoded_mli",
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="LT-1 geocoded GeoTIFF product not found")
    scene, radar = row
    marker = _scene_product_marker(scene)
    detail = {
        "id": scene.id,
        "product_id": marker["product_id"],
        "catalog_name": "sar_scene_geo",
        "product_family": "lt1_analysis_ready_geotiff",
        "product_type": "analysis_ready_geotiff",
        "display_name": radar.product_unique_id or radar.unique_id or f"radar_id={radar.id}",
        "profile_code": scene.analysis_profile,
        "engine_code": scene.analysis_engine,
        "status": scene.status,
        "publish_dir": scene.analysis_dir,
        "primary_asset_path": scene.analysis_tif_path,
        "summary": {
            "scene_count": 1,
            "radar_data_id": radar.id,
            "source_asset_ids": [radar.source_product_ref_id] if radar.source_product_ref_id else [],
            "imaging_date": radar.imaging_date,
            "polarization": radar.polarization,
            "pixel_size_m": scene.pixel_size_m,
            "backscatter_unit": scene.analysis_backscatter_unit,
        },
        "assets": _scene_asset_items(scene),
    }
    return detail


@router.get("/landsar-lt1-production/products/{product_db_id}/assets/{asset_id}")
async def get_landsar_lt1_product_asset(
    product_db_id: int,
    asset_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    scene = await db.get(SARSceneGeoORM, product_db_id)
    if scene is None or scene.analysis_engine != "lt_gamma" or scene.analysis_profile != "lt1_gamma_geocoded_mli":
        raise HTTPException(status_code=404, detail="LT-1 geocoded GeoTIFF product not found")
    asset = next((item for item in _scene_asset_items(scene) if int(item["id"]) == int(asset_id)), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="LT-1 geocoded GeoTIFF asset not found")
    absolute_path = str(asset.get("absolute_path") or "")
    if not absolute_path or not os.path.isfile(absolute_path):
        raise HTTPException(status_code=404, detail="Asset file not found")
    return FileResponse(
        absolute_path,
        media_type=str(asset.get("media_type") or "application/octet-stream"),
        filename=str(asset.get("name") or os.path.basename(absolute_path)),
        headers=STATIC_ASSET_CACHE_HEADERS,
    )
