# Sentinel-1 / LT-1 源数据与精密轨道资产设计

日期：2026-05-12

本文定义源数据管理和精密轨道管理的下一阶段底座设计。目标是把 Sentinel-1 和 LT-1 放到同等位置和能力上，而不是把 Sentinel-1 临时塞进现有 LT-1 扫描和轨道字段里。

本文只覆盖源数据、精密轨道、资产库存、轨道绑定、启动自维护和健康检查。D-InSAR 配对、任务分发和生产 profile 不在本阶段实现范围内，但后续应消费这里建立的资产与绑定结果。

## 1. 目标

本阶段要完成：

1. 统一管理 LT-1、Sentinel-1 的源产品资产。
2. 统一管理 LT-1 原生精轨和 Sentinel-1 EOF 精轨。
3. 让 `radar_data` 成为统一 scene 业务入口，而不是唯一资产库存表。
4. 建立 scene 与精轨的候选、选中和异常状态。
5. 把新 schema 纳入启动自维护、数据库 schema 检查和健康检查。
6. 保持现有 LT-1、GF-3、水体、配对、结果 catalog 的兼容运行。

本阶段暂不做：

1. 不新增 Sentinel-1 D-InSAR 生产链路。
2. 不改造现有 `lt1_gamma_dinsar` 或 `lt1_stripmap` 为多卫星 profile。
3. 不在启动时扫描大目录或解析大型 ZIP。
4. 不把 Sentinel-1 EOF 送入 LT-1 txt 到 XML 转换链路。

## 2. 现有约束

当前系统启动链路中，`backend/app/main.py` 的 lifespan 会先调用 `ensure_database_ready()`，再初始化数据库会话、同步 managed roots、同步 manifest catalog、启动 pairing cache 状态。因此新资产层必须满足：

- ORM 是 schema 事实来源。新增表和字段必须进入 ORM。
- SQL migration 是约束、索引、幂等修补的事实来源。新增 migration 必须加入 `backend/app/db_maintenance.py` 的 `MIGRATION_FILES`。
- migration 必须可重复运行，使用 `CREATE TABLE IF NOT EXISTS`、`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`、`CREATE INDEX IF NOT EXISTS`。
- 启动自维护只保证 schema 和轻量状态存在，不做 ZIP 解包、不扫大目录、不重建全量绑定。
- 健康检查必须能报告资产层状态，不能只看旧的 `MONITOR_RADAR_DIRS` 和 `MONITOR_ORBIT_DIR` 是否存在。

现有兼容字段需要保留：

- `radar_data.has_orbit_data`
- `radar_data.orbit_file_path`
- `radar_data.file_path`
- `radar_data.coverage_polygon`
- `radar_data.geom`

这些字段继续服务旧接口和旧前端，但由新的资产与绑定结果回填。

## 3. 领域模型

新的数据模型分成五类。

### 3.1 源产品资产

`source_product_assets` 记录物理源产品。一个资产是一份实际存在的文件或目录，例如：

- LT-1 scene 目录
- LT-1 原始压缩包或 tiff
- Sentinel-1 `.zip`
- Sentinel-1 `.SAFE` 目录
- GF-3 现有处理输入或输出目录，后续可接入同一库存

关键字段建议：

```text
id
asset_uid
logical_product_uid
satellite_family
satellite
source_format
product_type
product_level
imaging_mode
polarization
absolute_orbit
relative_orbit
orbit_direction
acquisition_start_time_utc
acquisition_stop_time_utc
imaging_date
root_path
file_path
archive_path
path_kind
file_name
file_stem
file_ext
size_bytes
mtime_epoch
checksum_sha256
checksum_status
parser_name
parser_version
parse_status
parse_error
parsed_at
metadata_json
is_active
missing_since
created_at
updated_at
```

字段语义：

