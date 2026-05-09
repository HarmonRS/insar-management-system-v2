import os
import shutil
import asyncio
import tempfile
import zipfile
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
    D-InSAR pairing/distribution works on the raw source product directory.
    """
    return path


def _resolve_orbit_dest_path(
    orbit_dir: str,
    role: str,
    source_path: str,
    used_dest_paths: Dict[str, str],
) -> str:
    base_name = os.path.basename(source_path)
    dest_path = os.path.join(orbit_dir, base_name)
    dest_key = os.path.normcase(os.path.abspath(dest_path))
    source_key = os.path.normcase(os.path.abspath(source_path))
    if dest_key not in used_dest_paths or used_dest_paths[dest_key] == source_key:
        return dest_path

    role_path = os.path.join(orbit_dir, f"{role}_{base_name}")
    role_key = os.path.normcase(os.path.abspath(role_path))
    if role_key not in used_dest_paths or used_dest_paths[role_key] == source_key:
        return role_path

    stem, ext = os.path.splitext(base_name)
    counter = 2
    while True:
        numbered_path = os.path.join(orbit_dir, f"{role}_{stem}_{counter}{ext}")
        numbered_key = os.path.normcase(os.path.abspath(numbered_path))
        if numbered_key not in used_dest_paths:
            return numbered_path
        counter += 1


async def _copy_dinsar_orbit_files(
    task_id: str,
    item: Dict[str, Any],
    task_dir: str,
    include_orbit_files: bool,
) -> List[Dict[str, Any]]:
    if not include_orbit_files:
        return []

    orbit_dir = os.path.join(task_dir, "orbit")
    copied_by_source: Dict[str, str] = {}
    used_dest_paths: Dict[str, str] = {}
    orbit_entries: List[Dict[str, Any]] = []
    for role, key in (
        ("master", "master_orbit_file_path"),
        ("slave", "slave_orbit_file_path"),
    ):
        raw_path = item.get(key)
        if not raw_path:
            await _log_and_update(task_id, f" -> {role} orbit missing in catalog metadata")
            orbit_entries.append(
                {
                    "role": role,
                    "source_path": None,
                    "copied": False,
                    "reason": "missing_orbit_path",
                }
            )
            continue

        source_path = os.path.normpath(os.path.abspath(str(raw_path)))
        if not os.path.isfile(source_path):
            await _log_and_update(task_id, f" -> {role} orbit file not found: {source_path}")
            orbit_entries.append(
                {
                    "role": role,
                    "source_path": source_path,
                    "copied": False,
                    "reason": "source_file_not_found",
                }
            )
            continue

        await asyncio.to_thread(os.makedirs, orbit_dir, exist_ok=True)
        source_key = os.path.normcase(source_path)
        if source_key in copied_by_source:
            dest_path = copied_by_source[source_key]
        else:
            dest_path = _resolve_orbit_dest_path(orbit_dir, role, source_path, used_dest_paths)
            await asyncio.to_thread(shutil.copy2, source_path, dest_path)
            copied_by_source[source_key] = dest_path
            used_dest_paths[os.path.normcase(os.path.abspath(dest_path))] = source_key

        orbit_entries.append(
            {
                "role": role,
                "source_path": source_path,
                "relative_path": os.path.relpath(dest_path, start=task_dir),
                "copied": True,
            }
        )
    return orbit_entries


def _zip_task_directory(task_dir: str, zip_path: str) -> None:
    parent_dir = os.path.dirname(task_dir)
    temp_zip_path = f"{zip_path}.tmp"
    try:
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
        with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for root, dirs, files in os.walk(task_dir):
                rel_root = os.path.relpath(root, start=parent_dir)
                for dirname in dirs:
                    arcname = os.path.join(rel_root, dirname).replace(os.sep, "/") + "/"
                    archive.writestr(arcname, "")
                for filename in files:
                    file_path = os.path.join(root, filename)
                    arcname = os.path.join(rel_root, filename).replace(os.sep, "/")
                    archive.write(file_path, arcname)
        os.replace(temp_zip_path, zip_path)
    finally:
        if os.path.exists(temp_zip_path):
            try:
                os.remove(temp_zip_path)
            except OSError:
                pass


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


async def run_dinsar_copy_items(
    task_id: str,
    items: List[Dict[str, Any]],
    dest_dir: str,
    *,
    include_orbit_files: bool = False,
    export_zip: bool = False,
) -> None:
    try:
        await task_service.start_task(task_id, message="Starting D-InSAR copy task...")
        await _log_and_update(task_id, f"D-InSAR copy started. Dest: {dest_dir}")
        await _log_and_update(
            task_id,
            (
                "D-InSAR copy options: "
                f"include_orbit_files={include_orbit_files}, "
                f"export_zip={export_zip}"
            ),
        )

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

            staging_root: Optional[str] = None
            try:
                if export_zip:
                    staging_root = await asyncio.to_thread(
                        tempfile.mkdtemp,
                        prefix="._dinsar_zip_",
                        dir=dest_dir,
                    )
                    task_dir = os.path.join(staging_root, task_alias)
                    zip_path = os.path.join(dest_dir, f"{task_alias}.zip")
                else:
                    task_dir = os.path.join(dest_dir, task_alias)
                    zip_path = None
                master_dir = os.path.join(task_dir, "master")
                slave_dir = os.path.join(task_dir, "slave")

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
                orbit_entries = await _copy_dinsar_orbit_files(
                    task_id,
                    item,
                    task_dir,
                    include_orbit_files,
                )
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
                        "scene_center_distance_meters": item.get("scene_center_distance_meters"),
                        "package_format": "zip" if export_zip else "folder",
                        "include_orbit_files": bool(include_orbit_files),
                        "master_orbit_file_path": item.get("master_orbit_file_path"),
                        "slave_orbit_file_path": item.get("slave_orbit_file_path"),
                        "orbit_files": orbit_entries,
                        "scene_pair_uid": item.get("scene_pair_uid") or item.get("pair_uid"),
                        "pair_uid": item.get("pair_uid") or item.get("scene_pair_uid"),
                        "network_run_id": item.get("network_run_id"),
                        "network_edge_id": item.get("network_edge_id"),
                        "policy_version": item.get("policy_version"),
                        "selection_strategy": item.get("selection_strategy"),
                        "copied_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    },
                )
                if export_zip and zip_path:
                    await asyncio.to_thread(_zip_task_directory, task_dir, zip_path)
                    await _log_and_update(task_id, f" -> ZIP: {zip_path}")

                await _log_and_update(task_id, " -> Success")
                success_count += 1
            except PermissionError:
                await _log_and_update(task_id, " -> Failed: permission denied")
                failed_count += 1
            except Exception as e:
                await _log_and_update(task_id, f" -> Failed: {e}")
                failed_count += 1
            finally:
                if staging_root:
                    await asyncio.to_thread(shutil.rmtree, staging_root, ignore_errors=True)

        final_msg = (
            f"D-InSAR copy finished. Mode {'zip' if export_zip else 'folder'}. "
            f"Success {success_count}, Failed {failed_count}"
        )
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
