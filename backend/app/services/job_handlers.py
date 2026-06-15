import asyncio
import base64
import glob
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from typing import Callable, Awaitable, Optional, Dict, Any, List

logger = logging.getLogger(__name__)

from sqlalchemy import select

from .. import database
from ..config import settings
from ..models import SystemJobORM, DinsarResultORM, HazardPointORM, DinsarTaskItemORM, PsTaskItemORM, RadarDataORM, SARSceneGeoORM, FloodDetectionORM, WaterDetectionORM, WaterExtractionORM, GF3ProcessingORM, AiDiagnosisORM
from ..scheduler import scan_data_job
from .data_service import data_service
from .asset_inventory_service import asset_inventory_service
from .dinsar_compat_service import dinsar_compat_service
from .dinsar_naming import build_run_key
from .dinsar_production_service import dinsar_production_service
from .dinsar_read_service import dinsar_read_service
from .dinsar_result_layout_service import (
    get_run_disp_asset_paths,
    get_run_native_output_dir,
    normalize_envi_run_layout,
)
from .dinsar_scan_service import dinsar_scan_service
from .engine_lock_service import engine_lock_service
from .envi_service import build_envi_runner_command, get_envi_runner_cwd, get_envi_runner_env
from .psinsar_catalog_service import psinsar_catalog_service
from .result_catalog_service import result_catalog_service
from .sbas_insar_catalog_service import sbas_insar_catalog_service
from .task_service import task_service
from .timeseries_service import (
    JOB_TYPE_TIMESERIES_MATERIALIZE,
    JOB_TYPE_TIMESERIES_PREPARE,
    JOB_TYPE_TIMESERIES_REGISTER_PRODUCT,
    JOB_TYPE_TIMESERIES_RUN_ISCE2_STACK,
    JOB_TYPE_TIMESERIES_RUN_MINTPY_SBAS,
    JOB_TYPE_TIMESERIES_RUN_SARSCAPE_SBAS,
    JOB_TYPE_TIMESERIES_SARSCAPE_PREFLIGHT,
    JOB_TYPE_TIMESERIES_STACK_PREP,
    JOB_TYPE_TIMESERIES_EXPORT_PUBLISH,
    timeseries_service,
)
from .unpack_service import run_unpack_task
from ..copier import run_ps_copy_items, run_dinsar_copy_items, run_dinsar_source_bundle_items
from ..ai_service import (
    train_quality_model,
    predict_quality,
    is_model_trained,
    warm_up_vlm,
    generate_dinsar_diagnosis,
)


JobHandler = Callable[[SystemJobORM], Awaitable[None]]


JOB_TYPE_SCAN_DATA = "SCAN_DATA"
JOB_TYPE_SCAN_DINSAR = "SCAN_DINSAR"
JOB_TYPE_COPY_DATA = "COPY_DATA"
JOB_TYPE_UNPACK = "UNPACK_ARCHIVES"
JOB_TYPE_UNPACK_SENTINEL1 = "UNPACK_SENTINEL1"
JOB_TYPE_AI_TRAIN = "AI_TRAIN"
JOB_TYPE_AI_PREDICT = "AI_PREDICT"
JOB_TYPE_AI_ANALYZE = "AI_ANALYZE"
JOB_TYPE_AI_WARMUP = "AI_WARMUP"
JOB_TYPE_AI_DIAGNOSIS = "AI_DIAGNOSIS"
JOB_TYPE_SCAN_HAZARD = "SCAN_HAZARD"
JOB_TYPE_IDL_RUN_IMPORT = "IDL_RUN_IMPORT"
JOB_TYPE_IDL_RUN_DINSAR = "IDL_RUN_DINSAR"
JOB_TYPE_WATER_GEOCODE = "WATER_GEOCODE"
JOB_TYPE_WATER_FLOOD = "WATER_FLOOD"
JOB_TYPE_WATER_DETECT = "WATER_DETECT"
JOB_TYPE_SAR_SCENE_PREPROCESS = "SAR_SCENE_PREPROCESS"
JOB_TYPE_FLOOD_DETECTION = "FLOOD_DETECTION"
JOB_TYPE_GF3_PROCESS = "GF3_PROCESS"
JOB_TYPE_GF3_UNPACK = "GF3_UNPACK"
JOB_TYPE_GF3_BATCH_PROCESS = "GF3_BATCH_PROCESS"
JOB_TYPE_GF3_SARSCAPE_PRODUCE = "GF3_SARSCAPE_PRODUCE"
JOB_TYPE_GF3_SARSCAPE_SYNC = "GF3_SARSCAPE_SYNC"
JOB_TYPE_GF3_SARSCAPE_CLEAN = "GF3_SARSCAPE_CLEAN"
JOB_TYPE_ISCE2_RUN = "ISCE2_RUN"
JOB_TYPE_PYINT_RUN = "PYINT_RUN"
JOB_TYPE_LANDSAR_RUN = "LANDSAR_RUN"
JOB_TYPE_PUBLISH_DINSAR_PRODUCTS = "PUBLISH_DINSAR_PRODUCTS"
JOB_TYPE_REBUILD_DINSAR_CATALOG = "REBUILD_DINSAR_CATALOG"
JOB_TYPE_REBUILD_PSINSAR_CATALOG = "REBUILD_PSINSAR_CATALOG"
JOB_TYPE_REBUILD_SBAS_INSAR_CATALOG = "REBUILD_SBAS_INSAR_CATALOG"
JOB_TYPE_SCAN_ASSET_INVENTORY = "SCAN_ASSET_INVENTORY"
JOB_TYPE_SBAS_COREGISTRATION = "SBAS_COREGISTRATION"
JOB_TYPE_SBAS_RDC_DEM = "SBAS_RDC_DEM"
JOB_TYPE_SBAS_INTERFEROGRAMS = "SBAS_INTERFEROGRAMS"
JOB_TYPE_SBAS_IPTA_TIMESERIES = "SBAS_IPTA_TIMESERIES"
JOB_TYPE_SBAS_GAMMA_WORKFLOW = "SBAS_GAMMA_WORKFLOW"
JOB_TYPE_SBAS_LANDSAR_WORKFLOW = "SBAS_LANDSAR_WORKFLOW"

COPY_ALLOWED_STATUSES = {"PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"}


def AsyncSessionLocal():
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _normalize_copy_statuses(raw_statuses: Any) -> List[str]:
    if not raw_statuses:
        return ["COMPLETED"]
    if not isinstance(raw_statuses, list):
        raise ValueError("COPY_DATA payload.copy_statuses must be a list.")

    normalized: List[str] = []
    for raw in raw_statuses:
        status = str(raw or "").strip().upper()
        if not status:
            continue
        if status not in COPY_ALLOWED_STATUSES:
            raise ValueError(f"Invalid copy status: {status}")
        if status not in normalized:
            normalized.append(status)

    return normalized or ["COMPLETED"]


def _normalize_positive_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dedupe_existing_dirs(paths: Any) -> List[str]:
    ordered: List[str] = []
    for raw_path in paths or []:
        path = os.path.normpath(os.path.abspath(str(raw_path or "").strip()))
        if not path or not os.path.isdir(path):
            continue
        if path in ordered:
            continue
        ordered.append(path)
    return ordered


async def _run_scan_data_custom(task_id: str, payload: Dict[str, Any]) -> None:
    from ..database import AsyncSessionLocal

    radar_dirs = payload.get("radar_dirs") or []
    orbit_dir = payload.get("orbit_dir") or None
    dinsar_dirs = payload.get("dinsar_dirs") or []
    has_radar_orbit = bool(radar_dirs or orbit_dir)
    has_dinsar = bool(dinsar_dirs)
    radar_progress_base = 0
    radar_progress_span = 100
    dinsar_progress_base = 0
    dinsar_progress_span = 100

    if has_radar_orbit and has_dinsar:
        radar_progress_base = 0
        radar_progress_span = 50
        dinsar_progress_base = 50
        dinsar_progress_span = 50

    async with AsyncSessionLocal() as db:
        await task_service.start_task(task_id, message="任务已启动")
        try:
            if radar_dirs or orbit_dir:
                await task_service.update_task(task_id, message="正在扫描雷达和精轨数据...")
                summary_radar = await data_service.scan_radar_data(
                    db,
                    radar_dirs,
                    orbit_dir,
                    task_id=task_id,
                    progress_base=radar_progress_base,
                    progress_span=radar_progress_span
                )
                msg_radar = summary_radar.get('message', '雷达扫描完成')
                msg_radar = (
                    f"{msg_radar}"
                    f" (场景: {summary_radar.get('processed_scenes', 0)},"
                    f" 纠正缓存新增: {summary_radar.get('cached_previews', 0)},"
                    f" 纠正缓存跳过: {summary_radar.get('skipped_previews', 0)},"
                    f" 原图缓存新增: {summary_radar.get('cached_raw_previews', 0)},"
                    f" 原图缓存跳过: {summary_radar.get('skipped_raw_previews', 0)})"
                )
            else:
                msg_radar = "跳过雷达扫描"

            if dinsar_dirs:
                await task_service.update_task(task_id, message="正在扫描 D-InSAR 结果...")
                summary_dinsar = await dinsar_scan_service.run_scan(
                    db,
                    source_directories=dinsar_dirs,
                    task_id=task_id,
                )
                rebuild_payload = summary_dinsar.get("rebuild") or {}
                compat_payload = summary_dinsar.get("compat_sync") or {}
                msg_dinsar = (
                    f"{summary_dinsar.get('message', 'D-InSAR unified scan completed')}"
                    f" (packages={rebuild_payload.get('manifest_count', 0)},"
                    f" catalog={rebuild_payload.get('registered', 0)},"
                    f" compat={compat_payload.get('compat_count', 0)})"
                )
            else:
                msg_dinsar = "跳过 D-InSAR 扫描"

            await task_service.update_task(
                task_id,
                status="COMPLETED",
                message=f"扫描完成: {msg_radar}; {msg_dinsar}",
                progress=100
            )
        except Exception as exc:
            await task_service.update_task(task_id, status="FAILED", message=f"扫描失败: {exc}")


async def _handle_scan_data(job: SystemJobORM) -> None:
    payload = job.payload or {}
    use_monitor = bool(payload.get("use_monitor_config", False))
    target = payload.get("target")

    if use_monitor:
        if job.task_id:
            await task_service.start_task(job.task_id, message="任务已启动")
        await scan_data_job(target=target, task_id=job.task_id)
    else:
        if not job.task_id:
            raise ValueError("SCAN_DATA requires task_id for progress tracking.")
        await _run_scan_data_custom(job.task_id, payload)


async def _handle_scan_asset_inventory(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SCAN_ASSET_INVENTORY requires task_id for progress tracking.")
    payload = job.payload or {}
    await task_service.start_task(job.task_id, message="Source/orbit asset inventory scan started")
    async with AsyncSessionLocal() as db:
        result = await asset_inventory_service.scan_configured_roots(
            db,
            inventory_types=payload.get("inventory_types") or None,
            root_ids=payload.get("root_ids") or None,
            bind_orbits=bool(payload.get("bind_orbits", True)),
            task_id=job.task_id,
        )
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            progress=100,
            message=(
                "Asset inventory scan completed: "
                f"sources={result.get('source_assets', 0)}, "
                f"orbits={result.get('orbit_assets', 0)}, "
                f"matched={((result.get('binding') or {}).get('matched_count', 0))}, "
                f"missing={((result.get('binding') or {}).get('missing_count', 0))}"
            ),
            db=db,
        )


def _resolve_hazard_shp_path() -> str:
    base_dir = settings.HAZARD_POINTS_DIR
    filename = settings.HAZARD_POINTS_FILENAME
    if not base_dir or not filename:
        raise ValueError("HAZARD_POINTS_DIR and HAZARD_POINTS_FILENAME must be configured.")
    if str(base_dir).lower().endswith(".shp"):
        raise ValueError("HAZARD_POINTS_DIR must be a directory, not a .shp path.")
    if not os.path.isdir(str(base_dir)):
        raise FileNotFoundError(f"Hazard points directory not found: {base_dir}")
    shp_path = os.path.join(str(base_dir), str(filename))
    if not shp_path.lower().endswith(".shp"):
        raise ValueError(f"HAZARD_POINTS_FILENAME must end with .shp: {filename}")
    if not os.path.isfile(shp_path):
        raise FileNotFoundError(f"Hazard points Shapefile not found: {shp_path}")
    return shp_path


async def _handle_scan_hazard(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SCAN_HAZARD requires task_id for progress tracking.")
    await task_service.start_task(job.task_id, message="任务已启动")
    shp_path = _resolve_hazard_shp_path()
    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await task_service.update_task(job.task_id, message="正在读取灾害点 Shapefile...", progress=20)
        summary = await data_service.scan_hazard_points(db, shp_path)
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            message=f"同步成功: 发现 {summary.get('count', 0)} 个点",
            progress=100
        )


async def _handle_scan_dinsar(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SCAN_DINSAR requires task_id for progress tracking.")
    await task_service.start_task(job.task_id, message="正在执行 D-InSAR 统一扫描...")
    payload = job.payload or {}
    dirs = payload.get("dirs") or payload.get("results_directories") or []
    publish_root = payload.get("publish_root") or None
    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        summary = await dinsar_scan_service.run_scan(
            db,
            source_directories=dirs,
            publish_root=publish_root,
            task_id=job.task_id,
        )
        rebuild_payload = summary.get("rebuild") or {}
        compat_payload = summary.get("compat_sync") or {}
        compat_error = summary.get("compat_error")
        message = (
            f"统一扫描完成: packages={rebuild_payload.get('manifest_count', 0)}, "
            f"catalog={rebuild_payload.get('registered', 0)}, "
            f"compat={compat_payload.get('compat_count', 0)}"
        )
        if compat_error:
            message += f", compat_error={compat_error}"
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            message=message,
            progress=100
        )

async def _handle_copy_data(job: SystemJobORM) -> None:
    payload = job.payload or {}
    file_type = payload.get("file_type")
    dest_dir = payload.get("dest_dir")
    batch_id = payload.get("batch_id")
    copy_statuses = _normalize_copy_statuses(payload.get("copy_statuses"))
    include_orbit_files = bool(payload.get("include_orbit_files"))
    export_zip = bool(payload.get("export_zip"))
    package_mode = str(payload.get("package_mode") or ("task_zip" if export_zip else "task_folder")).strip().lower()
    skip_existing = payload.get("skip_existing") is not False
    max_items = _normalize_positive_int(payload.get("max_items"))

    if not batch_id:
        raise ValueError("COPY_DATA requires batch_id payload.")

    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        if file_type == "PS_STACK":
            result = await db.execute(
                select(PsTaskItemORM)
                .where(PsTaskItemORM.batch_id == batch_id)
                .where(PsTaskItemORM.status.in_(copy_statuses))
                .order_by(PsTaskItemORM.id.asc())
            )
            items = [
                {"file_path": item.file_path}
                for item in result.scalars().all()
            ]
            if not items:
                raise ValueError(
                    f"No PS items matched copy statuses: {', '.join(copy_statuses)}"
                )
            await run_ps_copy_items(job.task_id, items, dest_dir)
            return

        if file_type == "DINSAR_PAIRS":
            result = await db.execute(
                select(DinsarTaskItemORM)
                .where(DinsarTaskItemORM.batch_id == batch_id)
                .where(DinsarTaskItemORM.status.in_(copy_statuses))
                .order_by(DinsarTaskItemORM.id.asc())
            )
            task_items = result.scalars().all()
            orbit_by_path: Dict[str, Optional[str]] = {}
            if include_orbit_files:
                scene_paths = [
                    str(path)
                    for item in task_items
                    for path in (item.master_path, item.slave_path)
                    if path
                ]
                if scene_paths:
                    scene_result = await db.execute(
                        select(RadarDataORM.file_path, RadarDataORM.orbit_file_path).where(
                            RadarDataORM.file_path.in_(scene_paths)
                        )
                    )
                    orbit_by_path = {
                        os.path.normcase(os.path.normpath(str(file_path))): orbit_path
                        for file_path, orbit_path in scene_result.all()
                        if file_path
                    }
            items = [
                {
                    "task_name": item.task_name,
                    "task_alias": item.task_alias or item.task_name,
                    "pair_key": item.pair_key,
                    "master_path": item.master_path,
                    "slave_path": item.slave_path,
                    "master_satellite": item.master_satellite,
                    "slave_satellite": item.slave_satellite,
                    "master_imaging_date": item.master_imaging_date,
                    "slave_imaging_date": item.slave_imaging_date,
                    "master_imaging_mode": item.master_imaging_mode,
                    "slave_imaging_mode": item.slave_imaging_mode,
                    "master_polarization": item.master_polarization,
                    "slave_polarization": item.slave_polarization,
                    "time_baseline_days": item.time_baseline_days,
                    "spatial_baseline_meters": item.spatial_baseline_meters,
                    "scene_center_distance_meters": item.scene_center_distance_meters,
                    "master_orbit_file_path": orbit_by_path.get(
                        os.path.normcase(os.path.normpath(str(item.master_path)))
                    ) if include_orbit_files and item.master_path else None,
                    "slave_orbit_file_path": orbit_by_path.get(
                        os.path.normcase(os.path.normpath(str(item.slave_path)))
                    ) if include_orbit_files and item.slave_path else None,
                    "scene_pair_uid": item.scene_pair_uid,
                    "pair_uid": item.scene_pair_uid,
                    "network_run_id": item.network_run_id,
                    "network_edge_id": item.network_edge_id,
                    "policy_version": item.policy_version,
                    "selection_strategy": item.selection_strategy,
                }
                for item in task_items
            ]
            if not items:
                raise ValueError(
                    f"No D-InSAR items matched copy statuses: {', '.join(copy_statuses)}"
                )
            if package_mode in {"source_bundle", "bundle", "dedupe_source"}:
                await run_dinsar_source_bundle_items(
                    job.task_id,
                    items,
                    dest_dir,
                    include_orbit_files=include_orbit_files,
                    skip_existing=skip_existing,
                    max_items=max_items,
                )
            else:
                await run_dinsar_copy_items(
                    job.task_id,
                    items,
                    dest_dir,
                    include_orbit_files=include_orbit_files,
                    export_zip=(package_mode == "task_zip" or export_zip),
                    skip_existing=skip_existing,
                    max_items=max_items,
                )
            return

    raise ValueError(f"Unknown COPY_DATA file_type: {file_type}")


async def _handle_unpack_archives(job: SystemJobORM) -> None:
    await run_unpack_task(job.task_id)


async def _handle_unpack_sentinel1(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("UNPACK_SENTINEL1 requires task_id for progress tracking.")
    payload = job.payload or {}
    if payload.get("asset_id"):
        await asset_inventory_service.run_sentinel1_unpack_task(job.task_id, payload)
        return
    await asset_inventory_service.run_sentinel1_unpack_batch_task(job.task_id, payload)


async def _handle_ai_train(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("AI_TRAIN requires task_id for progress tracking.")
    await task_service.start_task(job.task_id, message="任务已启动")

    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        records = await dinsar_read_service.list_catalog_records(db, labeled_only=True)

        if not records:
            await task_service.update_task(job.task_id, status="FAILED", message="暂无标注数据，无法训练。")
            return

        labeled_data = []
        for record in records:
            if record.product.user_label is None or not record.image_path:
                continue
            labeled_data.append((record.image_path, record.product.user_label))

        if not labeled_data:
            await task_service.update_task(
                job.task_id,
                status="FAILED",
                message="暂无可用于训练的已标注预览图像。",
            )
            return

        loop = asyncio.get_running_loop()

        def update_progress(p: int):
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    task_service.update_task(
                        job.task_id,
                        message=f"正在训练 AI 模型... ({p}%)",
                        progress=p
                    )
                )
            )

        try:
            train_result = await asyncio.to_thread(
                train_quality_model,
                labeled_data,
                progress_callback=update_progress
            )
            accuracy = train_result.get("accuracy") if isinstance(train_result, dict) else None
            sample_count = train_result.get("sample_count") if isinstance(train_result, dict) else None
            if accuracy is not None:
                summary = f"训练完成，准确率: {accuracy:.2f}"
                if sample_count is not None:
                    summary += f" (样本数: {sample_count})"
            else:
                summary = "训练完成"
            await task_service.update_task(job.task_id, status="COMPLETED", message=summary, progress=100)
        except Exception as exc:
            await task_service.update_task(job.task_id, status="FAILED", message=f"训练失败: {exc}")


async def _handle_ai_predict(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("AI_PREDICT requires task_id for progress tracking.")
    await task_service.start_task(job.task_id, message="任务已启动")

    if not is_model_trained():
        await task_service.update_task(job.task_id, status="FAILED", message="模型尚未训练。")
        return

    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await task_service.update_task(job.task_id, message="正在加载数据...", progress=5)
        all_records = await dinsar_read_service.list_catalog_records(db)

        paths_map: Dict[str, List[Any]] = {}
        paths_to_predict = []
        seen_paths = set()
        for record in all_records:
            img_path = record.image_path
            if not img_path:
                continue
            paths_map.setdefault(img_path, []).append(record)
            if img_path in seen_paths:
                continue
            seen_paths.add(img_path)
            paths_to_predict.append(img_path)

        if not paths_to_predict:
            await task_service.update_task(
                job.task_id,
                status="FAILED",
                message="没有可用于预测的预览图像。",
            )
            return

        loop = asyncio.get_running_loop()

        def update_progress(p: int):
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    task_service.update_task(
                        job.task_id,
                        message=f"正在进行 AI 质量预测 (样本数: {len(paths_to_predict)})...",
                        progress=p
                    )
                )
            )

        predictions = await asyncio.to_thread(predict_quality, paths_to_predict, update_progress)

        updated_count = 0
        updated_product_ids: set[str] = set()
        for path, score in predictions.items():
            for record in paths_map.get(path, []):
                record.product.ai_score = score
                updated_count += 1
                if record.product.product_id:
                    updated_product_ids.add(record.product.product_id)

        await db.commit()
        compat_sync = await dinsar_compat_service.sync_result_annotations_from_products(
            db,
            product_ids=sorted(updated_product_ids),
        )
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            message=(
                f"预测完成，更新了 {updated_count} 条目录记录，"
                f"兼容视图同步 {compat_sync.get('synced', 0)} 条。"
            ),
            progress=100,
        )