- `asset_uid` 是物理资产唯一键，优先由规范化绝对路径和大小/mtime 派生。
- `logical_product_uid` 是同一个遥感产品的逻辑身份，用于把 Sentinel-1 ZIP 和解包后的 SAFE 关联起来。
- `source_format` 使用稳定枚举，例如 `LT1_DIR`、`LT1_ARCHIVE`、`S1_ZIP`、`S1_SAFE_DIR`、`GF3_DIR`。
- `archive_path` 用于记录生产更偏好的原始归档路径，例如 Sentinel-1 ZIP。
- `metadata_json` 保存传感器专有字段，不把所有 Sentinel-1 annotation 字段拆成列。

### 3.2 Scene 业务入口

`radar_data` 仍然是前端检索、地图展示、水体、配对和后续生产的 scene 入口。它需要补充资产引用和 Sentinel-1/LT-1 通用字段：

```text
source_product_ref_id
source_archive_asset_id
selected_orbit_asset_id
orbit_binding_status
orbit_binding_reason
acquisition_start_time_utc
acquisition_stop_time_utc
absolute_orbit
relative_orbit
source_format
metadata_json
```

字段语义：

- `source_product_ref_id` 指向用于生成该 scene 元数据的主资产。
- `source_archive_asset_id` 指向后续分发或生产优先使用的原始资产。Sentinel-1 通常是 ZIP。
- `selected_orbit_asset_id` 指向当前选中的精轨资产。
- `orbit_binding_status` 标识 `UNBOUND`、`MATCHED`、`MISSING`、`AMBIGUOUS`、`ERROR`。
- 旧字段 `has_orbit_data` 和 `orbit_file_path` 从 `selected_orbit_asset_id` 兼容回填。

### 3.3 精密轨道资产

`orbit_assets` 记录原生轨道文件。它不记录派生到 ENVI/ISCE2 池里的文件。

关键字段建议：

```text
id
orbit_uid
satellite_family
satellite
orbit_type
native_format
quality_class
root_path
file_path
file_name
file_stem
file_ext
size_bytes
mtime_epoch
checksum_sha256
checksum_status
validity_start_time_utc
validity_stop_time_utc
generation_time_utc
published_time_utc
parser_name
parser_version
parse_status
parse_error
parsed_at
metadata_json
is_active
missing_since
created_at
updated_at
```

枚举建议：

- `satellite_family`: `LT1`、`S1`、`GF3`
- `native_format`: `LT1_TXT`、`S1_EOF`
- `orbit_type`: `LT1_GPS`、`AUX_POEORB`、`AUX_RESORB`
- `quality_class`: `precise`、`restituted`、`predicted`、`unknown`

Sentinel-1 EOF 的匹配逻辑基于：

```text
orbit.satellite == scene.satellite
and orbit.validity_start_time_utc <= scene.acquisition_time <= orbit.validity_stop_time_utc
```

LT-1 第一阶段可继续复用现有日期匹配逻辑，后续如果轨道文件能提供明确有效期，也应收敛到相同的时间窗模型。

### 3.4 Scene 与轨道绑定

`scene_orbit_bindings` 记录每个 scene 的候选轨道和最终选择。

关键字段建议：

```text
id
radar_data_id
orbit_asset_id
binding_role
match_status
selection_status
selection_rank
priority_score
coverage_margin_before_seconds
coverage_margin_after_seconds
match_rule_version
match_reason
selected_at
metadata_json
created_at
updated_at
```

字段语义：

- `binding_role` 第一阶段使用 `primary_orbit`。
- `match_status`: `CANDIDATE`、`MATCHED`、`REJECTED`、`STALE`、`ERROR`。
- `selection_status`: `SELECTED`、`CANDIDATE`、`NOT_SELECTED`。
- Sentinel-1 选择优先级：POEORB 优先于 RESORB；覆盖余量更大优先；generation time 更新优先。
- LT-1 选择优先级：现有可用轨道优先；坏源精轨不得入选；派生池同步失败时保留资产但标注异常。

### 3.5 轨道派生产物

