"""
Cluster data-transport endpoints for LandSAR distributed processing.

Provides HTTP-based input materialize (download) and result upload
so that remote Windows workers can pull Task_Pool pair data and
push finished products back to the main server without SMB / UNC.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.orm import DinsarProductionRunItemORM
from ..services.cluster_transport import safe_extract_zip

router = APIRouter()


def _cluster_materialize_temp_dir() -> str:
    explicit = str(os.environ.get("CLUSTER_MATERIALIZE_TEMP_DIR") or "").strip()
    if not explicit:
        explicit = str(
            getattr(settings, "CLUSTER_MATERIALIZE_TEMP_DIR", "") or ""
        ).strip()
    if explicit:
        return os.path.normpath(explicit)
    task_pool = str(getattr(settings, "TASK_POOL_ROOT", "") or "").strip()
    if task_pool:
        return os.path.normpath(os.path.join(task_pool, "_cluster_temp"))
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "runtime", "_cluster_temp"
        )
    )


def _cluster_shared_token() -> str:
    return str(os.environ.get("CLUSTER_SHARED_TOKEN") or "").strip()


def _require_cluster_token(
    x_cluster_token: Optional[str] = Header(default=None),
) -> None:
    expected = _cluster_shared_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="CLUSTER_SHARED_TOKEN is not configured.",
        )
    if x_cluster_token != expected:
        raise HTTPException(status_code=403, detail="Invalid cluster token.")


# ---------------------------------------------------------------------------
# Download input package
# ---------------------------------------------------------------------------

@router.get("/cluster/input-package/{item_id}")
async def download_cluster_input_package(
    item_id: int,
    _cluster_token: None = Depends(_require_cluster_token),
    db: AsyncSession = Depends(get_db),
):
    """Package a cluster item's source-task directory as a zip and stream it.

    The remote worker calls this when *source_task_dir* does not exist
    locally.  The zip mirrors the Task_Pool layout:

        Task_YYYYMMDD_YYYYMMDD/
          master/ ...
          slave/  ...
          orbit/  ...
          pair_metadata.json
    """
    item = await db.get(DinsarProductionRunItemORM, int(item_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Cluster item not found.")

    source_dir = os.path.normpath(str(item.source_task_dir or ""))
    if not source_dir or not os.path.isdir(source_dir):
        raise HTTPException(
            status_code=404,
            detail=f"Source task directory not found: {source_dir}",
        )

    task_name = os.path.basename(source_dir)
    parent_dir = os.path.dirname(source_dir)
    temp_root = _cluster_materialize_temp_dir()
    os.makedirs(temp_root, exist_ok=True)

    package_root = tempfile.mkdtemp(
        prefix=f"cluster_input_{item_id}_",
        dir=temp_root,
    )
    tmp_base = os.path.join(package_root, task_name)
    try:
        zip_path = shutil.make_archive(
            tmp_base,
            "zip",
            root_dir=parent_dir,
            base_dir=task_name,
        )
    except Exception as exc:
        try:
            shutil.rmtree(package_root)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create input package: {exc}",
        )

    def _cleanup():
        try:
            if os.path.isdir(package_root):
                shutil.rmtree(package_root)
        except Exception:
            pass

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"{task_name}.zip",
        background=BackgroundTask(_cleanup),
    )


# ---------------------------------------------------------------------------
# Upload result package
# ---------------------------------------------------------------------------

@router.post("/cluster/upload-result/{item_id}")
async def upload_cluster_result(
    item_id: int,
    run_id: str = Form(...),
    run_key: str = Form(...),
    result_zip: UploadFile = File(...),
    _cluster_token: None = Depends(_require_cluster_token),
    db: AsyncSession = Depends(get_db),
):
    """Receive a result zip from a cluster worker and register it in the
    D-InSAR catalog.

    The zip is extracted under the cluster item's standard
    ``results_root_dir/runs/<run_key>`` directory and then
    *result_catalog_service.publish_from_sources* is called so the product
    is immediately visible on the main server.
    """
    item = await db.get(DinsarProductionRunItemORM, int(item_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Cluster item not found.")

    normalized_run_id = str(run_id or "").strip()
    if normalized_run_id != str(item.run_id or "").strip():
        raise HTTPException(
            status_code=400,
            detail="run_id does not match item.",
        )

    publish_root = os.path.normpath(
        str(getattr(settings, "DINSAR_PRODUCT_DIR", "") or "")
    )
    if not publish_root or not os.path.isdir(publish_root):
        raise HTTPException(
            status_code=500,
            detail="DINSAR_PRODUCT_DIR is not configured.",
        )

    normalized_run_key = str(run_key or "").strip()
    if not normalized_run_key:
        raise HTTPException(status_code=400, detail="run_key is required.")

    item_results_root = os.path.normpath(str(item.results_root_dir or ""))
    if not item_results_root:
        raise HTTPException(
            status_code=500,
            detail="Cluster item results_root_dir is not configured.",
        )
    publish_root_abs = os.path.abspath(publish_root)
    item_results_root_abs = os.path.abspath(item_results_root)
    if item_results_root_abs != publish_root_abs and not item_results_root_abs.startswith(
        publish_root_abs + os.sep
    ):
        raise HTTPException(
            status_code=400,
            detail="Cluster item results_root_dir is outside DINSAR_PRODUCT_DIR.",
        )

    extract_dir = os.path.join(item_results_root_abs, "runs", normalized_run_key)
    os.makedirs(os.path.dirname(extract_dir), exist_ok=True)

    extract_parent = os.path.dirname(extract_dir)
    os.makedirs(extract_parent, exist_ok=True)
    upload_root = tempfile.mkdtemp(
        prefix=f"cluster_upload_{item_id}_",
        dir=extract_parent,
    )
    tmp_extract = os.path.join(upload_root, "extract")
    tmp_zip = os.path.join(upload_root, "upload.zip")
    backup_dir: Optional[str] = None
    try:
        with open(tmp_zip, "wb") as fh:
            while chunk := await result_zip.read(8 * 1024 * 1024):  # 8 MiB
                fh.write(chunk)

        os.makedirs(tmp_extract, exist_ok=True)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            safe_extract_zip(zf, tmp_extract)

        if os.path.isdir(extract_dir):
            backup_dir = f"{extract_dir}._replace_backup"
            if os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir)
            os.replace(extract_dir, backup_dir)
        os.replace(tmp_extract, extract_dir)
        if backup_dir and os.path.isdir(backup_dir):
            shutil.rmtree(backup_dir)
            backup_dir = None
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid zip archive.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        if backup_dir and os.path.isdir(backup_dir) and not os.path.exists(extract_dir):
            try:
                os.replace(backup_dir, extract_dir)
                backup_dir = None
            except Exception:
                pass
        raise
    finally:
        try:
            if os.path.isdir(upload_root):
                shutil.rmtree(upload_root)
        except Exception:
            pass
        try:
            if backup_dir and os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir)
        except Exception:
            pass

    # ----- catalog registration ------------------------------------------------
    from ..services.result_catalog_service import result_catalog_service as rcs

    try:
        publish_result = await rcs.publish_from_sources(db, [extract_dir])
        processed = int(publish_result.get("processed", 0) or 0)
        if processed > 0:
            await rcs.rebuild_catalog(db, full_rebuild=True)
        return {
            "registered": processed > 0,
            "processed": processed,
            "failed": int(publish_result.get("failed", 0) or 0),
            "catalog_path": extract_dir,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Catalog registration failed: {exc}",
        ) from exc
