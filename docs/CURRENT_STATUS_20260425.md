# 当前状态快照

更新时间：2026-04-25

## 1. 项目形态

当前项目已经形成以下稳定形态：

- 主界面仍然是地图工作区。
- 顶级生产入口已经统一为“生产管理”。
- “生产管理”同时包含：
  - D-InSAR 运行
  - 时序 InSAR 运行
  - D-InSAR 产物
  - 时序 InSAR 产物
- 时序入口的前端显示名已经统一为“时序 InSAR”。
- 当前时序链路默认接入的是 SBAS 实现，而不是传统 PS-InSAR 单一路径。

## 2. 当前生产引擎

### D-InSAR

- `sarscape`
  当前可用，已有 19 个发布产品进入 catalog。

- `isce2`
  已完成托管式接入，运行在 WSL 共享运行时中。

- `gamma / pyint`
  运行时接口与目录已经预留，后续可继续落生产设计。

### 时序 InSAR

- 当前产品名称统一为“时序 InSAR”。
- 当前默认执行路径是 SBAS。
- 后续可在该顶级入口下继续扩展 `psinsar`、`sbas-insar` 等类型。

## 3. 结果目录现状

当前结果目录已经统一收口到 `D:\production_results`：

```text
D:\production_results
├─ dinsar
├─ timeseries
└─ _quarantine
```

当前健康检查中的目录状态：

- `D:\production_results\dinsar`
  存在，catalog 正常。

- `D:\production_results\timeseries`
  存在，catalog 正常。

- `D:\production_results\_quarantine`
  已作为统一隔离根目录纳入配置模型。

说明：

- `backend\result_products` 旧目录已经不是当前文件系统事实来源。
- 数据库 `result_products` 表仍然保留，是发布产品登记表，不要与旧目录混淆。

## 4. 当前启动自维护链路

后端启动时会依次执行：

1. 数据库自维护
2. SQLAlchemy 会话初始化
3. 根目录登记同步
4. manifest inventory 同步
5. D-InSAR catalog 自举
6. 时序 InSAR catalog 自举
7. pairing cache 状态自举
8. 启动健康检查

这说明当前系统已经不是“纯手工维护目录 + 手工修库”的模式，而是有稳定的启动自维护链路。

## 5. 2026-04-25 现场健康摘要

本次审计现场读取到的核心状态如下：

- 数据库：
  - `ok = true`
  - `schema_ok = true`
  - `postgis_ok = true`
  - `required_table_count = 42`

- D-InSAR catalog：
  - `storage_root = D:\production_results\dinsar`
  - `manifest_count = 19`
  - `db_count = 19`
  - `needs_rebuild = false`

- 时序 InSAR catalog：
  - `storage_root = D:\production_results\timeseries`
  - `manifest_count = 0`
  - `db_count = 0`
  - `needs_rebuild = false`

- 产品包：
  - `total_count = 19`
  - `canonical_schema = insar.product-package/v1`
  - 缺失 manifest / publish dir / processor / runtime / native output 均为 0

- WSL 共享运行时：
  - `shared_distro = Ubuntu-24.04`
  - `shared_conda_env_name = insar_wsl_v1`
  - `shared_python_path = /home/administrator/miniconda3/envs/insar_wsl_v1/bin/python`
  - `required_runtime_count = 2`
  - `healthy_runtime_count = 2`

- 配对系统：
  - `status = READY`
  - `scene_count = 1500`
  - `pair_count = 91737`
  - `dirty_scene_count = 0`

## 6. 现场源目录状态

当前健康检查确认以下源目录可访问：

- `D:\LuTan1_Image_Pool`
- `D:\LT1_data_lsarorbit`
- `D:\DInSARResult`

即当前 `source_roots` 为全绿状态，没有不可访问目录。

## 7. 当前数据库设计是否匹配

结论：

- 当前 ORM 与现场数据库匹配。
- 当前 catalog、结果包、WSL 运行时和 pairing 状态与现有架构一致。
- 当前数据库自维护机制适合现在这批“新增字段、统一结果目录、增加运行时登记”的改动。

边界：

- 它仍然只是“增量补齐型”自维护，不是全功能 migration 框架。
- 如果后续要做字段改名、类型调整、约束重构，仍然需要显式迁移方案。

## 8. 当前最需要保持一致的事实

以下几件事已经是当前系统事实，后续文档和代码都应围绕它们展开：

- 顶级生产入口是“生产管理”，不是“生产规划”里的临时子入口。
- 时序 InSAR 是顶级产品族，当前默认接入 SBAS。
- 结果发布根目录是 `RESULT_PUBLISH_ROOT`，不是历史散落目录。
- WSL 运行时是共享模型，当前共享环境为 `insar_wsl_v1`。
- 数据库自维护默认保守，不自动做破坏性重建。
