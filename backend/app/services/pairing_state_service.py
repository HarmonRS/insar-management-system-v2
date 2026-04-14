from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..models import (
    PairingCacheStateORM,
    PairingDirtySceneORM,
    PairingMetricCacheORM,
    PairingNetworkEdgeORM,
    PairingNetworkRunORM,
    RadarDataORM,
)


PAIRING_CACHE_SCOPE_GLOBAL = "global"
DEFAULT_PAIRING_METRIC_VERSION = "2026.04.v1"
PAIRING_ORIENTATION_RULE_VERSION = "date_then_scene_uid_v1"


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_scene_uid(
    *,
    scene_ref_id: Optional[int],
    unique_id: Optional[str],
    file_path: Optional[str],
) -> str:
    unique_text = str(unique_id or "").strip()
    if unique_text:
        return unique_text
    file_text = str(file_path or "").strip()
    if file_text:
        return file_text
    if scene_ref_id is not None:
        return f"scene:{int(scene_ref_id)}"
    return "scene:unknown"


class PairingStateService:
    metric_version = DEFAULT_PAIRING_METRIC_VERSION
    orientation_rule_version = PAIRING_ORIENTATION_RULE_VERSION

    async def _count_radar_scenes(self, db: AsyncSession) -> int:
        result = await db.execute(select(func.count(RadarDataORM.id)))
        return int(result.scalar_one() or 0)

    async def _count_pending_dirty_scenes(self, db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count(PairingDirtySceneORM.id)).where(
                PairingDirtySceneORM.status == "PENDING"
            )
        )
        return int(result.scalar_one() or 0)

    async def _count_metric_cache_rows(self, db: AsyncSession) -> int:
        result = await db.execute(select(func.count(PairingMetricCacheORM.id)))
        return int(result.scalar_one() or 0)

    async def _get_global_state(self, db: AsyncSession) -> Optional[PairingCacheStateORM]:
        result = await db.execute(
            select(PairingCacheStateORM).where(
                PairingCacheStateORM.cache_scope == PAIRING_CACHE_SCOPE_GLOBAL
            )
        )
        return result.scalar_one_or_none()

    def _build_state_payload(
        self,
        *,
        state: Optional[PairingCacheStateORM],
        scene_count: int,
        dirty_scene_count: int,
        metric_cache_count: int,
    ) -> Dict[str, Any]:
        status = state.status if state is not None else "UNINITIALIZED"
        last_error = state.last_error if state is not None else None
        needs_rebuild = bool(
            dirty_scene_count > 0
            or status in {"DIRTY", "DEGRADED", "FAILED", "UNINITIALIZED"}
            or (scene_count > 1 and metric_cache_count == 0)
        )
        cache_ready = not needs_rebuild and status == "READY"
        state_dict = getattr(state, "__dict__", {}) if state is not None else {}
        return {
            "state_present": state is not None,
            "cache_scope": state.cache_scope if state is not None else PAIRING_CACHE_SCOPE_GLOBAL,
            "metric_version": (state.metric_version if state is not None else None) or self.metric_version,
            "status": status,
            "scene_count": int(scene_count),
            "pair_count": int(metric_cache_count),
            "dirty_scene_count": int(dirty_scene_count),
            "needs_rebuild": needs_rebuild,
            "cache_ready": cache_ready,
            "last_full_rebuild_at": state_dict.get("last_full_rebuild_at"),
            "last_incremental_reconcile_at": state_dict.get("last_incremental_reconcile_at"),
            "last_error": last_error,
            "updated_at": state_dict.get("updated_at"),
        }

    async def ensure_pairing_cache_state(
        self,
        db: AsyncSession,
        *,
        commit: bool = False,
    ) -> Dict[str, Any]:
        scene_count = await self._count_radar_scenes(db)
        dirty_scene_count = await self._count_pending_dirty_scenes(db)
        metric_cache_count = await self._count_metric_cache_rows(db)
        state = await self._get_global_state(db)
        created = False

        if state is None:
            state = PairingCacheStateORM(
                cache_scope=PAIRING_CACHE_SCOPE_GLOBAL,
                metric_version=self.metric_version,
                status="DIRTY" if scene_count > 1 else "READY",
                scene_count=scene_count,
                pair_count=metric_cache_count,
                dirty_scene_count=dirty_scene_count,
            )
            db.add(state)
            await db.flush()
            created = True

        state.metric_version = state.metric_version or self.metric_version
        state.scene_count = scene_count
        state.pair_count = metric_cache_count
        state.dirty_scene_count = dirty_scene_count

        if state.status == "READY" and (
            dirty_scene_count > 0 or (scene_count > 1 and metric_cache_count == 0)
        ):
            state.status = "DIRTY"
        elif state.status in {None, ""}:
            state.status = "DIRTY" if scene_count > 1 else "READY"

        if commit:
            await db.commit()
            await db.refresh(state)
        else:
            await db.flush()

        payload = self._build_state_payload(
            state=state,
            scene_count=scene_count,
            dirty_scene_count=dirty_scene_count,
            metric_cache_count=metric_cache_count,
        )
        payload["created"] = created
        return payload

    async def bootstrap_pairing_cache_state(self) -> Dict[str, Any]:
        async with _new_session() as db:
            return await self.ensure_pairing_cache_state(db, commit=True)

    async def mark_global_dirty(
        self,
        db: AsyncSession,
        *,
        reason: str = "manual",
        commit: bool = False,
    ) -> Dict[str, Any]:
        payload = await self.ensure_pairing_cache_state(db, commit=False)
        state = await self._get_global_state(db)
        if state is None:
            raise RuntimeError("Pairing cache state row is missing after bootstrap.")

        state.status = "DIRTY"
        if state.last_error and reason != "error_recovery":
            state.last_error = None

        if commit:
            await db.commit()
        else:
            await db.flush()

        payload = await self.ensure_pairing_cache_state(db, commit=False)
        payload["dirty_mark_mode"] = "global"
        payload["reason"] = reason
        return payload

    async def mark_scenes_dirty(
        self,
        db: AsyncSession,
        *,
        scene_ids: Sequence[int],
        reason: str = "scan",
        commit: bool = False,
    ) -> Dict[str, Any]:
        normalized_ids = sorted(
            {
                int(scene_id)
                for scene_id in (scene_ids or [])
                if scene_id is not None and int(scene_id) > 0
            }
        )
        if not normalized_ids:
            return await self.mark_global_dirty(db, reason=reason, commit=commit)

        result = await db.execute(
            select(RadarDataORM).where(RadarDataORM.id.in_(normalized_ids))
        )
        radar_rows = result.scalars().all()
        if not radar_rows:
            return await self.mark_global_dirty(db, reason=reason, commit=commit)

        existing_result = await db.execute(
            select(PairingDirtySceneORM.scene_ref_id).where(
                PairingDirtySceneORM.scene_ref_id.in_([row.id for row in radar_rows]),
                PairingDirtySceneORM.status == "PENDING",
            )
        )
        existing_pending_ids = {int(value) for value in existing_result.scalars().all()}

        created = 0
        now = _utcnow()
        for row in radar_rows:
            if int(row.id) in existing_pending_ids:
                continue
            db.add(
                PairingDirtySceneORM(
                    scene_ref_id=row.id,
                    scene_uid=_normalize_scene_uid(
                        scene_ref_id=row.id,
                        unique_id=row.unique_id,
                        file_path=row.file_path,
                    ),
                    reason=reason,
                    status="PENDING",
                    marked_at=now,
                )
            )
            created += 1

        await db.flush()

        state = await self._get_global_state(db)
        if state is None:
            await self.ensure_pairing_cache_state(db, commit=False)
            state = await self._get_global_state(db)
        if state is None:
            raise RuntimeError("Pairing cache state row is missing after bootstrap.")
        state.status = "DIRTY"
        state.last_error = None

        if commit:
            await db.commit()
        else:
            await db.flush()

        payload = await self.ensure_pairing_cache_state(db, commit=False)
        payload["dirty_mark_mode"] = "scene"
        payload["reason"] = reason
        payload["requested_scene_count"] = len(normalized_ids)
        payload["resolved_scene_count"] = len(radar_rows)
        payload["created_dirty_rows"] = created
        return payload

    async def get_pairing_system_status(
        self,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        own_session = db is None
        session = db or _new_session()
        try:
            scene_count = await self._count_radar_scenes(session)
            dirty_scene_count = await self._count_pending_dirty_scenes(session)
            metric_cache_count = await self._count_metric_cache_rows(session)
            state = await self._get_global_state(session)

            run_count_result = await session.execute(select(func.count(PairingNetworkRunORM.id)))
            network_run_count = int(run_count_result.scalar_one() or 0)

            edge_count_result = await session.execute(select(func.count(PairingNetworkEdgeORM.id)))
            network_edge_count = int(edge_count_result.scalar_one() or 0)

            reverse_duplicate_result = await session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pairing_metric_cache a
                    JOIN pairing_metric_cache b
                      ON a.master_scene_ref_id = b.slave_scene_ref_id
                     AND a.slave_scene_ref_id = b.master_scene_ref_id
                     AND a.metric_version = b.metric_version
                     AND a.id < b.id
                    """
                )
            )
            duplicate_reverse_pair_count = int(reverse_duplicate_result.scalar_one() or 0)

            orphan_edge_result = await session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pairing_network_edges e
                    LEFT JOIN pairing_network_runs r
                      ON r.id = e.network_run_ref_id
                    LEFT JOIN pairing_metric_cache m
                      ON m.id = e.metric_cache_ref_id
                    WHERE r.id IS NULL OR m.id IS NULL
                    """
                )
            )
            orphan_edge_count = int(orphan_edge_result.scalar_one() or 0)

            payload = self._build_state_payload(
                state=state,
                scene_count=scene_count,
                dirty_scene_count=dirty_scene_count,
                metric_cache_count=metric_cache_count,
            )
            payload.update(
                {
                    "ok": bool(
                        payload["status"] not in {"FAILED", "UNINITIALIZED", "ERROR"}
                        and orphan_edge_count == 0
                        and duplicate_reverse_pair_count == 0
                    ),
                    "network_run_count": network_run_count,
                    "network_edge_count": network_edge_count,
                    "duplicate_reverse_pair_count": duplicate_reverse_pair_count,
                    "orphan_edge_count": orphan_edge_count,
                    "orientation_rule_version": self.orientation_rule_version,
                    "error": None,
                }
            )
            return payload
        except Exception as exc:
            return {
                "ok": False,
                "state_present": False,
                "cache_scope": PAIRING_CACHE_SCOPE_GLOBAL,
                "metric_version": self.metric_version,
                "status": "ERROR",
                "scene_count": 0,
                "pair_count": 0,
                "dirty_scene_count": 0,
                "needs_rebuild": True,
                "cache_ready": False,
                "last_full_rebuild_at": None,
                "last_incremental_reconcile_at": None,
                "last_error": None,
                "updated_at": None,
                "network_run_count": 0,
                "network_edge_count": 0,
                "duplicate_reverse_pair_count": 0,
                "orphan_edge_count": 0,
                "orientation_rule_version": self.orientation_rule_version,
                "error": str(exc),
            }
        finally:
            if own_session:
                await session.close()


pairing_state_service = PairingStateService()