async def _handle_ai_warmup(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("AI_WARMUP requires task_id for progress tracking.")
    await task_service.start_task(job.task_id, message="任务已启动")
    await task_service.update_task(job.task_id, message="正在预热 AI 模型 (加载至显存)...", progress=20)
    success = await warm_up_vlm()
    if success:
        await task_service.update_task(job.task_id, status="COMPLETED", message="AI 模型预热成功，现在诊断将非常迅速。", progress=100)
    else:
        await task_service.update_task(job.task_id, status="FAILED", message="AI 模型预热失败，请检查 Ollama 服务。")


async def _handle_ai_analyze(job: SystemJobORM) -> None:
    payload = job.payload or {}
    result_id = payload.get("result_id")
    product_id = str(payload.get("product_id") or "").strip() or None
    if result_id is None:
        raise ValueError("AI_ANALYZE requires result_id payload.")
    if not job.task_id:
        raise ValueError("AI_ANALYZE requires task_id for progress tracking.")

    await task_service.start_task(job.task_id, message="任务已启动")
    from ..database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(job.task_id, message="正在检索空间上下文...", progress=10)
            record = await dinsar_read_service.get_compat_record(
                db,
                compat_result_id=int(result_id),
                include_geom=True,
            )
            if not record:
                await task_service.update_task(job.task_id, status="FAILED", message="未找到该 D-InSAR 结果")
                return

            from geoalchemy2.functions import ST_Covers
            hazard_res = await db.execute(
                select(HazardPointORM).where(ST_Covers(record.product.geom, HazardPointORM.geom))
            )
            hazards = hazard_res.scalars().all()

            hazard_info = "\n".join([f"- {h.hazard_name} ({h.hazard_type}): {h.city}{h.county}" for h in hazards])
            if not hazard_info:
                hazard_info = "该范围内暂无已知灾害点。"

            await task_service.update_task(job.task_id, message="正在准备影像数据...", progress=30)
            target_path = record.image_path

            if not target_path or not os.path.exists(target_path):
                await task_service.update_task(job.task_id, status="FAILED", message="未找到缓存图片，请先扫描。")
                return

            await task_service.update_task(job.task_id, message="正在准备影像数据 (PNG 格式)...", progress=35)
            from PIL import Image
            with Image.open(target_path) as img:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                img_std_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

            import re
            dates = re.findall(r'\d{8}', record.display_name or "")
            date_str = f"{dates[0]} 至 {dates[1]}" if len(dates) >= 2 else "日期未知"

            quality_context = "尚未评估"
            if record.product.ai_score is not None:
                score_pct = int(record.product.ai_score * 100)
                if record.product.ai_score > 0.7:
                    quality_context = f"高质量 ({score_pct}/100)"
                elif record.product.ai_score > 0.4:
                    quality_context = f"中等质量 ({score_pct}/100)"
                else:
                    quality_context = f"低质量 ({score_pct}/100)"

            await task_service.update_task(job.task_id, message="正在调用 VLM 进行智能诊断 (请耐心等待报告生成)...", progress=50)

            analysis = await generate_dinsar_diagnosis(
                images_base64=[img_std_base64],
                record_name=record.display_name,
                date_str=date_str,
                quality_context=quality_context,
                hazard_info=hazard_info
            )

            result_msg = json.dumps({
                "analysis": analysis,
                "hazards_found": len(hazards),
                "result_id": result_id,
                "product_id": product_id or record.product.product_id,
                "result_name": record.display_name,
            })

            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                message=result_msg,
                progress=100
            )

            await asyncio.sleep(5.0)
        except Exception as exc:
            error_msg = json.dumps({
                "error": str(exc),
                "result_id": result_id,
                "result_name": "诊断失败"
            })
            await task_service.update_task(job.task_id, status="FAILED", message=error_msg)


async def _handle_ai_diagnosis(job: SystemJobORM) -> None:
    """
    新版 AI 诊断 Handler（使用 ai_diagnosis 表）。

    Payload 字段：
    - result_id: D-InSAR 结果 ID
    - model_name: 模型名称
    - prompt_template: Prompt 模板名称
    - prompt_text: Prompt 文本
    """
    payload = job.payload or {}
    result_id = payload.get("result_id")
    product_id = str(payload.get("product_id") or "").strip() or None
    model_name = payload.get("model_name")
    prompt_template = payload.get("prompt_template")
    prompt_text = payload.get("prompt_text")

    if not result_id:
        raise ValueError("AI_DIAGNOSIS requires result_id payload.")
    if not model_name:
        raise ValueError("AI_DIAGNOSIS requires model_name payload.")
    if not prompt_text:
        raise ValueError("AI_DIAGNOSIS requires prompt_text payload.")
    if not job.task_id:
        raise ValueError("AI_DIAGNOSIS requires task_id for progress tracking.")

    start_time = time.time()
    diagnosis_id = None  # 初始化，避免异常处理时未定义

    async with AsyncSessionLocal() as db:
        try:
            # 启动任务（传递 db 会话）
            await task_service.start_task(job.task_id, message="任务已启动", db=db)

            # 1. 加载 D-InSAR 结果
            await task_service.update_task(job.task_id, message="正在检索 D-InSAR 结果...", progress=5, db=db)
            record = await dinsar_read_service.get_compat_record(
                db,
                compat_result_id=int(result_id),
                include_geom=True,
            )
            if not record:
                await task_service.update_task(job.task_id, status="FAILED", message="D-InSAR 结果不存在", db=db)
                return

            # 提前提取所有需要的属性（避免在 commit 后访问导致延迟加载）
            result_name = record.display_name
            result_geom = record.product.geom
            result_ai_score = record.product.ai_score
            resolved_product_id = product_id or record.product.product_id

            # 2. 创建诊断记录
            await task_service.update_task(job.task_id, message="正在创建诊断记录...", progress=10, db=db)
            diagnosis = AiDiagnosisORM(
                result_id=int(result_id),
                product_ref_id=record.product.id,
                product_id=resolved_product_id,
                task_id=job.task_id,
                model_name=model_name,
                prompt_template=prompt_template or "custom",
                prompt_text=prompt_text,
                result_name=result_name,
            )
            db.add(diagnosis)
            await db.flush()
            diagnosis_id = diagnosis.id
            await db.commit()

            # 3. 查询空间上下文（隐患点）
            await task_service.update_task(job.task_id, message="正在检索空间上下文...", progress=20, db=db)

            from geoalchemy2.functions import ST_Covers
            hazard_res = await db.execute(
                select(HazardPointORM).where(ST_Covers(result_geom, HazardPointORM.geom))
            )
            hazards = hazard_res.scalars().all()
            hazards_found = len(hazards)

            # 保存隐患点快照
            hazards_snapshot = [
                {
                    "tybh": h.tybh,
                    "hazard_name": h.hazard_name,
                    "hazard_type": h.hazard_type,
                    "city": h.city,
                    "county": h.county,
                    "longitude": h.longitude,
                    "latitude": h.latitude,
                }
                for h in hazards
            ]

            # 4. 提取日期范围
            import re
            dates = re.findall(r'\d{8}', result_name or "")
            date_range = f"{dates[0]} - {dates[1]}" if len(dates) >= 2 else "未知"

            # 5. 准备影像数据
            await task_service.update_task(job.task_id, message="正在准备影像数据...", progress=30, db=db)
            target_path = record.image_path

            if not target_path or not os.path.exists(target_path):
                # 更新诊断记录错误信息
                diag_res = await db.execute(
                    select(AiDiagnosisORM).where(AiDiagnosisORM.id == diagnosis_id)
                )
                diag = diag_res.scalar_one()
                diag.error_message = "未找到缓存图片，请先扫描"
                diag.duration_seconds = time.time() - start_time
                await db.commit()
                await task_service.update_task(job.task_id, status="FAILED", message="未找到缓存图片", db=db)
                return

            from PIL import Image
            with Image.open(target_path) as img:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')

            # 6. 构建完整 Prompt（注入上下文）
            await task_service.update_task(job.task_id, message="正在构建分析上下文...", progress=40, db=db)

            hazard_info = "\n".join([
                f"- {h['hazard_name']} ({h['hazard_type']}): {h['city']}{h['county']}"
                for h in hazards_snapshot
            ])
            if not hazard_info:
                hazard_info = "该范围内暂无已知灾害点。"

            quality_context = "尚未评估"
            if result_ai_score is not None:
                score_pct = int(result_ai_score * 100)
                if result_ai_score > 0.7:
                    quality_context = f"高质量 ({score_pct}/100)"
                elif result_ai_score > 0.4:
                    quality_context = f"中等质量 ({score_pct}/100)"
                else:
                    quality_context = f"低质量 ({score_pct}/100)"

            full_prompt = f"""### 基础背景
- **任务标识**: `{result_name}`
- **监测周期**: {date_range}
- **数据质量**: {quality_context}

### 空间上下文（已知灾害点）
影像覆盖范围内的已知灾害点信息如下：
{hazard_info}

### 影像说明
提供的影像采用固定色标（±0.1m），绿色代表稳定，红色代表沉降，蓝色代表抬升。

---

{prompt_text}
"""

            # 7. 调用 VLM 进行分析
            await task_service.update_task(
                job.task_id,
                message=f"正在调用 VLM ({model_name}) 进行智能诊断...",
                progress=50,
                db=db
            )

            from ..ai_service import analyze_map_with_vlm
            diagnosis_markdown = await analyze_map_with_vlm(
                images_base64=[img_base64],
                prompt=full_prompt
            )

            # 8. 解析风险等级和置信度（简单正则匹配）
            risk_level = None
            confidence_score = None

            risk_patterns = {
                "critical": r"极高风险|critical",
                "high": r"高风险|high risk",
                "medium": r"中风险|中等风险|medium risk",
                "low": r"低风险|low risk"
            }
            for level, pattern in risk_patterns.items():
                if re.search(pattern, diagnosis_markdown, re.IGNORECASE):
                    risk_level = level
                    break

            # 尝试提取置信度（如果 VLM 输出了）
            confidence_match = re.search(r"置信度[：:]\s*(\d+)%", diagnosis_markdown)
            if confidence_match:
                confidence_score = float(confidence_match.group(1)) / 100.0

            # 9. 评估数据质量（1-10 分）
            quality_score = None
            quality_match = re.search(r"质量评分[：:]\s*(\d+(?:\.\d+)?)\s*/\s*10", diagnosis_markdown)
            if quality_match:
                quality_score = float(quality_match.group(1))

            # 10. 更新诊断记录
            diag_res = await db.execute(
                select(AiDiagnosisORM).where(AiDiagnosisORM.id == diagnosis_id)
            )
            diag = diag_res.scalar_one()
            diag.diagnosis_markdown = diagnosis_markdown
            diag.risk_level = risk_level
            diag.confidence_score = confidence_score
            diag.quality_score = quality_score
            diag.hazards_found = hazards_found
            diag.hazards_snapshot = hazards_snapshot
            diag.date_range = date_range
            diag.duration_seconds = time.time() - start_time

            await db.commit()

            # 11. 完成任务
            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                message=f"AI 诊断完成 (风险等级: {risk_level or '未识别'})",
                progress=100,
                db=db
            )

        except Exception as exc:
            # 更新诊断记录错误信息（如果已创建）
            if diagnosis_id is not None:
                try:
                    diag_res = await db.execute(
                        select(AiDiagnosisORM).where(AiDiagnosisORM.id == diagnosis_id)
                    )
                    diag = diag_res.scalar_one_or_none()
                    if diag:
                        diag.error_message = str(exc)
                        diag.duration_seconds = time.time() - start_time
                        await db.commit()
                except Exception:
                    logger.exception("Failed to update AI diagnosis error state")

            await task_service.update_task(
                job.task_id,
                status="FAILED",
                message=f"AI 诊断失败: {str(exc)}",
                db=db
            )


def _scan_latest_mtime(directory: str) -> Optional[float]:
    """Scan a directory tree and return the most recent file mtime, or None."""
    if not directory or not os.path.isdir(directory):
        return None
    latest = 0.0
    try:
        for root, _dirs, files in os.walk(directory):
            for f in files:
                try:
                    mt = os.path.getmtime(os.path.join(root, f))
                    if mt > latest:
                        latest = mt
                except OSError as exc:
                    print(f"[WARN] _scan_latest_mtime getmtime: {exc}")
    except Exception as exc:
        print(f"[WARN] _scan_latest_mtime walk: {exc}")
    return latest if latest > 0 else None


