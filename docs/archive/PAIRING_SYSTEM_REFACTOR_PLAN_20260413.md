# InSAR 配对系统重构方案

更新日期：2026-04-13  
状态：待实施  
适用范围：`Z:\Code\Insar_management_system_v2` 当前代码基线

## 1. 文档目标

这份文档不是抽象算法说明，而是基于当前系统现状给出的可落地重构方案，目标是：

- 重构 D-InSAR 配对内核，解决当前正确性、稳定性和可维护性问题
- 尽量复用现有数据库自动维护、root/cursor 自维护、manifest inventory、自检面板
- 明确开发机条件下的迁移策略
- 为后续实现提供分阶段执行顺序、表设计、接口边界和验收口径

## 2. 当前系统基础与约束

### 2.1 当前可直接复用的系统能力

当前系统已经具备以下基础设施，重构应直接接入，而不是另起一套平行机制：

- 数据库启动自维护
  - `backend/app/db_maintenance.py`
  - `ensure_database_ready()` 会在启动时创建表、补缺列、应用 SQL migration，并可在开发机上按环境变量执行整库 schema reset
- root 与 cursor 自维护
  - `backend/app/services/root_registry_service.py`
  - 启动时会根据 `.env` 自动同步 `managed_roots` 与 `scan_cursors`
- manifest inventory 自维护
  - `backend/app/services/manifest_inventory_service.py`
  - 会维护 `path_inventory`、`scan_cursors.last_seen_*`、fingerprint 与 root 缺失状态
- 结果目录自维护
  - `backend/app/services/dinsar_scan_service.py`
  - D-InSAR 统一扫描链路已经是“发布 -> manifest inventory -> catalog rebuild -> compat sync”
- 健康检查和一致性检查
  - `backend/app/services/health_service.py`
  - `frontend/src/HealthCheckPanel.jsx`
  - 当前自检已包含：数据库 schema、worker、catalog、source roots、D-InSAR bridge、一致性统计、系统维护入口

### 2.2 当前配对系统的核心问题

现有配对系统的主要问题不是功能不够多，而是核心语义不稳定：

- SQL 路径和 Python fallback 路径不是同一算法
- 同日影像会出现反向重复配对
- `start_date` 在 SQL 主路径上已失效，但接口仍保留
- `star` 策略没有固定参考像角色
- `sequential` 对同日多景不稳定
- `all` 策略排序与前端描述不一致
- `aoi_overlap_threshold=0` 的语义前后端不一致
- 当前任务命名与配对唯一性混在一起，业务主键不稳定

### 2.3 当前系统约束

- 当前扫描模式是 manual-only，调度器默认关闭
  - `backend/app/scheduler.py`
- `radar_data` 是当前源影像事实表，配对系统必须以它为唯一源
- `dinsar_task_batches` / `dinsar_task_items` 已经是生产任务落地表
- `result_products` / `dinsar_product_profiles` 已经承担结果资产追踪
- 健康检查和统计接口已上线，新增配对系统状态应并入现有面板

## 3. 重构总目标

本轮重构的目标不是继续修补 `find_dinsar_pairs_v2`，而是将配对系统改造成四层结构：

1. 影像事实层
2. 配对指标缓存层
3. 网络选择层
4. 任务物化层

核心原则：

- 候选对生成与网络选择分层
- 所有策略共享同一候选池
- 所有配对方向统一规范化
- 所有结果可追溯到策略版本、参数快照和候选边来源
- 不再保留“算法不等价的 fallback”

## 4. 目标架构

### 4.1 Layer A：影像事实层

沿用现有 `radar_data` 表作为唯一源影像事实表。

重构要求：

- `radar_data.id` 继续作为数据库主键
- 引入明确的场景业务键 `scene_uid`
  - 优先复用 `radar_data.unique_id`
  - 若历史数据存在 `unique_id` 缺失或不稳定，启动时执行一次回填
  - 回填规则：`hash(normalized file_path)`，并写入新列或标准化字段
