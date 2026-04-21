# PyINT LT-1 精密轨道桥接设计

**日期**: 2026-04-19  
**状态**: 方案设计  
**范围**: LT-1 精密轨道真正参与 PyINT / Gamma 计算、与现有 `Task_*` 输入模式协同、前后端与运维落点、分阶段实施

## 1. 结论

当前仓库已经完成了 LT-1 轨道 TXT 的治理级接入，但还没有完成“精密轨道真实参与 PyINT / Gamma 计算”这一层。

本次设计的核心结论如下：

1. 不能把系统轨道池里的 `LT1*_GpsData_GAS_C_YYYYMMDD.txt` 简单当成 `par_LT1_SLC` 的直接输入，因为当前 PyINT / Gamma 的 LT-1 导入链并没有暴露这样的接口。
2. 正确的桥接点是 LT-1 导入完成后生成的 `.slc.par` / `.slc.update.par` 里的 `state_vector_*` 段，而不是当前外层 `run_lt1_pyint_pipeline.py` 的任务参数层。
3. 推荐方案是在现有 Windows 侧轨道治理不变的前提下，在 WSL 侧新增一个 LT-1 精轨桥接 helper，把系统选中的精轨 TXT 重采样到 Gamma 参数文件已有的时间栅格，再回写 `.slc.par`。
4. 桥接动作必须发生在 LT-1 导入之后、DEM / coreg / 干涉处理之前；只做“提交前预检”或“运行记录留痕”是不够的。
5. 一期先不改数据库，先把桥接结果写进 `input_assets`、`pyint_run_summary.json`、结果 manifest 和运行日志；只有在后续确实需要跨运行检索、统计、追责时，再通过现有数据库自维护机制补迁移。

## 2. 现状与依据

### 2.1 当前本地代码链路

现有 vendored PyINT 的 LT-1 流程为：

`pyintApp.py`  
-> `down2slc_LT1_all.py`  
-> `down2slc_LT1.py` 或 `down2slc_cat_LT1.py`  
-> `LT1_import_SLC_from_zipfiles1`  
-> `par_LT1_SLC` / `par_LT1_SLC_YSLi`  
-> 生成 `.slc.par` / `.slc.update.par`

已确认的关键事实：

- [pyintApp.py](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/pyintApp.py) 会先执行 LT-1 `raw2slc`，然后再进入 DEM、coreg、差分干涉。
- [down2slc_LT1.py](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/down2slc_LT1.py) 与 [down2slc_cat_LT1.py](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/down2slc_cat_LT1.py) 是当前 LT-1 Python 入口。
- [LT1_import_SLC_from_zipfiles1](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/LT1_import_SLC_from_zipfiles1) 已经显式处理 `state_vector_*`，说明轨道状态向量确实是 LT-1 导入链中的有效控制点。
- [20210110.slc.par](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/20210110.slc.par) 展示了 Gamma 参数文件中的状态向量布局，包括：
  - `number_of_state_vectors`
  - `time_of_first_state_vector`
  - `state_vector_interval`
  - `state_vector_position_i`
  - `state_vector_velocity_i`

### 2.2 当前系统已有能力

仓库已经具备以下基础：

- [pyint_input_assets_service.py](/D:/Code/Insar_management_system_v2/backend/app/services/pyint_input_assets_service.py) 已能从 `ORBIT_POOL_ENVI` / `PYINT_ORBIT_POOL_TXT` 解析 master/slave 对应的 LT-1 精轨 TXT。
- [run_lt1_pyint_pipeline.py](/D:/Code/Insar_management_system_v2/backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py) 已能把 `Task_*` 目录物化成 PyINT 工作区，并记录 `orbit_policy` 与 `input_assets`。
- [DinsarProductionPanel.jsx](/D:/Code/Insar_management_system_v2/frontend/src/DinsarProductionPanel.jsx) 已经有 PyINT 输入资产预检入口，能够把“轨道是否齐全”提前暴露给用户。
- ISCE2 侧已经有 LT-1 轨道 TXT 解析链，可复用 [convert_lt1_orbit_to_isce_xml.py](/D:/Code/Insar_management_system_v2/backend/app/isce2_pipeline/convert_lt1_orbit_to_isce_xml.py) 与 [lt1_input_resolver.py](/D:/Code/Insar_management_system_v2/backend/app/isce2_pipeline/lt1_input_resolver.py) 中的 `parse_orbit_file`、时间窗口解析等逻辑。

