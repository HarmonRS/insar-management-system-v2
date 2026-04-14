# SBAS InSAR 嵌入系统设计稿

更新日期：2026-04-06

## 1. 设计目标

目标不是把当前实验脚本“塞进系统”，而是把已经验证成功的 SBAS 路线变成系统内可提交、可跟踪、可复跑、可发布的正式能力。

设计必须同时满足四个约束：

- 不破坏现有 D-InSAR 生产
- 尽量复用现有 `task` / `job` / `workflow` / `result catalog` 骨架
- 以发布级 bundle 作为系统注册边界，而不是 MintPy 运行目录
- 保留 WSL + conda 的现实部署方式，但把路径和环境耦合收敛到专门层

## 2. 设计原则

### 2.1 规划层和生产层分离

现有：

- `find-ps-timeseries`
- `ps_task_batches`
- `ps_task_items`

这些应该继续保留，但它们只负责：

- 选片
- 栈定义
- 输入快照

它们不应该继续承担：

- 生产运行主记录
- 产物发布状态
- 重试与失败恢复

### 2.2 一个 SBAS 运行对应一个发布 bundle

不要把 SBAS 多个文件当成多个独立产品行。

推荐模型：

- 一个 `ps_timeseries_run`
- 对应一个 publish bundle
- 对应 `result_products` 中一条 `psinsar` 产品记录
- bundle 内部多个文件通过 `result_assets` 管理

这样最贴合当前系统的 `result_products -> result_assets` 结构。

### 2.3 发布边界固定为 `manifest.json`

系统不直接注册：

- `stack_work/`
- `mintpy_work/`
- `timeseries.h5`
- 任意临时中间目录

系统只注册：

- `publish/.../manifest.json`

原因：

- 这是当前实验里最稳定的对外契约
- 它天然适合作为 catalog rebuild 和产品详情入口
- 它把运行时目录与发布目录分开了

### 2.4 Phase 1 优先“隔离集成”，不优先“统一重构”

当前 `result_catalog_service` 明显偏 D-InSAR。

因此 Phase 1 不建议直接大改现有 D-InSAR catalog 服务为完全泛化版本。

更稳妥的做法是：

- 新增 `psinsar_catalog_service.py`
- 接口风格与 `result_catalog_service.py` 保持一致
- 底层继续复用 `result_products` / `result_assets` / `result_issues`

等 SBAS 生产链跑稳后，再提炼公共基类。

## 3. 目标架构

推荐分成五层。

### 3.1 规划层

复用现有：

- `POST /find-ps-timeseries`
- `POST /task-batches/ps`

职责：

- 根据 AOI 和筛选条件找到时间序列候选栈
- 生成一个可复用的 `ps_task_batch`

### 3.2 生产控制层

新增：

- `ps_timeseries_runs`
- `timeseries_production.py`
- `timeseries_service.py`

职责：

- 从一个 `ps_task_batch` 派生一次正式运行
- 固化参数、环境、输入路径、输出路径、DEM、轨道、水体掩膜策略
- 对接 `system_tasks` / `system_jobs` / `workflow_runs`

### 3.3 执行编排层

复用现有：

- `system_tasks`
- `system_jobs`
- `workflow_runs`
- `workflow_steps`
- `workflow_artifacts`

新增：

- `timeseries_workflow_factory.py`
- `mintpy_service.py`
- `isce2_stack_service.py`

职责：

- 把一次 SBAS 运行拆成多个受控步骤
- 每一步进入统一 worker
- 失败可定位到 step，而不是只看到“整条链失败”

### 3.4 发布与编目层

新增：

- `psinsar_catalog_service.py`
- `ps_products.py`

复用：

- `result_products`
- `result_assets`
- `result_issues`
- `result_catalog_states`

职责：

- 扫描和注册 `psinsar.publish.v1`
- 管理产品详情、预览、健康检查、重建

### 3.5 展示层

新增页面：

- `ps_production`
- `ps_products`
- `psinsar_results`

职责：

- 运行提交与监控
- 产品浏览与筛选
- 结果详情、预览和下载

## 4. 核心数据模型

