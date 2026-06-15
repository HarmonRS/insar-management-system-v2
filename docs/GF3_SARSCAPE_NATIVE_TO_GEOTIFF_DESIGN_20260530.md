# GF3 SARscape Native To GeoTIFF Design

更新日期：2026-05-30

## 1. 结论

GF3 生产链路后续采用“生产解耦、系统标准化”的模型：

```text
GF3 原始压缩包池
  -> 生产服务器使用 ENVI / IDL Runtime / SARscape 稳定生产
  -> 只保留 SARscape 最终 _geo 原生结果组
  -> 系统扫描原生结果池
  -> 后台转换为标准 GeoTIFF
  -> 入库、预览、洪涝分析和后续业务只消费 GeoTIFF
```

系统不直接把 SARscape `.sml` 或无后缀二进制作为业务算法输入。`.sml`、`.hdr` 和无后缀主数据属于原生证据层；`GeoTIFF` 属于平台消费层。

## 2. 目录约定

推荐把输入、生产结果和运行时目录分开，避免把系统工作文件混入业务结果池。

```env
GF3_ARCHIVE_SOURCE_DIRS=D:\production_inputs\gf3\archives
GF3_LEGACY_GDAL_ENABLED=false
GF3_SOURCE_DIRS=
GF3_SARSCAPE_NATIVE_DIRS=D:\production_results\gf3\sarscape_native
GF3_STORAGE_DIRS=D:\production_results\gf3\standard_l2
GF3_SARSCAPE_RUNTIME_DIR=D:\production_runtime\gf3\sarscape_runtime
SAR_ANALYSIS_READY_ROOT=D:\production_results\sar_analysis_ready
```

目录职责：

| 目录 | 职责 | 系统是否直接分析 |
| --- | --- | --- |
| `GF3_ARCHIVE_SOURCE_DIRS` | 原始 GF3 L1A `.tar.gz` 池 | 否 |
| `GF3_SOURCE_DIRS` | legacy Python/GDAL L1A 解包输入，默认关闭 | 否 |
| `GF3_SARSCAPE_NATIVE_DIRS` | SARscape `_geo` 原生结果池 | 否 |
| `GF3_STORAGE_DIRS` | GF3 标准 GeoTIFF 池 | 是 |
| `GF3_SARSCAPE_RUNTIME_DIR` | wrapper 配置、IDL 运行时临时文件 | 否 |
| `SAR_ANALYSIS_READY_ROOT` | 洪涝/水体分析级统一输入 | 是 |

生产服务器可以不部署完整管理系统。只要把完成后的 `_geo` 原生结果组放入 `GF3_SARSCAPE_NATIVE_DIRS`，管理系统就可以扫描、转换和入库。

## 3. 原生池结构

原生池以批次日期或人工批次号分组。单景目录名尽量保持 GF3 原始产品名。

```text
D:\production_results\gf3\sarscape_native
  20260514
    GF3_MH1_FSII_051377_E132.3_N48.2_20260514_L1A_HHHV_L10007356478
      GF3_MH1_FSII_..._hh_geo
      GF3_MH1_FSII_..._hh_geo.hdr
      GF3_MH1_FSII_..._hh_geo.sml
      GF3_MH1_FSII_..._hh_geo.ovr
      GF3_MH1_FSII_..._hh_geo.aux.xml
      GF3_MH1_FSII_..._hh_geo_ql.tif
      GF3_MH1_FSII_..._hh_geo.kml
      GF3_MH1_FSII_..._hv_geo
      GF3_MH1_FSII_..._hv_geo.hdr
      GF3_MH1_FSII_..._hv_geo.sml
      GF3_MH1_FSII_..._hv_geo.ovr
      GF3_MH1_FSII_..._hv_geo.aux.xml
      GF3_MH1_FSII_..._hv_geo_ql.tif
      GF3_MH1_FSII_..._hv_geo.kml
      gf3_sarscape_cli.log
```

