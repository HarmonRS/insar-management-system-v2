# 多引擎生产结果管理与路径设计

更新日期：2026-04-24

> 2026-06-14 状态说明：本文保留结果包、catalog、manifest、current 指针等通用目录约定；D-InSAR 当前引擎集合已调整为 ENVI/SARscape、LandSAR、Gamma/PyINT 三类，ISCE2 退出正式生产链路。D-InSAR 引擎、Task_Pool、结果聚合和中间文件清理以 `DINSAR_TASK_POOL_THREE_ENGINE_REFACTOR_20260614.md` 为准。

## 1. 目标

本设计解决三个长期问题：

- 让 `ENVI / SARscape`、`ISCE2`、后续 `Gamma` 的原生输出可以共存，但系统只消费统一结果包。
- 让生产、发布、catalog、数据库自维护、运维自检都围绕同一套受管理目录工作。
- 让同一对影像可以保留多引擎、多 profile、多次重跑结果，而不会互相覆盖。

本设计不是要求所有引擎输出长得一样，而是要求系统托管的结果目录、指针、manifest 和数据库语义一致。

## 2. 核心原则

- 结果管理以“标准包”为中心，不以某个引擎的原生目录命名为中心。
- 原生输出和系统发布资产必须分层，不能混在一起。
- `pair_key` 或 `stack_key` 是业务稳定主键，`run_key` 或 `run_id` 是一次具体生产实例。
- catalog 只扫描受管理发布根中的 `manifest.json`，不再把“历史扫描目录”和“正式生产目录”混用。
- `current` 指针只在“验收通过并成功发布”后更新。
- “只跑未完成”只看当前引擎和当前 profile 对应的 `current` 指针，不看裸目录是否存在。

## 3. 根路径设计

系统只保留一套正式受管理结果根：

```text
D:\production_results\
  dinsar\
  timeseries\
  _quarantine\
```

环境变量语义固定为：

- `RESULT_PUBLISH_ROOT`
  结果发布总根，默认 `D:\production_results`
- `DINSAR_PRODUCT_DIR`
  D-InSAR 正式发布根，默认 `D:\production_results\dinsar`
- `TIMESERIES_PRODUCT_DIR`
  时序 InSAR 正式发布根，默认 `D:\production_results\timeseries`
- `RESULT_QUARANTINE_ROOT`
  结果隔离区，默认 `D:\production_results\_quarantine`

以下路径不再视为正式结果根：

- `MONITOR_DINSAR_DIRS`
  只保留为历史导入或兼容扫描入口，不再作为新生产的默认落盘位置
- `Task_xxx\dinsar_results`
  只保留迁移期兼容语义，最终退出正式生产链

## 4. 目录分层

### 4.1 D-InSAR

```text
D:\production_results\dinsar\<pair_key>\
  current\
    current.json
    sarscape__custom6.json
    isce2__lt1_dinsar_production_v1.json
    gamma__lt1_gamma_dinsar_v1.json
  runs\
    <run_key>\
      execution_manifest.json
      manifest.json
      native\
        ...
      assets\
        disp\
          disp.tif
        coh\
          coh.tif
      preview\
        thumb.webp
```

目录语义：

- `<pair_key>`
  同一对影像的稳定业务目录
- `runs/<run_key>`
  一次具体生产
- `native/`
  引擎原生输出，用于追溯、审计、复核，不直接作为系统消费入口
- `assets/`
  系统统一消费的标准资产层
- `manifest.json`
  正式 catalog 入库契约
- `execution_manifest.json`
  生产执行记录
- `current/*.json`
  当前可用结果指针

### 4.2 时序 InSAR

```text
D:\production_results\timeseries\<stack_key>\
  current\
    current.json
    sbas__v1.json
    psinsar__v1.json
  runs\
    <run_id>\
      manifest.json
      native\
        ...
      assets\
        ...
      preview\
        thumb.webp
```

这里沿用同一哲学，只是主键从 `pair_key` 换成 `stack_key`。

## 5. 指针设计

### 5.1 引擎级 current 指针

每个结果根都允许多个指针同时存在：

