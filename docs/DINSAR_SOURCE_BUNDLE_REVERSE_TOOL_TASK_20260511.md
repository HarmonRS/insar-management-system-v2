# D-InSAR 去重源数据包反向还原工具任务书

日期：2026-05-11

## 背景

本系统新增“去重源数据包”分发模式。该模式不直接生成每个干涉对的 `Task_*` 目录，而是只分发唯一源影像、唯一精密轨道文件和配对关系文件，减少外部分发时的重复复制量。

反向还原工具由任务接收方本地运行，将去重源数据包还原为传统 D-InSAR `Task_*` 目录结构。

## 输入目录结构

```text
BundleRoot/
  data/
    scene_<hash>_<source_name>/
    ...
  orbit/
    orbit_<hash>_<orbit_name>.txt
    ...
  pairs.json
  manifest.json
```

`orbit/` 可能不存在，或 `pairs.json` 内某些配对的轨道字段为空。

## 输出目录结构

```text
OutputRoot/
  Task_YYYYMMDD_YYYYMMDD/
    master/
      <master source product content>
    slave/
      <slave source product content>
    orbit/
      <master/slave orbit files, if present>
    .dinsar_pair.json
```

输出目录名称优先使用 `pairs.json` 内的 `task_alias`，若为空则使用 `task_name`，再为空则使用 `pair_id`。

## pairs.json 关键字段

```json
{
  "schema": "dinsar_source_bundle_pairs.v1",
  "exported_at": "2026-05-11T00:00:00Z",
  "pairs": [
    {
      "pair_id": "pair_0001",
      "identity_key": "uid:<scene_pair_uid>",
      "task_name": "Task_20250101_20250113",
      "task_alias": "Task_20250101_20250113",
      "master_source_path": "D:/Source/master",
      "slave_source_path": "D:/Source/slave",
      "master_scene_id": "scene_0001",
      "slave_scene_id": "scene_0002",
      "master_data": "data/scene_xxx_master",
      "slave_data": "data/scene_yyy_slave",
      "master_orbit_id": "orbit_0001",
      "slave_orbit_id": "orbit_0002",
      "master_orbit_source_path": "D:/Orbit/master.EOF",
      "slave_orbit_source_path": "D:/Orbit/slave.EOF",
      "master_orbit": "orbit/orbit_xxx.txt",
      "slave_orbit": "orbit/orbit_yyy.txt",
      "master_imaging_date": "20250101",
      "slave_imaging_date": "20250113",
      "time_baseline_days": 12
    }
  ]
}
```

## 本系统分发续跑规则

去重源数据包支持向同一个 `BundleRoot` 多次分发：

- 每次启动时先读取目标目录内已有的 `pairs.json` 和 `manifest.json`。
- 已导出的 pair 通过 `identity_key`、`scene_pair_uid/pair_uid`、`pair_key`、`network_run_id + network_edge_id`、`master/slave_source_path` 或 `master_data + slave_data` 识别。
- 开启“每次最多追加新配对”时，系统会先跳过已导出的 pair，再从剩余 pair 中取下一批追加；例如 500 个 pair 第一次限制 100，第二次同一目录仍限制 100 时，会追加第 101-200 个未导出的 pair。
- `data/` 和 `orbit/` 按源路径哈希命名，已有文件或目录在 `skip_existing` 开启时不会重复复制。
- `pairs.json` 和 `manifest.json` 采用临时文件写入后原子替换，避免中途失败留下半写 JSON。

注意：如果用户手动删除了 `pairs.json`，系统无法再根据记录判断哪些 pair 已经分发，只能根据重新生成的 `data/` 路径做源数据级去重，pair 级续跑能力会丢失。

## 还原规则

1. 读取 `pairs.json`。
2. 对每个 pair 创建目标 `Task` 目录。
3. 将 `master_data` 指向的数据复制到 `Task/master/`。
4. 将 `slave_data` 指向的数据复制到 `Task/slave/`。
5. 如 `master_orbit` / `slave_orbit` 存在，将轨道文件复制到 `Task/orbit/`。
6. 生成 `.dinsar_pair.json`，至少保留：
   - `pair_id`
   - `identity_key`
   - `task_name`
   - `task_alias`
   - `master_scene_id`
   - `slave_scene_id`
   - `master_data`
   - `slave_data`
   - `master_orbit_id`
   - `slave_orbit_id`
   - `master_orbit`
   - `slave_orbit`
   - `master_imaging_date`
   - `slave_imaging_date`
   - `time_baseline_days`
   - `restored_at`
7. 每个 Task 应采用临时目录还原，全部成功后再重命名为最终目录，避免半成品。

## 覆盖策略

工具应提供参数：

- `--skip-existing`：默认开启。若目标 `Task/master` 和 `Task/slave` 均存在且非空，则跳过。
- `--overwrite`：删除并重建已存在的目标 Task。
- `--limit N`：最多还原 N 个 pair，便于分批执行。
- `--dry-run`：只打印计划，不复制。

`--skip-existing` 与 `--overwrite` 同时出现时应报错。

## 校验要求

启动前：

- 检查 `pairs.json` 是否存在且可解析。
- 检查 `data/` 是否存在。
- 检查每个 pair 的 `master_data` / `slave_data` 是否存在。
- 轨道缺失不应阻断还原，但要记录 warning。

还原后：

- `Task/master/` 非空。
- `Task/slave/` 非空。
- `.dinsar_pair.json` 存在。

## 日志与报告

工具结束后输出 `restore_report.json`：

```json
{
  "started_at": "...",
  "finished_at": "...",
  "input_root": "...",
  "output_root": "...",
  "total_pairs": 20,
  "restored": 18,
  "skipped": 2,
  "failed": 0,
  "warnings": []
}
```

同时建议输出人类可读日志 `restore.log`。

## 建议实现

建议使用 Python 3.10+：

- `argparse` 处理命令行参数。
- `pathlib.Path` 处理路径。
- `shutil.copytree(..., dirs_exist_ok=True)` / `shutil.copy2()` 处理复制。
- Windows 下注意长路径和权限异常。

该工具不需要连接本系统数据库，也不需要调用本系统 API。
