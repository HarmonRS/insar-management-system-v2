import os
import shutil
import asyncio
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any

from .services.task_service import task_service
from .services.dinsar_naming import write_pair_metadata

# --- Core Logic ---

class CopyTaskExecutionError(RuntimeError):
    """Raised when a copy task cannot be completed successfully."""

def find_ps_source_to_copy(original_path: str) -> Tuple[Optional[str], Optional[bool]]:
    """
    Find PS-InSAR source path.
    Prefer the *_envi_import directory when present.
    """
    envi_import_dir = f"{original_path}_envi_import"
    if os.path.isdir(envi_import_dir):
        return envi_import_dir, True
    if os.path.exists(original_path):
        return original_path, False
    return None, None


async def _log_and_update(task_id: str, message: str, progress: Optional[int] = None) -> None:
    await task_service.add_log(task_id, "INFO", message)
    if progress is not None:
        await task_service.update_task(task_id, message=message, progress=progress)


def find_dinsar_source_to_copy(path: str) -> str:
    """
    Find D-InSAR source path.
    Prefer the envi_import subfolder when present and non-empty.
    """
    envi_path = os.path.join(path, "envi_import")
    if os.path.isdir(envi_path) and os.listdir(envi_path):
        return envi_path
    return path


async def run_ps_copy_items(task_id: str, items: List[Dict[str, Any]], dest_dir: str) -> None:
    try:
        await task_service.start_task(task_id, message="Starting PS-InSAR copy task...")
        await _log_and_update(task_id, f"PS-InSAR copy started. Dest: {dest_dir}")

        if not os.path.exists(dest_dir):
            try:
                os.makedirs(dest_dir)
                await _log_and_update(task_id, f"Created destination directory: {dest_dir}")
            except Exception as e:
                await _log_and_update(task_id, f"Failed to create destination directory: {e}")
                await task_service.update_task(task_id, status="FAILED", message=str(e))
                raise CopyTaskExecutionError(str(e))

        tasks: List[Tuple[str, bool]] = []
        for item in items:
            original_path = item.get("file_path")
            if not original_path:
                continue
            source_path, is_directory = find_ps_source_to_copy(original_path)
            if source_path:
                tasks.append((source_path, bool(is_directory)))
            else:
                await _log_and_update(task_id, f"Missing source: {original_path}")

        await _log_and_update(task_id, f"Found {len(tasks)} items to copy.")

        total = len(tasks)
        if total == 0:
            await task_service.update_task(task_id, status="COMPLETED", message="No items to copy.", progress=100)
            return

        copied_count = 0
        failed_count = 0
        for i, (source_path, is_directory) in enumerate(tasks, start=1):
            prog = int(((i - 1) / total) * 100)
            await task_service.update_task(
                task_id,
                progress=prog,
                message=f"Copying ({i}/{total}): {os.path.basename(source_path)}"
            )

            base_name = os.path.basename(source_path)
            dest_path = os.path.join(dest_dir, base_name)
            await _log_and_update(task_id, f"[{i}/{total}] Processing: {base_name}")

            try:
                if os.path.exists(dest_path):
                    await _log_and_update(task_id, " -> Skipped (already exists)")
                    continue

                if is_directory:
                    await asyncio.to_thread(shutil.copytree, source_path, dest_path)
                else:
                    await asyncio.to_thread(shutil.copy2, source_path, dest_path)

                await _log_and_update(task_id, " -> Success")
                copied_count += 1
            except PermissionError:
                await _log_and_update(task_id, " -> Failed: permission denied")
                failed_count += 1
            except Exception as e:
                await _log_and_update(task_id, f" -> Failed: {e}")
                failed_count += 1

        final_msg = f"PS-InSAR copy finished. Success {copied_count}, Failed {failed_count}"
        await _log_and_update(task_id, final_msg, progress=100)
        if failed_count > 0:
            await task_service.update_task(task_id, status="FAILED", message=final_msg, progress=100)
            raise CopyTaskExecutionError(final_msg)
        await task_service.update_task(task_id, status="COMPLETED", message=final_msg, progress=100)
    except CopyTaskExecutionError:
        raise
    except Exception as e:
        fail_msg = f"PS-InSAR copy fatal error: {e}"
        try:
            await task_service.update_task(task_id, status="FAILED", message=fail_msg)
        except Exception:
            pass
        raise CopyTaskExecutionError(fail_msg) from e


