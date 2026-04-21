# PyINT + Gamma 实施清单

更新日期：2026-04-18

关联设计文档：

- [PYINT_GAMMA_INTEGRATION_DESIGN_20260418.md](PYINT_GAMMA_INTEGRATION_DESIGN_20260418.md)

## 当前落地进度

- [x] 已完成第一批 `PyINT` 生产引擎接入：配置、引擎注册、任务队列、WSL 包装脚本、生产面板入口。
- [x] 已完成 `PyINT` 运行目录规范化输出：生成 `.dinsar_run.json` 与 `pyint_run_summary.json`。
- [x] 已完成 `PyINT` 基础环境检查：WSL、Python、`PYINT_HOME`、`pyintApp.py`、Gamma 命令可达性。
- [x] 已将 `PyINT` 代码收编到仓库内 `third_party/PyINT`，不再依赖默认外部绝对路径。
- [ ] 尚未完成 `PyINT` 结果目录自动发布兼容。当前原生输出已保存，但现有结果 catalog 仍主要面向 ENVI / ISCE2 栅格产物。
- [ ] 尚未开始 `Gamma` 精配对后端与前端集成。

## 1. 文档定位

这份清单用于把 `PyINT` 生产引擎接入和 `Gamma` 精配对接入拆成可执行任务，作为后续实施顺序、联调顺序和验收顺序的统一依据。

本清单按以下原则编排：

- 一期优先打通 `PyINT` 生产引擎
- 二期再做 `Gamma` 精配对 MVP
- 一期不强制改数据库主结构
- 运维自检只加状态，不把主要操作堆回健康页

## 2. 实施总顺序

推荐顺序：

1. 先确认环境基线和配置项
2. 先打通 `PyINT` 后端引擎与任务执行
3. 再补结果归一化与目录扫描兼容
4. 再补前端生产入口
5. 然后做 `Gamma` 精配对 MVP
6. 最后补健康检查、烟测和治理

不建议顺序：

- 先改数据库再写主流程
- 先做健康页大改
- 先把 `PyINT` 全部高级参数暴露到前端

## 3. Phase 0：环境基线确认

目标：

- 确认当前机器上的 `PyINT + Gamma + WSL` 具备最小可执行条件
- 把配置字段定清楚，但不把敏感信息写入仓库文档

### 任务

- [ ] 确认 `D:\Code\PyINT` 的实际可执行入口路径
- [ ] 确认当前唯一 WSL distro 名称，默认与 `ISCE2_WSL_DISTRO` 对齐
- [ ] 确认 WSL 中 `PyINT` 可用 Python 路径
- [ ] 确认 `GAMMA_ENV_SCRIPT` 的实际路径
- [ ] 确认 `base_calc` 在 WSL 中可执行
- [ ] 确认 `pyintApp.py` 在 WSL 中可执行
- [ ] 确认 `SCRATCHDIR` / `TEMPLATEDIR` / `DEMDIR` 对应的系统托管目录方案
- [ ] 确认 PyINT 一期只支持的业务范围，建议锁定 `LT-1 + Gamma D-InSAR`

### 涉及文件

- [ ] `backend/app/config.py`
- [ ] `.env.example`
- [ ] 根 `.env` 本机配置对照，不入库敏感值

### 阶段验收

- [ ] 可以给出完整的 PyINT 运行必需配置字段列表
- [ ] 可以在 WSL 内成功跑通最小烟测命令
- [ ] 不需要把管理员密码、邮箱密码、sudo 密码写入代码或文档

## 4. Phase 1：PyINT 后端引擎接入

目标：

- 把 `PyINT` 作为新的 D-InSAR 引擎正式接入现有多引擎体系

### 4.1 配置层

- [ ] 在 `backend/app/config.py` 新增 `PYINT_*` 配置
- [ ] 增加默认继承逻辑：`PYINT_WSL_DISTRO` 默认跟随 `ISCE2_WSL_DISTRO`
- [ ] 增加默认继承逻辑：`PYINT_WSL_PYTHON` 默认跟随 `ISCE2_PYTHON`
- [ ] 增加系统托管目录默认值：
  - [ ] `PYINT_TEMPLATE_ROOT`
  - [ ] `PYINT_WORK_ROOT`
  - [ ] `PYINT_OUTPUT_ROOT`
