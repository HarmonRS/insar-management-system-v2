# 部署与运行说明

本文档描述当前有效的部署模型，不再沿用旧版“ENVI 单核 + 结果扫描目录”叙述。

## 1. 当前部署模型

当前项目默认部署在 Windows 主机上，核心组件如下：

- Web 后端：FastAPI
- 后台执行：独立 Worker
- 数据库：PostgreSQL + PostGIS
- 前端：React，通常由 Nginx 或 Vite Dev Server 提供
- D-InSAR 引擎：
  - `sarscape`，运行在 Windows + IDL/ENVI
  - `isce2`，运行在 WSL2 共享运行时
- SBAS-InSAR：
  - 当前主线为 Gamma DIFF + IPTA SBAS
  - 独立生产入口为 `/api/sbas-insar-production`
  - 独立结果入口为 `/api/sbas-insar-products`
- Gamma / PyINT：
  - Gamma 本体采用固定安装目录
  - Python 胶水共享 WSL 环境

## 2. 结果目录

当前结果目录已经收口到统一发布根目录。

推荐配置：

```env
RESULT_PUBLISH_ROOT=D:\production_results
DINSAR_PRODUCT_DIR=D:\production_results\dinsar
TIMESERIES_PRODUCT_DIR=D:\production_results\timeseries
RESULT_QUARANTINE_ROOT=D:\production_results\_quarantine
```

当前目录语义：

- `RESULT_PUBLISH_ROOT`
  统一结果发布根目录。

- `DINSAR_PRODUCT_DIR`
  D-InSAR 产物发布根目录。

- `TIMESERIES_PRODUCT_DIR`
  时序类产物发布根目录；当前 SBAS 结果默认位于其下的 `sbas` 子目录。

- `RESULT_QUARANTINE_ROOT`
  异常产物、待人工处理产物的隔离目录。

典型结构示意：

```text
D:\production_results
├─ dinsar
│  └─ <product_key>
│     └─ runs
│        └─ run_<timestamp>_<engine>_<profile>_<seq>_<suffix>
├─ timeseries
│  └─ sbas
│     └─ <managed SBAS result bundles>
└─ _quarantine
```

说明：

- 历史上的 `backend\result_products` 文件夹不再作为当前文件系统事实来源。
- 数据库中的 `result_products` 表仍然保留，是 catalog 的核心登记表，不要把表名和旧目录混淆。

## 3. 当前推荐环境变量分组

最小必配项见根目录 [`.env.example`](../.env.example)。

### 3.1 数据库与认证

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/insar_management
INIT_ADMIN_USERNAME=admin
INIT_ADMIN_PASSWORD=change_me
DB_SCHEMA_RESET_ON_MISMATCH=false
DB_SCHEMA_RESET_CONFIRM=false
```

### 3.2 服务启动

```env
PORT=18000
BACKEND_BIND_HOST=127.0.0.1
PYTHON_PATH=C:\ProgramData\anaconda3\envs\InSAR\python.exe
NGINX_PATH=C:\nginx\nginx.exe
NGINX_HEALTH_URL=http://127.0.0.1/
```

### 3.3 源数据与轨道目录

```env
UNPACK_SOURCE_DIRS=D:\Archives
INSAR_STORAGE_DIRS=D:\LuTan1_Image_Pool
MONITOR_RADAR_DIRS=D:\LuTan1_Image_Pool
MONITOR_DINSAR_DIRS=D:\production_results\dinsar
MONITOR_ORBIT_DIR=D:\LT1_data_lsarorbit

