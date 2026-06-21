"""
空间计算服务 - 纯 PostGIS 实现

将所有空间计算下放到数据库层，利用 PostGIS 的高效空间索引。
"""
import hashlib
import json
import logging
import math
import uuid
from collections import defaultdict
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, case, cast, func, or_, text
from sqlalchemy.orm import aliased

from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
from geoalchemy2.functions import ST_Intersects, ST_Intersection, ST_Area, ST_Centroid, ST_Covers
from shapely.geometry import Polygon
from shapely.ops import unary_union

from ..models import (
    DinsarProductionRunItemORM,
    DinsarProductionRunORM,
    HazardPoint,
    HazardPointORM,
    PairingNetworkEdgeORM,
    PairingNetworkRunORM,
    PairingMetricCacheORM,
    PairingRequest,
    PsRequest,
    RadarData,
    RadarDataORM,
    RadarPair,
    ResultProductORM,
    TimeseriesStackPlanEdgeORM,
    TimeseriesStackPlanItemORM,
    TimeseriesStackPlanORM,
)
from .dinsar_naming import build_pair_key, build_task_alias, ensure_unique_task_aliases
from .pairing_state_service import pairing_state_service


PAIRING_POLICY_VERSION = "2026.06.dinsar-production.v1"
PAIRING_WARNING_CANDIDATE_THRESHOLD = 3000
PAIRING_ALL_STRATEGY_HARD_LIMIT = 20000
logger = logging.getLogger(__name__)


def _normalized_satellite_family_expr(alias):
    compact_satellite = func.upper(
        func.replace(
            func.replace(
                func.replace(func.coalesce(alias.satellite, ""), "-", ""),
                "_",
                "",
            ),
            " ",
            "",
        )
    )
    inferred_family = case(
        (
            compact_satellite.in_(
                ["LT1", "LT1A", "LT1B", "LUTAN1", "LUTAN1A", "LUTAN1B"]
            ),
            "LT1",
        ),
        (
            compact_satellite.in_(
                [
                    "S1",
                    "S1A",
                    "S1B",
                    "S1C",
                    "SENTINEL1",
                    "SENTINEL1A",
                    "SENTINEL1B",
                    "SENTINEL1C",
                ]
            ),
            "S1",
        ),
        else_=func.upper(alias.satellite),
    )
    return func.coalesce(func.nullif(func.upper(alias.satellite_family), ""), inferred_family)


def _same_relative_orbit_expr(left_alias, right_alias):
    left_relative_orbit = func.upper(func.trim(func.coalesce(left_alias.relative_orbit, "")))
    right_relative_orbit = func.upper(func.trim(func.coalesce(right_alias.relative_orbit, "")))
    return and_(
        left_relative_orbit != "",
        right_relative_orbit != "",
        left_relative_orbit == right_relative_orbit,
    )


