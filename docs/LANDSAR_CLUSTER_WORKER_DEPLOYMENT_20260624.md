# LandSAR 集群 Worker 设计与部署记录（2026-06-24）

## 结论

本次改造是在保留本机 LandSAR 生产链路的前提下，新增 LandSAR 集群执行入口。

本机旧入口仍然是 `LANDSAR_RUN`：由主服务器上的一个控制器串行处理一个批次内的 pair。新入口是 `LANDSAR_CLUSTER_ITEM`：主服务器提交集群任务后，系统按 pair 拆成多条队列任务，由本机或远端 Windows worker 领取执行。

当前规划的远端计算服务器是：

- 主服务器 / PostgreSQL / Web 系统：`192.168.1.62`
- 远端 LandSAR worker：`192.168.1.6`

远端 worker 的 `.env` 里 `DATABASE_URL` 必须指向主服务器 `192.168.1.62`，不是写它自己 `192.168.1.6`。

## 已落地代码

后端集群入口：

- `backend/app/routers/dinsar_production.py`
  - 新增 `POST /dinsar-production/landsar-cluster/run`
  - 只接受 `engine_code=landsar`
  - 提交后按 Task/pair 拆分为多个 `LANDSAR_CLUSTER_ITEM`

生产服务：

- `backend/app/services/dinsar_production_service.py`
  - 新增 `create_landsar_cluster_run`
  - 复用现有 `DinsarProductionRunORM`、`DinsarProductionRunItemORM`、`DinsarProductionExecutionORM`
  - 不新增 PG 表结构
  - 新增 `LANDSAR_CLUSTER_RUN` 父任务类型

队列与 worker：

- `backend/app/services/job_queue_service.py`
  - `claim_next_job` 支持 `allowed_job_types`
- `backend/app/services/job_worker.py`
  - 新增 `JOB_WORKER_ALLOWED_TYPES` 过滤
  - 远端 worker 可配置为只领取 `LANDSAR_CLUSTER_ITEM`
- `backend/app/services/job_handlers.py`
  - 新增 `LANDSAR_CLUSTER_ITEM` handler
  - 每个 handler 只处理一个 pair
  - 使用本进程本地锁避免单台机器上多个 LandSAR 任务并发抢授权
  - 不使用旧 `wsl_dinsar_landsar` 全局数据库锁，因此多台服务器可以并行处理不同 pair

远端专用入口：

- `run_landsar_cluster_worker.py`
  - Windows 远端直接运行此脚本即可
  - 默认只领取 `LANDSAR_CLUSTER_ITEM`
  - 默认并发为 1
- `scripts/start_landsar_cluster_worker.ps1`
  - 远端 Windows 推荐启动器
  - 检查 `.env`
  - 自动定位 Python
  - 创建 `logs\landsar_cluster_worker`
  - 支持前台运行和 `-Background` 后台运行
- `scripts/start_landsar_cluster_worker.bat`
  - 远端双击启动入口，内部调用 PowerShell 启动器
- `scripts/stop_landsar_cluster_worker.ps1`
  - 停止后台 worker，默认使用 `runtime\landsar_cluster_worker\worker.pid`
  - 加 `-All` 可清理所有命令行包含 `run_landsar_cluster_worker.py` 的 Python worker
- `scripts/stop_landsar_cluster_worker.bat`
  - 远端双击停止入口，内部调用 PowerShell 停止脚本

主服务器网络准入脚本：

- `scripts/sync_landsar_cluster_network_access.ps1`
  - 从主服务器 `.env` 读取 `LANDSAR_CLUSTER_ALLOWED_WORKER_IPS`
  - 同步 PostgreSQL `pg_hba.conf`
  - 同步 Windows 防火墙 TCP `5432`
  - reload PostgreSQL

前端入口：

- `frontend/src/DinsarProductionPanel.jsx`
  - LandSAR 引擎下新增“提交 LandSAR 集群”按钮
  - 原“提交任务”按钮仍走本机旧链路

## 主服务器配置

主服务器 `.env` 增加：

```env
LANDSAR_CLUSTER_ALLOWED_WORKER_IPS=192.168.1.6
```

如果后续增加更多 worker，用逗号或分号分隔：

```env
LANDSAR_CLUSTER_ALLOWED_WORKER_IPS=192.168.1.6,192.168.1.7,192.168.1.8
```

