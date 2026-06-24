# Sentinel-1 Gamma SBAS 无拼接接入设计

最后更新：2026-06-02

本文设计 `sbas-insar-production` 对 Sentinel-1 Gamma SBAS 的稳定接入方案。目标是复用现有 SBAS 生产管理框架，但不影响现有 LT-1 Gamma SBAS 链路；允许传感器专用逻辑冗余实现，以稳定性和可回退为第一优先级。

## 1. 术语说明

本文目标是 Sentinel-1 SAR 数据，不是 Sentinel-2。

Sentinel-2 是光学卫星，不具备 SAR 干涉相位，不能迁移到 Gamma SBAS-InSAR。如果后续业务说“哨兵2”，需要先确认是不是口误；系统实现应使用 `Sentinel-1`、`S1`、`s1_gamma_sbas` 这些明确命名，避免把 Sentinel-2 暗含进 InSAR 链路。

## 2. 设计结论

当前 Gamma SBAS 核心是 LT-1 专用实现，包含 LT-1 目录扫描、LT-1 元数据解析、LT-1 精轨脚本和 `par_LT1_SLC` 导入脚本。Sentinel-1 不应在这条链上硬改。

新增 Sentinel-1 支持时采用 profile 并列方案：

```text
lt1_gamma_sbas  # 现有链路，保持行为不变
s1_gamma_sbas   # 新增链路，独立发现、独立脚本、独立校验
```

复用内容：

- API 路由和生产 Run 生命周期。
- Stack discovery / audit / create run 的外层流程。
- Workflow/job 调度、日志、状态机。
- Gamma 环境注入和 WSL runtime 管理。
- 产品发布、catalog、预览、监测点、下载接口。

不复用或只抽象复用的内容：

- 不复用 LT-1 场景扫描。
- 不复用 LT-1 SLC 导入脚本。
- 不复用 LT-1 精轨处理脚本。
- 不复用 LT-1 专家文档中的传感器专有命令。
- 不在 LT-1 脚本模板中加入 Sentinel-1 分支。

## 3. 目标与非目标

### 3.1 目标

1. 新增 Sentinel-1 Gamma SBAS profile，入口可发现 Sentinel-1 候选 stack。
2. Sentinel-1 使用 ZIP/SAFE + EOF 资产，不走 LT-1 `tiff/meta.xml/txt orbit` 逻辑。
3. 不支持拼接。第一阶段只支持单轨、同向、同 relative orbit、同 acquisition mode、同 polarization、同 subswath、同 burst 或同一稳定 burst key 的 stack。
4. 对需要拼接才能覆盖 AOI 的数据，系统不自动拼接，改为拆成多个独立候选 stack 或直接标记为 `NOT_READY_REQUIRES_STITCHING`。
5. LT-1 现有生产效果不变，默认入口仍可继续跑现有 LT-1 数据。
6. Sentinel-1 先做严格、保守、可解释的生产链，允许代码冗余，避免为了共用而引入隐性耦合。

### 3.2 非目标

1. 不支持 Sentinel-2 光学时序。
2. 不支持跨轨、跨 relative orbit、升降轨混合。
3. 不支持跨 swath 拼接。
4. 不支持跨 burst 拼接。
5. 不支持相邻 Sentinel-1 slice/frame 自动拼接。
6. 不把多个独立 Sentinel-1 SBAS 结果镶嵌成一张最终产品。
7. 不改现有 LT-1 专家文档脚本的含义和输出。

## 4. 当前 LT-1 链路中不能直接复用的点

当前实现里有多处 LT-1 硬编码：

```text
_iter_lt1_scene_dirs
_looks_like_lt1_scene_dir
_parse_lt1_scene
par_LT1_SLC
LT1_precision_orbit.py
Prepare LT1 SLCs
LT1_GAMMA_SBAS_逐命令处理流程.docx
layout_source = LT1_GAMMA_SBAS_expert_document
allowed_operations = lt1_gamma_sbas_workflow / lt1_gamma_sbas_step
```

这些不应扩展成大量 `if sensor == "S1"` 分支。否则 LT-1 的稳定链路会被 Sentinel-1 的 TOPS/burst 复杂性污染。

## 5. 总体架构

新增一个传感器 profile 适配层。现有 `SbasInsarProductionService` 保持外层协调角色，传感器专有逻辑下沉到 adapter。

建议模块：

```text
backend/app/services/sbas_profiles/
  __init__.py
  base.py
  lt1_gamma_sbas_profile.py
  s1_gamma_sbas_profile.py

backend/app/services/sbas_script_templates/
  lt1_gamma_sbas_scripts.py
  s1_gamma_sbas_scripts.py
```

核心接口建议：