def _read_progress_file(progress_path: str) -> Optional[Dict[str, Any]]:
    """Read the progress JSON file written by the subprocess."""
    try:
        if os.path.isfile(progress_path):
            with open(progress_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _get_envi_progress_file(job_id: str) -> str:
    """Return the progress file path matching envi_service convention."""
    runtime_dir = settings.IDL_WORKER_RUNTIME_DIR
    return os.path.join(runtime_dir, f"job_{job_id}_progress.json")


def _get_envi_runtime_cwd() -> str:
    # The ENVI runner is launched via `python -m backend.app.services.envi_runner_cli`,
    # so its import root must be the project root rather than the runtime directory.
    return get_envi_runner_cwd()


# Stale threshold: no progress file update AND no output file activity
# for this many seconds -> consider the subprocess dead.
_ENVI_FILE_STALE_SECONDS = int(settings.ENVI_FILE_STALE_SECONDS)
_ENVI_MONITOR_INTERVAL = 30  # seconds between checks

# File stability: after subprocess exits, wait until all output files
# have unchanged sizes for this many consecutive checks before declaring done.
# This handles ENVI writing large files in chunks after envipyengine returns.
_ENVI_STABILITY_CHECK_INTERVAL = int(settings.ENVI_STABILITY_CHECK_INTERVAL)
_ENVI_STABILITY_ROUNDS = int(settings.ENVI_STABILITY_ROUNDS)
_ENVI_STABILITY_MAX_WAIT = int(settings.ENVI_STABILITY_MAX_WAIT)


def _collect_file_sizes(directory: str) -> Dict[str, int]:
    """Return {filepath: size} for all files in directory tree."""
    sizes: Dict[str, int] = {}
    if not directory or not os.path.isdir(directory):
        return sizes
    try:
        for root, _dirs, files in os.walk(directory):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    sizes[fp] = os.path.getsize(fp)
                except OSError as exc:
                    print(f"[WARN] _collect_file_sizes getsize: {exc}")
    except Exception as exc:
        print(f"[WARN] _collect_file_sizes walk: {exc}")
    return sizes


# Per-task absolute timeout (default 6h). The total timeout for a batch is
# calculated as task_count * this value in _run_envi_workflow_job.
_ENVI_PER_TASK_TIMEOUT = int(settings.ENVI_PER_TASK_TIMEOUT)


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its children. Best-effort, never raises."""
    try:
        import psutil
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        psutil.wait_procs(children + [parent], timeout=10)
    except ImportError:
        # psutil not available, fallback to Windows taskkill
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=15,
            )
        except Exception as exc:
            print(f"[WARN] _kill_process_tree fallback: {exc}")
    except Exception as exc:
        print(f"[WARN] _kill_process_tree: pid={pid} — {exc}")


_ENVI_RESULT_NAME_RE = re.compile(r"^.+_rsp_disp$", re.IGNORECASE)


def _format_envi_keepalive(progress: Optional[Dict[str, Any]]) -> tuple[str, Optional[int]]:
    if not progress:
        return "ENVI processing...", None

    step = progress.get("step", 0)
    total = progress.get("total_steps", 6)
    step_msg = progress.get("message", "")
    pair_index = progress.get("pair_index", 0)
    total_pairs = progress.get("total_pairs", 0)
    pair_name = progress.get("pair_name", "")

    step_part = f"Step {step}/{total}: {step_msg}" if step_msg else f"Step {step}/{total}"
    if total_pairs > 0 and pair_index > 0:
        pair_part = f"Pair {pair_index}/{total_pairs}"
        if pair_name:
            pair_part += f" ({pair_name})"
        message = f"{pair_part} | {step_part}"
    else:
        message = step_part

    progress_value: Optional[int] = None
    if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
        if total_pairs > 0 and pair_index > 0:
            pair_frac = (pair_index - 1 + step / total) / total_pairs
            progress_value = min(90, 10 + int(80 * pair_frac))
        else:
            progress_value = min(90, 10 + int(80 * step / total))
    return message, progress_value


def _clear_envi_progress_file(job_id: Optional[str]) -> None:
    if not job_id:
        return
    try:
        progress_path = _get_envi_progress_file(job_id)
        if os.path.isfile(progress_path):
            os.remove(progress_path)
    except OSError:
        pass


def _find_latest_envi_result(output_dir: str) -> Dict[str, Any]:
    matches: List[tuple[float, str]] = []
    try:
        for current_root, _dirs, files in os.walk(output_dir):
            for name in files:
                if name.lower().endswith((".hdr", ".sml")):
                    continue
                if not _ENVI_RESULT_NAME_RE.match(name):
                    continue
                path = os.path.join(current_root, name)
                try:
                    stat = os.stat(path)
                    matches.append((max(stat.st_mtime, stat.st_ctime), path))
                except OSError:
                    matches.append((0.0, path))
    except OSError as exc:
        raise RuntimeError(f"Failed to scan ENVI output directory: {output_dir}: {exc}") from exc

    if not matches:
        raise RuntimeError(f"No ENVI displacement result found under: {output_dir}")

    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    primary_file = matches[0][1]
    source_files = [primary_file]
    for ext in (".hdr", ".sml"):
        sidecar = primary_file + ext
        if os.path.isfile(sidecar):
            source_files.append(sidecar)
    return {
        "primary_file": primary_file,
        "source_files": source_files,
    }


def _is_path_within(base_dir: str, candidate_path: str) -> bool:
    try:
        return os.path.commonpath(
            [
                os.path.normpath(os.path.abspath(str(base_dir or "").strip())),
                os.path.normpath(os.path.abspath(str(candidate_path or "").strip())),
            ]
        ) == os.path.normpath(os.path.abspath(str(base_dir or "").strip()))
    except ValueError:
        return False


def _normalize_managed_envi_output_dir(output_dir: str) -> Dict[str, Any]:
    normalized_output_dir = os.path.normpath(os.path.abspath(str(output_dir or "").strip()))
    if not normalized_output_dir or not os.path.isdir(normalized_output_dir):
        raise FileNotFoundError(f"Managed ENVI output directory not found: {output_dir}")

    managed_root = os.path.normpath(os.path.abspath(str(settings.DINSAR_PRODUCT_DIR or "").strip()))
    if not managed_root or not _is_path_within(managed_root, normalized_output_dir):
        result_files = _find_latest_envi_result(normalized_output_dir)
        return {
            "run_dir": normalized_output_dir,
            "native_output_dir": normalized_output_dir,
            "primary_file": result_files["primary_file"],
            "source_files": result_files["source_files"],
            "promoted_files": [],
            "moved_entries": [],
        }

    disp_paths = get_run_disp_asset_paths(normalized_output_dir)
    if os.path.isfile(disp_paths["primary"]):
        source_files = [disp_paths["primary"]]
        for ext in (".hdr", ".sml"):
            sidecar = disp_paths["primary"] + ext
            if os.path.isfile(sidecar):
                source_files.append(sidecar)
        return {
            "run_dir": normalized_output_dir,
            "native_output_dir": get_run_native_output_dir(normalized_output_dir),
            "primary_file": disp_paths["primary"],
            "source_files": source_files,
            "promoted_files": [],
            "moved_entries": [],
        }

    native_output_dir = get_run_native_output_dir(normalized_output_dir)
    search_dirs = []
    if os.path.isdir(native_output_dir):
        search_dirs.append(native_output_dir)
    search_dirs.append(normalized_output_dir)

    last_error: Optional[Exception] = None
    result_files: Optional[Dict[str, Any]] = None
    for search_dir in search_dirs:
        try:
            result_files = _find_latest_envi_result(search_dir)
            break
        except Exception as exc:
            last_error = exc
    if not result_files:
        raise RuntimeError(
            f"Failed to locate ENVI displacement result under managed run directory: {normalized_output_dir}"
        ) from last_error

    return normalize_envi_run_layout(
        normalized_output_dir,
        primary_file=result_files["primary_file"],
        source_files=result_files["source_files"],
    )


async def _run_envi_runner_command(
    job: SystemJobORM,
    runner_cmd: List[str],
    *,
    absolute_timeout_seconds: int,
    keepalive_formatter: Optional[Callable[[Optional[Dict[str, Any]]], tuple[str, Optional[int]]]] = None,
    register_pid: Optional[Callable[[int], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    if not job.task_id:
        raise ValueError(f"{job.job_type} requires task_id for progress tracking.")

    formatter = keepalive_formatter or _format_envi_keepalive
    progress_file = _get_envi_progress_file(job.job_id)
    pid_ready = asyncio.Event()
    proc_state: Dict[str, Any] = {}
    loop = asyncio.get_running_loop()

    async def _task_keepalive():
        while True:
            await asyncio.sleep(30)
            try:
                progress = _read_progress_file(progress_file)
                message, progress_value = formatter(progress)
                await task_service.update_task(job.task_id, message=message, progress=progress_value)
            except Exception as keepalive_exc:
                print(f"[keepalive] WARNING: failed to update task {job.task_id}: {keepalive_exc}")

    def _run_with_monitoring() -> Dict[str, Any]:
        stdout_fd = None
        stderr_fd = None
        stdout_path = None
        stderr_path = None
        proc = None
        try:
            stdout_fd, stdout_path = tempfile.mkstemp(suffix="_stdout.txt")
            stderr_fd, stderr_path = tempfile.mkstemp(suffix="_stderr.txt")

            proc = subprocess.Popen(
                runner_cmd,
                stdout=stdout_fd,
                stderr=stderr_fd,
                cwd=_get_envi_runtime_cwd(),
                env=get_envi_runner_env(),
            )
            proc_state["pid"] = proc.pid
            loop.call_soon_threadsafe(pid_ready.set)

            os.close(stdout_fd)
            os.close(stderr_fd)

            absolute_start = time.time()
            last_activity = time.time()
            last_step_msg = ""

            while proc.poll() is None:
                time.sleep(_ENVI_MONITOR_INTERVAL)

                now = time.time()
                progress = _read_progress_file(progress_file)
                if progress:
                    ts = progress.get("timestamp", 0)
                    if ts > last_activity:
                        last_activity = ts
                    msg = progress.get("message", "")
                    if msg and msg != last_step_msg:
                        last_step_msg = msg

                output_dir = ""
                if progress and progress.get("output_dir"):
                    output_dir = progress["output_dir"]

                if output_dir:
                    dir_mtime = _scan_latest_mtime(output_dir)
                    if dir_mtime and dir_mtime > last_activity:
                        last_activity = dir_mtime

                if (now - last_activity) > _ENVI_FILE_STALE_SECONDS:
                    _kill_process_tree(proc.pid)
                    try:
                        proc.wait(timeout=15)
                    except Exception as exc:
                        print(f"[WARN] stale kill: proc.wait timeout — {exc}")
                    raise RuntimeError(
                        f"ENVI subprocess stale: no file activity for "
                        f"{int(now - last_activity)}s "
                        f"(threshold={_ENVI_FILE_STALE_SECONDS}s). "
                        f"Last step: {last_step_msg}"
                    )

                if (now - absolute_start) > absolute_timeout_seconds:
                    _kill_process_tree(proc.pid)
                    try:
                        proc.wait(timeout=15)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"ENVI subprocess exceeded absolute timeout: "
                        f"{int(now - absolute_start)}s > {absolute_timeout_seconds}s. "
                        f"Last step: {last_step_msg}"
                    )

            output_dir = ""
            progress = _read_progress_file(progress_file)
            if progress and progress.get("output_dir"):
                output_dir = progress["output_dir"]

            if output_dir:
                stable_count = 0
                prev_sizes: Dict[str, int] = {}
                wait_start = time.time()
                while stable_count < _ENVI_STABILITY_ROUNDS:
                    if (time.time() - wait_start) > _ENVI_STABILITY_MAX_WAIT:
                        break
                    time.sleep(_ENVI_STABILITY_CHECK_INTERVAL)
                    cur_sizes = _collect_file_sizes(output_dir)
                    if cur_sizes == prev_sizes:
                        stable_count += 1
                    else:
                        stable_count = 0
                        prev_sizes = cur_sizes

            with open(stdout_path, "r", encoding="utf-8", errors="replace") as fp:
                stdout = fp.read()
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as fp:
                stderr = fp.read()
        finally:
            for fd in (stdout_fd, stderr_fd):
                if isinstance(fd, int):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            for path in (stdout_path, stderr_path):
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

        if proc is None:
            raise RuntimeError("ENVI runner subprocess failed to start.")
        if proc.returncode != 0:
            raise RuntimeError(
                "ENVI runner subprocess failed. "
                f"rc={proc.returncode} "
                f"stderr={(stderr or '').strip()[:1200]!r}"
            )
        output = (stdout or "").strip()
        if not output:
            raise RuntimeError("ENVI runner subprocess returned empty output.")
        try:
            return json.loads(output.splitlines()[-1])
        except Exception as exc:
            raise RuntimeError(f"ENVI runner returned non-JSON output: {output[:1200]!r}") from exc

    keepalive_task = asyncio.create_task(_task_keepalive())
    runner_task = asyncio.create_task(asyncio.to_thread(_run_with_monitoring))
    try:
        if register_pid is not None:
            try:
                await asyncio.wait_for(pid_ready.wait(), timeout=30)
                pid_value = int(proc_state.get("pid") or 0)
                if pid_value > 0:
                    await register_pid(pid_value)
            except asyncio.TimeoutError:
                pass
        return await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass


async def _run_envi_workflow_job(
    job: SystemJobORM,
    workflow: str,
    start_message: str,
) -> None:
    """Run an ENVI workflow via subprocess with file-activity monitoring.

    Uses Popen + a monitoring loop that checks both the progress file
    and output directory file activity to determine subprocess liveness.
    Only kills the process when BOTH signals are stale.
    """
    if not job.task_id:
        raise ValueError(f"{job.job_type} requires task_id for progress tracking.")

    payload = job.payload or {}
    root_dir = payload.get("root_dir", "")
    num_to_process = int(payload.get("num_to_process", 0) or 0)
    timeout_seconds = payload.get("timeout_seconds")

    # Count Task_* folders to calculate dynamic absolute timeout
    task_folder_count = 0
    if root_dir and os.path.isdir(root_dir):
        task_folder_count = len(
            [d for d in glob.glob(os.path.join(root_dir, "Task_*")) if os.path.isdir(d)]
        )
    if num_to_process > 0:
        task_folder_count = min(task_folder_count, num_to_process)
    effective_absolute_timeout = _ENVI_PER_TASK_TIMEOUT * max(1, task_folder_count)

    await task_service.start_task(job.task_id, message=start_message)
    await task_service.update_task(
        job.task_id, progress=10,
        message=f"Launching ENVI worker subprocess... (tasks={task_folder_count}, timeout={effective_absolute_timeout}s)",
    )

    runner_cmd = build_envi_runner_command(
        "--workflow",
        workflow,
        "--root-dir",
        str(root_dir),
        "--num-to-process",
        str(num_to_process),
        "--job-id",
        str(job.job_id),
    )
    if timeout_seconds is not None:
        runner_cmd.extend(["--timeout-seconds", str(int(timeout_seconds))])

    progress_file = _get_envi_progress_file(job.job_id)

    async def _task_keepalive():
        """Periodically update task updated_at so zombie detection doesn't kill it."""
        while True:
            await asyncio.sleep(30)
            try:
                prog = _read_progress_file(progress_file)
                msg = "ENVI processing..."
                pct = None
                if prog:
                    step = prog.get("step", 0)
                    total = prog.get("total_steps", 6)
                    step_msg = prog.get("message", "")
                    pair_index = prog.get("pair_index", 0)
                    total_pairs = prog.get("total_pairs", 0)
                    pair_name = prog.get("pair_name", "")

                    # Build human-readable message
                    step_part = f"Step {step}/{total}: {step_msg}" if step_msg else f"Step {step}/{total}"
                    if total_pairs > 0 and pair_index > 0:
                        pair_part = f"对 {pair_index}/{total_pairs}"
                        if pair_name:
                            pair_part += f" ({pair_name})"
                        msg = f"{pair_part} · {step_part}"
                    else:
                        msg = step_part

                    # Map to overall progress:
                    # pair contributes (pair_index-1)/total_pairs of the 10-90% range,
                    # plus step/total_steps within that pair's slice.
                    if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
                        if total_pairs > 0 and pair_index > 0:
                            pair_frac = (pair_index - 1 + step / total) / total_pairs
                            pct = min(90, 10 + int(80 * pair_frac))
                        else:
                            pct = min(90, 10 + int(80 * step / total))
                await task_service.update_task(job.task_id, message=msg, progress=pct)
            except Exception as _ka_exc:
                print(f"[keepalive] WARNING: failed to update task {job.task_id}: {_ka_exc}")

    def _run_with_monitoring() -> Dict[str, Any]:
        # Use named temp files instead of PIPE to avoid buffer-full deadlock.
        # TemporaryFile on Windows creates non-inheritable handles, so we use
        # mkstemp + manual cleanup instead.
        stdout_path = None
        stderr_path = None
        try:
            stdout_fd, stdout_path = tempfile.mkstemp(suffix="_stdout.txt")
            stderr_fd, stderr_path = tempfile.mkstemp(suffix="_stderr.txt")

            proc = subprocess.Popen(
                runner_cmd,
                stdout=stdout_fd,
                stderr=stderr_fd,
                cwd=_get_envi_runtime_cwd(),
                env=get_envi_runner_env(),
            )
            # Close our copy of the fds; the child process has its own.
            os.close(stdout_fd)
            os.close(stderr_fd)

            absolute_start = time.time()
            last_activity = time.time()
            last_step_msg = ""

            while proc.poll() is None:
                time.sleep(_ENVI_MONITOR_INTERVAL)

                now = time.time()

                # --- Signal 1: progress file ---
                progress = _read_progress_file(progress_file)
                if progress:
                    ts = progress.get("timestamp", 0)
                    if ts > last_activity:
                        last_activity = ts
                    msg = progress.get("message", "")
                    if msg and msg != last_step_msg:
                        last_step_msg = msg

                # --- Signal 2: output directory file mtime ---
                output_dir = ""
                if progress and progress.get("output_dir"):
                    output_dir = progress["output_dir"]
                elif root_dir:
                    output_dir = root_dir

                if output_dir:
                    dir_mtime = _scan_latest_mtime(output_dir)
                    if dir_mtime and dir_mtime > last_activity:
                        last_activity = dir_mtime

                # --- Stale check ---
                if (now - last_activity) > _ENVI_FILE_STALE_SECONDS:
                    _kill_process_tree(proc.pid)
                    try:
                        proc.wait(timeout=15)
                    except Exception as exc:
                        print(f"[WARN] stale kill: proc.wait timeout — {exc}")
                    raise RuntimeError(
                        f"ENVI subprocess stale: no file activity for "
                        f"{int(now - last_activity)}s "
                        f"(threshold={_ENVI_FILE_STALE_SECONDS}s). "
                        f"Last step: {last_step_msg}"
                    )

                # --- Absolute timeout ---
                if (now - absolute_start) > effective_absolute_timeout:
                    _kill_process_tree(proc.pid)
                    try:
                        proc.wait(timeout=15)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"ENVI subprocess exceeded absolute timeout: "
                        f"{int(now - absolute_start)}s > {effective_absolute_timeout}s. "
                        f"Last step: {last_step_msg}"
                    )

            # --- Post-exit file stability check ---
            # envipyengine may return before ENVI finishes writing large
            # output files. Wait until all file sizes are stable.
            output_dir = ""
            progress = _read_progress_file(progress_file)
            if progress and progress.get("output_dir"):
                output_dir = progress["output_dir"]
            elif root_dir:
                output_dir = root_dir

            if output_dir:
                stable_count = 0
                prev_sizes: Dict[str, int] = {}
                wait_start = time.time()
                while stable_count < _ENVI_STABILITY_ROUNDS:
                    if (time.time() - wait_start) > _ENVI_STABILITY_MAX_WAIT:
                        break  # safety cap
                    time.sleep(_ENVI_STABILITY_CHECK_INTERVAL)
                    cur_sizes = _collect_file_sizes(output_dir)
                    if cur_sizes == prev_sizes:
                        stable_count += 1
                    else:
                        stable_count = 0
                        prev_sizes = cur_sizes

            # Process finished - read output from temp files
            with open(stdout_path, "r", encoding="utf-8", errors="replace") as f:
                stdout = f.read()
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                stderr = f.read()
        finally:
            for p in (stdout_path, stderr_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        if proc.returncode != 0:
            raise RuntimeError(
                "ENVI runner subprocess failed. "
                f"rc={proc.returncode} "
                f"stderr={(stderr or '').strip()[:1200]!r}"
            )
        output = (stdout or "").strip()
        if not output:
            raise RuntimeError("ENVI runner subprocess returned empty output.")
        try:
            return json.loads(output.splitlines()[-1])
        except Exception as exc:
            raise RuntimeError(
                f"ENVI runner returned non-JSON output: {output[:1200]!r}"
            ) from exc

    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        run_meta = await asyncio.to_thread(_run_with_monitoring)
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass
    await task_service.add_log(
        job.task_id,
        "INFO",
        (
            f"ENVI workflow completed. workflow={run_meta.get('workflow')} "
            f"duration={run_meta.get('duration_seconds')}s "
            f"summary={run_meta.get('summary')}"
        ),
    )
    publish_note = ""
    if workflow in {"dinsar", "dinsar_custom"}:
        output_dirs = _dedupe_existing_dirs(run_meta.get("output_dirs"))
        if output_dirs:
            normalized_output_dirs: List[str] = []
            for output_dir in output_dirs:
                layout_result = await asyncio.to_thread(_normalize_managed_envi_output_dir, output_dir)
                normalized_output_dirs.append(layout_result["run_dir"])
            output_dirs = _dedupe_existing_dirs(normalized_output_dirs)
            await task_service.update_task(
                job.task_id,
                progress=92,
                message=f"ENVI {workflow} completed, publishing result catalog...",
            )
            try:
                async with AsyncSessionLocal() as db:
                    publish_result = await result_catalog_service.publish_from_sources(
                        db,
                        output_dirs,
                    )
                    rebuild_result = None
                    if int(publish_result.get("processed", 0) or 0) > 0:
                        rebuild_result = await result_catalog_service.rebuild_catalog(
                            db,
                            full_rebuild=True,
                        )
                publish_note = (
                    f"; catalog published {publish_result.get('processed', 0)}"
                    f" package(s), issues {rebuild_result.get('issue_count', 0) if rebuild_result else 0}"
                )
                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    f"Auto-published ENVI results from {len(output_dirs)} directory(s).",
                )
            except Exception as exc:
                publish_note = f"; catalog publish failed: {exc}"
                await task_service.add_log(
                    job.task_id,
                    "WARNING",
                    f"Auto-publish ENVI results failed: {exc}",
                )
    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            f"ENVI {workflow} completed. "
            f"run_id={run_meta.get('run_id')} "
            f"log={run_meta.get('log_path')}{publish_note}"
        ),
    )


async def _run_dinsar_production_controller(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("IDL_RUN_DINSAR production controller requires task_id.")

    payload = job.payload or {}
    production_run_id = str(payload.get("production_run_id") or "").strip()
    if not production_run_id:
        raise ValueError("IDL_RUN_DINSAR production controller requires production_run_id.")

    async with AsyncSessionLocal() as db:
        run = await dinsar_production_service.get_run(production_run_id, db)
        if run is None:
            raise ValueError(f"D-InSAR production run not found: {production_run_id}")

        items = await dinsar_production_service.list_run_items(run.run_id, db)
        if not items:
            raise ValueError(f"D-InSAR production run has no items: {production_run_id}")

        workflow = "dinsar_custom" if str(run.mode or "").strip().lower() == "custom" else "dinsar"
        params = run.params_json or {}
        timeout_seconds_raw = params.get("timeout_seconds")
        timeout_seconds = int(timeout_seconds_raw) if timeout_seconds_raw not in (None, "") else None
        absolute_timeout_seconds = max(_ENVI_PER_TASK_TIMEOUT, int(timeout_seconds or 0)) if timeout_seconds else _ENVI_PER_TASK_TIMEOUT
        total_items = len(items)
        run_log = dinsar_production_service.append_run_log

        async def _refresh_cancel_state() -> bool:
            await db.refresh(run)
            current_task = await task_service.get_task(job.task_id)
            task_cancelled = bool(current_task and current_task.status == "CANCELLED")
            if task_cancelled and not run.cancel_requested:
                run.cancel_requested = True
                await db.commit()
            return bool(run.cancel_requested or task_cancelled)

        await task_service.start_task(
            job.task_id,
            message=f"Starting ENVI D-InSAR production run {run.run_id} ({total_items} items)...",
        )
        await task_service.add_log(
            job.task_id,
            "INFO",
            (
                f"D-InSAR production controller started. run_id={run.run_id} "
                f"profile={run.profile_code} mode={run.mode} items={total_items}"
            ),
        )
        await dinsar_production_service.mark_run_started(
            run,
            db=db,
            message=f"Preparing {total_items} D-InSAR item(s)",
        )
        run_log(run.run_id, f"[start] workflow={workflow} items={total_items} source_root={run.source_root}")

        successful_output_dirs: List[str] = []
        async with engine_lock_service.acquire("envi_taskengine"):
            await task_service.update_task(
                job.task_id,
                progress=5,
                message=f"ENVI engine acquired. Preparing {total_items} item(s)...",
            )
            for item_index, item in enumerate(items, start=1):
                if await _refresh_cancel_state():
                    await task_service.add_log(
                        job.task_id,
                        "WARNING",
                        f"Cancellation detected before item {item.task_alias or item.task_name}.",
                    )
                    break

                await db.refresh(item)
                if str(item.status or "").upper() in {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}:
                    if item.status == "COMPLETED" and item.latest_output_dir and os.path.isdir(item.latest_output_dir):
                        successful_output_dirs.append(item.latest_output_dir)
                    continue

                run_key = (
                    f"{build_run_key('sarscape', run.profile_code, started_at=datetime.utcnow())}"
                    f"_{item.id}_{uuid.uuid4().hex[:6]}"
                )
                execution = await dinsar_production_service.begin_item_execution(
                    run=run,
                    item=item,
                    run_key=run_key,
                    db=db,
                )

                runner_cmd = build_envi_runner_command(
                    "--workflow",
                    workflow,
                    "--task-dir",
                    str(item.source_task_dir),
                    "--output-dir",
                    str(execution.output_dir),
                    "--source-root",
                    str(run.source_root),
                    "--job-id",
                    str(job.job_id),
                    "--run-key",
                    str(run_key),
                    "--profile-code",
                    str(run.profile_code),
                )
                if timeout_seconds is not None:
                    runner_cmd.extend(["--timeout-seconds", str(timeout_seconds)])

                def _keepalive_formatter(progress: Optional[Dict[str, Any]]) -> tuple[str, Optional[int]]:
                    message, progress_value = _format_envi_keepalive(progress)
                    prefix = f"{item_index}/{total_items} {item.task_alias or item.task_name}: "
                    if progress_value is None:
                        return prefix + message, None
                    local_fraction = max(0.0, min(1.0, progress_value / 100.0))
                    overall = ((item_index - 1) + local_fraction) / max(1, total_items)
                    return prefix + message, min(98, max(1, int(overall * 100)))

                async def _register_pid(pid: int) -> None:
                    async with AsyncSessionLocal() as pid_db:
                        await dinsar_production_service.set_execution_pid(
                            execution.execution_id,
                            pid,
                            db=pid_db,
                        )

                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    (
                        f"[{item_index}/{total_items}] Launching {item.task_alias or item.task_name} "
                        f"-> {execution.output_dir}"
                    ),
                )
                run_log(
                    run.run_id,
                    (
                        f"[item-start] {item_index}/{total_items} "
                        f"{item.task_alias or item.task_name} run_key={run_key} output={execution.output_dir}"
                    ),
                )

                try:
                    run_meta = await _run_envi_runner_command(
                        job,
                        runner_cmd,
                        absolute_timeout_seconds=absolute_timeout_seconds,
                        keepalive_formatter=_keepalive_formatter,
                        register_pid=_register_pid,
                    )
                    layout_result = await asyncio.to_thread(
                        _normalize_managed_envi_output_dir,
                        execution.output_dir,
                    )
                    metrics = {
                        "duration_seconds": run_meta.get("duration_seconds"),
                        "summary": run_meta.get("summary") or {},
                    }
                    if run_meta.get("task_results"):
                        metrics["task_result"] = run_meta["task_results"][0]

                    manifest_path = await asyncio.to_thread(
                        dinsar_production_service.build_execution_manifest,
                        run=run,
                        item=item,
                        execution=execution,
                        primary_file=layout_result["primary_file"],
                        source_files=layout_result["source_files"],
                        native_output_dir=layout_result["native_output_dir"],
                        metrics=metrics,
                    )
                    await asyncio.to_thread(
                        dinsar_production_service.write_current_pointer,
                        run=run,
                        item=item,
                        execution=execution,
                        manifest_path=manifest_path,
                        primary_file=layout_result["primary_file"],
                        source_files=layout_result["source_files"],
                        native_output_dir=layout_result["native_output_dir"],
                    )
                    await dinsar_production_service.mark_item_completed(
                        run=run,
                        item=item,
                        execution=execution,
                        manifest_path=manifest_path,
                        metrics=metrics,
                        db=db,
                    )
                    successful_output_dirs.append(execution.output_dir)
                    await task_service.add_log(
                        job.task_id,
                        "INFO",
                        f"[{item_index}/{total_items}] Completed {item.task_alias or item.task_name}",
                    )
                    run_log(
                        run.run_id,
                        f"[item-ok] {item_index}/{total_items} {item.task_alias or item.task_name}",
                    )
                except Exception as exc:
                    cancelled = await _refresh_cancel_state()
                    if cancelled:
                        await dinsar_production_service.mark_item_cancelled(
                            run=run,
                            item=item,
                            execution=execution,
                            error_message=f"Cancelled while processing {item.task_alias or item.task_name}",
                            db=db,
                        )
                        await task_service.add_log(
                            job.task_id,
                            "WARNING",
                            f"[{item_index}/{total_items}] Cancelled {item.task_alias or item.task_name}: {exc}",
                        )
                        run_log(
                            run.run_id,
                            f"[item-cancelled] {item_index}/{total_items} {item.task_alias or item.task_name}: {exc}",
                        )
                        break

                    error_message = str(exc)
                    await dinsar_production_service.mark_item_failed(
                        run=run,
                        item=item,
                        execution=execution,
                        error_message=error_message,
                        db=db,
                    )
                    await task_service.add_log(
                        job.task_id,
                        "WARNING",
                        f"[{item_index}/{total_items}] Failed {item.task_alias or item.task_name}: {error_message}",
                    )
                    run_log(
                        run.run_id,
                        f"[item-failed] {item_index}/{total_items} {item.task_alias or item.task_name}: {error_message}",
                    )
                finally:
                    _clear_envi_progress_file(job.job_id)

        publish_result = None
        publish_error = None
        publish_dirs = _dedupe_existing_dirs(successful_output_dirs)
        if publish_dirs:
            await task_service.update_task(
                job.task_id,
                progress=99,
                message=f"Publishing {len(publish_dirs)} successful result package(s)...",
            )
            try:
                publish_result = await result_catalog_service.publish_from_sources(db, publish_dirs)
                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    f"Published {publish_result.get('processed', 0)} result package(s).",
                )
                run_log(
                    run.run_id,
                    f"[publish] processed={publish_result.get('processed', 0)} failed={publish_result.get('failed', 0)}",
                )
            except Exception as exc:
                publish_error = str(exc)
                await task_service.add_log(
                    job.task_id,
                    "WARNING",
                    f"Result catalog publish failed: {publish_error}",
                )
                run_log(run.run_id, f"[publish-failed] {publish_error}")

        cancelled = await _refresh_cancel_state()
        await db.refresh(run)
        latest_message = ""
        final_status = "COMPLETED"
        if publish_error:
            final_status = "FAILED"
            latest_message = f"Result catalog publish failed: {publish_error}"
        elif cancelled:
            final_status = "CANCELLED"
            latest_message = (
                f"D-InSAR production cancelled. completed={run.completed_items} "
                f"failed={run.failed_items} total={run.total_items}"
            )
        elif int(run.failed_items or 0) > 0:
            final_status = "FAILED"
            latest_message = (
                f"D-InSAR production finished with failures. completed={run.completed_items} "
                f"failed={run.failed_items} total={run.total_items}"
            )
        else:
            latest_message = (
                f"D-InSAR production completed. completed={run.completed_items} "
                f"failed={run.failed_items} total={run.total_items}"
            )

        summary_payload = {
            "workflow": workflow,
            "engine_code": run.engine_code,
            "profile_code": run.profile_code,
            "mode": run.mode,
            "total_items": run.total_items,
            "completed_items": run.completed_items,
            "failed_items": run.failed_items,
            "skipped_items": run.skipped_items,
            "publish": publish_result,
            "publish_error": publish_error,
            "published_output_dirs": publish_dirs,
        }
        await dinsar_production_service.finalize_run(
            run,
            db=db,
            status=final_status,
            summary_payload=summary_payload,
            latest_message=latest_message,
        )
        run_log(run.run_id, f"[finish] status={final_status} message={latest_message}")

        if final_status == "COMPLETED":
            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                progress=100,
                message=latest_message,
            )
            return

        task_status = "CANCELLED" if final_status == "CANCELLED" else "FAILED"
        await task_service.update_task(
            job.task_id,
            status=task_status,
            progress=100,
            message=latest_message,
        )
        raise RuntimeError(latest_message)