### 2.3 Gamma 官方文档给出的关键约束

用户提供的 Gamma 官方文档是：

- <https://www.gamma-rs.ch/uploads/media/2023-1_TR_China_LT1_Support_in_GAMMA.pdf>

其中与本设计直接相关的结论有两点：

1. 在 LT-1 repeat-pass DInSAR 流程中，Gamma 文档明确说明，`par_LT1_SLC` 导入后需要立即检查并过滤 orbit state vectors，并使用 `ORB_filt_spline.py` 做校验。
2. 在 LT-1 tandem single-pass 流程中，文档同样建议“读入数据后立刻检查/过滤状态向量”，以确保后续 MLI 参数和几何步骤使用的是修正后的状态向量。

这意味着桥接点必须放在“LT-1 导入完成之后立刻执行”，而不是只在外层运行摘要里记录轨道来源。

## 3. 当前缺口

当前实现还缺以下一层：

1. 系统已经知道“这次任务应该用哪份精轨 TXT”，但 PyINT / Gamma 还不知道。
2. 预检面板只能阻断“轨道缺失”的任务，不能保证“轨道已进入计算”。
3. 只改外层 `run_lt1_pyint_pipeline.py` 不够，因为 PyINT 内部会自己完成 `raw2slc -> dem -> coreg` 连续流程，桥接必须插在内部 `raw2slc` 之后。
4. `cat` 场景不能只更新单个 `.slc.par`。当前 [down2slc_cat_LT1.py](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/down2slc_cat_LT1.py) 最终拼接依赖 `*.slc.update.par`，所以方案必须覆盖：
   - 每个分片导入后的参数文件
   - 最终拼接得到的 `<date>.slc.par`

## 4. 目标与非目标

### 4.1 目标

- 保留当前 `Task_*` 输入模式，不要求用户维护第二套 PyINT 原生目录。
- 让 LT-1 精轨 TXT 真正参与 PyINT / Gamma 计算，而不是只做治理留痕。
- 同时覆盖单场景和多分片 `cat` 场景。
- 与现有 DEM 策略、结果目录、运行日志、预检面板兼容。
- 为后续 Gamma 配对集成保留复用路径。

### 4.2 非目标

- 一期不修改数据库主结构。
- 一期不在运维自检页新增复杂操作区。
- 一期不承诺完成 LT1A/LT1B tandem 单通道单程干涉生产链，只保证当前 repeat-pass PyINT 流程的精轨桥接。
- 一期不让用户在前端手工输入单次轨道路径。

## 5. 推荐总体方案

### 5.1 分层思路

推荐把方案拆成“控制面”和“计算面”两层。

#### A. 控制面，继续由现有后端负责

控制面继续沿用现有资产治理链路：

- 从 `Task_*` 解析 master/slave 的卫星与日期
- 从 `PYINT_ORBIT_POOL_TXT` 或 `ORBIT_POOL_ENVI` 定位精轨 TXT
- 在 `input_assets/orbits/` 下留痕
- 在预检接口中返回“是否可提交”

这部分由现有 [pyint_input_assets_service.py](/D:/Code/Insar_management_system_v2/backend/app/services/pyint_input_assets_service.py) 继续承担。

#### B. 计算面，新增 WSL 侧精轨桥接 helper

新增一个 helper，例如：

