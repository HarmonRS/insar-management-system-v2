# 三数据本机生产与结果管理约定

最后更新：2026-06-17

本文件是陆探一号、Sentinel-1、高分三在当前系统中的运行边界。2026-06-16 起，UNC 不再作为活动生产链路、源数据池、精轨池或 Task_Pool。网络共享只可作为人工搬运的外部介质，不进入后台生产任务。

## 1. 总原则

1. 源数据和精轨都放在本机路径，UNC 不进入活动生产链路。
2. LT-1 和 Sentinel-1 当前管理对象是本机压缩包源池，不管理旧解包目录。
3. 管理阶段只从压缩包中抽取 XML/manifest、元数据和预览图，用于资产索引、日期/轨道绑定和检索。
4. 生产任务需要真实文件树时，才按任务解包或复制到本机 `Task_Pool`，不从 UNC 拉取。
5. D-InSAR 和 SBAS-InSAR 的运行材料、工作目录、结果发布目录必须是本机路径。
6. 生产结果进入数据管理或结果 catalog，InSAR 形变分析只读取已登记结果，不直接扫描临时工作目录。
7. 洪水检测模块本轮冻结，不纳入本次审计和改造。

## 2. 三类数据边界

| 数据 | 源数据位置 | 精轨位置 | 生产模块 | 结果管理 |
| --- | --- | --- | --- | --- |
| 陆探一号 LT-1 | `D:\LuTan1_Image_Pool_Zip`，本机 LT-1 压缩包源池 | `D:\LT1_data_lsarorbit` | 生产管理保留占位；D-InSAR 走 LandSAR、ENVI+SARscape、Gamma/PyINT；SBAS 走 Gamma/PyINT 主线 | D-InSAR/SBAS 结果 catalog |
| Sentinel-1 | `D:\Sentinel1_Image_Pool_ZIP`，本机 Sentinel-1 ZIP/SAFE 压缩包源池 | `D:\Sentinel1_EOF_Pool` | 生产管理保留占位；D-InSAR 当前只走 Gamma/PyINT；SBAS 只做发现和规划，执行未启用 | D-InSAR 结果 catalog；SBAS 暂无执行产物 |
| 高分三 GF3 | 本机只管理已生产的 `_geo` 成品；原始归档仅追踪 | 无精轨链路 | 外部 SARscape 服务器生产 `_geo`；本机不启动 SARscape wrapper | 复制到 `D:\GaoFen3_Pool\native_geo` 后登记，WebP 从 `_geo` 主二进制生成 |

高分三外部结果命名约定：

```text
D:\GaoFen3_Pool\native_geo\20260609_geo\
  GF3_MDJ_FSI_051759_E130.2_N43.5_20260609_L1A_HHHV_L10007375467\
    GF3_MDJ_FSI_051759_E130.2_N43.5_20260609_L1A_HHHV_L10007375467_hh_geo
    GF3_MDJ_FSI_051759_E130.2_N43.5_20260609_L1A_HHHV_L10007375467_hh_geo.hdr
    GF3_MDJ_FSI_051759_E130.2_N43.5_20260609_L1A_HHHV_L10007375467_hh_geo.sml
```

`*_geo_ql.tif` 可作为辅助材料，但不作为正式 WebP 预览源。正式预览从 SARscape `_geo` ENVI 二进制读取生成。

### GF3 成品池扫描入库流程

1. 外部 SARscape 服务器完成生产后，把整景目录复制到本机 `D:\GaoFen3_Pool\native_geo\YYYYMMDD_geo\<GF3_SCENE>`。
2. 每个场景目录至少包含一个完整极化的 `*_geo`、`*_geo.hdr`、`*_geo.sml`；`*_geo_ql.tif` 可以同时提供，作为范围读取兜底和人工核验材料。
3. 在系统中点击 `登记 _geo 结果`，后端递归扫描所有 `*_geo` 场景目录，写入 `gf3_native_manifest.json` 和 `gf3_native_preview_manifest.json`，并登记 `source_product_assets` 与 `radar_data`。
4. 登记时优先从 `*_geo` ENVI 主数据读取 CRS、范围和中心点；如果主数据无法读取范围，再尝试对应的 `*_geo_ql.tif`。
5. 点击 `生成 WebP` 后，系统从已登记的 `*_geo` 主数据生成本机 WebP 缓存，缓存路径仍使用 `backend\image_cache\radar_raw` / `radar_geo` 体系。
6. `D:\GaoFen3_Pool\catalog` 只保存平台登记 manifest 和后续可选派生物；默认不复制完整影像、不把 `_geo` 转成全量 GeoTIFF。

