# PyINT LT-1 DEM Source Experiment Results

**日期**: 2026-04-20  
**状态**: 已执行  
**目标问题**: `init_offsetm` 失败是否主要由 DEM 本体来源导致

## 1. 前置结论

在这轮 DEM 源对照之前，已经完成 `init_offsetm` patch 扫描。

- 基线 run root:
  - `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_cases\run_20260420T093322Z`
- 扫描结论:
  - 更换 `rpos/azpos` 可以把中心 patch 的零值数从 `184099/190155` 降到约 `168322/166114`
  - 但仍远高于 `32768` 阈值
  - 没有任何候选 patch 通过 `init_offsetm`

因此，这里把“中心 patch 选错”降级为次要因素，继续验证 DEM 源本体是否主导失败。

## 2. 实验设计

固定条件:

- 同一组 3 景 LT-1 数据
- 同一 master: `20230726`
- 同一 baseline orbit 几何
- 同一 PyINT/Gamma 处理链

只更换 DEM 来源:

1. `COPDEM`
   - `prepared_dem_source=/mnt/d/DEM/COPDEM_GLO30_China_4326_DEM`
2. `GMTED2010`
   - `prepared_dem_source=/mnt/d/DEM/GMTED2010.jp2`
3. `OpenTopography SRTMGL1`
   - 走 PyINT 默认下载路径

执行链路:

- `makedem_pyint`
- `generate_rdc_dem`
- `coreg_gamma`
- `audit_lt1_dem_geometry_chain.py`

## 3. 实验目录

本地 DEM 对照:

- run root:
  - `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_dem_cases\run_20260420T152607Z`
- 审计输出:
  - `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_dem_cases\run_20260420T152607Z\audit_dem_geometry`

OpenTopography SRTMGL1:

- 首次尝试:
  - `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_dem_cases\run_20260420T170406Z`
  - 失败原因: WSL `isce2` 环境缺少 `rasterio`
- 安装 `rasterio` 后重跑:
  - `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_dem_cases\run_20260420T220524Z`
  - 审计输出:
    - `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_dem_cases\run_20260420T220524Z\audit_dem_geometry`

## 4. 结果摘要

### 4.1 `coreg/init_offsetm` zero-count

`20230624`:

- `COPDEM`: `184099`
- `GMTED2010`: `262033`
- `SRTMGL1`: `183582`

`20230920`:

- `COPDEM`: `190155`
- `GMTED2010`: `262070`
- `SRTMGL1`: `189571`

阈值均为 `32768`。

### 4.2 `HGTSIM` / `lt0` / `mli0_samp_overlap`

`COPDEM`:

- `hgtsim zero_ratio`: `0.967266`
- `lt0 valid_pair_ratio`: `0.032734`
- `20230624 center overlap`: `0.297718`
- `20230920 center overlap`: `0.274616`

`GMTED2010`:

- `hgtsim zero_ratio`: `0.999964`
- `lt0 valid_pair_ratio`: `0.000036`
- `20230624 center overlap`: `0.000423`
- `20230920 center overlap`: `0.000282`

`SRTMGL1`:

- `hgtsim zero_ratio`: `0.967264`
- `lt0 valid_pair_ratio`: `0.032736`
- `20230624 center overlap`: `0.299690`
- `20230920 center overlap`: `0.276844`

## 5. 结论

### 5.1 当前 `COPDEM` 不是主要故障源

`SRTMGL1` 作为更接近原始 PyINT 默认路径的下载 DEM，跑出来的几何指标与当前 `COPDEM` 几乎一致:

- `hgtsim zero_ratio` 基本相同
- `lt0 valid_pair_ratio` 基本相同
- `mli0_samp_overlap` 基本相同
- `init_offsetm zero-count` 只改善了几百个像素，量级上没有本质变化

这说明:

- 把当前系统 DEM 替换成原始 PyINT 默认下载 DEM
- 并不能把问题从 `184k/190k` 拉到 `32768` 阈值附近

### 5.2 更差的 DEM 会进一步恶化问题

`GMTED2010` 把几何链几乎压成全零:

- `hgtsim zero_ratio` 逼近 `1.0`
- `lt0 valid_pair_ratio` 下降到 `0.000036`
- `init_offsetm zero-count` 直接升到 `262k`

这说明 DEM 源会影响结果，但当前问题不是“现有 DEM 明显坏掉”，而是:

- 当前几何链本来就已经非常稀疏
- 更粗或不合适的 DEM 只会让它更差

### 5.3 当前最可疑的位置继续落在 LT-1 几何导入链

综合 patch 扫描和 DEM 源对照，当前更像是以下链路问题，而不是 DEM 文件本体问题:

- `par_LT1_SLC / LT-1 导入几何`
- `generate_rdc_dem`
- `coreg_gamma` 中基于 DEM 的几何映射链

## 6. 补充记录

为完成 `OpenTopography SRTMGL1` 对照，已在 WSL `isce2` 环境安装:

- `rasterio==1.4.4`

安装原因不是业务修复，而是 PyINT 下载 DEM 路径在当前环境里依赖该包做分块 tif 校验与合并。

## 7. 建议下一步

下一轮不建议继续反复更换 DEM。

更值得做的是:

1. 对比 `COPDEM` 与 `SRTMGL1` 生成出来的 `pyint_stage.dem.par`、`UTMDEMpar`、`UTM2RDC/UTMTORDC` 是否几乎一致
2. 回到 LT-1 导入链，核查 `.slc.par` 中被 `gc_map1 / geocode / init_offsetm` 直接消费的几何字段
3. 如果需要继续做 DEM 类实验，优先做“同一 DEM 下替换导入几何参数”，而不是继续换 DEM 本体
