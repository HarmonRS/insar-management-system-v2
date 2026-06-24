# LandSAR SBAS-InSAR 接入设计

记录时间：2026-06-04

## 1. 结论

LandSAR 支持 SBAS-InSAR。其 SBAS 是一体化流程，算法编号为 `280039`，参数文件入口是：

```text
SBASProcess
ID 280039
```

这条链路和当前系统里的 Gamma SBAS 不同。Gamma SBAS 是分阶段工作流，包含栈发现、baseline audit、共参考配准、RDC DEM、干涉图、IPTA 反演和发布阶段；LandSAR SBAS 更接近 LandSAR D-InSAR 的模式：准备好 `Task_*/Input_Data` 后，通过 `InSAR_Console.exe + 280039.txt` 一次执行。

因此接入建议是：在 SBAS-InSAR 页面新增 `LandSAR SBAS` 处理器分支，而不是把 LandSAR 硬塞进现有 Gamma 分阶段按钮。

## 2. LandSAR SBAS 输入输出

### 输入目录

LandSAR SBAS 扫描 `Task_*` 目录，每个任务目录要求：

```text
Task_xxx
|-- Input_Data
|   |-- LT1*_SLC.xml
|   |-- LT1*_SLC.tif
|   `-- ...
`-- Output_Data
```

核心条件：

- `Input_Data` 下至少 3 景已导入的 LT-1 SLC。
- 每景需要 `LT1*_SLC.xml` 与 `LT1*_SLC.tif/.tiff` 配对。
- DEM 需要外部指定。
- 精轨最好已经写入 XML 或已通过 LandSAR 精轨导入流程处理。

当前 LandSAR 项目文档说明，TimeSeriesBuilder 输出的 `Task_TS_*/Input_Data` 可直接被 PS/SBAS 页面扫描。

### 输出目录

LandSAR SBAS 输出在每个任务的 `Output_Data`：

```text
Output_Data
|-- *.los.tif
|-- *.raster.tif
|-- vector/
|-- 280039.log
`-- 280039_console.log
```

成功判定可参考 LandSAR GUI：

- 日志包含 `SBAS` 和 `success`
- 或日志包含 `console success`
- 控制台返回码为 0
- 同时存在核心输出，如 `*.los.tif` 或 `*.raster.tif`

## 3. 与现有系统的差异

### 现有 Gamma SBAS

当前 `/api/sbas-insar-production` 是 Gamma / IPTA SBAS 主线：

- 从资产池发现 LT-1 或 Sentinel-1 候选栈。
- 创建 `run_manifest.json`。
- 分阶段执行 Gamma 脚本。
- catalog 期望的核心产品是：
  - `publish/geotiff/los_rate_toward_m_per_year.tif`
  - `publish/geotiff/los_sigma_m_per_year.tif`
  - 相关预览图、质量图和监测点曲线。

### LandSAR SBAS

LandSAR SBAS 的输入不是系统当前的资产栈 manifest，而是 LandSAR 风格的 `Task_*/Input_Data`。

LandSAR SBAS 的输出语义也和 Gamma 不完全一致。`*.los.tif` 在文档里描述为 LOS 时序形变场，不应在没有验证前直接标成 Gamma 那种 `los_rate_toward_m_per_year` 速率产品。

因此 LandSAR SBAS 需要独立 processor 标识：

```text
processor_code = landsar_sbas
profile_code   = lt1_landsar_sbas
engine_code    = landsar
proid          = 280039
```

## 4. 推荐接入路线

### 阶段 1：MVP，只接现成 Task/Input_Data

目标：先让系统能扫描、提交、监控和归档 LandSAR SBAS，不负责从原始 LT-1 自动构建时序 Input_Data。

新增配置：

```text
LANDSAR_SBAS_ENABLED=true
LANDSAR_SBAS_WORK_ROOT=D:\LandSAR_Work\sbas
LANDSAR_SBAS_DEM_PATH=D:\DEM\HeiLongJiang10M_DEM.tif
LANDSAR_SBAS_TIMEOUT_SECONDS=172800
LANDSAR_SBAS_MIN_SCENES=3
LANDSAR_SBAS_SOURCE_ROOTS=D:\Task_Pool\SBAS
```

后端新增：

```text
backend/app/services/landsar_sbas_service.py
```

职责：

- 检查 LandSAR runtime、授权服务、`InSAR_Console.exe`、SBAS 相关 DLL。
- 扫描 root 下的 `Task_*` 或单个 `Task_*`。
- 校验 `Input_Data` 内 SLC XML/TIF 数量。
- 生成 `280039.txt` 参数文件。
- 调用：

```text
D:\LandSAR\InSAR_Console.exe D:\LandSAR_Work\sbas\<run_id>\native\<task>\Output_Data\280039.txt
```

- 把日志和核心 GeoTIFF 复制到系统标准结果目录。
- 写入 `run_manifest.json`、`workflow_summary.json`、`product_summary.json`。

建议标准结果目录：

```text
D:\production_results\timeseries\sbas\<run_id>
|-- run_manifest.json
|-- workflow_summary.json
|-- product_summary.json
|-- native_logs
|   |-- 280039.txt
|   |-- 280039.log
|   `-- 280039_console.log
`-- publish
    `-- landsar
        |-- los_timeseries.tif
        |-- post_raster.tif
        `-- vector/
