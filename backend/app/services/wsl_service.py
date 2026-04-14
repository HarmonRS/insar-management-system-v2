"""WSL 环境校验与命令执行封装。

提供：
  - WslCheckResult      : 单项检查结果
  - WslEnvironmentReport: 完整校验报告
  - check_wsl_environment(): 执行全部检查项
  - run_wsl_command()   : 在指定 distro 中执行命令
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class WslCheckResult:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class WslEnvironmentReport:
    overall_ok: bool
    distro: str
    checks: List[WslCheckResult] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_ok": self.overall_ok,
            "distro": self.distro,
            "message": self.message,
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "detail": c.detail,
                    "skipped": c.skipped,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# 低层命令执行
# ---------------------------------------------------------------------------

def _decode_subprocess_output(data: bytes) -> str:
    if not data:
        return ""

    encodings: List[str] = []
    if data.count(b"\x00") * 4 >= max(1, len(data)):
        encodings.extend(["utf-16-le", "utf-16-be"])
    encodings.extend(["utf-8", "gbk"])

    for encoding in encodings:
        try:
            return data.decode(encoding).replace("\ufeff", "").replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace").replace("\ufeff", "").replace("\x00", "").strip()


def _find_wsl_executable() -> str:
    return shutil.which("wsl.exe") or ""


def _run_windows_command(
    args: List[str],
    timeout: int = 30,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    result = subprocess.run(
        args,
        capture_output=True,
        text=False,
        timeout=timeout,
        env=proc_env,
        check=False,
    )
    return (
        result.returncode,
        _decode_subprocess_output(result.stdout),
        _decode_subprocess_output(result.stderr),
    )


def run_wsl_command(
    cmd: str,
    distro: Optional[str] = None,
    timeout: int = 30,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    """在 WSL distro 中执行 bash 命令。"""
    wsl_exe = _find_wsl_executable()
    if not wsl_exe:
        return -2, "", "wsl.exe 未找到"

    wsl_args = [wsl_exe]
    if distro:
        wsl_args += ["-d", distro]
    wsl_args += ["bash", "-lc", cmd]

    try:
        return _run_windows_command(wsl_args, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时（{timeout}s）"
    except FileNotFoundError:
        return -2, "", "wsl.exe 未找到"
    except Exception as exc:
        return -3, "", str(exc)


def windows_path_to_wsl(win_path: str, distro: Optional[str] = None) -> str:
    """将 Windows 路径转换为 WSL 路径（调用 wslpath）。"""
    if not win_path:
        return ""
    rc, stdout, _ = run_wsl_command(
        f"wslpath -u '{win_path.replace(chr(39), '')}'",
        distro=distro,
        timeout=10,
    )
    return stdout if rc == 0 else ""


# ---------------------------------------------------------------------------
# 环境校验
# ---------------------------------------------------------------------------

def check_wsl_environment(
    distro: str,
    python_cmd: str = "/home/administrator/miniconda3/envs/isce2/bin/python",
    stripmap_app_path: str = "",    # stripmapApp.py 的 WSL 绝对路径
    pipeline_script_path: str = "", # run_lt1_dinsar_pipeline.py 的 WSL 绝对路径
    dem_path_win: str = "",
    orbit_dir_win: str = "",
    output_dir_win: str = "",
    smoke_test: bool = False,
) -> WslEnvironmentReport:
    """执行全部 WSL 环境校验，返回 WslEnvironmentReport。"""

    checks: List[WslCheckResult] = []

    def add(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
        checks.append(WslCheckResult(name=name, ok=ok, detail=detail, skipped=skipped))

    deferred_checks = [
        "WSL2 支持",
        "Distro 存在",
        "Distro 可启动",
        "bash -lc 可执行",
        "Python 可执行",
        "ISCE2 可 import",
        "stripmapApp 存在",
        "生产脚本存在",
        "DEM 路径可读",
        "轨道目录可读",
        "输出目录可写",
        "Windows→WSL 路径转换",
        "Smoke Test",
    ]

    # 1. wsl.exe 是否存在
    wsl_exe = _find_wsl_executable()
    wsl_installed = bool(wsl_exe)
    add("WSL 已安装", wsl_installed, wsl_exe or "wsl.exe 未找到")
    if not wsl_installed:
        for name in deferred_checks:
            add(name, False, "wsl.exe 未找到，跳过", skipped=True)
        return WslEnvironmentReport(
            overall_ok=False, distro=distro, checks=checks,
            message="WSL 未安装，后续检查跳过",
        )

    # 2. WSL 服务可访问
    rc, stdout, stderr = _run_windows_command([wsl_exe, "--list", "--quiet"], timeout=15)
    service_ok = rc == 0
    service_detail = stdout or stderr or "WSL 服务可访问"
    add("WSL 服务可访问", service_ok, service_detail)
    if not service_ok:
        for name in deferred_checks:
            add(name, False, f"WSL 服务不可访问，跳过：{service_detail}", skipped=True)
        return WslEnvironmentReport(
            overall_ok=False,
            distro=distro,
            checks=checks,
            message=f"WSL 服务不可访问：{service_detail}",
        )

    # 3. WSL2 支持
    try:
        rc, raw, err = _run_windows_command([wsl_exe, "--list", "--verbose"], timeout=15)
        verbose_lines = [line.strip() for line in raw.splitlines() if line.strip()]
        wsl2_ok = rc == 0 and any(line.endswith(" 2") or line.endswith("\t2") for line in verbose_lines)
        add("WSL2 支持", wsl2_ok, raw or err or ("检测到 WSL2 版本" if wsl2_ok else "未检测到 WSL2"))
    except Exception as exc:
        add("WSL2 支持", False, str(exc))

    # 4. 目标 distro 存在
    try:
        raw = stdout
        distro_exists = distro.lower() in raw.lower()
        add("Distro 存在", distro_exists,
            f"'{distro}' 已找到" if distro_exists else f"'{distro}' 未在列表中")
    except Exception as exc:
        distro_exists = False
        add("Distro 存在", False, str(exc))

    if not distro_exists:
        for name in deferred_checks[2:]:
            add(name, False, "Distro 不存在，跳过", skipped=True)
        return WslEnvironmentReport(
            overall_ok=False, distro=distro, checks=checks,
            message=f"Distro '{distro}' 不存在",
        )

    # 4. Distro 可启动
    rc, _, err = run_wsl_command("echo alive", distro=distro, timeout=15)
    distro_ok = rc == 0
    add("Distro 可启动", distro_ok, err if not distro_ok else "启动正常")

    # 5. bash -lc 可执行
    rc, out, err = run_wsl_command("echo bash_ok", distro=distro, timeout=10)
    bash_ok = rc == 0 and "bash_ok" in out
    add("bash -lc 可执行", bash_ok, err if not bash_ok else "bash 正常")

    # 6. Python 可执行
    rc, out, err = run_wsl_command(f"{python_cmd} --version", distro=distro, timeout=15)
    py_ok = rc == 0
    add("Python 可执行", py_ok, out or err)

    # 7. ISCE2 可 import
    rc, out, err = run_wsl_command(
        f'{python_cmd} -c "import isce; print(isce.__version__)"',
        distro=distro, timeout=30,
    )
    isce_ok = rc == 0
    add("ISCE2 可 import", isce_ok, out or err)

    # 8. stripmapApp.py 存在（全路径检查）
    if stripmap_app_path:
        rc, out, err = run_wsl_command(
            f"test -f '{stripmap_app_path}' && echo found",
            distro=distro, timeout=10,
        )
        app_ok = rc == 0 and "found" in out
        add("stripmapApp 存在", app_ok,
            stripmap_app_path if app_ok else f"{stripmap_app_path} 未找到")
    else:
        add("stripmapApp 存在", False, "ISCE2_STRIPMAP_APP 未配置", skipped=True)

    # 9. 生产流水线脚本存在
    if pipeline_script_path:
        rc, out, err = run_wsl_command(
            f"test -f '{pipeline_script_path}' && echo found",
            distro=distro, timeout=10,
        )
        script_ok = rc == 0 and "found" in out
        add("生产脚本存在", script_ok,
            pipeline_script_path if script_ok else f"{pipeline_script_path} 未找到")
    else:
        add("生产脚本存在", False, "ISCE2_PIPELINE_SCRIPT 未配置", skipped=True)

    # 10. DEM 路径可读
    if dem_path_win:
        wsl_dem = windows_path_to_wsl(dem_path_win, distro=distro)
        rc, _, err = run_wsl_command(f"test -r '{wsl_dem}'", distro=distro, timeout=10)
        add("DEM 路径可读", rc == 0, wsl_dem or err)
    else:
        add("DEM 路径可读", False, "ISCE2_DEM_PATH 未配置", skipped=True)

    # 11. 轨道目录可读
    if orbit_dir_win:
        wsl_orbit = windows_path_to_wsl(orbit_dir_win, distro=distro)
        rc, _, err = run_wsl_command(f"test -r '{wsl_orbit}'", distro=distro, timeout=10)
        add("轨道目录可读", rc == 0, wsl_orbit or err)
    else:
        add("轨道目录可读", False, "ORBIT_POOL_ISCE2 未配置", skipped=True)

    # 12. 输出目录可写
    if output_dir_win:
        wsl_out = windows_path_to_wsl(output_dir_win, distro=distro)
        rc, _, err = run_wsl_command(f"test -w '{wsl_out}'", distro=distro, timeout=10)
        add("输出目录可写", rc == 0, wsl_out or err)
    else:
        add("输出目录可写", False, "ISCE2_OUTPUT_ROOT 未配置", skipped=True)

    # 13. Windows→WSL 路径转换
    test_win = os.environ.get("TEMP", r"C:\Windows\Temp")
    wsl_converted = windows_path_to_wsl(test_win, distro=distro)
    path_ok = wsl_converted.startswith("/mnt/")
    add("Windows→WSL 路径转换", path_ok,
        f"{test_win} → {wsl_converted}" if wsl_converted else "转换失败")

    # 14. Smoke test（可选）
    if smoke_test:
        rc, out, err = run_wsl_command(
            f'{python_cmd} -c "import isce; from isceobj.Sensor import Lutan1; print(\'smoke_ok\')"',
            distro=distro, timeout=60,
        )
        smoke_ok = rc == 0 and "smoke_ok" in out
        add("Smoke Test", smoke_ok, out or err)
    else:
        add("Smoke Test", True, "已跳过（ISCE2_SMOKE_TEST_ENABLED=false）", skipped=True)

    # 汇总
    critical = [c for c in checks if not c.skipped]
    overall_ok = all(c.ok for c in critical)
    failed_names = [c.name for c in critical if not c.ok]
    message = "所有检查通过" if overall_ok else f"以下检查未通过：{', '.join(failed_names)}"

    return WslEnvironmentReport(
        overall_ok=overall_ok,
        distro=distro,
        checks=checks,
        message=message,
    )
