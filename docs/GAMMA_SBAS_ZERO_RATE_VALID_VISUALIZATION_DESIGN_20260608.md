# Gamma SBAS 0 速率无效值与结果展示设计

## 当前口径

专家 Gamma SBAS 核心速率产品：

- `publish/geotiff/geo_los_def_rate.tif`
- 原始单位按专家命令解释为 `m/yr`
- 前端展示统一换算为 `mm/yr`

当前处理口径按用户确认执行：`0` 速率按无效值处理。

原因是当前 GeoTIFF 的覆盖区外和部分背景区域都可能被写为 0；在没有专家显式稳定区 mask 或质量 mask 前，把 0 解释为稳定值会把背景大量纳入统计，导致 P05/P50/P95 等摘要失真。因此本阶段先按无效值处理 0，后续若专家确认稳定区 mask，再调整。

## 有效性规则

1. 有效像元必须位于专家 RGB 覆盖区内。
2. 有效像元必须是有限值：不是 `NaN`、`Inf`、`-Inf`。
3. 有效像元必须满足 `geo_los_def_rate != 0`。
4. `0` 不参与统计、不参与纯速率图渲染，显示为透明背景。
5. GeoTIFF 元数据 `nodata=0.0` 与当前口径一致，但后端仍显式记录该规则，避免前端误解。

## 后端实现

### `_build_expert_gamma_primary_geotiff_stats`

- 使用 `masked=False` 读取速率，避免 Rasterio 隐式规则不可见。
- 使用 `geo_los_def_rate_rgb.tif` 非黑像元推断专家覆盖区。
- 有效条件：`coverage & finite & (value != 0.0)`。
- 输出字段：
  - `zero_is_valid: false`
  - `validity_rule: "expert_rgb_coverage_finite_nonzero_values"`
  - `coverage_mask_source`
  - `zero_count`
  - `nonzero_count`
  - `metadata_nodata_applied: true`

### `_build_gamma_hls_rate_preview`

- 使用同一覆盖区规则；
- `0` 速率透明；
- 非零速率按 Gamma `hls.cm` 和专家固定范围 `[-0.08, 0.08] m/yr` 着色。

## 前端展示

1. `LOS 速率纯色图` 文案说明：
   - 由 `geo_los_def_rate.tif` 派生；
   - 不叠加强度图或底图；
   - 0 速率按无效值透明处理。
2. 统计摘要显示：
   - `0 速率：按无效值处理`
   - `有效规则：专家覆盖区内有限非零值`

## 后续增强

1. 若专家提供显式有效 mask 或稳定区定义，可将本规则切换为“mask 内 0 稳定、mask 外 0 无效”。
2. 多点时序、速率直方图、剖面线和质量图应基于同一有效性规则生成。
3. 所有派生图必须标注规则来源，避免与专家原始计算产物混淆。

## 验证标准

1. 后端语法检查通过。
2. 纯速率图非透明比例应接近非零有效像元比例。
3. API 产品详情：
   - `zero_is_valid=false`
   - `validity_rule="expert_rgb_coverage_finite_nonzero_values"`
   - `valid_count == nonzero_count`
4. 前端构建通过。