ORBIT_POOL_ENVI=D:\orbit_pools\envi
ORBIT_POOL_ISCE2=
ORBIT_POOL_LANDSAR=
```

`ORBIT_SOURCE_DIRS` is the source asset layer. LT-1 orbit scans also synchronize the production TXT pool under `ORBIT_POOL_ENVI\LT1A|LT1B`. `PYINT_ORBIT_POOL_TXT` and `GAMMA_SBAS_ORBIT_ROOTS` should point to that same local TXT pool unless a separate Gamma pool is deliberately maintained. `ORBIT_POOL_ISCE2` is legacy and remains empty while `ISCE2_ENABLED=false`.

### 3.4 ENVI / SARscape

```env
IDL_EXECUTABLE=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idl.exe
IDL_WORKBENCH_PATH=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlde.exe
IDL_DINSAR_DEM_BASE_FILE=D:\SRTM30m\SRTMDEM_RSP_SARscape
IDL_WORKER_RUNTIME_DIR=D:\production_runtime\idl_worker
ENVI_TASK_TIMEOUT_SECONDS=21600
```

### 3.5 GF3 SARscape 生产

GF3 当前主线是“ENVI/SARscape 生产原生 `_geo` 证据层，平台标准化成 GeoTIFF 后入库和供洪涝/水体算法消费”。旧 Python/GDAL L1A 预处理链路默认关闭。

```env
GF3_TASK_POOL_ROOT=D:\GaoFen3_Pool\task_pool
GF3_ARCHIVE_SOURCE_DIRS=D:\GaoFen3_Pool\archives
GF3_LEGACY_GDAL_ENABLED=false
GF3_SOURCE_DIRS=
GF3_SARSCAPE_NATIVE_DIRS=D:\GaoFen3_Pool\native_geo
GF3_STORAGE_DIRS=D:\GaoFen3_Pool\catalog
GF3_SARSCAPE_RUNTIME_DIR=D:\GaoFen3_Pool\task_pool\sarscape_runtime
GF3_SARSCAPE_WRAPPER_EXE=D:\Code\Insar_management_system_v2\third_party\GF3_L1A_To_L2_pipeline\dist\windows\gf3wrapper.exe
GF3_SARSCAPE_IDLRT_PATH=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlrt.exe
GF3_SARSCAPE_DEM_PATH=D:\DEM\COPDEM_GLO30_China_4326_DEM
GF3_SARSCAPE_POLARIZATIONS=HH,HV
GF3_SARSCAPE_AUTO_STANDARDIZE=false
GF3_SARSCAPE_CLEAN_AFTER_SUCCESS=true
```

说明：

- `GF3_SARSCAPE_NATIVE_DIRS` 长期保留 `_geo`、`.hdr`、`.sml`、快视、KML、日志和 manifest。
- `GF3_STORAGE_DIRS` 是标准目录池，后续入库、预览、洪涝/水体分析优先消费这里和 `SAR_ANALYSIS_READY_ROOT`。
- `GF3_SARSCAPE_RUNTIME_DIR` 只放 wrapper 配置和运行时临时文件，不应混入原生结果池。
- 如确需恢复旧 L1A 解包/预处理，需要同时设置 `GF3_LEGACY_GDAL_ENABLED=true` 和 `GF3_SOURCE_DIRS`。

### 3.6 WSL 共享运行时

当前推荐采用“一套共享 distro + 一套共享 conda 环境”模式。

```env
WSL_DISTRO=Ubuntu-24.04
WSL_SHARED_CONDA_ENV=insar_wsl_v1
WSL_SHARED_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
WSL_BROKER_JOB_ROOT=D:\production_runtime\wsl_jobs

ISCE2_RUNTIME_ID=isce2_runtime_v1
PYINT_RUNTIME_ID=gamma_pyint_runtime_v1
```

当前 registry 内的运行时定义：

- `isce2_runtime_v1`
  使用共享 python，runner 为 `deploy/wsl/runners/isce2_runner.py`

- `gamma_pyint_runtime_v1`
  使用共享 python，runner 为 `deploy/wsl/runners/gamma_pyint_runner.py`
  Gamma 固定环境脚本为 `deploy/wsl/profiles/gamma_env.sh`

### 3.7 ISCE2 D-InSAR 与旧时序兼容

```env
ISCE2_ENABLED=false
ISCE2_WSL_DISTRO=Ubuntu-24.04
ISCE2_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python

TIMESERIES_ENABLED=false
TIMESERIES_ENV_NAME=insar_wsl_v1
TIMESERIES_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
```

说明：

- ISCE2 可作为 D-InSAR 引擎启用。
- 旧 ISCE2/MintPy 时序生产链默认关闭，不再作为 SBAS 生产入口。
- 如果必须做历史链路对比，需要显式开启 `TIMESERIES_ENABLED=true` 并提供完整旧脚本路径。

### 3.8 Gamma / PyINT / SBAS

```env
PYINT_ENABLED=true
PYINT_WSL_DISTRO=Ubuntu-24.04
PYINT_WSL_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
PYINT_GAMMA_ENV_SCRIPT=D:\Code\Insar_management_system_v2\deploy\wsl\profiles\gamma_env.sh

