# 数据库自维护审计

审计日期：2026-04-25

## 1. 审计目标

本次审计关注两件事：

1. 当前数据库自维护机制，是否仍然符合现在的系统设计。
2. 当前现场数据库，是否已经与现有 ORM / catalog / 运行时设计对齐。

## 2. 当前自维护机制实际做什么

代码入口位于 `backend/app/db_maintenance.py`，系统启动时由 `backend/app/main.py` 调用。

当前自维护能力包括：

- 自动创建 `postgis` 扩展
- 自动创建缺失表
- 自动补齐缺失列
- 自动执行 `backend/migrations/001` 到 `006`
- 自动引导管理员账号
- 自动灌入灾害点数据

当前启动链路中的相关步骤：

1. `ensure_database_ready(...)`
2. `database.init_db()`
3. 根目录登记同步
4. manifest inventory 同步
5. D-InSAR / 时序 catalog 自举
6. pairing state 自举
7. 健康检查

## 3. 当前机制不做什么

当前实现不是完整迁移框架，它不会自动处理：

- 字段改名
- 字段类型变更
- 可空性从宽到严
- 索引 / 约束漂移修补
- 旧字段 / 旧表删除

破坏性重建只有在以下两个开关同时为 `true` 时才允许：

```env
DB_SCHEMA_RESET_ON_MISMATCH=true
DB_SCHEMA_RESET_CONFIRM=true
```

默认情况下这是关闭的，符合当前内网生产环境“保守自维护”的要求。

## 4. 本次现场检查方法

本次检查直接用当前项目代码读取现场数据库并执行：

- `inspect_database_structure(...)`
- `ensure_database_ready(settings.DATABASE_URL, bootstrap_admin=False, seed_hazard=False)`
- `get_health_status(include_external=False, include_details=True, refresh=True)`

使用的是项目当前 `.env` 中配置的 Python 解释器和数据库连接。

## 5. 现场检查结果

### 5.1 schema 结构检查

结果：

- `mismatch = false`
- `reason_count = 0`
- `required_table_count = 42`
- `missing_tables = []`
- `extra_tables = []`
- `missing_columns = {}`
- `type_mismatches = []`
- `nullable_mismatches = []`

结论：

- 现场数据库结构与当前 ORM 一致。
- 当前新增的结果包、运行时、catalog 相关字段已经在数据库中落稳。

### 5.2 自维护执行结果

结果：

- `schema_reset = false`
- `mismatch_detected = false`
- `added_columns = []`
- `bootstrap_initialized = false`

启动时仍会执行以下 SQL 文件：

- `001_st_intersection_agg.sql`
- `002_spatial_functions.sql`
- `003_pairing_enhancement.sql`
- `004_pairing_refactor.sql`
- `005_pairing_task_trace.sql`
- `006_result_pairing_trace.sql`

结论：

- 当前数据库已处于“无需修补”的稳定状态。
- 启动自维护仍会重复执行迁移 SQL，因此这些 SQL 文件必须继续保持幂等。

### 5.3 健康检查结果

结果摘要：

- `health.ok = true`
- `database.ok = true`
- `database.schema_ok = true`
- `database.postgis_ok = true`
- `dinsar_result_catalog.ok = true`
- `timeseries_result_catalog.ok = true`
- `dinsar_bridge.ok = true`
- `source_roots.ok = true`
- `product_packages.ok = true`
- `wsl_runtime.ok = true`
- `pairing_system.ok = true`

关键现场值：

- D-InSAR catalog：
  - `storage_root = D:\production_results\dinsar`
  - `manifest_count = 19`
  - `db_count = 19`

- 时序 InSAR catalog：
  - `storage_root = D:\production_results\timeseries`
  - `manifest_count = 0`
  - `db_count = 0`

- 产品包：
  - `total_count = 19`
  - `canonical_schema = insar.product-package/v1`
  - 所有缺失项计数均为 0

- WSL 共享运行时：
  - `shared_distro = Ubuntu-24.04`
  - `shared_conda_env_name = insar_wsl_v1`
  - `shared_python_path = /home/administrator/miniconda3/envs/insar_wsl_v1/bin/python`
  - `required_runtime_count = 2`
  - `healthy_runtime_count = 2`

## 6. 结论

结论很明确：

- 当前数据库自维护机制与当前系统状态相符。
- 对于当前这轮改造引入的新增字段、catalog、结果包、WSL runtime 信息，它是足够的。
- 当前现场数据库已经对齐当前 ORM 和结果目录设计。

这意味着：

- 现在可以继续在当前 schema 基础上推进 D-InSAR / 时序 InSAR 生产。
- 不需要为了“数据库跟不上代码”而先清空库或强制重建。

## 7. 残余风险

虽然当前是对齐的，但仍有三个明确边界：

### 7.1 它不是 migration framework

后续如果要做以下改动，不能只靠当前自维护：

- 重命名字段
- 修改字段类型
- 增加更严格的非空约束
- 重建索引或唯一约束
- 删除旧结构

### 7.2 SQL 文件必须幂等

因为启动时会重复执行 `001` 到 `006`，任何新增 SQL 文件也必须遵守同样原则。

### 7.3 健康面板已成为设计约束的一部分

当前运维自检不只是“看数据库能不能连”，而是在验证：

- catalog 是否正常
- product package 是否完整
- WSL 运行时是否齐全
- pairing trace 是否一致

因此后续只要改目录模型、结果包模型、运行时模型，就必须同步维护健康检查逻辑。

## 8. 建议

当前建议如下：

1. 保持 `DB_SCHEMA_RESET_ON_MISMATCH=false` 和 `DB_SCHEMA_RESET_CONFIRM=false`。
2. 后续涉及 schema 破坏性调整时，单独编写受控迁移，不要指望启动自维护自动兜底。
3. 新增数据库字段时，优先采用“可空 + 向后兼容 + 健康面板补校验”的方式推进。
4. 每次结果目录、运行时 registry、catalog 设计变动后，都重新跑一次：
   - schema 检查
   - `ensure_database_ready(...)`
   - `GET /api/health`

## 9. 审计结语

截至 2026-04-25，数据库自维护机制与当前系统状态是匹配的，且现场数据库处于健康状态。当前更大的风险已经不在“数据库结构漂移”，而在后续若继续做结果模型或运行时模型重构时，是否同步维护 catalog、健康检查和文档。
