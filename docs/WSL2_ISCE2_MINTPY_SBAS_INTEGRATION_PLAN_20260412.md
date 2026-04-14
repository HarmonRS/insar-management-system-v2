# WSL2 + ISCE2 + MintPy SBAS 集成方案

Updated: 2026-04-12

## 1. 背景

当前仓库已经具备 SBAS/PS-InSAR 的部分系统骨架：

- 已有 `ps_timeseries_runs` 业务表与基础 API
- 已有 `prepare -> stack_prep_initial -> materialize -> stack_prep_refresh` 四步 workflow
- 已有 `psinsar.publish.v1` manifest 解析与 `psinsar` catalog
- 已有 `ps_production`、`ps_products`、`psinsar_results` 前端入口

同时，实验层已经验证：

- LT-1 stack 可以在 WSL2 `Ubuntu-24.04` 下跑通到 `run_08_igram`
- MintPy SBAS 可以产出 `timeseries.h5`、`velocity.h5`
- geocode/export 可以产出 `psinsar.publish.v1` publish bundle
- 统一环境 `isce2_mintpy_v1` 可以同时导入 `isce` 与 `mintpy`

因此当前最合理的工作不是重写处理算法，而是把已经验证过的 WSL2 运行链稳定接入现有系统。

## 2. 本机环境核对

本方案基于 2026-04-12 的本机实际核对结果。

### 2.1 WSL2 发行版

- 可见发行版：
  - `Ubuntu`
  - `Ubuntu-24.04`
- SBAS 集成目标发行版：
  - `Ubuntu-24.04`

### 2.2 已确认 conda 环境

- `isce2`
  - Python:
    - `/home/administrator/miniconda3/envs/isce2/bin/python`
  - 可导入：
    - `isce`
- `mintpy`
  - Python:
    - `/home/administrator/miniconda3/envs/mintpy/bin/python`
  - 可导入：
    - `mintpy`
- `isce2_mintpy_v1`
  - Python:
    - `/home/administrator/miniconda3/envs/isce2_mintpy_v1/bin/python`
  - 可导入：
    - `isce`
    - `mintpy`

### 2.3 当前判断

- `isce2_mintpy_v1` 已满足“单环境跑 stack + MintPy”的基础条件
- 现有 `isce2` 环境仍应保留给当前 D-InSAR 生产链
- 运行时不应依赖交互式 `sudo`
  - 所有系统依赖、conda 环境、脚本权限应在部署阶段一次性准备完成

## 3. 设计结论

### 3.1 主方案

SBAS 系统集成默认采用：

- WSL distro:
  - `Ubuntu-24.04`
- runtime env:
  - `isce2_mintpy_v1`

### 3.2 备用方案

保留实验期的双环境桥接链路作为 fallback：

- `isce2`
  - 负责 ISCE2 stack
- `mintpy`
  - 负责 MintPy SBAS

但 fallback 不应作为系统默认路径，只在统一环境故障时使用。

### 3.3 产品边界

系统正式接入边界固定为 publish bundle：

- `manifest.json`
- `assets/`
- `preview/`
- `metadata/`

系统不直接把以下目录视为正式产品：

- `stack_work/`
- `mintpy_sbas_*`
- `Igrams/`
- 其他中间目录

## 4. 现状与缺口

### 4.1 已完成

- 运行记录模型：
  - `ps_timeseries_runs`
- 基础生产 API：
  - `timeseries-production`
- 基础产品 API：
  - `ps-products`
- `psinsar` catalog 解析与重建
- 前端生产页、产品页、结果页入口

### 4.2 未完成

当前系统还没有把下面四步接入正式 workflow：

1. `run_isce2_stack`
2. `run_mintpy_sbas`
3. `export_publish_bundle`
4. `register_psinsar_product`

这意味着当前系统是“桥接层已接入”，但还不是“完整自动生产闭环”。

## 5. 目标架构

