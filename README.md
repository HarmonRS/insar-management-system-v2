# InSAR Management System v2

面向内网运行环境的 InSAR 数据管理、生产调度、结果发布与运维自检系统。

当前项目已经收口为：

- 地图主界面；
- 数据管理与生产规划；
- 生产管理工作台；
- D-InSAR 与 Gamma SBAS-InSAR 生产；
- 统一结果目录与 catalog；
- 数据库启动自维护与运行健康检查。

## 当前生产入口

前端顶级入口为“生产管理”，内部视图如下：

```text
生产管理
  - D-InSAR 运行
  - SBAS-InSAR Production
  - SBAS-InSAR 结果
  - D-InSAR 产物
```

当前事实：

- D-InSAR 生产支持 `sarscape`、`isce2`，并保留 Gamma / PyINT D-InSAR 能力。
- SBAS-InSAR 主线是独立 Gamma DIFF + IPTA SBAS 工作流。
- 旧 ISCE2/MintPy 时序生产链和旧 `ps_production` / `ps_products` 页面不再作为生产入口。
- SBAS 结果通过独立 `/api/sbas-insar-products` catalog 管理。

## 目录约定

默认发布根：

```text
D:\production_results
```

关键目录：

```text
D:\production_results\dinsar
D:\production_results\timeseries
D:\production_results\_quarantine
backend\runtime\sbas_insar_production
backend\runtime\wsl_jobs
```

说明：

- `RESULT_PUBLISH_ROOT` 是文件系统结果发布事实来源。
- `result_products / result_assets / result_issues` 是结果登记和检索的数据库事实来源。
- SBAS 托管运行目录在 `backend/runtime/sbas_insar_production`，完成后由 SBAS catalog 登记为结果产品。

## 启动自维护

后端启动时会执行：

1. `ensure_database_ready(...)`
2. `database.init_db()`
3. `root_registry_service.sync_from_settings()`
4. `manifest_inventory_service.sync_manifest_roots()`
5. `result_catalog_service.bootstrap_catalog_on_startup_clean()`
6. `psinsar_catalog_service.bootstrap_catalog_on_startup_clean()`
7. `sbas_insar_catalog_service.bootstrap_catalog_on_startup_clean()`
8. `pairing_state_service.bootstrap_pairing_cache_state()`
9. `get_health_status(include_external=False)`

数据库自维护是增量自愈模型：

- 自动创建缺失表；
- 自动补齐缺失列；
- 自动执行已登记迁移；
- 默认不做破坏性重建。

只有同时设置：

```env
DB_SCHEMA_RESET_ON_MISMATCH=true
DB_SCHEMA_RESET_CONFIRM=true
```

才允许破坏性重建。

## 快速启动

1. 复制 `.env.example` 为 `.env`。
2. 按现场路径配置数据库、源数据目录、轨道目录、结果目录、IDL/ENVI、WSL 和 Gamma。
3. 准备 PostgreSQL + PostGIS。
4. 准备 Windows Python 环境。
5. 如需 WSL/Gamma/ISCE2 能力，准备 Ubuntu-24.04 与共享 conda 环境。
6. 启动后端和 Worker。

常用方式：

```powershell
start_system.bat
```

或分别启动：

```powershell
python run_backend.py
python run_worker.py
```

Worker 必须常驻，否则生产任务、扫描任务和解包任务不会执行。

## Git Clone 部署

```powershell
git clone <repo-url>
cd Insar_management_system_v2
Copy-Item .env.example .env
notepad .env
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitFrontend -BuildFrontend
start_system.bat
```

可选：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitWindowsConda
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitWslConda
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -All
```

## 文档入口

当前文档以 [docs/INDEX.md](docs/INDEX.md) 为准。

建议阅读顺序：

1. [部署与运行说明](docs/DEPLOYMENT.md)
2. [SBAS-InSAR 当前工作流](docs/SBAS_INSAR_CURRENT_WORKFLOW.md)
3. [前端导航架构](docs/FRONTEND_NAVIGATION_ARCHITECTURE.md)
4. [多引擎生产结果管理与路径设计](docs/PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)
5. [文档治理约定](docs/DOCUMENTATION_GOVERNANCE.md)

`INIT.md` 是工作笔记，不是当前架构或部署事实的最高依据。
