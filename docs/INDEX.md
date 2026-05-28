# 文档索引

最后更新：2026-05-28

本页是当前有效文档入口。没有列在本页的历史设计、实验记录和过程文档不再作为当前系统事实依据。

## 总览与部署

- [../README.md](../README.md)  
  项目总览、当前生产入口、启动链路和文档入口。

- [DEPLOYMENT.md](DEPLOYMENT.md)  
  Windows + PostgreSQL + WSL2 + Gamma/ISCE2/ENVI 的部署与运行说明。

- [DOCUMENTATION_GOVERNANCE.md](DOCUMENTATION_GOVERNANCE.md)  
  文档治理规则、事实来源优先级和清理约定。

- [FRONTEND_NAVIGATION_ARCHITECTURE.md](FRONTEND_NAVIGATION_ARCHITECTURE.md)  
  当前左侧导航和生产管理工作台视图模型。

## 生产与结果

- [PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md](PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)  
  统一结果目录、标准产品包、catalog 与多引擎结果共存约定。

- [DINSAR_PRODUCTION_CORES_OVERVIEW.md](DINSAR_PRODUCTION_CORES_OVERVIEW.md)  
  ENVI/SARscape、ISCE2、Gamma/PyINT 三条 D-InSAR 生产核心说明。

- [SBAS_INSAR_CURRENT_WORKFLOW.md](SBAS_INSAR_CURRENT_WORKFLOW.md)  
  当前 Gamma SBAS-InSAR 生产、AOI 选栈、结果 catalog、产物和 LOS 符号约定。

## 运行时与专项配置

- [WSL_RUNTIME_REFACTOR_DESIGN_20260422.md](WSL_RUNTIME_REFACTOR_DESIGN_20260422.md)  
  WSL 共享运行时和 Broker 设计。

- [PROJ_CONFIGURATION.md](PROJ_CONFIGURATION.md)  
  PROJ / GDAL 配置说明。

- [ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md](ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md)  
  ISCE2 托管 D-InSAR 落地说明。

- [ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md](ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md)  
  ISCE2 生产链路稳定性约束。

## 数据与业务模块

- [SENTINEL1_SOURCE_ORBIT_ASSET_DESIGN_20260512.md](SENTINEL1_SOURCE_ORBIT_ASSET_DESIGN_20260512.md)  
  Sentinel-1 / LT-1 源数据与精密轨道资产层设计。

- [FLOOD_GEOTIFF_GAMMA_PREPROCESS_DESIGN_20260515.md](FLOOD_GEOTIFF_GAMMA_PREPROCESS_DESIGN_20260515.md)  
  洪涝模块 GeoTIFF 化与 Gamma 前处理方向。

- [FLOOD_DISASTER_ANALYSIS_SYSTEM_DESIGN_20260514.md](FLOOD_DISASTER_ANALYSIS_SYSTEM_DESIGN_20260514.md)  
  洪涝灾害分析工作台、产品包和矢量套合边界。

## 安全

- [SECURITY_AUDIT_2026-03-12.md](SECURITY_AUDIT_2026-03-12.md)  
  安全审计记录。

## 工作笔记

- [../INIT.md](../INIT.md)  
  工作笔记。只用于辅助理解现场状态，不替代正式文档。

## 已删除的历史材料

以下材料已从仓库文档树删除，不再维护：

- 旧 `docs/archive/` 历史堆积目录；
- 旧 SBAS 过程文档和试验 runbook；
- 旧 ISCE2/MintPy/SARscape 时序生产设计；
- 过期的 PyINT/Gamma 实验记录；
- 过期的 Sentinel-1 阶段计划；
- 过期的配对增强计划和阶段性审计快照。

需要判断当前实现时，优先看代码入口和本索引列出的文档。
