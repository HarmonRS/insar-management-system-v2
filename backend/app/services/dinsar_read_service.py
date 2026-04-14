from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from ..models import DinsarResultORM, ResultProductORM
from .data_service import data_service


DINSAR_CATALOG_NAME = "dinsar"
_PIL_COMPATIBLE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


@dataclass(frozen=True)
class DinsarCatalogReadRecord:
    product: ResultProductORM
    compat_row: Optional[DinsarResultORM]
    display_name: str
    image_path: Optional[str]


def _safe_path(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _is_supported_image_path(path: Optional[str]) -> bool:
    if not path or not os.path.isfile(path):
        return False
    suffix = os.path.splitext(path)[1].lower()
    return suffix in _PIL_COMPATIBLE_EXTENSIONS


class DinsarReadService:
    def _build_record(
        self,
        product: ResultProductORM,
        compat_row: Optional[DinsarResultORM],
    ) -> DinsarCatalogReadRecord:
        return DinsarCatalogReadRecord(
            product=product,
            compat_row=compat_row,
            display_name=self.get_display_name(product, compat_row),
            image_path=self.resolve_image_path(product, compat_row),
        )

    def get_display_name(
        self,
        product: ResultProductORM,
        compat_row: Optional[DinsarResultORM] = None,
    ) -> str:
        for value in (
            product.display_name,
            product.task_alias,
            product.task_name,
            getattr(compat_row, "name", None),
            product.product_id,
        ):
            text = str(value or "").strip()
            if text:
                return text
        return "unknown"

    def resolve_preview_path(
        self,
        product: ResultProductORM,
        compat_row: Optional[DinsarResultORM] = None,
    ) -> Optional[str]:
        preview_path = _safe_path(product.preview_path)
        if _is_supported_image_path(preview_path):
            return preview_path

        if compat_row is not None:
            cache_path = data_service.get_dinsar_cache_path(compat_row.id, compat_row.name)
            if _is_supported_image_path(cache_path):
                return cache_path

        return None

    def resolve_image_path(
        self,
        product: ResultProductORM,
        compat_row: Optional[DinsarResultORM] = None,
    ) -> Optional[str]:
        preview_path = self.resolve_preview_path(product, compat_row)
        if preview_path:
            return preview_path

        for candidate in (
            _safe_path(product.primary_asset_path),
            _safe_path(product.source_primary_path),
        ):
            if _is_supported_image_path(candidate):
                return candidate

        return None

    async def list_catalog_records(
        self,
        db: AsyncSession,
        *,
        labeled_only: bool = False,
        include_geom: bool = False,
    ) -> List[DinsarCatalogReadRecord]:
        stmt = select(ResultProductORM).where(
            ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
        )
        if labeled_only:
            stmt = stmt.where(ResultProductORM.user_label.is_not(None))
        if not include_geom:
            stmt = stmt.options(defer(ResultProductORM.geom))
        stmt = stmt.order_by(ResultProductORM.id.asc())

        product_result = await db.execute(stmt)
        products = product_result.scalars().all()
        product_ids = [product.product_id for product in products if str(product.product_id or "").strip()]

        compat_by_product_id: Dict[str, DinsarResultORM] = {}
        if product_ids:
            compat_result = await db.execute(
                select(DinsarResultORM)
                .where(DinsarResultORM.compat_product_id.in_(product_ids))
                .order_by(DinsarResultORM.id.asc())
            )
            for row in compat_result.scalars().all():
                product_id = str(row.compat_product_id or "").strip()
                if product_id and product_id not in compat_by_product_id:
                    compat_by_product_id[product_id] = row

        return [
            self._build_record(product, compat_by_product_id.get(product.product_id))
            for product in products
        ]

    async def count_compat_records(self, db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count(DinsarResultORM.id))
            .select_from(DinsarResultORM)
            .join(
                ResultProductORM,
                ResultProductORM.product_id == DinsarResultORM.compat_product_id,
            )
            .where(ResultProductORM.catalog_name == DINSAR_CATALOG_NAME)
        )
        return int(result.scalar_one() or 0)

    async def list_compat_records(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        include_geom: bool = False,
    ) -> List[DinsarCatalogReadRecord]:
        stmt = (
            select(DinsarResultORM, ResultProductORM)
            .join(
                ResultProductORM,
                ResultProductORM.product_id == DinsarResultORM.compat_product_id,
            )
            .where(ResultProductORM.catalog_name == DINSAR_CATALOG_NAME)
            .order_by(
                ResultProductORM.published_at.desc().nullslast(),
                ResultProductORM.id.desc(),
            )
            .offset(max(0, int(offset or 0)))
            .limit(max(1, int(limit or 1)))
        )
        if not include_geom:
            stmt = stmt.options(
                defer(DinsarResultORM.geom),
                defer(ResultProductORM.geom),
            )

        result = await db.execute(stmt)
        return [
            self._build_record(product, compat_row)
            for compat_row, product in result.all()
        ]

    async def get_compat_record(
        self,
        db: AsyncSession,
        *,
        compat_result_id: int,
        include_geom: bool = False,
    ) -> Optional[DinsarCatalogReadRecord]:
        stmt = (
            select(DinsarResultORM, ResultProductORM)
            .join(
                ResultProductORM,
                ResultProductORM.product_id == DinsarResultORM.compat_product_id,
            )
            .where(
                DinsarResultORM.id == int(compat_result_id),
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
            )
        )
        if not include_geom:
            stmt = stmt.options(
                defer(DinsarResultORM.geom),
                defer(ResultProductORM.geom),
            )

        result = await db.execute(stmt)
        row = result.first()
        if row is None:
            return None
        compat_row, product = row
        return self._build_record(product, compat_row)

    async def list_compat_records_by_ids(
        self,
        db: AsyncSession,
        *,
        compat_result_ids: List[int],
        include_geom: bool = False,
    ) -> List[DinsarCatalogReadRecord]:
        normalized_ids = sorted({
            int(value)
            for value in compat_result_ids
            if value is not None
        })
        if not normalized_ids:
            return []

        stmt = (
            select(DinsarResultORM, ResultProductORM)
            .join(
                ResultProductORM,
                ResultProductORM.product_id == DinsarResultORM.compat_product_id,
            )
            .where(
                DinsarResultORM.id.in_(normalized_ids),
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
            )
            .order_by(DinsarResultORM.id.asc())
        )
        if not include_geom:
            stmt = stmt.options(
                defer(DinsarResultORM.geom),
                defer(ResultProductORM.geom),
            )

        result = await db.execute(stmt)
        return [
            self._build_record(product, compat_row)
            for compat_row, product in result.all()
        ]

    async def get_ai_status_counts(self, db: AsyncSession) -> Dict[str, int]:
        total_labeled_res = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
                ResultProductORM.user_label.is_not(None),
            )
        )
        good_res = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
                ResultProductORM.user_label == 1,
            )
        )
        bad_res = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
                ResultProductORM.user_label == 0,
            )
        )
        return {
            "labeled_count": int(total_labeled_res.scalar_one() or 0),
            "good_count": int(good_res.scalar_one() or 0),
            "bad_count": int(bad_res.scalar_one() or 0),
        }


dinsar_read_service = DinsarReadService()