- [ ] 在 `validate_runtime_config()` 中加入 PyINT 基础校验
- [ ] 在 `.env.example` 中补齐非敏感 PyINT 配置示例

### 4.2 服务层

- [ ] 新增 `backend/app/services/pyint_service.py`
- [ ] 封装 WSL 执行逻辑，复用现有 `wsl_service.py`
- [ ] 实现 Windows 路径到 WSL 路径转换
- [ ] 实现 PyINT 工作区目录初始化
- [ ] 实现模板文件生成
- [ ] 实现运行参数到模板字段的映射
- [ ] 实现运行摘要 JSON 输出
- [ ] 实现 PyINT 烟测函数

### 4.3 引擎层

- [ ] 新增 `backend/app/dinsar_engines/pyint_engine.py`
- [ ] 实现 `DinsarEngine` 接口
- [ ] 定义 `engine_code=pyint`
- [ ] 定义一期唯一 profile，建议为 `lt1_gamma_dinsar`
- [ ] 定义最小参数 schema，避免一开始暴露过多 PyINT 原生参数
- [ ] 实现 `check_available()`
- [ ] 实现 `run()`

### 4.4 注册与任务调度

- [ ] 在 `backend/app/dinsar_engines/registry.py` 注册 `PyINT`
- [ ] 在 `backend/app/services/job_handlers.py` 增加 `JOB_TYPE_PYINT_RUN`
- [ ] 新增对应 handler
- [ ] 在 `backend/app/routers/dinsar_production.py` 允许 `engine_code=pyint`
- [ ] 让生产提交逻辑按 `pyint` 分派到新 job type

### 4.5 WSL 包装脚本

- [ ] 新增 `backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py`
- [ ] 负责把系统任务目录映射为 PyINT 项目目录
- [ ] 负责设置 `SCRATCHDIR` / `TEMPLATEDIR` / `DEMDIR`
- [ ] 负责调用 `pyintApp.py` 或必要的细粒度 PyINT 脚本
- [ ] 负责收集输出路径和运行摘要

### 涉及文件

- [ ] `backend/app/config.py`
- [ ] `backend/app/dinsar_engines/registry.py`
- [ ] `backend/app/dinsar_engines/pyint_engine.py`
- [ ] `backend/app/services/pyint_service.py`
- [ ] `backend/app/services/job_handlers.py`
- [ ] `backend/app/routers/dinsar_production.py`
- [ ] `backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py`
- [ ] `.env.example`

### 阶段验收

- [ ] `/dinsar-production/engines` 能返回 `pyint`
- [ ] `PyINT` 引擎可在后端被识别为可用/不可用
- [ ] 可以成功提交一个 `pyint` 生产任务到队列
- [ ] 任务日志、任务状态、错误信息可通过现有任务体系查看

## 5. Phase 2：PyINT 结果归一化与结果治理兼容

目标：

- 保证 `PyINT` 输出能进入现有结果扫描、发布和 catalog 体系

### 任务

- [ ] 定义 PyINT 结果工作区与正式输出区的边界
- [ ] 统一输出 bundle 元数据格式
- [ ] 输出 engine/profile/run_key/task_name/pair trace 元数据
- [ ] 补齐 pair 相关元数据：
  - [ ] `pair_uid`
  - [ ] `network_run_id`
  - [ ] `network_edge_id`
  - [ ] `policy_version`
- [ ] 让现有 `dinsar_scan_service` 可以识别 PyINT 结果
- [ ] 验证现有 `result_catalog_service` 可处理 PyINT 产物
- [ ] 验证桥接一致性逻辑不会把 PyINT 结果识别坏

### 涉及文件