- 所有配对相关对象都基于 `scene_uid` 或 `radar_data.id` 建立引用

建议：

- 本轮实现阶段直接在 `radar_data` 上新增：
  - `scene_uid`
  - `scene_signature`
  - `pairing_eligible`
  - `pairing_updated_at`
- 如果希望减少变更面，也可以只新增 `scene_uid` 与 `pairing_updated_at`

### 4.2 Layer B：配对指标缓存层

这是本次重构的核心。

目标：

- 将“任意两个影像是否可配、其基础指标是什么”从请求时临时计算，改为可维护的持久化缓存
- 缓存只保存与 AOI 无关的基础指标
- AOI 相关指标在请求时动态叠加计算

建议新增表：

#### `pairing_metric_cache`

一行代表一个规范化后的影像对。

建议字段：

- `id`
- `master_scene_ref_id`
- `slave_scene_ref_id`
- `master_scene_uid`
- `slave_scene_uid`
- `pair_uid`
- `metric_version`
- `orientation_rule_version`
- `time_baseline_days`
- `spatial_baseline_meters`
- `scene_overlap_ratio`
- `orbit_direction`
- `same_satellite`
- `same_imaging_mode`
- `same_polarization`
- `master_imaging_date`
- `slave_imaging_date`
- `master_satellite`
- `slave_satellite`
- `master_imaging_mode`
- `slave_imaging_mode`
- `master_polarization`
- `slave_polarization`
- `master_file_path`
- `slave_file_path`
- `status`
- `computed_at`

唯一约束：

- `unique(master_scene_ref_id, slave_scene_ref_id, metric_version)`

关键约定：

- `master/slave` 不是请求期可变概念，而是规范化方向
- 规范化规则：
  - 早时间 = master
  - 同时间时，较小 `scene_uid` 或较小 `id` = master

### 4.3 Layer C：缓存状态与脏队列

为了接入现有“自动维护 + 健康检查”体系，建议新增两张状态表。

#### `pairing_cache_state`

建议设计为单例表或按 `cache_scope='global'` 管理。

字段建议：

- `id`
- `cache_scope`
- `metric_version`
- `status`
- `scene_count`
- `pair_count`
- `dirty_scene_count`
- `last_full_rebuild_at`
- `last_incremental_reconcile_at`
- `last_error`
- `updated_at`

状态建议：

- `READY`
- `DIRTY`
- `REBUILDING`
- `DEGRADED`
- `FAILED`

#### `pairing_dirty_scenes`

用于增量维护。

字段建议：

- `id`
- `scene_ref_id`
- `scene_uid`
- `reason`
- `marked_at`
- `resolved_at`
- `status`

唯一约束建议：

- `unique(scene_ref_id, status='PENDING')` 或代码层去重

### 4.4 Layer D：网络选择层

网络选择层不直接查 `radar_data`，而是查 `pairing_metric_cache`。

建议新增：

#### `pairing_network_runs`

代表一次配对请求或一次已保存的网络。

字段建议：

- `id`
- `network_run_id`
- `strategy`
- `policy_version`
- `request_hash`
- `request_params_json`
- `aoi_source`
- `aoi_hash`
- `aoi_summary_json`
- `candidate_count`
- `selected_edge_count`
- `warning_count`
- `status`
- `fallback_used`
- `created_by`
- `created_at`

#### `pairing_network_edges`

代表某个网络 run 选中的边。

字段建议：

- `id`
- `network_run_ref_id`
- `metric_cache_ref_id`
- `edge_rank`
- `selection_reason`
- `selection_score`
- `selection_meta_json`
- `is_reference_edge`
- `created_at`

唯一约束：

- `unique(network_run_ref_id, metric_cache_ref_id)`

### 4.5 Layer E：任务物化层

当前 `dinsar_task_batches` / `dinsar_task_items` 可继续保留。

但建议增加可追溯字段：

在 `dinsar_task_items` 上新增：