async def _handle_idl_run_import(job: SystemJobORM) -> None:
    async with engine_lock_service.acquire("envi_taskengine"):
        await _run_envi_workflow_job(
            job,
            workflow="import",
            start_message="Starting ENVI Import workflow...",
        )


async def _handle_idl_run_dinsar(job: SystemJobORM) -> None:
    payload = job.payload or {}
    production_run_id = str(payload.get("production_run_id") or "").strip()
    if production_run_id:
        try:
            await _run_dinsar_production_controller(job)
        except Exception as exc:
            latest_message = f"D-InSAR production controller failed: {exc}"
            try:
                async with AsyncSessionLocal() as db:
                    run = await dinsar_production_service.get_run(production_run_id, db)
                    if run is not None and str(run.status or "").strip().upper() not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        summary_payload = dict(run.summary_json or {})
                        summary_payload["controller_error"] = str(exc)
                        await dinsar_production_service.finalize_run(
                            run,
                            db=db,
                            status="FAILED",
                            summary_payload=summary_payload,
                            latest_message=latest_message,
                        )
                        dinsar_production_service.append_run_log(
                            run.run_id,
                            f"[controller-failed] {exc}",
                        )
            except Exception:
                pass

            try:
                current_task = await task_service.get_task(job.task_id)
                if current_task and current_task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    await task_service.add_log(job.task_id, "ERROR", latest_message)
                    await task_service.update_task(
                        job.task_id,
                        status="FAILED",
                        progress=100,
                        message=latest_message,
                    )
            except Exception:
                pass
            raise
        return

    mode = payload.get("mode", "metatask")
    workflow = "dinsar_custom" if mode == "custom" else "dinsar"
    async with engine_lock_service.acquire("envi_taskengine"):
        await _run_envi_workflow_job(
            job,
            workflow=workflow,
            start_message=f"Starting ENVI D-InSAR workflow (mode={mode})...",
        )


async def _handle_queued_engine_run(
    job: SystemJobORM,
    *,
    engine_title: str,
    fallback_timeout_seconds: int,
) -> None:
    """Run a queued D-InSAR engine task through the shared WSL execution path."""
    payload = job.payload or {}
    engine_code = payload.get("engine_code", "isce2")
    profile = payload.get("profile", "lt1_stripmap")
    root_dir = payload.get("root_dir", "")
    num_to_process = payload.get("num_to_process", 0)
    timeout_seconds = payload.get("timeout_seconds")
    extra = payload.get("extra", {})
    selected_task_count = max(1, int(extra.get("__validated_task_count") or 0 or 1))
    rerun_mode = str(payload.get("rerun_mode") or extra.get("__rerun_mode") or "rerun_all").strip()
    skipped_completed_count = int(extra.get("__skipped_completed_count") or 0)
    pair_timeout_seconds = int(timeout_seconds or fallback_timeout_seconds)

    await task_service.start_task(
        job.task_id,
        message=f"[{engine_code}/{profile}] 启动 {engine_title} 处理...",
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        (
            f"{engine_title} job accepted. root_dir={root_dir}, profile={profile}, "
            f"rerun_mode={rerun_mode}, timeout={pair_timeout_seconds}s, extra={extra}"
        ),
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        (
            f"{engine_title} batch contains {selected_task_count} pair task(s). "
            f"Pairs run sequentially and each pair uses timeout={pair_timeout_seconds}s."
            f"{f' Skipped completed={skipped_completed_count}.' if skipped_completed_count > 0 else ''}"
        ),
    )
    from ..dinsar_engines.base import RunRequest
    from ..dinsar_engines import registry

    engine = registry.get_engine(engine_code)
    if not engine:
        raise RuntimeError(f"引擎 '{engine_code}' 未注册")

    loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
    progress_state: Dict[str, Any] = {
        "progress": 5,
        "message": f"[{engine_code}/{profile}] Running in WSL...",
        "pair_index": 0,
        "pair_total": selected_task_count,
        "pair_label": "",
        "pair_started_monotonic": None,
    }

    def _emit_progress(event: Dict[str, Any]) -> None:
        if not event:
            return
        try:
            loop.call_soon_threadsafe(progress_queue.put_nowait, dict(event))
        except RuntimeError:
            return

    request = RunRequest(
        engine_code=engine_code,
        profile=profile,
        root_dir=root_dir,
        job_id=job.job_id,
        num_to_process=num_to_process,
        timeout_seconds=timeout_seconds,
        extra=extra,
        progress_callback=_emit_progress,
    )

    await task_service.update_task(
        job.task_id,
        progress=5,
        message=f"[{engine_code}/{profile}] 正在执行，请等待...",
    )

    async def _consume_progress() -> None:
        while True:
            event = await progress_queue.get()
            if event is None:
                return

            event_type = str(event.get("event") or "").strip().lower()
            pair_total = max(1, int(event.get("pair_total") or progress_state["pair_total"] or 1))
            pair_index = max(0, int(event.get("pair_index") or 0))
            task_label = str(event.get("task_alias") or event.get("task_name") or "").strip()

            if event_type == "log":
                level = str(event.get("level") or "INFO").strip().upper()
                if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
                    level = "INFO"
                source = str(event.get("source") or "").strip()
                message = str(event.get("message") or "").strip()
                if not message:
                    continue
                label = task_label or str(progress_state.get("pair_label") or "").strip() or "pair"
                prefix = f"{engine_title} {pair_index}/{pair_total} {label}"
                if source:
                    prefix = f"{prefix} {source}"
                await task_service.add_log(
                    job.task_id,
                    level,
                    f"{prefix}: {message}",
                )
                continue

            if event_type == "pair_started":
                progress = min(
                    90,
                    max(
                        int(progress_state["progress"] or 5),
                        5 + int((max(pair_index - 1, 0) / pair_total) * 80),
                    ),
                )
                progress_state.update(
                    {
                        "progress": progress,
                        "pair_index": pair_index,
                        "pair_total": pair_total,
                        "pair_label": task_label,
                        "pair_started_monotonic": time.monotonic(),
                        "message": f"[{engine_code}/{profile}] Running {pair_index}/{pair_total}: {task_label}",
                    }
                )
                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    (
                        f"{engine_title} pair {pair_index}/{pair_total} started: {task_label} "
                        f"(work_dir={event.get('work_dir')})"
                    ),
                )
                await task_service.update_task(
                    job.task_id,
                    progress=progress_state["progress"],
                    message=progress_state["message"],
                )
                continue

            if event_type == "pair_finished":
                success = bool(event.get("success"))
                returncode = int(event.get("returncode") or 0)
                progress = min(
                    90,
                    max(
                        int(progress_state["progress"] or 5),
                        5 + int((max(pair_index, 0) / pair_total) * 80) if success else int(progress_state["progress"] or 5),
                    ),
                )
                progress_state.update(
                    {
                        "progress": progress,
                        "pair_index": pair_index,
                        "pair_total": pair_total,
                        "pair_label": task_label,
                        "pair_started_monotonic": None,
                    }
                )
                if success:
                    progress_state["message"] = f"[{engine_code}/{profile}] Finished {pair_index}/{pair_total}: {task_label}"
                    await task_service.add_log(
                        job.task_id,
                        "INFO",
                        f"{engine_title} pair {pair_index}/{pair_total} completed: {task_label}",
                    )
                else:
                    error_text = str(event.get("error") or "").strip()
                    timeout_note = " (timeout)" if returncode == -1 else ""
                    progress_state["message"] = f"[{engine_code}/{profile}] Failed {pair_index}/{pair_total}: {task_label}"
                    await task_service.add_log(
                        job.task_id,
                        "WARNING",
                        (
                            f"{engine_title} pair {pair_index}/{pair_total} failed{timeout_note}: "
                            f"{task_label} (rc={returncode})"
                            f"{f', error={error_text}' if error_text else ''}"
                        ),
                    )
                    if pair_index < pair_total:
                        await task_service.add_log(
                            job.task_id,
                            "WARNING",
                            f"{engine_title} will continue with the next pair ({pair_index + 1}/{pair_total}).",
                        )
                await task_service.update_task(
                    job.task_id,
                    progress=progress_state["progress"],
                    message=progress_state["message"],
                )

    async def _task_keepalive():
        while True:
            await asyncio.sleep(30)
            try:
                message = str(progress_state.get("message") or f"[{engine_code}/{profile}] Running in WSL...")
                started_monotonic = progress_state.get("pair_started_monotonic")
                if isinstance(started_monotonic, (int, float)):
                    elapsed_seconds = max(0, int(time.monotonic() - float(started_monotonic)))
                    message = f"{message} (elapsed={elapsed_seconds}s)"
                await task_service.update_task(
                    job.task_id,
                    progress=int(progress_state.get("progress") or 5),
                    message=message,
                )
            except Exception as exc:
                print(f"[keepalive] WARNING: failed to update task {job.task_id}: {exc}")

    progress_task = asyncio.create_task(_consume_progress())
    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        result = await asyncio.to_thread(engine.run, request)
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass
        await progress_queue.put(None)
        await progress_task

    detail = result.detail or {}

    if detail.get("mode") or detail.get("task_count") is not None:
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"{engine_title} run mode={detail.get('mode', 'unknown')}, task_count={detail.get('task_count', 0)}",
        )

    for invalid in detail.get("invalid_candidates", []) or []:
        await task_service.add_log(
            job.task_id,
            "WARNING",
            f"Skipped invalid task candidate: {invalid.get('name')} missing {invalid.get('missing_subdirs')}",
        )

    for item in detail.get("task_results", []) or []:
        level = "INFO" if item.get("success") else "WARNING"
        await task_service.add_log(
            job.task_id,
            level,
            (
                f"Task {item.get('task_name')} "
                f"{'completed' if item.get('success') else 'failed'} "
                f"(rc={item.get('returncode')}, dir={item.get('task_dir')})"
            ),
        )
        if item.get("command"):
            await task_service.add_log(
                job.task_id,
                "INFO",
                f"WSL command [{item.get('task_name')}]: {item.get('command')}",
            )
        if item.get("runtime_id"):
            await task_service.add_log(
                job.task_id,
                "INFO",
                f"WSL runtime [{item.get('task_name')}]: {item.get('runtime_id')}",
            )
        if item.get("manifest_path_windows"):
            await task_service.add_log(
                job.task_id,
                "INFO",
                f"WSL manifest [{item.get('task_name')}]: {item.get('manifest_path_windows')}",
            )
        if item.get("stdout_tail"):
            await task_service.add_log(
                job.task_id,
                "INFO",
                f"WSL stdout tail [{item.get('task_name')}]:\n{item.get('stdout_tail')}",
            )
        if item.get("stderr_tail"):
            await task_service.add_log(
                job.task_id,
                "WARNING",
                f"WSL stderr tail [{item.get('task_name')}]:\n{item.get('stderr_tail')}",
            )

    if detail.get("wsl_task_dir"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL task dir: {detail['wsl_task_dir']}",
        )
    if detail.get("wsl_work_root"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL work root: {detail['wsl_work_root']}",
        )
    if detail.get("wsl_output_root"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL output root: {detail['wsl_output_root']}",
        )
    if detail.get("wsl_orbit_pool"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL orbit pool: {detail['wsl_orbit_pool']}",
        )
    if detail.get("wsl_dem"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL DEM: {detail['wsl_dem']}",
        )
    if detail.get("command"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL command: {detail['command']}",
        )
    if detail.get("runtime_id"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL runtime: {detail['runtime_id']}",
        )
    if detail.get("manifest_path_windows"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL manifest: {detail['manifest_path_windows']}",
        )
    if detail.get("stdout_tail"):
        await task_service.add_log(
            job.task_id,
            "INFO",
            f"WSL stdout tail:\n{detail['stdout_tail']}",
        )
    if detail.get("stderr_tail"):
        await task_service.add_log(
            job.task_id,
            "WARNING",
            f"WSL stderr tail:\n{detail['stderr_tail']}",
        )

    if result.success:
        output_dirs = _dedupe_existing_dirs(result.output_dirs)
        if output_dirs:
            await task_service.update_task(
                job.task_id,
                progress=92,
                message=f"[{engine_code}/{profile}] Production finished, publishing result catalog...",
            )
            try:
                async with AsyncSessionLocal() as db:
                    publish_result = await result_catalog_service.publish_from_sources(
                        db,
                        output_dirs,
                    )
                    rebuild_result = None
                    if int(publish_result.get("processed", 0) or 0) > 0:
                        rebuild_result = await result_catalog_service.rebuild_catalog(
                            db,
                            full_rebuild=True,
                        )
                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    (
                        f"Auto-published {engine_title} results from {len(output_dirs)} directory(s). "
                        f"processed={publish_result.get('processed', 0)} "
                        f"issues={rebuild_result.get('issue_count', 0) if rebuild_result else 0}"
                    ),
                )
                if int(publish_result.get("processed", 0) or 0) <= 0:
                    await task_service.add_log(
                        job.task_id,
                        "WARNING",
                        (
                            f"No publishable {engine_title} result bundle was detected under "
                            f"{len(output_dirs)} output director"
                            f"{'y' if len(output_dirs) == 1 else 'ies'}."
                        ),
                    )
            except Exception as exc:
                await task_service.add_log(
                    job.task_id,
                    "WARNING",
                    f"Auto-publish {engine_title} results failed: {exc}",
                )

    if result.success:
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            progress=100,
            message=(
                f"[{engine_code}/{profile}] 完成，"
                f"成功 {result.pairs_processed} 对，失败 {result.pairs_failed} 对"
            ),
        )
    else:
        error_text = (result.error or "未知错误").strip()
        error_summary_lines = [line.strip() for line in error_text.splitlines() if line.strip()]
        error_summary = error_summary_lines[-1] if error_summary_lines else "未知错误"
        if "Work directory already exists" in error_text:
            error_summary += "；请重试并启用 force 以重建工作目录"
        raise RuntimeError(
            f"[{engine_code}/{profile}] 执行失败：{error_summary}"
        )


