# LandSAR D-InSAR 与 SBAS-InSAR 服务接入采购需求说明书

编制日期：2026-06-04  
适用项目：InSAR 管理系统 v2  
重点数据：陆探一号（LT-1 / LuTan-1）SAR 数据  
文档用途：供应商技术沟通、采购询价、招标需求编制

## 1. 项目背景

现有 InSAR 管理系统已经具备雷达影像资产管理、任务队列、生产任务监控、结果目录管理、D-InSAR 结果入库、SBAS-InSAR 结果 catalog、地图预览和结果查询等工程能力。系统侧按“处理器 processor + 工作流 workflow + 结果包 manifest”的方式组织外部算法服务，要求外部服务能够以稳定接口接收任务、返回结构化状态并输出可入库的标准结果。

本次拟采购 LandSAR 服务版中的陆探一号 D-InSAR 与 SBAS-InSAR 两个处理服务模块。服务应以本地部署方式运行在生产服务器上，通过 HTTP API、消息队列 API 或二者结合的方式接收处理任务。业务系统负责选择数据、创建任务、轮询或订阅任务状态、读取结果清单并完成 catalog 入库；LandSAR 服务负责实际算法生产、运行日志、状态输出和结果文件组织。

## 2. 建设目标

采购目标是获得一套可本地部署、可长期稳定运行、可由现有系统调用的 LandSAR 服务版 API 模块，重点支撑陆探一号 D-InSAR 与 SBAS-InSAR 自动化生产。

核心建设目标如下：

1. 支持陆探一号 D-InSAR 差分干涉生产，产出可入库、可预览、可下载的标准 GeoTIFF 结果。
2. 支持陆探一号 SBAS-InSAR 时序形变生产，产出语义明确、可归档、可预览、可下载的栅格或点矢量结果。
3. 支持 D-InSAR 与 SBAS-InSAR 所需的数据导入、轨道处理、DEM 处理、地理编码和结果发布能力。
4. 提供完整的任务提交、排队、状态查询、日志查询、结果查询、任务取消和错误码机制。
5. 与现有系统任务队列、数据目录、D-InSAR 结果 catalog、SBAS-InSAR 结果 catalog 和前端生产页面集成。
6. 避免业务系统直接维护算法进程、底层授权细节和非结构化运行状态。

## 3. 系统现状

### 3.1 现有业务系统

现有系统主要技术栈：

- 后端：FastAPI / Python。
- 前端：React / Vite。
- 任务队列：系统内置任务队列和任务日志表。
- 数据库：系统已有结果 catalog 和任务状态表。
- 文件组织：生产结果主要进入 `D:\production_results`，临时工作目录可配置。

### 3.2 现有系统接入边界

现有系统侧可提供以下集成条件：

- 可配置的源数据目录、工作目录、结果目录和 DEM 路径。
- 可按任务生成 `run_id`、`task_id`、`job_id` 并维护任务日志。
- D-InSAR 结果目录支持按 pair/run 组织。
- SBAS-InSAR 结果目录支持按 processor/run 组织。
- 结果 catalog 可按 `processor_code`、`engine_code`、`profile_code` 区分不同处理器。
- 前端生产页面可根据处理器能力显示不同参数和任务状态。

### 3.3 当前痛点

1. D-InSAR 和 SBAS-InSAR 长流程任务需要稳定的异步服务接口。
2. 任务状态、阶段进度、日志和错误原因需要结构化输出。
3. 错误码需要统一，便于系统侧自动诊断和前端展示。
4. 多任务并发、排队、互斥和取消能力需要明确。
5. SBAS-InSAR 输出语义、质量指标和结果文件组织需要供应商明确说明。
6. D-InSAR 与 SBAS-InSAR 需要共用统一的 API、任务状态和结果 manifest 规范，降低系统侧维护成本。

## 4. 采购范围

本次采购范围分为必选能力和可选能力。

### 4.1 必选能力