`orbit_asset_derivatives` 记录从原生轨道资产生成或同步到引擎池的文件。

关键字段建议：

```text
id
orbit_asset_id
engine_code
derivative_format
derivative_role
pool_path
size_bytes
mtime_epoch
checksum_sha256
generation_status
generation_error
generated_at
metadata_json
created_at
updated_at
```

用途：

- LT-1 txt 同步到 ENVI pool。
- LT-1 txt 转换成 ISCE2 XML。
- Sentinel-1 EOF 第一阶段通常不转换，但后续可以记录 staging 到 OPOD 目录的结果。

### 3.6 库存状态与问题

为避免健康检查每次扫大目录，新增轻量状态表：

`asset_inventory_states`

```text
id
root_ref_id
inventory_type
root_path
scan_mode
status
last_scan_started_at
last_scan_finished_at
last_seen_entry_count
last_asset_count
last_issue_count
parser_version
needs_rescan
last_error
metadata_json
created_at
updated_at
```

`asset_inventory_issues`

```text
id
root_ref_id
inventory_type
asset_ref_id
radar_data_id
orbit_asset_id
severity
issue_code
issue_message
source_path
status
first_seen_at
last_seen_at
resolved_at
metadata_json
```

典型 issue：

- `source_path_missing`
- `source_parse_failed`
- `orbit_parse_failed`
- `duplicate_logical_product`
- `scene_missing_orbit`
- `scene_ambiguous_orbit`
- `selected_orbit_missing_file`
- `lt1_derivative_generation_failed`

## 4. Root 与配置

建议新增通用配置，同时保留旧配置作为兼容入口：

```text
SOURCE_PRODUCT_DIRS=
ORBIT_SOURCE_DIRS=
```

兼容关系：

- `INSAR_STORAGE_DIRS` 和 `MONITOR_RADAR_DIRS` 继续生效，并作为 `SOURCE_PRODUCT_DIRS` 的兼容来源。
- `MONITOR_ORBIT_DIR` 继续生效，并作为 `ORBIT_SOURCE_DIRS` 的兼容来源。
- `ORBIT_POOL_ENVI`、`ORBIT_POOL_ISCE2` 仍然是派生轨道池，不作为原生轨道资产源。

`root_registry_service` 需要增加或调整 root role：

- `source_product_pool`
- `legacy_scan_root_radar`
- `orbit_asset_pool`
- `orbit_pool_envi`
- `orbit_pool_isce2`

每个 root 的 `metadata_json` 可以记录：

```json
{
  "supported_families": ["LT1", "S1"],
  "discovery_patterns": ["LT1*", "S1*.zip", "*.SAFE", "*.EOF"],
  "imported_from": "settings"
}
```

对用户当前样本，推荐后续配置形态是：

```text
SOURCE_PRODUCT_DIRS=D:\LuTan1_Image_Pool;D:\Sentinel1_Image_Pool_ZIP
ORBIT_SOURCE_DIRS=D:\LT1_data_lsarorbit;D:\Sentinel1_EOF_Pool
```

## 5. Parser 设计

新增解析器按资产类型组织：

- `LT1SourceParser`
- `Sentinel1ZipParser`
- `Sentinel1SafeParser`
- `GF3SourceParser`
- `LT1OrbitParser`
- `Sentinel1EofParser`

Sentinel-1 ZIP 解析原则：

- 不全量解压。
- 使用 Python `zipfile` 读取 `manifest.safe` 和 annotation XML。
- 只读取必要 XML 文件和 quicklook 候选，不读取 measurement 大文件。
- 文件名提供粗字段，XML 提供 relative orbit、升降轨、footprint、swath、极化细节。
- `S1A`、`S1B`、`S1C` 都归一化为 `satellite_family = S1`。

Sentinel-1 EOF 解析原则：

- 优先从文件名解析 mission、orbit type、generation time、validity start、validity stop。
- 文件名不可靠时再读取 EOF XML 内容。
- `AUX_POEORB` 标为 precise，`AUX_RESORB` 标为 restituted。

