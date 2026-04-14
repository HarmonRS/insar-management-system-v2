# D-InSAR 增强 Task 文档

更新日期：2026-03-13

## 1. 文档目的

这份文档用于统一管理本轮 D-InSAR 系统重构任务，覆盖以下两大方向：

- D-InSAR 多引擎生产系统增强
- D-InSAR 结果管理系统增强

文档定位是“任务总表 + 实施边界 + 验收口径”，用于后续分阶段实施、联调和运维验收。

## 2. 背景

当前系统的 D-InSAR 生产能力主要绑定在 ENVI + SARscape 链路上，前端、后端任务模型、运行监控和结果入库都默认只有一个处理引擎。这会带来两个直接问题：

- 客户如果没有 ENVI/SARscape license，D-InSAR 核心生产能力会直接失效。
- 后续接入 ISCE2、LANDSAR 时，只能继续堆在现有 IDL/ENVI 语义上，导致架构持续恶化。

同时，当前 D-InSAR 结果管理能力偏轻，更多是“结果浏览 + 缓存管理”，还不足以支撑多引擎、多版本、多产物、多轮生产的正式管理需求。

## 3. 本轮目标

### 3.1 生产系统目标

- 将 D-InSAR 生产系统升级为“多引擎生产中心”
- 正式支持两类可执行引擎：
  - `sarscape`：ENVI + SARscape
  - `isce2`：WSL 内部署的 ISCE2
- 预留第三类引擎：
  - `landsar`：仅保留接口和前端占位，不实现算法
- 将运维自检升级为“系统级 + D-InSAR 专项级”双层检查

### 3.2 结果管理目标

- 将 D-InSAR 结果从“单文件记录”升级为“可追踪、可治理、可比对”的结果资产
- 支持来源追踪、版本管理、多产物管理、审核状态和运维治理
- 让结果管理适配多引擎场景，而不是继续依赖单一文件命名和单一路径假设

## 4. 范围与非范围

### 4.1 本轮范围

- 多引擎 D-InSAR 后端抽象层
- 新 D-InSAR 生产中心前端架构
- ENVI/SARscape 的兼容接入
- ISCE2 的 WSL 环境校验与运行接口
- 运维自检与 D-InSAR 专项健康检查
- D-InSAR 结果管理增强

### 4.2 明确不在本轮范围

- LANDSAR 算法实现
- 对现有配对算法本身做大幅重写
- 对 AI 诊断能力做独立重构
- 一次性替换全部旧接口

## 5. 现状要点

### 5.1 ENVI 现有处理模式

当前 ENVI 侧应明确建模为 `sarscape` 引擎下的两种 profile：

- `metatask`
- `custom6`

其中 `custom6` 的 6 步链路为：

1. Interferogram Generation
2. Filtering and Coherence
3. Orbital Trend Removal
4. Phase Unwrapping
5. GCP Generation + Refinement
6. Phase to Displacement + Geocoding

### 5.2 ISCE2 的特殊约束

ISCE2 不走本机 Windows 直接执行链路，默认按 WSL 方式部署与运行，因此必须把 WSL 环境校验设计成正式能力，而不是上线前人工检查事项。

### 5.3 当前结果管理主要短板

- 结果记录字段偏少，缺少引擎来源、运行来源、版本、产物类型等核心字段
- 结果入库逻辑仍偏向单文件扫描，不适合多引擎多版本
- 前端结果面板偏重展示，缺少治理、比对、版本和来源追踪能力
- 运维自检尚未覆盖结果资产层面的异常

## 6. Task 总览

| Task ID | 主题 | 目标 |
|---|---|---|
| T1 | 多引擎生产基座 | 建立统一的 D-InSAR 引擎抽象、调度和兼容层 |
| T2 | 前端生产中心 | 将现有 IDL 自动化面板升级为多引擎生产中心 |
| T3 | SARSCAPE 兼容接入 | 将现有 ENVI 链路适配到新抽象层 |
| T4 | ISCE2 + WSL 接入 | 建立 ISCE2 的 WSL 检查、运行和日志链路 |
| T5 | 运维自检增强 | 增加多引擎专项健康检查和自愈入口 |
| T6 | 结果管理增强 | 将 D-InSAR 结果升级为正式结果资产管理体系 |

## 7. 任务拆分