1. LandSAR 本地 API 服务部署授权。
2. 陆探一号 D-InSAR API。
3. 陆探一号 SBAS-InSAR API。
4. D-InSAR 与 SBAS-InSAR 所需的数据导入、轨道处理、DEM 支持和地理编码能力。
5. 任务队列、任务状态、日志、结果查询、任务取消和错误码接口。
6. API 调用文档、参数说明、返回值说明和示例代码。
7. 服务部署脚本、启动脚本、停止脚本、健康检查接口。
8. 授权服务部署说明和异常处理说明。
9. D-InSAR 与 SBAS-InSAR 各至少一组陆探一号样例数据的端到端验收支持。

### 4.2 可选能力

1. 陆探一号独立预处理 API。
2. 陆探一号 PS-InSAR API。
3. 多任务并发执行能力。
4. GPU 加速能力。
5. 断点续跑能力。
6. 结果自动发布为标准 GeoTIFF、COG 或瓦片服务。
7. 与第三方消息队列对接能力，例如 RabbitMQ、Redis Stream、Kafka 或 ZeroMQ。

## 5. 总体架构要求

### 5.1 部署方式

LandSAR API 服务应部署在本地生产服务器，推荐形态：

```text
InSAR 管理系统
  -> HTTP API / MQ
  -> LandSAR API Service
  -> LandSAR Engine
  -> 本地文件系统结果目录
```

服务应支持 Windows Server 环境部署，安装目录、工作目录、授权方式和监听端口均应可配置。

### 5.2 服务访问方式

服务应至少提供一种稳定接口方式：

- HTTP REST API，本地端口访问，如 `http://127.0.0.1:<port>`
- 或消息队列接口，任务提交后异步回传状态

推荐同时支持：

- HTTP API 用于任务提交、状态查询、日志查询、结果查询、健康检查。
- 消息队列用于长任务异步调度和状态通知。

### 5.3 任务执行模式

所有生产任务均应采用异步任务模式：

1. 系统提交任务。
2. LandSAR API 返回 `job_id`。
3. 系统定期查询状态或接收消息通知。
4. 任务完成后系统读取结果清单并入库。

不建议采用一次 HTTP 请求长期阻塞等待处理完成的模式。

## 6. 数据范围要求

### 6.1 支持数据类型

本次重点支持陆探一号：

- LT-1A
- LT-1B
- SLC 产品
- HH 极化优先，后续可扩展 HV/VV/VH
- 同轨、同模式、同区域数据配对和时序处理

### 6.2 输入数据形态

服务应明确支持以下至少一种输入形态。

优先要求：

```text
原始 LT-1 产品目录或压缩包
```

同时兼容：

```text
已导入的 LT1*_SLC.xml + LT1*_SLC.tif
```

对于 D-InSAR，服务应支持输入主影像和辅影像路径。  
对于 SBAS/PS，服务应支持输入多景时序数据目录。

### 6.3 辅助数据

服务应支持以下辅助数据配置：

- DEM 文件或 DEM 目录。
- 精密轨道文件目录。
- 输出目录。
- 临时工作目录。
- 可选 GCP 文件。
- 可选 GACOS 大气改正文件，若服务支持。

## 7. 功能需求

## 7.1 陆探一号支撑性预处理能力

### 7.1.1 功能目标

支撑性预处理能力用于满足 D-InSAR 和 SBAS-InSAR 生产前的数据导入、轨道处理、多视、地理编码和快视输出需要。若供应商提供独立预处理 API，应可作为后续扩展能力接入系统；若预处理仅作为 D-InSAR/SBAS-InSAR 内部阶段，也应在任务日志、阶段状态和结果 manifest 中体现。

### 7.1.2 必须支持的处理能力

1. 数据导入。
2. 精密轨道导入或轨道参数更新。
3. 多视处理。
4. 辐射定标或强度图生成。
5. 地形校正、正射校正或地理编码。
6. 输出标准 GeoTIFF。
7. 输出快视图。
8. 输出完整处理日志。

### 7.1.3 输入参数要求

如提供独立预处理 API，预处理任务应至少支持以下参数：

