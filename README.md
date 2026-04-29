# InSAR Management System v2

面向内网运行环境的 InSAR 数据管理、生产调度、结果发布与运维自检系统。

当前项目已经从“以 D-InSAR 结果扫描为中心”的旧形态，收口为“地图主界面 + 生产管理工作台 + 统一结果目录 + 数据库自维护”的架构。

## 当前状态

- 顶级生产入口已经统一为“生产管理”。
- “生产管理”同时承载 D-InSAR 运行、时序 InSAR 运行、D-InSAR 产物、时序 InSAR 产物。
- 前端显示名已经统一为“时序 InSAR”；当前默认接入的是 SBAS 流程，后续可继续扩展 PS-InSAR、SBAS-InSAR 等子类型。
- D-InSAR 当前支持 `sarscape` 与 `isce2` 两类引擎；`gamma / pyint` 已按共享 WSL 运行时模型预留接口。
- 结果目录已经统一发布到 `RESULT_PUBLISH_ROOT`，默认根目录为 `D:\production_results`。

## 当前有效目录约定

- D-InSAR 发布根目录：`D:\production_results\dinsar`
- 时序 InSAR 发布根目录：`D:\production_results\timeseries`
- 隔离目录：`D:\production_results\_quarantine`
- WSL Broker 作业目录：`backend\runtime\wsl_jobs`

说明：

- 现在的文件系统事实来源是 `RESULT_PUBLISH_ROOT` 及其下属目录，不再是历史性的 `backend\result_products` 目录。
- 数据库里的 `result_products / result_assets / result_issues` 表仍然保留，并继续作为结果登记与检索的核心表结构。

## 启动时自维护链路

后端启动时会按固定顺序做自维护：

1. `ensure_database_ready(...)`
2. `database.init_db()`
3. `root_registry_service.sync_from_settings()`
4. `manifest_inventory_service.sync_manifest_roots()`
5. `result_catalog_service.bootstrap_catalog_on_startup_clean()`
6. `psinsar_catalog_service.bootstrap_catalog_on_startup_clean()`
7. `pairing_state_service.bootstrap_pairing_cache_state()`
8. `get_health_status(include_external=False)`

这意味着系统启动后会自动完成数据库结构校验、根目录登记、结果 catalog 自举、配对缓存状态恢复和一次启动健康检查。

## 数据库自维护边界

当前数据库自维护是“增量自愈”模型，不是完整迁移框架。

- 会自动创建缺失表。
- 会自动补齐缺失列。
- 会自动执行 `backend/migrations/001` 到 `007` 的 SQL 文件。
- 只有同时设置 `DB_SCHEMA_RESET_ON_MISMATCH=true` 和 `DB_SCHEMA_RESET_CONFIRM=true`，才允许破坏性重建。

不会自动处理的情况：

- 字段改名
- 字段类型变更
- 可空性收紧
- 索引或约束漂移
- 删除列 / 删除表

## 快速启动

1. 复制 `.env.example` 为 `.env`，按现场路径修改数据库、源数据目录、结果目录、IDL/ENVI、WSL 运行时配置。
2. 准备 PostgreSQL + PostGIS。
3. 准备 Windows Python 运行环境。
4. 如需 ISCE2 / Gamma / 时序 InSAR，准备 WSL2 Ubuntu 运行时与共享 conda 环境。
5. 启动后端与 Worker。

常用启动方式：

```powershell
start_system.bat
```

或分别启动：

```powershell
python run_backend.py
python run_worker.py
```

## Git Clone Deployment

This repository is intended to stay deployable after a clean `git clone` on a new
Windows host.

Recommended bootstrap path:

```powershell
git clone <repo-url>
cd Insar_management_system_v2
Copy-Item .env.example .env
notepad .env
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitFrontend -BuildFrontend
start_system.bat
```

Optional runtime bootstrap commands:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitWindowsConda
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -InitWslConda
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_clone.ps1 -All
```

The clone bootstrap script keeps the existing startup, database self-maintenance,
and health-check chain unchanged. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
for the current deployment model and switch details.

说明：

- Worker 必须常驻，否则生产任务、扫描任务、解包任务不会执行。
- 当前扫描模式默认是 manual-only，不会自动开启后台定时扫描。

## 文档导航

建议阅读顺序：

1. [文档治理约定](docs/DOCUMENTATION_GOVERNANCE.md)
2. [文档索引](docs/INDEX.md)
3. [当前状态快照](docs/CURRENT_STATUS_20260425.md)
4. [部署与运行说明](docs/DEPLOYMENT.md)
5. [数据库自维护审计](docs/DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md)

说明：

- `docs/INDEX.md` 是当前有效文档与历史参考文档的总入口。
- `INIT.md` 是工作笔记，不应替代正式文档。
- 历史材料可从 `docs/archive/INDEX.md` 进入。

当前仍在生效的专题设计文档：

- [多引擎结果目录设计](docs/PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)
- [WSL 共享运行时重构](docs/WSL_RUNTIME_REFACTOR_DESIGN_20260422.md)
- [ISCE2 托管 D-InSAR 实施说明](docs/ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md)
- [ISCE2 生产可靠性加固设计](docs/ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md)

## 2026-04-25 运行态摘要

基于当前代码和现场数据库检查结果：

- 数据库 schema 与 ORM 一致，无缺表、缺列、类型漂移、可空性漂移。
- D-InSAR catalog 已登记 19 个产品。
- 时序 InSAR catalog 当前为 0 个产品。
- 产品包 schema 已统一为 `insar.product-package/v1`。
- WSL 共享运行时健康，当前共享环境为 `insar_wsl_v1`。

详细结果见 [docs/DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md](docs/DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md)。