### 3.1 必保文件

每个极化至少保留：

```text
*_geo
*_geo.hdr
*_geo.sml
```

建议同时保留：

```text
*_geo.ovr
*_geo.aux.xml
*_geo_ql.tif
*_geo.kml
gf3_sarscape_cli.log
```

说明：

- 无后缀 `*_geo` 是 SARscape 主数据。
- `.hdr` 是 ENVI/GDAL 读取二进制数据的关键 sidecar。
- `.sml` 是 SARscape 追溯和完成判定的关键 sidecar。
- `*_geo_ql.tif` 只能作为快视或预览参考，不作为科学分析主输入。

### 3.2 可清理文件

生产结束并确认 `_geo` 结果完整后，可以清理：

```text
.gf3_extract
temp
SLC 中间产物
*_ml*
*_filt*
```

如果需要完整复现 SARscape 处理过程，应额外保留 `work` 中的参数 XML、trace 和日志；否则可将 `work` 作为可选审计资料归档。

## 4. 标准 GeoTIFF 池结构

系统从原生池转换后写入 `GF3_STORAGE_DIRS`。

```text
D:\production_results\gf3\standard_l2
  20260514
    GF3_MH1_FSII_051377_E132.3_N48.2_20260514_L1A_HHHV_L10007356478
      HH_L2.tif
      HV_L2.tif
      preview_HH.png
      preview_HV.png
      gf3_standard_manifest.json
      quality_HH.json
      quality_HV.json
```

平台后续只从该目录或 `SAR_ANALYSIS_READY_ROOT` 消费 GeoTIFF，不直接读取 SARscape 原生目录。

## 5. 扫描与转换流程

推荐把“扫描”和“转换”都放在后台任务中执行，避免普通扫描接口长时间阻塞。

```text
用户触发 GF3 扫描
  -> 扫描 GF3_SARSCAPE_NATIVE_DIRS
  -> 识别 scene / polarization / _geo 完整性
  -> 对待转换项创建或执行 GF3_NATIVE_TO_TIF 任务
  -> 转换到 GF3_STORAGE_DIRS
  -> 写 gf3_standard_manifest.json
  -> 登记 radar_data / sar_scene_geo
  -> 生成预览图和 quality.json
```

### 5.1 完整性判定

一个极化的原生 `_geo` 结果完整条件：

```text
*_geo      存在且非空
*_geo.hdr  存在且非空
*_geo.sml  存在且非空
```

如果存在 `.aux.xml`、`.ovr`、`*_geo_ql.tif`、`.kml`，登记为辅助资产。

一个 scene 的状态：

| 状态 | 条件 |
| --- | --- |
| `DONE` | 请求极化全部具备完整 `_geo` 结果，且 GeoTIFF 转换成功 |
| `NATIVE_READY` | 原生 `_geo` 完整，但 GeoTIFF 尚未转换 |
| `PARTIAL` | 只完成部分极化 |
| `FAILED` | 原生结果不完整或转换失败 |

### 5.2 增量跳过规则

转换任务应根据 manifest 判断是否需要重跑：

```text
source path
source size
source mtime
converter version
target tif exists
```

当上述信息未变化时，跳过转换。

如果 `_geo` 原生文件被替换、修改时间变化、转换器版本变化或目标 tif 缺失，应重新转换。

## 6. 转换策略

优先使用 GDAL/rasterio 读取 ENVI header 或 SARscape sidecar。

输入优先级：

```text
1. *_geo + *_geo.hdr
2. 可被 GDAL 识别的 *_geo.sml
3. 其他明确可读的 SARscape/ENVI sidecar
```

输出要求：

```text
GeoTIFF
单极化单文件
尽量保留地理参考、nodata、数据类型和投影
默认输出 HH_L2.tif / HV_L2.tif
```

如果转换失败，不应把 quicklook tif 冒充为分析级 tif。应记录：

