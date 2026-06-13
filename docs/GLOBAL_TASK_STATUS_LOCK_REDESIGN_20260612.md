# 全局任务状态与界面锁重构设计

最后更新：2026-06-12

## 1. 背景

当前前端存在一个“全局界面锁”机制：只要系统检测到仍有活跃任务，并且该任务不在前端非阻塞白名单内，就会把整个系统切到全局锁定状态，并弹出 `ActiveTasksOverlay` 全屏遮罩。

这个设计在早期可以避免用户在 ENVI / SARscape / IDL 长任务执行期间重复点击、切换入口或提交冲突任务。但随着系统扩展到 D-InSAR、多引擎生产、SBAS-InSAR、数据接入、资产扫描、洪涝分析、AI 诊断等多个相对独立的任务域，全局锁已经过粗：

- SBAS Gamma / LandSAR SBAS 已经证明长任务可以不锁全局界面，只在模块内展示状态。
- 后端 `task_service.create_task()` 已经按 `task_type` 做同类型 `PENDING/RUNNING` 互斥。
- 很多任务只是扫描、解包、目录重建或产物刷新，不应阻断其他业务操作。
- 全屏遮罩会遮住当前功能页，用户看不到任务详情，也无法继续浏览结果或管理其他独立任务。

因此，本设计将“全局界面锁”降级为“全局任务状态中心”，并要求每个功能模块设计自己的任务状态面板和局部操作约束。

## 2. 当前实现审计

### 2.1 前端锁入口

核心文件：

- `frontend/src/hooks/useGlobalTaskControl.js`
- `frontend/src/hooks/useDinsarOperations.js`
- `frontend/src/components/ActiveTasksOverlay.jsx`
- `frontend/src/components/app/AppOverlays.jsx`
- `frontend/src/ProductionWorkspace.jsx`

当前逻辑：

1. `useGlobalTaskControl` 订阅 `/tasks/active` 或 SSE `/tasks/active/stream`。
2. 前端维护 `pendingTaskIds` 和 `nonBlockingTaskIds`。
3. 活跃任务中只要存在不属于 `NON_BLOCKING_TASK_TYPES` 的任务，就设置 `isGlobalLocked=true`。
4. `AppOverlays` 根据 `isGlobalLocked` 渲染 `ActiveTasksOverlay`。
5. `App.ensureCanOperate()` 会因为 `isGlobalLocked` 拒绝写操作。

### 2.2 当前非阻塞任务

当前白名单包括：

- `UNPACK_ARCHIVES`
- `UNPACK_SENTINEL1`
- `GF3_UNPACK`
- `GF3_SARSCAPE_PRODUCE`
- `GF3_SARSCAPE_SYNC`
- `GF3_SARSCAPE_CLEAN`
- `SCAN_ASSET_INVENTORY`
- `COPY_DATA`
- `SBAS_GAMMA_WORKFLOW`
- `SBAS_LANDSAR_WORKFLOW`
- `SBAS_COREGISTRATION`
- `SBAS_RDC_DEM`
- `SBAS_INTERFEROGRAMS`
- `SBAS_IPTA_TIMESERIES`
- `REBUILD_SBAS_INSAR_CATALOG`

这些任务已经以“不锁全局界面”的方式运行。

### 2.3 当前仍会锁全局界面的任务

主要原因通常不是任务本身必须锁，而是提交时没有传 `taskType` 或 `nonBlocking: true`。

已审计到的典型入口：

- D-InSAR 生产任务
  - `ProductionWorkspace.handleDinsarRunQueued()`
  - `DinsarProductionPanel.handleSubmit()`
- D-InSAR 结果扫描
  - `ProductionWorkspace.handleDinsarProductQueued()`
  - `DinsarProductsPanel.handleScan()`
- IDL Import / IDL DInSAR
  - `IDLAutomationPanel`
- AI 训练、全量预测、AI 诊断
  - `useDinsarOperations.handleTrainAi()`
  - `useDinsarOperations.handlePredictAll()`
  - `useDinsarOperations.handleAnalyzeResult()`
- 灾害点同步
  - `HazardPointPanel`
- 水体 / 洪涝检测任务
  - `WaterMonitorPanel`
- 部分数据监控任务
  - `GF3_BATCH_PROCESS`
  - `SCAN_DATA`
  - 手动 LT-1 / 精轨 / GF3 扫描
- 预览缓存重建
  - `App.rebuildRadarPreviewCache()` 通过 `handleTaskStart(null, ...)` 触发临时全局锁

