# Sentinel-1 数据管理改造规划

日期：2026-05-10

本文只规划“让系统能管理哨兵一号 Sentinel-1 数据”的改造范围，不记录已完成实现。当前建议把目标分成两层：先支持 Sentinel-1 源数据入库、检索、预览、配对和分发；真正进入 ISCE2 / PyINT / SARscape 的 Sentinel-1 自动生产链路，作为后续独立阶段处理。

## 1. 目标边界

本轮建议做：

1. 扫描并登记 Sentinel-1 SLC `.SAFE` 解包目录。
2. 解析 Sentinel-1 基础元数据，包括卫星、成像模式、极化、起止时间、绝对轨道、相对轨道、升降轨、footprint。
3. 管理 Sentinel-1 精密轨道 `.EOF` 文件，并把影像按 acquisition time 匹配到覆盖该时间窗的轨道文件。
4. 在源数据检索、配对规划、任务批次和数据分发中正确显示和使用 Sentinel-1。
5. 对 Sentinel-1 配对增加必要的轻量约束，避免只靠 footprint 交叠产生不科学的候选对。

本轮不建议做：

1. 不把 Sentinel-1 自动送入现有 LT1 专用 ISCE2 / PyINT 流水线。
2. 不做 burst 级精确公共覆盖计算。
3. 不计算真实垂直基线，不把 footprint 中心距伪装成 SAR 空间基线。
4. 不强制支持 `.SAFE.zip` 直接入库；优先支持已解包的 `.SAFE` 目录，zip 可作为后续增强。

## 2. 当前代码现状

已有基础：

- [backend/app/utils.py](../backend/app/utils.py) 已经有 `parse_s1_radar_filename()` 和 `parse_s1_orbit_filename()` 的雏形。
- [backend/app/services/data_service.py](../backend/app/services/data_service.py) 的 `scan_radar_data()` 是通用源数据扫描入口，理论上可以扫描 LT1 / S1 / GF3。
- `radar_data` 已有 `satellite`、`satellite_family`、`imaging_date`、`imaging_mode`、`polarization`、`orbit_direction`、`product_type`、`source_product_token`、`has_orbit_data`、`orbit_file_path`、`geom`、`coverage_polygon` 等通用字段。
- [backend/app/services/pairing_cache_service.py](../backend/app/services/pairing_cache_service.py) 已经把 `S1A/S1B` 归为 `S1` family，配对层具备同卫星族筛选能力。
- [backend/app/copier.py](../backend/app/copier.py) 分发逻辑是复制原始产品目录，不再依赖 `envi_import`，这对 `.SAFE` 目录是有利的。

主要缺口：

- Sentinel-1 `.SAFE` 的关键元数据主要在 `manifest.safe` 和 `annotation/*.xml`，当前 `find_xml_file()` / `parse_xml_metadata()` 更偏 LT1 风格 XML，不能可靠解析 S1 footprint、升降轨、相对轨道。
- 当前精轨模块 [backend/app/services/orbit_converter.py](../backend/app/services/orbit_converter.py) 基本写死 LT1 `LT1A_GpsData_GAS_C_YYYYMMDD.txt`，并会同步/转换到 ENVI、ISCE2 池；Sentinel-1 `.EOF` 不能走这套转换逻辑。
- 当前配对缓存只要求同升降轨、同 look direction、footprint 相交、同卫星族、同模式/极化等。对 Sentinel-1 来说还缺少“同相对轨道/同轨道方向/同 beam mode/必要时同 slice 或同 burst 覆盖”的约束。
- 前端卫星组 [frontend/src/config/appConstants.js](../frontend/src/config/appConstants.js) 目前只有 LT-1、GF-3；配对默认 `allowed_satellites` 是 `LT1A/LT1B`，导入 S1 后如果不改，会默认把 S1 排除。
- 现有 ISCE2 / PyINT 生产脚本明显是 LT1 专用，不能因为管理了 S1 数据就默认允许一键生产。

## 3. Sentinel-1 文件名能提供的信息

标准 Sentinel-1 SLC SAFE 名称通常类似：

