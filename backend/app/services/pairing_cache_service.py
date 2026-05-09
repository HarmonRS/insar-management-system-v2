from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PairingCacheStateORM, PairingDirtySceneORM, PairingMetricCacheORM, RadarDataORM
from .pairing_state_service import (
    PAIRING_CACHE_SCOPE_GLOBAL,
    pairing_state_service,
)


FULL_REBUILD_DIRTY_SCENE_THRESHOLD = 64
FULL_REBUILD_DIRTY_SCENE_RATIO = 0.25


def _utcnow() -> datetime:
    return datetime.utcnow()


def _scene_uid_expr(alias: str) -> str:
    return (
        f"COALESCE(NULLIF({alias}.unique_id, ''), NULLIF({alias}.file_path, ''), "
        f"'scene:' || {alias}.id::text)"
    )


def _satellite_family_expr(alias: str) -> str:
    compact = (
        f"upper(replace(replace(replace(COALESCE({alias}.satellite, ''), '-', ''), '_', ''), ' ', ''))"
    )
    return (
        f"COALESCE(NULLIF({alias}.satellite_family, ''), "
        f"CASE "
        f"WHEN {compact} IN ('LT1', 'LT1A', 'LT1B', 'LUTAN1', 'LUTAN1A', 'LUTAN1B') THEN 'LT1' "
        f"WHEN {compact} IN ('S1', 'S1A', 'S1B', 'SENTINEL1', 'SENTINEL1A', 'SENTINEL1B') THEN 'S1' "
        f"WHEN NULLIF({alias}.satellite, '') IS NOT NULL THEN upper({alias}.satellite) "
        f"ELSE NULL END)"
    )


def _same_satellite_family_expr(left_alias: str, right_alias: str) -> str:
    left_family = _satellite_family_expr(left_alias)
    right_family = _satellite_family_expr(right_alias)
    return (
        f"(NULLIF({left_family}, '') IS NOT NULL "
        f"AND NULLIF({right_family}, '') IS NOT NULL "
        f"AND {left_family} = {right_family})"
    )


def _same_look_direction_expr(left_alias: str, right_alias: str) -> str:
    return (
        f"(NULLIF({left_alias}.look_direction, '') IS NULL "
        f"OR NULLIF({right_alias}.look_direction, '') IS NULL "
        f"OR {left_alias}.look_direction = {right_alias}.look_direction)"
    )


def _orientation_is_left_master_expr(left_alias: str, right_alias: str) -> str:
    left_uid = _scene_uid_expr(left_alias)
    right_uid = _scene_uid_expr(right_alias)
    return (
        f"({left_alias}.imaging_date < {right_alias}.imaging_date "
        f"OR ({left_alias}.imaging_date = {right_alias}.imaging_date AND "
        f"({left_uid} < {right_uid} OR ({left_uid} = {right_uid} AND {left_alias}.id < {right_alias}.id))))"
    )


def _hard_constraints_expr(left_alias: str, right_alias: str) -> str:
    return (
        f"{left_alias}.id <> {right_alias}.id "
        f"AND {left_alias}.geom IS NOT NULL "
        f"AND {right_alias}.geom IS NOT NULL "
        f"AND {left_alias}.imaging_date ~ '^[0-9]{{8}}$' "
        f"AND {right_alias}.imaging_date ~ '^[0-9]{{8}}$' "
        f"AND {left_alias}.orbit_direction IS NOT NULL "
        f"AND {right_alias}.orbit_direction IS NOT NULL "
        f"AND {left_alias}.orbit_direction = {right_alias}.orbit_direction "
        f"AND COALESCE({left_alias}.insar_source_ready, false) "
        f"AND COALESCE({right_alias}.insar_source_ready, false) "
        f"AND { _same_look_direction_expr(left_alias, right_alias) } "
        f"AND ST_Intersects({left_alias}.geom, {right_alias}.geom)"
    )


