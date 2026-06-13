# SBAS-InSAR 当前工作流

最后更新：2026-05-28

本文件是当前 SBAS-InSAR 生产和结果管理的事实文档。旧的 ISCE2/MintPy 时序生产、早期 SBAS 设计草案、实验 runbook 和阶段性记录不再作为当前依据。

## 1. 当前结论

- 当前 SBAS-InSAR 主线是 Gamma DIFF + IPTA SBAS。
- 前端入口在“生产管理”工作台内：
  - `SBAS-InSAR Production`
  - `SBAS-InSAR 结果`
- 后端入口：
  - `/api/sbas-insar-production`
  - `/api/sbas-insar-products`
- 旧 `/api/timeseries-production`、旧 `ps_production`、旧 `ps_products` 只作为兼容代码保留，不再作为生产管理主入口。
- 专家文档 `LT1_GAMMA_SBAS_逐命令处理流程.docx` 是 Gamma 命令链路来源；系统实现负责把专家脚本组织成可配置、可审查、可重跑的生产流水线。

## 2. 代码入口

后端：

```text
backend/app/routers/sbas_insar_production.py
backend/app/services/sbas_insar_production_service.py
backend/app/routers/sbas_insar_products.py
backend/app/services/sbas_insar_catalog_service.py
deploy/wsl/runners/gamma_sbas_product_tools.py
```

前端：

```text
frontend/src/ProductionWorkspace.jsx
frontend/src/SbasInsarProductionPanel.jsx
frontend/src/SbasInsarProductsPanel.jsx
frontend/src/api/sbasInsarProducts.js
frontend/src/config/appConstants.js
```

启动自维护：

```text
backend/app/main.py
  -> sbas_insar_catalog_service.bootstrap_catalog_on_startup_clean()
```

## 3. 运行时配置

核心配置从 `.env` 读取：

```text
GAMMA_SBAS_ENABLED
GAMMA_SBAS_RUNTIME_ID
GAMMA_SBAS_WSL_DISTRO
GAMMA_SBAS_PYTHON
GAMMA_SBAS_ENV_SCRIPT
GAMMA_SBAS_WORK_ROOT
GAMMA_SBAS_PRODUCT_ROOT
GAMMA_SBAS_SCRIPT_TEMPLATE_ROOT
GAMMA_SBAS_SOURCE_ROOTS
GAMMA_SBAS_ORBIT_ROOTS
GAMMA_SBAS_DEFAULT_RLKS
GAMMA_SBAS_DEFAULT_AZLKS
GAMMA_SBAS_DEFAULT_MB_MODE
GAMMA_SBAS_DEFAULT_REFERENCE_WINDOW
GAMMA_SBAS_STEP_TIMEOUT_SECONDS
GAMMA_SBAS_WORKFLOW_TIMEOUT_SECONDS
```

默认工作根：

```text
backend/runtime/sbas_insar_production
```

默认产品根：

```text
D:\production_results\timeseries\sbas
```

## 4. 生产流程

用户侧推荐顺序：

1. 在 `SBAS-InSAR Production` 输入行政区，查找候选序列。
2. 选择 READY 候选，生成 Stack Manifest。
3. 创建计划 Run。
4. 提交 `Gamma SBAS Workflow` 后台任务。
5. 审查基线和 itab。
6. 执行后续 Gamma 阶段。
7. 发布产品并抽取监测点。
8. 到 `SBAS-InSAR 结果` 查看结果和下载资产。

当前托管阶段：

```text
prepare_slc
baseline_audit
coregistration
rdc_dem
interferograms
detrend_atm
ipta_timeseries
publish_products
monitor_points
```

`workflow/jobs` 是主入口，旧的逐阶段接口保留用于兼容、排错和历史 Run 读取。

## 5. AOI 选栈

当前页面面向用户显示“生产区域”，不是开发用的中心桶或硬分组。

发现接口支持：

```text
discovery_mode = strict | aoi
admin_region
aoi_bbox
min_aoi_coverage_ratio
min_common_overlap_ratio
```

AOI 模式处理逻辑：