```text
S1A_IW_SLC__1SDV_20250101T104105_20250101T104132_XXXXXX_YYYYYY_ZZZZ.SAFE
```

可直接从文件名得到：

- `satellite`: `S1A` / `S1B`
- `satellite_family`: `S1`
- `imaging_mode`: `IW` / `EW` / `SM`
- `product_type`: `SLC`
- `product_level`: 可从 `1SDV` 中解析出 level 1
- `polarization`: `DV`、`DH`、`SV`、`SH`
- `acquisition_start_time_utc`
- `acquisition_stop_time_utc`
- `imaging_date`
- `absolute_orbit`，即文件名中的 6 位轨道号
- datatake id / product unique id

文件名通常不能可靠提供：

- footprint
- orbit direction，升轨/降轨
- relative orbit / track
- look direction
- burst 范围
- 精轨覆盖关系

因此只靠文件名可以完成“粗入库”，但要做可靠配对，至少还要读取 `manifest.safe` 或 annotation XML。

## 4. 建议新增的元数据模型

为了控制工作量，不建议把 Sentinel-1 所有元数据拆成大量列。建议采用“少量关键列 + JSON 扩展”的方式。

建议新增字段：

- `acquisition_start_time_utc`
- `acquisition_stop_time_utc`
- `relative_orbit`
- `absolute_orbit`
- `slice_number`
- `sensor_operational_mode`，可与现有 `imaging_mode` 保持一致或作为补充
- `swath_list`，可用 JSON 或逗号文本保存 `IW1/IW2/IW3`
- `source_format`，例如 `SAFE_DIR`、`SAFE_ZIP`、`LT1_DIR`
- `metadata_json`，保存 Sentinel-1 专用扩展信息

如果想更轻量，也可以第一阶段只增加：

- `relative_orbit`
- `acquisition_start_time_utc`
- `acquisition_stop_time_utc`
- `metadata_json`

这四个字段已经足够支撑 Sentinel-1 基础管理和更科学的配对过滤。

数据库处理要求：

- 新增 ORM 字段和 Pydantic schema 字段。
- 新增幂等迁移 `010_sentinel1_source_fields.sql`。
- 把该迁移加入 [backend/app/db_maintenance.py](../backend/app/db_maintenance.py) 的 `MIGRATION_FILES`。
- 健康检查里至少确认新增列存在；如果后续扩展精轨库存表，再把库存一致性纳入健康检查。

## 5. 入库扫描设计

建议把现有解析逻辑拆成“产品类型适配器”：

```text
SourceProductParser
  - LT1 parser
  - Sentinel1 SAFE parser
  - GF3 parser
```

Sentinel-1 parser 负责：

1. 识别 `.SAFE` 目录。
2. 从目录名解析粗元数据。
3. 读取 `manifest.safe`。
4. 读取 annotation XML 获取或校正：
   - pass / orbit direction
   - relative orbit
   - absolute orbit
   - footprint 坐标
   - start / stop time
   - polarization / swath
5. 生成 `coverage_polygon`、`geom`、`min/max lon/lat`。
6. 判断 `insar_source_ready`：
   - 必须是 `SLC`
   - 必须有 footprint
   - 必须有 imaging date / acquisition time
   - 必须有 orbit direction
   - Sentinel-1 推荐必须有 relative orbit

兼容策略：

- 已解包 `.SAFE` 目录优先。
- `.SAFE.zip` 不在第一阶段直接解析，除非用户明确需要。后续可以在扫描器里只读 zip 内 `manifest.safe`，但这会明显增加实现和测试量。
- 预览图优先复用 SAFE 内 `preview/quick-look.png` 或其他 quicklook 文件；当前 `find_radar_preview_source()` 已有关键词扫描，可少量适配。

## 6. Sentinel-1 精轨管理设计

Sentinel-1 精轨文件是 `.EOF`，匹配逻辑不是“卫星 + 日期等于影像日期”，而是：

```text
orbit.satellite == scene.satellite
and orbit.validity_start <= scene.acquisition_time <= orbit.validity_stop
```

建议新增一个 Sentinel-1 orbit inventory 解析路径：

