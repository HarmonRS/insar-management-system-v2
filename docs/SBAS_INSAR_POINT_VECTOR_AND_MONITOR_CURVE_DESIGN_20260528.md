# SBAS-InSAR 点矢量导出与多监测点曲线设计

## 背景

当前 Gamma SBAS 专家路径已经产出 LOS 形变速率、LOS sigma、RGB 预览和单个自动监测点曲线。论文和报告中常见的表达方式不是只展示一个自动点，而是以 LOS 速率栅格为主图，并配合若干代表点的时序曲线、质量图和统计说明。

专家文档第十二步“结果输出、地理编码与点位时序”给出的标准路径包括：

- `ts_rate` 计算平均形变速率；
- `rasdt_pwr` 生成速率预览图；
- `geocode_back` 地理编码速率结果；
- `data2geotiff` 输出 GeoTIFF；
- `disp_prt_2d` 根据 `disp_point.txt` 输出点位时序。

专家文档没有要求把所有有效像元直接矢量化。全量点矢量属于发布产物扩展，不改变 Gamma/SBAS 计算链路。

## 目标

1. 保持 GeoTIFF 作为可信主产品。
2. 新增全量有效像元点 GeoJSON.gz，供用户下载后在 QGIS、ArcGIS、Python 或精细制图流程中使用。
3. 前端不渲染全量点，只展示文件、点数、字段和下载入口。
4. 默认自动监测点从 1 个扩展为多个代表点，便于结果页展示多条时序曲线。

## 非目标

- 不把全量点 GeoJSON 作为前端地图图层渲染。
- 不用点矢量替代 LOS 速率 GeoTIFF。
- 不把自动点解释为专家确认点、业务监测网或最终工程控制点。

## 点矢量产品

输出目录：

```text
publish/vectors/
  los_rate_points.geojson.gz
  los_rate_points_summary.json
```

点定义：

- 来源：地理编码后的 `los_rate_toward_mm_per_year.tif`、`los_rate_away_mm_per_year.tif`、`los_sigma_mm_per_year.tif`。
- 一个有效像元中心点对应一个 GeoJSON Feature。
- 有效条件：速率、sigma 为有限数值，且不是 NoData/0 掩膜值。

字段：

```text
run_id
row
col
lon
lat
los_rate_toward_mm_per_year
los_rate_away_mm_per_year
los_sigma_mm_per_year
date_start
date_end
reference_date
admin_province
admin_city
```

summary 字段：

```text
schema
generated_at
ready
feature_count
output_geojson_gz
fields
source_geotiffs
date_start
date_end
reference_date
los_convention
```

前端展示：

- 点数；
- 文件大小；
- 字段说明；
- 下载按钮。

## 多监测点曲线

默认自动点建议为 5 个：

```text
P1 auto_away_high_rate_low_sigma
P2 auto_toward_high_rate_low_sigma
P3 auto_abs_high_rate_low_sigma
P4 auto_stable_low_sigma
P5 auto_center_valid
```

选择原则：

- 排除边缘区域；
- 只使用有效像元；
- sigma 越低越优先；
- 高形变点用于展示明显形变信号；
- 稳定点用于对比；
- 中心点用于空间代表性；
- 点之间设置最小距离，避免扎堆。

手动点：

- 仍保留 `manual_lonlat` 模式；
- 当用户或后续点位管理页面提供点位时，按手动点优先；
- 自动点仅作为无手动点时的默认代表点。

前端展示：

- 保留每个点的 PNG/CSV/metadata 下载；
- 结果页可展示多张点位曲线预览；
- 后续再实现同一坐标轴上的多曲线叠加。

## 生产链路位置

点矢量和多监测点都放在专家路径第十二步之后：

1. Gamma 输出速率、sigma、GeoTIFF；
2. 生成点矢量 GeoJSON.gz；
3. 提取多个监测点时序；
4. catalog 自动登记产物；
5. 前端展示下载和曲线预览。

这样不会改变核心 SBAS 计算过程，只扩展发布和结果管理层。

## 风险与约束

- GeoJSON 体积会随范围快速增大，所以必须 gzip 压缩，前端不得加载。
- 大范围任务后续应增加抽稀点矢量、CSV/Parquet 或 GeoPackage/FlatGeobuf 导出。
- 自动点只适合作为快速检查和报告初稿候选点，正式报告应支持用户指定点、导入点位或专家确认点位。
