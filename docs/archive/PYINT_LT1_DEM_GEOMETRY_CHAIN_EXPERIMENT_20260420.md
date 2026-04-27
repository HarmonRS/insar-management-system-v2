# PyINT LT-1 DEM 几何链定位实验

**日期**: 2026-04-20  
**状态**: 实验设计  
**目标问题**: 验证 LT-1 在 PyINT/Gamma 中 `coreg/init_offsetm` 失败，是否由 DEM 本体问题引起，还是由 DEM 参与的几何映射链条引起

## 1. 结论先行

这轮实验不再直接回答“轨道是不是问题”，而是专门回答下面这个问题:

> `init_offsetm` 报错里的大量 `zero values in MLI1 image patch`，究竟是 DEM 文件本身有问题，还是 `DEM -> rdc_trans -> geocode -> mli0` 这一条几何映射链条有问题。

当前更值得优先验证的是:

1. `HGTSIM` 是否本身就存在异常空洞或覆盖错误
2. `lt0` 是否把参考图映射到了错误位置
3. `mli0` 的中心 `512 x 512` patch 是否天然就是大面积 0
4. `mli0` 与 `Samp` 的有效重叠区是否根本不在图像中心

## 2. 已知事实

当前 `coreg_gamma.py` 的关键顺序是:

- `rdc_trans`
- `geocode`
- `create_diff_par`
- `init_offsetm mli0 Samp diff0 1 1`

也就是说，`init_offsetm` 当前比较的是:

- `MLI1 = mli0`
- `MLI2 = Samp`

而不是直接比较 DEM。

已完成的 A/C/D 轨道实验说明:

1. 只改 state vector，`init_offsetm` 的 zero-count 会下降
2. 但下降后仍然失败
3. `ORB_filt_spline.py` 已经基本认可重写后的轨道

这说明:

- 轨道确实影响几何
- 但当前失败不太像“只有轨道问题”
- DEM 相关的几何映射链条值得单独定位

## 3. 三类假设

### H1: DEM 本体问题

DEM 文件本身有问题，例如:

- 覆盖范围不对
- 裁剪窗口不对
- sidecar / 投影信息不对
- 高程值大面积异常或空洞

若 H1 成立，则更换 DEM 源后应显著改变 `HGTSIM` 和 `mli0` 的空洞模式。

### H2: DEM 几何链问题

DEM 文件本身可用，但 LT-1 导入几何、轨道或 `generate_rdc_dem` 中间步骤有问题，导致:

- `rdc_trans` 查找表偏移
- `geocode` 后的 `mli0` 被映射到错误位置
- `mli0` 中心 patch 与 `Samp` 根本不重叠

若 H2 成立，则即使更换 DEM，本质失败形态也可能不变；但改变轨道或几何参数时，`mli0` 的零值分布会跟着变化。

### H3: patch 选取问题

`mli0` 与 `Samp` 不是完全不重叠，而是图像中心不是有效重叠区。

若 H3 成立，则:

- 全图并非都坏
- 换 `rpos/azpos` 后，`init_offsetm` 可能在其他 patch 上能工作

## 4. 实验原则

这轮实验尽量不跑整条生产链，只盯 `init_offsetm` 之前的中间产物。

固定不变:

- 同一批 3 景 LT-1 数据
- 同一个 master: `20230726`
- 同一套 looks 参数
- 同一份 PyINT/Gamma 环境

允许变化:

- DEM 来源
- 是否启用精轨重写
- `init_offsetm` 的 patch 位置

优先观测对象:

- `HGTSIM`
- `lt0`
- `mli0`
- `Samp`
- `diff0`

## 5. 分层实验设计

### Layer 1: 不改 DEM，先看中间产物

目的: 判断当前 DEM 链条到底在哪一步开始“空掉”。

#### 组 L1-A: 基线组

使用当前已经失败的 case，导出并统计:

- `HGTSIM`
- `lt0`
- `mli0`
- `Samp`

对每个文件都做:

1. 全图零值比例
2. 中心 `512 x 512` patch 零值比例
3. 中心 patch 的最小值、最大值、均值
4. 快速可视化图

判定意义:

- 如果 `HGTSIM` 自身就明显异常，优先怀疑 DEM 或 `generate_rdc_dem`
- 如果 `HGTSIM` 正常而 `mli0` 异常，优先怀疑 `lt0/geocode`
- 如果 `mli0` 正常但中心 patch 不在有效重叠区，优先怀疑 patch 选取

#### 组 L1-B: 精轨重写对照组

复用已经跑过的精轨重写 case，只比较:

- `HGTSIM`
- `mli0`
- `Samp`

判定意义:

- 如果只改轨道，`mli0` 的零值分布就跟着变，说明问题不在 DEM 文件本体
- 如果 `HGTSIM` 也明显变化，说明 DEM 映射结果强依赖 `.slc.par` 几何

### Layer 2: 改 DEM 源，固定几何

目的: 判断 DEM 文件本身是否是主因。

#### 组 L2-A: 当前 DEM

使用当前 `prepared_dem_source`，作为基线。

