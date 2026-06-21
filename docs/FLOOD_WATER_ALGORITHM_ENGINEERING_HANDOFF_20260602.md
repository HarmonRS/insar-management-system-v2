# 洪涝灾害分析模块工程交接文档

日期：2026-06-02  
面向对象：算法工程师、后端工程师  
范围：新洪涝灾害分析模块 `/flood/*`，不包含旧兼容 `/water/*` 页面和接口。

## 1. 当前定位

系统现在把洪涝分析拆成两层：

1. 工程层：负责数据入库、场景标准化、任务队列、状态更新、预览上图、套合分析、产品登记。
2. 算法层：只负责从标准化 SAR GeoTIFF 生成水体/洪涝分类栅格，并返回面积、像元数、阈值、模型信息等元数据。

后续算法优化应尽量只替换或新增 processor，不要绕开现有 `SARSceneGeoORM`、`WaterExtractionORM`、`FloodDetectionORM` 和任务队列。

## 2. 关键代码入口

| 职责 | 文件 |
| --- | --- |
| 洪涝 API 路由 | `backend/app/routers/flood.py` |
| 洪涝业务编排 | `backend/app/services/flood_analysis_service.py` |
| 后台任务执行 | `backend/app/services/job_handlers.py` |
| analysis-ready GeoTIFF 注册 | `backend/app/services/sar_analysis_ready_service.py` |
| 当前水体提取算法 | `backend/app/services/water_detect_service.py` |
| 水体 processor 包装 | `backend/app/services/water_extraction_service.py` |
| 当前洪涝变化检测算法 | `backend/app/services/flood_detection_service.py` |
| 洪涝矢量化与套合 | `backend/app/services/flood_overlay_service.py` |
| 洪涝产品登记 | `backend/app/services/flood_product_service.py` |
| 前端工作台 | `frontend/src/FloodAnalysisWorkspace.jsx` |
| 前端 API | `frontend/src/api/flood.js` |

旧 `/water/*` 路由仍在，但只作为历史兼容，不作为新算法接入目标。

## 3. 数据模型

### 3.1 SARSceneGeoORM

表：`sar_scene_geo`

这是算法输入场景表。每条记录对应一景已标准化的 SAR 分析影像。

关键字段：

| 字段 | 含义 |
| --- | --- |
| `radar_data_id` | 关联源影像 `radar_data.id` |
| `analysis_tif_path` | 算法统一输入，单波段 GeoTIFF |
| `analysis_dir` | analysis-ready 目录 |
| `analysis_preview_path` | 场景预览 PNG |
| `analysis_engine` | 标准化引擎，如 `gf3_sarscape`、`gf3_gdal`、`lt_gamma` |
| `analysis_profile` | 标准化 profile |
| `analysis_backscatter_unit` | 后向散射单位，如 `sigma0_db`、`unknown` |
| `analysis_quality_json` | 栅格尺寸、范围、nodata、采样统计 |
| `pixel_size_m` | 近似像元大小 |
| `status` | `PENDING/RUNNING/DONE/FAILED` |

当前 GF3 SARscape 链路会先产出原生 `_geo` ENVI 二进制，再由平台转换为 `D:\GaoFen3_Image_Pool\standard_l2` 下的 GeoTIFF，并注册到这里。

### 3.2 WaterExtractionORM

表：`water_extractions`

用于单景水体提取。

关键字段：

| 字段 | 含义 |
| --- | --- |
| `scene_id` | 输入场景 |
| `processor` | 算法名称，当前默认 `otsu` |
| `input_path` | 实际输入 GeoTIFF |
| `output_path` | 输出水体掩膜 GeoTIFF |
| `preview_path` | 预留，目前预览按需渲染 |
| `vector_path` | 预留，用于未来水体矢量 |
| `water_area_km2` | 水体面积 |
| `water_pixel_count` | 水体像元数 |
| `threshold_value` | 阈值或模型置信阈值 |
| `metadata_json` | 算法元数据 |
| `status/error_msg/task_id` | 任务状态 |

### 3.3 FloodDetectionORM

表：`flood_detections`

用于灾前/灾后两景洪涝变化检测。