- `backend/app/pyint_pipeline/apply_lt1_precise_orbit.py`

其职责是：

1. 读取当前任务已解析好的 orbit manifest / staged TXT。
2. 读取目标 `.slc.par` 或 `.slc.update.par`。
3. 复用现有 LT-1 TXT 解析逻辑，解析精轨状态向量。
4. 以 Gamma 当前参数文件已有的时间栅格为目标，进行插值和回写。
5. 备份原始参数文件。
6. 可选调用 `ORB_filt_spline.py` 做二次校验或残差诊断。
7. 输出 `orbit_bridge_summary.json`。

### 5.2 为什么目标时间栅格要复用 `.slc.par` 自身

推荐不要自己发明新的状态向量数量和时间间隔，而是直接复用当前 `.slc.par` 中已有的：

- `number_of_state_vectors`
- `time_of_first_state_vector`
- `state_vector_interval`

然后把系统精轨 TXT 插值到这个时间栅格上。

这样做的收益是：

- 不改变 Gamma 已经生成的参数文件结构。
- 不引入新的向量个数假设。
- 更容易和 `ORB_filt_spline.py`、后续 DEM / coreg 步骤兼容。
- 对 `cat` 场景、已有模板和下游脚本影响最小。

### 5.3 插值策略

推荐优先使用“基于位置和速度的 Hermite 插值”，原因是 LT-1 TXT 同时提供了位置和速度。

最小实现要求如下：

- 先复用 [convert_lt1_orbit_to_isce_xml.py](/D:/Code/Insar_management_system_v2/backend/app/isce2_pipeline/convert_lt1_orbit_to_isce_xml.py) 的 `parse_orbit_file` 解析状态向量。
- 根据 `.slc.par` 的采样时间点计算目标 UTC 时间序列。
- 对目标时间序列进行状态向量重采样。
- 回写 `state_vector_position_i` 和 `state_vector_velocity_i`。

如果一期为了稳妥，不想一次性引入更复杂的插值器，也可以先做：

- 线性插值作为第一落地版
- `ORB_filt_spline.py` 作为强校验

但长期建议还是切到 Hermite，以减少轨道形状失真。

## 6. 推荐挂接点

### 6.1 不推荐只改最外层 wrapper

不推荐只在 [run_lt1_pyint_pipeline.py](/D:/Code/Insar_management_system_v2/backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py) 里做轨道处理，因为它在 PyINT 看来只是外层启动器，无法插入到内部 `raw2slc` 与 `makedem` 之间。

### 6.2 推荐挂接点

推荐优先修改以下 vendored Python 脚本：

- [down2slc_LT1.py](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/down2slc_LT1.py)
- [down2slc_cat_LT1.py](/D:/Code/Insar_management_system_v2/third_party/PyINT/pyint/down2slc_cat_LT1.py)

推荐执行时机：

1. 每次 `LT1_import_SLC_from_zipfiles1` 完成后：
   - 对当前生成的 `.slc.par`
   - 对当前生成的 `.slc.update.par`
   执行一次桥接
2. `SLC_cat_list.py` 生成最终 `<date>.slc.par` 后：
   - 再对最终参数文件执行一次桥接或至少一次强校验

这样可以同时满足：

- 符合 Gamma 文档“导入后立即检查/过滤状态向量”的原则
- 覆盖单片和多分片场景
- 避免只在最终产物上补丁而遗漏拼接过程

`LT1_import_SLC_from_zipfiles1` 本身先不作为一期主改点，除非后续验证发现必须把逻辑进一步下沉到 shell 层才能完全覆盖。

## 7. 运行时流程

推荐的整体执行顺序如下：

1. 用户在生产面板选择 PyINT 引擎并填写 `root_dir`
2. 后端调用 PyINT 输入资产预检
3. 系统为每个 `Task_*` 解析：
   - master/slave 日期
   - master/slave 卫星
   - 对应精轨 TXT
   - DEM 策略