```json
{
  "job_type": "lt1_preprocess",
  "input_path": "D:/data/LT1/scene",
  "dem_path": "D:/DEM/HeiLongJiang10M_DEM.tif",
  "orbit_path": "D:/orbit_pools/landsar",
  "output_dir": "D:/production_results/landsar_preprocess/<job_id>",
  "work_dir": "D:/LandSAR_Work/api/<job_id>",
  "polarization": "HH",
  "az_looks": 3,
  "rg_looks": 3,
  "geocode": true,
  "orthorectify": true,
  "output_format": "GeoTIFF"
}
```

### 7.1.4 输出结果要求

如提供独立预处理 API，预处理任务应输出：

```text
output_dir
|-- manifest.json
|-- logs/
|-- quicklook/
|-- geotiff/
|   |-- intensity_geo.tif
|   |-- amplitude_geo.tif
|   `-- ...
`-- metadata/
```

GeoTIFF 必须满足：

- GDAL 可读。
- 有 CRS。
- 有 GeoTransform。
- NoData 值明确。
- 数据类型明确。
- 可被 QGIS 打开。
- 可被现有系统用于地图预览和后续洪涝分析。

## 7.2 陆探一号 D-InSAR API

### 7.2.1 功能目标

D-InSAR API 用于对两景陆探一号 SLC 数据执行差分干涉处理，输出 LOS 形变、相干性、解缠相位和快视产品。

### 7.2.2 必须支持的处理能力

1. 主辅影像导入。
2. 精密轨道处理。
3. 配准。
4. 重采样。
5. 干涉图生成。
6. 去平地和地形相位。
7. Goldstein 或等效滤波。
8. 相干性计算。
9. 相位解缠。
10. LOS 向形变计算。
11. 地理编码。
12. 结果 GeoTIFF 输出。
13. 处理日志和参数文件输出。

### 7.2.3 可选处理能力

1. LOS 转垂直向形变。
2. GCP 优化。
3. 大气相位改正。
4. 自定义解缠阈值。
5. 自定义滤波参数。

大气相位改正如依赖 GACOS，应明确 GACOS 文件来源、格式和命名规则。不得在无 GACOS 文件时静默启用。

### 7.2.4 输入参数要求

D-InSAR API 应至少支持以下参数：

```json
{
  "job_type": "lt1_dinsar",
  "master": {
    "xml": "D:/Task_Pool/DInSAR/Task_xxx/Input_Data/master.xml",
    "slc": "D:/Task_Pool/DInSAR/Task_xxx/Input_Data/master.tif"
  },
  "slave": {
    "xml": "D:/Task_Pool/DInSAR/Task_xxx/Input_Data/slave.xml",
    "slc": "D:/Task_Pool/DInSAR/Task_xxx/Input_Data/slave.tif"
  },
  "dem_path": "D:/DEM/HeiLongJiang10M_DEM.tif",
  "output_dir": "D:/production_results/dinsar/<pair_key>/<run_id>",
  "work_dir": "D:/LandSAR_Work/api/<job_id>",
  "az_looks": 3,
  "rg_looks": 3,
  "coh_mask_threshold": 0.3,
  "unwrap_coh_threshold": 0.3,
  "filter_alpha": 0.6,
  "geocode": true,
  "vertical_displacement": false,
  "atmospheric_correction": false
}
```

### 7.2.5 输出结果要求

D-InSAR 结果应至少包含：

```text
output_dir
|-- manifest.json
|-- logs/
|-- geotiff/
|   |-- los_displacement.tif
|   |-- coherence.tif
|   |-- unwrapped_phase.tif
|   |-- wrapped_phase.tif
|   `-- vertical_displacement.tif
|-- quicklook/
`-- metadata/
```

其中 `vertical_displacement.tif` 如未启用垂直向形变，可不生成。

核心 GeoTIFF 要求：

- 可被 GDAL/QGIS 读取。
- 坐标系和仿射变换完整。
- 单位明确，例如米、毫米或弧度。
- NoData 值明确。
- 方向约定明确，例如朝向雷达为正或远离雷达为正。
- 输出文件命名稳定，不随 GUI 语言环境变化。

