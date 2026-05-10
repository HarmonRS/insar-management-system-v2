# Sentinel-1 D-InSAR Gamma / ISCE2 可行性评估

日期：2026-05-10

本文只评估设计，不涉及代码修改。重点回答：在不使用 ENVI + SARscape 核心的前提下，现有 D-InSAR 生产链路是否适合接入 Sentinel-1，Gamma/PyINT 与 ISCE2 两条路线的实际工作量和风险分别是什么。

## 1. 结论

Gamma/PyINT 路线适合优先做 Sentinel-1 D-InSAR。

`D:\Code\PyINT` 中的专家代码对 Sentinel-1 支持比较完整，尤其是 Sentinel-1 TOPS/IW 的 ZIP 导入、精轨下载、burst 选择、TOPS burst 裁剪、ScanSAR/TOPS 配准、差分干涉、解缠、地理编码等流程。它不是只停留在文件名解析层面，而是已经围绕 GAMMA 命令组织了 Sentinel-1 的实际处理脚本。

ISCE2 本身也能处理 Sentinel-1 TOPS 数据，但本项目当前接入的 ISCE2 托管链路是 `lt1_stripmap`，核心是 LT-1 stripmap 的 `stripmapApp.py` 流程，不是 Sentinel-1 TOPS 的 `topsApp.py` 流程。因此 ISCE2 的 Sentinel-1 能力不能直接等价为“当前系统里马上可用”。如果要做，属于新增一个 `s1_tops` 生产 profile，工作量明显大于复用 PyINT/Gamma 的 Sentinel-1 脚本。

推荐顺序：

1. 先做 `s1_gamma_dinsar` profile，复用 `D:\Code\PyINT` 的 Sentinel-1 Gamma 处理路线。
2. 配对阶段保持轻量，只做同轨、同模式、同方向、同极化、覆盖重叠、时间基线等前置筛选。
3. 真正的垂直基线、可处理性和失败原因交给 Gamma/PyINT 在 SLC/RSLC 生成后校验和回填。
4. ISCE2 的 `s1_tops` profile 放到第二阶段，等 Gamma 路线跑通后再做。

## 2. 本项目当前 D-InSAR 引擎状态

### 2.1 PyINT/Gamma 当前是 LT-1 专用接入

本项目现有 PyINT 托管入口主要围绕 `lt1_gamma_dinsar`：

- `.env` 中当前 PyINT/Gamma D-InSAR 配置说明面向 LT-1。
- `backend/app/services/pyint_engine.py` 当前只接受 `lt1_gamma_dinsar`。
- `backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py` 是 LT-1 专用 runner。
- `backend/app/services/pyint_input_assets_service.py` 负责 LT-1 归档、LT-1 精轨和输入资产准备。

这说明当前系统“接入 PyINT”不等于“已经接入 Sentinel-1 PyINT”。要支持 Sentinel-1，合理做法是新增一个 profile，而不是把 LT-1 profile 混改成多卫星逻辑。

### 2.2 ISCE2 当前是 LT-1 stripmap 接入

本项目现有 ISCE2 托管入口主要围绕 `lt1_stripmap`：

- `.env` 中配置的是 `ISCE2_PROFILE=lt1_stripmap`。
- 当前 pipeline 脚本是 `backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py`。
- 当前使用的是 `stripmapApp.py`，不是 Sentinel-1 TOPS 常用的 `topsApp.py`。
- WSL runtime registry 允许的 ISCE2 profile 目前也是 LT-1 stripmap 方向。

因此，ISCE2 对 Sentinel-1 的支持在算法生态上成立，但在本项目现有托管实现里还没有落地。

## 3. `D:\Code\PyINT` 对 Sentinel-1 的支持证据

`D:\Code\PyINT` 的 README 和用户指南明确把 Sentinel-1 作为支持对象。代码中也存在成套 Sentinel-1 处理脚本：

- `pyint/down2slc_sen.py`
  Sentinel-1 ZIP 下载数据转 SLC。它会调用 `eof` 获取精轨，并通过 GAMMA 相关脚本读取 TOPS SLC。
- `pyint/down2slc_sen_all.py`
  批量发现 `DOWNLOAD/S1*.zip`，按日期调用 Sentinel-1 SLC 生成逻辑。
- `pyint/slc_sen_cat.py`
  老版本 Sentinel-1 SLC 导入与拼接逻辑，说明 PyINT 对多 swath / 多 burst / 多文件拼接有历史支持。
- `pyint/extract_s1_bursts.py`
  基于 master/slave 的 burst 参数，裁剪共同 burst 区间。
- `pyint/coreg_s1_gamma.py`
  使用 GAMMA `ScanSAR_coreg.py` 做 Sentinel-1 TOPS/ScanSAR 配准。
- `pyint/raw2ifg_s1.py`
  Sentinel-1 单对 D-InSAR 的一站式流程：下载数据转 SLC、burst 提取、DEM、配准、差分干涉、解缠、地理编码。
- `pyint/select_pairs.py`
  基于 GAMMA `base_calc` 计算基线并按 `max_tb`、`max_sb` 等约束选网。