关键字段：

| 字段 | 含义 |
| --- | --- |
| `pre_scene_id` | 灾前场景 |
| `post_scene_id` | 灾后场景 |
| `output_dir` | 输出目录，默认 `WATER_RESULTS_DIR/flood_{id}` |
| `classified_path` | 分类结果 GeoTIFF |
| `flood_area_km2` | 新增洪涝面积 |
| `stable_water_area_km2` | 稳定水体面积 |
| `status/error_msg` | 任务状态 |

分类图当前约定：

| 值 | 类别 | 前端颜色 |
| ---: | --- | --- |
| 0 | nodata | 透明 |
| 1 | stable_water | 蓝色 |
| 2 | flood | 红色 |
| 3 | high_backscatter | 橙色 |
| 4 | non_water | 灰色 |

## 4. 现有业务流程

### 4.1 单景水体提取

流程：

```text
前端选择 SARSceneGeo
-> POST /flood/water-extractions { scene_id }
-> 创建 WaterExtractionORM(PENDING)
-> 创建 SystemJob: WATER_DETECT
-> job_handlers._handle_water_detect
-> water_extraction_service.run_otsu_water_extraction
-> water_detect_service.run_water_detection
-> 写 water_mask.tif
-> 更新 WaterExtractionORM 为 DONE/FAILED
-> 前端 GET /flood/water-extractions/{id}/preview 上图
```

当前输出目录：

```text
WATER_RESULTS_DIR/
  water_extraction_{id}/
    water_mask.tif
```

当前算法状态：

- Otsu 阈值；
- GF3 线性强度自动转 `10*log10`；
- 支持 COPDEM/SRTM 类 DEM 栅格约束；
- 中值滤波、高斯滤波、形态学、连通域过滤；
- 可作为 baseline，不适合作为最终高精度算法。

### 4.2 灾前/灾后洪涝检测

流程：

```text
前端输入灾害日期 + 行政区 AOI
-> POST /flood/disaster-pairs/search
-> 后端按时间窗、AOI 覆盖率、重叠率推荐 pre/post 配对
-> POST /flood/detections { pre_scene_id, post_scene_id, refine }
-> 创建 FloodDetectionORM(PENDING)
-> 创建 SystemJob: FLOOD_DETECTION
-> job_handlers._handle_flood_detection
-> flood_detection_service.run_geotiff_flood_detection
-> 写 classified.tif/flood_mask.tif/stable_water_mask.tif/metadata.json
-> 更新 FloodDetectionORM
-> 前端加载 pre/post/classified 图层
```

当前输出目录：

```text
WATER_RESULTS_DIR/
  flood_{id}/
    classified.tif
    flood_mask.tif
    stable_water_mask.tif
    metadata.json
```

当前算法状态：

- 灾前、灾后分别 Otsu；
- 灾后水体且灾前非水体判为 flood；
- 灾前灾后均水体判为 stable_water；
- 可选 `refine` 做简单形态学清理；
- 支持灾前重投影到灾后网格；
- 还未接入更强的 GF3 双极化分类、深度学习或弱监督模型。

### 4.3 套合分析

流程：

```text
FloodDetection DONE
-> POST /flood/detections/{id}/overlay
-> classified.tif 中 value=2 的 flood 区域矢量化
-> 与灾害点、DInSAR 产品、行政区 AOI 套合
-> 写 flood_detection_{id}_overlay.geojson
-> 新增 FloodOverlayORM
```

输出：

```text
WATER_RESULTS_DIR/
  flood_overlays/
    flood_detection_{id}_overlay.geojson
```

### 4.4 产品登记

流程：

```text
FloodDetection DONE
-> POST /flood/detections/{id}/products
-> 创建 FloodProductORM
-> GET /flood/products 或 /flood/results 查询
```

当前只是轻量登记，没有完整归档包导出。

## 5. analysis-ready 输入契约

算法工程师应以 `SARSceneGeoORM.analysis_tif_path` 为唯一标准输入。

输入约定：

