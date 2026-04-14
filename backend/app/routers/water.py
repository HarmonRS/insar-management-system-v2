"""Water body monitoring v2 router — SARscape-based pipeline."""
from __future__ import annotations

import os
import re as _re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..database import get_db
from ..models import AuthUserORM, RadarDataORM, SARSceneGeoORM, FloodDetectionORM, WaterDetectionORM, GF3ProcessingORM
from ..services.job_handlers import JOB_TYPE_WATER_GEOCODE, JOB_TYPE_WATER_FLOOD, JOB_TYPE_WATER_DETECT, JOB_TYPE_GF3_PROCESS
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from .dependencies import _require_admin, _get_current_user

router = APIRouter()

_WATER_JOB_MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class GeocodeRequest(BaseModel):
    radar_data_id: int = Field(..., description="RadarDataORM 主键")


class FloodDetectRequest(BaseModel):
    pre_scene_id: int = Field(..., description="灾前 SARSceneGeoORM 主键")
    post_scene_id: int = Field(..., description="灾后 SARSceneGeoORM 主键")
    refine: bool = Field(default=False, description="是否启用 MRF 精化")


class WaterPairRequest(BaseModel):
    pre_start: Optional[str] = Field(default=None, description="灾前开始日期 YYYYMMDD")
    pre_end: Optional[str] = Field(default=None, description="灾前结束日期 YYYYMMDD")
    post_start: Optional[str] = Field(default=None, description="灾后开始日期 YYYYMMDD")
    post_end: Optional[str] = Field(default=None, description="灾后结束日期 YYYYMMDD")
    overlap_threshold: float = Field(default=0.3, ge=0.0, le=1.0, description="最小重叠比例")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _overlap_ratio(poly_a, poly_b) -> float:
    """计算两个 polygon 的重叠比例（相对于较小的那个）。
    支持 GeoJSON dict 或原始坐标数组 [[lon,lat], ...] 两种格式。
    """
    try:
        from shapely.geometry import shape, Polygon

        def _to_geom(poly):
            if isinstance(poly, str):
                import json
                poly = json.loads(poly)
            # 原始坐标数组格式：[[lon, lat], ...]
            if isinstance(poly, list):
                return Polygon(poly)
            # GeoJSON dict 格式
            return shape(poly)

        a = _to_geom(poly_a)
        b = _to_geom(poly_b)
        if not a.is_valid or not b.is_valid:
            return 0.0
        inter = a.intersection(b).area
        smaller = min(a.area, b.area)
        return inter / smaller if smaller > 0 else 0.0
    except Exception:
        return 0.0


