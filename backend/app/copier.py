import os
import shutil
import asyncio
import tempfile
import zipfile
import json
import hashlib
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


def _directory_has_entries(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        with os.scandir(path) as entries:
            return any(True for _ in entries)
    except OSError:
        return False


def _is_existing_dinsar_folder_complete(task_dir: str) -> bool:
    return (
        _directory_has_entries(os.path.join(task_dir, "master"))
        and _directory_has_entries(os.path.join(task_dir, "slave"))
    )


def _is_existing_dinsar_zip_complete(zip_path: str) -> bool:
    try:
        return os.path.isfile(zip_path) and os.path.getsize(zip_path) > 0
    except OSError:
        return False


def _build_dinsar_pair_metadata(
    item: Dict[str, Any],
    task_name: str,
    task_alias: str,
    package_format: str,
    include_orbit_files: bool,
    orbit_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
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
        "package_format": package_format,
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
    }


def _safe_bundle_entry_name(source_path: str, prefix: str) -> str:
    base_name = os.path.basename(os.path.normpath(str(source_path or ""))) or "item"
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(source_path)).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}_{base_name}"


def _bundle_relative_scene_path(source_path: str) -> str:
    normalized = os.path.normpath(os.path.abspath(str(source_path)))
    return os.path.join("data", _safe_bundle_entry_name(normalized, "scene")).replace(os.sep, "/")


def _normalize_bundle_relative_path(path: Any) -> str:
    return str(path or "").strip().replace("\\", "/")


def _source_bundle_id_number(value: Any, prefix: str) -> int:
    text = str(value or "")
    marker = f"{prefix}_"
    if not text.startswith(marker):
        return 0
    try:
        return int(text[len(marker):])
    except ValueError:
        return 0


def _next_source_bundle_id(records: List[Dict[str, Any]], id_key: str, prefix: str) -> int:
    max_number = 0
    for record in records:
        max_number = max(max_number, _source_bundle_id_number(record.get(id_key), prefix))
    return max(max_number, len(records)) + 1


def _source_bundle_pair_keys_from_item(item: Dict[str, Any]) -> List[str]:
    keys: List[str] = []

    def add(label: str, value: Any) -> None:
        text = str(value or "").strip()
        if text:
            keys.append(f"{label}:{text}")

    add("uid", item.get("scene_pair_uid") or item.get("pair_uid"))
    add("pair_key", item.get("pair_key"))
    network_run_id = str(item.get("network_run_id") or "").strip()
    network_edge_id = str(item.get("network_edge_id") or "").strip()
    if network_run_id and network_edge_id:
        keys.append(f"network:{network_run_id}:{network_edge_id}")

    master_path = item.get("master_path")
    slave_path = item.get("slave_path")
    if master_path and slave_path:
        master_abs = os.path.normcase(os.path.normpath(os.path.abspath(str(master_path))))
        slave_abs = os.path.normcase(os.path.normpath(os.path.abspath(str(slave_path))))
        keys.append(f"source_paths:{master_abs}|{slave_abs}")
        keys.append(
            "bundle_paths:"
            f"{_bundle_relative_scene_path(str(master_path))}|"
            f"{_bundle_relative_scene_path(str(slave_path))}"
        )
    return keys


def _source_bundle_pair_keys_from_pair(pair: Dict[str, Any]) -> List[str]:
    keys: List[str] = []

    identity_key = str(pair.get("identity_key") or "").strip()
    if identity_key:
        keys.append(identity_key)

    def add(label: str, value: Any) -> None:
        text = str(value or "").strip()
        if text:
            keys.append(f"{label}:{text}")

    add("uid", pair.get("scene_pair_uid") or pair.get("pair_uid"))
    add("pair_key", pair.get("pair_key"))
    network_run_id = str(pair.get("network_run_id") or "").strip()
    network_edge_id = str(pair.get("network_edge_id") or "").strip()
    if network_run_id and network_edge_id:
        keys.append(f"network:{network_run_id}:{network_edge_id}")

    master_source_path = pair.get("master_source_path")
    slave_source_path = pair.get("slave_source_path")
    if master_source_path and slave_source_path:
        master_abs = os.path.normcase(os.path.normpath(os.path.abspath(str(master_source_path))))
        slave_abs = os.path.normcase(os.path.normpath(os.path.abspath(str(slave_source_path))))
        keys.append(f"source_paths:{master_abs}|{slave_abs}")

    master_data = _normalize_bundle_relative_path(pair.get("master_data"))
    slave_data = _normalize_bundle_relative_path(pair.get("slave_data"))
    if master_data and slave_data:
        keys.append(f"bundle_paths:{master_data}|{slave_data}")

    return keys