- `network_run_id`
- `network_edge_id`
- `policy_version`
- `selection_strategy`
- `scene_pair_uid`

在结果侧建议同步补充：

- `result_products.pair_key` 继续保留
- 在 `dinsar_product_profiles` 或 `params_json` 中补充：
  - `network_run_id`
  - `network_edge_id`
  - `policy_version`
  - `master_scene_uid`
  - `slave_scene_uid`

这样最终可以把：

- 配对网络
- 任务项
- 生产结果

串成一条可追踪链路。

## 5. 配对算法设计

### 5.1 候选对生成

候选对生成只负责回答：

- 这两个场景在基础事实上是否可配
- 基础指标是什么

不负责回答：

- 用什么策略选
- 最后是否进入任务

基础硬约束：

- `master != slave`
- 方向规范化后只保留一个方向
- 同轨向
- 日期格式合法
- 几何相交

基础指标：

- `time_baseline_days`
- `spatial_baseline_meters`
- `scene_overlap_ratio`

注意：

- `same_satellite`
- `same_imaging_mode`
- `same_polarization`

只作为缓存事实，不在缓存层提前删边，以便不同请求复用同一缓存。

### 5.2 请求期过滤

请求期过滤发生在 `pairing_metric_cache` 之上：

- `time_baseline_min/max`
- `spatial_baseline_max_meters`
- `overlap_threshold`
- `allowed_satellites`
- `cross_satellite_pairing`
- `require_same_imaging_mode`
- `require_same_polarization`
- `master_date_from/to`
- `slave_date_from/to`
- `aoi_overlap_threshold`

AOI 处理原则：

- AOI 不进入持久化 pair cache 主键
- AOI 相关 overlap 在请求时动态计算
- `0` 不再表示数值阈值，而统一转成 `null`
- 前后端统一语义：
  - `null` = 不启用 AOI 覆盖阈值
  - `0 < x <= 1` = 启用阈值

### 5.3 四种策略的正式定义

#### `all`

- 返回全部合法候选边
- 排序统一为稳定顺序：
  - `master_imaging_date`
  - `slave_imaging_date`
  - `selection_score desc`
  - `pair_uid`

不再使用“数据库返回顺序”作为结果语义。

#### `sequential`

- 基于稳定时间序列排序构图
- 排序键：
  - `acquisition_time_utc`
  - 无则 `imaging_date`
  - 再无则 `scene_uid`
- 每景连接后续 `N` 景
- 同日多景必须稳定，不允许受候选池原始顺序影响

#### `star`

- 参考像必须固定角色
- 本系统建议统一采用：
  - 参考像始终为 `master`
- 若物理上参考像日期晚于目标像，则在选择层进行拒绝或显式反转并记录相位方向规则

本轮建议：

- 为降低复杂度，先采用“参考像必须是 master”的严格规则
- 如果用户选择的参考像晚于部分场景，则这些边不入网，并给出 warning

#### `sbas`

本轮不采用“简单按 overlap 贪心去重”的旧实现。

建议目标：

- 图连通
- 每景至少达到最小连接数
- 时间覆盖尽量连续
- 每景度数受控，避免过密
- AOI 覆盖尽量多样

可落地实现：

1. 从合法候选边中先构造基础时间邻接骨架
2. 保证每个场景至少连接前后最近邻
3. 再按综合评分补边
4. 每补一条边，对已覆盖区域施加惩罚
5. 达到目标连通性和最大边数后停止

综合评分建议：

`selection_score = w_time * time_score + w_spatial * spatial_score + w_overlap * overlap_score + w_aoi * aoi_gain - w_redundancy * redundancy_penalty`

### 5.4 删除旧 fallback 的原则

本轮不再保留“Python 语义独立 fallback”。

替代方案：

- 候选边缓存是主路径
- 如果缓存不存在或脏数据过多：
  - 返回明确的 `DEGRADED` / `DIRTY` 状态
  - 允许管理员触发重建
  - 或在安全阈值内自动执行增量重建

也就是说：

