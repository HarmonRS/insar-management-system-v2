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
from sqlalchemy import and_, cast, func
from sqlalchemy.orm import aliased

from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
from geoalchemy2.functions import ST_Intersects, ST_Intersection, ST_Area, ST_Centroid, ST_Covers
from shapely.geometry import Polygon
from shapely.ops import unary_union

from ..models import (
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
    TimeseriesStackPlanItemORM,
    TimeseriesStackPlanORM,
)
from .dinsar_naming import build_pair_key, build_task_alias, ensure_unique_task_aliases
from .pairing_state_service import pairing_state_service


PAIRING_POLICY_VERSION = "2026.04.phase3.v1"
PAIRING_WARNING_CANDIDATE_THRESHOLD = 3000
logger = logging.getLogger(__name__)


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

        if len(candidate_pool) > PAIRING_WARNING_CANDIDATE_THRESHOLD:
            warnings.append(
                f"候选配对数超过 {PAIRING_WARNING_CANDIDATE_THRESHOLD}（当前: {len(candidate_pool)}），建议收紧参数或缩小 AOI。"
            )

        selected_candidates, strategy_warnings = self._apply_strategy(
            candidate_pool,
            effective_params,
            aoi_wkt=aoi_wkt,
        )
        warnings.extend(strategy_warnings)

        for candidate in selected_candidates:
            candidate.setdefault("selection_strategy", effective_params.strategy)

        network_run_id = await self._persist_network_run(
            db,
            params=effective_params,
            aoi_wkt=aoi_wkt,
            require_orbit_data=require_orbit_data,
            warnings=warnings,
            candidate_pool=candidate_pool,
            selected_candidates=selected_candidates,
        )

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

        stmt = (
            select(PairingMetricCacheORM, master_alias, slave_alias)
            .join(master_alias, master_alias.id == PairingMetricCacheORM.master_scene_ref_id)
            .join(slave_alias, slave_alias.id == PairingMetricCacheORM.slave_scene_ref_id)
            .where(
                PairingMetricCacheORM.metric_version == pairing_state_service.metric_version,
                PairingMetricCacheORM.status == "READY",
                PairingMetricCacheORM.time_baseline_days >= params.time_baseline_min,
                PairingMetricCacheORM.time_baseline_days <= params.time_baseline_max,
                PairingMetricCacheORM.spatial_baseline_meters <= params.spatial_baseline_max_meters,
                PairingMetricCacheORM.scene_overlap_ratio >= params.overlap_threshold,
            )
        )

        if require_orbit_data:
            stmt = stmt.where(
                master_alias.has_orbit_data.is_(True),
                slave_alias.has_orbit_data.is_(True),
            )

        if not params.cross_satellite_pairing:
            stmt = stmt.where(PairingMetricCacheORM.same_satellite.is_(True))

        if params.require_same_imaging_mode:
            stmt = stmt.where(PairingMetricCacheORM.same_imaging_mode.is_(True))

        if params.require_same_polarization:
            stmt = stmt.where(PairingMetricCacheORM.same_polarization.is_(True))

        if params.allowed_satellites:
            stmt = stmt.where(
                master_alias.satellite.in_(params.allowed_satellites),
                slave_alias.satellite.in_(params.allowed_satellites),
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
            stmt = stmt.where(
                ST_Intersects(master_alias.geom, aoi_geom),
                ST_Intersects(slave_alias.geom, aoi_geom),
            )
            if params.aoi_overlap_threshold is not None:
                aoi_geog = cast(aoi_geom, Geography)
                aoi_area = func.nullif(ST_Area(aoi_geog), 0)
                master_inter_geog = cast(ST_Intersection(master_alias.geom, aoi_geom), Geography)
                slave_inter_geog = cast(ST_Intersection(slave_alias.geom, aoi_geom), Geography)
                stmt = stmt.where(
                    ST_Area(master_inter_geog) / aoi_area >= params.aoi_overlap_threshold,
                    ST_Area(slave_inter_geog) / aoi_area >= params.aoi_overlap_threshold,
                )

        stmt = stmt.order_by(
            PairingMetricCacheORM.master_imaging_date.asc(),
            PairingMetricCacheORM.slave_imaging_date.asc(),
            func.coalesce(PairingMetricCacheORM.scene_overlap_ratio, 0).desc(),
            PairingMetricCacheORM.pair_uid.asc(),
        )

        result = await db.execute(stmt)
        candidate_pool: List[dict] = []
        for metric_row, master_row, slave_row in result.all():
            candidate_pool.append(
                {
                    "metric_cache_ref_id": int(metric_row.id),
                    "pair_uid": metric_row.pair_uid,
                    "master_scene_uid": metric_row.master_scene_uid,
                    "slave_scene_uid": metric_row.slave_scene_uid,
                    "master": RadarData.model_validate(master_row),
                    "slave": RadarData.model_validate(slave_row),
                    "days": int(metric_row.time_baseline_days or 0),
                    "dist": float(metric_row.spatial_baseline_meters or 0),
                    "overlap_ratio": float(metric_row.scene_overlap_ratio or 0),
                }
            )
        return candidate_pool

    def _build_radar_pairs(self, selected_candidates: List[dict]) -> List[RadarPair]:
        result_pairs: List[RadarPair] = []
        for candidate in selected_candidates:
            master = candidate["master"]
            slave = candidate["slave"]
            task_alias = build_task_alias(master.imaging_date, slave.imaging_date)
            selection_score = candidate.get("selection_score")
            result_pairs.append(
                RadarPair(
                    master=master,
                    slave=slave,
                    task_name=task_alias,
                    task_alias=task_alias,
                    pair_key=build_pair_key(
                        master.file_path,
                        slave.file_path,
                        master.imaging_date,
                        slave.imaging_date,
                    ),
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
                )
            )
        return result_pairs

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
            "time_baseline_days": int(candidate.get("days") or 0),
            "spatial_baseline_meters": float(candidate.get("dist") or 0.0),
            "scene_overlap_ratio": float(candidate.get("overlap_ratio") or 0.0),
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
                    "orbit_direction": item.orbit_direction,
                    "satellite_family": self._normalize_timeseries_satellite_family(item),
                    "bbox": [item.min_lon, item.min_lat, item.max_lon, item.max_lat],
                    "scene_unique_id": item.unique_id,
                },
            )
            db.add(plan_item)
            await db.flush()
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
                    }
                )
            )

        return {
            "plan_id": plan.plan_id,
            "group_key": identity.get("group_key"),
            "stack_key": identity.get("stack_key"),
            "scenes": scene_payloads,
        }

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
            return self._apply_sequential_strategy(candidate_pool, params.num_connections)
        if params.strategy == "star":
            return self._apply_star_strategy(candidate_pool, params.reference_image_id)
        return self._apply_all_strategy(candidate_pool)

    def _apply_all_strategy(self, candidate_pool: List[dict]) -> Tuple[List[dict], List[str]]:
        return (
            [
                {
                    **candidate,
                    "selection_reason": "all_candidate",
                    "selection_score": float(candidate.get("overlap_ratio") or 0),
                }
                for candidate in self._sorted_candidates(candidate_pool)
            ],
            [],
        )

    def _apply_sequential_strategy(
        self,
        candidate_pool: List[dict],
        num_connections: int,
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
                        "selection_score": float(candidate.get("overlap_ratio") or 0),
                    }
                )
                picked_count += 1

        return selected, []

    def _apply_star_strategy(
        self,
        candidate_pool: List[dict],
        reference_image_id: Optional[int],
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
                        "selection_score": float(candidate.get("overlap_ratio") or 0),
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
        max_space = max(float(params.spatial_baseline_max_meters or 1), 1.0)
        time_score = 1.0 - min(float(candidate.get("days") or 0) / max_time, 1.0)
        spatial_score = 1.0 - min(float(candidate.get("dist") or 0) / max_space, 1.0)
        overlap_score = min(max(float(candidate.get("overlap_ratio") or 0), 0.0), 1.0)

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
            0.35 * time_score
            + 0.20 * spatial_score
            + 0.30 * overlap_score
            + 0.15 * aoi_gain
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
        if compact in {"S1", "S1A", "S1B", "SENTINEL1", "SENTINEL1A", "SENTINEL1B"}:
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
        Calculate spatial baseline in meters using PostGIS sphere distance.
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
