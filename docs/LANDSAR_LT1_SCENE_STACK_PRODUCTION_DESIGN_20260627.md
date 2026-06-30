# LandSAR 陆探一号单景/多景生产链路探索与系统设计（2026-06-27）

## 1. 结论

LandSAR 不只承担 LT-1 D-InSAR。仓库内 LandSAR 工具和文档显示，LandSAR 已有一条明确的 LT-1 数据生产基础链：

```text
LT-1 源数据
  -> 100016 LT-1 数据导入
  -> 100206 LT-1 精密轨道导入
  -> Task_*/Input_Data
```

这条链可以支撑两类非 D-InSAR 生产能力：

1. **单景生产基础产品**：把一景 LT-1 源数据导入为 LandSAR 统一 `Input_Data` 格式，形成后续处理可复用的标准输入。
2. **多景生产基础产品**：把同轨道、同模式、同极化的一组 LT-1 场景导入并注入精密轨道，形成 LandSAR 时序栈 `Task_TS_*/Input_Data`。

当前系统的问题不是 LandSAR 没能力，而是这条能力没有被后端产品化：

- 现有后端只在 `LandsarEngine.run()` 的 D-InSAR 前置阶段调用 `100016`。
- `100206` 精轨导入和多景导入逻辑主要沉淀在 `third_party/LandSAR/lt1_import_gui.py`，还没有进入后端生产服务。
- 单景/多景导入结果还没有独立 catalog、运行记录和本机/集群双模式。
- 地理编码、正射、强度图这类“业务影像产品”还需要进一步验证 LandSAR 的 `180016`、`180044`、`200016`、`200046` 等 proID 参数链，不能直接把 `100016` 导入结果命名为正射影像。

因此第一阶段建议先产品化：

```text
landsar.import.lt1.scene.v1
landsar.import.lt1.stack.v1
landsar.orbit.lt1.v1
```

第二阶段再在真实样例参数和运行验证基础上开放：

```text
landsar.image.lt1.geocode.v1
landsar.image.lt1.ortho.v1
```

## 1.1 当前实现状态

截至 2026-06-27，系统已落地第一阶段的本机生产入口：

- 后端新增 `LANDSAR_LT1_IMPORT` 任务类型。
- 后端新增 `/api/landsar-lt1-production/*` API。
- 已按 LandSAR GUI 中的真实参数格式生成 `100016.txt` 与可选 `100206.txt`。
- 运行结果以 `catalog_name=lt1_landsar` 写入 `result_products/result_assets`。
- 前端“生产管理 -> 陆探一工作台 -> 陆探一影像生产”已可提交单景或多景 LT-1 源资产/目录导入任务。
- 选择 `source_product_assets` 中的 LT-1 源资产时，任务会先 materialize 到 `LANDSAR_WORK_ROOT/lt1_import_tasks/<task_id>/scenes/`，再把解包后的 scene 目录交给 LandSAR。
- `manifest.summary.source_asset_ids` 会记录源资产 id；资产台账和检索列表根据 `result_products.summary_json.source_asset_ids` 下发 `lt1_landsar_produced` 标识。
- 已经登记为 READY 的 LT-1 LandSAR 产品会在生产面板中禁止再次选择；后端任务执行前也会再次拒绝已生产源资产。

当前实现边界：

- 输入支持资产台账中的 LT-1 源资产，或人工指定的已 materialize/解包 LT-1 scene 目录；人工目录无法可靠反查源资产去重状态。
- 输出产品语义是 LandSAR `Input_Data` 标准化生产结果，不是正射影像、地理编码强度图或 D-InSAR 结果。
- 集群执行尚未接入这一任务类型；当前先采用主服务器本机 LandSAR 执行，并通过同一 manifest/catalog 结构为后续生产节点适配保留边界。

## 2. 已确认的 LandSAR proID 与参数链

### 2.1 已有可复用参数生成器

当前仓库中 `third_party/LandSAR/lt1_import_gui.py` 已有以下参数生成器：