4. `run_lt1_pyint_pipeline.py` 物化：
   - `input_assets/orbits/`
   - `task_manifest.json`
   - PyINT 工作区和模板
5. 外层 wrapper 将 orbit manifest 路径、helper 路径和桥接开关注入 WSL 环境
6. PyINT 执行 `down2slc_LT1.py` / `down2slc_cat_LT1.py`
7. 每个 LT-1 导入步骤完成后，调用 `apply_lt1_precise_orbit.py`
8. helper 回写 `.slc.par`
9. PyINT 继续执行 DEM、coreg、差分干涉、解缠、地理编码
10. 系统输出：
    - `orbit_bridge_summary.json`
    - `pyint_run_summary.json`
    - 结果 manifest 摘要

## 8. 建议新增配置

当前已有：

```ini
PYINT_ORBIT_POLICY=require_txt
PYINT_ORBIT_POOL_TXT=
```

建议新增或明确以下配置：

```ini
PYINT_LT1_PRECISE_ORBIT_ENABLED=true
PYINT_LT1_PRECISE_ORBIT_MODE=replace_and_validate
PYINT_LT1_PRECISE_ORBIT_STRICT=true
PYINT_LT1_PRECISE_ORBIT_MARGIN_SECONDS=120
PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT=true
PYINT_LT1_PRECISE_ORBIT_BACKUP=true
```

建议含义如下：

- `PYINT_LT1_PRECISE_ORBIT_ENABLED`
  - 是否启用真实桥接
- `PYINT_LT1_PRECISE_ORBIT_MODE`
  - `replace_and_validate` 为推荐默认值
  - 后续也可扩展 `validate_only`
- `PYINT_LT1_PRECISE_ORBIT_STRICT`
  - 桥接失败时是否阻断任务
- `PYINT_LT1_PRECISE_ORBIT_MARGIN_SECONDS`
  - 对场景时间窗口额外扩展的秒数
- `PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT`
  - 是否调用 `ORB_filt_spline.py` 做残差校验
- `PYINT_LT1_PRECISE_ORBIT_BACKUP`
  - 是否在回写前备份原始 `.slc.par`

## 9. 元数据与落盘策略

一期建议不改数据库，先把桥接痕迹记录到运行产物里。

### 9.1 `input_assets` 侧

建议在 `input_assets/orbits/` 下保留：

- 解析到的 master/slave 精轨 TXT
- `orbit_resolution.json`
- `orbit_bridge_request.json`

### 9.2 运行摘要侧

建议在 `pyint_run_summary.json` 中新增：

```json
{
  "orbit_bridge": {
    "enabled": true,
    "mode": "replace_and_validate",
    "status": "applied",
    "master": {
      "orbit_txt": "..."
    },
    "slave": {
      "orbit_txt": "..."
    },
    "applied_files": [
      {
        "path": ".../20250309.slc.par",
        "role": "slave",
        "vector_count": 15,
        "validated": true
      }
    ]
  }
}
```

### 9.3 结果 manifest 侧

结果 manifest 只保留摘要，不重复放大块明细，建议记录：

- 是否启用精轨桥接
- 桥接状态
- master/slave 轨道来源 stem
- 是否通过 `ORB_filt_spline.py` 校验

### 9.4 数据库策略

一期不改数据库。

如果后续明确需要：

- 按轨道版本检索历史运行
- 统计桥接失败原因
- 审计某次产品到底使用了哪份精轨

再通过现有 [db_maintenance.py](/D:/Code/Insar_management_system_v2/backend/app/db_maintenance.py) 机制新增迁移。

## 10. 前端与运维落点

### 10.1 前端

前端主入口继续放在现有 PyINT 生产区域，不新增独立页面。

建议在 [DinsarProductionPanel.jsx](/D:/Code/Insar_management_system_v2/frontend/src/DinsarProductionPanel.jsx) 的 PyINT 输入资产预检卡中增加两类信息：