async def _run_wsl_dinsar_production_controller(
    job: SystemJobORM,
    *,
    engine_code: str,
    engine_title: str,
    fallback_timeout_seconds: int,
) -> None:
    if not job.task_id:
        raise ValueError(f"{engine_title} production controller requires task_id.")

    payload = job.payload or {}
    production_run_id = str(payload.get("production_run_id") or "").strip()
    if not production_run_id:
        raise ValueError(f"{engine_title} production controller requires production_run_id.")

    from ..dinsar_engines import registry
    from ..dinsar_engines.base import RunRequest

    engine = registry.get_engine(engine_code)
    if engine is None:
        raise RuntimeError(f"Engine '{engine_code}' is not registered.")

    async with AsyncSessionLocal() as db:
        run = await dinsar_production_service.get_run(production_run_id, db)
        if run is None:
            raise ValueError(f"D-InSAR production run not found: {production_run_id}")

        items = await dinsar_production_service.list_run_items(run.run_id, db)
        if not items:
            raise ValueError(f"D-InSAR production run has no items: {production_run_id}")

        params = run.params_json or {}
        user_extra = dict(params.get("extra") or {})
        timeout_seconds_raw = params.get("timeout_seconds")
        if timeout_seconds_raw not in (None, ""):
            per_task_timeout = int(timeout_seconds_raw)
        else:
            per_task_timeout = int(
                getattr(engine, "default_timeout_seconds", None)
                or fallback_timeout_seconds
                or 0
            )
        total_items = len(items)
        run_log = dinsar_production_service.append_run_log

        async def _refresh_cancel_state() -> bool:
            await db.refresh(run)
            current_task = await task_service.get_task(job.task_id)
            task_cancelled = bool(current_task and current_task.status == "CANCELLED")
            if task_cancelled and not run.cancel_requested:
                run.cancel_requested = True
                await db.commit()
            return bool(run.cancel_requested or task_cancelled)

        await task_service.start_task(
            job.task_id,
            message=f"Starting {engine_title} D-InSAR production run {run.run_id} ({total_items} items)...",
        )
        await task_service.add_log(
            job.task_id,
            "INFO",
            (
                f"{engine_title} D-InSAR production controller started. run_id={run.run_id} "
                f"profile={run.profile_code} items={total_items}"
            ),
        )
        await dinsar_production_service.mark_run_started(
            run,
            db=db,
            message=f"Preparing {total_items} {engine_title} item(s)",
        )
        run_log(
            run.run_id,
            f"[start] engine={engine_code} items={total_items} source_root={run.source_root}",
        )

        successful_output_dirs: List[str] = []
        async with engine_lock_service.acquire(f"wsl_dinsar_{engine_code}"):
            await task_service.update_task(
                job.task_id,
                progress=5,
                message=f"{engine_title} engine acquired. Preparing {total_items} item(s)...",
            )
            for item_index, item in enumerate(items, start=1):
                if await _refresh_cancel_state():
                    await task_service.add_log(
                        job.task_id,
                        "WARNING",
                        f"Cancellation detected before item {item.task_alias or item.task_name}.",
                    )
                    break

                await db.refresh(item)
                if str(item.status or "").upper() in {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}:
                    if item.status == "COMPLETED" and item.latest_output_dir and os.path.isdir(item.latest_output_dir):
                        successful_output_dirs.append(item.latest_output_dir)
                    continue

                run_key = (
                    f"{build_run_key(engine_code, run.profile_code, started_at=datetime.utcnow())}"
                    f"_{item.id}_{uuid.uuid4().hex[:6]}"
                )
                execution = await dinsar_production_service.begin_item_execution(
                    run=run,
                    item=item,
                    run_key=run_key,
                    db=db,
                )

                managed_run_dir = os.path.normpath(execution.output_dir)
                managed_native_output_dir = os.path.join(managed_run_dir, "native")
                if engine_code == "landsar":
                    landsar_work_root = str(getattr(settings, "LANDSAR_WORK_ROOT", "") or "").strip()
                    if landsar_work_root:
                        managed_native_output_dir = os.path.normpath(
                            os.path.join(landsar_work_root, run_key, "native")
                        )
                managed_work_dir = os.path.join(managed_native_output_dir, "workflow")
                managed_export_dir = os.path.join(managed_native_output_dir, "export")
                managed_orbit_output_dir = os.path.join(managed_work_dir, "orbits")
                item_label = item.task_alias or item.task_name
                base_progress = min(95, 5 + int(((item_index - 1) / max(1, total_items)) * 90))
                progress_state: Dict[str, Any] = {
                    "progress": base_progress,
                    "message": f"[{engine_code}/{run.profile_code}] Running {item_index}/{total_items}: {item_label}",
                    "started_monotonic": time.monotonic(),
                }
                progress_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
                loop = asyncio.get_running_loop()

                def _emit_progress(event: Dict[str, Any]) -> None:
                    if not event:
                        return
                    try:
                        loop.call_soon_threadsafe(progress_queue.put_nowait, dict(event))
                    except RuntimeError:
                        return

                async def _consume_progress() -> None:
                    while True:
                        event = await progress_queue.get()
                        if event is None:
                            return
                        event_type = str(event.get("event") or "").strip().lower()
                        if event_type == "log":
                            level = str(event.get("level") or "INFO").strip().upper()
                            if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
                                level = "INFO"
                            source = str(event.get("source") or "").strip()
                            message = str(event.get("message") or "").strip()
                            if message:
                                prefix = f"[{item_index}/{total_items}] {engine_title} {item_label}"
                                if source:
                                    prefix = f"{prefix} {source}"
                                await task_service.add_log(
                                    job.task_id,
                                    level,
                                    f"{prefix}: {message}",
                                )
                        elif event_type == "pair_started":
                            progress_state["message"] = (
                                f"[{engine_code}/{run.profile_code}] Running "
                                f"{item_index}/{total_items}: {item_label}"
                            )
                            progress_state["started_monotonic"] = time.monotonic()
                            await task_service.add_log(
                                job.task_id,
                                "INFO",
                                f"[{item_index}/{total_items}] {engine_title} started {item_label}",
                            )
                        elif event_type == "pair_finished":
                            if bool(event.get("success")):
                                progress_state["progress"] = min(
                                    98,
                                    5 + int((item_index / max(1, total_items)) * 90),
                                )
                                progress_state["message"] = (
                                    f"[{engine_code}/{run.profile_code}] Finished "
                                    f"{item_index}/{total_items}: {item_label}"
                                )
                            else:
                                progress_state["message"] = (
                                    f"[{engine_code}/{run.profile_code}] Failed "
                                    f"{item_index}/{total_items}: {item_label}"
                                )

                async def _task_keepalive() -> None:
                    while True:
                        await asyncio.sleep(30)
                        try:
                            message = str(progress_state.get("message") or "")
                            started_monotonic = progress_state.get("started_monotonic")
                            if isinstance(started_monotonic, (int, float)):
                                elapsed_seconds = max(0, int(time.monotonic() - float(started_monotonic)))
                                message = f"{message} (elapsed={elapsed_seconds}s)"
                            await task_service.update_task(
                                job.task_id,
                                progress=int(progress_state.get("progress") or base_progress),
                                message=message,
                            )
                        except Exception as exc:
                            logger.warning("keepalive update failed for %s item %s: %s", engine_title, item_label, exc)

                progress_task = asyncio.create_task(_consume_progress())
                keepalive_task = asyncio.create_task(_task_keepalive())
                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    f"[{item_index}/{total_items}] Launching {item_label} -> {managed_run_dir}",
                )
                run_log(
                    run.run_id,
                    f"[item-start] {item_index}/{total_items} {item_label} run_key={run_key} output={managed_run_dir}",
                )

                request = RunRequest(
                    engine_code=engine_code,
                    profile=run.profile_code,
                    root_dir=str(item.source_task_dir),
                    job_id=job.job_id,
                    num_to_process=1,
                    timeout_seconds=per_task_timeout or None,
                    extra={
                        **user_extra,
                        "__managed_run_dir": managed_run_dir,
                        "__managed_native_output_dir": managed_native_output_dir,
                        "__managed_work_dir": managed_work_dir,
                        "__managed_export_dir": managed_export_dir,
                        "__managed_orbit_output_dir": managed_orbit_output_dir,
                        "__managed_run_key": run_key,
                        "__source_root_override": run.source_root,
                        "__rerun_mode": "rerun_all",
                    },
                    progress_callback=_emit_progress,
                )

                result = None
                run_exception_text = ""
                try:
                    result = await asyncio.to_thread(engine.run, request)
                except Exception as exc:
                    run_exception_text = str(exc)
                finally:
                    keepalive_task.cancel()
                    try:
                        await keepalive_task
                    except asyncio.CancelledError:
                        pass
                    await progress_queue.put(None)
                    await progress_task

                detail = result.detail or {} if result else {}
                task_result = ((detail.get("task_results") or [{}])[0]) if result else {}

                try:
                    result_error = str(result.error or "").strip() if result else ""
                    result_success = bool(result.success) if result else False
                    if not result or not result_success or not bool(task_result.get("success", result_success)):
                        error_message = (
                            str(task_result.get("error") or "").strip()
                            or result_error
                            or run_exception_text
                            or str(task_result.get("stderr_tail") or "").strip()
                            or f"{engine_title} run failed."
                        )
                        raise RuntimeError(error_message)

                    run_dir = os.path.normpath(
                        str(task_result.get("run_dir") or task_result.get("output_dir") or execution.output_dir)
                    )
                    if run_dir != managed_run_dir:
                        raise RuntimeError(
                            f"{engine_title} managed run dir mismatch: expected {managed_run_dir}, got {run_dir}"
                        )

                    primary_file = str(task_result.get("primary_file") or "").strip()
                    source_files = [
                        str(path)
                        for path in (task_result.get("source_files") or [])
                        if str(path or "").strip()
                    ]
                    native_output_dir = str(
                        task_result.get("native_output_dir") or managed_native_output_dir
                    ).strip() or managed_native_output_dir
                    if not primary_file or not os.path.isfile(primary_file):
                        raise RuntimeError(f"{engine_title} primary output is missing: {primary_file or '<empty>'}")
                    if not source_files:
                        source_files = [primary_file]

                    metrics = {
                        "result_detail": detail,
                        "task_result": task_result,
                    }
                    manifest_path = await asyncio.to_thread(
                        dinsar_production_service.build_execution_manifest,
                        run=run,
                        item=item,
                        execution=execution,
                        primary_file=primary_file,
                        source_files=source_files,
                        native_output_dir=native_output_dir,
                        metrics=metrics,
                    )
                    await asyncio.to_thread(
                        dinsar_production_service.write_current_pointer,
                        run=run,
                        item=item,
                        execution=execution,
                        manifest_path=manifest_path,
                        primary_file=primary_file,
                        source_files=source_files,
                        native_output_dir=native_output_dir,
                    )
                    await dinsar_production_service.mark_item_completed(
                        run=run,
                        item=item,
                        execution=execution,
                        manifest_path=manifest_path,
                        metrics=metrics,
                        db=db,
                    )
                    successful_output_dirs.append(managed_run_dir)
                    await task_service.add_log(
                        job.task_id,
                        "INFO",
                        f"[{item_index}/{total_items}] Completed {item_label}",
                    )
                    run_log(run.run_id, f"[item-ok] {item_index}/{total_items} {item_label}")
                except Exception as exc:
                    cancelled = await _refresh_cancel_state()
                    error_message = str(exc)
                    if cancelled:
                        await dinsar_production_service.mark_item_cancelled(
                            run=run,
                            item=item,
                            execution=execution,
                            error_message=f"Cancelled while processing {item_label}",
                            db=db,
                        )
                        await task_service.add_log(
                            job.task_id,
                            "WARNING",
                            f"[{item_index}/{total_items}] Cancelled {item_label}: {error_message}",
                        )
                        run_log(
                            run.run_id,
                            f"[item-cancelled] {item_index}/{total_items} {item_label}: {error_message}",
                        )
                        break

                    await dinsar_production_service.mark_item_failed(
                        run=run,
                        item=item,
                        execution=execution,
                        error_message=error_message,
                        db=db,
                    )
                    await task_service.add_log(
                        job.task_id,
                        "WARNING",
                        f"[{item_index}/{total_items}] Failed {item_label}: {error_message}",
                    )
                    run_log(
                        run.run_id,
                        f"[item-failed] {item_index}/{total_items} {item_label}: {error_message}",
                    )
                    if task_result.get("command"):
                        await task_service.add_log(
                            job.task_id,
                            "INFO",
                            f"{engine_title} command [{item_label}]: {task_result.get('command')}",
                        )
                    if task_result.get("stdout_tail"):
                        await task_service.add_log(
                            job.task_id,
                            "INFO",
                            f"{engine_title} stdout tail [{item_label}]:\n{task_result.get('stdout_tail')}",
                        )
                    if task_result.get("stderr_tail"):
                        await task_service.add_log(
                            job.task_id,
                            "WARNING",
                            f"{engine_title} stderr tail [{item_label}]:\n{task_result.get('stderr_tail')}",
                        )

        publish_result = None
        rebuild_result = None
        publish_error = None
        publish_dirs = _dedupe_existing_dirs(successful_output_dirs)
        if publish_dirs:
            await task_service.update_task(
                job.task_id,
                progress=99,
                message=f"Publishing {len(publish_dirs)} successful {engine_title} result package(s)...",
            )
            try:
                publish_result = await result_catalog_service.publish_from_sources(db, publish_dirs)
                processed_count = int(publish_result.get("processed", 0) or 0)
                failed_count = int(publish_result.get("failed", 0) or 0)
                expected_count = len(publish_dirs)
                if processed_count > 0:
                    rebuild_result = await result_catalog_service.rebuild_catalog(
                        db,
                        full_rebuild=True,
                    )
                if processed_count != expected_count or failed_count != 0:
                    raise RuntimeError(
                        f"Expected to publish {expected_count} {engine_title} result package(s), "
                        f"but processed={processed_count}, failed={failed_count}"
                    )
                await task_service.add_log(
                    job.task_id,
                    "INFO",
                    (
                        f"Published {publish_result.get('processed', 0)} {engine_title} result package(s). "
                        f"issues={rebuild_result.get('issue_count', 0) if rebuild_result else 0}"
                    ),
                )
                run_log(
                    run.run_id,
                    (
                        f"[publish] processed={publish_result.get('processed', 0)} "
                        f"failed={publish_result.get('failed', 0)} "
                        f"issues={rebuild_result.get('issue_count', 0) if rebuild_result else 0}"
                    ),
                )
            except Exception as exc:
                publish_error = str(exc)
                await task_service.add_log(
                    job.task_id,
                    "WARNING",
                    f"Result catalog publish failed: {publish_error}",
                )
                run_log(run.run_id, f"[publish-failed] {publish_error}")

        cancelled = await _refresh_cancel_state()
        await dinsar_production_service.refresh_run_counters(run, db=db)
        if publish_error:
            final_status = "FAILED"
            latest_message = f"Result catalog publish failed: {publish_error}"
        elif cancelled:
            final_status = "CANCELLED"
            latest_message = (
                f"{engine_title} D-InSAR production cancelled. completed={run.completed_items} "
                f"failed={run.failed_items} total={run.total_items}"
            )
        elif int(run.failed_items or 0) > 0:
            final_status = "FAILED"
            latest_message = (
                f"{engine_title} D-InSAR production finished with failures. completed={run.completed_items} "
                f"failed={run.failed_items} total={run.total_items}"
            )
        else:
            final_status = "COMPLETED"
            latest_message = (
                f"{engine_title} D-InSAR production completed. completed={run.completed_items} "
                f"failed={run.failed_items} total={run.total_items}"
            )

        summary_payload = {
            "workflow": f"dinsar_{engine_code}",
            "engine_code": run.engine_code,
            "profile_code": run.profile_code,
            "mode": run.mode,
            "total_items": run.total_items,
            "completed_items": run.completed_items,
            "failed_items": run.failed_items,
            "skipped_items": run.skipped_items,
            "publish": publish_result,
            "rebuild": rebuild_result,
            "publish_error": publish_error,
            "published_output_dirs": publish_dirs,
        }
        await dinsar_production_service.finalize_run(
            run,
            db=db,
            status=final_status,
            summary_payload=summary_payload,
            latest_message=latest_message,
        )
        run_log(run.run_id, f"[finish] status={final_status} message={latest_message}")

        if final_status == "COMPLETED":
            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                progress=100,
                message=latest_message,
            )
            return

        task_status = "CANCELLED" if final_status == "CANCELLED" else "FAILED"
        await task_service.update_task(
            job.task_id,
            status=task_status,
            progress=100,
            message=latest_message,
        )
        raise RuntimeError(latest_message)


async def _handle_isce2_run(job: SystemJobORM) -> None:
    payload = job.payload or {}
    production_run_id = str(payload.get("production_run_id") or "").strip()
    if production_run_id:
        try:
            await _run_wsl_dinsar_production_controller(
                job,
                engine_code="isce2",
                engine_title="ISCE2",
                fallback_timeout_seconds=settings.ISCE2_PER_TASK_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            latest_message = f"ISCE2 D-InSAR production controller failed: {exc}"
            try:
                async with AsyncSessionLocal() as db:
                    run = await dinsar_production_service.get_run(production_run_id, db)
                    if run is not None and str(run.status or "").strip().upper() not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        summary_payload = dict(run.summary_json or {})
                        summary_payload["controller_error"] = str(exc)
                        await dinsar_production_service.finalize_run(
                            run,
                            db=db,
                            status="FAILED",
                            summary_payload=summary_payload,
                            latest_message=latest_message,
                        )
                        dinsar_production_service.append_run_log(
                            run.run_id,
                            f"[controller-failed] {exc}",
                        )
            except Exception:
                pass

            try:
                current_task = await task_service.get_task(job.task_id)
                if current_task and current_task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    await task_service.add_log(job.task_id, "ERROR", latest_message)
                    await task_service.update_task(
                        job.task_id,
                        status="FAILED",
                        progress=100,
                        message=latest_message,
                    )
            except Exception:
                pass
            raise
        return

    await _handle_queued_engine_run(
        job,
        engine_title="ISCE2",
        fallback_timeout_seconds=settings.ISCE2_PER_TASK_TIMEOUT_SECONDS,
    )


async def _handle_pyint_run(job: SystemJobORM) -> None:
    production_run_id = str((job.payload or {}).get("production_run_id") or "").strip()
    if production_run_id:
        try:
            await _run_wsl_dinsar_production_controller(
                job,
                engine_code="pyint",
                engine_title="PyINT/Gamma",
                fallback_timeout_seconds=settings.PYINT_DEFAULT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            latest_message = f"PyINT/Gamma D-InSAR production controller failed: {exc}"
            try:
                async with AsyncSessionLocal() as db:
                    run = await dinsar_production_service.get_run(production_run_id, db)
                    if run is not None and str(run.status or "").strip().upper() not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        summary_payload = dict(run.summary_json or {})
                        summary_payload["controller_error"] = str(exc)
                        await dinsar_production_service.finalize_run(
                            run,
                            db=db,
                            status="FAILED",
                            summary_payload=summary_payload,
                            latest_message=latest_message,
                        )
                        dinsar_production_service.append_run_log(
                            run.run_id,
                            f"[controller-failed] {exc}",
                        )
            except Exception:
                pass

            try:
                current_task = await task_service.get_task(job.task_id)
                if current_task and current_task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    await task_service.add_log(job.task_id, "ERROR", latest_message)
                    await task_service.update_task(
                        job.task_id,
                        status="FAILED",
                        progress=100,
                        message=latest_message,
                    )
            except Exception:
                pass
            raise
        return

    await _handle_queued_engine_run(
        job,
        engine_title="PyINT",
        fallback_timeout_seconds=settings.PYINT_DEFAULT_TIMEOUT_SECONDS,
    )


async def _handle_landsar_run(job: SystemJobORM) -> None:
    production_run_id = str((job.payload or {}).get("production_run_id") or "").strip()
    if production_run_id:
        try:
            await _run_wsl_dinsar_production_controller(
                job,
                engine_code="landsar",
                engine_title="LandSAR",
                fallback_timeout_seconds=int(getattr(settings, "LANDSAR_DINSAR_TIMEOUT_SECONDS", 0) or 43200),
            )
        except Exception as exc:
            latest_message = f"LandSAR D-InSAR production controller failed: {exc}"
            try:
                async with AsyncSessionLocal() as db:
                    run = await dinsar_production_service.get_run(production_run_id, db)
                    if run is not None and str(run.status or "").strip().upper() not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        summary_payload = dict(run.summary_json or {})
                        summary_payload["controller_error"] = str(exc)
                        await dinsar_production_service.finalize_run(
                            run,
                            db=db,
                            status="FAILED",
                            summary_payload=summary_payload,
                            latest_message=latest_message,
                        )
                        dinsar_production_service.append_run_log(
                            run.run_id,
                            f"[controller-failed] {exc}",
                        )
            except Exception:
                pass

            try:
                current_task = await task_service.get_task(job.task_id)
                if current_task and current_task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    await task_service.add_log(job.task_id, "ERROR", latest_message)
                    await task_service.update_task(
                        job.task_id,
                        status="FAILED",
                        progress=100,
                        message=latest_message,
                    )
            except Exception:
                pass
            raise
        return

    await _handle_queued_engine_run(
        job,
        engine_title="LandSAR",
        fallback_timeout_seconds=int(getattr(settings, "LANDSAR_DINSAR_TIMEOUT_SECONDS", 0) or 43200),
    )


async def _handle_water_geocode(job: SystemJobORM) -> None:
    """单景 SAR 地理编码 job handler（多视 + 地理编码 + 辐射定标）。"""
    from .water_service import run_geocoding_workflow, WATER_RESULTS_DIR

    payload = job.payload or {}
    scene_id = payload.get("scene_id")
    if not scene_id:
        raise ValueError("WATER_GEOCODE job 缺少 scene_id")

    await task_service.start_task(job.task_id, message="查询雷达数据...")

    async with AsyncSessionLocal() as db:
        scene = await db.get(SARSceneGeoORM, int(scene_id))
        if not scene:
            raise ValueError(f"SARSceneGeoORM id={scene_id} 不存在")
        from ..models import RadarDataORM
        radar = await db.get(RadarDataORM, scene.radar_data_id)
        if not radar:
            raise ValueError(f"RadarDataORM id={scene.radar_data_id} 不存在")
        file_path = radar.file_path
        unique_id = radar.unique_id

    output_dir = os.path.join(WATER_RESULTS_DIR, f"scene_{unique_id}")
    os.makedirs(output_dir, exist_ok=True)

    await task_service.update_task(job.task_id, progress=10, message="启动地理编码子进程...")

    def _run() -> Dict[str, Any]:
        return run_geocoding_workflow(
            file_path=file_path,
            output_dir=output_dir,
            job_id=job.job_id,
        )

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            scene = await db.get(SARSceneGeoORM, int(scene_id))
            if scene:
                scene.status = "FAILED"
                scene.error_msg = str(exc)
                await db.commit()
        raise

    async with AsyncSessionLocal() as db:
        scene = await db.get(SARSceneGeoORM, int(scene_id))
        if scene:
            if result.get("ok"):
                scene.geo_path = result.get("geo_path")
                scene.pixel_size_m = result.get("pixel_size_m")
                scene.status = "DONE"
                scene.error_msg = None
            else:
                scene.status = "FAILED"
                scene.error_msg = result.get("error", "Unknown error")
            await db.commit()

    if not result.get("ok"):
        raise RuntimeError(f"地理编码失败: {result.get('error')}")

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=f"地理编码完成: {result.get('geo_path')}",
    )


async def _handle_sar_scene_preprocess(job: SystemJobORM) -> None:
    """Build one analysis-ready GeoTIFF for flood/water algorithms."""
    payload = job.payload or {}
    scene_id = payload.get("scene_id")
    radar_data_id = payload.get("radar_data_id")
    engine = str(payload.get("engine") or "").strip().lower()
    if not scene_id and not radar_data_id:
        raise ValueError("SAR_SCENE_PREPROCESS requires scene_id or radar_data_id")
    if engine not in {"gf3_gdal", "lt_gamma"}:
        raise ValueError(f"Unsupported SAR scene preprocessing engine: {engine}")

    await task_service.start_task(job.task_id, message="Preparing analysis-ready SAR GeoTIFF...")

    async with AsyncSessionLocal() as db:
        scene: SARSceneGeoORM | None = None
        if scene_id:
            scene = await db.get(SARSceneGeoORM, int(scene_id))
        if not scene and radar_data_id:
            result = await db.execute(
                select(SARSceneGeoORM).where(SARSceneGeoORM.radar_data_id == int(radar_data_id))
            )
            scene = result.scalar_one_or_none()
        if not scene:
            scene = SARSceneGeoORM(radar_data_id=int(radar_data_id), status="PENDING")
            db.add(scene)
            await db.flush()
        radar = await db.get(RadarDataORM, int(scene.radar_data_id))
        if not radar:
            raise ValueError(f"RadarDataORM id={scene.radar_data_id} does not exist")
        scene.status = "RUNNING"
        scene.error_msg = None
        await db.commit()
        scene_id = int(scene.id)
        radar_data_id = int(radar.id)

    try:
        if engine == "gf3_gdal":
            await task_service.update_task(job.task_id, progress=20, message="Standardizing GF3 L2 GeoTIFF...")
            from .sar_analysis_ready_service import standardize_gf3_l2_for_radar

            async with AsyncSessionLocal() as db:
                manifest = await standardize_gf3_l2_for_radar(
                    db=db,
                    radar_id=int(radar_data_id),
                    l2_path=payload.get("l2_path"),
                    polarization=payload.get("polarization"),
                )
        else:
            await task_service.update_task(job.task_id, progress=15, message="Running LT Gamma single-scene preprocessing...")
            from .lt_gamma_scene_service import run_lt_gamma_scene_preprocess
            from .sar_analysis_ready_service import register_analysis_ready_tif

            async with AsyncSessionLocal() as db:
                scene = await db.get(SARSceneGeoORM, int(scene_id))
                radar = await db.get(RadarDataORM, int(radar_data_id))
                if not scene or not radar:
                    raise ValueError("Scene or radar record disappeared before LT Gamma preprocessing")

            def _run_lt() -> Dict[str, Any]:
                return run_lt_gamma_scene_preprocess(radar=radar, scene=scene, job_id=job.job_id)

            lt_manifest = await asyncio.to_thread(_run_lt)
            await task_service.update_task(job.task_id, progress=85, message="Registering LT analysis-ready GeoTIFF...")
            analysis_tif_path = str(lt_manifest.get("analysis_tif_path") or "").strip()
            if not analysis_tif_path:
                raise RuntimeError("LT Gamma preprocessing returned no analysis_tif_path")
            async with AsyncSessionLocal() as db:
                scene = await db.get(SARSceneGeoORM, int(scene_id))
                radar = await db.get(RadarDataORM, int(radar_data_id))
                if not scene or not radar:
                    raise ValueError("Scene or radar record disappeared before analysis-ready registration")
                manifest = await register_analysis_ready_tif(
                    db=db,
                    scene=scene,
                    radar=radar,
                    source_tif_path=analysis_tif_path,
                    engine="lt_gamma",
                    profile="lt1_gamma_geocoded_mli",
                    backscatter_unit=str(lt_manifest.get("backscatter_unit") or "gamma_mli_db"),
                    polarization=radar.polarization,
                    metadata=lt_manifest,
                )
                await db.commit()
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            scene = await db.get(SARSceneGeoORM, int(scene_id))
            if scene:
                scene.status = "FAILED"
                scene.error_msg = str(exc)
                await db.commit()
        raise

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=f"Analysis-ready GeoTIFF ready: {manifest.get('analysis_tif_path')}",
    )


