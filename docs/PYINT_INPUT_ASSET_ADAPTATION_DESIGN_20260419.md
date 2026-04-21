# PyINT 输入资产适配设计

**日期**: 2026-04-19  
**状态**: 总体设计  
**范围**: `Task_*` 路径适配、PyINT DEM 管理、LT-1 精密轨道治理、Gamma 配对前置条件、运维自检与前端入口

## 1. 结论

本次设计的核心结论如下：

1. 用户侧继续沿用现有的 `Task_*` 输入模式，不要求手工准备原生 PyINT 项目目录，也不允许直接把任意外部路径当作长期运行依赖。
2. 需要在现有 `pyint_engine -> run_lt1_pyint_pipeline.py` 之间补一层“输入资产适配层”，把 `Task_*`、DEM、精密轨道统一解析为系统托管的运行输入。
3. DEM 可以在一期做到“系统托管且真实参与计算”，推荐优先走“本地 FABDEM/DEM 瓦片源 + PyINT 本地生成 DEM 产物”的方案，而不是直接复用 ISCE2 的 `.wgs84` 成品 DEM。
4. LT-1 精密轨道在当前 PyINT 原生 LT-1 导入链路里，还没有现成的“接入系统轨道池并直接参与计算”的钩子。一期先做“治理级校验 + 按任务解析 + 随跑记录 + 可选准入阻断”，二期再补“真正参与 PyINT/Gamma 计算”的桥接。
5. 一期不必改数据库结构，先把输入资产记录写入 `.dinsar_run.json`、`pyint_run_summary.json` 和结果 manifest 的扩展摘要。二期只有在需要按 DEM/轨道版本检索历史时才改库，并且必须走现有数据库自维护迁移机制。
6. 前端主入口应放在现有 D-InSAR 生产面板的 PyINT 引擎区域；运维自检面板只保留状态摘要，不再堆叠新的操作区。

## 2. 现状与缺口

### 2.1 已经具备的部分

- `PyINT` 代码已经收编到仓库内 `third_party/PyINT`，不再依赖仓库外绝对路径。
- 当前后端已经支持：
  - `root_dir` 为单个任务目录，或为包含多个 `Task_*` 子目录的父目录
  - 对每个任务递归发现 `master/`、`slave/` 下的 `LT1*.tar.gz`
  - 自动生成 `ifgram_list.txt`
  - 自动生成 PyINT template
  - 在 `backend/runtime/pyint_work` 下构造 PyINT 工作区并调用 `pyintApp.py`
- 当前系统已有成型的精轨治理链路：
  - `MONITOR_ORBIT_DIR` 作为源目录
  - `ORBIT_POOL_ENVI` 作为 LT-1 `.txt` 精轨池
  - `ORBIT_POOL_ISCE2` 作为 ISCE2 `.xml` 精轨池
  - `orbit_converter.py` 已支持同步、修复、隔离和一致性检查
- 当前系统已有成型的健康检查和目录治理链路：
  - `health_service.py`
  - `root_registry_service.py`
  - 结果目录扫描和 manifest catalog

### 2.2 目前还没有解决的部分

- 当前 PyINT 集成只解决了“`Task_*` 到 PyINT 工作区”的映射，没有解决“系统托管 DEM / 系统托管精轨资产如何进入 PyINT”。
- 当前 `PYINT_DEM_ROOT` 只是 PyINT 的运行目录或缓存目录，不等价于“系统已经为本次任务解析好了 DEM 输入策略”。
- 当前 LT-1 PyINT 导入脚本并没有直接消费系统里的 `LT1*_GpsData_GAS_C_YYYYMMDD.txt` 精轨池。
- 当前前端也没有给 PyINT 提供“提交前资产预检/预览”的位置。

### 2.3 一个必须明确的现实约束

当前 vendored `PyINT` 的 LT-1 流程里：

- DEM 侧已有明确入口，`makedem_pyint.py` 可以走本地 `fabdem_dir` 或 OpenTopography。
- 精轨侧对 LT-1 没有现成的“使用系统 `.txt` 精轨池”的显式接口，现有 LT-1 导入脚本更接近“从压缩包和 XML 元数据生成 SLC 参数”。

因此本方案必须分两层描述精轨：

1. 治理层接入：系统知道本次任务应该使用哪份精轨，能阻断缺失任务，能把依赖记录下来。
2. 计算层接入：该精轨是否真的被 PyINT/Gamma 的 LT-1 导入过程消费。

