# PyINT LT-1 `coreg` 失败轨道假设验证实验

**日期**: 2026-04-20  
**状态**: 实验设计  
**目标问题**: 验证当前 LT-1 在 PyINT/Gamma 中 `coreg/init_offsetm` 失败，是否主要由 `par_LT1_SLC` 导入后的 orbit/state vector 处理缺失导致

## 1. 结论先行

这次实验不再重复验证配对逻辑，也不再重复验证 `Task_*` 路径组织、DEM 来源或多景输入形态。

本实验只回答一个更窄的问题:

> 在保持同一批 LT-1 影像、同一 master、同一 DEM、同一 `coreg` 参数不变的前提下，只改变 `.slc.par` 中的 state vector 处理方式，是否会显著改变 `coreg/init_offsetm` 的失败行为。

如果答案是“会”，则当前问题主要集中在 LT-1 导入后的轨道几何链条。  
如果答案是“不会”，则需要把排查重点转回 LT-1 导入本身的几何建模或 `MLI/SLC` 生成环节。

## 2. 已知事实

当前已经有一个可复现的 3 景隔离实验环境:

- 实验根目录: `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene`
- master: `20230726`
- slave:
  - `20230624`
  - `20230920`
- 已确认成功的阶段:
  - `down2slc_all`
  - `makedem_pyint`
  - `generate_rdc_dem`
- 已确认失败的阶段:
  - `coreg_gamma_all`
- 当前稳定失败特征:
  - `init_offsetm failed`
  - `ERROR: number of zero values ... exceeds threshold: 32768`

这说明:

1. 输入组织已经足够让 PyINT 跑到 `coreg`。
2. DEM 不构成这轮失败的主因。
3. 问题更像是 `coreg` 所依赖的几何输入有问题，尤其是 `.slc.par` 的 orbit/state vector 链条。

## 3. 实验假设

### H1: 当前主假设

`par_LT1_SLC` 导入后的 `.slc.par` 还需要额外的 orbit/state vector 处理。

这个处理至少可能包括两类:

1. 对现有 state vectors 做平滑/过滤
2. 用系统已有 LT-1 精密轨道 TXT 重写 state vectors，再做校验

### H0: 零假设

即使对 state vectors 做上述处理，`coreg/init_offsetm` 的失败模式也基本不变。  
若如此，则缺陷更可能位于:

- LT-1 导入后的几何参数生成
- `MLI` 生成质量
- `deskew` / 时序 / 采样参数
- 或 `par_LT1_SLC` 本身不适用于当前这批数据

## 4. 实验原则

本实验必须严格控制变量，避免再把多种问题混在一起。

固定不变的部分:

- 同一批 3 景 LT-1 数据
- 同一个 master: `20230726`
- 同一套 `range_looks=2`, `azimuth_looks=2`
- 同一个 DEM 数据源
- 同一个 `coreg_gamma.py`
- 同一个 PyINT/Gamma 环境

唯一允许变化的部分:

- `.slc.par` 内 state vector 的处理方式

不在本轮变化范围内:

- 不切换配对策略
- 不切换 DEM
- 不改 `coreg rescue`
- 不改 `select_pairs`
- 不重做新的场景池选择

## 5. 分组设计

### A 组: 基线组

目的: 复现当前失败，作为所有对照的基准。

处理方式:

- 使用 `par_LT1_SLC` 当前直接生成的 `.slc.par`
- 不做任何 orbit/state vector 改写
- 重新执行:
  - `generate_rdc_dem`
  - `coreg` 到每个 slave

预期:

- 与现有日志一致
- 两个 slave 都在 `init_offsetm` 附近失败

### B 组: 仅做 Gamma orbit filter

目的: 验证“问题是否只是 state vector 需要过滤/平滑，而不一定需要外部精轨替换”。

处理方式:

- 在 A 组同源 `.slc.par` 副本上，仅对 state vectors 做 Gamma orbit filtering
- 不引入系统外部 LT-1 精密轨道 TXT
- 之后重新执行:
  - `generate_rdc_dem`
  - `coreg`

