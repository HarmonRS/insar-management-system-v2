# D-InSAR 配对与分发逻辑梳理

更新时间：2026-05-09

本文按当前代码实现梳理 D-InSAR 从“雷达数据入库”到“配对规划”、“批次保存”、“数据分发”和“多引擎生产执行”的主链路。重点依据源码，而不是早期设计文档。

## 1. 总览

当前 D-InSAR 链路分为两层：

1. 配对规划层：把 `radar_data` 中的影像先预计算成 `pairing_metric_cache` 候选边，再按用户阈值和策略筛选，最后固化为一次 `pairing_network_runs` 和若干 `pairing_network_edges`。
2. 分发执行层：配对结果可保存为 `dinsar_task_batches/items`，再复制成 `Task_*/master`、`Task_*/slave` 生产目录；生产面板再以这个根目录提交到 SARscape、ISCE2 或 PyINT/Gamma 引擎，由 DB job queue 和 worker 执行。

核心入口：

- 配对 API：[backend/app/routers/pairing.py](../backend/app/routers/pairing.py)
- 配对服务：[backend/app/services/spatial_service.py](../backend/app/services/spatial_service.py)
- 配对缓存：[backend/app/services/pairing_cache_service.py](../backend/app/services/pairing_cache_service.py)
- 批次 API：[backend/app/routers/task_batches.py](../backend/app/routers/task_batches.py)
- 数据分发 API：[backend/app/routers/tools.py](../backend/app/routers/tools.py)
- 数据复制执行：[backend/app/copier.py](../backend/app/copier.py)
- 生产提交 API：[backend/app/routers/dinsar_production.py](../backend/app/routers/dinsar_production.py)
- 生产运行状态：[backend/app/services/dinsar_production_service.py](../backend/app/services/dinsar_production_service.py)
- job 队列和 worker：[backend/app/services/job_queue_service.py](../backend/app/services/job_queue_service.py)、[backend/app/services/job_worker.py](../backend/app/services/job_worker.py)

## 2. 数据入库与配对缓存失效

雷达数据扫描在 [backend/app/services/data_service.py](../backend/app/services/data_service.py) 中写入或更新 `radar_data`。每个 scene 使用 `unique_id` 做 upsert；如果发现新 scene 或补齐了轨道文件，会调用 `pairing_state_service.mark_scenes_dirty()` 或 `mark_global_dirty()`。

配对缓存状态由 [backend/app/services/pairing_state_service.py](../backend/app/services/pairing_state_service.py) 管理：

- 全局状态表：`pairing_cache_state`
- 待重算 scene 表：`pairing_dirty_scenes`
- 当前指标版本：`2026.05.raw.v1`
- 当前 master/slave 定向规则：`date_then_scene_uid_v1`

应用启动时会调用 `bootstrap_pairing_cache_state()`，但不会自动全量重建候选边。缓存如果是 `DIRTY`，配对仍可返回旧缓存结果并给 warning；如果是 `FAILED`、`UNINITIALIZED`、`ERROR`，或 scene 数大于 1 但候选边为 0，`/find-pairs` 会拒绝并提示先修复缓存。

## 3. 候选边缓存构建

候选边缓存由 [pairing_cache_service.py](../backend/app/services/pairing_cache_service.py) 写入 `pairing_metric_cache`。

全量重建逻辑：

- 删除全部 `pairing_metric_cache`
- 从 `radar_data m JOIN radar_data s` 重新生成候选边
- 只保留满足硬约束的 pair：
  - `m.id <> s.id`
  - 两景都有 `geom`
  - `imaging_date` 是 8 位日期
  - 两景都有 `orbit_direction` 且方向一致
  - 两景都是可用于 InSAR 的原始复数源：`insar_source_ready = true`
  - 如果两景都有 `look_direction`，要求视向一致
  - 几何相交 `ST_Intersects`
  - 按 `date_then_scene_uid_v1` 只保留一个方向，避免 A-B 和 B-A 双向重复

写入的主要指标：