```text
analysis_ready_status=FAILED
error_message=<转换错误>
source_native_status=NATIVE_READY
```

## 7. 数据库登记

### 7.1 `radar_data`

每个 GF3 scene 至少登记一条 `radar_data`：

```text
satellite=GF3
satellite_family=GF3
source_format=GF3_SARSCAPE_NATIVE
product_level=L2
file_path=<GF3_STORAGE_DIRS 下的 scene 标准目录>
metadata_json.native_dir=<GF3_SARSCAPE_NATIVE_DIRS 下的 scene 原生目录>
metadata_json.standard_manifest=<gf3_standard_manifest.json>
```

应从 scene 名解析：

```text
imaging_date
imaging_mode
polarization
scene_center_lon
scene_center_lat
product_unique_id
```

如果 GeoTIFF 可读，应同步：

```text
min_lon / min_lat / max_lon / max_lat
coverage_polygon
geom
```

### 7.2 `sar_scene_geo`

每个可分析 scene 记录：

```text
analysis_engine=gf3_sarscape
analysis_profile=gf3_sarscape_geo_to_tif
analysis_tif_path=<HH_L2.tif 或默认极化 tif>
analysis_dir=<SAR_ANALYSIS_READY_ROOT 下目录>
analysis_preview_path=<preview.png>
analysis_backscatter_unit=sigma0_linear 或 unknown
analysis_metadata_json.native_dir=<原生目录>
analysis_metadata_json.native_assets=<原生资产列表>
analysis_quality_json=<quality.json 内容>
status=DONE
```

如果需要同时保留 HH 和 HV 两个可分析产品，建议长期扩展为资产表或 scene-pol 级记录；短期可以选择默认极化写入 `sar_scene_geo.analysis_tif_path`，并在 metadata 中登记全部极化 tif。

## 8. Manifest 契约

### 8.1 原生 manifest

`gf3_native_manifest.json` 写入原生 scene 目录或系统索引目录：

```json
{
  "schema": "gf3_sarscape_native.v1",
  "scene_name": "GF3_MH1_FSII_...",
  "native_dir": "D:\\production_results\\gf3\\sarscape_native\\20260514\\GF3_MH1_FSII_...",
  "source_archive": "D:\\production_inputs\\gf3\\archives\\20260514\\GF3_MH1_FSII_....tar.gz",
  "polarizations": ["HH", "HV"],
  "status": "NATIVE_READY",
  "assets": [
    {
      "polarization": "HH",
      "role": "geo_native",
      "path": "..._hh_geo",
      "hdr": "..._hh_geo.hdr",
      "sml": "..._hh_geo.sml",
      "quicklook": "..._hh_geo_ql.tif"
    }
  ],
  "logs": ["gf3_sarscape_cli.log"]
}
```

### 8.2 标准 manifest

`gf3_standard_manifest.json` 写入标准 GeoTIFF scene 目录：

```json
{
  "schema": "gf3_standard_geotiff.v1",
  "scene_name": "GF3_MH1_FSII_...",
  "native_manifest": "D:\\production_results\\gf3\\sarscape_native\\...\\gf3_native_manifest.json",
  "standard_dir": "D:\\production_results\\gf3\\standard_l2\\20260514\\GF3_MH1_FSII_...",
  "status": "DONE",
  "converter": {
    "name": "gf3_sarscape_geo_to_tif",
    "version": "v1"
  },
  "assets": [
    {
      "polarization": "HH",
      "role": "analysis_tif",
      "path": "HH_L2.tif",
      "source_native": "..._hh_geo",
      "quality": "quality_HH.json",
      "preview": "preview_HH.png"
    }
  ]
}
```

## 9. 前端与操作入口

短期不新增复杂页面，沿用“数据管理 / 归档预处理”里的 GF3 操作区：

```text
GF3 解包
GF3 SARscape 原生扫描
GF3 原生转 GeoTIFF
扫描 GF3 标准结果
```