每次修改后在主服务器执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync_landsar_cluster_network_access.ps1
```

脚本会把允许的 worker IP 写入 `D:\PostgreSQLData\pg_hba.conf` 的受管控区块：

```text
# BEGIN InSAR LandSAR cluster workers
host    insar_management    all             192.168.1.6/32       scram-sha-256
# END InSAR LandSAR cluster workers
```

同时维护 Windows 防火墙规则：

```text
InSAR PostgreSQL 5432 LandSAR Cluster
```

当前主服务器已完成配置：

- PostgreSQL 监听 `0.0.0.0:5432`
- `pg_hba.conf` 只允许 `192.168.1.6/32` 访问 `insar_management`
- 防火墙 TCP `5432` 只允许 `192.168.1.6`

## 远端 192.168.1.6 需要复制什么

推荐复制整个当前仓库，而不是只挑脚本。

原因是远端 worker 虽然只运行 `run_landsar_cluster_worker.py`，但它会 import 后端配置、ORM、队列服务、LandSAR engine、结果发布服务、任务服务等模块。只复制单个脚本会缺依赖。

建议远端目录保持一致：

```text
D:\Code\Insar_management_system_v2
```

至少要确保这些内容在远端存在并与主服务器代码版本一致：

- `run_landsar_cluster_worker.py`
- `backend/`
- `scripts/`
- `config/`
- `.env`
- Python 依赖环境
- LandSAR 安装目录
- DEM 文件
- 任务输入目录或可访问的任务输入路径
- 结果返回目录或可访问的结果目录

前端 `frontend/` 对远端 worker 不是运行必需，但为了版本一致，建议整仓同步。

## 远端 192.168.1.6 的 .env

远端 `.env` 模板已维护在：

```text
config\landsar_cluster_worker.env.example
```

复制为远端项目根目录 `.env`：

```powershell
Copy-Item config\landsar_cluster_worker.env.example .env
```

远端 `.env` 的核心配置：

```env
DATABASE_URL=postgresql+asyncpg://postgres:WXZXzhb123456@192.168.1.62:5432/insar_management
JOB_WORKER_ALLOWED_TYPES=LANDSAR_CLUSTER_ITEM
JOB_WORKER_CONCURRENCY=1
JOB_WORKER_POLL_INTERVAL=1.0
LANDSAR_CLUSTER_WORKER_ID=
```

还需要按远端实际 LandSAR 环境配置这些项：

```env
LANDSAR_ENABLED=true
LANDSAR_HOME=D:\LandSAR
LANDSAR_CONSOLE_EXE=D:\LandSAR\InSAR_Console.exe
LANDSAR_WORK_ROOT=D:\LandSAR_Work
LANDSAR_RUNTIME_PATHS=D:\LandSAR
LANDSAR_LICENSE_MODE=netVersion
LANDSAR_LICENSE_HOST=127.0.0.1
LANDSAR_LICENSE_PORT=6666
LANDSAR_CONFIG_ROW=netVersion,zh,127.0.0.1,6666
LANDSAR_CONFIG_AUTO_WRITE=true
LANDSAR_AUTH_SERVER_EXE=D:\Code\Insar_management_system_v2\third_party\LandSAR\tools\_portable_release\LandSAR_auth_tools_win64\landsar_net_auth_server.exe
LANDSAR_AUTH_SERVER_AUTO_START=true
LANDSAR_AUTH_SERVER_HOST=127.0.0.1
LANDSAR_AUTH_SERVER_PORT=6666
LANDSAR_DEM_PATH=D:\DEM\SRTMDEM_RSP_SARscape_global_int16.tif
LANDSAR_DINSAR_TIMEOUT_SECONDS=43200
```

如果远端的 LandSAR 授权服务器、安装路径、DEM 路径不同，按远端实际路径填写。

建议给 `LANDSAR_CLUSTER_WORKER_ID` 一个稳定值，方便主服务器健康检查区分节点：

```env
LANDSAR_CLUSTER_WORKER_ID=landsar-worker-192-168-1-6
```

## 远端 Windows 启动命令

在 `192.168.1.6` 上进入项目目录：

```powershell
Set-Location D:\Code\Insar_management_system_v2
```

启动 worker：

```powershell
.\scripts\start_landsar_cluster_worker.ps1
```

看到类似输出即表示监听程序已启动：

```text
[*] Starting LandSAR cluster worker...
[*] Allowed job types: LANDSAR_CLUSTER_ITEM
[*] Poll interval: 1s
[*] Concurrency: 1
```

需要双击启动时，运行：

```text
D:\Code\Insar_management_system_v2\scripts\start_landsar_cluster_worker.bat
```

需要后台启动时，运行：

```powershell
.\scripts\start_landsar_cluster_worker.ps1 -Background
```

后台停止：

```powershell
.\scripts\stop_landsar_cluster_worker.ps1
```

如果需要强制清理全部 LandSAR cluster worker：

```powershell
.\scripts\stop_landsar_cluster_worker.ps1 -All
```

需要双击停止时，运行：

```text
D:\Code\Insar_management_system_v2\scripts\stop_landsar_cluster_worker.bat
```

启动日志在：

```text
D:\Code\Insar_management_system_v2\logs\landsar_cluster_worker
```

后台 worker PID 文件在：

```text
D:\Code\Insar_management_system_v2\runtime\landsar_cluster_worker\worker.pid
```

## 远端连通性检查

在 `192.168.1.6` 上检查能否连主服务器数据库：

```powershell
Test-NetConnection 192.168.1.62 -Port 5432
```

应看到：

```text
TcpTestSucceeded : True
```

再用 Python 检查数据库认证：

```powershell
$env:DATABASE_URL='postgresql+asyncpg://postgres:WXZXzhb123456@192.168.1.62:5432/insar_management'
@'
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    async with engine.connect() as conn:
        print((await conn.execute(text("select 1"))).scalar_one())
    await engine.dispose()