一期只能承诺第一层，第二层需要专门桥接。

## 3. 总体方案

### 3.1 新增一层输入资产适配服务

建议在 `pyint_service.py` 旁边新增或内聚出一层输入资产适配职责，例如：

- `resolve_pyint_tasks(root_dir)`
- `resolve_pyint_dem_asset(task_context)`
- `resolve_pyint_orbit_assets(task_context)`
- `materialize_pyint_input_assets(run_context)`
- `build_pyint_input_preview(root_dir)`

其职责不是替代 PyINT，而是在系统生产语义和 PyINT 原生语义之间做转换。

### 3.2 总体执行链路

建议链路如下：

`前端生产面板 root_dir`
-> `validate_pyint_root_dir()`
-> `PyINT 输入资产适配层`
-> `每个 Task_* 解析任务身份、DEM、精轨`
-> `运行目录 materialize`
-> `run_lt1_pyint_pipeline.py`
-> `PyINT / Gamma`
-> `pyint_run_summary.json + .dinsar_run.json`
-> `结果发布 / catalog`

### 3.3 不再要求用户准备 PyINT 原生目录

用户仍然只需要提供：

- 单个 `Task_YYYYMMDD_YYYYMMDD`
- 或者一个包含多个 `Task_*` 的批次根目录

系统内部自行生成：

- `project_name`
- `template`
- `DOWNLOAD/`
- `ifgram_list.txt`
- `input_assets/`
- `native output`

这保证 PyINT 继续是“受控执行器”，不是“要求用户手工维护目录结构的第二套系统”。

## 4. 与现有 `Task_*` 路径的配合方式

### 4.1 用户输入模式

沿用当前模式，不新增新的路径输入方式：

- 模式 A：直接选一个 `Task_*`
- 模式 B：选一个包含多个 `Task_*` 的父目录

任务目录仍要求至少满足：

- `master/`
- `slave/`
- 目录下可递归发现 `LT1*.tar.gz`

可选但推荐继续保留：

- `.dinsar_pair.json`

### 4.2 任务解析规则

建议继续沿用当前逻辑，并把它明确固化为正式约束：

1. `Task_*` 是业务输入根，不是 PyINT 工作区。
2. `master/`、`slave/` 下面允许多层子目录，但最终必须能发现原始压缩包。
3. 任务身份优先从 `.dinsar_pair.json` 读取。
4. 缺失时再从任务目录名和压缩包文件名推导：
   - `task_alias`
   - `pair_key`
   - `master_date`
   - `slave_date`

### 4.3 运行期目录建议

建议把每次运行的托管结构固定为：

```text
backend/runtime/pyint_work/<pair_key>/<run_key>/
  input_assets/
    task_manifest.json
    orbits/
    dem/
  <project_name>/
    DOWNLOAD/
    ifgram_list.txt
    ...

backend/runtime/pyint_templates/<pair_key>/<run_key>/
  <project_name>.template

backend/runtime/pyint_output/<pair_key>/<run_key>/native/
  pyint_run_summary.json
  .dinsar_run.json
  ifgrams/
  ...
```

原则：

- 原始 `Task_*` 只读，不回写。
- 每次运行独立目录，避免不同 run 相互污染。
- DEM、轨道、任务解析结果要在 `input_assets/` 下留痕。

## 5. DEM 方案

### 5.1 不建议直接把 ISCE2 DEM 方案硬套给 PyINT

当前系统已有 `ISCE2_DEM_PATH`，它对应的是 ISCE2 直接消费的成品 DEM。

但当前 PyINT 的 DEM 处理逻辑更接近：

- 先根据 master SLC 范围解析 DEM 覆盖区域
- 再通过 `makedem_pyint.py`
- 结合 `fabdem_dir` 或 OpenTopography
- 在 `DEMDIR` 下生成 PyINT / Gamma 所需的 DEM 产物

因此一期不建议把 `PYINT_DEM_SOURCE` 简单绑定为 `ISCE2_DEM_PATH`。

### 5.2 推荐的 DEM 分层

建议把 PyINT 的 DEM 分成三层：

1. DEM 源
   - 本地 FABDEM/DEM 瓦片根目录
   - 或 OpenTopography 在线源
2. DEM 运行缓存
   - 即当前 `PYINT_DEM_ROOT`
3. 本次任务解析后的 DEM 产物
   - 位于 `PYINT_DEM_ROOT/<project_name>/...`
   - 被 `generate_rdc_dem.py`、`geocode_gamma.py` 等步骤消费

