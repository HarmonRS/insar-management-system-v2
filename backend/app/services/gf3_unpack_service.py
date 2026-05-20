from __future__ import annotations

import logging
import os
import shutil
import stat
import tarfile
import zipfile
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..config import settings, split_env_paths

logger = logging.getLogger(__name__)

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, str], None]

DEFAULT_GF3_ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz")


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_paths(paths: Optional[Iterable[str]]) -> List[str]:
    ordered: List[str] = []
    for raw_path in paths or []:
        text = str(raw_path or "").strip().strip('"').strip("'")
        if not text:
            continue
        normalized = os.path.normpath(os.path.abspath(text))
        if normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _normalize_extensions(extensions: Optional[Iterable[str]]) -> List[str]:
    ordered: List[str] = []
    for raw_ext in extensions or DEFAULT_GF3_ARCHIVE_EXTS:
        ext = str(raw_ext or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext not in ordered:
            ordered.append(ext)
    return sorted(ordered or list(DEFAULT_GF3_ARCHIVE_EXTS), key=len, reverse=True)


def _strip_archive_extension(file_name: str, extensions: Iterable[str]) -> str:
    lower_name = file_name.lower()
    for ext in _normalize_extensions(extensions):
        if lower_name.endswith(ext):
            return file_name[: -len(ext)]
    return os.path.splitext(file_name)[0]


def _resolve_target_root(archive_path: str, source_dirs: List[str], target_dirs: List[str]) -> str:
    if not target_dirs:
        raise ValueError("GF3_SOURCE_DIRS is not configured.")
    if len(target_dirs) == 1:
        return target_dirs[0]
    if source_dirs and len(source_dirs) == len(target_dirs):
        archive_norm = os.path.normcase(os.path.abspath(archive_path))
        matches: List[Tuple[int, int]] = []
        for index, source_dir in enumerate(source_dirs):
            source_norm = os.path.normcase(os.path.abspath(source_dir))
            if archive_norm == source_norm or archive_norm.startswith(source_norm + os.sep):
                matches.append((len(source_norm), index))
        if matches:
            _prefix_len, best_index = max(matches)
            return target_dirs[best_index]
    return target_dirs[0]


def _validate_relative_member_name(member_name: str, archive_path: str) -> str:
    name = str(member_name or "").replace("\\", "/")
    if not name or name in {".", "./"}:
        return ""
    if name.startswith("/") or os.path.isabs(name) or os.path.splitdrive(name)[0]:
        raise ValueError(f"Unsafe archive member path in {archive_path}: {member_name}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(f"Unsafe archive member path in {archive_path}: {member_name}")
    if not parts:
        return ""
    return os.path.join(*parts)


def _safe_destination(root_dir: str, relative_name: str) -> str:
    root_abs = os.path.abspath(root_dir)
    destination = os.path.abspath(os.path.join(root_abs, relative_name))
    if destination != root_abs and not destination.startswith(root_abs + os.sep):
        raise ValueError(f"Unsafe extraction destination: {relative_name}")
    return destination


def _validate_tar_members(members: Iterable[tarfile.TarInfo], archive_path: str) -> None:
    for member in members:
        _validate_relative_member_name(member.name, archive_path)
        if member.issym() or member.islnk():
            raise ValueError(f"Unsupported link entry in {archive_path}: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise ValueError(f"Unsupported special entry in {archive_path}: {member.name}")


def _validate_zip_members(infos: Iterable[zipfile.ZipInfo], archive_path: str) -> None:
    for info in infos:
        _validate_relative_member_name(info.filename, archive_path)
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise ValueError(f"Unsupported symlink entry in {archive_path}: {info.filename}")


def _estimate_archive_size(archive_path: str) -> int:
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zip_obj:
            infos = zip_obj.infolist()
            _validate_zip_members(infos, archive_path)
            return sum(max(0, int(info.file_size or 0)) for info in infos if not info.is_dir())
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, "r:*") as tar_obj:
            members = tar_obj.getmembers()
            _validate_tar_members(members, archive_path)
            return sum(max(0, int(member.size or 0)) for member in members if member.isfile())
    raise ValueError(f"Unsupported GF3 archive format: {archive_path}")


def _ensure_disk_space(target_root: str, required_bytes: int, min_disk_space_gb: float) -> None:
    os.makedirs(target_root, exist_ok=True)
    _total, _used, free_bytes = shutil.disk_usage(target_root)
    min_free_bytes = int(max(0.0, min_disk_space_gb) * (1024 ** 3))
    if free_bytes - max(0, int(required_bytes or 0)) < min_free_bytes:
        raise OSError(
            "GF3 L1A storage has insufficient free space: "
            f"needed {required_bytes / (1024 ** 3):.2f} GB, "
            f"free {free_bytes / (1024 ** 3):.2f} GB, "
            f"min free after {min_disk_space_gb:.2f} GB"
        )


def _prepare_atomic_output(output_dir: str, tmp_suffix: str) -> Tuple[bool, str, str]:
    tmp_dir = output_dir + tmp_suffix
    lock_path = output_dir + ".unpacking"
    if os.path.exists(output_dir):
        return False, tmp_dir, lock_path
    if os.path.exists(tmp_dir):
        return False, tmp_dir, lock_path
    if os.path.exists(lock_path):
        return False, tmp_dir, lock_path
    os.makedirs(tmp_dir, exist_ok=False)
    with open(lock_path, "w", encoding="utf-8") as stream:
        stream.write(datetime.now().isoformat())
    return True, tmp_dir, lock_path


def _cleanup_atomic_paths(tmp_dir: str, lock_path: str) -> None:
    if os.path.exists(lock_path):
        try:
            os.remove(lock_path)
        except OSError:
            pass
    if os.path.exists(tmp_dir):
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass


def _extract_tar_archive(archive_path: str, tmp_dir: str) -> int:
    extracted_files = 0
    with tarfile.open(archive_path, "r:*") as tar_obj:
        members = tar_obj.getmembers()
        _validate_tar_members(members, archive_path)
        for member in members:
            relative_name = _validate_relative_member_name(member.name, archive_path)
            if not relative_name:
                continue
            destination = _safe_destination(tmp_dir, relative_name)
            if member.isdir():
                os.makedirs(destination, exist_ok=True)
                continue
            if not member.isfile():
                continue
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            source = tar_obj.extractfile(member)
            if source is None:
                raise OSError(f"Failed to read tar member: {member.name}")
            with source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            extracted_files += 1
    return extracted_files


def _extract_zip_archive(archive_path: str, tmp_dir: str) -> int:
    extracted_files = 0
    with zipfile.ZipFile(archive_path, "r") as zip_obj:
        infos = zip_obj.infolist()
        _validate_zip_members(infos, archive_path)
        for info in infos:
            relative_name = _validate_relative_member_name(info.filename, archive_path)
            if not relative_name:
                continue
            destination = _safe_destination(tmp_dir, relative_name)
            if info.is_dir():
                os.makedirs(destination, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with zip_obj.open(info, "r") as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            extracted_files += 1
    return extracted_files


def _extract_archive_atomic(archive_path: str, output_dir: str, tmp_suffix: str) -> Tuple[str, int]:
    prepared, tmp_dir, lock_path = _prepare_atomic_output(output_dir, tmp_suffix)
    if not prepared:
        return "EXISTS", 0

    try:
        if zipfile.is_zipfile(archive_path):
            extracted_files = _extract_zip_archive(archive_path, tmp_dir)
        elif tarfile.is_tarfile(archive_path):
            extracted_files = _extract_tar_archive(archive_path, tmp_dir)
        else:
            raise ValueError(f"Unsupported GF3 archive format: {archive_path}")

        if extracted_files <= 0:
            raise OSError("GF3 archive extraction produced no files.")
        os.replace(tmp_dir, output_dir)
        return "EXTRACTED", extracted_files
    finally:
        _cleanup_atomic_paths(tmp_dir, lock_path)


def _discover_archives(source_dirs: List[str], extensions: List[str], log_callback: Optional[LogCallback]) -> List[str]:
    archives: List[str] = []
    normalized_extensions = _normalize_extensions(extensions)
    for source_dir in source_dirs:
        if not os.path.isdir(source_dir):
            message = f"GF3 archive source does not exist or is not a directory: {source_dir}"
            logger.warning(message)
            if log_callback:
                log_callback("WARNING", message)
            continue
        for root, _dirs, files in os.walk(source_dir):
            for file_name in files:
                lower_name = file_name.lower()
                if any(lower_name.endswith(ext) for ext in normalized_extensions):
                    archives.append(os.path.join(root, file_name))
    return sorted(archives)


def run_gf3_archive_unpack(
    *,
    source_dirs: Optional[Iterable[str]] = None,
    target_dirs: Optional[Iterable[str]] = None,
    archive_exts: Optional[Iterable[str]] = None,
    max_files_per_run: Optional[int] = None,
    delete_archive: Optional[bool] = None,
    min_disk_space_gb: Optional[float] = None,
    tmp_suffix: Optional[str] = None,
    log_callback: Optional[LogCallback] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    configured_source_dirs = _normalize_paths(
        source_dirs if source_dirs is not None else split_env_paths(settings.GF3_ARCHIVE_SOURCE_DIRS)
    )
    configured_target_dirs = _normalize_paths(
        target_dirs if target_dirs is not None else split_env_paths(settings.GF3_SOURCE_DIRS)
    )
    extensions = _normalize_extensions(
        archive_exts if archive_exts is not None else split_env_paths(settings.GF3_ARCHIVE_EXTS)
    )
    should_delete_archive = settings.GF3_UNPACK_DELETE_ARCHIVE if delete_archive is None else bool(delete_archive)
    limit = max(0, int(max_files_per_run or 0))
    min_free_gb = (
        _parse_float(os.getenv("UNPACK_MIN_DISK_SPACE_GB"), 50.0)
        if min_disk_space_gb is None
        else float(min_disk_space_gb)
    )
    atomic_tmp_suffix = str(tmp_suffix or os.getenv("UNPACK_TMP_SUFFIX") or ".unpack_tmp").strip() or ".unpack_tmp"

    if not configured_source_dirs:
        raise ValueError("GF3_ARCHIVE_SOURCE_DIRS is not configured.")
    if not configured_target_dirs:
        raise ValueError("GF3_SOURCE_DIRS is not configured.")

    def _log(level: str, message: str) -> None:
        logger.log(getattr(logging, level.upper(), logging.INFO), message)
        if log_callback:
            log_callback(level.upper(), message)

    def _progress(progress: int, message: str) -> None:
        if progress_callback:
            progress_callback(max(0, min(100, int(progress))), message)

    _progress(2, "Scanning GF3 archive source directories...")
    archives = _discover_archives(configured_source_dirs, extensions, log_callback)
    if limit > 0:
        archives_to_process = archives[:limit]
    else:
        archives_to_process = archives

    total = len(archives_to_process)
    summary: Dict[str, Any] = {
        "total": total,
        "found": len(archives),
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "remaining": max(0, len(archives) - total),
        "source_dirs": configured_source_dirs,
        "target_dirs": configured_target_dirs,
        "archive_exts": extensions,
        "delete_archive": should_delete_archive,
        "failures": [],
    }

    if not archives_to_process:
        _progress(100, "No GF3 archives pending.")
        summary["message"] = "No GF3 archives found."
        return summary

    _log("INFO", f"Found {len(archives)} GF3 archives; processing {total}.")

    for index, archive_path in enumerate(archives_to_process, start=1):
        archive_name = os.path.basename(archive_path)
        progress_base = 5 + int(((index - 1) / max(1, total)) * 90)
        _progress(progress_base, f"Unpacking GF3 archive {index}/{total}: {archive_name}")

        try:
            target_root = _resolve_target_root(archive_path, configured_source_dirs, configured_target_dirs)
            os.makedirs(target_root, exist_ok=True)
            output_name = _strip_archive_extension(os.path.basename(archive_path), extensions)
            output_dir = os.path.join(target_root, output_name)
            required_bytes = _estimate_archive_size(archive_path)
            _ensure_disk_space(target_root, required_bytes, min_free_gb)
            status, extracted_files = _extract_archive_atomic(archive_path, output_dir, atomic_tmp_suffix)

            if status == "EXISTS":
                summary["skipped"] += 1
                _log("INFO", f"GF3 archive already unpacked, skipped: {archive_path}")
                continue

            if should_delete_archive:
                os.remove(archive_path)
                _log("INFO", f"GF3 archive deleted after successful unpack: {archive_path}")
            summary["processed"] += 1
            _log("INFO", f"GF3 archive unpacked: {archive_path} -> {output_dir} ({extracted_files} files)")
        except Exception as exc:
            summary["failed"] += 1
            failure = {
                "archive_path": archive_path,
                "error": str(exc),
            }
            summary["failures"].append(failure)
            _log("ERROR", f"GF3 archive unpack failed: {archive_path}: {exc}")

    _progress(100, "GF3 archive unpack completed.")
    summary["message"] = (
        f"GF3 unpack complete: processed {summary['processed']}, "
        f"skipped {summary['skipped']}, failed {summary['failed']}"
    )
    return summary
