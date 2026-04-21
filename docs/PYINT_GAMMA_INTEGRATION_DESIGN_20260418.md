# PyINT + Gamma 集成总体设计

**日期**: 2026-04-18  
**状态**: 总体设计  
**范围**: D-InSAR 生产引擎接入、Gamma 精配对接入、配置管理、数据库策略、运维自检、前端入口

## 1. 结论

本次集成建议采用两条并行但相互衔接的路线：

1. 将 `PyINT` 作为新的 D-InSAR 生产引擎接入现有多引擎框架，统一走现有任务队列、运行日志、结果登记与结果目录治理链路。
2. 将 `Gamma` 配对能力接入现有“配对基础 -> 配对规划 -> 生产执行”链路，作为数据库粗配对结果之上的精化步骤，而不是替换当前配对基础缓存。

核心判断如下：

- `PyINT` 更适合作为“受控外部引擎”集成，而不是直接作为后端内部 Python 库深度嵌入。
- `Gamma` 精配对是可集成的，但更适合针对“已经筛出的场景集合/网络运行”做二次优化，不适合直接取代当前全库候选对缓存。
- 一期集成建议不强制修改数据库主结构；优先复用现有 `pairing_network_runs` / `pairing_network_edges` 的 JSON 承载精配对元数据。
- 如果二期需要对 Gamma 精配对历史做独立检索、统计和运维闭环，再引入单独迁移文件，并通过现有数据库自维护机制自动落库。

## 2. 现状与约束

### 2.1 当前系统已有基础

- 已有 D-InSAR 多引擎抽象：`backend/app/dinsar_engines/base.py`
- 已有引擎注册表：`backend/app/dinsar_engines/registry.py`
- 已有生产任务接口与队列：`backend/app/routers/dinsar_production.py`
- 已有配对基础缓存、网络运行与边追踪：
  - `backend/app/models/orm.py`
  - `backend/app/services/pairing_cache_service.py`
  - `backend/app/services/pairing_state_service.py`
  - `backend/app/services/spatial_service.py`
- 已有数据库自维护与 SQL 迁移自动执行：`backend/app/db_maintenance.py`
- 已有运维自检面板与健康检查汇总：`backend/app/services/health_service.py`、`frontend/src/HealthCheckPanel.jsx`
- 已有生产页与配对规划页：
  - `frontend/src/DinsarProductionPanel.jsx`
  - `frontend/src/panels/PairPlanningPanel.jsx`

### 2.2 PyINT 项目特征

从 `D:\Code\PyINT` 现状看，`PyINT` 不是干净的 SDK，而是以模板和脚本为中心的流程编排层：

- 主入口为 `pyint/pyintApp.py`
- 配对能力入口为 `pyint/select_pairs.py`
- 严重依赖环境变量：
  - `SCRATCHDIR`
  - `TEMPLATEDIR`
  - `DEMDIR`
- 运行方式偏 Linux / WSL，广泛调用外部命令与 GAMMA CLI
- 更适合作为“流程执行器”被调用，而不是被后端直接 import 后逐步复用内部函数

### 2.3 明确约束

- 本机只有一个 WSL 环境，不需要设计多 distro 调度系统。
- 系统级 Windows Python 解释器已经在根 `.env` 中维护，可复用，不应再为 PyINT 额外复制一套 Windows Python 配置。
- 管理员口令不应进入设计文档、代码或 `.env.example`。权限控制继续复用现有登录态与管理员角色校验。
- 现有运维自检面板已经较重，PyINT/Gamma 的“操作入口”不应继续堆在健康检查页里。

## 3. 总体集成架构

### 3.1 总体原则

- 不新建平行子系统，优先复用现有引擎、作业、配对、结果目录与目录扫描体系。
- 不改变现有 `pairing_metric_cache.spatial_baseline_meters` 的语义。
- 不把 Gamma 精配对结果直接覆盖数据库粗配对缓存。
- 运维页只看状态，实际操作放在生产页与配对规划页。

### 3.2 架构分层

#### A. 生产引擎层

新增 `pyint` 引擎，挂到现有 `registry` 中，与 `sarscape` / `isce2` / `landsar` 并列。

