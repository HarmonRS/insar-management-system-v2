# 当前文档总览

这是 `docs/` 目录的统一入口文档。

从现在开始，`docs/` 根目录只保留“当前仍在使用”的文档；历史过程材料、已完成实施记录、被新文档覆盖的旧方案，统一放入 `docs/archive/`。

## 1. 当前有效文档

### 核心使用

- **[README.md](../README.md)**  
  系统主文档。优先阅读，包含整体功能、架构、快速开始和主要配置说明。
- **[DEPLOYMENT.md](DEPLOYMENT.md)**  
  部署与运维细节文档。适合正式部署、数据库初始化、服务启动和环境核对时使用。
- **[PROJ_CONFIGURATION.md](PROJ_CONFIGURATION.md)**  
  PROJ/GDAL 相关专项配置说明。只有在坐标转换或 PROJ 冲突时才需要单独查看。

### 当前安全状态

- **[SECURITY_AUDIT_2026-03-12.md](SECURITY_AUDIT_2026-03-12.md)**  
  当前有效的安全审计结论。已经把“开发机阶段可接受暴露”和“上线前必须修复的问题”分开说明，后续安全判断以这份为准。

### 仍在推进的设计与任务

- **[DINSAR_ENHANCEMENT_TASKS.md](DINSAR_ENHANCEMENT_TASKS.md)**  
  D-InSAR 多引擎生产增强与结果管理增强的总 Task 文档。覆盖前端架构、ENVI 六步链路、ISCE2 的 WSL 校验、运维自检与结果治理。
- **[PAIRING_ENHANCEMENT_DESIGN.md](PAIRING_ENHANCEMENT_DESIGN.md)**  
  配对能力增强设计。属于未完全落地的专项设计文档。
- **[GAMMA_WSL2_INTEGRATION_PLAN.md](GAMMA_WSL2_INTEGRATION_PLAN.md)**  
  GAMMA + WSL2 双引擎方案。属于后续扩展规划，不是当前默认运行链路。
- **[WSL2_ISCE2_MINTPY_SBAS_INTEGRATION_PLAN_20260412.md](WSL2_ISCE2_MINTPY_SBAS_INTEGRATION_PLAN_20260412.md)**  
  基于本机 `Ubuntu-24.04` WSL2、`isce2` / `mintpy` / `isce2_mintpy_v1` 实际环境核对后的 SBAS 集成落地方案，明确推荐运行时、workflow 补全顺序和正式产品边界。
- **[ISCE2_SBAS_TIMESERIES_DESIGN.md](ISCE2_SBAS_TIMESERIES_DESIGN.md)**  
  ISCE2 框架下 SBAS / 时序生产设计稿，说明如何从现有 `PS stack batch` 扩展到 `workflow + psinsar catalog` 的正式生产链路。
- **[SBAS_PROGRESS_STATUS_20260406.md](SBAS_PROGRESS_STATUS_20260406.md)**  
  SBAS 系统改造当前落地状态。优先查看这份文档以了解“已经做到哪一步、验证做到哪一步、下一步该补什么”。
- **[SBAS_SYSTEM_INTEGRATION_AUDIT_20260406.md](SBAS_SYSTEM_INTEGRATION_AUDIT_20260406.md)**  
  对现有系统接入 SBAS 的审计结果，聚焦现状缺口与最小可行接入点。
- **[SBAS_SYSTEM_EMBEDDING_DESIGN_20260406.md](SBAS_SYSTEM_EMBEDDING_DESIGN_20260406.md)**  
  SBAS 嵌入现有系统的详细设计稿，覆盖运行记录、作业编排、结果注册与前端页面边界。
- **[SBAS_TASK_PATH_MODE_EVALUATION_20260406.md](SBAS_TASK_PATH_MODE_EVALUATION_20260406.md)**  
  评估是否沿用现有 D-InSAR `task 路径 -> 生产` 模式，以及 SBAS 为什么必须增加 `prepare` 阶段。
