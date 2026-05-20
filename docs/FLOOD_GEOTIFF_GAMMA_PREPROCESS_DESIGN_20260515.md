# 洪涝模块 GeoTIFF 化与 GAMMA 前处理评估

更新日期：2026-05-15

## 1. 结论

洪涝模块后续应把业务算法全部下放到标准 GeoTIFF 层：

```text
原始 SAR 数据
  -> 可插拔前处理器：GAMMA 优先，其他处理器兜底
  -> 分析级 GeoTIFF
  -> Python 水体提取 / 洪涝检测 / 套合分析 / 产品生成
```

ENVI/SARscape 不再作为洪涝算法主线。它可以短期保留为历史兼容处理器，但不应继续决定数据模型、接口命名和算法逻辑。

GAMMA 适合作为第一候选前处理底座，原因是当前项目已经具备 WSL、PyINT、GAMMA 环境注入、DEM、`geocode_back`、`data2geotiff` 和 D-InSAR 生产发布经验。但现有 GAMMA/PyINT 接入是 D-InSAR pair 流程，不是洪涝需要的“单景分析级 GeoTIFF 预处理服务”。因此不能直接把 `lt1_gamma_dinsar` 或 `s1_gamma_dinsar` 原样接到洪涝预处理按钮上，必须新增单景前处理适配层。

## 2. 当前真实情况

### 2.1 洪涝模块当前仍绑定 ENVI/SARscape

当前 `/flood/preprocess` 最终仍执行旧的 `water_service.run_geocoding_workflow()`，内部流程是：

```text
SARsImportLuTan1
  -> SARsBasicMultilooking
  -> SARsBasicGeocoding
```

输出写入 `SARSceneGeoORM.geo_path`，语义是 SARscape 地理编码 dB 影像 base path，不是标准 GeoTIFF 交付物。

当前 `/flood/detections` 后台任务仍调用 `water_service.run_flood_detection()`，内部流程是：

```text
SARsBasicFeFloodingClassification
  -> 可选 SARsBasicFeFloodingClassificationRefinement
  -> rasterio 统计分类结果面积
```

分类值约定是：

```text
0 = 无数据 / 背景
1 = 稳定水体
2 = 洪涝 / 新增水体
3 = 高散射点
4 = 非水体
```

这说明当前“洪涝检测算法”实际是 SARscape 黑盒，不是平台自有算法。

### 2.2 已经接近目标形态的部分

以下部分已经更接近“标准栅格 + Python 算法”的目标：

```text
水体提取：
  water_extraction_service -> water_detect_service
  Otsu + DEM/坡度约束 + 形态学 + 连通分量过滤

套合分析：
  flood_overlay_service
  读取 classified 栅格，class=2 转矢量，套合灾害点、DInSAR 产品和 AOI

GF3：
  gf3_service
  Python/GDAL 完成 L1A -> L2：辐射定标 + RPC 几何校正
```

其中 GF3 路线虽然不走 GAMMA，但输出理念已经是“处理到 GeoTIFF 后进入平台算法”。它应该作为 `external/gdal` 前处理器接入统一 Scene，而不是继续挂在 `/water/gf3-process` 下。

### 2.3 当前 GAMMA/PyINT 接入情况

当前仓库里的 GAMMA/PyINT 能力主要服务 D-InSAR 生产：

```text
backend/app/dinsar_engines/pyint_engine.py
  - lt1_gamma_dinsar
  - s1_gamma_dinsar

backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py
  - LT-1 托管 runner
  - geocode_gamma.py
  - data2geotiff
  - 标准产品发布

backend/app/pyint_pipeline/run_s1_pyint_pipeline.py
  - Sentinel-1 runner
  - 复用 LT-1 runner 的日志、发布和质量检查框架

deploy/wsl/profiles/gamma_env.sh
  - 注入 GAMMA_HOME
  - 注入 GAMMA 各模块 bin/scripts
  - 注入 third_party/PyINT/pyint
```

当前 `.env` 中 PyINT/GAMMA 是启用状态：

```text
PYINT_ENABLED=true
PYINT_WSL_DISTRO=Ubuntu-24.04
PYINT_WSL_PYTHON=/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python
PYINT_DEM_MODE=prepared_file
PYINT_PREPARED_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape.wgs84
PYINT_GEO_INTERP=1
PYINT_GAMMA_NODATA_VALUE=-9999.0
```

需要注意一个不一致点：`pyint_engine.py` 已经暴露 `s1_gamma_dinsar`，但 `wsl_runtime_registry.py` 当前允许的 PyINT operation 仍只有 `lt1_gamma_dinsar` 和 `gamma_refine`。如果后续洪涝或生产任务走 WSL Broker，需要同步补齐允许列表。

