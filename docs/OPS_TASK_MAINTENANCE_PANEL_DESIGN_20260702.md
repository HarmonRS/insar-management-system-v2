# 运维任务维护面板设计

日期：2026-07-02

## 背景

当前系统的生产任务、扫描任务和数据准备任务已经统一沉淀到 `system_tasks`、`system_jobs`、`task_logs` 以及各业务 run 表中。实际运维中经常出现以下场景：

- 用户提交 D-InSAR 生产或数据准备任务后，外部进程失败、服务器断电或 worker 心跳超时。
- 数据库任务已经 `FAILED`，但业务 item/execution 仍残留 `RUNNING`。
- 磁盘上存在半成品目录，但数据库记录已经失败或需要清理。
- 需要先判断任务是否仍在真实执行，再决定是否清理数据库记录和磁盘目录。

过去这些动作依赖人工查表和命令行处理。第一版运维面板要把这个过程产品化：用户能查看任务状态、诊断失败原因、预览清理影响，并由管理员执行清理。面板不负责重跑，清理完成后用户回到业务页面重新提交。

## 目标

1. 在“运行维护”中增加“任务维护”能力。
2. 自动列出异常任务，尤其是 D-InSAR 生产、D-InSAR 数据准备、扫描、配对缓存重建等长耗时任务。
3. 对单个任务提供诊断详情：任务、job、run、item、execution、日志、结果登记和磁盘路径。
4. 对可清理任务提供清理预览，明确会删除哪些数据库记录和哪些目录。
5. 管理员确认后执行清理，并写入审计日志。
6. 不提供重跑按钮，不在运维页重新提交业务任务。

## 非目标

- 不做自动重跑。
- 不做跨业务的复杂恢复编排。
- 不在第一版支持任意 SQL 或任意路径删除。
- 不清理源资产库、DEM 库、精密轨道库、代码目录。
- 不处理仍有真实生产进程运行的任务；这种任务应先取消或等待结束。

## 异常任务定义

第一版将以下任务纳入维护列表：

- `system_tasks.status` 属于 `FAILED`、`PARTIAL_SUCCESS`、`CANCELLED`。
- `system_jobs.status` 为 `RUNNING` 但 `heartbeat_at` 超过阈值。
- `system_tasks.status` 为 `RUNNING`，但无活跃 job 且无真实进程证据。
- D-InSAR production run 终态失败，但 item/execution 中存在 `RUNNING` 或 `PENDING` 残留。
- 数据库记录引用的生产结果目录不存在。
- 生产结果目录存在，但没有匹配的 run 或 result product 登记。

第一版优先实现：

- `LANDSAR_RUN` / `LANDSAR_CLUSTER_RUN` / `PYINT_RUN` / `IDL_RUN_DINSAR`
- `COPY_DATA`
- `PAIRING_CACHE_REBUILD`
- 扫描类任务只读展示，清理动作后续扩展

## 后端设计

新增运维任务维护路由：

`backend/app/routers/ops_maintenance.py`

建议接口：

- `GET /api/ops-maintenance/tasks`
  - 返回异常任务列表。
  - 支持 `task_type`、`status`、`limit`、`offset`。
- `GET /api/ops-maintenance/tasks/{task_id}/diagnosis`
  - 返回单任务诊断详情。
- `POST /api/ops-maintenance/tasks/{task_id}/cleanup-preview`
  - 生成清理预览，不执行删除。
- `POST /api/ops-maintenance/tasks/{task_id}/cleanup`
  - 管理员确认后执行清理。

### 列表返回模型

每条任务至少包含：

- `task_id`
- `task_type`
- `task_name`
- `status`
- `created_at`
- `started_at`
- `ended_at`
- `updated_at`
- `progress`
- `message`
- `run_id`
- `job_id`
- `job_status`
- `issue_level`: `info` / `warning` / `danger`
- `issue_summary`
- `cleanup_supported`
- `cleanup_blocked_reason`
- `counts`
  - `jobs`
  - `logs`
  - `run_items`
  - `executions`
  - `result_products`
  - `disk_paths`

### 诊断详情模型

诊断详情包含：

- `task`
- `jobs`
- `production_run`
- `production_item_counts`
- `production_execution_counts`
- `result_products`
- `compat_results`
- `recent_logs`
- `disk_paths`
- `process_check`
- `diagnosis`
  - `summary`
  - `findings`
  - `cleanup_supported`
  - `cleanup_blockers`

