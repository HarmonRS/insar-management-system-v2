# InSAR 自动化管理与智能评估系统 - 部署与使用说明

本文件为本项目主中文文档，整合了产品说明、技术架构、业务流程、部署与启动、数据库与授权等内容。  
补充专题文档见：`docs/TODO.md`、`docs/RADAR_PREVIEW_SCHEME_B.md`。

---

## 1. 产品概述
本系统是一套深度集成 **IDL/ENVI SARscape** 的专业级 InSAR 数据管理与智能分析平台，聚焦地质灾害监测领域，解决大规模雷达影像处理流程繁琐、数据关联困难、结果解译依赖人工等问题。

通过“Web 化管理 + 自动化引擎 + AI 智能诊断”的三位一体架构，实现从原始 SAR 影像入库到生成形变诊断报告的全生命周期自动化管理。

---

## 2. 核心优势
- **核心流程自动化**：覆盖数据监控、精轨关联、干涉配对、自动化处理引导与结果发布（当前扫描调度默认手动触发）。
- **高精度空间引擎**：凸包算法 + 仿射变换，支持亚像素级 Footprint 提取与空间关联。
- **AI 辅助决策**：集成随机森林质量评估模型与 VLM 视觉大模型，给出专家级解译建议。
- **工业级稳定性**：针对 Windows Server 环境优化，支持 GB 级超大影像处理。

---

## 3. 技术架构
- **管理层 (Web Interface)**：React + Vite + Leaflet（支持离线多级细节加载）
- **逻辑层 (Service Layer)**：FastAPI 异步框架，集成 PostgreSQL + PostGIS
- **空间引擎 (Spatial Engine)**：PostGIS 原生计算 + GeoAlchemy2 + Rasterio
- **AI 引擎 (Intelligence)**：Scikit-learn + 本地 Ollama VLM
- **计算引擎 (Compute)**：IDL 8.8 / ENVI 5.6（SARscape 5.6+）

---

## 4. 业务流程指南

### 4.1 数据资产监控
当前系统默认采用 **Manual-only（手动触发）** 扫描模式，不启用后台定时守护任务。  
可通过前端“立即扫描”或接口 `POST /api/monitor/run-now` 触发扫描；扫描内部仍采用增量策略以降低系统负载。

**D-InSAR 结果扫描说明（重要）：**  
当前 D-InSAR 结果扫描为**全量遍历 + 仅新增入库**。  
即：每次扫描都会遍历所有 `.hdr` 文件，但数据库只写入“新发现”的结果记录，以保证准确性和稳定性。

扫描任务的进度条基于**实际遍历数量与处理数量**计算，前端显示为真实进度。

数据列表已支持“影像信息”按钮，可查看影像基础元数据（卫星/日期/模式/极化/精轨等）。
影像元数据解析已扩展：优先从 XML 获取成像模式/极化/轨道方向，并补充卫星模式、接收站代号、圈号、景中心经纬度、采集时间、产品类型/级别、唯一标识等字段用于展示。
源数据预览缓存接口：`GET /api/radar-data/{data_id}/thumb`（首次请求若无缓存会自动生成）。

**源影像预览（Scheme B：后端预纠正缓存）**  
系统已接入双层缓存：
- `backend/image_cache/radar_geo/`：地理纠正后的主缓存（前端优先使用）
- `backend/image_cache/radar_raw/`：原图 WebP 回退缓存

**D-InSAR 结果缓存（标准目录）**
- `backend/image_cache/dinsar/`：D-InSAR 结果可视化缓存目录
- 文件命名：`ID_{id}_{name}.webp`
- 清单文件：`backend/image_cache/dinsar/cache_manifest.json`

**一致性检测（统计面板）**
- `GET /api/statistics` 已新增一致性字段，用于校验“数据库记录 vs 实际缓存文件 vs XML 读取结果”。
- 重点包含：
  - D-InSAR：`db_cached_but_file_missing_count`、`db_uncached_but_file_exists_count`、`manifest_missing_file_count`
  - 源影像预览：`db_ready_but_cache_missing_count`、`preview_missing_count`
  - XML：`xml_detected_but_unparsed_count`、`xml_missing_count`
- 前端「数据统计仪表盘」会直接显示异常数量并高亮提示，便于运维快速定位问题。

新增接口：
- `GET /api/radar-data/{id}/preview-status`：查询单条源影像预览缓存状态
- `POST /api/radar-data/{id}/rebuild-preview-cache`：管理员强制重建该影像预览缓存
- `GET /api/radar-data?limit=500&offset=0`：分页获取源数据列表（单次最多 2000 条）
- `POST /api/radar-data/search`：源数据分页检索（支持详情字段筛选 + 行政区/上传SHP空间筛选）
- `GET /api/radar-data/search/options`：获取源数据检索下拉选项（来自数据库去重值）
- `GET /api/dinsar-results?limit=500&offset=0`：分页获取 D-InSAR 结果列表（单次最多 2000 条）
- `GET /api/radar-data/imaging-dates`：获取所有可用成像日期（用于配对起始日期下拉框）
- `GET /api/statistics`：默认返回缓存快照；管理员可用 `GET /api/statistics?fresh=true` 强制重算

`/api/radar-data/search` 说明：
- 权限：管理员与只读账号均可调用（只读安全查询接口）。
- 筛选字段：可按卫星、成像时间范围、模式/极化/产品元数据等详情字段检索。
- 空间筛选：支持 `region_tree_id`（复用行政区）或上传 AOI 文件（`.shp`/GeoJSON）。
- 分页性能：首次 AOI 查询会返回 `aoi_token`，后续翻页可仅携带 token，无需重复上传文件。
- 前端交互：检索字段采用“全下拉可选”，避免手工输入导致的筛选误差。

分页接口响应结构（`/api/radar-data` 与 `/api/dinsar-results` 一致）：
```json
{
  "items": [],
  "total": 12345,
  "limit": 200,
  "offset": 0,
  "has_more": true
}
```

前端分页说明：
- 「数据列表」与「D-InSAR 结果」均支持上一页/下一页、每页条数切换（50/100/200/500）与页码跳转。
- 前端默认每页 200 条，避免一次性加载全量数据导致页面和地图卡顿。
- 「数据列表」改为“先检索后展示”：系统启动后不自动加载源数据，需点击“搜索”或“搜索全部源数据”后才显示列表与分页结果。
- 当检索条件变更并再次执行搜索时，前端会先清空旧结果再渲染新结果，避免“旧页数据残留”造成误判。
- 异常/堆栈文本默认不参与界面翻译（e.g. `NoneType object is not callable`、`Traceback`），保持原始语言以避免日志或异常信息被误替换。