class SpatialService:
    """
    纯 PostGIS 空间计算服务
    
    利用 PostGIS 空间索引执行高效的地理计算，
    比 Python Shapely 快 10-100 倍。
    """
    
    async def find_dinsar_pairs(
        self,
        db: AsyncSession,
        params: PairingRequest,
        aoi_wkt: Optional[str] = None,
        require_orbit_data: bool = True,
    ) -> Tuple[List[RadarPair], List[str], Dict[str, Any]]:
        """
        基于 pairing_metric_cache 的统一配对入口。

        该路径不再静默回退到另一套 Python/SQL 语义，而是只使用候选缓存层。
        当缓存处于 DIRTY/DEGRADED 状态时允许返回降级结果，但会明确告警。
        """
        warnings: List[str] = []
        effective_params = self._normalize_pairing_request(params)
        pairing_status = await pairing_state_service.get_pairing_system_status(db)

        cache_status = str(pairing_status.get("status") or "UNINITIALIZED")
        scene_count = int(pairing_status.get("scene_count") or 0)
        pair_count = int(pairing_status.get("pair_count") or 0)
        degraded = bool(pairing_status.get("needs_rebuild"))

        if cache_status in {"FAILED", "UNINITIALIZED", "ERROR"} or (scene_count > 1 and pair_count == 0):
            raise RuntimeError(
                "配对候选缓存当前不可用，请先在生产规划页执行“修复配对基础”或“强制全量重建”。"
            )

        if degraded:
            warnings.append(
                f"配对候选缓存当前状态为 {cache_status}，本次结果基于现有缓存生成，建议尽快在生产规划页执行缓存修复。"
            )

        candidate_pool = await self._query_pairing_metric_cache(
            db,
            effective_params,
            aoi_wkt=aoi_wkt,
            require_orbit_data=require_orbit_data,
        )

        if len(candidate_pool) > PAIRING_ALL_STRATEGY_HARD_LIMIT:
            raise RuntimeError(
                f"全部配对命中 {len(candidate_pool)} 条候选边，超过系统一次性返回上限 "
                f"{PAIRING_ALL_STRATEGY_HARD_LIMIT}。请收紧 AOI、日期范围、重叠率或中心距离阈值。"
            )

        if len(candidate_pool) > PAIRING_WARNING_CANDIDATE_THRESHOLD:
            warnings.append(
                f"候选配对数超过 {PAIRING_WARNING_CANDIDATE_THRESHOLD}（当前: {len(candidate_pool)}），建议收紧参数或缩小 AOI。"
            )

        selected_candidates, strategy_warnings = self._apply_dinsar_production_strategy(candidate_pool, effective_params)
        warnings.extend(strategy_warnings)
        if not selected_candidates:
            warnings.extend(
                await self._build_empty_pairing_diagnostics(
                    db,
                    effective_params,
                    aoi_wkt=aoi_wkt,
                    require_orbit_data=require_orbit_data,
                )
            )

        for candidate in selected_candidates:
            candidate["selection_strategy"] = "dinsar_production"
            self._ensure_candidate_identity(candidate)

        network_run_id = await self._persist_network_run(
            db,
            params=effective_params,
            aoi_wkt=aoi_wkt,
            require_orbit_data=require_orbit_data,
            warnings=warnings,
            candidate_pool=candidate_pool,
            selected_candidates=selected_candidates,
        )

        await self._attach_dinsar_production_summaries(db, selected_candidates)
        result_pairs = self._generate_task_names(self._build_radar_pairs(selected_candidates))
        metadata = {
            "fallback_used": False,
            "degraded": degraded,
            "policy_version": PAIRING_POLICY_VERSION,
            "network_run_id": network_run_id,
            "candidate_count": len(candidate_pool),
            "selected_edge_count": len(result_pairs),
        }
        return result_pairs, warnings, metadata

    def _normalize_pairing_request(self, params: PairingRequest) -> PairingRequest:
        updates: Dict[str, Any] = {}

        if params.aoi_overlap_threshold is not None and float(params.aoi_overlap_threshold) <= 0:
            updates["aoi_overlap_threshold"] = None

        if params.start_date:
            if not params.master_date_from:
                updates["master_date_from"] = params.start_date
            if not params.slave_date_from:
                updates["slave_date_from"] = params.start_date

        return params.model_copy(update=updates) if updates else params

    async def _query_pairing_metric_cache(
        self,
        db: AsyncSession,
        params: PairingRequest,
        *,
        aoi_wkt: Optional[str],
        require_orbit_data: bool,
    ) -> List[dict]:
        master_alias = aliased(RadarDataORM)
        slave_alias = aliased(RadarDataORM)
        master_family_expr = _normalized_satellite_family_expr(master_alias)
        slave_family_expr = _normalized_satellite_family_expr(slave_alias)
        center_distance_expr = func.coalesce(
            PairingMetricCacheORM.scene_center_distance_meters,
            PairingMetricCacheORM.spatial_baseline_meters,
        )

        stmt = (
            select(PairingMetricCacheORM, master_alias, slave_alias)
            .join(master_alias, master_alias.id == PairingMetricCacheORM.master_scene_ref_id)
            .join(slave_alias, slave_alias.id == PairingMetricCacheORM.slave_scene_ref_id)
            .where(
                PairingMetricCacheORM.metric_version == pairing_state_service.metric_version,
                PairingMetricCacheORM.status == "READY",
                PairingMetricCacheORM.time_baseline_days >= params.time_baseline_min,
                PairingMetricCacheORM.time_baseline_days <= params.time_baseline_max,
                PairingMetricCacheORM.master_imaging_date < PairingMetricCacheORM.slave_imaging_date,
                PairingMetricCacheORM.scene_overlap_ratio >= params.overlap_threshold,
                PairingMetricCacheORM.same_look_direction.is_(True),
                PairingMetricCacheORM.dinsar_readiness.in_(["RECOMMENDED", "CANDIDATE"]),
            )
        )

        stmt = stmt.where(center_distance_expr <= params.spatial_baseline_max_meters)
        stmt = stmt.where(
            or_(
                and_(master_family_expr == "LT1", slave_family_expr == "LT1"),
                and_(master_family_expr == "S1", slave_family_expr == "S1"),
            )
        )

        if require_orbit_data:
            stmt = stmt.where(
                master_alias.has_orbit_data.is_(True),
                slave_alias.has_orbit_data.is_(True),
            )

        stmt = stmt.where(PairingMetricCacheORM.same_satellite_family.is_(True))

        if params.require_same_imaging_mode:
            stmt = stmt.where(PairingMetricCacheORM.same_imaging_mode.is_(True))

        if params.require_same_polarization:
            stmt = stmt.where(PairingMetricCacheORM.same_polarization.is_(True))

        stmt = stmt.where(
            or_(
                master_family_expr != "S1",
                slave_family_expr != "S1",
                _same_relative_orbit_expr(master_alias, slave_alias),
            )
        )

        if params.allowed_satellites:
            allowed_satellites = []
            for item in params.allowed_satellites:
                compact = str(item).strip().upper().replace("-", "").replace("_", "").replace(" ", "")
                if compact in {"LT1", "LT1A", "LT1B", "LUTAN1", "LUTAN1A", "LUTAN1B"}:
                    allowed_satellites.append("LT1")
                elif compact in {"S1", "S1A", "S1B", "S1C", "SENTINEL1", "SENTINEL1A", "SENTINEL1B", "SENTINEL1C"}:
                    allowed_satellites.append("S1")
            allowed_satellites = list(dict.fromkeys(allowed_satellites))
            if not allowed_satellites:
                return []
            stmt = stmt.where(
                master_family_expr.in_(allowed_satellites),
                slave_family_expr.in_(allowed_satellites),
            )

        if params.master_date_from:
            stmt = stmt.where(PairingMetricCacheORM.master_imaging_date >= params.master_date_from)
        if params.master_date_to:
            stmt = stmt.where(PairingMetricCacheORM.master_imaging_date <= params.master_date_to)
        if params.slave_date_from:
            stmt = stmt.where(PairingMetricCacheORM.slave_imaging_date >= params.slave_date_from)
        if params.slave_date_to:
            stmt = stmt.where(PairingMetricCacheORM.slave_imaging_date <= params.slave_date_to)

        if aoi_wkt:
            aoi_geom = func.ST_GeomFromText(aoi_wkt, 4326)
            aoi_geog = cast(aoi_geom, Geography)
            aoi_area = func.nullif(ST_Area(aoi_geog), 0)
            pair_overlap_geom = ST_Intersection(master_alias.geom, slave_alias.geom)
            pair_aoi_geom = ST_Intersection(pair_overlap_geom, aoi_geom)
            pair_aoi_overlap_expr = (ST_Area(cast(pair_aoi_geom, Geography)) / aoi_area).label("pair_aoi_overlap_ratio")
            stmt = stmt.add_columns(pair_aoi_overlap_expr)
            stmt = stmt.where(
                ST_Intersects(pair_overlap_geom, aoi_geom),
            )
            if params.aoi_overlap_threshold is not None:
                stmt = stmt.where(pair_aoi_overlap_expr >= params.aoi_overlap_threshold)

        stmt = stmt.order_by(
            PairingMetricCacheORM.master_imaging_date.asc(),
            PairingMetricCacheORM.slave_imaging_date.asc(),
            PairingMetricCacheORM.dinsar_quality_tier.asc(),
            func.coalesce(PairingMetricCacheORM.dinsar_quality_score, 0).desc(),
            func.coalesce(PairingMetricCacheORM.scene_overlap_ratio, 0).desc(),
            center_distance_expr.asc(),
            PairingMetricCacheORM.pair_uid.asc(),
        )

        result = await db.execute(stmt)
        candidate_pool: List[dict] = []
        for row in result.all():
            metric_row, master_row, slave_row = row[0], row[1], row[2]
            pair_aoi_overlap_ratio = row[3] if len(row) > 3 else None
            center_distance = float(
                metric_row.scene_center_distance_meters
                if metric_row.scene_center_distance_meters is not None
                else (metric_row.spatial_baseline_meters or 0)
            )
            candidate_pool.append(
                {
                    "metric_cache_ref_id": int(metric_row.id),
                    "pair_uid": metric_row.pair_uid,
                    "master_scene_uid": metric_row.master_scene_uid,
                    "slave_scene_uid": metric_row.slave_scene_uid,
                    "master": RadarData.model_validate(master_row),
                    "slave": RadarData.model_validate(slave_row),
                    "days": int(metric_row.time_baseline_days or 0),
                    "dist": center_distance,
                    "scene_center_distance_meters": center_distance,
                    "overlap_ratio": float(metric_row.scene_overlap_ratio or 0),
                    "dinsar_quality_tier": metric_row.dinsar_quality_tier,
                    "dinsar_quality_score": (
                        float(metric_row.dinsar_quality_score)
                        if metric_row.dinsar_quality_score is not None
                        else None
                    ),
                    "dinsar_readiness": metric_row.dinsar_readiness,
                    "dinsar_reasons": [
                        str(item)
                        for item in (metric_row.dinsar_reasons_json or [])
                        if isinstance(item, str) and item.strip()
                    ],
                    "same_relative_orbit": bool(metric_row.same_relative_orbit),
                    "master_relative_orbit": metric_row.master_relative_orbit,
                    "slave_relative_orbit": metric_row.slave_relative_orbit,
                    "pair_aoi_overlap_ratio": (
                        float(pair_aoi_overlap_ratio)
                        if pair_aoi_overlap_ratio is not None
                        else None
                    ),
                }
            )
        return candidate_pool

    def _ensure_candidate_identity(self, candidate: dict) -> Tuple[str, str]:
        master = candidate["master"]
        slave = candidate["slave"]
        task_alias = str(candidate.get("task_alias") or "").strip() or build_task_alias(
            master.imaging_date,
            slave.imaging_date,
        )
        pair_key = str(candidate.get("pair_key") or "").strip() or build_pair_key(
            master.file_path,
            slave.file_path,
            master.imaging_date,
            slave.imaging_date,
            master.satellite_family or slave.satellite_family or master.satellite or slave.satellite,
        )
        candidate["task_alias"] = task_alias
        candidate["pair_key"] = pair_key
        return task_alias, pair_key

    def _build_radar_pairs(self, selected_candidates: List[dict]) -> List[RadarPair]:
        result_pairs: List[RadarPair] = []
        for candidate in selected_candidates:
            master = candidate["master"]
            slave = candidate["slave"]
            task_alias, pair_key = self._ensure_candidate_identity(candidate)
            selection_score = candidate.get("selection_score")
            result_pairs.append(
                RadarPair(
                    master=master,
                    slave=slave,
                    task_name=task_alias,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    pair_uid=candidate.get("pair_uid"),
                    metric_cache_ref_id=candidate.get("metric_cache_ref_id"),
                    network_run_id=candidate.get("network_run_id"),
                    network_edge_id=candidate.get("network_edge_id"),
                    policy_version=candidate.get("policy_version"),
                    selection_strategy=candidate.get("selection_strategy"),
                    selection_score=float(selection_score) if selection_score is not None else None,
                    selection_reason=candidate.get("selection_reason"),
                    time_baseline_days=int(candidate["days"]),
                    spatial_baseline_meters=float(candidate["dist"]),
                    scene_center_distance_meters=float(
                        candidate.get("scene_center_distance_meters")
                        if candidate.get("scene_center_distance_meters") is not None
                        else candidate.get("dist") or 0
                    ),
                    scene_overlap_ratio=float(candidate.get("overlap_ratio") or 0.0),
                    pair_aoi_overlap_ratio=(
                        float(candidate["pair_aoi_overlap_ratio"])
                        if candidate.get("pair_aoi_overlap_ratio") is not None
                        else None
                    ),
                    dinsar_quality_tier=candidate.get("dinsar_quality_tier"),
                    dinsar_quality_score=(
                        float(candidate["dinsar_quality_score"])
                        if candidate.get("dinsar_quality_score") is not None
                        else None
                    ),
                    dinsar_readiness=candidate.get("dinsar_readiness"),
                    dinsar_reasons=candidate.get("dinsar_reasons") or [],
                    same_relative_orbit=bool(candidate.get("same_relative_orbit")),
                    master_relative_orbit=candidate.get("master_relative_orbit"),
                    slave_relative_orbit=candidate.get("slave_relative_orbit"),
                    production_summary=candidate.get("production_summary"),
                )
            )
        return result_pairs

    async def _attach_dinsar_production_summaries(
        self,
        db: AsyncSession,
        selected_candidates: List[dict],
    ) -> None:
        if not selected_candidates:
            return

        pair_uids: set[str] = set()
        pair_keys: set[str] = set()
        aliases: set[str] = set()
        for candidate in selected_candidates:
            task_alias, pair_key = self._ensure_candidate_identity(candidate)
            pair_uid = str(candidate.get("pair_uid") or "").strip()
            if pair_uid:
                pair_uids.add(pair_uid)
            if pair_key:
                pair_keys.add(pair_key)
            if task_alias:
                aliases.add(task_alias)

        run_conditions = []
        if pair_uids:
            run_conditions.append(DinsarProductionRunItemORM.pair_uid.in_(pair_uids))
        if pair_keys:
            run_conditions.append(DinsarProductionRunItemORM.pair_key.in_(pair_keys))
        if aliases:
            run_conditions.append(DinsarProductionRunItemORM.task_alias.in_(aliases))
            run_conditions.append(DinsarProductionRunItemORM.task_name.in_(aliases))

        product_conditions = []
        if pair_uids:
            product_conditions.append(ResultProductORM.pair_uid.in_(pair_uids))
        if pair_keys:
            product_conditions.append(ResultProductORM.pair_key.in_(pair_keys))
        if aliases:
            product_conditions.append(ResultProductORM.task_alias.in_(aliases))
            product_conditions.append(ResultProductORM.task_name.in_(aliases))

        run_rows = []
        if run_conditions:
            result = await db.execute(
                select(DinsarProductionRunItemORM, DinsarProductionRunORM)
                .join(DinsarProductionRunORM, DinsarProductionRunItemORM.run_id == DinsarProductionRunORM.run_id)
                .where(or_(*run_conditions))
                .order_by(
                    DinsarProductionRunItemORM.updated_at.desc().nullslast(),
                    DinsarProductionRunItemORM.id.desc(),
                )
            )
            run_rows = result.all()

        products = []
        if product_conditions:
            result = await db.execute(
                select(ResultProductORM)
                .where(ResultProductORM.catalog_name == "dinsar")
                .where(or_(*product_conditions))
                .order_by(
                    ResultProductORM.published_at.desc().nullslast(),
                    ResultProductORM.id.desc(),
                )
            )
            products = result.scalars().all()

        run_by_uid: Dict[str, List[Tuple[DinsarProductionRunItemORM, DinsarProductionRunORM]]] = defaultdict(list)
        run_by_key: Dict[str, List[Tuple[DinsarProductionRunItemORM, DinsarProductionRunORM]]] = defaultdict(list)
        run_by_alias: Dict[str, List[Tuple[DinsarProductionRunItemORM, DinsarProductionRunORM]]] = defaultdict(list)
        for item, run in run_rows:
            pair_uid = str(item.pair_uid or "").strip()
            pair_key = str(item.pair_key or "").strip()
            if pair_uid:
                run_by_uid[pair_uid].append((item, run))
            if pair_key:
                run_by_key[pair_key].append((item, run))
            for alias in {str(item.task_alias or "").strip(), str(item.task_name or "").strip()}:
                if alias:
                    run_by_alias[alias].append((item, run))

        products_by_uid: Dict[str, List[ResultProductORM]] = defaultdict(list)
        products_by_key: Dict[str, List[ResultProductORM]] = defaultdict(list)
        products_by_alias: Dict[str, List[ResultProductORM]] = defaultdict(list)
        for product in products:
            pair_uid = str(product.pair_uid or "").strip()
            pair_key = str(product.pair_key or "").strip()
            if pair_uid:
                products_by_uid[pair_uid].append(product)
            if pair_key:
                products_by_key[pair_key].append(product)
            for alias in {str(product.task_alias or "").strip(), str(product.task_name or "").strip()}:
                if alias:
                    products_by_alias[alias].append(product)

        for candidate in selected_candidates:
            task_alias, pair_key = self._ensure_candidate_identity(candidate)
            pair_uid = str(candidate.get("pair_uid") or "").strip()
            exact_runs = self._dedupe_by_object_id(
                [*run_by_uid.get(pair_uid, []), *run_by_key.get(pair_key, [])],
                key=lambda row: getattr(row[0], "id", None),
            )
            alias_runs = self._dedupe_by_object_id(
                run_by_alias.get(task_alias, []),
                key=lambda row: getattr(row[0], "id", None),
            )
            exact_products = self._dedupe_by_object_id(
                [*products_by_uid.get(pair_uid, []), *products_by_key.get(pair_key, [])],
                key=lambda product: getattr(product, "id", None),
            )
            alias_products = self._dedupe_by_object_id(
                products_by_alias.get(task_alias, []),
                key=lambda product: getattr(product, "id", None),
            )
            matched_runs = exact_runs or alias_runs
            matched_products = exact_products or alias_products
            candidate["production_summary"] = self._summarize_dinsar_production(
                matched_runs,
                matched_products,
                match_level="identity" if (exact_runs or exact_products) else ("task_alias" if (alias_runs or alias_products) else "none"),
            )

    def _summarize_dinsar_production(
        self,
        run_rows: List[Tuple[DinsarProductionRunItemORM, DinsarProductionRunORM]],
        products: List[ResultProductORM],
        *,
        match_level: str = "none",
    ) -> Dict[str, Any]:
        latest_run_row = max(
            run_rows,
            key=lambda row: self._datetime_sort_key(
                row[0].updated_at,
                row[0].ended_at,
                row[0].started_at,
                row[0].created_at,
            ),
            default=None,
        )
        latest_product = max(
            products,
            key=lambda product: self._datetime_sort_key(
                product.published_at,
                product.produced_at,
                product.updated_at,
                product.registered_at,
            ),
            default=None,
        )
        ready_products = [product for product in products if self._is_ready_result_product(product)]
        completed_statuses = {"COMPLETED", "READY", "SUCCESS", "PUBLISHED"}
        failed_statuses = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}
        completed_run_count = sum(
            1
            for item, run in run_rows
            if str(item.status or "").strip().upper() in completed_statuses
            or str(run.status or "").strip().upper() in completed_statuses
        )
        failed_run_count = sum(
            1
            for item, run in run_rows
            if str(item.status or "").strip().upper() in failed_statuses
            or str(run.status or "").strip().upper() in failed_statuses
        )

        latest_item = latest_run_row[0] if latest_run_row else None
        latest_run = latest_run_row[1] if latest_run_row else None
        if ready_products and latest_product is not None:
            latest_status = str(latest_product.status or "").strip().upper()
        elif latest_item is not None or latest_run is not None:
            latest_status = str(
                (latest_item.status if latest_item is not None else None)
                or (latest_run.status if latest_run is not None else None)
                or ""
            ).strip().upper()
        elif latest_product is not None:
            latest_status = str(latest_product.status or "").strip().upper()
        else:
            latest_status = ""
        if ready_products:
            status = "READY"
        elif completed_run_count > 0:
            status = "COMPLETED"
        elif latest_status:
            status = latest_status
        else:
            status = "MISSING"

        engine_codes = sorted(
            {
                str(value or "").strip().lower()
                for value in [
                    *(product.engine_code for product in products),
                    *(run.engine_code for _, run in run_rows),
                ]
                if str(value or "").strip()
            }
        )
        return {
            "has_record": bool(run_rows or products),
            "is_produced": bool(ready_products or completed_run_count > 0),
            "status": status,
            "match_level": match_level,
            "run_item_count": len(run_rows),
            "completed_run_count": completed_run_count,
            "failed_run_count": failed_run_count,
            "product_count": len(products),
            "ready_product_count": len(ready_products),
            "engine_codes": engine_codes,
            "latest_engine_code": (
                str(latest_product.engine_code or "").strip().lower()
                if latest_product is not None and latest_product.engine_code
                else (
                    str(latest_run.engine_code or "").strip().lower()
                    if latest_run is not None and latest_run.engine_code
                    else None
                )
            ),
            "latest_run_id": latest_run.run_id if latest_run is not None else None,
            "latest_run_status": latest_run.status if latest_run is not None else None,
            "latest_item_status": latest_item.status if latest_item is not None else None,
            "latest_output_dir": latest_item.latest_output_dir if latest_item is not None else None,
            "latest_product_id": latest_product.id if latest_product is not None else None,
            "latest_product_identifier": latest_product.product_id if latest_product is not None else None,
            "latest_product_status": latest_product.status if latest_product is not None else None,
            "latest_product_health": latest_product.health_status if latest_product is not None else None,
            "latest_product_published_at": latest_product.published_at if latest_product is not None else None,
            "updated_at": (
                latest_item.updated_at
                if latest_item is not None
                else (
                    latest_product.updated_at
                    if latest_product is not None
                    else None
                )
            ),
        }

    def _is_ready_result_product(self, product: ResultProductORM) -> bool:
        status = str(product.status or "").strip().upper()
        health = str(product.health_status or "").strip().upper()
        return status in {"READY", "COMPLETED", "SUCCESS"} and health not in {"ERROR", "FAILED"}

    def _datetime_sort_key(self, *values: Any) -> float:
        for value in values:
            if value is None:
                continue
            try:
                return float(value.timestamp())
            except Exception:
                continue
        return 0.0

    def _dedupe_by_object_id(self, items: List[Any], *, key) -> List[Any]:
        seen: set[Any] = set()
        output: List[Any] = []
        for item in items:
            item_key = key(item)
            if item_key is None:
                item_key = id(item)
            if item_key in seen:
                continue
            seen.add(item_key)
            output.append(item)
        return output

    async def _persist_network_run(
        self,
        db: AsyncSession,
        *,
        params: PairingRequest,
        aoi_wkt: Optional[str],
        require_orbit_data: bool,
        warnings: List[str],
        candidate_pool: List[dict],
        selected_candidates: List[dict],
    ) -> str:
        request_payload = params.model_dump(exclude_none=True)
        request_payload["require_orbit_data"] = bool(require_orbit_data)
        aoi_hash = self._stable_sha1(aoi_wkt) if aoi_wkt else None
        request_hash = self._stable_sha1(
            {
                "params": request_payload,
                "aoi_hash": aoi_hash,
            }
        )

        network_run = PairingNetworkRunORM(
            network_run_id=f"pnr_{uuid.uuid4().hex[:24]}",
            strategy=params.strategy,
            policy_version=PAIRING_POLICY_VERSION,
            request_hash=request_hash,
            request_params_json=request_payload,
            aoi_source="wkt" if aoi_wkt else None,
            aoi_hash=aoi_hash,
            aoi_summary_json=self._build_aoi_summary(aoi_wkt),
            candidate_count=len(candidate_pool),
            selected_edge_count=len(selected_candidates),
            warning_count=len(warnings),
            status="READY",
            fallback_used=False,
        )
        db.add(network_run)
        await db.flush()

        for edge_rank, candidate in enumerate(self._sorted_candidates(selected_candidates), start=1):
            edge = PairingNetworkEdgeORM(
                network_run_ref_id=network_run.id,
                metric_cache_ref_id=int(candidate["metric_cache_ref_id"]),
                edge_rank=edge_rank,
                selection_reason=candidate.get("selection_reason"),
                selection_score=(
                    float(candidate["selection_score"])
                    if candidate.get("selection_score") is not None
                    else None
                ),
                selection_meta_json=self._build_edge_meta(candidate),
                is_reference_edge=bool(candidate.get("is_reference_edge")),
            )
            db.add(edge)
            await db.flush()
            candidate["network_run_id"] = network_run.network_run_id
            candidate["network_edge_id"] = int(edge.id)
            candidate["policy_version"] = PAIRING_POLICY_VERSION

        await db.commit()
        return network_run.network_run_id

    def _build_aoi_summary(self, aoi_wkt: Optional[str]) -> Optional[Dict[str, Any]]:
        geometry = self._parse_optional_aoi_polygon(aoi_wkt)
        if geometry is None:
            return None
        min_x, min_y, max_x, max_y = geometry.bounds
        return {
            "geom_type": geometry.geom_type,
            "bounds": [float(min_x), float(min_y), float(max_x), float(max_y)],
            "area": float(geometry.area or 0.0),
        }

    def _build_edge_meta(self, candidate: dict) -> Dict[str, Any]:
        return {
            "selection_strategy": candidate.get("selection_strategy"),
            "reference_image_id": candidate.get("reference_image_id"),
            "master_scene_uid": candidate.get("master_scene_uid"),
            "slave_scene_uid": candidate.get("slave_scene_uid"),
            "pair_uid": candidate.get("pair_uid"),
            "pair_key": candidate.get("pair_key"),
            "task_alias": candidate.get("task_alias"),
            "time_baseline_days": int(candidate.get("days") or 0),
            "spatial_baseline_meters": float(candidate.get("dist") or 0.0),
            "scene_center_distance_meters": float(
                candidate.get("scene_center_distance_meters")
                if candidate.get("scene_center_distance_meters") is not None
                else candidate.get("dist") or 0.0
            ),
            "legacy_spatial_baseline_field": "scene_center_distance_meters",
            "scene_overlap_ratio": float(candidate.get("overlap_ratio") or 0.0),
            "pair_aoi_overlap_ratio": (
                float(candidate["pair_aoi_overlap_ratio"])
                if candidate.get("pair_aoi_overlap_ratio") is not None
                else None
            ),
        }

    def _stable_sha1(self, value: Any) -> str:
        if isinstance(value, str):
            payload = value
        else:
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()

    def _build_timeseries_stack_identity(
        self,
        direction: Optional[str],
        scenes: List[RadarDataORM],
    ) -> Dict[str, Any]:
        sorted_scenes = sorted(scenes, key=lambda item: str(item.imaging_date or ""))
        first = sorted_scenes[0]
        satellite = self._normalize_timeseries_satellite_family(first)
        imaging_mode = str(first.imaging_mode or "").strip() or "UNKNOWN"
        polarization = str(first.polarization or "").strip() or "UNKNOWN"
        orbit_direction = (
            str(direction or first.orbit_direction or "").strip().upper() or "UNKNOWN"
        )
        group_key = "_".join(
            part
            for part in (satellite, imaging_mode, polarization, orbit_direction)
            if str(part).strip()
        )
        stack_dates = [
            str(item.imaging_date or "").strip()
            for item in sorted_scenes
            if str(item.imaging_date or "").strip()
        ]
        digest = self._stable_sha1(
            {
                "direction": orbit_direction,
                "scene_ids": [int(item.id) for item in sorted_scenes],
                "stack_dates": stack_dates,
            }
        )[:10]
        date_start = stack_dates[0] if stack_dates else "NA"
        date_end = stack_dates[-1] if stack_dates else "NA"
        return {
            "direction": orbit_direction,
            "group_key": group_key,
            "stack_key": f"{group_key}_{date_start}_{date_end}_{digest}",
            "stack_dates": stack_dates,
        }

    async def _build_timeseries_network_edges(
        self,
        db: AsyncSession,
        scenes: List[RadarDataORM],
        params: PsRequest,
        *,
        aoi_wkt: Optional[str],
        selection_mode: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        scene_ids = [int(item.id) for item in scenes if item.id is not None]
        if len(scene_ids) < 2:
            return [], []

        master_alias = aliased(RadarDataORM)
        slave_alias = aliased(RadarDataORM)
        center_distance_expr = func.coalesce(
            PairingMetricCacheORM.scene_center_distance_meters,
            PairingMetricCacheORM.spatial_baseline_meters,
        )
        stmt = (
            select(PairingMetricCacheORM, master_alias, slave_alias)
            .join(master_alias, master_alias.id == PairingMetricCacheORM.master_scene_ref_id)
            .join(slave_alias, slave_alias.id == PairingMetricCacheORM.slave_scene_ref_id)
            .where(
                PairingMetricCacheORM.metric_version == pairing_state_service.metric_version,
                PairingMetricCacheORM.status == "READY",
                PairingMetricCacheORM.master_scene_ref_id.in_(scene_ids),
                PairingMetricCacheORM.slave_scene_ref_id.in_(scene_ids),
                PairingMetricCacheORM.time_baseline_days >= params.time_baseline_min,
                PairingMetricCacheORM.time_baseline_days <= params.time_baseline_max,
                center_distance_expr <= params.spatial_baseline_max_meters,
                PairingMetricCacheORM.scene_overlap_ratio >= params.network_overlap_threshold,
                PairingMetricCacheORM.same_look_direction.is_(True),
            )
            .order_by(
                PairingMetricCacheORM.master_imaging_date.asc(),
                PairingMetricCacheORM.slave_imaging_date.asc(),
                PairingMetricCacheORM.time_baseline_days.asc(),
                center_distance_expr.asc(),
                func.coalesce(PairingMetricCacheORM.scene_overlap_ratio, 0).desc(),
                PairingMetricCacheORM.id.asc(),
            )
        )
        result = await db.execute(stmt)

        candidate_pool: List[dict] = []
        for metric_row, master_row, slave_row in result.all():
            center_distance = float(
                metric_row.scene_center_distance_meters
                if metric_row.scene_center_distance_meters is not None
                else (metric_row.spatial_baseline_meters or 0.0)
            )
            candidate_pool.append(
                {
                    "metric_cache_ref_id": int(metric_row.id),
                    "pair_uid": metric_row.pair_uid,
                    "master_scene_uid": metric_row.master_scene_uid,
                    "slave_scene_uid": metric_row.slave_scene_uid,
                    "master": RadarData.model_validate(master_row),
                    "slave": RadarData.model_validate(slave_row),
                    "days": int(metric_row.time_baseline_days or 0),
                    "dist": center_distance,
                    "scene_center_distance_meters": center_distance,
                    "overlap_ratio": float(metric_row.scene_overlap_ratio or 0.0),
                }
            )

        warnings: List[str] = []
        if not candidate_pool:
            warnings.append(
                "No pairing_metric_cache edges matched the time-series SBAS network thresholds."
            )
            return [], warnings
        candidate_scene_ids = {
            int(candidate[role].id)
            for candidate in candidate_pool
            for role in ("master", "slave")
            if candidate.get(role) is not None
        }
        missing_scene_count = len(set(scene_ids) - candidate_scene_ids)
        if missing_scene_count > 0:
            warnings.append(
                f"{missing_scene_count} selected scenes have no metric-cache edge under the current SBAS thresholds."
            )

        pairing_params = PairingRequest(
            time_baseline_min=params.time_baseline_min,
            time_baseline_max=params.time_baseline_max,
            overlap_threshold=params.network_overlap_threshold,
            spatial_baseline_max_meters=params.spatial_baseline_max_meters,
            coverage_diversity_penalty=0.3,
            require_same_imaging_mode=True,
            require_same_polarization=True,
            strategy="sbas",
            num_connections=params.num_connections,
        )
        selected_candidates, strategy_warnings = self._apply_sbas_strategy(
            candidate_pool,
            pairing_params,
            aoi_wkt=aoi_wkt,
        )
        warnings.extend(strategy_warnings)

        edges: List[Dict[str, Any]] = []
        for edge_rank, candidate in enumerate(self._sorted_candidates(selected_candidates), start=1):
            master = candidate["master"]
            slave = candidate["slave"]
            edges.append(
                {
                    "edge_rank": edge_rank,
                    "metric_cache_ref_id": candidate.get("metric_cache_ref_id"),
                    "master_scene_ref_id": int(master.id),
                    "slave_scene_ref_id": int(slave.id),
                    "master_imaging_date": master.imaging_date,
                    "slave_imaging_date": slave.imaging_date,
                    "temporal_baseline_days": int(candidate.get("days") or 0),
                    "spatial_baseline_meters": float(candidate.get("dist") or 0.0),
                    "scene_center_distance_meters": float(
                        candidate.get("scene_center_distance_meters")
                        if candidate.get("scene_center_distance_meters") is not None
                        else candidate.get("dist") or 0.0
                    ),
                    "scene_overlap_ratio": float(candidate.get("overlap_ratio") or 0.0),
                    "selection_reason": candidate.get("selection_reason"),
                    "selection_score": (
                        float(candidate["selection_score"])
                        if candidate.get("selection_score") is not None
                        else None
                    ),
                    "selection_meta_json": {
                        "source": "pairing_metric_cache",
                        "selection_mode": selection_mode,
                        "pair_uid": candidate.get("pair_uid"),
                        "metric_version": pairing_state_service.metric_version,
                        "scene_center_distance_meters": float(
                            candidate.get("scene_center_distance_meters")
                            if candidate.get("scene_center_distance_meters") is not None
                            else candidate.get("dist") or 0.0
                        ),
                        "time_baseline_min": params.time_baseline_min,
                        "time_baseline_max": params.time_baseline_max,
                        "spatial_baseline_max_meters": params.spatial_baseline_max_meters,
                        "network_overlap_threshold": params.network_overlap_threshold,
                        "num_connections": params.num_connections,
                    },
                    "enabled": True,
                }
            )

        if not edges:
            warnings.append("SBAS strategy did not select any network edges for this stack.")
        return edges, warnings

    async def _persist_timeseries_stack_plan(
        self,
        db: AsyncSession,
        *,
        direction: Optional[str],
        params: PsRequest,
        aoi_wkt: Optional[str],
        scenes: List[RadarDataORM],
        common_aoi_coverage_ratio: Optional[float] = None,
        coverage_consistency_ratio: Optional[float] = None,
        threshold_satisfied: Optional[bool] = None,
        selection_mode: Optional[str] = None,
        network_edges: Optional[List[Dict[str, Any]]] = None,
        network_warnings: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        request_payload = params.model_dump(exclude_none=True)
        aoi_hash = self._stable_sha1(aoi_wkt) if aoi_wkt else None
        identity = self._build_timeseries_stack_identity(direction, scenes)
        plan = TimeseriesStackPlanORM(
            plan_id=f"tsp_{uuid.uuid4().hex[:24]}",
            strategy="sbas_stack",
            request_hash=self._stable_sha1(
                {
                    "params": request_payload,
                    "aoi_hash": aoi_hash,
                    "direction": identity.get("direction"),
                    "scene_ids": [int(item.id) for item in scenes],
                }
            ),
            request_params_json=request_payload,
            aoi_source="wkt" if aoi_wkt else None,
            aoi_hash=aoi_hash,
            aoi_summary_json=self._build_aoi_summary(aoi_wkt),
            direction=identity.get("direction"),
            scene_count=len(scenes),
            stack_key=identity.get("stack_key"),
            group_key=identity.get("group_key"),
            status="READY",
            created_by="system:find_ps_timeseries",
        )
        db.add(plan)
        await db.flush()

        sorted_scenes = sorted(scenes, key=lambda item: str(item.imaging_date or ""))
        scene_payloads: List[RadarData] = []
        plan_item_by_scene_id: Dict[int, TimeseriesStackPlanItemORM] = {}
        safe_network_edges = list(network_edges or [])
        safe_network_warnings = [str(item) for item in (network_warnings or []) if str(item).strip()]
        for rank, item in enumerate(sorted_scenes, start=1):
            plan_item = TimeseriesStackPlanItemORM(
                plan_ref_id=plan.id,
                radar_data_ref_id=int(item.id) if item.id is not None else None,
                scene_rank=rank,
                file_path=item.file_path,
                satellite=item.satellite,
                imaging_date=item.imaging_date,
                imaging_mode=item.imaging_mode,
                polarization=item.polarization,
                has_orbit_data=bool(item.has_orbit_data),
                selection_meta_json={
                    "source": "find_ps_timeseries",
                    "direction": identity.get("direction"),
                    "group_key": identity.get("group_key"),
                    "stack_key": identity.get("stack_key"),
                    "initial_overlap_threshold": params.initial_overlap_threshold,
                    "final_overlap_threshold": params.final_overlap_threshold,
                    "common_aoi_coverage_ratio": common_aoi_coverage_ratio,
                    "coverage_consistency_ratio": coverage_consistency_ratio,
                    "threshold_satisfied": threshold_satisfied,
                    "selection_mode": selection_mode,
                    "network_edge_count": len(safe_network_edges),
                    "network_warnings": safe_network_warnings,
                    "orbit_direction": item.orbit_direction,
                    "satellite_family": self._normalize_timeseries_satellite_family(item),
                    "bbox": [item.min_lon, item.min_lat, item.max_lon, item.max_lat],
                    "scene_unique_id": item.unique_id,
                },
            )
            db.add(plan_item)
            await db.flush()
            if item.id is not None:
                plan_item_by_scene_id[int(item.id)] = plan_item
            scene_payloads.append(
                RadarData.model_validate(item).model_copy(
                    update={
                        "orbit_direction": identity.get("direction") or item.orbit_direction,
                        "stack_plan_id": plan.plan_id,
                        "stack_plan_item_id": int(plan_item.id),
                        "stack_scene_rank": rank,
                        "stack_group_key": identity.get("group_key"),
                        "stack_key": identity.get("stack_key"),
                        "stack_common_aoi_coverage_ratio": common_aoi_coverage_ratio,
                        "stack_coverage_consistency_ratio": coverage_consistency_ratio,
                        "stack_threshold_satisfied": threshold_satisfied,
                        "stack_selection_mode": selection_mode,
                        "stack_network_edge_count": len(safe_network_edges),
                        "stack_network_warnings": safe_network_warnings,
                    }
                )
            )

        for edge_payload in safe_network_edges:
            master_scene_id = edge_payload.get("master_scene_ref_id")
            slave_scene_id = edge_payload.get("slave_scene_ref_id")
            master_plan_item = (
                plan_item_by_scene_id.get(int(master_scene_id))
                if master_scene_id is not None
                else None
            )
            slave_plan_item = (
                plan_item_by_scene_id.get(int(slave_scene_id))
                if slave_scene_id is not None
                else None
            )
            edge = TimeseriesStackPlanEdgeORM(
                plan_ref_id=plan.id,
                master_plan_item_ref_id=(
                    int(master_plan_item.id)
                    if master_plan_item is not None and master_plan_item.id is not None
                    else None
                ),
                slave_plan_item_ref_id=(
                    int(slave_plan_item.id)
                    if slave_plan_item is not None and slave_plan_item.id is not None
                    else None
                ),
                metric_cache_ref_id=edge_payload.get("metric_cache_ref_id"),
                master_scene_ref_id=master_scene_id,
                slave_scene_ref_id=slave_scene_id,
                edge_rank=int(edge_payload.get("edge_rank") or 0),
                master_imaging_date=edge_payload.get("master_imaging_date"),
                slave_imaging_date=edge_payload.get("slave_imaging_date"),
                temporal_baseline_days=edge_payload.get("temporal_baseline_days"),
                spatial_baseline_meters=edge_payload.get("spatial_baseline_meters"),
                perpendicular_baseline_meters=edge_payload.get("perpendicular_baseline_meters"),
                scene_overlap_ratio=edge_payload.get("scene_overlap_ratio"),
                pair_aoi_overlap_ratio=edge_payload.get("pair_aoi_overlap_ratio"),
                selection_reason=edge_payload.get("selection_reason"),
                selection_score=edge_payload.get("selection_score"),
                selection_meta_json=edge_payload.get("selection_meta_json"),
                enabled=bool(edge_payload.get("enabled", True)),
            )
            db.add(edge)

        return {
            "plan_id": plan.plan_id,
            "group_key": identity.get("group_key"),
            "stack_key": identity.get("stack_key"),
            "edge_count": len(safe_network_edges),
            "network_warnings": safe_network_warnings,
            "scenes": scene_payloads,
        }

    def _apply_dinsar_production_strategy(
        self,
        candidate_pool: List[dict],
        params: PairingRequest,
    ) -> Tuple[List[dict], List[str]]:
        if not candidate_pool:
            return [], []

        warnings: List[str] = []
        rejected_count = sum(
            1
            for candidate in candidate_pool
            if str(candidate.get("dinsar_readiness") or "").upper() == "NOT_RECOMMENDED"
        )
        if rejected_count:
            warnings.append(f"{rejected_count}条候选因D-InSAR生产前置条件不足被过滤。")

        selected = []
        for candidate in candidate_pool:
            readiness = str(candidate.get("dinsar_readiness") or "CANDIDATE").upper()
            if readiness == "NOT_RECOMMENDED":
                continue
            tier = str(candidate.get("dinsar_quality_tier") or "C").upper()
            quality_score = candidate.get("dinsar_quality_score")
            selected.append(
                {
                    **candidate,
                    "selection_reason": f"dinsar_{readiness.lower()}_{tier.lower()}",
                    "selection_score": (
                        float(quality_score)
                        if quality_score is not None
                        else self._score_pair_candidate(candidate, params)
                    ),
                }
            )

        selected.sort(
            key=lambda item: (
                {"A": 0, "B": 1, "C": 2}.get(str(item.get("dinsar_quality_tier") or "C").upper(), 9),
                -float(item.get("selection_score") or 0),
                int(item.get("days") or 0),
                float(item.get("dist") or 0),
                str(getattr(item.get("master"), "imaging_date", "") or ""),
                str(getattr(item.get("slave"), "imaging_date", "") or ""),
            )
        )
        return selected, warnings

    async def _build_empty_pairing_diagnostics(
        self,
        db: AsyncSession,
        params: PairingRequest,
        *,
        aoi_wkt: Optional[str],
        require_orbit_data: bool,
    ) -> List[str]:
        allowed_families: List[str] = []
        for item in params.allowed_satellites or []:
            compact = str(item).strip().upper().replace("-", "").replace("_", "").replace(" ", "")
            if compact in {"LT1", "LT1A", "LT1B", "LUTAN1", "LUTAN1A", "LUTAN1B"}:
                allowed_families.append("LT1")
            elif compact in {"S1", "S1A", "S1B", "S1C", "SENTINEL1", "SENTINEL1A", "SENTINEL1B", "SENTINEL1C"}:
                allowed_families.append("S1")
        allowed_families = list(dict.fromkeys(allowed_families))

        sql = text(
            """
            WITH base AS (
                SELECT
                    pmc.*,
                    m.satellite AS master_satellite_actual,
                    s.satellite AS slave_satellite_actual,
                    m.has_orbit_data AS master_has_orbit,
                    s.has_orbit_data AS slave_has_orbit,
                    COALESCE(pmc.scene_center_distance_meters, pmc.spatial_baseline_meters) AS center_m
                FROM pairing_metric_cache pmc
                JOIN radar_data m ON m.id = pmc.master_scene_ref_id
                JOIN radar_data s ON s.id = pmc.slave_scene_ref_id
                WHERE pmc.metric_version = :metric_version
                  AND pmc.status = 'READY'
                  AND pmc.master_imaging_date < pmc.slave_imaging_date
                  AND pmc.same_look_direction IS TRUE
                  AND pmc.same_satellite_family IS TRUE
                  AND pmc.dinsar_readiness IN ('RECOMMENDED', 'CANDIDATE')
                  AND (:require_orbit_data IS FALSE OR (m.has_orbit_data IS TRUE AND s.has_orbit_data IS TRUE))
                  AND (:require_same_imaging_mode IS FALSE OR pmc.same_imaging_mode IS TRUE)
                  AND (:require_same_polarization IS FALSE OR pmc.same_polarization IS TRUE)
                  AND (
                      :allowed_families_is_empty IS TRUE
                      OR pmc.master_satellite_family = ANY(:allowed_families)
                      OR pmc.master_satellite = ANY(:allowed_families)
                  )
                  AND (CAST(:master_date_from AS text) IS NULL OR pmc.master_imaging_date >= CAST(:master_date_from AS text))
                  AND (CAST(:master_date_to AS text) IS NULL OR pmc.master_imaging_date <= CAST(:master_date_to AS text))
                  AND (CAST(:slave_date_from AS text) IS NULL OR pmc.slave_imaging_date >= CAST(:slave_date_from AS text))
                  AND (CAST(:slave_date_to AS text) IS NULL OR pmc.slave_imaging_date <= CAST(:slave_date_to AS text))
                  AND (
                      CAST(:aoi_wkt AS text) IS NULL
                      OR ST_Intersects(
                          ST_Intersection(m.geom, s.geom),
                          ST_GeomFromText(CAST(:aoi_wkt AS text), 4326)
                      )
                  )
            ),
            time_ok AS (
                SELECT * FROM base
                WHERE time_baseline_days BETWEEN :time_baseline_min AND :time_baseline_max
            ),
            overlap_ok AS (
                SELECT * FROM time_ok
                WHERE scene_overlap_ratio >= :overlap_threshold
            ),
            center_ok AS (
                SELECT * FROM overlap_ok
                WHERE center_m <= :center_distance_max
            )
            SELECT
                (SELECT count(*) FROM base) AS base_count,
                (SELECT count(*) FROM time_ok) AS time_ok_count,
                (SELECT count(*) FROM overlap_ok) AS overlap_ok_count,
                (SELECT count(*) FROM center_ok) AS center_ok_count,
                (
                    SELECT json_build_object(
                        'master_date', master_imaging_date,
                        'slave_date', slave_imaging_date,
                        'master_satellite', master_satellite_actual,
                        'slave_satellite', slave_satellite_actual,
                        'time_baseline_days', time_baseline_days,
                        'center_meters', center_m,
                        'overlap_ratio', scene_overlap_ratio,
                        'quality_tier', dinsar_quality_tier,
                        'readiness', dinsar_readiness
                    )
                    FROM base
                    ORDER BY
                        CASE
                            WHEN time_baseline_days BETWEEN :time_baseline_min AND :time_baseline_max
                            THEN 0 ELSE 1
                        END,
                        CASE WHEN scene_overlap_ratio >= :overlap_threshold THEN 0 ELSE 1 END,
                        abs(time_baseline_days - :time_baseline_max),
                        center_m ASC NULLS LAST
                    LIMIT 1
                ) AS nearest_candidate;
            """
        )
        result = await db.execute(
            sql,
            {
                "metric_version": pairing_state_service.metric_version,
                "require_orbit_data": require_orbit_data,
                "require_same_imaging_mode": bool(params.require_same_imaging_mode),
                "require_same_polarization": bool(params.require_same_polarization),
                "allowed_families": allowed_families or ["__NONE__"],
                "allowed_families_is_empty": not allowed_families,
                "master_date_from": params.master_date_from or None,
                "master_date_to": params.master_date_to or None,
                "slave_date_from": params.slave_date_from or None,
                "slave_date_to": params.slave_date_to or None,
                "aoi_wkt": aoi_wkt,
                "time_baseline_min": int(params.time_baseline_min),
                "time_baseline_max": int(params.time_baseline_max),
                "overlap_threshold": float(params.overlap_threshold),
                "center_distance_max": float(params.spatial_baseline_max_meters),
            },
        )
        row = result.mappings().first()
        if not row:
            return ["未找到满足条件的 D-InSAR 配对；诊断查询未返回统计结果。"]

        base_count = int(row.get("base_count") or 0)
        time_ok_count = int(row.get("time_ok_count") or 0)
        overlap_ok_count = int(row.get("overlap_ok_count") or 0)
        center_ok_count = int(row.get("center_ok_count") or 0)
        if base_count <= 0:
            family_text = "、".join(allowed_families) if allowed_families else "LT-1/Sentinel-1"
            return [
                f"未找到 {family_text} 的可生产基础候选边。请检查数据体系、主从日期范围、AOI、精轨绑定和配对缓存状态。"
            ]

        messages = [
            (
                "当前筛选下基础候选 {base} 条；时间基线 {min_days}-{max_days} 天后剩 {time_ok} 条；"
                "重叠率 >= {overlap:.2f} 后剩 {overlap_ok} 条；footprint 中心距离 <= {center:.0f} 米后剩 {center_ok} 条。"
            ).format(
                base=base_count,
                min_days=int(params.time_baseline_min),
                max_days=int(params.time_baseline_max),
                time_ok=time_ok_count,
                overlap=float(params.overlap_threshold),
                overlap_ok=overlap_ok_count,
                center=float(params.spatial_baseline_max_meters),
                center_ok=center_ok_count,
            )
        ]
        nearest = row.get("nearest_candidate")
        if isinstance(nearest, str):
            try:
                nearest = json.loads(nearest)
            except Exception:
                nearest = None
        if isinstance(nearest, dict):
            messages.append(
                (
                    "最接近的一对是 {master_satellite} {master_date} -> {slave_satellite} {slave_date}，"
                    "时间基线 {days} 天，中心距离 {center:.1f} 米，重叠率 {overlap:.3f}，质量 {tier}/{readiness}。"
                ).format(
                    master_satellite=nearest.get("master_satellite") or "?",
                    master_date=nearest.get("master_date") or "?",
                    slave_satellite=nearest.get("slave_satellite") or "?",
                    slave_date=nearest.get("slave_date") or "?",
                    days=int(nearest.get("time_baseline_days") or 0),
                    center=float(nearest.get("center_meters") or 0.0),
                    overlap=float(nearest.get("overlap_ratio") or 0.0),
                    tier=nearest.get("quality_tier") or "?",
                    readiness=nearest.get("readiness") or "?",
                )
            )
        return messages

    def _apply_strategy(
        self,
        candidate_pool: List[dict],
        params: PairingRequest,
        aoi_wkt: Optional[str] = None,
    ) -> Tuple[List[dict], List[str]]:
        """根据策略处理候选配对池，并返回策略级告警。"""
        if not candidate_pool:
            return [], []

        if params.strategy == "sbas":
            return self._apply_sbas_strategy(candidate_pool, params, aoi_wkt=aoi_wkt)
        if params.strategy == "sequential":
            return self._apply_sequential_strategy(candidate_pool, params.num_connections, params)
        if params.strategy == "star":
            return self._apply_star_strategy(candidate_pool, params.reference_image_id, params)
        return self._apply_all_strategy(candidate_pool, params)

    def _score_pair_candidate(self, candidate: dict, params: PairingRequest) -> float:
        max_time = max(float(params.time_baseline_max or 1), 1.0)
        time_score = 1.0 - min(float(candidate.get("days") or 0) / max_time, 1.0)
        if params.limit_footprint_center_distance:
            max_center = max(float(params.spatial_baseline_max_meters or 1), 1.0)
            center_score = 1.0 - min(float(candidate.get("dist") or 0) / max_center, 1.0)
        else:
            center_score = 1.0
        overlap_score = min(max(float(candidate.get("overlap_ratio") or 0), 0.0), 1.0)
        source_score = 1.0 if (
            bool(getattr(candidate.get("master"), "insar_source_ready", False))
            and bool(getattr(candidate.get("slave"), "insar_source_ready", False))
        ) else 0.0
        orbit_score = 1.0 if (
            bool(getattr(candidate.get("master"), "has_orbit_data", False))
            and bool(getattr(candidate.get("slave"), "has_orbit_data", False))
        ) else 0.0
        return (
            0.25 * time_score
            + 0.20 * center_score
            + 0.35 * overlap_score
            + 0.15 * source_score
            + 0.05 * orbit_score
        )

    def _apply_all_strategy(
        self,
        candidate_pool: List[dict],
        params: PairingRequest,
    ) -> Tuple[List[dict], List[str]]:
        return (
            [
                {
                    **candidate,
                    "selection_reason": "all_candidate",
                    "selection_score": self._score_pair_candidate(candidate, params),
                }
                for candidate in self._sorted_candidates(candidate_pool)
            ],
            [],
        )

    def _apply_sequential_strategy(
        self,
        candidate_pool: List[dict],
        num_connections: int,
        params: PairingRequest,
    ) -> Tuple[List[dict], List[str]]:
        """
        Sequential: 按稳定时间序列排序，每景连接后续 N 景。
        同日多景通过 acquisition_time_utc -> imaging_date -> scene_uid 稳定排序。
        """
        scene_entries = self._build_scene_entries(candidate_pool)
        if len(scene_entries) < 2:
            return [], []

        pair_index = self._build_pair_index(candidate_pool)
        selected: List[dict] = []
        selected_ids = set()
        safe_num_connections = max(1, int(num_connections or 1))

        for index, scene_entry in enumerate(scene_entries):
            picked_count = 0
            for next_index in range(index + 1, len(scene_entries)):
                if picked_count >= safe_num_connections:
                    break
                if next_index >= len(scene_entries):
                    break
                candidate = pair_index.get(
                    self._pair_lookup_key(scene_entry["id"], scene_entries[next_index]["id"])
                )
                if not candidate:
                    continue
                candidate_id = int(candidate.get("metric_cache_ref_id") or 0)
                if candidate_id in selected_ids:
                    continue
                selected_ids.add(candidate_id)
                selected.append(
                    {
                        **candidate,
                        "selection_reason": "sequential_neighbor",
                        "selection_score": self._score_pair_candidate(candidate, params),
                    }
                )
                picked_count += 1

        return selected, []

    def _apply_star_strategy(
        self,
        candidate_pool: List[dict],
        reference_image_id: Optional[int],
        params: PairingRequest,
    ) -> Tuple[List[dict], List[str]]:
        """
        Star: 参考像固定为 master。
        如果未显式指定，自动选择稳定时间序列居中的场景作为参考像。
        """
        warnings: List[str] = []
        scene_entries = self._build_scene_entries(candidate_pool)
        if not scene_entries:
            return [], warnings

        if reference_image_id is None:
            master_side_ids = {int(candidate["master"].id) for candidate in candidate_pool}
            center_index = len(scene_entries) // 2
            ranked_entries = sorted(
                enumerate(scene_entries),
                key=lambda item: (
                    abs(item[0] - center_index),
                    item[0],
                ),
            )
            selected_entry = next(
                (entry for _, entry in ranked_entries if int(entry["id"]) in master_side_ids),
                None,
            )
            if selected_entry is None:
                warnings.append("当前候选边中不存在可作为 master 的参考像，星型配对无法生成结果。")
                return [], warnings
            reference_image_id = int(selected_entry["id"])
            warnings.append(f"未指定参考像，已自动选择场景 ID {reference_image_id} 作为星型配对参考像。")

        available_scene_ids = {int(entry["id"]) for entry in scene_entries}
        if reference_image_id not in available_scene_ids:
            warnings.append(f"指定的参考像 ID {reference_image_id} 不在当前候选场景集中。")
            return [], warnings

        master_side_ids = {int(candidate["master"].id) for candidate in candidate_pool}
        if reference_image_id not in master_side_ids:
            warnings.append(
                f"指定的参考像 ID {reference_image_id} 在当前候选边中无法作为 master，按“参考像固定为 master”规则不生成星型结果。"
            )
            return [], warnings

        selected: List[dict] = []
        skipped_slave_side = 0
        for candidate in self._sorted_candidates(candidate_pool):
            if int(candidate["master"].id) == int(reference_image_id):
                selected.append(
                    {
                        **candidate,
                        "selection_reason": "star_reference_master",
                        "selection_score": self._score_pair_candidate(candidate, params),
                        "is_reference_edge": True,
                        "reference_image_id": int(reference_image_id),
                    }
                )
            elif int(candidate["slave"].id) == int(reference_image_id):
                skipped_slave_side += 1

        if skipped_slave_side > 0:
            warnings.append(
                f"参考像 ID {reference_image_id} 在 {skipped_slave_side} 条候选边中位于 slave 侧；按“参考像固定为 master”规则，这些边已被排除。"
            )

        return selected, warnings

    def _apply_sbas_strategy(
        self,
        candidate_pool: List[dict],
        params: PairingRequest,
        *,
        aoi_wkt: Optional[str] = None,
    ) -> Tuple[List[dict], List[str]]:
        """
        SBAS: 先构造时间骨架，再补齐连通性、低度节点和覆盖多样性。
        """
        warnings: List[str] = []
        scene_entries = self._build_scene_entries(candidate_pool)
        if len(scene_entries) < 2:
            return [], warnings

        scene_ids = [int(entry["id"]) for entry in scene_entries]
        pair_index = self._build_pair_index(candidate_pool)
        sorted_candidates = self._sorted_candidates(candidate_pool)
        degree: Dict[int, int] = defaultdict(int)
        parents = {scene_id: scene_id for scene_id in scene_ids}
        selected: List[dict] = []
        selected_ids = set()
        selected_coverage = Polygon()
        geometry_cache: Dict[int, Any] = {}
        aoi_poly = self._parse_optional_aoi_polygon(aoi_wkt)

        min_degree = min(max(1, int(params.num_connections or 1)), max(1, len(scene_ids) - 1))
        max_degree = min(max(min_degree + 2, 3), max(1, len(scene_ids) - 1))
        max_edges = min(
            len(candidate_pool),
            max(len(scene_ids) - 1, len(scene_ids) * min_degree),
        )

        def select_candidate(candidate: dict, *, reason: str, score: float) -> bool:
            nonlocal selected_coverage
            candidate_id = int(candidate.get("metric_cache_ref_id") or 0)
            if candidate_id in selected_ids:
                return False

            selected_ids.add(candidate_id)
            selected.append(
                {
                    **candidate,
                    "selection_reason": reason,
                    "selection_score": float(score),
                }
            )

            master_id = int(candidate["master"].id)
            slave_id = int(candidate["slave"].id)
            degree[master_id] += 1
            degree[slave_id] += 1
            self._union_components(parents, master_id, slave_id)

            candidate_geom = self._get_candidate_intersection_geom(
                candidate,
                aoi_poly=aoi_poly,
                geometry_cache=geometry_cache,
            )
            if candidate_geom is not None and not candidate_geom.is_empty:
                selected_coverage = unary_union([selected_coverage, candidate_geom])
            return True

        for index in range(len(scene_entries) - 1):
            candidate = pair_index.get(
                self._pair_lookup_key(scene_entries[index]["id"], scene_entries[index + 1]["id"])
            )
            if not candidate:
                continue
            score = self._score_sbas_candidate(
                candidate,
                params,
                selected_coverage=selected_coverage,
                geometry_cache=geometry_cache,
                aoi_poly=aoi_poly,
            )
            select_candidate(candidate, reason="sbas_time_skeleton", score=score)

        for candidate in sorted_candidates:
            if len(selected) >= max_edges or self._component_count(parents) <= 1:
                break
            candidate_id = int(candidate.get("metric_cache_ref_id") or 0)
            if candidate_id in selected_ids:
                continue
            master_id = int(candidate["master"].id)
            slave_id = int(candidate["slave"].id)
            if self._find_component(parents, master_id) == self._find_component(parents, slave_id):
                continue
            score = self._score_sbas_candidate(
                candidate,
                params,
                selected_coverage=selected_coverage,
                geometry_cache=geometry_cache,
                aoi_poly=aoi_poly,
            ) + 0.35
            select_candidate(candidate, reason="sbas_connect_components", score=score)

        while len(selected) < max_edges:
            low_degree_ids = {scene_id for scene_id in scene_ids if degree[scene_id] < min_degree}
            component_count = self._component_count(parents)
            best_candidate: Optional[dict] = None
            best_reason = "sbas_fill"
            best_score: Optional[float] = None
            best_tiebreak: Optional[Tuple[Any, ...]] = None

            for candidate in sorted_candidates:
                candidate_id = int(candidate.get("metric_cache_ref_id") or 0)
                if candidate_id in selected_ids:
                    continue

                master_id = int(candidate["master"].id)
                slave_id = int(candidate["slave"].id)
                if degree[master_id] >= max_degree and degree[slave_id] >= max_degree:
                    continue

                score = self._score_sbas_candidate(
                    candidate,
                    params,
                    selected_coverage=selected_coverage,
                    geometry_cache=geometry_cache,
                    aoi_poly=aoi_poly,
                )
                reason = "sbas_fill"

                if component_count > 1 and self._find_component(parents, master_id) != self._find_component(parents, slave_id):
                    score += 0.35
                    reason = "sbas_connect_components"

                if low_degree_ids and (master_id in low_degree_ids or slave_id in low_degree_ids):
                    score += 0.20
                    reason = "sbas_low_degree_fill"

                tiebreak = self._candidate_sort_key({**candidate, "selection_score": score})
                if (
                    best_candidate is None
                    or score > float(best_score)
                    or (abs(score - float(best_score)) <= 1e-9 and tiebreak < best_tiebreak)
                ):
                    best_candidate = candidate
                    best_reason = reason
                    best_score = score
                    best_tiebreak = tiebreak

            if best_candidate is None or best_score is None:
                break

            select_candidate(best_candidate, reason=best_reason, score=best_score)

            if self._component_count(parents) == 1 and all(degree[scene_id] >= min_degree for scene_id in scene_ids):
                break

        if self._component_count(parents) > 1:
            warnings.append("SBAS 网络未能构成完整连通图，当前筛选条件下候选边不足。")

        zero_degree_count = sum(1 for scene_id in scene_ids if degree[scene_id] == 0)
        low_degree_count = sum(1 for scene_id in scene_ids if degree[scene_id] < min_degree)
        if zero_degree_count > 0:
            warnings.append(f"SBAS 网络仍有 {zero_degree_count} 个场景没有任何连接。")
        elif low_degree_count > 0:
            warnings.append(f"SBAS 网络未达到目标最小连接数 {min_degree}，仍有 {low_degree_count} 个场景连接不足。")

        return selected, warnings

    def _build_scene_entries(self, candidate_pool: List[dict]) -> List[dict]:
        scene_index: Dict[int, dict] = {}
        for candidate in candidate_pool:
            for role in ("master", "slave"):
                scene = candidate[role]
                scene_id = int(scene.id)
                if scene_id in scene_index:
                    continue
                scene_index[scene_id] = {
                    "id": scene_id,
                    "scene": scene,
                    "scene_uid": str(
                        candidate.get(f"{role}_scene_uid")
                        or scene.file_path
                        or f"scene:{scene_id}"
                    ),
                }
        return sorted(scene_index.values(), key=self._scene_order_key)

    def _build_pair_index(self, candidate_pool: List[dict]) -> Dict[Tuple[int, int], dict]:
        pair_index: Dict[Tuple[int, int], dict] = {}
        for candidate in candidate_pool:
            pair_index.setdefault(
                self._pair_lookup_key(candidate["master"].id, candidate["slave"].id),
                candidate,
            )
        return pair_index

    def _pair_lookup_key(self, left_id: int, right_id: int) -> Tuple[int, int]:
        left_value = int(left_id)
        right_value = int(right_id)
        return (left_value, right_value) if left_value <= right_value else (right_value, left_value)

    def _scene_order_key(self, scene_entry: dict) -> Tuple[str, str, int]:
        scene = scene_entry["scene"]
        scene_uid = str(scene_entry.get("scene_uid") or "")
        return (
            self._normalize_scene_time_key(scene),
            scene_uid,
            int(scene_entry["id"]),
        )

    def _normalize_scene_time_key(self, scene: RadarData) -> str:
        imaging_digits = "".join(ch for ch in str(scene.imaging_date or "") if ch.isdigit())[:8]
        imaging_digits = imaging_digits.ljust(8, "0")
        acquisition_digits = "".join(ch for ch in str(scene.acquisition_time_utc or "") if ch.isdigit())
        if acquisition_digits:
            date_part = acquisition_digits[:8].ljust(8, "0")
            time_part = acquisition_digits[8:14].ljust(6, "0")
            return f"{date_part}{time_part}"
        return f"{imaging_digits}000000"

    def _candidate_sort_key(self, candidate: dict) -> Tuple[Any, ...]:
        selection_score = candidate.get("selection_score")
        if selection_score is None:
            selection_score = float(candidate.get("overlap_ratio") or 0)
        return (
            str(candidate["master"].imaging_date or ""),
            str(candidate["slave"].imaging_date or ""),
            -float(selection_score),
            str(candidate.get("pair_uid") or f"{candidate['master'].id}:{candidate['slave'].id}"),
        )

    def _sorted_candidates(self, candidate_pool: List[dict]) -> List[dict]:
        return sorted(candidate_pool, key=self._candidate_sort_key)

    def _find_component(self, parents: Dict[int, int], scene_id: int) -> int:
        current = int(scene_id)
        trail = []
        while parents[current] != current:
            trail.append(current)
            current = parents[current]
        for item in trail:
            parents[item] = current
        return current

    def _union_components(self, parents: Dict[int, int], left_id: int, right_id: int) -> None:
        left_root = self._find_component(parents, left_id)
        right_root = self._find_component(parents, right_id)
        if left_root != right_root:
            parents[right_root] = left_root

    def _component_count(self, parents: Dict[int, int]) -> int:
        return len({self._find_component(parents, scene_id) for scene_id in parents})

    def _parse_optional_aoi_polygon(self, aoi_wkt: Optional[str]):
        if not aoi_wkt:
            return None
        try:
            from shapely import wkt as shapely_wkt

            geometry = shapely_wkt.loads(aoi_wkt)
            if geometry.is_empty or not geometry.is_valid:
                return None
            return geometry
        except Exception:
            return None

    def _get_candidate_intersection_geom(
        self,
        candidate: dict,
        *,
        aoi_poly,
        geometry_cache: Dict[int, Any],
    ):
        cache_key = int(candidate.get("metric_cache_ref_id") or 0)
        if cache_key in geometry_cache:
            return geometry_cache[cache_key]

        try:
            master_poly = Polygon(candidate["master"].coverage_polygon)
            slave_poly = Polygon(candidate["slave"].coverage_polygon)
            if master_poly.is_empty or slave_poly.is_empty:
                geometry_cache[cache_key] = None
                return None
            pair_geom = master_poly.intersection(slave_poly)
            if aoi_poly is not None:
                pair_geom = pair_geom.intersection(aoi_poly)
            geometry_cache[cache_key] = None if pair_geom.is_empty else pair_geom
            return geometry_cache[cache_key]
        except Exception:
            geometry_cache[cache_key] = None
            return None

    def _score_sbas_candidate(
        self,
        candidate: dict,
        params: PairingRequest,
        *,
        selected_coverage,
        geometry_cache: Dict[int, Any],
        aoi_poly,
    ) -> float:
        max_time = max(float(params.time_baseline_max or 1), 1.0)
        time_score = 1.0 - min(float(candidate.get("days") or 0) / max_time, 1.0)
        if params.limit_footprint_center_distance:
            max_space = max(float(params.spatial_baseline_max_meters or 1), 1.0)
            spatial_score = 1.0 - min(float(candidate.get("dist") or 0) / max_space, 1.0)
        else:
            spatial_score = 1.0
        overlap_score = min(max(float(candidate.get("overlap_ratio") or 0), 0.0), 1.0)
        source_score = 1.0 if (
            bool(getattr(candidate.get("master"), "insar_source_ready", False))
            and bool(getattr(candidate.get("slave"), "insar_source_ready", False))
        ) else 0.0
        orbit_score = 1.0 if (
            bool(getattr(candidate.get("master"), "has_orbit_data", False))
            and bool(getattr(candidate.get("slave"), "has_orbit_data", False))
        ) else 0.0

        aoi_gain = 0.0
        redundancy_penalty = 0.0
        candidate_geom = self._get_candidate_intersection_geom(
            candidate,
            aoi_poly=aoi_poly,
            geometry_cache=geometry_cache,
        )
        if candidate_geom is not None and not candidate_geom.is_empty:
            candidate_area = float(candidate_geom.area or 0.0)
            if candidate_area > 0:
                new_area = float(candidate_geom.difference(selected_coverage).area or 0.0)
                overlap_area = float(candidate_geom.intersection(selected_coverage).area or 0.0)
                aoi_gain = max(0.0, min(new_area / candidate_area, 1.0))
                redundancy_penalty = max(0.0, min(overlap_area / candidate_area, 1.0))

        return (
            0.30 * time_score
            + 0.15 * spatial_score
            + 0.30 * overlap_score
            + 0.10 * aoi_gain
            + 0.10 * source_score
            + 0.05 * orbit_score
            - float(params.coverage_diversity_penalty or 0.0) * redundancy_penalty
        )

    def _generate_task_names(self, pairs: List[RadarPair]) -> List[RadarPair]:
        return ensure_unique_task_aliases(pairs)

    def _normalize_timeseries_direction(self, image: RadarDataORM) -> str:
        raw_direction = str(image.orbit_direction or "").strip().upper()
        if raw_direction in {"ASC", "ASCENDING"}:
            return "ASC"
        if raw_direction in {"DSC", "DESC", "DESCENDING"}:
            return "DSC"
        if "ASC" in raw_direction:
            return "ASC"
        if "DSC" in raw_direction or "DESC" in raw_direction:
            return "DSC"
        return raw_direction or "UNKNOWN"

    def _normalize_timeseries_satellite_family(self, image: RadarDataORM) -> str:
        raw_satellite = str(image.satellite or "").strip().upper()
        compact = raw_satellite.replace("-", "").replace("_", "").replace(" ", "")
        if compact in {"LT1", "LT1A", "LT1B", "LUTAN1", "LUTAN1A", "LUTAN1B"}:
            return "LT1"
        if compact in {"S1", "S1A", "S1B", "S1C", "SENTINEL1", "SENTINEL1A", "SENTINEL1B", "SENTINEL1C"}:
            return "S1"
        return raw_satellite or "UNKNOWN"

    def _timeseries_compatibility_key(self, image: RadarDataORM) -> Tuple[str, str, str, str]:
        return (
            self._normalize_timeseries_direction(image),
            self._normalize_timeseries_satellite_family(image),
            str(image.imaging_mode or "UNKNOWN").strip().upper() or "UNKNOWN",
            str(image.polarization or "UNKNOWN").strip().upper() or "UNKNOWN",
        )

    def _format_timeseries_group_label(self, group_key: Tuple[str, str, str, str]) -> str:
        return "_".join(part for part in group_key if part and part != "UNKNOWN") or "STACK"

    async def _calculate_wkt_area(self, db: AsyncSession, geom_wkt: str) -> float:
        geom = func.ST_GeomFromText(geom_wkt, 4326)
        result = await db.execute(select(ST_Area(cast(geom, Geography))))
        return float(result.scalar() or 0.0)

    async def _select_stable_timeseries_stack(
        self,
        db: AsyncSession,
        images: List[RadarDataORM],
        params: PsRequest,
        *,
        aoi_wkt: str,
        aoi_area: float,
    ) -> Tuple[List[RadarDataORM], float, float, bool, str]:
        original_images = sorted(images, key=lambda item: (str(item.imaging_date or ""), int(item.id or 0)))
        remaining = list(original_images)
        best_stack: List[RadarDataORM] = []
        best_consistency_ratio = 0.0
        best_common_aoi_ratio = 0.0
        min_stack_size = 3
        target_ratio = float(params.final_overlap_threshold)
        scene_aoi_areas: Dict[int, float] = {}
        for img in remaining:
            if img.id is None:
                continue
            scene_aoi_areas[int(img.id)] = await self._calculate_overlap_area(db, int(img.id), aoi_wkt)

        def _score_stack(stack: List[RadarDataORM], common_area: float) -> Tuple[float, float]:
            scene_areas = [
                float(scene_aoi_areas.get(int(img.id or 0)) or 0.0)
                for img in stack
                if img.id is not None
            ]
            min_scene_area = min(scene_areas) if scene_areas else 0.0
            consistency_ratio = common_area / min_scene_area if min_scene_area > 0 else 0.0
            common_aoi_ratio = common_area / aoi_area if aoi_area > 0 else 0.0
            return (
                max(0.0, min(consistency_ratio, 1.0)),
                max(0.0, min(common_aoi_ratio, 1.0)),
            )

        while len(remaining) >= min_stack_size:
            common_overlap = await self._find_common_overlap(
                db,
                [int(img.id) for img in remaining if img.id is not None],
                clip_wkt=aoi_wkt,
            )
            common_area = float((common_overlap or {}).get("area") or 0.0)
            consistency_ratio, common_aoi_ratio = _score_stack(remaining, common_area)

            if (
                consistency_ratio > best_consistency_ratio + 1e-9
                or (
                    abs(consistency_ratio - best_consistency_ratio) <= 1e-9
                    and common_aoi_ratio > best_common_aoi_ratio + 1e-9
                )
                or (
                    abs(consistency_ratio - best_consistency_ratio) <= 1e-9
                    and abs(common_aoi_ratio - best_common_aoi_ratio) <= 1e-9
                    and len(remaining) > len(best_stack)
                )
            ):
                best_stack = list(remaining)
                best_consistency_ratio = consistency_ratio
                best_common_aoi_ratio = common_aoi_ratio

            if consistency_ratio >= target_ratio:
                return remaining, consistency_ratio, common_aoi_ratio, True, "common_overlap"

            if len(remaining) == min_stack_size:
                break

            trial_options: List[Tuple[float, float, int, List[RadarDataORM]]] = []
            for remove_index, _ in enumerate(remaining):
                trial = remaining[:remove_index] + remaining[remove_index + 1:]
                trial_overlap = await self._find_common_overlap(
                    db,
                    [int(img.id) for img in trial if img.id is not None],
                    clip_wkt=aoi_wkt,
                )
                trial_area = float((trial_overlap or {}).get("area") or 0.0)
                trial_consistency_ratio, trial_common_aoi_ratio = _score_stack(trial, trial_area)
                removed_id = int(remaining[remove_index].id or 0)
                trial_options.append((trial_consistency_ratio, trial_common_aoi_ratio, -removed_id, trial))

            if not trial_options:
                break

            _, _, _, remaining = max(trial_options, key=lambda item: (item[0], item[1], item[2]))

        if best_consistency_ratio >= target_ratio and len(best_stack) >= min_stack_size:
            return best_stack, best_consistency_ratio, best_common_aoi_ratio, True, "common_overlap"

        network_stack, network_ratio = await self._select_pairwise_sbas_network_stack(
            db,
            original_images,
            scene_aoi_areas,
            target_ratio,
            aoi_wkt=aoi_wkt,
        )
        if len(network_stack) >= min_stack_size:
            return network_stack, network_ratio, best_common_aoi_ratio, True, "pairwise_sbas_network"

        return [], best_consistency_ratio, best_common_aoi_ratio, False, "none"

    async def _select_pairwise_sbas_network_stack(
        self,
        db: AsyncSession,
        images: List[RadarDataORM],
        scene_aoi_areas: Dict[int, float],
        target_ratio: float,
        *,
        aoi_wkt: str,
    ) -> Tuple[List[RadarDataORM], float]:
        if len(images) < 3:
            return [], 0.0

        image_by_id = {int(img.id): img for img in images if img.id is not None}
        adjacency: Dict[int, List[Tuple[int, float]]] = {scene_id: [] for scene_id in image_by_id}

        aoi_geom = func.ST_GeomFromText(aoi_wkt, 4326)
        for left, right in combinations(images, 2):
            if left.id is None or right.id is None:
                continue
            left_id = int(left.id)
            right_id = int(right.id)
            left_area = float(scene_aoi_areas.get(left_id) or 0.0)
            right_area = float(scene_aoi_areas.get(right_id) or 0.0)
            min_scene_area = min(left_area, right_area)
            if min_scene_area <= 0:
                continue

            left_geom = select(RadarDataORM.geom).where(RadarDataORM.id == left_id).scalar_subquery()
            right_geom = select(RadarDataORM.geom).where(RadarDataORM.id == right_id).scalar_subquery()
            pair_geom = ST_Intersection(ST_Intersection(left_geom, right_geom), aoi_geom)
            result = await db.execute(select(ST_Area(cast(pair_geom, Geography))))
            pair_area = float(result.scalar() or 0.0)
            pair_ratio = max(0.0, min(pair_area / min_scene_area, 1.0))
            if pair_ratio >= target_ratio:
                adjacency[left_id].append((right_id, pair_ratio))
                adjacency[right_id].append((left_id, pair_ratio))

        visited: set[int] = set()
        best_component: List[int] = []
        best_component_ratio = 0.0

        for scene_id in sorted(adjacency):
            if scene_id in visited:
                continue
            stack = [scene_id]
            visited.add(scene_id)
            component: List[int] = []
            component_edge_ratios: List[float] = []
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor, ratio in adjacency.get(current, []):
                    component_edge_ratios.append(float(ratio))
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)

            if len(component) < 3:
                continue
            component_ratio = min(component_edge_ratios) if component_edge_ratios else 0.0
            if (
                len(component) > len(best_component)
                or (
                    len(component) == len(best_component)
                    and component_ratio > best_component_ratio
                )
            ):
                best_component = component
                best_component_ratio = component_ratio

        if len(best_component) < 3:
            return [], 0.0

        selected = [image_by_id[scene_id] for scene_id in best_component if scene_id in image_by_id]
        selected.sort(key=lambda item: (str(item.imaging_date or ""), int(item.id or 0)))
        return selected, best_component_ratio

    async def find_ps_timeseries_data(
        self,
        db: AsyncSession,
        params: PsRequest,
        aoi_wkt: str
    ) -> Dict[str, List[RadarData]]:
        """
        利用 PostGIS 查找 PS-InSAR 时序影像栈。
        
        Args:
            db: 数据库会话
            params: PS-InSAR 请求参数
            aoi_wkt: 感兴趣区域 WKT 字符串
            
        Returns:
            按轨道方向分组的影像字典
        """
        # 1. 初始筛选：找到与 AOI 相交且单景覆盖率达标的影像
        aoi_geom = func.ST_GeomFromText(aoi_wkt, 4326)
        aoi_geog = cast(aoi_geom, Geography)
        aoi_area = await self._calculate_wkt_area(db, aoi_wkt)
        if aoi_area <= 0:
            logger.warning("timeseries stack planning skipped: AOI area is empty")
            return {}

        intersection_geog = cast(ST_Intersection(RadarDataORM.geom, aoi_geom), Geography)
        stmt = select(RadarDataORM).where(
            and_(
                ST_Intersects(RadarDataORM.geom, aoi_geom),
                ST_Area(intersection_geog) / ST_Area(aoi_geog) >= params.initial_overlap_threshold
            )
        )
        
        result = await db.execute(stmt)
        candidates = result.scalars().all()
        logger.info(
            "timeseries stack planning: candidates_after_aoi_gate=%s initial_threshold=%.3f final_consistency_threshold=%.3f",
            len(candidates),
            float(params.initial_overlap_threshold),
            float(params.final_overlap_threshold),
        )
        
        if not candidates:
            return {}
        
        # 2. 按轨道方向、卫星、成像模式、极化分组，避免混入不兼容场景。
        images_by_group: Dict[Tuple[str, str, str, str], List[RadarDataORM]] = {}
        for img in candidates:
            images_by_group.setdefault(self._timeseries_compatibility_key(img), []).append(img)
        logger.info(
            "timeseries stack planning: compatible_groups=%s group_sizes=%s",
            len(images_by_group),
            {
                self._format_timeseries_group_label(group_key): len(items)
                for group_key, items in images_by_group.items()
            },
        )
        
        # 3. 每个兼容组内寻找满足公共 AOI 覆盖阈值的最大稳定候选栈。
        final_results: Dict[str, List[RadarData]] = {}
        plans_created = False
        
        for group_key, images in images_by_group.items():
            if len(images) < 3:
                logger.info(
                    "timeseries stack planning: group=%s skipped because scene_count=%s < 3",
                    self._format_timeseries_group_label(group_key),
                    len(images),
                )
                continue
            
            try:
                (
                    final_stack,
                    consistency_ratio,
                    common_aoi_ratio,
                    threshold_satisfied,
                    selection_mode,
                ) = await self._select_stable_timeseries_stack(
                    db,
                    images,
                    params,
                    aoi_wkt=aoi_wkt,
                    aoi_area=aoi_area,
                )
                logger.info(
                    "timeseries stack planning: group=%s input_scenes=%s selected_scenes=%s consistency=%.4f common_aoi=%.4f threshold_satisfied=%s mode=%s",
                    self._format_timeseries_group_label(group_key),
                    len(images),
                    len(final_stack),
                    consistency_ratio,
                    common_aoi_ratio,
                    threshold_satisfied,
                    selection_mode,
                )
                if len(final_stack) >= 3:
                    final_stack.sort(key=lambda x: str(x.imaging_date or ""))
                    direction = group_key[0]
                    network_edges, network_warnings = await self._build_timeseries_network_edges(
                        db,
                        final_stack,
                        params,
                        aoi_wkt=aoi_wkt,
                        selection_mode=selection_mode,
                    )
                    logger.info(
                        "timeseries stack planning: group=%s network_edges=%s network_warnings=%s",
                        self._format_timeseries_group_label(group_key),
                        len(network_edges),
                        len(network_warnings),
                    )
                    persisted_plan = await self._persist_timeseries_stack_plan(
                        db,
                        direction=direction,
                        params=params,
                        aoi_wkt=aoi_wkt,
                        scenes=final_stack,
                        common_aoi_coverage_ratio=common_aoi_ratio,
                        coverage_consistency_ratio=consistency_ratio,
                        threshold_satisfied=threshold_satisfied,
                        selection_mode=selection_mode,
                        network_edges=network_edges,
                        network_warnings=network_warnings,
                    )
                    result_key = persisted_plan.get("group_key") or self._format_timeseries_group_label(group_key)
                    if result_key in final_results:
                        result_key = f"{result_key}_{len(final_results) + 1}"
                    final_results[result_key] = persisted_plan["scenes"]
                    plans_created = True
                    
            except Exception as e:
                print(f"处理时序候选组 {self._format_timeseries_group_label(group_key)} 时出错: {e}")
                continue
        
        if plans_created:
            await db.commit()

        return final_results
    
    async def find_hazard_points_in_area(
        self,
        db: AsyncSession,
        geom_wkt: str
    ) -> List[HazardPoint]:
        """
        查找指定区域内的灾害点。
        
        Args:
            db: 数据库会话
            geom_wkt: 区域 WKT 字符串
            
        Returns:
            灾害点列表
        """
        area_geom = func.ST_GeomFromText(geom_wkt, 4326)
        stmt = select(HazardPointORM).where(
            ST_Covers(
                area_geom,
                HazardPointORM.geom
            )
        )
        
        result = await db.execute(stmt)
        points = result.scalars().all()
        
        return [HazardPoint.model_validate(p) for p in points]
    
    async def find_dinsar_results_near_hazard(
        self,
        db: AsyncSession,
        hazard_point_id: int,
        buffer_degrees: float = 0.1
    ) -> List[ResultProductORM]:
        """
        查找指定灾害点附近的 D-InSAR 结果。
        
        Args:
            db: 数据库会话
            hazard_point_id: 灾害点 ID
            buffer_degrees: 搜索半径（度）
            
        Returns:
            D-InSAR 结果列表
        """
        # 获取灾害点位置
        stmt = select(HazardPointORM).where(HazardPointORM.id == hazard_point_id)
        result = await db.execute(stmt)
        hazard = result.scalar_one_or_none()
        
        if not hazard:
            return []
        
        # 使用 PostGIS 空间查询
        stmt = select(ResultProductORM).where(
            ResultProductORM.catalog_name == "dinsar",
            ST_Covers(
                ResultProductORM.geom,
                hazard.geom
            )
        )

        result = await db.execute(stmt)
        return result.scalars().all()
    
    async def _calculate_spatial_distance(
        self,
        db: AsyncSession,
        master: RadarDataORM,
        slave: RadarDataORM
    ) -> float:
        """
        Calculate footprint center distance in meters using PostGIS sphere distance.
        """
        master_alias = RadarDataORM.__table__.alias("master")
        slave_alias = RadarDataORM.__table__.alias("slave")
        stmt = select(
            func.ST_DistanceSphere(
                ST_Centroid(master_alias.c.geom),
                ST_Centroid(slave_alias.c.geom)
            )
        ).select_from(
            master_alias.join(slave_alias, master_alias.c.id != slave_alias.c.id)
        ).where(
            master_alias.c.id == master.id,
            slave_alias.c.id == slave.id,
        )
        result = await db.execute(stmt)
        return result.scalar() or 0

    async def _calculate_overlap_ratio(
        self,
        db: AsyncSession,
        master: RadarDataORM,
        slave: RadarDataORM
    ) -> float:
        """
        Calculate overlap ratio using geography areas for accuracy.
        """
        master_alias = RadarDataORM.__table__.alias("master")
        slave_alias = RadarDataORM.__table__.alias("slave")
        stmt = select(
            ST_Area(cast(ST_Intersection(master_alias.c.geom, slave_alias.c.geom), Geography)) /
            func.greatest(
                ST_Area(cast(master_alias.c.geom, Geography)),
                ST_Area(cast(slave_alias.c.geom, Geography))
            )
        ).select_from(
            master_alias.join(slave_alias, master_alias.c.id != slave_alias.c.id)
        ).where(
            master_alias.c.id == master.id,
            slave_alias.c.id == slave.id,
        )
        result = await db.execute(stmt)
        return result.scalar() or 0

    async def _calculate_overlap_area(
        self,
        db: AsyncSession,
        image_id: int,
        area_wkt: str
    ) -> float:
        """
        Calculate overlap area against a WKT AOI using geography area.
        """
        area_geom = func.ST_GeomFromText(area_wkt, 4326)
        inter_geog = cast(ST_Intersection(RadarDataORM.geom, area_geom), Geography)
        stmt = select(
            ST_Area(inter_geog)
        ).where(RadarDataORM.id == image_id)
        result = await db.execute(stmt)
        return result.scalar() or 0

    async def _find_common_overlap(
        self,
        db: AsyncSession,
        image_ids: List[int],
        clip_wkt: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Compute common overlap geometry and area using DB aggregation.
        """
        if not image_ids:
            return None

        geom_expr = RadarDataORM.geom
        if clip_wkt:
            clip_geom = func.ST_GeomFromText(clip_wkt, 4326)
            geom_expr = ST_Intersection(RadarDataORM.geom, clip_geom)

        intersection_expr = func.st_intersection_agg(geom_expr)
        stmt = select(
            ST_Area(cast(intersection_expr, Geography)).label("common_area"),
            intersection_expr.label("common_geom")
        ).where(RadarDataORM.id.in_(image_ids))
        result = await db.execute(stmt)
        row = result.first()
        if not row or not row.common_geom:
            return None

        return {"geom": row.common_geom, "area": float(row.common_area or 0)}

    def _optimize_coverage_diversity(
        self,
        candidate_pool: List[dict],
        penalty_factor: float,
        aoi_wkt: Optional[str] = None,
    ) -> List[dict]:
        """
        优化任务选择，实现空间覆盖多样性。

        当提供 AOI 时，只在 AOI 范围内计算覆盖面积，
        避免影像全幅覆盖范围干扰优化结果。

        Args:
            candidate_pool: 候选任务池
            penalty_factor: 重复覆盖惩罚因子
            aoi_wkt: 可选的 AOI WKT 几何，用于裁剪计算区域

        Returns:
            优化后的任务列表
        """
        if not candidate_pool:
            return []

        from shapely import wkt as shapely_wkt

        # 解析 AOI 几何
        aoi_poly = None
        if aoi_wkt:
            try:
                aoi_poly = shapely_wkt.loads(aoi_wkt)
                if aoi_poly.is_empty or not aoi_poly.is_valid:
                    aoi_poly = None
            except Exception:
                aoi_poly = None

        # 按重叠面积降序排序
        candidate_pool.sort(key=lambda x: x.get('overlap_ratio', 0), reverse=True)

        selected_tasks = []
        total_geom = Polygon()

        for cand in candidate_pool:
            m_poly = Polygon(cand['master'].coverage_polygon)
            s_poly = Polygon(cand['slave'].coverage_polygon)
            inter_poly = m_poly.intersection(s_poly)

            # 如果有 AOI，裁剪到 AOI 范围内再计算
            if aoi_poly is not None:
                inter_poly = inter_poly.intersection(aoi_poly)

            if inter_poly.is_empty:
                continue

            new_area = inter_poly.difference(total_geom).area
            overlap_area = inter_poly.intersection(total_geom).area
            score = new_area - (overlap_area * penalty_factor)

            if score > 1e-8:
                selected_tasks.append(cand)
                total_geom = unary_union([total_geom, inter_poly])

        return selected_tasks
    
    def _haversine_distance(self, coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
        """计算两点之间的大圆距离（米）"""
        R = 6371000  # 地球半径（米）
        lon1, lat1 = coord1
        lon2, lat2 = coord2
        phi1, phi2 = map(math.radians, [lat1, lat2])
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_phi / 2.0) ** 2 + 
             math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# 全局服务实例
spatial_service = SpatialService()