asyncio.run(main())
'@ | C:\ProgramData\anaconda3\envs\InSAR\python.exe -
```

应输出：

```text
1
```

## 生产使用流程

1. 主服务器前端进入 D-InSAR 生产管理。
2. 选择 LandSAR 引擎。
3. 选择生产根目录，例如 `D:\Task_Pool\DInSAR` 或某个具体 `Task_*` 父目录。
4. 点击“提交 LandSAR 集群”。
5. 后端创建一个 `LANDSAR_CLUSTER_RUN` 父任务。
6. 每个 pair 生成一条 `LANDSAR_CLUSTER_ITEM` 队列任务。
7. 本机或远端 worker 抢占 item。
8. worker 调用本机 LandSAR 环境处理该 pair。
9. item 完成后写 execution manifest，并尝试发布到 D-InSAR 结果 catalog。
10. 所有 item 进入终态后，父 run 标记为完成、失败或取消。

## 当前重要约束

这次改造解决的是“按 pair 分片调度和多 worker 领取”的问题，不是完整的数据自动搬运系统。

因此远端 `192.168.1.6` 必须满足以下路径条件之一：

1. 与主服务器保持相同盘符和目录结构，并能看到同样的 `Task_Pool` 输入数据。
2. 或者远端通过映射盘、同步工具、计划任务等方式，把所需 pair 的输入数据准备到相同路径。
3. 结果输出目录也必须让主服务器可以扫描或访问，否则 worker 虽然能计算，结果不会自然回到主服务器 catalog。

当前代码中的 LandSAR cluster item 使用数据库里已有的 `source_task_dir` 作为 LandSAR 输入目录；它不会自动从源压缩包解包到远端，也不会自动把远端本地结果复制回主服务器。

后续若要彻底工程化，应新增两个能力：

- 集群 item 开始前：按 pair 从源压缩包或主服务器 Task_Pool materialize 到远端本地工作目录。
- 集群 item 完成后：把标准产品包从远端回传到主服务器结果目录，再由主服务器统一入库。

## 不要混淆的 IP

`192.168.1.62` 是主服务器 IP，负责：

- Web 后端
- PostgreSQL
- 任务队列
- 数据库自维护
- 前端操作入口

`192.168.1.6` 是远端 LandSAR worker IP，负责：

- 常驻监听 `LANDSAR_CLUSTER_ITEM`
- 调用本机 LandSAR
- 写 item 执行状态

所以远端 `.env` 中：

```env
DATABASE_URL=...@192.168.1.62:5432/insar_management
```

而主服务器 `.env` 中：

```env
LANDSAR_CLUSTER_ALLOWED_WORKER_IPS=192.168.1.6
```

这两个配置方向不同，不能互换。