- `time_baseline_days`：两景日期差的绝对值
- `scene_center_distance_meters`：两景 footprint 质心的球面距离
- `spatial_baseline_meters`：兼容旧 API 的历史字段；新缓存中暂存同一个 footprint 中心距，不能解释为 SAR 空间/垂直基线
- `scene_overlap_ratio`：两景交集面积 / 两景较大 footprint 面积
- `same_satellite`
- `same_satellite_family`：同一卫星族，例如 LT1A/LT1B 归为 `LT1`
- `same_look_direction`
- `same_imaging_mode`
- `same_polarization`
- `pair_uid = md5(master_scene_uid + '|' + slave_scene_uid)`

增量重算逻辑：

- 如果 dirty scene 数过多、占比过高、或缓存为空，会转全量重建
- 否则删除涉及 dirty scene 的缓存边
- 对每个 dirty scene 与其他 scene 重新计算边
- resolved 对应 dirty rows

阈值：

- dirty scene 数量达到 64 触发全量重建
- dirty scene 占 scene 总数比例达到 25% 触发全量重建

## 4. `/find-pairs` 配对查询

前端在 [frontend/src/hooks/usePairingLogic.js](../frontend/src/hooks/usePairingLogic.js) 中把配对参数、AOI 文件或行政区 GeoJSON 组装为 `FormData`，提交到 `POST /api/find-pairs`。

后端入口是 [pairing.py](../backend/app/routers/pairing.py)：

- 解析配对参数为 `PairingRequest`
- 解析 AOI：支持上传 Shapefile 或传入 GeoJSON
- 调用 `spatial_service.find_dinsar_pairs()`
- 返回 `PairingResponse`，包含 pairs、warnings、`network_run_id`、`policy_version`、候选数和入选边数

`PairingRequest` 在 [backend/app/models/schemas.py](../backend/app/models/schemas.py) 中定义，主要参数包括：

- `time_baseline_min/max`
- `overlap_threshold`
- `spatial_baseline_max_meters`
- `coverage_diversity_penalty`
- `require_same_imaging_mode`
- `require_same_polarization`
- `aoi_overlap_threshold`
- master/slave 日期范围
- `strategy`: `all | sbas | sequential | star`
- `num_connections`
- `reference_image_id`
- `allowed_satellites`
- `cross_satellite_pairing`
- `start_date` 兼容旧参数

## 5. 候选池过滤条件

`spatial_service._query_pairing_metric_cache()` 只查询缓存表，不再实时两两计算。基础过滤条件：

- `metric_version == 2026.05.raw.v1`
- `status == READY`
- `time_baseline_days` 在请求范围内
- `scene_center_distance_meters <= spatial_baseline_max_meters`
- `scene_overlap_ratio >= overlap_threshold`
- `same_look_direction = true`
- 如果 `require_orbit_data = true`，master 和 slave 都要有精轨
- 默认要求同卫星族；除非 `cross_satellite_pairing = true`
- 默认要求成像模式一致、极化一致
- 如果 `allowed_satellites` 不为空，master/slave 的卫星名或卫星族都必须在列表内
- 如果传入 master/slave 日期范围，分别约束 `master_imaging_date` 和 `slave_imaging_date`
- 如果有 AOI，master/slave footprint 都要与 AOI 相交
- 如果 `aoi_overlap_threshold` 有值，master/slave 各自覆盖 AOI 的比例都要达标

排序默认按：

1. master 日期升序
2. slave 日期升序
3. overlap 降序
4. pair_uid 升序

## 6. 配对策略

策略选择在 `spatial_service._apply_strategy()`。

### 6.1 all

`all` 策略不再做网络抽稀，直接返回过滤后的全部候选边。每条边的：

- `selection_reason = all_candidate`
- `selection_score` 综合时间基线、footprint 中心距、重叠率、源数据可用性和精轨状态

### 6.2 sequential

`sequential` 策略先从候选池提取 scene，按稳定时间键排序：

- 优先 `acquisition_time_utc`
- 否则 `imaging_date`
- 再按 scene_uid 和 id 打平同日多景

