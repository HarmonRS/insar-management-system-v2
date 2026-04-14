# SBAS 系统改造当前状态

更新日期：2026-04-06

## 1. 当前结论

截至本轮，SBAS / PS-InSAR 已经完成第一阶段的“低侵入接入”改造：

- 已能在系统内创建正式 `ps_timeseries_runs`
- 已能把现有 PS 匹配结果转换为 `stack_input_manifest.json`
- 已能把实验产物 `psinsar.publish.v1` bundle 注册到系统结果目录
- 已能在前端查看 SBAS 运行记录与 `psinsar` 产品目录
- 已补齐 `psinsar` catalog 的启动期 bootstrap 与健康检查接入

这意味着系统已经不再只是“影像管理口”，而是具备了 SBAS 正式生产入口的骨架。当前已经串起实验级四步链：

- `prepare`
- `stack_prep_initial`
- `materialize`
- `stack_prep_refresh`

但仍未接入真正的 ISCE2 stack / MintPy / geocode / publish 自动执行链。

## 2. 已完成范围

### 2.1 后端

- 新增 `ps_timeseries_runs` 运行记录模型
- 新增 `timeseries_service.py`
- 新增 `psinsar_catalog_service.py`
- 新增 `timeseries_production.py`
- 新增 `ps_products.py`
- 作业系统已支持：
  - `TIMESERIES_PREPARE`
  - `TIMESERIES_STACK_PREP`
  - `TIMESERIES_MATERIALIZE`
- `timeseries_service.py` 已补齐 workflow 驱动的：
  - `create_run`
  - `prepare_run`
  - `build_stack_prep`
  - `materialize_run`
  - `list_runs`
  - `get_run_detail`
- 运行详情已可返回 `workflow.steps`
- 启动阶段已同时检查 `dinsar` 与 `psinsar` 两套 catalog
- `/health` 已保留原有 `result_catalog` 兼容字段，并新增：
  - `dinsar_result_catalog`
  - `psinsar_result_catalog`
  - `catalogs`

### 2.2 前端

- 已接入 `TimeseriesProductionPanel`
- 已接入 `PsinsarCatalogPanel`
- 已替换 `ps_production`、`ps_products`、`psinsar_results` 占位页
- `TimeseriesProductionPanel` 已补充 Phase 2 状态色和 workflow 步骤展示

### 2.3 文档

已形成以下系统设计与风险文档：

- `SBAS_SYSTEM_INTEGRATION_AUDIT_20260406.md`
- `SBAS_SYSTEM_EMBEDDING_DESIGN_20260406.md`
- `SBAS_TASK_PATH_MODE_EVALUATION_20260406.md`
- `SBAS_SYSTEM_CHANGE_RISK_ASSESSMENT_20260406.md`
- `SBAS_RESULT_MANAGEMENT_AND_DISPLAY_SPEC_20260406.md`
- `SBAS_FRONTEND_UX_SPEC_20260406.md`
- `SBAS_IMPLEMENTATION_TODO_20260406.md`

## 3. 本轮验证结果

### 3.1 已通过

- 前端 `npm run build` 已通过
- WSL `Ubuntu-24.04` 下，`isce2` 环境对本轮修改后的后端文件执行 `py_compile` 通过
- 启动日志已补充 `PSInSAR-Catalog` 状态输出

### 3.2 当前验证边界

本轮在 WSL `isce2` 环境做后端“导入级冒烟测试”时失败，失败原因不是本轮代码语法问题，而是该环境缺少系统后端依赖：

- `ModuleNotFoundError: No module named 'fastapi'`

这说明当前 `isce2` 环境可以作为 SBAS 处理链环境使用，也可以做后端源码级语法校验，但还不能直接作为系统后端完整运行环境。

## 4. 当前缺口

### 4.1 处理链缺口

系统尚未自动执行以下步骤：

- ISCE2 stack 执行
- MintPy SBAS 执行
- geocode / export / publish
- 产物自动注册收口

### 4.2 环境缺口

若目标是“统一环境”，则还需要复制一份稳定的 ISCE2 环境并补齐系统依赖，形成一个既能跑：

- ISCE2 / MintPy
- 又能支撑 FastAPI 后端依赖

的独立统一环境。当前不建议直接污染现有 D-InSAR 正在使用的生产环境。

## 5. 下一步建议

建议按以下顺序继续：

1. 保持当前 Phase 2 bridge 收口，不继续扩散系统侧改动，先把 `prepare -> stack_prep -> materialize -> refresh` 稳定住。
2. 在实验层继续推进 `isce2_stack -> mintpy -> export_publish` 分步脚本与统一环境验证。
3. 单独复制一份 WSL `isce2` 环境，做“统一环境”安装实验，不动现有 D-InSAR 生产环境。
4. 统一环境验证稳定后，再把真正的 stack / MintPy / publish 接回系统作业编排。

## 6. 当前判断

当前路线是成立的。

成立点不在于“已经跑通全系统自动 SBAS”，而在于：

- 系统边界已经切对
- D-InSAR 老链路基本保持隔离
- SBAS 已有正式运行记录、结果目录和前端入口
- 后续只需要继续往中间补处理链，而不是推倒重来
