# SBAS-InSAR Gamma 实现与专家文档对照审阅记录

审阅日期：2026-06-03  
审阅对象：

- 专家文档：`D:\Code\Insar_management_system_v2\LT1_GAMMA_SBAS_逐命令处理流程.docx`
- 当前实现：`backend/app/services/sbas_insar_production_service.py`
- 当前入库：`backend/app/services/sbas_insar_catalog_service.py`
- 样本 Run：`backend/runtime/sbas_insar_production/runs/sbas_7537cc71c998`

## 1. 审阅结论

当前 SBAS-InSAR Gamma 实现不能视为专家文档的逐命令复刻。

它更接近“参考专家文档后形成的 Gamma/PyINT 混合生产链路”。这条链路曾跑出完整产物，但从专家文档一致性、结果验收语义和空间范围表达看，存在需要优先修正的问题。专家反馈“结果和实现路径有问题”，从代码和样本产物看是有依据的。

核心判断：

1. 生产坐标系中的 RDC/SAR 栅格本身是矩形。
2. 系统展示和入库使用的是地理坐标 bbox/GeoTIFF，而不是 SAR 坐标矩形。
3. 当前把“发现阶段影像范围”“最终地理编码外包矩形”“最终有效像元范围”混在一起，容易造成范围和质量误判。
4. 样本 Run 的 DEM 没有覆盖完整 stack bbox，但仍被标记为 ready，这是结果边缘异常和黑边风险的直接原因。

## 2. 专家文档主流程

专家文档的 LT-1 Gamma SBAS 主链路可以概括为：

```text
par_LT1_SLC
ORB_filt_spline.py
multi_look
dem_import / fill_gaps
gc_map2 / pixel_area / create_diff_par / offset_pwrm / offset_fitm / gc_map_fine / geocode
create_offset / init_offset_orbit / init_offset / offset_pwr / offset_fit / SLC_interp
mk_mli_all
base_calc / base_plot / mk_diff_2d
mk_adf_2d / ave_image / rascc_mask / mk_unw_2d
quad_fit / quad_sub / atm_mod_2d / fill_gaps / atm_sim_2d / sub_phase
mb / unw_to_cpx / unw_model / mb / mb
replace_values / mask_data / dispmap / ts_rate
geocode_back / data2geotiff / disp_prt_2d
```

专家文档强调正式运行前必须用实际数据替换日期、极化、宽度、行数、DEM 宽度、种子点、阈值和小基线阈值，并用 `grep`、`SLC_corners`、显示检查命令进行核对。

## 3. 当前实现主流程

当前系统实现的主要阶段是：

```text
01_baseline_audit.sh
02_coreg_common_ref.sh
03_prepare_rdc_dem.sh
04_diff_unwrap_common_ref.sh
05_detrend_atm.sh
05_mb_ts_rate.sh
07_publish_products.sh
08_point_timeseries.sh
```

实现入口集中在：

- `backend/app/services/sbas_insar_production_service.py`
- `backend/app/services/job_handlers.py`
- `deploy/wsl/runners/gamma_sbas_product_tools.py`

当前文档化的 stage 名称与专家文档相近，但部分脚本内部命令不是专家文档原命令序列。

## 4. 主要问题

### 4.1 DEM 允许只覆盖中心点，不强制覆盖完整 stack

严重级别：高

当前 DEM 选择逻辑允许以下任一条件成立即保留 DEM：

```python
self._bbox_contains(coverage, stack_bbox, margin_degrees=0.05)
or self._bbox_contains_point(coverage, self._stack_center(stack_manifest), margin_degrees=0.05)
```

位置：

- `backend/app/services/sbas_insar_production_service.py::_resolve_rdc_dem_source`

样本 Run `sbas_7537cc71c998` 的证据：

```json
"covers_stack_bbox": false,
"covers_stack_center": true,
"stack_bbox": {
  "min_lon": 128.7690438245,
  "min_lat": 43.7486321624,
  "max_lon": 129.6293024728,
  "max_lat": 44.3582486206
}
```

同一 Run 的 DEM coverage：

```json
"coverage": {
  "min_lon": 127.99998768,
  "max_lon": 130.99998756,
  "min_lat": 44.00000064,
  "max_lat": 46.000000560000004
}
```

也就是说，stack 南界到 `43.7486`，DEM 南界只到约 `44.0000`。这会造成南侧边缘缺失、NoData、黑边或地理编码结果范围不足。

当前 summary 仍显示：

```json
"ready": true
```

这是验收逻辑漏洞。

建议：

- DEM 选择必须强制 `covers_stack_bbox=true`，并增加安全缓冲。
- 如果 DEM 不覆盖完整 stack，应直接阻断 RDC DEM 阶段，不允许标记 ready。
- `selection_note` 不应写“covering the SBAS stack extent”，除非确实覆盖完整 stack bbox。

