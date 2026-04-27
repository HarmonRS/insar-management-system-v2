# SBAS 结果管理与展示规范

更新日期：2026-04-06

## 1. 管理目标

SBAS 结果进入系统后，必须满足三个层面的要求：

- 可登记
- 可追溯
- 可展示

当前推荐的管理边界不是 MintPy 运行目录，而是发布级 bundle。

唯一注册入口：

- `publish/.../manifest.json`

## 2. 结果层级

### 2.1 运行层

运行层记录在：

- `ps_timeseries_runs`

职责：

- 记录一次正式处理运行的业务身份
- 记录输入、参数、DEM、轨道、掩膜、目录与状态

### 2.2 产品层

产品层继续复用：

- `result_products`
- `result_assets`
- `result_issues`

约定：

- `catalog_name = psinsar`
- `product_type = psinsar_bundle`
- `run_key = ps_timeseries_runs.run_id`

### 2.3 资产层

资产层由 `result_assets` 管理。

建议角色：

- `timeseries_cube`
- `velocity_map`
- `velocity_geotiff`
- `temporal_coherence`
- `temporal_coherence_geotiff`
- `quality_mask`
- `quality_mask_geotiff`
- `preview_png`
- `diagnostic_png`
- `config`
- `quality_summary`

## 3. 发布级 bundle 约束

标准目录：

```text
<PSINSAR_PRODUCT_DIR>/<year>/<run_id>/
  manifest.json
  assets/
  preview/
  metadata/
```

最低要求：

- `manifest.json`
- `assets/geo_timeseries.h5`
- `assets/velocity.tif`
- `preview/velocity_preview.png`

建议同时保留：

- `assets/geo_velocity.h5`
- `assets/temporalCoherence.tif`
- `assets/maskTempCoh.tif`
- `preview/numTriNonzeroIntAmbiguity.png`
- `metadata/source_quality_summary.json`

## 4. 产品主文件定义

### 4.1 科学主产物

- `assets/geo_timeseries.h5`

这是系统证明“具备时序处理能力”的核心文件。

### 4.2 展示主产物

- `assets/velocity.tif`

这是结果首屏展示、地图浏览、预览生成的主文件。

### 4.3 质量主产物

- `assets/temporalCoherence.tif`
- `assets/maskTempCoh.tif`

## 5. manifest 解析要求

系统应支持：

- `schema_version = psinsar.publish.v1`

系统应从 manifest 中提取：

- 基础身份
  - `catalog_name`
  - `mode`
  - `engine_code`
  - `processor_code`
- 时序信息
  - `reference_date`
  - `stack_dates`
- 资产信息
  - `artifacts[]`
- 质量信息
  - `quality`
- 辅助摘要
  - `summaries`

如 manifest 缺少显式 bbox，可从 `summaries.*.attrs` 中推导：

- `X_FIRST`
- `Y_FIRST`
- `X_STEP`
- `Y_STEP`
- `WIDTH`
- `LENGTH`

## 6. 展示规范

## 6.1 列表页

优先展示：

- 产品名称
- 运行标识
- 参考日期
- 影像数量
- 发布时间
- 缩略图

## 6.2 详情页

详情页应至少展示：

- 运行信息
- 时间序列日期列表
- 质量摘要
- 资产清单
- 问题清单

## 6.3 地图展示优先级

当前优先级：

1. `velocity.tif`
2. `temporalCoherence.tif`
3. `maskTempCoh.tif`

不建议当前阶段直接做：

- `geo_timeseries.h5` 全量地图服务化

## 7. 结果与运行的关联

必须能从结果反查运行。

推荐方式：

- `result_products.run_key = ps_timeseries_runs.run_id`

这样可以回答：

- 这个结果来自哪个 batch
- 用了哪些日期
- DEM 和轨道来自哪里
- 使用了什么水体掩膜策略

## 8. 目录与数据库职责边界

目录是事实源：

- `manifest.json`
- `assets/*`
- `preview/*`

数据库是索引层：

- 产品列表
- 检索字段
- 资产索引
- 问题索引

因此不应让数据库替代目录事实源，也不应让前端直接依赖实验运行目录。

## 9. Phase 1 / Phase 2 边界

### Phase 1

- 支持 bundle 扫描与登记
- 支持产品列表和详情
- 支持缩略图展示

### Phase 2

- 支持地图叠加 velocity
- 支持 run 与 product 联动
- 支持更多质量视图

### Phase 3

- 支持 `geo_timeseries.h5` 点位时序查询
- 支持图表和专题分析

## 10. 当前结论

SBAS 结果管理的核心不是“把很多文件存起来”，而是把下面三件事稳定下来：

- 用 `manifest.json` 定义结果边界
- 用 `result_products/result_assets` 登记 bundle
- 用 `run_key` 把结果和运行记录连起来

这三件事一旦成立，系统就具备了承接 SBAS 成果的基础能力。