async def _handle_water_flood(job: SystemJobORM) -> None:
    """洪涝检测 job handler（灾前 + 灾后配对分类）。"""
    from .water_service import run_flood_detection, WATER_RESULTS_DIR

    payload = job.payload or {}
    detection_id = payload.get("detection_id")
    if not detection_id:
        raise ValueError("WATER_FLOOD job 缺少 detection_id")
    refine = bool(payload.get("refine", False))

    await task_service.start_task(job.task_id, message="查询洪涝检测记录...")

    async with AsyncSessionLocal() as db:
        det = await db.get(FloodDetectionORM, int(detection_id))
        if not det:
            raise ValueError(f"FloodDetectionORM id={detection_id} 不存在")
        pre_scene = await db.get(SARSceneGeoORM, det.pre_scene_id)
        post_scene = await db.get(SARSceneGeoORM, det.post_scene_id)
        if not pre_scene or not post_scene:
            raise ValueError("灾前或灾后场景记录不存在")
        if pre_scene.status != "DONE" or post_scene.status != "DONE":
            raise ValueError("灾前或灾后场景尚未完成地理编码")
        pre_geo = pre_scene.analysis_tif_path or pre_scene.geo_path
        post_geo = post_scene.analysis_tif_path or post_scene.geo_path
        if not pre_geo or not post_geo:
            raise ValueError("Pre/post scenes must have analysis-ready GeoTIFF paths")

    output_dir = os.path.join(WATER_RESULTS_DIR, f"flood_{detection_id}")
    os.makedirs(output_dir, exist_ok=True)

    await task_service.update_task(job.task_id, progress=10, message="启动洪涝检测子进程...")

    def _run() -> Dict[str, Any]:
        return run_flood_detection(
            pre_geo_path=pre_geo,
            post_geo_path=post_geo,
            output_dir=output_dir,
            job_id=job.job_id,
            refine=refine,
        )

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            det = await db.get(FloodDetectionORM, int(detection_id))
            if det:
                det.status = "FAILED"
                det.error_msg = str(exc)
                await db.commit()
        raise

    async with AsyncSessionLocal() as db:
        det = await db.get(FloodDetectionORM, int(detection_id))
        if det:
            if result.get("ok"):
                det.classified_path = result.get("classified_path")
                det.flood_area_km2 = result.get("flood_area_km2")
                det.stable_water_area_km2 = result.get("stable_water_area_km2")
                det.output_dir = output_dir
                det.status = "DONE"
                det.error_msg = None
            else:
                det.status = "FAILED"
                det.error_msg = result.get("error", "Unknown error")
            await db.commit()

    if not result.get("ok"):
        raise RuntimeError(f"洪涝检测失败: {result.get('error')}")

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            f"洪涝检测完成: 洪涝面积={result.get('flood_area_km2')} km², "
            f"稳定水体={result.get('stable_water_area_km2')} km²"
        ),
    )


async def _handle_flood_detection(job: SystemJobORM) -> None:
    """Flood-analysis detection job: pure Python GeoTIFF change classification."""
    from .flood_detection_service import run_geotiff_flood_detection

    payload = job.payload or {}
    detection_id = payload.get("detection_id")
    if not detection_id:
        raise ValueError("FLOOD_DETECTION job requires detection_id")
    refine = bool(payload.get("refine", False))

    await task_service.start_task(job.task_id, message="Reading flood-detection scene pair...")

    async with AsyncSessionLocal() as db:
        det = await db.get(FloodDetectionORM, int(detection_id))
        if not det:
            raise ValueError(f"FloodDetectionORM id={detection_id} does not exist")
        pre_scene = await db.get(SARSceneGeoORM, det.pre_scene_id)
        post_scene = await db.get(SARSceneGeoORM, det.post_scene_id)
        if not pre_scene or not post_scene:
            raise ValueError("Pre/post scene records do not exist")
        if pre_scene.status != "DONE" or post_scene.status != "DONE":
            raise ValueError("Pre/post scenes are not DONE")
        pre_tif = pre_scene.analysis_tif_path
        post_tif = post_scene.analysis_tif_path
        if not pre_tif or not post_tif:
            raise ValueError("Flood detection requires analysis-ready GeoTIFF paths for both scenes")

    output_dir = os.path.join(settings.WATER_RESULTS_DIR, f"flood_{detection_id}")
    os.makedirs(output_dir, exist_ok=True)

    await task_service.update_task(job.task_id, progress=10, message="Running GeoTIFF flood classification...")

    def _run() -> Dict[str, Any]:
        return run_geotiff_flood_detection(
            pre_tif_path=pre_tif,
            post_tif_path=post_tif,
            output_dir=output_dir,
            job_id=job.job_id,
            refine=refine,
        )

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            det = await db.get(FloodDetectionORM, int(detection_id))
            if det:
                det.status = "FAILED"
                det.error_msg = str(exc)
                await db.commit()
        raise

    async with AsyncSessionLocal() as db:
        det = await db.get(FloodDetectionORM, int(detection_id))
        if det:
            if result.get("ok"):
                det.classified_path = result.get("classified_path")
                det.flood_area_km2 = result.get("flood_area_km2")
                det.stable_water_area_km2 = result.get("stable_water_area_km2")
                det.output_dir = output_dir
                det.status = "DONE"
                det.error_msg = None
            else:
                det.status = "FAILED"
                det.error_msg = result.get("error", "Unknown error")
            await db.commit()

    if not result.get("ok"):
        raise RuntimeError(f"Flood detection failed: {result.get('error')}")

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            f"GeoTIFF flood detection completed: flood_area={result.get('flood_area_km2')} km2, "
            f"stable_water={result.get('stable_water_area_km2')} km2"
        ),
    )


async def _handle_water_detect(job: SystemJobORM) -> None:
    """水体检测 job handler（Otsu + DEM + 形态学 + 连通分量）。"""
    from .gf3_water_extraction_service import GF3_HH_HV_PROCESSOR, run_gf3_hh_hv_water_extraction
    from .water_extraction_service import run_otsu_water_extraction

    payload = job.payload or {}
    extraction_id = payload.get("extraction_id")
    detection_id = payload.get("detection_id")
    record_id = extraction_id or detection_id
    if not record_id:
        raise ValueError("WATER_DETECT job 缺少 extraction_id/detection_id")
    use_extraction_table = extraction_id is not None

    await task_service.start_task(job.task_id, message="读取检测任务信息...")

    async with AsyncSessionLocal() as db:
        det = await db.get(WaterExtractionORM if use_extraction_table else WaterDetectionORM, int(record_id))
        if not det:
            model_name = "WaterExtractionORM" if use_extraction_table else "WaterDetectionORM"
            raise ValueError(f"{model_name} id={record_id} 不存在")
        input_path = det.input_path
        metadata = det.metadata_json if use_extraction_table and isinstance(det.metadata_json, dict) else {}
        processor = str(payload.get("processor") or getattr(det, "processor", None) or "otsu").strip().lower()
        if processor in {"gf3_water", "gf3_water_hh_hv", "hh_hv"}:
            processor = GF3_HH_HV_PROCESSOR
        input_assets = metadata.get("input_assets") if isinstance(metadata.get("input_assets"), dict) else {}
        processor_params = dict(metadata.get("processor_params") or {})
        processor_params.update(dict(payload.get("processor_params") or {}))
        hh_path = payload.get("hh_path") or input_assets.get("hh")
        hv_path = payload.get("hv_path") or input_assets.get("hv")
        det.status = "RUNNING"
        if use_extraction_table and hasattr(det, "task_id"):
            det.task_id = job.task_id
        if not use_extraction_table:
            mirror = await db.get(WaterExtractionORM, int(record_id))
            if mirror:
                mirror.status = "RUNNING"
                mirror.task_id = job.task_id
        await db.commit()

    if processor == GF3_HH_HV_PROCESSOR:
        if not hh_path or not hv_path:
            raise ValueError("GF3 HH/HV water extraction requires hh_path and hv_path")
    elif not input_path:
        raise ValueError("水体检测缺少输入路径 input_path")

    output_name = f"water_extraction_{record_id}" if use_extraction_table else f"water_detect_{record_id}"
    output_root = settings.WATER_RESULTS_DIR or os.path.join(settings.BACKEND_DIR, "water_results")
    output_dir = os.path.join(output_root, processor, output_name) if use_extraction_table else os.path.join(output_root, output_name)
    os.makedirs(output_dir, exist_ok=True)

    await task_service.update_task(job.task_id, progress=10, message="启动水体检测算法...")

    def _run() -> Dict[str, Any]:
        if processor == GF3_HH_HV_PROCESSOR:
            return run_gf3_hh_hv_water_extraction(
                hh_path=hh_path,
                hv_path=hv_path,
                output_dir=output_dir,
                job_id=job.job_id,
                params=processor_params,
            )
        return run_otsu_water_extraction(
            input_path=input_path,
            output_dir=output_dir,
            job_id=job.job_id,
        )

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            det = await db.get(WaterExtractionORM if use_extraction_table else WaterDetectionORM, int(record_id))
            if det:
                det.status = "FAILED"
                det.error_msg = str(exc)
            if not use_extraction_table:
                mirror = await db.get(WaterExtractionORM, int(record_id))
                if mirror:
                    mirror.status = "FAILED"
                    mirror.error_msg = str(exc)
                    mirror.task_id = job.task_id
            await db.commit()
        raise

    async with AsyncSessionLocal() as db:
        det = await db.get(WaterExtractionORM if use_extraction_table else WaterDetectionORM, int(record_id))
        if det:
            if result.get("ok"):
                det.output_path = result.get("output_path")
                if hasattr(det, "preview_path"):
                    det.preview_path = result.get("preview_path")
                if hasattr(det, "vector_path"):
                    det.vector_path = result.get("vector_path")
                det.water_area_km2 = result.get("water_area_km2")
                det.water_pixel_count = result.get("water_pixel_count")
                if use_extraction_table:
                    det.processor = result.get("processor") or det.processor or "otsu"
                    det.threshold_value = result.get("threshold_value")
                    result_metadata = result.get("metadata_json")
                    if isinstance(result_metadata, dict):
                        det.metadata_json = result_metadata
                    else:
                        det.metadata_json = {
                            "legacy_otsu_threshold_db": result.get("otsu_threshold_db"),
                            "value_transform": result.get("value_transform"),
                            "job_id": job.job_id,
                        }
                else:
                    det.otsu_threshold_db = result.get("otsu_threshold_db")
                det.status = "DONE"
                det.error_msg = None
            else:
                det.status = "FAILED"
                det.error_msg = result.get("error", "Unknown error")
            if not use_extraction_table:
                mirror = await db.get(WaterExtractionORM, int(record_id))
                if mirror:
                    mirror.output_path = det.output_path
                    mirror.preview_path = result.get("preview_path")
                    mirror.vector_path = result.get("vector_path")
                    mirror.water_area_km2 = det.water_area_km2
                    mirror.water_pixel_count = det.water_pixel_count
                    mirror.threshold_value = result.get("threshold_value") or result.get("otsu_threshold_db")
                    mirror.processor = result.get("processor") or mirror.processor or "otsu"
                    mirror.status = det.status
                    mirror.error_msg = det.error_msg
                    mirror.task_id = job.task_id
                    mirror.metadata_json = {
                        "legacy_otsu_threshold_db": result.get("otsu_threshold_db"),
                        "value_transform": result.get("value_transform"),
                        "legacy_detection_id": int(record_id),
                        "job_id": job.job_id,
                    }
            await db.commit()

    if not result.get("ok"):
        raise RuntimeError(f"水体检测失败: {result.get('error')}")

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            f"水体检测完成: 水体面积={result.get('water_area_km2')} km², "
            f"水体像素={result.get('water_pixel_count')}, "
            f"Otsu阈值={result.get('otsu_threshold_db')}"
        ),
    )


async def _handle_gf3_process(job: SystemJobORM) -> None:
    """GF3 L1A→L2 处理 job handler（辐射定标 + RPC 几何校正）。"""
    from .gf3_service import run_gf3_l1a_to_l2

    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise ValueError(
            "Legacy GF3 Python/GDAL preprocessing is disabled. "
            "Use GF3 SARscape production or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
        )

    payload = job.payload or {}
    processing_id = payload.get("processing_id")
    if not processing_id:
        raise ValueError("GF3_PROCESS job 缺少 processing_id")

    await task_service.start_task(job.task_id, message="读取 GF3 处理任务信息...")

    async with AsyncSessionLocal() as db:
        proc = await db.get(GF3ProcessingORM, int(processing_id))
        if not proc:
            raise ValueError(f"GF3ProcessingORM id={processing_id} 不存在")
        input_dir = proc.input_dir
        resolution = proc.resolution or 0.0002
        output_dir = proc.output_dir
        proc.status = "RUNNING"
        await db.commit()

    if not output_dir:
        output_dir = os.path.join(settings.GF3_STORAGE_DIRS, f"gf3_{processing_id}")
    os.makedirs(output_dir, exist_ok=True)

    await task_service.update_task(job.task_id, progress=10, message="启动 GF3 L1A→L2 处理...")

    def _run() -> Dict[str, Any]:
        return run_gf3_l1a_to_l2(
            input_dir=input_dir,
            output_dir=output_dir,
            resolution=resolution,
            job_id=job.job_id,
        )

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            proc = await db.get(GF3ProcessingORM, int(processing_id))
            if proc:
                proc.status = "FAILED"
                proc.error_msg = str(exc)
                await db.commit()
        raise

    async with AsyncSessionLocal() as db:
        proc = await db.get(GF3ProcessingORM, int(processing_id))
        if proc:
            if result.get("ok"):
                import json as _json
                proc.output_dir = result.get("output_dir", output_dir)
                proc.polarizations = _json.dumps(result.get("polarizations", []))
                proc.l2_paths = _json.dumps(result.get("l2_paths", []))
                proc.status = "DONE"
                proc.error_msg = None
            else:
                proc.status = "FAILED"
                proc.error_msg = result.get("error", "Unknown error")
            await db.commit()

    if not result.get("ok"):
        raise RuntimeError(f"GF3 处理失败: {result.get('error')}")

    pols = result.get("polarizations", [])
    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=f"GF3 处理完成: 极化={pols}, L2产品={len(result.get('l2_paths', []))}个",
    )


