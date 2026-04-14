from __future__ import annotations

import json
import logging
import os
import re as _re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..auth_service import ROLE_ADMIN
from ..config import settings
from ..database import get_db
from ..models import (
    AuthUserORM,
    RadarDataORM,
    SARSceneGeoORM,
)
from ..services.data_service import data_service
from ..services.dinsar_read_service import dinsar_read_service
from ..services.pairing_state_service import pairing_state_service
from ..utils import find_xml_file
from . import dependencies as _deps
from .dependencies import _get_current_user

router = APIRouter()


@router.get("/statistics")
async def get_statistics(
    fresh: bool = False,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    获取关于Dinsar结果和源数据的统计信息。
    """
    if fresh and current_user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Only admin can force refresh statistics.")

    now_mono = time.monotonic()
    if _deps.STATS_CACHE_TTL_SECONDS > 0 and not fresh:
        async with _deps._STATS_CACHE_LOCK:
            if _deps._STATS_CACHE_DATA is not None and now_mono < _deps._STATS_CACHE_EXPIRES_AT:
                return {
                    **_deps._STATS_CACHE_DATA,
                    "cache_meta": {
                        "enabled": True,
                        "hit": True,
                        "ttl_seconds": _deps.STATS_CACHE_TTL_SECONDS,
                        "generated_at": _deps._STATS_CACHE_GENERATED_AT_UTC,
                    },
                }

    # 1. D-InSAR 结果统计（catalog 主读模型）
    dinsar_records = await dinsar_read_service.list_catalog_records(db)
    dinsar_total_count = len(dinsar_records)
    dinsar_cache_consistency = {
        "db_marked_cached_count": 0,
        "cache_file_exists_count": 0,
        "db_cached_and_file_exists_count": 0,
        "db_cached_but_file_missing_count": 0,
        "db_uncached_but_file_exists_count": 0,
        "db_uncached_and_file_missing_count": 0,
        "manifest_entries_count": 0,
        "manifest_missing_file_count": 0,
    }
    try:
        for record in dinsar_records:
            preview_path = str(record.product.preview_path or "").strip()
            manifest_path = str(record.product.manifest_path or "").strip()
            preview_exists = bool(preview_path and os.path.exists(preview_path))
            fallback_exists = bool(record.image_path and os.path.exists(record.image_path))

            if preview_path:
                dinsar_cache_consistency["db_marked_cached_count"] += 1
            if preview_exists:
                dinsar_cache_consistency["cache_file_exists_count"] += 1
            if preview_path and preview_exists:
                dinsar_cache_consistency["db_cached_and_file_exists_count"] += 1
            elif preview_path and (not preview_exists):
                dinsar_cache_consistency["db_cached_but_file_missing_count"] += 1
            elif fallback_exists:
                dinsar_cache_consistency["db_uncached_but_file_exists_count"] += 1
            else:
                dinsar_cache_consistency["db_uncached_and_file_missing_count"] += 1

            if manifest_path:
                dinsar_cache_consistency["manifest_entries_count"] += 1
                if not os.path.exists(manifest_path):
                    dinsar_cache_consistency["manifest_missing_file_count"] += 1
    except Exception as e:
        dinsar_cache_consistency["error"] = str(e)

    dinsar_cached_count = dinsar_cache_consistency["db_marked_cached_count"]

    # 2. 源数据统计
    source_data_total_count = 0
    envi_processed_count = 0
    with_orbit_data_count = 0
    by_satellite: Dict[str, Any] = {}
    source_preview_consistency = {
        "total_records_count": 0,
        "geo_cache_exists_count": 0,
        "raw_cache_exists_count": 0,
        "preview_exists_count": 0,
        "preview_missing_count": 0,
        "db_ready_count": 0,
        "db_ready_and_cache_exists_count": 0,
        "db_ready_but_cache_missing_count": 0,
    }
    source_xml_consistency = {
        "total_records_count": 0,
        "xml_detected_count": 0,
        "xml_missing_count": 0,
        "xml_parsed_ok_count": 0,
        "xml_detected_but_unparsed_count": 0,
    }

    try:
        source_data_total_count_res = await db.execute(select(func.count(RadarDataORM.id)))
        source_data_total_count = source_data_total_count_res.scalar_one()

        if source_data_total_count > 0:
            envi_processed_count_res = await db.execute(select(func.count(RadarDataORM.id)).where(RadarDataORM.is_envi_processed == True))
            envi_processed_count = envi_processed_count_res.scalar_one()

            with_orbit_data_count_res = await db.execute(select(func.count(RadarDataORM.id)).where(RadarDataORM.has_orbit_data == True))
            with_orbit_data_count = with_orbit_data_count_res.scalar_one()

            by_satellite_res = await db.execute(select(RadarDataORM.satellite, func.count(RadarDataORM.id)).group_by(RadarDataORM.satellite))
            by_satellite = {sat: count for sat, count in by_satellite_res.all()}

        source_rows_res = await db.execute(
            select(
                RadarDataORM.unique_id,
                RadarDataORM.file_path,
                RadarDataORM.preview_cache_status,
                RadarDataORM.scene_center_lon,
                RadarDataORM.scene_center_lat,
                RadarDataORM.acquisition_time_utc,
                RadarDataORM.satellite_mode,
                RadarDataORM.receiving_station,
                RadarDataORM.product_level,
                RadarDataORM.product_unique_id,
            )
        )
        source_rows = source_rows_res.all()
        source_preview_consistency["total_records_count"] = len(source_rows)
        source_xml_consistency["total_records_count"] = len(source_rows)

        for (
            unique_id,
            file_path,
            preview_cache_status,
            scene_center_lon,
            scene_center_lat,
            acquisition_time_utc,
            satellite_mode,
            receiving_station,
            product_level,
            product_unique_id,
        ) in source_rows:
            if not file_path:
                source_preview_consistency["preview_missing_count"] += 1
                source_xml_consistency["xml_missing_count"] += 1
                continue

            cache_key = unique_id or file_path
            raw_cache_path = data_service.get_radar_raw_cache_path(cache_key, file_path)
            geo_cache_path = data_service.get_radar_geo_cache_path(cache_key, file_path)
            has_raw_cache = os.path.exists(raw_cache_path)
            has_geo_cache = os.path.exists(geo_cache_path)

            if has_geo_cache:
                source_preview_consistency["geo_cache_exists_count"] += 1
            if has_raw_cache:
                source_preview_consistency["raw_cache_exists_count"] += 1

            has_any_preview_cache = has_geo_cache or has_raw_cache
            if has_any_preview_cache:
                source_preview_consistency["preview_exists_count"] += 1
            else:
                source_preview_consistency["preview_missing_count"] += 1

            status = (preview_cache_status or "NONE").upper()
            if status == "READY":
                source_preview_consistency["db_ready_count"] += 1
                if has_any_preview_cache:
                    source_preview_consistency["db_ready_and_cache_exists_count"] += 1
                else:
                    source_preview_consistency["db_ready_but_cache_missing_count"] += 1

            scene_dir = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
            xml_path = find_xml_file(scene_dir) if scene_dir else None
            has_xml = bool(xml_path and os.path.exists(xml_path))
            if has_xml:
                source_xml_consistency["xml_detected_count"] += 1
                parsed_ok = any(
                    value is not None and value != ""
                    for value in [
                        scene_center_lon,
                        scene_center_lat,
                        acquisition_time_utc,
                        satellite_mode,
                        receiving_station,
                        product_level,
                        product_unique_id,
                    ]
                )
                if parsed_ok:
                    source_xml_consistency["xml_parsed_ok_count"] += 1
                else:
                    source_xml_consistency["xml_detected_but_unparsed_count"] += 1
            else:
                source_xml_consistency["xml_missing_count"] += 1

    except Exception as e:
        logger.warning("统计源数据时发生错误 (可能是表不存在): %s", e)
        source_preview_consistency["error"] = str(e)
        source_xml_consistency["error"] = str(e)

    # 4. AI 质量统计
    labeled_good_count = sum(1 for record in dinsar_records if record.product.user_label == 1)
    labeled_bad_count = sum(1 for record in dinsar_records if record.product.user_label == 0)

    unlabeled_count = dinsar_total_count - labeled_good_count - labeled_bad_count

    # 5. AI 预测统计
    ai_good_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is not None and record.product.ai_score >= 0.7
    )
    ai_bad_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is not None and record.product.ai_score < 0.4
    )
    ai_medium_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is not None and 0.4 <= record.product.ai_score < 0.7
    )
    ai_unpredicted_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is None
    )

    # 6. IDL 处理统计（读取 runs/*.json）
    idl_processing_stats: Dict[str, Any] = {
        "by_workflow_success": {},
        "avg_duration_by_workflow": {},
    }
    try:
        all_runs = data_service.envi_service_list_runs_all() if hasattr(data_service, "envi_service_list_runs_all") else []
        # 直接读取 runs 目录
        from ..services.envi_service import list_recent_runs as _list_runs
        all_runs = _list_runs(limit=500)
        wf_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failed": 0})
        wf_durations: Dict[str, list] = defaultdict(list)
        for run in all_runs:
            wf = run.get("workflow", "unknown")
            status = run.get("status", "")
            dur = run.get("duration_seconds")
            if status == "success":
                wf_counts[wf]["success"] += 1
            elif status == "failed":
                wf_counts[wf]["failed"] += 1
            if dur is not None:
                try:
                    wf_durations[wf].append(float(dur))
                except (TypeError, ValueError):
                    pass
        idl_processing_stats["by_workflow_success"] = {k: dict(v) for k, v in wf_counts.items()}
        idl_processing_stats["avg_duration_by_workflow"] = {
            k: round(sum(v) / len(v), 1) for k, v in wf_durations.items() if v
        }
    except Exception as _e:
        idl_processing_stats["error"] = str(_e)

    # 8. 水体地理编码一致性检测
    water_geo_consistency: Dict[str, Any] = {
        "water_results_dir": settings.WATER_RESULTS_DIR,
        "dir_scanned_count": 0,
        "geo_db_exists_count": 0,
        "matched_in_db_count": 0,
        "unregistered_count": 0,
        "registered_but_missing_count": 0,
    }
    try:
        import re as _re2
        water_dir = settings.WATER_RESULTS_DIR
        _uid_re = _re2.compile(r"_(\d{7,})$")

        # 从 DB 拉取所有 product_unique_id -> radar_data_id 映射
        uid_rows = await db.execute(
            select(RadarDataORM.product_unique_id, RadarDataORM.id)
            .where(RadarDataORM.product_unique_id.isnot(None))
        )
        uid_to_radar_id: Dict[str, int] = {uid: rid for uid, rid in uid_rows.all() if uid}

        # 从 DB 拉取所有 DONE 的 radar_data_id 集合
        done_rows = await db.execute(
            select(SARSceneGeoORM.radar_data_id, SARSceneGeoORM.geo_path)
            .where(SARSceneGeoORM.status == "DONE")
        )
        done_radar_ids: Dict[int, str] = {rid: gp for rid, gp in done_rows.all()}

        if os.path.isdir(water_dir):
            for entry in os.scandir(water_dir):
                if not entry.is_dir() or not entry.name.startswith("scene_"):
                    continue
                water_geo_consistency["dir_scanned_count"] += 1

                # 检查目录内是否有 *_geo_db 文件
                geo_db_path = None
                for f in os.scandir(entry.path):
                    if f.name.endswith("_geo_db") and not f.name.endswith(".hdr") and not f.name.endswith(".sml"):
                        geo_db_path = f.path
                        break
                if not geo_db_path:
                    continue
                water_geo_consistency["geo_db_exists_count"] += 1

                # 解析 product_unique_id
                m = _uid_re.search(entry.name)
                if not m:
                    continue
                uid = m.group(1).lstrip("0") or m.group(1)
                radar_id = uid_to_radar_id.get(m.group(1)) or uid_to_radar_id.get(uid)
                if not radar_id:
                    continue
                water_geo_consistency["matched_in_db_count"] += 1

                if radar_id not in done_radar_ids:
                    water_geo_consistency["unregistered_count"] += 1

        # 反向检查：DB DONE 但 geo_db 文件不存在
        for radar_id, geo_path in done_radar_ids.items():
            if geo_path and not os.path.exists(geo_path):
                water_geo_consistency["registered_but_missing_count"] += 1

    except Exception as _e:
        water_geo_consistency["error"] = str(_e)

    # 7. D-InSAR 结果按月统计（从 name 字段解析主影像日期）
    dinsar_by_month: list = []
    try:
        _month_counts: Dict[str, int] = defaultdict(int)
        _date_re = _re.compile(r"(\d{8})")
        for record in dinsar_records:
            name = (
                record.product.task_alias
                or record.product.display_name
                or record.product.task_name
                or record.display_name
            )
            if not name:
                continue
            dates = _date_re.findall(name)
            if not dates:
                continue
            master_date = dates[0]
            _month_counts[f"{master_date[:4]}-{master_date[4:6]}"] += 1
        dinsar_by_month = [
            {"month": k, "count": v}
            for k, v in sorted(_month_counts.items())
        ]
    except Exception as _e:
        dinsar_by_month = []

    pairing_consistency: Dict[str, Any] = {
        "metric_cache_count": 0,
        "network_run_count": 0,
        "network_edge_count": 0,
        "dirty_scene_count": 0,
        "cache_status": None,
        "needs_rebuild": None,
        "duplicate_reverse_pair_count": 0,
        "invalid_orientation_count": 0,
        "network_edge_orphan_count": 0,
        "task_orphan_count": 0,
        "result_trace_missing_count": 0,
        "result_trace_orphan_count": 0,
        "result_trace_pair_mismatch_count": 0,
    }
    try:
        pairing_status = await pairing_state_service.get_pairing_system_status(db)
        pairing_consistency["metric_cache_count"] = int(pairing_status.get("pair_count") or 0)
        pairing_consistency["network_run_count"] = int(pairing_status.get("network_run_count") or 0)
        pairing_consistency["network_edge_count"] = int(pairing_status.get("network_edge_count") or 0)
        pairing_consistency["dirty_scene_count"] = int(pairing_status.get("dirty_scene_count") or 0)
        pairing_consistency["cache_status"] = pairing_status.get("status")
        pairing_consistency["needs_rebuild"] = bool(pairing_status.get("needs_rebuild"))
        pairing_consistency["duplicate_reverse_pair_count"] = int(
            pairing_status.get("duplicate_reverse_pair_count") or 0
        )
        pairing_consistency["network_edge_orphan_count"] = int(
            pairing_status.get("orphan_edge_count") or 0
        )

        invalid_orientation_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pairing_metric_cache
                WHERE
                    master_imaging_date IS NULL
                    OR slave_imaging_date IS NULL
                    OR master_scene_uid IS NULL
                    OR slave_scene_uid IS NULL
                    OR master_imaging_date > slave_imaging_date
                    OR (
                        master_imaging_date = slave_imaging_date
                        AND (
                            master_scene_uid > slave_scene_uid
                            OR (
                                master_scene_uid = slave_scene_uid
                                AND master_scene_ref_id > slave_scene_ref_id
                            )
                        )
                    )
                """
            )
        )
        pairing_consistency["invalid_orientation_count"] = int(
            invalid_orientation_result.scalar_one() or 0
        )

        result_trace_missing_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM result_products
                WHERE catalog_name = 'dinsar'
                  AND (
                    COALESCE(pair_uid, '') = ''
                    OR COALESCE(network_run_id, '') = ''
                    OR network_edge_id IS NULL
                    OR COALESCE(policy_version, '') = ''
                  )
                """
            )
        )
        pairing_consistency["result_trace_missing_count"] = int(
            result_trace_missing_result.scalar_one() or 0
        )

        result_trace_orphan_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM result_products rp
                LEFT JOIN pairing_network_runs pnr
                    ON pnr.network_run_id = rp.network_run_id
                LEFT JOIN pairing_network_edges pne
                    ON pne.id = rp.network_edge_id
                    AND pne.network_run_ref_id = pnr.id
                WHERE rp.catalog_name = 'dinsar'
                  AND COALESCE(rp.pair_uid, '') <> ''
                  AND COALESCE(rp.network_run_id, '') <> ''
                  AND rp.network_edge_id IS NOT NULL
                  AND (pnr.id IS NULL OR pne.id IS NULL)
                """
            )
        )
        pairing_consistency["result_trace_orphan_count"] = int(
            result_trace_orphan_result.scalar_one() or 0
        )

        result_trace_pair_mismatch_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM result_products rp
                JOIN pairing_network_runs pnr
                    ON pnr.network_run_id = rp.network_run_id
                JOIN pairing_network_edges pne
                    ON pne.id = rp.network_edge_id
                    AND pne.network_run_ref_id = pnr.id
                JOIN pairing_metric_cache pmc
                    ON pmc.id = pne.metric_cache_ref_id
                WHERE rp.catalog_name = 'dinsar'
                  AND COALESCE(rp.pair_uid, '') <> ''
                  AND COALESCE(pmc.pair_uid, '') <> ''
                  AND rp.pair_uid <> pmc.pair_uid
                """
            )
        )
        pairing_consistency["result_trace_pair_mismatch_count"] = int(
            result_trace_pair_mismatch_result.scalar_one() or 0
        )
    except Exception as _e:
        pairing_consistency["error"] = str(_e)

    stats_payload = {
        "dinsar_results_overview": {
            "total_count": dinsar_total_count,
            "cached_count": dinsar_cached_count,
            "uncached_count": dinsar_total_count - dinsar_cached_count,
        },
        "dinsar_cache_consistency": dinsar_cache_consistency,
        "source_data_overview": {
            "total_count": source_data_total_count,
            "envi_processed_count": envi_processed_count,
            "with_orbit_data_count": with_orbit_data_count,
        },
        "source_preview_consistency": source_preview_consistency,
        "source_xml_consistency": source_xml_consistency,
        "water_geo_consistency": water_geo_consistency,
        "pairing_consistency": pairing_consistency,
        "by_satellite": by_satellite,
        "idl_processing_stats": idl_processing_stats,
        "dinsar_by_month": dinsar_by_month,
        "ai_quality_overview": {
            "good_count": labeled_good_count,
            "bad_count": labeled_bad_count,
            "unlabeled_count": unlabeled_count
        },
        "ai_prediction_overview": {
            "good_count": ai_good_count,
            "bad_count": ai_bad_count,
            "medium_count": ai_medium_count,
            "unpredicted_count": ai_unpredicted_count
        }
    }

    generated_at = datetime.utcnow().isoformat() + "Z"
    if _deps.STATS_CACHE_TTL_SECONDS > 0:
        async with _deps._STATS_CACHE_LOCK:
            _deps._STATS_CACHE_DATA = stats_payload
            _deps._STATS_CACHE_EXPIRES_AT = time.monotonic() + _deps.STATS_CACHE_TTL_SECONDS
            _deps._STATS_CACHE_GENERATED_AT_UTC = generated_at

    return {
        **stats_payload,
        "cache_meta": {
            "enabled": _deps.STATS_CACHE_TTL_SECONDS > 0,
            "hit": False,
            "ttl_seconds": _deps.STATS_CACHE_TTL_SECONDS,
            "generated_at": generated_at,
        },
    }