### T1. 多引擎生产基座

#### 目标

- 将 D-InSAR 生产从 `IDL/ENVI 单引擎假设` 中抽离
- 为 `sarscape`、`isce2`、`landsar` 建立统一抽象

#### 关键任务

- 定义统一引擎接口
- 建立引擎注册表
- 建立统一运行记录模型
- 建立统一产物收集与入库接口
- 保留旧 `/idl/...` 路由兼容转发能力

#### 目标结构

- `dinsar_engines/base.py`
- `dinsar_engines/registry.py`
- `dinsar_engines/sarscape_engine.py`
- `dinsar_engines/isce2_engine.py`
- `dinsar_engines/landsar_engine.py`
- `dinsar_orchestrator.py`

#### 验收标准

- 新生产调度层可按 `engine_code` 分发
- `sarscape`、`isce2`、`landsar` 均可在注册表中查询到
- 旧 ENVI 生产功能不回归

### T2. 前端生产中心

#### 目标

- 用统一生产中心替代当前单引擎 `IDLAutomationPanel`
- 支持多引擎选择、运行监控、日志查看和结果入库操作

#### 前端信息架构

- 引擎状态区
  - SARSCAPE
  - ISCE2
  - LANDSAR
- 生产提交区
  - 批次选择
  - 引擎选择
  - profile 选择
  - 输入/输出目录
  - 参数模板
- 运行监控区
  - 当前任务
  - 步骤进度
  - 运行日志
  - 历史记录
- 产物处理区
  - 提取
  - 入库
  - 重扫
  - 缓存重建

#### 关键任务

- 新建 `DinsarProductionPanel`
- 废弃前端对 `/idl/...` 语义的直接依赖
- 增加引擎状态卡和 WSL 状态卡
- 运行记录中展示 `engine`、`profile`、`run_id`

#### 验收标准

- 前端可以清晰区分三类引擎状态
- `landsar` 显示为预留，不可执行
- 当 ENVI 不可用但 ISCE2 可用时，页面仍可提交 ISCE2 任务

### T3. SARSCAPE 兼容接入

#### 目标

- 将现有 ENVI 链路正式纳入新生产体系
- 明确 `metatask` 与 `custom6` 是同一引擎下的两个 profile

#### 关键任务

- 封装现有 ENVI 运行逻辑为 `sarscape_engine`
- 将 `metatask` 和 `custom6` 暴露为 profile
- 保留进度文件、日志、超时监控和历史运行能力

#### 验收标准

- 旧 ENVI 作业仍可执行
- 新系统中可选择 `sarscape/metatask` 与 `sarscape/custom6`
- 原有任务状态、日志和结果提取行为保持兼容

### T4. ISCE2 + WSL 接入

#### 目标

- 建立 ISCE2 运行的正式环境检查、任务执行和输出接入能力

#### WSL 环境校验任务

- 检查 WSL 是否安装
- 检查是否支持 WSL2
- 检查目标 distro 是否存在
- 检查目标 distro 是否可启动
- 检查 `bash -lc` 是否可执行
- 检查 Python 是否可执行
- 检查 ISCE2 是否可 import
- 检查目标入口命令是否可执行
- 检查 DEM 路径在 WSL 内是否可读
- 检查轨道目录在 WSL 内是否可读
- 检查输出目录在 WSL 内是否可写
- 检查 Windows 路径到 WSL 路径转换是否正确
- 增加可选 smoke test

#### 建议配置项

- `ISCE2_ENABLED`
- `ISCE2_WSL_DISTRO`
- `ISCE2_PYTHON`
- `ISCE2_PROFILE`
- `ISCE2_DEM_PATH`
- `ISCE2_ORBIT_DIR`
- `ISCE2_WORK_ROOT`
- `ISCE2_OUTPUT_ROOT`
- `ISCE2_SMOKE_TEST_ENABLED`

#### 关键任务

- 新建 `wsl_service.py`
- 新建 `isce2_engine.py`
- 建立 WSL 执行和日志封装
- 将 ISCE2 输出纳入统一结果收集和入库流程

#### 验收标准

- 前端可见 ISCE2 环境状态
- WSL 校验结果可解释、可定位故障点
- ISCE2 至少有一个正式 profile 可提交并跑通

### T5. 运维自检增强

#### 目标

