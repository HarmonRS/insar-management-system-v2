import json
import logging
import os
import shutil
import tarfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "tasks", "unpacker")
os.makedirs(LOG_DIR, exist_ok=True)

log_date = datetime.now().strftime("%Y%m%d")
LEGACY_LOG_FILE = os.path.join(LOG_DIR, f"unpacker_{log_date}.json")
LOG_FILE = os.path.join(LOG_DIR, f"unpacker_parallel_{log_date}.json")
REPORT_FILE = os.path.join(LOG_DIR, f"unpacker_parallel_{log_date}_report.txt")
ACTIVITY_LOG = os.path.join(LOG_DIR, f"unpacker_parallel_{log_date}.log")


class ProjWarningFilter(logging.Filter):
    """Suppress duplicate PROJ database warnings."""

    def __init__(self):
        super().__init__()
        self.proj_warning_logged = False

    def filter(self, record):
        message = record.getMessage()
        if "PROJ: proj_identify" in message and "DATABASE.LAYOUT.VERSION.MINOR" in message:
            if self.proj_warning_logged:
                return False
            self.proj_warning_logged = True
            record.msg = record.msg + " (duplicate warnings suppressed)"
        return True


def load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def parse_dirs(value):
    if not value:
        return []
    value = value.replace(";", ",")
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value, default, minimum=1, maximum=None):
    try:
        parsed = int(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        parsed = int(default)
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _default_scan_workers(source_dirs):
    cpu = os.cpu_count() or 4
    return max(1, min(len(source_dirs) or 1, cpu, 8))


def _default_extract_workers():
    cpu = os.cpu_count() or 4
    return max(1, min(cpu, 8))


def _format_limit_reason(reason, limit_value):
    if reason == "max_files_per_run":
        return f"reached max files per run: {limit_value}"
    if reason == "max_runtime_minutes":
        return f"reached max runtime: {limit_value} minutes"
    return str(reason or "stopped")


def get_disk_usage(path):
    try:
        os.makedirs(path, exist_ok=True)
        total, used, free = shutil.disk_usage(path)
        return total, used, free
    except FileNotFoundError:
        logging.error("disk usage failed for path: %s", path)
        return 0, 0, 0


def _normalize_extensions(extensions):
    normalized = {str(ext).strip().lower() for ext in extensions if str(ext).strip()}
    return tuple(sorted(normalized, key=len, reverse=True))


def _scan_archive_dir(directory, normalized_extensions):
    if not os.path.isdir(directory):
        return directory, [], False

    archive_files = []
    for root, _, files in os.walk(directory):
        for file_name in files:
            lower_name = file_name.lower()
            if any(lower_name.endswith(ext) for ext in normalized_extensions):
                archive_files.append(os.path.join(root, file_name))
    return directory, archive_files, True


def find_archives(directories, extensions, workers=1, log_fn=None):
    normalized_extensions = _normalize_extensions(extensions)
    if not directories or not normalized_extensions:
        return []

    worker_count = max(1, min(int(workers), len(directories)))
    archive_files = []

    if worker_count == 1:
        for directory in directories:
            scanned_dir, matches, exists = _scan_archive_dir(directory, normalized_extensions)
            if not exists:
                if log_fn:
                    log_fn(logging.WARNING, "source directory not found: %s", scanned_dir)
                else:
                    logging.warning("source directory not found: %s", scanned_dir)
                continue
            archive_files.extend(matches)
        return sorted(archive_files)

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="unpack-scan") as executor:
        futures = [
            executor.submit(_scan_archive_dir, directory, normalized_extensions)
            for directory in directories
        ]
        for future in futures:
            scanned_dir, matches, exists = future.result()
            if not exists:
                if log_fn:
                    log_fn(logging.WARNING, "source directory not found: %s", scanned_dir)
                else:
                    logging.warning("source directory not found: %s", scanned_dir)
                continue
            archive_files.extend(matches)

    return sorted(archive_files)


def load_progress(log_file):
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.warning("failed to read log file '%s': %s", log_file, e)
    return {"processed_files": [], "failed_files": []}