### 5.3 推荐配置

建议新增或明确以下配置：

```ini
PYINT_DEM_MODE=local_fabdem|opentopo
PYINT_FABDEM_ROOT=
PYINT_OPENTOPO_DEM_TYPE=SRTMGL1
PYINT_DEM_ROOT=
PYINT_DEM_STRICT=true
```

说明：

- `PYINT_DEM_MODE=local_fabdem` 为推荐默认值。
- `PYINT_FABDEM_ROOT` 指向本机统一维护的 FABDEM/DEM 瓦片目录。
- `PYINT_DEM_ROOT` 继续作为 PyINT DEM 运行缓存根。
- 若后续确实验证可直接复用某个成品 DEM，再新增单独模式，不要和一期混在一起。

### 5.4 运行时行为

当用户提交 PyINT 任务时：

1. 适配层先解析 DEM 模式。
2. 若为 `local_fabdem`：
   - 把 `fabdem_dir` 写入本次运行生成的 template
   - `DEMDIR` 指向本次受控缓存根
3. 若为 `opentopo`：
   - 只在运行时注入 API key，不把敏感值写入仓库文档或 `.env.example`
4. 运行完成后记录：
   - DEM 模式
   - DEM 源根目录
   - 生成产物目录
   - 关键 DEM 文件是否生成成功

### 5.5 DEM 与前端的关系

不建议在前端让用户手工输入单次 DEM 路径。

推荐做法是：

- 前端只展示“当前 DEM 策略”
- 例如：
  - `本地 FABDEM`
  - `OpenTopography`
  - `未配置`
- 如果 DEM 不可用，则在 PyINT 引擎区阻断提交

## 6. 精密轨道方案

### 6.1 一期目标不是“假装已经真正进计算”

当前 LT-1 PyINT 原生脚本没有明确消费系统精轨池的接口，因此一期要把目标定义准确：

- 系统必须能按任务解析 master/slave 对应的精轨文件
- 系统必须能知道精轨是否缺失
- 系统必须把这次运行实际匹配到的精轨记录下来
- 系统必须能根据策略决定“警告放行”还是“阻断提交”

但不能在未完成桥接前，对外宣称“精轨已经真实参与 LT-1 PyINT 计算”。

### 6.2 一期建议的精轨策略

建议精轨配置分为：

```ini
PYINT_ORBIT_POLICY=validate_only|require_txt|stage_txt
PYINT_ORBIT_POOL_TXT=
PYINT_RECORD_INPUT_ASSETS=true
```

默认建议：

- `PYINT_ORBIT_POOL_TXT` 为空时默认继承 `ORBIT_POOL_ENVI`
- `PYINT_ORBIT_POLICY=require_txt`

三种策略含义：

- `validate_only`
  - 找得到则记录
  - 找不到只警告
- `require_txt`
  - 找不到直接阻断运行
- `stage_txt`
  - 除了要求存在，还把匹配到的轨道文件复制或硬链接到本次运行目录

### 6.3 轨道解析规则

对每个 task，按如下顺序解析：

1. 从 `.dinsar_pair.json`、原始压缩包文件名或元数据确定：
   - 卫星 `LT1A/LT1B`
   - `master_date`
   - `slave_date`
2. 到系统轨道池中查找：
   - `LT1A_GpsData_GAS_C_YYYYMMDD.txt`
   - `LT1B_GpsData_GAS_C_YYYYMMDD.txt`
3. 分别解析 master/slave 结果
4. 形成本次运行的轨道摘要

### 6.4 与现有轨道治理链路的关系

PyINT 不应新建第二套精轨目录。

应直接复用现有治理链路：

- 源目录：`MONITOR_ORBIT_DIR`
- 运行池：`ORBIT_POOL_ENVI`
- 一致性修复：`orbit_converter.py`
- 健康检查：`health_service.py`

也就是说：

- PyINT 的精轨输入来源仍应是系统托管的轨道池
- 不是让用户每次在前端再手工填一条轨道路径

### 6.5 一期的落地方式

建议每次运行都在 `input_assets/orbits/` 下落盘一个轨道摘要，例如：

