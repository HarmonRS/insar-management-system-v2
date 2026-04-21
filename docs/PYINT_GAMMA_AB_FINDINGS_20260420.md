# PyINT + Gamma A/B 排查结论

更新时间：2026-04-20

## 1. 排查目标

验证当前仓库内改过的 `PyINT/Gamma` 流程，是否只是“流程层修改”而没有影响科学结果；尤其要定位为什么同一组 LT-1 `Task` 在 ENVI/IDL 核心可产出结果，而当前 PyINT 结果为空。

本轮对照任务：

- 任务目录：`D:\Task_Pool\DInSAR\Task_260416_Gamma_PyINT\Task_20230602_20230720`
- DEM：`D:\DEM\COPDEM_GLO30_China_4326_DEM`
- 实验根目录：`D:\PyINT_AB`
- 生产链 Python：`/home/administrator/miniconda3/envs/isce2/bin/python`

## 2. 对照实验

### Case A：当前代码 + 轨道桥接开启 + rescue 开启

- case：`D:\PyINT_AB\current_orbit_rescue`
- 结果：流程可跑完到 `diff`
- 但关键中间结果全 0：
  - `diff_filt.zero_ratio = 1.0`
  - `cor.zero_ratio = 1.0`

### Case B：当前代码 + 轨道桥接关闭 + rescue 开启

- case：`D:\PyINT_AB\no_orbit_rescue`
- 结果：流程同样可跑完到 `diff`
- 关键中间结果仍然全 0：
  - `diff_filt.zero_ratio = 1.0`
  - `cor.zero_ratio = 1.0`

结论：轨道桥接开关不是这次“全 0 结果”的主责任点。

### Case C：轨道桥接开启 + 去掉 rescue

- case：`D:\PyINT_AB\orbit_no_rescue`
- 结果：流程直接死在 `coreg`
- 关键报错：
  - `init_offsetm failed`
  - `ERROR: number of zero values 195367 in MLI1 image patch exceeds threshold: 32768`

结论：当前仓库里的 `coreg rescue` 确实在掩盖真实失败。它让一个本应失败的配准任务继续往后执行，最终产生“流程成功但科学结果全 0”的假成功。

## 3. 直接结论

1. 不能再说“我们现在的改动只改流程、不影响结果”。当前实现已经改变了失败语义，导致无效结果被当成成功结果收口。
2. 这次任务的主要问题点在 `coreg`，不是 GeoTIFF 导出，不是地理编码，也不是解缠。
3. LT-1 精密轨道桥接不是这次空结果的主责任点；更深层的根因仍然要继续排查 LT-1 导入 / 配准链路与 ENVI/IDL 核心之间的差异。

## 4. 已落实的代码策略

为避免系统继续产出“成功但全 0”的无效结果，当前仓库已做两项收敛：

1. `third_party/PyINT/pyint/coreg_gamma.py`
   - 去掉两个 rescue/fallback
   - `init_offsetm/offset_pwrm/offset_fitm/gc_map_fine` 失败时不再复制 `lt0 -> lt1`
   - offset refinement 失败时不再把 `Srslc0` 直接提升为最终 `RSLC`
   - 改为失败即退出
2. `backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py`
   - 增加产物有效性检查
   - 如果 `diff_filt` / `coh` / `unw` / `geo_unw` / `geo_los` 出现“文件存在但二进制全 0”，直接判定此次运行失败
   - 运行失败时附带阶段错误日志路径，例如 `coreg_gamma_all.err`

## 5. 后续真正要解决的问题

这次代码收敛只解决“假成功”问题，还没有解决“为什么 LT-1 在 PyINT/Gamma 下配不准”这个根因。下一阶段建议继续做以下对照：

1. 对比 ENVI/IDL 成功任务与 PyINT 导入后的 `.slc.par`、多视幅度、DEM 配准输入是否一致。
2. 对比 LT-1 导入脚本生成的 `SLC/MLI` 几何参数，尤其是时序、PRF、采样间隔、deskew、状态矢量相关字段。
3. 对比 `mli0` 与目标 `Samp` 的重叠区域，确认 `init_offsetm` 为什么会在中心 patch 上出现大量 0 值。

当前判断：真正的科学问题仍在 LT-1 导入 / coreg 前置几何链路，而不是后面的 unwrap / geocode。
