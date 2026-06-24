# Gamma SBAS 正确实现路线 2026-06-07

## 结论

当前系统的 Gamma SBAS 实现是错误模式，不是“不严格模式”。它把专家文档中的逐命令流程抽象成了另一条 Gamma/PyINT 混合链路，导致生成结果无法按专家命令行过程复核，也不能作为正确 SBAS 成果交付。

本次重构目标：默认 LT1 Gamma SBAS 只能执行专家文档 `LT1_GAMMA_SBAS_逐命令处理流程.docx` 对应的命令链。系统的职责是封装专家命令行流程，而不是替换成看起来相似的工具链。

## 错误来源

| 专家文档要求 | 当前错误实现 | 重构要求 |
| --- | --- | --- |
| `par_LT1_SLC` 后执行 `ORB_filt_spline.py` | 当前使用 `LT1_precision_orbit.py` 桥接 | 改为专家文档记录的 `ORB_filt_spline.py`，保留轨道输入审计 |
| `create_offset/init_offset_orbit/init_offset/offset_pwr/offset_fit/SLC_interp` | 当前使用 `SLC_coreg.py --init_offset` | 删除默认流程中的 `SLC_coreg.py`，逐场景展开专家命令 |
| `dem_import/fill_gaps/gc_map2/pixel_area/create_diff_par/offset_pwrm/offset_fitm/gc_map_fine/geocode/geocode_back` | 当前复用 PyINT DEM cache 并使用 `gc_map1` | DEM 从源 GeoTIFF 导入，必须覆盖完整 stack bbox，不允许只覆盖中心点 |
| `base_calc/base_plot/mk_diff_2d` | 当前使用 `phase_sim_orb/SLC_diff_intf` | 改为 `mk_diff_2d` |
| `mk_adf_2d/ave_image/rascc_mask/mk_unw_2d` | 当前使用 `adf/cc_wave/mcf` | 改为专家文档的 `mk_adf_2d` 和两次 `mk_unw_2d` |
| `quad_fit/quad_sub/atm_mod_2d/fill_gaps/atm_sim_2d/sub_phase` | 当前部分接近但输入来自错误解缠链 | 保留命令类型，输入统一改为专家链路产物 |
| 三轮 `mb` 加复数转换/`unw_model` | 当前一轮 `mb` 后直接 `ts_rate` | 改为专家文档三轮反演；本机 Gamma 环境未提供 `unw_to_cpx`，实际使用 `real_to_cpx - <unw> <cpx> <width> 1` 执行同一位置的实数到复数转换 |
| `replace_values/mask_data/dispmap/ts_rate/geocode_back/data2geotiff/disp_prt_2d` | 当前 Python 转 LOS 后发布多套派生产品 | 改为专家文档输出链，派生产品只能在专家产物之后追加，不能替代主产品 |

## 正确流程

### 01 Workspace

创建专家文档目录结构：

- `RAW`
- `SLC`
- `dem`
- `rslc_prep`
- `mli_dir`
- `diff_dir`
- `diff1_dir`
- `sbas`
- `publish`
- `logs`
- `scripts`
- `state`

验收条件：目录存在，场景清单和源文件路径写入 manifest。

### 02 Import LT1 SLC

每景执行：

```bash
par_LT1_SLC <scene>.tiff <scene>.meta.xml <date>.slc.par <date>.slc 0
cp <date>.slc.par <date>.slc.par.orig
ORB_filt_spline.py <date>.slc.par.orig <date>.slc.par --ignore_start 3 --ignore_end 17 --degree 5
SLC_corners <date>.slc.par
disSLC <date>.slc <width> ...
dismph_fft <date>.slc <width> ...
```

验收条件：每景 `.slc/.slc.par/.slc.par.orig` 存在，`SLC_tab` 行数等于场景数。

### 03 Reference MLI

参考景执行：

```bash
multi_look <ref>.slc <ref>.slc.par <ref>_<rlks>_<azlks>.mli <ref>_<rlks>_<azlks>.mli.par <rlks> <azlks>
grep range_samples <ref>.mli.par
grep azimuth_lines <ref>.mli.par
ras_dB <ref>.mli <width> ...
SLC_corners <ref>.mli.par
```

验收条件：参考 MLI、参数文件、宽高审计和 BMP 浏览图存在。

### 04 DEM Lookup

执行专家 DEM 链：

