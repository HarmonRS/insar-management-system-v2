# IDL/ENVI 集成状态文档

> 最后更新: 2026-02-21

## 1. 架构概述

系统的 D-InSAR 生产完全依赖 ENVI 5.6 + IDL 8.8 + SARscape 5.6+。
当前采用 **envipyengine** 作为唯一执行引擎，通过 Python subprocess 调用 `taskengine.exe`。

### 执行链路

```
前端 (IDLAutomationPanel.jsx)
  → POST /api/idl/jobs/import 或 /api/idl/jobs/dinsar
  → job_handlers.py: subprocess 启动 envi_runner_cli.py
  → envi_service.py: 调用 envipyengine → taskengine.exe
  → ENVI Task 执行 (SARsImportLuTan1 / SARsMetataskInSARDisplacementGeneration)
```

### 核心文件

| 文件 | 职责 |
|------|------|
| `backend/app/services/envi_service.py` | 核心服务：工作流、预检查、状态、历史 |
| `backend/app/services/envi_runner_cli.py` | CLI 入口，subprocess 中执行工作流 |
| `backend/app/services/job_handlers.py` | 任务分发，启动 envi_runner_cli 子进程 |
| `backend/app/routers/idl.py` | API 端点 |
| `frontend/src/IDLAutomationPanel.jsx` | 前端面板 (Step 1/2 布局) |
| `frontend/src/api/idl.js` | 前端 API 客户端 |

## 2. 环境配置

### ENVI 安装路径

```
C:\Program Files\Harris\ENVI56\
├── IDL88\bin\bin.x86_64\idl.exe
├── IDL88\bin\bin.x86_64\idlde.exe
└── IDL88\bin\bin.x86_64\taskengine.exe
```

### .env 关键配置

```ini
IDL_EXECUTABLE=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idl.exe
IDL_WORKBENCH_PATH=C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlde.exe
IDL_WORKER_RUNTIME_DIR=...\backend\runtime\idl_worker
IDL_WORKER_DEFAULT_TIMEOUT_SECONDS=14400
IDL_WORKER_MAX_TIMEOUT_SECONDS=43200
IDL_DINSAR_DEM_BASE_FILE=D:\SRTM30m\SRTMDEM_RSP_SARscape
```

### envipyengine 配置

```python
# 已通过 envipyengine.config.set() 配置:
engine = r'C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\taskengine.exe'
```

Python 环境: `C:\Users\16216\.conda\envs\InSAR\python.exe` (envipyengine v1.0.9)

## 3. 根因分析：历史集成失败

### 3.1 症状回顾

之前尝试了三种引擎均不稳定：
- `idl.exe -e` 直连：进程挂起、管道错误、idl_opserver 崩溃 (0x0000000000000001)
- `taskengine.exe` 直调：内存访问违规 (0xC0000005 / exit code 3221225477)
- `envipyengine`：单独测试通过，集成到项目后崩溃

### 3.2 根因定位

**`.env` 中 `IDL_PATH` 变量名与 IDL 内置环境变量冲突。**

- `IDL_PATH` 是 IDL 的保留环境变量，用于指定 `.pro` 文件搜索路径
- 项目 `.env` 曾使用 `IDL_PATH` 存储 `idl.exe` 的可执行文件路径
- `load_dotenv()` 将其注入 `os.environ`，子进程继承后 IDL 读到无效的搜索路径
- `taskengine.exe` 因此崩溃，退出码 `0xC0000005`

### 3.3 验证过程

三态测试确认因果关系：

| 状态 | IDL_PATH 值 | 结果 |
|------|-------------|------|
| 干净环境 | 未设置 | ✅ 成功 |
| 设置错误值 | `C:\...\idl.exe` | ❌ 崩溃 (0xC0000005) |
| 移除后恢复 | 未设置 | ✅ 成功 |

### 3.4 修复

将 `.env` 中 `IDL_PATH` 重命名为 `IDL_EXECUTABLE`，同步更新所有引用。

## 4. 当前架构设计

### 4.1 设计决策

1. **envipyengine 为唯一执行引擎** — 删除了 idl 直连和 taskengine 直调的全部代码
2. **Import → D-InSAR 固定流水线** — D-InSAR 自动检测未导入数据，先 Import 再处理（智能串联）
3. **D-InSAR 使用 metatask** — 精细参数 (filter_method 等) metatask 不支持，已移除
4. **DEM 路径为系统级配置** — 存储在 .env，不暴露到前端
5. **subprocess 执行模式** — envipyengine 在独立子进程中运行，隔离 FastAPI 主进程

