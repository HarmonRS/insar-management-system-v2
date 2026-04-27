# IDL/ENVI + 系统集成技术备忘录

> 更新日期: 2026-02-23

## 一、架构概览

```
前端 (React) → FastAPI → Job Queue (PostgreSQL) → Job Worker → 子进程 (envi_runner_cli) → envipyengine → ENVI/SARscape
```

- 前端提交任务 → 创建 Task + Job 记录 → Worker 领取 Job → 启动独立 Python 子进程执行 ENVI 工作流
- 子进程通过 progress JSON 文件向父进程报告进度
- 父进程监控子进程存活状态，同时通过 `_task_keepalive` 协程每 30 秒刷新 Task 的 `updated_at` 防止僵尸检测误杀
- 前端有两层锁: IDLAutomationPanel 内的黄色横幅 + App.jsx 的全局蓝色遮罩

## 二、已踩的坑与解决方案

### 1. subprocess.PIPE 缓冲区死锁

**现象**: 子进程挂起，不再输出
**原因**: Windows 上 `subprocess.Popen(stdout=PIPE)` 的管道缓冲区约 4KB，envipyengine 输出超过后阻塞
**方案**: 使用 `tempfile.mkstemp()` 创建临时文件接收 stdout/stderr，子进程退出后读取

### 2. Windows TemporaryFile 句柄不可继承

**现象**: `Popen` 无法写入 `tempfile.TemporaryFile` 创建的文件
**原因**: Windows 上 `TemporaryFile` 创建的句柄默认不可被子进程继承
**方案**: 改用 `tempfile.mkstemp()` + `os.close(fd)` + 手动 `os.unlink()` 清理

### 3. envipyengine 提前返回（文件未写完）

**现象**: `task.execute()` 返回后，ENVI 仍在写输出文件（特别是 step 6 的 `_rsp_disp` 文件）
**原因**: envipyengine 在 taskengine 返回后立即返回 Python，但 ENVI 后台仍在写大文件
**方案**:
- 在 step 6 后（无论成功/异常）调用 `_wait_for_disp_stable()` 等待 `*_rsp_disp` 文件出现并稳定
- 最后调用 `_wait_files_stable()` 确认所有文件写完
- 稳定判定: 连续 3 轮（每轮 15 秒）文件大小不变

### 4. envipyengine 报错但文件已生成

**现象**: `task.execute()` 抛出 "outputs not generated" 异常，但实际文件已正确生成
**原因**: envipyengine 的输出验证逻辑与 SARscape 实际行为不一致（特别是 step 3 Orbital Trend Removal）
**方案**: except 块中扫描输出目录，按文件名模式（如 `*ISARRRPF*.sml`）查找已生成的文件，构建 SARSCAPEDATA 字典继续后续步骤

### 5. envipyengine 永久挂起

**现象**: `task.execute()` 永远不返回，即使 ENVI 已完成处理
**原因**: envipyengine 内部的 taskengine 进程未正确退出
**方案**: 使用 `ThreadPoolExecutor` 包装 `task.execute()`，设置超时 `ENVI_TASK_TIMEOUT_SECONDS=14400`（4小时），超时后抛出异常，由文件扫描逻辑兜底

### 6. 子进程文件活动监控误杀

**现象**: 前端提前解锁，ENVI 进程仍在运行
**原因**: `job_handlers.py` 的监控循环检测到进度文件和输出目录超过 `ENVI_FILE_STALE_SECONDS` 无变化，kill 了子进程。某些步骤（如 Phase Unwrapping）长时间在内存中计算不写文件
**方案**: 将 `ENVI_FILE_STALE_SECONDS` 从 600 增大到 14400，与任务超时一致

### 7. Task 僵尸检测误杀（根本原因）

**现象**: 前端在 ENVI 处理过程中解锁
**原因**: `task_service.get_active_tasks()` 内置僵尸检测——每次前端轮询时检查 RUNNING 任务的 `updated_at`，超过 `TASK_TIMEOUT_MINUTES=60` 分钟未更新则标记为 FAILED。子进程运行期间 handler 没有更新 task 的 `updated_at`，而单个 step 可能超过 60 分钟
**方案**: 在 `_run_envi_workflow_job` 中添加 `_task_keepalive` 协程，每 30 秒读取进度文件并调用 `task_service.update_task()` 刷新 `updated_at`，同时更新进度百分比（step 映射到 10%-90%）和步骤信息

### 8. 强制解锁按钮被全局遮罩遮挡

**现象**: 强制解锁按钮写在 IDLAutomationPanel 的黄色横幅里，但全局蓝色遮罩（`App.jsx` 的 `global-task-overlay`）盖住了整个页面，用户无法点击
**原因**: 系统有两层锁——IDLAutomationPanel 内部的 `isLocked` 控制按钮禁用，App.jsx 的 `isGlobalLocked` 控制全屏蓝色遮罩。强制解锁只加在了内层
**方案**: 在 `App.jsx` 的全局遮罩上添加「管理员强制解锁」按钮（仅管理员可见），点击后展开密码输入框，确认后取消所有活跃任务并解锁。同时保留 IDLAutomationPanel 内的强制解锁作为备用

## 三、当前超时/保护参数