#### B. WSL 执行适配层

新增受控执行服务，负责：

- 读取 `.env` 配置
- 复用现有 WSL 命令执行与路径转换能力
- 组装 `PyINT` 所需环境变量
- 生成模板文件和运行目录
- 执行 `PyINT` 包装脚本
- 将输出归一化到系统现有结果结构

#### C. 配对精化层

保留现有数据库候选对缓存与网络运行。

在此基础上新增“Gamma 精配对”步骤：

1. 先由当前配对接口生成候选网络
2. 再将该网络对应场景集送入 Gamma / PyINT 配对流程
3. 生成新的精化网络结果
4. 前端允许用户查看并选择使用精化后的网络结果

#### D. 结果治理层

PyINT/Gamma 原始工作目录不直接作为系统正式结果。

必须经过适配层输出统一结果包，保证继续兼容：

- 结果目录扫描
- 结果目录发布
- 结果目录桥接一致性
- 预览图/缩略图生成
- AI 诊断与 catalog 追踪

## 4. PyINT 生产引擎设计

### 4.1 目标

目标不是把 `PyINT` 原封不动暴露给用户，而是把它包装成当前系统理解的“一个可选生产引擎”。

### 4.2 推荐实现方式

新增以下后端组件：

- `backend/app/dinsar_engines/pyint_engine.py`
- `backend/app/services/pyint_service.py`
- `backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py`

职责划分：

- `pyint_engine.py`
  - 实现 `DinsarEngine`
  - 暴露 `engine_code=pyint`
  - 提供可用性检查、处理 profile、参数 schema
- `pyint_service.py`
  - WSL 执行
  - 路径转换
  - 模板写入
  - 环境变量组装
  - 烟测检查
- `run_lt1_pyint_pipeline.py`
  - 作为受控包装脚本在 WSL 中运行
  - 负责把系统已有 `Task_*` / 配对任务目录映射成 PyINT 项目工作区
  - 调用 `pyintApp.py` 或更细粒度子脚本
  - 收集输出并生成系统结果清单

### 4.3 与当前生产链路的关系

沿用现有生产链路：

`前端生产页 -> /dinsar-production/run -> job queue -> pyint_engine.run() -> WSL -> PyINT -> 统一结果包 -> catalog/scan`

这样做的收益：

- 不需要新增独立任务中心
- 不需要新增另一套运行日志
- 不需要新增另一套前端生产入口
- 与当前 `DinsarProductionPanel.jsx` 的多引擎 UI 完全兼容

### 4.4 输入与工作区组织

建议一期仍以当前系统已有的任务目录为输入，不要求用户先手工构造原生 PyINT 项目。

推荐工作区结构：

- 系统输入根目录：沿用当前生产面板 `root_dir`
- PyINT 工作根目录：系统管理目录，例如 `backend/runtime/pyint_work`
- 模板目录：系统管理目录，例如 `backend/runtime/pyint_templates`
- 每次运行独立 `run_key`
- 每个 pair/task 独立 workspace，避免相互污染

### 4.5 结果输出策略

PyINT 原始输出不能直接作为系统正式结果目录暴露。

推荐新增“输出归一化”步骤，将 PyINT/Gamma 输出转为系统现有 bundle 约定，至少包含：

- 结果主清单
- 关键输出文件路径
- 运行元数据
- pair trace 信息
- engine/profile 信息

必须保证与现有结果目录扫描机制兼容。

### 4.6 Profile 设计建议

一期建议只开放一个稳定 profile：

- `lt1_gamma_dinsar`

不建议一开始把 `PyINT` 全部开关都暴露到前端。应只暴露对当前业务必要的参数，例如：

- 是否强制重跑
- 多视参数
- 相干阈值
- geocode 开关
- unwrap 开关
- 超时

其余细节由模板生成器按系统默认值填充。

## 5. Gamma 精配对设计

### 5.1 目标定位

Gamma 精配对不替代当前数据库候选对缓存，而是建立在现有候选网络之上的二次精化机制。

推荐定位为：

- 当前数据库配对：全库级、粗筛级、可快速响应
- Gamma 精配对：项目级、网络级、精筛级、可生成更可靠的时空基线网络

