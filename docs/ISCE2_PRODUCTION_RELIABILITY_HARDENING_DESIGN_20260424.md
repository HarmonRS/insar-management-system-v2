# ISCE2 生产可靠性加固设计 2026-04-24

## 1. 背景

当前 ISCE2 生产链路已经具备：

- 统一的 WSL runtime 入口
- 共享 conda 环境 `insar_wsl_v1`
- 结果发布到 `D:\production_results`
- 统一 catalog / 自检 / 运维面板

但这条链路仍存在一个核心问题：

系统现在更像是“子进程执行器”，还不是“结果可靠性交付器”。

也就是说，当前成功判定主要依赖：

- WSL runner 返回码是否为 `0`
- 输出目录是否存在
- 自动发布阶段是否“尝试过”

这会导致几类假成功：

1. ISCE2 子进程返回 `0`，但没有形成可发布的主产物，任务仍显示完成。
2. 历史运行只留下目录和 `.dinsar_run.json`，但主产物已损坏或缺失，“只跑未完成” 仍会跳过。
3. 运维自检用的是一套路径规则，真实运行用的是另一套路径规则，面板和实际行为可能不一致。

本设计的目标不是重写 ISCE2 算法链，而是把 ISCE2 生产从“命令执行成功”收紧到“结果可验收、可发布、可重跑、可运维”。

## 2. 本轮范围

本轮只做可靠性加固，不做算法重写。

### 纳入本轮

- ISCE2 原生产结果的验收契约
- “只跑未完成” 判定收紧
- 自动发布阶段收紧
- 健康检查与真实运行入口对齐
- 文档、自检、日志字段同步

### 暂不纳入本轮

- stripmap/SNAPHU/PICKLE 恢复链的算法级改写
- 与 ENVI/IDL 流水线逐步逐项数值对比
- Gamma 生产实现细节
- MintPy / 时序生产链的算法可靠性评估

## 3. 当前问题拆解

### 3.1 成功判定过宽

当前 `backend/app/dinsar_engines/isce2_engine.py` 中，单对任务成功主要由 `rc == 0` 决定。

现状后果：

- 只要 runner 没报错，就写入 `.dinsar_run.json`
- 只要 `output_dir` 被加入 `output_dirs`，后续任务层就进入自动发布
- 自动发布即使 `processed == 0`，任务最终仍可标记为 `COMPLETED`

这不符合“生产完成”的系统定义。

### 3.2 未完成重跑判定过宽

当前 `_has_completed_task_result()` 只看：

- 历史 `run_dir`
- `.dinsar_run.json`
- `output_dir` 目录存在

它不看：

- 主产物是否存在
- 主产物是否可读
- 该结果是否满足发布器识别规则

因此历史半成品会被误判为“已完成”。

### 3.3 健康检查与真实运行入口不一致

当前存在两套 stripmapApp 路径逻辑：

- 健康检查依赖 `ISCE2_STRIPMAP_APP`
- 真正运行时在 WSL 内通过 `import isce` 动态定位

这会导致：

- 面板报错但实际能跑
- 面板通过但实际解释器/包位置已经漂移

### 3.4 手工恢复链缺少显式边界

当前 `run_lt1_dinsar_pipeline.py` 通过复制 `PICKLE` 中文件并改写 XML 来衔接：

- `filter -> filter_high_band`
- `unwrap -> ionosphere`

这条链路短期可用，但它对 ISCE2 内部文件结构有隐含依赖。

本轮不重写它，但必须把它从“隐式成功”改成“显式验收”。

## 4. 目标状态

ISCE2 生产链路调整后，系统对“完成”的定义变为：

1. WSL runner 执行完成。
2. 原生产物通过本地验收。
3. 验收通过的产物可被统一发布器识别。
4. 自动发布成功写入标准包。
5. 自检面板能看到 runtime、native output、publish package 三层状态一致。

换句话说：

- `rc == 0` 只表示“执行成功”
- “任务完成” 必须是“执行成功 + 产物验收成功 + 发布链成功”

## 5. 具体改造方案

### 5.1 新增 ISCE2 原生产物验收层

新增一个独立验收模块，建议位置：

- `backend/app/services/isce2_result_validator.py`

职责：

- 不参与计算
- 不参与数据库写入
- 只负责判断一个 `output_dir` 是否是“可发布、可交付”的 ISCE2 原生结果