### 2.4 后端已有保护

后端 `backend/app/services/task_service.py` 在 `create_task()` 中已做同类型任务互斥：

- 查询相同 `task_type` 且状态为 `PENDING` / `RUNNING` 的任务。
- 如果存在，直接返回任务冲突错误。

这说明前端全局锁不是唯一安全机制。真正的任务并发保护应继续下沉到后端，并从 `task_type` 扩展到更精确的资源锁。

## 3. 设计目标

1. 取消“任意阻塞任务遮住整个系统”的交互模式。
2. 全局层只负责展示所有活跃任务、最近任务、失败任务和快捷入口。
3. 每个功能模块拥有自己的任务状态面板，只展示和本功能相关的任务。
4. 默认任务不锁全局界面。
5. 需要互斥的场景由后端资源锁保证，而不是靠前端遮罩保证。
6. 前端只做局部禁用：禁用同一个功能里会造成重复提交或破坏状态的按钮。
7. 保留管理员取消任务能力，但从“强制解锁”改为“取消/中止任务”。

## 4. 非目标

本设计不要求一次性重写所有任务系统。

不在本阶段处理：

- 重做后端任务表结构。
- 重写 job worker。
- 改变现有任务 API 的基本返回格式。
- 一次性把所有历史任务类型改名。
- 删除所有旧 overlay 代码。

本设计优先保证渐进迁移。

## 5. 新架构概览

```text
后端任务系统
├─ SystemTaskORM / TaskLogORM
├─ SystemJobORM
├─ task_type 同类型互斥
└─ 后续扩展：resource_lock_key / resource_scope

前端任务状态层
├─ 全局任务状态中心
│  ├─ 展示所有活跃任务
│  ├─ 展示最近失败 / 完成任务
│  ├─ 支持跳转到所属功能
│  └─ 不遮住全系统
├─ 功能级任务面板
│  ├─ D-InSAR 生产任务面板
│  ├─ SBAS-InSAR 生产任务面板
│  ├─ 数据接入任务面板
│  ├─ 资产库存任务面板
│  ├─ 洪涝分析任务面板
│  └─ AI / 灾害点任务面板
└─ 局部操作约束
   ├─ 同类任务运行中，禁用同类提交按钮
   ├─ 目录重建中，禁用同一目录重建按钮
   └─ 其他功能仍可浏览和操作
```

## 6. 全局任务状态中心

### 6.1 职责

全局任务状态中心只做观察和导航，不做全局阻断。

职责：

- 汇总 `/tasks/active`。
- 汇总最近任务 `/tasks/recent`。
- 显示任务类型、状态、进度、开始时间、最近消息。
- 支持按功能域筛选：
  - 数据接入
  - D-InSAR
  - SBAS-InSAR
  - 洪涝
  - AI
  - 运维
- 支持点击任务跳转到所属功能页。
- 支持查看任务日志。
- 支持管理员取消任务。

### 6.2 交互形态

建议替换现有 `ActiveTasksOverlay`：

- 不再使用全屏遮罩。
- 使用顶部状态入口或右下角任务抽屉。
- 有活跃任务时显示小型状态指示。
- 点击后打开任务中心抽屉或弹层。
- 弹层不阻止用户关闭和继续使用系统。

### 6.3 状态文案

旧文案：

```text
为了保证数据一致性，耗时任务执行期间 UI 已锁定。
```

应替换为：

```text
后台任务正在执行。你可以继续使用其他功能；同类任务的重复提交会由系统自动限制。
```

## 7. 功能级任务面板

每个功能页应自行展示本功能任务状态。这样用户在当前业务上下文里能直接看到“我刚提交的任务跑到哪一步”，而不是被全局遮罩挡住。

### 7.1 通用组件建议

新增通用组件：

- `TaskStatusPanel`
- `TaskLogPanel`
- `TaskProgressRow`
- `TaskStatusBadge`
- `useTaskMonitor`

建议参数：

```ts
type TaskStatusPanelProps = {
  title: string;
  taskTypes?: string[];
  taskTypePrefixes?: string[];
  taskIds?: string[];
  showRecent?: boolean;
  compact?: boolean;
  onTaskClick?: (task) => void;
};
```

`useTaskMonitor` 负责：

- 按 `taskTypes` / `taskTypePrefixes` 查询活跃任务。
- 轮询或复用全局 SSE 数据。
- 拉取指定任务日志。
- 返回 `activeTasks`、`recentTasks`、`latestTask`、`isBusy`。