- `sarscape__custom6.json`
- `isce2__lt1_dinsar_production_v1.json`
- `gamma__lt1_gamma_dinsar_v1.json`

用途：

- 支持同一对影像保留多引擎结果
- 支持前端按引擎切换结果
- 支持“只跑未完成”按引擎和 profile 做跳过判断

### 5.2 默认 current 指针

`current.json` 不是必须立即启用。

建议语义：

- 只有在系统明确需要“默认展示结果”时才写入
- 默认值由人工或策略层决定，而不是由最近一次运行自动覆盖

## 6. 原生输出与发布资产分层

这是本轮设计的关键约束。

### 6.1 原生输出层

原生输出层由引擎控制，但必须落到受管理 run 目录的 `native/` 下：

- ENVI / SARscape
  保留其成熟原生流程，但原生落盘最终收敛到 `native/`
- ISCE2
  `stripmapApp` 原生输出、日志、中间文件全部进入 `native/`
- Gamma
  后续接入时也遵循相同约束

### 6.2 标准资产层

系统统一暴露资产层：

- `assets/disp/disp.tif`
- `assets/coh/coh.tif`
- `preview/thumb.webp`

未来允许增加：

- `assets/conncomp/conncomp.tif`
- `assets/debug/...`

但 catalog 识别仍以标准 `manifest.json` 为准，不直接猜原生文件结构。

## 7. D-InSAR 生产路径设计

### 7.1 生产服务拥有 run 根目录

对任意一个 `pair_key`，生产服务先创建稳定结果根：

```text
<DINSAR_PRODUCT_DIR>\<pair_key>\runs\<run_key>\
```

然后再把引擎执行和打包都约束到这个 run 目录下。

这意味着：

- `results_root_dir` 指向 `<DINSAR_PRODUCT_DIR>\<pair_key>`
- `execution.output_dir` 指向 `<DINSAR_PRODUCT_DIR>\<pair_key>\runs\<run_key>`
- 原生输出默认进入 `<run_dir>\native`

### 7.2 ISCE2 的路径设计

对正式生产链，`ISCE2_OUTPUT_ROOT` 不再承担“正式发布根”的职责。

建议语义调整为：

- `ISCE2_WORK_ROOT`
  仅用于调试、缓存、临时工作空间，或 standalone 调试运行
- 正式生产时，由编排层直接下发 `<run_dir>\native` 作为原生输出目录
- `ISCE2_OUTPUT_ROOT`
  只保留 fallback 含义，不再默认等于 `DINSAR_PRODUCT_DIR`

### 7.3 ENVI 的路径设计

ENVI 当前仍可能写入 `Task_xxx\dinsar_results`。迁移完成后应统一为：

- 原生结果写入 `<run_dir>\native`
- 标准资产与 manifest 写入 `<run_dir>`

迁移期允许 packager 从历史目录导入，但这只是兼容，不再作为正式目标结构。

## 8. ISCE2 D-InSAR 生产设计约束

### 8.1 生产 profile

建议正式生产 profile 收敛为：

- `lt1_dinsar_production_v1`

不再为 ISCE2 复制 ENVI 的 `metatask/custom6` 形态。ISCE2 自己已经是成熟 orchestrator，项目层不应再人为拆成外部 6 步工作流。

### 8.2 流程边界

正式流程分四层：

1. `preflight`
2. `native_isce2`
3. `postprocess`
4. `acceptance_publish`

其中：

- `preflight`
  负责解析主从影像、DEM、精轨、bbox、多视参数
- `native_isce2`
  必须是一趟原生 `stripmapApp` 跑通到 geocode
- `postprocess`
  负责位移导出、质量掩膜、稳定点精化、标准资产生成
- `acceptance_publish`
  负责写 manifest、更新 current 指针、交给 catalog

### 8.3 参数设计

ISCE2 应继承 ENVI 已经验证过的工程经验，而不是机械复制 ENVI 步骤。

保留或新增的正式生产参数：