- 全局级：
  - `精轨桥接: 已启用 / 仅治理 / 未启用`
  - `桥接模式`
- 任务级：
  - master/slave 是否已解析精轨
  - 本次是否满足真实桥接前置条件

不要让用户手工输入 orbit 路径。

### 10.2 运维自检

运维自检页不再承载新的操作区，只保留状态摘要。

建议在引擎健康或 PyINT 健康项里补充：

- `PYINT_LT1_PRECISE_ORBIT_ENABLED`
- helper 脚本是否存在
- `PYINT_ORBIT_POOL_TXT` / `ORBIT_POOL_ENVI` 是否可读
- `ORB_filt_spline.py` 是否可调用

不建议把桥接按钮堆进现有 [HealthCheckPanel.jsx](/D:/Code/Insar_management_system_v2/frontend/src/HealthCheckPanel.jsx)。

## 11. 与 Gamma 配对集成的关系

这套桥接不是只服务 D-InSAR 生产，也是在为后续 Gamma 配对打基础。

原因是：

- 如果后续要把 Gamma / PyINT 配对结果真正纳入系统，配对阶段对 baseline 和场景几何的一致性要求会更高。
- 只做“轨道存在性预检”仍然不够，仍然需要一条“导入后立即修正状态向量”的内部链路。

因此推荐把本次 helper 设计成通用能力：

- 当前用于 `pyintApp.py` 的 LT-1 `raw2slc`
- 后续也可复用于 `select_pairs.py` 前的 LT-1 导入准备

## 12. 风险与未决问题

当前仍有几项需要在实现阶段验证：

1. LT-1 TXT 的时间系统与 `.slc.par` 的 `date + seconds-of-day` 是否存在跨日边界问题。
2. `ORB_filt_spline.py` 更适合用于“替换后校验”还是“替换后再执行一次修正”，需要先做小样本验证。
3. `SLC_cat_list.py` 当前并未 vendored 到仓库中，实现阶段要进一步确认它对输入 `.par` 的依赖细节。
4. 当前 repeat-pass 流程与 LT1A/LT1B tandem single-pass 流程并不完全等价，后者需要单独设计。
5. 如果发现某些场景的 Gamma 原始导入时间栅格明显不合理，可能需要从“复用现有时间栅格”升级到“按 scene window 重新构造时间栅格”。

## 13. 分阶段实施建议

### Phase 1

- 新增 `apply_lt1_precise_orbit.py`
- 复用现有 LT-1 TXT 解析器
- 实现 `.slc.par` 读取、备份、状态向量回写
- 产出 `orbit_bridge_summary.json`

### Phase 2

- 修改 `down2slc_LT1.py` 和 `down2slc_cat_LT1.py`
- 在导入后与最终拼接后调用 helper
- 跑通单任务 smoke test

### Phase 3

- 在运行摘要和结果 manifest 中纳入桥接信息
- 在生产面板预检区域增加“精轨桥接已启用”可见性
- 在 health 中增加最小状态摘要

### Phase 4

- 用真实 LT-1 样本比较桥接前后：
  - `ORB_filt_spline.py` 残差
  - 后续 coreg 质量
  - 干涉相位整体趋势
- 评估是否为 Gamma 配对链复用相同 helper

## 14. 最终建议

推荐按以下原则推进：

1. 继续保留现有 `Task_*` 输入模式和轨道治理链。
2. 把 LT-1 精轨接入点明确落到 `.slc.par` 的 `state_vector_*` 回写，不再停留在治理层。
3. 一期先实现“桥接 helper + vendored LT-1 raw2slc 挂接 + 运行元数据留痕”。
4. 数据库先不动，运维面板先不扩张。
5. 桥接 helper 从第一天起就按“未来可复用于 Gamma 配对”来设计接口。