LT-1 解析原则：

- 现有文件名和 XML 解析逻辑保留。
- 现有坏源精轨检测和 quarantine 逻辑保留，但结果写入 `orbit_assets` 和 `asset_inventory_issues`。
- 现有 `sync_orbit_pools()` 逐步下沉为 `orbit_asset_derivatives` 的生成步骤。

## 6. 扫描与绑定流程

### 6.1 源产品扫描

流程：

1. 从 managed roots 找到 source product roots。
2. 发现候选文件或目录。
3. upsert `source_product_assets`。
4. 调用对应 parser。
5. 生成或更新 `radar_data`。
6. 更新 `asset_inventory_states`。
7. 记录解析失败或重复产品到 `asset_inventory_issues`。

`radar_data.unique_id` 的生成应优先使用稳定逻辑身份：

```text
satellite_family + satellite + product_unique_id
```

如果缺少产品唯一号，再回退到规范化路径。

### 6.2 轨道扫描

流程：

1. 从 managed roots 找到 orbit asset roots。
2. 发现 LT-1 txt 和 Sentinel-1 EOF。
3. upsert `orbit_assets`。
4. 调用轨道 parser。
5. LT-1 轨道执行坏源检测。
6. 记录 parse status 和 inventory issues。

### 6.3 轨道绑定

流程：

1. 找出新增或变更的 scene 与 orbit asset。
2. 按卫星族调用绑定规则。
3. 写入候选 `scene_orbit_bindings`。
4. 选出 `selection_status = SELECTED` 的绑定。
5. 回填 `radar_data.selected_orbit_asset_id`、`orbit_binding_status`、`has_orbit_data`、`orbit_file_path`。
6. 对无轨道或多候选未决的 scene 写入 issue。

绑定规则版本需要显式记录，例如：

```text
lt1_orbit_binding.v1
s1_eof_window_binding.v1
```

## 7. 启动自维护

新增资产层后，启动自维护必须按以下方式运行：

1. `ensure_database_ready()` 创建 PostGIS 扩展。
2. ORM `create_all()` 创建新增表和新增列。
3. migration `010_source_orbit_asset_inventory.sql` 补齐索引、约束、兼容列、视图或必要函数。
4. `root_registry_service.sync_from_settings()` 同步新旧 root。
5. 启动阶段只初始化 `asset_inventory_states` 的空状态，不做真实扫描。
6. 健康检查报告 `needs_rescan = true`，由用户手动触发扫描任务。

注意事项：

- 不允许启动时遍历 `D:\Sentinel1_Image_Pool_ZIP` 这类大目录并打开 ZIP。
- 不允许启动时自动删除资产记录。文件消失时标记 `is_active = false` 和 `missing_since`。
- schema mismatch 必须在启动日志中可见，健康检查必须能展示缺表、缺列和资产库存异常。

## 8. 健康检查

`health_service` 增加 `asset_inventory` 检查项。

建议返回结构：

```json
{
  "ok": true,
  "source_roots": {
    "configured_count": 2,
    "accessible_count": 2,
    "needs_rescan_count": 1
  },
  "orbit_roots": {
    "configured_count": 2,
    "accessible_count": 2,
    "needs_rescan_count": 1
  },
  "source_assets": {
    "total_count": 0,
    "lt1_count": 0,
    "s1_count": 0,
    "parse_failed_count": 0
  },
  "orbit_assets": {
    "total_count": 0,
    "lt1_count": 0,
    "s1_count": 0,
    "parse_failed_count": 0
  },
  "bindings": {
    "scene_count": 0,
    "matched_count": 0,
    "missing_count": 0,
    "ambiguous_count": 0
  },
  "issues": {
    "open_count": 0,
    "error_count": 0,
    "warning_count": 0
  }
}
```

整体健康状态建议：