- `force`
- `target_grid_size_m`
- `bbox`
- `bbox_margin`
- `orbit_margin_sec`
- `coh_threshold`
- `unwrap_coh_threshold`
- `stable_point_coh_threshold`
- `stable_point_count`
- `refinement_mode`

不建议前端暴露的参数：

- `wavelength`
  LT1 固定值，只读
- `filter_method`
  v1 固定为工程默认值，不向前端开放高级切换

### 8.4 ENVI 经验映射

ENVI 现有工程经验中，值得保留的是：

- `target_resolution -> looks` 换算逻辑
- 基于高相干稳定点的精化思想
- 导出后再做稳定性等待与验收

不应该照搬的是：

- 把 ISCE2 拆成 ENVI 式的外部 6 步调度
- 依赖扫目录“捞”中间文件来续跑

## 9. Catalog、数据库与自检

### 9.1 Catalog

catalog 只扫描：

- `DINSAR_PRODUCT_DIR`
- `TIMESERIES_PRODUCT_DIR`

并且只认 `manifest.json`。

`MONITOR_DINSAR_DIRS` 不再参与正式 catalog rebuild，只保留外部导入用途。

### 9.2 数据库

现有数据库设计可以继续使用，不需要推倒重建：

- `result_products`
- `result_assets`
- `result_issues`
- `result_catalog_states`
- `dinsar_production_runs`
- `dinsar_production_run_items`
- `dinsar_production_executions`

约束：

- `results_root_dir` 继续指向 `<pair_key>` 根目录
- `publish_root_dir` 继续指向 `DINSAR_PRODUCT_DIR`
- `native_output_dir` 由 manifest 持久化
- 启动自维护仍采用“增量补列，不破坏现有数据”的方式

### 9.3 运维自检

运维自检继续围绕 `storage_root` 工作，不围绕任务目录工作。

重点检查三层：

- 结果发布根是否存在
- catalog 状态是否健康
- manifest 数、数据库记录数、needs_rebuild 是否一致

后续再加两项专项检查：

- `product_packages`
  检查 manifest 契约和资产完整性
- `wsl_runtime`
  检查 WSL shared runtime、Python、ISCE2/Gamma 入口一致性

## 10. 迁移顺序

### Phase 1

- 固化本设计
- 不打断当前正在运行的 ENVI 生产
- 仅允许文档、审计、只读检查

### Phase 2

- D-InSAR run 目录强制收敛到 `<pair_key>\runs\<run_key>`
- 把引擎原生输出收敛到 `native/`
- packager 只在 run 目录内生成标准资产和 manifest
- current 指针更新改为“验收通过后再写”

### Phase 3

- ISCE2 正式 profile 切换到 `lt1_dinsar_production_v1`
- 停止 synthetic resume 方案
- 增加 preflight、稳定点精化、结果验收

### Phase 4

- ENVI 生产链迁移到同一结果结构
- legacy `Task_xxx\dinsar_results` 退出正式生产路径

### Phase 5

- catalog rebuild
- compat 视图同步
- 运维自检补齐结果包专项检查

## 11. 当前执行约束

当前如果 ENVI 仍在生产中，以下修改必须等待生产结束后再做：

- 修改 `ISCE2_OUTPUT_ROOT` 默认语义
- 修改 D-InSAR 生产 run 目录结构
- 修改 current 指针判定逻辑
- 修改 packager 输入路径和 in-place 规则
- 修改 catalog rebuild 入口和默认扫描路径
- 修改 ENVI 正式落盘位置

当前可以立即做的事情：

- 文档固化
- 代码审计
- 只读自检
- 改造计划维护

原因很简单：本轮改造会动到结果路径、发布契约、跳过判定和指针语义。生产进行中切这些逻辑，风险不是“某个功能出错”，而是直接污染正在生成的结果根。

## 12. 结论

健康的方案不是让所有引擎“输出一样”，而是让系统消费的“受管理结果结构”一样。

这样之后：

- ENVI 可以继续保留自己的成熟原生算法链
- ISCE2 可以按自己的原生工作流稳定生产
- Gamma 接入时不需要再改目录哲学
- 数据库自维护、catalog、自检、前端结果工作台都只围绕一套标准结果结构工作
