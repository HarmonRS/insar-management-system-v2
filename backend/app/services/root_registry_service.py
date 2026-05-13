from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..config import settings, split_env_paths
from ..models import AssetInventoryStateORM, ManagedRootORM, PathInventoryORM, ScanCursorORM


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")


@dataclass(frozen=True)
class RootSpec:
    root_code: str
    root_role: str
    display_name: str
    path: str
    path_kind: str
    source_kind: str
    source_ref: Optional[str]
    scan_mode: str
    owner_engine: Optional[str]
    metadata_json: Dict[str, Any]


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _slugify(value: str, *, max_len: int = 96) -> str:
    text = _SLUG_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return (text or "item")[:max_len]


def _normalize_root_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith("\\\\"):
        return os.path.normpath(text)
    if _WINDOWS_DRIVE_RE.match(text):
        return os.path.normpath(text)
    if text.startswith("/"):
        return text.replace("\\", "/")
    return os.path.normpath(os.path.abspath(text))


def _detect_path_kind(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("\\\\"):
        return "unc"
    if _WINDOWS_DRIVE_RE.match(text):
        return "windows"
    if text.startswith("/mnt/"):
        return "wsl_mount"
    if text.startswith("/"):
        return "posix"
    return "relative"


def _build_root_code(root_role: str, source_ref: Optional[str]) -> str:
    role_part = _slugify(root_role, max_len=48)
    source_part = _slugify(source_ref or "manual", max_len=40)
    return f"{role_part}__{source_part}"[:96]


def _cursor_type_for_scan_mode(scan_mode: str) -> str:
    mapping = {
        "archive_walk": "archive_walk",
        "scene_directory": "directory_walk",
        "manifest_tree": "manifest_tree",
        "file_pool": "file_pool",
        "workspace": "workspace",
        "quarantine": "directory_walk",
        "directory_walk": "directory_walk",
    }
    return mapping.get(str(scan_mode or "").strip().lower(), "directory_walk")


def _asset_inventory_type_for_root_role(root_role: str) -> Optional[str]:
    role = str(root_role or "").strip().lower()
    if role == "source_product_pool":
        return "source_product"
    if role == "orbit_asset_pool":
        return "orbit_asset"
    return None


def _iter_multi_root_specs(
    *,
    env_var: str,
    paths: Iterable[str],
    root_role: str,
    display_prefix: str,
    scan_mode: str,
    owner_engine: Optional[str] = None,
) -> List[RootSpec]:
    specs: List[RootSpec] = []
    deduped: List[str] = []
    for raw_path in paths:
        normalized_path = _normalize_root_path(raw_path)
        if normalized_path and normalized_path not in deduped:
            deduped.append(normalized_path)

    for idx, path in enumerate(deduped, start=1):
        source_ref = f"{env_var}[{idx}]"
        specs.append(
            RootSpec(
                root_code=_build_root_code(root_role, source_ref),
                root_role=root_role,
                display_name=f"{display_prefix} {idx}",
                path=path,
                path_kind=_detect_path_kind(path),
                source_kind="env",
                source_ref=source_ref,
                scan_mode=scan_mode,
                owner_engine=owner_engine,
                metadata_json={
                    "env_var": env_var,
                    "env_index": idx,
                    "imported_from": "settings",
                },
            )
        )
    return specs


def _iter_single_root_specs(
    *,
    env_var: str,
    path: Optional[str],
    root_role: str,
    display_name: str,
    scan_mode: str,
    owner_engine: Optional[str] = None,
) -> List[RootSpec]:
    normalized_path = _normalize_root_path(path or "")
    if not normalized_path:
        return []
    source_ref = env_var
    return [
        RootSpec(
            root_code=_build_root_code(root_role, source_ref),
            root_role=root_role,
            display_name=display_name,
            path=normalized_path,
            path_kind=_detect_path_kind(normalized_path),
            source_kind="env",
            source_ref=source_ref,
            scan_mode=scan_mode,
            owner_engine=owner_engine,
            metadata_json={
                "env_var": env_var,
                "imported_from": "settings",
            },
        )
    ]


def _build_root_specs_from_settings() -> List[RootSpec]:
    specs: List[RootSpec] = []
    specs.extend(
        _iter_multi_root_specs(
            env_var="UNPACK_SOURCE_DIRS",
            paths=split_env_paths(settings.UNPACK_SOURCE_DIRS),
            root_role="archive_inbox",
            display_prefix="Archive Inbox",
            scan_mode="archive_walk",
        )
    )
    source_product_paths = split_env_paths(settings.SOURCE_PRODUCT_DIRS)
    if not source_product_paths:
        source_product_paths = (
            split_env_paths(settings.INSAR_STORAGE_DIRS)
            + split_env_paths(settings.MONITOR_RADAR_DIRS)
        )
    specs.extend(
        _iter_multi_root_specs(
            env_var="SOURCE_PRODUCT_DIRS",
            paths=source_product_paths,
            root_role="source_product_pool",
            display_prefix="Source Product Pool",
            scan_mode="file_pool",
        )
    )
    source_product_path_set = {_normalize_root_path(path) for path in source_product_paths}
    sentinel1_storage_paths = [
        path
        for path in split_env_paths(settings.SENTINEL1_STORAGE_DIRS)
        if _normalize_root_path(path) not in source_product_path_set
    ]
    specs.extend(
        _iter_multi_root_specs(
            env_var="SENTINEL1_STORAGE_DIRS",
            paths=sentinel1_storage_paths,
            root_role="source_product_pool",
            display_prefix="Sentinel-1 Storage Pool",
            scan_mode="file_pool",
        )
    )
    specs.extend(
        _iter_multi_root_specs(
            env_var="INSAR_STORAGE_DIRS",
            paths=split_env_paths(settings.INSAR_STORAGE_DIRS),
            root_role="source_pool_radar",
            display_prefix="Radar Source Pool",
            scan_mode="scene_directory",
        )
    )
    specs.extend(
        _iter_multi_root_specs(
            env_var="MONITOR_RADAR_DIRS",
            paths=split_env_paths(settings.MONITOR_RADAR_DIRS),
            root_role="legacy_scan_root_radar",
            display_prefix="Legacy Radar Scan Root",
            scan_mode="directory_walk",
        )
    )
    specs.extend(
        _iter_multi_root_specs(
            env_var="MONITOR_DINSAR_DIRS",
            paths=split_env_paths(settings.MONITOR_DINSAR_DIRS),
            root_role="legacy_scan_root_dinsar",
            display_prefix="Legacy D-InSAR Scan Root",
            scan_mode="directory_walk",
        )
    )
    specs.extend(
        _iter_multi_root_specs(
            env_var="GF3_SOURCE_DIRS",
            paths=split_env_paths(settings.GF3_SOURCE_DIRS),
            root_role="source_pool_gf3_input",
            display_prefix="GF3 Input Pool",
            scan_mode="scene_directory",
        )
    )
    specs.extend(
        _iter_multi_root_specs(
            env_var="GF3_STORAGE_DIRS",
            paths=split_env_paths(settings.GF3_STORAGE_DIRS),
            root_role="source_pool_gf3_output",
            display_prefix="GF3 Output Pool",
            scan_mode="scene_directory",
        )
    )
    orbit_source_paths = split_env_paths(settings.ORBIT_SOURCE_DIRS)
    if not orbit_source_paths:
        orbit_source_paths = split_env_paths(settings.MONITOR_ORBIT_DIR)
    specs.extend(
        _iter_multi_root_specs(
            env_var="ORBIT_SOURCE_DIRS",
            paths=orbit_source_paths,
            root_role="orbit_asset_pool",
            display_prefix="Orbit Asset Pool",
            scan_mode="file_pool",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="MONITOR_ORBIT_DIR",
            path=settings.MONITOR_ORBIT_DIR,
            root_role="orbit_source",
            display_name="Orbit Source",
            scan_mode="file_pool",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="ORBIT_POOL_ENVI",
            path=settings.ORBIT_POOL_ENVI,
            root_role="orbit_pool_envi",
            display_name="ENVI Orbit Pool",
            scan_mode="file_pool",
            owner_engine="envi",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="ORBIT_POOL_ISCE2",
            path=settings.ORBIT_POOL_ISCE2,
            root_role="orbit_pool_isce2",
            display_name="ISCE2 Orbit Pool",
            scan_mode="file_pool",
            owner_engine="isce2",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="ORBIT_POOL_LANDSAR",
            path=settings.ORBIT_POOL_LANDSAR,
            root_role="orbit_pool_landsar",
            display_name="LandSAR Orbit Pool",
            scan_mode="file_pool",
            owner_engine="landsar",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="RESULT_PUBLISH_ROOT",
            path=settings.RESULT_PUBLISH_ROOT,
            root_role="result_publish_root",
            display_name="Result Publish Root",
            scan_mode="manifest_tree",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="DINSAR_PRODUCT_DIR",
            path=settings.DINSAR_PRODUCT_DIR,
            root_role="publish_root_dinsar",
            display_name="D-InSAR Publish Root",
            scan_mode="manifest_tree",
            owner_engine="dinsar",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="TIMESERIES_PRODUCT_DIR",
            path=settings.TIMESERIES_PRODUCT_DIR,
            root_role="publish_root_timeseries",
            display_name="Timeseries Publish Root",
            scan_mode="manifest_tree",
            owner_engine="timeseries",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="RESULT_QUARANTINE_ROOT",
            path=settings.RESULT_QUARANTINE_ROOT,
            root_role="quarantine_root",
            display_name="Result Quarantine Root",
            scan_mode="quarantine",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="ISCE2_WORK_ROOT",
            path=settings.ISCE2_WORK_ROOT,
            root_role="work_root_isce2",
            display_name="ISCE2 Work Root",
            scan_mode="workspace",
            owner_engine="isce2",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="TIMESERIES_WORK_ROOT",
            path=settings.TIMESERIES_WORK_ROOT,
            root_role="work_root_timeseries",
            display_name="Timeseries Work Root",
            scan_mode="workspace",
            owner_engine="timeseries",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="WSL_BROKER_JOB_ROOT",
            path=settings.WSL_BROKER_JOB_ROOT,
            root_role="work_root_wsl_broker",
            display_name="WSL Broker Root",
            scan_mode="workspace",
            owner_engine="wsl",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="IDL_WORKER_RUNTIME_DIR",
            path=settings.IDL_WORKER_RUNTIME_DIR,
            root_role="work_root_idl",
            display_name="IDL Runtime Root",
            scan_mode="workspace",
            owner_engine="idl",
        )
    )
    specs.extend(
        _iter_single_root_specs(
            env_var="PYINT_WORK_ROOT",
            path=settings.PYINT_WORK_ROOT,
            root_role="work_root_pyint",
            display_name="Gamma / PyINT Work Root",
            scan_mode="workspace",
            owner_engine="pyint",
        )
    )
    return specs


class RootRegistryService:
    async def _ensure_default_cursor(self, db: AsyncSession, root: ManagedRootORM) -> Optional[str]:
        cursor_result = await db.execute(
            select(ScanCursorORM).where(
                ScanCursorORM.root_ref_id == root.id,
                ScanCursorORM.cursor_key == "default",
            )
        )
        cursor = cursor_result.scalar_one_or_none()
        desired_cursor_type = _cursor_type_for_scan_mode(root.scan_mode)
        if cursor is None:
            cursor = ScanCursorORM(
                root_ref_id=root.id,
                cursor_key="default",
                cursor_type=desired_cursor_type,
                scan_scope="root",
                status="IDLE",
            )
            db.add(cursor)
            return "created"

        changed = False
        if cursor.cursor_type != desired_cursor_type:
            cursor.cursor_type = desired_cursor_type
            changed = True
        if cursor.scan_scope != "root":
            cursor.scan_scope = "root"
            changed = True
        return "updated" if changed else None

    async def _ensure_asset_inventory_state(self, db: AsyncSession, root: ManagedRootORM) -> Optional[str]:
        inventory_type = _asset_inventory_type_for_root_role(root.root_role)
        if not inventory_type:
            return None

        result = await db.execute(
            select(AssetInventoryStateORM).where(
                AssetInventoryStateORM.root_ref_id == root.id,
                AssetInventoryStateORM.inventory_type == inventory_type,
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = AssetInventoryStateORM(
                root_ref_id=root.id,
                inventory_type=inventory_type,
                root_path=root.path,
                scan_mode=root.scan_mode,
                status="NEVER_SCANNED",
                needs_rescan=True,
                metadata_json={
                    "root_role": root.root_role,
                    "created_by": "root_registry_sync",
                },
            )
            db.add(state)
            return "created"

        changed = False
        if state.root_path != root.path:
            state.root_path = root.path
            changed = True
        if state.scan_mode != root.scan_mode:
            state.scan_mode = root.scan_mode
            changed = True
        return "updated" if changed else None

    async def sync_from_settings(self, db: Optional[AsyncSession] = None) -> Dict[str, Any]:
        generated_session = db is None
        if generated_session:
            db = _new_session()

        assert db is not None

        try:
            specs = _build_root_specs_from_settings()
            existing_result = await db.execute(
                select(ManagedRootORM).where(ManagedRootORM.source_kind == "env")
            )
            existing_rows = existing_result.scalars().all()
            existing_by_code = {row.root_code: row for row in existing_rows}

            created = 0
            updated = 0
            disabled = 0
            cursor_created = 0
            cursor_updated = 0
            inventory_state_created = 0
            inventory_state_updated = 0
            synced_codes: set[str] = set()

            for spec in specs:
                synced_codes.add(spec.root_code)
                exists_flag = os.path.exists(spec.path)
                row = existing_by_code.get(spec.root_code)

                if row is None:
                    row = ManagedRootORM(
                        root_code=spec.root_code,
                        root_role=spec.root_role,
                        display_name=spec.display_name,
                        path=spec.path,
                        path_kind=spec.path_kind,
                        source_kind=spec.source_kind,
                        source_ref=spec.source_ref,
                        scan_mode=spec.scan_mode,
                        owner_engine=spec.owner_engine,
                        enabled=True,
                        exists_flag=exists_flag,
                        metadata_json=spec.metadata_json,
                    )
                    db.add(row)
                    await db.flush()
                    created += 1
                else:
                    changed = False
                    for field_name, value in (
                        ("root_role", spec.root_role),
                        ("display_name", spec.display_name),
                        ("path", spec.path),
                        ("path_kind", spec.path_kind),
                        ("source_kind", spec.source_kind),
                        ("source_ref", spec.source_ref),
                        ("scan_mode", spec.scan_mode),
                        ("owner_engine", spec.owner_engine),
                        ("exists_flag", exists_flag),
                        ("metadata_json", spec.metadata_json),
                    ):
                        if getattr(row, field_name) != value:
                            setattr(row, field_name, value)
                            changed = True
                    if not row.enabled:
                        row.enabled = True
                        changed = True
                    if changed:
                        updated += 1

                cursor_change = await self._ensure_default_cursor(db, row)
                if cursor_change == "created":
                    cursor_created += 1
                elif cursor_change == "updated":
                    cursor_updated += 1

                inventory_state_change = await self._ensure_asset_inventory_state(db, row)
                if inventory_state_change == "created":
                    inventory_state_created += 1
                elif inventory_state_change == "updated":
                    inventory_state_updated += 1

            for row in existing_rows:
                if row.root_code in synced_codes:
                    continue
                if row.enabled:
                    row.enabled = False
                    disabled += 1

            await db.commit()

            summary = await self.get_summary(db)
            return {
                "synced": len(specs),
                "created": created,
                "updated": updated,
                "disabled": disabled,
                "cursor_created_or_updated": cursor_created + cursor_updated,
                "asset_inventory_state_created_or_updated": inventory_state_created + inventory_state_updated,
                "summary": summary,
            }
        except Exception:
            await db.rollback()
            raise
        finally:
            if generated_session:
                await db.close()

    async def list_roots(self, db: AsyncSession, *, include_disabled: bool = False) -> List[Dict[str, Any]]:
        stmt = select(ManagedRootORM).order_by(
            ManagedRootORM.root_role.asc(),
            ManagedRootORM.display_name.asc(),
            ManagedRootORM.id.asc(),
        )
        if not include_disabled:
            stmt = stmt.where(ManagedRootORM.enabled == True)  # noqa: E712
        root_result = await db.execute(stmt)
        roots = root_result.scalars().all()
        root_ids = [root.id for root in roots]

        cursor_by_root_id: Dict[int, ScanCursorORM] = {}
        inventory_counts: Dict[int, int] = {}
        if root_ids:
            cursor_result = await db.execute(
                select(ScanCursorORM)
                .where(ScanCursorORM.root_ref_id.in_(root_ids))
                .where(ScanCursorORM.cursor_key == "default")
            )
            cursor_by_root_id = {
                item.root_ref_id: item
                for item in cursor_result.scalars().all()
            }

            inventory_result = await db.execute(
                select(PathInventoryORM.root_ref_id, func.count(PathInventoryORM.id))
                .where(PathInventoryORM.root_ref_id.in_(root_ids))
                .where(PathInventoryORM.status != "REMOVED")
                .group_by(PathInventoryORM.root_ref_id)
            )
            inventory_counts = {
                int(root_ref_id): int(count or 0)
                for root_ref_id, count in inventory_result.all()
            }

        payload: List[Dict[str, Any]] = []
        for root in roots:
            cursor = cursor_by_root_id.get(root.id)
            payload.append(
                {
                    "id": root.id,
                    "root_code": root.root_code,
                    "root_role": root.root_role,
                    "display_name": root.display_name,
                    "path": root.path,
                    "path_kind": root.path_kind,
                    "source_kind": root.source_kind,
                    "source_ref": root.source_ref,
                    "scan_mode": root.scan_mode,
                    "owner_engine": root.owner_engine,
                    "enabled": bool(root.enabled),
                    "exists_flag": bool(root.exists_flag),
                    "metadata_json": root.metadata_json,
                    "created_at": root.created_at,
                    "updated_at": root.updated_at,
                    "inventory_count": inventory_counts.get(root.id, 0),
                    "cursor": (
                        {
                            "id": cursor.id,
                            "cursor_key": cursor.cursor_key,
                            "cursor_type": cursor.cursor_type,
                            "scan_scope": cursor.scan_scope,
                            "status": cursor.status,
                            "last_scan_started_at": cursor.last_scan_started_at,
                            "last_scan_finished_at": cursor.last_scan_finished_at,
                            "last_seen_mtime": cursor.last_seen_mtime,
                            "last_seen_entry_count": cursor.last_seen_entry_count,
                            "last_seen_fingerprint": cursor.last_seen_fingerprint,
                            "last_error": cursor.last_error,
                            "updated_at": cursor.updated_at,
                        }
                        if cursor is not None
                        else None
                    ),
                }
            )
        return payload

    async def get_summary(self, db: AsyncSession) -> Dict[str, Any]:
        total_roots = int(
            (await db.execute(select(func.count(ManagedRootORM.id)))).scalar_one() or 0
        )
        enabled_roots = int(
            (
                await db.execute(
                    select(func.count(ManagedRootORM.id)).where(ManagedRootORM.enabled == True)  # noqa: E712
                )
            ).scalar_one()
            or 0
        )
        existing_roots = int(
            (
                await db.execute(
                    select(func.count(ManagedRootORM.id)).where(ManagedRootORM.exists_flag == True)  # noqa: E712
                )
            ).scalar_one()
            or 0
        )
        total_cursors = int(
            (await db.execute(select(func.count(ScanCursorORM.id)))).scalar_one() or 0
        )
        total_inventory = int(
            (
                await db.execute(
                    select(func.count(PathInventoryORM.id)).where(PathInventoryORM.status != "REMOVED")
                )
            ).scalar_one()
            or 0
        )

        role_rows = await db.execute(
            select(
                ManagedRootORM.root_role,
                func.count(ManagedRootORM.id),
            )
            .group_by(ManagedRootORM.root_role)
            .order_by(ManagedRootORM.root_role.asc())
        )
        role_counts = {
            str(role): int(count or 0)
            for role, count in role_rows.all()
        }

        return {
            "total_roots": total_roots,
            "enabled_roots": enabled_roots,
            "existing_roots": existing_roots,
            "missing_roots": max(0, enabled_roots - existing_roots),
            "total_cursors": total_cursors,
            "total_inventory_items": total_inventory,
            "role_counts": role_counts,
        }

    async def get_status(self, db: AsyncSession, *, include_disabled: bool = False) -> Dict[str, Any]:
        roots = await self.list_roots(db, include_disabled=include_disabled)
        summary = await self.get_summary(db)
        return {
            "summary": summary,
            "roots": roots,
        }


root_registry_service = RootRegistryService()