```python
class GammaSbasProfile:
    profile_code: str
    sensor_family: str

    def discover_scenes(source_roots, orbit_roots, filters) -> SceneDiscoveryResult: ...
    def group_stack_candidates(scenes, aoi, options) -> list[StackCandidate]: ...
    def audit_stack(stack_id, context) -> StackAudit: ...
    def build_run_manifest(stack, options) -> dict: ...
    def build_workflow_manifest(run_dir, run_manifest, options) -> dict: ...
    def materialize_scripts(run_dir, workflow_manifest) -> list[ScriptArtifact]: ...
    def validate_no_stitch_policy(stack) -> list[Issue]: ...
```

LT-1 profile 可以先只是封装现有函数，不改变行为。Sentinel-1 profile 独立实现。

## 6. API 和配置

### 6.1 API 参数

现有接口保持兼容，新增可选 `profile_code`：

```json
{
  "profile_code": "lt1_gamma_sbas",
  "source_roots": [],
  "orbit_roots": [],
  "admin_region": "",
  "discovery_mode": "strict",
  "aoi_bbox": null
}
```

默认值为 `lt1_gamma_sbas`。这样老前端和老调用不受影响。

新增 Sentinel-1 时使用：

```json
{
  "profile_code": "s1_gamma_sbas",
  "source_roots": ["D:\\Sentinel1_Image_Pool_ZIP"],
  "orbit_roots": ["D:\\Sentinel1_Orbit_Pool"],
  "admin_region": "..."
}
```

### 6.2 配置项

新增配置建议：

```text
GAMMA_SBAS_PROFILES=lt1_gamma_sbas,s1_gamma_sbas
GAMMA_SBAS_DEFAULT_PROFILE=lt1_gamma_sbas

GAMMA_SBAS_S1_ENABLED=false
GAMMA_SBAS_S1_SOURCE_ROOTS=D:\Sentinel1_Image_Pool_ZIP
GAMMA_SBAS_S1_ORBIT_ROOTS=D:\Sentinel1_Orbit_Pool
GAMMA_SBAS_S1_NO_STITCH=true
GAMMA_SBAS_S1_MIN_SCENES=8
GAMMA_SBAS_S1_DEFAULT_SUBSWATH=IW2
GAMMA_SBAS_S1_DEFAULT_POLARIZATION=VV
```

`GAMMA_SBAS_S1_ENABLED` 初始应为 `false`。完成样本验证后再开放。

### 6.3 Runtime 白名单

`wsl_runtime_registry.py` 需要新增 operation：

```text
s1_gamma_sbas_workflow
s1_gamma_sbas_step
```

不要复用 `lt1_gamma_sbas_workflow` 的 operation 名称。

## 7. Sentinel-1 数据发现与分组

### 7.1 数据来源

优先复用资产库存层：

- Sentinel-1 ZIP / SAFE 源产品资产。
- Sentinel-1 EOF 精密轨道资产。
- `logical_product_uid` 关联 ZIP 和 SAFE。
- EOF 使用 validity window 匹配 scene。

如果资产库存不可用，S1 profile 可提供只读目录扫描兜底，但目录扫描结果必须写入同样的 `SceneDescriptor` 结构。

### 7.2 SceneDescriptor

Sentinel-1 scene 描述结构至少包含：

```json
{
  "sensor_family": "S1",
  "satellite": "S1A",
  "product_type": "SLC",
  "acquisition_mode": "IW",
  "polarization": "VV",
  "orbit_direction": "ASCENDING",
  "relative_orbit": "40",
  "absolute_orbit": "...",
  "start_time_utc": "...",
  "stop_time_utc": "...",
  "source_archive_path": "...zip",
  "safe_dir": "...SAFE",
  "manifest_path": "...manifest.safe",
  "orbit_file_path": "...EOF",
  "footprint": {},
  "available_subswaths": ["IW1", "IW2", "IW3"],
  "burst_index_summary": {}
}
```

### 7.3 Stack 分组规则

Sentinel-1 stack candidate 必须满足：

1. 同 `sensor_family = S1`。
2. 同 `acquisition_mode = IW`。
3. 同 `orbit_direction`。
4. 同 `relative_orbit`。
5. 同 polarization，第一阶段建议只支持 `VV`。
6. 所有 scene 都有匹配 EOF。
7. 所有 scene 与 AOI 有交集。
8. 能解析出共同 subswath 和 burst key。
9. 不需要跨 swath/burst/相邻 slice 拼接。

不满足第 8、9 条时，不应尝试自动修复，直接输出：

```text
status = NOT_READY_REQUIRES_STITCHING
```

或者拆成多个候选：

```text
s1_rel040_asc_iw2_burst_013
s1_rel040_asc_iw2_burst_014
```

