# 成果交付与本地下载设计

日期：2026-06-30

## 背景

当前“结果提取”页面同时承载了两个不同概念：

1. 生产结果入库：从 LandSAR/ENVI/Gamma 等生产目录提取结果并登记到 catalog。
2. 成果交付导出：用户从已登记 catalog 中选择成果，下载或复制到本地使用。

现有 `POST /api/dinsar-results/export` 是同步文件复制接口，并且要求 admin。这个模型不适合普通用户下载大体量成果：请求容易超时，目标路径由用户输入也不利于审计和权限控制。

## 目标

- 所有登录用户都可以申请成果交付。
- D-InSAR 已登记成果接入真实交付下载。
- LT-1 正射与 GF3 正射接入真实交付下载。
- SBAS 与 Sentinel-1 正射在页面和接口中保留清晰占位，不暴露假执行能力。
- 大文件交付改为后台任务，不再由 HTTP 请求同步复制。
- 用户最终可以把成果下载到本地；服务器交付区只是临时缓存。
- 每次交付可审计、可过期清理、可校验。
- 数据库变更必须接入现有自维护机制。

## 非目标

- 本阶段不实现 Sentinel-1 正射生产。
- 本阶段不实现 SBAS 成果交付打包，只显示目录已接入但交付未接入。
- 本阶段不触发 GF3 生产；GF3 交付只面向已经登记的 SARscape 标准化正射成品。
- 本阶段不做跨节点对象存储或外部网盘。
- 本阶段不把普通用户开放到任意服务器路径写入。

## 权限模型

当前系统只有 `admin` 和 `viewer`。本阶段不强制新增角色，采用能力约定：

- 登录用户：可查看已授权 catalog，可创建自己的成果交付任务，可查看和下载自己的交付包。
- admin：除普通用户能力外，可查看所有交付任务，可配置交付根目录，可清理或取消交付任务。

后续如果拆角色，建议增加：

- `exporter`：可创建成果交付任务。
- `operator`：可做生产、入库和成果交付。
- `admin`：用户、系统配置和全局清理。

## 交付模式

## 正射成果口径

- LT-1 正射：由服务器侧单景正射流水线生产，登记在 `sar_scene_geo`，主交付物是 `analysis_ready.tif`，随包包含预览、manifest、quality 等 sidecar 文件。
- GF3 正射：系统拿到的就是外部 SARscape 已生产成品，当前可登记为 `GF3_SARSCAPE_NATIVE_PREVIEW`，标准化后也可登记为 `GF3_SARSCAPE_L2`；交付阶段只做受控打包和下载，不触发 GF3 生产。
- Sentinel-1 正射：生产链尚未接入，结果提取页面保留占位，避免用户误操作。
- SBAS-InSAR：成果目录可查，但交付打包尚未接入。

### 1. 目录交付

默认模式。后台将选中的结果文件复制到：

```text
{RESULT_DELIVERY_ROOT}/{username}/{delivery_id}/
```

目录内包含：

- `manifest.json`
- `checksums.sha256`
- 结果文件或结果子目录

适合几十 GB 到 TB 级数据。用户可以通过共享目录或逐文件 HTTP 下载到本地。

### 2. 压缩包交付

可选模式。只允许低于阈值的交付包生成 zip：

```text
{RESULT_DELIVERY_ROOT}/{username}/{delivery_id}.zip
```

阈值由环境变量控制，例如 `RESULT_DELIVERY_ZIP_MAX_BYTES`。超过阈值时，接口返回明确错误，要求使用目录交付或逐文件下载。

### 3. HTTP 下载

下载不由 FastAPI 直接流式传大文件。推荐 Nginx 静态服务交付区，并支持 Range 断点续传。

第一版接口可以返回文件下载 URL，由后端验证交付归属后通过 `FileResponse` 交付；后续切到 Nginx `X-Accel-Redirect` 或专门静态路径。

## 数据模型

新增两张表：

