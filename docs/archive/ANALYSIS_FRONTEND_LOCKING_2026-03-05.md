# 前端锁定问题分析（2026-03-05）

## 问题描述

用户报告在执行以下操作时前端没有锁定：
1. D-InSAR 结果扫描
2. 数据分发（复制任务）

## 根本原因分析

### 问题 1：扫描任务前端未锁定

**原因**：任务执行太快（< 3 秒），前端轮询间隔（3 秒）来不及捕获。

**证据**：
```
[性能] Footprint 提取耗时: 1622.1ms (文件: Task_20250120_20250217_2_geo_disp)
```
单个文件处理只需 1.6 秒，如果只有少量文件，整个扫描任务可能在 2-3 秒内完成。

**前端轮询逻辑**：
```javascript
// useGlobalTaskControl.js
useEffect(() => {
  const interval = setInterval(() => {
    syncActiveTasks();  // 每 3 秒轮询一次
  }, 3000);
  return () => clearInterval(interval);
}, []);
```

**时间线**：
```
T=0s:   用户点击"扫描"按钮
T=0.1s: 后端创建 SystemTask (status=PENDING)
T=0.2s: job_worker 开始执行，更新为 RUNNING
T=2.0s: 扫描完成，更新为 COMPLETED
T=3.0s: 前端第一次轮询，任务已经完成
```

**结论**：前端来不及捕获 RUNNING 状态，直接看到 COMPLETED。

---

### 问题 2：数据分发任务前端未锁定

**根本原因**：`DataCopierPanel` 没有调用全局锁定机制。

**证据**：

1. **App.jsx 中的调用对比**：

```javascript
// IDLAutomationPanel - 有 onJobQueued 回调 ✅
<IDLAutomationPanel
    apiEndpoint={apiClient.defaults.baseURL}
    readOnly={isReadOnlyUser}
    onJobQueued={(taskId) => handleTaskStart(taskId, '任务已入队，等待处理...')}
/>

// DataMonitorPanel - 有 onTaskStart 回调 ✅
<DataMonitorPanel
    apiEndpoint={apiClient.defaults.baseURL}
    onTaskStart={handleTaskStart}
    readOnly={isReadOnlyUser}
/>

// DataCopierPanel - 没有任何回调 ❌
<DataCopierPanel
    apiEndpoint={apiClient.defaults.baseURL}
    readOnly={isReadOnlyUser}
/>
```

2. **DataCopierPanel 内部逻辑**：

```javascript
// DataCopierPanel.jsx:97-133
const handleStartCopy = async () => {
    // ... 验证逻辑 ...

    setIsUploading(true);
    setLogs([]);
    setStatus('RUNNING');  // 只更新组件内部状态

    try {
        const response = await axios.post(endpoint, {
            batch_id: selectedBatchId,
            dest_dir: destDir,
            copy_statuses: copyStatuses,
        }, { withCredentials: true });
        setTaskId(response.data.task_id);  // 只保存 task_id
        // ❌ 没有调用 onJobQueued 或 onTaskStart
    } catch (error) {
        // ... 错误处理 ...
    } finally {
        setIsUploading(false);
    }
};
```

3. **组件自己管理状态**：

`DataCopierPanel` 使用自己的状态管理（`status`、`taskId`、`logs`），通过轮询 `/tools/copy-status/${taskId}` 获取进度，**完全独立于全局任务控制系统**。

```javascript
// DataCopierPanel.jsx:82-95
const fetchLogs = async () => {
    if (!taskId) return;
    try {
        const response = await axios.get(`${apiEndpoint}/tools/copy-status/${taskId}`, { withCredentials: true });
        setLogs(response.data.logs);
        const nextStatus = normalizeStatus(response.data.status);
        if (nextStatus && nextStatus !== 'UNKNOWN') {
            setStatus(nextStatus);  // 只更新组件内部状态
        }
    } catch (error) {
        console.error('Failed to load logs:', error);
    }
};
```

**结论**：`DataCopierPanel` 是一个独立的任务管理系统，不与全局锁定机制集成。

---

## 架构对比

### 全局锁定机制（IDLAutomationPanel、DataMonitorPanel）

```
用户操作 → 组件调用 API → 后端创建 SystemTask
                    ↓
        组件调用 onJobQueued(taskId)
                    ↓
        App.jsx 调用 handleTaskStart(taskId)
                    ↓
        setPendingTaskIds([...prev, taskId])
                    ↓
        useGlobalTaskControl 轮询 /tasks/active
                    ↓
        检测到 RUNNING 任务 → setIsGlobalLocked(true)
                    ↓
        任务完成 → setIsGlobalLocked(false)
```

### 独立任务管理（DataCopierPanel）

```
用户操作 → 组件调用 API → 后端创建 SystemTask
                    ↓
        组件保存 taskId（内部状态）
                    ↓
        组件轮询 /tools/copy-status/${taskId}
                    ↓
        更新组件内部状态（status、logs）
                    ↓
        ❌ 不触发全局锁定
```

---

## 影响评估

### 扫描任务未锁定

**影响等级**：🟢 低

**原因**：
- 扫描任务通常很快（< 3 秒）
- 扫描是只读操作，不会修改数据
- 即使用户在扫描时操作，也不太可能造成冲突

**是否需要修复**：可选
- 如果希望用户明确感知到扫描正在进行，可以修复
- 如果接受"快速任务不锁定"的行为，可以不修复