每个候选独立生产，不做最终合成。

## 8. 无拼接策略

本文中的“不支持拼接”定义如下：

1. 不把相邻 Sentinel-1 产品 slice 合成一个输入。
2. 不把多个 subswath 合成一个输入。
3. 不把多个 burst 的结果合成一个输出。
4. 不把多个独立 SBAS run 的 GeoTIFF 合成一个产品。

第一阶段最稳策略是 `single_subswath_single_burst`：

```text
stack geometry = relative orbit + direction + IW subswath + burst key
```

如果 AOI 跨多个 burst，系统给出多个独立候选。用户可以分别生产和查看，但系统不拼接。

这样牺牲覆盖范围，但能显著降低 TOPS 拼接、边界相位、burst overlap 和几何一致性的风险。

## 9. Sentinel-1 Workflow 阶段

新增 Sentinel-1 专用 workflow 模板，不修改 LT-1 模板。

建议阶段：

```text
01_workspace_data
02_import_s1_slc
03_select_single_burst
04_reference_mli
05_baseline_audit
06_coregister_scenes
07_rdc_dem
08_diff_network
09_filter_unwrap
10_detrend_atm
11_sbas_inversion
12_publish_products
13_monitor_points
```

与 LT-1 的主要差异在前半段：

- LT-1：`par_LT1_SLC` + LT-1 txt 精轨。
- S1：ZIP/SAFE + EOF + TOPS/burst 选择。

后半段可复用 Gamma DIFF/IPTA 的思想，但脚本仍建议独立生成，避免 LT-1 和 S1 共用同一个 shell 模板。

## 10. 脚本隔离设计

LT-1 当前脚本路径保持不变：

```text
scripts/01_workspace_data.sh
scripts/02_import_lt1_slc.sh
...
```

Sentinel-1 使用独立命名：

```text
scripts/s1/01_workspace_data.sh
scripts/s1/02_import_s1_slc.sh
scripts/s1/03_select_single_burst.sh
scripts/s1/04_reference_mli.sh
...
```

`run_manifest.json` 中明确记录：

```json
{
  "profile_code": "s1_gamma_sbas",
  "sensor_family": "S1",
  "stitching_policy": "disabled",
  "stack_geometry_policy": "single_subswath_single_burst"
}
```

## 11. 产物与 Catalog

Sentinel-1 产物仍进入 SBAS catalog，但必须带 profile 和 sensor 标签：

```json
{
  "catalog_name": "sbas_insar",
  "product_family": "timeseries",
  "processor_code": "gamma_ipta_sbas",
  "profile_code": "s1_gamma_sbas",
  "sensor_family": "S1"
}
```

核心资产仍保持现有约定：

```text
publish/geotiff/los_rate_toward_m_per_year.tif
publish/geotiff/los_rate_away_m_per_year.tif
publish/geotiff/los_sigma_m_per_year.tif
publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png
publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png
publish/vectors/los_rate_points.geojson.gz
publish/monitor_points/*
```

产品目录建议按 profile 分层，避免和 LT-1 混在一起：

```text
D:\production_results\timeseries\sbas
|-- lt1_gamma_sbas
|   `-- <run_id>
`-- s1_gamma_sbas
    `-- <run_id>