然后每个 scene 向后寻找最多 `num_connections` 个有候选边的后继 scene。不存在于候选池的边不会被补造。

输出边：

- `selection_reason = sequential_neighbor`
- `selection_score` 综合时间基线、footprint 中心距、重叠率、源数据可用性和精轨状态

### 6.3 star

`star` 策略要求参考影像固定作为 master。

如果用户未指定 `reference_image_id`，系统会在时间序列中找靠近中位位置、且能作为 master 的 scene 自动作为参考影像。注意当前实现不会把 slave 侧边反转为 master 侧边；如果参考影像在候选边中只能出现在 slave 侧，这些边会被跳过并给 warning。

输出边：

- `selection_reason = star_reference_master`
- `is_reference_edge = true`
- `reference_image_id` 写入 edge meta

### 6.4 sbas

`sbas` 策略用于构造小基线网络，流程是：

1. 按时间顺序先选相邻 scene 的候选边，形成时间骨架。
2. 如果网络有多个连通分量，优先选能连接分量的候选边。
3. 继续补低度数节点，直到达到目标连接数或达到最大边数。
4. 如果无法形成完整连通图，或存在 0 度/低度数节点，返回 warning。

关键参数：

- `min_degree = min(max(1, num_connections), scene_count - 1)`
- `max_degree = min(max(min_degree + 2, 3), scene_count - 1)`
- `max_edges = min(candidate_count, max(scene_count - 1, scene_count * min_degree))`

候选边评分：

```text
score =
  0.30 * time_score
+ 0.15 * center_distance_score
+ 0.30 * overlap_score
+ 0.10 * aoi_gain
+ 0.10 * source_ready_score
+ 0.05 * orbit_score
- coverage_diversity_penalty * redundancy_penalty
```

其中 `aoi_gain` 和 `redundancy_penalty` 基于 master/slave 交集几何计算；如果有 AOI，会先把交集裁到 AOI 范围。

## 7. 网络运行留痕

每次 `/find-pairs` 都会创建一条 `pairing_network_runs`：

- `network_run_id = pnr_<uuid>`
- `strategy`
- `policy_version = 2026.05.raw-source.v1`
- `request_hash`
- 请求参数 JSON
- AOI hash 和 summary
- 候选边数量、入选边数量、warning 数量

每条入选边写入 `pairing_network_edges`：

- 指向 `pairing_metric_cache`
- `edge_rank`
- `selection_reason`
- `selection_score`
- `selection_meta_json`
- `is_reference_edge`

之后 `RadarPair` 响应会携带：

- `pair_key`
- `pair_uid`
- `metric_cache_ref_id`
- `network_run_id`
- `network_edge_id`
- `policy_version`
- `selection_strategy`
- `selection_score`
- `selection_reason`
- `scene_center_distance_meters`
- `task_name/task_alias`

`task_alias` 由 [dinsar_naming.py](../backend/app/services/dinsar_naming.py) 生成，格式是 `Task_YYYYMMDD_YYYYMMDD`；同名时追加 `_1`、`_2` 保证唯一。

## 8. 批次保存

前端找到 pairs 后，用户勾选结果并调用 `createDinsarBatch()`，提交到 `POST /api/task-batches/dinsar`。

后端 [task_batches.py](../backend/app/routers/task_batches.py) 会创建：

- `dinsar_task_batches`
- `dinsar_task_items`

每条 item 会保存：

- `task_name/task_alias`
- `pair_key`
- `scene_pair_uid`
- `network_run_id`
- `network_edge_id`
- `policy_version`
- `selection_strategy`
- master/slave 文件路径
- master/slave 卫星、日期、成像模式、极化
- 时间基线、footprint 中心距
- 人工审核状态，默认 `PENDING`

前端批次面板可把 item 状态改成：

- `PENDING`
- `IN_PROGRESS`
- `COMPLETED`
- `FAILED`

数据分发默认只复制 `COMPLETED` 状态的条目。