新增环境变量（可选）：
```env
RADAR_GEO_CACHE_WORKERS=2
RADAR_GEO_CACHE_VERSION=b1
RADAR_GEO_CACHE_QUALITY=84
RADAR_PREVIEW_BUILD_ON_DEMAND=true
```

说明：
- `thumb` 接口优先返回 `radar_geo`，失败时自动回退 `radar_raw`。
- 扫描任务会增量构建 `radar_geo`，并维护 `radar_raw` 作为兜底。
- 如纠正逻辑升级，提升 `RADAR_GEO_CACHE_VERSION` 可触发重建。
- 纠正方向判定优先使用 XML 的 `sceneCornerCoord/refRow/refColumn` 建立“像素角 ↔ 地理角”映射，可显著减少左右/上下方向错误。

### 4.2 任务分发与处理
系统生成处理清单对接 IDL 自动化脚本，用户可在 Web 端一键启动本地 IDL 工作站，实现“云端规划，本地计算”。

### 4.3 智能诊断报告
AI 模块结合影像范围内的已知灾害点生成 Markdown 报告，涵盖：活动性评估、隐患搜寻、风险评级与处置建议。

---

## 5. 核心算法与机制概览
为保持文档完整性，以下为算法专题的简要说明（不再拆分子文档）：
- **智能任务规划与贪心优化**：多维约束模型、覆盖多样性惩罚与任务筛选策略。
- **空间几何与极速 Footprint**：降采样探测 + 凸包生成，结合拓扑简化提升效率。
- **AI 诊断与图像处理**：百分比截断拉伸、质量评估特征与自动裁剪策略。

---

## 6. 环境与依赖
- **OS**：Windows Server 2019+（推荐）
- **数据库**：PostgreSQL 14+ + PostGIS
- **Python**：推荐 Conda 环境（本项目 environment.yml 已固定为 Python 3.10）
- **Nginx**：用于前端静态资源与反向代理（推荐）
- **Node.js**：仅构建前端时需要
- **IDL/ENVI**：仅启用 IDL 自动化时需要

---

## 7. 离线 Python 环境（推荐：conda-pack）

### 7.1 开发机（有外网）
```powershell
conda env create -f environment.yml
conda activate InSAR
```

如需授权功能，请确保安装 cryptography：
```powershell
pip install cryptography
```

如需打包授权 GUI：
```powershell
pip install pyinstaller
```

打包环境：
```powershell
conda install -n base conda-pack
conda pack -n InSAR -o insar_env.tar.gz
```

将 `insar_env.tar.gz` 拷贝到服务器。

### 7.2 服务器（离线）
解压到目标目录（示例：`C:\envs\InSAR`）：
```powershell
mkdir C:\envs\InSAR
tar -xzf insar_env.tar.gz -C C:\envs\InSAR
C:\envs\InSAR\Scripts\conda-unpack.exe
```

如果没有 `tar`，可使用 7-Zip 解压。

在 `.env` 中配置：
```
PYTHON_PATH=C:\envs\InSAR\python.exe
```
`PYTHON_PATH` 也支持命令名（如 `python`），`scripts/start_app.ps1` 会通过 `Get-Command` 解析真实可执行文件路径。  
若使用 Conda，建议先激活 `InSAR` 环境后再启动，或直接填入该环境的 `python.exe` 绝对路径。

也可使用 Conda 原生模式（推荐）：
```
CONDA_EXE=D:\anaconda3\Scripts\conda.exe
CONDA_ENV_NAME=InSAR
```
当 `CONDA_ENV_NAME` 非空时，`scripts/start_app.ps1` 会优先使用  
`CONDA_EXE` 定位目标环境并解析其 `python.exe`，随后使用该解释器执行数据库检查、初始化、后端与 Worker 启动流程（避免并发 `conda run` 稳定性问题）。

**不建议直接复制 venv**，二进制依赖容易失效。

---

## 8. 数据库准备

### 8.1 创建数据库
```powershell
psql -U postgres -h localhost -p 5432
CREATE DATABASE insar_management;
\q
```

### 8.2 启用 PostGIS
```powershell
psql -U postgres -h localhost -p 5432 -d insar_management
CREATE EXTENSION IF NOT EXISTS postgis;
\q
```

### 8.3 配置连接并检测
在 `.env` 中设置：
```
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/insar_management
```

手动检查连接：
```powershell
D:\anaconda3\Scripts\conda.exe run -n InSAR python scripts/check_db_connection.py
```

---

## 9. 授权（离线）

启动授权管理工具：
```powershell
D:\anaconda3\Scripts\conda.exe run -n InSAR python scripts/license_manager_gui.py
```

使用流程：
1) 选择 `.env` 路径
2) 输入到期时间（UTC，格式：`YYYY-MM-DD HH:MM:SS`，留空默认 1 年）
3) 点击“生成/续期授权”
4) 自动写入 `.env` 中的 `LICENSE_SECRET` / `LICENSE_PUBLIC_KEY`
5) 生成 `backend\license\license.lic` 与 `license_private_key.txt`

说明：
- `license_private_key.txt` 请妥善保存（用于续期）。
- 服务器只需要公钥与授权文件，不要部署私钥。
- 建议仅在离线管理机保存私钥文件，避免在业务服务器落盘或备份私钥。
- 离线授权属于本地合规校验机制：可显著提高误用门槛，但不等同于“对拥有服务器管理员权限场景”的绝对防篡改。

授权相关接口：
```
/api/license/status
/api/license/upload   （仅管理员可调用）
/api/license/refresh  （仅管理员可调用）
```

返回约束（安全脱敏）：
- `GET /api/license/status` 默认仅返回 `ok`、`reason`、`expires_at`。
- 当请求携带管理员会话时，`GET /api/license/status` 会额外返回调试字段（如 `fingerprint`、`license_path`）。

---

