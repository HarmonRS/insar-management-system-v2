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
- 时序 InSAR：
  - 当前前端名称为“时序 InSAR”
  - 当前默认接入为 SBAS 流程
- Gamma / PyINT：
  - 运行时接口已预留
  - Gamma 本体采用固定安装目录，Python 胶水共享 WSL 环境

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
  时序 InSAR 产物发布根目录。

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
│  └─ <stack_key>
│     └─ runs
│        └─ run_<timestamp>_<engine>_<profile>_<seq>_<suffix>
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
MONITOR_DINSAR_DIRS=D:\DInSARResult
MONITOR_ORBIT_DIR=D:\LT1_data_lsarorbit

ORBIT_POOL_ENVI=D:\orbit_pools\envi
ORBIT_POOL_ISCE2=D:\orbit_pools\isce2
ORBIT_POOL_LANDSAR=
```

### 3.4 ENVI / SARscape

```env
IDL_EXECUTABLE=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idl.exe
IDL_WORKBENCH_PATH=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlde.exe
IDL_DINSAR_DEM_BASE_FILE=D:\SRTM30m\SRTMDEM_RSP_SARscape
IDL_WORKER_RUNTIME_DIR=D:\Code\Insar_management_system_v2\backend\runtime\idl_worker
ENVI_TASK_TIMEOUT_SECONDS=21600
```

### 3.5 WSL 共享运行时

当前推荐采用“一套共享 distro + 一套共享 conda 环境”模式。

```env
WSL_DISTRO=Ubuntu-24.04
WSL_SHARED_CONDA_ENV=insar_wsl_v1
WSL_SHARED_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
WSL_BROKER_JOB_ROOT=D:\Code\Insar_management_system_v2\backend\runtime\wsl_jobs

ISCE2_RUNTIME_ID=isce2_runtime_v1
PYINT_RUNTIME_ID=gamma_pyint_runtime_v1
```

当前 registry 内的运行时定义：

- `isce2_runtime_v1`
  使用共享 python，runner 为 `deploy/wsl/runners/isce2_runner.py`

- `gamma_pyint_runtime_v1`
  使用共享 python，runner 为 `deploy/wsl/runners/gamma_pyint_runner.py`
  Gamma 固定环境脚本为 `deploy/wsl/profiles/gamma_env.sh`

### 3.6 ISCE2 / 时序 InSAR

```env
ISCE2_ENABLED=true
ISCE2_WSL_DISTRO=Ubuntu-24.04
ISCE2_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python

TIMESERIES_ENABLED=true
TIMESERIES_ENV_NAME=insar_wsl_v1
TIMESERIES_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
```

说明：

- 目前现场共享运行时已经对齐到 `insar_wsl_v1`。
- 当前时序入口默认接入 SBAS 工作流，因此时序链路仍然依赖 ISCE2 / MintPy 实验脚本集合。

### 3.7 Gamma / PyINT

```env
PYINT_ENABLED=true
PYINT_WSL_DISTRO=Ubuntu-24.04
PYINT_WSL_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
PYINT_GAMMA_ENV_SCRIPT=D:\Code\Insar_management_system_v2\deploy\wsl\profiles\gamma_env.sh
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
7. `pairing_state_service.bootstrap_pairing_cache_state()`
8. `get_health_status(include_external=False)`

这套链路要求：

- `.env` 中的根目录配置要真实可访问。
- `backend/migrations/001` 到 `006` 必须保持幂等。
- 启动自维护默认是保守模式，不会在 schema 不匹配时自动破坏性重建。

## 6. 数据库自维护边界

当前实现位于 `backend/app/db_maintenance.py`，能力边界如下：

- 支持：
  - 自动创建 `postgis` 扩展
  - 自动创建缺失表
  - 自动补齐缺失列
  - 自动执行 `001` 到 `006` 号 SQL 文件
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
3. 触发一次实际生产任务，确认结果能发布到 `DINSAR_PRODUCT_DIR` 或 `TIMESERIES_PRODUCT_DIR`，并被 catalog 收录。

## 11. 相关文档

- [../README.md](../README.md)
- [CURRENT_STATUS_20260425.md](CURRENT_STATUS_20260425.md)
- [DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md](DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md)
- [PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md](PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)
- [WSL_RUNTIME_REFACTOR_DESIGN_20260422.md](WSL_RUNTIME_REFACTOR_DESIGN_20260422.md)
