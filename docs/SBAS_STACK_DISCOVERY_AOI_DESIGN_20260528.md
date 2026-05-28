# SBAS-InSAR 序列发现与 AOI 选栈设计

## 现状结论

当前系统不是限制最多 7 景。对 `D:\LuTan1_Image_Pool` 的检查结果为：

- LT1 场景目录：1500 个；
- 按当前严格规则分组后：886 个候选序列；
- 最大可用序列：7 个日期。

当前发现逻辑的硬分组键为：

```text
satellite
satellite_mode
receiving_station
relative_orbit
orbit_direction
imaging_mode
polarization
center_bucket
```

其中 `center_bucket` 约为 0.1 度经纬度格网。这个规则保守、容易复现，但它不是标准 SBAS 选栈方法。它会把相邻 frame、中心点略有偏移但实际覆盖同一 AOI 的影像拆成不同序列。

## 一般 SBAS 选序列方法

SBAS 序列选择通常不是先按影像中心点硬分组，而是围绕一个目标区域 AOI 建栈：

1. 选择目标区域  
   AOI 可以是行政区、工程区、多边形、bbox、中心点缓冲区或已有项目范围。

2. 选择同一观测几何  
   一般要求同一轨道方向、同一相对轨道、同一成像模式、同一极化、相近视角和足够 footprint 重叠。  
   对 LT1 当前实现，默认仍应保持 LT1A/LT1B 分开；跨星合并只能作为高级实验模式。

3. 按 AOI 覆盖筛选影像  
   影像 footprint 需要覆盖 AOI，或者至少满足指定覆盖比例。最终处理范围通常取所有入选影像的公共交集。

4. 检查时间密度  
   关注日期数量、最大时间间隔、季节性断档、时间跨度。SBAS 越密越好，但必须保证网络连通。

5. 检查轨道和 DEM 可用性  
   精轨缺失的影像可先展示，但默认不进入可生产栈。

6. 构建小基线网络  
   不是简单相邻配对。常见做法是根据时间基线和垂直基线构图，选择满足阈值的边，并保证图连通。

7. 用处理引擎验证基线  
   真实垂直基线应由 Gamma `base_calc` 或等价步骤计算。元数据阶段只能做预筛选，不能替代最终 baseline audit。

## 专家文档关系

专家文档没有写自动“找序列”算法，它假设用户已经准备好 `RAW/<date>/` 数据，并在运行前手动修改日期、阈值、宽高和种子点等参数。

文档中的 `base_calc` 小基线阈值示例类似：

```text
spatial baseline: -1000 1000
temporal baseline: 0 120
```

这说明专家链路里真正决定 SBAS 网络的是 base_calc/itab 阶段。系统需要做的是把“人工准备 RAW 日期序列”产品化成可审查的 AOI 选栈和网络计划。

## 设计目标

1. 保留当前严格模式，作为快速、保守、可复现实验路径。
2. 增加 AOI 发现模式，按行政区/AOI 查找覆盖同一目标区域的影像。
3. 把 `center_bucket` 从用户可见的生产条件降级为内部诊断字段。
4. 把接收站从硬条件降级为软提示，除非后续实测证明必须拆分。
5. 在创建 Run 前只展示用户需要判断的生产信息：时间范围、景数、覆盖质量、网络质量和风险标签。
6. 生成可审查的 Stack Manifest v2 和 Pair Network Plan，再交给 Gamma baseline audit 验证。

## 发现模式

### 1. Strict 模式

当前模式，继续保留。

适用场景：

- 快速测试；
- 已经验证能跑通的固定栈；
- 用户希望尽量避免覆盖差异和几何风险。

硬分组字段：

```text
satellite
relative_orbit
orbit_direction
imaging_mode
polarization
center_bucket
```

`receiving_station` 建议改为默认软字段，不再强拆。

### 2. AOI 模式

新推荐模式。

输入：

```text
admin_region
bbox
geojson polygon
center + radius
```

处理：

1. 找到所有 footprint 与 AOI 相交的 LT1 场景；
2. 按观测几何分组；
3. 计算每景 AOI 覆盖比例；
4. 过滤覆盖比例不足的影像；
5. 计算公共交集范围；
6. 统计日期、时间间隔和精轨完整性；
7. 输出候选栈。

建议默认阈值：

```text
min_scenes: 5
dev_min_scenes: 3
min_aoi_coverage_ratio: 0.80
min_common_overlap_ratio: 0.60
warn_max_gap_days: 120
hard_fail_max_gap_days: none，改为 warning
```

如果 AOI 是行政区且行政区很大，不应要求单景覆盖整个行政区。应允许用户进一步选择 bbox/工程区，或者默认用行政区中心缓冲区进行候选发现。

### 3. 内部诊断

诊断不是用户入口，也不作为生产模式展示。它只用于日志、运维、自检和开发排查。

内部输出：

```text
raw_scene_count
parsed_scene_count
group_count
top_groups_by_scene_count
top_groups_by_date_count
excluded_by_missing_orbit
excluded_by_geometry
excluded_by_aoi_coverage
excluded_by_common_overlap
```

这些信息可以写入 manifest/log，必要时在管理员调试页查看。普通用户不需要看到“为什么只有 7 景”这类开发解释。

## 小基线网络设计

发现阶段只生成候选网络，最终以 Gamma `base_calc` 为准。

建议流程：