## 10. 关键配置（.env）
常用参数示例：
```
# 服务显示地址（用于启动日志与前端展示）
SERVER_HOST=192.168.1.100

# 后端端口（示例使用 18000，避免与常见本机服务冲突）
PORT=18000

# 前端开发模式代理目标（仅 npm run dev 使用）
VITE_BACKEND_TARGET=http://localhost:18000

# Python / Nginx 路径
PYTHON_PATH=C:\envs\InSAR\python.exe
CONDA_EXE=D:\anaconda3\Scripts\conda.exe
CONDA_ENV_NAME=InSAR
NGINX_PATH=C:\nginx-1.29.4\nginx.exe

# IDL / ENVI 路径与 worker 管理目录
IDL_EXECUTABLE=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idl.exe
IDL_WORKBENCH_PATH=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlde.exe
IDL_WORKER_SCRIPT_SOURCE_DIR=Z:\Code\Insar_management_system_v2\IDL\origin
IDL_WORKER_SCRIPT_DIR=Z:\Code\Insar_management_system_v2\IDL\worker_managed
IDL_WORKER_RUNTIME_DIR=Z:\Code\Insar_management_system_v2\backend\runtime\idl_worker
IDL_WORKER_DEFAULT_TIMEOUT_SECONDS=14400
IDL_WORKER_MAX_TIMEOUT_SECONDS=43200
IDL_WORKER_TEMP_DIRECTORY=D:\Sarscape_IDL_Area

# IDL worker 默认任务参数（可被 API 请求体覆盖）
IDL_IMPORT_ROOT_DIR=
IDL_IMPORT_NUM_TO_PROCESS=0
IDL_DINSAR_ROOT_DIR=
IDL_DINSAR_DEM_BASE_FILE=
IDL_DINSAR_NUM_TO_PROCESS=0
IDL_DINSAR_TARGET_GROUND_RESOLUTION_M=10.0
IDL_DINSAR_FILTER_METHOD=GOLDSTEIN
IDL_DINSAR_UNWRAP_COH_THRESHOLD=0.05
IDL_DINSAR_GCP_COH_THRESHOLD=0.7
IDL_DINSAR_GCP_NUMBER=100
IDL_DINSAR_GEOCODING_COH_THRESHOLD=0.0
IDL_DINSAR_GEOCODING_PIXEL_SIZE_M=10.0

# 授权
LICENSE_SECRET=...
LICENSE_PUBLIC_KEY=...
LICENSE_PATH=...\backend\license\license.lic
LICENSE_STATE_PATH=...\backend\license\license_state.json
MAX_LICENSE_UPLOAD_BYTES=1048576

# 鉴权（Session + Cookie）
INIT_ADMIN_USERNAME=admin
INIT_ADMIN_PASSWORD=<REQUIRED_STRONG_PASSWORD>
INIT_ADMIN_RESET_PASSWORD=false
SESSION_TTL_HOURS=12
AUTH_SESSION_COOKIE_NAME=ims_session
AUTH_COOKIE_SAMESITE=Lax
AUTH_COOKIE_SECURE=false
AUTH_LOGIN_MAX_FAILURES=5
AUTH_LOGIN_WINDOW_SECONDS=900
AUTH_LOGIN_LOCK_SECONDS=900
AUTH_LOGIN_CLEANUP_INTERVAL_SECONDS=300

# AOI 上传限流
AOI_UPLOAD_MAX_FILES=10
AOI_UPLOAD_MAX_SINGLE_FILE_BYTES=20971520
AOI_UPLOAD_MAX_TOTAL_BYTES=104857600

# 扫描请求边界
MAX_SCAN_DIRECTORY_COUNT=64
MAX_SCAN_PATH_LENGTH=2048

# 高频列表查询边界与超时
LIST_QUERY_MAX_LIMIT=2000
LIST_QUERY_MAX_OFFSET=200000
LIST_QUERY_MAX_WINDOW=202000
LIST_QUERY_TIMEOUT_MS=20000
RADAR_SEARCH_OPTIONS_MAX_VALUES=5000
RADAR_IMAGING_DATES_MAX_VALUES=5000

# 队列入队边界（防止异常大 payload 冲击数据库）
JOB_QUEUE_MAX_PAYLOAD_BYTES=524288
JOB_QUEUE_MAX_ATTEMPTS=10
JOB_QUEUE_MAX_PRIORITY_ABS=1000
JOB_QUEUE_MAX_ID_LENGTH=128
JOB_QUEUE_MAX_TYPE_LENGTH=64

# 任务/日志查询边界（防止单次查询放大）
TASK_ACTIVE_DEFAULT_LIMIT=100
TASK_ACTIVE_MAX_LIMIT=500
TASK_LOG_DEFAULT_LIMIT=100
TASK_LOG_MAX_LIMIT=1000
TASK_QUERY_MAX_OFFSET=500000
MONITOR_LOG_DEFAULT_LIMIT=50
MONITOR_LOG_MAX_LIMIT=200
MONITOR_LOG_MAX_OFFSET=500000

# 批次与复制请求边界
TASK_BATCH_MAX_ITEMS=5000
TASK_BATCH_TEXT_MAX_LENGTH=256
TASK_BATCH_REMARK_MAX_LENGTH=2000
TASK_BATCH_LIST_DEFAULT_LIMIT=200
TASK_BATCH_LIST_MAX_LIMIT=1000
TASK_BATCH_LIST_MAX_OFFSET=500000
COPY_BATCH_TEXT_MAX_LENGTH=2048
COPY_BATCH_MAX_STATUS_COUNT=8

# 工作流请求边界
WORKFLOW_MAX_STEPS=200
WORKFLOW_MAX_DEPENDS=32
WORKFLOW_TEXT_MAX_LENGTH=128

# AI 地图分析请求边界
AI_ANALYZE_MAP_MAX_IMAGES=4
AI_ANALYZE_MAP_MAX_IMAGE_BASE64_CHARS=12582912
AI_ANALYZE_MAP_PROMPT_MAX_CHARS=8000

# 生产环境请使用白名单，不建议 *
CORS_ORIGINS=http://192.168.1.100
CORS_ALLOW_CREDENTIALS=true
CORS_STRICT_MODE=false

# 统计缓存（秒）
STATS_CACHE_TTL_SECONDS=120

# 预览缓存（可选）
RADAR_THUMBNAIL_MAX_SIZE=1600
RADAR_CACHE_WORKERS=2

# 灾害点数据源
HAZARD_POINTS_DIR=Z:\Code\Insar_management_system_v2\backend\Point
HAZARD_POINTS_FILENAME=Point.shp
```