#### 组 L2-B: 替代 DEM

推荐优先选一个同区域、同分辨率级别、不同来源的 DEM，例如:

- 已有的另一份系统 DEM
- 或成功 ENVI/IDL 任务中使用过的 DEM

要求:

- 不改 `.slc.par`
- 不改轨道
- 只重跑 `makedem_pyint -> generate_rdc_dem`

判定意义:

- 如果换 DEM 后 `HGTSIM/mli0` 明显改善，DEM 本体有嫌疑
- 如果几乎不变，DEM 本体不是主因

#### 组 L2-C: 合成平坦 DEM

用一个覆盖相同区域、常数高程的测试 DEM。

这组不是为了出正确结果，而是为了测试:

- 失败是否强依赖真实地形起伏
- 还是只要进入几何映射链就已经错位

判定意义:

- 如果平坦 DEM 仍在同一位置失败，说明不是高程细节导致
- 如果平坦 DEM 反而明显改善，则真实 DEM 参与的映射可能存在投影或裁剪问题

### Layer 3: 不改 DEM，扫描 patch 位置

目的: 判断中心 patch 是否只是选错了地方。

方法:

- 保持 `mli0`、`Samp`、`diff0` 不变
- 只改变 `init_offsetm` 的:
  - `rpos`
  - `azpos`
- 在图像中心周围做稀疏网格扫描

推荐:

- 先做 `5 x 5` 或 `7 x 7` 网格
- patch 大小仍保持默认 `512`

每个点记录:

- 是否报 zero-patch 错误
- zero-count
- 是否能进入下一步

判定意义:

- 如果某些位置能通过，说明不是整幅图都坏，而是中心 patch 选取不对
- 如果所有位置都报大面积 0，更像几何链整体错位

## 6. 关键观测指标

### DEM 相关

1. `HGTSIM` 是否生成成功
2. `HGTSIM` 的全图与中心 patch 零值比例
3. `HGTSIM` 是否存在明显空洞、条带或边界错切

### 几何映射相关

1. `lt0` 是否可用
2. `mli0` 的全图与中心 patch 零值比例
3. `mli0` 与 `Samp` 的有效像元重叠比例
4. `mli0` 与 `Samp` 是否在中心 patch 上具有相似纹理

### `init_offsetm` 相关

1. 是否仍失败在同一位置
2. zero-count 是否显著下降
3. 换 patch 位置后是否存在可工作区域

## 7. 推荐输出物

每轮实验至少产出:

- 中间产物清单
- 每个文件的统计 JSON
- 快速可视化 PNG/TIF
- 一张对照表

推荐的统计字段:

```json
{
  "case": "L1-A_baseline",
  "file": "mli0",
  "width": 0,
  "lines": 0,
  "global_zero_ratio": 0.0,
  "center_patch_zero_ratio": 0.0,
  "center_patch_size": 512,
  "nonzero_overlap_with_samp": 0.0
}
```

## 8. 判定标准

### 支持“DEM 本体问题”的证据

1. 更换 DEM 后，`HGTSIM` 和 `mli0` 的空洞模式大幅变化
2. 同一套几何下，只有某一份 DEM 会触发大面积零值
3. 合成平坦 DEM 能显著改善中心 patch

### 支持“DEM 几何链问题”的证据

1. `HGTSIM` 看起来基本正常，但 `mli0` 大面积为 0
2. 只改轨道或 `.slc.par`，`mli0` 零值分布就发生变化
3. 换 DEM 后失败模式基本不变

### 支持“patch 选取问题”的证据

1. 中心 patch 失败，但偏移后的 patch 可以工作
2. `mli0` 与 `Samp` 在全图上存在局部重叠区，只是中心不对

## 9. 推荐的最小闭环

不建议一上来就换很多 DEM。最小闭环应按这个顺序:

1. 先做 Layer 1
   - 对现有基线 case 和精轨重写 case 提取 `HGTSIM/lt0/mli0/Samp` 统计和快视图
2. 再做 Layer 3
   - 扫描 `init_offsetm` 的 `rpos/azpos`
3. 只有在 Layer 1/3 仍无法判断时，再做 Layer 2 换 DEM

原因:

- 这能先区分“DEM 文件坏了”与“中心 patch 选错了”
- 也能避免过早把问题全部甩给 DEM

## 10. 推荐下一步

推荐直接执行两个子实验:

1. 中间产物审计实验
   - 把 A 组和 C 组的 `HGTSIM/lt0/mli0/Samp` 取出来做零值统计和快视图
2. `init_offsetm` patch 扫描实验
   - 在同一 case 上只扫描 `rpos/azpos`

如果这两步做完后发现:

- `mli0` 全图都坏，再优先查 `generate_rdc_dem` 和 LT-1 导入几何
- 只有中心 patch 坏，再优先查 patch 选取
- 换 DEM 才有明显改善，再回到 DEM 本体问题

这轮实验的目的不是立刻修好 `coreg`，而是把“DEM 文件问题”和“DEM 几何链问题”从概念判断变成可测的证据链。