## 4.1 新增业务表：`ps_timeseries_runs`

建议字段：

- `run_id`
- `batch_id`
- `run_name`
- `catalog_name`
- `mode`
  - `sbas`
- `engine_code`
  - `isce2`
- `processor_code`
  - `isce2_stack_mintpy`
- `env_name`
  - 例如 `isce2_mintpy_v1`
- `wsl_distro`
  - 例如 `Ubuntu-24.04`
- `status`
  - `PENDING` / `RUNNING` / `FAILED` / `COMPLETED` / `PUBLISHED`
- `workflow_run_id`
- `task_id`
- `direction`
- `stack_size`
- `reference_date`
- `water_mask_mode`
  - `local` / `synthetic_fallback`
- `dem_path_windows`
- `dem_path_wsl`
- `orbit_pool_windows`
- `orbit_pool_wsl`
- `work_root_windows`
- `work_root_wsl`
- `publish_dir_windows`
- `publish_dir_wsl`
- `manifest_path_windows`
- `manifest_path_wsl`
- `params_json`
- `summary_json`
- `input_snapshot_json`
- `orbit_summary_json`
- `quality_summary_json`
- `error_message`
- `created_by`
- `created_at`
- `updated_at`
- `started_at`
- `ended_at`

### 4.2 不新增产品主表

继续使用：

- `result_products`
- `result_assets`
- `result_issues`

但对 `psinsar` 约定如下：

- `catalog_name = psinsar`
- `product_type = psinsar_bundle`
- `engine_code = isce2`
- `task_alias` 存运行展示名
- `run_key` 直接绑定 `ps_timeseries_runs.run_id`

原因：

- 当前系统的产品层本来就是“一个产品行 + 多资产”
- SBAS publish bundle 天然适合这种结构

### 4.3 `result_assets.asset_role` 约定

建议统一定义以下资产角色：

- `timeseries_cube`
- `velocity_h5`
- `velocity_geotiff`
- `temporal_coherence_h5`
- `temporal_coherence_geotiff`
- `quality_mask_h5`
- `quality_mask_geotiff`
- `preview_png`
- `diagnostic_png`
- `config`
- `quality_summary`
- `input_manifest`

### 4.4 精轨 provenance 建议

最小建议不是新表，而是先把运行级 summary 做扎实。

在 `ps_timeseries_runs.orbit_summary_json` 中记录：

- 每景影像日期
- 轨道 stem
- 轨道 XML 路径
- 来源
  - `existing_xml` / `generated_from_txt`
- checksum
- 是否来自 ISCE2 pool

Phase 2 再考虑单独抽 `orbit_assets`。

## 5. 路径与目录设计

## 5.1 总原则

- 原始影像目录不复制
- 工作目录和发布目录必须分离
- 发布目录一旦注册，尽量不再原地改写

### 5.2 推荐目录结构

Windows 侧：

```text
<TIMESERIES_WORK_ROOT>\{run_id}\
  input\
  stack\
  mintpy\
  logs\
  publish\
```

WSL 侧：

```text
/mnt/.../timeseries_work/{run_id}/
  input/
  stack/
  mintpy/
  logs/
  publish/
```

正式发布根目录：

```text
<PSINSAR_PRODUCT_DIR>\{year}\{run_id}\
  manifest.json
  assets/
  preview/
  metadata/
```

### 5.3 路径服务

不要在 job handler、router、script wrapper 里反复手拼 `/mnt/z`。

新增：

- `timeseries_paths.py`

统一负责：

- Windows -> WSL 路径转换
- 运行目录分配
- 发布目录分配
- 产物入口路径解析

## 6. 工作流设计

推荐 workflow name：

- `isce2_sbas_mintpy_v1`

推荐步骤如下。

### Step 1 `prepare_stack_input`

输入：

- `batch_id`
- 运行参数

动作：

- 读取 `ps_task_items`
- 校验栈大小、方向一致性、日期连续性
- 固化 `input_snapshot_json`
- 解析 DEM、轨道池、输出路径
- 生成 stack manifest

产物：