| 能力 | proID | 现有函数 | 状态 |
| --- | --- | --- | --- |
| LT-1 数据导入 | `100016` | `generate_param_file()` | 已有，偏 master/slave 两目录 |
| LT-1 多景导入 | `100016` | `generate_lt1_multiscene_import_param_file()` | 已有，支持 `文件夹导入个数=N` |
| LT-1 精密轨道导入 | `100206` | `generate_orbit_param_file()` | 已有 |
| D-InSAR | `200014` | `generate_dinsar_param_file()` | 已接入后端 |
| SBAS 一体化 | `280039` | `generate_sbas_param_file()` | 已有参数生成器，运行授权/能力需另行验证 |
| PS-InSAR 分步 | `280000~280032` | `generate_psinsar_step_param_file()` | 已有参数生成器，运行链路需另行验证 |
| Stacking | `300001` | `generate_stacking_param_file()` | 已有参数生成器 |

这说明 `100016 + 100206` 不是猜测能力，已经有明确参数格式、GUI 调用方式和成功判定逻辑。

### 2.2 已确认的第一阶段链路

#### `100016`: LT-1 数据导入

用途：

- 从 LT-1 源 scene 目录读取 XML/TIFF。
- 输出 LandSAR 统一格式 `Input_Data`。
- 可用于单景，也可用于多景。

关键参数形态：

```text
卫星数据导入LT-1
处理编号       100016
设置数据导入形式_0文件夹导入_1数据导入  文件夹导入
读取成像参数文件_0否_1是 1
读取SLC数据文件_0否_1是 1
文件夹导入标识  TRUE
文件夹导入个数  N
文件夹1路径  <scene_dir_1>
...
设置数据导出目标路径_0原目录_1新目录  1
设置输出文件目录  <Input_Data>
```

成功判定：

- 日志包含 `module [LT-1数据导入] success`。
- 或 `console success`。
- 输出目录存在 `LT1*_SLC.xml` 和对应 `LT1*_SLC.tif`。

注意：

- 当前后端 `backend/app/dinsar_engines/landsar_engine.py` 的 `_generate_import_param_file()` 写死 `文件夹导入个数 2`，适合 D-InSAR pair 前置导入。
- 单景/多景产品化应改用 N 景参数生成逻辑，而不是复用 pair-shaped 参数。

#### `100206`: LT-1 精密轨道导入

用途：

- 对 `Input_Data` 中的 LT-1 XML 注入或关联精密轨道。
- 为后续 D-InSAR、SBAS、PS、Stacking 或影像处理提供已校正输入。

关键参数形态：

```text
LT-1精密轨道数据导入
处理编号       100206
输入数据个数  N
输入数据1的xml  <xml_path_1>
...
输入精密轨道数据文件夹  <orbit_dir>
选择XML文件保存方式  0
设置数据导出目录形式0原目录1新目录  0
输出更新处理后数据目录  <Input_Data>
```

成功判定：

- 日志包含 `module [LT-1精密轨道数据导入] success`。
- 或日志同时包含 `精密轨道` 和 `success`。
- 或 `console success`。

### 2.3 候选但未验证的影像产品链

LandSAR 文档列出了以下与单景影像产品相关的 proID：

| proID | 功能 | 当前判断 |
| --- | --- | --- |
| `180044` | 多视处理 | 可能用于单景强度/幅度产品前置，但参数格式未在后端沉淀 |
| `180016` | 地理编码 | 可能用于单景地理编码产品，但缺少样例参数 |
| `180070` | SLC 处理 | 可能用于单景 SLC 派生处理，但语义需验证 |
| `200016` | 地理编码（流程） | 可能是一体化地理编码流程，但缺少样例参数 |
| `200046` | SLC 处理（流程） | 可能是一体化 SLC 处理流程，但缺少样例参数 |
| `280032` | SLC 数据多视 | PS/MTInSAR 链路中的多视步骤，不应直接等同于通用单景影像生产 |

这些 proID 不能直接进入正式 UI。需要先从 LandSAR GUI 生成真实参数文件，拿一景样本跑通，再决定产品定义。

## 3. 产品能力分层

### 3.1 第一层：导入型生产产品

这是近期可落地层。

#### `landsar.import.lt1.scene.v1`

```text
输入：
  LT-1 单景源压缩包或 materialized scene 目录

处理：
  100016 LT-1 数据导入
  可选 100206 LT-1 精密轨道导入

输出：
  Input_Data/
    LT1*_SLC.xml
    LT1*_SLC.tif
    *.thumb.jpg
  params/
    100016.txt
    100206.txt
  logs/
    100016_console.log
    100206_console.log
  import_manifest.json

catalog:
  catalog_name = lt1_landsar
  product_family = lt1_scene_import
  product_type = landsar_input_data
```

