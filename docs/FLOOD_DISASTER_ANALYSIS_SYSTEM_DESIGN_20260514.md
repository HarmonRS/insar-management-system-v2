# 水体提取与洪涝灾害分析设计

更新日期：2026-05-14

## 1. 设计结论

这个模块不应该被设计成一堆遥感处理工具的集合。它的业务目标很简单：

```text
提取水体 -> 检测洪涝 -> 套合已有矢量数据 -> 输出结果
```

因此前端和后端都应围绕这条流水线组织，而不是暴露过多工程阶段。用户关心的是：

- 哪些数据可以拿来提水体。
- 哪两期数据可以用来判断洪涝。
- 洪涝范围在哪里、面积多大。
- 影响了哪些灾害点、行政区、AOI 或其他矢量对象。
- 结果能不能上图、导出、形成报告。

## 2. 当前真实边界

现有代码不能理解为已经具备多源 ENVI 洪涝系统。

当前事实：

- LT-1 有 `SARsImportLuTan1` 相关 ENVI/SARscape 链路，可作为第一条精密检测主线。
- GF3 有纯 Python/GDAL 的 L1A 到 L2 处理，不是 ENVI/SARscape 洪涝导入。
- Sentinel-1 有源产品、ZIP/SAFE、EOF、PyINT/Gamma 相关管理设计，不是 ENVI/SARscape 洪涝导入。
- 当前 `water` 模块已经有旧的单景 geocode、Otsu 快速检测、精密洪涝检测和地图预览能力，可作为迁移基础。

所以第一阶段不能承诺 GF3/Sentinel-1 可直接做 ENVI 精密洪涝检测。页面上必须显示为“待接入导入适配器”。

## 3. 产品信息架构

左侧一级入口合并为：

```text
洪涝灾害分析
├─ 洪涝灾害分析
└─ 水体监测（旧入口）
```

新的“洪涝灾害分析”工作台只保留四个视图：

```text
1. 水体提取
2. 洪涝检测
3. 套合分析
4. 结果与任务
```

旧“水体监测”入口短期保留，承载当前 `WaterMonitorPanel` 的功能。后续功能成熟后再逐步迁入新工作台。

## 4. 地图设计

不新建第二套地图。

当前中间地图大屏已经有底图、行政区定位、导出、雷达覆盖、洪涝影像叠加能力。新工作台应复用 `AppMapWorkspace` 和 `App.jsx` 中的 Leaflet 实例，通过回调控制图层：

```text
FloodAnalysisWorkspace
  -> onShowSourceSceneOnMap
  -> onShowFloodOnMap
  -> onToggleFloodLayer
```

地图只负责展示，业务按钮放在左侧工作台。

## 5. 流水线设计

### 5.1 水体提取

目标：把单期雷达影像处理成水体范围。

用户动作：

- 选择数据。
- 查看数据是否可处理。
- 提交水体提取。
- 查看水体范围。
- 加载到地图。

数据状态建议：

- `已入库`：系统只管理了原始/解压数据。
- `可提取`：已有可用处理器。
- `处理中`：任务正在运行。
- `已提取`：已有水体范围产品。
- `待接入`：数据存在，但缺少对应导入/处理适配器。

传感器边界：

- LT-1：优先接旧 ENVI/SARscape 链路。
- GF3：待补 ENVI/SARscape 洪涝导入；现有 Python/GDAL 成果可作为后续快速路线输入。
- Sentinel-1：待补 ENVI/SARscape 洪涝导入，或后续明确非 ENVI 检测路线。

### 5.2 洪涝检测

目标：比较灾前/灾后水体变化，识别新增水体作为洪涝范围。

用户动作：

- 选择灾前数据。
- 选择灾后数据。
- 自动推荐配对。
- 提交洪涝检测。
- 查看洪涝面积和稳定水体面积。
- 加载灾前、灾后、分类图到地图。

检测路线：

- 精密路线：ENVI/SARscape `SARsBasicFeFloodingClassification`。
- 快速路线：Otsu/GeoTIFF 水体掩膜差异，作为轻量能力。

输出类别：

```text
1 = 稳定水体
2 = 洪涝/新增水体
3 = 高散射
4 = 非水体
```

### 5.3 套合分析

