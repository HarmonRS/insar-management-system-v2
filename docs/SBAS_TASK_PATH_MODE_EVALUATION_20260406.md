# SBAS 是否可复用现有 D-InSAR Task 路径生产模式

更新日期：2026-04-06

## 1. 结论

结论分两层：

- 可以复用“以标准输入目录作为生产入口”的模式
- 不能直接复用现有 D-InSAR 的 `Task_x/master + slave` 目录协议和 `copy-ps-stack` 实现

也就是说：

- 可以借用 D-InSAR 的生产思想
- 不能照搬 D-InSAR 的输入结构

## 2. 现有 D-InSAR 生产模式是什么

当前 D-InSAR 的 ISCE2 生产模式，本质上是：

1. 先把一对影像整理成标准任务目录
2. 每个任务目录固定包含：
   - `master/`
   - `slave/`
   - `.dinsar_pair.json`
3. 生产入口只接收一个 `root_dir`
4. 引擎自行扫描这个 `root_dir`
   - 要么它本身就是一个 task 目录
   - 要么它下面有多个 `Task_*` 子目录
5. 每个 task 目录独立产出一个 pair result

关键实现：

- `backend/app/dinsar_engines/isce2_engine.py:314-361`
  - `validate_root_dir()` 要求：
    - 单个 task 目录必须有 `master` 和 `slave`
    - 或父目录下有多个合法 `Task_*`
- `backend/app/copier.py:178-218`
  - D-InSAR 复制时会构建：
    - `<dest>/<task_alias>/master`
    - `<dest>/<task_alias>/slave`
    - `.dinsar_pair.json`
- `backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py:262-298`
  - pipeline 运行时明确按 `master` / `slave` 两景读取

所以这是一套“面向影像对”的输入协议。

## 3. 现有 PS/SBAS 选片算法能不能找出时序队列

可以，但当前更准确地说，它是：

- 一个“候选栈发现器”
- 还不是一个“生产级栈定义器”

现有实现：

- `backend/app/services/spatial_service.py:414-481`

当前逻辑会：

- 先选出与 AOI 初始重叠比例满足阈值的影像
- 按轨道方向分组
- 求每个方向的公共重叠区
- 再筛出对公共区覆盖比例满足阈值的影像
- 最终按日期排序返回

这对 SBAS 是有价值的，因为它已经能给出：

- 同轨方向
- 共享公共覆盖区
- 时间排序后的影像列表

但它当前还缺少若干生产级约束：

- 没有显式要求 `has_orbit_data = True`
- 没有显式要求相同 `imaging_mode`
- 没有显式要求相同 `polarization`
- 没有最小栈规模规则
- 没有最大时间间隔或参考影像策略
- 没有把结果固化为“一个稳定的 stack identity”

所以答案是：

- 用来找 SBAS 候选时序队列，可以
- 直接当生产入口，不够

## 4. 为什么 D-InSAR 的 task 路径模式不能原样套给 SBAS

### 4.1 D-InSAR 是二元输入，SBAS 是栈输入

D-InSAR 当前 pipeline 只解析两个角色：

- `master`
- `slave`

见：

- `backend/app/isce2_pipeline/run_lt1_dinsar_pipeline.py:262-298`

而 SBAS 至少需要：

- 一个有序场景集合
- 栈级别的公共参数
- 栈级别的参考日期
- 栈级别的输出目录约定

因此 SBAS 的输入目录协议必须是“stack contract”，不是“pair contract”。

### 4.2 当前 `copy-ps-stack` 是平铺复制，不会生成栈元数据

现有 PS 复制逻辑：

- `backend/app/copier.py:45-115`

它的行为是：

- 从 `PsTaskItemORM.file_path` 取路径
- 优先复制 `original_path + "_envi_import"`
- 否则复制原路径
- 直接平铺复制到 `dest_dir/<basename>`

它不会生成：

- 栈 manifest
- 日期排序
- 参考日期
- 轨道清单
- DEM / water mask / publish 约定

因此这套复制结果目前更像“把选中的影像搬过去”，不是“形成一个可投产的 SBAS 输入包”。

### 4.3 当前 PS 批次模型保存的信息不够

现有：

- `backend/app/models/orm.py:532-550`
- `backend/app/routers/task_batches.py:303-312`