- 解析 `S1A/S1B`
- 解析轨道类型：`AUX_POEORB` / `AUX_RESORB`
- 解析 validity start / stop
- 记录原始 `.EOF` 路径

第一阶段可以不建独立轨道表，仍然把匹配结果写回 `radar_data.has_orbit_data` 和 `radar_data.orbit_file_path`。但代码上要避免把 `.EOF` 送进 LT1 的 txt->xml 转换流程。

更稳的设计是后续新增 `orbit_files` 表：

- `satellite`
- `satellite_family`
- `orbit_type`
- `validity_start_utc`
- `validity_stop_utc`
- `file_path`
- `metadata_json`

考虑到用户希望初级任务不要太复杂，第一阶段建议先不建表，只做扫描时内存索引和 `radar_data` 回填。

## 7. 配对逻辑改造

当前配对缓存可继续使用，但 Sentinel-1 应增加轻量硬约束。

建议硬约束：

- 同 `satellite_family = S1`
- 默认允许 `S1A/S1B` 跨星，但必须同 Sentinel-1 family
- 同 `orbit_direction`
- 同 `relative_orbit`
- 同 `imaging_mode`
- 同 `polarization` 或至少主极化兼容
- footprint 有交叠
- `insar_source_ready = true`

建议筛选指标：

- `time_baseline_days`
- `scene_overlap_ratio`
- `scene_center_distance_meters`，只作为 footprint 中心距，不叫真实空间基线
- `has_orbit_data`

不建议第一阶段做：

- 真实垂直基线计算
- TOPS burst 级 overlap
- 自动下载 ASF / Copernicus metadata
- Sentinel-1 burst id 图层化管理

配对缓存字段建议：

- 给 `pairing_metric_cache` 增加 `same_relative_orbit` 或通用 `same_track`。
- 或者在 SQL 硬约束里按 `radar_data.relative_orbit` 直接过滤。
- 在 `selection_meta_json` 中记录 `relative_orbit`、`orbit_direction`、`source_family`，便于追溯。

## 8. 数据分发设计

分发层整体可以复用：

- master 复制完整 `.SAFE` 目录到 `Task_*/master/`
- slave 复制完整 `.SAFE` 目录到 `Task_*/slave/`
- 如果启用精轨分发，则把 `.EOF` 复制到 `Task_*/orbit/`
- `.dinsar_pair.json` 中增加 Sentinel-1 专用元数据，如 relative orbit、acquisition start/stop、orbit EOF 路径

需要注意：

- `.SAFE` 目录可能很大，zip 分发开关已经存在的话可以复用。
- 不建议在分发阶段裁剪 SAFE 或抽取 measurement 文件，这会把“数据管理”变成“预处理”。
- 如果源数据是 `.SAFE.zip`，第一阶段可以要求用户先解包；否则分发逻辑要支持复制 zip 并在生产侧再解包。

## 9. 前端改造

需要处理的点：

- `SATELLITE_GROUPS` 增加 `S1` / `Sentinel-1`。
- 源数据检索高级字段增加相对轨道、起止时间、源格式等字段。
- 配对弹窗默认不要固定 `LT1A/LT1B`；建议默认“不限定卫星”，或者根据当前检索结果自动选择可见卫星。
- 配对参数文案继续使用“footprint 中心距上限”，不要改回“空间基线”。
- 对 Sentinel-1 配对显示 `relative_orbit` 和 `orbit_direction`，让用户一眼能看出是不是同轨道。
- 生产提交页如果任务包是 Sentinel-1，但选择了 LT1 专用 PyINT/ISCE2 profile，应给出阻止或强提示。

## 10. 生产链路边界

现有生产引擎不应直接宣布支持 Sentinel-1：

- ISCE2 当前 pipeline 和 profile 明显是 LT1 定制，包含 LT1 wavelength、LT1 输入 resolver、LT1 轨道转换。
- PyINT / Gamma 当前输入搜索是 `LT1*.tar.gz` / `LT1*.tiff`，也属于 LT1 专用。
- SARscape 理论上能处理 Sentinel-1，但当前系统侧是否能用 raw SAFE 驱动 SARscape D-InSAR，需要单独验证模板、参数和 task runner。

