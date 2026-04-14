from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import ResultAssetORM, ResultCatalogStateORM, ResultIssueORM, ResultProductORM
from .manifest_snapshot_service import (
    build_manifest_snapshot,
    evaluate_manifest_reconcile,
    iter_manifest_paths,
)


PSINSAR_CATALOG_NAME = "psinsar"
JOB_TYPE_REBUILD_PSINSAR_CATALOG = "REBUILD_PSINSAR_CATALOG"
TASK_TYPE_REBUILD_PSINSAR_CATALOG = "REBUILD_PSINSAR_CATALOG"

_ARTIFACT_ROLE_MAP = {
    "timeseries_cube": "timeseries_cube",
    "velocity_map": "velocity_map",
    "velocity_geotiff": "velocity_geotiff",
    "temporal_coherence": "temporal_coherence",
    "temporal_coherence_geotiff": "temporal_coherence_geotiff",
    "quality_mask": "quality_mask",
    "quality_mask_geotiff": "quality_mask_geotiff",
    "preview_png": "preview_png",
    "diagnostic_png": "diagnostic_png",
}
_PRIMARY_PRODUCT_TYPES = {"timeseries_cube", "velocity_geotiff"}
_PREFERRED_PRIMARY_PRODUCT_TYPES = ("velocity_geotiff", "timeseries_cube", "velocity_map")
_PREVIEW_PRODUCT_TYPES = {"preview_png"}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _stable_digest(*parts: Any, length: int = 20) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _resolve_relative_path(base_dir: str, relative_path: str) -> str:
    base_dir = _normalize_path(base_dir)
    target = _normalize_path(os.path.join(base_dir, relative_path))
    if not target.startswith(base_dir + os.sep) and target != base_dir:
        raise ValueError(f"Invalid relative path outside package: {relative_path}")
    return target


def _build_bbox_polygon(
    min_lon: Optional[float],
    min_lat: Optional[float],
    max_lon: Optional[float],
    max_lat: Optional[float],
):
    if None in (min_lon, min_lat, max_lon, max_lat):
        return None
    return Polygon(
        [
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
        ]
    )


def _artifact_format(path: str) -> Optional[str]:
    ext = os.path.splitext(str(path or "").lower())[1]
    return {
        ".h5": "hdf5",
        ".tif": "geotiff",
        ".tiff": "geotiff",
        ".png": "png",
        ".json": "json",
        ".cfg": "cfg",
    }.get(ext)


def _artifact_media_type(path: str) -> Optional[str]:
    ext = os.path.splitext(str(path or "").lower())[1]
    return {
        ".h5": "application/x-hdf5",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".png": "image/png",
        ".json": "application/json",
        ".cfg": "text/plain",
    }.get(ext)


def _derive_bbox_from_summary(manifest: Dict[str, Any]) -> Dict[str, Optional[float]]:
    summaries = manifest.get("summaries") or {}
    for key in ("geo_velocity", "geo_timeseries", "velocity"):
        summary = summaries.get(key) or {}
        attrs = summary.get("attrs") or {}
        x_first = _safe_float(attrs.get("X_FIRST"))
        y_first = _safe_float(attrs.get("Y_FIRST"))
        x_step = _safe_float(attrs.get("X_STEP"))
        y_step = _safe_float(attrs.get("Y_STEP"))
        width = _safe_int(attrs.get("WIDTH"))
        length = _safe_int(attrs.get("LENGTH"))
        if None in (x_first, y_first, x_step, y_step, width, length):
            continue
        x_last = x_first + x_step * max(width - 1, 0)
        y_last = y_first + y_step * max(length - 1, 0)
        return {
            "min_lon": min(x_first, x_last),
            "max_lon": max(x_first, x_last),
            "min_lat": min(y_first, y_last),
            "max_lat": max(y_first, y_last),
        }
    return {
        "min_lon": None,
        "max_lon": None,
        "min_lat": None,
        "max_lat": None,
    }