def _read_json_file(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {}
    return payload


def _copy_source_into_bundle(source_path: str, dest_path: str, skip_existing: bool) -> str:
    if skip_existing and os.path.exists(dest_path):
        return "skipped"
    if os.path.exists(dest_path):
        if os.path.isdir(dest_path):
            shutil.rmtree(dest_path)
        else:
            os.remove(dest_path)
    parent_dir = os.path.dirname(dest_path)
    os.makedirs(parent_dir, exist_ok=True)
    if os.path.isdir(source_path):
        shutil.copytree(source_path, dest_path)
    else:
        shutil.copy2(source_path, dest_path)
    return "copied"


def _write_json_file(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


async def run_dinsar_source_bundle_items(
    task_id: str,
    items: List[Dict[str, Any]],
    dest_dir: str,
    *,
    include_orbit_files: bool = True,
    skip_existing: bool = True,
    max_items: Optional[int] = None,
) -> None:
    try:
        if max_items is not None:
            try:
                max_items = int(max_items)
            except (TypeError, ValueError):
                max_items = None
            if max_items is not None and max_items <= 0:
                max_items = None

        await task_service.start_task(task_id, message="Starting D-InSAR source bundle export...")
        await _log_and_update(task_id, f"D-InSAR source bundle export started. Dest: {dest_dir}")
        await _log_and_update(
            task_id,
            (
                "D-InSAR source bundle options: "
                f"include_orbit_files={include_orbit_files}, "
                f"skip_existing={skip_existing}, "
                f"max_items={max_items if max_items is not None else 'unlimited'}"
            ),
        )

        await asyncio.to_thread(os.makedirs, dest_dir, exist_ok=True)
        data_dir = os.path.join(dest_dir, "data")
        orbit_dir = os.path.join(dest_dir, "orbit")
        await asyncio.to_thread(os.makedirs, data_dir, exist_ok=True)
        if include_orbit_files:
            await asyncio.to_thread(os.makedirs, orbit_dir, exist_ok=True)

        pairs_path = os.path.join(dest_dir, "pairs.json")
        manifest_path = os.path.join(dest_dir, "manifest.json")
        existing_pairs_payload = await asyncio.to_thread(_read_json_file, pairs_path)
        existing_manifest_payload = await asyncio.to_thread(_read_json_file, manifest_path)

        raw_existing_pairs = existing_pairs_payload.get("pairs")
        existing_pairs: List[Dict[str, Any]] = [
            dict(pair)
            for pair in raw_existing_pairs
            if isinstance(pair, dict)
        ] if isinstance(raw_existing_pairs, list) else []

        raw_existing_scenes = existing_manifest_payload.get("scenes")
        existing_scene_list: List[Dict[str, Any]] = [
            dict(scene)
            for scene in raw_existing_scenes
            if isinstance(scene, dict)
        ] if isinstance(raw_existing_scenes, list) else []

        raw_existing_orbits = existing_manifest_payload.get("orbits")
        existing_orbit_list: List[Dict[str, Any]] = [
            dict(orbit)
            for orbit in raw_existing_orbits
            if isinstance(orbit, dict)
        ] if isinstance(raw_existing_orbits, list) else []

        existing_pair_keys = {
            key
            for pair in existing_pairs
            for key in _source_bundle_pair_keys_from_pair(pair)
        }
        if existing_pairs:
            await _log_and_update(task_id, f"Existing source bundle pairs found: {len(existing_pairs)}")

        candidate_tasks: List[Dict[str, Any]] = []
        for item in items:
            task_name = item.get("task_name") or item.get("task_alias") or "task"
            task_alias = item.get("task_alias") or task_name
            master_path = item.get("master_path")
            slave_path = item.get("slave_path")
            if not master_path or not slave_path:
                continue
            candidate_tasks.append(
                {
                    **item,
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "master_path": master_path,
                    "slave_path": slave_path,
                }
            )

        skipped_existing_pair_count = 0
        exportable_tasks: List[Dict[str, Any]] = []
        for item in candidate_tasks:
            pair_keys = _source_bundle_pair_keys_from_item(item)
            item["identity_key"] = pair_keys[0] if pair_keys else None
            if existing_pair_keys and any(key in existing_pair_keys for key in pair_keys):
                skipped_existing_pair_count += 1
                continue
            exportable_tasks.append(item)

        if max_items is not None:
            deferred_count = max(0, len(exportable_tasks) - max_items)
            tasks = exportable_tasks[:max_items]
        else:
            deferred_count = 0
            tasks = exportable_tasks

        total_pairs = len(tasks)
        await _log_and_update(
            task_id,
            (
                f"Found {len(candidate_tasks)} candidate pairs; "
                f"{skipped_existing_pair_count} already exported; "
                f"{total_pairs} new pairs selected for this run."
            ),
        )
        if total_pairs == 0:
            await task_service.update_task(
                task_id,
                status="COMPLETED",
                message=(
                    "No new source bundle pairs to export. "
                    f"Existing pairs: {len(existing_pairs)}; "
                    f"already exported in request: {skipped_existing_pair_count}."
                ),
                progress=100,
            )
            return

        scene_records: List[Dict[str, Any]] = []
        scene_entries_by_source: Dict[str, Dict[str, Any]] = {}
        scene_entries_by_relative: Dict[str, Dict[str, Any]] = {}
        scene_entries_by_id: Dict[str, Dict[str, Any]] = {}
        orbit_records: List[Dict[str, Any]] = []
        orbit_entries_by_source: Dict[str, Dict[str, Any]] = {}
        orbit_entries_by_relative: Dict[str, Dict[str, Any]] = {}
        orbit_entries_by_id: Dict[str, Dict[str, Any]] = {}
        next_scene_number = _next_source_bundle_id(existing_scene_list, "scene_id", "scene")
        next_orbit_number = _next_source_bundle_id(existing_orbit_list, "orbit_id", "orbit")
        next_pair_number = _next_source_bundle_id(existing_pairs, "pair_id", "pair")
        copy_units_by_target: Dict[str, Tuple[str, str, str, str]] = {}
        pairs: List[Dict[str, Any]] = []
        missing_sources: List[Dict[str, Any]] = []

        def register_scene_entry(raw_entry: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal next_scene_number
            entry = dict(raw_entry)
            source_path = entry.get("source_path")
            source_key = ""
            if source_path:
                source_path = os.path.normpath(os.path.abspath(str(source_path)))
                source_key = os.path.normcase(source_path)
                entry["source_path"] = source_path
            relative_path = _normalize_bundle_relative_path(entry.get("relative_path"))
            if not relative_path and source_path:
                relative_path = _bundle_relative_scene_path(source_path)
            if relative_path:
                entry["relative_path"] = relative_path

            existing = None
            if source_key:
                existing = scene_entries_by_source.get(source_key)
            if existing is None and relative_path:
                existing = scene_entries_by_relative.get(relative_path)
            scene_id = str(entry.get("scene_id") or "").strip()
            if existing is None and scene_id:
                existing = scene_entries_by_id.get(scene_id)
            if existing is not None:
                if source_path and not existing.get("source_path"):
                    existing["source_path"] = source_path
                    scene_entries_by_source[source_key] = existing
                if relative_path and not existing.get("relative_path"):
                    existing["relative_path"] = relative_path
                    scene_entries_by_relative[relative_path] = existing
                return existing

            if not scene_id:
                scene_id = f"scene_{next_scene_number:04d}"
                entry["scene_id"] = scene_id
                next_scene_number += 1
            else:
                next_scene_number = max(next_scene_number, _source_bundle_id_number(scene_id, "scene") + 1)

            scene_records.append(entry)
            scene_entries_by_id[scene_id] = entry
            if source_key:
                scene_entries_by_source[source_key] = entry
            if relative_path:
                scene_entries_by_relative[relative_path] = entry
            return entry

        def register_orbit_entry(raw_entry: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal next_orbit_number
            entry = dict(raw_entry)
            source_path = entry.get("source_path")
            source_key = ""
            if source_path:
                source_path = os.path.normpath(os.path.abspath(str(source_path)))
                source_key = os.path.normcase(source_path)
                entry["source_path"] = source_path
            relative_path = _normalize_bundle_relative_path(entry.get("relative_path"))
            if not relative_path and source_path:
                relative_path = os.path.join("orbit", _safe_bundle_entry_name(source_path, "orbit")).replace(os.sep, "/")
            if relative_path:
                entry["relative_path"] = relative_path

            existing = None
            if source_key:
                existing = orbit_entries_by_source.get(source_key)
            if existing is None and relative_path:
                existing = orbit_entries_by_relative.get(relative_path)
            orbit_id = str(entry.get("orbit_id") or "").strip()
            if existing is None and orbit_id:
                existing = orbit_entries_by_id.get(orbit_id)
            if existing is not None:
                if source_path and not existing.get("source_path"):
                    existing["source_path"] = source_path
                    orbit_entries_by_source[source_key] = existing
                if relative_path and not existing.get("relative_path"):
                    existing["relative_path"] = relative_path
                    orbit_entries_by_relative[relative_path] = existing
                return existing

            if not orbit_id:
                orbit_id = f"orbit_{next_orbit_number:04d}"
                entry["orbit_id"] = orbit_id
                next_orbit_number += 1
            else:
                next_orbit_number = max(next_orbit_number, _source_bundle_id_number(orbit_id, "orbit") + 1)
            if not isinstance(entry.get("used_by"), list):
                entry["used_by"] = []

            orbit_records.append(entry)
            orbit_entries_by_id[orbit_id] = entry
            if source_key:
                orbit_entries_by_source[source_key] = entry
            if relative_path:
                orbit_entries_by_relative[relative_path] = entry
            return entry

        def add_copy_unit(kind: str, unit_id: str, source_path: Optional[str], relative_path: Optional[str]) -> None:
            if not source_path or not relative_path:
                return
            target_path = os.path.join(dest_dir, _normalize_bundle_relative_path(relative_path))
            target_key = os.path.normcase(os.path.abspath(target_path))
            copy_units_by_target.setdefault(target_key, (kind, unit_id, source_path, target_path))

        def add_orbit_usage(entry: Dict[str, Any], scene_id: str, role: str) -> None:
            used_by = entry.setdefault("used_by", [])
            usage = {"scene_id": scene_id, "role": role}
            if not any(
                str(item.get("scene_id")) == scene_id and str(item.get("role")) == role
                for item in used_by
                if isinstance(item, dict)
            ):
                used_by.append(usage)

        for scene in existing_scene_list:
            register_scene_entry(scene)

        for orbit in existing_orbit_list:
            register_orbit_entry(orbit)

        for pair in existing_pairs:
            for role in ("master", "slave"):
                scene_relative = _normalize_bundle_relative_path(pair.get(f"{role}_data"))
                if scene_relative:
                    register_scene_entry(
                        {
                            "scene_id": pair.get(f"{role}_scene_id"),
                            "source_path": pair.get(f"{role}_source_path"),
                            "relative_path": scene_relative,
                        }
                    )
                orbit_relative = _normalize_bundle_relative_path(pair.get(f"{role}_orbit"))
                if orbit_relative:
                    register_orbit_entry(
                        {
                            "orbit_id": pair.get(f"{role}_orbit_id"),
                            "source_path": pair.get(f"{role}_orbit_source_path"),
                            "relative_path": orbit_relative,
                            "used_by": [
                                {
                                    "scene_id": pair.get(f"{role}_scene_id"),
                                    "role": role,
                                }
                            ] if pair.get(f"{role}_scene_id") else [],
                        }
                    )

        def ensure_scene_entry(scene_path: str, role_item: Dict[str, Any], prefix: str) -> Dict[str, Any]:
            source_path = os.path.normpath(os.path.abspath(str(scene_path)))
            source_key = os.path.normcase(source_path)
            relative_path = _bundle_relative_scene_path(source_path)
            existing = scene_entries_by_source.get(source_key) or scene_entries_by_relative.get(relative_path)
            if existing:
                if not existing.get("source_path"):
                    existing["source_path"] = source_path
                    scene_entries_by_source[source_key] = existing
                if not existing.get("relative_path"):
                    existing["relative_path"] = relative_path
                    scene_entries_by_relative[relative_path] = existing
                add_copy_unit("scene", existing["scene_id"], source_path, existing["relative_path"])
                return existing
            entry = register_scene_entry(
                {
                    "source_path": source_path,
                    "relative_path": relative_path,
                    "satellite": role_item.get("satellite"),
                    "imaging_date": role_item.get("imaging_date"),
                    "imaging_mode": role_item.get("imaging_mode"),
                    "polarization": role_item.get("polarization"),
                }
            )
            add_copy_unit("scene", entry["scene_id"], source_path, entry["relative_path"])
            return entry

        def ensure_orbit_entry(orbit_path: Optional[str], scene_id: str, role: str) -> Optional[Dict[str, Any]]:
            if not include_orbit_files or not orbit_path:
                return None
            source_path = os.path.normpath(os.path.abspath(str(orbit_path)))
            source_key = os.path.normcase(source_path)
            relative_path = os.path.join("orbit", _safe_bundle_entry_name(source_path, "orbit")).replace(os.sep, "/")
            existing = orbit_entries_by_source.get(source_key) or orbit_entries_by_relative.get(relative_path)
            if existing:
                if not existing.get("source_path"):
                    existing["source_path"] = source_path
                    orbit_entries_by_source[source_key] = existing
                if not existing.get("relative_path"):
                    existing["relative_path"] = relative_path
                    orbit_entries_by_relative[relative_path] = existing
                add_orbit_usage(existing, scene_id, role)
                add_copy_unit("orbit", existing["orbit_id"], source_path, existing["relative_path"])
                return existing
            entry = register_orbit_entry(
                {
                    "source_path": source_path,
                    "relative_path": relative_path,
                }
            )
            add_orbit_usage(entry, scene_id, role)
            add_copy_unit("orbit", entry["orbit_id"], source_path, entry["relative_path"])
            return entry

        for item in tasks:
            task_name = item["task_name"]
            task_alias = item["task_alias"]
            master_entry = ensure_scene_entry(
                item["master_path"],
                {
                    "satellite": item.get("master_satellite"),
                    "imaging_date": item.get("master_imaging_date"),
                    "imaging_mode": item.get("master_imaging_mode"),
                    "polarization": item.get("master_polarization"),
                },
                "scene",
            )
            slave_entry = ensure_scene_entry(
                item["slave_path"],
                {
                    "satellite": item.get("slave_satellite"),
                    "imaging_date": item.get("slave_imaging_date"),
                    "imaging_mode": item.get("slave_imaging_mode"),
                    "polarization": item.get("slave_polarization"),
                },
                "scene",
            )
            master_orbit = ensure_orbit_entry(
                item.get("master_orbit_file_path"),
                master_entry["scene_id"],
                "master",
            )
            slave_orbit = ensure_orbit_entry(
                item.get("slave_orbit_file_path"),
                slave_entry["scene_id"],
                "slave",
            )
            master_source_path = os.path.normpath(os.path.abspath(str(item["master_path"])))
            slave_source_path = os.path.normpath(os.path.abspath(str(item["slave_path"])))
            pairs.append(
                {
                    "pair_id": f"pair_{next_pair_number:04d}",
                    "identity_key": item.get("identity_key"),
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": item.get("pair_key"),
                    "scene_pair_uid": item.get("scene_pair_uid") or item.get("pair_uid"),
                    "pair_uid": item.get("pair_uid") or item.get("scene_pair_uid"),
                    "master_scene_id": master_entry["scene_id"],
                    "slave_scene_id": slave_entry["scene_id"],
                    "master_source_path": master_source_path,
                    "slave_source_path": slave_source_path,
                    "master_data": master_entry["relative_path"],
                    "slave_data": slave_entry["relative_path"],
                    "master_orbit_id": master_orbit.get("orbit_id") if master_orbit else None,
                    "slave_orbit_id": slave_orbit.get("orbit_id") if slave_orbit else None,
                    "master_orbit_source_path": master_orbit.get("source_path") if master_orbit else None,
                    "slave_orbit_source_path": slave_orbit.get("source_path") if slave_orbit else None,
                    "master_orbit": master_orbit.get("relative_path") if master_orbit else None,
                    "slave_orbit": slave_orbit.get("relative_path") if slave_orbit else None,
                    "master_imaging_date": item.get("master_imaging_date"),
                    "slave_imaging_date": item.get("slave_imaging_date"),
                    "time_baseline_days": item.get("time_baseline_days"),
                    "scene_center_distance_meters": item.get("scene_center_distance_meters"),
                    "spatial_baseline_meters": item.get("spatial_baseline_meters"),
                    "network_run_id": item.get("network_run_id"),
                    "network_edge_id": item.get("network_edge_id"),
                    "policy_version": item.get("policy_version"),
                    "selection_strategy": item.get("selection_strategy"),
                }
            )
            next_pair_number += 1

        scene_list = sorted(
            scene_records,
            key=lambda entry: (_source_bundle_id_number(entry.get("scene_id"), "scene"), str(entry.get("scene_id") or "")),
        )
        orbit_list = sorted(
            orbit_records,
            key=lambda entry: (_source_bundle_id_number(entry.get("orbit_id"), "orbit"), str(entry.get("orbit_id") or "")),
        )
        copy_units = list(copy_units_by_target.values())

        copied_count = 0
        skipped_count = 0
        failed_count = 0
        total_units = max(1, len(copy_units))
        for index, (kind, unit_id, source_path, target_path) in enumerate(copy_units, start=1):
            prog = int(((index - 1) / total_units) * 90)
            await task_service.update_task(
                task_id,
                progress=prog,
                message=f"Bundling {kind} ({index}/{total_units}): {unit_id}",
            )
            await _log_and_update(task_id, f"[{index}/{total_units}] Bundling {kind}: {unit_id}")
            if not os.path.exists(source_path):
                await _log_and_update(task_id, f" -> Missing source: {source_path}")
                missing_sources.append({"kind": kind, "id": unit_id, "source_path": source_path})
                failed_count += 1
                continue
            try:
                action = await asyncio.to_thread(_copy_source_into_bundle, source_path, target_path, skip_existing)
                if action == "skipped":
                    skipped_count += 1
                    await _log_and_update(task_id, " -> Skipped (already exists in bundle)")
                else:
                    copied_count += 1
                    await _log_and_update(task_id, " -> Success")
            except PermissionError:
                await _log_and_update(task_id, " -> Failed: permission denied")
                failed_count += 1
            except Exception as exc:
                await _log_and_update(task_id, f" -> Failed: {exc}")
                failed_count += 1

        combined_pairs = existing_pairs + pairs
        final_msg = (
            "D-InSAR source bundle export finished. "
            f"Pairs {len(combined_pairs)} (+{len(pairs)}), Scenes {len(scene_list)}, "
            f"Orbits {len(orbit_list)}, "
            f"Copied {copied_count}, Skipped {skipped_count}, "
            f"Already exported pairs {skipped_existing_pair_count}, "
            f"Deferred {deferred_count}, Failed {failed_count}"
        )
        if failed_count > 0:
            await task_service.update_task(task_id, status="FAILED", message=final_msg, progress=100)
            raise CopyTaskExecutionError(final_msg)

        exported_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        pairs_payload = {
            "schema": "dinsar_source_bundle_pairs.v1",
            "exported_at": exported_at,
            "pairs": combined_pairs,
        }
        manifest_payload = {
            "schema": "dinsar_source_bundle_manifest.v1",
            "exported_at": exported_at,
            "package_format": "source_bundle",
            "destination": os.path.normpath(os.path.abspath(dest_dir)),
            "pair_count": len(combined_pairs),
            "new_pair_count": len(pairs),
            "existing_pair_count": len(existing_pairs),
            "candidate_pair_count": len(candidate_tasks),
            "skipped_existing_pairs": skipped_existing_pair_count,
            "scene_count": len(scene_list),
            "orbit_count": len(orbit_list),
            "include_orbit_files": bool(include_orbit_files),
            "skip_existing": bool(skip_existing),
            "max_items": max_items,
            "deferred_pairs": deferred_count,
            "copied_units": copied_count,
            "skipped_units": skipped_count,
            "failed_units": failed_count,
            "missing_sources": missing_sources,
            "directories": {
                "data": "data",
                "orbit": "orbit" if orbit_list else None,
            },
            "scenes": scene_list,
            "orbits": orbit_list,
        }
        await asyncio.to_thread(_write_json_file, pairs_path, pairs_payload)
        await asyncio.to_thread(_write_json_file, manifest_path, manifest_payload)

        await _log_and_update(task_id, final_msg, progress=100)
        await task_service.update_task(task_id, status="COMPLETED", message=final_msg, progress=100)
    except CopyTaskExecutionError:
        raise
    except Exception as e:
        fail_msg = f"D-InSAR source bundle fatal error: {e}"
        try:
            await task_service.update_task(task_id, status="FAILED", message=fail_msg)
        except Exception:
            pass
        raise CopyTaskExecutionError(fail_msg) from e


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
    skip_existing: bool = True,
    max_items: Optional[int] = None,
) -> None:
    try:
        if max_items is not None:
            try:
                max_items = int(max_items)
            except (TypeError, ValueError):
                max_items = None
            if max_items is not None and max_items <= 0:
                max_items = None

        await task_service.start_task(task_id, message="Starting D-InSAR copy task...")
        await _log_and_update(task_id, f"D-InSAR copy started. Dest: {dest_dir}")
        await _log_and_update(
            task_id,
            (
                "D-InSAR copy options: "
                f"include_orbit_files={include_orbit_files}, "
                f"export_zip={export_zip}, "
                f"skip_existing={skip_existing}, "
                f"max_items={max_items if max_items is not None else 'unlimited'}"
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
        skipped_count = 0
        failed_count = 0
        deferred_count = 0
        attempted_count = 0
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
                final_task_dir = os.path.join(dest_dir, task_alias)
                zip_path = os.path.join(dest_dir, f"{task_alias}.zip") if export_zip else None

                if skip_existing:
                    if export_zip and zip_path and _is_existing_dinsar_zip_complete(zip_path):
                        await _log_and_update(task_id, " -> Skipped (existing zip package)")
                        skipped_count += 1
                        continue
                    if (not export_zip) and _is_existing_dinsar_folder_complete(final_task_dir):
                        await _log_and_update(task_id, " -> Skipped (existing master/slave folders)")
                        skipped_count += 1
                        continue

                if max_items is not None and attempted_count >= max_items:
                    deferred_count = total - i + 1
                    await _log_and_update(
                        task_id,
                        f"Reached max_items={max_items}; deferred {deferred_count} remaining candidates.",
                    )
                    break
                attempted_count += 1

                if export_zip:
                    staging_root = await asyncio.to_thread(
                        tempfile.mkdtemp,
                        prefix="._dinsar_zip_",
                        dir=dest_dir,
                    )
                    task_dir = os.path.join(staging_root, task_alias)
                else:
                    staging_root = await asyncio.to_thread(
                        tempfile.mkdtemp,
                        prefix="._dinsar_copy_",
                        dir=dest_dir,
                    )
                    task_dir = os.path.join(staging_root, task_alias)
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
                    _build_dinsar_pair_metadata(
                        item,
                        task_name,
                        task_alias,
                        "zip" if export_zip else "folder",
                        include_orbit_files,
                        orbit_entries,
                    ),
                )
                if export_zip and zip_path:
                    await asyncio.to_thread(_zip_task_directory, task_dir, zip_path)
                    await _log_and_update(task_id, f" -> ZIP: {zip_path}")
                elif not export_zip:
                    if os.path.exists(final_task_dir):
                        raise CopyTaskExecutionError(
                            "Destination folder already exists and cannot be atomically replaced: "
                            f"{final_task_dir}"
                        )
                    await asyncio.to_thread(os.replace, task_dir, final_task_dir)
                    await _log_and_update(task_id, f" -> Folder: {final_task_dir}")

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
            f"Copied {success_count}, Skipped {skipped_count}, "
            f"Deferred {deferred_count}, Failed {failed_count}"
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