def _full_rebuild_insert_sql() -> str:
    master_uid = _scene_uid_expr("m")
    slave_uid = _scene_uid_expr("s")
    center_distance = "ST_DistanceSphere(ST_Centroid(m.geom), ST_Centroid(s.geom))::double precision"
    master_family = _satellite_family_expr("m")
    slave_family = _satellite_family_expr("s")
    return f"""
        INSERT INTO pairing_metric_cache (
            master_scene_ref_id,
            slave_scene_ref_id,
            master_scene_uid,
            slave_scene_uid,
            pair_uid,
            metric_version,
            orientation_rule_version,
            time_baseline_days,
            spatial_baseline_meters,
            scene_center_distance_meters,
            scene_overlap_ratio,
            orbit_direction,
            same_satellite,
            same_satellite_family,
            same_look_direction,
            same_imaging_mode,
            same_polarization,
            master_imaging_date,
            slave_imaging_date,
            master_satellite,
            slave_satellite,
            master_satellite_family,
            slave_satellite_family,
            master_imaging_mode,
            slave_imaging_mode,
            master_polarization,
            slave_polarization,
            master_look_direction,
            slave_look_direction,
            master_file_path,
            slave_file_path,
            status,
            computed_at
        )
        SELECT
            m.id AS master_scene_ref_id,
            s.id AS slave_scene_ref_id,
            {master_uid} AS master_scene_uid,
            {slave_uid} AS slave_scene_uid,
            md5({master_uid} || '|' || {slave_uid}) AS pair_uid,
            :metric_version AS metric_version,
            :orientation_rule_version AS orientation_rule_version,
            ABS(to_date(s.imaging_date, 'YYYYMMDD') - to_date(m.imaging_date, 'YYYYMMDD')) AS time_baseline_days,
            {center_distance} AS spatial_baseline_meters,
            {center_distance} AS scene_center_distance_meters,
            (
                ST_Area(ST_Intersection(m.geom, s.geom)::geography) /
                NULLIF(GREATEST(ST_Area(m.geom::geography), ST_Area(s.geom::geography)), 0)
            )::double precision AS scene_overlap_ratio,
            m.orbit_direction,
            (m.satellite IS NOT NULL AND s.satellite IS NOT NULL AND m.satellite = s.satellite) AS same_satellite,
            { _same_satellite_family_expr('m', 's') } AS same_satellite_family,
            { _same_look_direction_expr('m', 's') } AS same_look_direction,
            (
                NULLIF(m.imaging_mode, '') IS NOT NULL
                AND NULLIF(s.imaging_mode, '') IS NOT NULL
                AND m.imaging_mode = s.imaging_mode
            ) AS same_imaging_mode,
            (
                NULLIF(m.polarization, '') IS NOT NULL
                AND NULLIF(s.polarization, '') IS NOT NULL
                AND m.polarization = s.polarization
            ) AS same_polarization,
            m.imaging_date AS master_imaging_date,
            s.imaging_date AS slave_imaging_date,
            m.satellite AS master_satellite,
            s.satellite AS slave_satellite,
            {master_family} AS master_satellite_family,
            {slave_family} AS slave_satellite_family,
            m.imaging_mode AS master_imaging_mode,
            s.imaging_mode AS slave_imaging_mode,
            m.polarization AS master_polarization,
            s.polarization AS slave_polarization,
            m.look_direction AS master_look_direction,
            s.look_direction AS slave_look_direction,
            m.file_path AS master_file_path,
            s.file_path AS slave_file_path,
            'READY' AS status,
            NOW() AS computed_at
        FROM radar_data m
        JOIN radar_data s
          ON { _hard_constraints_expr('m', 's') }
         AND { _orientation_is_left_master_expr('m', 's') }
    """