## 9. 数据分发到 Task 目录

数据分发入口是 `POST /api/tools/copy-dinsar-pairs`，代码在 [tools.py](../backend/app/routers/tools.py)。

请求参数：

- `batch_id`
- `dest_dir`
- `copy_statuses`，为空时默认 `["COMPLETED"]`
- `include_orbit_files`，默认 `false`；为 `true` 时把 master/slave 精轨复制到 Task 内的 `orbit/`
- `export_zip`，默认 `false`；为 `true` 时每个 Task 输出为一个 `.zip` 包

后端动作：

1. 校验目标路径。
2. 创建 `SystemTask`，类型为 `COPY_DATA`。
3. 创建 `SystemJob`，job_type 也是 `COPY_DATA`。
4. worker 领取 job 后进入 `job_handlers._handle_copy_data()`。
5. `_handle_copy_data()` 根据 `batch_id` 查询 `dinsar_task_items`，只取 `copy_statuses` 命中的条目。
6. 调用 [backend/app/copier.py](../backend/app/copier.py) 的 `run_dinsar_copy_items()`。

`run_dinsar_copy_items()` 对每个 item 执行：

- 文件夹模式目标目录：`<dest_dir>/<task_alias>/`
- zip 模式目标文件：`<dest_dir>/<task_alias>.zip`
- master 目录：`<task_alias>/master`
- slave 目录：`<task_alias>/slave`
- 如果启用 `include_orbit_files`，从 `radar_data.orbit_file_path` 找 master/slave 精轨并复制到 `<task_alias>/orbit/`
- 直接复制配对时保存的原始产品目录；D-InSAR 分发不再优先使用 `envi_import/`
- 使用 `shutil.copytree(..., dirs_exist_ok=True)` 复制 master/slave
- 写入 `<task_alias>/.dinsar_pair.json`

`.dinsar_pair.json` 是后续生产追踪的关键 sidecar，包含：

- `pair_key`
- `task_name/task_alias`
- master/slave 原始路径和元数据
- `time_baseline_days`
- `spatial_baseline_meters`
- `scene_center_distance_meters`
- `package_format`
- `include_orbit_files`
- `orbit_files`
- `scene_pair_uid/pair_uid`
- `network_run_id`
- `network_edge_id`
- `policy_version`
- `selection_strategy`
- `copied_at`

当前实现不会清空已有 Task 目录，而是合并复制；如果目标已有旧文件，需要人工确认目录状态。

## 10. 生产提交与运行分发

生产入口是 `POST /api/dinsar-production/run`，前端在 [frontend/src/DinsarProductionPanel.jsx](../frontend/src/DinsarProductionPanel.jsx) 手动输入“根目录或单个任务目录”并选择引擎/模板。

支持引擎来自 [backend/app/dinsar_engines/registry.py](../backend/app/dinsar_engines/registry.py)：

- `sarscape`
- `isce2`
- `pyint`
- `landsar`，目前预留，不进入 D-InSAR queued production 主链路

提交流程：

1. 校验 engine 是否注册且可用。
2. 校验 profile 是否属于该 engine。
3. 对 ISCE2/PyINT 调用 engine 的 `validate_root_dir()` 和 `normalize_extra()`。
4. PyINT 会额外做输入资产预检。
5. 当前 SARscape、ISCE2、PyINT 都走 managed production run。
6. 调用 `dinsar_production_service.create_run()`。

`create_run()` 做的事情：

- 根据引擎映射 task_type：
  - SARscape -> `IDL_RUN_DINSAR`
  - ISCE2 -> `ISCE2_RUN`
  - PyINT/Gamma -> `PYINT_RUN`
- 扫描 root 下的 `Task_*` 目录，或把 root 本身当单个 Task 目录
- 从 `.dinsar_pair.json` 解析 pair identity；如果没有 sidecar，则按目录名和路径生成 fallback
- 根据 `rerun_mode` 跳过已有 current pointer 的完成项
- 创建 `SystemTask`
- 创建 `dinsar_production_runs`
- 创建 `dinsar_production_run_items`
- 创建一个 workflow run，只有一个 step：`execute_items`
- workflow step 入队为 `SystemJob`