GAMMA_SBAS_ENABLED=true
GAMMA_SBAS_WSL_DISTRO=Ubuntu-24.04
GAMMA_SBAS_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
GAMMA_SBAS_ENV_SCRIPT=D:\Code\Insar_management_system_v2\deploy\wsl\profiles\gamma_env.sh
GAMMA_SBAS_SOURCE_ROOTS=D:\LuTan1_Image_Pool
GAMMA_SBAS_ORBIT_ROOTS=D:\orbit_pools\envi
```

说明：

- `PYINT_GAMMA_ENV_SCRIPT` 当前仍可配置，但新模型推荐以固定 profile 为准。
- Gamma 二进制不建议塞进 conda；应保持固定安装位置，再由 profile 注入 `PATH`。

## 4. 启动顺序

推荐使用一键脚本：

```powershell
start_system.bat
```

若分开启动：

```powershell
python run_backend.py
python run_worker.py
```

说明：

- Worker 不启动，生产任务、扫描任务、解包任务都不会真正执行。
- 当前系统默认 manual-only 扫描模式，不会自动开启周期调度。

## 5. 启动时自维护链路

后端 `lifespan` 会执行以下步骤：

1. `ensure_database_ready(...)`
2. `database.init_db()`
3. `root_registry_service.sync_from_settings()`
4. `manifest_inventory_service.sync_manifest_roots()`
5. `result_catalog_service.bootstrap_catalog_on_startup_clean()`
6. `psinsar_catalog_service.bootstrap_catalog_on_startup_clean()`
7. `sbas_insar_catalog_service.bootstrap_catalog_on_startup_clean()`
8. `pairing_state_service.bootstrap_pairing_cache_state()`
9. `get_health_status(include_external=False)`

这套链路要求：

- `.env` 中的根目录配置要真实可访问。
- `backend/migrations/001` 到 `007` 必须保持幂等。
- 启动自维护默认是保守模式，不会在 schema 不匹配时自动破坏性重建。

## 6. 数据库自维护边界

当前实现位于 `backend/app/db_maintenance.py`，能力边界如下：

- 支持：
  - 自动创建 `postgis` 扩展
  - 自动创建缺失表
  - 自动补齐缺失列
  - 自动执行 `001` 到 `007` 号 SQL 文件
  - 自动引导管理员账号与灾害点初始化

- 不支持：
  - 列改名
  - 列类型调整
  - 可空性收紧
  - 索引 / 约束漂移修复
  - 删除旧字段或旧表

如需破坏性重建，必须同时显式开启：

```env
DB_SCHEMA_RESET_ON_MISMATCH=true
DB_SCHEMA_RESET_CONFIRM=true
```

生产环境不建议开启。

## 7. 运维自检应看到什么

当前健康检查不仅检查数据库，还会检查系统是否与当前设计一致。

关键项目包括：

- `database`
- `dinsar_result_catalog`
- `timeseries_result_catalog`
- `sbas_insar_result_catalog`
- `dinsar_bridge`
- `source_roots`
- `product_packages`
- `wsl_runtime`
- `pairing_system`

其中：

- `product_packages`
  用于核查 `result_products` 是否全部具备 canonical package manifest、发布目录、运行时信息。

- `wsl_runtime`
  用于核查共享 distro、共享 conda 环境、runner 文件、Gamma profile 是否齐全。

## 8. 解包限流

LT1 解包已经从全局页面锁中剥离，并支持单次任务限流。

当前建议配置：

```env
UNPACK_MAX_FILES_PER_RUN=100
UNPACK_MAX_RUNTIME_MINUTES=360
```

含义：

- 每次解包任务最多处理 100 个压缩包
- 单次解包任务最长运行 6 小时

前端会基于这两个默认值预填单次任务参数弹窗。

## 9. 地图底图服务

地图底图切片不是由本项目直接提供，通常依赖外部 tile server。

当前接入方式：

```env
VITE_TILE_SERVER_URL=http://127.0.0.1:8910
VITE_TILE_SERVER_TOKEN=change_me
```

现场如果使用 `D:\Code\tile-server`，需要确保该项目处于运行状态，否则前端会出现底图切片请求失败。

## 10. 当前推荐校验动作

部署或改动后，至少检查以下三项：

1. 打开前端并确认地图、生产管理和运维自检可正常进入。
2. 访问 `GET /api/health`，确认 database / catalog / product_packages / wsl_runtime 为 `ok`。
3. 触发一次实际生产任务，确认 D-InSAR 或 SBAS 结果能被对应 catalog 收录。

## 11. 相关文档

- [../README.md](../README.md)
- [PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md](PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)
- [SBAS_INSAR_CURRENT_WORKFLOW.md](SBAS_INSAR_CURRENT_WORKFLOW.md)
- [WSL_RUNTIME_REFACTOR_DESIGN_20260422.md](WSL_RUNTIME_REFACTOR_DESIGN_20260422.md)

## DEM Sidecar Migration Warning

If you copy or move a prepared ISCE DEM bundle to a new directory or machine, do not assume
that the sidecar XML files are portable as-is.

Affected files typically include:

- `<dem>.xml`
- `<dem>.vrt`
- `<dem>.wgs84`
- `<dem>.wgs84.xml`
- `<dem>.wgs84.vrt`

The ISCE XML sidecars may still contain absolute historical paths in:

- `file_name`
- `metadata_location`
- `extra_file_name`

Typical failure symptom:

- `verifyDEM` succeeds, but `topo` fails with `FileNotFoundError` pointing at an old
  `/mnt/...` path from the previous machine or previous directory layout.

Recommended post-migration repair:

```powershell
C:\ProgramData\anaconda3\envs\InSAR\python.exe `
  backend\app\isce2_pipeline\repair_dem_sidecars.py `
  --root D:\DEM `
  --repair
```