| 项 | 要求 |
| --- | --- |
| 格式 | 单波段 GeoTIFF |
| 坐标 | 有 CRS，推荐 EPSG:4326 或投影坐标 |
| transform | 必须正确 |
| nodata | 支持 `NaN` 或明确 nodata |
| 单位 | 可能是 dB，也可能是线性强度，需读 `analysis_backscatter_unit` 或自行稳健判断 |
| 文件大小 | GF3 单极化可达几千万像元 |

现有 GF3 SARscape 标准化结果大致为：

- 数据来自 ENVI/SARscape `_geo`；
- 转为 GeoTIFF 后注册；
- `analysis_backscatter_unit` 当前可能为 `unknown`；
- 实际数值可能是线性强度，需做 dB 转换。

## 6. 算法 processor 输出契约

### 6.1 水体提取 processor

建议新增统一接口：

```python
def run_xxx_water_extraction(
    *,
    input_path: str,
    output_dir: str,
    job_id: str | None = None,
    options: dict | None = None,
) -> dict:
    ...
```

返回：

```python
{
    "ok": True,
    "processor": "gf3_rf_v1",
    "output_path": ".../water_mask.tif",
    "water_area_km2": 123.45,
    "water_pixel_count": 123456,
    "threshold_value": 0.62,
    "metadata": {
        "model_version": "...",
        "features": ["hh_db", "hv_db", "ratio", "slope"],
        "confidence_path": ".../water_probability.tif"
    }
}
```

最低要求：

- `output_path` 是 GeoTIFF；
- 水体像元值为 `255` 或 `1`，背景为 `0`；
- CRS/transform 与输入一致；
- nodata 推荐为 `0`；
- 面积统计要与输出一致。

### 6.2 洪涝检测 processor

建议接口：

```python
def run_xxx_flood_detection(
    *,
    pre_tif_path: str,
    post_tif_path: str,
    output_dir: str,
    job_id: str | None = None,
    refine: bool = False,
    options: dict | None = None,
) -> dict:
    ...
```

返回：

```python
{
    "ok": True,
    "processor": "gf3_change_rf_v1",
    "classified_path": ".../classified.tif",
    "flood_mask_path": ".../flood_mask.tif",
    "stable_water_mask_path": ".../stable_water_mask.tif",
    "metadata_path": ".../metadata.json",
    "flood_area_km2": 12.34,
    "stable_water_area_km2": 56.78,
    "flood_pixel_count": 12345,
    "stable_water_pixel_count": 67890,
    "metadata": {
        "model_version": "...",
        "pre_scene_reprojected": True
    }
}
```

`classified.tif` 必须遵守第 3.3 节的分类值，否则前端预览和套合分析会失效。

## 7. 推荐算法路线

### 7.1 短期：GF3 快速分类器

目标：替换当前单阈值水体提取，减少误判。

建议 processor 名称：

- `gf3_rf_v1`
- `gf3_lgbm_v1`

输入：

- 优先支持 GF3 HH/HV 双极化；
- 如果系统当前只注册单极化 `analysis_ready.tif`，工程侧需要补充“同一产品多极化查找”能力，或算法先支持单极化。

特征建议：

- `HH_db`
- `HV_db`
- `HH-HV`
- `HH/HV ratio`
- 局部均值、方差、纹理；
- DEM 高程、坡度；
- 可选永久水体、河网、土地覆盖先验。

样本策略：

- 第一版可用弱监督样本：永久水体为正样本，远离水系/坡度较大/高后向散射区域为负样本；
- 后续在系统内加入人工修正样本导出；
- 不建议用当前 Otsu 结果直接当唯一标签。

### 7.2 中期：GF3 深度学习推理

目标：面向洪涝产品的高质量识别。

可参考：

- Sen2GF3Floods：GF3 洪水数据集和 PyTorch 代码；
- FCN/UNet++/DeepLabV3+/SegFormer；
- 支持 patch 推理和边缘重叠融合。

工程要求：

- 模型权重必须版本化；
- processor 输出必须仍是标准 GeoTIFF；
- 推理可以 GPU 加速，但不能阻塞任务队列主进程；
- 大图必须 tile 化，避免一次性占满显存/内存。

### 7.3 保留 baseline

