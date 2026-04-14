# 水体监测模块 v2 — 任务清单

## 背景

原 water_service.py（手动 OTSU + 形态学）方案废弃。
改用 SARscape 原生任务链，精度更高，与 D-InSAR 流程一致。

## SARscape 任务链

```
单景预处理（每景独立）：
  SARsBasicMultilooking   → 多视处理（SLC → 强度图，降噪）
  SARsBasicGeocoding      → 地理编码 + 辐射定标（输出 dB 图）

洪涝检测（两景配对）：
  SARsBasicFeFloodingClassification          → 洪涝分类图
  SARsBasicFeFloodingClassificationRefinement → MRF 精化（可选）
```

## 关键 Task 参数速查

### SARsBasicMultilooking
- INPUT_SARSCAPEDATA (required)
- OUTPUT_SARSCAPEDATA (output)
- RANGE_MULTILOOK, AZIMUTH_MULTILOOK (可选，默认自动)
- GRID_SIZE_FOR_SUGGESTED_LOOKS (目标分辨率 m)
- ROOT_URI_FOR_OUTPUT

### SARsBasicGeocoding
- INPUT_SARSCAPEDATA (required)
- OUTPUT_SARSCAPEDATA, OUTPUT_DB_SARSCAPEDATA (output)
- DEM_SARSCAPEDATA (可选，有 DEM 精度更高)
- GEOCODE_GRID_SIZE_X, GEOCODE_GRID_SIZE_Y (像素大小 m)
- CALIBRATION: true（辐射定标）
- OUTPUT_TYPE: "output_type_db"（输出 dB）
- ROOT_URI_FOR_OUTPUT

### SARsBasicFeFloodingClassification
- INPUT_SARSCAPEDATA (required, 灾前)
- POST_EVENT_FILE (required, 灾后)
- OUTPUT_SARSCAPEDATA (output, 分类图)
- DEM_FILE, SLOPE_FILE (可选，提升精度)
- SWL_TH: 水体阈值 dB（默认约 -14）
- RATIO_TH: 比值阈值 dB
- HIGH_SCATT_POINT_TH: 高散射点阈值 dB
- RATIO_SARSCAPEDATA, PRE_EVENT_SARSCAPEDATA, POST_EVENT_SARSCAPEDATA (output)
- ROOT_URI_FOR_OUTPUT

### SARsBasicFeFloodingClassificationRefinement
- PRE_EVENT_FILE, POST_EVENT_FILE, CLASSIFIED_FILE, RATIO_FILE (required)
- OUTPUT_SARSCAPEDATA (output)
- DEM_FILE, SLOPE_FILE (可选)
- MRF 参数：M_STABLE_WATER, M_FLOOD, ALPHA_STABLE_WATER, ALPHA_FLOOD 等
- ROOT_URI_FOR_OUTPUT

---

## 任务列表

### W2-01 [TODO] 清理旧 water 代码
- 删除 backend/app/services/water_service.py
- 删除 backend/app/routers/water.py
- 清理 backend/app/models/orm.py 中的 WaterMaskORM / WaterPairORM / FloodEventORM
- 清理 backend/app/models/__init__.py 中的 water 导入
- 清理 backend/app/services/job_handlers.py 中的 WATER_EXTRACT / WATER_DETECT
- 删除 backend/alembic/versions/0002_water_tables.py
- 删除 frontend/src/api/water.js
- 删除 frontend/src/WaterMonitorPanel.jsx
- 清理 frontend/src/config/appConstants.js（移除 'water' tab）
- 清理 frontend/src/utils/appUiHelpers.js（移除 water case）
- 清理 frontend/src/App.jsx（移除 WaterMonitorPanel 引用）

### W2-02 [TODO] 设计新 ORM 模型
新增两张表：

**SARSceneGeoORM** (sar_scene_geo)
- id, radar_data_id (FK → radar_data.id)
- geo_path: 地理编码 dB 文件路径（无扩展名，ENVI 格式）
- pixel_size_m: 像素大小
- status: PENDING / RUNNING / DONE / FAILED
- error_msg
- created_at, updated_at

**FloodDetectionORM** (flood_detections)
- id
- pre_scene_id (FK → sar_scene_geo.id)
- post_scene_id (FK → sar_scene_geo.id)
- output_dir: 输出目录
- classified_path: 分类图路径
- flood_area_km2: 洪涝面积
- stable_water_area_km2: 稳定水体面积
- status: PENDING / RUNNING / DONE / FAILED
- error_msg
- created_at, updated_at