def save_progress(log_file, progress):
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(progress, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logging.error("failed to write log file '%s': %s", log_file, e)


def create_report(report_file, reason, processed_count, remaining_count):
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            f.write("--- Unpacker Report ---\n\n")
            f.write("Stopped at: %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            f.write("Reason: %s\n\n" % reason)
            f.write("Processed files: %s\n" % processed_count)
            f.write("Remaining files: %s\n" % remaining_count)
        logging.info("report written: %s", report_file)
    except IOError as e:
        logging.error("failed to write report '%s': %s", report_file, e)


def _is_safe_tar_member(member_name):
    norm_name = os.path.normpath(member_name)
    if os.path.isabs(norm_name):
        return False
    if norm_name.startswith("..") or norm_name.startswith("../") or norm_name.startswith("..\\"):
        return False
    return True


def _validate_tar_members(members, archive_path):
    for member in members:
        if not _is_safe_tar_member(member.name):
            raise IOError(f"unsafe tar entry detected: {member.name} in {archive_path}")
        if member.islnk() or member.issym():
            raise IOError(f"unsupported tar link entry: {member.name} in {archive_path}")
        if member.ischr() or member.isblk() or member.isfifo():
            raise IOError(f"unsupported tar special entry: {member.name} in {archive_path}")


def _copy_fileobj(src, dst, chunk_size=1024 * 1024):
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        dst.write(chunk)


def get_archive_uncompressed_size(archive_path):
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            members = tar.getmembers()
            _validate_tar_members(members, archive_path)
            return sum(member.size for member in members if member.isfile())
    except (tarfile.TarError, FileNotFoundError, IsADirectoryError) as e:
        logging.error("failed to calculate size for '%s': %s", archive_path, e)
        return -1


def pick_storage_dir(storage_dirs, required_bytes, min_free_bytes):
    candidates = []
    for directory in storage_dirs:
        _, _, free = get_disk_usage(directory)
        if (free - required_bytes) >= min_free_bytes:
            candidates.append((free, directory))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _normalize_path(path):
    return os.path.normcase(os.path.abspath(path))


def _resolve_target_root(archive_path, source_dirs, target_dirs):
    if not target_dirs:
        return None

    if len(target_dirs) == 1:
        return target_dirs[0]

    if source_dirs and len(source_dirs) == len(target_dirs):
        archive_norm = _normalize_path(archive_path)
        matches = []
        for idx, src in enumerate(source_dirs):
            src_norm = _normalize_path(src)
            if archive_norm == src_norm or archive_norm.startswith(src_norm + os.sep):
                matches.append((len(src_norm), idx))
        if matches:
            _, best_idx = max(matches)
            return target_dirs[best_idx]

    return target_dirs[0]


def _strip_archive_extension(file_name, extensions):
    lower_name = file_name.lower()
    for ext in _normalize_extensions(extensions):
        if lower_name.endswith(ext):
            return file_name[: -len(ext)]
    return os.path.splitext(file_name)[0]


def _extract_tar_to_output(tar_obj, members, output_dir, tmp_suffix):
    tmp_dir = output_dir + tmp_suffix
    lock_path = output_dir + ".unpacking"

    if os.path.exists(output_dir):
        logging.warning("output exists, skip: %s", output_dir)
        return False
    if os.path.exists(tmp_dir):
        logging.warning("temp dir exists, skip: %s", tmp_dir)
        return False
    if os.path.exists(lock_path):
        logging.warning("lock exists, skip: %s", lock_path)
        return False

    os.makedirs(tmp_dir, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())

    try:
        for member in members:
            destination = os.path.join(tmp_dir, member.name)
            if member.isdir():
                os.makedirs(destination, exist_ok=True)
                continue
            if not member.isfile():
                continue

            parent_dir = os.path.dirname(destination)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            extracted_file = tar_obj.extractfile(member)
            if extracted_file is None:
                raise IOError(f"failed to extract file entry: {member.name}")
            with extracted_file:
                with open(destination, "wb") as output_file:
                    _copy_fileobj(extracted_file, output_file)

        if not os.listdir(tmp_dir):
            raise IOError("extracted directory is empty")
        os.replace(tmp_dir, output_dir)
        return True
    finally:
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


def atomic_extract(archive_path, output_dir, tmp_suffix):
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            members = tar.getmembers()
            _validate_tar_members(members, archive_path)
            return _extract_tar_to_output(tar, members, output_dir, tmp_suffix)
    except (tarfile.TarError, FileNotFoundError, IsADirectoryError) as e:
        logging.error("failed to extract '%s': %s", archive_path, e)
        raise


class ProgressStore:
    def __init__(self, log_file):
        data = load_progress(log_file)
        if _normalize_path(log_file) != _normalize_path(LEGACY_LOG_FILE):
            legacy_data = load_progress(LEGACY_LOG_FILE)
            data = {
                "processed_files": sorted(
                    set(data.get("processed_files", [])) | set(legacy_data.get("processed_files", []))
                ),
                "failed_files": list(legacy_data.get("failed_files", [])) + list(data.get("failed_files", [])),
            }
        self.log_file = log_file
        self.lock = threading.Lock()
        self.processed_files = set(data.get("processed_files", []))
        self.failed_files = list(data.get("failed_files", []))

    def snapshot_processed_files(self):
        with self.lock:
            return set(self.processed_files)

    def _persist_unlocked(self):
        payload = {
            "processed_files": sorted(self.processed_files),
            "failed_files": list(self.failed_files),
        }
        save_progress(self.log_file, payload)

    def mark_processed(self, archive_path):
        with self.lock:
            self.processed_files.add(archive_path)
            self._persist_unlocked()

    def mark_failed(self, archive_path, error_message):
        with self.lock:
            self.failed_files.append(
                {
                    "file": archive_path,
                    "error": error_message,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            self._persist_unlocked()


class DiskReservationManager:
    def __init__(self, min_free_bytes):
        self.min_free_bytes = int(min_free_bytes)
        self.lock = threading.Lock()
        self.reserved_bytes = {}

    def reserve(self, target_root, required_bytes):
        required_bytes = max(0, int(required_bytes))
        target_key = _normalize_path(target_root)
        with self.lock:
            _, _, free = get_disk_usage(target_root)
            already_reserved = int(self.reserved_bytes.get(target_key, 0))
            available_after = free - already_reserved - required_bytes
            if available_after < self.min_free_bytes:
                return False, {
                    "free_bytes": free,
                    "already_reserved_bytes": already_reserved,
                    "required_bytes": required_bytes,
                    "min_free_bytes": self.min_free_bytes,
                }
            self.reserved_bytes[target_key] = already_reserved + required_bytes
            return True, {
                "free_bytes": free,
                "already_reserved_bytes": already_reserved,
                "required_bytes": required_bytes,
                "min_free_bytes": self.min_free_bytes,
            }

    def release(self, target_root, required_bytes):
        required_bytes = max(0, int(required_bytes))
        target_key = _normalize_path(target_root)
        with self.lock:
            remaining = int(self.reserved_bytes.get(target_key, 0)) - required_bytes
            if remaining > 0:
                self.reserved_bytes[target_key] = remaining
            else:
                self.reserved_bytes.pop(target_key, None)


def _format_space_reason(target_root, min_disk_gb, reservation_info):
    return (
        "insar_storage has insufficient free space\n"
        "  needed: %.2f GB\n"
        "  reserved by active unpack jobs: %.2f GB\n"
        "  min free after: %.2f GB\n"
        "  target: %s\n"
        % (
            reservation_info["required_bytes"] / (1024 ** 3),
            reservation_info["already_reserved_bytes"] / (1024 ** 3),
            min_disk_gb,
            target_root,
        )
    )


def _process_archive(
    archive_path,
    archive_index,
    total_files,
    source_dirs,
    target_dirs,
    extensions,
    tmp_suffix,
    delete_archive,
    reservation_manager,
    progress_store,
    min_disk_gb,
    log_fn,
):
    target_root = _resolve_target_root(archive_path, source_dirs, target_dirs) if target_dirs else None
    if not target_root:
        target_root = os.path.dirname(archive_path)

    base_name = _strip_archive_extension(os.path.basename(archive_path), extensions)
    output_dir = os.path.join(target_root, base_name)
    reserved_bytes = 0

    log_fn(
        logging.INFO,
        "--- processing %s/%s: %s ---",
        archive_index,
        total_files,
        archive_path,
    )

    try:
        with tarfile.open(archive_path, "r:*") as tar:
            members = tar.getmembers()
            _validate_tar_members(members, archive_path)
            reserved_bytes = sum(member.size for member in members if member.isfile())

            reserved_ok, reservation_info = reservation_manager.reserve(target_root, reserved_bytes)
            if not reserved_ok:
                return {
                    "status": "stop",
                    "archive_path": archive_path,
                    "reason": _format_space_reason(target_root, min_disk_gb, reservation_info),
                }

            extracted = _extract_tar_to_output(tar, members, output_dir, tmp_suffix)

        if not extracted:
            return {"status": "skipped", "archive_path": archive_path}

        if delete_archive:
            os.remove(archive_path)

        progress_store.mark_processed(archive_path)
        return {"status": "processed", "archive_path": archive_path}

    except (tarfile.TarError, FileNotFoundError, IsADirectoryError, IOError, OSError) as e:
        progress_store.mark_failed(archive_path, str(e))
        return {
            "status": "failed",
            "archive_path": archive_path,
            "error": str(e),
        }
    except Exception as e:
        error_message = "unexpected: %s" % e
        progress_store.mark_failed(archive_path, error_message)
        return {
            "status": "failed",
            "archive_path": archive_path,
            "error": error_message,
        }
    finally:
        if reserved_bytes:
            reservation_manager.release(target_root, reserved_bytes)


def _configure_logging():
    proj_filter = ProjWarningFilter()
    handlers = [logging.StreamHandler()]
    file_handler_error = None

    try:
        handlers.insert(0, logging.FileHandler(ACTIVITY_LOG, "a", "utf-8"))
    except OSError as exc:
        file_handler_error = exc

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
    for handler in logging.root.handlers:
        handler.addFilter(proj_filter)
    if file_handler_error is not None:
        logging.warning("failed to open activity log '%s': %s", ACTIVITY_LOG, file_handler_error)


def run_unpack_job(env_path=None, log_callback=None, progress_callback=None, config_overrides=None):
    def _log(level, message, *args):
        logging.log(level, message, *args)
        if log_callback:
            formatted = message % args if args else message
            log_callback(logging.getLevelName(level), formatted)

    def _progress(progress, message):
        if progress_callback:
            progress_callback(progress, message)

    _configure_logging()

    env = load_env(env_path or ENV_PATH)
    config_overrides = config_overrides if isinstance(config_overrides, dict) else {}
    source_dirs = parse_dirs(env.get("UNPACK_SOURCE_DIRS"))
    target_dirs = parse_dirs(
        env.get("INSAR_STORAGE_DIRS")
        or env.get("UNPACK_TARGET_DIRS")
        or env.get("UNPACK_STORAGE_DIRS")
    )
    min_disk_gb = float(env.get("UNPACK_MIN_DISK_SPACE_GB", "50"))
    delete_archive = parse_bool(env.get("UNPACK_DELETE_ARCHIVE", "true"))
    tmp_suffix = env.get("UNPACK_TMP_SUFFIX", ".unpack_tmp")
    extensions = parse_dirs(env.get("UNPACK_ARCHIVE_EXTS", ".tar.gz"))
    scan_workers = parse_int(
        env.get("UNPACK_SCAN_WORKERS"),
        default=_default_scan_workers(source_dirs),
        minimum=1,
        maximum=max(1, len(source_dirs) or 1),
    )
    extract_workers = parse_int(
        env.get("UNPACK_EXTRACT_WORKERS"),
        default=_default_extract_workers(),
        minimum=1,
        maximum=32,
    )
    max_files_per_run = parse_int(
        config_overrides.get("max_files_per_run", env.get("UNPACK_MAX_FILES_PER_RUN")),
        default=0,
        minimum=0,
    )
    max_runtime_minutes = parse_int(
        config_overrides.get("max_runtime_minutes", env.get("UNPACK_MAX_RUNTIME_MINUTES")),
        default=0,
        minimum=0,
    )

    _log(logging.INFO, "=== start unpack job ===")
    if "max_files_per_run" in config_overrides or "max_runtime_minutes" in config_overrides:
        _log(
            logging.INFO,
            "run overrides: max_files_per_run=%s, max_runtime_minutes=%s",
            max_files_per_run,
            max_runtime_minutes,
        )

    if not source_dirs:
        _log(logging.INFO, "no UNPACK_SOURCE_DIRS configured, exit")
        return {
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
            "message": "no source dirs configured",
        }

    progress_store = ProgressStore(LOG_FILE)
    processed_files = progress_store.snapshot_processed_files()

    _log(
        logging.INFO,
        "unpack workers: scan=%s, extract=%s, visible_cpus=%s",
        scan_workers,
        extract_workers,
        os.cpu_count(),
    )

    all_archives = find_archives(source_dirs, extensions, workers=scan_workers, log_fn=_log)
    files_to_process = [archive_path for archive_path in all_archives if archive_path not in processed_files]
    total_pending_files = len(files_to_process)

    _log(
        logging.INFO,
        "found %s archives, %s processed, %s pending",
        len(all_archives),
        len(processed_files),
        len(files_to_process),
    )

    if not files_to_process:
        _log(logging.INFO, "nothing to do")
        return {
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
            "message": "nothing to do",
        }

    if max_files_per_run > 0 and len(files_to_process) > max_files_per_run:
        _log(
            logging.INFO,
            "apply UNPACK_MAX_FILES_PER_RUN=%s, this run will process the first %s pending archives",
            max_files_per_run,
            max_files_per_run,
        )
        files_to_process = files_to_process[:max_files_per_run]

    min_space_bytes = min_disk_gb * (1024 ** 3)
    reservation_manager = DiskReservationManager(min_space_bytes)

    total_files = len(files_to_process)
    remaining_backlog_count = max(0, total_pending_files - total_files)
    processed_count = 0
    failed_count = 0
    skipped_count = 0
    completed_count = 0
    stop_reason = None
    stop_reason_limit = None
    jobs = list(enumerate(files_to_process, start=1))
    started_at = time.monotonic()
    max_runtime_seconds = max_runtime_minutes * 60

    def _runtime_limit_reached():
        return max_runtime_seconds > 0 and (time.monotonic() - started_at) >= max_runtime_seconds

    _progress(0, f"processing 0/{total_files}")

    with ThreadPoolExecutor(
        max_workers=max(1, min(extract_workers, total_files)),
        thread_name_prefix="unpack-extract",
    ) as executor:
        active_futures = {}
        next_job_index = 0

        while next_job_index < total_files and len(active_futures) < extract_workers:
            archive_index, archive_path = jobs[next_job_index]
            future = executor.submit(
                _process_archive,
                archive_path,
                archive_index,
                total_files,
                source_dirs,
                target_dirs,
                extensions,
                tmp_suffix,
                delete_archive,
                reservation_manager,
                progress_store,
                min_disk_gb,
                _log,
            )
            active_futures[future] = (archive_index, archive_path)
            next_job_index += 1

        while active_futures:
            done, _ = wait(set(active_futures), return_when=FIRST_COMPLETED)
            for future in done:
                _, archive_path = active_futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    error_message = "unexpected worker failure: %s" % exc
                    progress_store.mark_failed(archive_path, error_message)
                    result = {
                        "status": "failed",
                        "archive_path": archive_path,
                        "error": error_message,
                    }

                status = result.get("status")
                if status == "processed":
                    processed_count += 1
                    completed_count += 1
                    _log(logging.INFO, "done: %s", archive_path)
                elif status == "skipped":
                    skipped_count += 1
                    completed_count += 1
                elif status == "failed":
                    failed_count += 1
                    completed_count += 1
                    _log(logging.ERROR, "failed to process '%s': %s", archive_path, result.get("error"))
                elif status == "stop":
                    if not stop_reason:
                        stop_reason = result.get("reason") or "insufficient free space"
                    _log(logging.WARNING, stop_reason)
                else:
                    failed_count += 1
                    completed_count += 1
                    _log(logging.ERROR, "unknown unpack worker status for '%s': %s", archive_path, status)

                pct = int((completed_count / max(total_files, 1)) * 100)
                _progress(pct, f"processed {completed_count}/{total_files}")

            if not stop_reason and next_job_index < total_files and _runtime_limit_reached():
                stop_reason_limit = max_runtime_minutes
                stop_reason = _format_limit_reason("max_runtime_minutes", max_runtime_minutes)
                _log(logging.INFO, "stop scheduling new archives: %s", stop_reason)

            while (
                next_job_index < total_files
                and len(active_futures) < extract_workers
                and not stop_reason
            ):
                archive_index, archive_path = jobs[next_job_index]
                future = executor.submit(
                    _process_archive,
                    archive_path,
                    archive_index,
                    total_files,
                    source_dirs,
                    target_dirs,
                    extensions,
                    tmp_suffix,
                    delete_archive,
                    reservation_manager,
                    progress_store,
                    min_disk_gb,
                    _log,
                )
                active_futures[future] = (archive_index, archive_path)
                next_job_index += 1

    if not stop_reason and remaining_backlog_count > 0 and max_files_per_run > 0:
        stop_reason_limit = max_files_per_run
        stop_reason = _format_limit_reason("max_files_per_run", max_files_per_run)

    if stop_reason:
        remaining_count = remaining_backlog_count + max(0, total_files - completed_count)
        create_report(REPORT_FILE, stop_reason, processed_count, remaining_count)
        return {
            "processed": processed_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "total": total_files,
            "remaining": remaining_count,
            "limit_value": stop_reason_limit,
            "message": stop_reason,
        }

    if os.path.exists(REPORT_FILE):
        os.remove(REPORT_FILE)

    _log(logging.INFO, "=== unpack job complete ===")
    return {
        "processed": processed_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "total": total_files,
        "remaining": 0,
        "message": "completed",
    }


def main():
    run_unpack_job()


if __name__ == "__main__":
    main()