目标：把洪涝范围与已有矢量数据叠加，回答“影响了什么”。

第一阶段套合对象：

- 灾害点。
- 行政区。
- 当前 AOI。
- 自定义矢量。

输出：

- 洪涝范围矢量。
- 受影响灾害点清单。
- 行政区/AOI 洪涝面积统计。
- 距离洪涝范围一定阈值内的风险点。
- GeoJSON 和表格导出。

灾害点关系建议：

- `inside_flood`：点落在洪涝范围内。
- `near_flood`：点距离洪涝边界小于阈值。
- `inside_scene_only`：点在影像覆盖范围内，但未受洪涝影响。

面积统计必须使用合适投影，不能直接用经纬度面积作为正式统计。

### 5.4 结果与任务

目标：统一查看任务、图层、结果和导出。

展示字段：

- 结果 ID。
- 灾前日期。
- 灾后日期。
- 卫星组合。
- 处理器。
- 洪涝面积。
- 稳定水体面积。
- 影响灾害点数量。
- 状态。
- 更新时间。

操作：

- 加载图层。
- 打开产品包。
- 导出 GeoTIFF/GeoJSON。
- 生成报告。

## 6. 后端域模型

后端可以继续兼容 `/water/*`，但新能力建议逐步进入 `/flood/*`。

最小模型不要过度复杂，先围绕四类对象：

```text
WaterExtractionRun
FloodDetectionRun
FloodOverlayRun
FloodResultProduct
```

如果后续需要多源产品治理，再扩展：

```text
FloodReadyProduct
FloodPair
FloodVectorAsset
FloodReport
```

## 7. API 草案

### 水体提取

```text
GET  /flood/sources
POST /flood/water-extractions
GET  /flood/water-extractions
GET  /flood/water-extractions/{id}
GET  /flood/water-extractions/{id}/preview
```

### 洪涝检测

```text
POST /flood/pairs/search
POST /flood/detections
GET  /flood/detections
GET  /flood/detections/{id}
GET  /flood/detections/{id}/preview/{layer}
```

### 套合分析

```text
POST /flood/detections/{id}/vectorize
POST /flood/detections/{id}/overlay
GET  /flood/detections/{id}/impact
```

### 结果导出

```text
GET  /flood/results
GET  /flood/results/{id}
POST /flood/reports
```

## 8. 前端按钮流

### 水体提取

- 查询数据。
- 显示覆盖。
- 提交水体提取。
- 批量提取。
- 查看水体结果。

### 洪涝检测

- 自动推荐配对。
- 选择灾前/灾后。
- 提交检测。
- MRF 精化开关。
- 加载图层。
- 去套合分析。

### 套合分析

- 运行套合分析。
- 加载影响点。
- 导出影响清单。
- 导出 GeoJSON。

### 结果与任务

- 刷新结果。
- 加载全部图层。
- 打开产品包。
- 生成报告。
- 导出成果。

## 9. 分阶段实施

### Phase 1：前端流水线壳层

- 合并“水体监测”和“洪涝灾害分析”为一个业务组。
- 新工作台只保留水体提取、洪涝检测、套合分析、结果与任务。
- 复用旧 `/water/*` 数据展示当前已有能力。
- 明确 GF3/Sentinel-1 为待接入，不显示为可精密检测。

### Phase 2：LT-1 主线打通

- 把当前 `water_service.py` 中 LT-1 ENVI 链路整理成清晰的水体/洪涝任务。
- 输出标准结果。
- 完成地图预览和结果列表。

### Phase 3：套合分析

- 分类栅格转洪涝矢量。
- 接入灾害点套合。
- 接入行政区/AOI 统计。
- 输出影响清单。

### Phase 4：多源扩展

- GF3 ENVI/SARscape 导入适配器或替代检测路线。
- Sentinel-1 ENVI/SARscape 导入适配器或替代检测路线。
- 多源配对策略和质量提示。

## 10. 设计原则

- 页面按业务问题组织，不按算法步骤堆控件。
- 数据已入库不代表可检测，必须明确可用性状态。
- 地图只保留一套，避免图层和状态重复。
- 旧 `water` 模块保留迁移期入口，但新功能进入 `FloodAnalysisWorkspace`。
- 套合分析是核心能力，不是结果页上的附属按钮。