async def run_dinsar_copy_items(task_id: str, items: List[Dict[str, Any]], dest_dir: str) -> None:
    try:
        await task_service.start_task(task_id, message="Starting D-InSAR copy task...")
        await _log_and_update(task_id, f"D-InSAR copy started. Dest: {dest_dir}")

        if not os.path.exists(dest_dir):
            try:
                os.makedirs(dest_dir)
                await _log_and_update(task_id, f"Created destination directory: {dest_dir}")
            except Exception as e:
                await _log_and_update(task_id, f"Failed to create destination directory: {e}")
                await task_service.update_task(task_id, status="FAILED", message=str(e))
                raise CopyTaskExecutionError(str(e))

        tasks: List[Dict[str, Any]] = []
        for item in items:
            task_name = item.get("task_name") or item.get("task_alias") or "task"
            task_alias = item.get("task_alias") or task_name
            master_path = item.get("master_path")
            slave_path = item.get("slave_path")
            if not master_path or not slave_path:
                continue
            tasks.append(
                {
                    **item,
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "master_path": master_path,
                    "slave_path": slave_path,
                }
            )

        await _log_and_update(task_id, f"Found {len(tasks)} items to copy.")

        total = len(tasks)
        if total == 0:
            await task_service.update_task(task_id, status="COMPLETED", message="No items to copy.", progress=100)
            return

        success_count = 0
        failed_count = 0
        for i, item in enumerate(tasks, start=1):
            task_name = item["task_name"]
            task_alias = item["task_alias"]
            master_path = item["master_path"]
            slave_path = item["slave_path"]
            prog = int(((i - 1) / total) * 100)
            await task_service.update_task(task_id, progress=prog, message=f"Copying ({i}/{total}): {task_name}")

            await _log_and_update(task_id, f"[{i}/{total}] Processing: {task_name}")

            task_dir = os.path.join(dest_dir, task_alias)
            master_dir = os.path.join(task_dir, "master")
            slave_dir = os.path.join(task_dir, "slave")

            try:
                master_src_path = find_dinsar_source_to_copy(master_path)
                if not os.path.exists(master_src_path):
                    await _log_and_update(task_id, f" -> Missing master: {master_src_path}")
                    failed_count += 1
                    continue

                slave_src_path = find_dinsar_source_to_copy(slave_path)
                if not os.path.exists(slave_src_path):
                    await _log_and_update(task_id, f" -> Missing slave: {slave_src_path}")
                    failed_count += 1
                    continue

                await asyncio.to_thread(shutil.copytree, master_src_path, master_dir, dirs_exist_ok=True)
                await asyncio.to_thread(shutil.copytree, slave_src_path, slave_dir, dirs_exist_ok=True)
                await asyncio.to_thread(
                    write_pair_metadata,
                    task_dir,
                    {
                        "pair_key": item.get("pair_key"),
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "master_path": item.get("master_path"),
                        "slave_path": item.get("slave_path"),
                        "master_satellite": item.get("master_satellite"),
                        "slave_satellite": item.get("slave_satellite"),
                        "master_imaging_date": item.get("master_imaging_date"),
                        "slave_imaging_date": item.get("slave_imaging_date"),
                        "master_imaging_mode": item.get("master_imaging_mode"),
                        "slave_imaging_mode": item.get("slave_imaging_mode"),
                        "master_polarization": item.get("master_polarization"),
                        "slave_polarization": item.get("slave_polarization"),
                        "time_baseline_days": item.get("time_baseline_days"),
                        "spatial_baseline_meters": item.get("spatial_baseline_meters"),
                        "scene_pair_uid": item.get("scene_pair_uid") or item.get("pair_uid"),
                        "pair_uid": item.get("pair_uid") or item.get("scene_pair_uid"),
                        "network_run_id": item.get("network_run_id"),
                        "network_edge_id": item.get("network_edge_id"),
                        "policy_version": item.get("policy_version"),
                        "selection_strategy": item.get("selection_strategy"),
                        "copied_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    },
                )

                await _log_and_update(task_id, " -> Success")
                success_count += 1
            except PermissionError:
                await _log_and_update(task_id, " -> Failed: permission denied")
                failed_count += 1
            except Exception as e:
                await _log_and_update(task_id, f" -> Failed: {e}")
                failed_count += 1

        final_msg = f"D-InSAR copy finished. Success {success_count}, Failed {failed_count}"
        await _log_and_update(task_id, final_msg, progress=100)
        if failed_count > 0:
            await task_service.update_task(task_id, status="FAILED", message=final_msg, progress=100)
            raise CopyTaskExecutionError(final_msg)
        await task_service.update_task(task_id, status="COMPLETED", message=final_msg, progress=100)
    except CopyTaskExecutionError:
        raise
    except Exception as e:
        fail_msg = f"D-InSAR copy fatal error: {e}"
        try:
            await task_service.update_task(task_id, status="FAILED", message=fail_msg)
        except Exception:
            pass
        raise CopyTaskExecutionError(fail_msg) from e
