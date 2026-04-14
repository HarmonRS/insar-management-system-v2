# GAMMA + WSL2 双引擎集成方案

## Context

当前系统使用 ENVI/SARscape 做 D-InSAR 处理，速度慢（单对处理需数十分钟到数小时）。用户有 GAMMA License，希望在 Windows 服务器的 WSL2 中部署 GAMMA 作为第二处理引擎。ENVI 保留不动，用户可在前端选择用哪个引擎。

GAMMA 通过 WSL2 调用完全可行：`subprocess.run(['wsl', '-d', 'Ubuntu', 'bash', '-c', 'gamma_cmd ...'])`，与现有 ENVI 的 subprocess 模式一致。

---

## 架构对比

```
ENVI 链路（保留不动）：
  POST /idl/jobs/dinsar (engine=envi)
  → SystemJob(IDL_RUN_DINSAR) → envi_runner_cli 子进程
  → envi_dinsar.run_dinsar_custom_workflow() → 6步 SARscape
  → envipyengine → taskengine.exe

GAMMA 链路（新增）：
  POST /idl/jobs/dinsar (engine=gamma)
  → SystemJob(GAMMA_RUN_DINSAR) → gamma_runner_cli 子进程
  → gamma_dinsar.run_dinsar_workflow() → 9步 GAMMA CLI
  → subprocess.run(['wsl', ...]) → GAMMA 原生命令
```

两条链路共享：SystemTask/SystemJob 任务队列、progress.json 进度机制、job_worker 监控逻辑。

---

## 新增文件

| 文件 | 职责 |
|------|------|
| `backend/app/services/gamma_service.py` | WSL2 执行器、路径转换（win↔wsl）、配置读取、环境检查、进度写入 |
| `backend/app/services/gamma_dinsar.py` | GAMMA D-InSAR 9步工作流 |
| `backend/app/services/gamma_runner_cli.py` | 子进程 CLI 入口（对标 envi_runner_cli.py） |
| `backend/app/services/gamma_import.py` | LT-1 原始数据 → GAMMA SLC 格式导入 |
| `scripts/check_gamma_wsl.py` | GAMMA + WSL2 环境诊断脚本 |

## 修改文件

| 文件 | 修改内容 |
|------|----------|
| `.env` | 新增 `GAMMA_ENABLED`、`GAMMA_WSL_DISTRO`、`GAMMA_HOME`、`GAMMA_ENV_SCRIPT`、`GAMMA_DEM_FILE` 等配置 |
| `backend/app/config.py` | Settings 类新增 GAMMA 配置字段 |
| `backend/app/services/job_handlers.py` | 新增 `GAMMA_RUN_DINSAR` handler，注册到 `_HANDLERS` |
| `backend/app/routers/idl.py` | `DinsarJobRequest` 新增 `engine` 字段（envi/gamma），路由分发 |
| `frontend/src/IDLAutomationPanel.jsx` | Step 2 新增引擎选择器（radio：ENVI / GAMMA），GAMMA 不可用时灰显 |
| `frontend/src/api/idl.js` | `queueDinsarJob` payload 新增 `engine` |

---

## GAMMA D-InSAR 9步工作流

每步通过 `gamma_service.gamma_exec(cmd, work_dir)` → `wsl -d {distro} bash -c 'source gamma_env.sh && {cmd}'` 执行。

| 步骤 | GAMMA 命令 | 功能 | 对应 ENVI 步骤 |
|------|-----------|------|---------------|
| 1 | LT-1 导入（自定义） | 原始数据 → .slc + .slc.par | SARsImportLuTan1 |
| 2 | `gc_map` | DEM 准备 + lookup table | （ENVI 内置在 Step1） |
| 3 | `create_offset` + `offset_pwr` + `offset_fit` + `SLC_interp` | 配准 | InterferogramGeneration 内部 |
| 4 | `SLC_intf` + `multi_look` | 干涉图生成 | InterferogramGeneration |
| 5 | `adf` | 自适应滤波（Goldstein） | FilterAndCoherence |
| 6 | `cc_wave` | 相干性估计 | FilterAndCoherence |
| 7 | `mcf` | 相位解缠 | PhaseUnwrapping |
| 8 | `dispmap` | 相位→位移 | PhaseToDisplacement |
| 9 | `geocode_back` + `data2geotiff` | 地理编码 + 转 GeoTIFF | PhaseToDisplacement |

---

## 关键设计

