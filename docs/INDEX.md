# 文档索引

本页用于区分“当前有效文档”和“历史参考文档”。

原则：

- 只有列在“当前有效”区域的文档，才作为现网架构、部署、运维和产品边界的事实依据。
- 没有列入“当前有效”区域的材料，默认只作为设计过程记录、阶段性任务单或历史参考。
- 历史文档即使还保留在仓库中，也不应被当作当前系统的真实规则。

## 1. 当前有效

- [../README.md](../README.md)
  项目总览、当前架构和启动入口。

- [DOCUMENTATION_GOVERNANCE.md](DOCUMENTATION_GOVERNANCE.md)
  文档治理约定，定义事实来源优先级、命名规则、归档规则与语言/编码约束。

- [CURRENT_STATUS_20260425.md](CURRENT_STATUS_20260425.md)
  2026-04-25 的项目状态快照，包含结果目录、运行时、健康检查摘要。

- [DEPLOYMENT.md](DEPLOYMENT.md)
  当前 Windows + PostgreSQL + WSL2 部署模型、环境变量和启动链路。

- [DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md](DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md)
  当前数据库自维护机制与现场数据库一致性审计结果。

- [CODEBASE_CLEANUP_AUDIT_20260425.md](CODEBASE_CLEANUP_AUDIT_20260425.md)
  代码库清理审计，记录过渡文件、文档治理和编码显示问题的判定结果。

- [PROJ_CONFIGURATION.md](PROJ_CONFIGURATION.md)
  PROJ / GDAL 相关专项配置说明。

## 2. 当前执行中的核心设计

- [PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md](PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)
  多引擎结果目录、发布结构和 catalog 治理设计。

- [FLOOD_DISASTER_ANALYSIS_SYSTEM_DESIGN_20260514.md](FLOOD_DISASTER_ANALYSIS_SYSTEM_DESIGN_20260514.md)
  洪涝灾害分析独立系统设计，定义多源 SAR 数据、ENVI/SARscape 洪涝流程、标准产品包和矢量套合分析边界。

- [WSL_RUNTIME_REFACTOR_DESIGN_20260422.md](WSL_RUNTIME_REFACTOR_DESIGN_20260422.md)
  WSL 共享运行时和 Broker 设计。

- [ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md](ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md)
  ISCE2 托管式 D-InSAR 落地说明。

- [ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md](ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md)
  ISCE2 生产链路稳定性修补与约束。

## 3. 时序 InSAR / SBAS

- [ISCE2_SBAS_TIMESERIES_DESIGN.md](ISCE2_SBAS_TIMESERIES_DESIGN.md)
- [ISCE2_SBAS_PRODUCT_SPEC.md](ISCE2_SBAS_PRODUCT_SPEC.md)
- [ISCE2_SBAS_ENGINEERING_DESIGN_20260428.md](ISCE2_SBAS_ENGINEERING_DESIGN_20260428.md)
  Current-phase engineering design for the managed ISCE2 + MintPy SBAS route.

说明：

- 当前前端顶级显示名已经统一为“时序 InSAR”。
- 当前默认接入仍然是 SBAS 路径，因此这两份文档仍然有效。
- `SBAS_*` 命名的一批旧文档已经归档，只保留历史追溯价值。

## 4. 配对与前端导航

- [SENTINEL1_SOURCE_ORBIT_ASSET_DESIGN_20260512.md](SENTINEL1_SOURCE_ORBIT_ASSET_DESIGN_20260512.md)
  Sentinel-1 / LT-1 源数据与精密轨道资产层设计，定义统一源产品库存、轨道资产、scene 绑定、启动自维护和健康检查边界。
- [SENTINEL1_SYSTEM_ENHANCEMENT_MASTER_PLAN_20260510.md](SENTINEL1_SYSTEM_ENHANCEMENT_MASTER_PLAN_20260510.md)
  Sentinel-1 系统增强主维护文档，汇总数据管理、精轨、配对、任务分发、Gamma/PyINT 生产、结果管理和分阶段实施边界。
- [SENTINEL1_DATA_MANAGEMENT_ADAPTATION_PLAN_20260510.md](SENTINEL1_DATA_MANAGEMENT_ADAPTATION_PLAN_20260510.md)
  Sentinel-1 源数据管理、精轨匹配、配对约束、分发和后续生产适配的分阶段改造规划。
