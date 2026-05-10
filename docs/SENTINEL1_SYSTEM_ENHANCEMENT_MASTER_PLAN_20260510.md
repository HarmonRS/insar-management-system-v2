# Sentinel-1 系统增强总纲

日期：2026-05-10

本文作为 Sentinel-1 增强工作的主维护文档。后续如果继续推进 Sentinel-1 数据管理、D-InSAR 配对、Gamma/PyINT 生产和结果归档，优先维护本文；更细的调研材料保留为参考：

- [SENTINEL1_DATA_MANAGEMENT_ADAPTATION_PLAN_20260510.md](SENTINEL1_DATA_MANAGEMENT_ADAPTATION_PLAN_20260510.md)
- [SENTINEL1_DINSAR_GAMMA_ISCE2_FEASIBILITY_20260510.md](SENTINEL1_DINSAR_GAMMA_ISCE2_FEASIBILITY_20260510.md)

## 1. 总体目标

让当前系统能够管理 Sentinel-1 数据，并把 Sentinel-1 D-InSAR 生产纳入现有生产管理和结果管理体系。

推荐边界是：

- 系统负责数据管理、元数据解析、精轨匹配、配对、任务分发、生产调度、日志、结果归档和前端展示。
- Gamma + PyINT 负责 Sentinel-1 D-InSAR 的处理内核，包括 SLC 导入、burst 裁剪、TOPS 配准、差分干涉、解缠和地理编码。
- ISCE2 Sentinel-1 TOPS 作为后续可选 profile，不作为第一阶段主线。

第一阶段不要求系统自己实现 SAR 核心算法，也不要求配对阶段计算真实垂直基线。

## 2. 总体架构

建议新增一条独立 Sentinel-1 Gamma 生产链：

```text
Sentinel-1 数据池
  -> 数据扫描与元数据入库
  -> EOF 精轨管理与匹配
  -> D-InSAR 轻量配对
  -> 任务批次保存
  -> Task 目录分发
  -> s1_gamma_dinsar profile
  -> PyINT + Gamma 处理
  -> 结果扫描与 catalog 发布
  -> 生产管理 / 结果管理前端展示
```

关键原则：

- Sentinel-1 不混进 LT-1 专用的 `lt1_gamma_dinsar` 或 `lt1_stripmap`。
- 新增 `s1_gamma_dinsar` profile 作为第一阶段生产入口。
- 真实 `perpendicular_baseline_meters` 由 Gamma/PyINT 在生成 SLC/RSLC 后计算或回填。
- footprint 质心距离只能叫 `centroid_distance_meters`，不能再叫空间基线。

## 3. 数据管理

### 3.1 支持的数据形态

第一阶段建议以 Sentinel-1 SLC ZIP 为生产标准输入。

系统可以管理两类形态：

- `.SAFE` 目录：适合浏览、入库、解析元数据。
- `.zip` 原始产品：适合 PyINT/Gamma 生产分发。

如果数据池只有 `.SAFE` 目录，也可以入库和配对；但进入 PyINT/Gamma 生产时，建议要求关联原始 ZIP。原因是 `D:\Code\PyINT` 的 Sentinel-1 脚本默认从 `DOWNLOAD/S1*.zip` 发现和导入数据。

### 3.2 元数据解析

文件名可以提供粗信息：

- `S1A` / `S1B`
- `IW` / `EW` / `SM`
- `SLC`
- 极化组合，例如 `DV`、`DH`、`SV`、`SH`
- 起止时间
- absolute orbit
- datatake id / product id

可靠配对还需要从 `manifest.safe` 或 annotation XML 中解析：

- relative orbit
- 升降轨方向
- footprint
- swath / burst 相关信息
- 更完整的极化和产品结构

建议采用“关键列 + JSON 扩展”的模型。关键列服务检索、筛选和配对；JSON 保存 Sentinel-1 专有细节，避免一开始改出大量窄字段。

### 3.3 精轨管理

Sentinel-1 EOF 不走 LT-1 的精轨转换链路。

建议新增 Sentinel-1 EOF 管理逻辑：

- 扫描 `.EOF` 文件。
- 解析 mission、validity start、validity stop、generation time。
- 按影像 acquisition time 匹配覆盖该时段的 EOF。
- 允许把匹配到的 EOF 复制到 Task 目录。
- PyINT runner 将 EOF 目录作为 `OPOD_DIR` 暴露给 PyINT/GAMMA。

这样可以避免生产时临时联网下载精轨，也便于任务复现。