### 5.2 推荐流程

1. 用户在现有配对规划页完成粗配对查询
2. 后端返回 `network_run_id`
3. 用户在“Gamma 精配对”区域发起精化
4. 系统根据该网络运行对应的场景集合，构建 PyINT/Gamma 工作区
5. 调用 `select_pairs.py` / `base_calc` 生成精配对网络
6. 后端将结果落回系统网络结果表示
7. 前端展示“粗配对结果”和“Gamma 精配对结果”的对比摘要
8. 用户选择使用哪一版网络继续生产

### 5.3 为什么不能直接覆盖当前 pairing cache

当前 `pairing_metric_cache` 里的 `spatial_baseline_meters` 已经在系统内承担既有语义与下游用途。  
Gamma 计算出的垂直基线/网络属性与当前字段不等价，直接覆盖会带来语义混乱和回归风险。

因此必须坚持：

- 现有缓存保留原语义
- Gamma 精配对结果单独存储
- 精配对结果仅作为网络选择依据，不回写粗配对主缓存

### 5.4 一期存储策略

一期推荐不新建强结构化表，优先复用：

- `pairing_network_runs.request_params_json`
- `pairing_network_edges.selection_meta_json`

建议约定写入内容：

- `refinement_engine: gamma_pyint`
- `refinement_source_run_id`
- `gamma_bperp_m`
- `gamma_tbase_days`
- `gamma_rank`
- `gamma_ifgram_list_path`
- `gamma_artifact_dir`
- `gamma_selection_reason`

同时新增一个新的 `network_run_id`，把“精配对结果”作为新的网络运行保存，而不是修改原粗配对运行。

这样做的收益：

- 一期可不改数据库结构
- 保留粗配对和精配对双轨结果，便于审计与回退
- 复用现有 network run / edge 追踪模型

### 5.5 二期可选扩展

如果后续有以下需求，再引入数据库迁移：

- 精配对历史独立检索
- 精配对任务状态长期统计
- 精配对工作区清理与资产追踪
- 精配对失败类型聚合运维

二期建议新增表，例如：

- `pairing_refinement_runs`
- `pairing_refinement_artifacts`

但这不是一期必须项。

## 6. 配置与运行管理方案

### 6.1 配置原则

- Windows 侧解释器继续复用根 `.env` 中已有的 `PYTHON_PATH`
- WSL 侧只维护 PyINT/Gamma 运行必须配置
- 因为本机只有一个 WSL 环境，`PyINT` 与 `ISCE2` 默认共用 distro

### 6.2 建议新增配置项

建议在 `.env` / `.env.example` / `backend/app/config.py` 中新增：

```ini
PYINT_ENABLED=false
PYINT_WSL_DISTRO=
PYINT_WSL_PYTHON=
PYINT_HOME=
PYINT_APP_SCRIPT=
PYINT_TEMPLATE_ROOT=
PYINT_WORK_ROOT=
PYINT_OUTPUT_ROOT=
PYINT_DEM_ROOT=
PYINT_GAMMA_ENV_SCRIPT=
PYINT_DEFAULT_TIMEOUT_SECONDS=43200
PYINT_SMOKE_TEST_ENABLED=false

PAIRING_GAMMA_ENABLED=false
PAIRING_GAMMA_WORK_ROOT=
PAIRING_GAMMA_TEMPLATE_ROOT=
PAIRING_GAMMA_TIMEOUT_SECONDS=7200
```

默认策略建议：

- `PYINT_WSL_DISTRO` 为空时，默认取 `ISCE2_WSL_DISTRO`
- `PYINT_WSL_PYTHON` 为空时，默认取 `ISCE2_PYTHON`
- `PYINT_APP_SCRIPT` 指向 `pyintApp.py`
- `PYINT_WORK_ROOT` / `PAIRING_GAMMA_WORK_ROOT` 使用系统托管目录，不直接让用户任意指定

### 6.3 不建议写入设计或配置的内容

- 管理员明文密码
- ASF/GACOS 邮箱密码
- WSL sudo 密码

这些信息如确需使用，也应通过运行时安全注入或机器本地安全配置处理，不写入仓库文档。