## 7.3 陆探一号 SBAS-InSAR API

### 7.3.1 能力定位

SBAS-InSAR 是本次采购的必选服务模块。供应商应明确该模块是否已产品化支持 LT-1，并提供真实样例数据、处理报告、输出文件说明和接口调用示例。

### 7.3.2 基本要求

SBAS-InSAR API 应至少具备：

1. 多景 LT-1 SLC 输入。
2. 干涉对自动选择。
3. 垂直基线阈值设置。
4. 时间基线阈值设置。
5. 多视参数设置。
6. 相干点或有效像元筛选。
7. 时序形变反演。
8. LOS 时序或速率产品输出。
9. 编码后栅格产品输出。
10. 完整日志和质量指标输出。

### 7.3.3 输入参数要求

SBAS-InSAR API 应至少支持以下参数：

```json
{
  "job_type": "lt1_sbas_insar",
  "input_stack": {
    "mode": "task_input_data",
    "path": "D:/Task_Pool/SBAS/Task_xxx/Input_Data"
  },
  "dem_path": "D:/DEM/HeiLongJiang10M_DEM.tif",
  "orbit_path": "D:/orbit_pools/landsar",
  "output_dir": "D:/production_results/timeseries/sbas_landsar/<run_id>",
  "work_dir": "D:/LandSAR_Work/api/<job_id>",
  "az_looks": 3,
  "rg_looks": 3,
  "intf_method": "single",
  "perp_baseline": 200,
  "time_baseline": 300,
  "doppler_baseline": 100,
  "network_type": "delaunay",
  "solve_method": "periodogram",
  "los_output": true,
  "post_raster": true,
  "vector_output": false
}
```

服务应明确支持以下至少一种多景输入形态：

- 原始 LT-1 多景产品目录或压缩包列表。
- 已导入的 `LT1*_SLC.xml + LT1*_SLC.tif` 多景目录。
- LandSAR 服务约定的 `Task_*/Input_Data` 多景任务目录。

### 7.3.4 输出结果要求

SBAS-InSAR 结果应至少包含：

```text
output_dir
|-- manifest.json
|-- logs/
|-- geotiff/
|   |-- los_timeseries.tif
|   |-- los_rate.tif
|   |-- quality.tif
|   `-- post_raster.tif
|-- vectors/
|-- quicklook/
`-- metadata/
```

其中 `los_rate.tif`、`quality.tif`、`vectors/` 可按供应商算法实际输出调整，但 manifest 必须准确标明每个资产的角色、单位、维度和业务含义。

### 7.3.5 输出说明要求

供应商必须说明 SBAS 输出文件语义：

- 输出是累计形变、平均速率还是多波段时序。
- 单位是米、毫米、弧度还是其他。
- 正负号方向约定。
- 多波段时序的日期映射关系。
- 质量图或相干性图的含义。
- 参考点或参考区域信息。
- 干涉网络信息，包括时间基线、垂直基线和选对策略。

在未明确输出语义前，业务系统只按 LandSAR 原始产品归档，不直接标记为业务级年速率产品。供应商如要求系统展示年速率产品，必须明确输出确为年速率图，并提供单位、正负号方向和质量控制说明。

## 7.4 陆探一号 PS-InSAR API

### 7.4.1 能力定位

PS-InSAR 作为可选扩展能力。若供应商 API 服务支持，应明确接口成熟度和样例验证情况。

### 7.4.2 基本要求

如支持 PS-InSAR，应至少具备：

1. 多景 LT-1 SLC 输入。
2. PS 点选择。
3. 网络构建。
4. 参数估计。
5. 大气/轨道残差处理。
6. 时序形变反演。
7. 点矢量结果输出。
8. 栅格化或可视化输出。
9. 点位时间序列导出。

### 7.4.3 输出要求

PS-InSAR 输出应至少包含：