- 可以有“降级状态”
- 不能再有“悄悄换算法”

## 6. 与现有扫描和自动维护机制的集成

### 6.1 源影像扫描后的集成点

当前源影像扫描入口：

- `backend/app/services/data_service.py::scan_radar_data`
- `backend/app/scheduler.py::scan_data_job`

建议改造：

在 `scan_radar_data()` 中，对新增、更新、删除的 `radar_data` 记录执行：

1. 标记 `pairing_dirty_scenes`
2. 更新 `pairing_cache_state.status = DIRTY`
3. 根据变更规模决定是否立即增量重建

建议阈值：

- 变更场景数 `<= SMALL_RECONCILE_LIMIT`
  - 扫描结束后直接执行增量 reconcile
- 变更场景数 `> SMALL_RECONCILE_LIMIT`
  - 仅标脏，不在扫描链路内同步重建
  - 由管理员在自检面板手动触发

这与当前 manual-only 模式兼容，不依赖后台常驻调度器。

### 6.2 启动期自维护

当前启动期会执行：

- `ensure_database_ready()`
- `root_registry_service.sync_from_settings()`
- `manifest_inventory_service.sync_manifest_roots()`
- catalog bootstrap
- health check

建议新增启动动作：

- `pairing_service.bootstrap_pairing_cache_state()`

只做轻量检查：

- 新表是否存在
- 是否有缓存状态行
- `metric_version` 是否匹配当前版本
- dirty scene 是否堆积

不建议启动即全量重建，避免启动时间失控。

### 6.3 数据库自动维护策略

当前数据库自维护具备两个模式：

- 常规模式：建表、补列、应用 migration
- 开发机强制 reset 模式：整库 schema reset

由于本轮是配对内核重构，且当前是开发机，推荐采用：

#### 推荐落地策略

第一轮实施时直接执行一次开发机 schema reset。

原因：

- 旧配对 SQL 函数与新缓存设计语义完全不同
- 旧任务表中的配对数据没有保留价值
- 旧结果可以通过 manifest inventory 和结果 catalog 重建
- 源影像可以重新扫描

具体方式：

- 合并新 ORM 与 migration 后
- 启动前临时设置：
  - `DB_SCHEMA_RESET_ON_MISMATCH=true`
  - `DB_SCHEMA_RESET_CONFIRM=true`
- 启动一次后让 `ensure_database_ready()` 重建 schema
- 启动完成后恢复这两个环境变量为 `false`

### 6.4 migration 文件策略

当前 `db_maintenance.py` 中 migration 顺序为：

- `001_st_intersection_agg.sql`
- `002_spatial_functions.sql`
- `003_pairing_enhancement.sql`

建议新增：

- `004_pairing_refactor.sql`

该 migration 负责：

- 创建新 pairing 表
- 创建必要索引
- 创建候选边计算函数或 SQL helper view
- 如有需要，保留旧函数为 deprecated wrapper

建议：

- 不要在 `004` 里继续强化 `find_dinsar_pairs_v2`
- 将旧函数标记 deprecated，仅作为过渡壳
- 新逻辑直接走新 service + 新表

## 7. 自检与一致性检测升级

### 7.1 Health 接口扩展

当前健康检查聚合点：

- `backend/app/services/health_service.py::get_health_status`

建议新增检查项：

#### `pairing_system`

字段建议：

- `ok`
- `status`
- `metric_version`
- `scene_count`
- `pair_count`
- `dirty_scene_count`
- `network_run_count_last_7d`
- `last_full_rebuild_at`
- `last_incremental_reconcile_at`
- `last_error`
- `duplicate_reverse_pair_count`
- `orphan_metric_scene_ref_count`
- `task_without_network_ref_count`
- `result_without_pair_trace_count`

判定规则建议：

- `READY` 且无关键异常 -> `ok=true`
- `DIRTY` 但 dirty 数量较少 -> `ok=false`，级别 `warn`
- 存在重复反向边、孤儿引用、版本漂移 -> `ok=false`，级别 `error`