所以建议第一阶段只做到：

```text
Sentinel-1 数据管理 -> 配对规划 -> 批次保存 -> 数据分发
```

生产执行留到第二阶段：

```text
Sentinel-1 Task_* -> 指定 Sentinel-1 engine/profile -> 预检 -> 生产
```

## 11. 实施阶段建议

### 阶段 1：管理与检索

工作内容：

- 完成 Sentinel-1 SAFE parser。
- 从 `manifest.safe` / annotation XML 提取 footprint、relative orbit、pass、start/stop time。
- 支持 `.EOF` 精轨扫描和时间窗匹配。
- 增加必要数据库字段和迁移。
- 前端增加 Sentinel-1 过滤和显示。

预估工作量：2-4 天。

风险：主要在不同 SAFE 版本 XML 结构差异，需要准备 3-5 个真实样本覆盖 S1A/S1B、IW、单双极化。

### 阶段 2：配对与分发

工作内容：

- 配对缓存加入 `relative_orbit` 约束。
- 配对结果、批次 item、`.dinsar_pair.json` 保留 Sentinel-1 扩展元数据。
- 分发 `.SAFE` + `.EOF`。
- 前端配对弹窗默认卫星选择调整。

预估工作量：1-2 天。

风险：如果数据库新增字段后没有正确标 dirty，需要强制重建 pairing cache。

### 阶段 3：生产预检保护

工作内容：

- 识别 task source family。
- Sentinel-1 批次提交到 LT1 专用 engine/profile 时阻止。
- 在生产面板显示“当前仅支持管理和分发，生产适配待完成”。

预估工作量：0.5-1 天。

风险：低。

### 阶段 4：Sentinel-1 生产适配

工作内容：

- 选择 SARscape、ISCE2 TOPS、GMTSAR 或其他处理链。
- 设计 Sentinel-1 专用 engine/profile。
- 做 TOPS 配准、轨道、DEM、burst overlap、输出发布包和 catalog 适配。

预估工作量：按引擎不同约 1-3 周，不建议和第一阶段混在一起。

## 12. 推荐最小方案

如果目标是“先把系统能管理哨兵数据”，推荐最小闭环如下：

1. 只支持已解包 `.SAFE` 目录入库。
2. 只解析 SLC 产品。
3. 只新增 `relative_orbit`、`acquisition_start_time_utc`、`acquisition_stop_time_utc`、`metadata_json` 四类关键字段。
4. `.EOF` 精轨只做匹配和路径保存，不做转换。
5. 配对只增加同 relative orbit 约束，不做真实空间基线。
6. 分发复制完整 SAFE 和 EOF。
7. 生产页阻止 Sentinel-1 进入 LT1 专用 profile。

这个方案对现有 LT1 链路侵入最小，能让 Sentinel-1 数据先进入“可检索、可配对、可分发、可追踪”的状态。

## 13. 验收标准

最小验收：

- 扫描一个包含 S1A/S1B `.SAFE` 的目录后，`radar_data` 能看到 S1 数据。
- 源数据检索可以按 Sentinel-1、IW、SLC、极化、升降轨、相对轨道过滤。
- 每个 S1 scene 有 footprint，可在地图上显示。
- 有匹配 `.EOF` 时 `has_orbit_data = true`，`orbit_file_path` 指向 EOF。
- 配对结果不会跨 relative orbit。
- 批次保存和数据分发能生成 `Task_*`，其中包含 master/slave SAFE 和可选 orbit EOF。
- Sentinel-1 批次不会误提交到 LT1 专用生产 profile。

## 14. 需要用户确认的输入

开工前最好确认：

1. Sentinel-1 数据池是 `.SAFE` 解包目录，还是 `.SAFE.zip` 为主。
2. 精轨 `.EOF` 是否已经本地保存，还是希望后续系统自动下载。
3. 第一阶段是否只做管理/配对/分发，不接生产。
4. 是否接受新增少量数据库字段和一份幂等迁移。

