from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DinsarResultORM, ResultProductORM


_BBOX_TOLERANCE = 1e-6


@dataclass(frozen=True)
class _ProjectedProduct:
    product: ResultProductORM
    name: str
    file_path: str


def _safe_text(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_float(left: Any, right: Any, *, tol: float = _BBOX_TOLERANCE) -> bool:
    left_num = _safe_float(left)
    right_num = _safe_float(right)
    if left_num is None and right_num is None:
        return True
    if left_num is None or right_num is None:
        return False
    return abs(left_num - right_num) <= tol


def _same_bbox(row: DinsarResultORM, product: ResultProductORM) -> bool:
    return (
        _same_float(row.min_lon, product.min_lon)
        and _same_float(row.min_lat, product.min_lat)
        and _same_float(row.max_lon, product.max_lon)
        and _same_float(row.max_lat, product.max_lat)
    )


class DinsarCompatService:
    def _project_product(self, product: ResultProductORM) -> Optional[_ProjectedProduct]:
        file_path = _safe_text(
            product.primary_asset_path
            or product.source_primary_path
            or product.manifest_path
        )
        if not file_path:
            return None
        name = _safe_text(
            product.display_name
            or product.task_alias
            or product.task_name
            or product.product_id,
            default=product.product_id,
        )
        return _ProjectedProduct(
            product=product,
            name=name,
            file_path=file_path,
        )

    def _apply_product_fields(self, row: DinsarResultORM, projected: _ProjectedProduct) -> bool:
        product = projected.product
        changed = False
        for field_name, field_value in (
            ("compat_product_id", product.product_id),
            ("name", projected.name),
            ("file_path", projected.file_path),
            ("min_lon", product.min_lon),
            ("min_lat", product.min_lat),
            ("max_lon", product.max_lon),
            ("max_lat", product.max_lat),
            ("coverage_polygon", product.coverage_polygon),
            ("geom", product.geom),
        ):
            if getattr(row, field_name) != field_value:
                setattr(row, field_name, field_value)
                changed = True
        return changed

    def _mirror_legacy_annotations(self, row: DinsarResultORM, product: ResultProductORM) -> bool:
        changed = False
        for field_name in ("ai_score", "user_label"):
            row_value = getattr(row, field_name)
            if getattr(product, field_name) != row_value:
                setattr(product, field_name, row_value)
                changed = True
        return changed

    def _mirror_product_annotations_to_row(self, product: ResultProductORM, row: DinsarResultORM) -> bool:
        changed = False
        for field_name in ("ai_score", "user_label"):
            product_value = getattr(product, field_name)
            if getattr(row, field_name) != product_value:
                setattr(row, field_name, product_value)
                changed = True
        return changed

    async def sync_from_catalog(
        self,
        db: AsyncSession,
        *,
        prune_missing: bool = True,
    ) -> Dict[str, Any]:
        product_result = await db.execute(
            select(ResultProductORM)
            .where(ResultProductORM.catalog_name == "dinsar")
            .order_by(ResultProductORM.id.asc())
        )
        products = product_result.scalars().all()

        row_result = await db.execute(
            select(DinsarResultORM).order_by(DinsarResultORM.id.asc())
        )
        rows = row_result.scalars().all()

        by_product_id = {
            str(row.compat_product_id): row
            for row in rows
            if _safe_text(row.compat_product_id)
        }
        by_file_path = {
            _safe_text(row.file_path): row
            for row in rows
            if _safe_text(row.file_path)
        }
        by_name: Dict[str, List[DinsarResultORM]] = {}
        for row in rows:
            key = _safe_text(row.name)
            if not key:
                continue
            by_name.setdefault(key, []).append(row)

        touched_row_ids: set[int] = set()
        created = 0
        updated = 0
        skipped = 0
        mirrored_product_fields = 0

        for product in products:
            projected = self._project_product(product)
            if projected is None:
                skipped += 1
                continue

            row = by_product_id.get(product.product_id)
            if row is None:
                row = by_file_path.get(projected.file_path)
            if row is None:
                for candidate in by_name.get(projected.name, []):
                    if candidate.id in touched_row_ids:
                        continue
                    if _same_bbox(candidate, product):
                        row = candidate
                        break

            if row is None:
                row = DinsarResultORM(
                    compat_product_id=product.product_id,
                    name=projected.name,
                    file_path=projected.file_path,
                    min_lon=product.min_lon,
                    min_lat=product.min_lat,
                    max_lon=product.max_lon,
                    max_lat=product.max_lat,
                    coverage_polygon=product.coverage_polygon,
                    geom=product.geom,
                    is_cached=False,
                    ai_score=product.ai_score,
                    user_label=product.user_label,
                )
                db.add(row)
                await db.flush()
                created += 1
            else:
                if self._apply_product_fields(row, projected):
                    updated += 1

            if self._mirror_legacy_annotations(row, product):
                mirrored_product_fields += 1

            touched_row_ids.add(int(row.id))

        deleted = 0
        if prune_missing:
            for row in rows:
                if row.id in touched_row_ids:
                    continue
                await db.delete(row)
                deleted += 1

        await db.commit()
        return {
            "catalog_count": len(products),
            "compat_count": len(touched_row_ids),
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "skipped": skipped,
            "mirrored_product_fields": mirrored_product_fields,
        }

    async def sync_product_annotations_from_result(
        self,
        db: AsyncSession,
        *,
        result_id: int,
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(DinsarResultORM).where(DinsarResultORM.id == int(result_id))
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        product_id = _safe_text(row.compat_product_id)
        if not product_id:
            return None

        product_result = await db.execute(
            select(ResultProductORM).where(ResultProductORM.product_id == product_id)
        )
        product = product_result.scalar_one_or_none()
        if product is None:
            return None

        changed = self._mirror_legacy_annotations(row, product)
        if changed:
            await db.commit()

        return {
            "result_id": row.id,
            "product_id": product.product_id,
            "changed": changed,
        }

    async def sync_result_annotations_from_products(
        self,
        db: AsyncSession,
        *,
        product_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        stmt = select(ResultProductORM).where(ResultProductORM.catalog_name == "dinsar")
        normalized_product_ids = [
            str(product_id or "").strip()
            for product_id in (product_ids or [])
            if str(product_id or "").strip()
        ]
        if normalized_product_ids:
            stmt = stmt.where(ResultProductORM.product_id.in_(normalized_product_ids))

        product_result = await db.execute(stmt.order_by(ResultProductORM.id.asc()))
        products = product_result.scalars().all()
        if not products:
            return {
                "requested": len(normalized_product_ids),
                "synced": 0,
                "updated": 0,
                "missing_rows": 0,
            }

        product_keys = [product.product_id for product in products]
        row_result = await db.execute(
            select(DinsarResultORM)
            .where(DinsarResultORM.compat_product_id.in_(product_keys))
            .order_by(DinsarResultORM.id.asc())
        )
        rows = row_result.scalars().all()
        rows_by_product_id: Dict[str, DinsarResultORM] = {}
        for row in rows:
            product_id = _safe_text(row.compat_product_id)
            if product_id and product_id not in rows_by_product_id:
                rows_by_product_id[product_id] = row

        synced = 0
        updated = 0
        missing_rows = 0
        for product in products:
            row = rows_by_product_id.get(product.product_id)
            if row is None:
                missing_rows += 1
                continue
            synced += 1
            if self._mirror_product_annotations_to_row(product, row):
                updated += 1

        if updated:
            await db.commit()

        return {
            "requested": len(normalized_product_ids) or len(products),
            "synced": synced,
            "updated": updated,
            "missing_rows": missing_rows,
        }


dinsar_compat_service = DinsarCompatService()
