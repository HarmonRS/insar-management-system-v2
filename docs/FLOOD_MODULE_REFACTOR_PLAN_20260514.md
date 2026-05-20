# 洪涝监测模块整改实施方案

> 日期：2026-05-14  
> 目标：将当前分散的水体监测、洪涝检测、GF3 处理和结果管理，收敛为可部署、可维护、可扩展的洪涝灾害分析流水线。

## 1. 总体原则

1. 新业务统一走 `/flood/*`。
2. “水体监测”不再作为独立业务模块出现，只作为“洪涝灾害分析 -> 水体提取”步骤存在。
3. `/water/*` 暂时保留兼容窗口，但标记 deprecated，不再新增功能。
4. 数据库优先新增表和回填数据，避免直接破坏旧表。
5. 每个阶段独立提交，保证任一阶段都能构建、部署和回滚。

## 2. 目标流水线

```text
场景准备 Scene
  -> 水体提取 WaterExtraction
  -> 洪涝检测 FloodDetection
  -> 套合分析 FloodOverlay
  -> 洪涝产品 FloodProduct / Report
```

其中水体提取是洪涝分析的前置步骤，不再和洪涝灾害分析并列成两个业务入口。

## 3. 阶段 1：收敛 API 契约

目标：先消灭“前端调用不存在接口”的问题，避免部署后出现静默 404。

修改范围：

```text
frontend/src/api/flood.js
backend/app/routers/flood.py
docs/FLOOD_MODULE_REFACTOR_PLAN_20260514.md
```

处理方式：

- `frontend/src/api/flood.js` 只暴露后端当前真实支持的接口。
- 暂时移除或注释未实现接口，例如 `/flood/sources`、`/flood/ready-products`、`/flood/pairs` 保存删除、`/flood/reports`、`/flood/results`。
- 后端 `/flood` 路由继续保留现有功能，但接口命名统一为 `preprocess`、`scenes`、`water-extractions`、`pairs/search`、`detections`、`detections/{id}/preview/{layer}`。

验收标准：

```text
npm run build 通过
前端 flood API 文件中没有明显会 404 的已导出函数
/flood 主流程现有功能不退化
```

## 4. 阶段 2：新增数据模型

目标：建立洪涝流水线需要的数据承载，不直接破坏旧表。

新增模型：

```text
WaterExtractionORM -> water_extractions
FloodOverlayORM    -> flood_overlays
FloodProductORM    -> flood_products
```

关键取舍：

- 保留旧 `WaterDetectionORM -> water_detections`。
- 新表 `water_extractions` 从旧表回填。
- 后续新任务写入 `water_extractions`。
- 旧接口读旧表或兼容映射，等稳定后再清理。

建议字段：

```text
water_extractions:
  id
  scene_id
  processor
  task_id
  input_path
  output_path
  preview_path
  vector_path
  water_area_km2
  water_pixel_count
  threshold_value
  metadata_json
  status
  error_msg
  created_at
  updated_at

flood_overlays:
  id
  detection_id
  flood_vector_path
  hazard_points_hit
  hazard_points_near
  hazard_points_total
  dinsar_products_intersecting
  affected_area_km2
  summary_json
  created_at

flood_products:
  id
  product_id
  detection_id
  overlay_id
  display_name
  status
  publish_dir
  manifest_path
  summary_json
  created_at
```

验收标准：

```text
alembic upgrade head 成功
旧 water_detections 数据可迁移到 water_extractions
模型 import 正常
```

## 5. 阶段 3：抽离 Service

目标：让 `/flood` 成为真正的业务路由，而不是代理 `water.py` 的壳。

新增服务：

```text
backend/app/services/flood_analysis_service.py
backend/app/services/water_extraction_service.py
backend/app/services/flood_product_service.py
backend/app/services/flood_overlay_service.py
```

职责划分：

```text
flood_analysis_service.py
  场景列表、预处理提交、配对搜索、洪涝检测提交、检测列表

water_extraction_service.py
  Otsu 水体提取、ENVI/SARscape 水体提取

flood_product_service.py
  产品列表、manifest、产品包生成

flood_overlay_service.py
  分类栅格矢量化、灾害点/DInSAR/AOI 套合
```