当前 `otsu` 应保留为：

- 快速预览；
- 无模型时兜底；
- 算法对比 baseline。

不建议作为最终默认高质量结果。

## 8. 工程侧下一步

### 8.1 后端 processor 注册

建议在 `water_extraction_service.py` 增加 processor 分发：

```python
def run_water_extraction(processor: str, **kwargs):
    if processor == "otsu":
        return run_otsu_water_extraction(**kwargs)
    if processor == "gf3_rf_v1":
        return run_gf3_rf_water_extraction(**kwargs)
    ...
```

`FloodWaterExtractionRequest` 需要增加：

```python
processor: str = "otsu"
options: dict | None = None
```

然后 `submit_water_extraction` 把 processor/options 写入 `WaterExtractionORM` 和 job payload。

### 8.2 多极化场景组织

目前 `SARSceneGeoORM` 与 `RadarDataORM` 基本是一景一条。GF3 标准化链路可能存在 HH/HV 两个 GeoTIFF，但洪涝算法输入仍是单 `analysis_tif_path`。

如果算法需要 HH/HV，应补一个工程能力：

- 在 `analysis_metadata_json` 中记录同产品全部极化 GeoTIFF；
- 或新增 `SARSceneBandORM`/`analysis_assets` 表；
- 或在 processor 内根据当前路径和命名规则寻找同目录同产品 HV/HH。

建议先采用 metadata 方案，改动最小。

### 8.3 水体结果矢量化

当前水体提取只按需返回 PNG 预览，没有持久化矢量。

建议新增：

- `water_vector_path` GeoJSON；
- `confidence_path` 概率图；
- `preview_path` 持久 PNG；
- 后端接口支持水体结果矢量上图。

### 8.4 质量评估字段

建议 `metadata_json` 至少写入：

- `processor`
- `model_version`
- `input_paths`
- `features`
- `threshold`
- `confidence_stats`
- `valid_pixel_count`
- `water_ratio`
- `runtime_seconds`
- `warnings`

### 8.5 前端入口

当前前端有水体提取按钮，但没有 processor 选择。

建议增加：

- 水体提取 processor 下拉框；
- `快速 Otsu / GF3 RF / 深度学习`；
- 结果行显示 processor 和模型版本；
- 可选显示置信度图层。

## 9. 算法开发边界

算法工程师只需要保证：

1. 能读取输入 GeoTIFF；
2. 能输出符合契约的 GeoTIFF；
3. 返回标准 dict；
4. 大图处理不会把内存/显存打爆；
5. 错误抛出清晰异常或返回 `{"ok": False, "error": "..."}`。

算法工程师不需要处理：

- 前端；
- 任务队列；
- 用户权限；
- 数据库事务；
- 资产扫描；
- 上图预览；
- 套合分析；
- 产品登记。

## 10. 当前风险和已知问题

1. 当前 Otsu 水体提取误判较多，只能作为 baseline。
2. GF3 双极化没有形成正式算法输入契约。
3. `analysis_backscatter_unit` 对 GF3 SARscape 输出仍可能是 `unknown`。
4. 洪涝变化检测仍是双阈值差分，复杂地物下误判会明显。
5. 水体和洪涝结果缺少质量评价和置信度图层。
6. 产品登记还不是完整归档包。
7. 旧 `/water/*` 和新 `/flood/*` 共存，后续需要逐步收敛到 `/flood/*`。

## 11. 建议交付里程碑

### M1：GF3 RF 水体 processor

- 输入单景 GF3 HH/HV 或单极化；
- 输出 `water_mask.tif`；
- 写入模型元数据；
- 接入 `/flood/water-extractions`。

### M2：多极化输入契约

- 工程侧让 processor 能稳定拿到 HH/HV；
- 文档化极化路径和 metadata；
- 前端显示使用的极化。

### M3：洪涝变化 processor

- 输入灾前/灾后；
- 输出标准 `classified.tif`；
- 与现有套合和产品链路兼容。

### M4：深度学习推理

- 支持 tile 推理；
- 支持模型版本；
- 输出概率图和二值图；
- 与 RF/baseline 可切换。