- schema 不完整：`ok = false`
- root 不可访问：`ok = false`
- 有 error 级 issue：`ok = false`
- 仅 `needs_rescan` 或 warning issue：`ok = true`，但显示 degraded/warning

## 9. API 与前端边界

本阶段后端需要提供：

- 源产品资产列表、详情、重扫。
- 轨道资产列表、详情、重扫。
- scene 的轨道绑定详情。
- 资产库存健康摘要。
- 手动触发源产品扫描、轨道扫描、轨道绑定重建。

前端数据管理页面需要显示：

- satellite family: LT1、S1、GF3
- source format: LT1_DIR、S1_ZIP、S1_SAFE_DIR
- relative orbit、absolute orbit
- acquisition start/stop
- orbit binding status
- selected orbit asset
- parser status 和 issue 摘要

轨道管理页面需要显示：

- 原生轨道资产列表。
- LT-1 与 Sentinel-1 的轨道类型。
- validity window。
- 派生产物状态。
- 绑定到多少 scene。
- 解析失败或缺文件问题。

## 10. 兼容与迁移策略

迁移后，现有业务继续读 `radar_data`。新资产层提供更完整的 provenance。

兼容规则：

- 旧扫描入口可以保留，但应逐步改为调用资产扫描服务。
- `radar_data.file_path` 继续保存主 scene 路径。
- `radar_data.orbit_file_path` 继续保存选中原生轨道路径。
- `radar_data.has_orbit_data` 继续表示是否存在选中轨道。
- 现有 pairing cache 可以暂时继续使用旧字段，后续再改为读取 `relative_orbit` 和资产绑定状态。

数据回填：

1. 对已有 `radar_data` 记录创建 `source_product_assets`。
2. 对已有 `orbit_file_path` 创建 `orbit_assets`。
3. 生成 `scene_orbit_bindings`。
4. 确认兼容字段和新绑定结果一致。

## 11. 验收标准

以当前样本池作为第一轮验收：

- `D:\Sentinel1_Image_Pool_ZIP` 中 29 个 Sentinel-1 ZIP 能登记为 `source_product_assets`。
- S1A 与 S1C 都归入 `satellite_family = S1`。
- 每个 ZIP 能生成或更新一条 `radar_data` scene。
- `D:\Sentinel1_EOF_Pool` 中 24 个 EOF 能登记为 `orbit_assets`。
- 29 个 Sentinel-1 scene 全部能通过 validity window 绑定到 EOF。
- `has_orbit_data = true` 和 `orbit_file_path` 能兼容回填。
- LT-1 现有源数据扫描和精轨同步不退化。
- 启动后数据库健康检查不出现 schema mismatch。
- 健康检查能展示源产品、轨道资产、绑定状态和 issue 统计。

## 12. 实施顺序

建议按以下顺序开工：

1. Schema 与 ORM：新增资产表、绑定表、状态表，扩展 `radar_data`。
2. Migration 与启动自维护：新增 `010_source_orbit_asset_inventory.sql`，加入 `MIGRATION_FILES`。
3. 健康检查：新增 `asset_inventory` 状态，不依赖真实扫描也能返回稳定结构。
4. Root registry：支持通用 `SOURCE_PRODUCT_DIRS`、`ORBIT_SOURCE_DIRS`，兼容旧配置。
5. Parser 与库存扫描：先实现 Sentinel-1 ZIP、Sentinel-1 EOF、LT-1 现有源数据和轨道。
6. 轨道绑定服务：实现 LT-1 日期绑定和 Sentinel-1 EOF 时间窗绑定。
7. 兼容回填：更新 `radar_data.has_orbit_data`、`orbit_file_path`。
8. API 与前端：展示资产、轨道、绑定和 issue。
9. 回归测试：用 Sentinel-1 样本池和现有 LT-1 数据池验证。

这一路径先把底座做稳，再接 D-InSAR 配对和 `s1_gamma_dinsar` 生产 profile。