- **[SBAS_SYSTEM_CHANGE_RISK_ASSESSMENT_20260406.md](SBAS_SYSTEM_CHANGE_RISK_ASSESSMENT_20260406.md)**  
  SBAS 系统改造风险评估，重点约束“新增不伤旧链”。
- **[SBAS_RESULT_MANAGEMENT_AND_DISPLAY_SPEC_20260406.md](SBAS_RESULT_MANAGEMENT_AND_DISPLAY_SPEC_20260406.md)**  
  SBAS 结果管理、编目和展示约定，明确发布级 bundle 与系统结果目录的关系。
- **[SBAS_FRONTEND_UX_SPEC_20260406.md](SBAS_FRONTEND_UX_SPEC_20260406.md)**  
  SBAS 生产页、结果页与分析页的前端交互设计。
- **[SBAS_IMPLEMENTATION_TODO_20260406.md](SBAS_IMPLEMENTATION_TODO_20260406.md)**  
  SBAS 系统改造任务清单，适合作为后续 Phase 2 / Phase 3 推进的执行列表。

## 2. 已归档文档

以下文档已移入 `docs/archive/`，原因通常是“历史记录”“已完成实施”或“被后续结论覆盖”。

### 本轮归档

- `SECURITY_AUDIT_2026-03-04.md`
- `SECURITY_AUDIT_ANALYSIS_2026-03-04.md`
- `SECURITY_FIX_PLAN.md`
- `SECURITY_FIX_PROGRESS.md`
- `SECURITY_FIX_CHECKLIST.md`
- `SECURITY_FIX_STAGE1_SUMMARY.md`
- `SECURITY_OVERVIEW.md`
- `HARDCODE_AUDIT_2026-03-05.md`
- `LOG_MANAGEMENT_IMPLEMENTATION_2026-03-05.md`

### 既有归档

`docs/archive/` 中原有的旧设计、旧 TODO、阶段性分析和历史修复记录继续保留，仅作历史参考，不再视为当前执行依据。

## 3. 建议阅读顺序

### 新接手项目

1. 先读 [README.md](../README.md)
2. 再读 [DEPLOYMENT.md](DEPLOYMENT.md)
3. 若涉及上线前风险，再读 [SECURITY_AUDIT_2026-03-12.md](SECURITY_AUDIT_2026-03-12.md)

### 正式部署

1. [README.md](../README.md)
2. [DEPLOYMENT.md](DEPLOYMENT.md)
3. [PROJ_CONFIGURATION.md](PROJ_CONFIGURATION.md)（仅在需要时）

### 后续功能规划

1. [DINSAR_ENHANCEMENT_TASKS.md](DINSAR_ENHANCEMENT_TASKS.md)
2. [PAIRING_ENHANCEMENT_DESIGN.md](PAIRING_ENHANCEMENT_DESIGN.md)
3. [PAIRING_SYSTEM_REFACTOR_PLAN_20260413.md](PAIRING_SYSTEM_REFACTOR_PLAN_20260413.md)
4. [ISCE2_SBAS_TIMESERIES_DESIGN.md](ISCE2_SBAS_TIMESERIES_DESIGN.md)
5. [WSL2_ISCE2_MINTPY_SBAS_INTEGRATION_PLAN_20260412.md](WSL2_ISCE2_MINTPY_SBAS_INTEGRATION_PLAN_20260412.md)
6. [GAMMA_WSL2_INTEGRATION_PLAN.md](GAMMA_WSL2_INTEGRATION_PLAN.md)

## 4. 后续维护规则

- `docs/` 根目录只放当前有效文档。
- 已完成实施总结、阶段性修复过程、旧审计分析、被覆盖方案，统一放入 `docs/archive/`。
- 新增文档前，优先判断是否可以并入现有文档，避免再次堆出多份并行说明。
- 如果某份文档只记录一次任务过程，而不是长期参考资料，不应继续留在根目录。

**最后整理**：2026-03-13