```

任务队列新增：

```text
JOB_TYPE_SBAS_LANDSAR_WORKFLOW = "SBAS_LANDSAR_WORKFLOW"
```

前端新增：

- 在 `SBAS-InSAR 生产` 页面增加处理器选择：
  - `Gamma / IPTA SBAS`
  - `LandSAR SBAS`
- 选择 `LandSAR SBAS` 后显示：
  - Task 根目录
  - DEM 路径
  - 最少景数
  - 干涉对策略：`single` / `prim`
  - 垂直基线阈值
  - 时间基线阈值
  - 方位向/距离向多视
  - 输出 LOS 时序
  - 输出编码后栅格
- 隐藏 Gamma 的 baseline/coreg/RDC DEM/IPTA 分阶段按钮。

### 阶段 2：接入 catalog 和结果页

当前 `sbas_insar_catalog_service.py` 主要按 Gamma 产品定义扫描。LandSAR 接入后有两种选择：

1. 复用 `sbas_insar` catalog，但让资产定义按 `processor_code` 分支。
2. 新建 `landsar_sbas` catalog。

建议选择 1。理由是前端结果页还是 SBAS-InSAR 结果，只是处理器不同。

需要调整：

- `_READY_STATUSES` 增加 LandSAR 完成状态。
- `_CORE_ASSETS` 改成按 processor 分支。
- LandSAR 主资产角色：

```text
primary_geotiff      -> publish/landsar/los_timeseries.tif
secondary_geotiff    -> publish/landsar/post_raster.tif
run_manifest         -> run_manifest.json
workflow_summary     -> workflow_summary.json
native_console_log   -> native_logs/280039_console.log
```

注意：不要把 LandSAR `*.los.tif` 直接命名为 `los_rate_toward_m_per_year.tif`，除非算法工程师确认该文件确实是年速率图。

### 阶段 3：从资产池自动构建 LandSAR 时序 Input_Data

阶段 1 只接现成 `Task_*/Input_Data`。如果需要从系统资产池直接生产 LandSAR SBAS，需要新增前处理：

1. 从 LT-1 资产池选择同轨同极化多景。
2. 调用 LandSAR LT-1 数据导入 `100016`，构建多景 `Input_Data`。
3. 调用 LandSAR 精轨导入 `100206`，或确认精轨已进入 XML。
4. 输出 `Task_TS_*/Input_Data`。
5. 再调用 SBAS `280039`。

这一阶段风险比 MVP 高，建议在 LandSAR SBAS 一体化流程跑通后再做。

## 5. 参数建议

MVP 默认参数应与 LandSAR GUI 保持一致：

```text
dem_data_type=1          # 文件。注意 SBAS 模板中 0 是目录，1 是文件
dem_format=4             # COPERNICUS，需结合实际 DEM 测试
intf_method=0            # single
perp_baseline=200
time_baseline=300
doppler_baseline=100
az_looks=3
rg_looks=3
da_threshold=0.25
intensity_threshold=0.0
calibration_threshold=0.4
fine_reg_window=128
resample_factor=2
network_type=0           # Delaunay
max_arc_distance=1000
solve_method=0           # Periodogram
max_temporal_coh=0.7
ref_point_index=0
spatial_filter_dist=1000
unwrap_ref_index=0
time_filter_threshold=0.3
do_los_output=1
gen_vector_map=0
gen_pre_raster=0
gen_post_raster=1
```

需要特别注意 DEM 参数。DInSAR 用 `HeiLongJiang10M_DEM.tif` 已跑通，但 SBAS 模板里的 DEM 类型字段和 DInSAR 不同：

- SBAS：`dem_data_type=0` 表示目录，`1` 表示文件。
- 当前 DEM 是 GeoTIFF 文件，所以应传 `1`。

## 6. 风险点

1. 输入结构风险

   现有 Gamma SBAS 的候选栈不是 LandSAR 的 `Task_*/Input_Data`。MVP 必须明确只支持 LandSAR 已导入后的时序任务目录。

2. 输出语义风险

   LandSAR `*.los.tif` 是否为单幅速率图、累计形变图，还是多波段时序图，需要用一次真实输出确认。没有确认前不能按 Gamma 年速率产品入库。

3. DLL 风险

   SBAS/PS 可能需要 DInSAR 之外的 DLL，例如：

   - `SAR_InSAR_MTInSARModel.dll`
   - `SAR_InSAR_PSInSAR_CSU.dll`
   - `SAR_InSAR_MBCP_MTInSARModel.dll`

   LandSAR SBAS availability check 应单独检查这些 DLL。

4. 运行时间风险

   SBAS 是多景时序处理，默认 timeout 应明显大于 DInSAR，建议先设为 48 小时。

5. catalog 风险

   当前 SBAS catalog 以 Gamma 结果为主。LandSAR 接入时要做 processor-specific asset mapping，否则会出现“运行成功但结果页查不到”。

## 7. 建议下一步

先实现阶段 1：

1. 新增 `landsar_sbas_service.py`，只支持扫描现成 `Task_*/Input_Data`。
2. 新增 `SBAS_LANDSAR_WORKFLOW` 后台任务。
3. 前端 SBAS 页面增加 `LandSAR SBAS` 分支。
4. 先不改资产池自动构建，不接 Sentinel-1，不开放 GACOS。
5. 用一个 3 景以上的 `Task_TS_*` 做首轮真实测试。
6. 根据真实输出再接 catalog 和结果页。

这个路线保留 Gamma SBAS 现状，同时利用已经验证的 LandSAR runtime 和授权链路，风险最低。