async def _handle_gf3_unpack(job: SystemJobORM) -> None:
    """GF3 archive inbox -> persistent L1A source pool."""
    from .gf3_unpack_service import run_gf3_archive_unpack

    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise ValueError(
            "Legacy GF3 archive unpack is disabled. "
            "Use GF3 SARscape production or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
        )

    if not job.task_id:
        raise ValueError("GF3_UNPACK requires task_id for progress tracking.")

    payload = job.payload or {}
    await task_service.start_task(job.task_id, message="扫描 GF3 压缩包来源目录...")

    loop = asyncio.get_running_loop()

    def _submit(coro):
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            return

        def _swallow_errors(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.warning("[GF3 Unpack] task callback failed: %s", exc)

        future.add_done_callback(_swallow_errors)

    def _log_cb(level: str, message: str) -> None:
        _submit(task_service.add_log(job.task_id, level, message))

    def _progress_cb(progress: int, message: str) -> None:
        _submit(task_service.update_task(job.task_id, progress=progress, message=message))

    try:
        result = await asyncio.to_thread(
            run_gf3_archive_unpack,
            source_dirs=payload.get("source_dirs"),
            target_dirs=payload.get("target_dirs"),
            archive_exts=payload.get("archive_exts"),
            max_files_per_run=payload.get("max_files_per_run"),
            delete_archive=payload.get("delete_archive") if "delete_archive" in payload else None,
            min_disk_space_gb=payload.get("min_disk_space_gb"),
            tmp_suffix=payload.get("tmp_suffix"),
            log_callback=_log_cb,
            progress_callback=_progress_cb,
        )
    except Exception as exc:
        await task_service.update_task(job.task_id, status="FAILED", progress=100, message=f"GF3 解包失败: {exc}")
        raise

    message = (
        f"GF3 解包完成: 成功 {int(result.get('processed') or 0)}, "
        f"跳过 {int(result.get('skipped') or 0)}, "
        f"失败 {int(result.get('failed') or 0)}"
    )
    remaining = int(result.get("remaining") or 0)
    if remaining > 0:
        message += f", 剩余 {remaining}"
    await task_service.update_task(job.task_id, status="COMPLETED", progress=100, message=message)


async def _handle_gf3_batch_process(job: SystemJobORM) -> None:
    """批量 GF3 L1A→L2：扫描来源目录，逐个处理并自动入库到 radar_data。"""
    from .gf3_service import run_gf3_l1a_to_l2, register_l2_to_radar_data
    from .sar_analysis_ready_service import standardize_gf3_l2_for_radar

    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise ValueError(
            "Legacy GF3 Python/GDAL preprocessing is disabled. "
            "Use GF3 SARscape production or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
        )

    payload = job.payload or {}
    source_dirs = payload.get("source_dirs") or []
    if not source_dirs:
        raise ValueError("GF3_BATCH_PROCESS: source_dirs 为空")

    await task_service.start_task(job.task_id, message="扫描 GF3 L1A 来源目录...")

    # Collect all L1A subdirectories
    l1a_dirs: List[str] = []
    for src in source_dirs:
        if not os.path.isdir(src):
            logger.warning("[GF3 Batch] source dir not found: %s", src)
            continue
        for entry in os.scandir(src):
            if entry.is_dir(follow_symlinks=False):
                l1a_dirs.append(entry.path)

    if not l1a_dirs:
        await task_service.update_task(
            job.task_id, status="COMPLETED", progress=100,
            message="未在来源目录中找到 L1A 子目录",
        )
        return

    # Filter out already processed (check GF3_STORAGE_DIRS for output)
    gf3_storage = settings.GF3_STORAGE_DIRS
    os.makedirs(gf3_storage, exist_ok=True)

    pending_dirs: List[str] = []
    for d in l1a_dirs:
        out_name = f"gf3_{os.path.basename(d)}"
        out_path = os.path.join(gf3_storage, out_name)
        if os.path.isdir(out_path):
            # Check if any L2 TIFF exists
            has_l2 = any(f.lower().endswith((".tif", ".tiff")) for f in os.listdir(out_path))
            if has_l2:
                continue
        pending_dirs.append(d)

    if not pending_dirs:
        await task_service.update_task(
            job.task_id, status="COMPLETED", progress=100,
            message=f"所有 {len(l1a_dirs)} 个 L1A 目录已处理过",
        )
        return

    total = len(pending_dirs)
    success_count = 0
    fail_count = 0

    await task_service.update_task(
        job.task_id, progress=5,
        message=f"待处理 {total} 个 L1A 目录（共 {len(l1a_dirs)} 个，已跳过 {len(l1a_dirs) - total} 个）",
    )

    for idx, input_dir in enumerate(pending_dirs):
        dir_name = os.path.basename(input_dir)
        output_dir = os.path.join(gf3_storage, f"gf3_{dir_name}")
        pct = int(5 + (idx / total) * 90)

        await task_service.update_task(
            job.task_id, progress=pct,
            message=f"处理 {idx + 1}/{total}: {dir_name}",
        )

        try:
            result = await asyncio.to_thread(
                run_gf3_l1a_to_l2,
                input_dir=input_dir,
                output_dir=output_dir,
                resolution=0.0002,
                job_id=job.job_id,
            )
            if result.get("ok"):
                success_count += 1
                # Auto-register to radar_data
                try:
                    async with AsyncSessionLocal() as db:
                        radar_id = await register_l2_to_radar_data(
                            l2_dir=result.get("output_dir", output_dir),
                            input_dir_name=dir_name,
                            polarizations=result.get("polarizations", []),
                            db=db,
                        )
                        if radar_id:
                            await standardize_gf3_l2_for_radar(
                                db=db,
                                radar_id=int(radar_id),
                                l2_path=result.get("output_dir", output_dir),
                            )
                except Exception as reg_err:
                    logger.warning("[GF3 Batch] Auto-register failed for %s: %s", dir_name, reg_err)
            else:
                fail_count += 1
                logger.warning("[GF3 Batch] Failed: %s — %s", dir_name, result.get("error"))
        except Exception as exc:
            fail_count += 1
            logger.error("[GF3 Batch] Exception for %s: %s", dir_name, exc)

    await task_service.update_task(
        job.task_id, status="COMPLETED", progress=100,
        message=f"GF3 批量处理完成: 成功 {success_count}/{total}, 失败 {fail_count}",
    )


async def _handle_gf3_sarscape_sync(job: SystemJobORM) -> None:
    """Scan SARscape native GF3 _geo outputs, convert them to GeoTIFF, and register them."""
    from .gf3_standardize_service import standardize_gf3_sarscape_native_roots

    if not job.task_id:
        raise ValueError("GF3_SARSCAPE_SYNC requires task_id for progress tracking.")

    payload = job.payload or {}
    native_dirs = payload.get("native_dirs") or []
    storage_root = payload.get("storage_root") or settings.GF3_STORAGE_DIRS
    if not native_dirs:
        raise ValueError("GF3_SARSCAPE_SYNC: native_dirs is empty")
    if not storage_root:
        raise ValueError("GF3_SARSCAPE_SYNC: storage_root is empty")

    await task_service.start_task(job.task_id, message="扫描 GF3 SARscape 原生 _geo 结果池...")

    loop = asyncio.get_running_loop()

    def _progress_cb(progress: int, message: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                task_service.update_task(job.task_id, progress=progress, message=message),
                loop,
            )
            def _swallow_progress_error(fut):
                try:
                    fut.result()
                except Exception as exc:
                    logger.warning("[GF3 SARscape] progress callback failed: %s", exc)

            future.add_done_callback(_swallow_progress_error)
        except RuntimeError:
            return

    try:
        async with AsyncSessionLocal() as db:
            result = await standardize_gf3_sarscape_native_roots(
                db,
                native_dirs=native_dirs,
                storage_root=storage_root,
                force=bool(payload.get("force", False)),
                register=bool(payload.get("register", True)),
                progress_callback=_progress_cb,
            )
    except Exception as exc:
        await task_service.update_task(
            job.task_id,
            status="FAILED",
            progress=100,
            message=f"GF3 SARscape 标准化失败: {exc}",
        )
        raise

    message = (
        "GF3 SARscape 标准化完成: "
        f"发现 {int(result.get('scene_count') or 0)} 景, "
        f"可转换 {int(result.get('ready_scene_count') or 0)} 景, "
        f"转换 {int(result.get('converted_scenes') or 0)} 景, "
        f"部分 {int(result.get('partial_scenes') or 0)} 景, "
        f"失败 {int(result.get('failed_scenes') or 0)} 景, "
        f"新增/更新 GeoTIFF {int(result.get('converted_assets') or 0)} 个, "
        f"跳过 {int(result.get('skipped_assets') or 0)} 个, "
        f"入库 {int(result.get('registered') or 0)} 景"
    )
    await task_service.update_task(job.task_id, status="COMPLETED", progress=100, message=message)


async def _handle_gf3_sarscape_produce(job: SystemJobORM) -> None:
    """Run GF3 raw archive -> SARscape native -> GeoTIFF registration chain."""
    from .gf3_sarscape_production_service import (
        cleanup_gf3_sarscape_native_pool,
        run_gf3_sarscape_production,
    )
    from .gf3_standardize_service import standardize_gf3_sarscape_native_roots

    if not job.task_id:
        raise ValueError("GF3_SARSCAPE_PRODUCE requires task_id for progress tracking.")

    payload = job.payload or {}
    source_dirs = payload.get("source_dirs") or []
    native_dirs = payload.get("native_dirs") or []
    storage_root = payload.get("storage_root") or settings.GF3_STORAGE_DIRS
    native_root = payload.get("native_root") or (native_dirs[0] if native_dirs else "")
    if not source_dirs:
        raise ValueError("GF3_SARSCAPE_PRODUCE: source_dirs is empty")
    if not native_root:
        raise ValueError("GF3_SARSCAPE_PRODUCE: native_root is empty")
    if not storage_root:
        raise ValueError("GF3_SARSCAPE_PRODUCE: storage_root is empty")

    await task_service.start_task(job.task_id, message="GF3 SARscape production starting...")
    loop = asyncio.get_running_loop()

    def _progress_cb(progress: int, message: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                task_service.update_task(job.task_id, progress=progress, message=message),
                loop,
            )

            def _swallow_progress_error(fut):
                try:
                    fut.result()
                except Exception as exc:
                    logger.warning("[GF3 SARscape Produce] progress callback failed: %s", exc)

            future.add_done_callback(_swallow_progress_error)
        except RuntimeError:
            return

    def _log_cb(level: str, message: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                task_service.add_log(job.task_id, level, message),
                loop,
            )

            def _swallow_log_error(fut):
                try:
                    fut.result()
                except Exception as exc:
                    logger.warning("[GF3 SARscape Produce] log callback failed: %s", exc)

            future.add_done_callback(_swallow_log_error)
        except RuntimeError:
            return

    async def _production_keepalive() -> None:
        progress = 8
        while True:
            await asyncio.sleep(60)
            progress = min(68, progress + 1)
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message="GF3 SARscape production is still running...",
            )

    try:
        production_task = asyncio.create_task(
            asyncio.to_thread(
                run_gf3_sarscape_production,
                source_dirs=source_dirs,
                native_root=native_root,
                wrapper_exe=payload.get("wrapper_exe"),
                dem_path=payload.get("dem_path"),
                idlrt_path=payload.get("idlrt_path"),
                polarizations=payload.get("polarizations"),
                archive_exts=payload.get("archive_exts") or [],
                max_archives_per_run=payload.get("max_archives_per_run"),
                selected_dates=payload.get("selected_dates") or [],
                task_id=job.task_id,
                local_staging_root=payload.get("local_staging_root") or settings.GF3_TASK_POOL_ROOT,
                timeout_seconds=payload.get("timeout_seconds"),
                keep_extracted=payload.get("keep_extracted"),
                log_callback=_log_cb,
                progress_callback=_progress_cb,
            )
        )
        keepalive_task = asyncio.create_task(_production_keepalive())
        try:
            production_result = await production_task
        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

        standardize_result: Dict[str, Any] = {}
        if bool(payload.get("auto_standardize", True)):
            await task_service.update_task(
                job.task_id,
                progress=72,
                message="GF3 SARscape production finished; standardizing native _geo outputs...",
            )
            async with AsyncSessionLocal() as db:
                standardize_result = await standardize_gf3_sarscape_native_roots(
                    db,
                    native_dirs=native_dirs or [native_root],
                    storage_root=storage_root,
                    force=bool(payload.get("force_standardize", False)),
                    register=bool(payload.get("register", True)),
                    progress_callback=lambda pct, msg: _progress_cb(72 + int(max(0, min(100, pct)) * 0.16), msg),
                )

        cleanup_result: Dict[str, Any] = {}
        production_ok = int(production_result.get("failed_count") or 0) == 0
        standardize_ok = (
            not standardize_result
            or (
                int(standardize_result.get("failed_assets") or 0) == 0
                and int(standardize_result.get("failed_scenes") or 0) == 0
            )
        )
        if bool(payload.get("clean_after_success", True)) and production_ok and standardize_ok:
            await task_service.update_task(
                job.task_id,
                progress=90,
                message="Cleaning GF3 SARscape intermediate files...",
            )
            cleanup_result = await asyncio.to_thread(
                cleanup_gf3_sarscape_native_pool,
                native_dirs=native_dirs or [native_root],
                storage_root=storage_root,
                require_standardized=bool(payload.get("cleanup_require_standardized", True)),
                dry_run=bool(payload.get("cleanup_dry_run", False)),
                max_scenes=payload.get("cleanup_max_scenes"),
                log_callback=_log_cb,
                progress_callback=lambda pct, msg: _progress_cb(90 + int(max(0, min(100, pct)) * 0.09), msg),
            )
        elif bool(payload.get("clean_after_success", True)):
            _log_cb(
                "WARNING",
                "GF3 SARscape automatic cleanup skipped because production or standardization had failures.",
            )
    except Exception as exc:
        await task_service.update_task(
            job.task_id,
            status="FAILED",
            progress=100,
            message=f"GF3 SARscape production failed: {exc}",
        )
        raise

    failed_count = int(production_result.get("failed_count") or 0)
    failed_assets = int(standardize_result.get("failed_assets") or 0)
    cleanup_errors = int(cleanup_result.get("error_scene_count") or 0)
    final_status = "FAILED" if failed_count or failed_assets or cleanup_errors else "COMPLETED"
    message = (
        "GF3 SARscape production chain finished: "
        f"found={int(production_result.get('found_count') or 0)}, "
        f"produced={int(production_result.get('processed_count') or 0)}, "
        f"skipped={int(production_result.get('skipped_count') or 0)}, "
        f"failed={failed_count}, "
        f"converted_assets={int(standardize_result.get('converted_assets') or 0)}, "
        f"registered={int(standardize_result.get('registered') or 0)}, "
        f"cleaned_scenes={int(cleanup_result.get('cleaned_scene_count') or 0)}, "
        f"cleaned_bytes={int(cleanup_result.get('bytes_deleted') or 0)}"
    )
    await task_service.update_task(job.task_id, status=final_status, progress=100, message=message)


async def _handle_gf3_sarscape_clean(job: SystemJobORM) -> None:
    """Clean GF3 SARscape intermediate files from native pool."""
    from .gf3_sarscape_production_service import cleanup_gf3_sarscape_native_pool

    if not job.task_id:
        raise ValueError("GF3_SARSCAPE_CLEAN requires task_id for progress tracking.")

    payload = job.payload or {}
    native_dirs = payload.get("native_dirs") or []
    storage_root = payload.get("storage_root") or settings.GF3_STORAGE_DIRS
    if not native_dirs:
        raise ValueError("GF3_SARSCAPE_CLEAN: native_dirs is empty")

    await task_service.start_task(job.task_id, message="GF3 SARscape native cleanup starting...")
    loop = asyncio.get_running_loop()

    def _progress_cb(progress: int, message: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                task_service.update_task(job.task_id, progress=progress, message=message),
                loop,
            )

            def _swallow_progress_error(fut):
                try:
                    fut.result()
                except Exception as exc:
                    logger.warning("[GF3 SARscape Clean] progress callback failed: %s", exc)

            future.add_done_callback(_swallow_progress_error)
        except RuntimeError:
            return

    def _log_cb(level: str, message: str) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                task_service.add_log(job.task_id, level, message),
                loop,
            )

            def _swallow_log_error(fut):
                try:
                    fut.result()
                except Exception as exc:
                    logger.warning("[GF3 SARscape Clean] log callback failed: %s", exc)

            future.add_done_callback(_swallow_log_error)
        except RuntimeError:
            return

    try:
        result = await asyncio.to_thread(
            cleanup_gf3_sarscape_native_pool,
            native_dirs=native_dirs,
            storage_root=storage_root,
            require_standardized=bool(payload.get("require_standardized", True)),
            dry_run=bool(payload.get("dry_run", False)),
            max_scenes=payload.get("max_scenes"),
            log_callback=_log_cb,
            progress_callback=_progress_cb,
        )
    except Exception as exc:
        await task_service.update_task(
            job.task_id,
            status="FAILED",
            progress=100,
            message=f"GF3 SARscape native cleanup failed: {exc}",
        )
        raise

    status = "FAILED" if int(result.get("error_scene_count") or 0) else "COMPLETED"
    message = (
        "GF3 SARscape native cleanup finished: "
        f"scenes={int(result.get('scene_count') or 0)}, "
        f"cleaned={int(result.get('cleaned_scene_count') or 0)}, "
        f"skipped={int(result.get('skipped_scene_count') or 0)}, "
        f"errors={int(result.get('error_scene_count') or 0)}, "
        f"bytes={int(result.get('bytes_deleted') or 0)}, "
        f"dry_run={bool(result.get('dry_run'))}"
    )
    await task_service.update_task(job.task_id, status=status, progress=100, message=message)


async def _handle_publish_dinsar_products_clean(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("PUBLISH_DINSAR_PRODUCTS requires task_id for progress tracking.")
    payload = job.payload or {}
    source_directories = payload.get("source_directories") or []
    publish_root = payload.get("publish_root") or None
    rebuild_catalog = bool(payload.get("rebuild_catalog", True))

    await task_service.start_task(job.task_id, message="正在发布 D-InSAR 结果包...")
    async with AsyncSessionLocal() as db:
        publish_result = await result_catalog_service.publish_from_sources(
            db,
            source_directories,
            publish_root=publish_root,
        )
        message = (
            f"结果发布完成: 处理 {publish_result.get('processed', 0)} 个，"
            f"失败 {publish_result.get('failed', 0)} 个。"
        )
        if rebuild_catalog:
            await task_service.update_task(
                job.task_id,
                message="正在重建 D-InSAR 结果目录索引...",
                progress=70,
            )
            rebuild_result = await result_catalog_service.rebuild_catalog(
                db,
                publish_root=publish_root,
                full_rebuild=True,
            )
            message += (
                f" 目录索引已更新 {rebuild_result.get('registered', 0)} 条，"
                f"问题 {rebuild_result.get('issue_count', 0)} 条。"
            )
            compat_payload = rebuild_result.get("compat_sync") or {}
            compat_error = rebuild_result.get("compat_error")
            message += f" 兼容视图同步 {compat_payload.get('compat_count', 0)} 条。"
            if compat_error:
                message += f" compat_error={compat_error}。"

        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            progress=100,
            message=message,
        )


async def _handle_rebuild_dinsar_catalog_clean(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("REBUILD_DINSAR_CATALOG requires task_id for progress tracking.")
    payload = job.payload or {}
    publish_root = payload.get("publish_root") or None
    full_rebuild = bool(payload.get("full_rebuild", True))

    await task_service.start_task(job.task_id, message="正在重建 D-InSAR 结果目录索引...")
    async with AsyncSessionLocal() as db:
        result = await result_catalog_service.rebuild_catalog(
            db,
            publish_root=publish_root,
            full_rebuild=full_rebuild,
        )
        compat_payload = result.get("compat_sync") or {}
        compat_error = result.get("compat_error")
        message = (
            f"结果目录索引已重建: 发现 {result.get('manifest_count', 0)} 个包，"
            f"入库 {result.get('registered', 0)} 条，失败 {result.get('failed', 0)} 条，"
            f"兼容视图 {compat_payload.get('compat_count', 0)} 条。"
        )
        if compat_error:
            message += f" compat_error={compat_error}。"
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            progress=100,
            message=message,
        )