- `stack_input_manifest.json`
- `source stack snapshot`

### Step 2 `materialize_stack_scenes`

动作：

- 复用现有实验脚本逻辑
- 为每景影像生成 `SLC/<date>/`
- 显式写入 `sensor.orbitFile`

产物：

- 物化后的 SLC
- `materialization_summary.json`

### Step 3 `run_isce2_stack`

动作：

- 运行 stripmapStack 预处理链
- 生成几何、配准、干涉网络基础产物

产物：

- stack work 目录
- run files
- log files

### Step 4 `run_mintpy_sbas`

动作：

- 运行 `smallbaselineApp`
- 生成 `timeseries.h5`、`velocity.h5`、`temporalCoherence.h5`
- 记录 MintPy 版本、参数模板、参考点信息

产物：

- MintPy work 目录
- inversion quality summary

### Step 5 `export_publish_bundle`

动作：

- 地理编码
- 导出 GeoTIFF
- 生成 preview
- 写出 `manifest.json`

产物：

- `publish/.../manifest.json`
- `assets/`
- `preview/`
- `metadata/`

### Step 6 `register_psinsar_product`

动作：

- 调用 `psinsar_catalog_service`
- 把 bundle 注册进 `result_products`
- 回填 `ps_timeseries_runs.manifest_path_*`

产物：

- `result_products` 一条记录
- `result_assets` 多条记录

### Step 7 `post_check`

动作：

- 校验关键资产是否存在
- 校验 `velocity.tif` / `geo_timeseries.h5` / `temporalCoherence.tif`
- 生成最终健康状态

## 7. 调度与任务设计

## 7.1 一个用户可见主任务

建议每次 SBAS 运行只创建一个主 `system_task`：

- `task_type = RUN_PSINSAR_PRODUCTION`

用户在 UI 上主要看这条任务。

### 7.2 一个工作流运行

创建：

- `workflow_run`

每个步骤单独入 `workflow_steps`。

worker 现有能力已经支持：

- job 完成后自动 `mark_step_completed`
- job 失败后自动 `mark_step_failed`

因此不需要再发明新的 step 状态机制。

### 7.3 建议 job types

- `TIMESERIES_PREP_STACK`
- `TIMESERIES_MATERIALIZE_STACK`
- `TIMESERIES_RUN_ISCE2_STACK`
- `TIMESERIES_RUN_MINTPY_SBAS`
- `TIMESERIES_EXPORT_PRODUCTS`
- `TIMESERIES_REGISTER_PRODUCTS`
- `TIMESERIES_POST_CHECK`

## 8. 环境设计

## 8.1 运行环境原则

不修改现有 D-InSAR 使用的 `isce2` 环境。

SBAS 生产使用独立统一环境：

- WSL distro：`Ubuntu-24.04`
- conda env：建议 `isce2_mintpy_v1`

这是当前实验已验证成功的路线。

### 8.2 配置隔离

不要直接复用所有 `ISCE2_*` 配置项来承载 SBAS。

建议新增：

- `TIMESERIES_ENABLED`
- `TIMESERIES_WSL_DISTRO`
- `TIMESERIES_PYTHON`
- `TIMESERIES_ENV_NAME`
- `TIMESERIES_WORK_ROOT`
- `TIMESERIES_PUBLISH_ROOT`
- `TIMESERIES_DEM_PATH`
- `TIMESERIES_ORBIT_POOL_ISCE2`
- `TIMESERIES_WATER_MASK_ROOT`
- `TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK`
- `TIMESERIES_SMALLBASELINE_TEMPLATE`

其中可允许默认回退：

- `TIMESERIES_WSL_DISTRO -> ISCE2_WSL_DISTRO`
- `TIMESERIES_DEM_PATH -> ISCE2_DEM_PATH`
- `TIMESERIES_ORBIT_POOL_ISCE2 -> ORBIT_POOL_ISCE2`

## 9. 精轨、DEM、水体掩膜策略

## 9.1 精轨

直接复用现有：

- source orbit scan
- ENVI / ISCE2 orbit pool sync
- LT-1 orbit XML 解析/生成 helper

生产要求：

