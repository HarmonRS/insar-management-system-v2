# PyINT LT-1 Pair Selection Experiment

**日期**: 2026-04-21  
**状态**: 已执行  
**目标问题**: 之前 `init_offsetm` 失败是否只是当前任务配对选得不好

## 1. 实验思路

上一轮根因定位主要围绕这一组 2023 年 SYC 数据:

- `2023-06-24`
- `2023-07-26`
- `2023-09-20`

其中:

- `2023-06-24 -> 2023-07-26`
- `2023-07-26 -> 2023-09-20`

两对都失败，且 `2023-06-24 -> 2023-07-26` 已经是较短时基。

为了验证是不是“只是这几对碰巧不行”，本轮换了一组新的、同条带同中心点的 2024 年三景，并改用中间时相作为 master。

## 2. 实验数据

实验根目录:

- `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.4_N45.0_3scene_2024`

从影像库复制的三景:

1. `LT1A_MONO_SYC_STRIP1_012583_E129.4_N45.0_20240520_SLC_HH_S2A_0000402579`
2. `LT1A_MONO_SYC_STRIP1_013416_E129.4_N45.0_20240715_SLC_HH_S2A_0000454453`
3. `LT1A_MONO_SYC_STRIP1_014249_E129.4_N45.0_20240909_SLC_HH_S2A_0000505340`

配置:

- `masterDate=20240715`
- `startDate=20240501`
- `endDate=20240930`
- DEM 仍使用:
  - `/mnt/d/DEM/COPDEM_GLO30_China_4326_DEM`

## 3. 执行链路

执行脚本:

- 复制实验根:
  - `D:\Code\Insar_management_system_v2\.codex_tmp\setup_lt1_pool_multiscene_experiment.ps1`
- 跑三景最小链路:
  - `D:\Code\Insar_management_system_v2\.codex_tmp\run_lt1_pool_multiscene_generic.sh`
- 审计几何中间产物:
  - `D:\Code\Insar_management_system_v2\.codex_tmp\audit_lt1_pool_multiscene_root.py`

实际运行结果:

- `down2slc_all`: 成功
- `makedem_pyint`: 成功
- `generate_rdc_dem`: 成功
- `coreg_gamma_all`: 失败

由于 `coreg_gamma_all` 在第一对失败后停止，又额外补跑了:

- `coreg_20240909`

这样两对都拿到了独立结果。

## 4. 结果

### 4.1 `init_offsetm` 失败情况

`20240520 <- 20240715(master)`:

- `zero_count = 197702`
- `threshold = 32768`

`20240909 <- 20240715(master)`:

- `zero_count = 196712`
- `threshold = 32768`

对应日志:

- `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.4_N45.0_3scene_2024\logs\coreg_gamma_all.stderr.log`
- `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.4_N45.0_3scene_2024\logs\coreg_20240909.stderr.log`

### 4.2 中间产物审计

审计汇总:

- `D:\PyINT_POOL_TEST\LT1A_MONO_SYC_STRIP1_E129.4_N45.0_3scene_2024\audit_dem_geometry\audit_summary.tsv`

关键指标:

- `hgtsim zero_ratio = 0.974356`
- `lt0 valid_pair_ratio = 0.025644`
- `20240520 center overlap = 0.245827`
- `20240909 center overlap = 0.249603`

## 5. 与 2023 年那组三景对比

2023 年 `E129.6_N45.0` 那组基线结果:

- `20230624`: `zero_count = 184099`
- `20230920`: `zero_count = 190155`
- `hgtsim zero_ratio = 0.967266`
- `lt0 valid_pair_ratio = 0.032734`

2024 年 `E129.4_N45.0` 新组三景结果:

- `20240520`: `zero_count = 197702`
- `20240909`: `zero_count = 196712`
- `hgtsim zero_ratio = 0.974356`
- `lt0 valid_pair_ratio = 0.025644`

## 6. 结论

这轮实验不支持“只是当前 pair 选坏了”这个解释。

原因很直接:

1. 换了一整组三景
2. 换了 master
3. 两对 slave 都单独跑到了 `init_offsetm`
4. 结果仍然失败
5. 而且零值规模没有改善，反而比 2023 那组更差

因此当前更合理的判断是:

- 问题不是单纯 pair selection
- 也不是只集中在 `2023-07-26` 这一景
- 更像 LT-1 在 PyINT/Gamma 下的导入几何链存在系统性问题

## 7. 建议下一步

下一步不建议继续只靠“再换几对”来试。

更有价值的是继续往几何链前面查:

1. 对比不同实验根生成的 `.slc.par` 几何字段
2. 对比 `generate_rdc_dem` 产生的:
   - `*.utm.dem.par`
   - `*.UTM_TO_RDC`
   - `*.rdc.dem`
3. 重点核查 LT-1 导入程序和 Gamma 实际消费字段之间是否存在系统性偏差