- PS 点 GeoJSON / Shapefile / CSV。
- 点位形变速率。
- 点位时序形变。
- 质量指标。
- 参考点信息。
- 坐标系统说明。

## 8. API 通用接口要求

### 8.1 健康检查接口

服务应提供：

```http
GET /health
GET /version
GET /capabilities
```

返回内容至少包括：

- 服务状态。
- LandSAR 引擎版本。
- 授权状态。
- 支持模块列表。
- 支持数据类型。
- 当前队列长度。
- 当前运行任务数量。

### 8.2 任务提交接口

服务应提供统一任务提交接口：

```http
POST /jobs
```

返回：

```json
{
  "job_id": "string",
  "status": "queued",
  "message": "job accepted"
}
```

### 8.3 任务状态接口

```http
GET /jobs/{job_id}
```

返回：

```json
{
  "job_id": "string",
  "job_type": "lt1_dinsar",
  "status": "running",
  "progress": 45,
  "stage": "geocoding",
  "message": "processing geocoding",
  "created_at": "2026-06-04T10:00:00Z",
  "started_at": "2026-06-04T10:01:00Z",
  "updated_at": "2026-06-04T10:30:00Z"
}
```

### 8.4 日志接口

```http
GET /jobs/{job_id}/logs
```

要求：

- 支持获取完整日志。
- 支持按 offset 或时间增量获取日志。
- 日志级别包含 INFO、WARNING、ERROR。
- 日志中应包含 LandSAR 原始错误信息。

### 8.5 结果接口

```http
GET /jobs/{job_id}/result
```

返回：

```json
{
  "job_id": "string",
  "status": "completed",
  "output_dir": "D:/production_results/...",
  "assets": [
    {
      "role": "primary_geotiff",
      "path": "D:/production_results/.../los_displacement.tif",
      "format": "GeoTIFF",
      "unit": "m",
      "description": "LOS displacement"
    }
  ],
  "logs": [],
  "metadata": {}
}
```

### 8.6 任务取消接口

```http
POST /jobs/{job_id}/cancel
```

要求：

- 支持取消排队任务。
- 支持尽可能安全地中止运行中任务。
- 被取消任务应有明确状态 `cancelled`。
- 不得留下无法再次运行的锁文件或僵尸进程。

## 9. 状态码和错误码要求

服务必须提供稳定错误码，至少包括：

| 错误码 | 含义 |
| --- | --- |
| LICENSE_UNAVAILABLE | 授权不可用 |
| ENGINE_NOT_READY | LandSAR 引擎不可用 |
| INVALID_INPUT | 输入参数错误 |
| INPUT_NOT_FOUND | 输入文件不存在 |
| DEM_NOT_FOUND | DEM 不存在 |
| ORBIT_NOT_FOUND | 精轨文件不存在 |
| UNSUPPORTED_SENSOR | 不支持的数据类型 |
| PROCESS_FAILED | 处理失败 |
| OUTPUT_MISSING | 处理完成但结果缺失 |
| TIMEOUT | 任务超时 |
| CANCELLED | 用户取消 |

错误响应应包含：

```json
{
  "error_code": "DEM_NOT_FOUND",
  "message": "DEM file not found",
  "detail": "D:/DEM/xxx.tif",
  "recoverable": true
}
```

## 10. 与现有系统集成要求

### 10.1 任务队列集成

现有系统会将 LandSAR API 服务视为外部处理器。业务系统负责创建本地任务记录，LandSAR API 负责实际生产。

集成流程：

```text
用户提交生产任务
-> 系统创建任务记录
-> 系统调用 LandSAR API
-> LandSAR API 返回 job_id
-> 系统轮询或订阅 job 状态
-> job 完成
-> 系统读取 result assets
-> 系统入库 catalog
-> 前端展示结果
```

### 10.2 文件目录集成

建议目录：

```text
D:\LandSAR_Work\api
D:\production_results\landsar_preprocess
D:\production_results\dinsar
D:\production_results\timeseries\sbas
D:\production_results\timeseries\psinsar
```

