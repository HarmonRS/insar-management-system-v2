# SBAS 系统集成与数据管理审计报告

更新日期：2026-04-06

## 1. 审计范围

本次审计聚焦三件事：

- 当前 ISCE2 SBAS 实验是否已经真实读取并使用精密轨道
- 现有系统的数据管理方案是否适合承接 SBAS/PS-InSAR 生产口
- 如果后续要把实验方案嵌入系统，推荐怎样分阶段落地

本报告基于当前仓库实现与已成功的实验产物，不涉及对现有 D-InSAR 生产链的破坏性修改。

## 2. 结论摘要

- 结论 1：当前 SBAS 实验已经真实读取并使用精密轨道，不只是“数据库里有轨道路径”。
- 结论 2：当前系统可以完成 PS/SBAS 选片和实验产物生成，但还不能把 `psinsar` 产物作为正式生产结果注册、编目、健康检查和发布。
- 结论 3：当前数据管理整体偏“路径驱动”，原始数据身份、存储位置、运行产物三层边界不够清晰，因此用户会感到“数据管理比较松散”。
- 结论 4：轨道管理反而是当前较完整的一块，已经具备源目录扫描、池同步、一致性检查和数据库状态统计能力。

## 3. 审计发现

### P0 / 高优先级

#### 3.1 结果编目和发布入口仍然是 D-InSAR 专用，`psinsar` 实验产物无法直接接入系统

证据：

- `backend/app/services/result_catalog_service.py:707-713`
  - `_load_manifest()` 只接受 `product_type == dinsar`
- `backend/app/services/result_catalog_service.py:750-753`
  - 即使读入 manifest，也把 `product_type` 固定写成 `dinsar`
- `backend/app/routers/dinsar_products.py:107-140`
  - 现有编目 API 和后台任务只暴露 `/dinsar-products/*`
- `backend/app/services/health_service.py:124-170`
  - 结果目录健康检查只统计 `catalog_name == dinsar`
- `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/publish/mintpy_sbas_unified_v1/manifest.json:2-9`
  - 当前实验成功产物的 manifest 是 `schema_version = psinsar.publish.v1`，`catalog_name = psinsar`

影响：

- 实验已经能产出发布级 bundle，但系统当前不会把它识别为正式产品
- 不能复用现有结果页、目录重建、健康检查、产品详情接口
- 后续如果直接“硬塞”进 D-InSAR 目录，会污染现有 D-InSAR 语义

建议：

- Phase 1 不要新建一套完全独立的产品表，继续复用 `result_products` / `result_assets`
- 但必须让 catalog 服务显式支持 `catalog_name = psinsar`
- 最小可行改造是新增 `psinsar` manifest 解析路径，或把当前 catalog service 抽象成多 catalog 分发器

#### 3.2 `ps_task_batches` / `ps_task_items` 目前只是选片快照，不是生产记录

证据：

- `backend/app/models/orm.py:516-550`
  - `PsTaskBatchORM` / `PsTaskItemORM` 只保存批次、`file_path`、日期、极化、是否有轨道和状态
- `backend/app/routers/task_batches.py:281-320`
  - 创建 `/task-batches/ps` 时只是把选中的影像元数据写入 `ps_task_items`
- `backend/app/routers/pairing.py:124-147`
  - 现有 `find-ps-timeseries` 只负责找 stack
- `backend/app/services/spatial_service.py:444-481`
  - `find_ps_timeseries_data()` 只返回按轨道方向分组的影像栈
- `backend/app/services/job_handlers.py:306-321`
  - 对 `PS_STACK` 的后台处理目前只是复制选中的影像路径

影响：

- 系统里没有“某次 SBAS 生产运行”的业务主记录
- 无法稳定记录 `work_dir`、`publish_dir`、运行环境、参数版本、DEM、轨道来源、水体掩膜来源
- 无法形成真正可追溯、可重跑、可审计的生产口

建议：

- 新增业务表 `ps_timeseries_runs`
- 保留 `ps_task_batches` 作为“输入栈定义”
- 把真正的处理运行、产物发布、失败重试、步骤状态全部挂到 `ps_timeseries_runs + workflow_runs`

#### 3.3 数据身份与存储路径耦合过深，迁移和重构成本偏高

证据：

- `backend/app/models/orm.py:22-40`
  - `RadarDataORM` 直接保存 `file_path`、`orbit_file_path`、`preview_cache_path`
- `backend/app/models/orm.py:82-101`
  - `ResultProductORM` 直接保存 `publish_dir`、`manifest_path`、`source_primary_path`、`primary_asset_path`
- `backend/app/models/orm.py:182-195`
  - `ResultAssetORM` 保存 `absolute_path`
- `backend/app/services/data_service.py:621-668`
  - `unique_id` 通过 `os.path.relpath()` 生成，并按 `unique_id` 做 upsert

影响：

- 数据身份会受存储根目录变化影响
- Windows 根目录变更、WSL 路径调整、NAS 迁移时，数据库主身份和存储位置语义混在一起
- 产物迁移到正式发布目录后，历史绝对路径会变成系统耦合点

