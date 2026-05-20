# 洪涝模块整改进展记录

## 2026-05-15 第二轮：水体提取表切换

本轮目标是把洪涝工作台里的水体提取正式从旧 `water_detections`
迁到新 `water_extractions`，同时保留旧 `/water/*` 兼容窗口。

### 已完成

```text
1. 新增 backend/app/services/water_extraction_service.py。
   - run_otsu_water_extraction 复用旧 Otsu 实现。
   - 对外使用 extraction/processor/threshold_value 命名。
   - run_envi_water_extraction 暂留占位，后续接 ENVI/SARscape。

2. /flood/water-extractions 新提交任务开始写 water_extractions。
   - 任务 payload 使用 extraction_id。
   - WaterExtractionORM.processor 默认 otsu。
   - task_id、threshold_value、metadata_json 随任务更新。

3. WATER_DETECT job handler 支持双轨兼容。
   - 新 payload: extraction_id -> 写 water_extractions。
   - 旧 payload: detection_id -> 继续写 water_detections。
   - 如果旧 detection_id 已经被 alembic 0003 回填到 water_extractions，
     handler 会同步更新同 ID 的新表记录，避免迁移期状态卡住。

4. /flood/water-extractions 列表改读 water_extractions。
   - 旧数据依赖 alembic 0003 从 water_detections 回填。
   - preview 优先读 WaterExtractionORM，找不到时回退 WaterDetectionORM。
```

### 仍保留

```text
1. 旧 /water/detect 仍创建 water_detections。
   这是兼容窗口内的刻意保留，不再作为洪涝工作台主链路。

2. ENVI/SARscape 精密水体提取还没有接入任务队列。

3. 如果部署环境已有旧 PENDING water_detections 任务，
   需要先执行 alembic 0003，再启动 worker。
```

### 下一步

```text
1. 给候选洪涝配对增加灾前/灾后 footprint 地图预览。
2. 把 flood_vector_path 作为矢量图层上图。
3. 套合分析补行政区受影响面积统计。
4. 结果与任务页接 flood_products。
```

## 2026-05-15 第三轮：地图闭环与产品页

### 已完成

```text
1. 候选洪涝配对支持地图预览。
   - 灾前 footprint 蓝色。
   - 灾后 footprint 绿色。
   - 配对行增加“预览覆盖”。

2. 套合结果支持洪涝矢量上图。
   - GET /flood/detections/{id}/impact 返回 overlay_id、flood_vector_path、flood_vector_geojson。
   - 前端套合分析页增加“加载洪涝矢量”。

3. 套合分析补行政区影响统计。
   - overlay 运行时读取 AOI 行政区边界索引。
   - 返回 affected_aois，包含 tree_id、name、level、flood_area_km2。
   - 前端展示影响行政区数量和前 5 个行政区。

4. 结果与任务页接 flood_products。
   - 洪涝结果行增加“生成产品”。
   - 结果页增加“产品”tab。
   - 产品行展示 product_id、洪涝面积、影响面积、生成时间。
   - Manifest 按钮读取 /flood/products/{id}/manifest。
```

### 当前可集中测试的链路

```text
1. 水体提取：
   入库影像查询 -> 行政区筛选 -> 源影像上图 -> 提交预处理 -> 提交水体提取 -> 水体结果上图。

2. 洪涝检测：
   输入灾害时间 + 灾害位置 -> 推荐配对 -> 预览覆盖 -> 提交洪涝检测 -> 分类图上图。

3. 套合分析：
   选择 DONE 洪涝结果 -> 运行套合分析 -> 加载洪涝矢量 -> 查看灾害点、DInSAR、行政区统计。

4. 产品：
   DONE 洪涝结果 -> 生成产品 -> 产品 tab 查看 -> 读取 manifest。
```

## 2026-05-15 第四轮：GeoTIFF 化技术决策

本轮根据新的产品判断调整洪涝模块技术路线：不再把 ENVI/SARscape
作为洪涝算法主线，后续水体提取、洪涝检测、套合分析全部运行在标准
GeoTIFF 层。原始 SAR 到 GeoTIFF 的部分做成可插拔前处理器，优先评估
GAMMA，GAMMA 覆盖不了的传感器使用 GF3_GDAL、SNAP、ISCE 或 external
处理器兜底。

### 已完成

```text
1. 新增 docs/FLOOD_GEOTIFF_GAMMA_PREPROCESS_DESIGN_20260515.md。
   - 总结当前洪涝模块仍绑定 ENVI/SARscape 的真实状态。
   - 梳理当前 GAMMA/PyINT 在项目中的能力和边界。
   - 明确 GAMMA 适合作为 TIF 前处理候选，但不能把 D-InSAR pair profile
     直接复用为洪涝单景预处理。
   - 定义 analysis_ready.tif / SarAnalysisScene 数据契约。
   - 给出 Python GeoTIFF 洪涝检测和 GAMMA 单景前处理分阶段方案。

2. 更新 docs/INDEX.md。
   - 将 GeoTIFF 化设计列入当前执行中的核心设计。
   - 标明 2026-05-14 旧洪涝设计中的 ENVI/SARscape 主线表述已被新设计取代。
```

### 当前判断

```text
1. 洪涝算法应先从 SARscape 黑盒迁出，改成 pre/post GeoTIFF 上的 Python 算法。
2. GAMMA 适合优先做 LT-1/Sentinel-1 的 analysis_ready.tif 生产器。
3. 当前 GAMMA 接入已有 geocode/data2geotiff 能力，但缺少独立单景预处理服务。
4. GF3 已有 Python/GDAL L1A->L2 路线，不应强行改走 GAMMA。
5. 另一台服务器部署时，Git 能提供平台代码和算法；GAMMA 本体、DEM、轨道池、
   conda/WSL 运行时仍是外部部署前提。
```