- [ ] `backend/app/services/pyint_service.py`
- [ ] `backend/app/services/dinsar_scan_service.py`
- [ ] `backend/app/services/result_catalog_service.py`
- [ ] 可能涉及现有结果元数据写入辅助模块

### 阶段验收

- [ ] 跑完 PyINT 后可被系统扫描到
- [ ] 可进入结果目录索引
- [ ] 不影响现有 SARscape/ISCE2 结果扫描

## 6. Phase 3：前端生产页接入 PyINT

目标：

- 在现有生产页中把 `PyINT` 作为正式引擎展示和提交

### 任务

- [ ] 在 `frontend/src/DinsarProductionPanel.jsx` 中显示 `PyINT` 引擎卡片
- [ ] 补充 `ENGINE_LABEL` / `TASK_TYPE_LABEL`
- [ ] 根据 `PyINT` profile 渲染参数输入项
- [ ] 对不可用状态显示明确原因
- [ ] 提交成功后沿用现有任务监控
- [ ] 验证运行列表中能正确显示 `pyint`

### 涉及文件

- [ ] `frontend/src/DinsarProductionPanel.jsx`
- [ ] `frontend/src/api/dinsarProduction.js`
- [ ] `frontend/src/utils/dinsarEngines.js` 如需要

### 阶段验收

- [ ] 前端可看到 `PyINT`
- [ ] 可提交 `PyINT` 任务
- [ ] 可看到任务状态和日志
- [ ] 不影响现有 SARscape/ISCE2 提交

## 7. Phase 4：Gamma 精配对 MVP

目标：

- 在现有配对规划体系上实现一版可用的 `Gamma` 精配对

### 7.1 后端能力

- [ ] 新增 `backend/app/services/pairing_refinement_service.py`
- [ ] 新增 `backend/app/services/gamma_pairing_service.py` 或同等职责模块
- [ ] 实现基于现有 `network_run_id` 的场景集提取
- [ ] 实现 PyINT/Gamma 配对工作区构建
- [ ] 调用 `select_pairs.py` / `base_calc`
- [ ] 解析 `ifgram_list.txt` / baseline 输出
- [ ] 生成新的 refined `network_run_id`
- [ ] 将精配对结果写入：
  - [ ] `pairing_network_runs`
  - [ ] `pairing_network_edges`
  - [ ] `selection_meta_json`

### 7.2 接口层

- [ ] 在 `backend/app/routers/pairing.py` 增加 `POST /pairing/refine-gamma`
- [ ] 设计请求体和响应体
- [ ] 设计运行告警返回字段
- [ ] 如需要，增加 refined artifacts 查询接口

### 7.3 存储策略

- [ ] 明确一期不改 `pairing_metric_cache` 语义
- [ ] 明确只在 run/edge JSON 中落精配对元数据
- [ ] 保留粗配对网络和精配对网络双轨并存

### 涉及文件

- [ ] `backend/app/routers/pairing.py`
- [ ] `backend/app/services/spatial_service.py` 如需复用
- [ ] `backend/app/services/pairing_refinement_service.py`
- [ ] `backend/app/services/gamma_pairing_service.py`
- [ ] `backend/app/models/schemas.py`

### 阶段验收

- [ ] 可基于一个已有 `network_run_id` 发起精配对
- [ ] 返回新的 refined `network_run_id`
- [ ] 精配对结果可通过现有 network 查询接口查看
- [ ] 不破坏原粗配对结果

## 8. Phase 5：前端配对规划页接入 Gamma 精配对

目标：

- 在配对规划页提供精配对入口和结果摘要

### 任务

- [ ] 在 `frontend/src/panels/PairPlanningPanel.jsx` 新增 `Gamma 精配对` 区块
- [ ] 展示当前粗配对网络摘要
- [ ] 增加发起精配对按钮
- [ ] 展示精配对结果摘要
- [ ] 展示粗配对与精配对差异提示
- [ ] 增加“采用哪一版网络继续生产”的状态表达

### 涉及文件

- [ ] `frontend/src/panels/PairPlanningPanel.jsx`
- [ ] `frontend/src/api/pairing.js`

### 阶段验收