建议：

- 把“数据身份”和“存储位置”拆开
- 原始影像层至少补充稳定的 `scene_uid` 或 `product_uid`
- 结果层至少补充稳定的 `run_id` / `product_id` 业务主键，把绝对路径退化为可替换的存储定位信息

### P1 / 中优先级

#### 3.4 原始影像与轨道的关联粒度偏粗，缺少轨道版本与来源追踪

证据：

- `backend/app/services/data_service.py:611-612`
  - 影像是否有轨道仅按 `(satellite, imaging_date)` 关联
- `backend/app/services/data_service.py:688-692`
  - 后续补关联时也是按同一键更新

影响：

- 当同一天存在多个候选轨道版本时，数据库层无法区分来源和版本
- 无法在结果层明确回答“本次 SBAS 用的是哪份轨道文件、其 checksum 是多少、来自 source 目录还是 ISCE2 pool”

建议：

- 最小可行做法：在 `RadarDataORM` 或新表中增加
  - `orbit_stem`
  - `orbit_checksum`
  - `orbit_source_type`
  - `orbit_pool_path`
- 更完整做法：单独建 `orbit_assets` / `orbit_bindings`

#### 3.5 数据扫描服务职责过重，扫描、轨道同步、预览缓存、数据库写入耦合在一起

证据：

- `backend/app/services/data_service.py:426-463`
  - 扫描原始轨道并同步 ENVI / ISCE2 池
- `backend/app/services/data_service.py:568-699`
  - 同一流程里完成影像元数据读取、轨道关联、数据库 upsert
- `backend/app/services/data_service.py:709-739`
  - 后面又继续做缓存重建候选收集

影响：

- 这个服务变成“总装配间”，测试和回归都更困难
- 后续要引入 SBAS 生产口时，很容易继续把更多责任塞进同一个服务

建议：

- 拆分为至少四块：
  - `radar_inventory_service`
  - `orbit_inventory_service`
  - `radar_catalog_service`
  - `preview_cache_service`

#### 3.6 Windows/WSL 双路径模型是当前现实，但需要收口到专门的路径层

证据：

- `backend/app/config.py:150-186`
  - 同时维护 Windows 轨道池、DEM、IDL、WSL distro、WSL Python 等配置
- `experiments/isce2_sbas_timeseries/scripts/build_lt1_stack_prep.py:140-149`
  - 实验准备阶段会解析 orbit XML
- `experiments/isce2_sbas_timeseries/scripts/build_lt1_stack_prep.py:171-193`
  - 同时生成 Windows 路径和 WSL 路径
- `experiments/isce2_sbas_timeseries/scripts/materialize_lt1_stack_scenes.py:98-103`
  - 物化阶段直接把 orbit XML 传给 ISCE2 sensor

影响：

- 当前方案能跑通，但如果没有统一的路径转换层，后续每个服务都可能自己拼接 `/mnt/*`
- 这会继续加重“数据管理松散”的体感

建议：