```text
Windows FastAPI / Worker
  -> submit run / record metadata / monitor progress
  -> dispatch WSL jobs
  -> register publish bundle into psinsar catalog
  -> serve API to frontend

WSL2 Ubuntu-24.04
  -> ISCE2 stripmapStack execution
  -> MintPy SBAS inversion
  -> geocode / export / preview generation
  -> build manifest.json

Formal Product Boundary
  -> PSINSAR_PRODUCT_DIR/<product_id>/
     manifest.json
     assets/
     preview/
     metadata/
```

设计原则：

- 系统负责编排，不负责重计算
- WSL2 负责执行，不负责业务登记
- 正式产品只认 publish bundle
- D-InSAR 与 SBAS 运行时保持隔离

## 6. 推荐 workflow

### 6.1 Phase 1 已有步骤

1. `prepare_stack_input`
2. `build_stack_prep_initial`
3. `materialize_stack_scenes`
4. `refresh_stack_ready`

### 6.2 需要新增的正式步骤

5. `run_isce2_stack`
   - 在 `Ubuntu-24.04` 中执行生成的 `run_01` 至 `run_08_igram`
   - 产出 `Igrams/`、`geom_reference/`、`baselines/`

6. `run_mintpy_sbas`
   - 在 `isce2_mintpy_v1` 中执行 MintPy SBAS
   - 产出：
     - `timeseries.h5`
     - `velocity.h5`
     - `temporalCoherence.h5`
     - `maskTempCoh.h5`

7. `export_publish_bundle`
   - geocode
   - GeoTIFF 导出
   - 生成预览图
   - 生成 `manifest.json`

8. `register_psinsar_product`
   - 将 publish bundle 复制或落到正式 `PSINSAR_PRODUCT_DIR`
   - 增量登记到 `psinsar` catalog

## 7. 后端设计

### 7.1 配置项

建议显式配置以下参数，不再依赖默认继承：

```dotenv
TIMESERIES_ENABLED=true
TIMESERIES_WSL_DISTRO=Ubuntu-24.04
TIMESERIES_ENV_NAME=isce2_mintpy_v1
TIMESERIES_PYTHON=/home/administrator/miniconda3/envs/isce2_mintpy_v1/bin/python
TIMESERIES_WORK_ROOT=<windows path>
TIMESERIES_DEM_PATH=<windows path>
TIMESERIES_ORBIT_POOL_ISCE2=<windows path>
TIMESERIES_EXPERIMENT_ROOT=<repo>/experiments/isce2_sbas_timeseries
TIMESERIES_STACK_PREP_SCRIPT=<repo>/experiments/isce2_sbas_timeseries/scripts/build_lt1_stack_prep.py
TIMESERIES_MATERIALIZE_SCRIPT=<repo>/experiments/isce2_sbas_timeseries/scripts/materialize_lt1_stack_scenes.py
PSINSAR_PRODUCT_DIR=<formal publish root>
```

关键要求：

- `TIMESERIES_ENV_NAME` 与 `TIMESERIES_PYTHON` 必须指向同一环境
- 不再让 `TIMESERIES_PYTHON` 默认继承 `ISCE2_PYTHON`

### 7.2 模块拆分

建议新增或补强以下模块：

- `timeseries_paths.py`
  - 统一维护 Windows/WSL 路径转换
  - 统一生成 work/publish/logs 路径

- `timeseries_runtime_service.py`
  - 统一执行 WSL 命令
  - 统一处理 timeout、stdout/stderr、返回码

- `isce2_stack_runtime_service.py`
  - 封装 `run_01` 至 `run_08_igram` 执行

- `mintpy_runtime_service.py`
  - 封装 MintPy SBAS 运行
  - 封装统一环境 runner

- `psinsar_publish_service.py`
  - 封装 export/publish bundle 生成
  - 封装产品落库前的目录检查

### 7.3 建议新增 job types

- `TIMESERIES_ISCE2_STACK_RUN`
- `TIMESERIES_MINTPY_SBAS_RUN`
- `TIMESERIES_EXPORT_PUBLISH`
- `TIMESERIES_REGISTER_PRODUCT`

当前已有：

- `TIMESERIES_PREPARE`
- `TIMESERIES_STACK_PREP`
- `TIMESERIES_MATERIALIZE`

