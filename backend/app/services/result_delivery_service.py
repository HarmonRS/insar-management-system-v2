from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import database
from ..config import settings
from ..models import (
    AuthUserORM,
    DinsarResultORM,
    RadarDataORM,
    ResultAssetORM,
    ResultDeliveryItemORM,
    ResultDeliveryRequestORM,
    ResultProductORM,
    SARSceneGeoORM,
)
from .dinsar_read_service import dinsar_read_service
from .job_queue_service import job_queue_service
from .task_service import task_service


JOB_TYPE_RESULT_DELIVERY_BUILD = "RESULT_DELIVERY_BUILD"
TASK_TYPE_RESULT_DELIVERY_BUILD = "RESULT_DELIVERY_BUILD"

DELIVERY_STATUS_PENDING = "PENDING"
DELIVERY_STATUS_RUNNING = "RUNNING"
DELIVERY_STATUS_READY = "READY"
DELIVERY_STATUS_FAILED = "FAILED"
DELIVERY_STATUS_CANCELLED = "CANCELLED"
DELIVERY_STATUS_EXPIRED = "EXPIRED"

ITEM_STATUS_PENDING = "PENDING"
ITEM_STATUS_COPIED = "COPIED"
ITEM_STATUS_FAILED = "FAILED"
ITEM_STATUS_SKIPPED = "SKIPPED"

CHANNEL_DINSAR = "dinsar"
CHANNEL_LT1_ORTHO = "lt1_ortho"
CHANNEL_GF3_ORTHO = "gf3_ortho"
CHANNEL_SBAS = "sbas"
CHANNEL_S1_ORTHO = "s1_ortho"
SUPPORTED_READY_CHANNELS = {CHANNEL_DINSAR, CHANNEL_LT1_ORTHO, CHANNEL_GF3_ORTHO}

LT1_ANALYSIS_ENGINE = "lt_gamma"
LT1_ANALYSIS_PROFILE = "lt1_gamma_geocoded_mli"
GF3_STANDARD_SOURCE_FORMAT = "GF3_SARSCAPE_L2"
GF3_NATIVE_PREVIEW_SOURCE_FORMAT = "GF3_SARSCAPE_NATIVE_PREVIEW"
GF3_DELIVERABLE_SOURCE_FORMATS = {GF3_STANDARD_SOURCE_FORMAT, GF3_NATIVE_PREVIEW_SOURCE_FORMAT}

PACKAGE_MODE_DIRECTORY = "directory"
PACKAGE_MODE_ZIP = "zip"

_ASSOCIATED_EXTENSIONS = ("", ".hdr", ".sml", ".xml", ".aux.xml", ".prj")
_INVALID_SEGMENT_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')
_SAFE_ID_RE = re.compile(r"[^0-9A-Za-z_-]+")


@dataclass(frozen=True)
class DeliverySource:
    display_name: str
    source_path: str
    product: Optional[ResultProductORM] = None
    compat_row: Optional[DinsarResultORM] = None
    source_asset_id: Optional[int] = None
    source_radar_data_id: Optional[int] = None
    source_scene_geo_id: Optional[int] = None
    product_id: Optional[str] = None
    task_name: Optional[str] = None
    source_kind: str = "result_product"


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_channel(value: str) -> str:
    channel = str(value or "").strip().lower()
    if not channel:
        raise ValueError("channel is required")
    return channel


def _normalize_package_mode(value: str) -> str:
    mode = str(value or PACKAGE_MODE_DIRECTORY).strip().lower()
    if mode not in {PACKAGE_MODE_DIRECTORY, PACKAGE_MODE_ZIP}:
        raise ValueError("package_mode must be directory or zip")
    return mode


def _normalize_int_ids(values: Optional[Iterable[Any]], *, max_count: int) -> List[int]:
    normalized: List[int] = []
    seen = set()
    for raw in values or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
        if len(normalized) > max_count:
            raise ValueError(f"selected item count exceeds max limit ({max_count})")
    return normalized


def _sanitize_segment(value: Any, *, default: str = "item", max_len: int = 120) -> str:
    text = str(value or "").strip()
    text = _INVALID_SEGMENT_RE.sub("_", text).strip(" .")
    text = _SAFE_ID_RE.sub("_", text)
    return (text or default)[:max_len]


def _same_file_size(left: str, right: str) -> bool:
    try:
        return os.path.getsize(left) == os.path.getsize(right)
    except OSError:
        return False


def _file_size(path: str) -> int:
    try:
        return int(os.path.getsize(path))
    except OSError:
        return 0


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)


def _path_within(parent: str, child: str) -> bool:
    parent_path = Path(parent).resolve()
    child_path = Path(child).resolve()
    try:
        child_path.relative_to(parent_path)
        return True
    except ValueError:
        return False


def _iter_associated_files(source_path: str) -> List[str]:
    source = os.path.normpath(source_path)
    if os.path.isdir(source):
        files: List[str] = []
        for root, _dirs, names in os.walk(source):
            for name in names:
                files.append(os.path.join(root, name))
        return sorted(files)
    if not os.path.isfile(source):
        return []

    src_dir = os.path.dirname(source)
    base_name = os.path.basename(source)
    files: List[str] = []
    for ext in _ASSOCIATED_EXTENSIONS:
        candidate = os.path.join(src_dir, base_name + ext) if ext else source
        if os.path.isfile(candidate) and candidate not in files:
            files.append(candidate)
    return files


def _delivery_root() -> str:
    root = str(settings.RESULT_DELIVERY_ROOT or "").strip()
    if not root:
        raise ValueError("RESULT_DELIVERY_ROOT is not configured")
    return os.path.normpath(os.path.abspath(root))


def _owner_dir(root: str, username: str) -> str:
    return os.path.join(root, _sanitize_segment(username, default="user", max_len=64))