### 6.4 管理与治理策略

建议增加以下治理规则：

- 所有 PyINT/Gamma 工作目录按 `run_key` 或 `network_run_id` 分目录
- 所有运行都必须写运行摘要 JSON
- 所有正式产物必须进入统一结果发布目录
- 中间工作区可按保留策略定期清理
- 清理动作仅允许管理员执行

## 7. 数据库与数据库自维护策略

### 7.1 一期结论

一期建议：

- `PyINT` 生产引擎接入不强制改库
- `Gamma` 精配对接入不强制改库
- 优先复用现有 run/edge JSON 元数据承载扩展信息

### 7.2 二期改库触发条件

当满足以下任意条件时，再进入改库：

- 需要独立查询 Gamma 精配对运行历史
- 需要单独统计 Gamma 精配对失败率
- 需要把精配对资产纳入长期运维对象
- 需要做更细粒度的后台管理界面

### 7.3 改库时的落地方式

如果二期改库，必须沿用现有数据库自维护机制：

1. 在 `backend/migrations/` 新增 SQL 迁移文件，例如 `007_pyint_gamma_integration.sql`
2. 在 `backend/app/db_maintenance.py` 的 `MIGRATION_FILES` 中追加文件名
3. 由 `ensure_database_ready()` 在启动时自动执行迁移

约束：

- 不修改既有字段语义
- 不破坏现有 `pairing_metric_cache` / `pairing_network_*` 查询逻辑
- 迁移必须支持重复执行幂等

## 8. 运维自检与健康检查设计

### 8.1 设计原则

运维自检页继续只做“状态观察”，不做主操作入口。

PyINT/Gamma 的正式操作入口放在：

- 生产页
- 配对规划页

### 8.2 健康检查应新增的内容

建议在引擎可用性检查中加入 PyINT 项：

- `PYINT_ENABLED`
- WSL distro 可访问
- WSL Python 可执行
- `PYINT_HOME` 存在
- `pyintApp.py` 存在
- `GAMMA_ENV_SCRIPT` 可 source
- 关键命令如 `base_calc` 可执行
- 模板目录可读
- 工作目录可写
- DEM 根目录可读

### 8.3 健康页展示策略

不建议在 `HealthCheckPanel.jsx` 再新增一大块复杂操作区。

建议只保留两类展示：

1. 在现有 `D-InSAR 引擎` 卡片中自然显示 `PyINT`
2. 在健康详情或备注中显示 PyINT/Gamma 的简要检查摘要

不建议：

- 在健康页提供精配对执行按钮
- 在健康页提供模板编辑入口
- 在健康页堆叠大量结果目录说明

### 8.4 运维修复入口位置

- 引擎级问题：在生产页提示不可用原因
- 配对级问题：在配对规划页处理
- 只有“环境诊断/烟测”可以保留在运维页

## 9. 后端接口设计

### 9.1 生产接口

现有 `/dinsar-production/engines` 和 `/dinsar-production/run` 可继续复用。

需要做的只是：

- 在引擎注册表中加入 `pyint`
- `list_engines()` 自动返回 PyINT
- `submit_run()` 允许 `engine_code=pyint`

### 9.2 配对接口

建议新增以下接口：

- `POST /pairing/refine-gamma`
  - 输入：`network_run_id` 或明确场景列表
  - 输出：新的精配对 `network_run_id`、摘要、警告、产物位置
- `GET /pairing/networks/{network_run_id}`
  - 继续复用现有接口查看粗配对/精配对网络详情
- 可选：`GET /pairing/refine-gamma/{network_run_id}/artifacts`
  - 用于查看 artifact 摘要，不建议一期必做

### 9.3 管理接口

建议增加一个轻量管理接口用于 PyINT/Gamma 环境烟测，例如：

- `POST /dinsar-production/engines/pyint/smoke-check`

用途仅限管理员环境校验，不参与正式生产提交。

## 10. 前端入口与交互布局

### 10.1 生产页

位置：`frontend/src/DinsarProductionPanel.jsx`

建议改动：