def _incremental_insert_sql() -> str:
    dirty_uid = _scene_uid_expr("d")
    other_uid = _scene_uid_expr("o")
    dirty_is_master = _orientation_is_left_master_expr("d", "o")
    center_distance = "ST_DistanceSphere(ST_Centroid(d.geom), ST_Centroid(o.geom))::double precision"
    dirty_family = _satellite_family_expr("d")
    other_family = _satellite_family_expr("o")
    return f"""
        INSERT INTO pairing_metric_cache (
            master_scene_ref_id,
            slave_scene_ref_id,
            master_scene_uid,
            slave_scene_uid,
            pair_uid,
            metric_version,
            orientation_rule_version,
            time_baseline_days,
            spatial_baseline_meters,
            scene_center_distance_meters,
            scene_overlap_ratio,
            orbit_direction,
            same_satellite,
            same_satellite_family,
            same_look_direction,
            same_imaging_mode,
            same_polarization,
            master_imaging_date,
            slave_imaging_date,
            master_satellite,
            slave_satellite,
            master_satellite_family,
            slave_satellite_family,
            master_imaging_mode,
            slave_imaging_mode,
            master_polarization,
            slave_polarization,
            master_look_direction,
            slave_look_direction,
            master_file_path,
            slave_file_path,
            status,
            computed_at
        )
        SELECT
            CASE WHEN {dirty_is_master} THEN d.id ELSE o.id END AS master_scene_ref_id,
            CASE WHEN {dirty_is_master} THEN o.id ELSE d.id END AS slave_scene_ref_id,
            CASE WHEN {dirty_is_master} THEN {dirty_uid} ELSE {other_uid} END AS master_scene_uid,
            CASE WHEN {dirty_is_master} THEN {other_uid} ELSE {dirty_uid} END AS slave_scene_uid,
            md5(
                CASE WHEN {dirty_is_master}
                    THEN {dirty_uid} || '|' || {other_uid}
                    ELSE {other_uid} || '|' || {dirty_uid}
                END
            ) AS pair_uid,
            :metric_version AS metric_version,
            :orientation_rule_version AS orientation_rule_version,
            ABS(to_date(o.imaging_date, 'YYYYMMDD') - to_date(d.imaging_date, 'YYYYMMDD')) AS time_baseline_days,
            {center_distance} AS spatial_baseline_meters,
            {center_distance} AS scene_center_distance_meters,
            (
                ST_Area(ST_Intersection(d.geom, o.geom)::geography) /
                NULLIF(GREATEST(ST_Area(d.geom::geography), ST_Area(o.geom::geography)), 0)
            )::double precision AS scene_overlap_ratio,
            d.orbit_direction,
            (d.satellite IS NOT NULL AND o.satellite IS NOT NULL AND d.satellite = o.satellite) AS same_satellite,
            { _same_satellite_family_expr('d', 'o') } AS same_satellite_family,
            { _same_look_direction_expr('d', 'o') } AS same_look_direction,
            (
                NULLIF(d.imaging_mode, '') IS NOT NULL
                AND NULLIF(o.imaging_mode, '') IS NOT NULL
                AND d.imaging_mode = o.imaging_mode
            ) AS same_imaging_mode,
            (
                NULLIF(d.polarization, '') IS NOT NULL
                AND NULLIF(o.polarization, '') IS NOT NULL
                AND d.polarization = o.polarization
            ) AS same_polarization,
            CASE WHEN {dirty_is_master} THEN d.imaging_date ELSE o.imaging_date END AS master_imaging_date,
            CASE WHEN {dirty_is_master} THEN o.imaging_date ELSE d.imaging_date END AS slave_imaging_date,
            CASE WHEN {dirty_is_master} THEN d.satellite ELSE o.satellite END AS master_satellite,
            CASE WHEN {dirty_is_master} THEN o.satellite ELSE d.satellite END AS slave_satellite,
            CASE WHEN {dirty_is_master} THEN {dirty_family} ELSE {other_family} END AS master_satellite_family,
            CASE WHEN {dirty_is_master} THEN {other_family} ELSE {dirty_family} END AS slave_satellite_family,
            CASE WHEN {dirty_is_master} THEN d.imaging_mode ELSE o.imaging_mode END AS master_imaging_mode,
            CASE WHEN {dirty_is_master} THEN o.imaging_mode ELSE d.imaging_mode END AS slave_imaging_mode,
            CASE WHEN {dirty_is_master} THEN d.polarization ELSE o.polarization END AS master_polarization,
            CASE WHEN {dirty_is_master} THEN o.polarization ELSE d.polarization END AS slave_polarization,
            CASE WHEN {dirty_is_master} THEN d.look_direction ELSE o.look_direction END AS master_look_direction,
            CASE WHEN {dirty_is_master} THEN o.look_direction ELSE d.look_direction END AS slave_look_direction,
            CASE WHEN {dirty_is_master} THEN d.file_path ELSE o.file_path END AS master_file_path,
            CASE WHEN {dirty_is_master} THEN o.file_path ELSE d.file_path END AS slave_file_path,
            'READY' AS status,
            NOW() AS computed_at
        FROM radar_data d
        JOIN radar_data o
          ON d.id = :dirty_scene_id
         AND { _hard_constraints_expr('d', 'o') }
        ON CONFLICT (master_scene_ref_id, slave_scene_ref_id, metric_version)
        DO NOTHING
    """