判定意义:

- 如果 B 组明显优于 A 组，说明 `par_LT1_SLC` 的原始 state vectors 质量不足，但问题可能主要是“需要轨道过滤”
- 如果 B 组与 A 组几乎一样失败，则仅做过滤不够

### C 组: 精密轨道重写组

目的: 验证“问题是否是导入后缺少外部精密轨道替换”。

处理方式:

- 使用系统已有 LT-1 精密轨道 TXT
- 通过 [apply_lt1_precise_orbit.py](/D:/Code/Insar_management_system_v2/backend/app/pyint_pipeline/apply_lt1_precise_orbit.py) 重写 `.slc.par` 的 `state_vector_*`
- 插值目标时间栅格沿用 `.slc.par` 自身:
  - `number_of_state_vectors`
  - `time_of_first_state_vector`
  - `state_vector_interval`
- 重写后重新执行:
  - `generate_rdc_dem`
  - `coreg`

判定意义:

- 如果 C 组明显优于 A 组，而 B 组没有明显改善，则说明问题更偏向“缺少精密轨道重写”
- 如果 C 组和 B 组都改善，则说明“导入后的轨道链条不完整”成立，但是否必须引入外部精轨还需进一步量化

### D 组: 精密轨道重写后校验组

目的: 观察“精轨重写后，Gamma 自身的 spline 校验是否仍给出大修正量”。

处理方式:

- 先执行 C 组重写
- 再调用 `ORB_filt_spline.py` 生成验证副本
- 不一定把验证副本作为正式输入使用，先记录校验结果

判定意义:

- 如果校验修正量很小，说明 C 组重写后的 state vectors 与 Gamma 的平滑约束基本一致
- 如果校验修正量仍然很大，说明即使引入精轨，state vector 时间栅格或插值方式仍可能有问题

## 6. 实验执行路径

推荐直接复用现有 3 景实验目录，不重新拷贝数据:

- 基础目录: `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene`
- 现有日志目录: `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene\logs`
- 现有 SLC 目录: `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene\pyint_stage\SLC`

推荐把每个实验组做成并列工作副本，例如:

```text
D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_cases\
  case_A_baseline\
  case_B_orb_filt\
  case_C_precise_orbit_rewrite\
  case_D_precise_orbit_validate\
```

每个 case 必须从同一份 `down2slc_all` 完成后的结果拷贝出来，避免导入过程本身再次引入差异。

## 7. 每组执行顺序

### Step 1: 冻结基线输入

先选取一个共同起点:

- `down2slc_all` 已完成
- `makedem_pyint` 已完成
- `coreg` 尚未执行，或清理已有 `coreg` 中间产物

这样所有 case 都基于同一份:

- `SLC/*.slc`
- `SLC/*.slc.par`
- `MLI`
- `DEM`

### Step 2: 仅修改 `.slc.par`

按分组分别对 `.slc.par` 做处理:

- A 组: 不改
- B 组: 仅 filter
- C 组: 精轨重写
- D 组: 精轨重写后再做 Gamma 校验

注意:

- 不要重新导入原始压缩包
- 不要改模板参数
- 不要改 `ifgram_list`

### Step 3: 重做 `generate_rdc_dem`

因为 `rdc_dem` 与几何参数耦合，改完 `.slc.par` 后必须重做一次。

### Step 4: 单独跑每个 slave 的 `coreg`

推荐不要一开始就跑 `coreg_gamma_all.py`，而是先分别跑:

- `20230624`
- `20230920`

这样更容易判断某个处理是否对所有 slave 都有效。

### Step 5: 收集指标

每个 case 对每个 slave 都产出独立日志，并汇总到统一表格。

## 8. 观测指标

本实验不能只看“是否跑通”，还要看失败形态是否发生了有意义的变化。

核心指标:

1. `coreg` 是否成功进入下一阶段
2. `init_offsetm` 是否仍失败
3. 报错中的 zero-value patch 数量
4. 失败位置是否仍固定在 `init_offsetm`
5. 是否产生有效的 `RSLC`