- 新增 `PyINT` 引擎卡片
- 显示 PyINT 可用性状态
- 根据 profile 展示少量必要参数
- 保留当前“根目录 + 参数 + 提交任务”交互，不新造独立页面

### 10.2 配对规划页

位置：`frontend/src/panels/PairPlanningPanel.jsx`

建议新增一个独立区域：

- 标题：`Gamma 精配对`
- 放置位置：`配对基础` 卡片下方，`结果与刷新` 卡片上方

该区域建议包含：

- 粗配对网络摘要
- 发起 Gamma 精配对按钮
- 精配对结果摘要
- 粗配对 / 精配对差异提示
- 选择采用哪一版网络继续生产

不建议把精配对塞进现有健康检查页。

### 10.3 健康检查页

位置：`frontend/src/HealthCheckPanel.jsx`

建议只做最小改动：

- 让 `D-InSAR 引擎` 卡片中自动出现 `PyINT`
- 如需要，增加一条 PyINT/Gamma 环境说明

不增加复杂控制区，避免界面继续变重。

## 11. 涉及改动位置

### 11.1 后端

- `backend/app/dinsar_engines/registry.py`
- `backend/app/dinsar_engines/pyint_engine.py` 新增
- `backend/app/services/pyint_service.py` 新增
- `backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py` 新增
- `backend/app/routers/dinsar_production.py`
- `backend/app/routers/pairing.py`
- `backend/app/services/health_service.py`
- `backend/app/config.py`
- `.env.example`

### 11.2 前端

- `frontend/src/DinsarProductionPanel.jsx`
- `frontend/src/panels/PairPlanningPanel.jsx`
- `frontend/src/HealthCheckPanel.jsx`
- `frontend/src/api/dinsarProduction.js`
- `frontend/src/api/pairing.js`

### 11.3 数据库

一期可不改。

二期若改，涉及：

- `backend/migrations/007_pyint_gamma_integration.sql` 新增
- `backend/app/db_maintenance.py`

## 12. 分阶段实施建议

### Phase 1: PyINT 引擎接入

- 新增 `pyint_engine`
- 完成 WSL 可用性检查
- 完成模板生成与工作目录治理
- 完成生产页引擎选择
- 完成结果归一化与目录扫描兼容

### Phase 2: Gamma 精配对 MVP

- 新增 `/pairing/refine-gamma`
- 基于现有 `network_run_id` 做精化
- 精配对结果复用现有 network run / edge 模型表达
- 前端在配对规划页增加精配对区块

### Phase 3: 运维与治理补齐

- 增加烟测接口
- 增加工作区清理策略
- 增加 artifact 摘要与失败类型归档

### Phase 4: 二期结构化增强

- 若业务确认需要，再加数据库迁移
- 把精配对历史与资产纳入更细粒度可检索对象

## 13. 风险与规避

### 13.1 PyINT 代码稳定性

风险：

- 模板字段和脚本依赖较多
- 对目录命名和环境变量较敏感

规避：

- 不做深度 import 复用
- 使用受控包装脚本
- 限制一期只开放一个稳定 profile

### 13.2 WSL 与路径问题

风险：

- Windows 路径和 WSL 路径混用
- 工作区权限与可写性问题

规避：

- 所有路径统一通过适配层转换
- 工作目录和模板目录由系统托管

### 13.3 配对语义污染

风险：

- 把 Gamma 垂直基线直接混写进现有粗配对缓存字段

规避：

- 明确不覆盖 `pairing_metric_cache` 语义
- 精配对结果单独落在网络运行元数据中

### 13.4 前端继续膨胀

风险：

- 把运维、配对、生产操作继续堆到健康检查页

规避：

- 健康页只显示状态
- 生产操作只放生产页
- 配对操作只放配对规划页

## 14. 最终建议

建议按以下判断执行：

- `PyINT` 生产引擎接入：必要，且应尽快按现有多引擎架构落地。
- `Gamma` 精配对接入：可行，但应作为“粗配对之后的精化层”落地。
- 数据库：一期不强制改库；二期若需更强管理能力，再走数据库自维护迁移。
- 运维自检：只加状态，不加大块操作区。
- 前端入口：生产页接 `PyINT`，配对规划页接 `Gamma 精配对`。