完成后：

- `backend/app/routers/flood.py` 不再 `import water as water_compat`。
- `backend/app/routers/water.py` 标记 deprecated，后续可反向调用新 service。
- `water_detect_service.py` 逐步迁移到 `water_extraction_service.py`。

验收标准：

```text
flood.py 不再 import water.py
/water/* 旧接口仍可用
/flood/* 主接口可用
```

## 6. 阶段 4：补齐产品端点

目标：让“结果与任务”视图有真实后端数据。

主接口建议使用 `products`，避免和 DInSAR result/product 概念混淆：

```text
POST /flood/detections/{id}/products
GET  /flood/products
GET  /flood/products/{id}
GET  /flood/products/{id}/manifest
```

兼容别名可选：

```text
GET /flood/results
GET /flood/results/{id}
GET /flood/results/{id}/manifest
```

验收标准：

```text
前端结果视图能展示真实产品
manifest 能返回 JSON
没有空接口或静默失败
```

## 7. 阶段 5：实现套合分析

目标：把洪涝模块从检测工具提升为灾害分析模块。

新增接口：

```text
POST /flood/detections/{id}/overlay
GET  /flood/detections/{id}/impact
```

处理逻辑：

```text
1. 读取 flood_detections.classified_path
2. 提取 class=2 洪涝区域
3. 栅格转矢量
4. 写 flood_overlays.flood_vector_path
5. 查询灾害点命中
6. 查询近邻风险点
7. 查询相交 DInSAR 产品
8. 查询 AI 诊断摘要
9. 写 summary_json
```

注意：

- AOI、PostGIS、DInSAR 产品几何不完整时，不让整个任务失败。
- 返回部分结果，并在 `summary_json.warnings` 中说明缺失项。

验收标准：

```text
POST overlay 能生成 flood_overlays 记录
GET impact 返回结构化 JSON
没有数据时返回空数组而不是 500
```

## 8. 阶段 6：前端重整

目标：只保留一个洪涝灾害分析工作台。

新增目录：

```text
frontend/src/components/flood/
```

建议组件：

```text
FloodStatusBadge.jsx
FloodButton.jsx
FloodSceneRow.jsx
FloodWaterExtractionRow.jsx
FloodDetectionRow.jsx
FloodOverlayPanel.jsx
FloodProductPanel.jsx
```

界面改成四站式：

```text
1. 场景准备
2. 水体提取
3. 洪涝检测
4. 套合与产品
```

处理方式：

- `WaterMonitorPanel.jsx` 加弃用提示。
- 左侧导航只引导用户进入洪涝灾害分析。
- `api/water.js` 和 `api/gf3.js` 标记 deprecated。
- 新功能只接入 `api/flood.js`。

验收标准：

```text
左侧导航只引导用户进入洪涝灾害分析
水体提取不再作为独立业务重复出现
套合分析和产品视图接真实接口
npm run build 通过
```

## 9. 阶段 7：报告生成壳

目标：先形成 Markdown 报告能力，再扩展 PDF。

新增接口：

```text
POST /flood/reports
GET  /flood/reports/{id}
```

第一版报告包含：

```text
灾前/灾后场景信息
洪涝面积
灾害点命中
DInSAR 产品关联
AI 诊断摘要
套合统计
```

## 10. 推荐提交顺序

```text
1. refactor flood api contract
2. add flood pipeline orm models
3. extract flood analysis services
4. add flood product endpoints
5. implement flood overlay impact analysis
6. consolidate flood frontend workspace
7. scaffold flood report generation
```

核心思路：先稳住接口和数据，再拆服务，再补业务能力，最后整理 UI。这样 Git 中的代码、迁移、前端调用和部署环境是闭合的，另一台服务器拉取后不会出现关键页面依赖未实现接口的问题。

## 11. 2026-05-15 补充设计：水体提取、灾害配对与套合展示

本节根据最新审阅意见补充。结论：洪涝工作台不应该继续以“手动选日期范围 + 查配对”为核心，而应该改成“灾害事件驱动”：