轨道相关指标:

1. 每个 `.slc.par` 改写前后的 state vector 位置差范数
2. 每个 `.slc.par` 改写前后的 state vector 速度差范数
3. `ORB_filt_spline.py` 若运行成功，其输出副本相对输入的修正量

结果质量指标:

1. 如果 `coreg` 成功，后续 `diff/cor` 是否仍全 0
2. `diff_filt.zero_ratio`
3. `cor.zero_ratio`

## 9. 判定标准

### 支持主假设的证据

以下任一情况都可视为强支持:

1. B 组或 C 组能让两个 slave 中至少一个从 `init_offsetm` 失败变成成功
2. B 组或 C 组虽然仍失败，但 zero-value patch 数量显著下降
3. C 组优于 B 组，说明外部精轨重写比单纯过滤更关键

### 反对主假设的证据

以下情况说明当前假设不足:

1. A/B/C/D 四组都在同一位置、以近似相同错误失败
2. 改写前后 state vector 差异很大，但 `coreg` 行为几乎不变
3. `ORB_filt_spline.py` 校验显示修正量不大，但 `coreg` 仍完全失败

若出现这些情况，下一轮应优先排查:

- `par_LT1_SLC` 生成的几何字段是否本身错误
- `MLI` 数据中大面积零值的真实来源
- LT-1 导入后的采样/deskew/时序链条

## 10. 推荐的最小可执行版本

如果想尽快验证，不必一次把四组都跑全，先做最小闭环:

1. A 组: 当前基线
2. C 组: 精密轨道重写
3. D 组: 精密轨道重写后做 `ORB_filt_spline.py` 校验

理由:

- A 组已经稳定存在
- C 组最直接验证“外部精轨重写是否必要”
- D 组可以帮助判断“即便重写了，Gamma 仍不认可的程度有多大”

B 组可以作为补充组，用来区分“只需过滤”还是“必须引入精轨”。

## 11. 执行前提

执行本实验前，需要确认:

1. WSL 中可调用 Gamma 工具
2. `apply_lt1_precise_orbit.py` 可以访问本次实验所需的 LT-1 精密轨道 TXT
3. `ORB_filt_spline.py` 如 shebang 不可用，则通过明确的 Python 解释器调用
4. 每个 case 使用独立日志目录，避免覆盖

## 12. 风险与注意事项

1. 不能在同一个 `SLC` 目录上反复覆盖做多组实验，否则会污染基线
2. 改写 `.slc.par` 后如果不重做 `generate_rdc_dem`，实验结论不可靠
3. 这轮实验只验证“轨道处理是不是主因”，不等于验证“最终科学结果已经正确”
4. 即使某组 `coreg` 成功，也必须继续检查后续 `diff/cor` 是否仍然全 0

## 13. 实验产出物

建议每组最终至少保留:

- 处理后的 `.slc.par`
- `generate_rdc_dem` 日志
- 每个 slave 的 `coreg` 日志
- 一个汇总 JSON 或 Markdown 表

建议的汇总字段:

```json
{
  "case": "case_C_precise_orbit_rewrite",
  "master": "20230726",
  "slave": "20230920",
  "state_vector_mode": "precise_orbit_rewrite",
  "coreg_success": false,
  "failed_stage": "init_offsetm",
  "zero_patch_count": 190155,
  "rslc_exists": false,
  "orb_validation_status": "not_run"
}
```

## 14. 推荐下一步

推荐按下面顺序执行:

1. 先在现有 3 景实验目录上做 A/C/D 三组
2. 如果 C 组明显改善，再补 B 组区分“过滤”与“精轨重写”的贡献
3. 如果 A/B/C/D 全部失败，再正式转向 `par_LT1_SLC` 输出几何字段和 `MLI` 零值来源排查

这轮实验的价值不在于立刻修好流程，而在于把问题边界收窄到“轨道链条”还是“导入几何链条”。