```json
{
  "policy": "require_txt",
  "pool_root": "D:\\orbit_pools\\envi",
  "master": {
    "date": "20250112",
    "satellite": "LT1A",
    "path": "D:\\orbit_pools\\envi\\LT1A\\LT1A_GpsData_GAS_C_20250112.txt",
    "staged_path": "...\\input_assets\\orbits\\LT1A_GpsData_GAS_C_20250112.txt",
    "resolved": true
  },
  "slave": {
    "date": "20250309",
    "satellite": "LT1A",
    "path": "D:\\orbit_pools\\envi\\LT1A\\LT1A_GpsData_GAS_C_20250309.txt",
    "staged_path": "...\\input_assets\\orbits\\LT1A_GpsData_GAS_C_20250309.txt",
    "resolved": true
  }
}
```

这一步先解决：

- 任务是否可跑
- 运行可追溯
- 后续桥接可复用

### 6.6 二期的“真正参与计算”桥接

如果要让系统精轨真实参与 LT-1 PyINT/Gamma 计算，建议单独做一个技术 Spike，候选方向有两个：

1. 修改或包装 PyINT 的 LT-1 导入步骤
   - 在 `down2slc_LT1.py` / `LT1_import_SLC_from_zipfiles1` 前后插入系统精轨桥接步骤
2. 在 PyINT 前增加一个 LT-1 预处理适配器
   - 先把系统精轨和原始场景解析成更稳定的中间输入
   - 再把中间输入交给 PyINT 后续流程

建议优先方向是第 1 种，因为它改动面更小。

但在明确 Gamma 对 LT-1 外部精轨的实际消费方式之前，不建议直接承诺实现周期。

## 7. Gamma 配对集成的关系

`select_pairs.py` / Gamma 精配对本身是可集成的，但它不应绕开输入资产治理。

建议关系如下：

1. 生产引擎侧先把 PyINT 的 DEM / 轨道输入治理打通。
2. Gamma 精配对继续作为“配对规划之后的精化步骤”存在。
3. 精配对任务默认继承同一套：
   - WSL 环境
   - PyINT vendored 代码
   - 轨道治理配置
4. 前端入口仍放在 `PairPlanningPanel`，不放进运维自检。

换句话说：

- “生产引擎接入”是必要前置。
- “Gamma 配对接入”是可行的，但它应复用同一套输入资产治理，而不是另起一套路径和配置。

## 8. 运行元数据、结果治理与数据库策略

### 8.1 一期不改数据库主结构

一期建议不改库，原因是：

- 当前已有 `.dinsar_run.json`
- 当前已有 `pyint_run_summary.json`
- 当前已有结果 manifest / catalog

这些已经足够承载输入资产摘要。

### 8.2 一期建议记录的内容

建议把以下信息写入运行元数据：

- `input_assets.task_source`
  - `root_dir`
  - `task_dir`
  - `archives.master[]`
  - `archives.slave[]`
- `input_assets.dem`
  - `mode`
  - `source_root`
  - `cache_root`
  - `resolved_output_dir`
  - `key_outputs`
- `input_assets.orbits`
  - `policy`
  - `pool_root`
  - `master`
  - `slave`
  - `stage_mode`

### 8.3 二期改库触发条件

只有出现以下需求时再改库：

- 需要按 DEM 版本检索历史 PyINT 结果
- 需要按轨道版本检索历史 PyINT 结果
- 需要统计“某批结果使用了哪套轨道/DEM”
- 需要把 PyINT 输入资产做成后台长期查询对象

### 8.4 如果改库，必须走现有数据库自维护机制

如果进入二期改库，必须：

1. 在 `backend/migrations/` 新增 SQL 迁移文件
2. 在 `backend/app/db_maintenance.py` 的迁移列表中登记
3. 让现有数据库自维护机制自动执行

不允许手工改表绕过现有机制。

## 9. 健康检查、接口与前端位置

### 9.1 运维自检面板只做状态，不做主操作入口

当前健康页已经比较重，因此新增内容应控制在“状态摘要”层面：

- `PyINT enabled`
- `PyINT home`
- `PyINT WSL`
- `PyINT DEM strategy`
- `PyINT orbit policy`
- `PYINT_FABDEM_ROOT` 可读
- `PYINT_ORBIT_POOL_TXT` / `ORBIT_POOL_ENVI` 可读

不建议新增：

- 手工触发 PyINT 任务按钮
- 手工填 DEM 路径
- 手工填轨道路径

### 9.2 生产页的建议位置

在 `DinsarProductionPanel` 的 PyINT 引擎区域增加“输入资产预检摘要”，展示：