### 2.4 GAMMA 现有输出与洪涝需求的差距

现有 D-InSAR GAMMA 链已经能导出 GeoTIFF，但输出重点是 pair 产品：

```text
disp.tif
disp_unmasked.tif
coh.tif
wrapped_phase.tif
vertical displacement
look vector
```

`hyp3format_gamma.py` 也能把 `geocode_gamma.py` 产生的 geocoded GAMMA 二进制导出为 GeoTIFF，包含：

```text
amp
corr
dem
unw_phase
wrapped_phase
los_disp
vert_disp
lv_theta
lv_phi
```

但洪涝模块真正需要的是单景产品：

```text
analysis_ready.tif
  - 单景后向散射强度
  - 明确单位：sigma0_db / gamma0_db / power_db
  - 明确极化：VV / VH / HH / HV
  - 明确 CRS、分辨率、nodata、覆盖范围
  - 可与另一景重采样到同一网格
```

当前没有一个独立的 `gamma_single_scene_preprocess` 服务稳定地产出上述文件。现有 D-InSAR 流程里的 `geo_<master>_*.amp` 可以证明 GAMMA 有地理编码 MLI/AMP 的能力，但它嵌在 pair 配准和干涉流程中，不能直接作为洪涝单景预处理接口。

## 3. 目标数据契约

后续洪涝模块不再以 `geo_path` 表示模糊的“地理编码产物”，而应引入明确的分析级 Scene 产物语义。

建议模型命名：

```text
SarAnalysisScene / AnalysisReadyScene
```

最小字段：

```text
id
radar_data_id
engine                         # gamma / gf3_gdal / snap / envi_legacy / external
engine_profile                 # lt1_gamma_scene / s1_gamma_scene / gf3_rpc_l2
analysis_tif_path              # 主分析 GeoTIFF
preview_path
coverage_polygon
satellite
satellite_family               # LT1 / S1 / GF3 / ...
imaging_date
polarization
orbit_direction
relative_orbit
backscatter_unit               # sigma0_db / gamma0_db / power_db / unknown
crs
pixel_size_x
pixel_size_y
nodata_value
grid_id                        # 同网格配对时使用
dem_path
incidence_angle_tif_path       # 可选
layover_shadow_mask_path       # 可选
quality_json
lineage_json
status
error_msg
created_at
updated_at
```

输出文件要求：

```text
analysis_ready.tif:
  - GeoTIFF，优先 COG
  - float32
  - nodata = -9999
  - 推荐值域为 dB
  - rasterio/GDAL 可直接读取

preview.png:
  - 仅用于地图快视

metadata.json:
  - 记录处理器、命令、输入资产、DEM、参数、软件版本、质量检查
```

## 4. 目标流水线

### 4.1 前处理器只负责到 GeoTIFF 为止

前处理器接口建议统一为：

```text
submit_scene_preprocess(radar_data_id, engine="gamma", profile="auto")
```

处理器职责：

```text
1. 识别输入资产和传感器类型。
2. 导入原始产品到处理器内部格式。
3. 做必要的辐射定标。
4. 做多视 / 滤波 / speckle 控制。
5. 使用 DEM 做地形校正或地理编码。
6. 导出 analysis_ready.tif。
7. 生成 coverage、preview、metadata、quality。
8. 注册 SarAnalysisScene。
```

处理器不负责：

```text
1. 水体提取。
2. 洪涝分类。
3. 洪涝矢量化。
4. 灾害点套合。
5. 报告生成。
```

### 4.2 洪涝算法只认 GeoTIFF

水体提取：

```text
analysis_ready.tif
  -> 读取 dB 栅格
  -> 有效像元 / nodata / DEM / 坡度约束
  -> Otsu 或自适应阈值
  -> 形态学与连通域过滤
  -> water_mask.tif + water_vector.geojson
```

洪涝检测：

```text
pre analysis_ready.tif
post analysis_ready.tif
  -> 检查 CRS / 分辨率 / 极化 / 覆盖范围
  -> 重采样到同一网格
  -> 分别生成 pre_water_mask / post_water_mask
  -> post_water && !pre_water = 洪涝 / 新增水体
  -> pre_water && post_water = 稳定水体
  -> 结合 dB 差值、DEM 坡度、连通域和面积阈值过滤
  -> classified.tif
```

建议分类值继续沿用现有约定，降低前端和套合分析改动量：

```text
0 = nodata
1 = stable_water
2 = flood
3 = high_scatter_or_uncertain
4 = non_water
```

套合分析：

```text
classified.tif
  -> class=2 转矢量
  -> 灾害点命中
  -> 近洪涝风险点
  -> DInSAR 产品 footprint 相交
  -> AOI / 行政区面积统计
```

## 5. GAMMA 适配性判断

