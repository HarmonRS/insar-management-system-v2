import os
import json
import shutil
import tarfile
import logging
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

# 使用统一的日志目录
LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "tasks", "unpacker")
os.makedirs(LOG_DIR, exist_ok=True)

# 使用日期命名日志文件
log_date = datetime.now().strftime("%Y%m%d")
LOG_FILE = os.path.join(LOG_DIR, f"unpacker_{log_date}.json")
REPORT_FILE = os.path.join(LOG_DIR, f"unpacker_{log_date}_report.txt")
ACTIVITY_LOG = os.path.join(LOG_DIR, f"unpacker_{log_date}.log")


class ProjWarningFilter(logging.Filter):
    """过滤重复的 PROJ 数据库版本警告"""
    def __init__(self):
        super().__init__()
        self.proj_warning_logged = False

    def filter(self, record):
        # 检查是否是 PROJ 警告
        if "PROJ: proj_identify" in record.getMessage() and "DATABASE.LAYOUT.VERSION.MINOR" in record.getMessage():
            if self.proj_warning_logged:
                return False  # 已经记录过，过滤掉
            else:
                self.proj_warning_logged = True
                # 修改消息，添加提示
                record.msg = record.msg + " (后续相同警告已过滤)"
                return True
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


def get_disk_usage(path):
    try:
        os.makedirs(path, exist_ok=True)
        total, used, free = shutil.disk_usage(path)
        return total, used, free
    except FileNotFoundError:
        logging.error("disk usage failed for path: %s", path)
        return 0, 0, 0


def find_archives(directories, extensions):
    archive_files = []
    for directory in directories:
        if not os.path.isdir(directory):
            logging.warning("source directory not found: %s", directory)
            continue
        for root, _, files in os.walk(directory):
            for file in files:
                lower = file.lower()
                if any(lower.endswith(ext) for ext in extensions):
                    archive_files.append(os.path.join(root, file))
    return archive_files


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


def _validate_tar_members(tar_obj, archive_path):
    for member in tar_obj.getmembers():
        if not _is_safe_tar_member(member.name):
            raise IOError(f"unsafe tar entry detected: {member.name} in {archive_path}")


def get_archive_uncompressed_size(archive_path):
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            _validate_tar_members(tar, archive_path)
            return sum(m.size for m in tar.getmembers() if m.isfile())
    except (tarfile.TarError, FileNotFoundError, IsADirectoryError) as e:
        logging.error("failed to calculate size for '%s': %s", archive_path, e)
        return -1


def pick_storage_dir(storage_dirs, required_bytes, min_free_bytes):
    candidates = []
    for d in storage_dirs:
        _, _, free = get_disk_usage(d)
        if (free - required_bytes) >= min_free_bytes:
            candidates.append((free, d))
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


def atomic_extract(archive_path, output_dir, tmp_suffix):
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
        with tarfile.open(archive_path, "r:*") as tar:
            _validate_tar_members(tar, archive_path)
            tar.extractall(path=tmp_dir)
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


