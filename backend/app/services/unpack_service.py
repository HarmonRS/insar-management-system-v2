import asyncio
import importlib.util
import os
from typing import Any, Dict, Optional

from .task_service import task_service
from ..config import settings

_UNPACK_MODULE = None


def _load_unpack_module():
    global _UNPACK_MODULE
    if _UNPACK_MODULE is not None:
        return _UNPACK_MODULE

    script_path = os.path.join(settings.PROJECT_ROOT, "scripts", "unpack_archives_parallel.py")
    spec = importlib.util.spec_from_file_location("unpack_archives", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load unpack_archives_parallel.py module.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _UNPACK_MODULE = module
    return module


def get_unpack_config() -> Dict[str, Any]:
    module = _load_unpack_module()
    env = module.load_env(module.ENV_PATH)
    source_dirs = module.parse_dirs(env.get("UNPACK_SOURCE_DIRS"))
    insar_storage_dirs = module.parse_dirs(
        env.get("INSAR_STORAGE_DIRS")
        or env.get("UNPACK_TARGET_DIRS")
        or env.get("UNPACK_STORAGE_DIRS")
    )
    archive_exts = module.parse_dirs(env.get("UNPACK_ARCHIVE_EXTS", ".tar.gz"))
    return {
        "source_dirs": source_dirs,
        "insar_storage_dirs": insar_storage_dirs,
        "min_disk_space_gb": float(env.get("UNPACK_MIN_DISK_SPACE_GB", "50")),
        "delete_archive": module.parse_bool(env.get("UNPACK_DELETE_ARCHIVE", "true")),
        "tmp_suffix": env.get("UNPACK_TMP_SUFFIX", ".unpack_tmp"),
        "archive_exts": archive_exts,
        "scan_workers": module.parse_int(
            env.get("UNPACK_SCAN_WORKERS"),
            default=module._default_scan_workers(source_dirs),
            minimum=1,
            maximum=max(1, len(source_dirs) or 1),
        ),
        "extract_workers": module.parse_int(
            env.get("UNPACK_EXTRACT_WORKERS"),
            default=module._default_extract_workers(),
            minimum=1,
            maximum=32,
        ),
    }


async def run_unpack_task(task_id: str):
    module = _load_unpack_module()
    loop = asyncio.get_running_loop()

    def _submit(coro):
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            return

        def _swallow_errors(fut):
            try:
                fut.result()
            except Exception as exc:
                print(f"[WARN] unpack callback: {exc}")

        future.add_done_callback(_swallow_errors)

    try:
        await task_service.start_task(task_id, message="Archive unpack started")

        def log_cb(level: str, message: str):
            _submit(task_service.add_log(task_id, level, message))

        def progress_cb(progress: int, message: str):
            _submit(task_service.update_task(task_id, progress=progress, message=message))

        result: Optional[Dict[str, Any]] = await asyncio.to_thread(
            module.run_unpack_job,
            log_callback=log_cb,
            progress_callback=progress_cb,
        )

        if not result:
            result = {"processed": 0, "failed": 0, "skipped": 0, "total": 0}

        summary = (
            "Unpack complete: processed {processed}, failed {failed}, skipped {skipped}"
        ).format(**result)
        await task_service.update_task(task_id, status="COMPLETED", progress=100, message=summary)
    except Exception as exc:
        await task_service.update_task(task_id, status="FAILED", message=f"Unpack failed: {exc}")