LandSAR API 应允许调用方指定：

- `work_dir`
- `output_dir`
- `log_dir`
- `temp_dir`

### 10.3 结果入库集成

LandSAR API 结果清单应便于系统入库。推荐每个任务输出：

```text
manifest.json
```

manifest 至少包含：

- job_id
- job_type
- processor_code
- input_files
- output_files
- parameters
- start_time
- end_time
- status
- CRS
- bbox
- unit
- NoData
- software_version
- license_mode

## 11. 性能和稳定性要求

### 11.1 并发要求

供应商应明确：

- 是否支持多任务并行。
- 最大并发任务数。
- 不同模块是否互斥。
- D-InSAR 与预处理是否可同时执行。
- API 服务是否支持排队。

如不支持并发，服务也必须支持内部排队或返回明确的忙碌状态。

### 11.2 超时要求

建议默认超时：

- 预处理：6 小时。
- D-InSAR：12 小时。
- SBAS-InSAR：48 小时。
- PS-InSAR：72 小时。

超时后应返回明确状态，并保留日志。

### 11.3 稳定性要求

服务应支持：

- 长时间运行。
- 进程异常退出后自动恢复。
- 服务重启后查询历史任务。
- 任务失败后保留工作目录和日志。
- 任务成功后可按配置清理中间文件。

## 12. 安全和授权要求

1. API 服务应支持本机访问限制，默认只监听 `127.0.0.1`。
2. 如监听局域网地址，应支持 Token 或 API Key。
3. 授权异常应有明确错误码。
4. 授权服务应支持开机自启动或由 API 服务托管启动。
5. 授权有效期、授权模块列表应可查询。

## 13. 文档和交付物要求

供应商应提供：

1. API 接口文档。
2. OpenAPI / Swagger 文档。
3. 参数说明表。
4. 错误码说明表。
5. 部署说明。
6. 授权说明。
7. 示例调用代码，至少包括 Python 示例。
8. 示例数据处理报告。
9. 结果文件格式说明。
10. 运维手册。

## 14. 验收要求

### 14.1 支撑性预处理验收

如供应商提供独立预处理 API，使用至少 1 景 LT-1 数据完成预处理，验收项：

- API 可提交任务。
- 任务状态可查询。
- 日志可查询。
- 输出 GeoTIFF 可被 GDAL/QGIS 打开。
- 输出包含 CRS 和 GeoTransform。
- 系统可读取结果并生成预览。

### 14.2 D-InSAR 验收

使用至少 1 组 LT-1 主辅影像完成 D-InSAR，验收项：

- API 可提交任务。
- 可输出 LOS 形变 GeoTIFF。
- 可输出相干性 GeoTIFF。
- 可输出解缠相位或差分相位产品。
- 可输出处理日志。
- 结果可进入现有系统 D-InSAR 结果管理。
- 任务失败时错误码明确。

### 14.3 SBAS-InSAR 验收

使用不少于 3 景 LT-1 数据完成 SBAS-InSAR 样例处理。若供应商建议更高的最小景数，应按供应商推荐值提供样例数据和验收结果。

验收项：

- API 可提交异步 SBAS-InSAR 任务。
- 任务状态、阶段进度和日志可查询。
- 可设置时间基线、垂直基线、多视参数和干涉网络策略。
- 可输出 LOS 时序、速率、累计形变或供应商算法定义的主产品。
- 输出结果语义明确，包括单位、正负号方向、日期映射、参考点和质量指标。
- 输出 GeoTIFF 或点矢量结果可被 GDAL/QGIS 读取。
- 结果可进入现有系统 SBAS-InSAR 结果 catalog。
- 任务失败、结果缺失、输入不足、DEM 缺失和授权异常时错误码明确。

### 14.4 PS-InSAR 可选验收

如采购包含 PS-InSAR：

- 使用供应商建议的最小 LT-1 数据景数完成 PS 样例处理。
- 输出结果语义明确。
- 输出点/栅格产品可被 GIS 软件打开。
- 系统能归档结果和日志。

