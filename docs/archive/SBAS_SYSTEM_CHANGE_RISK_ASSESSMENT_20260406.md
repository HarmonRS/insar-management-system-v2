# SBAS 系统改造风险评估

更新日期：2026-04-06

## 1. 目标

本次改造的目标不是把实验脚本直接搬进系统，而是在尽量不影响现有 D-InSAR 生产链的前提下，为系统增加一条独立的 SBAS/PS-InSAR 生产与结果管理能力。

约束条件：

- 现有 D-InSAR 生产逻辑必须保持可用
- 现有匹配逻辑先保持不动
- SBAS 生产只基于原始影像，不再沿用 `*_envi_import`
- 结果必须最终落到系统内可管理、可展示、可追踪的发布级 bundle
- 统一环境实验优先，但不能污染当前 D-InSAR 使用的 `isce2` 生产环境

## 2. 风险分级总览

### P0 高风险

- 误改现有 D-InSAR 生产入口、目录契约或作业类型，导致原有生产失败
- 把 SBAS 直接套进现有 `master/slave` 双景输入模型，导致设计方向错误
- 结果编目直接改写现有 `result_catalog_service.py` 的 D-InSAR 语义，造成历史产品异常
- 在当前生产 `isce2` 环境内直接安装新依赖，破坏现有运行稳定性

### P1 中风险

- SBAS 运行记录、任务记录、结果记录之间关系不清，后期无法追溯
- Windows/WSL 双路径继续散落在多个服务中，后续维护成本继续升高
- 前端直接绑定运行时目录结构，导致实验目录变化后页面失效
- 结果展示只展示 `velocity.tif`，忽略 `geo_timeseries.h5`，造成“有图无时序能力”的误判

### P2 低风险

- 字段命名不统一，后续扩展时需要补充适配
- 初期前端只做结果浏览，不做像元级时序查询
- 先不接入健康检查总览，短期不会阻断主流程

## 3. 风险清单

### 3.1 生产链串扰风险

风险：

- 新增 SBAS 能力时误复用或改写 D-InSAR 路由、作业类型、拷贝逻辑、结果目录逻辑

触发点：

- 修改 `backend/app/routers/dinsar_production.py`
- 修改 `backend/app/copier.py` 中现有 D-InSAR 行为
- 在 `result_catalog_service.py` 中直接硬改通用逻辑

影响：

- D-InSAR 生产失败
- 历史产物目录重建异常

控制策略：

- Phase 1 新增独立入口：
  - `timeseries_production.py`
  - `ps_products.py`
  - `psinsar_catalog_service.py`
- 不修改 D-InSAR 的路由路径和任务类型
- 不复用 `copy-ps-stack` 作为 SBAS 生产输入准备

### 3.2 输入契约错误风险

风险：

- 继续沿用 D-InSAR 的 `Task_*/master + slave` 输入目录契约

影响：

- SBAS 处理逻辑被错误建模为“双景处理”
- 后续 prepare/materialize/stack workflow 无法自然落地

控制策略：

- 明确采用 `Stack_<group_key>` / `stack_input_manifest.json` 契约
- 生产入口接收 `batch_id`，由系统生成 stack 级 manifest
- 匹配层继续保留，生产层单独增加 prepare

### 3.3 环境污染风险

风险：

- 在当前 D-InSAR 使用的 `isce2` 运行环境内直接安装 MintPy 或其他依赖

影响：

- 现有 D-InSAR 生产不可复现
- 环境漂移，难以排查

控制策略：

- 当前系统 Phase 1 仅记录并使用 `TIMESERIES_*` 独立配置
- 当前实验优先对接已经验证过的统一环境或桥接环境
- 保留现有 D-InSAR `isce2` 环境不动

### 3.4 数据身份与结果归档风险

风险：

- 只有批次，没有正式“运行记录”
- 只有实验目录，没有系统级产品登记

影响：

- 无法回答“某次 SBAS 运行用的 DEM、轨道、参考日期、掩膜策略是什么”
- 无法稳定展示和管理结果

控制策略：

- 新增 `ps_timeseries_runs`
- 结果仍复用 `result_products` / `result_assets` / `result_issues`
- `run_key` 绑定 `ps_timeseries_runs.run_id`
- 发布边界固定为 `manifest.json`

### 3.5 前端误导风险

风险：

- 仅提供一个“PS 生产占位页”，但用户无法区分：
  - 规划批次
  - 正式生产运行
  - 结果目录
  - 成果展示

影响：

- 操作路径混乱
- 误以为已有生产能力

控制策略：

- 生产页和结果页分离
- 生产页展示：
  - 可选批次
  - 运行参数
  - 最近运行
  - 当前准备状态
- 结果页展示：
  - 编目状态
  - 产品列表
  - 产品详情
  - 资产列表
  - 质量摘要

## 4. 本次推荐改造范围

## 4.1 必做

- 新增 `ps_timeseries_runs` 业务表
- 新增 `timeseries_production` 路由
- 新增 SBAS prepare 任务
- 新增 `psinsar_catalog_service`
- 新增 `ps_products` 路由
- 新增前端 `TimeseriesProductionPanel`
- 新增前端 `PsinsarCatalogPanel`

## 4.2 暂缓

- 不改现有 `find-ps-timeseries` 匹配算法
- 不改现有 D-InSAR 生产入口
- 不把 SBAS 直接接进 `copy-ps-stack`
- 不做像元级 `geo_timeseries.h5` 在线查询
- 不做地图服务化切片发布

## 5. 回滚策略

回滚必须满足“删新不伤旧”。

可回滚对象：

- 新增路由
- 新增 ORM 表
- 新增前端面板
- 新增 `psinsar` 编目服务

不应触碰回滚的对象：

- 现有 D-InSAR 路由
- 现有 `system_jobs` / `system_tasks` 表结构语义
- 现有 D-InSAR 结果目录

推荐回滚方式：

1. 先停用前端入口
2. 再停用新路由注册
3. 保留数据库表，不做破坏性删除
4. 保留已发布 `psinsar` bundle，必要时仅停止 catalog rebuild

## 6. 验收标准

### 6.1 低风险验收

- D-InSAR 现有页面正常打开
- D-InSAR 现有生产入口不报错
- D-InSAR 现有结果目录查询不受影响

### 6.2 SBAS Phase 1 验收

- 可以从已有 `ps_task_batch` 创建一个正式 `ps_timeseries_run`
- 系统能为该 run 生成 stack 级 manifest
- 系统能记录 work/publish 目录、参考日期、轨道统计和输入快照
- 系统可以扫描 `psinsar.publish.v1` bundle 并登记到结果目录
- 前端能查看运行记录与产品详情

## 7. 当前结论

本次改造最重要的不是“尽快把 SBAS 跑起来”，而是先把系统边界做对：

- 规划层不动
- 生产层新增
- 结果层隔离接入
- D-InSAR 旧链路不碰

按这个边界推进，风险可控，且后续可以逐步把实验能力提升为正式生产能力。