| 参数 | 值 | 位置 | 作用 |
|------|-----|------|------|
| `ENVI_TASK_TIMEOUT_SECONDS` | 14400 (4h) | `.env` | 单步 `task.execute()` 最大等待 |
| `ENVI_FILE_STALE_SECONDS` | 14400 (4h) | `.env` | 子进程无文件活动的 kill 阈值 |
| `JOB_WORKER_STALE_RUNNING_SECONDS` | 7200 (2h) | `.env` | Job 心跳超时（每 5s 更新） |
| `TASK_TIMEOUT_MINUTES` | 60 | `task_service.py` | 僵尸任务检测阈值（keepalive 每 30s 刷新） |
| `ENVI_STABILITY_CHECK_INTERVAL` | 15s | `.env` | 文件稳定检查间隔 |
| `ENVI_STABILITY_ROUNDS` | 3 | `.env` | 连续稳定轮数 |
| `ENVI_STABILITY_MAX_WAIT` | 3600 (1h) | `.env` | 文件稳定等待上限 |
| `IDL_JOB_MAX_ATTEMPTS` | 1 | `.env` | 不重试，失败即终止 |

## 四、自定义 D-InSAR 6 步流程

| 步骤 | Task 名称 | 典型耗时 | 已知问题 |
|------|-----------|----------|----------|
| 1. 干涉图生成 | `SARsInSARInterferogramGeneration` | ~900s | 无 |
| 2. 滤波+相干性 | `SARsInSARFilterAndCoherence` | ~400s | 无 |
| 3. 轨道趋势去除 | `SARsInSARRemoveResidualPhaseFrequency` | ~65s | 报 "outputs not generated" 但文件已生成 |
| 4. 相位解缠 | `SARsInSARPhaseUnwrapping` | ~1000s | 无 |
| 5a. GCP 生成 | Python (rasterio+geopandas) | <1s | 无 |
| 5b. 精化再平化 | `SARsInSARRefinementAndReflattening` | ~30s | 无 |
| 6. 位移+地理编码 | `SARsInSARPhaseToDisplacement` | ~7600s | envipyengine 可能提前返回或挂起；第二个 Task 对曾出现 "SARscape process unexpectedly terminated" |

单个 Task 对总耗时约 2.8 小时（测试数据）。两个 Task 对串行处理约 3.8 小时。

## 五、前端锁机制

### 两层锁结构

1. **全局蓝色遮罩** (`App.jsx` → `global-task-overlay`)
   - `App.jsx` 每 3 秒轮询 `GET /tasks/active`
   - 有任何活跃任务 → `isGlobalLocked=true` → 全屏蓝色遮罩覆盖整个 UI
   - 显示所有活跃任务的名称、进度条、状态消息
   - 管理员可见「管理员强制解锁」按钮 → 输入密码 → 取消所有任务 → 遮罩消失
   - 任务完成后自动刷新页面数据

2. **IDLAutomationPanel 内部锁** (`IDLAutomationPanel.jsx`)
   - 每 10 秒轮询 `GET /tasks/active`，查找 IDL 类型任务
   - 有活跃 IDL 任务 → `isLocked=true` → 按钮禁用 + 黄色横幅
   - 横幅内也有强制解锁按钮（作为备用入口）

### 强制解锁 API

- `POST /tasks/{task_id}/force-cancel`
- 需要管理员 JWT + 请求体中的密码双重验证
- 将任务标记为 CANCELLED，前端下次轮询时解锁

## 六、关键文件清单

| 文件 | 职责 |
|------|------|
| `backend/app/services/envi_service.py` | ENVI 工作流核心（6 步流程、GCP 生成、文件稳定等待） |
| `backend/app/services/envi_runner_cli.py` | 子进程入口（加载 .env、调用 run_workflow） |
| `backend/app/services/job_handlers.py` | Job handler（子进程监控、keepalive、文件稳定检查） |
| `backend/app/services/job_worker.py` | Worker 主循环（心跳、stale 检测） |
| `backend/app/services/task_service.py` | Task 管理（僵尸检测在 get_active_tasks 中） |
| `backend/app/routers/tasks.py` | Task API（含 force-cancel 端点） |
| `backend/app/routers/idl.py` | IDL 路由（Import/D-InSAR 任务提交） |
| `frontend/src/App.jsx` | 全局遮罩锁 + 强制解锁 UI |
| `frontend/src/IDLAutomationPanel.jsx` | SARscape 面板（内部锁 + 备用强制解锁） |
| `frontend/src/api/idl.js` | 前端 API 函数 |
| `.env` | 所有超时和处理参数配置 |

## 七、待测试内容

1. **keepalive 机制验证**: 确认前端在整个处理过程中保持锁定（蓝色遮罩不消失），进度和步骤信息正确显示
2. **双 Task 对完整流程**: 两个 Task 对串行处理，全部完成后前端才解锁
3. **全局遮罩强制解锁**: 管理员在蓝色遮罩上输入密码后能正确取消任务并解锁
4. **进度显示**: 遮罩上的进度条和步骤信息随 ENVI 处理实时更新（Step 1/6 → Step 6/6, 10% → 90%）
5. **异常恢复**: envipyengine 报错时文件扫描兜底逻辑是否可靠
6. **metatask 模式**: 确认默认模式不受自定义模式改动影响
7. **多用户场景**: 管理员操作锁定时，只读用户仍可正常浏览（只读用户看不到强制解锁按钮）
8. **第二个 Task 对 step 6 失败**: 上次测试中第二个 Task 对的 step 6 报 "SARscape process unexpectedly terminated"，需确认是数据问题还是系统问题

## 八、最近一次测试结果 (2026-02-23)

- Task_20250309_20250112: 全部 6 步成功，总耗时 10104.9s
- Task_20250310_20250113_9: 步骤 1-5b 成功，step 6 失败 ("SARscape process unexpectedly terminated"，耗时 1853s)
- 第一个 Task 的 `_wait_for_disp_stable` 修复生效（成功路径也等待 disp 文件）
- 前端锁在第一个 Task 处理期间因僵尸检测被误杀（已通过 keepalive 修复，待验证）