### W2-03 [TODO] 编写 Alembic 迁移
- 新建 backend/alembic/versions/0002_water_v2.py
- down_revision = "0001"
- 创建 sar_scene_geo 和 flood_detections 表
- 注意：旧 water_masks / water_pairs / flood_events 表如存在需 drop

### W2-04 [TODO] 实现单景预处理服务
新建 backend/app/services/water_service.py（全新）

函数：`run_geocoding_workflow_sync(radar_data_id, db_session, job_id)`
1. 查 RadarDataORM 获取 file_path
2. 找 SLC 文件（_slc 后缀，ENVI 格式）
3. 创建输出目录：`water_results/{radar_unique_id}/`
4. 调用 execute_envi_task("SARsBasicMultilooking", ...)
5. 调用 execute_envi_task("SARsBasicGeocoding", ..., OUTPUT_TYPE="output_type_db")
6. 解析输出路径，更新 SARSceneGeoORM.status = DONE
7. 写进度文件（复用 _write_progress 机制）

### W2-05 [TODO] 实现洪涝检测服务
在 water_service.py 中新增：

函数：`run_flood_detection_sync(pre_scene_id, post_scene_id, db_session, job_id, refine=False)`
1. 查两个 SARSceneGeoORM，验证 status=DONE
2. 创建输出目录：`water_results/flood_{pre_id}_{post_id}/`
3. 调用 execute_envi_task("SARsBasicFeFloodingClassification", ...)
4. 可选：调用 SARsBasicFeFloodingClassificationRefinement
5. 用 rasterio 读分类图，统计各类像素面积（km²）
6. 更新 FloodDetectionORM

### W2-06 [TODO] 更新 job_handlers.py
- 注册 JOB_TYPE_WATER_GEOCODE = "WATER_GEOCODE"
- 注册 JOB_TYPE_WATER_FLOOD = "WATER_FLOOD"
- 实现 _handle_water_geocode / _handle_water_flood
- 加入 _HANDLERS 字典

### W2-07 [TODO] 更新 envi_runner_cli.py
- choices 增加 "water_geocode" / "water_flood"
- main() 中路由到对应 water_service 函数

### W2-08 [TODO] 新建 water.py router
- POST /water/geocode (admin): 提交单景地理编码任务
- GET  /water/scenes: 列出已处理场景（关联 radar_data 的 satellite/date）
- POST /water/flood-detect (admin): 提交洪涝检测任务
- GET  /water/flood-events: 列出洪涝检测结果
- GET  /water/flood-events/{id}/preview: 返回分类图预览

### W2-09 [TODO] 新建 WaterMonitorPanel.jsx（前端）
三个 Tab：

**Tab 1 — 单景预处理**
- 从雷达数据列表选择一景（或输入 radar_data_id）
- 显示已处理场景列表（状态、像素大小、处理时间）
- 提交按钮 → POST /water/geocode

**Tab 2 — 洪涝检测**
- 选择灾前场景 + 灾后场景（从已处理列表选）
- 可选：是否启用 MRF 精化
- 提交按钮 → POST /water/flood-detect

**Tab 3 — 洪涝事件**
- 列表：灾前日期、灾后日期、洪涝面积、稳定水体面积、状态
- 点击查看分类图预览

### W2-10 [TODO] 注册路由 + 前端 Tab
- backend/app/routers/__init__.py 引入 water router
- frontend/src/config/appConstants.js 加 'water' tab
- frontend/src/utils/appUiHelpers.js 加 case
- frontend/src/App.jsx 引入 WaterMonitorPanel
- frontend/src/api/water.js（新建 API 封装）

### W2-11 [TODO] 测试验证
- 用 Image_Pool_2025 中一景 LT1 数据测试单景预处理
- 选两景配对测试洪涝检测
- 验证分类图输出和面积统计

---

## 进度

| 任务 | 状态 |
|------|------|
| W2-01 清理旧代码 | DONE |
| W2-02 新 ORM 模型 | DONE |
| W2-03 Alembic 迁移 | DONE |
| W2-04 单景预处理服务 | DONE |
| W2-05 洪涝检测服务 | DONE |
| W2-06 job_handlers | DONE |
| W2-07 envi_runner_cli | DONE |
| W2-08 water.py router | DONE |
| W2-09 WaterMonitorPanel | DONE |
| W2-10 路由注册+前端Tab | DONE |
| W2-11 测试验证 | TODO |
