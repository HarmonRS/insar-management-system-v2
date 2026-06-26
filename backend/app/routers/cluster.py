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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse

from ..config import settings
from ..database import get_db
from ..models.orm import (
    DinsarProductionExecutionORM,
    DinsarProductionRunItemORM,
    DinsarProductionRunORM,
)
from ..services.cluster_transport import safe_extract_zip
from ..services.cluster_transport import (
    build_cluster_input_manifest,
    iter_cluster_input_package_files,
    normalize_cluster_relative_path,
    stream_zip_files,
)

router = APIRouter()


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

async def _get_cluster_item_source_dir(
    item_id: int,
    db: AsyncSession,
) -> tuple[DinsarProductionRunItemORM, str]:
    item = await db.get(DinsarProductionRunItemORM, int(item_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Cluster item not found.")

    source_dir = os.path.normpath(str(item.source_task_dir or ""))
    if not source_dir or not os.path.isdir(source_dir):
        raise HTTPException(
            status_code=404,
            detail=f"Source task directory not found: {source_dir}",
        )
    return item, source_dir


@router.get("/cluster/input-manifest/{item_id}")
async def get_cluster_input_manifest(
    item_id: int,
    _cluster_token: None = Depends(_require_cluster_token),
    db: AsyncSession = Depends(get_db),
):
    _, source_dir = await _get_cluster_item_source_dir(item_id, db)
    manifest = build_cluster_input_manifest(source_dir)
    if int(manifest.get("file_count") or 0) <= 0:
        raise HTTPException(
            status_code=404,
            detail=f"No packageable LandSAR input files found: {source_dir}",
        )
    return manifest


@router.get("/cluster/input-file/{item_id}")
async def download_cluster_input_file(
    item_id: int,
    relative_path: str = Query(...),
    _cluster_token: None = Depends(_require_cluster_token),
    db: AsyncSession = Depends(get_db),
):
    _, source_dir = await _get_cluster_item_source_dir(item_id, db)
    normalized_rel = normalize_cluster_relative_path(relative_path)
    package_files = {
        normalize_cluster_relative_path(rel_path): abs_path
        for abs_path, rel_path in iter_cluster_input_package_files(source_dir)
    }
    source_path = package_files.get(normalized_rel)
    if not source_path or not os.path.isfile(source_path):
        raise HTTPException(status_code=404, detail="Cluster input file not found.")
    return FileResponse(
        path=source_path,
        media_type="application/octet-stream",
        filename=os.path.basename(source_path),
    )

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
    _, source_dir = await _get_cluster_item_source_dir(item_id, db)

    task_name = os.path.basename(source_dir)
    package_files = list(iter_cluster_input_package_files(source_dir))
    if not package_files:
        raise HTTPException(
            status_code=404,
            detail=f"No packageable LandSAR input files found: {source_dir}",
        )

    return StreamingResponse(
        stream_zip_files(package_files, top_level_dir=task_name),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{task_name}.zip"',
            "X-Cluster-Package-File-Count": str(len(package_files)),
        },
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
    from ..services.dinsar_production_service import dinsar_production_service

    try:
        publish_result = await rcs.publish_from_sources(db, [extract_dir])
        processed = int(publish_result.get("processed", 0) or 0)
        failed = int(publish_result.get("failed", 0) or 0)
        if processed > 0:
            await rcs.rebuild_catalog(db, full_rebuild=True)
        if processed != 1 or failed != 0:
            raise RuntimeError(
                f"expected processed=1 failed=0, got processed={processed} failed={failed}"
            )

        details = publish_result.get("details") if isinstance(publish_result, dict) else []
        detail = next(
            (
                item_detail
                for item_detail in (details or [])
                if str(item_detail.get("run_key") or "").strip() == normalized_run_key
            ),
            (details or [{}])[0] if details else {},
        )
        execution_manifest_path = str(
            detail.get("execution_manifest_path")
            or os.path.join(extract_dir, "execution_manifest.json")
        )
        current_pointer_path = str(detail.get("current_pointer_path") or "")
        if not os.path.isfile(execution_manifest_path):
            raise RuntimeError(
                f"execution manifest was not created: {execution_manifest_path}"
            )
        if not current_pointer_path or not os.path.isfile(current_pointer_path):
            raise RuntimeError(
                f"current pointer was not created: {current_pointer_path or '<empty>'}"
            )

        run_result = await db.execute(
            select(DinsarProductionRunORM).where(
                DinsarProductionRunORM.run_id == str(item.run_id or "").strip()
            )
        )
        run = run_result.scalar_one_or_none()
        if run is None:
            raise RuntimeError(f"Cluster run not found for item {item_id}: {item.run_id}")
        execution_result = await db.execute(
            select(DinsarProductionExecutionORM).where(
                DinsarProductionExecutionORM.item_id == item.id,
                DinsarProductionExecutionORM.run_key == normalized_run_key,
            )
        )
        execution = execution_result.scalar_one_or_none()
        if execution is None:
            raise RuntimeError(
                f"Cluster execution not found for item={item.id} run_key={normalized_run_key}"
            )
        execution.output_dir = extract_dir
        execution.error_message = None
        item.latest_output_dir = extract_dir
        await dinsar_production_service.mark_item_completed(
            run=run,
            item=item,
            execution=execution,
            manifest_path=execution_manifest_path,
            metrics={
                "cluster_upload": True,
                "catalog_processed": processed,
                "catalog_failed": failed,
                "current_pointer_path": current_pointer_path,
            },
            db=db,
        )
        final_status = await dinsar_production_service.finalize_cluster_run_if_complete(
            run,
            db=db,
        )
        return {
            "registered": processed > 0,
            "completed": True,
            "final_status": final_status,
            "processed": processed,
            "failed": failed,
            "catalog_path": extract_dir,
            "execution_manifest_path": execution_manifest_path,
            "current_pointer_path": current_pointer_path,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Catalog registration failed: {exc}",
        ) from exc