鉴权说明：
- 系统不提供公开注册入口。
- 首次启动或重置管理员密码时，`INIT_ADMIN_PASSWORD` 为必填项（不再提供默认密码回退）。
- `scripts/init_db.py` 会检查管理员账号；若不存在则按 `INIT_ADMIN_*` 自动创建。
- 如需强制重置管理员密码：将 `INIT_ADMIN_RESET_PASSWORD=true`（仅重置当次生效，完成后建议改回 `false`）。
- 内网 HTTP 部署请保持 `AUTH_COOKIE_SECURE=false`；若启用 HTTPS，请改为 `true`。
- 登录防爆破参数：`AUTH_LOGIN_MAX_FAILURES`（窗口内失败阈值）、`AUTH_LOGIN_WINDOW_SECONDS`（失败统计窗口）、`AUTH_LOGIN_LOCK_SECONDS`（触发后锁定时长）、`AUTH_LOGIN_CLEANUP_INTERVAL_SECONDS`（限流状态表后台清理间隔，秒）。
- AOI 上传限流参数：`AOI_UPLOAD_MAX_FILES`（上传文件数量上限）、`AOI_UPLOAD_MAX_SINGLE_FILE_BYTES`（单文件字节上限）、`AOI_UPLOAD_MAX_TOTAL_BYTES`（同次请求总字节上限）。超限时接口返回 `HTTP 400`。
- 扫描请求边界参数：`MAX_SCAN_DIRECTORY_COUNT`（单次扫描请求目录数量上限）、`MAX_SCAN_PATH_LENGTH`（单路径字符串长度上限）。
- 高频列表查询边界参数：`LIST_QUERY_MAX_LIMIT`（单页最大返回条数）、`LIST_QUERY_MAX_OFFSET`（最大偏移）、`LIST_QUERY_MAX_WINDOW`（`limit+offset` 窗口上限），`LIST_QUERY_TIMEOUT_MS`（PostgreSQL 列表查询超时，毫秒）；`RADAR_SEARCH_OPTIONS_MAX_VALUES` / `RADAR_IMAGING_DATES_MAX_VALUES` 用于限制雷达检索选项和成像日期去重结果规模。
- 队列入队边界参数：`JOB_QUEUE_MAX_PAYLOAD_BYTES`（单任务 payload 字节上限）、`JOB_QUEUE_MAX_ATTEMPTS`（重试次数上限）、`JOB_QUEUE_MAX_PRIORITY_ABS`（优先级绝对值上限）、`JOB_QUEUE_MAX_ID_LENGTH`（关联 ID 字符上限）、`JOB_QUEUE_MAX_TYPE_LENGTH`（任务类型字符串上限）。
- 任务/日志查询边界参数：`TASK_ACTIVE_DEFAULT_LIMIT`、`TASK_ACTIVE_MAX_LIMIT`（`GET /api/tasks/active` 分页上限），`TASK_LOG_DEFAULT_LIMIT`、`TASK_LOG_MAX_LIMIT`、`TASK_QUERY_MAX_OFFSET`（任务日志分页上限），`MONITOR_LOG_DEFAULT_LIMIT`、`MONITOR_LOG_MAX_LIMIT`、`MONITOR_LOG_MAX_OFFSET`（`GET /api/monitor/logs` 分页与偏移上限）。
- 批次与复制请求边界参数：`TASK_BATCH_MAX_ITEMS`（单批次条目数上限）、`TASK_BATCH_TEXT_MAX_LENGTH`（批次名称/方向长度上限）、`TASK_BATCH_REMARK_MAX_LENGTH`（条目备注长度上限）、`TASK_BATCH_LIST_DEFAULT_LIMIT` / `TASK_BATCH_LIST_MAX_LIMIT` / `TASK_BATCH_LIST_MAX_OFFSET`（批次列表与明细分页上限）、`COPY_BATCH_TEXT_MAX_LENGTH`（复制接口 `batch_id`/`dest_dir` 长度上限）、`COPY_BATCH_MAX_STATUS_COUNT`（复制状态列表数量上限）。
- 工作流请求边界参数：`WORKFLOW_MAX_STEPS`（单工作流步骤上限）、`WORKFLOW_MAX_DEPENDS`（单步骤依赖数上限）、`WORKFLOW_TEXT_MAX_LENGTH`（工作流文本字段长度上限）。
- AI 地图分析请求边界参数：`AI_ANALYZE_MAP_MAX_IMAGES`（单次请求图片数上限）、`AI_ANALYZE_MAP_MAX_IMAGE_BASE64_CHARS`（单图 Base64 长度上限）、`AI_ANALYZE_MAP_PROMPT_MAX_CHARS`（prompt 长度上限）。
- 授权上传默认采用“先校验后替换”的原子更新流程；无效授权不会覆盖当前在用授权文件。
- `MAX_LICENSE_UPLOAD_BYTES` 用于限制授权上传文件大小（默认 1MB）。
- `CORS_ORIGINS` 建议配置为明确白名单；生产环境不建议使用 `*`。
- 当 `CORS_STRICT_MODE=true` 时，若 `CORS_ORIGINS` 包含 `*` 且 `CORS_ALLOW_CREDENTIALS=true`，服务将拒绝启动。
- 严禁将包含真实 `DATABASE_URL`、`LICENSE_SECRET` 的 `.env` 提交到代码仓库或共享目录。
- 管理员可通过 `GET /api/auth/audit-logs?limit=200` 查看最近审计日志（仅 admin 可访问）。
- 许可文件上传与刷新接口（`POST /api/license/upload`、`POST /api/license/refresh`）仅允许管理员会话访问。
- 前端入口：左侧「运行维护」分组下新增「用户管理」「审计日志」两个管理员面板。

源影像缓存说明：
- 系统在扫描雷达源数据时，会尝试在每个场景目录中查找预览图（jpg/jpeg/png/webp/bmp/tif/tiff）并生成 WebP 缓存。
- 雷达缓存目录：`backend/image_cache/radar_geo/`（主）与 `backend/image_cache/radar_raw/`（回退）。
- D-InSAR 缓存目录：`backend/image_cache/dinsar/`（统一使用 `ID_{id}_{name}.webp`）。
- 前端「数据列表」支持源影像单条显示/隐藏与“一键显示/一键隐藏”。
- 长期稳定方案（后端预纠正缓存，B 方案）设计见：`docs/RADAR_PREVIEW_SCHEME_B.md`。

---

## 11. 启动方式

### 11.1 一键启动（推荐）
双击：
```
start_system.bat
```