### 7.4 运行记录要求

`ps_timeseries_runs` 至少应稳定记录：

- `wsl_distro`
- `env_name`
- `python_path`
- `work_root`
- `publish_dir`
- `manifest_path`
- `reference_date`
- `stack_dates`
- `dem_path`
- `orbit_pool`
- `water_mask_mode`
- `step logs`
- `quality summary`
- `command snapshots`

目标不是“只知道跑过”，而是“知道当时用什么环境、什么 DEM、什么 orbit、怎么跑出来的”。

## 8. 结果目录与 catalog 策略

### 8.1 正式产品目录

正式产品目录统一使用：

- `PSINSAR_PRODUCT_DIR`

实验目录：

- `experiments/.../publish/...`

只作为验证来源，不应继续充当正式产品根目录。

### 8.2 推荐策略

第一阶段先支持两种接入方式：

1. `import_experiment_bundle`
   - 将已有实验成功 bundle 导入正式产品目录
   - 用于快速验证 catalog 和前端显示

2. `publish_from_workflow`
   - 由 workflow 自动生成 bundle 并注册
   - 作为正式闭环目标

### 8.3 catalog 设计要求

`psinsar_catalog_service` 建议补充：

- 单 manifest 增量注册
- 单产品刷新
- publish 目录健康检查
- run_id 到 product_id 的稳定关联

## 9. 前端设计

### 9.1 `ps_production`

第一阶段重点不是分析，而是运维可用：

- 显示 WSL/环境状态
- 显示 workflow 步骤状态
- 显示 stdout/stderr 日志位置
- 失败后支持步骤级重试
- 成功后跳转到产品详情

### 9.2 `ps_products`

第一阶段重点：

- list/filter
- 查看 manifest
- 查看 preview
- 手动 rebuild catalog
- 从 run 跳到 product

### 9.3 `psinsar_results`

第一阶段继续复用 catalog 只读视图即可。

### 9.4 `psinsar_analysis`

继续预留，不在当前阶段展开。

## 10. 稳定性约束

必须遵守以下边界：

- 不修改当前 D-InSAR 的默认 `ISCE2_PYTHON`
- 不把当前 D-InSAR 脚本改指向 `isce2_mintpy_v1`
- 不让业务层代码直接拼 `/mnt/z`
- 不把 `MintPy` 工作目录直接暴露为正式产品
- 不依赖交互式 `sudo`

## 11. 推荐实施顺序

### Phase A

先做“实验成果纳入系统”：

- 支持导入已有 experiment publish bundle
- 落到正式 `PSINSAR_PRODUCT_DIR`
- 让 catalog 和前端先可见

### Phase B

再做“完整 WSL2 自动执行闭环”：

- 接入 `run_isce2_stack`
- 接入 `run_mintpy_sbas`
- 接入 `export_publish_bundle`
- 接入 `register_psinsar_product`

### Phase C

最后做“生产级稳定化”：

- 失败重试
- 恢复执行
- 健康检查
- 环境快照
- provenance 补齐

## 12. 验收标准

达到以下条件后，才算 SBAS 正式接入完成：

1. 系统可从一个已有 `PS stack batch` 发起 SBAS 运行。
2. Worker 可在 `Ubuntu-24.04` 中自动驱动 ISCE2 stack。
3. Worker 可在 `isce2_mintpy_v1` 中自动驱动 MintPy。
4. 系统可自动生成 publish bundle。
5. bundle 可自动注册到 `psinsar` catalog。
6. 前端可查看运行状态、产品列表、preview 和 manifest。
7. 失败步骤可重试，且不会污染现有 D-InSAR 生产链。

## 13. 当前推荐

当前最稳的方向是：

- 继续以 `isce2_mintpy_v1` 作为 SBAS 主运行时
- 保留 `isce2` 与 `mintpy` 双环境桥接链路作为 fallback
- 先把 experiment publish bundle 纳入正式系统
- 再补自动执行后四步

这条路线最符合当前仓库状态，也最符合“稳定优先、不破坏现有 D-InSAR”的约束。