| 能力项 | 当前情况 | 适配判断 |
| --- | --- | --- |
| WSL/GAMMA 环境 | 已有 `gamma_env.sh`，能注入 GAMMA 与 PyINT 路径 | 可复用 |
| LT-1 导入 | 已有 `down2slc_LT1_*` 与精轨桥接，历史上处理过几何问题 | 可作为第一批验证对象，但需专项 QA |
| Sentinel-1 导入 | PyINT 脚本支持 ZIP、EOF、burst、TOPS 相关流程，项目已有 `s1_gamma_dinsar` runner | 可做第二批，但 runtime operation 白名单需补齐 |
| GF3 | 当前项目已有 Python/GDAL L1A->L2，不依赖 GAMMA | 不建议强行走 GAMMA，先走 `gf3_gdal` 处理器 |
| 地理编码 | 已有 `geocode_gamma.py`、`geocode_back`、DEM 生成链 | 可复用思想和部分脚本 |
| GeoTIFF 导出 | 已有 `data2geotiff`、HyP3/标准产品导出经验 | 可复用 |
| 单景分析级输出 | 当前没有独立服务，D-InSAR 中只顺带产出 master amp | 需要新建 |
| 辐射定标语义 | Sentinel-1 导入使用 calibration XML；LT-1/GF3 各有差异 | 必须在 metadata 中明确单位，不能含糊写 dB |
| 生产稳定性 | 当前 D-InSAR runner 已有日志、repair、quality、manifest | 可复用框架，但不能复用 pair 假设 |

结论：GAMMA 适合做洪涝模块的优先前处理引擎，但第一阶段目标应是：

```text
LT-1 单景 -> GAMMA -> analysis_ready.tif
```

跑通并验证后再扩展：

```text
Sentinel-1 ZIP -> GAMMA -> analysis_ready.tif
GF3 L1A -> gf3_gdal -> analysis_ready.tif
```

## 6. 不应采用的方案

### 6.1 不应继续以 ENVI/SARscape 作为洪涝主线

原因：

```text
1. 数据格式和任务对象绑定 SARscape。
2. 洪涝分类是黑盒，难以解释和调参。
3. 服务器部署依赖重。
4. 后续多源数据扩展会反复补导入适配器。
5. 输出不天然适合平台统一发布。
```

### 6.2 不应把洪涝算法绑定到 GAMMA

GAMMA 应只负责把原始 SAR 处理成标准 GeoTIFF。洪涝算法不能依赖 GAMMA 中间二进制，也不能要求输入来自 GAMMA。否则只是把“绑定 ENVI”换成“绑定 GAMMA”。

正确边界是：

```text
GAMMA / GF3_GDAL / SNAP / ISCE / external
  -> analysis_ready.tif
  -> 同一套 Python 洪涝算法
```

### 6.3 不应直接复用 D-InSAR pair profile 做单景预处理

现有 `lt1_gamma_dinsar` / `s1_gamma_dinsar` 是 pair 生产 profile，包含：

```text
master/slave
coreg
diff
unwrap
geocode pair products
disp/coh export
```

洪涝单景预处理只需要：

```text
single scene import
radiometric calibration
multilook/filter
terrain correction/geocoding
GeoTIFF export
QA/register
```

两者不能混成一个 profile。

## 7. 建议实施阶段

### Phase 1：建立 GeoTIFF 数据契约

```text
1. 新建或扩展 Scene 表，明确 analysis_tif_path。
2. 保留旧 SARSceneGeoORM.geo_path 兼容字段。
3. /flood/scenes 改为优先返回 analysis_tif_path。
4. 前端把“可处理”判断改为是否存在 analysis_ready scene。
```

### Phase 2：先把洪涝检测改成 Python GeoTIFF 算法

```text
1. 新建 flood_detection_service.py。
2. 输入 pre_scene.analysis_tif_path 和 post_scene.analysis_tif_path。
3. 对齐网格。
4. 复用水体提取算法生成 pre/post water mask。
5. 输出 classified.tif。
6. 统计 flood_area_km2 和 stable_water_area_km2。
```

这一步可以先用 ENVI 现有 geocode 结果或 GF3 L2 GeoTIFF 做输入测试，目的是先解除洪涝分类对 SARscape 的依赖。

### Phase 3：新增 GAMMA LT-1 单景前处理器

建议新增：

```text
backend/app/services/sar_scene_preprocess_service.py
backend/app/services/gamma_scene_preprocess_service.py
backend/app/pyint_pipeline/run_gamma_scene_preprocess.py
```

最小 runner：

```text
1. stage LT-1 输入资产。
2. 调用 LT-1 导入脚本，产出 SLC。
3. multi_look 生成 MLI/AMP。
4. generate_rdc_dem 或等价 DEM 几何。
5. geocode_back 把 AMP 映射到地理网格。
6. data2geotiff 输出 analysis_ready.tif。
7. 可选转 COG。
8. rasterio 质量检查。
```

