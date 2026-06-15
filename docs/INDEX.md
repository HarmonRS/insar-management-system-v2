# 文档索引

最后更新：2026-06-14

本页是当前有效文档入口。没有列在本页的历史设计、实验记录和过程文档不再作为当前系统事实依据。

## 总览与部署

- [../README.md](../README.md)  
  项目总览、当前生产入口、启动链路和文档入口。

- [DEPLOYMENT.md](DEPLOYMENT.md)  
  Windows + PostgreSQL + WSL2 + Gamma/ISCE2/ENVI 的部署与运行说明。

- [BASEMAP_TILESERVER_PROXY_AND_ACCESS_20260613.md](BASEMAP_TILESERVER_PROXY_AND_ACCESS_20260613.md)  
  Tile-server proxy, LAN access, token configuration, and Nginx IP whitelist.
- [DOCUMENTATION_GOVERNANCE.md](DOCUMENTATION_GOVERNANCE.md)  
  文档治理规则、事实来源优先级和清理约定。

- [FRONTEND_NAVIGATION_ARCHITECTURE.md](FRONTEND_NAVIGATION_ARCHITECTURE.md)  
  当前左侧导航和生产管理工作台视图模型。

- [GLOBAL_TASK_STATUS_LOCK_REDESIGN_20260612.md](GLOBAL_TASK_STATUS_LOCK_REDESIGN_20260612.md)  
  全局界面锁降级为任务状态中心、功能级任务面板和后端资源锁的重构设计。

## 生产与结果

- [PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md](PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md)  
  统一结果目录、标准产品包、catalog 与多引擎结果共存约定。D-InSAR 当前引擎集合以 2026-06-14 三引擎 Task_Pool 重构设计为准。

- [DINSAR_TASK_POOL_THREE_ENGINE_REFACTOR_20260614.md](DINSAR_TASK_POOL_THREE_ENGINE_REFACTOR_20260614.md)  
  D-InSAR 保留 ENVI/SARscape、LandSAR、Gamma/PyINT 三引擎，退出 ISCE2，统一 Task_Pool、结果聚合和中间文件清理的当前设计。
- [UNC_SOURCE_ARCHIVE_AND_MATERIALIZE_DESIGN_20260615.md](UNC_SOURCE_ARCHIVE_AND_MATERIALIZE_DESIGN_20260615.md)
  UNC/SMB 源压缩包管理、包内 XML/manifest 资产化、本地 materialize 和 D-InSAR/SBAS 生产边界。

- [DINSAR_PRODUCTION_CORES_OVERVIEW.md](DINSAR_PRODUCTION_CORES_OVERVIEW.md)  
  旧版 ENVI/SARscape、ISCE2、Gamma/PyINT D-InSAR 生产核心说明。ISCE2 相关内容仅作历史背景。

- [SBAS_INSAR_CURRENT_WORKFLOW.md](SBAS_INSAR_CURRENT_WORKFLOW.md)  
  当前 Gamma SBAS-InSAR 生产、AOI 选栈、结果 catalog、产物和 LOS 符号约定。

## 运行时与专项配置

- [WSL_RUNTIME_REFACTOR_DESIGN_20260422.md](WSL_RUNTIME_REFACTOR_DESIGN_20260422.md)  
  WSL 共享运行时和 Broker 设计。

- [PROJ_CONFIGURATION.md](PROJ_CONFIGURATION.md)  
  PROJ / GDAL 配置说明。

- [ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md](ISCE2_MANAGED_DINSAR_IMPLEMENTATION_20260424.md)  
  ISCE2 托管 D-InSAR 历史落地说明。新 D-InSAR 生产不再采用。

- [ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md](ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md)  
  ISCE2 生产链路历史稳定性约束。新 D-InSAR 生产不再采用。

## 数据与业务模块

- [SENTINEL1_SOURCE_ORBIT_ASSET_DESIGN_20260512.md](SENTINEL1_SOURCE_ORBIT_ASSET_DESIGN_20260512.md)  
  Sentinel-1 / LT-1 源数据与精密轨道资产层设计。

- [FLOOD_GEOTIFF_GAMMA_PREPROCESS_DESIGN_20260515.md](FLOOD_GEOTIFF_GAMMA_PREPROCESS_DESIGN_20260515.md)  
  洪涝模块 GeoTIFF 化与 Gamma 前处理方向。

- [GF3_SARSCAPE_NATIVE_TO_GEOTIFF_DESIGN_20260530.md](GF3_SARSCAPE_NATIVE_TO_GEOTIFF_DESIGN_20260530.md)  
  GF3 SARscape 原生 `_geo` 二进制池、GeoTIFF 标准化、入库和洪涝接入设计。

- [FLOOD_DISASTER_ANALYSIS_SYSTEM_DESIGN_20260514.md](FLOOD_DISASTER_ANALYSIS_SYSTEM_DESIGN_20260514.md)  
  洪涝灾害分析工作台、产品包和矢量套合边界。

- [FLOOD_WATER_ALGORITHM_ENGINEERING_HANDOFF_20260602.md](FLOOD_WATER_ALGORITHM_ENGINEERING_HANDOFF_20260602.md)  
  洪涝/水体算法接入现状、processor 输出契约和工程交接路线。

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