## 4. D-InSAR 配对

Sentinel-1 配对适合嵌入现有 D-InSAR 配对体系，但应使用独立策略：

```text
sentinel1_dinsar_pairing
```

第一阶段配对只做轻量预筛选：

- 同一卫星族：`S1A` / `S1B` 可以互配，不按字面同卫星硬卡死。
- 同一成像模式：优先只支持 `IW`。
- 同一产品类型：优先只支持 `SLC`。
- 同一升降轨方向。
- 同一 relative orbit。
- 极化兼容，例如 `VV` 对 `VV`。
- footprint 重叠率达到阈值。
- 时间基线不超过阈值。
- master/slave 都能匹配 EOF。

不在配对阶段强制完成：

- 真实垂直基线计算。
- burst 级精确公共覆盖计算。
- TOPS 配准质量判断。
- 解缠可行性判断。

这些应交给 Gamma/PyINT 的处理链路完成。配对阶段只负责筛出大概率可跑的候选对。

## 5. 任务分发

Sentinel-1 D-InSAR 任务分发需要输出一个可复现的 Task 目录。

建议包含：

```text
Task/
  manifest.json
  pair.json
  input/
    master/
      <S1 master zip or SAFE>
    slave/
      <S1 slave zip or SAFE>
    orbit/
      <matched EOF files>
    dem/
      <optional dem reference or config>
  pyint/
    <project>.template
```

分发开关建议：

- 是否复制 EOF 到 Task 目录。
- 是否输出 ZIP 压缩包。
- 是否保留中间 SLC/RSLC 产物。
- 是否只发布核心结果。

如果生产目标是 PyINT/Gamma，Task 到 PyINT scratch 目录之间可以由 runner 再做一次结构化投放，把 master/slave ZIP 放入：

```text
$SCRATCHDIR/<project>/DOWNLOAD/
```

## 6. Gamma/PyINT 生产

第一阶段新增 profile：

```text
s1_gamma_dinsar
```

它应独立于当前 `lt1_gamma_dinsar`。

推荐 runner 行为：

1. 读取 Task manifest 和 pair 信息。
2. 创建 PyINT project 目录。
3. 复制或链接 master/slave Sentinel-1 ZIP 到 `DOWNLOAD/`。
4. 准备 EOF / OPOD 目录。
5. 写入 PyINT template。
6. 调用 Sentinel-1 PyINT 脚本链。
7. 收集每一步日志和退出码。
8. 扫描输出产物并发布到系统结果 catalog。

可复用的 `D:\Code\PyINT` Sentinel-1 脚本包括：

- `pyint/down2slc_sen.py`
- `pyint/down2slc_sen_all.py`
- `pyint/extract_s1_bursts.py`
- `pyint/coreg_s1_gamma.py`
- `pyint/raw2ifg_s1.py`
- `pyint/select_pairs.py`

生产系统不建议直接无控制地调用完整 app，而应显式控制步骤、日志、失败原因和产物发布。

## 7. 结果管理

PyINT/Gamma 生产结束后，系统负责结果归档。

第一阶段建议发布：

- pair manifest
- PyINT template
- 全量日志
- preflight 报告
- SLC/RSLC 生成状态
- 共同 burst 检查结果
- interferogram
- coherence
- unwrapped phase
- geocoded result
- quicklook
- 真实 `perpendicular_baseline_meters`

结果管理应复用现有生产结果 catalog，不新建一套孤立页面。前端仍然从生产管理和结果管理入口查看，只是引擎 profile 显示为 `s1_gamma_dinsar`。

## 8. 前端入口

前端需要在现有页面中增强，而不是新增一个割裂的 Sentinel-1 子系统。

建议改动范围：

- 数据管理页面支持 Sentinel-1 过滤、详情和 EOF 匹配状态。
- 配对页面新增 Sentinel-1 策略和参数。
- 配对结果中显示 relative orbit、升降轨、极化、时间基线、重叠率和 EOF 状态。
- 生产管理中支持 `s1_gamma_dinsar` profile。
- 任务分发 UI 中增加 EOF 复制和 ZIP 导出开关。
- 结果详情中显示 PyINT/Gamma 日志、核心产物和真实垂直基线。

第一阶段不需要让用户配置所有 PyINT template 字段。可以只暴露少量参数，其余使用系统默认值。

## 9. 数据库和自检

本系统有数据库自维护和系统自检机制，Sentinel-1 增强必须纳入这两部分。

需要评估的数据库变更：