## 3. 本机路径与按需解包

当前核心路径：

```text
UNPACK_SOURCE_DIRS=
SOURCE_PRODUCT_DIRS=D:\LuTan1_Image_Pool_Zip;D:\Sentinel1_Image_Pool_ZIP
SENTINEL1_STORAGE_DIRS=
INSAR_STORAGE_DIRS=
MONITOR_RADAR_DIRS=
ORBIT_SOURCE_DIRS=D:\LT1_data_lsarorbit;D:\Sentinel1_EOF_Pool
ORBIT_POOL_ENVI=D:\orbit_pools\envi
PYINT_ORBIT_POOL_TXT=D:\orbit_pools\envi
GAMMA_SBAS_ORBIT_ROOTS=D:\orbit_pools\envi
ISCE2_ENABLED=false
ORBIT_POOL_ISCE2=
TASK_POOL_ROOT=D:\Task_Pool
DINSAR_TASK_POOL_ROOT=D:\Task_Pool\DInSAR
SBAS_TASK_POOL_ROOT=D:\Task_Pool\SBAS
DATA_DISTRIBUTION_ROOT=D:\Task_Pool\Data_Distribution
GF3_TASK_POOL_ROOT=D:\GaoFen3_Pool\task_pool
GF3_ARCHIVE_SOURCE_DIRS=D:\GaoFen3_Pool\archives
GF3_SARSCAPE_NATIVE_DIRS=D:\GaoFen3_Pool\native_geo
GF3_STORAGE_DIRS=D:\GaoFen3_Pool\catalog
GF3_SARSCAPE_RUNTIME_DIR=D:\GaoFen3_Pool\task_pool\sarscape_runtime
```

压缩包资产索引只建立资产索引、预览缓存和轨道绑定，不提供“全量入库/全量解包”按钮。当前 LT-1/Sentinel-1 主流程从本机压缩包源池扫描，只抽取 XML/manifest、元数据和预览图；参与计算时才按任务解包或复制到 `Task_Pool` 或具体任务目录。

生产准备和数据分发是两个归口。前端不让用户输入服务器绝对路径，只填写任务名；后端按 `.env` 中固定根目录创建子目录，避免远程浏览器把用户本机路径传给服务器。`生产数据准备` 以批次配对为输入，从 LT-1/Sentinel-1 源压缩包按需 materialize 到 `DINSAR_TASK_POOL_ROOT\<任务名>`，在其下生成 `Task_YYYYMMDD_YYYYMMDD\master`、`slave`、`orbit` 和 `pair_metadata.json`，供 D-InSAR 引擎直接运行；这是生产工作区，不是源池。`数据分发` 只用于外发或跨目录搬运源压缩包，导出到 `DATA_DISTRIBUTION_ROOT\<任务名>`，目录结构为 `data/`、`orbit/`、`pairs.json`、`manifest.json`。其中 `data/` 只能保存 LT-1/Sentinel-1 源压缩包文件，不能保存旧解包目录。旧批次如果仍指向 `D:\LuTan1_Image_Pool` 或 `D:\Sentinel1_Image_Pool` 解包目录，应重新从压缩包资产重建批次后再执行生产准备或分发。

源池扫描采用增量解析语义：系统仍递归列出 `SOURCE_PRODUCT_DIRS` 下的候选压缩包文件名，用于发现新增和删除；数据库中已有且 `file_path`、`size_bytes`、`mtime_epoch`、`parser_version`、`parse_status` 均满足未变化条件的 `S1_ZIP` / `LT1_ARCHIVE` 资产会跳过包内 XML/manifest 读取，不再每次全量重读压缩包内容。新增、修改、曾经失效或解析器版本变化的压缩包会重新解析并更新资产索引。