产品语义：

- 这是 LandSAR 输入标准化产品。
- 不是正射影像。
- 不是地理编码强度图。
- 可作为 D-InSAR、SBAS、PS、后续影像产品的上游缓存。

#### `landsar.import.lt1.stack.v1`

```text
输入：
  同一轨道/模式/极化/方向的一组 LT-1 scene

处理：
  100016 LT-1 多景导入
  100206 LT-1 精密轨道导入

输出：
  Task_TS_<track>_<pol>_<orbit_direction>_<start>_<end>_<count>/
    Input_Data/
    Output_Data/
    stack_import_manifest.json
    时序数据构建报告.txt

catalog:
  catalog_name = lt1_landsar
  product_family = lt1_stack_import
  product_type = landsar_timeseries_input_data
```

产品语义：

- 这是 LandSAR 多景时序输入产品。
- 可作为 PS/SBAS/MT-InSAR 的上游准备结果。
- 不直接表示形变结果。

### 3.2 第二层：影像型生产产品

这层需要先验证 LandSAR 影像处理 proID。

候选 profile：

```text
landsar.image.lt1.multilook.v1
landsar.image.lt1.geocode.v1
landsar.image.lt1.ortho.v1
```

可能链路：

```text
landsar.import.lt1.scene.v1
  -> 180044 多视处理
  -> 180016 地理编码
```

或：

```text
landsar.import.lt1.scene.v1
  -> 200046 SLC 处理（流程）
  -> 200016 地理编码（流程）
```

当前必须标记为待验证：

- 缺少真实参数文件。
- 缺少真实输出样例。
- 缺少对输出单位、坐标系、辐射定标、nodata、分辨率的确认。
- 缺少成功判定和错误摘要规则。

## 4. 系统架构设计

### 4.1 后端模块

建议新增独立模块，不放进 `dinsar_engines`：

```text
backend/app/landsar_lt1/
  contracts.py
  param_files.py
  runtime.py
  discovery.py
  scene_import_adapter.py
  stack_import_adapter.py
  result_package.py
```

职责：

- `contracts.py`：定义 scene/stack 输入 manifest、结果 manifest、能力描述。
- `param_files.py`：沉淀 `100016`、`100206` 参数文件生成器，不再依赖 GUI 代码。
- `runtime.py`：统一调用 `InSAR_Console.exe`、日志捕获、超时、成功判定、错误摘要。
- `discovery.py`：从资产表或 materialized 目录解析 LT-1 scene，按轨道/极化/日期分组。
- `scene_import_adapter.py`：执行 `landsar.import.lt1.scene.v1`。
- `stack_import_adapter.py`：执行 `landsar.import.lt1.stack.v1`。
- `result_package.py`：生成标准发布包、manifest、current 指针。

LandSAR D-InSAR 当前已有的 runtime 检查、授权服务启动、DLL 校验逻辑可以抽取共用，但不要把单景/多景生产继续塞进 `LandsarEngine.run()`。

### 4.2 调度模型

建议走统一生产节点协议：

```text
主服务器创建 production run
  -> 每个 scene 或 stack group 生成 production item
  -> 本机 worker 或远端 production node 领取
  -> adapter 执行 LandSAR
  -> 上传/登记标准产品包
```

执行模式：

| 模式 | 说明 |
| --- | --- |
| `local` | 主服务器本机执行 |
| `cluster` | 指定远端生产节点执行 |
| `auto` | 按节点能力、负载、数据缓存选择 |

节点能力：

```json
{
  "capabilities": [
    "landsar.import.lt1.scene.v1",
    "landsar.import.lt1.stack.v1",
    "landsar.orbit.lt1.v1"
  ]
}
```

### 4.3 运行记录模型

现有 `dinsar_production_runs` 虽然有 `product_family` 字段，但表名、字段和 item 语义都偏 pair。为了避免技术债，不建议把单景/多景陆探生产继续塞进 D-InSAR run 表。

推荐新增通用生产表：