- 保留当前双路径现实，不要硬回避
- 但把路径转换统一收敛到 `timeseries_paths.py` 或类似组件
- 生产口只暴露业务字段，不让上层 UI 和业务逻辑直接感知 `/mnt/z` 与 `F:\`

### P2 / 正向发现

#### 3.7 轨道管理是当前系统里相对成熟的一块

证据：

- `backend/app/services/data_service.py:426-463`
  - 已有源目录扫描与 ENVI / ISCE2 轨道池同步
- `backend/app/routers/orbit.py:27-108`
  - 已有数据库统计、池一致性检查、缺口汇总

结论：

- 不需要为 SBAS 重新发明一套轨道管理
- 更合理的方向是复用现有轨道池和一致性检查能力，再补生产级 provenance

## 4. 关于“现在实验读取精密轨道了吗”

答案：是，已经读取，而且已进入实际处理链。

证据链如下：

1. 轨道解析入口：
   - `backend/app/isce2_pipeline/lt1_input_resolver.py:165-201`
   - `ensure_lt1_orbit_xml()` 会优先取已有 XML，找不到才从 LT-1 轨道 txt 生成 XML
2. Stack 准备阶段：
   - `experiments/isce2_sbas_timeseries/scripts/build_lt1_stack_prep.py:140-149`
   - 每景影像都调用 `ensure_lt1_orbit_xml()`
3. Stack 物化阶段：
   - `experiments/isce2_sbas_timeseries/scripts/materialize_lt1_stack_scenes.py:98-103`
   - `sensor.orbitFile = orbit_xml`
4. 实验 manifest 证据：
   - `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_input_manifest.json:92-95`
   - `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_input_manifest.json:123-126`
   - `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_input_manifest.json:154-157`
   - manifest 已明确记录 `D:\\orbit_pools\\isce2\\LT1A_GpsData_GAS_C_*.xml`
5. 运行日志证据：
   - `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/logs/run_01_reference.log:18`
   - `experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1/stack_work/logs/run_03_geo2rdr_coarseResamp.log:6`
   - 日志中已出现 `Orbit interpolation method: hermite`

结论补充：

- 当前 SBAS 实验使用的是 ISCE2 轨道池中的 XML 精轨
- 因此“精轨是否已读入”这件事在实验层已经成立
- 真正缺的不是“有没有轨道”，而是“系统如何把这次运行及其轨道 provenance 规范地登记为生产记录”

## 5. 集成设计建议

### 5.1 集成边界

当前最合理的系统集成边界不是 MintPy 运行目录，而是发布级 bundle：

- 入口文件：`manifest.json`
- 目录结构：`assets/`、`preview/`、`metadata/`

这与现有产品规范一致：

- `docs/ISCE2_SBAS_PRODUCT_SPEC.md:69-88`

因此：

- 不要直接注册 `stack_work/mintpy_sbas_*`
- 要注册 `publish/.../manifest.json` 所代表的一整个 bundle

### 5.2 推荐最小落地架构

#### 层 1：规划层，继续复用现有能力

- 继续使用 `find-ps-timeseries`
- 继续使用 `ps_task_batches` / `ps_task_items`
- 其职责只保留为“选片与栈定义”

#### 层 2：生产层，新增业务主记录

新增 `ps_timeseries_runs`，至少包含：

- `run_id`
- `batch_id`
- `catalog_name`
- `engine_code`
- `processor_code`
- `env_name`
- `wsl_distro`
- `work_dir`
- `publish_dir`
- `manifest_path`
- `reference_date`
- `direction`
- `params_json`
- `summary_json`
- `orbit_summary_json`
- `dem_path`
- `water_mask_source`
- `status`
- `error_message`
- `started_at`
- `ended_at`

#### 层 3：编目层，扩展现有结果目录

不要单独再造一套产品表，继续复用：

- `result_products`
- `result_assets`
- `result_issues`

但要新增 `psinsar` catalog 入口，支持：

- 解析 `psinsar.publish.v1`
- 注册 `catalog_name = psinsar`
- 单独的 catalog status / rebuild / health-check

#### 层 4：接口层

建议新增：

- `POST /timeseries-production/runs`
- `GET /timeseries-production/runs`
- `GET /timeseries-production/runs/{run_id}`
- `POST /timeseries-production/runs/{run_id}/retry-step`
- `GET /ps-products`
- `GET /ps-products/{product_id}`
- `POST /ps-products/rebuild-catalog`

### 5.3 与当前设计文档的一致性

当前仓库里的 SBAS 设计文档已经提前写出了这条路线：

- `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md:227`
  - 建议新增 `ps_timeseries_runs`
- `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md:292-296`
  - 建议新增 SBAS 相关 job types
- `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md:306-313`
  - 建议新增 `timeseries_production` / `ps_products` / `psinsar_catalog_service`
- `docs/ISCE2_SBAS_TIMESERIES_DESIGN.md:563-575`
  - TODO 中已明确列出 ORM、路由、job handler、catalog 扩展和健康检查

所以本次审计的判断不是与现有设计冲突，而是说明：

- 实验层已经坐实
- 代码现实也证明下一步确实该进入“生产层与 catalog 层补齐”

## 6. 建议 TODO

### 6.1 立即做

- [ ] 新增 `ps_timeseries_runs` ORM / schema / migration
- [ ] 新增 `timeseries_production` router
- [ ] 新增 `PUBLISH_PSINSAR_PRODUCTS` 及相关 job handlers
- [ ] 让 catalog 服务支持 `psinsar.publish.v1`
- [ ] 把 `publish/.../manifest.json` 作为唯一注册入口

### 6.2 紧接着做

- [ ] 给 `ps_timeseries_runs` 记录 DEM、轨道、水体掩膜、运行环境版本
- [ ] 给轨道绑定补 provenance 字段或单独轨道资产表
- [ ] 把 Windows/WSL 路径转换收敛到独立路径服务
- [ ] 为 `psinsar` 增加 catalog health-check 与 rebuild

### 6.3 暂缓做

- [ ] 不要现在就改动现有 D-InSAR 生产链
- [ ] 不要把 SBAS 运行目录直接暴露为产品目录
- [ ] 不要为了 SBAS 先拆掉现有 `result_products`

## 7. 审计结论

如果现在问“SBAS 能不能开始往系统里接”，答案是：

- 能，但应该先接“发布 bundle + 生产记录 + psinsar catalog”
- 不能直接把实验目录粗暴塞进现有 D-InSAR 产品体系

如果现在问“系统的数据管理为什么显得松散”，核心原因是：

- 原始数据身份、物理存储路径、生产产物目录三层目前还没有彻底分开

如果现在问“下一步最值得做什么”，答案是：

- 先把 `ps_timeseries_runs + psinsar catalog` 这两个缺口补上

这样既不会动坏现有 D-InSAR 生产，又能把已经验证成功的 SBAS 实验稳稳接成系统能力。