The managed ISCE2 pipeline now repairs the selected DEM sidecar paths before running, but
the directory-level repair is still recommended after deployment or storage migration so the
whole DEM bundle remains internally consistent.

## ISCE2 Rubbersheeting Runtime Dependency

The managed `ISCE2` `lt1_stripmap` profile enables dense offsets plus range and azimuth
rubbersheeting by default. This is an ISCE2 native workflow step, not an export-time
correction.

The range rubbersheeting implementation imports `astropy.convolution`, so every deployed
or migrated WSL runtime must include `astropy` in `insar_wsl_v1`.

Check:

```bash
/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python -c "from astropy.convolution import convolve; print('astropy_ok')"
```

Repair:

```bash
conda install -n insar_wsl_v1 -c conda-forge astropy
```

If this package is missing, production now fails during preflight instead of after the
long dense-offset stage.

## ISCE2 Ionosphere Runtime Dependency

The managed `ISCE2` `lt1_stripmap` profile now keeps the standard stripmap
`split-spectrum -> low/high-band unwrap -> ionosphere -> geocode` path enabled.
This is part of the native workflow and replaces the older fake `PICKLE/ionosphere`
resume shortcut.

Operational consequences:

- `resume_from=unwrap` now resumes the full stage-2 chain up to `ionosphere`
- `resume_from=geocode` now starts from real `ionosphere` state when available
- the export step prefers geocoded `ionosphere/nondispersive.bil.unwCor.filt`
  when that product exists

The ionosphere implementation in ISCE2 imports `cv2` and `scipy`, so every
deployed or migrated WSL runtime must include both packages.

Check:

```bash
/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python -c "import cv2, scipy; print('ionosphere_ok')"
```

Repair:

```bash
conda install -n insar_wsl_v1 -c conda-forge opencv scipy
```

If these packages are missing, production now fails during preflight instead of
after the long stripmap filtering / unwrap stage.

## Git Clone Bootstrap

Goal: a clean `git clone` on a new Windows host should already contain the
deployment entrypoints required to install dependencies, validate `.env`, and
start the system.

Recommended path:

```powershell
git clone <repo-url>
cd Insar_management_system_v2
Copy-Item .env.example .env
notepad .env
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitFrontend -BuildFrontend
start_system.bat
```

Optional runtime bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitWindowsConda
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitWslConda
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -All
```

`scripts/bootstrap_clone.ps1` is intentionally conservative:

- It copies `.env.example` to `.env` only when `.env` is missing.
- It can create or update the Windows conda env from `environment.yml`.
- It can create or update the shared WSL conda env from
  `deploy/wsl/conda/insar_wsl_v1.environment.yml`.
- It can run `npm ci` and `npm run build` in `frontend/`.
- It runs `scripts/check_runtime_config.py` unless `-SkipChecks` is specified.
- `start_system.bat` now fails early when `frontend/dist/index.html` is missing.
- It does not auto-start backend, worker, or Nginx.
- It does not bypass the existing database self-maintenance safeguards.

Typical deployment sequence:

1. Edit `.env` to match the new server.
2. Run `bootstrap_clone.ps1` with the switches required by that server.
3. Run `start_system.bat`.
4. Check `GET /api/health`.
5. Trigger a small real production task and confirm the result is registered in
   the catalog.
