from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import ResultAssetORM, ResultCatalogStateORM, ResultIssueORM, ResultProductORM
from .admin_region_lookup_service import lookup_admin_region_for_point
from .sbas_insar_production_service import sbas_insar_production_service


SBAS_INSAR_CATALOG_NAME = "sbas_insar"
JOB_TYPE_REBUILD_SBAS_INSAR_CATALOG = "REBUILD_SBAS_INSAR_CATALOG"
TASK_TYPE_REBUILD_SBAS_INSAR_CATALOG = "REBUILD_SBAS_INSAR_CATALOG"

_READY_STATUSES = {"PRODUCTS_READY", "MONITOR_POINTS_READY", "WORKFLOW_COMPLETED"}
_REQUIRED_ASSET_ROLES = {"primary_geotiff", "quality_geotiff"}

_CORE_ASSETS = (
    ("run_manifest", "Run manifest", "run_manifest.json", True, False),
    ("stack_manifest", "Stack manifest", "stack_manifest.json", True, False),
    ("workflow_summary", "Workflow summary", "workflow_summary.json", False, False),
    ("product_summary", "Product summary", "product_summary.json", False, False),
    ("quality_summary", "Quality summary", "quality_summary.json", False, False),
    ("monitor_points_summary", "Monitor points summary", "monitor_points_summary.json", False, False),
    (
        "point_vector_summary",
        "LOS point-vector summary",
        "publish/vectors/los_rate_points_summary.json",
        False,
        False,
    ),
    (
        "point_vector_geojson_gz",
        "LOS point-vector GeoJSON.gz",
        "publish/vectors/los_rate_points.geojson.gz",
        False,
        False,
    ),
    (
        "primary_geocoded_preview",
        "LOS velocity preview, toward radar positive",
        "publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png",
        False,
        True,
    ),
    (
        "quality_geocoded_preview",
        "LOS velocity sigma preview",
        "publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png",
        False,
        False,
    ),
    (
        "primary_geotiff",
        "LOS velocity GeoTIFF, toward radar positive",
        "publish/geotiff/los_rate_toward_m_per_year.tif",
        True,
        True,
    ),
    (
        "alternate_geotiff",
        "LOS velocity GeoTIFF, away from radar positive",
        "publish/geotiff/los_rate_away_m_per_year.tif",
        False,
        False,
    ),
    (
        "quality_geotiff",
        "LOS velocity sigma GeoTIFF",
        "publish/geotiff/los_sigma_m_per_year.tif",
        True,
        False,
    ),
    (
        "primary_rgb_geotiff",
        "LOS velocity RGB GeoTIFF",
        "publish/geotiff/los_rate_toward_m_per_year.hls.geo_rgb.tif",
        False,
        False,
    ),
    (
        "quality_rgb_geotiff",
        "LOS velocity sigma RGB GeoTIFF",
        "publish/geotiff/los_sigma_m_per_year.cc.geo_rgb.tif",
        False,
        False,
    ),
    ("gamma_phase_rate", "Gamma phase-rate GeoTIFF", "publish/geotiff/ts_rate_rad_per_year.tif", False, False),
    ("gamma_sigma_rate", "Gamma sigma-rate GeoTIFF", "publish/geotiff/sigma_rate_rad_per_year.tif", False, False),
    ("height_correction", "Height correction GeoTIFF", "publish/geotiff/hgt_correction_m.tif", False, False),
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return payload if isinstance(payload, dict) else {}


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return _read_json(path)
    except Exception:
        return {}


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _stable_digest(*parts: Any, length: int = 20) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _asset_format(path: str) -> Optional[str]:
    lowered = path.lower()
    if lowered.endswith(".geojson.gz"):
        return "geojson.gz"
    ext = Path(path).suffix.lower()
    return {
        ".bmp": "bmp",
        ".csv": "csv",
        ".geo": "gamma_binary",
        ".gz": "gzip",
        ".json": "json",
        ".log": "log",
        ".png": "png",
        ".sh": "shell",
        ".tif": "geotiff",
        ".tiff": "geotiff",
        ".txt": "text",
    }.get(ext)


def _media_type(path: str) -> Optional[str]:
    lowered = path.lower()
    if lowered.endswith(".geojson.gz"):
        return "application/gzip"
    ext = Path(path).suffix.lower()
    explicit = {
        ".bmp": "image/bmp",
        ".csv": "text/csv",
        ".gz": "application/gzip",
        ".json": "application/json",
        ".log": "text/plain",
        ".png": "image/png",
        ".sh": "text/x-shellscript",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".txt": "text/plain",
    }
    return explicit.get(ext) or mimetypes.guess_type(path)[0]


def _bbox_polygon(
    min_lon: Optional[float],
    min_lat: Optional[float],
    max_lon: Optional[float],
    max_lat: Optional[float],
):
    if None in (min_lon, min_lat, max_lon, max_lat):
        return None
    if min_lon == max_lon or min_lat == max_lat:
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


def _stack_dates_from_manifest(stack_manifest: dict[str, Any], manifest: dict[str, Any], stack: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for source in (
        stack_manifest.get("dates"),
        stack.get("dates"),
        manifest.get("dates"),
        [scene.get("date") for scene in stack_manifest.get("scenes") or [] if isinstance(scene, dict)],
        [scene.get("date") for scene in manifest.get("scenes") or [] if isinstance(scene, dict)],
    ):
        if not isinstance(source, list):
            continue
        for item in source:
            text = str(item or "").strip()
            if text:
                values.append(text)
    return sorted(dict.fromkeys(values))


class SbasInsarCatalogService:
    def get_run_root(self) -> str:
        root = Path(settings.GAMMA_SBAS_WORK_ROOT or Path(settings.BACKEND_DIR) / "runtime" / "sbas_insar_production")
        run_root = root / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        return _normalize_path(run_root)

    def _iter_run_manifest_paths(self, run_root: Optional[str] = None) -> list[str]:
        root = Path(run_root or self.get_run_root())
        if not root.is_dir():
            return []
        return [
            _normalize_path(path)
            for path in sorted(root.glob("*/run_manifest.json"))
            if self._is_publish_ready(path.parent, _safe_read_json(path))
        ]

    def _is_publish_ready(self, run_dir: Path, manifest: dict[str, Any]) -> bool:
        status = str(manifest.get("status") or "").strip().upper()
        required_outputs_ready = all(
            (run_dir / relative_path).is_file()
            for role, _name, relative_path, is_required, _is_primary in _CORE_ASSETS
            if role in _REQUIRED_ASSET_ROLES and is_required
        )
        return status in _READY_STATUSES or required_outputs_ready

    def _tree_fingerprint(self, manifest_paths: list[str]) -> str:
        records: list[dict[str, Any]] = []
        for raw_path in manifest_paths:
            manifest_path = Path(raw_path)
            run_dir = manifest_path.parent
            tracked_paths = [
                manifest_path,
                run_dir / "product_summary.json",
                run_dir / "quality_summary.json",
                run_dir / "monitor_points_summary.json",
            ]
            tracked_paths.extend(run_dir / relative_path for _role, _name, relative_path, _required, _primary in _CORE_ASSETS)
            for path in tracked_paths:
                if not path.exists():
                    continue
                stat = path.stat()
                records.append(
                    {
                        "path": str(path.relative_to(run_dir)).replace("\\", "/"),
                        "run": run_dir.name,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
        encoded = json.dumps(records, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    async def _get_or_create_catalog_state(self, db: AsyncSession, *, storage_root: str) -> ResultCatalogStateORM:
        result = await db.execute(
            select(ResultCatalogStateORM).where(ResultCatalogStateORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = ResultCatalogStateORM(
                catalog_name=SBAS_INSAR_CATALOG_NAME,
                product_family="timeseries",
                storage_root=storage_root,
                status="READY",
                needs_rebuild=False,
            )
            db.add(state)
            await db.flush()
        elif state.storage_root != storage_root:
            state.storage_root = storage_root
        if state.product_family != "timeseries":
            state.product_family = "timeseries"
        return state

    def _asset_row(
        self,
        run_dir: Path,
        *,
        role: str,
        name: str,
        relative_path: str,
        is_required: bool,
        is_primary: bool,
    ) -> ResultAssetORM:
        absolute_path = run_dir / relative_path
        exists = absolute_path.is_file()
        return ResultAssetORM(
            asset_role=role[:32],
            asset_name=name,
            relative_path=relative_path.replace("\\", "/"),
            absolute_path=_normalize_path(absolute_path),
            format=_asset_format(relative_path),
            media_type=_media_type(relative_path),
            is_required=is_required,
            is_primary=is_primary,
            exists_flag=exists,
            file_size=absolute_path.stat().st_size if exists else None,
            srid=4326 if (
                (relative_path.lower().endswith((".tif", ".tiff")) and "/geotiff/" in relative_path)
                or relative_path.lower().endswith(".geojson.gz")
            ) else None,
        )

    def _monitor_asset_rows(self, run_dir: Path) -> list[ResultAssetORM]:
        monitor_dir = run_dir / "publish" / "monitor_points"
        if not monitor_dir.is_dir():
            return []
        rows: list[ResultAssetORM] = []
        for path in sorted(monitor_dir.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in {".png", ".csv", ".json"}:
                continue
            role = {
                ".png": "monitor_point_curve",
                ".csv": "monitor_point_csv",
                ".json": "monitor_point_metadata",
            }[suffix]
            relative_path = str(path.relative_to(run_dir)).replace("\\", "/")
            rows.append(
                self._asset_row(
                    run_dir,
                    role=role,
                    name=path.name,
                    relative_path=relative_path,
                    is_required=False,
                    is_primary=False,
                )
            )
        return rows

    def _build_product(self, manifest_path: str) -> ResultProductORM:
        manifest_file = Path(manifest_path)
        run_dir = manifest_file.parent
        manifest = _read_json(manifest_file)
        if not self._is_publish_ready(run_dir, manifest):
            raise ValueError(f"run is not publish-ready: {manifest.get('status') or 'UNKNOWN'}")

        detail = sbas_insar_production_service.get_run_detail(run_dir.name)
        coverage = detail.get("geographic_coverage") or {}
        stack_manifest = _safe_read_json(run_dir / "stack_manifest.json")
        product_summary = _safe_read_json(run_dir / "product_summary.json")
        quality_summary = _safe_read_json(run_dir / "quality_summary.json")
        monitor_summary = _safe_read_json(run_dir / "monitor_points_summary.json")
        point_vector_summary = _safe_read_json(run_dir / "publish" / "vectors" / "los_rate_points_summary.json")
        workflow_summary = _safe_read_json(run_dir / "workflow_summary.json")

        bbox = coverage.get("bbox") or {}
        min_lon = _safe_float(bbox.get("min_lon"))
        min_lat = _safe_float(bbox.get("min_lat"))
        max_lon = _safe_float(bbox.get("max_lon"))
        max_lat = _safe_float(bbox.get("max_lat"))
        poly = _bbox_polygon(min_lon, min_lat, max_lon, max_lat)

        run_id = str(manifest.get("run_id") or run_dir.name).strip() or run_dir.name
        stack = stack_manifest.get("stack") or manifest.get("stack") or {}
        stack_id = str(manifest.get("stack_id") or stack_manifest.get("stack_id") or stack.get("stack_id") or "").strip()
        stack_dates = _stack_dates_from_manifest(stack_manifest, manifest, stack)
        reference_date = str(
            manifest.get("reference_date")
            or stack.get("reference_date")
            or (manifest.get("coregistration") or {}).get("reference_date")
            or ""
        ).strip() or None
        display_name = stack_id or f"Gamma SBAS {run_id}"
        product_id = str(manifest.get("product_id") or "").strip() or f"gamma_sbas_{run_id}"
        if len(product_id) > 64:
            product_id = f"gamma_sbas_{_stable_digest(product_id, run_dir, length=32)}"

        assets: list[ResultAssetORM] = [
            self._asset_row(
                run_dir,
                role=role,
                name=name,
                relative_path=relative_path,
                is_required=is_required,
                is_primary=is_primary,
            )
            for role, name, relative_path, is_required, is_primary in _CORE_ASSETS
        ]
        assets.extend(self._monitor_asset_rows(run_dir))
        preview_asset = next((asset for asset in assets if asset.asset_role == "primary_geocoded_preview" and asset.exists_flag), None)
        primary_asset = next((asset for asset in assets if asset.asset_role == "primary_geotiff" and asset.exists_flag), None)
        missing_required = [asset for asset in assets if asset.is_required and not asset.exists_flag]

        produced_at = (
            _parse_datetime(monitor_summary.get("generated_at"))
            or _parse_datetime(product_summary.get("generated_at"))
            or _parse_datetime(workflow_summary.get("generated_at"))
            or _parse_datetime(manifest.get("updated_at"))
            or _parse_datetime(manifest.get("created_at"))
        )
        center = coverage.get("center") or {}
        admin_region = coverage.get("admin_region") or lookup_admin_region_for_point(center.get("lon"), center.get("lat"))
        scene_count = (
            _safe_int(manifest.get("scene_count"))
            or len(stack_manifest.get("scenes") or [])
            or len(stack_dates)
        )

        summary_json = {
            "schema": "insar.gamma-sbas-result-catalog-summary/v1",
            "run_id": run_id,
            "stack_id": stack_id or None,
            "stack": stack,
            "reference_date": reference_date,
            "stack_dates": stack_dates,
            "stack_size": len(stack_dates),
            "date_start": stack_dates[0] if stack_dates else None,
            "date_end": stack_dates[-1] if stack_dates else None,
            "scene_count": scene_count,
            "pair_count": _safe_int(manifest.get("pair_count")),
            "status": manifest.get("status"),
            "next_stage": manifest.get("next_stage"),
            "los_sign_convention": (
                product_summary.get("los_sign_convention")
                or "toward radar positive; away from radar negative"
            ),
            "default_los_product": product_summary.get("default_los_product") or "los_rate_toward_m_per_year",
            "center": center or None,
            "admin_region": admin_region,
            "geographic_coverage": coverage,
            "quality": quality_summary,
            "monitor_points": monitor_summary,
            "point_vector": point_vector_summary,
            "workflow": {
                "status": ((manifest.get("workflow") or {}).get("status")),
                "summary": ((manifest.get("workflow") or {}).get("summary")) or workflow_summary,
            },
            "source_run_dir": str(run_dir),
        }

        product = ResultProductORM(
            product_id=product_id,
            catalog_name=SBAS_INSAR_CATALOG_NAME,
            product_family="timeseries",
            product_type="sbas_insar",
            display_name=display_name,
            task_name="Gamma SBAS-InSAR",
            task_alias=run_id,
            stack_key=stack_id or run_id,
            run_key=run_id,
            profile_code=str(stack.get("relative_orbit") or manifest.get("relative_orbit") or "").strip() or None,
            engine_code="gamma",
            engine_version=str((manifest.get("engine") or {}).get("version") or "").strip() or None,
            package_schema=str(manifest.get("schema") or "").strip() or "insar.gamma-sbas-run/v1",
            package_layout="gamma_sbas_expert_workflow_run",
            processor_code="gamma_ipta_sbas",
            runtime_id=settings.GAMMA_SBAS_RUNTIME_ID,
            status="READY" if not missing_required else "INCOMPLETE",
            health_status="OK" if not missing_required else "WARN",
            publish_dir=_normalize_path(run_dir / "publish"),
            manifest_path=_normalize_path(manifest_file),
            source_primary_path=primary_asset.absolute_path if primary_asset else None,
            native_output_dir=_normalize_path(run_dir),
            preview_path=preview_asset.absolute_path if preview_asset else None,
            primary_asset_path=primary_asset.absolute_path if primary_asset else None,
            summary_json=summary_json,
            tags_json={
                "sensor": stack.get("satellite") or manifest.get("platform"),
                "orbit_direction": stack.get("orbit_direction") or manifest.get("direction"),
                "product": "Gamma SBAS",
                "admin_region": (admin_region or {}).get("display_name") if isinstance(admin_region, dict) else None,
            },
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            geom=from_shape(poly, srid=4326) if poly is not None else None,
            coverage_polygon=(coverage.get("geojson") or coverage.get("scene_footprints_geojson")),
            produced_at=produced_at,
            published_at=produced_at,
        )
        for asset in assets:
            product.assets.append(asset)
            if asset.is_required and not asset.exists_flag:
                product.issues.append(
                    ResultIssueORM(
                        asset=asset,
                        issue_code="MISSING_REQUIRED_ASSET",
                        severity="ERROR",
                        status="OPEN",
                        scope="file",
                        message=f"Required SBAS asset is missing: {asset.relative_path}",
                    )
                )
        if not preview_asset:
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_PREVIEW",
                    severity="WARN",
                    status="OPEN",
                    scope="product",
                    message="Primary geocoded preview PNG is missing.",
                )
            )
        if poly is None:
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_COVERAGE",
                    severity="WARN",
                    status="OPEN",
                    scope="product",
                    message="No valid EPSG:4326 geographic coverage bbox was found.",
                )
            )
        return product

    async def rebuild_catalog(self, db: AsyncSession, *, full_rebuild: bool = True) -> dict[str, Any]:
        run_root = self.get_run_root()
        manifest_paths = await asyncio.to_thread(self._iter_run_manifest_paths, run_root)
        fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
        state = await self._get_or_create_catalog_state(db, storage_root=run_root)
        state.status = "REBUILDING"
        state.needs_rebuild = False
        state.last_message = "SBAS catalog rebuild in progress"
        await db.commit()

        if full_rebuild:
            await db.execute(delete(ResultProductORM).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME))
            await db.commit()

        registered = 0
        failed = 0
        issue_count = 0
        details: list[dict[str, Any]] = []
        for manifest_path in manifest_paths:
            try:
                product = await asyncio.to_thread(self._build_product, manifest_path)
                product_issue_count = len(product.issues)
                product_id = product.product_id
                product_status = product.status
                db.add(product)
                await db.flush()
                await db.commit()
                registered += 1
                issue_count += product_issue_count
                details.append(
                    {
                        "manifest_path": manifest_path,
                        "product_id": product_id,
                        "status": product_status,
                        "issues": product_issue_count,
                    }
                )
            except Exception as exc:
                await db.rollback()
                failed += 1
                issue_count += 1
                details.append({"manifest_path": manifest_path, "status": "error", "message": str(exc)})

        await db.commit()
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        )
        db_count = int(db_count_result.scalar_one() or 0)
        state = await self._get_or_create_catalog_state(db, storage_root=run_root)
        state.manifest_count = len(manifest_paths)
        state.manifest_fingerprint = fingerprint
        state.db_count = db_count
        state.issue_count = issue_count
        state.needs_rebuild = False
        state.status = "READY" if failed == 0 else "WARN"
        now = _utcnow()
        state.last_full_rebuild_at = now
        state.last_incremental_scan_at = now
        state.last_message = (
            f"SBAS catalog rebuild finished: runs={len(manifest_paths)}, "
            f"registered={registered}, failed={failed}, issues={issue_count}"
        )
        await db.commit()
        return {
            "catalog_name": SBAS_INSAR_CATALOG_NAME,
            "storage_root": run_root,
            "run_count": len(manifest_paths),
            "manifest_count": len(manifest_paths),
            "manifest_fingerprint": fingerprint,
            "registered": registered,
            "failed": failed,
            "issue_count": issue_count,
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
        admin_region: Optional[str] = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        safe_offset = max(0, int(offset or 0))
        stmt = select(ResultProductORM).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        count_stmt = select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        if status:
            stmt = stmt.where(ResultProductORM.status == status)
            count_stmt = count_stmt.where(ResultProductORM.status == status)
        if query:
            like_value = f"%{query.strip()}%"
            predicate = or_(
                ResultProductORM.display_name.ilike(like_value),
                ResultProductORM.product_id.ilike(like_value),
                ResultProductORM.run_key.ilike(like_value),
                ResultProductORM.stack_key.ilike(like_value),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        if admin_region:
            like_value = f"%{admin_region.strip()}%"
            predicate = or_(
                cast(ResultProductORM.summary_json, String).ilike(like_value),
                cast(ResultProductORM.tags_json, String).ilike(like_value),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        total_result = await db.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)
        result = await db.execute(
            stmt.order_by(ResultProductORM.published_at.desc().nullslast(), ResultProductORM.id.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        items: list[dict[str, Any]] = []
        for product in result.scalars().all():
            summary = product.summary_json or {}
            items.append(
                {
                    "id": product.id,
                    "product_id": product.product_id,
                    "display_name": product.display_name,
                    "run_key": product.run_key,
                    "stack_key": product.stack_key,
                    "engine_code": product.engine_code,
                    "processor_code": product.processor_code,
                    "runtime_id": product.runtime_id,
                    "status": product.status,
                    "health_status": product.health_status,
                    "preview_path": product.preview_path,
                    "primary_asset_path": product.primary_asset_path,
                    "reference_date": summary.get("reference_date"),
                    "date_start": summary.get("date_start"),
                    "date_end": summary.get("date_end"),
                    "stack_dates": summary.get("stack_dates") or [],
                    "stack_size": summary.get("stack_size") or len(summary.get("stack_dates") or []),
                    "scene_count": summary.get("scene_count"),
                    "pair_count": summary.get("pair_count"),
                    "los_sign_convention": summary.get("los_sign_convention"),
                    "center": summary.get("center") or ((summary.get("geographic_coverage") or {}).get("center")),
                    "admin_region": summary.get("admin_region") or ((summary.get("geographic_coverage") or {}).get("admin_region")),
                    "min_lon": product.min_lon,
                    "min_lat": product.min_lat,
                    "max_lon": product.max_lon,
                    "max_lat": product.max_lat,
                    "published_at": product.published_at,
                }
            )
        return {
            "items": items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < total,
        }

    async def get_product_detail(self, db: AsyncSession, *, product_db_id: int) -> Optional[dict[str, Any]]:
        result = await db.execute(select(ResultProductORM).where(ResultProductORM.id == product_db_id))
        product = result.scalar_one_or_none()
        if product is None or product.catalog_name != SBAS_INSAR_CATALOG_NAME:
            return None
        assets_result = await db.execute(
            select(ResultAssetORM)
            .where(ResultAssetORM.product_ref_id == product.id)
            .order_by(ResultAssetORM.is_primary.desc(), ResultAssetORM.asset_role.asc(), ResultAssetORM.id.asc())
        )
        issues_result = await db.execute(
            select(ResultIssueORM)
            .where(ResultIssueORM.product_ref_id == product.id)
            .order_by(ResultIssueORM.severity.asc(), ResultIssueORM.id.asc())
        )
        summary = product.summary_json or {}
        return {
            "id": product.id,
            "product_id": product.product_id,
            "catalog_name": product.catalog_name,
            "product_type": product.product_type,
            "display_name": product.display_name,
            "run_key": product.run_key,
            "run_id": summary.get("run_id") or product.run_key,
            "stack_key": product.stack_key,
            "profile_code": product.profile_code,
            "engine_code": product.engine_code,
            "engine_version": product.engine_version,
            "package_schema": product.package_schema,
            "package_layout": product.package_layout,
            "processor_code": product.processor_code,
            "runtime_id": product.runtime_id,
            "status": product.status,
            "health_status": product.health_status,
            "publish_dir": product.publish_dir,
            "manifest_path": product.manifest_path,
            "source_primary_path": product.source_primary_path,
            "native_output_dir": product.native_output_dir,
            "preview_path": product.preview_path,
            "primary_asset_path": product.primary_asset_path,
            "reference_date": summary.get("reference_date"),
            "date_start": summary.get("date_start"),
            "date_end": summary.get("date_end"),
            "stack_dates": summary.get("stack_dates") or [],
            "stack_size": summary.get("stack_size") or len(summary.get("stack_dates") or []),
            "scene_count": summary.get("scene_count"),
            "pair_count": summary.get("pair_count"),
            "los_sign_convention": summary.get("los_sign_convention"),
            "default_los_product": summary.get("default_los_product"),
            "quality": summary.get("quality"),
            "monitor_points": summary.get("monitor_points"),
            "point_vector": summary.get("point_vector"),
            "workflow": summary.get("workflow"),
            "geographic_coverage": summary.get("geographic_coverage"),
            "center": summary.get("center") or ((summary.get("geographic_coverage") or {}).get("center")),
            "admin_region": summary.get("admin_region") or ((summary.get("geographic_coverage") or {}).get("admin_region")),
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
                    "srid": asset.srid,
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

    async def get_asset(self, db: AsyncSession, *, product_db_id: int, asset_id: int) -> Optional[ResultAssetORM]:
        result = await db.execute(
            select(ResultAssetORM)
            .join(ResultProductORM, ResultProductORM.id == ResultAssetORM.product_ref_id)
            .where(
                ResultProductORM.id == product_db_id,
                ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME,
                ResultAssetORM.id == asset_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_catalog_status(self, db: AsyncSession) -> dict[str, Any]:
        run_root = self.get_run_root()
        manifest_paths = await asyncio.to_thread(self._iter_run_manifest_paths, run_root)
        fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
        state = await self._get_or_create_catalog_state(db, storage_root=run_root)
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        )
        db_count = int(db_count_result.scalar_one() or 0)
        needs_rebuild = (
            state.manifest_count != len(manifest_paths)
            or state.db_count != db_count
            or state.manifest_fingerprint != fingerprint
        )
        state.manifest_count = len(manifest_paths)
        state.db_count = db_count
        state.needs_rebuild = needs_rebuild
        state.last_incremental_scan_at = _utcnow()
        state.status = "WARN" if needs_rebuild else "READY"
        state.last_message = (
            f"SBAS catalog rebuild required: runs={len(manifest_paths)}, db={db_count}"
            if needs_rebuild
            else "SBAS catalog is in sync"
        )
        await db.commit()
        return {
            "catalog_name": state.catalog_name,
            "product_family": state.product_family,
            "storage_root": state.storage_root,
            "status": state.status,
            "needs_rebuild": state.needs_rebuild,
            "run_count": len(manifest_paths),
            "manifest_count": state.manifest_count,
            "manifest_fingerprint": state.manifest_fingerprint,
            "current_manifest_fingerprint": fingerprint,
            "db_count": db_count,
            "issue_count": state.issue_count,
            "last_message": state.last_message,
            "last_boot_check_at": state.last_boot_check_at,
            "last_full_rebuild_at": state.last_full_rebuild_at,
            "last_incremental_scan_at": state.last_incremental_scan_at,
        }

    async def bootstrap_catalog_on_startup_clean(self) -> dict[str, Any]:
        from ..database import AsyncSessionLocal

        if AsyncSessionLocal is None:
            raise RuntimeError("Database session factory is not initialized.")
        async with AsyncSessionLocal() as db:
            run_root = self.get_run_root()
            manifest_paths = await asyncio.to_thread(self._iter_run_manifest_paths, run_root)
            fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
            state = await self._get_or_create_catalog_state(db, storage_root=run_root)
            db_count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
            )
            db_count = int(db_count_result.scalar_one() or 0)
            needs_rebuild = (
                state.manifest_count != len(manifest_paths)
                or state.db_count != db_count
                or state.manifest_fingerprint != fingerprint
            )
            state.last_boot_check_at = _utcnow()
            await db.commit()

            rebuilt = False
            result: dict[str, Any] = {}
            if needs_rebuild and settings.RESULT_CATALOG_AUTO_REBUILD_ON_STARTUP:
                result = await self.rebuild_catalog(db, full_rebuild=True)
                rebuilt = True
                db_count = int(result.get("registered") or db_count)
            else:
                state.manifest_count = len(manifest_paths)
                state.db_count = db_count
                state.manifest_fingerprint = fingerprint if not needs_rebuild else state.manifest_fingerprint
                state.needs_rebuild = needs_rebuild
                state.status = "WARN" if needs_rebuild else "READY"
                state.last_message = "SBAS boot check complete"
                await db.commit()

            return {
                "storage_root": run_root,
                "manifest_count": len(manifest_paths),
                "current_manifest_fingerprint": fingerprint,
                "indexed_manifest_fingerprint": state.manifest_fingerprint,
                "db_count": db_count,
                "needs_rebuild": needs_rebuild and not rebuilt,
                "rebuilt": rebuilt,
                "queued": False,
                "registered": result.get("registered"),
                "failed": result.get("failed"),
            }


sbas_insar_catalog_service = SbasInsarCatalogService()
