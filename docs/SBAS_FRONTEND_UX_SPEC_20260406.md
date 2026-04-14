# SBAS 前端设计说明

更新日期：2026-04-06

## 1. 设计目标

SBAS 前端必须解决四个问题：

- 让用户知道“匹配结果”和“正式生产运行”不是一回事
- 让用户能从已有 PS 批次发起正式运行
- 让用户能查看 `psinsar` 成果，而不是只看实验目录
- 让用户能逐步过渡到更复杂的时序成果浏览，而不是一次性堆满功能

## 2. 导航结构

当前建议保留现有导航层级，只把占位页替换成真实页面。

### 2.1 生产规划组

- `ps_results`
  - 匹配结果浏览与批次创建
- `ps_production`
  - 正式生产入口
- `ps_products`
  - 成果编目与产品管理

### 2.2 InSAR 变形分析组

- `psinsar_results`
  - 以成果浏览为主的只读结果页
- `psinsar_analysis`
  - 后续时序分析页，当前可继续保留占位

## 3. 页面一：`ps_production`

## 3.1 页面目标

从已有 `ps_task_batches` 中选择一个 stack，提交一次正式 SBAS 运行，并查看最近运行状态。

## 3.2 页面布局

建议分成三块：

- 运行说明卡
- 新建运行表单
- 最近运行列表

## 3.3 新建运行表单字段

必填字段：

- `batch_id`

可选字段：

- `run_name`
- `reference_date`
- `water_mask_mode`
- `notes`

只读展示字段：

- `direction`
- `stack_size`
- `with_orbit_count`
- `missing_orbit_count`

## 3.4 提交后反馈

提交后需要立刻给出：

- `task_id`
- `run_id`
- 当前状态

当前 Phase 1 仅执行 prepare，因此状态建议为：

- `PENDING`
- `RUNNING`
- `PREPARED`
- `FAILED`

## 3.5 最近运行列表

每个运行卡片至少展示：

- `run_name`
- `run_id`
- `batch_id`
- `reference_date`
- `stack_size`
- `status`
- `created_at`

详情展开建议展示：

- DEM 路径
- 轨道池路径
- 生成的 stack manifest 路径
- 输入日期列表
- 质量/预检查摘要

## 4. 页面二：`ps_products`

## 4.1 页面目标

承接管理员视角的产物目录维护。

能力包括：

- 查看 `psinsar` catalog 状态
- 手动重建 catalog
- 浏览产品列表
- 查看产品详情

## 4.2 页面布局

建议分成三块：

- catalog 状态卡
- 管理操作区
- 产品列表与详情区

## 4.3 catalog 状态卡

展示字段：

- `catalog_name`
- `storage_root`
- `status`
- `needs_rebuild`
- `manifest_count`
- `db_count`
- `issue_count`
- `last_full_rebuild_at`

## 4.4 产品列表

每个产品卡片建议展示：

- `display_name`
- `product_id`
- `run_key`
- `reference_date`
- `stack_size`
- `status`
- `published_at`

优先展示缩略图：

- `preview/velocity_preview.png`

## 4.5 产品详情

详情建议包含：

- 基本信息
- 时序信息
- 质量摘要
- 资产列表
- 问题列表

资产列表首屏优先顺序：

- `velocity_geotiff`
- `timeseries_cube`
- `temporal_coherence_geotiff`
- `quality_mask_geotiff`
- `preview_png`
- 其他辅助资产

## 5. 页面三：`psinsar_results`

## 5.1 页面目标

承接业务用户视角的成果浏览，不承担目录维护操作。

## 5.2 与 `ps_products` 的区别

- `ps_products` 偏运维和管理
- `psinsar_results` 偏展示和检索

## 5.3 当前 Phase 1 能力

- 浏览产品列表
- 查看缩略图
- 查看资产清单
- 查看质量摘要

## 5.4 后续 Phase 2/3 扩展

- 地图叠加 `velocity.tif`
- 按 AOI 或时间筛选
- 像元点位时序查询
- 专题统计与热点识别

## 6. 交互原则

- 生产和结果页面必须分开
- 管理操作只出现在管理页
- 结果页优先展示 `velocity.tif` 对应预览
- 但详情页必须强调主科学产物是 `geo_timeseries.h5`

## 7. 前端阶段性实施建议

### Phase 1

- `ps_production`：真实可提交 prepare 任务
- `ps_products`：真实 catalog 管理页
- `psinsar_results`：复用 catalog 浏览能力做只读页

### Phase 2

- 地图叠加 `velocity.tif`
- 结果筛选
- 与 run 详情联动

### Phase 3

- `geo_timeseries.h5` 点查询
- 时间曲线图
- 多产品对比

## 8. 当前结论

前端第一阶段不应追求“一口气把时序分析全做完”，而应优先做三件事：

- 让 SBAS 生产有正式入口
- 让 SBAS 结果有正式目录
- 让用户在系统里能看到真正的 `psinsar` 成果

这三件事完成后，系统才算真正开始承接 SBAS 能力。