def run_unpack_job(env_path=None, log_callback=None, progress_callback=None):
    def _log(level, message, *args):
        logging.log(level, message, *args)
        if log_callback:
            formatted = message % args if args else message
            log_callback(logging.getLevelName(level), formatted)

    def _progress(progress, message):
        if progress_callback:
            progress_callback(progress, message)

    # 配置日志过滤器
    proj_filter = ProjWarningFilter()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(ACTIVITY_LOG, "a", "utf-8"),
            logging.StreamHandler(),
        ],
    )

    # 为所有 handler 添加过滤器
    for handler in logging.root.handlers:
        handler.addFilter(proj_filter)

    env = load_env(env_path or ENV_PATH)
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

    _log(logging.INFO, "=== start unpack job ===")

    if not source_dirs:
        _log(logging.INFO, "no UNPACK_SOURCE_DIRS configured, exit")
        return {
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0,
            "message": "no source dirs configured",
        }

    progress = load_progress(LOG_FILE)
    processed_files = set(progress.get("processed_files", []))

    all_archives = find_archives(source_dirs, extensions)
    files_to_process = [f for f in all_archives if f not in processed_files]

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

    min_space_bytes = min_disk_gb * (1024 ** 3)
    processed_count = 0
    failed_count = 0
    skipped_count = 0

    total_files = len(files_to_process)
    for i, archive_path in enumerate(files_to_process):
        current_file_number = i + 1
        pct = int((i / max(total_files, 1)) * 100)
        _progress(pct, f"processing {current_file_number}/{total_files}")

        _log(
            logging.INFO,
            "--- processing %s/%s: %s ---",
            current_file_number,
            total_files,
            archive_path,
        )

        uncompressed_size_bytes = get_archive_uncompressed_size(archive_path)
        if uncompressed_size_bytes == -1:
            failure_record = {
                "file": archive_path,
                "error": "size_check_failed",
                "timestamp": datetime.now().isoformat(),
            }
            progress.setdefault("failed_files", []).append(failure_record)
            save_progress(LOG_FILE, progress)
            failed_count += 1
            continue

        if target_dirs:
            target_root = _resolve_target_root(archive_path, source_dirs, target_dirs)
            if not target_root:
                target_root = target_dirs[0]
            _, _, free = get_disk_usage(target_root)
            if (free - uncompressed_size_bytes) < min_space_bytes:
                reason = (
                    "insar_storage has insufficient free space\n"
                    "  needed: %.2f GB\n"
                    "  min free after: %.2f GB\n"
                    "  target: %s\n"
                    % (
                        uncompressed_size_bytes / (1024 ** 3),
                        min_disk_gb,
                        target_root,
                    )
                )
                _log(logging.WARNING, reason)
                create_report(REPORT_FILE, reason, processed_count, total_files - i)
                return {
                    "processed": processed_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "total": total_files,
                    "message": "insufficient free space",
                }
        else:
            target_root = os.path.dirname(archive_path)

        base_name = os.path.basename(archive_path)
        for ext in [".tar.gz", ".tgz"]:
            if base_name.lower().endswith(ext):
                base_name = base_name[: -len(ext)]
                break
        output_dir = os.path.join(target_root, base_name)

        try:
            ok = atomic_extract(archive_path, output_dir, tmp_suffix)
            if not ok:
                skipped_count += 1
                continue

            if delete_archive:
                os.remove(archive_path)

            processed_files.add(archive_path)
            progress["processed_files"] = sorted(list(processed_files))
            save_progress(LOG_FILE, progress)
            _log(logging.INFO, "done: %s", archive_path)
            processed_count += 1

        except (tarfile.TarError, IOError, OSError) as e:
            _log(logging.ERROR, "failed to process '%s': %s", archive_path, e)
            failure_record = {
                "file": archive_path,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
            progress.setdefault("failed_files", []).append(failure_record)
            save_progress(LOG_FILE, progress)
            failed_count += 1

        except Exception as e:
            _log(logging.CRITICAL, "unexpected error '%s': %s", archive_path, e)
            failure_record = {
                "file": archive_path,
                "error": "unexpected: %s" % e,
                "timestamp": datetime.now().isoformat(),
            }
            progress.setdefault("failed_files", []).append(failure_record)
            save_progress(LOG_FILE, progress)
            failed_count += 1

        pct = int(((i + 1) / max(total_files, 1)) * 100)
        _progress(pct, f"processed {current_file_number}/{total_files}")

    if os.path.exists(REPORT_FILE):
        os.remove(REPORT_FILE)

    _log(logging.INFO, "=== unpack job complete ===")
    return {
        "processed": processed_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "total": total_files,
        "message": "completed",
    }


def main():
    run_unpack_job()


if __name__ == "__main__":
    main()