### 7.2 Health 面板扩展

当前 `HealthCheckPanel.jsx` 已能展示结构化卡片。

建议新增卡片：

- `配对系统`

展示内容：

- 缓存状态
- 指标版本
- 场景数 / 缓存对数
- dirty scene 数
- 最近全量 / 增量时间
- 关键错误数

操作按钮建议：

- `增量修复配对缓存`
- `全量重建配对缓存`
- `清理旧网络记录`

### 7.3 Statistics 接口扩展

当前 `/statistics` 已承担一致性统计角色。

建议追加：

- `pairing_consistency`

字段建议：

- `metric_cache_count`
- `duplicate_reverse_pair_count`
- `invalid_orientation_count`
- `network_edge_orphan_count`
- `task_orphan_count`
- `result_trace_missing_count`

## 8. API 重构计划

### 8.1 后端接口

当前接口：

- `POST /api/find-pairs`

建议分两阶段改造。

#### Phase 1：保持前端可用

保留：

- `POST /api/find-pairs`

但内部改为：

- 从 `pairing_metric_cache` 查询
- 选择网络
- 返回 `network_run_id + pairs`

#### Phase 2：正式 API

新增：

- `POST /api/pairing/preview`
- `POST /api/pairing/networks/{network_run_id}/materialize-batch`
- `POST /api/pairing/reconcile-dirty`
- `POST /api/pairing/rebuild-cache`
- `GET /api/pairing/health`
- `GET /api/pairing/networks/{network_run_id}`

### 8.2 请求模型

`PairingRequest` 应清理为正式语义：

- 删除 `start_date`
- `aoi_overlap_threshold` 前端空值统一传 `null`
- `strategy` 继续保留：
  - `all`
  - `sequential`
  - `star`
  - `sbas`
- `reference_image_id` 仅在 `star` 时有效

### 8.3 返回模型

建议新增返回字段：

- `network_run_id`
- `policy_version`
- `candidate_count`
- `selected_edge_count`
- `degraded`
- `warnings`

## 9. 前端改造计划

涉及文件：

- `frontend/src/components/PairingModal.jsx`
- `frontend/src/hooks/usePairingLogic.js`
- `frontend/src/store/pairingStore.js`

改造原则：

- 前端不再承载配对语义补丁
- 所有“0 表示不限”“默认排序说明”等语义统一交回后端协议
- 结果列表显示：
  - `network_run_id`
  - `strategy`
  - `policy_version`
  - warning

界面调整建议：

- `all` 策略文案改为“全部合法候选边，按稳定顺序展示”
- `star` 明确说明“参考像固定为主像”
- `sequential` 明确说明“同日多景按稳定键排序”
- AOI 覆盖阈值为空时传 `null`

## 10. 旧代码处理策略

### 10.1 可以删除的内容

在新缓存路径稳定后，可以删除：

- `spatial_service.find_dinsar_pairs()` 中当前 Python fallback
- 旧 `_apply_star_strategy()` 非固定角色实现
- 旧 `start_date` 兼容逻辑

### 10.2 需要保留过渡一段时间的内容

- 旧前端 `find-pairs` 调用入口
- 旧 `task_alias / pair_key` 命名辅助

但注意：

- 命名只能作为展示与兼容字段
- 不能继续作为算法主键

## 11. 实施阶段划分

### Phase 0：开发机切换准备

- 确认当前开发机允许 DB reset
- 记录 `.env` 中路径配置
- 保留现有 `managed_roots` / `path_inventory` / manifest publish 目录，不做文件层清理
- 准备新 migration 与 ORM

验收：

- 可以安全启动一次 schema reset

### Phase 1：数据模型落地

- 新增 pairing 相关 ORM
- 新增 `004_pairing_refactor.sql`
- 接入 `ensure_database_ready()`
- 建立 `pairing_cache_state` 初始化逻辑

验收：

- 后端启动后新表自动创建
- 健康检查不报 schema 错