或命令行：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_app.ps1
```

说明：
- 启动链路保持不变：`start_system.bat` -> `scripts/start_app.ps1` -> `check_db_connection.py` -> `init_db.py`。
- `scripts/start_app.ps1` 中 `PYTHON_PATH` 支持“绝对路径”或“命令名”（例如 `python`）；脚本会先解析为可执行文件再调用。
- `init_db.py` 会在“建库/校验修复”后执行鉴权初始化（自动补齐管理员账号）。
- 扫描调度默认 Manual-only，不会后台定时自动触发；需通过前端或 `/api/monitor/run-now` 手动触发扫描。
- 默认不执行破坏性重建；若确需重建，请显式设置 `DB_SCHEMA_RESET_ON_MISMATCH=true`。

### 11.2 仅启动后端（无 Nginx）
```powershell
D:\anaconda3\Scripts\conda.exe run -n InSAR python run_backend.py
```

说明：后端默认绑定 `127.0.0.1`，仅本机访问。

### 11.3 后台队列 Worker（必需）
系统后台任务已改为“入队执行”，必须启动 Worker 才会真正执行扫描、AI、解包、复制等任务。

启动方式：
```powershell
D:\anaconda3\Scripts\conda.exe run -n InSAR python run_worker.py
```

说明：
- `start_system.bat` 已包含 Worker 启动。
- 若只运行 `run_backend.py`，请手动启动 `run_worker.py`。
- 数据分发/复制任务仅从“批次”读取，不再支持 Excel。
- `POST /api/monitor/run-now` 的 `target` 仅支持：`radar`、`orbit`、`dinsar`（留空表示全部）。

Worker 可选环境变量：
```
JOB_WORKER_POLL_INTERVAL=1.0   # 轮询间隔（秒）
JOB_WORKER_CONCURRENCY=1       # 并发执行数
JOB_WORKER_JOB_HEARTBEAT_INTERVAL=5   # 运行中作业心跳上报间隔（秒）
JOB_WORKER_STALE_RECOVER_INTERVAL=15  # 僵尸 RUNNING 作业回收检查间隔（秒）
JOB_WORKER_STALE_RUNNING_SECONDS=300  # 判定作业僵尸的超时阈值（秒）
IDL_JOB_MAX_ATTEMPTS=6                # IDL 队列任务最大重试次数
```

ENVI/SARscape 集成（envipyengine）：
- 执行引擎：`envipyengine`（pip 包）→ `taskengine.exe` 子进程
- 工作流在独立 Python 子进程中执行，隔离 FastAPI 主进程
- D-InSAR 工作流自动检测未导入数据，先执行 Import 再处理 D-InSAR（智能串联）
- DEM 路径为系统级配置，存储在 `.env` 的 `IDL_DINSAR_DEM_BASE_FILE`
- 运行日志目录：`backend/runtime/idl_worker/`

ENVI 关键环境变量：
```
IDL_EXECUTABLE=...                       # IDL 可执行路径
IDL_WORKBENCH_PATH=...                   # IDL Workbench 路径
IDL_WORKER_RUNTIME_DIR=...               # 运行日志目录
IDL_WORKER_DEFAULT_TIMEOUT_SECONDS=14400 # 单任务执行超时（秒）
IDL_WORKER_MAX_TIMEOUT_SECONDS=43200     # 单任务允许最大超时（秒）
IDL_DINSAR_DEM_BASE_FILE=...             # DEM 路径（D-InSAR 必需）
```

API 端点（管理员）：
```
GET  /api/idl/status              — 系统状态（含 DEM 路径和可用性）
POST /api/idl/launch-workbench    — 启动 IDL Workbench
POST /api/idl/inspect/import      — Import 预检查
POST /api/idl/inspect/dinsar      — D-InSAR 预检查（含 Import 状态检测）
POST /api/idl/jobs/import         — 提交 Import 任务
POST /api/idl/jobs/dinsar         — 提交 D-InSAR 任务
GET  /api/idl/jobs/recent         — 最近运行记录
```

提交任务请求体示例：
```json
{
  "root_dir": "Z:/Test_data/Test_IDL_1",
  "num_to_process": 0,
  "timeout_seconds": 14400
}
```

说明：
- 提交后会创建系统任务并入队，由 `run_worker.py` 消费执行。
- D-InSAR 工作流自动检测未导入数据，先执行 Import 再处理（智能串联）。
- DEM 路径从 `.env` 读取，不需要在请求中传递。
- Worker 在执行时会周期性更新作业心跳；若进程崩溃导致作业长期停留 `RUNNING`，会自动回收。
- 运行日志保存在 `IDL_WORKER_RUNTIME_DIR/` 下。

### 11.4 前端构建（仅构建时需要）
```powershell
cd frontend
npm install
npm run build
```

说明：
- 前端日期控件已统一为 `flatpickr`（原生实例接入），无需浏览器原生 `input[type=date]`。
- 若采用“本地构建后上传 `frontend/dist`”部署方式，服务器端无需额外安装前端依赖包。

### 11.5 离线切片底图切换
前端从 `frontend/public/tiles/` 读取离线切片，当前支持三套（统一 `webp`）：
- `tiles/google_image/{z}/{x}/{y}.webp`
- `tiles/gaode_image/{z}/{x}/{y}.webp`
- `tiles/gaode_shp/{z}/{x}/{y}.webp`

地图右上角「底图」按钮可在三套底图间切换（默认 `gaode_shp`，便于仅下载高德矢量时直接可用）。

缩放级别已放开到 0-16；如需调整默认底图或级别，请修改 `frontend/src/App.jsx` 中的 `TILE_LAYER_DEFAULT_KEY` 与 `TILE_LAYER_OPTIONS`。

行政区边界叠加图层（地图参考边界）默认读取：
- `frontend/public/geojson/全国行政区.geojson`

建议使用 UTF-8 无 BOM 编码，且文件体积较大时优先通过 `public/` 静态加载（不要直接放入 `frontend/src` 参与打包）。

地图左上角新增「区域定位」按钮（可折叠），复用行政区二级级联（省/市）：
- 可只选择到省级或市级进行定位。
- 定位时会调用后端 `/api/aoi/regions/{tree_id}/geometry` 获取边界并自动缩放到该区域。
- 定位成功后，面板会显示“当前定位”行政区名称，方便与当前视角对照。
- 面板支持「清除定位高亮」，可一键移除地图上的定位边界高亮。
- 依赖 `backend/geojson/层级映射.json` 与 `backend/geojson/全国行政区.geojson` 的行政区数据。

---

## 12. 访问与验证
- **前端**：`http://SERVER_HOST`
- **后端（内部）**：`http://127.0.0.1:8000`
- **接口文档（内部）**：`http://127.0.0.1:8000/docs`