### `result_delivery_requests`

- `delivery_id`：业务 ID。
- `owner_user_id` / `owner_username`：申请人。
- `channel`：`dinsar`、`sbas`、`lt1_ortho`、`s1_ortho`、`gf3_ortho`。
- `status`：`PENDING`、`RUNNING`、`READY`、`FAILED`、`CANCELLED`、`EXPIRED`。
- `package_mode`：`directory`、`zip`。
- `item_count`、`total_bytes`、`copied_bytes`。
- `delivery_root`、`delivery_dir`、`zip_path`、`manifest_path`。
- `expires_at`。
- `task_id`、`job_id`。
- `error_message`。
- `request_json`、`summary_json`。

### `result_delivery_items`

- `delivery_id`。
- `source_product_id` / `source_result_id`。
- `source_radar_data_id` / `source_scene_geo_id`：用于 LT-1/GF3 单景正射资产追踪。
- `display_name`。
- `source_path`。
- `relative_path`。
- `file_size`。
- `checksum_sha256`。
- `status`：`PENDING`、`COPIED`、`FAILED`、`SKIPPED`。
- `error_message`。

## 数据库自维护

新增 migration：

```text
backend/migrations/013_result_delivery_requests.sql
```

并加入 `backend/app/db_maintenance.py` 的维护文件列表。迁移必须幂等：

- `CREATE TABLE IF NOT EXISTS`
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- `CREATE INDEX IF NOT EXISTS`

ORM 同步也要定义对应模型，避免启动时 metadata 检查缺表。

## API 设计

### 获取通道能力

```http
GET /api/result-deliveries/channels
```

返回：

- `dinsar`: `ready`
- `sbas`: `planned`
- `lt1_ortho`: `ready`
- `s1_ortho`: `placeholder`
- `gf3_ortho`: `ready`

### 获取可交付目录

```http
GET /api/result-deliveries/catalog/{channel}
```

第一版支持：

- `dinsar`：兼容已有 D-InSAR catalog。
- `lt1_ortho`：读取 `sar_scene_geo` 中 `lt_gamma / lt1_gamma_geocoded_mli` 且 `DONE` 的分析就绪 GeoTIFF。
- `gf3_ortho`：读取 `radar_data.source_format in (GF3_SARSCAPE_NATIVE_PREVIEW, GF3_SARSCAPE_L2)` 且 `geocoded_flag = true` 的 SARscape 正射成品。

返回统一字段包括 `item_id`、`source_kind`、`product_id`、`display_name`、`primary_asset_path`、`publish_dir`、`radar_data_id`、`scene_geo_id`。

### 创建交付任务

```http
POST /api/result-deliveries
```

请求：

```json
{
  "channel": "dinsar",
  "product_ids": ["..."],
  "compat_result_ids": [1, 2, 3],
  "item_ids": [101, 102],
  "package_mode": "directory",
  "include_manifest": true,
  "include_checksums": true
}
```

约束：

- 普通用户只创建自己的任务。
- `dinsar` 使用 `compat_result_ids` 或 `product_ids`。
- `lt1_ortho` 使用 `item_ids = sar_scene_geo.id`。
- `gf3_ortho` 使用 `item_ids = radar_data.id`。
- 每次最大数量由 `RESULT_DELIVERY_MAX_ITEMS` 控制。
- 不允许传入任意服务器输出路径。

### 列出交付任务

```http
GET /api/result-deliveries?mine=true
```

普通用户只能看到自己的任务，admin 可查看全部。

### 查看交付详情

```http
GET /api/result-deliveries/{delivery_id}
```

返回交付状态、文件清单、下载 URL、过期时间。

### 下载文件

```http
GET /api/result-deliveries/{delivery_id}/files/{item_id}/download
GET /api/result-deliveries/{delivery_id}/archive/download
GET /api/result-deliveries/{delivery_id}/manifest
```

第一版由后端验证权限后返回文件。后续可迁移到 Nginx token 或 `X-Accel-Redirect`。