这批脚本说明 PyINT 的 Sentinel-1 支持不是临时拼出来的文件名适配，而是围绕 GAMMA Sentinel-1 TOPS 处理能力组织出的完整流程。

## 4. Gamma/PyINT 接入方案

### 4.1 新增 profile

建议新增独立 profile：

```text
s1_gamma_dinsar
```

不要复用或扩展 `lt1_gamma_dinsar` 的内部假设。两者输入资产、轨道文件、SLC 生成方式、配准方式和模板字段都不同。

最小改造对象：

- 新增 Sentinel-1 输入资产准备逻辑。
- 新增 Sentinel-1 PyINT runner，例如 `run_s1_pyint_pipeline.py`。
- 生产引擎 registry 增加 `s1_gamma_dinsar`。
- 任务 preflight 增加 Sentinel-1 ZIP、EOF、GAMMA 命令、模板字段检查。
- 输出 catalog 增加 Sentinel-1 结果归档映射。

### 4.2 输入数据约定

PyINT 现有 Sentinel-1 脚本默认从：

```text
$SCRATCHDIR/<project>/DOWNLOAD/S1*.zip
```

发现数据。

因此最省事、风险最低的约定是：系统管理 Sentinel-1 时尽量保留原始 `.zip` 产品，并在生产分发时把 master/slave 的 ZIP 放入 PyINT 项目的 `DOWNLOAD` 目录。

如果数据池里只有解压后的 `.SAFE` 目录，当前 PyINT 脚本不能直接等价复用。可选方案有三个：

1. 要求 Sentinel-1 生产任务必须绑定原始 ZIP。
2. 在分发阶段把 `.SAFE` 目录重新打包成 ZIP。
3. 改造 PyINT 的 Sentinel-1 导入脚本，让它直接接受 `.SAFE` 路径。

推荐第 1 种。第 2 种会增加磁盘和时间成本，第 3 种会扩大对专家代码的修改面。

### 4.3 单对 D-InSAR 最小流程

对于本系统的 D-InSAR 配对任务，建议先做“系统选出一对，PyINT 处理这一对”，而不是马上做完整时序网。

可控 runner 可以按以下顺序执行：

1. 准备 `$SCRATCHDIR/<project>/DOWNLOAD`，放入 master/slave Sentinel-1 ZIP。
2. 写入 `$TEMPLATEDIR/<project>.template`，包含 masterDate、slaveDate、swath、burst、look、轨道、DEM、网络参数等。
3. 调用 `down2slc_sen.py <project> <masterDate>`。
4. 调用 `down2slc_sen.py <project> <slaveDate>`。
5. 调用 `extract_s1_bursts.py <project> <masterDate> <slaveDate>`。
6. 调用 `generate_rdc_dem.py <project>` 或等价 DEM 生成步骤。
7. 调用 `coreg_s1_gamma.py <project> <masterDate> <slaveDate>`。
8. 调用 `diff_gamma.py <project> <masterDate> <slaveDate>`。
9. 调用 `unwrap_gamma.py <project> <masterDate> <slaveDate>`。
10. 调用 `geocode_gamma.py <project> <masterDate>-<slaveDate>`。

也可以参考 `raw2ifg_s1.py` 作为端到端样板，但不建议生产系统直接无控制地调用全流程脚本。托管系统应该显式控制每一步、日志、失败原因、输出发布和清理策略。

### 4.4 精轨处理

PyINT 的 Sentinel-1 脚本会使用 `eof` / OPOD 目录获取精轨。系统前面已经设计了 Sentinel-1 精轨管理和分发逻辑，因此两者可以分工：

- 数据管理层负责发现、缓存、匹配和可选分发 EOF。
- PyINT runner 负责把 EOF 目录暴露给 PyINT/GAMMA。
- preflight 负责确认 master/slave 覆盖时段都有可用 EOF。

这样可以避免每次生产任务都临时联网下载精轨，也能保证任务可复现。

## 5. ISCE2 接入方案

ISCE2 Sentinel-1 TOPS 路线理论上可行，但本项目当前没有现成托管实现。

如果做 ISCE2，需要新增：

- `s1_tops` profile。
- `topsApp.py` 路径配置，例如 `ISCE2_TOPS_APP`。
- Sentinel-1 SAFE/ZIP 输入准备逻辑。
- EOF 轨道文件挂载逻辑。
- `topsApp.py` XML 生成器。
- topsStack 或 topsApp 的执行脚本。
- Sentinel-1 TOPS 输出目录识别、catalog 发布和日志解析。
- 与 LT-1 `stripmapApp.py` profile 隔离的健康检查。

这条路线的主要风险不是 ISCE2 不支持 Sentinel-1，而是当前系统的 ISCE2 封装抽象是为 LT-1 stripmap 写的。直接把 Sentinel-1 塞进现有 `lt1_stripmap` runner 会形成大量条件分支，后期维护会很差。

如果要做，建议完全独立成 `s1_tops`，不要污染 LT-1 runner。

## 6. 配对与基线策略

Sentinel-1 的配对建议分为两层。