async def _queue_water_job(
    job_type: str,
    task_type: str,
    task_name: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        task_id = await task_service.create_task(
            task_type=task_type,
            task_name=task_name,
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=job_type,
            payload=payload,
            task_id=task_id,
            max_attempts=_WATER_JOB_MAX_ATTEMPTS,
        )
        return {"task_id": task_id, "job_id": job_id, "job_type": job_type, "message": "Job queued."}
    except ValueError as exc:
        msg = str(exc)
        raise HTTPException(status_code=409 if "冲突" in msg else 400, detail=msg) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/water/geocode", status_code=202)
async def submit_geocode(
    req: GeocodeRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """提交单景 SAR 地理编码任务（多视 + 地理编码 + 辐射定标）。"""
    radar = await db.get(RadarDataORM, req.radar_data_id)
    if not radar:
        raise HTTPException(status_code=404, detail=f"RadarData id={req.radar_data_id} 不存在")

    result = await db.execute(
        select(SARSceneGeoORM)
        .where(SARSceneGeoORM.radar_data_id == req.radar_data_id)
        .with_for_update(skip_locked=True)
    )
    scene = result.scalar_one_or_none()
    if scene and scene.status in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=409, detail="该场景已有进行中的地理编码任务")
    if not scene:
        scene = SARSceneGeoORM(radar_data_id=req.radar_data_id, status="PENDING")
        db.add(scene)
        await db.flush()
    else:
        scene.status = "PENDING"
        scene.error_msg = None
    await db.flush()
    scene_id = scene.id
    await db.commit()

    try:
        return await _queue_water_job(
            job_type=JOB_TYPE_WATER_GEOCODE,
            task_type=f"WATER_GEOCODE_{scene_id}",
            task_name=f"水体地理编码 radar_id={req.radar_data_id}",
            payload={"scene_id": scene_id, "radar_data_id": req.radar_data_id},
        )
    except HTTPException:
        # job 入队失败，回滚 scene 状态避免卡在 PENDING
        async with db.begin():
            s = await db.get(SARSceneGeoORM, scene_id)
            if s and s.status == "PENDING":
                s.status = "FAILED"
                s.error_msg = "任务入队失败"
        raise


@router.post("/water/scenes/{scene_id}/reset", status_code=200)
async def reset_scene_status(
    scene_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """将卡住的场景状态重置为 FAILED，允许重新提交。"""
    scene = await db.get(SARSceneGeoORM, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail=f"场景 id={scene_id} 不存在")
    if scene.status not in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=400, detail=f"场景当前状态为 {scene.status}，无需重置")
    scene.status = "FAILED"
    scene.error_msg = "手动重置（任务已取消）"
    await db.commit()
    return {"id": scene_id, "status": "FAILED", "message": "已重置"}


@router.get("/water/scenes/done-ids")
async def list_done_scene_radar_ids(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """返回所有 status=DONE 的场景对应的 radar_data_id 列表，用于前端标注。"""
    result = await db.execute(
        select(SARSceneGeoORM.radar_data_id).where(SARSceneGeoORM.status == "DONE")
    )
    return {"ids": [row for (row,) in result.all()]}


@router.get("/water/scenes/active-ids")
async def list_active_scene_radar_ids(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """返回所有 status=PENDING/RUNNING 的场景对应的 radar_data_id 列表，用于前端标注。"""
    result = await db.execute(
        select(SARSceneGeoORM.radar_data_id).where(
            SARSceneGeoORM.status.in_(["PENDING", "RUNNING"])
        )
    )
    return {"ids": [row for (row,) in result.all()]}


@router.get("/water/scenes")
async def list_scenes(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """列出所有单景地理编码记录（分页）。"""
    from sqlalchemy import func
    total_result = await db.execute(select(func.count()).select_from(SARSceneGeoORM))
    total = total_result.scalar_one()

    result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .order_by(SARSceneGeoORM.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()

    items = []
    for scene, radar in rows:
        items.append({
            "id": scene.id,
            "radar_data_id": scene.radar_data_id,
            "satellite": radar.satellite,
            "imaging_date": radar.imaging_date,
            "geo_path": scene.geo_path,
            "pixel_size_m": scene.pixel_size_m,
            "status": scene.status,
            "error_msg": scene.error_msg,
            "created_at": scene.created_at.isoformat() if scene.created_at else None,
            "coverage_polygon": radar.coverage_polygon,
            "min_lat": radar.min_lat,
            "max_lat": radar.max_lat,
            "min_lon": radar.min_lon,
            "max_lon": radar.max_lon,
        })
    return {"items": items, "total": total}


@router.delete("/water/scenes/cleanup", status_code=200)
async def cleanup_failed_scenes(
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """删除所有 FAILED 状态的场景记录。"""
    from sqlalchemy import delete as sql_delete
    result = await db.execute(
        sql_delete(SARSceneGeoORM).where(SARSceneGeoORM.status == "FAILED")
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.post("/water/flood-detect", status_code=202)
async def submit_flood_detect(
    req: FloodDetectRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """提交洪涝检测任务（灾前 + 灾后两景配对）。"""
    pre = await db.get(SARSceneGeoORM, req.pre_scene_id)
    post = await db.get(SARSceneGeoORM, req.post_scene_id)
    if not pre:
        raise HTTPException(status_code=404, detail=f"灾前场景 id={req.pre_scene_id} 不存在")
    if not post:
        raise HTTPException(status_code=404, detail=f"灾后场景 id={req.post_scene_id} 不存在")
    if pre.status != "DONE":
        raise HTTPException(status_code=400, detail=f"灾前场景尚未完成地理编码 (status={pre.status})")
    if post.status != "DONE":
        raise HTTPException(status_code=400, detail=f"灾后场景尚未完成地理编码 (status={post.status})")

    result = await db.execute(
        select(FloodDetectionORM).where(
            FloodDetectionORM.pre_scene_id == req.pre_scene_id,
            FloodDetectionORM.post_scene_id == req.post_scene_id,
        ).with_for_update(skip_locked=True)
    )
    det = result.scalar_one_or_none()
    if det and det.status in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=409, detail="该配对已有进行中的洪涝检测任务")
    if not det:
        det = FloodDetectionORM(
            pre_scene_id=req.pre_scene_id,
            post_scene_id=req.post_scene_id,
            status="PENDING",
        )
        db.add(det)
        await db.flush()
    else:
        det.status = "PENDING"
        det.error_msg = None
    await db.flush()
    det_id = det.id
    await db.commit()

    try:
        return await _queue_water_job(
            job_type=JOB_TYPE_WATER_FLOOD,
            task_type=f"WATER_FLOOD_{det_id}",
            task_name=f"洪涝检测 pre={req.pre_scene_id} post={req.post_scene_id}",
            payload={"detection_id": det_id, "refine": req.refine},
        )
    except HTTPException:
        async with db.begin():
            d = await db.get(FloodDetectionORM, det_id)
            if d and d.status == "PENDING":
                d.status = "FAILED"
                d.error_msg = "任务入队失败"
        raise


@router.get("/water/flood-events")
async def list_flood_events(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """列出所有洪涝检测结果。"""
    result = await db.execute(
        select(FloodDetectionORM)
        .options(
            selectinload(FloodDetectionORM.pre_scene).selectinload(SARSceneGeoORM.radar_data),
            selectinload(FloodDetectionORM.post_scene).selectinload(SARSceneGeoORM.radar_data),
        )
        .order_by(FloodDetectionORM.id.desc())
    )
    dets = result.scalars().all()

    items = []
    for det in dets:
        pre_rd = det.pre_scene.radar_data if det.pre_scene else None
        post_rd = det.post_scene.radar_data if det.post_scene else None
        items.append({
            "id": det.id,
            "pre_scene_id": det.pre_scene_id,
            "post_scene_id": det.post_scene_id,
            "pre_imaging_date": pre_rd.imaging_date if pre_rd else None,
            "post_imaging_date": post_rd.imaging_date if post_rd else None,
            "pre_satellite": pre_rd.satellite if pre_rd else None,
            "post_satellite": post_rd.satellite if post_rd else None,
            "pre_geo_path": det.pre_scene.geo_path if det.pre_scene else None,
            "post_geo_path": det.post_scene.geo_path if det.post_scene else None,
            "classified_path": det.classified_path,
            "flood_area_km2": det.flood_area_km2,
            "stable_water_area_km2": det.stable_water_area_km2,
            "status": det.status,
            "error_msg": det.error_msg,
            "created_at": det.created_at.isoformat() if det.created_at else None,
            "updated_at": det.updated_at.isoformat() if det.updated_at else None,
        })
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Flood event map preview helpers
# ---------------------------------------------------------------------------

def _open_envi_rasterio(path: str):
    """打开 ENVI 格式栅格（路径可能无扩展名）。优先尝试裸路径，再尝试加 .bin/.img。"""
    import rasterio
    # 统一用正斜杠（Windows rasterio 也接受）
    path = path.replace("\\", "/")
    # 如果路径本身可以直接打开（ENVI 无扩展名文件）
    try:
        return rasterio.open(path)
    except Exception:
        pass
    # 尝试常见扩展名
    for ext in (".bin", ".img", ".tif", ".tiff"):
        try:
            return rasterio.open(path + ext)
        except Exception:
            pass
    raise FileNotFoundError(f"无法打开栅格文件: {path}")


def _raster_to_png_bytes(path: str, colormap: dict) -> tuple[bytes, list]:
    """将单波段分类栅格渲染为 RGBA PNG，返回 (png_bytes, [min_lat, min_lon, max_lat, max_lon])。"""
    import numpy as np
    from PIL import Image
    import io

    with _open_envi_rasterio(path) as ds:
        data = ds.read(1)
        bounds = ds.bounds
        geo_bounds = [bounds.bottom, bounds.left, bounds.top, bounds.right]

    h, w = data.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    for val, color in colormap.items():
        mask = data == val
        rgba[mask] = color
    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), geo_bounds


def _geo_raster_to_png_bytes(path: str) -> tuple[bytes, list]:
    """将地理编码后的 SAR 强度图渲染为灰度 PNG（百分位拉伸）。"""
    import numpy as np
    from PIL import Image
    import io

    with _open_envi_rasterio(path) as ds:
        data = ds.read(1).astype(np.float32)
        nodata = ds.nodata
        bounds = ds.bounds
        geo_bounds = [bounds.bottom, bounds.left, bounds.top, bounds.right]

    # 构建 nodata 掩膜：优先用 ds.nodata，否则用 nan/inf
    if nodata is not None:
        nodata_mask = (data == nodata) | ~np.isfinite(data)
    else:
        nodata_mask = ~np.isfinite(data)

    valid = data[~nodata_mask]
    if valid.size == 0:
        data_norm = np.zeros_like(data, dtype=np.uint8)
    else:
        p2, p98 = np.percentile(valid, 2), np.percentile(valid, 98)
        clipped = np.clip(data, p2, p98)
        data_norm = ((clipped - p2) / max(p98 - p2, 1e-9) * 255).astype(np.uint8)

    # 组装 RGBA：nodata 区域 alpha=0，其余 alpha=200
    rgba = np.stack([data_norm, data_norm, data_norm, np.full_like(data_norm, 200)], axis=-1)
    rgba[nodata_mask, 3] = 0
    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), geo_bounds


# 分类结果色表：值 → RGBA
_FLOOD_COLORMAP = {
    1: (24, 144, 255, 200),    # 稳定水体 — 蓝
    2: (255, 77, 79, 220),     # 洪涝     — 红
    3: (250, 173, 20, 180),    # 高散射   — 黄
    4: (80, 80, 80, 80),       # 非水体   — 灰（半透明）
}


@router.get("/water/flood-events/{event_id}/preview/classified")
async def flood_event_classified_preview(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """返回洪涝分类结果的 PNG 预览图 + 地理范围（JSON）。"""
    from fastapi.responses import JSONResponse
    import base64

    det = await db.get(FloodDetectionORM, event_id)
    if not det or not det.classified_path:
        raise HTTPException(status_code=404, detail="分类结果文件不存在")
    path = det.classified_path.replace("\\", "/")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="请求的文件不存在")
    try:
        import asyncio
        png_bytes, geo_bounds = await asyncio.to_thread(
            _raster_to_png_bytes, path, _FLOOD_COLORMAP
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"渲染失败: {e}")
    return JSONResponse({
        "image_b64": base64.b64encode(png_bytes).decode(),
        "bounds": geo_bounds,   # [min_lat, min_lon, max_lat, max_lon]
        "legend": {
            "稳定水体": "#1890ff",
            "洪涝": "#ff4d4f",
            "高散射": "#faad14",
            "非水体": "#505050",
        },
    })


@router.get("/water/flood-events/{event_id}/preview/pre")
async def flood_event_pre_preview(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """返回灾前地理编码影像的灰度 PNG 预览 + 地理范围。"""
    from fastapi.responses import JSONResponse
    import base64

    det = await db.get(FloodDetectionORM, event_id)
    if not det:
        raise HTTPException(status_code=404, detail="记录不存在")
    pre = await db.get(SARSceneGeoORM, det.pre_scene_id)
    if not pre or not pre.geo_path:
        raise HTTPException(status_code=404, detail="灾前场景路径不存在")
    path = pre.geo_path.replace("\\", "/")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="请求的文件不存在")
    try:
        import asyncio
        png_bytes, geo_bounds = await asyncio.to_thread(_geo_raster_to_png_bytes, path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"渲染失败: {e}")
    return JSONResponse({
        "image_b64": base64.b64encode(png_bytes).decode(),
        "bounds": geo_bounds,
    })


@router.get("/water/flood-events/{event_id}/preview/post")
async def flood_event_post_preview(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """返回灾后地理编码影像的灰度 PNG 预览 + 地理范围。"""
    from fastapi.responses import JSONResponse
    import base64

    det = await db.get(FloodDetectionORM, event_id)
    if not det:
        raise HTTPException(status_code=404, detail="记录不存在")
    post = await db.get(SARSceneGeoORM, det.post_scene_id)
    if not post or not post.geo_path:
        raise HTTPException(status_code=404, detail="灾后场景路径不存在")
    path = post.geo_path.replace("\\", "/")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="请求的文件不存在")
    try:
        import asyncio
        png_bytes, geo_bounds = await asyncio.to_thread(_geo_raster_to_png_bytes, path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"渲染失败: {e}")
    return JSONResponse({
        "image_b64": base64.b64encode(png_bytes).decode(),
        "bounds": geo_bounds,
    })


@router.post("/water/find-pairs")
async def find_water_pairs(
    req: WaterPairRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """按时间范围查找满足重叠条件的灾前/灾后场景配对。"""
    # 查询灾前候选（status=DONE，日期在范围内）
    pre_filters = [SARSceneGeoORM.status == "DONE"]
    if req.pre_start:
        pre_filters.append(RadarDataORM.imaging_date >= req.pre_start)
    if req.pre_end:
        pre_filters.append(RadarDataORM.imaging_date <= req.pre_end)
    pre_result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*pre_filters)
    )
    pre_scenes = pre_result.all()

    # 查询灾后候选
    post_filters = [SARSceneGeoORM.status == "DONE"]
    if req.post_start:
        post_filters.append(RadarDataORM.imaging_date >= req.post_start)
    if req.post_end:
        post_filters.append(RadarDataORM.imaging_date <= req.post_end)
    post_result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*post_filters)
    )
    post_scenes = post_result.all()

    # 计算所有候选配对的重叠率
    candidates = []
    for pre_scene, pre_radar in pre_scenes:
        for post_scene, post_radar in post_scenes:
            # 同一景不能自配
            if pre_scene.id == post_scene.id:
                continue
            ratio = 0.0
            if pre_radar.coverage_polygon and post_radar.coverage_polygon:
                ratio = _overlap_ratio(pre_radar.coverage_polygon, post_radar.coverage_polygon)
            if ratio < req.overlap_threshold:
                continue
            try:
                pre_date = datetime.strptime(pre_radar.imaging_date, "%Y%m%d")
                post_date = datetime.strptime(post_radar.imaging_date, "%Y%m%d")
                time_diff = abs((post_date - pre_date).days)
            except Exception:
                time_diff = None
            candidates.append({
                "pre": {
                    "id": pre_scene.id,
                    "imaging_date": pre_radar.imaging_date,
                    "satellite": pre_radar.satellite,
                    "geo_path": pre_scene.geo_path,
                },
                "post": {
                    "id": post_scene.id,
                    "imaging_date": post_radar.imaging_date,
                    "satellite": post_radar.satellite,
                    "geo_path": post_scene.geo_path,
                },
                "overlap_ratio": round(ratio, 4),
                "time_diff_days": time_diff,
            })

    # 按重叠率降序，贪心去重：每个 pre/post 场景只出现一次
    candidates.sort(key=lambda x: x["overlap_ratio"], reverse=True)
    used_pre, used_post = set(), set()
    pairs = []
    for c in candidates:
        pid, qid = c["pre"]["id"], c["post"]["id"]
        if pid in used_pre or qid in used_post:
            continue
        used_pre.add(pid)
        used_post.add(qid)
        pairs.append(c)

    pairs.sort(key=lambda x: x["overlap_ratio"], reverse=True)
    return {"pairs": pairs, "total": len(pairs)}


_UID_RE = _re.compile(r"_(\d{7,})$")


@router.post("/water/sync-from-disk")
async def sync_water_scenes_from_disk(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_require_admin),
):
    """扫描 WATER_RESULTS_DIR，把有 geo_db 但未入库的场景补录为 DONE。"""
    water_dir = settings.WATER_RESULTS_DIR
    if not os.path.isdir(water_dir):
        raise HTTPException(status_code=400, detail=f"WATER_RESULTS_DIR 不存在: {water_dir}")

    # product_unique_id -> radar_data_id
    uid_rows = await db.execute(
        select(RadarDataORM.product_unique_id, RadarDataORM.id)
        .where(RadarDataORM.product_unique_id.isnot(None))
    )
    uid_to_radar_id: Dict[str, int] = {uid: rid for uid, rid in uid_rows.all() if uid}

    # 已有 DONE 记录的 radar_data_id
    done_rows = await db.execute(
        select(SARSceneGeoORM.radar_data_id).where(SARSceneGeoORM.status == "DONE")
    )
    done_set = {rid for (rid,) in done_rows.all()}

    inserted = 0
    skipped_no_geo_db = 0
    skipped_no_match = 0
    skipped_already_done = 0

    for entry in os.scandir(water_dir):
        if not entry.is_dir() or not entry.name.startswith("scene_"):
            continue

        # 找 *_geo_db 文件
        geo_db_path = None
        for f in os.scandir(entry.path):
            if f.name.endswith("_geo_db") and not f.name.endswith(".hdr") and not f.name.endswith(".sml"):
                geo_db_path = f.path
                break
        if not geo_db_path:
            skipped_no_geo_db += 1
            continue

        m = _UID_RE.search(entry.name)
        if not m:
            skipped_no_match += 1
            continue
        raw_uid = m.group(1)
        stripped_uid = raw_uid.lstrip("0") or raw_uid
        radar_id = uid_to_radar_id.get(raw_uid) or uid_to_radar_id.get(stripped_uid)
        if not radar_id:
            skipped_no_match += 1
            continue

        if radar_id in done_set:
            skipped_already_done += 1
            continue

        # 检查是否已有非 DONE 记录（upsert）
        existing = await db.execute(
            select(SARSceneGeoORM).where(SARSceneGeoORM.radar_data_id == radar_id)
        )
        scene = existing.scalar_one_or_none()
        if scene:
            scene.status = "DONE"
            scene.geo_path = geo_db_path
        else:
            scene = SARSceneGeoORM(
                radar_data_id=radar_id,
                status="DONE",
                geo_path=geo_db_path,
            )
            db.add(scene)
        inserted += 1

    await db.commit()
    return {
        "inserted": inserted,
        "skipped_no_geo_db": skipped_no_geo_db,
        "skipped_no_match": skipped_no_match,
        "skipped_already_done": skipped_already_done,
    }


# ---------------------------------------------------------------------------
# Water body detection (Otsu + DEM + morphology)
# ---------------------------------------------------------------------------

class _WaterDetectReq(BaseModel):
    scene_id: Optional[int] = Field(default=None, description="SARSceneGeoORM 主键（可选）")
    input_path: Optional[str] = Field(default=None, description="直接指定 GeoTIFF 路径（可选）")


@router.post("/water/detect", status_code=202)
async def submit_water_detect(
    req: _WaterDetectReq,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """提交水体检测任务（Otsu 自适应阈值 + DEM/坡度约束 + 形态学 + 连通分量过滤）。"""
    input_path = req.input_path
    scene_id = req.scene_id

    if scene_id:
        scene = await db.get(SARSceneGeoORM, scene_id)
        if not scene:
            raise HTTPException(status_code=404, detail=f"SARSceneGeoORM id={scene_id} 不存在")
        if not scene.geo_path:
            raise HTTPException(status_code=400, detail="该场景尚未完成地理编码")
        # geo_path is ENVI format; try to find a GeoTIFF equivalent or use the path
        input_path = scene.geo_path

    if not input_path:
        raise HTTPException(status_code=400, detail="必须提供 scene_id 或 input_path")

    det = WaterDetectionORM(
        scene_id=scene_id,
        input_path=input_path,
        status="PENDING",
    )
    db.add(det)
    await db.flush()
    detection_id = det.id
    await db.commit()

    try:
        return await _queue_water_job(
            job_type=JOB_TYPE_WATER_DETECT,
            task_type=f"WATER_DETECT_{detection_id}",
            task_name=f"水体检测 id={detection_id}",
            payload={"detection_id": detection_id},
        )
    except HTTPException:
        async with db.begin():
            d = await db.get(WaterDetectionORM, detection_id)
            if d and d.status == "PENDING":
                d.status = "FAILED"
                d.error_msg = "任务入队失败"
        raise


@router.get("/water/detections")
async def list_water_detections(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """列出水体检测结果（分页）。"""
    from sqlalchemy import func

    q = select(func.count()).select_from(WaterDetectionORM)
    if status:
        q = q.where(WaterDetectionORM.status == status)
    total = (await db.execute(q)).scalar_one()

    q2 = select(WaterDetectionORM).order_by(WaterDetectionORM.id.desc()).limit(limit).offset(offset)
    if status:
        q2 = q2.where(WaterDetectionORM.status == status)
    rows = (await db.execute(q2)).scalars().all()

    items = []
    for det in rows:
        items.append({
            "id": det.id,
            "scene_id": det.scene_id,
            "input_path": det.input_path,
            "output_path": det.output_path,
            "water_area_km2": det.water_area_km2,
            "water_pixel_count": det.water_pixel_count,
            "otsu_threshold_db": det.otsu_threshold_db,
            "status": det.status,
            "error_msg": det.error_msg,
            "created_at": det.created_at.isoformat() if det.created_at else None,
            "updated_at": det.updated_at.isoformat() if det.updated_at else None,
        })
    return {"items": items, "total": total}


@router.get("/water/detections/{detection_id}")
async def get_water_detection(
    detection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """获取单个水体检测详情。"""
    det = await db.get(WaterDetectionORM, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail=f"检测记录 id={detection_id} 不存在")
    return {
        "id": det.id,
        "scene_id": det.scene_id,
        "input_path": det.input_path,
        "output_path": det.output_path,
        "water_area_km2": det.water_area_km2,
        "water_pixel_count": det.water_pixel_count,
        "otsu_threshold_db": det.otsu_threshold_db,
        "status": det.status,
        "error_msg": det.error_msg,
        "created_at": det.created_at.isoformat() if det.created_at else None,
        "updated_at": det.updated_at.isoformat() if det.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Water detection result preview (binary mask -> PNG)
# ---------------------------------------------------------------------------

@router.get("/water/detections/{detection_id}/preview")
async def get_water_detection_preview(
    detection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """返回水体检测结果的 PNG 预览及地理范围。"""
    det = await db.get(WaterDetectionORM, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail=f"检测记录 id={detection_id} 不存在")
    if not det.output_path or not os.path.isfile(det.output_path):
        raise HTTPException(status_code=404, detail="输出文件不存在")

    import rasterio
    import io
    from PIL import Image
    import numpy as np
    from fastapi.responses import JSONResponse

    with rasterio.open(det.output_path) as src:
        data = src.read(1)
        transform = src.transform
        h, w = data.shape
        min_lon = transform.c
        max_lon = transform.c + w * transform.a
        max_lat = transform.f
        min_lat = transform.f + h * transform.e

    # Create RGBA image: water=blue semi-transparent, non-water=transparent
    rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)
    water_mask = data > 0
    rgba[water_mask] = [24, 144, 255, 160]  # #1890ff with alpha

    img = Image.fromarray(rgba, "RGBA")
    # Downsample if large
    max_dim = 1024
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    import base64
    png_b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "png_base64": png_b64,
        "bounds": {
            "min_lon": min_lon, "min_lat": min_lat,
            "max_lon": max_lon, "max_lat": max_lat,
        },
    }


# ---------------------------------------------------------------------------
# GF3 L1A -> L2 processing
# ---------------------------------------------------------------------------

class _GF3ProcessReq(BaseModel):
    input_dir: str = Field(..., description="GF3 L1A 数据目录路径")
    resolution: float = Field(default=0.0002, ge=0.00001, le=0.01, description="输出分辨率（度）")


@router.post("/water/gf3-process", status_code=202)
async def submit_gf3_process(
    req: _GF3ProcessReq,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """提交 GF3 L1A→L2 处理任务（辐射定标 + RPC 几何校正）。"""
    if not os.path.exists(req.input_dir):
        raise HTTPException(status_code=400, detail=f"输入路径不存在: {req.input_dir}")

    output_dir = os.path.join(settings.GF3_STORAGE_DIRS, f"gf3_{os.path.basename(req.input_dir)}")

    proc = GF3ProcessingORM(
        input_dir=req.input_dir,
        output_dir=output_dir,
        resolution=req.resolution,
        status="PENDING",
    )
    db.add(proc)
    await db.flush()
    processing_id = proc.id
    await db.commit()

    try:
        return await _queue_water_job(
            job_type=JOB_TYPE_GF3_PROCESS,
            task_type=f"GF3_PROCESS_{processing_id}",
            task_name=f"GF3 处理 id={processing_id}",
            payload={"processing_id": processing_id},
        )
    except HTTPException:
        async with db.begin():
            p = await db.get(GF3ProcessingORM, processing_id)
            if p and p.status == "PENDING":
                p.status = "FAILED"
                p.error_msg = "任务入队失败"
        raise


@router.get("/water/gf3-results")
async def list_gf3_results(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """列出 GF3 处理结果（分页）。"""
    from sqlalchemy import func

    total = (await db.execute(
        select(func.count()).select_from(GF3ProcessingORM)
    )).scalar_one()

    rows = (await db.execute(
        select(GF3ProcessingORM).order_by(GF3ProcessingORM.id.desc()).limit(limit).offset(offset)
    )).scalars().all()

    items = []
    for proc in rows:
        items.append({
            "id": proc.id,
            "input_dir": proc.input_dir,
            "output_dir": proc.output_dir,
            "polarizations": proc.polarizations,
            "l2_paths": proc.l2_paths,
            "resolution": proc.resolution,
            "status": proc.status,
            "error_msg": proc.error_msg,
            "created_at": proc.created_at.isoformat() if proc.created_at else None,
            "updated_at": proc.updated_at.isoformat() if proc.updated_at else None,
        })
    return {"items": items, "total": total}


@router.get("/water/gf3-results/{result_id}")
async def get_gf3_result(
    result_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """获取单个 GF3 处理结果详情。"""
    proc = await db.get(GF3ProcessingORM, result_id)
    if not proc:
        raise HTTPException(status_code=404, detail=f"GF3 处理记录 id={result_id} 不存在")
    return {
        "id": proc.id,
        "input_dir": proc.input_dir,
        "output_dir": proc.output_dir,
        "polarizations": proc.polarizations,
        "l2_paths": proc.l2_paths,
        "resolution": proc.resolution,
        "status": proc.status,
        "error_msg": proc.error_msg,
        "created_at": proc.created_at.isoformat() if proc.created_at else None,
        "updated_at": proc.updated_at.isoformat() if proc.updated_at else None,
    }