LT-1 解析字段必须区分：规范文件名中的 `SLC/SSC` 是 `product_type` 和干涉源类型判断依据；XML 中 `imageDataInfo/imageDataType=COMPLEX` 只能写入 `image_data_type`，不能覆盖 `product_type`。前端“范围/可用性”依赖 `coverage_polygon + imaging_date + imaging_mode + polarization + complex token`，因此解析器变更必须同时验证 LT-1 `coverage_polygon` 和 `insar_source_ready` 计数。

LT-1/GF3 XML 的四个 `sceneCornerCoord` 不得直接按 XML 出现顺序连线。解析器必须把角点重排为非自交闭合四边形后再写入 `coverage_polygon` 和 PostGIS `geom`；验收时需检查 Shapely/PostGIS polygon valid，避免前端显示成沙漏形。

资产扫描入口支持三种语义：`families=["LT1"]` 只扫陆探源包/精轨，`families=["S1"]` 只扫哨兵源包/精轨，空 `families` 表示合扫。精密轨道扫描同样按 `inventory_types=["orbit_asset"] + families` 区分 LT-1、S1 或全部，前端不得再通过固定路径顺序猜测 root id。

精轨生产池按 [PRECISE_ORBIT_PRODUCTION_CONTRACT_20260617.md](PRECISE_ORBIT_PRODUCTION_CONTRACT_20260617.md) 执行：`ORBIT_SOURCE_DIRS` 是原生资产层；LT-1 扫描后同步到 `ORBIT_POOL_ENVI\LT1A|LT1B`，供 ENVI/SARscape、Gamma/PyINT D-InSAR 和 Gamma SBAS 使用；Sentinel-1 使用 EOF 原生资产绑定；ISCE2 精轨 XML 池为 legacy，默认不启用。

`UNPACK_SOURCE_DIRS` 为空是当前设计状态；通用全量解包入口不进入前端主流程。压缩包源池由 `SOURCE_PRODUCT_DIRS` 管理。

已执行的代码约束：

- `validate_runtime_config()` 对源数据、精轨、Task_Pool、D-InSAR/SBAS 工作根、结果根、GF3 `_geo` 根执行 UNC 校验。
- Sentinel-1 单个/批量解包和通用 materialize 拒绝 UNC 源路径与 UNC 目标路径。
- `/monitor/status` 返回按实际磁盘卷汇总的 `storage_roots`，例如多个 `D:\...` 路径只展示一个 `D:\` 容量项，同时报告配置路径数量和缺失路径数量。

## 4. 生产管理界面

生产管理工作台现在承担三类数据的生产边界展示：

- `陆探生产占位`：说明 LT-1 本机压缩包源池、精轨、按需解包、D-InSAR/SBAS 本机 Task_Pool。
- `哨兵生产占位`：说明 Sentinel-1 本机压缩包源池、EOF 精轨、按需解包、D-InSAR Gamma/PyINT、SBAS 规划态。
- `高分三结果登记`：说明外部 SARscape 生产、本机 `_geo` 登记和 WebP 生成。
- `D-InSAR 运行`、`D-InSAR 产物`、`SBAS-InSAR Production`、`SBAS-InSAR 结果` 保留现有生产和结果 catalog 功能。

这些占位不是最终生产向导，但先把三类数据放进同一生产管理域，避免继续把数据扫描、生产运行、结果登记混在数据监控按钮里。

## 5. InSAR 形变分析审计

当前边界：

- D-InSAR 结果由 D-InSAR product catalog 管理，分析页不应直接读取 Task_Pool 临时目录。
- SBAS 结果由 SBAS product catalog 管理，速率图、质量指标和监测点曲线从发布包读取。
- Sentinel-1 SBAS 目前只是规划态，不能在形变分析中伪装成可执行生产链路。
- GF3 `_geo` 登记到雷达资产和 WebP 缓存后，可作为数据管理资产；它不是 D-InSAR/SBAS 形变分析的生产输入。

后续如果要把分析页做成正式工作台，应先统一读取结果 catalog，再补地图叠加、剖面、时间序列和质量过滤，不应回退到扫描任意目录。

## 6. 本轮不改内容

- 洪水检测、GF3 水体检测和洪水 GeoTIFF 预处理暂不调整。
- D-InSAR 三引擎内部执行细节不在本轮重写，只强化本机路径边界。
- Sentinel-1 SBAS 不启用执行，只保留发现和规划。
