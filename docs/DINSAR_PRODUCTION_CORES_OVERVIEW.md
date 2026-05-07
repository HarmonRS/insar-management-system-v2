# 三条 D-InSAR 生产核心说明

本文按当前仓库实现，梳理三条 D-InSAR 生产核心的处理链路：
ENVI + SARscape、ISCE2、Gamma / PyINT。重点说明它们各自如何做干涉、解缠、轨道/残差修正、地理编码和导出。

相关代码入口：
- [backend/app/dinsar_engines/sarscape_engine.py](../backend/app/dinsar_engines/sarscape_engine.py)
- [backend/app/services/envi_service.py](../backend/app/services/envi_service.py)
- [backend/app/dinsar_engines/isce2_engine.py](../backend/app/dinsar_engines/isce2_engine.py)
- [backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py](../backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py)
- [backend/app/isce2_pipeline/export_isce_geotiff.py](../backend/app/isce2_pipeline/export_isce_geotiff.py)
- [backend/app/dinsar_engines/pyint_engine.py](../backend/app/dinsar_engines/pyint_engine.py)
- [backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py](../backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py)

## 总览

| 核心 | 处理方式 | 轨道精炼 / 重去平 | 输出特点 |
|---|---|---|---|
| ENVI + SARscape | 调用 SARscape 原生任务链 | 有，且 custom6 里是显式一步 | 产物由 SARscape 导出，地理编码像元大小可直接配置 |
| ISCE2 | WSL 下 managed stripmap 流程 | 有，但拆散在 rubbersheeting 和 export 阶段 | 先处理，再在导出阶段做参考归一化和 deramp |
| Gamma / PyINT | Gamma 原生命令链 + PyINT 封装 | 有，当前实现为 `reflatten` 分支 | 保留 Gamma 原生命令结果，Python 只做调度和导出组织 |

## 1. ENVI + SARscape

入口分两类：
- `metatask`：自动链路，走 `SARsMetataskInSARDisplacementGeneration`
- `custom6`：手工 6 步链路，逐步串接 SARscape 原生任务

当前代码里，`custom6` 最接近你关心的“轨道精炼 + 重去平”流程。实际步骤是：

1. 干涉图生成 `SARsInSARInterferogramGeneration`
2. 滤波与相干性计算 `SARsInSARFilterAndCoherence`
3. 残余相位频率/轨道趋势去除 `SARsInSARRemoveResidualPhaseFrequency`
4. 相位解缠 `SARsInSARPhaseUnwrapping`
5. 先按相干性生成 GCP，再做 `SARsInSARRefinementAndReflattening`
6. 相位转位移并地理编码 `SARsInSARPhaseToDisplacement`

这里的关键点是：
- 第 3 步负责先做一轮残差趋势处理。
- 第 5 步才是更接近“轨道精炼与重去平”的核心，使用相干性阈值生成 GCP，再回到 SARscape 原生任务里拟合和修正残余相位。
- 这条链路没有跳出 SARscape，自定义逻辑主要在“串任务”和“自动补 GCP”。

当前配置里比较关键的参数是：
- `IDL_DINSAR_CUSTOM_UNWRAP_COH_THRESHOLD=0.05`
- `IDL_DINSAR_CUSTOM_GCP_COH_THRESHOLD=0.7`
- `IDL_DINSAR_CUSTOM_GEOCODING_COH_THRESHOLD=0.0`
- `IDL_DINSAR_CUSTOM_GEOCODING_PIXEL_SIZE_M=10.0`

这意味着：
- 解缠阈值偏松，目的是尽量先解出来。
- GCP 阈值偏高，目的是让精炼尽量依赖高相干点。
- 地理编码输出网格直接按配置控制，不是后处理重采样。

## 2. ISCE2

当前 managed stripmap 流程由 [run_lt1_dinsar_pipeline.py](../backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py) 驱动，阶段顺序是：

1. `filter`
2. `unwrap`
3. `geocode`
4. `export`

ISCE2 这条链的“增强”不是一个单独的重去平任务，而是分散在两个地方：

- `dense offsets` + `rubbersheeting`：用于几何精化，尽量修正 range/azimuth 的残余偏差。
- `export_products()`：在导出阶段做参考归一化和可选 deramp。

当前默认值是：
- `target_grid_size_m=10`
- `coh_threshold=0.05`
- `reference_mode=coh_median`
- `reference_coh_threshold=0.30`
- `deramp_mode=plane`
- `deramp_coh_threshold=0.30`
- `ionosphere_correction=True`
- `dense_offsets=True`
- `rubbersheet_range=True`
- `rubbersheet_azimuth=True`