- [SENTINEL1_DINSAR_GAMMA_ISCE2_FEASIBILITY_20260510.md](SENTINEL1_DINSAR_GAMMA_ISCE2_FEASIBILITY_20260510.md)
  Sentinel-1 D-InSAR 在不使用 ENVI + SARscape 核心时，基于 Gamma/PyINT 与 ISCE2 的可行性、接入边界和推荐实施顺序。
- [DINSAR_PAIRING_DISTRIBUTION_LOGIC_20260508.md](DINSAR_PAIRING_DISTRIBUTION_LOGIC_20260508.md)
  2026-05-08 源码走读记录，梳理 D-InSAR 配对缓存、策略筛选、批次保存、数据分发和生产 worker 执行链路。
- [DINSAR_SOURCE_BUNDLE_REVERSE_TOOL_TASK_20260511.md](DINSAR_SOURCE_BUNDLE_REVERSE_TOOL_TASK_20260511.md)
  D-InSAR 去重源数据包的目录协议、续分发规则和外部反向还原工具任务书。
- [PAIRING_ENHANCEMENT_DESIGN.md](PAIRING_ENHANCEMENT_DESIGN.md)
- [FRONTEND_NAVIGATION_ARCHITECTURE.md](FRONTEND_NAVIGATION_ARCHITECTURE.md)

说明：

- `PAIRING_ENHANCEMENT_DESIGN.md` 保留配对语义、策略命名和产品交互边界设计价值。
- 当前 pairing cache 的运行态事实，以 `CURRENT_STATUS_20260425.md` 和 `DATABASE_SELF_MAINTENANCE_AUDIT_20260425.md` 为准。

## 5. Gamma / PyINT

- [PYINT_GAMMA_INTEGRATION_DESIGN_20260418.md](PYINT_GAMMA_INTEGRATION_DESIGN_20260418.md)
- [PYINT_INPUT_ASSET_ADAPTATION_DESIGN_20260419.md](PYINT_INPUT_ASSET_ADAPTATION_DESIGN_20260419.md)
- [PYINT_LT1_PRECISE_ORBIT_BRIDGE_DESIGN_20260419.md](PYINT_LT1_PRECISE_ORBIT_BRIDGE_DESIGN_20260419.md)
- [PYINT_GAMMA_AB_FINDINGS_20260420.md](PYINT_GAMMA_AB_FINDINGS_20260420.md)

说明：

- 这部分保留当前仍有导航价值的 PyINT / Gamma 设计与结论。
- 旧的 `GAMMA_WSL2_INTEGRATION_PLAN.md` 已于 2026-04-26 归档，因为它描述的是已被替代的 `/idl` + 独立 Gamma 服务方案。
- 真正的当前运行时事实，以 `WSL_RUNTIME_REFACTOR_DESIGN_20260422.md`、`README.md` 和健康面板为准。

## 6. 安全与审计

- [SECURITY_AUDIT_2026-03-12.md](SECURITY_AUDIT_2026-03-12.md)

## Clone Bootstrap

- [../scripts/bootstrap_clone.ps1](../scripts/bootstrap_clone.ps1)
  Fresh-server bootstrap entry for clone-based deployment. It keeps `.env`
  initialization, frontend dependency install/build, runtime bootstrap, and
  deployment validation in one place without changing the main startup chain.

## 7. 工作笔记

- [../INIT.md](../INIT.md)

说明：

- `INIT.md` 视为工作笔记，不是架构、部署和运行事实的最高依据。
- 需要判断“当前真实规则”时，优先看 `README.md`、本页和长期有效文档。

## 8. 历史参考

以下材料默认视为历史设计记录或过程文档：

- [archive/INDEX.md](archive/INDEX.md)
- `docs/archive/`
- 已归档的 `SBAS_*` 阶段文档
- 已归档的 `GAMMA_WSL2_INTEGRATION_PLAN.md`
- 已归档的 `PAIRING_SYSTEM_REFACTOR_PLAN_20260413.md`
- 已归档的 `DINSAR_ENHANCEMENT_TASKS.md`
- 已归档的 `WSL2_ISCE2_MINTPY_SBAS_INTEGRATION_PLAN_20260412.md`
- 已归档的 `项目汇报.md`
- 已归档的各类 `*_EXPERIMENT_*` / `*_PROGRESS_*` / `*_TODO_*`

最后更新：2026-05-12