前端语言切换：
- 顶部状态栏新增 `中文 / EN` 切换按钮。
- 切换结果会保存在浏览器本地（`localStorage`），刷新页面后保持上次语言选择。
- 站内文案翻译映射位于 `frontend/src/i18n/translations.js`，新增界面文案时需同步补充映射，避免中英文混杂。
- 第二轮已补齐高频业务模块（批次/分发、运维自检、用户管理、审计日志、IDL 自动化、统计面板）常用文案映射。
- 第三轮已支持运行日志双语展示：右侧日志栏、数据监控日志、数据分发任务日志会随语言切换进行前端翻译。
- 当前词典已覆盖常见后端运行日志模板（扫描、拷贝、解包、任务生命周期）；若新增日志模板，建议同步补充词典条目。
- 收口说明：当前前端可见业务文案已基本完成双语覆盖，剩余英文主要为样式类名/技术标识（不面向客户显示）。

---

## 13. 常用脚本说明
- `start_system.bat`：一键启动入口（调用 PowerShell 启动脚本）
- `scripts/start_app.ps1`：启动后端 + Nginx，并执行数据库检查
- `run_backend.py`：仅启动后端服务
- `run_worker.py`：后台任务 Worker（队列执行）
- `scripts/check_db_connection.py`：数据库连接检查
- `scripts/init_db.py`：数据库结构检查与初始化
- `scripts/license_manager_gui.py`：授权文件生成与续期
- `scripts/get_fingerprint.py`：机器指纹获取
- `scripts/unpack_archives.py`：解包与数据整理（支持 tar.gz）
- `scripts/pack_environment.bat`：打包 Conda 环境（如使用）
- `scripts/auth_smoke_check.py`：鉴权冒烟检查（401/403/200 核心链路）

---

## 14. 常见问题

1) **Python executable not found**  
检查 `.env` 中 `PYTHON_PATH` 是否正确；可使用绝对路径，或在已激活 Conda 环境后填写 `python`。  
如启用 Conda 原生模式，还需确认 `CONDA_EXE` 与 `CONDA_ENV_NAME` 配置正确。

2) **数据库连接失败**  
确认 PostgreSQL 服务已启动、`DATABASE_URL` 正确，并运行 `scripts/check_db_connection.py` 测试。

3) **授权无效/过期**  
使用授权工具续期，检查系统时间是否回退过多。

4) **前端打不开但后端正常**  
确认 Nginx 已启动、`NGINX_PATH` 正确、前端已构建。  
若启动时报 `no "events" section in configuration`，请检查 `nginx/nginx.conf` 是否为空或损坏，需恢复包含 `events {}` 与 `http {}` 的完整配置。

5) **后端端口被占用（Address already in use / Access denied）**  
`scripts/start_app.ps1` 启动前会检查 `PORT` 对应监听占用；若被占用会直接报错并终止。  
请停止占用该端口的进程，或在 `.env` 中修改 `PORT` 后重试。  
脚本会在启动时自动把 `nginx/nginx.conf` 中的 `proxy_pass` 同步到该 `PORT`，无需手动改 Nginx 反向代理端口。

6) **后端或 Worker 启动后立即退出**  
`scripts/start_app.ps1` 会在拉起后端与 Worker 后做快速存活检查；若进程秒退，脚本会立即报错并退出。  
请优先检查终端输出与后端日志，常见原因包括端口冲突、依赖缺失、环境变量配置错误。

最小可用 `nginx.conf` 模板（按需替换路径，`proxy_pass` 端口需与 `.env` 的 `PORT` 一致）：
```nginx
worker_processes  1;

events {
    worker_connections  1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    sendfile        on;
    keepalive_timeout  65;

    server {
        listen       80;
        server_name  localhost;

        root   "G:/Code/Insar_management_system_v2/frontend/dist";
        index  index.html;

        location / {
            try_files $uri $uri/ /index.html;
        }

        location /api/ {
            proxy_pass http://127.0.0.1:18000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /image_cache/ {
            alias  "G:/Code/Insar_management_system_v2/backend/image_cache/";
        }
    }
}
```

### 14.1 鉴权与登录排查

- **`GET /api/auth/me` 返回 `401`**：未登录状态下这是正常现象，前端应显示登录页。  
- **`POST /api/auth/login` 返回 `401`**：用户名或密码错误，先核对 `.env` 中 `INIT_ADMIN_USERNAME/INIT_ADMIN_PASSWORD`；若为首次启动且 `INIT_ADMIN_PASSWORD` 为空，初始化会直接报错。  
- **`POST /api/auth/login` 返回 `429`**：触发登录限流/锁定，等待 `Retry-After` 秒后重试，或调整 `AUTH_LOGIN_MAX_FAILURES / AUTH_LOGIN_WINDOW_SECONDS / AUTH_LOGIN_LOCK_SECONDS`。  
- **登录后接口仍持续 `401`**：检查浏览器是否携带 Cookie（请求需 `withCredentials=true`），并确认反向代理没有丢弃 `Set-Cookie` 头。  
- **需要重置管理员密码**：设置 `INIT_ADMIN_RESET_PASSWORD=true`，重启一次系统完成重置后再改回 `false`。  
- **Cookie 在内网 HTTP 不生效**：确认 `AUTH_COOKIE_SECURE=false`；只有 HTTPS 场景才应设置为 `true`。  
- **需要核查谁触发了高风险写操作**：使用 `GET /api/auth/audit-logs?limit=200`（管理员会话）。  
- **需要快速回归鉴权链路**：后端启动后运行 `D:\anaconda3\Scripts\conda.exe run -n InSAR python scripts/auth_smoke_check.py --base-url http://127.0.0.1:8000`。  

5) **任务一直处于“执行中”**  
确认 `run_worker.py` 已启动（或使用 `start_system.bat` 一键启动）。
- 若期望系统自动周期扫描：当前默认是手动模式（Manual-only），请使用前端“立即扫描”或调用 `POST /api/monitor/run-now`。

6) **配对结果异常或为空**  
- 影像日期需为 `YYYYMMDD` 格式（系统从文件夹名解析）。  
- AOI 支持 **SHP 上传** 与 **GeoJSON 传参**（`aoi_geojson`）两种模式。  
- AOI Shapefile 建议使用 EPSG:4326（WGS84），否则需先进行投影转换。  
- 若使用“行政区选择”，请准备标准 GeoJSON（见 17.7），并确保 `features[*].properties.treeID` 与 `backend/geojson/层级映射.json` 对应。

7) **灾害点导入为 0 条**  
请确保 Shapefile 中存在唯一标识字段（优先 `TYBH`），并具备经纬度或点几何：  
- 必填：`TYBH`（唯一编号）  
- 推荐：`hazard_type` / `hazard_name` / `city` / `county` / `township`  
- 坐标：点几何优先；若无几何，则读取经度/纬度字段  
数据源路径：`.env` 中 `HAZARD_POINTS_DIR` + `HAZARD_POINTS_FILENAME`（默认 `backend/Point/Point.shp`）。

