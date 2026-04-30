# PyINT/Gamma LT-1 集成维护记录

更新时间：2026-04-30

## 1. 背景

本轮工作目标是评估并吸收专家提供的 `D:\Code\PyINT_LT1` 中 LT-1 / Gamma 相关修复，继续推进当前项目内 `PyINT / Gamma` 的 D-InSAR 生产能力。

已有排查结论见：

- `docs/PYINT_GAMMA_AB_FINDINGS_20260420.md`
- `docs/PYINT_GAMMA_INTEGRATION_DESIGN_20260418.md`

之前 A/B 排查已经确认：当前 PyINT/Gamma 的关键问题主要在 LT-1 导入、DEM 几何和 `coreg` 前置链路，不是解缠、地理编码或导出问题。并且旧的 rescue/fallback 曾经掩盖真实 `coreg` 失败，导致流程成功但产物全 0。

## 2. 专家代码判断

专家目录：

`D:\Code\PyINT_LT1`

该目录不是完整 PyINT 替代仓库，而是一组 LT-1 专用脚本补丁，主要包含：

- `down2slc_LT1.py`
- `down2slc_cat_LT1.py`
- `down2slc_LT1_all.py`
- `coreg_gamma.py`
- `coreg_gamma_all.py`
- `generate_rdc_dem.py`
- `makedem_pyint.py`
- `makedem.py`
- `diff/unwrap/geocode` 相关脚本

可吸收价值：

- 修正 LT-1 文件名日期提取偏移。
- 修正 LT-1 批处理错误日志名。
- 改善 `coreg_gamma.py` 中 master/slave 路径、RSLC 路径和 masterDate 选择。
- 增加 master 日期本身的 RSLC 生成处理。
- 对多景拼接场景更明确地区分单片 SLC、update SLC 和最终拼接 SLC。

不能整包覆盖的原因：

- 专家版多个脚本大量使用裸 `os.system()`，部分命令失败不会立即中断。
- 当前项目已经收紧失败语义，要求 Gamma 核心命令失败即失败，避免再次出现“假成功、全 0 结果”。
- 专家版引用 `LT1_precision_orbit.py`，但该文件不在 `D:\Code\PyINT_LT1` 中。
- 当前项目已经有系统级精轨桥接 `backend/app/pyint_pipeline/apply_lt1_precise_orbit.py`，不应退回到未纳管的外部精轨脚本。
- 当前 WSL 环境没有 `csh/tcsh`，不能让 Python 运行链硬依赖 `LT1_import_SLC_from_zipfiles1` 这类 csh 脚本。

## 3. 本轮改动范围

本轮只修改 Gamma/PyINT vendored 脚本，不修改 ISCE2 或 ENVI D-InSAR 生产链。

改动文件：

- `third_party/PyINT/pyint/down2slc_LT1.py`
- `third_party/PyINT/pyint/down2slc_cat_LT1.py`
- `third_party/PyINT/pyint/down2slc_LT1_all.py`
- `third_party/PyINT/pyint/coreg_gamma.py`
- `third_party/PyINT/pyint/generate_rdc_dem.py`

## 4. 已落实策略

### 4.1 LT-1 日期解析

旧实现使用固定切片 `file0[41:48]`，会把 `20241206` 截成 `0241206`。

本轮改为从文件名中正则提取 `20\d{6}`，同时兼容 `.tar.gz` 和 `.tiff` 输入。

### 4.2 LT-1 单景导入

保留当前 Python 直接调用 `par_LT1_SLC` 的方式，不强制依赖 `csh`。

如果运行环境中存在 `par_LT1_SLC_YSLi`：

- 额外生成 `.slc.update` / `.slc.update.par`
- 将 update 参数文件中的 `state_vector_*` 合回主 `.slc.par`
- 同时对 `.slc.par` 和 `.slc.update.par` 执行当前项目的精轨桥接

如果运行环境中不存在 `par_LT1_SLC_YSLi`：

- 继续使用 `par_LT1_SLC` 的输出
- 打印 warning
- 仍执行当前项目的精轨桥接

当前 WSL/Gamma 环境检查结果：