1. 对候选日期生成全部可能 pair；
2. 先按时间基线过滤；
3. 运行或计划 Gamma `base_calc` 得到真实垂直基线；
4. 按垂直基线过滤；
5. 检查网络连通性；
6. 如果断开，允许加入 bridge edge，并标记为超阈值连接；
7. 输出 `itab` 和 pair network summary；
8. 前端要求用户审批。

推荐网络策略：

```text
primary: connected small-baseline graph
fallback: adjacent chain
bridge: allow one or more warning edges when sparse archive causes seasonal gap
```

对当前数据尤其重要：如果严格使用 120 天时间阈值，2024-10 到 2025-05 的 224 天断档会导致网络断开。系统应显示风险，而不是静默删除后续年份。

## Stack Manifest v2

新增字段：

```text
discovery_mode
aoi
aoi_source
geometry_group_key
hard_group_fields
soft_group_fields
scene_coverage
common_intersection
date_stats
orbit_stats
candidate_pair_network
diagnostics
```

每景新增：

```text
aoi_overlap_ratio
common_intersection_participation
selection_status
selection_reasons
```

每个 pair 新增：

```text
temporal_baseline_days
perpendicular_baseline_m
pair_status
bridge_edge
rejection_reason
```

## 前端设计

候选序列发现页增加：

1. 生产区域选择：行政区、bbox、GeoJSON 或中心点缓冲区；
2. 观测条件：轨道方向、相对轨道、极化、时间范围；
3. 高级参数折叠区：覆盖阈值、最小景数、是否要求精轨完整；
4. 候选列表显示：
   - 日期数；
   - 可生产景数；
   - AOI 覆盖率；
   - 公共交集面积；
   - 最大时间间隔；
   - 网络质量；
   - 风险标签；
   - 推荐/可生产/需确认状态。

候选详情显示：

```text
日期列表
覆盖范围摘要
时间跨度和最大间隔
pair network 摘要
base_calc 审核结果
```

用户界面不展示原始分组数、center_bucket 分裂原因、解析失败目录等开发诊断。若需要追踪问题，这些信息进入后台日志或管理员自检接口。

## 实施步骤

### 阶段一：AOI 选栈入口

- 增加 AOI discovery mode 参数；
- 支持行政区/bbox/GeoJSON 输入；
- 前端只显示推荐候选和风险标签。

### 阶段二：场景覆盖筛选

- 使用已有 LT1 bbox 元数据；
- 用 shapely 计算 AOI 交集和覆盖率；
- 输出 AOI candidate stack。

### 阶段三：Stack Manifest v2

- 记录 AOI、覆盖率、软硬分组字段；
- 创建 Run 时冻结 v2 manifest；
- 保持现有生产链路可读取 scenes 列表。

### 阶段四：网络计划升级

- 发现阶段生成候选 pair graph；
- baseline audit 阶段用 Gamma `base_calc` 回填真实 Bperp；
- 前端审批连通网络，而不是只审批相邻链。

### 阶段五：生产联调

- 用当前 1500 景数据池分别测试：
  - 严格模式是否仍得到 7 景；
  - AOI 模式是否能扩大目标区域候选；
  - 扩大后公共交集是否仍足够；
  - Gamma coreg/base_calc 是否接受新栈。

### 阶段六：内部诊断与自检

- 将严格分组统计、排除原因和解析错误写入 discovery log；
- 管理员自检接口可查看诊断摘要；
- 普通生产页面不展示开发诊断细节。

## 风险

- AOI 模式可能把相邻 frame 合进来，导致公共交集变小。
- 跨 LT1A/LT1B 合并可能存在几何和相位一致性风险，默认不启用。
- 接收站是否可合并需要用实测验证；先作为软字段。
- 时间阈值过严会把稀疏数据切断，过宽会降低反演质量，需要前端显式提示。

## 当前建议

短期先做 AOI 模式候选发现，不要把“为什么只有几景”的开发诊断放到普通用户页面。  
生产仍默认走可靠可审查的栈，等 AOI 候选经过 baseline audit 和一次完整 Gamma 测试后，再把 AOI 模式设为推荐入口。

## 2026-05-28 实施记录

本轮已把“生产区域”接入 SBAS 候选发现链路：

- `/sbas-insar-production/stacks/discover` 支持 `discovery_mode=aoi`、`admin_region`、`aoi_bbox`、`min_aoi_coverage_ratio`、`min_common_overlap_ratio`；
- 后端可把行政区名称解析成 AOI 几何，使用 LT1 元数据 bbox 与 AOI 相交关系筛选场景；
- AOI 模式按观测几何分组，不再把 `center_bucket` 和 `receiving_station` 作为用户生产入口的硬拆分条件；
- 候选结果新增 `discovery_mode`、`aoi`、`common_overlap_ratio`、`aoi_overlap_ratio_mean/min/max`、`hard_group_fields`、`soft_group_fields`；
- `audit_stack` 和 `create_run` 已传递同一套 AOI 参数，确保发现、Manifest、Run 计划冻结的是同一候选序列；
- 前端“候选 SBAS 序列发现”改为“SBAS 生产区域”，用户只输入行政区并查看日期、景数、精轨、公共重叠和覆盖摘要；
- Run 列表不再显示 `center_bucket`，改显示行政区、平台和相对轨道。

当前前端只暴露行政区入口；bbox/GeoJSON 可作为下一步高级入口接入，但不应在普通页面展示开发诊断信息。