### 路径转换
```python
def win_to_wsl(win_path: str) -> str:
    # Z:\Test_data\Task_001 → /mnt/z/Test_data/Task_001
    # D:\SRTM\dem.tif → /mnt/d/SRTM/dem.tif
```

### 进度跟踪
写入与 ENVI 完全相同格式的 `backend/runtime/idl_worker/job_{id}_progress.json`，job_handler 的 keepalive 无需修改。GAMMA 的 `total_steps=9`（ENVI 为 6），进度公式 `(pair_index-1+step/total)/total_pairs` 天然适配。

### 输出格式统一
GAMMA 最终输出转为 GeoTIFF（`data2geotiff`），同时生成 ENVI `.hdr`，使 `extract_disp_results()` 和 D-InSAR 结果扫描能统一识别。

### 前端引擎选择
页面加载时调 `GET /gamma/status` 检查 GAMMA 是否可用。可用时显示 radio 选择器，不可用时 GAMMA 选项灰显标注"(未配置)"。

---

## .env 配置项

```ini
# ============ GAMMA + WSL2 配置 ============
# 总开关（false 则前端不显示 GAMMA 选项）
GAMMA_ENABLED=false

# WSL2 发行版名称（wsl -l -v 查看）
GAMMA_WSL_DISTRO=Ubuntu-22.04

# GAMMA 安装路径（Linux 路径，WSL2 内部）
GAMMA_HOME=/opt/GAMMA_SOFTWARE-20230101

# GAMMA 环境初始化脚本（source 此文件后 GAMMA 命令可用）
GAMMA_ENV_SCRIPT=/opt/GAMMA_SOFTWARE-20230101/gamma_env.sh

# DEM 文件路径（Windows 路径，自动转为 WSL 路径）
GAMMA_DEM_FILE=D:\SRTM30m\srtm_dem.tif

# 处理参数
GAMMA_GEOCODE_PIXEL_SIZE_M=10.0
GAMMA_ADF_WINDOW_SIZE=32
GAMMA_MCF_COH_THRESHOLD=0.3
GAMMA_RANGE_LOOKS=4
GAMMA_AZIMUTH_LOOKS=1
```

---

## 分阶段实施

### Phase 1：基础设施 + 环境验证
- `.env` / `config.py` 配置层
- `gamma_service.py`：`win_to_wsl()`、`wsl_exec()`、`gamma_exec()`、`check_gamma_environment()`
- `scripts/check_gamma_wsl.py` 诊断脚本
- `GET /gamma/status` 端点
- 手动验证：`wsl -d Ubuntu bash -c 'source gamma_env.sh && which par_S1_SLC'`

### Phase 2：LT-1 数据导入（技术风险最高）
- `gamma_import.py`：解析 LT-1 `.meta.xml` → 构建 GAMMA `.slc.par` 参数文件
- **关键验证**：对一个 Task_*/master/ 执行导入，检查 .slc.par 参数是否正确
- 如果 GAMMA 内置 LT-1 支持，直接调用；否则需手工构建参数文件

### Phase 3：D-InSAR 核心工作流
- `gamma_dinsar.py`：9步工作流
- `gamma_runner_cli.py`：CLI 入口
- 逐步调试，每步验证输入输出

### Phase 4：任务调度 + 前端集成
- `job_handlers.py`：新增 GAMMA handler
- `idl.py`：engine 字段分发
- `IDLAutomationPanel.jsx`：引擎选择器
- 端到端测试

---

## 风险点

1. **LT-1 导入**（最大风险）：GAMMA 是否内置 LT-1 读取器需要安装后确认。Plan B：用 GDAL 读 TIFF + 手工构建 .slc.par
2. **WSL2 跨文件系统 I/O**：`/mnt/z/` 比 WSL 原生文件系统慢 ~5x。初期可接受，后续可优化为先拷贝到 WSL 内部处理
3. **GAMMA License**：WSL2 中需配置 FlexLM `LM_LICENSE_FILE`，在 `gamma_env.sh` 中统一设置

---

## 验证

1. `py_compile` 所有新增/修改的 .py 文件
2. `npm run build` 前端构建通过
3. `python scripts/check_gamma_wsl.py` 环境诊断通过
4. 前端选 GAMMA 引擎 → 提交任务 → 进度正常更新 → 输出 GeoTIFF 位移图
5. 前端选 ENVI 引擎 → 行为与改造前完全一致（回归测试）
6. GAMMA 未配置时 → 前端 GAMMA 选项灰显，无法选择