也可以先合并为一个按钮：

```text
扫描 GF3
```

后台自动完成：

```text
native scan -> convert missing tif -> register standard result
```

任务日志必须显示：

```text
发现 scene 数
NATIVE_READY 数
转换成功数
转换失败数
跳过数
失败原因
```

## 10. 实施顺序

### Phase 1：设计与配置

- 新增本文档。
- 新增 `.env.example` 中的 `GF3_SARSCAPE_NATIVE_DIRS`。
- 保留 `GF3_STORAGE_DIRS` 作为标准 GeoTIFF 池。

### Phase 2：原生扫描

- 新增 `gf3_native_inventory_service.py`。
- 扫描 `_geo` 原生结果组。
- 生成 native manifest。
- 不做转换、不入业务分析。

### Phase 3：GeoTIFF 标准化

- 新增 `gf3_standardize_service.py`。
- 将 `_geo` 原生结果转换为 `HH_L2.tif` / `HV_L2.tif`。
- 生成 preview 和 quality。
- 写 standard manifest。

### Phase 4：入库与洪涝接入

- 登记 `radar_data`。
- 登记或更新 `sar_scene_geo`。
- `/flood/preprocess` 对 GF3 优先复用已标准化 GeoTIFF。

### Phase 5：清理策略

- 增加 native pool 检查报告。
- 增加可选中间文件清理建议，但系统不主动删除生产机文件。
- 后续如需自动清理，应只清理系统明确生成的临时文件。

## 11. 当前约束

- 不把 `*_geo_ql.tif` 当作分析级产品。
- 不让洪涝、水体、地图预览直接依赖 `.sml`。
- 不要求生产服务器部署管理系统。
- 不在扫描请求同步执行长时间转换，应使用后台任务。
- 不删除用户生产目录中的文件，除非后续新增明确的、受控的清理任务。

## 12. 与旧 GF3 GDAL 路线关系

现有 `gf3_service.py` 的 Python/GDAL L1A -> L2 路线可以保留为 fallback 或实验处理器：

```text
gf3_gdal
```

新 SARscape 原生池路线作为正式现场路线：

```text
gf3_sarscape
```

两条路线最终都必须收敛到：

```text
GF3_STORAGE_DIRS / SAR_ANALYSIS_READY_ROOT 中的标准 GeoTIFF
```

因此后续业务模块只关心标准 GeoTIFF，不关心上游来自 SARscape、GDAL、GAMMA 或其他处理器。

## 13. 2026-05-30 首轮落地

首轮代码已按本文档的主路径实现最小闭环：

- 新增 `GF3_SARSCAPE_NATIVE_DIRS` 配置，作为 SARscape/ENVI 原生 `_geo` 二进制池。
- 新增 `gf3_native_inventory_service.py`，扫描 `*_geo + *_geo.hdr + *_geo.sml` 并写 `gf3_native_manifest.json`。
- 新增 `gf3_standardize_service.py`，将完整原生结果转换到 `GF3_STORAGE_DIRS` 下的 `HH_L2.tif` / `HV_L2.tif`，并写 `gf3_standard_manifest.json`、`quality_*.json`、`preview_*.png`。
- 新增后台任务 `GF3_SARSCAPE_SYNC` 和接口 `POST /api/monitor/gf3-sarscape-sync`。
- 数据监控面板新增 `GF3 SARscape 入库` 按钮。
- 转换成功后登记 `radar_data`，并通过 `sar_analysis_ready_service` 登记 `sar_scene_geo`，使洪涝/水体模块可以继续消费标准 GeoTIFF。

当前实现仍遵守约束：

- 不把 `*_geo_ql.tif` 当作分析级输入。
- 不要求生产服务器部署管理系统。
- 转换优先使用 GDAL Python 绑定；当前环境没有 `osgeo` 时走 rasterio 兜底。