### 4.2 干涉和解缠命令链与专家文档不一致

严重级别：高

专家文档：

```text
mk_diff_2d
mk_adf_2d
ave_image
rascc_mask
mk_unw_2d
```

当前实现：

```text
create_offset
phase_sim_orb
SLC_diff_intf
adf
cc_wave
rascc_mask
mcf
```

位置：

- `backend/app/services/sbas_insar_production_service.py::_write_interferogram_script`

这不是简单命令名称不同，而是处理策略不同。当前链路可能可以跑通，但不能直接说“与专家逐命令流程一致”。如果专家按文档检查结果，当前实现路径会对不上。

建议：

- 保留当前混合链路时，应将 profile 标记为 `lt1_gamma_sbas_hybrid` 或 `experimental`。
- 新增严格专家链路 profile，例如 `lt1_gamma_sbas_expert_v1`，按专家文档生成 `mk_diff_2d/mk_adf_2d/mk_unw_2d` 脚本。

### 4.3 SBAS 反演缺少专家文档中的二次修正链路

严重级别：高

专家文档在第一次 `mb` 后包含：

```text
unw_to_cpx
unw_model
mb
mb
```

当前实现基本是：

```text
mb
ts_rate
```

位置：

- `backend/app/services/sbas_insar_production_service.py::_write_ipta_timeseries_script`

这会影响 2π 跳变修正、最终反演稳定性和专家验收一致性。

建议：

- 明确把第一次 `mb`、模型辅助解缠修正、第二次 `mb`、最终 `mb` 分成独立可审计步骤。
- 每次 `mb` 输出都应记录输入列表、`itab`、参考点、窗口、阈值和输出统计。

### 4.4 配准实现不是专家文档的显式逐命令配准

严重级别：中高

专家文档配准链：

```text
create_offset
init_offset_orbit
init_offset
offset_pwr
offset_fit
SLC_interp
```

当前实现调用：

```text
SLC_coreg.py --init_offset
```

位置：

- `backend/app/services/sbas_insar_production_service.py::_write_coregistration_script`

如果 `SLC_coreg.py` 内部等价，仍需要把内部日志和参数展开到系统审计里。否则专家无法按逐命令流程核对。

建议：

- 专家链路 profile 中显式生成 `create_offset/init_offset_orbit/init_offset/offset_pwr/offset_fit/SLC_interp`。
- 混合链路可以保留 `SLC_coreg.py`，但必须与专家链路区分。

### 4.5 DEM 查找表链路与专家文档不一致

严重级别：中

专家文档：

```text
dem_import
fill_gaps
gc_map2
pixel_area
create_diff_par
offset_pwrm
offset_fitm
gc_map_fine
geocode
```

当前实现：

```text
复用已有 Gamma DEM cache
replace_values
gc_map1
geocode
create_diff_par
init_offsetm
offset_pwrm
offset_fitm
gc_map_fine
geocode
```

位置：

- `backend/app/services/sbas_insar_production_service.py::_write_rdc_dem_script`

当前方式可能是工程上可行的，但与专家文档命令链不一致；同时 DEM 覆盖检查还存在高风险漏洞。

建议：

- 专家链路 profile 中按文档执行 `dem_import/gc_map2/pixel_area`。
- 混合链路继续使用 DEM cache 时，必须加强 DEM 覆盖、坐标、分辨率、NoData 验收。

### 4.6 入库和展示范围来自 stack 元数据，不是最终产品有效范围

严重级别：中

当前 geographic coverage 构造来自 stack scenes 的 metadata bbox：

- `backend/app/services/sbas_insar_production_service.py::_build_stack_geographic_coverage`

入库时使用该 coverage 写入：

- `min_lon`
- `min_lat`
- `max_lon`
- `max_lat`
- `geom`
- `coverage_polygon`

位置：

- `backend/app/services/sbas_insar_catalog_service.py::_build_product`

这意味着系统中展示的是“发现阶段影像范围”，不是最终 GeoTIFF 的真实 footprint，更不是最终有效像元 footprint。

样本 Run 的最终 GeoTIFF `los_rate_toward_mm_per_year.tif` 信息：

```text
Size is 966, 435
Origin = (128.790404333349983,44.362917266649994)
Pixel Size = (0.000833333300000,-0.000833333300000)
Upper Left  = (128.7904043, 44.3629173)
Lower Right = (129.5954043, 44.0004173)
NoData Value=0
```

而 stack bbox 是：

```json
{
  "min_lon": 128.7690438245,
  "min_lat": 43.7486321624,
  "max_lon": 129.6293024728,
  "max_lat": 44.3582486206
}
```

两者明显不同。

建议：

- 入库时从主 GeoTIFF 读取外包矩形、CRS、transform 和 NoData。
- 另行计算有效像元 footprint，或至少计算有效像元 bbox。
- 前端明确区分：
  - stack metadata footprint
  - RDC/SAR processing grid
  - geocoded raster extent
  - valid-pixel footprint

