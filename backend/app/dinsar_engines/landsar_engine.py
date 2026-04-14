"""LANDSAR 引擎占位实现。

本轮不实现算法，仅保留接口和前端占位。
check_available() 永远返回 not_implemented。
run() 直接抛出 NotImplementedError。
"""
from __future__ import annotations

from typing import List

from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult


class LandsarEngine(DinsarEngine):

    @property
    def engine_code(self) -> str:
        return "landsar"

    @property
    def engine_label(self) -> str:
        return "LANDSAR（预留）"

    def get_profiles(self) -> List[EngineProfile]:
        return [
            EngineProfile(
                code="standard",
                label="标准链路（未实现）",
                description="LANDSAR 处理链路，本版本暂未实现",
            ),
        ]

    def check_available(self) -> EngineAvailability:
        return EngineAvailability(
            engine_code=self.engine_code,
            status="not_implemented",
            available=False,
            checks=[],
            message="LANDSAR 引擎尚未实现，仅作接口预留",
        )

    def run(self, request: RunRequest) -> RunResult:
        return RunResult(
            success=False,
            engine_code=self.engine_code,
            profile=request.profile,
            job_id=request.job_id,
            error="LANDSAR 引擎尚未实现",
        )