```bash
dem_import <dem>.tif SRTM.dem SRTM.dem.par ...
fill_gaps SRTM.dem <dem_width> SRTM_dem_fill
gc_map2 <ref>.mli.par SRTM.dem.par SRTM_dem_fill <ref>_seg.dem_par <ref>_seg.dem <ref>.lt ...
pixel_area <ref>.mli.par <ref>_seg.dem_par <ref>_seg.dem <ref>.lt ...
create_diff_par <ref>.mli.par - <ref>.diff_par 1 0
offset_pwrm <ref>.gamma0 <ref>.mli <ref>.diff_par ...
offset_fitm <ref>.offs <ref>.snr <ref>.diff_par ...
gc_map_fine <ref>.lt <dem_width> <ref>.diff_par <ref>.lt_fine 1
geocode <ref>.lt_fine <ref>_seg.dem <dem_width> <ref>.hgt <mli_width> <mli_lines>
geocode_back <ref>.mli <mli_width> <ref>.lt_fine <ref>.geo <dem_width> <dem_lines> 5 0
```

验收条件：`<ref>.lt_fine`、`<ref>.hgt`、`<ref>_seg.dem_par` 存在；DEM 覆盖必须包含完整 stack bbox。

### 05 Coreg Prep

执行：

```bash
cp SLC/dates rslc_prep/dates
cp <ref>.slc <ref>.rslc
cp <ref>.slc.par <ref>.rslc.par
```

验收条件：参考 RSLC 和 `rslc_tab` 初始化完成。

### 06 Coregister Scenes

非参考景逐景执行：

```bash
create_offset <ref>.rslc.par <date>.slc.par <ref>_<date>.off 1
init_offset_orbit <ref>.rslc.par <date>.slc.par <ref>_<date>.off
init_offset <ref>.rslc <date>.slc <ref>.rslc.par <date>.slc.par <ref>_<date>.off <rlks> <azlks>
offset_pwr <ref>.rslc <date>.slc <ref>.rslc.par <date>.slc.par <ref>_<date>.off ...
offset_fit <ref>_<date>.offs <ref>_<date>.snr <ref>_<date>.off ...
SLC_interp <date>.slc <ref>.rslc.par <date>.slc.par <ref>_<date>.off <date>.rslc <date>.rslc.par
```

验收条件：每景 `.rslc/.rslc.par/.off` 存在，`rslc_tab` 行数等于场景数。

### 07 RMLI Average

执行：

```bash
mk_mli_all rslc_tab . <rlks> <azlks> 1 1.0 0.4 mli.ave
grep range_samples mli.ave.par
grep azimuth_lines mli.ave.par
ras_dB mli.ave <width> ...
```

验收条件：`mli.ave/mli.ave.par/mli.ave.bmp` 存在。

### 08 Diff Network

执行：

```bash
base_calc rslc_tab <ref>.rslc.par bprep_file itab 1 1 <bmin> <bmax> <tmin> <tmax> -
base_plot rslc_tab <ref>.rslc.par itab bprep_file 1
mk_diff_2d rslc_tab itab 0 <ref>.hgt - mli.ave mli_dir . <rlks> <azlks> 3 1 1 0 -u
```

验收条件：`itab`、`bprep_file`、每对 `.diff/.diff.bmp` 存在。

### 09 Filter Unwrap

执行：

```bash
mk_adf_2d rslc_tab itab mli.ave . 5 0.6 32 8 -u
ave_image cc.list <width> mean.cc
rascc_mask mean.cc - <width> 1 1 - 1 1 <threshold>
mk_unw_2d rslc_tab itab mli.ave . <threshold> 0 1 1 1 1 <r_seed> <a_seed> 1 -u
mk_unw_2d rslc_tab itab mli.ave . - - 1 1 1 1 <r_seed> <a_seed> 1 mean.cc_mask.bmp -u
```

验收条件：每对 `.adf.diff/.adf.cc/.adf.unw` 存在，`mean.cc_mask.bmp` 存在。

### 10 Detrend ATM

每对执行：

```bash
create_diff_par <pair>.off <pair>.off <pair>.diff_par 0 0
quad_fit <pair>.adf.unw <pair>.diff_par 5 5 - - 3 <pair>.unw_linear
quad_sub <pair>.adf.unw <pair>.diff_par <pair>.unw_sub_linear 0 0
atm_mod_2d <pair>.unw_sub_linear <ref>.hgt <pair>.adf.cc <pair>.diff_par - 0 <pair>.a0 <pair>.a1 ...
fill_gaps <pair>.a0 <model_width> <pair>.a0_fill ...
fill_gaps <pair>.a1 <model_width> <pair>.a1_fill ...
atm_sim_2d <pair>.diff_par <ref>.hgt <pair>.a0_fill <pair>.a1_fill <pair>.atm_model
sub_phase <pair>.unw_sub_linear <pair>.atm_model <pair>.diff_par <pair>.unw.atmsub 0
```