async def _handle_timeseries_prepare(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_PREPARE requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_PREPARE requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.start_task(
                job.task_id,
                message="Preparing SBAS stack selection manifest...",
                db=db,
            )
            result = await timeseries_service.prepare_run(run_id, db=db)
            await task_service.update_task(
                job.task_id,
                progress=25,
                message=(
                    f"SBAS prepare complete: scenes={result.get('scene_count', 0)} "
                    f"manifest={result.get('manifest_path')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_stack_prep(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_STACK_PREP requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    refresh = bool(payload.get("refresh", False))
    if not run_id:
        raise ValueError("TIMESERIES_STACK_PREP requires run_id payload.")

    progress = 90 if refresh else 50
    message = (
        "Refreshing SBAS stack readiness after materialization..."
        if refresh
        else "Building LT-1 stack-prep workspace..."
    )

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message=message,
                db=db,
            )
            result = await timeseries_service.build_stack_prep(run_id, refresh=refresh, db=db)
            if refresh:
                await task_service.update_task(
                    job.task_id,
                    progress=85,
                    message=(
                        f"SBAS stack ready: scenes={result.get('scene_count', 0)} "
                        f"manifest={result.get('manifest_path')}"
                    ),
                    db=db,
                )
            else:
                ready_text = "ready" if result.get("ready") else "waiting_for_materialization"
                await task_service.update_task(
                    job.task_id,
                    progress=55,
                    message=(
                        f"Initial stack-prep complete: state={ready_text} "
                        f"manifest={result.get('manifest_path')}"
                    ),
                    db=db,
                )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_materialize(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_MATERIALIZE requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    force = bool(payload.get("force", False))
    if not run_id:
        raise ValueError("TIMESERIES_MATERIALIZE requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=75,
                message="Materializing LT-1 SLC scenes in WSL...",
                db=db,
            )
            result = await timeseries_service.materialize_run(run_id, force=force, db=db)
            await task_service.update_task(
                job.task_id,
                progress=80,
                message=(
                    f"Materialization complete: scenes={result.get('scene_count', 0)} "
                    f"summary={result.get('summary_path')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_run_isce2_stack(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_RUN_ISCE2_STACK requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_RUN_ISCE2_STACK requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=88,
                message="Running ISCE2 stripmap stack workflow in WSL...",
                db=db,
            )
            result = await timeseries_service.run_isce2_stack(run_id, db=db)
            await task_service.update_task(
                job.task_id,
                progress=91,
                message=(
                    f"ISCE2 stack complete: run_steps={len(result.get('run_sequence') or [])} "
                    f"work_dir={result.get('stack_work_dir')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_run_mintpy_sbas(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_RUN_MINTPY_SBAS requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_RUN_MINTPY_SBAS requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=93,
                message="Running MintPy SBAS inversion...",
                db=db,
            )
            result = await timeseries_service.run_mintpy_sbas(run_id, db=db)
            await task_service.update_task(
                job.task_id,
                progress=95,
                message=(
                    f"MintPy SBAS complete: work_dir={result.get('mintpy_work_dir')} "
                    f"cfg={result.get('config_path')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_sarscape_preflight(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_SARSCAPE_PREFLIGHT requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_SARSCAPE_PREFLIGHT requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=45,
                message="Building SARscape SBAS processor manifest...",
                db=db,
            )
            result = await timeseries_service.build_sarscape_processor_preflight(run_id, db=db)
            ready_text = "ready" if result.get("ready_for_execution") else "planning_only"
            is_preflight_only = str(result.get("execution_mode") or "").strip() == "preflight_only"
            await task_service.update_task(
                job.task_id,
                status="COMPLETED" if is_preflight_only else None,
                progress=100 if is_preflight_only else 55,
                message=(
                    f"SARscape SBAS preflight complete: state={ready_text} "
                    f"manifest={result.get('processor_manifest_path')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_run_sarscape_sbas(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_RUN_SARSCAPE_SBAS requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_RUN_SARSCAPE_SBAS requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=90,
                message="Running SARscape SBAS pipeline...",
                db=db,
            )
            async with engine_lock_service.acquire("sarscape_sbas_timeseries"):
                result = await timeseries_service.run_sarscape_sbas(run_id, db=db)
            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                progress=100,
                message=(
                    f"SARscape SBAS complete: tasks={result.get('task_count', 0)} "
                    f"report={result.get('report_path')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_export_publish(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_EXPORT_PUBLISH requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_EXPORT_PUBLISH requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=96,
                message="Exporting PS-InSAR publish bundle...",
                db=db,
            )
            result = await timeseries_service.export_publish_bundle(run_id, db=db)
            await task_service.update_task(
                job.task_id,
                progress=98,
                message=(
                    f"Publish bundle exported: manifest={result.get('manifest_path')} "
                    f"publish_dir={result.get('publish_dir')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_timeseries_register_product(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("TIMESERIES_REGISTER_PRODUCT requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("TIMESERIES_REGISTER_PRODUCT requires run_id payload.")

    async with AsyncSessionLocal() as db:
        try:
            await task_service.update_task(
                job.task_id,
                progress=99,
                message="Registering PS-InSAR product into catalog...",
                db=db,
            )
            result = await timeseries_service.register_psinsar_product(run_id, db=db)
            await task_service.update_task(
                job.task_id,
                status="COMPLETED",
                progress=100,
                message=(
                    f"PS-InSAR run published: run_id={result.get('run_id')} "
                    f"product_id={result.get('product_id')}"
                ),
                db=db,
            )
        except Exception as exc:
            await timeseries_service.mark_run_failed(run_id, str(exc), db=db)
            raise


async def _handle_rebuild_psinsar_catalog(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("REBUILD_PSINSAR_CATALOG requires task_id for progress tracking.")
    payload = job.payload or {}
    publish_root = payload.get("publish_root") or None
    full_rebuild = bool(payload.get("full_rebuild", True))

    await task_service.start_task(job.task_id, message="正在重建 PS-InSAR 结果目录索引...")
    async with AsyncSessionLocal() as db:
        result = await psinsar_catalog_service.rebuild_catalog(
            db,
            publish_root=publish_root,
            full_rebuild=full_rebuild,
        )
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            progress=100,
            message=(
                f"PS-InSAR 结果目录已重建: 发现 {result.get('manifest_count', 0)} 个包, "
                f"入库 {result.get('registered', 0)} 条, 失败 {result.get('failed', 0)} 条。"
            ),
        )


async def _handle_rebuild_sbas_insar_catalog(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("REBUILD_SBAS_INSAR_CATALOG requires task_id for progress tracking.")
    payload = job.payload or {}
    full_rebuild = bool(payload.get("full_rebuild", True))

    await task_service.start_task(job.task_id, message="Rebuilding SBAS-InSAR result catalog...")
    async with AsyncSessionLocal() as db:
        result = await sbas_insar_catalog_service.rebuild_catalog(
            db,
            full_rebuild=full_rebuild,
        )
        await task_service.update_task(
            job.task_id,
            status="COMPLETED",
            progress=100,
            message=(
                f"SBAS-InSAR result catalog rebuilt: runs={result.get('run_count', 0)}, "
                f"registered={result.get('registered', 0)}, failed={result.get('failed', 0)}, "
                f"issues={result.get('issue_count', 0)}"
            ),
        )


async def _handle_sbas_coregistration(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SBAS_COREGISTRATION requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("SBAS_COREGISTRATION requires run_id payload.")

    rlks = _normalize_positive_int(payload.get("rlks")) or 8
    azlks = _normalize_positive_int(payload.get("azlks")) or 8
    timeout_seconds = _normalize_positive_int(payload.get("timeout_seconds")) or 43200

    await task_service.start_task(job.task_id, message="正在执行 SBAS-InSAR Gamma 共参考配准...")
    await task_service.update_task(
        job.task_id,
        progress=5,
        message=f"准备运行 Gamma SLC_coreg.py: run_id={run_id}",
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        f"SBAS coregistration queued: run_id={run_id}, rlks={rlks}, azlks={azlks}, timeout={timeout_seconds}s",
    )

    from .sbas_insar_production_service import sbas_insar_production_service

    async def _task_keepalive() -> None:
        progress = 12
        while True:
            await asyncio.sleep(60)
            progress = min(88, progress + 2)
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message=f"Gamma 共参考配准仍在运行: run_id={run_id}",
            )

    runner_task = asyncio.create_task(
        asyncio.to_thread(
            sbas_insar_production_service.execute_coregistration,
            run_id,
            rlks=rlks,
            azlks=azlks,
            timeout_seconds=timeout_seconds,
        )
    )
    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        result = await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    manifest = result.get("manifest") or {}
    run = result.get("run") or {}
    summary = (manifest.get("coregistration") or {}).get("summary") or {}
    status = str(run.get("status") or manifest.get("status") or "").strip()
    if status != "COREGISTRATION_READY":
        raise RuntimeError(
            "SBAS coregistration failed: "
            f"status={status or 'UNKNOWN'}, "
            f"missing_dates={summary.get('missing_dates') or []}, "
            f"missing_tabs={summary.get('missing_tabs') or []}"
        )

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            "SBAS-InSAR 共参考配准完成: "
            f"{summary.get('ready_secondary_count', 0)}/{summary.get('expected_secondary_count', 0)} secondary scenes ready"
        ),
    )


async def _handle_sbas_rdc_dem(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SBAS_RDC_DEM requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("SBAS_RDC_DEM requires run_id payload.")

    rlks = _normalize_positive_int(payload.get("rlks")) or 8
    timeout_seconds = _normalize_positive_int(payload.get("timeout_seconds")) or 43200

    await task_service.start_task(job.task_id, message="正在执行 SBAS-InSAR Gamma RDC DEM...")
    await task_service.update_task(
        job.task_id,
        progress=5,
        message=f"准备运行 Gamma gc_map1/gc_map_fine: run_id={run_id}",
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        f"SBAS RDC DEM queued: run_id={run_id}, rlks={rlks}, timeout={timeout_seconds}s",
    )

    from .sbas_insar_production_service import sbas_insar_production_service

    async def _task_keepalive() -> None:
        progress = 12
        while True:
            await asyncio.sleep(60)
            progress = min(88, progress + 2)
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message=f"Gamma RDC DEM 仍在运行: run_id={run_id}",
            )

    runner_task = asyncio.create_task(
        asyncio.to_thread(
            sbas_insar_production_service.execute_rdc_dem,
            run_id,
            rlks=rlks,
            timeout_seconds=timeout_seconds,
        )
    )
    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        result = await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    manifest = result.get("manifest") or {}
    run = result.get("run") or {}
    summary = (manifest.get("rdc_dem") or {}).get("summary") or {}
    status = str(run.get("status") or manifest.get("status") or "").strip()
    if status != "RDC_DEM_READY":
        raise RuntimeError(
            "SBAS RDC DEM failed: "
            f"status={status or 'UNKNOWN'}, "
            f"missing_outputs={summary.get('missing_outputs') or []}"
        )

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            "SBAS-InSAR RDC DEM 完成: "
            f"rdc_dem={((summary.get('outputs') or {}).get('rdc_dem') or {}).get('path') or '-'}"
        ),
    )


async def _handle_sbas_interferograms(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SBAS_INTERFEROGRAMS requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("SBAS_INTERFEROGRAMS requires run_id payload.")

    rlks = _normalize_positive_int(payload.get("rlks")) or 8
    azlks = _normalize_positive_int(payload.get("azlks")) or 8
    timeout_seconds = _normalize_positive_int(payload.get("timeout_seconds")) or 43200
    try:
        unwrap_threshold = float(payload.get("unwrap_threshold") or 0.20)
    except (TypeError, ValueError):
        unwrap_threshold = 0.20

    await task_service.start_task(job.task_id, message="正在执行 SBAS-InSAR Gamma 差分干涉图...")
    await task_service.update_task(
        job.task_id,
        progress=5,
        message=f"准备运行 Gamma phase_sim_orb/SLC_diff_intf/mcf: run_id={run_id}",
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        (
            f"SBAS interferograms queued: run_id={run_id}, rlks={rlks}, azlks={azlks}, "
            f"unwrap_threshold={unwrap_threshold}, timeout={timeout_seconds}s"
        ),
    )

    from .sbas_insar_production_service import sbas_insar_production_service

    async def _task_keepalive() -> None:
        progress = 12
        while True:
            await asyncio.sleep(60)
            progress = min(88, progress + 2)
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message=f"Gamma 差分干涉图仍在运行: run_id={run_id}",
            )

    runner_task = asyncio.create_task(
        asyncio.to_thread(
            sbas_insar_production_service.execute_interferograms,
            run_id,
            rlks=rlks,
            azlks=azlks,
            unwrap_threshold=unwrap_threshold,
            timeout_seconds=timeout_seconds,
        )
    )
    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        result = await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    manifest = result.get("manifest") or {}
    run = result.get("run") or {}
    summary = (manifest.get("interferograms") or {}).get("summary") or {}
    status = str(run.get("status") or manifest.get("status") or "").strip()
    if status != "INTERFEROGRAMS_READY":
        raise RuntimeError(
            "SBAS interferograms failed: "
            f"status={status or 'UNKNOWN'}, "
            f"missing_pairs={summary.get('missing_pairs') or []}, "
            f"missing_tabs={summary.get('missing_tabs') or []}"
        )

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            "SBAS-InSAR 差分干涉图完成: "
            f"{summary.get('ready_pair_count', 0)}/{summary.get('pair_count', 0)} pairs ready"
        ),
    )


async def _handle_sbas_ipta_timeseries(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SBAS_IPTA_TIMESERIES requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("SBAS_IPTA_TIMESERIES requires run_id payload.")

    rlks = _normalize_positive_int(payload.get("rlks")) or 8
    reference_window = _normalize_positive_int(payload.get("reference_window")) or 16
    try:
        mb_mode = int(payload.get("mb_mode") or 0)
    except (TypeError, ValueError):
        mb_mode = 0
    if mb_mode not in {0, 1, 2}:
        mb_mode = 0
    timeout_seconds = _normalize_positive_int(payload.get("timeout_seconds")) or 43200

    await task_service.start_task(job.task_id, message="Running SBAS-InSAR Gamma IPTA time-series inversion...")
    await task_service.update_task(
        job.task_id,
        progress=5,
        message=f"Preparing Gamma mb/ts_rate: run_id={run_id}",
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        (
            f"SBAS IPTA time-series queued: run_id={run_id}, rlks={rlks}, "
            f"reference_window={reference_window}, mb_mode={mb_mode}, "
            f"timeout={timeout_seconds}s"
        ),
    )

    from .sbas_insar_production_service import sbas_insar_production_service

    async def _task_keepalive() -> None:
        progress = 12
        while True:
            await asyncio.sleep(60)
            progress = min(88, progress + 2)
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message=f"Gamma IPTA mb/ts_rate still running: run_id={run_id}",
            )

    runner_task = asyncio.create_task(
        asyncio.to_thread(
            sbas_insar_production_service.execute_ipta_timeseries,
            run_id,
            rlks=rlks,
            reference_window=reference_window,
            mb_mode=mb_mode,
            timeout_seconds=timeout_seconds,
        )
    )
    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        result = await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    manifest = result.get("manifest") or {}
    run = result.get("run") or {}
    summary = (manifest.get("ipta_timeseries") or {}).get("summary") or {}
    status = str(run.get("status") or manifest.get("status") or "").strip()
    if status != "IPTA_TIMESERIES_READY":
        raise RuntimeError(
            "SBAS IPTA time-series failed: "
            f"status={status or 'UNKNOWN'}, "
            f"missing_outputs={summary.get('missing_outputs') or []}"
        )

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            "SBAS-InSAR IPTA time-series complete: "
            f"ts_rate={((summary.get('outputs') or {}).get('ts_rate') or {}).get('path') or '-'}"
        ),
    )


async def _handle_sbas_gamma_workflow(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SBAS_GAMMA_WORKFLOW requires task_id for progress tracking.")
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("SBAS_GAMMA_WORKFLOW requires run_id payload.")

    from_step = str(payload.get("from_step") or "").strip() or None
    to_step = str(payload.get("to_step") or "").strip() or None
    only_steps_raw = payload.get("only_steps") or []
    only_steps = [str(item).strip() for item in only_steps_raw if str(item).strip()] if isinstance(only_steps_raw, list) else []
    force = bool(payload.get("force", False))
    timeout_seconds = _normalize_positive_int(payload.get("timeout_seconds")) or int(settings.GAMMA_SBAS_WORKFLOW_TIMEOUT_SECONDS)

    await task_service.start_task(job.task_id, message="Running Gamma SBAS expert workflow...")
    await task_service.update_task(
        job.task_id,
        progress=5,
        message=f"Preparing Gamma SBAS manifest runner: run_id={run_id}",
    )
    await task_service.add_log(
        job.task_id,
        "INFO",
        (
            f"SBAS Gamma workflow queued: run_id={run_id}, from={from_step or '-'}, "
            f"to={to_step or '-'}, only={only_steps or '-'}, force={force}, timeout={timeout_seconds}s"
        ),
    )

    from .sbas_insar_production_service import sbas_insar_production_service

    async def _task_keepalive() -> None:
        progress = 10
        while True:
            await asyncio.sleep(60)
            progress = min(92, progress + 2)
            await task_service.update_task(
                job.task_id,
                progress=progress,
                message="Gamma SBAS workflow is still running...",
            )

    runner_task = asyncio.create_task(
        asyncio.to_thread(
            sbas_insar_production_service.execute_workflow,
            run_id,
            from_step=from_step,
            to_step=to_step,
            only_steps=only_steps,
            force=force,
            timeout_seconds=timeout_seconds,
        )
    )
    keepalive_task = asyncio.create_task(_task_keepalive())
    try:
        result = await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    run = result.get("run") or {}
    manifest = result.get("manifest") or {}
    workflow = manifest.get("workflow") or {}
    summary = workflow.get("summary") or {}
    status = str(run.get("status") or manifest.get("status") or "").strip()
    if status not in {"WORKFLOW_COMPLETED", "WORKFLOW_PARTIAL"}:
        raise RuntimeError(
            "SBAS Gamma workflow failed: "
            f"status={status or 'UNKNOWN'}, failed_steps={summary.get('failed_count') or 0}"
        )

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            f"Gamma SBAS expert workflow {status.lower()}: "
            f"completed={summary.get('completed_count', 0)}, "
            f"skipped={summary.get('skipped_count', 0)}, "
            f"planned={summary.get('planned_count', 0)}"
        ),
    )


async def _handle_sbas_landsar_workflow(job: SystemJobORM) -> None:
    from .landsar_sbas_service import landsar_sbas_service

    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    auto_select = bool(payload.get("auto_select"))
    if not run_id and not auto_select:
        raise ValueError("SBAS_LANDSAR_WORKFLOW requires run_id or auto_select")
    timeout_seconds = _normalize_positive_int(payload.get("timeout_seconds")) or int(
        getattr(settings, "LANDSAR_SBAS_TIMEOUT_SECONDS", 0) or 172800
    )

    await task_service.start_task(
        job.task_id,
        message=(
            "LandSAR SBAS auto workflow started: selecting LT-1 stack"
            if auto_select
            else f"LandSAR SBAS workflow started: {run_id}"
        ),
    )
    await task_service.update_task(
        job.task_id,
        progress=5,
        message=(
            "Using Gamma SBAS production-area stack discovery for LandSAR input selection..."
            if auto_select
            else f"Preparing LandSAR SBAS workflow: {run_id}"
        ),
    )

    last_log_at = 0.0

    loop = asyncio.get_running_loop()

    def _progress(event: dict[str, Any]) -> None:
        nonlocal last_log_at
        level = str(event.get("level") or "INFO").upper()
        message = str(event.get("message") or "").strip()
        if not message:
            return
        now = time.monotonic()
        if level == "INFO" and now - last_log_at < 0.2:
            return
        last_log_at = now
        try:
            asyncio.run_coroutine_threadsafe(
                task_service.add_log(job.task_id, level, message),
                loop,
            )
        except Exception:
            pass

    if auto_select:
        selection_request = dict(payload.get("selection_request") or {})
        selection_limit = selection_request.get("limit")
        if selection_limit is None:
            selection_limit = 30
        await task_service.add_log(
            job.task_id,
            "INFO",
            (
                "LandSAR auto workflow is reusing Gamma stack discovery: "
                f"admin_region={selection_request.get('admin_region') or '-'}, "
                f"min_scenes={selection_request.get('min_scenes') or '-'}"
            ),
        )
        try:
            detail = await asyncio.to_thread(
                landsar_sbas_service.create_run_from_best_stack,
                run_label=selection_request.get("run_label"),
                source_roots=selection_request.get("source_roots"),
                orbit_roots=selection_request.get("orbit_roots"),
                min_scenes=selection_request.get("min_scenes"),
                discovery_mode=selection_request.get("discovery_mode") or "strict",
                admin_region=selection_request.get("admin_region"),
                aoi_bbox=selection_request.get("aoi_bbox"),
                min_aoi_coverage_ratio=selection_request.get("min_aoi_coverage_ratio", 0.01),
                min_common_overlap_ratio=selection_request.get(
                    "min_common_overlap_ratio",
                    settings.GAMMA_SBAS_MIN_COMMON_OVERLAP_RATIO,
                ),
                limit=selection_limit,
                dem_path=selection_request.get("dem_path"),
                timeout_seconds=selection_request.get("timeout_seconds"),
                import_timeout_seconds=selection_request.get("import_timeout_seconds"),
                params=dict(selection_request.get("params") or {}),
            )
        except Exception as exc:
            await task_service.add_log(job.task_id, "ERROR", f"LandSAR auto stack selection failed: {exc}")
            raise

        run_id = (
            (detail.get("run") or {}).get("run_id")
            or (detail.get("manifest") or {}).get("run_id")
            or ""
        )
        if not run_id:
            raise ValueError("LandSAR auto workflow did not create a run.")
        selection = detail.get("selection") or {}
        await task_service.add_log(
            job.task_id,
            "INFO",
            (
                "LandSAR auto stack selected: "
                f"stack_id={selection.get('selected_stack_id') or '-'}, run_id={run_id}"
            ),
        )
        await task_service.update_task(
            job.task_id,
            progress=15,
            message=f"LandSAR SBAS Run created from Gamma-selected stack: {run_id}",
        )

    runner_task = asyncio.create_task(
        asyncio.to_thread(
            landsar_sbas_service.execute_run,
            run_id,
            timeout_seconds=timeout_seconds,
            progress_callback=_progress,
        )
    )

    async def _keepalive() -> None:
        while not runner_task.done():
            await asyncio.sleep(30)
            await task_service.update_task(
                job.task_id,
                progress=50,
                message=f"LandSAR SBAS workflow is still running: {run_id}",
            )

    keepalive_task = asyncio.create_task(_keepalive())
    try:
        result = await runner_task
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    manifest = result.get("manifest") or {}
    workflow = manifest.get("workflow") or {}
    summary = workflow.get("summary") or {}
    status = str(manifest.get("status") or "").strip().upper()
    if status not in {"LANDSAR_SBAS_COMPLETED", "LANDSAR_SBAS_PARTIAL"}:
        failed_count = summary.get("failed_count") or manifest.get("failed_task_count") or 0
        if status == "LANDSAR_SBAS_RUNTIME_UNSUPPORTED":
            unsupported_count = summary.get("unsupported_proid_count") or 0
            configured_proid = manifest.get("proid") or "unknown"
            message = (
                f"LandSAR SBAS runtime unsupported: configured proID {configured_proid} is not recognized by "
                f"this LandSAR installation. failure_kind=unsupported_proid, "
                f"next_stage={manifest.get('next_stage') or 'configure_landsar_sbas_runtime'}, "
                f"unsupported_tasks={unsupported_count}"
            )
            await task_service.add_log(job.task_id, "ERROR", message)
            await task_service.update_task(
                job.task_id,
                status="FAILED",
                progress=100,
                message=message,
            )
            raise RuntimeError(message)
        raise RuntimeError(
            "LandSAR SBAS workflow failed: "
            f"status={status or 'UNKNOWN'}, failed={failed_count}"
        )

    await task_service.update_task(
        job.task_id,
        status="COMPLETED",
        progress=100,
        message=(
            f"LandSAR SBAS workflow {status.lower()}: "
            f"completed={summary.get('completed_count', 0)}, "
            f"failed={summary.get('failed_count', 0)}"
        ),
    )


_HANDLERS = {
    JOB_TYPE_SCAN_DATA: _handle_scan_data,
    JOB_TYPE_SCAN_ASSET_INVENTORY: _handle_scan_asset_inventory,
    JOB_TYPE_SCAN_DINSAR: _handle_scan_dinsar,
    JOB_TYPE_PUBLISH_DINSAR_PRODUCTS: _handle_publish_dinsar_products_clean,
    JOB_TYPE_REBUILD_DINSAR_CATALOG: _handle_rebuild_dinsar_catalog_clean,
    JOB_TYPE_TIMESERIES_PREPARE: _handle_timeseries_prepare,
    JOB_TYPE_TIMESERIES_STACK_PREP: _handle_timeseries_stack_prep,
    JOB_TYPE_TIMESERIES_MATERIALIZE: _handle_timeseries_materialize,
    JOB_TYPE_TIMESERIES_RUN_ISCE2_STACK: _handle_timeseries_run_isce2_stack,
    JOB_TYPE_TIMESERIES_RUN_MINTPY_SBAS: _handle_timeseries_run_mintpy_sbas,
    JOB_TYPE_TIMESERIES_SARSCAPE_PREFLIGHT: _handle_timeseries_sarscape_preflight,
    JOB_TYPE_TIMESERIES_RUN_SARSCAPE_SBAS: _handle_timeseries_run_sarscape_sbas,
    JOB_TYPE_TIMESERIES_EXPORT_PUBLISH: _handle_timeseries_export_publish,
    JOB_TYPE_TIMESERIES_REGISTER_PRODUCT: _handle_timeseries_register_product,
    JOB_TYPE_REBUILD_PSINSAR_CATALOG: _handle_rebuild_psinsar_catalog,
    JOB_TYPE_REBUILD_SBAS_INSAR_CATALOG: _handle_rebuild_sbas_insar_catalog,
    JOB_TYPE_COPY_DATA: _handle_copy_data,
    JOB_TYPE_UNPACK: _handle_unpack_archives,
    JOB_TYPE_UNPACK_SENTINEL1: _handle_unpack_sentinel1,
    JOB_TYPE_AI_TRAIN: _handle_ai_train,
    JOB_TYPE_AI_PREDICT: _handle_ai_predict,
    JOB_TYPE_AI_ANALYZE: _handle_ai_analyze,
    JOB_TYPE_AI_WARMUP: _handle_ai_warmup,
    JOB_TYPE_AI_DIAGNOSIS: _handle_ai_diagnosis,
    JOB_TYPE_SCAN_HAZARD: _handle_scan_hazard,
    JOB_TYPE_IDL_RUN_IMPORT: _handle_idl_run_import,
    JOB_TYPE_IDL_RUN_DINSAR: _handle_idl_run_dinsar,
    JOB_TYPE_ISCE2_RUN: _handle_isce2_run,
    JOB_TYPE_PYINT_RUN: _handle_pyint_run,
    JOB_TYPE_LANDSAR_RUN: _handle_landsar_run,
    JOB_TYPE_WATER_GEOCODE: _handle_water_geocode,
    JOB_TYPE_SAR_SCENE_PREPROCESS: _handle_sar_scene_preprocess,
    JOB_TYPE_WATER_FLOOD: _handle_water_flood,
    JOB_TYPE_FLOOD_DETECTION: _handle_flood_detection,
    JOB_TYPE_WATER_DETECT: _handle_water_detect,
    JOB_TYPE_GF3_PROCESS: _handle_gf3_process,
    JOB_TYPE_GF3_UNPACK: _handle_gf3_unpack,
    JOB_TYPE_GF3_BATCH_PROCESS: _handle_gf3_batch_process,
    JOB_TYPE_GF3_SARSCAPE_PRODUCE: _handle_gf3_sarscape_produce,
    JOB_TYPE_GF3_SARSCAPE_SYNC: _handle_gf3_sarscape_sync,
    JOB_TYPE_GF3_SARSCAPE_CLEAN: _handle_gf3_sarscape_clean,
    JOB_TYPE_SBAS_COREGISTRATION: _handle_sbas_coregistration,
    JOB_TYPE_SBAS_RDC_DEM: _handle_sbas_rdc_dem,
    JOB_TYPE_SBAS_INTERFEROGRAMS: _handle_sbas_interferograms,
    JOB_TYPE_SBAS_IPTA_TIMESERIES: _handle_sbas_ipta_timeseries,
    JOB_TYPE_SBAS_GAMMA_WORKFLOW: _handle_sbas_gamma_workflow,
    JOB_TYPE_SBAS_LANDSAR_WORKFLOW: _handle_sbas_landsar_workflow,
}


def get_job_handler(job_type: str) -> Optional[JobHandler]:
    return _HANDLERS.get(job_type)