### 清理预览模型

清理预览必须返回完整影响范围：

- `database_deletes`
  - `system_tasks`
  - `system_jobs`
  - `task_logs`
  - `dinsar_production_runs`
  - `dinsar_production_run_items`
  - `dinsar_production_executions`
  - `result_products`
  - `dinsar_results`
  - `workflow_runs`
  - `workflow_steps`
  - `workflow_artifacts`
- `disk_deletes`
  - `path`
  - `kind`: `task_pool` / `production_result` / `run_log`
  - `exists`
  - `size_bytes` 可选
  - `allowed`
- `blocked`
- `blockers`
- `requires_admin`

### 清理执行

执行接口只接受预览中允许的清理项，不接受前端传来的任意路径。

请求体：

```json
{
  "confirm": true,
  "delete_task_records": true,
  "delete_logs": true,
  "delete_production_records": true,
  "delete_result_products": true,
  "delete_production_dirs": true,
  "delete_task_pool_dir": true
}
```

执行顺序：

1. 重新生成清理预览。
2. 校验任务不是 `PENDING` / `RUNNING`，或已被诊断为 stale 且无真实进程。
3. 校验所有待删目录在白名单根目录内。
4. 删除数据库记录。
5. 删除磁盘目录。
6. 写审计日志。
7. 返回复查结果。

## 路径白名单

允许删除的目录根：

- `settings.DINSAR_TASK_ROOTS` 或系统配置的数据准备根，例如 `D:\Task_Pool\DInSAR`
- `settings.PRODUCTION_RESULTS_ROOT` 下的 D-InSAR 发布目录，例如 `D:\production_results\dinsar`
- 系统 runtime run log 目录中与 run_id 精确匹配的日志文件

禁止删除：

- 磁盘根目录
- 项目代码目录
- 源产品资产目录
- 精密轨道目录
- DEM 目录
- 未由系统登记的任意路径

## 前端设计

入口放在 `HealthCheckPanel.jsx` 的“低频维护”区域，新增“任务维护”区块。整体延续现有运维页视觉：高密度、少装饰、状态明确。

### 页面结构

1. 顶部摘要条
   - 异常任务数
   - 可清理任务数
   - 阻塞清理任务数
   - 最近一次清理时间

2. 筛选栏
   - 任务类型
   - 状态
   - 关键词
   - 刷新按钮

3. 异常任务表
   - 状态
   - 类型
   - 名称
   - 进度/统计
   - 失败摘要
   - 更新时间
   - 操作：诊断

4. 诊断详情抽屉或内联详情
   - 基础任务信息
   - job 状态
   - D-InSAR run 状态
   - item/execution 统计
   - 磁盘路径检查
   - 最近日志
   - 诊断结论

5. 清理预览
   - 数据库删除数量
   - 磁盘目录列表
   - 阻塞原因
   - 管理员确认按钮

### 交互原则

- 非管理员可以查看诊断，但不能执行清理。
- 清理按钮只在预览完成且无阻塞时启用。
- 真实进程仍在运行时禁止清理。
- 清理完成后刷新列表，不提供“重新执行”入口。
- 删除影响必须在按钮前可见，不用弹窗隐藏关键信息。

## 审计日志

每次清理写入审计日志：

- 操作人
- 操作时间
- `task_id`
- `run_id`
- 删除选项
- 删除前状态快照
- 数据库删除数量
- 磁盘删除路径
- 删除结果
- 失败原因

## 第一版实现边界

第一版聚焦 D-InSAR 生产和数据准备任务：

- `LANDSAR_RUN`
- `LANDSAR_CLUSTER_RUN`
- `PYINT_RUN`
- `IDL_RUN_DINSAR`
- `COPY_DATA`

扫描任务、SBAS 任务和结果交付任务先进入只读诊断列表，后续按同一接口扩展清理策略。

## 验收标准

1. 运维页能列出失败或部分成功的 D-InSAR/COPY_DATA 任务。
2. 点击任务能看到 task/job/run/item/execution/log/path 诊断详情。
3. 对失败且无真实进程的 D-InSAR 任务能生成清理预览。
4. 管理员能执行清理，普通用户不能。
5. 清理后关联数据库记录为 0，登记过的生产目录被删除。
6. 清理动作写入审计日志。
7. 前端构建通过。