1. 将行政区解析为 AOI 几何。
2. 使用 LT1 元数据 bbox 筛选与 AOI 相交的影像。
3. 按观测几何分组：
   - satellite
   - satellite_mode
   - relative_orbit
   - orbit_direction
   - imaging_mode
   - polarization
4. 后端内部按 footprint 公共重叠继续做空间聚类。
5. 输出日期范围、可用景数、缺精轨数、最大时间间隔、公共重叠和 AOI 覆盖摘要。

`center_bucket` 和 `receiving_station` 是内部诊断字段，不作为用户生产入口展示。

2026-06-09 修正：候选序列发现不再把 `center_bucket` 作为 strict 模式的硬分组字段。此前全库查找会按中心点小格拆成大量小序列，1500 景 LT-1 数据里最大候选只有 7 景；现在 strict 和 aoi 都先按同卫星/同模式/同相对轨道/同方向/同极化形成观测组，再按 footprint 公共重叠聚类。实际验证：全库 LT-1 候选从最大 7 景恢复到 32/29/28 景级别；牡丹江 AOI 候选也能返回 32/29/26 景级别。

## 6. 产物契约

一次完成的 SBAS Run 是一个结果产品包。核心资产包括：

```text
run_manifest.json
stack_manifest.json
pair_network.json
gamma_command_manifest.json
workflow_summary.json
product_summary.json
quality_summary.json
monitor_points_summary.json

publish/geotiff/los_rate_toward_m_per_year.tif
publish/geotiff/los_rate_away_m_per_year.tif
publish/geotiff/los_sigma_m_per_year.tif
publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png
publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png
publish/vectors/los_rate_points.geojson.gz
publish/vectors/los_rate_points_summary.json
publish/monitor_points/*_timeseries.png
publish/monitor_points/*_timeseries.csv
publish/monitor_points/*_metadata.json
```

结果页默认展示：

- LOS 速率预览图；
- LOS sigma 预览图；
- 监测点曲线；
- 点矢量下载入口；
- 产品资产列表；
- 行政区、中心点、日期范围、景数和配对数。

全量点 GeoJSON.gz 只提供下载，前端不渲染全量点。

## 7. LOS 符号约定

Gamma `ts_rate` 输出是相位速率，系统同时保留两种 LOS 约定：

```text
los_rate_away_mm_per_year = phase_rate * wavelength / (4*pi) * 1000
los_rate_toward_mm_per_year = -phase_rate * wavelength / (4*pi) * 1000
```

默认展示：

```text
LOS toward radar positive
```

即朝向雷达为正，远离雷达为负。该约定接近 Gamma `dispmap` 默认 `sflg=0` 的表达习惯。

## 8. 结果目录与 catalog

SBAS 结果 catalog 复用通用结果表：

```text
result_products
result_assets
result_issues
result_catalog_state
```

SBAS catalog 名称：

```text
sbas_insar
```

结果管理接口：

```text
GET  /api/sbas-insar-products/catalog-status
POST /api/sbas-insar-products/rebuild-catalog
GET  /api/sbas-insar-products
GET  /api/sbas-insar-products/{product_id}
GET  /api/sbas-insar-products/{product_id}/assets/{asset_id}
```

启动时会自动扫描已完成 Run 并维护 catalog 状态。

## 9. 旧链路状态

以下内容不再作为当前生产事实：

- `experiments/isce2_sbas_timeseries`
- ISCE2 + MintPy 时序生产链
- SARscape SBAS 时序方案
- 旧 `ps_production` / `ps_products` 生产页面设计
- 旧 `geo_timeseries.h5` 作为当前 SBAS 主产品的设计

旧代码可保留兼容，但新功能、测试和文档默认围绕 Gamma SBAS 独立生产链路展开。

## 10. 当前验证基线

截至 2026-05-28 已验证：

- Gamma SBAS 实验链路可跑通；
- 后端 AOI 候选发现可按行政区返回 READY 候选；
- 牡丹江 AOI 可发现超过旧严格分组 7 景上限的候选；
- 选中候选可生成 Stack Manifest；
- 结果发布支持 GeoTIFF、预览图、监测点曲线和点矢量下载；
- 前端生产页和结果页构建通过。