## 14. 2026-05-30 生产链路接入

在首轮“原生结果入库”基础上，系统进一步接入 GF3 SARscape wrapper：

```text
GF3_ARCHIVE_SOURCE_DIRS
  -> gf3wrapper.exe / IDL Runtime / SARscape
  -> GF3_SARSCAPE_NATIVE_DIRS
  -> GF3_SARSCAPE_SYNC 标准化
  -> GF3_STORAGE_DIRS
  -> 雷达数据扫描、预览、洪涝/水体业务
```

新增配置：

```env
GF3_SARSCAPE_WRAPPER_EXE=D:\Code\Insar_management_system_v2\third_party\GF3_L1A_To_L2_pipeline\dist\windows\gf3wrapper.exe
GF3_SARSCAPE_IDLRT_PATH=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlrt.exe
GF3_SARSCAPE_DEM_PATH=D:\DEM\GMTED2010.jp2
GF3_SARSCAPE_POLARIZATIONS=HH,HV
GF3_SARSCAPE_KEEP_EXTRACTED=true
GF3_SARSCAPE_AUTO_STANDARDIZE=true
GF3_SARSCAPE_CLEAN_AFTER_SUCCESS=true
GF3_SARSCAPE_PRODUCE_TIMEOUT_SECONDS=0
```

新增后台任务：

| 任务 | 接口 | 用途 |
| --- | --- | --- |
| `GF3_SARSCAPE_PRODUCE` | `POST /api/monitor/gf3-sarscape-produce` | 从原始 `.tar.gz/.tgz` 触发 SARscape 生产，随后自动标准化、入库、清理 |
| `GF3_SARSCAPE_SYNC` | `POST /api/monitor/gf3-sarscape-sync` | 仅扫描已有 `_geo` 原生结果并转 GeoTIFF 入库 |
| `GF3_SARSCAPE_CLEAN` | `POST /api/monitor/gf3-sarscape-clean` | 手动清理原生池中间数据 |

清理策略：

- 只处理 `GF3_SARSCAPE_NATIVE_DIRS` 内的场景目录。
- 默认要求 `GF3_STORAGE_DIRS/<batch>/<scene>/gf3_standard_manifest.json` 状态为 `DONE` 后才清理。
- 保留最终原生 `_geo` 主数据、`.hdr/.sml/.ovr/.aux.xml`、`*_geo_ql.tif/.kml`、日志和 manifest。
- 删除 `.gf3_extract`、`temp`、`work`，以及根目录中的 `*_slc*`、`*_ml*`、`*_filt*`、`.par/.trace/.working/.list` 等中间文件。
- 每景写 `gf3_cleanup_manifest.json`，记录删除条目和释放字节数。
- 不删除 `GF3_ARCHIVE_SOURCE_DIRS` 中的原始压缩包，也不删除 `GF3_STORAGE_DIRS` 中的标准 GeoTIFF。

这样 `D:\production_results\gf3\sarscape_native` 只长期保存可追溯的最终 `_geo` 原生结果组，中间过程文件在标准化完成后自动释放空间；wrapper 配置和临时运行文件放在 `D:\production_runtime\gf3\sarscape_runtime`。

## 2026-06-15 Production Preflight Rule

GF3 SARscape production must check existing results before invoking the external wrapper.

Skip production when either condition is true:

- SARscape native output is already complete in `GF3_SARSCAPE_NATIVE_DIRS` for every requested polarization.
- Standardized L2 output already exists in `GF3_STORAGE_DIRS/<imaging_date>/<scene_name>` with `gf3_standard_manifest.json` status `DONE`/`PARTIAL` and every requested polarization has a valid `*_L2.tif`.

This prevents reprocessing when native intermediates were cleaned but registered/standardized results still exist. The standardized L2 and registered assets are the durable result layer; source archives on UNC should not be reprocessed unless the operator explicitly removes or invalidates the existing result.