class PsinsarCatalogService:
    def get_publish_root(self, publish_root: Optional[str] = None) -> str:
        root = publish_root or settings.PSINSAR_PRODUCT_DIR
        normalized = _normalize_path(root)
        os.makedirs(normalized, exist_ok=True)
        return normalized

    async def _get_or_create_catalog_state(
        self,
        db: AsyncSession,
        *,
        storage_root: Optional[str] = None,
    ) -> ResultCatalogStateORM:
        root = self.get_publish_root(storage_root)
        result = await db.execute(
            select(ResultCatalogStateORM).where(
                ResultCatalogStateORM.catalog_name == PSINSAR_CATALOG_NAME
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = ResultCatalogStateORM(
                catalog_name=PSINSAR_CATALOG_NAME,
                storage_root=root,
                status="READY",
                needs_rebuild=False,
            )
            db.add(state)
            await db.flush()
        elif state.storage_root != root:
            state.storage_root = root
        return state

    def _iter_manifest_paths(self, publish_root: str) -> List[str]:
        return iter_manifest_paths(self.get_publish_root(publish_root))

    def _load_manifest(self, manifest_path: str) -> Dict[str, Any]:
        with open(manifest_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        schema_version = str(payload.get("schema_version") or "").strip().lower()
        catalog_name = str(payload.get("catalog_name") or "").strip().lower()
        if schema_version != "psinsar.publish.v1":
            raise ValueError("manifest schema_version is not psinsar.publish.v1")
        if catalog_name != PSINSAR_CATALOG_NAME:
            raise ValueError("manifest catalog_name is not psinsar")
        return payload

    def _build_rows_from_manifest(
        self,
        manifest_path: str,
        manifest: Dict[str, Any],
    ) -> ResultProductORM:
        package_dir = _normalize_path(os.path.dirname(manifest_path))
        basename = os.path.basename(package_dir)
        group_key = str(manifest.get("group_key") or "").strip() or None
        reference_date = str(manifest.get("reference_date") or "").strip() or None
        stack_dates = [str(item).strip() for item in (manifest.get("stack_dates") or []) if str(item).strip()]
        run_key = str(manifest.get("run_id") or "").strip() or basename
        display_name = group_key or basename
        product_id = "psinsar_" + _stable_digest(package_dir, run_key, reference_date, length=20)
        bbox = _derive_bbox_from_summary(manifest)
        poly = _build_bbox_polygon(
            bbox.get("min_lon"),
            bbox.get("min_lat"),
            bbox.get("max_lon"),
            bbox.get("max_lat"),
        )

        summary_json = {
            "group_key": group_key,
            "reference_date": reference_date,
            "reference_point": manifest.get("reference_point"),
            "stack_dates": stack_dates,
            "stack_size": len(stack_dates),
            "mode": manifest.get("mode"),
            "processor_code": manifest.get("processor_code"),
            "quality": manifest.get("quality"),
            "summaries": manifest.get("summaries"),
        }
        published_at = _parse_datetime(manifest.get("published_at"))
        if published_at is None:
            try:
                published_at = datetime.utcfromtimestamp(os.path.getmtime(manifest_path))
            except OSError:
                published_at = _utcnow()

        product = ResultProductORM(
            product_id=product_id,
            catalog_name=PSINSAR_CATALOG_NAME,
            product_type="psinsar_bundle",
            display_name=display_name,
            task_name=display_name,
            task_alias=group_key or basename,
            pair_key=None,
            run_key=run_key,
            profile_code=str(manifest.get("processor_code") or "").strip() or None,
            engine_code=str(manifest.get("engine_code") or "unknown"),
            engine_version=None,
            status="READY",
            health_status="OK",
            publish_dir=package_dir,
            manifest_path=_normalize_path(manifest_path),
            source_primary_path=None,
            preview_path=None,
            primary_asset_path=None,
            summary_json=summary_json,
            tags_json=None,
            min_lon=bbox.get("min_lon"),
            min_lat=bbox.get("min_lat"),
            max_lon=bbox.get("max_lon"),
            max_lat=bbox.get("max_lat"),
            geom=from_shape(poly, srid=4326) if poly is not None else None,
            coverage_polygon={
                "type": "Polygon",
                "coordinates": [[
                    [bbox["min_lon"], bbox["min_lat"]],
                    [bbox["max_lon"], bbox["min_lat"]],
                    [bbox["max_lon"], bbox["max_lat"]],
                    [bbox["min_lon"], bbox["max_lat"]],
                    [bbox["min_lon"], bbox["min_lat"]],
                ]],
            } if None not in (bbox["min_lon"], bbox["min_lat"], bbox["max_lon"], bbox["max_lat"]) else None,
            produced_at=_parse_datetime(manifest.get("produced_at")) or published_at,
            published_at=published_at,
            registered_at=_utcnow(),
        )

        has_warn = False
        has_error = False
        artifacts = manifest.get("artifacts") or []
        chosen_primary_path = None
        for candidate_type in _PREFERRED_PRIMARY_PRODUCT_TYPES:
            for artifact in artifacts:
                if str(artifact.get("product_type") or "").strip() == candidate_type:
                    chosen_primary_path = str(artifact.get("path") or "").strip()
                    break
            if chosen_primary_path:
                break

        for artifact in artifacts:
            relative_path = str(artifact.get("path") or "").strip()
            if not relative_path:
                continue
            absolute_path = _resolve_relative_path(package_dir, relative_path)
            exists_flag = os.path.exists(absolute_path)
            try:
                file_size = os.path.getsize(absolute_path) if exists_flag else None
            except OSError:
                file_size = None

            product_type = str(artifact.get("product_type") or "asset").strip()
            asset_role = _ARTIFACT_ROLE_MAP.get(product_type, product_type or "asset")
            is_required = product_type in _PRIMARY_PRODUCT_TYPES or product_type in _PREVIEW_PRODUCT_TYPES
            is_primary = relative_path == chosen_primary_path
            asset = ResultAssetORM(
                asset_role=asset_role,
                asset_name=os.path.basename(relative_path) or asset_role,
                relative_path=relative_path,
                absolute_path=absolute_path,
                format=_artifact_format(relative_path),
                media_type=_artifact_media_type(relative_path),
                is_required=is_required,
                is_primary=is_primary,
                exists_flag=exists_flag,
                file_size=file_size,
            )
            product.assets.append(asset)
            if product_type == "timeseries_cube" and exists_flag:
                product.source_primary_path = absolute_path
            if is_primary and exists_flag:
                product.primary_asset_path = absolute_path
            if product_type in _PREVIEW_PRODUCT_TYPES and exists_flag:
                product.preview_path = absolute_path
            if is_required and not exists_flag:
                has_error = True
                product.issues.append(
                    ResultIssueORM(
                        issue_code="MISSING_REQUIRED_ASSET",
                        severity="ERROR",
                        status="OPEN",
                        scope="file",
                        message=f"required asset missing: {relative_path}",
                        repair_action="rebuild_catalog",
                    )
                )

        if not product.preview_path:
            has_warn = True
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_PREVIEW",
                    severity="WARN",
                    status="OPEN",
                    scope="file",
                    message="preview image is missing",
                    repair_action="rebuild_preview",
                )
            )

        if has_error:
            product.status = "QUARANTINED"
            product.health_status = "ERROR"
        elif has_warn:
            product.status = "PARTIAL"
            product.health_status = "WARN"
        return product

    async def _catalog_counts(self, db: AsyncSession) -> tuple[int, int]:
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME
            )
        )
        issue_count_result = await db.execute(
            select(func.count(ResultIssueORM.id))
            .select_from(ResultIssueORM)
            .join(ResultProductORM, ResultIssueORM.product_ref_id == ResultProductORM.id)
            .where(ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME)
        )
        return (
            int(db_count_result.scalar_one() or 0),
            int(issue_count_result.scalar_one() or 0),
        )

    async def register_manifest(
        self,
        db: AsyncSession,
        *,
        manifest_path: str,
        publish_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        root = self.get_publish_root(publish_root)
        normalized_manifest_path = _normalize_path(manifest_path)
        if not os.path.isfile(normalized_manifest_path):
            raise FileNotFoundError(f"PS-InSAR manifest not found: {normalized_manifest_path}")
        if not (
            normalized_manifest_path == root
            or normalized_manifest_path.startswith(root + os.sep)
        ):
            raise ValueError("Manifest path is outside the configured PS-InSAR publish root.")

        state = await self._get_or_create_catalog_state(db, storage_root=root)
        state.status = "UPDATING"
        state.last_message = f"registering manifest: {normalized_manifest_path}"
        state.needs_rebuild = False
        await db.flush()

        manifest = await asyncio.to_thread(self._load_manifest, normalized_manifest_path)
        product = self._build_rows_from_manifest(normalized_manifest_path, manifest)

        existing_result = await db.execute(
            select(ResultProductORM).where(
                ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME,
                or_(
                    ResultProductORM.manifest_path == product.manifest_path,
                    ResultProductORM.publish_dir == product.publish_dir,
                    ResultProductORM.run_key == product.run_key,
                ),
            )
        )
        existing_products = existing_result.scalars().all()
        deleted_existing = 0
        for existing in existing_products:
            await db.delete(existing)
            deleted_existing += 1
        if existing_products:
            await db.flush()

        db.add(product)
        await db.flush()

        snapshot = await asyncio.to_thread(build_manifest_snapshot, root)
        db_count, issue_count = await self._catalog_counts(db)
        state.manifest_count = snapshot.manifest_count
        state.manifest_fingerprint = snapshot.tree_fingerprint
        state.db_count = db_count
        state.issue_count = issue_count
        state.needs_rebuild = False
        state.status = "READY" if product.health_status == "OK" else "WARN"
        state.last_incremental_scan_at = _utcnow()
        state.last_message = (
            f"registered manifest: run_key={product.run_key}, "
            f"product_id={product.product_id}, replaced={deleted_existing}"
        )
        await db.commit()

        return {
            "manifest_path": normalized_manifest_path,
            "publish_dir": product.publish_dir,
            "product_db_id": product.id,
            "product_id": product.product_id,
            "run_key": product.run_key,
            "manifest_fingerprint": snapshot.tree_fingerprint,
            "status": product.status,
            "health_status": product.health_status,
            "deleted_existing": deleted_existing,
        }

    async def rebuild_catalog(
        self,
        db: AsyncSession,
        *,
        publish_root: Optional[str] = None,
        full_rebuild: bool = True,
    ) -> Dict[str, Any]:
        root = self.get_publish_root(publish_root)
        state = await self._get_or_create_catalog_state(db, storage_root=root)
        state.status = "REBUILDING"
        state.last_message = "catalog rebuild in progress"
        state.needs_rebuild = False
        await db.commit()

        snapshot = await asyncio.to_thread(build_manifest_snapshot, root)
        manifest_paths = list(snapshot.manifest_paths)
        if full_rebuild:
            await db.execute(
                delete(ResultProductORM).where(
                    ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME
                )
            )
            await db.commit()

        created = 0
        failed = 0
        issue_count = 0
        details: List[Dict[str, Any]] = []

        for manifest_path in manifest_paths:
            try:
                manifest = await asyncio.to_thread(self._load_manifest, manifest_path)
                product = self._build_rows_from_manifest(manifest_path, manifest)
                db.add(product)
                await db.flush()
                issue_count += len(product.issues)
                created += 1
                details.append(
                    {
                        "manifest_path": manifest_path,
                        "product_id": product.product_id,
                        "status": product.status,
                    }
                )
            except Exception as exc:
                failed += 1
                details.append(
                    {
                        "manifest_path": manifest_path,
                        "status": "error",
                        "message": str(exc),
                    }
                )

        await db.commit()

        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME
            )
        )
        db_count = int(db_count_result.scalar_one() or 0)
        state.manifest_count = snapshot.manifest_count
        state.manifest_fingerprint = snapshot.tree_fingerprint
        state.db_count = db_count
        state.issue_count = issue_count + failed
        state.needs_rebuild = False
        state.status = "READY" if failed == 0 else "WARN"
        now = _utcnow()
        state.last_full_rebuild_at = now
        state.last_incremental_scan_at = now
        state.last_message = (
            f"catalog rebuild finished: manifests={snapshot.manifest_count}, "
            f"registered={created}, failed={failed}, issues={state.issue_count}"
        )
        await db.commit()

        return {
            "publish_root": root,
            "manifest_count": snapshot.manifest_count,
            "manifest_fingerprint": snapshot.tree_fingerprint,
            "registered": created,
            "failed": failed,
            "issue_count": state.issue_count,
            "details": details,
        }

    async def list_products(
        self,
        db: AsyncSession,
        *,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        safe_offset = max(0, int(offset or 0))
        stmt = select(ResultProductORM).where(ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME)
        count_stmt = select(func.count(ResultProductORM.id)).where(
            ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME
        )
        if status:
            stmt = stmt.where(ResultProductORM.status == status)
            count_stmt = count_stmt.where(ResultProductORM.status == status)
        if query:
            like_value = f"%{query.strip()}%"
            stmt = stmt.where(
                ResultProductORM.display_name.ilike(like_value)
                | ResultProductORM.product_id.ilike(like_value)
                | ResultProductORM.run_key.ilike(like_value)
            )
            count_stmt = count_stmt.where(
                ResultProductORM.display_name.ilike(like_value)
                | ResultProductORM.product_id.ilike(like_value)
                | ResultProductORM.run_key.ilike(like_value)
            )

        total_result = await db.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)
        result = await db.execute(
            stmt.order_by(
                ResultProductORM.published_at.desc().nullslast(),
                ResultProductORM.id.desc(),
            )
            .offset(safe_offset)
            .limit(safe_limit)
        )
        items = result.scalars().all()
        payload_items: List[Dict[str, Any]] = []
        for item in items:
            summary = item.summary_json or {}
            payload_items.append(
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "display_name": item.display_name,
                    "run_key": item.run_key,
                    "profile_code": item.profile_code,
                    "engine_code": item.engine_code,
                    "status": item.status,
                    "health_status": item.health_status,
                    "preview_path": item.preview_path,
                    "primary_asset_path": item.primary_asset_path,
                    "reference_date": summary.get("reference_date"),
                    "stack_dates": summary.get("stack_dates") or [],
                    "stack_size": summary.get("stack_size") or len(summary.get("stack_dates") or []),
                    "published_at": item.published_at,
                }
            )
        return {
            "items": payload_items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(payload_items) < total,
        }

    async def get_product_detail(
        self,
        db: AsyncSession,
        *,
        product_db_id: int,
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(ResultProductORM).where(ResultProductORM.id == product_db_id)
        )
        product = result.scalar_one_or_none()
        if product is None or product.catalog_name != PSINSAR_CATALOG_NAME:
            return None

        assets_result = await db.execute(
            select(ResultAssetORM)
            .where(ResultAssetORM.product_ref_id == product.id)
            .order_by(ResultAssetORM.asset_role.asc(), ResultAssetORM.id.asc())
        )
        issues_result = await db.execute(
            select(ResultIssueORM)
            .where(ResultIssueORM.product_ref_id == product.id)
            .order_by(ResultIssueORM.detected_at.desc(), ResultIssueORM.id.desc())
        )
        summary = product.summary_json or {}
        return {
            "id": product.id,
            "product_id": product.product_id,
            "catalog_name": product.catalog_name,
            "product_type": product.product_type,
            "display_name": product.display_name,
            "run_key": product.run_key,
            "profile_code": product.profile_code,
            "engine_code": product.engine_code,
            "status": product.status,
            "health_status": product.health_status,
            "publish_dir": product.publish_dir,
            "manifest_path": product.manifest_path,
            "source_primary_path": product.source_primary_path,
            "preview_path": product.preview_path,
            "primary_asset_path": product.primary_asset_path,
            "reference_date": summary.get("reference_date"),
            "reference_point": summary.get("reference_point"),
            "stack_dates": summary.get("stack_dates") or [],
            "stack_size": summary.get("stack_size") or len(summary.get("stack_dates") or []),
            "quality": summary.get("quality"),
            "summaries": summary.get("summaries"),
            "coverage_polygon": product.coverage_polygon,
            "min_lon": product.min_lon,
            "min_lat": product.min_lat,
            "max_lon": product.max_lon,
            "max_lat": product.max_lat,
            "produced_at": product.produced_at,
            "published_at": product.published_at,
            "registered_at": product.registered_at,
            "updated_at": product.updated_at,
            "assets": [
                {
                    "id": asset.id,
                    "asset_role": asset.asset_role,
                    "asset_name": asset.asset_name,
                    "relative_path": asset.relative_path,
                    "absolute_path": asset.absolute_path,
                    "format": asset.format,
                    "media_type": asset.media_type,
                    "is_required": asset.is_required,
                    "is_primary": asset.is_primary,
                    "exists_flag": asset.exists_flag,
                    "file_size": asset.file_size,
                }
                for asset in assets_result.scalars().all()
            ],
            "issues": [
                {
                    "id": issue.id,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "status": issue.status,
                    "scope": issue.scope,
                    "message": issue.message,
                    "detected_at": issue.detected_at,
                }
                for issue in issues_result.scalars().all()
            ],
        }

    async def get_catalog_status(
        self,
        db: AsyncSession,
        *,
        publish_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        root = self.get_publish_root(publish_root)
        state = await self._get_or_create_catalog_state(db, storage_root=root)
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME
            )
        )
        db_count = int(db_count_result.scalar_one() or 0)
        payload = {
            "catalog_name": state.catalog_name,
            "storage_root": state.storage_root,
            "status": state.status,
            "needs_rebuild": state.needs_rebuild,
            "manifest_count": state.manifest_count,
            "manifest_fingerprint": state.manifest_fingerprint,
            "db_count": db_count,
            "issue_count": state.issue_count,
            "last_message": state.last_message,
            "last_boot_check_at": state.last_boot_check_at,
            "last_full_rebuild_at": state.last_full_rebuild_at,
            "last_incremental_scan_at": state.last_incremental_scan_at,
        }
        await db.commit()
        return payload

    async def bootstrap_catalog_on_startup_clean(self) -> Dict[str, Any]:
        from ..database import AsyncSessionLocal
        from .job_queue_service import job_queue_service
        from .task_service import task_service

        if AsyncSessionLocal is None:
            raise RuntimeError("Database session factory is not initialized.")

        async with AsyncSessionLocal() as db:
            root = self.get_publish_root()
            state = await self._get_or_create_catalog_state(db, storage_root=root)
            snapshot = await asyncio.to_thread(build_manifest_snapshot, root)
            db_count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(
                    ResultProductORM.catalog_name == PSINSAR_CATALOG_NAME
                )
            )
            db_count = int(db_count_result.scalar_one() or 0)
            reconcile = evaluate_manifest_reconcile(
                manifest_count=snapshot.manifest_count,
                db_count=db_count,
                current_fingerprint=snapshot.tree_fingerprint,
                indexed_fingerprint=state.manifest_fingerprint,
            )
            needs_rebuild = bool(reconcile["needs_rebuild"])

            state.manifest_count = snapshot.manifest_count
            state.db_count = db_count
            state.needs_rebuild = needs_rebuild
            state.last_boot_check_at = _utcnow()
            state.status = "READY" if not needs_rebuild else "WARN"
            if not needs_rebuild:
                state.manifest_fingerprint = snapshot.tree_fingerprint
            state.last_message = (
                "boot check complete"
                if not needs_rebuild
                else (
                    f"catalog rebuild required: manifests={snapshot.manifest_count}, "
                    f"db={db_count}, reasons={','.join(reconcile['reasons'])}"
                )
            )
            await db.commit()

            queued = False
            if needs_rebuild and settings.RESULT_CATALOG_AUTO_REBUILD_ON_STARTUP:
                task_id = await task_service.create_task(
                    TASK_TYPE_REBUILD_PSINSAR_CATALOG,
                    "PS-InSAR 结果目录重建",
                    params={"publish_root": root, "full_rebuild": True},
                    db=db,
                )
                await job_queue_service.create_job(
                    JOB_TYPE_REBUILD_PSINSAR_CATALOG,
                    payload={"publish_root": root, "full_rebuild": True},
                    task_id=task_id,
                    db=db,
                )
                queued = True

            return {
                "storage_root": root,
                "manifest_count": snapshot.manifest_count,
                "current_manifest_fingerprint": snapshot.tree_fingerprint,
                "indexed_manifest_fingerprint": state.manifest_fingerprint,
                "db_count": db_count,
                "needs_rebuild": needs_rebuild,
                "reasons": reconcile["reasons"],
                "queued": queued,
            }


psinsar_catalog_service = PsinsarCatalogService()