#### 输入

- `output_dir`
- `task_alias`
- `engine_code`
- `profile_code`
- `run_key`

#### 核心检查项

必选检查：

- `output_dir` 目录存在
- 至少存在一个符合发布规则的主位移产物
- 主产物文件大小大于 0
- 主产物能被 GDAL 打开
- 若存在配对的相干产品，则记录为辅助资产

建议主产物识别规则直接复用发布器当前的规则，避免两套规则分叉：

- `*_disp.tif`
- 可选 `*_coh.tif`

#### 输出结构

返回统一 `acceptance_result`：

```json
{
  "accepted": true,
  "primary_file": "D:/.../Task_xxx_disp.tif",
  "asset_files": [
    "D:/.../Task_xxx_disp.tif",
    "D:/.../Task_xxx_coh.tif"
  ],
  "issues": [],
  "metrics": {
    "primary_exists": true,
    "primary_readable": true,
    "asset_count": 2
  }
}
```

#### 运行时行为

`isce2_engine.run()` 中：

- `rc != 0` 直接失败
- `rc == 0` 后必须执行验收
- 只有 `accepted == true` 才计入 `pairs_processed`
- 只有验收通过的 `output_dir` 才进入 `output_dirs`

这样“runner 成功但没产物”的情况会被收口为失败。

### 5.2 收紧 `.dinsar_run.json` 语义

当前 `.dinsar_run.json` 只是运行元数据，需要补成“运行 + 验收”的统一侧写。

本轮不改数据库表结构，先改 sidecar 内容。

新增字段建议：

```json
{
  "run_key": "run_xxx",
  "engine_code": "isce2",
  "profile_code": "lt1_stripmap",
  "runtime_id": "isce2_runtime_v1",
  "output_dir": "D:/.../native",
  "acceptance": {
    "accepted": true,
    "accepted_at": "2026-04-24T15:00:00Z",
    "primary_file": "D:/.../Task_xxx_disp.tif",
    "asset_files": [
      "D:/.../Task_xxx_disp.tif",
      "D:/.../Task_xxx_coh.tif"
    ],
    "issues": [],
    "metrics": {
      "primary_exists": true,
      "primary_readable": true,
      "asset_count": 2
    }
  }
}
```

原则：

- `.dinsar_run.json` 不再只表示“跑过”
- 它要能表达“验收通过 / 未通过”

### 5.3 重写“只跑未完成”判定

将 `_has_completed_task_result()` 调整为 `_has_accepted_task_result()`。

新的跳过条件：

1. 历史 run 的 `.dinsar_run.json` 存在
2. `engine_code / profile_code` 匹配当前请求
3. `acceptance.accepted == true`
4. `acceptance.primary_file` 存在且可读

只有满足以上条件，才视为“已完成”。

#### 不再采用的旧判断

- 仅凭 `output_dir` 存在
- 仅凭 `.dinsar_run.json` 存在

#### 这样做的效果

- 目录空壳不会再被跳过
- 历史损坏结果会重新进入生产
- “只跑未完成” 的含义变成“只跳过已验收成功的对”

### 5.4 自动发布阶段从“尽力而为”改为“交付闭环”

当前自动发布逻辑即使 `processed == 0` 也只记日志。

本轮改为：

- 引擎层只把“已验收通过”的 `output_dir` 交给发布器
- 任务层以“验收通过数量”作为发布期望值
- 发布结果必须满足：
  - `processed == accepted_output_count`
  - `failed == 0`

若不满足，则任务整体失败。

#### 原则

- 自动发布不再是可有可无的附属步骤
- 对托管生产来说，发布成功是交付定义的一部分

#### 失败语义

若 native output 已验收通过，但标准包发布失败：

- 任务状态标记为 `FAILED`
- 保留 native output 目录
- 日志明确写出：
  - accepted count
  - processed count
  - failed count

这样后续可以修发布器或手动重建 catalog，但不会再出现“任务完成却没有结果”的误导状态。

### 5.5 健康检查与真实运行入口统一

目标：只保留一套 runtime 事实来源。

#### 调整方向

`ISCE2_STRIPMAP_APP` 不再作为运行事实来源，只保留为兼容字段。

健康检查改成：

1. 从 `wsl_runtime_registry` 读取共享 Python
2. 在对应 Python 中执行：

