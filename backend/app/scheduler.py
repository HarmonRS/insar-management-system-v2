import asyncio
from datetime import datetime
from typing import Optional

from . import database
from .config import settings, split_env_paths
from .services.data_service import data_service
from .services.dinsar_scan_service import dinsar_scan_service
from .services.task_service import task_service


MONITOR_CONFIG = {
    # LT-1 链路
    "radar_dirs": split_env_paths(settings.MONITOR_RADAR_DIRS),
    "orbit_dir": settings.MONITOR_ORBIT_DIR,
    "dinsar_dirs": split_env_paths(settings.MONITOR_DINSAR_DIRS),
    # Sentinel-1 链路
    "s1_source_dirs": split_env_paths(settings.SOURCE_PRODUCT_DIRS),
    "s1_storage_dirs": split_env_paths(settings.SENTINEL1_STORAGE_DIRS),
    "s1_orbit_dirs": split_env_paths(settings.ORBIT_SOURCE_DIRS),
    # GF3 链路
    "gf3_archive_source_dirs": split_env_paths(settings.GF3_ARCHIVE_SOURCE_DIRS),
    "gf3_source_dirs": split_env_paths(settings.GF3_SOURCE_DIRS),
    "gf3_storage_dirs": split_env_paths(settings.GF3_STORAGE_DIRS),
    "mode": "manual",
    "config_source": "env",
}