```text
灾害时间 + 灾害位置
  -> 过滤可用 SAR 场景匹配池
  -> 推荐灾前/灾后配对
  -> 执行洪涝检测
  -> 套合分析
  -> 结果展示与产品导出
```

### 11.1 水体提取视图复用管理页能力

当前管理页面已经具备三类能力，洪涝模块应复用，而不是重新做一套弱化版：

1. 雷达数据查询能力：`/radar-data/search` 已支持成像日期、成像模式、极化方式、产品级别、行政区 AOI、上传 AOI 文件。
2. 地图能力：`App.jsx` 中已经有源影像 footprint 上图、源影像预览缓存 `radar-data/{id}/thumb`、地图定位和图层开关逻辑。
3. 行政区 AOI 能力：`/aoi/regions/children` 与 `/aoi/regions/{treeId}/geometry` 已可用于按省/市范围查询和定位。

因此水体提取界面调整为：

```text
左侧：场景匹配池
  - 灾害位置 / AOI
  - 成像日期范围
  - 卫星、模式、极化、产品级别
  - 只显示有 footprint 的数据
  - 可显示源影像预览图
  - 显示成像时间、极化方式、成像模式、产品级别

右侧：已完成地理编码场景 / 水体提取结果
  - 场景 footprint 上图
  - 水体提取掩膜上图
  - 与源影像预览可叠加对比
```

前端复用建议：

```text
复用 buildRadarSearchFormData / normalizeRadarSearchCriteria
复用 UnifiedDatePicker
复用 RadarDataRow 的预览状态表达
复用 App.jsx 中 updateRadarPreviewVisibility 的源影像预览图层逻辑
复用行政区 AOI 选择和地图定位逻辑
```

需要新增的洪涝专用组件：

```text
FloodSourceSearchPanel.jsx
  封装灾害位置、日期、极化、模式、产品级别过滤。

FloodSourceSceneRow.jsx
  显示源影像：成像时间、极化方式、模式、产品级别、预览状态、上图按钮。

FloodWaterExtractionRow.jsx
  显示水体提取结果：面积、状态、输入场景、上图按钮、错误信息。
```

地图图层约定：

```text
source_preview:{radar_data_id}       源影像预览
source_footprint:{radar_data_id}     源影像覆盖范围
scene_footprint:{scene_id}           地理编码场景范围
water_mask:{water_extraction_id}     水体提取掩膜
```

### 11.2 洪涝检测配对改为灾害事件驱动

当前 `/flood/pairs/search` 只接收灾前/灾后日期区间和 overlap 阈值，实际使用体验不够：用户通常知道的是“灾害发生时间”和“灾害位置”，不是一开始就知道灾前灾后影像窗口。

新的配对入口应改为：

```text
灾害名称 disaster_name      可选
灾害时间 disaster_date      必填
灾害位置 disaster_aoi       必填，来自行政区 / 地图框选 / 上传 SHP / GeoJSON
灾前窗口 pre_window_days    默认 30 天
灾后窗口 post_window_days   默认 30 天
最小 AOI 覆盖率 min_aoi_coverage_ratio 默认 0.3
最小两景重叠率 min_pair_overlap_ratio  默认 0.5
是否要求同极化 require_same_polarization 默认 true
是否要求同成像模式 require_same_imaging_mode 默认 false
卫星过滤 satellites 可选
极化过滤 polarization 可选
```

前端交互：

```text
1. 用户选择灾害日期
2. 用户选择灾害位置
   - 行政区：省 / 市
   - 地图框选：后续实现
   - 上传 SHP/GeoJSON：沿用现有 AOI 解析
3. 系统自动推导：
   - 灾前窗口：disaster_date - pre_window_days 至 disaster_date - 1
   - 灾后窗口：disaster_date 至 disaster_date + post_window_days
4. 后端返回：
   - pre_pool
   - post_pool
   - candidate_pairs
   - warnings
5. 用户在候选配对中选择一组提交洪涝检测
```

推荐新增接口：

```text
POST /flood/disaster-pairs/search
```

请求格式：

