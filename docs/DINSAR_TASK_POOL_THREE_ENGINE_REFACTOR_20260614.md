# D-InSAR 三引擎 Task_Pool 生产链路重构设计

更新日期：2026-06-14

## 1. 结论

D-InSAR 后续生产只保留三类核心引擎：

- `sarscape`：ENVI + SARscape。
- `landsar`：LandSAR D-InSAR。
- `pyint`：Gamma / PyINT。

`isce2` 从 D-InSAR 正式生产链路退出。后端运行入口、引擎注册、任务类型、结果发布特殊分支、前端引擎选择和结果筛选都需要清理。已有 ISCE2 历史结果不在本次设计中直接删除，默认进入 legacy 只读语义：不参与新任务分发、不在默认结果管理视图中展示，只在显式历史查询或迁移脚本中处理。

三条保留链路统一走 Task_Pool 任务格式。LandSAR 和 ENVI + SARscape 只处理陆探 LT-1 数据；Gamma / PyINT 同时支持 LT-1 和 Sentinel-1。

## 2. 当前代码事实

当前系统已经具备多引擎基础，但还没有完成 Task_Pool 一致化：

- `backend/app/routers/dinsar_production.py` 暴露 D-InSAR 多引擎生产接口，当前仍接受 `sarscape / isce2 / pyint / landsar`。
- `backend/app/dinsar_engines/registry.py` 仍注册 `Isce2Engine`。
- `backend/app/services/dinsar_production_service.py` 已经按 engine/profile 生成运行项、当前指针和未完成判断，但 `_task_type_for_engine`、`_workflow_name_for_engine`、`_workflow_step_name_for_engine` 仍包含 ISCE2。
- `backend/app/services/job_handlers.py` 同时存在 SARscape、ISCE2、PyINT、LandSAR 的 job handler。
- `backend/app/dinsar_engines/landsar_engine.py` 已经围绕 LT-1 `Task_*/Input_Data` 和 `master/slave` 原始目录做输入检查。
- `backend/app/dinsar_engines/pyint_engine.py` 支持 `lt1_gamma_dinsar` 和 `s1_gamma_dinsar` profile，但未完成判断目前偏向 LT-1 profile，需要按 profile 收敛。
- `backend/app/services/result_catalog_service.py` 的结果目录、manifest、current 指针和 `result_products` 表已经能承载多引擎多次运行，但展示层仍是“一个引擎结果一条记录”。
- `backend/app/routers/task_batches.py` 的 D-InSAR batch/item 表保存了配对身份和任务状态，但没有保存或返回各引擎结果矩阵，也没有显式 Task_Pool 物理发布状态。
- `frontend/src/components/DinsarCatalogPanel.jsx` 和 `frontend/src/utils/dinsarEngines.js` 仍把 ISCE2 当作当前引擎之一，结果管理也是平铺展示。

## 3. 目标 Task_Pool 契约

D-InSAR 正式生产入口统一接受 Task_Pool 任务目录，不再把任意平铺目录作为正式生产输入。兼容导入可以保留在迁移工具或单独入口，不能作为标准链路。

推荐根路径：

```text
D:\Task_Pool\DInSAR\
  Task_<master_date>_<slave_date>[_suffix]\
    task_manifest.json
    .dinsar_pair.json
    master\
      ...
    slave\
      ...
    Input_Data\
      ...
```

目录语义：

- `task_manifest.json`：Task_Pool 任务清单，记录任务来源、配对身份、数据类型、允许引擎、各引擎结果快照和分发状态。新实现应写入；旧任务可由 `.dinsar_pair.json` 和目录结构推断。
- `.dinsar_pair.json`：现有配对侧车文件，继续作为 `pair_key`、`pair_uid`、`network_run_id`、`network_edge_id` 等身份信息的本地来源。
- `master/`、`slave/`：标准主从影像资产目录。LT-1 放陆探原始包或解包后的 xml/tif；Sentinel-1 放 SAFE/ZIP 及可解析的轨道资产引用。
- `Input_Data/`：LandSAR 运行所需的 LT-1 输入目录。可以由 `master/slave` 导入生成，但生成关系必须写入 manifest，避免后续误判源数据。

任务身份以 `pair_key` 为稳定主键，`network_edge_id` 为配对网络中的边身份，`run_key` 为一次具体生产实例。已有代码中的 `.dinsar_run.json` 继续用于单次运行元数据。

## 4. 引擎能力矩阵