def _make_delivery_id(username: str) -> str:
    stamp = _utcnow().strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha1(f"{username}|{stamp}|{os.urandom(8).hex()}".encode("utf-8")).hexdigest()[:10]
    return f"rd_{stamp}_{digest}"


def _resolve_source_path(product: ResultProductORM, assets: List[ResultAssetORM]) -> tuple[str, Optional[int]]:
    primary_assets = [
        asset for asset in assets
        if asset.exists_flag and (asset.is_primary or str(asset.asset_role or "").lower() in {"disp", "primary_geotiff"})
    ]
    primary_assets.sort(key=lambda item: (not item.is_primary, item.id))
    for asset in primary_assets:
        path = str(asset.absolute_path or "").strip()
        if path:
            return path, asset.id
    for path in (product.primary_asset_path, product.source_primary_path, product.publish_dir):
        text = str(path or "").strip()
        if text:
            return text, None
    return "", None


def _scene_product_id(scene: SARSceneGeoORM) -> str:
    return f"sar_scene_geo:{scene.id}"


def _scene_display_name(scene: SARSceneGeoORM, radar: RadarDataORM) -> str:
    for value in (
        radar.product_unique_id,
        radar.unique_id,
        radar.source_product_token,
        os.path.basename(str(radar.file_path or "").rstrip("/\\")),
        _scene_product_id(scene),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return f"scene_{scene.id}"


def _radar_display_name(radar: RadarDataORM) -> str:
    for value in (
        radar.product_unique_id,
        radar.unique_id,
        radar.source_product_token,
        os.path.basename(str(radar.file_path or "").rstrip("/\\")),
        f"radar_{radar.id}",
    ):
        text = str(value or "").strip()
        if text:
            return text
    return f"radar_{radar.id}"


def _manifest_path_for_scene(scene: SARSceneGeoORM) -> Optional[str]:
    metadata = scene.analysis_metadata_json if isinstance(scene.analysis_metadata_json, dict) else {}
    text = str(metadata.get("manifest_path") or "").strip()
    if text:
        return text
    if scene.analysis_dir:
        return os.path.join(scene.analysis_dir, "manifest.json")
    return None


def _quality_path_for_scene(scene: SARSceneGeoORM) -> Optional[str]:
    if scene.analysis_dir:
        return os.path.join(scene.analysis_dir, "quality.json")
    return None


def _catalog_file_size(path: Any) -> int:
    text = str(path or "").strip()
    return _file_size(text) if text else 0


class ResultDeliveryService:
    def channels(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": CHANNEL_DINSAR,
                "group": "InSAR 成果",
                "label": "D-InSAR 结果",
                "state": "ready",
                "state_text": "可交付",
                "description": "已登记 D-InSAR catalog，可创建后台交付包并下载到本地。",
            },
            {
                "key": CHANNEL_SBAS,
                "group": "InSAR 成果",
                "label": "SBAS-InSAR 结果",
                "state": "planned",
                "state_text": "目录可查",
                "description": "SBAS 结果 catalog 已有基础能力，本阶段暂不开放交付打包。",
            },
            {
                "key": CHANNEL_LT1_ORTHO,
                "group": "正射成果",
                "label": "LT-1 正射结果",
                "state": "ready",
                "state_text": "可交付",
                "description": "服务器生产的 LT-1 分析就绪正射 GeoTIFF 已接入交付，可打包下载到本地。",
            },
            {
                "key": CHANNEL_S1_ORTHO,
                "group": "正射成果",
                "label": "Sentinel-1 正射结果",
                "state": "placeholder",
                "state_text": "待接入",
                "description": "Sentinel-1 正射生产尚未接入，当前只保留交付通道占位。",
            },
            {
                "key": CHANNEL_GF3_ORTHO,
                "group": "正射成果",
                "label": "GF3 SARscape _geo",
                "state": "ready",
                "state_text": "可交付",
                "description": "已登记的 GF3 SARscape 标准化正射成品可直接创建交付包。",
            },
        ]

    async def _resolve_dinsar_sources(
        self,
        db: AsyncSession,
        *,
        product_ids: List[int],
        compat_result_ids: List[int],
    ) -> List[DeliverySource]:
        sources: List[DeliverySource] = []
        seen_products = set()

        if product_ids:
            result = await db.execute(
                select(ResultProductORM)
                .options(selectinload(ResultProductORM.assets))
                .where(
                    ResultProductORM.id.in_(product_ids),
                    ResultProductORM.catalog_name == CHANNEL_DINSAR,
                    ResultProductORM.status.in_(["READY", "PUBLISHED", "OK"]),
                )
                .order_by(ResultProductORM.id.asc())
            )
            for product in result.scalars().unique().all():
                assets = list(product.assets or [])
                source_path, source_asset_id = _resolve_source_path(product, assets)
                if not source_path:
                    continue
                seen_products.add(int(product.id))
                sources.append(
                    DeliverySource(
                        display_name=dinsar_read_service.get_display_name(product),
                        source_path=source_path,
                        product=product,
                        compat_row=None,
                        source_asset_id=source_asset_id,
                        product_id=product.product_id,
                        task_name=product.task_alias or product.task_name,
                        source_kind="dinsar_product",
                    )
                )

        if compat_result_ids:
            records = await dinsar_read_service.list_compat_records_by_ids(
                db,
                compat_result_ids=compat_result_ids,
            )
            product_lookup_ids = [record.product.id for record in records if record.product is not None]
            assets_by_product: Dict[int, List[ResultAssetORM]] = {}
            if product_lookup_ids:
                asset_result = await db.execute(
                    select(ResultAssetORM)
                    .where(ResultAssetORM.product_ref_id.in_(product_lookup_ids))
                    .order_by(ResultAssetORM.id.asc())
                )
                for asset in asset_result.scalars().all():
                    assets_by_product.setdefault(int(asset.product_ref_id), []).append(asset)

            for record in records:
                product = record.product
                if int(product.id) in seen_products:
                    continue
                assets = assets_by_product.get(int(product.id), [])
                source_path, source_asset_id = _resolve_source_path(product, assets)
                if not source_path and record.compat_row is not None:
                    source_path = str(record.compat_row.file_path or "").strip()
                if not source_path:
                    continue
                seen_products.add(int(product.id))
                sources.append(
                    DeliverySource(
                        display_name=record.display_name,
                        source_path=source_path,
                        product=product,
                        compat_row=record.compat_row,
                        source_asset_id=source_asset_id,
                        product_id=product.product_id,
                        task_name=product.task_alias or product.task_name,
                        source_kind="dinsar_product",
                    )
                )

        return sources

    async def _resolve_lt1_sources(
        self,
        db: AsyncSession,
        *,
        item_ids: List[int],
        product_ids: List[int],
    ) -> List[DeliverySource]:
        selected_ids = item_ids or product_ids
        if not selected_ids:
            return []
        result = await db.execute(
            select(SARSceneGeoORM, RadarDataORM)
            .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
            .where(
                SARSceneGeoORM.id.in_(selected_ids),
                SARSceneGeoORM.status == "DONE",
                SARSceneGeoORM.analysis_tif_path.isnot(None),
                SARSceneGeoORM.analysis_engine == LT1_ANALYSIS_ENGINE,
                SARSceneGeoORM.analysis_profile == LT1_ANALYSIS_PROFILE,
            )
            .order_by(SARSceneGeoORM.id.asc())
        )
        sources: List[DeliverySource] = []
        for scene, radar in result.all():
            analysis_dir = str(scene.analysis_dir or "").strip()
            analysis_tif = str(scene.analysis_tif_path or "").strip()
            source_path = analysis_dir if analysis_dir and os.path.isdir(analysis_dir) else analysis_tif
            if not source_path:
                continue
            product_id = _scene_product_id(scene)
            display_name = _scene_display_name(scene, radar)
            sources.append(
                DeliverySource(
                    display_name=display_name,
                    source_path=source_path,
                    source_radar_data_id=int(radar.id) if radar.id is not None else None,
                    source_scene_geo_id=int(scene.id) if scene.id is not None else None,
                    product_id=product_id,
                    task_name=display_name,
                    source_kind="lt1_scene_geo",
                )
            )
        return sources

    async def _resolve_gf3_sources(
        self,
        db: AsyncSession,
        *,
        item_ids: List[int],
    ) -> List[DeliverySource]:
        if not item_ids:
            return []
        result = await db.execute(
            select(RadarDataORM, SARSceneGeoORM)
            .outerjoin(SARSceneGeoORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
            .where(
                RadarDataORM.id.in_(item_ids),
                RadarDataORM.source_format.in_(GF3_DELIVERABLE_SOURCE_FORMATS),
                RadarDataORM.geocoded_flag.is_(True),
            )
            .order_by(RadarDataORM.id.asc())
        )
        sources: List[DeliverySource] = []
        for radar, scene in result.all():
            source_path = ""
            source_scene_geo_id = None
            if scene is not None and scene.analysis_tif_path:
                analysis_dir = str(scene.analysis_dir or "").strip()
                analysis_tif = str(scene.analysis_tif_path or "").strip()
                source_path = analysis_dir if analysis_dir and os.path.isdir(analysis_dir) else analysis_tif
                source_scene_geo_id = int(scene.id) if scene.id is not None else None
            if not source_path:
                source_path = str(radar.file_path or "").strip()
            if not source_path:
                continue
            display_name = _radar_display_name(radar)
            sources.append(
                DeliverySource(
                    display_name=display_name,
                    source_path=source_path,
                    source_radar_data_id=int(radar.id) if radar.id is not None else None,
                    source_scene_geo_id=source_scene_geo_id,
                    product_id=f"gf3_sarscape:{radar.id}",
                    task_name=display_name,
                    source_kind="gf3_radar_data",
                )
            )
        return sources

    async def _resolve_sources_for_channel(
        self,
        db: AsyncSession,
        *,
        channel: str,
        product_ids: List[int],
        compat_result_ids: List[int],
        item_ids: List[int],
    ) -> List[DeliverySource]:
        if channel == CHANNEL_DINSAR:
            return await self._resolve_dinsar_sources(
                db,
                product_ids=product_ids,
                compat_result_ids=compat_result_ids,
            )
        if channel == CHANNEL_LT1_ORTHO:
            return await self._resolve_lt1_sources(db, item_ids=item_ids, product_ids=product_ids)
        if channel == CHANNEL_GF3_ORTHO:
            return await self._resolve_gf3_sources(db, item_ids=item_ids or product_ids)
        return []

    async def list_channel_catalog(
        self,
        db: AsyncSession,
        *,
        channel: str,
        limit: int = 100,
        offset: int = 0,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        channel = _normalize_channel(channel)
        safe_limit = min(500, max(1, int(limit or 100)))
        safe_offset = max(0, int(offset or 0))
        query_text = str(query or "").strip()

        if channel == CHANNEL_LT1_ORTHO:
            filters = [
                SARSceneGeoORM.status == "DONE",
                SARSceneGeoORM.analysis_tif_path.isnot(None),
                SARSceneGeoORM.analysis_engine == LT1_ANALYSIS_ENGINE,
                SARSceneGeoORM.analysis_profile == LT1_ANALYSIS_PROFILE,
            ]
            if query_text:
                like = f"%{query_text}%"
                filters.append(
                    or_(
                        RadarDataORM.product_unique_id.ilike(like),
                        RadarDataORM.unique_id.ilike(like),
                        RadarDataORM.file_path.ilike(like),
                        RadarDataORM.imaging_date.ilike(like),
                    )
                )
            total_result = await db.execute(
                select(func.count(SARSceneGeoORM.id))
                .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
                .where(*filters)
            )
            rows_result = await db.execute(
                select(SARSceneGeoORM, RadarDataORM)
                .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
                .where(*filters)
                .order_by(SARSceneGeoORM.updated_at.desc().nullslast(), SARSceneGeoORM.id.desc())
                .limit(safe_limit)
                .offset(safe_offset)
            )
            items = []
            for scene, radar in rows_result.all():
                display_name = _scene_display_name(scene, radar)
                manifest_path = _manifest_path_for_scene(scene)
                quality_path = _quality_path_for_scene(scene)
                items.append(
                    {
                        "id": scene.id,
                        "item_id": scene.id,
                        "source_kind": "lt1_scene_geo",
                        "product_id": _scene_product_id(scene),
                        "catalog_name": "sar_scene_geo",
                        "product_family": "lt1_analysis_ready_geotiff",
                        "product_type": "analysis_ready_geotiff",
                        "display_name": display_name,
                        "task_name": display_name,
                        "status": scene.status,
                        "health_status": "OK",
                        "engine_code": scene.analysis_engine,
                        "profile_code": scene.analysis_profile,
                        "radar_data_id": radar.id,
                        "scene_geo_id": scene.id,
                        "imaging_date": radar.imaging_date,
                        "polarization": radar.polarization,
                        "pixel_size_m": scene.pixel_size_m,
                        "backscatter_unit": scene.analysis_backscatter_unit,
                        "publish_dir": scene.analysis_dir,
                        "manifest_path": manifest_path,
                        "quality_path": quality_path,
                        "primary_asset_path": scene.analysis_tif_path,
                        "file_size": _catalog_file_size(scene.analysis_tif_path),
                        "summary": {
                            "radar_data_id": radar.id,
                            "source_asset_ids": [radar.source_product_ref_id] if radar.source_product_ref_id else [],
                            "imaging_date": radar.imaging_date,
                            "polarization": radar.polarization,
                            "pixel_size_m": scene.pixel_size_m,
                        },
                        "produced_at": scene.updated_at.isoformat() if scene.updated_at else None,
                        "published_at": scene.updated_at.isoformat() if scene.updated_at else None,
                    }
                )
            return {
                "items": items,
                "total": int(total_result.scalar_one() or 0),
                "limit": safe_limit,
                "offset": safe_offset,
            }

        if channel == CHANNEL_GF3_ORTHO:
            filters = [
                RadarDataORM.source_format.in_(GF3_DELIVERABLE_SOURCE_FORMATS),
                RadarDataORM.geocoded_flag.is_(True),
            ]
            if query_text:
                like = f"%{query_text}%"
                filters.append(
                    or_(
                        RadarDataORM.product_unique_id.ilike(like),
                        RadarDataORM.unique_id.ilike(like),
                        RadarDataORM.file_path.ilike(like),
                        RadarDataORM.imaging_date.ilike(like),
                    )
                )
            total_result = await db.execute(select(func.count(RadarDataORM.id)).where(*filters))
            rows_result = await db.execute(
                select(RadarDataORM, SARSceneGeoORM)
                .outerjoin(SARSceneGeoORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
                .where(*filters)
                .order_by(RadarDataORM.acquisition_start_time_utc.desc().nullslast(), RadarDataORM.id.desc())
                .limit(safe_limit)
                .offset(safe_offset)
            )
            items = []
            for radar, scene in rows_result.all():
                display_name = _radar_display_name(radar)
                primary_path = (
                    str(scene.analysis_tif_path or "").strip()
                    if scene is not None and scene.analysis_tif_path
                    else str(radar.file_path or "").strip()
                )
                publish_dir = (
                    str(scene.analysis_dir or "").strip()
                    if scene is not None and scene.analysis_dir
                    else str(radar.file_path or "").strip()
                )
                items.append(
                    {
                        "id": radar.id,
                        "item_id": radar.id,
                        "source_kind": "gf3_radar_data",
                        "product_id": f"gf3_sarscape:{radar.id}",
                        "catalog_name": "radar_data",
                        "product_family": "gf3_ortho",
                        "product_type": "sarscape_l2_geotiff" if radar.source_format == GF3_STANDARD_SOURCE_FORMAT else "sarscape_native_geo",
                        "display_name": display_name,
                        "task_name": display_name,
                        "status": "READY",
                        "health_status": "OK",
                        "engine_code": (scene.analysis_engine if scene is not None else None) or "gf3_sarscape",
                        "profile_code": (
                            (scene.analysis_profile if scene is not None else None)
                            or ("gf3_standard_geotiff" if radar.source_format == GF3_STANDARD_SOURCE_FORMAT else "gf3_native_geo")
                        ),
                        "source_format": radar.source_format,
                        "radar_data_id": radar.id,
                        "scene_geo_id": scene.id if scene is not None else None,
                        "imaging_date": radar.imaging_date,
                        "polarization": radar.polarization,
                        "pixel_size_m": scene.pixel_size_m if scene is not None else None,
                        "backscatter_unit": scene.analysis_backscatter_unit if scene is not None else None,
                        "publish_dir": publish_dir,
                        "manifest_path": _manifest_path_for_scene(scene) if scene is not None else None,
                        "primary_asset_path": primary_path,
                        "file_size": _catalog_file_size(primary_path),
                        "summary": {
                            "radar_data_id": radar.id,
                            "source_asset_ids": [radar.source_product_ref_id] if radar.source_product_ref_id else [],
                            "imaging_date": radar.imaging_date,
                            "polarization": radar.polarization,
                        },
                        "produced_at": (
                            scene.updated_at.isoformat() if scene is not None and scene.updated_at else None
                        ),
                        "published_at": (
                            scene.updated_at.isoformat() if scene is not None and scene.updated_at else None
                        ),
                    }
                )
            return {
                "items": items,
                "total": int(total_result.scalar_one() or 0),
                "limit": safe_limit,
                "offset": safe_offset,
            }

        if channel == CHANNEL_DINSAR:
            total = await dinsar_read_service.count_compat_records(db)
            records = await dinsar_read_service.list_compat_records(db, limit=safe_limit, offset=safe_offset)
            items = []
            for record in records:
                product = record.product
                compat_row = record.compat_row
                items.append(
                    {
                        "id": int(compat_row.id if compat_row is not None else product.id),
                        "item_id": int(compat_row.id if compat_row is not None else product.id),
                        "source_kind": "dinsar_result",
                        "product_id": product.product_id,
                        "catalog_name": product.catalog_name,
                        "product_family": product.product_family,
                        "product_type": product.product_type,
                        "display_name": record.display_name,
                        "task_name": product.task_alias or product.task_name,
                        "status": product.status,
                        "health_status": product.health_status,
                        "engine_code": product.engine_code,
                        "profile_code": product.profile_code,
                        "pair_key": product.pair_key,
                        "publish_dir": product.publish_dir,
                        "manifest_path": product.manifest_path,
                        "primary_asset_path": product.primary_asset_path or product.source_primary_path,
                        "file_size": _catalog_file_size(product.primary_asset_path or product.source_primary_path),
                        "produced_at": product.produced_at.isoformat() if product.produced_at else None,
                        "published_at": product.published_at.isoformat() if product.published_at else None,
                    }
                )
            return {"items": items, "total": total, "limit": safe_limit, "offset": safe_offset}

        return {"items": [], "total": 0, "limit": safe_limit, "offset": safe_offset}

    async def create_delivery(
        self,
        db: AsyncSession,
        *,
        user: AuthUserORM,
        channel: str,
        product_ids: Optional[List[int]] = None,
        compat_result_ids: Optional[List[int]] = None,
        item_ids: Optional[List[int]] = None,
        package_mode: str = PACKAGE_MODE_DIRECTORY,
        include_checksums: Optional[bool] = None,
    ) -> ResultDeliveryRequestORM:
        channel = _normalize_channel(channel)
        if channel not in SUPPORTED_READY_CHANNELS:
            raise ValueError(f"{channel} delivery is not connected yet")

        package_mode = _normalize_package_mode(package_mode)
        max_items = max(1, int(settings.RESULT_DELIVERY_MAX_ITEMS or 500))
        normalized_product_ids = _normalize_int_ids(product_ids, max_count=max_items)
        normalized_compat_ids = _normalize_int_ids(compat_result_ids, max_count=max_items)
        normalized_item_ids = _normalize_int_ids(item_ids, max_count=max_items)
        if not normalized_product_ids and not normalized_compat_ids and not normalized_item_ids:
            raise ValueError("select at least one result")

        if len(normalized_product_ids) + len(normalized_compat_ids) + len(normalized_item_ids) > max_items:
            raise ValueError(f"selected item count exceeds max limit ({max_items})")

        sources = await self._resolve_sources_for_channel(
            db,
            channel=channel,
            product_ids=normalized_product_ids,
            compat_result_ids=normalized_compat_ids,
            item_ids=normalized_item_ids,
        )

        if not sources:
            raise ValueError("selected results do not have deliverable files")
        if len(sources) > max_items:
            raise ValueError(f"resolved result count exceeds max limit ({max_items})")

        delivery_root = _delivery_root()
        delivery_id = _make_delivery_id(str(user.username or "user"))
        delivery_dir = os.path.join(_owner_dir(delivery_root, str(user.username or "user")), delivery_id)
        expires_at = _utcnow() + timedelta(days=max(1, int(settings.RESULT_DELIVERY_RETENTION_DAYS or 7)))
        request_json = {
            "channel": channel,
            "product_ids": normalized_product_ids,
            "compat_result_ids": normalized_compat_ids,
            "item_ids": normalized_item_ids,
            "package_mode": package_mode,
            "include_checksums": (
                bool(settings.RESULT_DELIVERY_CHECKSUM_ENABLED)
                if include_checksums is None
                else bool(include_checksums)
            ),
        }
        delivery = ResultDeliveryRequestORM(
            delivery_id=delivery_id,
            owner_user_id=user.id,
            owner_username=str(user.username or "unknown"),
            channel=channel,
            status=DELIVERY_STATUS_PENDING,
            package_mode=package_mode,
            item_count=len(sources),
            total_bytes=0,
            copied_bytes=0,
            delivery_root=delivery_root,
            delivery_dir=delivery_dir,
            zip_path=(f"{delivery_dir}.zip" if package_mode == PACKAGE_MODE_ZIP else None),
            expires_at=expires_at,
            request_json=request_json,
            summary_json={
                "source_count": len(sources),
                "created_message": "Delivery package queued.",
            },
        )
        db.add(delivery)
        await db.flush()

        task_type = f"{TASK_TYPE_RESULT_DELIVERY_BUILD}_{delivery_id}"
        task_id = await task_service.create_task(
            task_type,
            f"成果交付包生成: {delivery_id}",
            params={
                "delivery_id": delivery_id,
                "channel": channel,
                "item_count": len(sources),
                "package_mode": package_mode,
            },
            db=db,
        )
        job_id = await job_queue_service.create_job(
            JOB_TYPE_RESULT_DELIVERY_BUILD,
            payload={"delivery_id": delivery_id},
            task_id=task_id,
            max_attempts=1,
            db=db,
        )
        delivery.task_id = task_id
        delivery.job_id = job_id
        await db.flush()
        return delivery

    async def list_deliveries(
        self,
        db: AsyncSession,
        *,
        user: AuthUserORM,
        include_all: bool = False,
        include_items: bool = False,
        item_limit: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = min(200, max(1, int(limit or 50)))
        safe_offset = max(0, int(offset or 0))
        stmt = select(ResultDeliveryRequestORM)
        if include_items:
            stmt = stmt.options(selectinload(ResultDeliveryRequestORM.items))
        count_stmt = select(func.count(ResultDeliveryRequestORM.id))
        if not include_all:
            owner_filter = ResultDeliveryRequestORM.owner_user_id == user.id
            stmt = stmt.where(owner_filter)
            count_stmt = count_stmt.where(owner_filter)
        stmt = (
            stmt.order_by(ResultDeliveryRequestORM.created_at.desc(), ResultDeliveryRequestORM.id.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        result = await db.execute(stmt)
        total_result = await db.execute(count_stmt)
        deliveries = result.scalars().unique().all() if include_items else result.scalars().all()
        items = [
            self.serialize_delivery(item, include_items=include_items, item_limit=item_limit)
            for item in deliveries
        ]
        total = int(total_result.scalar_one() or 0)
        return {
            "items": items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < total,
        }

    async def get_delivery(
        self,
        db: AsyncSession,
        *,
        delivery_id: str,
        user: AuthUserORM,
        include_items: bool = True,
    ) -> Optional[ResultDeliveryRequestORM]:
        stmt = select(ResultDeliveryRequestORM).where(
            ResultDeliveryRequestORM.delivery_id == str(delivery_id or "").strip()
        )
        if include_items:
            stmt = stmt.options(selectinload(ResultDeliveryRequestORM.items))
        if str(user.role or "").lower() != "admin":
            stmt = stmt.where(ResultDeliveryRequestORM.owner_user_id == user.id)
        result = await db.execute(stmt)
        return result.scalars().unique().one_or_none()

    def serialize_delivery(
        self,
        delivery: ResultDeliveryRequestORM,
        *,
        include_items: bool = False,
        item_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        items = list(delivery.items or []) if include_items else []
        if item_limit is not None:
            items = items[: max(0, int(item_limit))]
        summary = delivery.summary_json if isinstance(delivery.summary_json, dict) else {}
        payload = {
            "id": delivery.id,
            "delivery_id": delivery.delivery_id,
            "owner_user_id": delivery.owner_user_id,
            "owner_username": delivery.owner_username,
            "channel": delivery.channel,
            "status": delivery.status,
            "package_mode": delivery.package_mode,
            "item_count": delivery.item_count,
            "total_bytes": delivery.total_bytes,
            "copied_bytes": delivery.copied_bytes,
            "delivery_dir": delivery.delivery_dir,
            "zip_path": delivery.zip_path,
            "manifest_path": delivery.manifest_path,
            "expires_at": delivery.expires_at,
            "task_id": delivery.task_id,
            "job_id": delivery.job_id,
            "error_message": delivery.error_message,
            "summary": summary,
            "created_at": delivery.created_at,
            "updated_at": delivery.updated_at,
            "started_at": delivery.started_at,
            "completed_at": delivery.completed_at,
            "download_urls": {
                "manifest": f"/api/result-deliveries/{delivery.delivery_id}/manifest",
                "archive": (
                    f"/api/result-deliveries/{delivery.delivery_id}/archive/download"
                    if delivery.zip_path
                    else None
                ),
            },
        }
        if include_items:
            payload["items"] = [self.serialize_item(item, delivery_id=delivery.delivery_id) for item in items]
        return payload

    def serialize_item(self, item: ResultDeliveryItemORM, *, delivery_id: str) -> Dict[str, Any]:
        return {
            "id": item.id,
            "delivery_id": item.delivery_id,
            "source_product_id": item.source_product_id,
            "source_result_id": item.source_result_id,
            "source_asset_id": item.source_asset_id,
            "source_radar_data_id": item.source_radar_data_id,
            "source_scene_geo_id": item.source_scene_geo_id,
            "display_name": item.display_name,
            "relative_path": item.relative_path,
            "file_size": item.file_size,
            "checksum_sha256": item.checksum_sha256,
            "status": item.status,
            "error_message": item.error_message,
            "download_url": (
                f"/api/result-deliveries/{delivery_id}/files/{item.id}/download"
                if item.status == ITEM_STATUS_COPIED
                else None
            ),
        }

    async def build_delivery(self, delivery_id: str) -> Dict[str, Any]:
        async with _new_session() as db:
            result = await db.execute(
                select(ResultDeliveryRequestORM).where(ResultDeliveryRequestORM.delivery_id == delivery_id)
            )
            delivery = result.scalar_one_or_none()
            if delivery is None:
                raise ValueError(f"delivery not found: {delivery_id}")
            if delivery.status in {DELIVERY_STATUS_READY, DELIVERY_STATUS_CANCELLED, DELIVERY_STATUS_EXPIRED}:
                return self.serialize_delivery(delivery)

            delivery.status = DELIVERY_STATUS_RUNNING
            delivery.started_at = _utcnow()
            delivery.error_message = None
            await db.commit()

        try:
            summary = await self._build_delivery_files(delivery_id)
        except Exception as exc:
            async with _new_session() as db:
                result = await db.execute(
                    select(ResultDeliveryRequestORM).where(ResultDeliveryRequestORM.delivery_id == delivery_id)
                )
                delivery = result.scalar_one_or_none()
                if delivery is not None:
                    delivery.status = DELIVERY_STATUS_FAILED
                    delivery.error_message = str(exc)
                    delivery.completed_at = _utcnow()
                    delivery.summary_json = {
                        **(delivery.summary_json if isinstance(delivery.summary_json, dict) else {}),
                        "error": str(exc),
                    }
                    await db.commit()
                    if delivery.task_id:
                        await task_service.update_task(delivery.task_id, status="FAILED", progress=100, message=str(exc))
            raise

        async with _new_session() as db:
            result = await db.execute(
                select(ResultDeliveryRequestORM)
                .options(selectinload(ResultDeliveryRequestORM.items))
                .where(ResultDeliveryRequestORM.delivery_id == delivery_id)
            )
            delivery = result.scalars().unique().one_or_none()
            if delivery is None:
                raise ValueError(f"delivery not found: {delivery_id}")
            available_files = int(summary.get("copied_files", 0) or 0) + int(summary.get("skipped_files", 0) or 0)
            delivery.status = DELIVERY_STATUS_READY if available_files > 0 else DELIVERY_STATUS_FAILED
            delivery.completed_at = _utcnow()
            delivery.error_message = summary.get("error_message")
            delivery.summary_json = summary
            await db.commit()
            if delivery.task_id:
                if delivery.status == DELIVERY_STATUS_READY:
                    await task_service.update_task(
                        delivery.task_id,
                        status="COMPLETED",
                        progress=100,
                        message=(
                            f"成果交付包已生成: files={summary.get('copied_files', 0)}, "
                            f"bytes={summary.get('copied_bytes', 0)}"
                        ),
                    )
                else:
                    await task_service.update_task(
                        delivery.task_id,
                        status="FAILED",
                        progress=100,
                        message=summary.get("error_message") or "成果交付包生成失败",
                    )
            return self.serialize_delivery(delivery, include_items=True)

    async def _build_delivery_files(self, delivery_id: str) -> Dict[str, Any]:
        async with _new_session() as db:
            result = await db.execute(
                select(ResultDeliveryRequestORM).where(ResultDeliveryRequestORM.delivery_id == delivery_id)
            )
            delivery = result.scalar_one()
            request_json = delivery.request_json if isinstance(delivery.request_json, dict) else {}
            max_items = int(settings.RESULT_DELIVERY_MAX_ITEMS or 500)
            sources = await self._resolve_sources_for_channel(
                db,
                channel=delivery.channel,
                product_ids=_normalize_int_ids(request_json.get("product_ids"), max_count=max_items),
                compat_result_ids=_normalize_int_ids(request_json.get("compat_result_ids"), max_count=max_items),
                item_ids=_normalize_int_ids(request_json.get("item_ids"), max_count=max_items),
            )
            task_id = delivery.task_id

        if not sources:
            raise ValueError("delivery has no deliverable sources")

        include_checksums = bool(
            request_json.get("include_checksums")
            if "include_checksums" in request_json
            else settings.RESULT_DELIVERY_CHECKSUM_ENABLED
        )
        delivery_dir = os.path.normpath(os.path.abspath(delivery.delivery_dir))
        root = os.path.normpath(os.path.abspath(delivery.delivery_root))
        if not _path_within(root, delivery_dir):
            raise ValueError("delivery directory is outside RESULT_DELIVERY_ROOT")

        os.makedirs(delivery_dir, exist_ok=True)
        copied_files = 0
        skipped_files = 0
        failed_items = 0
        copied_bytes = 0
        total_bytes = 0
        checksum_lines: List[str] = []
        item_payloads: List[Dict[str, Any]] = []
        manifest_items: List[Dict[str, Any]] = []

        total_sources = len(sources)
        for source_index, source in enumerate(sources, start=1):
            folder = _sanitize_segment(
                source.task_name
                or source.display_name
                or source.product_id,
                default=f"product_{source_index}",
            )
            source_files = _iter_associated_files(source.source_path)
            if not source_files:
                failed_items += 1
                item_payloads.append(
                    {
                        "delivery_id": delivery_id,
                        "source_product_id": source.product.id if source.product else None,
                        "source_result_id": source.compat_row.id if source.compat_row else None,
                        "source_asset_id": source.source_asset_id,
                        "source_radar_data_id": source.source_radar_data_id,
                        "source_scene_geo_id": source.source_scene_geo_id,
                        "display_name": source.display_name,
                        "source_path": source.source_path,
                        "relative_path": None,
                        "file_size": 0,
                        "checksum_sha256": None,
                        "status": ITEM_STATUS_FAILED,
                        "error_message": "source file not found",
                    }
                )
                continue

            for file_index, source_file in enumerate(source_files, start=1):
                if os.path.isdir(source.source_path):
                    relative_under_source = os.path.relpath(source_file, source.source_path)
                    relative_path = os.path.join(folder, relative_under_source)
                else:
                    relative_path = os.path.join(folder, os.path.basename(source_file))
                target_path = os.path.join(delivery_dir, relative_path)
                size = _file_size(source_file)
                total_bytes += size
                status = ITEM_STATUS_COPIED
                error_message = None
                checksum = None
                try:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    if os.path.isfile(target_path) and _same_file_size(source_file, target_path):
                        skipped_files += 1
                    else:
                        shutil.copy2(source_file, target_path)
                        copied_files += 1
                    copied_bytes += size
                    if include_checksums and os.path.isfile(target_path):
                        checksum = _sha256_file(target_path)
                        checksum_lines.append(f"{checksum}  {relative_path.replace(os.sep, '/')}")
                except OSError as exc:
                    status = ITEM_STATUS_FAILED
                    error_message = str(exc)
                    failed_items += 1

                if task_id and (copied_files + skipped_files + failed_items) % 10 == 0:
                    progress = min(95, int(((source_index - 1) / max(1, total_sources)) * 100))
                    await task_service.update_task(
                        task_id,
                        progress=progress,
                        message=f"成果交付包生成中: {source_index}/{total_sources}",
                    )

                item_payload = {
                    "delivery_id": delivery_id,
                    "source_product_id": source.product.id if source.product else None,
                    "source_result_id": source.compat_row.id if source.compat_row else None,
                    "source_asset_id": source.source_asset_id if file_index == 1 else None,
                    "source_radar_data_id": source.source_radar_data_id,
                    "source_scene_geo_id": source.source_scene_geo_id,
                    "display_name": source.display_name,
                    "source_path": source_file,
                    "relative_path": relative_path,
                    "file_size": size,
                    "checksum_sha256": checksum,
                    "status": status,
                    "error_message": error_message,
                }
                item_payloads.append(item_payload)
                manifest_items.append(
                    {
                        "display_name": source.display_name,
                        "product_id": source.product_id or (source.product.product_id if source.product else None),
                        "product_ref_id": source.product.id if source.product else None,
                        "source_result_id": source.compat_row.id if source.compat_row else None,
                        "source_radar_data_id": source.source_radar_data_id,
                        "source_scene_geo_id": source.source_scene_geo_id,
                        "source_kind": source.source_kind,
                        "relative_path": relative_path.replace(os.sep, "/"),
                        "file_size": size,
                        "checksum_sha256": checksum,
                        "status": status,
                        "error_message": error_message,
                    }
                )

        checksums_path = os.path.join(delivery_dir, "checksums.sha256")
        if include_checksums:
            with open(checksums_path, "w", encoding="utf-8") as stream:
                stream.write("\n".join(checksum_lines))
                if checksum_lines:
                    stream.write("\n")

        manifest_path = os.path.join(delivery_dir, "manifest.json")
        summary = {
            "delivery_id": delivery_id,
            "channel": delivery.channel,
            "package_mode": delivery.package_mode,
            "source_count": total_sources,
            "file_count": len(item_payloads),
            "copied_files": copied_files,
            "skipped_files": skipped_files,
            "failed_items": failed_items,
            "total_bytes": total_bytes,
            "copied_bytes": copied_bytes,
            "include_checksums": include_checksums,
            "manifest_path": manifest_path,
            "checksums_path": checksums_path if include_checksums else None,
            "created_at": _utcnow().isoformat(timespec="seconds"),
        }
        if failed_items:
            summary["error_message"] = f"{failed_items} files failed during delivery build"
        _write_json(
            manifest_path,
            {
                "schema_version": "insar.result-delivery/v1",
                "summary": summary,
                "items": manifest_items,
            },
        )

        zip_path = None
        if delivery.package_mode == PACKAGE_MODE_ZIP:
            if total_bytes > int(settings.RESULT_DELIVERY_ZIP_MAX_BYTES or 0):
                raise ValueError(
                    f"delivery size exceeds zip limit: {total_bytes} > {settings.RESULT_DELIVERY_ZIP_MAX_BYTES}"
                )
            zip_path = str(delivery.zip_path or f"{delivery_dir}.zip")
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for root_dir, _dirs, files in os.walk(delivery_dir):
                    for name in files:
                        path = os.path.join(root_dir, name)
                        archive.write(path, os.path.relpath(path, delivery_dir))

        async with _new_session() as db:
            result = await db.execute(
                select(ResultDeliveryRequestORM).where(ResultDeliveryRequestORM.delivery_id == delivery_id)
            )
            delivery_row = result.scalar_one()
            await db.execute(
                ResultDeliveryItemORM.__table__.delete().where(
                    ResultDeliveryItemORM.delivery_id == delivery_id
                )
            )
            for payload in item_payloads:
                db.add(ResultDeliveryItemORM(**payload))
            delivery_row.total_bytes = total_bytes
            delivery_row.copied_bytes = copied_bytes
            delivery_row.item_count = len(item_payloads)
            delivery_row.manifest_path = manifest_path
            delivery_row.zip_path = zip_path or delivery_row.zip_path
            await db.commit()

        return summary

    async def resolve_manifest_path(
        self,
        db: AsyncSession,
        *,
        delivery_id: str,
        user: AuthUserORM,
    ) -> str:
        delivery = await self.get_delivery(db, delivery_id=delivery_id, user=user, include_items=False)
        if delivery is None:
            raise ValueError("delivery not found")
        if delivery.status != DELIVERY_STATUS_READY:
            raise ValueError("delivery is not ready")
        path = str(delivery.manifest_path or "").strip()
        if not path or not os.path.isfile(path):
            raise ValueError("manifest file is missing")
        if not _path_within(delivery.delivery_root, path):
            raise ValueError("manifest path is outside delivery root")
        return path

    async def resolve_archive_path(
        self,
        db: AsyncSession,
        *,
        delivery_id: str,
        user: AuthUserORM,
    ) -> str:
        delivery = await self.get_delivery(db, delivery_id=delivery_id, user=user, include_items=False)
        if delivery is None:
            raise ValueError("delivery not found")
        if delivery.status != DELIVERY_STATUS_READY:
            raise ValueError("delivery is not ready")
        path = str(delivery.zip_path or "").strip()
        if not path or not os.path.isfile(path):
            raise ValueError("zip archive is not available")
        if not _path_within(delivery.delivery_root, path):
            raise ValueError("zip path is outside delivery root")
        return path

    async def resolve_item_path(
        self,
        db: AsyncSession,
        *,
        delivery_id: str,
        item_id: int,
        user: AuthUserORM,
    ) -> str:
        delivery = await self.get_delivery(db, delivery_id=delivery_id, user=user, include_items=False)
        if delivery is None:
            raise ValueError("delivery not found")
        if delivery.status != DELIVERY_STATUS_READY:
            raise ValueError("delivery is not ready")
        result = await db.execute(
            select(ResultDeliveryItemORM).where(
                ResultDeliveryItemORM.id == int(item_id),
                ResultDeliveryItemORM.delivery_id == delivery_id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise ValueError("delivery item not found")
        if item.status != ITEM_STATUS_COPIED:
            raise ValueError("delivery item is not available")
        relative_path = str(item.relative_path or "").strip()
        if not relative_path:
            raise ValueError("delivery item path is missing")
        path = os.path.normpath(os.path.abspath(os.path.join(delivery.delivery_dir, relative_path)))
        if not os.path.isfile(path):
            raise ValueError("delivery item file is missing")
        if not _path_within(delivery.delivery_dir, path):
            raise ValueError("delivery item path is outside delivery directory")
        return path


result_delivery_service = ResultDeliveryService()