验收条件：`unw_atmsub_tab` 行数等于 `itab` 行数。

### 11 SBAS Inversion

执行：

```bash
mb unw_atmsub_tab RMLI_tab itab - itab_ts ras/diff1 1 diff1.sigma_ts 1 - <r_ref> <a_ref> 15 15 0.0 mli.ave.par
real_to_cpx - <pair>.unw.atmsub <pair>.unw.atmsub.cpx <width> 1
unw_model <pair>.unw.atmsub.cpx <pair>.unw.atmsub_sim <pair>.unw.atmsub_1 <width> <r_ref> <a_ref>
mb unw.atmsub_1_tab RMLI_tab itab - itab_ts ras/diff2 1 diff2.sigma_ts 0 - <r_ref> <a_ref> 15 15 0.0 mli.ave.par
mb final_unw_tab RMLI_tab itab - itab_ts ras/diff 0 diff.sigma_ts 0 - <r_ref> <a_ref> 15 15 0.5 mli.ave.par
```

验收条件：`ras/diff*.tab`、`diff.sigma_ts`、`itab_ts` 存在。

### 12 Outputs Points

执行：

```bash
replace_values diff.sigma_ts 0.5 0.0 diff.sigma_ts.masked <width> 1 2 0
mask_data ras/diff_<date> <width> ras/diff_<date>.masked diff.sigma_ts.masked.bmp 0
dispmap ras/<date>.disp.phase - mli.ave.par - ras/<date>.disp 0 0
ts_rate disp.TS_tab RMLI_tab itab_ts - los_def_rate los_def_const los_def_sigma 0
geocode_back los_def_rate <width> <ref>.lt_fine geo_los_def_rate <dem_width> <dem_lines> 5 0
data2geotiff <ref>_seg.dem_par geo_los_def_rate 2 geo_los_def_rate.tif
disp_prt_2d disp_geo.TS_tab RMLI_tab itab_ts - 3 disp_point.txt - geo_los_def_rate geo_diff.sigma_ts items.txt disp_tab.txt 3 1 0
```

验收条件：`geo_los_def_rate.tif` 和点时序输出来自专家输出链。

## 工程改造路线

1. 更新 stage plan：默认 `lt1_gamma_sbas` 的工具清单改为专家命令，删除 `SLC_coreg.py/gc_map1/SLC_diff_intf/mcf/单轮 mb` 作为默认阶段描述。
2. 更新专家步骤状态：`implementation_status` 不再使用 `implemented_bridge`，只有 `implemented`、`planned`、`blocked`。
3. 重写 `_materialize_workflow_scripts`：生成 12 个专家脚本，禁止复制旧桥接脚本作为专家步骤。
4. 增加命令审计：从每个脚本提取命令，和专家步骤允许命令集比对；发现旧错误命令或缺少核心命令时，任务不能进入完成状态。
5. 修正 DEM 选择：DEM 必须覆盖完整 stack bbox；只覆盖中心点不能通过。
6. 重构各阶段执行：`prepare/execute_coregistration`、`prepare/execute_rdc_dem`、`prepare/execute_interferograms`、`prepare/execute_ipta_timeseries`、`prepare/execute_publish_products` 逐步改为调用对应专家脚本和专家输出路径。
7. 修正完成判定：`WORKFLOW_COMPLETED` 必须同时满足专家脚本全部完成、命令审计通过、关键输出来自专家链路。
8. 标记历史产物：旧错误链路产物不能被 catalog 当作有效 SBAS 成果。

## 本轮编码边界

本轮先完成默认模式的结构性改造：

- 文档化正确流程和验收条件。
- 修改 stage plan 和 manifest 口径。
- 生成专家 12 步脚本，不再复制旧桥接脚本。
- 增加命令审计，阻断旧错误命令链。
- 修正 DEM 覆盖判定。

后续继续把每个 `execute_*` 阶段从旧阶段脚本迁移到 12 步专家脚本。迁移过程中只允许使用专家文档命令；任何替代命令必须显式标记为未启用，不能进入生产完成状态。