## 后台任务

新增 job type：

```text
RESULT_DELIVERY_BUILD
```

处理流程：

1. 将 delivery 标记为 `RUNNING`。
2. 按 channel 解析可交付源文件路径。
3. 复制到交付目录。
4. 生成 `manifest.json`。
5. 可选计算 checksum。
6. 可选生成 zip。
7. 更新状态为 `READY` 或 `FAILED`。

任务日志应写清：

- 总项目数。
- 已复制数量。
- 总大小。
- 失败项和原因。
- 交付目录。
- 过期时间。

## 存储与清理

环境变量建议：

```text
RESULT_DELIVERY_ROOT=D:\Result_Delivery
RESULT_DELIVERY_PUBLIC_BASE_URL=/deliveries
RESULT_DELIVERY_RETENTION_DAYS=7
RESULT_DELIVERY_MAX_ITEMS=500
RESULT_DELIVERY_ZIP_MAX_BYTES=21474836480
RESULT_DELIVERY_CHECKSUM_ENABLED=true
```

清理策略：

- `expires_at < now` 的 `READY/FAILED/CANCELLED` 交付包可清理。
- 清理后状态改为 `EXPIRED`，保留数据库审计记录。
- 正式成果 catalog 原文件绝不能被清理任务删除。

## 前端设计

结果提取页面改名语义：

- “生产结果入库”：保留现有 D-InSAR 入库入口，admin 可用。
- “成果交付下载”：所有登录用户可用。

页面结构：

- 通道栏：D-InSAR、LT-1 正射、GF3 正射可用；SBAS/Sentinel-1 显示未接入交付。
- 成果选择区：D-InSAR 复用现有 catalog 列表，LT-1/GF3 使用统一交付 catalog 查询。
- 交付选项：目录交付 / 压缩包交付。
- 我的交付包：状态、大小、文件数、过期时间、下载入口。

交互约束：

- 不再让普通用户输入服务器路径。
- 创建后显示任务 ID 和交付 ID。
- 对大文件提示“建议使用逐文件下载或共享目录复制”。
- 下载入口只在 `READY` 状态显示。

## 与用户管理联动

第一版：

- `viewer` 也可以创建自己的成果交付任务。
- 前端不再用 `readOnly` 禁用成果交付下载。
- 生产结果入库、删除、系统配置仍要求 admin。

后续：

- 增加 `exporter/operator` 角色后，前端用户管理页需要增加角色选项。
- 后端新增能力级依赖，例如 `require_capability("result.delivery.create")`。

## 风险与防护

- 大文件复制拖慢生产盘：限制 worker 并发，交付任务可单独限流。
- 用户重复申请导致空间膨胀：按用户限制未过期交付包数量和总大小。
- 任意路径写入风险：只允许系统配置的交付根目录。
- HTTP 下载超时：优先 Nginx Range，后端只负责授权。
- catalog 文件被移动：任务记录 item 失败，不影响其他文件。

## 实施阶段

### 阶段 1

- 文档落地。
- 数据表和自维护 migration。
- D-InSAR、LT-1 正射、GF3 正射目录交付后台任务。
- 我的交付包列表和详情。

### 阶段 2

- zip 打包阈值和下载。
- manifest/checksum 下载。
- Nginx 静态交付路径或 `X-Accel-Redirect`。

### 阶段 3

- SBAS 交付接入。
- Sentinel-1 正射在生产 catalog 完成后接入。
- exporter/operator 角色拆分。

## 验收标准

- 普通登录用户能创建 D-InSAR、LT-1 正射、GF3 正射成果交付任务。
- HTTP 请求只排队任务，不再同步复制大文件。
- 交付完成后用户能下载到本地。
- 普通用户不能指定任意服务器目录。
- admin 能看到所有交付任务。
- 数据库重启自维护能创建交付相关表和索引。
- Sentinel-1 正射通道显示占位，不可误点击执行。