- 每景影像在运行前就解析到明确 orbit XML
- 运行记录中保存 orbit provenance

## 9.2 DEM

生产态不依赖在线下载。

要求：

- 本地 DEM 路径必须可读
- 运行记录中固定写入 DEM 路径与版本描述

## 9.3 水体掩膜

这里要区分实验和生产。

实验态：

- 允许 `synthetic waterMask fallback`

生产态建议：

- 优先本地水体数据源
- 若只能 synthetic fallback，则产品可生成，但 `health_status = WARN`
- UI 上必须显式标注“使用了 synthetic water mask”

## 10. `psinsar` 产品注册策略

## 10.1 产品行设计

`result_products` 一条记录代表一个 SBAS bundle，而不是单个文件。

推荐约定：

- `product_id = psinsar_<run_id>`
- `catalog_name = psinsar`
- `product_type = psinsar_bundle`
- `display_name = SBAS_<direction>_<reference_date>_<run_id short>`
- `source_primary_path = assets/geo_timeseries.h5`
- `primary_asset_path = assets/velocity.tif`
- `preview_path = preview/velocity_preview.png`

### 10.2 产品详情展示

产品详情页重点展示：

- 栈日期列表
- 参考日期
- 方向
- DEM
- 轨道来源
- 速度图预览
- 时间序列 cube 下载
- 质量图层下载
- 运行日志入口

## 11. API 设计

### 11.1 生产接口

- `POST /timeseries-production/runs`
  - 基于 `batch_id` 提交一次 SBAS 运行
- `GET /timeseries-production/runs`
  - 列表
- `GET /timeseries-production/runs/{run_id}`
  - 详情
- `POST /timeseries-production/runs/{run_id}/retry-step`
  - 重试单步
- `POST /timeseries-production/wsl-check`
  - 环境检查

### 11.2 产品接口

- `GET /ps-products/catalog-status`
- `POST /ps-products/rebuild`
- `GET /ps-products`
- `GET /ps-products/{product_id}`
- `GET /ps-products/{product_id}/preview`

## 12. 前端页面设计

### 12.1 `ps_production`

页面模块：

- 选择已有 `ps_task_batch`
- 设定运行参数
- 提交运行
- 查看步骤进度
- 查看失败原因和重试按钮

### 12.2 `ps_products`

页面模块：

- 产品列表
- AOI / 时间 / 方向 / 状态筛选
- preview 缩略图
- 一键跳转到产品详情

### 12.3 `psinsar_results`

页面模块：

- 地图展示 `velocity.tif`
- 质量图层切换
- 时间序列主资产下载
- 后续再扩展点时序查询

## 13. 推荐实施顺序

### Phase A：最小生产主链

- 新增 `ps_timeseries_runs`
- 新增 `timeseries_production.py`
- 新增 workflow factory
- 先直接复用实验脚本作为受控 worker wrapper

目标：

- 系统能发起一次 SBAS 运行
- 能看到步骤状态
- 能落到 publish bundle

### Phase B：产品注册

- 新增 `psinsar_catalog_service.py`
- 新增 `ps_products.py`
- 注册 `psinsar.publish.v1`

目标：

- 发布 bundle 能在系统里被浏览和重建

### Phase C：稳定性与治理

- 增加 orbit provenance
- 增加 DEM / water mask 质量标记
- 增加 post-check 和 health-check
- 完善路径服务

### Phase D：UI 完善

- 补生产页
- 补产品页
- 补结果页

## 14. 最终建议

结合当前审计结果，最稳妥的路线不是“直接把 SBAS 纳入现有 D-InSAR 产品通道”，而是：

1. 继续复用 `ps_task_batches` 作为输入栈定义
2. 新增 `ps_timeseries_runs` 作为生产主记录
3. 复用现有任务、作业、工作流骨架
4. 以 publish bundle 为产品注册边界
5. 先独立实现 `psinsar_catalog_service`，避免影响现有 D-InSAR 生产

这样可以做到：

- 技术上最贴合现有系统
- 风险上最小
- 后续也最容易把实验链稳稳升级成正式生产链