### 7.2 D-InSAR 生产

任务类型：

- `IDL_RUN_DINSAR`
- `ISCE2_RUN`
- `PYINT_RUN`
- `LANDSAR_RUN`

面板位置：

- `DinsarProductionPanel` 右侧或提交区下方。

局部约束：

- 如果同一 engine 的任务正在运行，禁用同 engine 再提交。
- 其他 engine 是否允许并行应由后端资源锁决定。
- 用户仍可查看历史 run、日志、结果列表。

### 7.3 D-InSAR 产物

任务类型：

- `SCAN_DINSAR`
- `DINSAR_RESULT_SCAN`
- `DINSAR_RESULT_PACKAGE`
- 以当前后端实际 task_type 为准。

面板位置：

- `DinsarProductsPanel` 扫描按钮旁或结果列表顶部。

局部约束：

- 同一 catalog 重建任务运行中，禁用重复重建按钮。
- 不影响 SBAS 生产、D-InSAR 生产、结果浏览。

### 7.4 SBAS-InSAR 生产

任务类型：

- `SBAS_GAMMA_WORKFLOW`
- `SBAS_LANDSAR_WORKFLOW`
- `SBAS_COREGISTRATION`
- `SBAS_RDC_DEM`
- `SBAS_INTERFEROGRAMS`
- `SBAS_IPTA_TIMESERIES`

当前状态：

- 已基本符合新设计。
- 已在模块内展示 Runtime Status。
- 已显式设置 `nonBlocking: true`。

后续调整：

- 将 Runtime Status 抽成 `TaskStatusPanel` 风格组件。
- 支持按 `run_id` 过滤关联任务。
- 全局任务中心只显示摘要和跳转入口。

### 7.5 SBAS-InSAR 结果

任务类型：

- `REBUILD_SBAS_INSAR_CATALOG`

局部约束：

- 重建中只禁用“重建目录”按钮。
- 结果列表仍可浏览。

当前已接近目标。

### 7.6 数据接入与资产扫描

任务类型：

- `UNPACK_ARCHIVES`
- `UNPACK_SENTINEL1`
- `GF3_UNPACK`
- `GF3_BATCH_PROCESS`
- `GF3_SARSCAPE_PRODUCE`
- `GF3_SARSCAPE_SYNC`
- `GF3_SARSCAPE_CLEAN`
- `SCAN_DATA`
- `SCAN_ASSET_INVENTORY`

建议：

- 数据接入页按卫星/流程展示独立状态卡。
- `SCAN_DATA` 不应锁全局界面。
- `GF3_BATCH_PROCESS` 是否需要局部锁取决于是否写共享目录；默认只锁 GF3 预处理按钮。

### 7.7 洪涝 / 水体分析

任务类型：

- `WATER_GEOCODE_*`
- `WATER_DETECT_*`
- `WATER_FLOOD_*`
- `FLOOD_SCENE_PREPROCESS_*`
- `FLOOD_WATER_EXTRACTION_*`
- `FLOOD_DETECTION_*`
- `GF3_PROCESS_*`

建议：

- 洪涝工作台显示场景级任务状态。
- 使用 task type prefix 匹配。
- 不再触发全局锁。
- 单个场景处理时，只禁用该场景相关按钮。

### 7.8 AI 与灾害点

任务类型：

- `AI_TRAIN`
- `AI_PREDICT`
- `AI_ANALYZE`
- `AI_WARMUP`
- `SCAN_HAZARD`

建议：

- AI 质量页显示 AI 训练/预测任务状态。
- AI 诊断页显示诊断任务状态。
- `AI_TRAIN` 运行中禁用再次训练；不阻止浏览结果。
- `AI_ANALYZE` 运行中只禁用同一结果的重复诊断。
- `SCAN_HAZARD` 只影响灾害点同步按钮。

## 8. 任务分类模型

建议引入统一任务元数据配置，前端和后端可逐步共享。

```ts
type TaskUiPolicy = {
  taskType: string;
  featureScope: string;
  label: string;
  globalVisible: boolean;
  globalBlocking: boolean;
  localBlocking: boolean;
  resourceScope?: string;
  routeTarget?: string;
};
```

默认策略：

- `globalVisible=true`
- `globalBlocking=false`
- `localBlocking=true`

也就是说，任务默认显示在全局任务中心，但不锁整个系统。

只有极少数任务可设置：

```ts
globalBlocking=true
```

但这应作为过渡兼容，不作为长期设计。