- 识别到的任务数
- 无效任务数
- DEM 策略
- 轨道策略
- 已解析轨道数量
- 缺失轨道数量
- 是否允许提交

### 9.3 建议新增一个轻量预检接口

建议新增：

- `POST /dinsar-production/engines/pyint/preview-input-assets`

返回：

- 任务解析结果
- DEM 配置状态
- 轨道解析状态
- 阻断原因
- 警告列表

这样前端可以在正式提交前给出明确反馈，而不是等任务进入队列后才失败。

### 9.4 PairPlanning 页的位置

Gamma 精配对仍建议放在 `PairPlanningPanel`：

- 先显示已有粗配对网络
- 再提供 Gamma 精配对入口
- 不把它塞回健康检查页

## 10. 推荐实施顺序

### Phase 1：输入资产适配层

- 固化 `Task_*` 路径解析规则
- 新增输入资产预检模型
- 生成 `task_manifest.json`
- 生成 `input_assets/orbits/` 与 `input_assets/dem/` 目录

### Phase 2：DEM 正式接入

- 增加 `PYINT_DEM_MODE`
- 增加 `PYINT_FABDEM_ROOT`
- 在 template 生成时注入 `fabdem_dir` / `opentopo_*`
- 把 DEM 解析摘要写入 run metadata

### Phase 3：精轨治理接入

- 增加 `PYINT_ORBIT_POLICY`
- 复用 `ORBIT_POOL_ENVI`
- 运行前做 master/slave 精轨解析
- 缺失时按策略阻断
- 把轨道文件 staging 到运行目录

### Phase 4：前端预检入口

- 生产面板显示资产预检摘要
- 健康页只增加状态项

### Phase 5：精轨计算桥接 Spike

- 研究 LT-1 PyINT/Gamma 当前导入链路如何真正消费外部精轨
- 决定是补包装步骤还是补脚本修改

### Phase 6：Gamma 精配对集成

- 在配对规划页复用同一套 PyINT/Gamma 环境和资产治理策略

## 11. 最终建议

对于你提出的三个问题，建议明确回答如下：

1. `Task_*` 路径怎么配合  
   继续沿用现在的任务目录，不改用户输入方式；系统内部新增适配层把任务目录转换为 PyINT 工作区。

2. DEM 怎么处理  
   一期就做系统托管，推荐以本地 FABDEM/DEM 源目录为标准输入，由 PyINT 在受控 `DEMDIR` 下生成本次任务真正使用的 DEM 产物。

3. 精密轨道怎么处理  
   一期先接入系统精轨池做校验、阻断、记录和 staging；二期再补“真实参与 LT-1 PyINT/Gamma 计算”的桥接。当前实现还不能直接把这一步视为已经完成。

基于当前代码现状，最稳妥的方向不是“删掉 Task_* 模式重新发明一套 PyINT 路径”，而是“把 Task_* 保留为业务输入，把 DEM/精轨补成系统托管资产适配层”。

## 补充：现有 DEM 复用实现（2026-04-19）

当前代码已补充 `PYINT_DEM_MODE=prepared_file`，用于复用系统现有 DEM 资产。

- `PYINT_PREPARED_DEM_PATH` 可显式指定现有 DEM 基础文件。
- 若该值为空，运行时会按顺序回退解析 `ISCE2_DEM_PATH` 和 `IDL_DINSAR_DEM_BASE_FILE`。
- 如果目标文件同名存在 `.par`，则视为现成的 Gamma DEM，直接写入 PyINT 模板中的 `DEM=...`。
- 如果目标文件没有 `.par`，但同名存在 `.xml`、`.hdr` 或 `.vrt`，则视为系统现有源 DEM。
- 对“系统现有源 DEM”，PyINT 在 `makedem_pyint.py` 中会根据 `master` 的 `SLC_par` 覆盖范围先裁剪局部窗口，再转换为本次任务使用的 Gamma DEM。
- 这样可以复用系统已经维护的中国区或全局 DEM，不必强制切回 FABDEM 或重新在线下载。

这个实现的约束也需要明确：

- 现有源 DEM 仍必须是可被 GDAL 打开的本地文件。
- 运行环境里仍需要 `gdal_translate` 可用，因为裁剪发生在 WSL/PyINT 侧。
- 该模式的本质不是“直接把 ISCE2 DEM 原样交给 Gamma”，而是“把系统现有 DEM 当作受控源，再为每次 PyINT 任务生成 Gamma 可消费的局部 DEM 产物”。