### Phase 2：候选缓存引擎

- 实现 `pairing_metric_cache` 全量构建
- 实现 `pairing_dirty_scenes`
- 在 `scan_radar_data()` 中接入 dirty 标记

验收：

- 能从 `radar_data` 构建稳定 pair cache
- 同日反向重复为 0

### Phase 3：网络选择器

- 重写 `all / sequential / star / sbas`
- 删除算法不等价 fallback
- 为 `find-pairs` 接口切换到新内核

验收：

- 同一输入重复请求结果稳定
- `star` 固定参考像角色
- `sequential` 同日多景稳定

### Phase 4：任务物化与结果追踪

- `dinsar_task_items` 增加 network trace 字段
- 结果 manifest / product profile 补充配对追踪字段

验收：

- 任一结果可追溯到 network edge

### Phase 5：自维护与自检

- 健康检查新增 `pairing_system`
- Health 面板新增配对卡片与修复按钮
- Statistics 新增 pairing consistency

验收：

- 能显示 dirty scene 数、缓存状态、重复边异常
- 能从 UI 触发增量修复与全量重建

### Phase 6：清理旧逻辑

- 删除旧 fallback
- 删除 `start_date`
- 下线旧语义说明

验收：

- 配对链路只剩单一语义实现

## 12. 测试矩阵

本轮至少补以下自动化测试：

### 12.1 候选缓存层

- 同日双景只生成单方向边
- 同日多景生成顺序稳定
- 不同轨向不生成边
- 时间基线越界不入缓存

### 12.2 请求过滤层

- `allowed_satellites` 生效
- `cross_satellite_pairing=false` 生效
- `require_same_imaging_mode/polarization` 生效
- `aoi_overlap_threshold=null` 与 `0.2` 行为不同

### 12.3 策略层

- `all` 顺序稳定
- `sequential` 连接数正确
- `star` 参考像固定为 master
- `sbas` 至少满足基础连通性

### 12.4 集成层

- 扫描新增源影像后 dirty scene 正确增加
- 执行增量 reconcile 后 dirty scene 清零
- `find-pairs` 返回 `network_run_id`
- `materialize-batch` 后任务项带 network trace

### 12.5 自检层

- 重复边异常能被 health 检出
- 孤儿网络边能被 health 检出
- 任务无 network trace 能被 statistics 检出

## 13. 风险与取舍

### 13.1 本轮明确接受的取舍

- 开发机允许整库 reset
- 不迁移旧配对记录
- 不保留旧 fallback

### 13.2 需要重点控制的风险

- 首轮全量构建 pair cache 可能较慢
- `radar_data.unique_id` 可能不稳定，需要一次性规范
- 旧结果如果缺少 trace 字段，需要通过 manifest/profile 尽可能补齐
- 若前端仍传 `0`，后端必须做强制归一化，不能再依赖 UI 自觉

## 14. 推荐实施顺序

建议严格按下面顺序推进：

1. 先落表和缓存状态，不先改前端
2. 再实现候选缓存和增量脏标记
3. 再重写四种策略
4. 再接任务物化追踪
5. 最后接健康检查与自修复入口

不要反过来做：

- 不要先改 UI 文案
- 不要先修补 `find_dinsar_pairs_v2`
- 不要保留旧 fallback 到最后

## 15. 本轮结论

在当前代码基线上，最合理的重构路线不是继续维护“实时 SQL + Python fallback”模式，而是：

- 用 `radar_data` 作为唯一事实层
- 用 `pairing_metric_cache` 承载稳定候选对
- 用 `pairing_network_runs / edges` 承载策略结果
- 用 `pairing_cache_state / dirty_scenes` 承接自维护
- 用现有 `HealthCheckPanel` 和 `/statistics` 承接自检

这样做的直接收益是：

- 正确性可锁定
- 结果可追溯
- 与现有 DB 自维护、扫描链路、自检体系自然对接
- 后续再接 SBAS、跨卫星和多引擎生产时不会继续堆积配对债务
