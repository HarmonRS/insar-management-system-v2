from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .manifest_inventory_service import manifest_inventory_service
from .result_catalog_service import result_catalog_service
from .task_service import task_service


def _dedupe_existing_dirs(paths: Optional[List[str]]) -> tuple[List[str], List[str]]:
    normalized: List[str] = []
    ignored: List[str] = []
    for raw_path in paths or []:
        path = os.path.normpath(os.path.abspath(str(raw_path or "").strip()))
        if not path:
            continue
        if not os.path.isdir(path):
            ignored.append(path)
            continue
        if path in normalized:
            continue
        normalized.append(path)
    return normalized, ignored


class DinsarScanService:
    async def _update_task(self, task_id: Optional[str], *, message: str, progress: int) -> None:
        if not task_id:
            return
        await task_service.update_task(
            task_id,
            message=message,
            progress=max(0, min(100, int(progress))),
        )

    async def run_scan(
        self,
        db: AsyncSession,
        *,
        source_directories: Optional[List[str]] = None,
        publish_root: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_dirs, ignored_dirs = _dedupe_existing_dirs(source_directories)

        publish_result = None
        if source_dirs:
            await self._update_task(
                task_id,
                message="正在将 D-InSAR 原始结果发布为 package...",
                progress=20,
            )
            publish_result = await result_catalog_service.publish_from_sources(
                db,
                source_dirs,
                publish_root=publish_root,
            )

        await self._update_task(
            task_id,
            message="正在同步 manifest inventory...",
            progress=55,
        )
        inventory_result = await manifest_inventory_service.sync_manifest_roots(db)

        await self._update_task(
            task_id,
            message="正在重建 D-InSAR catalog 与兼容视图...",
            progress=75,
        )
        rebuild_result = await result_catalog_service.rebuild_catalog(
            db,
            publish_root=publish_root,
            full_rebuild=True,
        )

        compat_result = rebuild_result.get("compat_sync")
        compat_error = rebuild_result.get("compat_error")
        return {
            "source_directories": source_dirs,
            "ignored_source_directories": ignored_dirs,
            "publish": publish_result,
            "manifest_inventory": inventory_result,
            "rebuild": rebuild_result,
            "compat_sync": compat_result,
            "compat_error": compat_error,
            "message": (
                "D-InSAR unified scan completed"
                if not compat_error
                else f"D-InSAR unified scan completed with compat error: {compat_error}"
            ),
        }


dinsar_scan_service = DinsarScanService()