| 引擎 | engine_code | 数据类型 | 输入目录 | 结果 profile |
| --- | --- | --- | --- | --- |
| ENVI + SARscape | `sarscape` | LT-1 | `Task_*/master` + `Task_*/slave` | `custom6`、`metatask` |
| LandSAR | `landsar` | LT-1 | `Task_*/Input_Data`，或从 `master/slave` 导入生成 | `lt1_dinsar` |
| Gamma / PyINT | `pyint` | LT-1 | `Task_*/master` + `Task_*/slave` | `lt1_gamma_dinsar` |
| Gamma / PyINT | `pyint` | Sentinel-1 | `Task_*/master` + `Task_*/slave`，配套轨道资产 | `s1_gamma_dinsar` |

生产入口需要按数据类型和 profile 做硬约束：

- LT-1 任务允许 `sarscape`、`landsar`、`pyint`。
- Sentinel-1 任务只允许 `pyint`。
- `sarscape` 和 `landsar` 对 Sentinel-1 返回明确不可分发状态，而不是进入运行后失败。
- `pyint` 的未完成判断必须按 profile 区分，不能只检查 `lt1_gamma_dinsar`。

## 5. 配对发布与完成状态

当前 `/task-batches/dinsar` 只保存配对条目，不知道每个 engine 是否已经产出结果。后续需要在“发布到 Task_Pool”和“查看任务池条目”两个位置补齐结果矩阵。

建议在 D-InSAR task item 响应中增加 `engine_results`：

```json
{
  "pair_key": "lt1_20250101_20250201_xxxxx",
  "task_alias": "Task_20250101_20250201",
  "task_pool_dir": "D:\\Task_Pool\\DInSAR\\Task_20250101_20250201",
  "engine_results": {
    "sarscape": {
      "allowed": true,
      "status": "missing",
      "profile_code": "custom6",
      "latest_product_id": null,
      "can_dispatch": true,
      "skip_reason": null
    },
    "landsar": {
      "allowed": true,
      "status": "ready",
      "profile_code": "lt1_dinsar",
      "latest_product_id": 123,
      "can_dispatch": false,
      "skip_reason": "result_exists"
    },
    "pyint": {
      "allowed": true,
      "status": "failed",
      "profile_code": "lt1_gamma_dinsar",
      "latest_product_id": 124,
      "can_dispatch": true,
      "skip_reason": "retry_allowed"
    }
  }
}
```

状态建议：

- `missing`：没有对应引擎/profile 的有效产品。
- `running`：已有未完成 job 或运行锁。
- `ready`：已有有效产品，默认不重复分发。
- `failed`：最近一次产品或 job 失败，可按策略允许重跑。
- `blocked`：数据类型、轨道、DEM、许可证或输入结构不满足该引擎。
- `legacy`：历史结果存在，但不参与当前三引擎分发规则。

结果矩阵优先从 `result_products` 和运行状态表实时聚合，不建议一开始复制到 batch item 表中。`task_manifest.json` 可以保存发布时快照，但界面展示应以数据库/catalog 聚合结果为准。

## 6. 结果管理重构

当前结果管理按 `result_products` 平铺展示，导致同一任务的三个引擎结果分成三条记录。目标视图应按任务/配对聚合：

```text
Task_20250101_20250201
  pair_key: lt1_20250101_20250201_xxxxx
  sarscape: ready / latest run / preview / assets
  landsar: missing
  pyint: ready / latest run / preview / assets
```

后端建议保留 `result_products` 作为原子产品表，新增聚合查询：

- `GET /dinsar-products/pairs`：按 `pair_key`、`pair_uid`、`network_edge_id` 聚合结果。
- `GET /dinsar-products/pairs/{pair_key}`：返回同一任务下三个引擎的运行历史、资产、预览和 current 指针。
- `GET /dinsar-products/{product_db_id}`：继续保留单产品详情，作为资产钻取入口。

前端结果管理调整为“一行一个任务/配对”，内部显示三个引擎状态 chip。引擎筛选不再把结果拆成多行，而是控制哪些引擎列/状态参与显示。ISCE2 默认不显示，只能通过 legacy 显式开关查看历史记录。

## 7. 中间文件删除模块

删除能力必须建立在“结果已注册成资产”之后，不能直接按目录名粗暴删除。建议新增 D-InSAR intermediate cleanup 服务，职责如下：

- 扫描某个 `pair_key`、`run_key` 或 `product_db_id` 下可清理路径。
- 校验 `manifest.json`、`execution_manifest.json`、`result_assets` 或 manifest assets 中的必要资产都存在。
- 只删除或隔离中间目录，例如 `native/`、engine work dir、临时转换目录、LandSAR 导入过程缓存、PyINT/Gamma 临时处理目录。
- 永远保留标准发布资产：`assets/`、`preview/`、`manifest.json`、`execution_manifest.json`、`.dinsar_run.json`、current 指针、Task_Pool 任务 manifest 和 `.dinsar_pair.json`。
- 支持 `dry_run`，返回将删除的路径、大小和风险原因。
- 第一阶段建议移动到 quarantine/trash，再由运维策略做永久删除。
- 删除动作写审计日志，并把清理状态写回产品摘要或独立 cleanup 表。