### Phase 4：接 Sentinel-1 和 GF3

Sentinel-1：

```text
1. 复用 Sentinel-1 ZIP/SAFE/EOF 管理设计。
2. 新增 s1_gamma_scene profile。
3. 不走 diff/unwrap，只产出单景 analysis_ready.tif。
4. 修正 WSL runtime operation 白名单。
```

GF3：

```text
1. 保留 gf3_service.py 的 GDAL/RPC 路线。
2. 输出注册为 analysis_ready scene。
3. 不强行改成 GAMMA。
```

### Phase 5：退役 ENVI 洪涝主线

```text
1. `/flood/detections` 默认使用 GeoTIFF 算法。
2. ENVI/SARscape 作为 `envi_legacy` 处理器保留一段时间。
3. 新任务不再默认创建 SARscape 洪涝分类。
4. 文档、界面和部署说明移除“ENVI 是洪涝必须项”的表述。
```

## 8. 验收标准

### 8.1 Scene 前处理

```text
1. 任意一个完成的 analysis_ready.tif 可以被 rasterio 打开。
2. CRS、bounds、transform、nodata 完整。
3. 有效像元比例达到阈值。
4. dB 值域在合理范围内。
5. metadata.json 能追溯输入资产、处理器、DEM、命令和版本。
6. preview 和 coverage 能在地图上显示和清除。
```

### 8.2 洪涝检测

```text
1. pre/post 两个 GeoTIFF 可自动对齐到同一网格。
2. classified.tif 只依赖 Python/rasterio/numpy/scipy/skimage，不调用 ENVI/SARscape。
3. class=1/2 面积统计稳定。
4. 套合分析可直接读取 classified.tif。
5. 同一算法能跑 GAMMA TIF、GF3 L2 TIF 和其他外部 TIF。
```

### 8.3 GAMMA 适配

```text
1. LT-1 单景 GAMMA 前处理能稳定产出 analysis_ready.tif。
2. 失败时有明确阶段日志，不出现“任务成功但全 0”的假成功。
3. data2geotiff 输出通过 GDAL/rasterio 质量检查。
4. 处理器输出不要求后续算法知道 GAMMA 中间目录结构。
```

## 9. 对部署的影响

Git 可以提供平台代码、Python 算法、任务编排、数据库迁移和前端能力，但不能内置 GAMMA 商业软件本体。另一台服务器部署时必须额外满足：

```text
1. WSL 或 Linux 环境可用。
2. GAMMA 安装路径可被 gamma_env.sh 发现，或配置 PYINT_GAMMA_HOME/GAMMA_HOME。
3. PyINT 代码随仓库存在。
4. WSL Python/conda 环境具备 rasterio、GDAL、numpy、scipy、skimage 等依赖。
5. DEM、轨道池和源数据池路径在 .env 中配置。
```

如果目标服务器没有 GAMMA，系统仍应允许：

```text
1. 使用已经存在的 analysis_ready.tif。
2. 使用 GF3_GDAL 处理器。
3. 后续接 SNAP/ISCE/external 处理器。
4. 继续执行水体提取、洪涝检测、套合分析和产品生成。
```

这也是把算法下放到 GeoTIFF 层的核心价值。
## 2026-05-16 implementation note

The first code pass now implements LT1 and GF3 as analysis-ready scene preprocessors:

- New config roots: `SAR_ANALYSIS_READY_ROOT`, `SAR_ANALYSIS_WORK_ROOT`, `SAR_ANALYSIS_PREVIEW_ROOT`.
- `sar_scene_geo` now stores `analysis_tif_path`, `analysis_dir`, preview path, engine/profile, backscatter unit, nodata, metadata and quality JSON.
- `/flood/preprocess` routes GF3 scenes to `gf3_gdal` standardization and LT1 scenes to `lt_gamma` single-scene preprocessing.
- GF3 uses the existing GDAL/RPC L1A->L2 result, then registers the selected L2 GeoTIFF under `SAR_ANALYSIS_READY_ROOT`.
- LT1 uses a new Gamma/PyINT runner that stops at single-scene geocoded MLI GeoTIFF and does not write into D-InSAR product directories.
- Water extraction, flood detection and scene previews now prefer `analysis_tif_path` and fall back to legacy `geo_path`.
- Startup database maintenance now treats Alembic `0004` as the managed head, can auto-add missing ORM columns/indexes, and records the `alembic_version` marker after the schema is healthy.
- `/health?full=true&refresh=true` now includes `sar_analysis_ready` root and scene-path checks.

This keeps D-InSAR product publication isolated from flood/water analysis inputs.
