"""SARscape 引擎 — 封装现有 envi_service 逻辑。

profiles:
  - metatask : 调用 run_dinsar_workflow()（SARscape 自动链路）
  - custom6  : 调用 run_dinsar_custom_workflow()（6 步手动链路）
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult


class SarscapeEngine(DinsarEngine):

    @property
    def engine_code(self) -> str:
        return "sarscape"

    @property
    def engine_label(self) -> str:
        return "ENVI / SARscape"

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def get_profiles(self) -> List[EngineProfile]:
        return [
            EngineProfile(
                code="metatask",
                label="SARscape 自动链路",
                description="调用 SARscape MetaTask，全自动完成 D-InSAR 处理",
            ),
            EngineProfile(
                code="custom6",
                label="6 步手动链路",
                description="逐步执行：干涉图生成 → 滤波相干 → 轨道去趋势 → 相位解缠 → GCP → 形变地理编码",
            ),
        ]

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def check_available(self) -> EngineAvailability:
        from ..services import envi_service

        status_raw = envi_service.get_status()
        idl_ok: bool = status_raw.get("idl_installed", False)
        dem_ok: bool = status_raw.get("dem_exists", False)

        checks = [
            {
                "name": "IDL/ENVI 可执行文件",
                "ok": idl_ok,
                "detail": status_raw.get("idl_executable", ""),
            },
            {
                "name": "DEM 文件",
                "ok": dem_ok,
                "detail": status_raw.get("dem_base_file", ""),
            },
        ]

        if idl_ok and dem_ok:
            status = "ok"
            available = True
            message = "ENVI/SARscape 可用"
        elif idl_ok:
            status = "degraded"
            available = True
            message = "IDL 可用但 DEM 未配置，部分功能受限"
        else:
            status = "unavailable"
            available = False
            message = "IDL/ENVI 未安装或路径错误"

        return EngineAvailability(
            engine_code=self.engine_code,
            status=status,
            available=available,
            checks=checks,
            message=message,
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, request: RunRequest) -> RunResult:
        from ..services import envi_service

        profile = request.profile
        root_dir = request.root_dir
        num = request.num_to_process
        timeout = request.timeout_seconds
        job_id = request.job_id

        try:
            if profile == "metatask":
                result = envi_service.run_dinsar_workflow(
                    root_dir=root_dir,
                    num_to_process=num,
                    timeout_seconds=timeout,
                    job_id=job_id,
                )
            elif profile == "custom6":
                result = envi_service.run_dinsar_custom_workflow(
                    root_dir=root_dir,
                    num_to_process=num,
                    timeout_seconds=timeout,
                    job_id=job_id,
                )
            else:
                return RunResult(
                    success=False,
                    engine_code=self.engine_code,
                    profile=profile,
                    job_id=job_id,
                    error=f"未知 profile: {profile}",
                )

            pairs_ok = result.get("processed", 0)
            pairs_fail = result.get("failed", 0)
            return RunResult(
                success=pairs_fail == 0 or pairs_ok > 0,
                engine_code=self.engine_code,
                profile=profile,
                job_id=job_id,
                pairs_processed=pairs_ok,
                pairs_failed=pairs_fail,
                detail=result,
            )

        except Exception as exc:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=profile,
                job_id=job_id,
                error=str(exc),
            )