- [ ] 管理员可在配对规划页发起精配对
- [ ] 能看到 refined 结果摘要
- [ ] 不需要进入健康检查页做配对操作

## 9. Phase 6：运维自检与烟测补齐

目标：

- 让 PyINT/Gamma 的环境状态可被健康检查观察

### 任务

- [ ] 在 `backend/app/services/health_service.py` 中纳入 PyINT 检查
- [ ] 检查项至少包括：
  - [ ] `PYINT_ENABLED`
  - [ ] distro 可访问
  - [ ] WSL Python 可执行
  - [ ] `pyintApp.py` 存在
  - [ ] `GAMMA_ENV_SCRIPT` 存在
  - [ ] `base_calc` 可执行
  - [ ] 模板目录可读
  - [ ] 工作目录可写
- [ ] 增加管理员烟测接口
- [ ] 前端健康页只展示状态摘要，不加复杂操作区

### 涉及文件

- [ ] `backend/app/services/health_service.py`
- [ ] `backend/app/routers/dinsar_production.py`
- [ ] `frontend/src/HealthCheckPanel.jsx`

### 阶段验收

- [ ] 健康页可看到 PyINT 状态
- [ ] 可区分“引擎不可用”和“系统整体故障”
- [ ] 不把精配对主操作入口放回健康页

## 10. Phase 7：可选数据库结构化增强

目标：

- 只有在业务确认需要更强的历史与运维管理时才进入本阶段

### 进入条件

- [ ] 需要独立查询精配对历史
- [ ] 需要统计精配对失败率
- [ ] 需要管理精配对 artifacts 生命周期
- [ ] 需要构建更完整的后台管理页

### 任务

- [ ] 设计 `pairing_refinement_runs` 等新表
- [ ] 新增迁移文件，例如 `007_pyint_gamma_integration.sql`
- [ ] 在 `backend/app/db_maintenance.py` 中加入迁移列表
- [ ] 验证 `ensure_database_ready()` 启动自动迁移
- [ ] 验证幂等执行

### 阶段验收

- [ ] 新表结构不破坏现有 pairing 逻辑
- [ ] 启动时可自动应用迁移
- [ ] 老数据和老接口保持兼容

## 11. 联调与验收矩阵

### 后端

- [ ] `py_compile` 或等价语法检查通过
- [ ] 新增路由可正常注册
- [ ] 新增引擎可正常列出
- [ ] 任务队列能执行 `PyINT`
- [ ] 精配对接口能生成 refined network

### 前端

- [ ] `npm run build` 通过
- [ ] 生产页能显示 `PyINT`
- [ ] 配对规划页能显示 `Gamma 精配对`
- [ ] 健康页能显示 PyINT 状态

### 集成

- [ ] `PyINT` 单任务最小链路跑通
- [ ] 结果能被系统扫描
- [ ] 精配对 MVP 跑通
- [ ] 现有 SARscape/ISCE2 不回归

## 12. 当前明确不做

- [ ] 一期不把 PyINT 的全部模板参数开放到前端
- [ ] 一期不接入 GACOS 自动邮箱下载链路
- [ ] 一期不接入 POT、phase bias、完整时序 MintPy 流程
- [ ] 一期不改写现有 `pairing_metric_cache` 字段语义
- [ ] 一期不在健康检查页增加主操作面板
- [ ] 一期不做多 WSL distro 管理

## 13. 当前建议的首批落地包

建议第一轮直接落以下内容：

- [ ] `config.py` + `.env.example` 的 `PYINT_*` 配置
- [ ] `pyint_service.py`
- [ ] `pyint_engine.py`
- [ ] `registry.py` 注册
- [ ] `job_handlers.py` 的 `JOB_TYPE_PYINT_RUN`
- [ ] `dinsar_production.py` 的 `pyint` 提交分派
- [ ] `run_lt1_pyint_pipeline.py`
- [ ] `DinsarProductionPanel.jsx` 的 `PyINT` 引擎展示与提交

这批完成后，再进入 `Gamma` 精配对 MVP。