- Sentinel-1 关键元数据字段是否已有通用列可复用。
- relative orbit、acquisition start/stop、source archive path、orbit match status 是否需要新增列。
- Sentinel-1 专有元数据是否放入 JSON 扩展字段。
- pairing cache 是否需要保存 `centroid_distance_meters` 与 `perpendicular_baseline_meters` 的区分。
- 生产结果 catalog 是否需要新增 engine profile 或产品类型枚举。

自检需要增加：

- Sentinel-1 数据根目录是否存在。
- EOF 根目录是否存在。
- Sentinel-1 解析器是否可用。
- `s1_gamma_dinsar` profile 是否启用。
- PyINT 路径是否存在。
- GAMMA 环境是否可用。
- `eof` 工具是否可用。
- 关键 GAMMA/PyINT 命令是否可调用。
- DEM 配置是否完整。

任何 schema 改动都必须同步数据库自维护逻辑，避免启动时 schema check 报错。

## 10. 分阶段实施

### 阶段 1：数据管理和配对

目标：

- Sentinel-1 SAFE / ZIP 可扫描入库。
- 基础元数据和 footprint 可解析。
- EOF 可扫描、匹配和显示。
- Sentinel-1 配对可生成候选对。
- 配对不进入生产也能保存批次。

验收：

- 同一 relative orbit 的 S1 数据能被正确筛出。
- 不同方向、不同 relative orbit、不同模式的数据不会误配。
- footprint 重叠率和时间基线显示正确。
- EOF 状态清晰可见。

### 阶段 2：任务分发

目标：

- Sentinel-1 pair 可分发为 Task 目录。
- 可选复制 EOF。
- 可选导出 ZIP。
- manifest 可复现输入数据和参数。

验收：

- Task 目录包含 master/slave、EOF、pair manifest、profile 参数。
- ZIP 导出可直接交给生产或转移归档。

### 阶段 3：Gamma/PyINT 生产

目标：

- 新增 `s1_gamma_dinsar` profile。
- 托管调用 PyINT/Gamma 完成单对 Sentinel-1 D-InSAR。
- 生产日志进入系统。

验收：

- 至少一对 Sentinel-1 SLC ZIP 能跑通到 geocoded 结果。
- 失败时能看到明确步骤和原因。
- 真实垂直基线可以回填或记录。

### 阶段 4：结果管理

目标：

- PyINT/Gamma 产物进入现有结果 catalog。
- 前端可以查看日志、manifest、quicklook 和核心产物。

验收：

- 用户不需要进入 PyINT 工作目录即可查看结果。
- 结果与 pair、task、engine profile 可追踪。

### 阶段 5：ISCE2 Sentinel-1 可选增强

目标：

- 新增 `s1_tops` profile。
- 使用 ISCE2 `topsApp.py` 或 topsStack。
- 与 Gamma 路线共享数据管理和 EOF 管理。

验收：

- 不污染现有 LT-1 `lt1_stripmap` runner。
- Sentinel-1 TOPS 生产有独立健康检查、日志和结果发布。

## 11. 主要风险

- 数据池只保留 `.SAFE`，没有原始 ZIP，会增加 PyINT/Gamma 接入复杂度。
- EOF 匹配不稳定会导致生产不可复现。
- Sentinel-1 annotation XML 解析不完整会影响 relative orbit、方向和 footprint。
- 中间产物体积大，SLC/RSLC 保留策略要可配置。
- PyINT 专家代码可用，但生产系统仍要做日志、失败分类和输出扫描。
- ISCE2 Sentinel-1 虽然可行，但当前系统没有 tops profile，不能当作低成本改动。

## 12. 当前推荐决策

建议确认以下产品决策后再进入代码实现：

1. Sentinel-1 生产第一阶段要求保留原始 SLC ZIP。
2. 配对策略命名为 `sentinel1_dinsar_pairing`。
3. 生产 profile 命名为 `s1_gamma_dinsar`。
4. 第一阶段只支持 `IW + SLC + 同 relative orbit + 同方向 + 兼容极化`。
5. `centroid_distance_meters` 与 `perpendicular_baseline_meters` 严格区分。
6. 真实垂直基线由 Gamma/PyINT 处理后回填。
7. ISCE2 Sentinel-1 TOPS 放到第二阶段。

这套边界可以把系统改造控制在数据管理、配对、分发、生产托管和结果管理范围内，把 SAR 处理精度交给 Gamma/PyINT，避免在配对阶段过度复杂化。