第一层是系统内的轻量预筛选：

- 同一 relative orbit。
- 同一升降轨方向。
- 同一 beam mode，例如 IW。
- 极化兼容，例如 VV 对 VV、VH 对 VH。
- 覆盖范围有足够重叠。
- 时间基线在阈值内。
- 产品级别满足生产要求，优先 SLC。
- EOF 精轨可获得。

第二层是处理引擎内的精确校验：

- SLC/TOPS 导入是否成功。
- master/slave 是否有共同 burst。
- DEM 覆盖是否足够。
- GAMMA `base_calc` 计算出的垂直基线是否超过阈值。
- TOPS 配准质量是否达标。
- 干涉、解缠、地理编码是否生成有效产物。

这符合“配对是初级任务，不要太复杂”的目标。系统配对不应该冒充严密的 SAR 处理器；它只需要筛出大概率能跑的候选对。真正的物理基线和可处理性，由 GAMMA/ISCE2 在生成 SLC/RSLC 后确认。

注意命名上应避免再把 footprint 质心距离叫作 `spatial_baseline_meters`。对 Sentinel-1 更合理的字段区分是：

- `centroid_distance_meters`：覆盖 footprint 质心距离，只是几何覆盖近似指标。
- `temporal_baseline_days`：时间基线。
- `perpendicular_baseline_meters`：由处理引擎计算或轨道模型计算出的垂直基线。

## 7. 工作量评估

### Gamma/PyINT 优先路线

前提：Sentinel-1 数据管理、ZIP 保留、EOF 匹配和分发机制已经具备。

预计工作量：

- 最小可用单对 D-InSAR：约 4 到 7 个工作日。
- 加上稳定的前端配置、健康检查、日志归档、失败原因归类和输出发布：约 1 到 2 周。
- 扩展到时序网、自动 burst 推荐、多 pair 网络：另算，不建议第一阶段做。

主要风险：

- 原始数据是否保留 ZIP。
- WSL/GAMMA 环境中的 Sentinel-1 命令是否完整。
- `eof` 工具和 OPOD 目录是否稳定。
- PyINT 模板字段默认值是否适配当前项目。
- 大量 ZIP、SLC、RSLC 中间产物带来的磁盘压力。

### ISCE2 Sentinel-1 路线

预计工作量：

- 可用 demo：约 1 周。
- 托管生产级接入：约 2 周或更长。

主要风险：

- 当前 `isce2_engine.py` 和 WSL registry 偏 LT-1 stripmap。
- 需要新增 topsApp/topsStack 方向的 XML、输入资产和产物识别。
- 输出结构和错误日志与当前 LT-1 ISCE2 产物不一致。
- 对系统健康检查和 profile registry 的影响更大。

## 8. 推荐实施路线

第一阶段只做 Gamma/PyINT Sentinel-1 单对 D-InSAR：

1. 完成 Sentinel-1 数据管理文档中的数据入库、元数据、EOF 匹配和分发约定。
2. 新增 `s1_gamma_dinsar` profile。
3. 新增 Sentinel-1 PyINT runner，显式调用 `D:\Code\PyINT` 中已存在的 Sentinel-1 脚本链。
4. 生产任务只接受 master/slave 两景 SLC ZIP。
5. 配对表只保存轻量预筛选指标和处理后回填的真实 `perpendicular_baseline_meters`。
6. 跑通后再考虑自动 burst 推荐、多 pair 网络和 ISCE2 `s1_tops`。

第二阶段再做 ISCE2 Sentinel-1：

1. 新增 `s1_tops` profile。
2. 使用 `topsApp.py` 或 topsStack，不复用 LT-1 stripmap runner。
3. 与 Gamma 路线共享数据管理和 EOF 匹配能力。
4. 输出 catalog 与生产日志保持同一前端体验。

## 9. 验收标准

Gamma/PyINT Sentinel-1 接入的第一阶段验收建议如下：

- 同一对 Sentinel-1 ZIP 可以从任务分发目录进入 PyINT `DOWNLOAD`。
- preflight 能明确报告 ZIP、EOF、DEM、GAMMA 命令和模板字段是否齐全。
- master/slave 能成功生成 SLC。
- 能识别共同 burst 并完成裁剪。
- 能完成 TOPS 配准并生成 RSLC。
- 能生成差分干涉图、解缠结果和地理编码结果。
- 生产日志能展示每一步命令、耗时、退出码和失败原因。
- 结果 catalog 能发布核心产物。
- 配对记录能回填真实 `perpendicular_baseline_meters`，同时保留原始 `centroid_distance_meters`。

## 10. 总体判断

不使用 ENVI + SARscape 核心没有问题。对 Sentinel-1 D-InSAR 来说，本项目更应该优先利用 Gamma/PyINT 的专家代码路线。

Gamma/PyINT 是“已有专家脚本，需要做系统托管适配”；ISCE2 是“算法生态支持，但本项目需要新增 tops profile”。所以第一阶段选择 Gamma/PyINT 更稳、更快，也更符合“配对不要做得过度复杂，但生产尽可能有精度”的目标。