```json
{
  "disaster_name": "汶川洪涝",
  "disaster_date": "20260715",
  "aoi_geojson": {},
  "region_tree_id": "510000",
  "pre_window_days": 30,
  "post_window_days": 30,
  "min_aoi_coverage_ratio": 0.3,
  "min_pair_overlap_ratio": 0.5,
  "require_same_polarization": true,
  "require_same_imaging_mode": false,
  "satellites": ["LT-1", "GF3"],
  "polarization": "VV"
}
```

返回格式：

```json
{
  "disaster": {
    "name": "汶川洪涝",
    "date": "20260715",
    "pre_range": ["20260615", "20260714"],
    "post_range": ["20260715", "20260814"]
  },
  "aoi": {
    "source": "region",
    "name": "汶川县",
    "aoi_geojson": {}
  },
  "pre_pool": [],
  "post_pool": [],
  "candidate_pairs": [
    {
      "pre": {
        "scene_id": 1,
        "radar_data_id": 10,
        "imaging_date": "20260701",
        "satellite": "LT-1",
        "polarization": "VV",
        "imaging_mode": "SM",
        "aoi_coverage_ratio": 0.82,
        "coverage_polygon": []
      },
      "post": {
        "scene_id": 2,
        "radar_data_id": 11,
        "imaging_date": "20260718",
        "satellite": "LT-1",
        "polarization": "VV",
        "imaging_mode": "SM",
        "aoi_coverage_ratio": 0.79,
        "coverage_polygon": []
      },
      "pair_overlap_ratio": 0.91,
      "pre_delta_days": 14,
      "post_delta_days": 3,
      "score": 0.86,
      "warnings": []
    }
  ],
  "warnings": []
}
```

排序建议：

```text
score =
  pair_overlap_ratio * 0.35
  + min(pre_aoi_coverage, post_aoi_coverage) * 0.30
  + time_score * 0.20
  + same_polarization_bonus * 0.10
  + same_mode_bonus * 0.05
```

后端实现建议：

```text
1. 复用 radar search 的 AOI 解析能力，避免重复解析行政区和 SHP。
2. 查询 sar_scene_geo.status=DONE 的场景，并 join radar_data。
3. 按 disaster_date 自动构造灾前/灾后窗口。
4. 用 PostGIS 计算单景 AOI 覆盖率。
5. 用 footprint 相交面积计算 pair_overlap_ratio。
6. 返回匹配池和候选配对，而不是只返回最终 pairs。
```

兼容处理：

```text
旧接口 POST /flood/pairs/search 保留，但只作为手动日期模式。
新工作台默认使用 POST /flood/disaster-pairs/search。
```

### 11.3 套合分析结果展示逻辑

套合分析不能只是一个“运行按钮”。它应展示“为什么这个洪涝结果重要”：

```text
洪涝范围
灾害点命中
近洪涝风险点
DInSAR 产品关联
行政区影响统计
结果图层
产品/报告入口
```

前端 `FloodOverlayPanel.jsx` 设计：

```text
顶部摘要：
  - 洪涝面积
  - 命中灾害点数量
  - 近洪涝风险点数量
  - 关联 DInSAR 产品数量
  - warnings 数量

中部地图控制：
  - 洪涝分类图
  - 洪涝矢量范围
  - 命中灾害点
  - 近洪涝风险点
  - DInSAR 产品 footprint

下部结果表：
  - 灾害点列表：名称、类型、行政区、距离、是否命中
  - DInSAR 产品列表：product_id、engine、形变量、AI 风险等级、预览/详情
  - AOI 统计：行政区、洪涝面积、占比
```

后端 `GET /flood/detections/{id}/impact` 应保证即使没有运行套合，也返回稳定结构：

```json
{
  "detection_id": 1,
  "flood_area_km2": 12.5,
  "hazard_points": {
    "inside_flood": [],
    "near_flood": [],
    "total_in_scene": 0
  },
  "dinsar_products": [],
  "affected_aois": [],
  "map_layers": {
    "classified_preview": true,
    "flood_vector_path": null,
    "hazard_points": true,
    "dinsar_footprints": true
  },
  "warnings": []
}
```

需要新增或完善的地图层约定：

```text
flood_classified:{detection_id}      洪涝分类栅格预览
flood_vector:{overlay_id}            洪涝矢量面
hazard_inside:{overlay_id}           洪涝范围内灾害点
hazard_near:{overlay_id}             近洪涝风险点
dinsar_intersect:{overlay_id}        相交 DInSAR 产品 footprint
```