- 将当前“系统健康检查”升级为“系统级 + D-InSAR 专项级”双层运维自检

#### 关键任务

- 保留现有 `/health` 简版接口
- 增加管理员详细健康接口
- 增加多引擎专项检查
- 增加 WSL 专项检查
- 增加 D-InSAR 队列、存储、结果一致性检查
- 增加自愈动作入口

#### 专项健康检查项

- 核心服务状态
  - 数据库
  - schema
  - worker
  - queue
- 引擎状态
  - `sarscape`
  - `isce2`
  - `landsar`
- WSL 状态
- DEM/轨道/输出目录可用性
- D-InSAR 运行任务积压
- stale run 检测
- 输出未入库检测
- 入库但源文件缺失检测
- 缓存一致性检测

#### 自愈入口建议

- 重扫结果目录
- 重建缓存
- 重试入库
- 重置 stale run
- 标记失效结果

#### 验收标准

- 运维面板能区分系统级故障与引擎级故障
- 当 ENVI 不可用但 ISCE2 可用时，总体 D-InSAR 服务状态应为 `degraded` 或 `ok`，不能直接判全系统失败
- WSL 故障能定位到具体检查项

### T6. 结果管理增强

#### 目标

- 将 D-InSAR 结果从“扫描到的一条文件记录”升级为“正式结果资产”

#### 核心设计

- `Run`
  - 一次真实生产执行
- `Artifact`
  - 一次运行产出的具体文件
- `Managed Result`
  - 面向前端与业务的主结果对象

#### 关键任务

- 扩充结果模型字段
- 增加结果来源追踪
- 增加版本管理
- 增加多产物管理
- 增加结果生命周期状态
- 增加批量治理能力
- 增加多引擎结果对比能力

#### 结果管理能力清单

- 来源追踪
  - engine
  - profile
  - batch
  - pair
  - run
- 版本管理
  - 当前版本
  - 历史版本
  - 最新成功版本
- 多产物管理
  - disp
  - coherence
  - unwrapped phase
  - geotiff
  - browse
  - log
- 生命周期管理
  - `NEW`
  - `INGESTED`
  - `QC_PENDING`
  - `QC_PASSED`
  - `QC_REJECTED`
  - `PUBLISHED`
  - `ARCHIVED`
- 批量操作
  - 发布
  - 归档
  - 重扫
  - 重建缓存
  - 重绑定来源
  - 导出
- 检索增强
  - 按引擎
  - 按批次
  - 按任务名
  - 按状态
  - 按 AOI
  - 按版本

#### 前端结果管理架构建议

- 结果列表视图
- 结果详情视图
- 来源与版本视图
- 治理与运维视图

#### 运维治理项

- 入库记录存在但源文件缺失
- run 存在但 artifact 缺失
- 当前版本指针异常
- 同名结果冲突
- 缓存与主文件不一致
- 已发布结果未完成审核

#### 验收标准

- 同一对影像允许保留多引擎结果
- 同一任务允许保留多版本
- 结果详情页能追踪到来源 run 和主产物列表
- 运维面板可识别结果资产层异常

## 8. 实施顺序

### Phase 1

- 完成多引擎与结果管理设计冻结
- 完成接口契约
- 完成数据模型草案

### Phase 2

- 实现后端抽象层
- 完成 SARSCAPE 兼容接入
- 保持旧接口兼容

### Phase 3

- 实现新前端生产中心
- 实现引擎状态、运行监控和运维自检增强

### Phase 4

- 接入 ISCE2 + WSL
- 打通首条正式生产链

### Phase 5

- 实现结果管理增强
- 补齐版本、来源、治理与自愈

## 9. 交付顺序建议

建议按照以下顺序落地：

1. 先重构生产抽象层，不先碰最终结果治理细节
2. 先让 `sarscape` 在新体系下跑通
3. 再接入 `isce2`
4. 最后再把结果管理全面升级

这样可以避免在“生产链尚未稳定”时提前锁死结果模型细节。

## 10. 当前结论

本轮不应被理解为“给当前 ENVI 面板再加一个 ISCE2 按钮”，而应被理解为：

- D-InSAR 生产系统平台化
- D-InSAR 结果资产化
- 运维自检从系统健康升级为多引擎专项健康

后续所有开发任务、联调任务和运维验收，均以本 Task 文档为总入口。