建议接口：

- `POST /dinsar-products/{product_db_id}/cleanup-intermediates?dry_run=true`
- `POST /dinsar-products/pairs/{pair_key}/cleanup-intermediates?dry_run=true`
- `GET /dinsar-products/{product_db_id}/cleanup-intermediates/plan`

## 8. 后端改造清单

第一阶段只做读模型和状态聚合，不改变生产执行：

- 新增三引擎常量和能力矩阵，作为后续后端和前端共同事实来源。
- 新增 D-InSAR task item engine result 聚合函数。
- 新增 result catalog pair-grouped 查询。
- 修正 PyINT 按 profile 判断完成状态。

第二阶段收紧 Task_Pool 输入：

- D-InSAR 运行入口只接受 Task_Pool 任务目录或任务目录集合。
- Pairing 发布 batch 时生成或补齐 `task_manifest.json` 与 `.dinsar_pair.json`。
- 对 LT-1 / Sentinel-1 做引擎可分发校验。
- LandSAR 的 `Input_Data` 生成关系写入 manifest。

第三阶段移除 ISCE2 正式链路：

- 删除 `Isce2Engine` 注册。
- 移除 D-InSAR submit API 中的 `isce2` 分支。
- 移除 ISCE2 job type 到新任务分发的映射。
- 移除 result catalog 新发布中的 ISCE2 特殊处理。
- 前端移除 ISCE2 引擎选项、筛选项和当前说明文案。
- 历史 ISCE2 产品标记为 legacy，只在显式历史入口展示。

第四阶段上线中间文件清理：

- 先提供 dry-run 和 quarantine。
- 加入资产完整性校验。
- 加入审计记录和恢复说明。
- 稳定后再允许永久删除。

## 9. 前端改造清单

- D-InSAR 生产面板只展示 `sarscape`、`landsar`、`pyint`。
- 数据类型为 Sentinel-1 时，只允许选择 Gamma / PyINT；LT-1 时允许三引擎。
- Batch/Task_Pool 列表展示每个任务的三引擎结果状态。
- 已有结果的任务默认显示“已有结果”，并允许按策略重跑或跳过。
- 结果管理从产品平铺改成任务聚合视图。
- 单产品详情保留，用于下载、预览和资产级操作。
- 中间文件清理入口放在产品详情或任务聚合详情内，默认先显示 dry-run 计划。

## 10. 风险与待定项

- 现有 ISCE2 历史结果是否需要迁移到 legacy catalog，还是只隐藏默认入口后保留原记录，需在实际清理前确认。
- ENVI + SARscape 的 `metatask` 是否继续作为可选 profile，还是统一收敛到 `custom6`，需要结合现有项目参数模板决定。
- Task_Pool 根路径需要明确配置项，建议新增 `DINSAR_TASK_POOL_ROOT`，默认 `D:\Task_Pool\DInSAR`。
- 中间文件清理需要先确认每个引擎的“可删目录”和“必须保留资产”清单，不能用一套规则覆盖全部。
- 前端 grouped 结果视图需要兼容旧 flat API 一段时间，避免已有页面一次性断裂。

## 2026-06-15 Task_Pool Materialize Update

Current direction:

- `TASK_POOL_ROOT` defaults to `D:\Task_Pool`.
- `DINSAR_TASK_POOL_ROOT` defaults to `D:\Task_Pool\DInSAR`.
- `SBAS_TASK_POOL_ROOT` defaults to `D:\Task_Pool\SBAS`.
- D-InSAR distribution materializes source inputs inside the task folder:
  - directory sources are copied into `master/` and `slave/`;
  - `S1_ZIP`, `LT1_ARCHIVE`, and other supported archives are extracted into `master/` and `slave/`;
  - staged orbit files go into `orbit/`.
- Engines must consume local Task_Pool paths. LT-1 and Sentinel-1 source archives are also local; UNC is not an active production source pool.
- `.dinsar_pair.json` records `source_materialization` so cleanup can distinguish copied directories, extracted archives, and staged files.

Cleanup implication:

- `master/`, `slave/`, `orbit/`, and engine `work/` folders are local materialized inputs/workspace and may be cleaned after all required results are registered.
- `publish/`, manifests, result assets, previews, and catalog metadata are preserved.