### 4.7 质量统计把 0 当成有效值

严重级别：中

当前 `_gamma_float32_stats` 使用 finite 像元作为 `valid_count`，只额外记录 `nonzero_count`。但 GeoTIFF 明确 `NoData Value=0`，因此黑边或无效像元会被 `valid_count` 掩盖。

位置：

- `backend/app/services/sbas_insar_production_service.py::_gamma_float32_stats`

样本质量统计：

```json
"pixel_count": 11480259,
"valid_count": 11480259,
"nonzero_count": 1956770
```

`nonzero_count` 只占约 17%，但 `valid_count` 却是 100%。这会误导验收。

建议：

- 对最终产品统计必须把 NoData 排除。
- 对 RDC 中间文件可以同时报告：
  - finite_count
  - nonzero_count
  - nodata_count
  - valid_pixel_ratio
  - valid_bbox
- `ready` 不应只看文件尺寸和 finite 统计。

## 5. SAR 坐标矩形问题

专家反馈“SAR 坐标应该是矩形”，需要拆成两个层次理解。

### 5.1 处理坐标

RDC/SAR 处理网格应该是规则矩形。

样本 Run 中：

```json
"reference_geometry": {
  "range_samples": 2693,
  "azimuth_lines": 4263,
  "expected_float32_bytes": 45921036
}
```

RDC 文件大小匹配 `2693 * 4263 * 4`，说明处理中间产品本身是矩形栅格。

### 5.2 展示和入库坐标

系统前端和 catalog 当前展示的是 EPSG:4326 的地理 bbox 或 scene bbox，不是 SAR 坐标矩形。

地理编码后的 GeoTIFF 是经纬度网格矩形，但有效像元可能因为 SAR 覆盖、DEM 覆盖、查找表外推、NoData 而不是满矩形。前端如果只画地理 bbox，会让用户误以为整块都有效。

### 5.3 结论

当前问题不是“RDC 文件不是矩形”，而是系统把以下内容混用了：

1. SAR/RDC 处理矩形。
2. 原始 scene metadata bbox。
3. stack 多景 bbox union/intersection。
4. 最终 GeoTIFF 地理外包矩形。
5. 最终有效像元 footprint。

建议把这些空间语义拆开保存和展示。

## 6. 建议整改路线

### 第一阶段：先修验收与范围表达

目标：不改变核心 Gamma 命令，先避免错误结果被标记为 ready。

1. DEM 必须完整覆盖 stack bbox，加缓冲；否则阻断。
2. `rdc_dem_summary.ready` 必须检查 DEM coverage。
3. `publish_product_summary` 读取主 GeoTIFF 的真实 extent、NoData、valid pixel ratio。
4. catalog 入库优先使用主 GeoTIFF extent 和 valid footprint，而不是 stack metadata bbox。
5. 前端展示拆分为“数据发现范围”和“产品有效范围”。

### 第二阶段：建立专家文档严格链路

目标：给专家可逐命令审计的生产路径。

新增 profile：

```text
lt1_gamma_sbas_expert_v1
```

特性：

- 按专家文档 12 节生成脚本。
- 每节脚本命令、输入、输出、日志与专家文档一一对应。
- 保留当前混合链路，但命名为 hybrid/experimental，不再和专家链路混称。

### 第三阶段：结果质量验收

目标：形成可解释的质量结论。

建议增加：

- DEM 覆盖检查。
- RDC 栅格尺寸检查。
- GeoTIFF extent 检查。
- NoData/valid-pixel ratio 检查。
- 相干性统计。
- 每对干涉图解缠覆盖率。
- `mb` 输入层数、有效像元、参考点窗口记录。
- LOS 速度范围和 sigma 分布阈值告警。

## 7. 当前不建议的做法

1. 不建议继续把当前链路称为“专家文档逐命令链路”。
2. 不建议只看产物文件存在和文件大小判断成功。
3. 不建议用 stack metadata bbox 代表最终产品有效范围。
4. 不建议把 0 NoData 统计为有效像元。
5. 不建议在 DEM 未覆盖完整 stack 的情况下继续标记 ready。

## 8. 后续需要专家确认的问题

1. 是否要求严格使用专家文档中的 `mk_diff_2d/mk_adf_2d/mk_unw_2d`，还是允许保留 `SLC_diff_intf/adf/mcf` 混合链路。
2. DEM 来源是否必须每次从 GeoTIFF 通过 `dem_import` 重新导入，还是允许复用 Gamma DEM cache。
3. `mb` 的三次反演和 `unw_model` 修正是否必须进入正式链路。
4. 参考点 `R_REF/A_REF`、窗口、阈值是否由系统自动选择，还是必须由专家人工确认。
5. 最终业务展示默认应展示 SAR/RDC 矩形、GeoTIFF extent，还是有效像元 footprint。