def add_log(message: str):
    """
    记录日志到标准输出。
    注意：现在日志主要通过 task_service 持久化，这里仅用于控制台快速输出。
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _ = f"[{timestamp}] {message}"
    print(f"[Scheduler] {message}")


async def scan_data_job(target: Optional[str] = None, task_id: Optional[str] = None):
    """
    定时任务：扫描雷达、精轨和Dinsar结果目录并更新数据库。
    target: 'radar', 'orbit', 'dinsar' 或 None (全部)
    """
    radar_dirs = MONITOR_CONFIG.get("radar_dirs", [])
    orbit_dir = MONITOR_CONFIG.get("orbit_dir", "")
    dinsar_dirs = MONITOR_CONFIG.get("dinsar_dirs", [])

    has_radar_orbit = bool(radar_dirs or orbit_dir)
    has_dinsar = bool(dinsar_dirs)

    radar_progress_base = 0
    radar_progress_span = 100
    dinsar_progress_base = 0
    dinsar_progress_span = 100

    if target is None:
        if has_radar_orbit and has_dinsar:
            radar_progress_base = 0
            radar_progress_span = 50
            dinsar_progress_base = 50
            dinsar_progress_span = 50
        elif has_radar_orbit:
            radar_progress_base = 0
            radar_progress_span = 100
        elif has_dinsar:
            dinsar_progress_base = 0
            dinsar_progress_span = 100

    if not radar_dirs and not orbit_dir and not dinsar_dirs:
        add_log("未配置监控目录，跳过扫描。")
        if task_id:
            await task_service.update_task(task_id, status="COMPLETED", message="未配置监控目录，跳过扫描。", progress=100)
        return

    add_log(f"开始扫描任务 (目标: {target or '全部'})...")
    if task_id:
        await task_service.update_task(task_id, message=f"开始扫描任务 (目标: {target or '全部'})...")

    if database.AsyncSessionLocal is None:
        add_log("错误: 数据库未初始化，无法执行扫描任务。")
        if task_id:
            await task_service.update_task(task_id, status="FAILED", message="数据库未初始化")
        return

    async with database.AsyncSessionLocal() as db:
        # 1. 扫描源数据 (雷达 + 精轨)
        if target is None or target in ["radar", "orbit"]:
            current_radar_dirs = radar_dirs if (target is None or target == "radar") else []
            current_orbit_dir = orbit_dir if (target is None or target == "orbit") else ""

            if current_radar_dirs or current_orbit_dir:
                if current_radar_dirs:
                    add_log(f"扫描雷达目录: {current_radar_dirs}")
                if current_orbit_dir:
                    add_log(f"扫描精轨目录: {current_orbit_dir}")
                if task_id:
                    await task_service.update_task(task_id, message="正在扫描雷达和精轨数据...")

                try:
                    summary = await data_service.scan_radar_data(
                        db=db,
                        radar_dirs=current_radar_dirs,
                        orbit_dir=current_orbit_dir,
                        task_id=task_id,
                        progress_base=radar_progress_base,
                        progress_span=radar_progress_span,
                    )
                    msg = summary.get("message", "源数据扫描完成")
                    processed = summary.get("processed_scenes", 0)
                    orbit_files = summary.get("total_orbit_files", 0)
                    cached_previews = summary.get("cached_previews", 0)
                    skipped_previews = summary.get("skipped_previews", 0)
                    cached_raw_previews = summary.get("cached_raw_previews", 0)
                    skipped_raw_previews = summary.get("skipped_raw_previews", 0)
                    missing_previews = summary.get("missing_preview_sources", 0)
                    add_log(
                        f"源数据: {msg} (场景: {processed}, 精轨: {orbit_files}, "
                        f"纠正缓存新增: {cached_previews}, 跳过: {skipped_previews}, "
                        f"原图缓存新增: {cached_raw_previews}, 跳过: {skipped_raw_previews}, "
                        f"无源图: {missing_previews})"
                    )
                except Exception as e:
                    add_log(f"源数据扫描出错: {e}")
                    if task_id:
                        await task_service.update_task(task_id, message=f"源数据扫描出错: {str(e)}")
            elif target is not None:
                add_log(f"未配置{'雷达' if target == 'radar' else '精轨'}目录，跳过。")

        # 2. 扫描 Dinsar 结果
        if target is None or target == "dinsar":
            if dinsar_dirs:
                add_log(f"扫描 Dinsar 结果目录: {dinsar_dirs}")
                if task_id:
                    await task_service.update_task(task_id, message="正在执行 D-InSAR 统一扫描...")
                try:
                    summary = await dinsar_scan_service.run_scan(
                        db,
                        source_directories=dinsar_dirs,
                        task_id=task_id,
                    )
                    msg = summary.get("message", "D-InSAR unified scan completed")
                    rebuild_payload = summary.get("rebuild") or {}
                    compat_payload = summary.get("compat_sync") or {}
                    compat_error = summary.get("compat_error")
                    package_count = int(rebuild_payload.get("manifest_count", 0) or 0)
                    catalog_count = int(rebuild_payload.get("registered", 0) or 0)
                    compat_count = int(compat_payload.get("compat_count", 0) or 0)
                    add_log(
                        f"Dinsar结果: {msg} (packages: {package_count}, "
                        f"catalog: {catalog_count}, compat: {compat_count}"
                        f"{', compat_error: ' + str(compat_error) if compat_error else ''})"
                    )
                except Exception as e:
                    add_log(f"Dinsar结果扫描出错: {e}")
                    if task_id:
                        await task_service.update_task(task_id, message=f"Dinsar结果扫描出错: {str(e)}")

    add_log("扫描任务结束。")
    if task_id:
        await task_service.update_task(task_id, status="COMPLETED", message="扫描任务全部完成", progress=100)


class SchedulerManager:
    def __init__(self):
        self.job_id = "data_monitor_job"

    def start(self):
        add_log("Manual-only mode: scheduler disabled.")

    def shutdown(self):
        add_log("Manual-only mode: scheduler disabled.")

    def update_job(self):
        """
        Manual-only mode: no scheduler jobs are created.
        """
        add_log("Manual-only mode: update_job ignored.")

    async def run_once(self, target: Optional[str] = None, task_id: Optional[str] = None):
        """
        手动触发一次扫描任务。
        """
        add_log(f"手动触发扫描任务 (目标: {target or '全部'})...")
        await scan_data_job(target=target, task_id=task_id)


scheduler_manager = SchedulerManager()