```text
production_runs
  run_id
  product_family
  processor_code
  profile_code
  execution_mode
  source_scope
  status
  total_items
  completed_items
  failed_items
  params_json

production_run_items
  item_id
  run_id
  item_key
  item_type              # scene / stack_group
  source_asset_ids_json
  source_paths_json
  target_key             # scene_uid / stack_key
  status
  latest_run_key
  latest_manifest_path
  metrics_json

production_executions
  execution_id
  run_id
  item_id
  node_id
  run_key
  status
  output_dir
  manifest_path
  log_path
  error_message
```

也可以短期复用 `system_jobs` 做执行队列，但正式 UI、重试、批次统计、集群调度和结果追溯需要上述通用运行表。

### 4.4 结果 catalog

现有 `result_products` / `result_assets` 表可以承载单景/多景产品，因为它们已经有：

- `catalog_name`
- `product_family`
- `product_type`
- `engine_code`
- `processor_code`
- `profile_code`
- `stack_key`
- `summary_json`
- `assets`

但现有 `result_catalog_service._load_manifest()` 明确拒绝非 `dinsar` 的 manifest。需要拆出通用结果登记服务：

```text
result_package_registry
  register_manifest(manifest_path)
  validate_manifest(product_family)
  upsert_result_product()
  upsert_result_assets()
```

LandSAR LT-1 生产建议使用：

```text
catalog_name   = lt1_landsar
product_family = lt1_scene_import / lt1_stack_import
product_type   = landsar_input_data / landsar_timeseries_input_data
engine_code    = landsar
processor_code = landsar.import.lt1.scene / landsar.import.lt1.stack
profile_code   = landsar.import.lt1.scene.v1 / landsar.import.lt1.stack.v1
```

## 5. 输入与分组设计

### 5.1 单景输入

来源：

- `source_product_assets` 中的 `LT1_ARCHIVE`。
- 已 materialized 的 LT-1 scene 目录。
- 人工指定的受控服务器目录。

流程：

```text
source asset
  -> materialize scene
  -> run 100016
  -> optional run 100206
  -> publish result package
```

### 5.2 多景输入

分组键建议：

```text
satellite_family = LT1
track / orbit number
imaging_mode
polarization
orbit_direction
product_type = SLC
admin/aoi 或用户选择范围
date range
```

多景生产不应简单把用户勾选的所有 LT-1 都丢给 LandSAR。需要先做分组预检：

- 是否同一轨道或可构成同一时序栈。
- 是否同一极化。
- 是否同一成像模式。
- 日期是否可解析。
- 是否都有源包。
- 精轨是否匹配。
- scene footprint 是否满足业务区域覆盖要求。

输出 `stack_key` 示例：

```text
lt1_stack_<track>_<pol>_<orbit_direction>_<start_date>_<end_date>_<scene_count>_<hash>
```

## 6. 发布目录设计

```text
D:\production_results\lt1_landsar\
  scene_import\
    <scene_uid>\
      current\
        landsar.import.lt1.scene.v1.json
      runs\
        <run_key>\
          manifest.json
          execution_manifest.json
          native\
            Input_Data\
            params\
            logs\
          assets\
            input_data\
            thumb\
            metadata\

  stack_import\
    <stack_key>\
      current\
        landsar.import.lt1.stack.v1.json
      runs\
        <run_key>\
          manifest.json
          execution_manifest.json
          native\
            Task_TS_...\Input_Data\
            Task_TS_...\Output_Data\
            params\
            logs\
          assets\
            input_data_manifest.json
            stack_report.txt
            scene_index.json
```

`native/` 保留 LandSAR 原始结构，`assets/` 放系统标准化索引文件和必要缩略图。不要把完整 `Input_Data` 再复制两份；标准资产层可以用 manifest 指向 native 内的相对路径。

## 7. API 与前端入口

### 7.1 API

建议新增：

```text
GET  /api/landsar-lt1-production/capabilities
POST /api/landsar-lt1-production/preview
POST /api/landsar-lt1-production/run
GET  /api/landsar-lt1-production/products
GET  /api/landsar-lt1-production/products/{product_db_id}
GET  /api/landsar-lt1-production/products/{product_db_id}/assets/{asset_id}
```

`preview` 必须先返回可执行性：

- 选中 scene 数。
- 分组结果。
- 缺失源包。
- 缺失精轨。
- 预计输出目录。
- 是否可本机执行。
- 是否有集群节点支持。
- 源资产是否已经存在 READY 状态的 LT-1 LandSAR 产品。