### 4.2 ENVI Tasks

| Task | 用途 | 关键参数 |
|------|------|----------|
| `SARsImportLuTan1` | 导入 LuTan-1 原始数据 | `INPUT_FILE_LIST`, `ROOT_URI_FOR_OUTPUT` |
| `SARsMetataskInSARDisplacementGeneration` | D-InSAR 位移生成 | `REFERENCE_SARSCAPEDATA`, `SECONDARY_SARSCAPEDATA`, `DEM_SARSCAPEDATA`, `OUTPUT_FOLDER` |

### 4.3 智能串联逻辑 (D-InSAR 工作流)

```
对每个 Task_* 文件夹:
  1. 检查 master/slave 是否有 .sml
  2. 没有 → 查找 .meta.xml → 自动执行 Import
  3. Import 完成后验证 .sml 生成
  4. 执行 D-InSAR metatask
```

### 4.4 API 端点

```
GET  /api/idl/status              — 系统状态 (含 DEM 路径和可用性)
POST /api/idl/launch-workbench    — 启动 IDL Workbench
POST /api/idl/inspect/import      — Import 预检查
POST /api/idl/inspect/dinsar      — D-InSAR 预检查 (含 Import 状态检测)
POST /api/idl/jobs/import         — 提交 Import 任务
POST /api/idl/jobs/dinsar         — 提交 D-InSAR 任务
GET  /api/idl/jobs/recent         — 最近运行记录
```

### 4.5 目录结构要求

Import 支持两种布局：
```
# Task_* 结构 (推荐)
root_dir/
├── Task_001/
│   ├── master/  → *.meta.xml
│   └── slave/   → *.meta.xml
└── Task_002/
    ├── master/
    └── slave/

# 平铺结构
root_dir/
├── scene_001/  → *.meta.xml
└── scene_002/  → *.meta.xml
```

D-InSAR 仅支持 Task_* 结构。

## 5. 测试记录

### 5.1 envipyengine 验证 (2026-02-20)

修复 IDL_PATH 冲突后，envipyengine Import 连续测试：

| 次数 | 耗时 | 结果 |
|------|------|------|
| 1 | 100.5s | ✅ 成功 |
| 2 | 75.8s | ✅ 成功 |
| 3 | 71.8s | ✅ 成功 |
| 4 | 73.6s | ✅ 成功 |
| 5 | 81.7s | ✅ 成功 |

测试数据: `Z:\Test_data\Test_IDL_1`

### 5.2 集成测试 — Step 1: Import (2026-02-21)

重构后通过前端 → 后端 → envipyengine 完整链路测试。

- 状态: ✅ 通过
- 链路: 前端提交 → job_handlers subprocess → envi_runner_cli → envi_service → envipyengine

### 5.3 集成测试 — Step 2: D-InSAR (待测试)

- 状态: ⏳ 待测试
- 前置条件: DEM 文件 `D:\SRTM30m\SRTMDEM_RSP_SARscape` 需存在于测试机
- 智能串联 (自动 Import) 待验证

## 6. 已删除的遗留代码

重构中删除的文件 (2026-02-21)：

| 文件 | 行数 | 说明 |
|------|------|------|
| `backend/app/services/idl_worker_service.py` | 2249 | 三引擎架构的巨型服务 |
| `backend/app/services/idl_runner_cli.py` | 58 | 旧 CLI runner |
| `backend/app/services/envipyengine_runner.py` | 47 | 调试用独立 runner |

清理的 .env 配置：所有 `IDL_TASKENGINE_*`、`IDL_WORKER_ENGINE`、`IDL_JOB_RUNNER_MODE`、
`IDL_IDL_*`、`IDL_WORKER_PREFLIGHT_*`、D-InSAR 精细参数 (filter_method 等)。

## 7. 已知限制

1. **D-InSAR 精细参数不可调** — metatask 不支持 filter_method、unwrapping_coh_threshold 等参数
2. **DEM 路径固定** — 从 .env 读取，不支持前端动态指定
3. **单进程串行** — envipyengine 调用 taskengine.exe 是同步阻塞的，同一时间只能执行一个 ENVI Task
4. **仅支持 LuTan-1** — Import task 为 `SARsImportLuTan1`，其他卫星数据需要不同的 Task