---

### 数据分发任务未锁定

**影响等级**：🟡 中等

**原因**：
- 数据分发任务可能耗时较长（几分钟到几十分钟）
- 用户可能在分发过程中启动其他任务
- 可能导致资源竞争（磁盘 I/O、网络带宽）

**潜在问题**：
1. 用户可能同时启动多个分发任务，导致磁盘 I/O 饱和
2. 用户可能在分发过程中启动 ENVI 工作流，导致系统卡顿
3. 用户可能不知道分发任务正在后台运行

**是否需要修复**：建议修复
- 数据分发是长时间运行的任务
- 应该与全局锁定机制集成
- 提升用户体验和系统稳定性

---

## 修复方案

### 方案 A：集成到全局锁定机制（推荐）

**修改文件**：
1. `App.jsx`
2. `DataCopierPanel.jsx`

**步骤**：

1. **在 App.jsx 中传递回调**：

```javascript
{leftPanelTab === 'copier' && (
    <div className="panel-content" style={{ flex: '1 1 auto', padding: 0, overflow: 'hidden' }}>
        <DataCopierPanel
            apiEndpoint={apiClient.defaults.baseURL}
            readOnly={isReadOnlyUser}
            onJobQueued={(taskId) => handleTaskStart(taskId, '数据分发任务已入队')}  // 添加此行
        />
    </div>
)}
```

2. **在 DataCopierPanel.jsx 中接收并调用回调**：

```javascript
// 修改组件签名
const DataCopierPanel = ({ apiEndpoint, readOnly = false, onJobQueued }) => {
    // ... 现有代码 ...

    const handleStartCopy = async () => {
        // ... 验证逻辑 ...

        setIsUploading(true);
        setLogs([]);
        setStatus('RUNNING');

        try {
            const response = await axios.post(endpoint, {
                batch_id: selectedBatchId,
                dest_dir: destDir,
                copy_statuses: copyStatuses,
            }, { withCredentials: true });

            const taskId = response.data.task_id;
            setTaskId(taskId);

            // 调用全局锁定回调
            if (onJobQueued) {
                onJobQueued(taskId);
            }
        } catch (error) {
            // ... 错误处理 ...
        } finally {
            setIsUploading(false);
        }
    };
};
```

**优点**：
- 与现有架构一致
- 最小改动
- 复用全局锁定机制

**缺点**：
- 组件仍然保留自己的状态管理（有一定冗余）

---

### 方案 B：完全重构为全局任务管理

**修改范围**：大

**步骤**：
1. 移除 `DataCopierPanel` 的内部状态管理
2. 使用全局 `activeTasks` 和 `ActiveTasksOverlay` 显示进度
3. 移除组件内的轮询逻辑

**优点**：
- 架构统一
- 减少代码冗余

**缺点**：
- 改动较大
- 可能影响现有功能
- 需要充分测试

---

### 方案 C：保持现状，添加警告提示

**修改文件**：`DataCopierPanel.jsx`

**步骤**：
在任务运行时显示警告提示：

```javascript
{status === 'RUNNING' && (
    <div className="warning-banner">
        ⚠️ 数据分发任务正在后台运行，请勿关闭浏览器或启动其他耗时任务
    </div>
)}
```

**优点**：
- 改动最小
- 不影响现有架构

**缺点**：
- 不解决根本问题
- 用户仍可能启动冲突任务

---

## 扫描任务修复方案（可选）

### 方案 A：前端立即锁定

在提交扫描任务后立即锁定，不等待轮询：

```javascript
// DataManagementPanel.jsx
const handleScanDinsarResults = async () => {
    try {
        // 立即锁定前端
        if (onTaskStart) {
            onTaskStart(null, 'SCAN_DINSAR', '正在扫描 D-InSAR 结果...');
        }

        const response = await scanDinsarResults(selectedDirs);
        const taskId = response.data.task_id;

        // 更新任务 ID
        if (onTaskStart) {
            onTaskStart(taskId, 'SCAN_DINSAR', '正在扫描 D-InSAR 结果...');
        }

        addLog('info', `扫描任务已提交: ${taskId}`);
    } catch (error) {
        // 解锁前端
        setIsGlobalLocked(false);
        addLog('error', `扫描失败: ${error.message}`);
    }
};
```

### 方案 B：后端添加最小执行时间

```python
# job_handlers.py
async def _handle_scan_dinsar(job: SystemJobORM) -> None:
    start_time = time.time()

    # ... 执行扫描 ...

    # 确保任务至少运行 2 秒
    elapsed = time.time() - start_time
    if elapsed < 2.0:
        await asyncio.sleep(2.0 - elapsed)
```

---

## 推荐修复顺序

1. **优先修复数据分发任务**（方案 A）
   - 影响较大
   - 修改简单
   - 风险低

2. **可选修复扫描任务**（方案 A）
   - 影响较小
   - 提升用户体验
   - 风险低

3. **长期考虑重构**（方案 B）
   - 统一架构
   - 减少冗余
   - 需要充分测试

---

## 总结

- **扫描任务未锁定**：任务太快，轮询来不及捕获（影响小，可选修复）
- **数据分发任务未锁定**：组件独立管理状态，未集成全局锁定（影响中，建议修复）
- **推荐方案**：为 `DataCopierPanel` 添加 `onJobQueued` 回调，集成到全局锁定机制