```python
import isce
from pathlib import Path
print(Path(isce.__file__).resolve().parent / "applications" / "stripmapApp.py")
```

3. 检查该路径是否存在
4. 将解析出的真实路径回显到自检面板

#### 自检项调整

保留：

- WSL 可用
- 共享 Python 可执行
- `import isce` 成功
- stripmapApp 动态解析成功
- runner 脚本存在

弱化：

- 直接检查 `.env` 中的 `ISCE2_STRIPMAP_APP`

#### 自检返回建议新增字段

- `resolved_stripmap_app`
- `resolved_isce_package_path`
- `python_matches_shared`
- `runtime_id`

### 5.6 数据库与自维护策略

本轮尽量不动数据库 schema。

原因：

- 当前问题核心在文件系统验收语义，不在表结构
- 用户已经明确要求兼容数据库自维护与自检
- 现阶段没有必要为了可靠性问题引入一轮 schema 变更风险

#### 本轮原则

- 不新增非必要列
- 先把验收信息写入 `.dinsar_run.json`
- 数据库继续通过现有 `result_products / result_assets / result_issues` 表表达发布结果

#### 若后续必须加列

只允许：

- nullable
- 可由 `_add_missing_columns()` 自动补齐
- 不要求清库

但这不属于本轮必须项。

### 5.7 运维自检扩展

为避免“native 有结果但 publish 不一致”的灰区，本轮建议在健康面板新增两个聚合视角。

#### 1. runtime 视角

- 共享 distro / Python / stripmapApp 动态解析是否一致

#### 2. product 视角

- 已入库产品是否存在 `native_output_dir`
- `native_output_dir` 是否仍存在
- `manifest_path` 是否存在

#### 3. 新增建议计数项

- `missing_native_output_count`
- `missing_manifest_count`
- `missing_runtime_count`

其中前两项已有基础，重点是让 ISCE2 runtime 检查结果和产品检查结果在面板里串起来。

## 6. 文件落点

### 核心代码

- `backend/app/dinsar_engines/isce2_engine.py`
- `backend/app/services/job_handlers.py`
- `backend/app/services/result_catalog_service.py`
- `backend/app/services/health_service.py`
- `backend/app/services/wsl_service.py`

### 新增模块

- `backend/app/services/isce2_result_validator.py`

### 文档与运维

- `docs/ISCE2_PRODUCTION_RELIABILITY_HARDENING_DESIGN_20260424.md`

## 7. 分阶段实施

### Phase 1: 成功判定收口

- 新增 `isce2_result_validator`
- `rc == 0` 后执行验收
- 未验收通过不写成功结果
- `.dinsar_run.json` 补 `acceptance`

### Phase 2: 重跑语义收口

- `_has_completed_task_result()` 改为验收驱动
- “只跑未完成” 只跳过已验收成功的结果

### Phase 3: 发布闭环收口

- 自动发布必须与验收通过数量对齐
- `processed == 0` 不再仅告警，改为任务失败

### Phase 4: 自检对齐

- stripmapApp 改为动态解析检查
- 面板展示 runtime 实际解析结果

### Phase 5: 后续科学性加固

不在本轮立即落地，但建议保留后续专题：

- PIKCLE 恢复链版本敏感性评估
- bbox / geoPosting 的科学性收紧
- 与 ENVI/IDL 基线链的阶段性对比

## 8. 验收标准

改造完成后，至少满足以下场景：

1. WSL runner 返回 `0`，但没有 `*_disp.tif`。
   - 结果：任务失败，不得显示完成。

2. 历史 run 目录仍在，但 `*_disp.tif` 已被删掉。
   - 结果：`只跑未完成` 不应跳过，应重新生产。

3. 健康面板显示 stripmapApp 路径。
   - 结果：必须来自共享 Python 动态解析，不再依赖旧硬编码。

4. native output 验收通过，但发布器没识别到包。
   - 结果：任务失败，日志明确指出 publish mismatch。

5. 自检面板与实际 runtime 使用的 Python / runner / runtime_id 一致。

## 9. 本轮结论

本轮实现不应该再把 ISCE2 生产理解为“跑完脚本”，而应理解为：

“执行成功 -> 原生结果验收通过 -> 标准包发布成功 -> 系统完成交付”。

先把这个闭环收紧，再谈后续算法科学性优化，顺序才是健康的。