8) **Excel 功能找不到了**  
Excel 导入/导出已移除，请使用“任务批次 + 数据分发”流程完成复制与人工状态管理。

9) **日志出现 “got Future attached to a different loop”**  
该错误通常由**子线程内创建事件循环并复用 asyncpg 连接**引起，多发生在自建 `asyncio.run(...)` 的耗时任务里。  
处理方式：  
- 确保所有耗时任务由 Worker 执行（统一事件循环），避免在线程内自行 `asyncio.run`。  
- 重启后端与 Worker 以释放异常连接。  
- 如仍复现，请检查是否有自定义脚本在子线程内直接调用数据库。  
  
  ---

## 15. 数据库结构自动对齐（重要）
系统每次启动会进行数据库结构检查：
- 首次启动：自动创建表结构。
- 后续启动：校验表/列/类型/可空性。
- 不一致（默认）：不做破坏性重建，仅补齐缺失结构并输出告警。
- 不一致（显式开启）：仅当 `DB_SCHEMA_RESET_ON_MISMATCH=true` 时执行 drop/recreate 重建。

推荐在生产/内网环境保持如下默认值（保留历史数据）：
```
DB_SCHEMA_RESET_ON_MISMATCH=false
```

### 15.1 SQL 函数更新说明
若修改了 `backend/migrations/002_spatial_functions.sql`（例如配对函数 `find_dinsar_pairs`），需要执行一次：
- 重新启动系统（`start_system.bat` 会自动调用 `scripts/init_db.py`）
- 或手动运行 `D:\anaconda3\Scripts\conda.exe run -n InSAR python scripts/init_db.py`

### 15.2 空间函数迁移（必需）
启动时会自动执行以下迁移文件（用于空间函数、视图与日志表）：
- `backend/migrations/001_st_intersection_agg.sql`
- `backend/migrations/002_spatial_functions.sql`

如需手工修改，请保证 SQL 语法正确且使用 UTF-8 编码。

---

## 16. 工作流与任务队列（新增）
后台任务已改为队列执行（DB Job Queue）：
- 复制、解包、扫描、AI 训练/预测/诊断等任务都会入队。
- Worker 执行任务并更新状态，前端可查看任务进度。
- 所有耗时任务在 Worker 的事件循环中执行，避免跨事件循环导致的 asyncpg 连接异常。

工作流 API：
- `POST /api/workflow/runs` 创建工作流实例
- `GET /api/workflow/runs/{run_id}` 查询运行状态与步骤

任务查询 API（新增分页参数）：
- `GET /api/tasks/active?limit=100&offset=0`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/logs?limit=100&offset=0`
- `GET /api/monitor/logs?limit=50&offset=0`

---

## 17. 任务批次管理（替代 Excel）
为适配 IDL 线下生产流程，D-InSAR 与 PS-InSAR 任务改为“批次”形式存储在数据库：
- 前端创建批次（不再使用 Excel 导入/导出）。
- 可按批次查看明细、手动标记完成、添加备注。
- 数据分发从批次中读取任务条目并复制到目标目录。
- Excel 导入/导出接口已移除。

相关 API：
- `POST /api/task-batches/dinsar`
- `GET /api/task-batches/dinsar?limit=200&offset=0`
- `GET /api/task-batches/dinsar/{batch_id}/items?limit=200&offset=0`
- `PATCH /api/task-batches/dinsar/{batch_id}/complete-all`
- `PATCH /api/task-batches/dinsar/items/{item_id}`
- `POST /api/task-batches/ps`
- `GET /api/task-batches/ps?limit=200&offset=0`
- `GET /api/task-batches/ps/{batch_id}/items?limit=200&offset=0`
- `PATCH /api/task-batches/ps/{batch_id}/complete-all`
- `PATCH /api/task-batches/ps/items/{item_id}`

数据分发 API：
- `POST /api/tools/copy-ps-stack` (body: `batch_id`, `dest_dir`, `copy_statuses?`)
- `POST /api/tools/copy-dinsar-pairs` (body: `batch_id`, `dest_dir`, `copy_statuses?`)
- `GET /api/tools/copy-status/{task_id}?limit=100&offset=0`

### 17.1 前端操作流程（推荐）
1) **生成候选列表**  
   - D-InSAR：左侧“干涉对配对”生成 pairs。  
   - PS-InSAR：左侧“PS 时序栈”生成 stack。  

2) **保存为批次**  
   - 点击“保存批次”，系统将当前列表写入数据库。  
   - 批次会自动生成 `batch_id`，支持自定义名称。  
   - 保存成功后前端会自动切换到“任务批次”并定位到新建批次。  

3) **批次管理与人工标注**  
   - 在右侧“任务批次”面板选择 D-InSAR 或 PS。  
   - 进入某批次后：  
      - 修改每条任务状态（PENDING / IN_PROGRESS / COMPLETED / FAILED）  
      - 接口仅接受上述四种状态值（大小写不敏感，非法值会返回 `400`）  
      - 为每条任务添加备注（如：文件损坏、未完成原因）  
      - 支持“一键全部完成”  

4) **数据分发（复制）**  
   - 进入“数据分发”面板选择批次与目标目录后启动复制。  
   - 可按任务状态筛选复制（默认仅复制 `COMPLETED`）。  
   - 复制任务由 Worker 执行，进度可在前端日志中查看。  
   - 若出现部分文件复制失败，任务最终会标记为 `FAILED`（不再误报成功）。  

### 17.2 批次数据结构（数据库）
系统会创建以下表：
- `dinsar_task_batches` / `dinsar_task_items`  
- `ps_task_batches` / `ps_task_items`  

每条 item 包含：
- `status`（人工状态）
- `remark`（备注）
- 任务路径与元数据（用于复制与后续追踪）

### 17.3 数据分发复制规则
**PS-InSAR：**  
- 复制源优先使用 `{original_path}_envi_import`（若存在）  
- 否则使用原始路径  

**D-InSAR：**  
- 为每个任务创建子目录：  
  - `<dest>/<task_name>/master`  
  - `<dest>/<task_name>/slave`  
- 若源目录内存在 `envi_import` 且非空，优先复制 `envi_import`  

### 17.4 Excel 迁移说明
历史流程若依赖 Excel：  
- 现在请在系统中生成批次并使用“数据分发”复制。  
- 复制任务仅接收 `batch_id`，不再接收 Excel 文件。  

### 17.5 API 示例（简化）
创建 D-InSAR 批次：
```json
POST /api/task-batches/dinsar
{
  "name": "DINSAR_20260208",
  "pairs": [
    {
      "task_name": "T001",
      "master": {"file_path": "D:/data/master1", "...": "..."},
      "slave": {"file_path": "D:/data/slave1", "...": "..."},
      "time_baseline_days": 12,
      "spatial_baseline_meters": 1234.5
    }
  ]
}
```

复制 D-InSAR 批次：
```json
POST /api/tools/copy-dinsar-pairs
{
  "batch_id": "xxxx-xxxx-xxxx",
  "dest_dir": "D:/IDL/tasks/dinsar",
  "copy_statuses": ["COMPLETED"]
}
```

### 17.6 配对参数说明（新增）
`/api/find-pairs` 支持以下可选参数（表单提交）：
- `require_same_imaging_mode`：是否要求主/辅影像成像模式一致（默认 `true`，空值视为不匹配）
- `require_same_polarization`：是否要求主/辅影像极化一致（默认 `true`，空值视为不匹配）
- `aoi_overlap_threshold`：AOI 覆盖比例阈值，计算方式为 `Area(image ∩ AOI) / Area(AOI)`，需主/辅影像同时满足
- `aoi_geojson`：可选，标准 GeoJSON 字符串（与 `files` 二选一或都不传）

系统会在候选配对数超过 3000 时返回提示信息。若数据库配对函数不可用，仅在影像数量 ≤1500 时允许回退计算，并返回强提醒。

前端已提供“成像模式一致 / 极化一致 / AOI 覆盖率阈值”的开关与输入框，默认关闭/0（不额外限制）。

### 17.7 行政区选择（SHP/行政区二选一）
生产规划弹窗中，AOI 来源支持：
- 上传 `SHP`（兼容旧流程）
- 行政区选择（省/市二级联动）

后端新增接口：
- `GET /api/aoi/regions/children?parent_tree_id=1`
- `GET /api/aoi/regions/{tree_id}/geometry`

相关环境变量（后端 `.env`）：
```
AOI_REGION_INDEX_FILE=backend/geojson/层级映射.json
AOI_REGION_GEOJSON_FILE=backend/geojson/全国行政区.geojson
```

### 17.8 行政区 GeoJSON 数据规范（需提前准备）
建议提供一个标准 `FeatureCollection` 文件（UTF-8），示例：
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "treeID": "1-23-4",
        "name": "示例地市",
        "level": "city"
      },
      "geometry": {
        "type": "MultiPolygon",
        "coordinates": [[[ [116.0, 39.0], [116.2, 39.0], [116.2, 39.2], [116.0, 39.2], [116.0, 39.0] ]]]
      }
    }
  ]
}
```