这条链的特点是：
- 目标网格大小会影响 multilook 和 geocode 采样。
- `prepare_geocode_dem_subset()` 会按 bbox 裁 DEM 子块，而不是直接拿整幅 DEM 去跑。
- `export_products()` 里会先找参考像元做中位数归一化，再按需拟合长波趋势面做 deramp。
- 导出结果更像“生产交付层”的标准化产品，不是纯原始中间件输出。

## 3. Gamma / PyINT

Gamma 这条链由 [pyint_engine.py](../backend/app/dinsar_engines/pyint_engine.py) 和 [run_lt1_pyint_pipeline.py](../backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py) 共同完成。

核心命令链是 Gamma 原生命令，不是 Python 自己改像元：
- `raw2slc_all`
- `coreg_all`
- `diff`
- `unwrap`
- `atmcor_all`
- `reflatten`
- `geocode_all`
- `data2geotiff`

当前实现里，和“轨道精炼 / 重去平”最接近的是 `reflatten` 分支，内部流程是：

1. `rascc_mask` 依据相干性生成拟合掩膜
2. `quad_fit` 对解缠相位做趋势拟合
3. `quad_sub` 去掉拟合趋势
4. `geocode_back` 回到地理网格
5. `dispmap` 生成 LOS / vertical 位移

这和 ENVI 的 `SARsInSARRefinementAndReflattening` 不是同一个任务，但功能上是同类问题处理：都是在解缠后处理残余趋势。

当前关键配置是：
- `PYINT_UNWRAP_COH_THRESHOLD=0.05`
- `PYINT_PRODUCT_COH_THRESHOLD=0.20`
- `PYINT_GEO_INTERP=1`
- `PYINT_REFLATTEN_COH_THRESHOLD=0.70`
- `PYINT_REFLATTEN_FALLBACK_COH_THRESHOLD=0.20`

当前链路还支持：
- `atmcor`：Gamma 大气校正分支
- `atmcor_use_for_disp`：如果存在 atmcor 结果，可用它作为位移导出源
- `geo_interp=1`：`geocode_back` 使用双三次样条插值

这条链最重要的约束是：
- 科学结果保留 Gamma 原始输出值。
- Python 层不再对最终 `disp.tif` 做额外像元改写。
- `disp_unmasked.tif` 保留原始 Gamma 导出。
- `thumb.webp` 之类预览文件只属于展示层，不代表科学结果被改坏。

另外，Gamma 这条链里的 `target_grid_size_m` 更偏记录用途，不等价于 ISCE2 / ENVI 那种强制输出网格控制。

## 4. 三者差异

| 维度 | ENVI + SARscape | ISCE2 | Gamma / PyINT |
|---|---|---|---|
| 处理风格 | 黑盒原生任务串联 | 论文/工程化脚本链 | 原生命令封装 + Python 管理 |
| 几何精化 | GCP + refinement + reflatten | dense offsets + rubbersheeting | residual phase reflatten |
| 解缠后修正 | SARscape 原生任务内完成 | 主要在 export 阶段归一化 / deramp | `quad_fit` / `quad_sub` / `geocode_back` / `dispmap` |
| 输出控制 | geocode 像元大小可直接配 | target grid size 真正影响输出 | 以 Gamma 原生输出为主，Python 不重写科学值 |
| 结果风格 | 更接近 SARscape 习惯 | 更规范、更标准化 | 更接近 Gamma 原生结果 |

## 5. 容易混淆的点

- 轨道精炼与重去平不是只有一个名字。ENVI 里它是显式任务，ISCE2 里分散在几何精化和导出阶段，Gamma 里则体现在 `reflatten`。
- `DEM 30m` 和 `地理编码输出 10m` 不是同一件事。DEM 是地形源，输出网格是地理编码采样。
- 当前仓库里 `D:\DEM\SRTMDEM_RSP_SARscape.wgs84.vrt` 的网格是 `0.0008333333` 度，属于 3 arc-second DEM，不是字面意义上的 30m 栅格。
- Gamma 的预览图可以看起来比较粗或有马赛克感，但只要最终科学输出没被改写，这不等于流程错误。

## 6. 结论

如果只看“是否具备生产 D-InSAR 的完整链路”，三条核心现在都是能跑的：

- ENVI + SARscape 更像原厂工作流，适合遵循 SARscape 习惯链路的用户。
- ISCE2 更像标准化工程链，强调几何精化、导出规范和可恢复性。
- Gamma / PyINT 则保持 Gamma 原生算法路径，当前已把重去平、atmcor 和导出组织成可管理的生产链。

如果你的要求是“只用 Gamma 里已有的流程和步骤，不自己改科学数据”，那当前 Gamma 链路的原则是符合的：Python 只负责调度、选择和导出组织，最终科学值仍来自 Gamma 原生命令输出。