class PairingCacheService:
    async def _get_state_row(self, db: AsyncSession) -> PairingCacheStateORM:
        payload = await pairing_state_service.ensure_pairing_cache_state(db, commit=False)
        result = await db.execute(
            select(PairingCacheStateORM).where(
                PairingCacheStateORM.cache_scope == PAIRING_CACHE_SCOPE_GLOBAL
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            raise RuntimeError(f"Pairing cache state row missing after bootstrap: {payload}")
        return state

    async def _count_pair_rows(self, db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count(PairingMetricCacheORM.id)).where(
                PairingMetricCacheORM.metric_version == pairing_state_service.metric_version
            )
        )
        return int(result.scalar_one() or 0)

    async def _count_scene_rows(self, db: AsyncSession) -> int:
        result = await db.execute(select(func.count(RadarDataORM.id)))
        return int(result.scalar_one() or 0)

    async def _count_pending_dirty_rows(self, db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count(PairingDirtySceneORM.id)).where(
                PairingDirtySceneORM.status == "PENDING"
            )
        )
        return int(result.scalar_one() or 0)

    async def _resolve_dirty_rows(
        self,
        db: AsyncSession,
        *,
        scene_ids: Optional[Sequence[int]] = None,
    ) -> int:
        stmt = (
            update(PairingDirtySceneORM)
            .where(PairingDirtySceneORM.status == "PENDING")
            .values(status="RESOLVED", resolved_at=_utcnow())
        )
        if scene_ids is not None:
            normalized_ids = [int(value) for value in scene_ids if value is not None]
            if not normalized_ids:
                return 0
            stmt = stmt.where(PairingDirtySceneORM.scene_ref_id.in_(normalized_ids))
        result = await db.execute(stmt)
        return int(result.rowcount or 0)

    async def _set_state_rebuilding(self, db: AsyncSession, *, last_error: Optional[str] = None) -> None:
        state = await self._get_state_row(db)
        state.status = "REBUILDING"
        state.last_error = last_error
        await db.flush()

    async def _finalize_state_success(
        self,
        db: AsyncSession,
        *,
        full_rebuild: bool,
    ) -> Dict[str, Any]:
        state = await self._get_state_row(db)
        pair_count = await self._count_pair_rows(db)
        scene_count = await self._count_scene_rows(db)
        dirty_scene_count = await self._count_pending_dirty_rows(db)
        state.metric_version = pairing_state_service.metric_version
        state.scene_count = scene_count
        state.pair_count = pair_count
        state.dirty_scene_count = dirty_scene_count
        state.status = (
            "READY"
            if dirty_scene_count == 0 and not (scene_count > 1 and pair_count == 0)
            else "DIRTY"
        )
        state.last_error = None
        if full_rebuild:
            state.last_full_rebuild_at = _utcnow()
        else:
            state.last_incremental_reconcile_at = _utcnow()
        await db.flush()
        return {
            "status": state.status,
            "scene_count": scene_count,
            "pair_count": pair_count,
            "dirty_scene_count": dirty_scene_count,
            "metric_version": state.metric_version,
            "cache_ready": state.status == "READY",
            "needs_rebuild": dirty_scene_count > 0 or (scene_count > 1 and pair_count == 0),
        }

    async def _finalize_state_failure(self, db: AsyncSession, *, error: Exception) -> None:
        state = await self._get_state_row(db)
        state.status = "FAILED"
        state.last_error = str(error)
        await db.flush()

    def _should_full_rebuild(
        self,
        *,
        dirty_scene_count: int,
        scene_count: int,
        pair_count: int,
        force_full: bool,
    ) -> bool:
        if force_full:
            return True
        if pair_count == 0 and scene_count > 1:
            return True
        if dirty_scene_count >= FULL_REBUILD_DIRTY_SCENE_THRESHOLD:
            return True
        if scene_count > 0 and dirty_scene_count / max(scene_count, 1) >= FULL_REBUILD_DIRTY_SCENE_RATIO:
            return True
        return False

    async def rebuild_metric_cache(
        self,
        db: AsyncSession,
        *,
        commit: bool = True,
    ) -> Dict[str, Any]:
        await pairing_state_service.ensure_pairing_cache_state(db, commit=False)
        await self._set_state_rebuilding(db)
        try:
            delete_result = await db.execute(delete(PairingMetricCacheORM))
            await db.execute(
                text(_full_rebuild_insert_sql()),
                {
                    "metric_version": pairing_state_service.metric_version,
                    "orientation_rule_version": pairing_state_service.orientation_rule_version,
                },
            )
            resolved_dirty = await self._resolve_dirty_rows(db)
            summary = await self._finalize_state_success(db, full_rebuild=True)
            if commit:
                await db.commit()
            else:
                await db.flush()
            return {
                "ok": True,
                "mode": "full_rebuild",
                "deleted_pair_rows": int(delete_result.rowcount or 0),
                "resolved_dirty_rows": resolved_dirty,
                **summary,
            }
        except Exception as exc:
            await db.rollback()
            await self._finalize_state_failure(db, error=exc)
            if commit:
                await db.commit()
            raise

    async def reconcile_dirty_scenes(
        self,
        db: AsyncSession,
        *,
        force_full: bool = False,
        commit: bool = True,
    ) -> Dict[str, Any]:
        await pairing_state_service.ensure_pairing_cache_state(db, commit=False)
        dirty_result = await db.execute(
            select(PairingDirtySceneORM.scene_ref_id)
            .where(PairingDirtySceneORM.status == "PENDING")
            .order_by(PairingDirtySceneORM.marked_at.asc(), PairingDirtySceneORM.id.asc())
        )
        dirty_scene_ids = [int(value) for value in dirty_result.scalars().all() if value is not None]
        dirty_scene_count = len(dirty_scene_ids)
        pair_count = await self._count_pair_rows(db)
        scene_count = await self._count_scene_rows(db)

        if dirty_scene_count == 0 and self._should_full_rebuild(
            dirty_scene_count=dirty_scene_count,
            scene_count=scene_count,
            pair_count=pair_count,
            force_full=force_full,
        ):
            result = await self.rebuild_metric_cache(db, commit=commit)
            result["trigger_dirty_scene_count"] = dirty_scene_count
            result["forced"] = force_full
            return result

        if dirty_scene_count == 0:
            summary = await self._finalize_state_success(db, full_rebuild=False)
            if commit:
                await db.commit()
            return {
                "ok": True,
                "mode": "noop",
                "dirty_scene_count": 0,
                "deleted_pair_rows": 0,
                "resolved_dirty_rows": 0,
                "insert_attempts": 0,
                **summary,
            }

        if self._should_full_rebuild(
            dirty_scene_count=dirty_scene_count,
            scene_count=scene_count,
            pair_count=pair_count,
            force_full=force_full,
        ):
            result = await self.rebuild_metric_cache(db, commit=commit)
            result["trigger_dirty_scene_count"] = dirty_scene_count
            result["forced"] = force_full
            return result

        await self._set_state_rebuilding(db)
        try:
            delete_result = await db.execute(
                delete(PairingMetricCacheORM).where(
                    or_(
                        PairingMetricCacheORM.master_scene_ref_id.in_(dirty_scene_ids),
                        PairingMetricCacheORM.slave_scene_ref_id.in_(dirty_scene_ids),
                    )
                )
            )

            insert_attempts = 0
            insert_sql = text(_incremental_insert_sql())
            for dirty_scene_id in dirty_scene_ids:
                insert_result = await db.execute(
                    insert_sql,
                    {
                        "dirty_scene_id": int(dirty_scene_id),
                        "metric_version": pairing_state_service.metric_version,
                        "orientation_rule_version": pairing_state_service.orientation_rule_version,
                    },
                )
                insert_attempts += int(insert_result.rowcount or 0)

            resolved_dirty = await self._resolve_dirty_rows(db, scene_ids=dirty_scene_ids)
            summary = await self._finalize_state_success(db, full_rebuild=False)
            if commit:
                await db.commit()
            else:
                await db.flush()
            return {
                "ok": True,
                "mode": "incremental_reconcile",
                "dirty_scene_count": dirty_scene_count,
                "deleted_pair_rows": int(delete_result.rowcount or 0),
                "resolved_dirty_rows": resolved_dirty,
                "insert_attempts": insert_attempts,
                **summary,
            }
        except Exception as exc:
            await db.rollback()
            await self._finalize_state_failure(db, error=exc)
            if commit:
                await db.commit()
            raise

    async def get_admin_summary(self, db: AsyncSession) -> Dict[str, Any]:
        payload = await pairing_state_service.get_pairing_system_status(db)
        pending_result = await db.execute(
            select(
                PairingDirtySceneORM.scene_ref_id,
                PairingDirtySceneORM.scene_uid,
                PairingDirtySceneORM.reason,
                PairingDirtySceneORM.marked_at,
            )
            .where(PairingDirtySceneORM.status == "PENDING")
            .order_by(PairingDirtySceneORM.marked_at.asc(), PairingDirtySceneORM.id.asc())
            .limit(20)
        )
        pending_rows = pending_result.all()
        payload["pending_dirty_scenes"] = [
            {
                "scene_ref_id": int(scene_ref_id),
                "scene_uid": scene_uid,
                "reason": reason,
                "marked_at": marked_at,
            }
            for scene_ref_id, scene_uid, reason, marked_at in pending_rows
        ]
        return payload


pairing_cache_service = PairingCacheService()