- `par_LT1_SLC` 可用
- `SLC_cat_list.py` 可用
- `par_LT1_SLC_YSLi` 不可用
- `csh/tcsh` 不可用

因此 `par_LT1_SLC_YSLi` 必须保持可选，不能作为硬依赖。

### 4.3 LT-1 多景拼接导入

多景场景改为逐景直接调用 Gamma 导入命令，并明确产出：

- `<date>_<product>.slc`
- `<date>_<product>.slc.par`
- 可选 `<date>_<product>.slc.update`
- 可选 `<date>_<product>.slc.update.par`

拼接时：

- 如果所有分片都有 update SLC，则优先用 update SLC tab 调用 `SLC_cat_list.py`
- 如果 update SLC 不可用，则回退到普通 SLC tab，并打印 warning
- 最终 `<date>.slc.par` 仍会再执行一次当前项目的精轨桥接

### 4.4 错误日志

`down2slc_LT1_all.py` 的错误日志从：

`down2slc_sen_all.err`

修正为：

`down2slc_LT1_all.err`

这能让运行失败时的阶段日志更准确。

### 4.5 coreg / DEM masterDate 兜底

`coreg_gamma.py` 和 `generate_rdc_dem.py` 增加 masterDate 实际存在性检查：

- 如果模板中的 `masterDate` 在 `SLC/` 下存在，则照常使用。
- 如果不存在，则选择最接近的已有 SLC 日期，并打印提示。

`coreg_gamma.py` 同时增加 master 日期本身处理：

- 如果 `Mdate == Sdate`，直接将 master SLC 复制为 master RSLC。
- 生成 master RSLC 的多视幅度。
- 不再进入 slave coreg 流程。

## 5. 保留的安全边界

本轮没有恢复旧 rescue/fallback。

必须继续保留：

- Gamma 核心命令失败即失败。
- `run_lt1_pyint_pipeline.py` 的全 0 二进制产物检查。
- 当前项目的 LT-1 精轨桥接和运行摘要记录。
- `Task_*` 输入资产适配层。

这几个边界是防止 PyINT/Gamma 再次产出“看似成功但科学结果无效”的关键。

## 6. 已完成验证

已完成静态验证：

```bash
python3 -m py_compile \
  third_party/PyINT/pyint/down2slc_LT1.py \
  third_party/PyINT/pyint/down2slc_cat_LT1.py \
  third_party/PyINT/pyint/down2slc_LT1_all.py \
  third_party/PyINT/pyint/coreg_gamma.py \
  third_party/PyINT/pyint/generate_rdc_dem.py
```

已完成 diff 检查：

```bash
git diff --check
```

已确认本轮 Git 改动只包含 Gamma/PyINT 文件和本维护文档，不涉及 ISCE2 / ENVI 生产链。

## 7. 后续验证建议

建议用历史失败样例做 A/B：

- `D:\Task_Pool\DInSAR\Task_260416_Gamma_PyINT\Task_20230602_20230720`

重点看：

- `down2slc_LT1_all.err`
- `coreg_gamma_all.err`
- `SLC/<date>/orbit_bridge_summary.json`
- `pyint_run_summary.json`
- `diff_filt` / `cor` / `unw` / `geo_unw` 的 all-zero 检查结果

判断标准：

- 不能靠 rescue 跳过失败。
- `coreg` 若失败，应明确失败并留下阶段日志。
- 若流程成功，关键二进制产物不能全 0。
- 精轨桥接 summary 应覆盖 master/slave 日期。

## 8. 建议提交

建议和本轮代码一起提交：

```powershell
git add docs/PYINT_GAMMA_LT1_INTEGRATION_NOTES_20260430.md `
  third_party/PyINT/pyint/coreg_gamma.py `
  third_party/PyINT/pyint/down2slc_LT1.py `
  third_party/PyINT/pyint/down2slc_LT1_all.py `
  third_party/PyINT/pyint/down2slc_cat_LT1.py `
  third_party/PyINT/pyint/generate_rdc_dem.py

git commit -m "fix(pyint): integrate LT1 gamma import and coreg fixes"
```