注意：生产面板目前不直接从 `dinsar_task_batches` 选择批次。实际串联方式是：先在“分发”面板把批次复制到生产根目录，再在“生产”面板提交这个根目录。

## 11. worker 与执行控制

后台 worker 在 [job_worker.py](../backend/app/services/job_worker.py)：

- 周期性 `claim_next_job()`
- DB 查询使用 `FOR UPDATE SKIP LOCKED`
- 按 `priority DESC, id ASC` 领取 `READY/RETRY` job
- 支持 worker heartbeat
- 支持 stale RUNNING job 恢复为 RETRY 或 FAILED
- `run_worker_loop()` 参数支持 job 级并发，但默认并发为 1

`SystemTask` 在 [task_service.py](../backend/app/services/task_service.py) 管理：

- 创建任务时会检查同一 `task_type` 是否已有 `PENDING/RUNNING`
- PostgreSQL 下使用 advisory lock 防止并发创建同类任务
- 因此同一类生产任务天然串行提交

workflow 在 [workflow_service.py](../backend/app/services/workflow_service.py)：

- 创建 workflow run 和 steps
- 没有依赖的 step 立即入队
- job 完成后 mark step completed
- step 全部终态后 workflow run 完成

## 12. 各引擎生产控制器

job handler 在 [job_handlers.py](../backend/app/services/job_handlers.py)。

### 12.1 SARscape

`_handle_idl_run_dinsar()` 如果 payload 有 `production_run_id`，会进入 `_run_dinsar_production_controller()`。

执行特点：

- 使用 `engine_lock_service.acquire("envi_taskengine")`，保证 ENVI/SARscape taskengine 串行
- 对 run item 逐个执行
- 每个 item 创建一个 `DinsarProductionExecution`
- 调用 `build_envi_runner_command()` 启动 runner
- 运行结束后规范化输出目录
- 写 `execution_manifest.json`
- 写 `current/<engine>__<profile>.json`
- 标记 item completed/failed/cancelled
- 成功输出目录会进入 `result_catalog_service.publish_from_sources()`

### 12.2 ISCE2 与 PyINT/Gamma

`_handle_isce2_run()` 和 `_handle_pyint_run()` 在 managed 模式下都进入 `_run_wsl_dinsar_production_controller()`。

执行特点：

- 使用 `engine_lock_service.acquire(f"wsl_dinsar_{engine_code}")`
- 每个 item 构造独立 managed run 目录：
  - run dir
  - native dir
  - workflow dir
  - export dir
  - orbit output dir
- 构造 `RunRequest` 调用 engine 的 `run()`
- engine 返回 `primary_file`、`source_files`、`native_output_dir`
- 校验 primary output 存在
- 写 `execution_manifest.json`
- 写 current pointer
- 标记 item 状态
- 发布成功包，并对结果 catalog 做 rebuild

一个 production run 内部 item 是串行执行的。多个 worker 可以领取不同 job，但同类任务创建限制和 engine lock 会进一步限制实际并发。

## 13. 结果发布与追踪

生产完成后会生成标准包结构，并由 result catalog 接管。`execution_manifest.json` 中保留：

- `run_id`
- `task_id`
- `engine_code`
- `profile_code`
- `runtime_id`
- `task_name/task_alias`
- `pair_key`
- `pair_uid`
- `network_run_id`
- `network_edge_id`
- `policy_version`
- `selection_strategy`
- `source_task_dir`
- `results_root_dir`
- `publish_root_dir`
- `primary_file`
- `source_files`
- `metrics`

catalog 注册逻辑在 [backend/app/services/result_catalog_service.py](../backend/app/services/result_catalog_service.py) 中会继续把 pairing trace 字段写到结果产品，便于从结果反查配对网络。

## 14. 关键表关系

配对规划：