`PsTaskItemORM` 只保存：

- `file_path`
- `satellite`
- `imaging_date`
- `polarization`
- `has_orbit_data`

没有保存：

- `orbit_direction`
- `imaging_mode`
- 稳定 `group_key`
- 公共覆盖区
- 参考影像策略

这说明 PS 批次目前还是“输入快照”，不是“可直接投产的 stack contract”。

## 5. 哪部分可以直接复用

可以直接复用的，不少。

### 5.1 生产入口模式

复用思路：

- 一个 `root_dir`
- 引擎自己扫描这个目录
- 支持“单个 stack 目录”或“父目录下多个 stack 目录”

这点可以直接仿照：

- `backend/app/dinsar_engines/isce2_engine.py:314-361`

只是把目录判定从：

- `master + slave`

换成：

- `stack_input_manifest.json` 或 `.ps_stack.json`

### 5.2 作业提交模式

可以直接复用 D-InSAR 当前的：

- router 提交任务
- `task_service.create_task()`
- `job_queue_service.create_job()`
- worker 执行
- 返回单次运行状态

### 5.3 目录驱动执行

这部分思想也可以复用：

- 输入目录是唯一入口
- engine 不依赖前端临时状态
- 只要目录契约稳定，就可以重复运行

这对 SBAS 是对的。

## 6. SBAS 推荐的“task 路径模式”

建议做一个 SBAS 版输入目录协议，而不是复用 D-InSAR 的 `Task_*` 结构。

推荐：

```text
Stack_<group_key>/
  stack_input_manifest.json
  scenes/
    20250118/
      source_scene.json
      <raw scene or link>
    20250315/
      source_scene.json
      <raw scene or link>
    20250510/
      source_scene.json
      <raw scene or link>
  inputs/
    dem/
  logs/
```

后续运行阶段再生成：

```text
Stack_<group_key>/
  stack_input_manifest.json
  scenes/
  SLC/
  stack_work/
  publish/
  logs/
```

## 7. 推荐的 `stack_input_manifest.json` 最小字段

至少应包含：

- `group_key`
- `batch_id`
- `mode`
  - `sbas`
- `direction`
- `reference_date`
- `stack_dates`
- `scene_count`
- `dem_path_windows`
- `dem_path_wsl`
- `orbit_pool_windows`
- `orbit_pool_wsl`
- `publish_dir_windows`
- `publish_dir_wsl`
- `water_mask_mode`
- `scenes[]`
  - `date`
  - `file_path`
  - `satellite`
  - `imaging_mode`
  - `polarization`
  - `orbit_direction`
  - `orbit_xml_path`
  - `has_orbit_data`

这个文件对 SBAS 的作用，等价于 D-InSAR 里的 `.dinsar_pair.json`，但它描述的是一个栈，不是一对。

## 8. 物理复制要不要照搬 D-InSAR

不建议原样照搬。

原因：

- SBAS 常常是 5 景、10 景甚至更多
- 全量复制原始影像会放大 IO 和存储成本
- 你当前原始数据已经在 WSL 可见路径下，不一定需要再复制一遍

更合理的 Phase 1 做法：

- 保留“输入目录”概念
- 但目录里优先存：
  - manifest
  - sidecar metadata
  - 规范化链接或原路径引用
- 仅在必要时物化最小运行所需文件

也就是说：

- 推荐“目录驱动”
- 不推荐“全量复制驱动”

## 9. 最终判断

如果你的问题是：

- “现有匹配算法能不能找出 SBAS 需要的时序影像队列？”

答案是：

- 可以作为候选栈发现器
- 但还需要补生产级校验和栈固化

如果你的问题是：

- “能不能沿用 D-InSAR 生产时的输入 task 路径模式？”

答案是：

- 可以沿用这个模式的思想
- 不能沿用现有 D-InSAR 的具体目录协议和复制实现

最推荐的落地方式是：

1. 保留“目录作为生产入口”
2. 为 SBAS 定义独立的 `Stack_*` 输入契约
3. 用 `stack_input_manifest.json` 替代 `.dinsar_pair.json`
4. 用 stack-aware 的 prepare/materialize 步骤替代当前 `copy-ps-stack` 平铺复制

这样既能继承现有系统的生产模式，又不会把 SBAS 错套成二景 D-InSAR。