### 11.4 前端整改顺序调整

基于以上补充，前端整改顺序调整为：

```text
1. 抽 FloodSourceSearchPanel，复用管理页雷达搜索/AOI 查询。
2. 水体提取页增加源影像预览图、成像时间、极化方式、模式、产品级别。
3. 洪涝检测页改为灾害事件输入：灾害时间 + 灾害位置。
4. 新增 /flood/disaster-pairs/search 后接入候选池和推荐配对。
5. 套合分析页接入 /flood/detections/{id}/overlay 和 /impact。
6. 结果与任务页从 flood_detections 过渡到 flood_products。
```

验收标准补充：

```text
水体提取：
  - 能按行政区/上传 AOI 查询源影像
  - 能显示源影像预览图
  - 列表中显示成像日期、极化方式、成像模式、产品级别
  - 水体提取结果可叠加到地图

洪涝检测：
  - 用户只需输入灾害时间和灾害位置即可获得匹配池
  - 返回 pre_pool/post_pool/candidate_pairs
  - 推荐配对可在地图上预览灾前/灾后 footprint
  - 配对结果显示时间差、AOI 覆盖率、两景重叠率、极化一致性

套合分析：
  - 运行后能看到摘要指标
  - 能看到灾害点命中和近邻风险点列表
  - 能看到关联 DInSAR 产品列表
  - warnings 可见，不静默失败
```

## 12. 2026-05-15 第一轮落地记录

本轮先闭合三条可直接使用的业务链路，不把报告生成和完整产品包导出放进同一次改动。

### 12.1 已落地

后端：
```text
1. 新增 POST /flood/disaster-pairs/search。
   输入 disaster_date + region_tree_id/aoi_geojson + 灾前灾后窗口。
   输出 pre_pool、post_pool、candidate_pairs、summary、warnings。

2. 水体提取列表补充源影像元数据。
   /flood/scenes 与 /flood/water-extractions 现在返回 imaging_date、polarization、
   imaging_mode、product_level、coverage_polygon、min/max lon/lat。

3. 套合分析接口保持并接入前端。
   POST /flood/detections/{id}/overlay
   GET  /flood/detections/{id}/impact
```

前端：
```text
1. 洪涝灾害分析 / 水体提取页：
   - 复用 AOI 行政区索引。
   - 支持按行政区筛选雷达源影像。
   - 雷达结果行增加“覆盖”和“源影像”上图。
   - 场景/水体结果行显示成像日期、极化方式、模式、产品级别。

2. 洪涝检测页：
   - 从手工填写灾前/灾后日期，改为输入灾害名称、灾害发生日期、灾害位置。
   - 自动根据窗口期生成灾前池和灾后池。
   - 候选配对展示评分、AOI 覆盖率、两景重叠率、灾前/灾后时间差、极化方式。

3. 套合分析页：
   - 可运行 overlay。
   - 可刷新并展示 impact。
   - 展示洪涝面积、命中灾害点、近洪涝风险点、关联 DInSAR 产品和 warnings。
```

### 12.2 当前保留的兼容点

```text
1. GET/POST /water/* 暂未删除，旧页面仍可过渡使用。
2. 旧 POST /flood/pairs/search 保留为手工日期兼容接口，但新工作台默认使用 /flood/disaster-pairs/search。
3. 水体提取任务底层仍写 water_detections；water_extractions 新表和正式迁移已建模，但任务处理器尚未完全切换。
4. AOI 只先复用行政区查询；上传 SHP/GeoJSON 到洪涝工作台可作为下一步补充。
```

### 12.3 下一轮建议

```text
1. 把水体提取 job handler 从 WaterDetectionORM 切到 WaterExtractionORM。
2. 给候选配对增加地图 footprint 预览按钮，辅助人工确认灾前/灾后覆盖。
3. 把 overlay 生成的 flood_vector_path 也作为地图矢量层显示。
4. 套合分析补行政区受影响面积统计。
5. 结果与任务页开始接 flood_products，而不是只列 flood_detections。
```