```

如果短期不改目录，也必须在 manifest/catalog 中保留 `profile_code`，前端筛选时不能只看 product family。

## 12. 前端设计

`SBAS-InSAR Production` 增加 profile 选择：

```text
数据类型:
[ LT-1 Gamma SBAS ] [ Sentinel-1 Gamma SBAS ]
```

默认仍是 LT-1。

Sentinel-1 页面提示：

```text
当前 Sentinel-1 Gamma SBAS 使用无拼接策略。
仅支持同轨同向、同 relative orbit、同 subswath、同 burst 的稳定候选序列。
跨 burst / 跨 subswath / 相邻 slice 自动拼接暂不支持。
```

候选列表增加字段：

- sensor family
- satellite
- acquisition mode
- relative orbit
- orbit direction
- polarization
- subswath
- burst key
- stitching policy
- missing EOF count

如果候选需要拼接，按钮置灰，原因显示为 `需要拼接，当前策略不支持`。

## 13. 稳定性护栏

### 13.1 不影响 LT-1 的护栏

1. 默认 profile 不变。
2. LT-1 常量、模板、脚本文件名不改。
3. Sentinel-1 代码放到新 adapter 和新脚本模板中。
4. `GAMMA_SBAS_S1_ENABLED=false` 时前端不展示 S1。
5. LT-1 的单元测试和脚本快照测试必须先通过。

### 13.2 Sentinel-1 提交前校验

提交生产前必须全部通过：

```text
source ZIP/SAFE exists
EOF exists for every scene
same relative orbit
same orbit direction
same acquisition mode
same polarization
same subswath
same burst key
scene count >= minimum
no stitching required
DEM coverage exists
AOI intersects all selected scenes
```

任一失败，禁止提交 workflow job。

### 13.3 执行阶段校验

每阶段输出必须有 manifest 记录：

```text
stage_status.json
stage stdout/stderr log
expected outputs
missing outputs
quality flags
```

Sentinel-1 首批样本不自动发布到正式 catalog。建议先生成 run artifact，人工确认后再开启 publish。

## 14. 实施阶段

### Phase 0：文档和开关

- 新增本文档。
- 新增配置项设计。
- S1 默认关闭。

### Phase 1：Profile 框架拆分

- 新增 `GammaSbasProfile` 基类。
- 当前 LT-1 逻辑包一层 `Lt1GammaSbasProfile`。
- 保证 LT-1 行为不变。
- 增加 LT-1 脚本快照测试。

### Phase 2：Sentinel-1 Discovery

- 实现 `S1GammaSbasProfile.discover_scenes`。
- 优先读取 source/orbit asset inventory。
- 输出 S1 stack candidates。
- 对需要拼接的候选输出 `NOT_READY_REQUIRES_STITCHING`。

### Phase 3：Sentinel-1 Planning Run

- 能创建 `s1_gamma_sbas` planning run。
- 能生成 `run_manifest.json`、`stack_manifest.json`、`gamma_command_manifest.json`。
- 只生成脚本，不执行 Gamma。

### Phase 4：Sentinel-1 Script Dry Run

- 生成 `scripts/s1/*.sh`。
- 在样本数据上执行到导入和 reference MLI。
- 验证 no-stitch 限制是否真实有效。

### Phase 5：Sentinel-1 Workflow 执行

- 执行 coreg、RDC DEM、diff、unwrap、detrend、IPTA。
- 先不自动发布正式 catalog。
- 输出质量报告和人工验收包。

### Phase 6：Catalog 发布和前端展示

- Sentinel-1 样本通过后开启 publish。
- 前端结果页支持 profile/sensor 筛选。
- 产品详情显示 `stitching_policy=disabled`。

## 15. 验收标准

### 15.1 LT-1 回归

1. LT-1 discovery 结果不变。
2. LT-1 run manifest 关键字段不变。
3. LT-1 脚本输出与改造前一致。
4. LT-1 样本能继续跑通。
5. LT-1 catalog 结果不受 S1 profile 影响。

### 15.2 Sentinel-1 最小可用

1. 能发现 Sentinel-1 候选 stack。
2. 能绑定 EOF。
3. 能拒绝需要拼接的候选。
4. 能生成 S1 planning run 和脚本。
5. 能在一个单 subswath / 单 burst 样本上跑通 workflow。
6. 输出标准 SBAS 产品资产。
7. Catalog 中能按 `profile_code=s1_gamma_sbas` 查询。

## 16. 风险与取舍

### 16.1 覆盖范围变小

不拼接意味着 AOI 覆盖能力会变弱。跨 burst 或跨 subswath 的区域不会自动合成，只能拆成多个独立结果查看。

这是稳定性优先的取舍。

### 16.2 Sentinel-1 TOPS 复杂度高

Sentinel-1 TOPS 的 burst、Doppler、coregistration 对脚本稳定性要求高。第一阶段不应追求通用覆盖，应先让单一稳定样本跑通。

### 16.3 Gamma 命令版本差异

Sentinel-1 导入和 TOPS 处理命令需要以生产服务器安装的 Gamma 版本为准。脚本模板中所有 Sentinel-1 命令必须经过样本数据验证后再开放。

### 16.4 不共享脚本导致代码冗余

本设计接受冗余。相比强行抽象共用，冗余脚本更容易保证 LT-1 不被影响，也更容易单独回滚 Sentinel-1。

## 17. 回滚策略

如果 Sentinel-1 profile 出现问题：

1. 设置 `GAMMA_SBAS_S1_ENABLED=false`。
2. 前端隐藏 Sentinel-1 profile。
3. Runtime 白名单保留不影响 LT-1。
4. 已生成的 S1 run 保留为实验记录，不进入正式 catalog。
5. LT-1 profile 不需要回滚。

## 18. 推荐下一步

1. 先实现 profile 拆分，但不改 LT-1 行为。
2. 准备一个 Sentinel-1 最小样本集：同 relative orbit、同方向、同 polarization、同 subswath、同 burst key，至少 8 景。
3. 只实现 discovery + planning run。
4. 人工核对 `gamma_command_manifest.json` 和 `scripts/s1/*.sh`。
5. 再进入 Gamma 实际执行阶段。
