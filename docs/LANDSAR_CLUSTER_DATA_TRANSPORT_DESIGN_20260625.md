 # LandSAR 集群数据搬运与运维设计（2026-06-25）

 ## 背景

 [LANDSAR_CLUSTER_WORKER_DEPLOYMENT_20260624.md](LANDSAR_CLUSTER_WORKER_DEPLOYMENT_20260624.md) 已完成集群调度骨架：主服务器提交 LandSAR 集群任务 → 按 pair 拆分为 `LANDSAR_CLUSTER_ITEM` → 本机或远端 worker 通过 DB 队列领取执行。

 该文档留下了两个明确缺口：

 1. **数据搬运**：远端 worker 执行 `engine.run()` 时读取 `item.source_task_dir`（如 `D:\Task_Pool\DInSAR\Task_20250601_20250612\master`），该路径在远端不存在。
 2. **结果回传**：远端 worker 处理完成后，标准产品包在远端本地磁盘，不会自动进入主服务器 D-InSAR catalog。

 本文档定义这两个能力的设计，以及 Windows 集群运维方案。

 ## 设计目标

 - 不依赖 Windows 文件共享（SMB）、映射盘符、UNC 路径
 - 不要求主服务器和 worker 共用盘符或路径结构
 - Worker 节点可以动态增减，配置简单
 - 复用现有 `SOURCE_PRODUCT_DIRS` 压缩包源池和 `TASK_POOL_ROOT` 体系
 - 传输失败利用队列系统自带的重试机制

 ## 架构总览

 ```
 主服务器 (192.168.1.62)                       远端 Worker (192.168.1.6 / .7 / ...)
 ════════════════════════                     ═══════════════════════════════════

 [前端] 提交集群任务
    │
    ▼
 [Router] POST /landsar-cluster/run
    │
    ▼
 [Service] create_landsar_cluster_run()
    │ 扫描 Task_* → 为每个 pair 创建
    │ DinsarProductionRunItemORM +
    │ LANDSAR_CLUSTER_ITEM 队列任务
    ▼
 [system_jobs 表] ◄─────────── claim_next_job ────── [run_landsar_cluster_worker.py]
                                                           │
                                                      ┌────▼──────────────────┐
                                                      │ 检查 source_task_dir  │
                                                      │ 本地是否存在           │
                                                      ├────有─────────────────┤
                                                      │ → 跳到 LandSAR 执行    │
                                                      ├────无─────────────────┤
                                                      │ 1. GET /api/cluster/  │
                                                      │    input-package/     │
                                                      │ 2. 解包到本地路径      │
                                                      └───────────────────────┘
                                                           │
                                                           ▼
                                                      LandSAR engine.run()
                                                           │
                                                           ▼
                                                      ┌───────────────────────┐
                                                      │ POST /api/cluster/    │
                                                      │ upload-result/        │
                                                      │ → 主服务器写入结果目录  │
                                                      │ → catalog 登记         │
                                                      └───────────────────────┘
 ```

 ## 数据搬运详细设计

 ### 阶段 1: Pre-flight 输入数据下载

 **触发时机**：`_handle_landsar_cluster_item` 执行 `engine.run()` 之前。

 **Worker 端流程**：

 1. 读取 `item.source_task_dir`，检查本地目录是否存在且包含 `master/`、`slave/`、`pair_metadata.json`
 2. 若无 → 调用 `GET /api/cluster/input-package/{item_id}`
 3. Worker 请求头携带 `X-Cluster-Token`，主服务器校验 `CLUSTER_SHARED_TOKEN`
 4. 主服务器读取 `item.source_task_dir` 指向的现有 Task_Pool 目录
 5. 将该 Task_Pool 目录打成 zip 流式返回给 worker
 6. Worker 解包到 `item.source_task_dir`（保持与主服务器一致的路径结构）
 7. Worker 对 zip 成员路径做目录逃逸校验，并校验解包后的 `master/`、`slave/` 目录可用

 **主服务器新增 API**：

 ```
 GET /api/cluster/input-package/{item_id}
  Header: X-Cluster-Token: <CLUSTER_SHARED_TOKEN>
   Response: application/zip (streaming)
   内容结构:
     Task_YYYYMMDD_YYYYMMDD/
       master/
         <源文件...>
       slave/
         <源文件...>
       orbit/
         <精轨文件...>
       pair_metadata.json
 ```

 **当前实现边界**：主服务器不在传输接口里重新从 `SOURCE_PRODUCT_DIRS` 解包源压缩包；传输接口只打包已经由 Task_Pool 准备流程生成的 `item.source_task_dir`。如果主服务器上的 `source_task_dir` 不存在，接口返回 404，队列重试会保留失败信息。

 ### 阶段 2: Post-flight 结果上传

 **触发时机**：LandSAR 执行成功、execution manifest 构建完成后。

 **Worker 端流程**：

 1. LandSAR 完成后，收集标准产品包文件列表（从 `task_result.source_files` 和 manifest 获取）
 2. 调用 `POST /api/cluster/upload-result/{item_id}`
 3. 以 multipart 或流式上传产品包（含 primary file、auxiliary files、metadata）
 4. 主服务器接收后写入该 item 的标准目录 `results_root_dir\runs\<run_key>`
 5. 触发 catalog 登记（复用现有 `result_catalog_service.bootstrap` 或增量登记）

 **主服务器新增 API**：

 ```
 POST /api/cluster/upload-result/{item_id}
  Header: X-Cluster-Token: <CLUSTER_SHARED_TOKEN>
   Body: multipart/form-data
     - result_zip: managed run directory zip
     - run_id: 集群 run ID
     - run_key: 当前执行 run key
   Response: { "registered": true, "processed": 1, "failed": 0, "catalog_path": "..." }
 ```

 **上传后处理**：

 1. 校验文件完整性（与 manifest 对比）
 2. 写入 `D:\production_results\dinsar\<engine_code>\<profile>\<task_name>\`
 3. 调用 `result_catalog_service` 增量登记
 4. 更新 `DinsarProductionExecutionORM` 指向最终结果路径
 5. 返回登记结果给 worker

 ### 错误处理与重试

 - 下载失败：worker 端抛异常 → `job_queue_service.mark_failed` → 按 `max_attempts` 自动重试
 - 上传失败：同上，LandSAR 已完成的中间结果在 worker 本地保留（下次重试跳过 LandSAR，直接上传）
 - 主服务器端打包失败：返回 500 + 错误详情，worker 捕获后走重试
 - 超时控制：复用 `LANDSAR_DINSAR_TIMEOUT_SECONDS`，传输阶段额外设 `CLUSTER_TRANSFER_TIMEOUT_SECONDS`（默认 3600）

 ## Windows 集群运维设计

 ### Worker 开机自启

 每个 worker 节点配置一条 **Windows Task Scheduler** 任务：

 ```powershell
 # 创建计划任务（以管理员身份运行）
 $action = New-ScheduledTaskAction -Execute "powershell.exe" `
     -Argument "-NoProfile -ExecutionPolicy Bypass -File D:\Code\Insar_management_system_v2\scripts\start_landsar_cluster_worker.ps1 -Background"
 $trigger = New-ScheduledTaskTrigger -AtStartup
 $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
     -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 Register-ScheduledTask -TaskName "InSAR_LandSAR_Cluster_Worker" `
     -Action $action -Trigger $trigger -Settings $settings `
     -RunLevel Highest -Description "LandSAR 集群 Worker 常驻进程"
 ```

 关键设置：
 - 触发器：系统启动时
 - 失败重试：3 次，间隔 1 分钟
 - 运行级别：最高权限
 - 不因电池模式停止（针对笔记本）

 ### Worker 监控

 Worker 进程已内建心跳机制（`_touch_worker`），每隔 `JOB_WORKER_HEARTBEAT_INTERVAL` 秒向 `system_worker_heartbeats` 表写入心跳。主服务器健康检查页面可以展示：

 - 各 worker 的 hostname / PID / worker_id
 - 最后心跳时间
 - 当前正在处理的 job 数量
 - 历史完成/失败统计

 ### 代码同步

 远端 worker 建议通过 Git 同步代码：

 ```powershell
 # 在远端 192.168.1.6 上
 cd D:\Code\Insar_management_system_v2
 git fetch origin
 git checkout <branch>
 ```

 `.env` 文件不在 Git 中，需手动维护。远端 `.env` 的 `DATABASE_URL` 指向主服务器，LandSAR 路径指向远端本地。

 ## 配置新增项

 主服务器 `.env` 新增：

 ```env
 # 集群传输配置
 CLUSTER_SHARED_TOKEN=<same-long-random-token-on-main-and-workers>
 CLUSTER_TRANSFER_TIMEOUT_SECONDS=3600
 CLUSTER_MATERIALIZE_TEMP_DIR=D:\Task_Pool\_cluster_temp
 ```

 Worker 端 `.env` 新增（或保持现有模板字段）：

 ```env
 # 集群 worker 主服务器地址
 CLUSTER_MAIN_SERVER_URL=http://192.168.1.62
 CLUSTER_SHARED_TOKEN=<same-long-random-token-on-main-and-workers>
 CLUSTER_TRANSFER_TIMEOUT_SECONDS=3600
 ```

 `CLUSTER_MAIN_SERVER_URL` 用于 worker 构造下载/上传 API 的完整 URL。未配置时默认使用 `DATABASE_URL` 中的 host 推断。
 `CLUSTER_SHARED_TOKEN` 是集群传输接口的专用共享密钥，主服务器和所有远端 worker 必须一致且非空；未配置时 `/api/cluster/...` 接口返回 503。

 ## 实现路线图

 | 阶段 | 内容 | 依赖 |
 | --- | --- | --- |
 | 1 | 主服务器 `GET /api/cluster/input-package/{item_id}` | Task_Pool `source_task_dir` |
 | 2 | Worker handler 增加 pre-flight download + extract | 阶段 1 |
 | 3 | 主服务器 `POST /api/cluster/upload-result/{item_id}` | `result_catalog_service` |
 | 4 | Worker handler 增加 post-flight upload | 阶段 3 |
 | 5 | 端到端测试（主服务器 + 远端 .6） | 阶段 1-4 |
 | 6 | Windows Task Scheduler 开机自启配置 | 阶段 5 |

 ## 当前状态（2026-06-26）

 - [x] 集群调度骨架（提交 → 拆 item → DB 队列 → worker 领取 → LandSAR 执行）
 - [x] Worker 入口脚本 + 启动/停止脚本
 - [x] Worker 心跳上报
 - [x] 本机集群模式验证通过（`SameSite=lax` Cookie 修复后）
 - [x] 数据搬运 pre-flight（HTTP 下载 Task_Pool zip + 安全解压）
 - [x] 结果上传 post-flight（HTTP 上传结果 zip + catalog 登记）
 - [x] 集群传输接口 `CLUSTER_SHARED_TOKEN` 鉴权
 - [ ] 远端 .6 端到端测试
 - [ ] Worker 开机自启

 ## 相关文档

 - [LANDSAR_CLUSTER_WORKER_DEPLOYMENT_20260624.md](LANDSAR_CLUSTER_WORKER_DEPLOYMENT_20260624.md) — 集群调度骨架与部署记录
 - [LANDSAR_DEM_PREPARATION_CONTRACT_20260618.md](LANDSAR_DEM_PREPARATION_CONTRACT_20260618.md) — LandSAR DEM 准备约定
 - [THREE_SENSOR_LOCAL_PRODUCTION_CONTRACT_20260616.md](THREE_SENSOR_LOCAL_PRODUCTION_CONTRACT_20260616.md) — 三数据本机生产约定
 - [DINSAR_TASK_POOL_THREE_ENGINE_REFACTOR_20260614.md](DINSAR_TASK_POOL_THREE_ENGINE_REFACTOR_20260614.md) — Task_Pool 与三引擎 refactor
