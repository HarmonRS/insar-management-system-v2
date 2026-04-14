from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..models import ManagedRootORM, PathInventoryORM, ScanCursorORM
from .manifest_snapshot_service import ManifestEntrySnapshot, build_manifest_snapshot


def _utcnow() -> datetime:
    return datetime.utcnow()


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _normcase_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path or "").strip())))


def _is_parent_path(parent: str, child: str) -> bool:
    if not parent or not child or parent == child:
        return False
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def _manifest_status(metadata: Dict[str, Any]) -> str:
    return "INVALID" if metadata.get("parse_error") else "DISCOVERED"


def _read_manifest_metadata(path: str, *, root: ManagedRootORM) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "root_role": root.root_role,
        "owner_engine": root.owner_engine,
    }
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            metadata["parse_error"] = "manifest root must be a JSON object"
            return metadata
    except Exception as exc:
        metadata["parse_error"] = f"{exc.__class__.__name__}: {exc}"
        return metadata

    for key in (
        "schema_version",
        "catalog_name",
        "product_type",
        "product_id",
        "display_name",
        "run_key",
        "run_id",
        "group_key",
        "reference_date",
        "published_at",
        "produced_at",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            metadata[key] = value
    return metadata


class ManifestInventoryService:
    async def _get_manifest_roots(
        self,
        db: AsyncSession,
        *,
        root_id: Optional[int] = None,
    ) -> Tuple[List[ManagedRootORM], List[Dict[str, Any]]]:
        stmt = select(ManagedRootORM).where(ManagedRootORM.scan_mode == "manifest_tree")
        if root_id is None:
            stmt = stmt.where(ManagedRootORM.enabled == True)  # noqa: E712
        else:
            stmt = stmt.where(ManagedRootORM.id == int(root_id))
        stmt = stmt.order_by(ManagedRootORM.path.asc(), ManagedRootORM.id.asc())
        result = await db.execute(stmt)
        roots = result.scalars().all()
        if root_id is not None:
            return roots, []

        normalized_paths = {
            root.id: _normcase_path(root.path)
            for root in roots
            if str(root.path or "").strip()
        }
        effective_roots: List[ManagedRootORM] = []
        skipped_roots: List[Dict[str, Any]] = []
        for root in roots:
            root_path = normalized_paths.get(root.id, "")
            nested_child = next(
                (
                    other
                    for other in roots
                    if other.id != root.id
                    and _is_parent_path(root_path, normalized_paths.get(other.id, ""))
                ),
                None,
            )
            if nested_child is not None:
                skipped_roots.append(
                    {
                        "root_id": root.id,
                        "root_code": root.root_code,
                        "path": root.path,
                        "reason": "covered_by_nested_root",
                        "nested_root_id": nested_child.id,
                        "nested_root_code": nested_child.root_code,
                        "nested_root_path": nested_child.path,
                    }
                )
                continue
            effective_roots.append(root)
        return effective_roots, skipped_roots

    async def _ensure_default_cursor(self, db: AsyncSession, root: ManagedRootORM) -> ScanCursorORM:
        result = await db.execute(
            select(ScanCursorORM).where(
                ScanCursorORM.root_ref_id == root.id,
                ScanCursorORM.cursor_key == "default",
            )
        )
        cursor = result.scalar_one_or_none()
        if cursor is None:
            cursor = ScanCursorORM(
                root_ref_id=root.id,
                cursor_key="default",
                cursor_type="manifest_tree",
                scan_scope="root",
                status="IDLE",
            )
            db.add(cursor)
            await db.flush()
        return cursor

    async def _mark_root_removed(self, db: AsyncSession, *, root_id: int, seen_at: datetime) -> int:
        result = await db.execute(
            select(PathInventoryORM).where(
                PathInventoryORM.root_ref_id == root_id,
                PathInventoryORM.status != "REMOVED",
            )
        )
        rows = result.scalars().all()
        removed = 0
        for row in rows:
            row.status = "REMOVED"
            row.last_seen_at = seen_at
            removed += 1
        return removed

    async def _sync_single_root(self, db: AsyncSession, root: ManagedRootORM) -> Dict[str, Any]:
        started_at = _utcnow()
        cursor = await self._ensure_default_cursor(db, root)
        cursor.status = "RUNNING"
        cursor.last_scan_started_at = started_at
        cursor.last_error = None
        await db.flush()

        root_exists = os.path.isdir(root.path)
        root.exists_flag = root_exists
        if not root_exists:
            removed = await self._mark_root_removed(db, root_id=root.id, seen_at=started_at)
            cursor.last_seen_entry_count = 0
            cursor.last_seen_fingerprint = ""
            cursor.last_seen_mtime = None
            cursor.status = "IDLE"
            cursor.last_scan_finished_at = _utcnow()
            cursor.last_error = "root_missing"
            await db.commit()
            return {
                "root_id": root.id,
                "root_code": root.root_code,
                "path": root.path,
                "manifest_count": 0,
                "created": 0,
                "updated": 0,
                "removed": removed,
                "invalid": 0,
                "fingerprint": "",
                "status": "root_missing",
            }

        snapshot = await asyncio.to_thread(build_manifest_snapshot, root.path)
        seen_at = _utcnow()
        existing_result = await db.execute(
            select(PathInventoryORM).where(PathInventoryORM.root_ref_id == root.id)
        )
        existing_rows = existing_result.scalars().all()
        existing_by_rel = {row.relative_path: row for row in existing_rows}
        seen_paths: set[str] = set()

        created = 0
        updated = 0
        removed = 0
        invalid = 0

        for entry in snapshot.entries:
            seen_paths.add(entry.relative_path)
            metadata = _read_manifest_metadata(entry.absolute_path, root=root)
            status = _manifest_status(metadata)
            if status == "INVALID":
                invalid += 1
            row = existing_by_rel.get(entry.relative_path)
            values = {
                "basename": os.path.basename(entry.relative_path),
                "extension": os.path.splitext(entry.relative_path)[1].lower() or None,
                "size_bytes": entry.size_bytes,
                "mtime": entry.mtime,
                "ctime": entry.ctime,
                "fingerprint": entry.fingerprint,
                "status": status,
                "metadata_json": metadata,
                "last_seen_at": seen_at,
                "last_parsed_at": seen_at,
            }
            if row is None:
                row = PathInventoryORM(
                    root_ref_id=root.id,
                    relative_path=entry.relative_path,
                    path_type="file",
                    first_seen_at=seen_at,
                    **values,
                )
                db.add(row)
                created += 1
                continue

            changed = False
            for field_name, field_value in values.items():
                if getattr(row, field_name) != field_value:
                    setattr(row, field_name, field_value)
                    changed = True
            if changed:
                updated += 1

        for row in existing_rows:
            if row.relative_path in seen_paths:
                continue
            if row.status != "REMOVED":
                row.status = "REMOVED"
                row.last_seen_at = seen_at
                removed += 1

        latest_mtime = max(
            (float(entry.mtime) for entry in snapshot.entries if entry.mtime is not None),
            default=None,
        )
        cursor.last_seen_entry_count = snapshot.manifest_count
        cursor.last_seen_fingerprint = snapshot.tree_fingerprint
        cursor.last_seen_mtime = latest_mtime
        cursor.status = "IDLE"
        cursor.last_scan_finished_at = _utcnow()
        cursor.last_error = None
        await db.commit()

        return {
            "root_id": root.id,
            "root_code": root.root_code,
            "path": root.path,
            "manifest_count": snapshot.manifest_count,
            "created": created,
            "updated": updated,
            "removed": removed,
            "invalid": invalid,
            "fingerprint": snapshot.tree_fingerprint,
            "status": "ok",
        }

    async def sync_manifest_roots(
        self,
        db: Optional[AsyncSession] = None,
        *,
        root_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        generated_session = db is None
        if generated_session:
            db = _new_session()
        assert db is not None

        try:
            roots, skipped_roots = await self._get_manifest_roots(db, root_id=root_id)
            results: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            total_created = 0
            total_updated = 0
            total_removed = 0
            total_invalid = 0
            total_manifests = 0

            for root in roots:
                try:
                    item = await self._sync_single_root(db, root)
                    results.append(item)
                    total_created += int(item["created"])
                    total_updated += int(item["updated"])
                    total_removed += int(item["removed"])
                    total_invalid += int(item["invalid"])
                    total_manifests += int(item["manifest_count"])
                except Exception as exc:
                    await db.rollback()
                    errors.append(
                        {
                            "root_id": root.id,
                            "root_code": root.root_code,
                            "path": root.path,
                            "error": str(exc),
                        }
                    )

            return {
                "root_id": root_id,
                "scanned_roots": len(results),
                "skipped_roots": len(skipped_roots),
                "manifest_count": total_manifests,
                "created": total_created,
                "updated": total_updated,
                "removed": total_removed,
                "invalid": total_invalid,
                "errors": errors,
                "results": results,
                "skipped": skipped_roots,
            }
        finally:
            if generated_session:
                await db.close()

    async def list_inventory(
        self,
        db: AsyncSession,
        *,
        root_id: int,
        limit: int = 200,
        offset: int = 0,
        include_removed: bool = False,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 200), 1000))
        safe_offset = max(0, int(offset or 0))

        root_result = await db.execute(
            select(ManagedRootORM).where(ManagedRootORM.id == int(root_id))
        )
        root = root_result.scalar_one_or_none()
        if root is None:
            raise ValueError(f"Managed root not found: {root_id}")

        stmt = select(PathInventoryORM).where(PathInventoryORM.root_ref_id == root.id)
        count_stmt = select(func.count(PathInventoryORM.id)).where(PathInventoryORM.root_ref_id == root.id)
        if not include_removed:
            stmt = stmt.where(PathInventoryORM.status != "REMOVED")
            count_stmt = count_stmt.where(PathInventoryORM.status != "REMOVED")

        total_result = await db.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)
        rows_result = await db.execute(
            stmt.order_by(PathInventoryORM.relative_path.asc()).offset(safe_offset).limit(safe_limit)
        )
        rows = rows_result.scalars().all()

        return {
            "root": {
                "id": root.id,
                "root_code": root.root_code,
                "root_role": root.root_role,
                "display_name": root.display_name,
                "path": root.path,
                "scan_mode": root.scan_mode,
                "owner_engine": root.owner_engine,
                "enabled": bool(root.enabled),
                "exists_flag": bool(root.exists_flag),
            },
            "items": [
                {
                    "id": row.id,
                    "relative_path": row.relative_path,
                    "path_type": row.path_type,
                    "basename": row.basename,
                    "extension": row.extension,
                    "size_bytes": row.size_bytes,
                    "mtime": row.mtime,
                    "ctime": row.ctime,
                    "fingerprint": row.fingerprint,
                    "status": row.status,
                    "metadata_json": row.metadata_json,
                    "first_seen_at": row.first_seen_at,
                    "last_seen_at": row.last_seen_at,
                    "last_parsed_at": row.last_parsed_at,
                }
                for row in rows
            ],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
        }


manifest_inventory_service = ManifestInventoryService()