## 9. 后端资源锁设计

后端当前只有 `task_type` 级互斥。这不足以表达以下场景：

- 同一个 run 不能同时执行两个会修改同一状态文件的步骤。
- 同一个 catalog 不能并发重建。
- 同一个输出目录不能被两个生产任务同时写入。
- 同一个 ENVI / SARscape 单实例资源不能并行调用。

建议新增资源锁概念。

### 9.1 资源锁 key

示例：

```text
sbas-run:{run_id}
sbas-catalog:{catalog_root}
dinsar-engine:{engine_code}
dinsar-root:{root_dir}
envi-runtime:{host_or_profile}
gf3-sarscape-root:{root_dir}
water-scene:{scene_id}
ai-model:{model_id}
```

### 9.2 后端行为

创建任务时检查：

- 同 `task_type` 是否已有活跃任务。
- 同 `resource_lock_key` 是否已有活跃任务。

如果冲突：

- 返回 `409 Conflict`。
- 返回冲突任务 ID、任务类型、状态、消息。

前端收到后：

- 不弹全局锁。
- 在当前功能面板提示“已有同资源任务运行中”。
- 提供跳转到任务日志。

### 9.3 迁移方式

第一阶段不必改数据库结构，可把资源锁写入 `SystemTaskORM.params`：

```json
{
  "resource_lock_key": "sbas-run:sbas_e21648e52bd4",
  "feature_scope": "sbas_insar"
}
```

后续再考虑独立列或资源锁表。

## 10. 前端状态管理重构

### 10.1 保留内容

保留：

- `/tasks/active` SSE / fallback polling。
- `activeTasks` 全局缓存。
- 任务完成后刷新相关数据的能力。

### 10.2 移除或降级内容

降级：

- `isGlobalLocked`
- `pendingTaskIds`
- `nonBlockingTaskIds`
- `NON_BLOCKING_TASK_TYPES`
- `ActiveTasksOverlay`

迁移后：

- `isGlobalLocked` 不再驱动全屏遮罩。
- `pendingTaskIds` 不再用于判断系统锁定。
- `nonBlockingTaskIds` 不再需要。
- `NON_BLOCKING_TASK_TYPES` 变成 `TASK_UI_POLICIES`。
- `ActiveTasksOverlay` 替换为 `GlobalTaskCenter`。

### 10.3 新 hook

建议新增：

```text
frontend/src/hooks/useTaskCenter.js
frontend/src/hooks/useTaskMonitor.js
frontend/src/config/taskUiPolicies.js
```

`useTaskCenter`：

- 订阅 active tasks。
- 维护全局任务状态。
- 提供任务完成事件分发。

`useTaskMonitor`：

- 从全局任务状态中过滤当前功能相关任务。
- 提供 `isBusy`、`latestTask`、`activeTasks`、`recentTasks`。

## 11. 任务完成后的刷新策略

当前全局锁解除时会调用 `initializeAppData({ refreshRadarSearch: true })`，这也过粗。

应改成按任务类型刷新：

| 任务类型 | 刷新目标 |
|---|---|
| `UNPACK_ARCHIVES` | LT-1 数据检索选项、当前检索页 |
| `UNPACK_SENTINEL1` | Sentinel-1 资产状态 |
| `SCAN_ASSET_INVENTORY` | 资产库存状态 |
| `SBAS_*` | 对应 SBAS run detail 或产品 catalog |
| `REBUILD_SBAS_INSAR_CATALOG` | SBAS 产品列表 |
| `SCAN_DINSAR` | D-InSAR 产品列表 |
| `AI_TRAIN` / `AI_PREDICT` | AI 状态与结果质量 |
| `SCAN_HAZARD` | 灾害点列表 |
| `WATER_*` / `FLOOD_*` | 洪涝工作台当前场景/事件 |

实现上可维护：

```ts
TASK_COMPLETION_REFRESH_POLICIES
```

每个功能页也可以订阅自己的任务完成事件。

## 12. 迁移计划

### 阶段 1：文档与审计

状态：本设计文档。

输出：

- 当前锁机制审计。
- 新交互原则。
- 迁移边界。

### 阶段 2：抽象任务 UI policy

新增：

- `frontend/src/config/taskUiPolicies.js`

内容：

- task type -> label
- task type -> feature scope
- task type -> route target
- task type -> local/global blocking policy

替换：

- `useGlobalTaskControl.NON_BLOCKING_TASK_TYPES`
- `useDinsarOperations.NON_BLOCKING_TASK_TYPES`
- `ActiveTasksOverlay.getTaskTypeLabel`