字段要求：
- 必填：`properties.treeID`（与 `层级映射.json` 一致）
- 可选：`properties.name`、`properties.level`
- `geometry` 建议使用 EPSG:4326（经纬度）

说明：
- 当前前端行政区选择为省/市二级，不提供区县单独选择入口。
- 若仅提供区县级数据，系统会在省/市选择时自动合并其下级边界。
- 若仅提供到市级数据，前端可直接按市级选择，后端会在必要时回退到最近可用上级边界（市→省）。
- 若 `treeID` 不匹配或缺失，行政区选择将无法返回 AOI 边界。

---

## 18. 运维自检（新增）
系统提供运维自检接口与前端面板，用于快速判断关键服务状态。

后端接口：
```
GET /api/health
```

返回约束（安全脱敏）：
- 未登录或只读会话：返回最小健康摘要（`ok/timestamp` + 各子系统布尔状态与基础计数）。
- 管理员会话：返回完整诊断信息（包含错误详情、Worker 列表、外部服务状态细节）。

检查内容：
- 数据库连接 / PostGIS 扩展 / 关键表结构
  - 关键表覆盖包含：任务与队列（`system_tasks/task_logs/system_jobs/scan_states`）、工作流（`workflow_defs/workflow_runs/workflow_steps/workflow_artifacts`）、鉴权（`auth_users/auth_sessions/auth_audit_logs/auth_rate_limits`）等核心表
- Worker 心跳
- IDL/ENVI 状态
- Ollama 连通性
- Nginx 连通性
- 一致性异常汇总（来自 `GET /api/statistics`）：
  - D-InSAR 缓存：数据库标记与实际缓存文件是否一致
  - 源影像预览缓存：`READY` 状态与 `radar_geo/radar_raw` 文件是否一致
  - XML 读取：是否检测到 XML 以及关键字段是否成功入库

相关环境变量：
```
JOB_WORKER_HEARTBEAT_INTERVAL=5   # Worker 心跳上报间隔（秒）
JOB_WORKER_HEALTH_TIMEOUT=60      # 运维自检判定 Worker 离线的阈值（秒）
```

前端入口：
- 左侧「系统工具」->「运维自检」
- 顶部状态栏可一键刷新自检状态
- 运维自检面板新增「一致性检测」卡片，显示异常总数、严重/一般异常，并列出具体异常项

---

## 19. 一致性统计接口（新增）
用于检查“数据库记录、缓存文件、XML读取结果”是否一致。

后端接口：
```
GET /api/statistics
```

新增返回字段（关键）：
- `dinsar_cache_consistency`
  - `db_cached_but_file_missing_count`：数据库标记已缓存，但缓存文件不存在
  - `db_uncached_but_file_exists_count`：数据库标记未缓存，但缓存文件已存在
  - `manifest_missing_file_count`：`cache_manifest.json` 记录的缓存文件缺失
- `source_preview_consistency`
  - `preview_missing_count`：源影像预览缓存缺失（`radar_geo` 和 `radar_raw` 均不存在）
  - `db_ready_but_cache_missing_count`：数据库 `READY`，但预览缓存实际缺失
- `source_xml_consistency`
  - `xml_missing_count`：未检测到 XML
  - `xml_detected_but_unparsed_count`：检测到 XML 但关键字段未成功入库

前端展示位置：
- 「数据统计仪表盘」：新增一致性图表与异常高亮
- 「运维自检」：新增一致性异常汇总卡片（总数 / 严重 / 一般）

建议运维动作：
- 出现 `db_cached_but_file_missing_count` 或 `db_ready_but_cache_missing_count`：执行一次重扫重建缓存
- 出现 `xml_detected_but_unparsed_count`：检查对应场景 XML 格式与解析字段
- 出现 `xml_missing_count`：检查场景目录是否包含 XML 元数据文件

---

*© 2026 InSAR Management System - 致力于更高效、更智能的雷达遥感监测*