### 7.2 前端

生产管理里将 `陆探一生产占位` 改成实际工作台：

```text
陆探一生产
  ├─ 单景导入
  ├─ 多景时序输入构建
  ├─ 运行记录
  └─ 产品结果
```

第一版按钮只开放：

- 预检。
- 提交单景导入。
- 提交多景导入。
- 查看日志。
- 打开结果目录。
- 查看 catalog 资产。

不要第一版就开放：

- 正射产品。
- 地理编码强度图。
- PS/SBAS 自动执行。
- Stacking 自动执行。

这些应建立在第一层 `Input_Data` 产品稳定之后。

## 8. 集群化设计

单景/多景导入非常适合纳入生产节点：

- 输入大但结构明确。
- 输出可通过 manifest 回传。
- 不需要主服务器承担长时间 LandSAR 进程。
- `Input_Data` 可作为远端缓存，后续 D-InSAR/SBAS 复用。

远端节点能力：

```text
landsar.import.lt1.scene.v1
landsar.import.lt1.stack.v1
landsar.orbit.lt1.v1
```

第一版并发建议：

- LandSAR 进程并发：每节点 1。
- 多 scene 导入内部由 LandSAR 控制，不在外层并发拆太碎。
- 多个单景任务可以排队，但不要同节点同时启动多个 `InSAR_Console.exe`，除非实测证明安全。

数据搬运：

- 主服务器给 input manifest。
- worker 下载源包或 materialized scene。
- worker 执行 `100016/100206`。
- worker 上传 manifest 和必要产物。
- 大体量 `Input_Data` 是否全量回传可配置：第一版建议回传完整受管产品；后续可做远端缓存引用。

## 9. 实施路线

### 阶段 1：参数链固化

- 从 `third_party/LandSAR/lt1_import_gui.py` 抽取 `100016`、`100206` 参数生成逻辑。
- 增加参数文件 golden tests。
- 不接 UI，不跑真实任务。

### 阶段 2：本机单景导入

- 实现 `landsar.import.lt1.scene.v1` adapter。
- 输入一个 LT-1 scene。
- 执行 `100016`。
- 可选执行 `100206`。
- 生成标准 manifest。
- 登记到 `result_products`，catalog 为 `lt1_landsar`。

### 阶段 3：本机多景导入

- 实现分组预检。
- 实现 `landsar.import.lt1.stack.v1` adapter。
- 复用 `generate_lt1_multiscene_import_param_file()` 语义。
- 执行 `100016 -> 100206`。
- 发布 `Task_TS_*/Input_Data` 产品。

### 阶段 4：生产节点接入

- 把 scene/stack import adapter 接入生产节点协议。
- 远端节点上报能力。
- 实现输入下载、结果回传、日志上报。

### 阶段 5：影像产品验证

- 用 LandSAR GUI 对单景生成多视、地理编码或正射产品。
- 收集真实参数文件。
- 确认 proID、输出命名、坐标系、单位和成功判定。
- 再实现 `landsar.image.lt1.*`。

## 10. 需要运行验证的问题

1. `100016` 单景导入时 `文件夹导入个数=1` 是否被当前 LandSAR runtime 接受。
2. `100016` 多景导入最大稳定 scene 数是多少。
3. `100206` 对已导入 XML 是原地改写还是复制输出。
4. `100206` 精轨注入后 XML 中可验证字段是什么。
5. `180044 -> 180016` 是否能独立从单景 `Input_Data` 生成地理编码影像。
6. `200046/200016` 是否比单步 `180xxx` 更适合作为正式影像产品链。
7. LandSAR 同一台机器是否允许多个导入任务并发。
8. 导入输出是否可跨机器复用，还是强依赖本机路径。

## 11. 近期不建议做的事

- 不建议把 `100016` 单景导入继续藏在 D-InSAR 前置步骤里。
- 不建议把 `100016` 输出命名为正射或地理编码产品。
- 不建议把单景/多景生产塞进 `dinsar_production_runs`。
- 不建议在未验证 `180016/200016` 参数前开放“LandSAR 正射生产”按钮。
- 不建议先做复杂 UI。应先做 adapter、manifest、catalog、真实样例验证。