验收：

- 所有任务类型 label 来自同一配置。
- 新任务默认不锁全局。

### 阶段 3：全局遮罩改为任务中心

新增：

- `GlobalTaskCenter`
- `TaskCenterButton` 或顶部状态入口

替换：

- `ActiveTasksOverlay`

验收：

- 有活跃任务时不再遮住全系统。
- 用户可继续切换页面和浏览结果。
- 管理员仍可取消任务。

### 阶段 4：功能级任务面板

优先级：

1. D-InSAR 生产
2. D-InSAR 产物
3. 数据接入
4. 洪涝分析
5. AI / 灾害点
6. SBAS 组件收敛到通用面板

验收：

- 每个功能页能看到本功能任务状态。
- 同类任务运行中，只禁用同类提交按钮。

### 阶段 5：后端资源锁

新增：

- 任务 params 中写入 `feature_scope`、`resource_lock_key`。
- `task_service.create_task()` 支持资源锁冲突检查。

验收：

- 同 run、同目录、同 catalog 的冲突任务由后端返回 `409`。
- 前端不靠全局遮罩防冲突。

### 阶段 6：删除旧全局锁状态

删除或废弃：

- `isGlobalLocked`
- `pendingTaskIds`
- `nonBlockingTaskIds`
- `handleTaskStart(null, ...)` 触发全局锁的路径

验收：

- 代码中不存在“无 taskId 触发全局锁”的逻辑。
- 全局任务中心只展示状态，不控制系统可用性。

## 13. 风险与处理

### 13.1 后端资源冲突未覆盖

风险：

- 去掉前端全局锁后，某些共享目录或单实例程序可能被并发调用。

处理：

- 迁移初期保留少量 `globalBlocking=true` 兼容策略。
- 优先给 ENVI / SARscape / 同目录生产补资源锁。
- 对不确定任务先做局部锁，不做全局遮罩。

### 13.2 用户忽略后台任务

风险：

- 没有全屏遮罩后，用户可能不知道任务仍在跑。

处理：

- 顶部或右下角常驻任务指示。
- 功能页内明确显示任务状态。
- 失败任务有醒目提示。

### 13.3 任务完成刷新过少

风险：

- 以前全局刷新掩盖了局部刷新缺失。

处理：

- 建立 `TASK_COMPLETION_REFRESH_POLICIES`。
- 逐功能补刷新策略。
- 保留手动刷新入口。

## 14. 验收标准

完成重构后应满足：

1. 任意 SBAS 任务运行时，系统其他功能可正常浏览和操作。
2. D-InSAR 任务运行时，不再弹全屏锁；D-InSAR 面板显示任务进度。
3. 同一个 D-InSAR engine 或同一输出目录的冲突提交由后端拒绝。
4. 数据接入扫描/解包任务运行时，不影响生产管理和结果浏览。
5. 洪涝场景级任务运行时，只影响对应场景按钮。
6. AI 训练/预测运行时，不阻断地图、结果浏览和 SBAS/DInSAR 生产。
7. 所有活跃任务都能在全局任务中心找到。
8. 每个功能页能看到与本功能相关的任务。
9. 管理员取消任务能力仍可用，但语义是“取消任务”，不是“强制解锁界面”。
10. 代码中不再通过 `handleTaskStart(null, ...)` 触发全局锁。

## 15. 推荐首批改动清单

建议第一批代码改动只做前端交互，不动后端任务模型：

1. 新建 `taskUiPolicies.js`，统一任务 label、scope、blocking 策略。
2. 将 D-InSAR 生产、D-InSAR 产物、IDL、AI、灾害点、水体任务全部标为 `globalBlocking=false`。
3. `ActiveTasksOverlay` 改为可关闭的 `GlobalTaskCenter`。
4. `ensureCanOperate()` 不再读取 `isGlobalLocked`，只检查用户权限。
5. `rebuildRadarPreviewCache()` 删除 `handleTaskStart(null, ...)`，改用局部 loading。
6. D-InSAR 生产面板增加任务状态卡。
7. D-InSAR 产物面板增加目录扫描任务状态卡。

第二批再做后端资源锁。

## 16. 结论

全局界面锁应退出核心设计。任务系统的正确边界应是：

- 全局：看见所有任务。
- 功能页：管理本功能任务。
- 后端：保证资源互斥和并发安全。

前端全屏锁只应作为临时兼容手段，不应继续扩展。