- `radar_data`
- `pairing_cache_state`
- `pairing_dirty_scenes`
- `pairing_metric_cache`
- `pairing_network_runs`
- `pairing_network_edges`

人工批次：

- `dinsar_task_batches`
- `dinsar_task_items`

后台任务：

- `system_tasks`
- `task_logs`
- `system_jobs`
- `system_worker_heartbeats`
- `workflow_runs`
- `workflow_steps`

生产执行：

- `dinsar_production_runs`
- `dinsar_production_run_items`
- `dinsar_production_executions`

## 15. 常用 API 链路

配对健康和修复：

- `GET /api/pairing/health`
- `POST /api/pairing/rebuild-cache`
- `POST /api/pairing/reconcile-dirty?force_full=false`

配对规划：

- `POST /api/find-pairs`
- `GET /api/pairing/networks/{network_run_id}`

批次：

- `POST /api/task-batches/dinsar`
- `GET /api/task-batches/dinsar`
- `GET /api/task-batches/dinsar/{batch_id}/items`
- `PATCH /api/task-batches/dinsar/items/{item_id}`
- `PATCH /api/task-batches/dinsar/{batch_id}/complete-all`

数据分发：

- `POST /api/tools/copy-dinsar-pairs`
- `GET /api/tools/copy-status/{task_id}`

生产：

- `GET /api/dinsar-production/engines`
- `POST /api/dinsar-production/engines/pyint/preview-input-assets`
- `POST /api/dinsar-production/run`
- `GET /api/dinsar-production/runs`

## 16. 当前实现边界

1. 配对查询完全依赖 `pairing_metric_cache`。缓存未初始化、失败、或 scene 足够但 pair 为 0 时不会降级实时计算。
2. `scene_center_distance_meters` 是 footprint 质心距离；`spatial_baseline_meters` 仅为旧 API 兼容字段，不是 SAR 几何中的垂直基线。
3. master/slave 方向在缓存层已经固定为“日期优先、scene_uid 次之”。`star` 策略不会把参考影像位于 slave 的边翻转。
4. `aoi_overlap_threshold` 约束的是每一景对 AOI 的覆盖比例，不是 pair 交集对 AOI 的覆盖比例。
5. 数据分发默认只复制 `COMPLETED` 状态 item；如果用户没有在批次面板审核或一键完成，分发可能没有条目。
6. 数据分发使用 `dirs_exist_ok=True` 合并复制，不会自动清理目标旧内容。
7. 生产提交和批次保存之间没有数据库级直接引用；生产侧通过 `Task_*` 目录和 `.dinsar_pair.json` sidecar 重新恢复 pair trace。
8. 每个 production run 内部 item 串行执行；job worker 可并发，但 task_type 冲突检查和 engine lock 会限制同类引擎并发。
9. `landsar` 已注册为 engine，但当前 `/dinsar-production/run` 仅对 SARscape、ISCE2、PyINT 建立 queued production 主链路。

## 17. 推荐排查路径

配对为空：

1. 查 `GET /api/pairing/health`
2. 看 `pair_count`、`dirty_scene_count`、`status`
3. 必要时执行 `POST /api/pairing/reconcile-dirty` 或 `POST /api/pairing/rebuild-cache`
4. 放宽 `time_baseline_max`、`spatial_baseline_max_meters`、`overlap_threshold`
5. 检查 `insar_source_ready`、`require_orbit_data`、同卫星族、同视向、同模式、同极化约束

分发为空：

1. 查 batch item 是否存在
2. 查 item 状态是否命中 `copy_statuses`，默认只取 `COMPLETED`
3. 查 master/slave 源路径是否存在
4. 查目标目录是否已有旧文件影响判断

生产未执行：

1. 查 `system_tasks` 状态和 task logs
2. 查 `system_jobs` 是否 READY/RUNNING/FAILED
3. 查 worker heartbeat
4. 查 engine lock 是否被长任务持有
5. 查生产根目录是否包含有效 `Task_*/master`、`Task_*/slave`
6. 对 PyINT 先跑输入资产预检