## 15. 供应商需确认问题

请供应商在报价或技术响应中明确回答以下问题：

1. API 服务是 HTTP、消息队列，还是二者都支持？
2. API 服务是否可本地离线部署？
3. 默认监听端口是多少，是否可配置？
4. 是否提供 OpenAPI / Swagger 文档？
5. 是否支持 LT-1 原始产品直接输入？
6. 是否支持已导入的 `LT1*_SLC.xml + LT1*_SLC.tif` 输入？
7. 预处理是否包含正射校正或地理编码？
8. D-InSAR 输出的 LOS 形变单位和正负号约定是什么？
9. 是否支持垂直向形变？
10. 是否支持 GACOS 或其他大气改正？
11. SBAS-InSAR 是否已产品化支持 LT-1？最小建议景数是多少？
12. PS-InSAR 是否已产品化支持 LT-1？
13. 支持的最大并发任务数是多少？
14. 是否支持任务取消？
15. 是否支持断点续跑？
16. 是否支持服务重启后恢复任务状态？
17. 授权服务如何部署，是否支持无加密狗本地授权服务？
18. 授权模块是否区分预处理、D-InSAR、SBAS、PS？
19. 是否提供示例数据和验收报告？
20. 是否提供二次开发技术支持？

## 16. 采购实施建议

建议采用“两个必选服务模块、分阶段实施验收”的方式推进：

### 第一阶段：D-InSAR 服务接入

必须交付：

- LandSAR API 服务。
- LT-1 D-InSAR API。
- 任务状态、日志、结果和错误码接口。
- D-InSAR 示例数据验收。

### 第二阶段：SBAS-InSAR

必须交付：

- LT-1 SBAS-InSAR API。
- 多景时序输入。
- 时序结果输出。
- 结果语义说明和系统入库适配。
- SBAS-InSAR 示例数据验收。

### 第三阶段：PS-InSAR

可选扩展：

- LT-1 PS-InSAR API。
- PS 点结果输出。
- 点位时序曲线输出。
- 监测点分析接口。

## 17. 本系统侧预计改造内容

采购 LandSAR API 服务后，现有系统侧需要进行以下改造：

1. 新增 LandSAR API 客户端模块。
2. 新增 API 服务健康检查。
3. 新增 D-InSAR 服务版处理器分支。
4. 新增 SBAS-InSAR 服务版处理器分支。
5. 新增 LandSAR API job_id 与系统 task_id/run_id 的绑定关系。
6. 新增 LandSAR 结果 manifest 解析。
7. 新增错误码映射。
8. 新增前端参数页和任务监控展示。
9. 新增中间文件清理策略。
10. 按 `processor_code` 适配 D-InSAR 与 SBAS-InSAR 结果 catalog。

## 18. 当前建议结论

本次采购应要求供应商交付“可本地部署、可 API 调用、可异步任务化”的 LandSAR 服务版 D-InSAR 与 SBAS-InSAR 两个模块。D-InSAR 与 SBAS-InSAR 均应作为强制响应项和验收项，PS-InSAR 可作为可选扩展项。

原因是：

- 现有系统已经具备 D-InSAR 和 SBAS-InSAR 生产调度、结果 catalog 和前端展示入口。
- D-InSAR 是成对影像生产流程，结果需进入现有 D-InSAR 结果管理。
- SBAS-InSAR 是多景时序生产流程，结果需进入现有 SBAS-InSAR 结果 catalog。
- 两类服务应共享统一任务状态、日志、错误码、结果 manifest 和授权健康检查接口。
- PS-InSAR 输出形态和业务展示方式与 SBAS/D-InSAR 差异较大，可在 D-InSAR 与 SBAS-InSAR 服务稳定后扩展。

建议招标时将“D-InSAR API + SBAS-InSAR API + 服务部署授权 + 任务状态/日志/结果/错误码接口”列为必须响应项，将“独立预处理 API、PS-InSAR API、GPU 加速、断点续跑、第三方消息队列”列为可选响应项。
