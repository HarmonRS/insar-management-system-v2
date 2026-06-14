from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ResultAssetORM, ResultProductORM
from .dinsar_engine_matrix import is_current_dinsar_engine


DINSAR_CATALOG_NAME = "dinsar"
PRESERVED_DIR_NAMES = {"assets", "preview", "current"}
PRESERVED_FILE_NAMES = {
    "manifest.json",
    "execution_manifest.json",
    ".dinsar_run.json",
    ".dinsar_pair.json",
    "task_manifest.json",
}


def _norm_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _is_within(child: str, parent: str) -> bool:
    child_norm = _norm_path(child)
    parent_norm = _norm_path(parent)
    if not child_norm or not parent_norm:
        return False
    try:
        return os.path.commonpath([child_norm, parent_norm]) == parent_norm
    except ValueError:
        return False


def _path_size(path: str) -> int:
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        try:
            return int(os.path.getsize(path) or 0)
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                total += int(os.path.getsize(file_path) or 0)
            except OSError:
                continue
    return total


def _required_assets_ok(assets: List[ResultAssetORM]) -> tuple[bool, List[Dict[str, Any]]]:
    missing: List[Dict[str, Any]] = []
    for asset in assets:
        if not asset.is_required:
            continue
        path = _norm_path(asset.absolute_path)
        exists = bool(path and os.path.exists(path))
        if not exists:
            missing.append(
                {
                    "asset_id": asset.id,
                    "asset_role": asset.asset_role,
                    "asset_name": asset.asset_name,
                    "absolute_path": asset.absolute_path,
                }
            )
    return len(missing) == 0, missing


def _is_preserved_path(path: str, product: ResultProductORM, asset_paths: Set[str]) -> bool:
    normalized = _norm_path(path)
    if not normalized:
        return True
    if normalized in asset_paths:
        return True
    if any(_is_within(asset_path, normalized) for asset_path in asset_paths):
        return True
    for preserve in [
        product.publish_dir,
        product.manifest_path,
        product.preview_path,
        product.primary_asset_path,
    ]:
        preserve_norm = _norm_path(preserve)
        if preserve_norm and normalized == preserve_norm:
            return True
        if preserve_norm and _is_within(preserve_norm, normalized):
            return True
    name = os.path.basename(normalized)
    if name in PRESERVED_FILE_NAMES or name in PRESERVED_DIR_NAMES:
        return True
    return False


class DinsarIntermediateCleanupService:
    async def build_product_plan(
        self,
        db: AsyncSession,
        *,
        product_db_id: int,
    ) -> Optional[Dict[str, Any]]:
        product_result = await db.execute(
            select(ResultProductORM).where(
                ResultProductORM.id == product_db_id,
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
            )
        )
        product = product_result.scalar_one_or_none()
        if product is None:
            return None

        asset_result = await db.execute(
            select(ResultAssetORM)
            .where(ResultAssetORM.product_ref_id == product.id)
            .order_by(ResultAssetORM.asset_role.asc(), ResultAssetORM.id.asc())
        )
        assets = asset_result.scalars().all()
        asset_paths = {_norm_path(asset.absolute_path) for asset in assets if _norm_path(asset.absolute_path)}
        required_ok, missing_required_assets = _required_assets_ok(assets)
        manifest_exists = bool(product.manifest_path and os.path.isfile(_norm_path(product.manifest_path)))
        current_engine = is_current_dinsar_engine(product.engine_code)

        blockers: List[str] = []
        if not current_engine:
            blockers.append("legacy_or_unknown_engine")
        if not manifest_exists:
            blockers.append("manifest_missing")
        if not required_ok:
            blockers.append("required_assets_missing")

        candidates: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        def add_candidate(path: Any, reason: str) -> None:
            normalized = _norm_path(path)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            if _is_preserved_path(normalized, product, asset_paths):
                return
            publish_dir = _norm_path(product.publish_dir)
            native_output_dir = _norm_path(product.native_output_dir)
            if publish_dir and normalized == publish_dir:
                return
            if publish_dir and not _is_within(normalized, publish_dir) and normalized != native_output_dir:
                blockers.append(f"candidate_outside_publish_dir:{normalized}")
                return
            candidates.append(
                {
                    "path": normalized,
                    "reason": reason,
                    "exists": os.path.exists(normalized),
                    "is_dir": os.path.isdir(normalized),
                    "size_bytes": _path_size(normalized),
                }
            )

        native_output_dir = _norm_path(product.native_output_dir)
        if native_output_dir:
            add_candidate(native_output_dir, "native_output_dir")

        publish_dir = _norm_path(product.publish_dir)
        if publish_dir:
            add_candidate(os.path.join(publish_dir, "native"), "managed_native_dir")
            for name in ("work", "tmp", "temp", "intermediate", "landsar_input", "landsar_output"):
                add_candidate(os.path.join(publish_dir, name), f"managed_{name}_dir")

        deletable = len(blockers) == 0
        total_size = sum(int(item.get("size_bytes") or 0) for item in candidates if item.get("exists"))
        return {
            "schema": "insar.dinsar-intermediate-cleanup-plan/v1",
            "dry_run": True,
            "deletable": deletable,
            "product": {
                "id": product.id,
                "product_id": product.product_id,
                "pair_key": product.pair_key,
                "run_key": product.run_key,
                "engine_code": product.engine_code,
                "profile_code": product.profile_code,
                "publish_dir": product.publish_dir,
                "manifest_path": product.manifest_path,
                "native_output_dir": product.native_output_dir,
            },
            "checks": {
                "manifest_exists": manifest_exists,
                "required_assets_ok": required_ok,
                "current_engine": current_engine,
                "missing_required_assets": missing_required_assets,
            },
            "blockers": blockers,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "total_size_bytes": total_size,
            "preserve": {
                "directories": sorted(PRESERVED_DIR_NAMES),
                "files": sorted(PRESERVED_FILE_NAMES),
                "asset_count": len(asset_paths),
            },
        }

    async def build_pair_plan(
        self,
        db: AsyncSession,
        *,
        pair_key: str,
    ) -> Dict[str, Any]:
        normalized_pair_key = str(pair_key or "").strip()
        result = await db.execute(
            select(ResultProductORM)
            .where(
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME,
                ResultProductORM.pair_key == normalized_pair_key,
            )
            .order_by(ResultProductORM.published_at.desc().nullslast(), ResultProductORM.id.desc())
        )
        products = result.scalars().all()
        product_plans: List[Dict[str, Any]] = []
        for product in products:
            plan = await self.build_product_plan(db, product_db_id=int(product.id))
            if plan is not None:
                product_plans.append(plan)
        return {
            "schema": "insar.dinsar-intermediate-cleanup-pair-plan/v1",
            "dry_run": True,
            "pair_key": normalized_pair_key,
            "product_count": len(product_plans),
            "deletable_product_count": sum(1 for item in product_plans if item.get("deletable")),
            "total_size_bytes": sum(int(item.get("total_size_bytes") or 0) for item in product_plans),
            "products": product_plans,
        }


dinsar_intermediate_cleanup_service = DinsarIntermediateCleanupService()
