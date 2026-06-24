# 存储压力清理与治理设计

最后更新：2026-06-23

本文档记录系统后续增加“释放存储压力”能力的设计边界。当前阶段仅作为维护和设计依据，不代表已经实现清理按钮、接口或数据库表。

## 1. 背景

当前系统已经明确退出 UNC 活动生产链路，LT-1、Sentinel-1、高分三、精密轨道、DEM、Task_Pool、运行时和结果发布目录均要求走本机路径。

本机化以后，磁盘压力主要来自：

- LT-1 / Sentinel-1 源压缩包持续增长；
- WebP、预览图源缓存和雷达缩略缓存持续增长；
- D-InSAR / SBAS-InSAR 的 Task_Pool materialize 目录持续增长；
- LandSAR、ENVI/SARscape、Gamma/PyINT、IDL、WSL broker 等运行时临时目录持续增长；
- `system_tasks` / `task_logs`、生产运行日志、诊断日志等数据库记录持续增长；
- 失败任务、调试任务和隔离区残留。

清理能力必须服务于生产稳定性，不能把“释放空间”做成粗暴删除目录。系统需要先判断数据角色、数据库引用、任务状态和可重建性，再生成清理计划。

## 2. 总原则

1. 源数据不清理。
   LT-1 / Sentinel-1 源压缩包、高分三 `_geo` 原生成果、DEM、精密轨道池是生产输入或登记对象，普通存储清理不得删除。

2. 先 dry-run，后执行。
   所有清理动作必须先生成计划，列出路径、大小、数据库影响、风险等级和预计释放空间。用户确认后才执行。

3. 清理动作必须可审计。
   系统必须记录谁在什么时候按什么规则清理了哪些文件、释放了多少空间、哪些失败、哪些数据库记录被更新。

4. 优先清理可重建派生物。
   日志、过期缓存、旧版本缓存、失败任务临时目录、运行时临时目录、过期隔离区优先进入第一阶段。

5. 正式成果不走普通清理。
   `D:\production_results` 及其 catalog 注册成果不能被“一键清理”删除。成果删除或归档应走单独的结果管理流程。

6. Task_Pool 清理必须依赖生产状态。
   `D:\Task_Pool` 下 materialize 出来的生产输入理论上可从源压缩包重建，但只有在任务已结束、无活动执行、结果已登记或用户明确确认后才能清理。

7. 文件状态和数据库状态必须同步。
   如果删除了数据库引用的 WebP 缓存、运行记录、结果资产或任务日志，必须同步更新对应表，避免前端显示“可用”但文件已不存在。

## 3. 永不进入普通清理的对象

以下对象默认不可被普通“释放存储压力”功能删除：

| 对象 | 典型路径 / 配置 | 原因 |
| --- | --- | --- |
| LT-1 源压缩包 | `SOURCE_PRODUCT_DIRS` 中的 `D:\LuTan1_Image_Pool_Zip` | 源数据，是按需解包和重新生产的根 |
| Sentinel-1 源压缩包 | `SOURCE_PRODUCT_DIRS` 中的 `D:\Sentinel1_Image_Pool_ZIP` | 源数据，是按需解包和重新生产的根 |
| 高分三 `_geo` 成果池 | `GF3_SARSCAPE_NATIVE_DIRS=D:\GaoFen3_Pool\native_geo` | 本机登记对象，WebP 从这里生成 |
| 高分三 catalog | `GF3_STORAGE_DIRS=D:\GaoFen3_Pool\catalog` | 平台登记 manifest 和追踪材料 |
| LT-1 / S1 原生精轨源池 | `ORBIT_SOURCE_DIRS` | 轨道源资产 |
| LT-1 生产精轨池 | `ORBIT_POOL_ENVI` / `PYINT_ORBIT_POOL_TXT` / `GAMMA_SBAS_ORBIT_ROOTS` | ENVI、LandSAR、Gamma/PyINT 生产依赖 |
| DEM | `D:\DEM` 及相关 DEM 配置 | D-InSAR / SBAS / GF3 生产依赖 |
| 正式发布成果 | `RESULT_PUBLISH_ROOT`、`DINSAR_PRODUCT_DIR`、`TIMESERIES_PRODUCT_DIR` | 结果 catalog 管理对象，不走普通清理 |

如确需删除以上对象，必须另设“源数据归档/删除”或“成果归档/删除”专项流程，不能复用普通清理按钮。

## 4. 可清理对象分级

### 4.1 低风险：第一阶段优先实现

| 类别 | 规则 | 数据库动作 |
| --- | --- | --- |
| 任务日志 | 清理已结束任务的旧日志，保留最近 N 天或每任务最后 N 条 | 删除 `task_logs`，可保留任务摘要 |
| 已结束旧任务记录 | 只清 `COMPLETED` / `FAILED` / `CANCELLED`，不得清 `PENDING` / `RUNNING` | 删除 `system_tasks` 及日志，或仅压缩日志 |
| 无引用缓存文件 | `backend\image_cache` 下没有数据库引用、文件不存在于当前版本策略的缓存 | 文件删除即可，记录清理项 |
| 旧版本 WebP 缓存 | `RADAR_GEO_CACHE_VERSION` 已变化且数据库不再引用 | 删除文件；如仍被引用，先更新数据库 |
| 过期隔离区 | 隔离超过保留期的文件 | 删除隔离记录或更新清理项 |

### 4.2 中风险：第二阶段实现

| 类别 | 规则 | 数据库动作 |
| --- | --- | --- |
| 雷达 WebP 缓存 | 可从源压缩包或 GF3 `_geo` 重建；默认只清旧版本、孤立文件 | 若删除当前引用缓存，`radar_data.preview_cache_status` 改为 `NONE`，写入 `preview_cache_error=storage_cleanup_removed` |
| 预览图源缓存 | `radar_archive_preview_sources` 等从压缩包提取的中间缓存 | 可删除，后续扫描或预览重建 |
| 运行时临时目录 | `production_runtime`、IDL runtime、WSL jobs、PyINT work、临时 DEM 裁剪 | 仅清无活动任务、超过保留期的目录 |
| GF3 SARscape runtime | `GF3_TASK_POOL_ROOT` / `GF3_SARSCAPE_RUNTIME_DIR` | 当前本机 GF3 不生产，原则上仅清失败/过期 runtime，不清 `_geo` |

### 4.3 高风险：第三阶段谨慎实现

| 类别 | 规则 | 数据库动作 |
| --- | --- | --- |
| D-InSAR Task_Pool materialize 目录 | 任务结束、无活动执行、可由源压缩包重建、用户确认 | 更新批次/任务的 materialize 状态 |
| SBAS Task_Pool materialize 目录 | 任务结束、无活动执行、结果或失败状态明确 | 更新 SBAS 生产运行状态 |
| D-InSAR / SBAS 中间文件 | 只清已发布结果之外的中间产物 | 必须依赖 result catalog 和 run manifest |

### 4.4 不在本功能处理

- 源压缩包去重、归档、外发；
- 正式结果删除；
- DEM 版本删除；
- 精轨池删除；
- PostgreSQL VACUUM / 备份压缩；
- 洪水检测专项数据清理。

## 5. 清理计划模型

后续实现时，清理流程应分为两个动作：

1. `PLAN`
   只扫描并估算，不删除文件，不修改业务表。

2. `APPLY`
   按用户确认的计划执行，逐项记录结果，必要时同步更新数据库。

清理计划每一项至少包含：

```json
{
  "category": "radar_preview_cache",
  "action": "delete_file",
  "path": "D:\\Code\\Insar_management_system_v2\\backend\\image_cache\\radar_geo\\xxx.webp",
  "size_bytes": 123456,
  "risk_level": "low",
  "reason": "old_cache_version",
  "db_table": "radar_data",
  "db_pk": 123,
  "db_update": {
    "preview_cache_status": "NONE",
    "preview_cache_error": "storage_cleanup_removed"
  }
}
```

## 6. 建议新增数据库表

为了审计和可追溯，建议新增两张表。

### 6.1 `storage_cleanup_runs`

| 字段 | 含义 |
| --- | --- |
| `run_id` | 清理任务 ID |
| `status` | `PLANNED` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` |
| `dry_run` | 是否只生成计划 |
| `categories` | 本次涉及类别 |
| `planned_bytes` | 计划释放空间 |
| `released_bytes` | 实际释放空间 |
| `planned_count` | 计划项数量 |
| `succeeded_count` | 成功项数量 |
| `failed_count` | 失败项数量 |
| `started_at` / `ended_at` | 执行时间 |
| `operator_user_id` | 操作用户 |
| `report_json` | 汇总报告 |

### 6.2 `storage_cleanup_items`

| 字段 | 含义 |
| --- | --- |
| `run_id` | 所属清理任务 |
| `category` | 清理类别 |
| `action` | `delete_file` / `delete_dir` / `quarantine` / `delete_db_rows` / `update_db_rows` |
| `path` | 文件或目录路径 |
| `size_bytes` | 大小 |
| `risk_level` | `low` / `medium` / `high` |
| `reason` | 命中规则 |
| `db_table` / `db_pk` | 关联数据库对象 |
| `before_json` / `after_json` | 数据库变更前后摘要 |
| `quarantine_path` | 隔离路径 |
| `status` | 单项执行状态 |
| `error` | 失败原因 |

第一阶段也可以先不建表，使用 `system_tasks` + JSON 报告落地，但正式实现建议单独建表。

## 7. 路径安全规则

所有文件清理必须满足以下规则：

1. 路径必须位于白名单根目录下。
2. 禁止删除盘符根目录，例如 `D:\`。
3. 禁止删除项目根目录、数据库目录、Python 环境目录、Nginx 目录。
4. 禁止处理 UNC 路径。
5. 禁止跟随符号链接逃逸白名单根目录。
6. 删除目录前必须重新计算 resolved path 并确认仍在白名单内。
7. 默认先移动到隔离区，隔离区过期后再永久删除。
8. 单次执行应有最大删除数量和最大删除字节数上限。

建议白名单根目录来自配置和系统常量：

- `backend\image_cache`
- `TASK_POOL_ROOT`
- `DINSAR_TASK_POOL_ROOT`
- `SBAS_TASK_POOL_ROOT`
- `GF3_TASK_POOL_ROOT`
- `DATA_DISTRIBUTION_ROOT`
- `IDL_WORKER_RUNTIME_DIR`
- `SAR_ANALYSIS_WORK_ROOT`
- `WSL_BROKER_JOB_ROOT`
- `PYINT_WORK_ROOT`
- `PYINT_DEM_ROOT`
- `GAMMA_SBAS_TRIAL_ROOT`
- `RESULT_QUARANTINE_ROOT`

其中 `RESULT_PUBLISH_ROOT` 只允许扫描统计，不允许普通清理删除。

## 8. 任务互斥和运行保护

执行清理前必须检查活动任务：

- 存在 WebP 构建任务时，禁止清理 `backend\image_cache`；
- 存在资产扫描任务时，禁止清理预览图源缓存；
- 存在 D-InSAR 生产任务时，禁止清理 `DINSAR_TASK_POOL_ROOT` 和 D-InSAR runtime；
- 存在 SBAS 生产任务时，禁止清理 `SBAS_TASK_POOL_ROOT`、`GAMMA_SBAS_WORK_ROOT` 和 SBAS runtime；
- 存在 GF3 标准化或 WebP 生成任务时，禁止清理 `GF3_TASK_POOL_ROOT`；
- 禁止清理任何 `PENDING` / `RUNNING` 任务关联的目录。

后端实现应使用任务类型锁或 PostgreSQL advisory lock，避免多个清理任务并发执行。

## 9. 建议默认保留策略

以下值是初始建议，后续可放入 `.env`：

| 配置 | 建议默认值 | 含义 |
| --- | --- | --- |
| `RUNTIME_CLEANUP_TASK_LOG_RETENTION_DAYS` | 30 | 已结束任务日志保留天数 |
| `RUNTIME_CLEANUP_TASK_RECORD_RETENTION_DAYS` | 90 | 已结束任务记录保留天数 |
| `RUNTIME_CLEANUP_IMAGE_CACHE_RETENTION_DAYS` | 60 | 无引用缓存保留天数 |
| `RUNTIME_CLEANUP_RUNTIME_RETENTION_DAYS` | 30 | 运行时临时目录保留天数 |
| `RUNTIME_CLEANUP_FAILED_RUNTIME_RETENTION_DAYS` | 7 | 失败任务临时目录保留天数 |
| `RUNTIME_CLEANUP_TASK_POOL_RETENTION_DAYS` | 30 | 可重建 Task_Pool materialize 目录保留天数 |
| `RUNTIME_CLEANUP_QUARANTINE_RETENTION_DAYS` | 14 | 隔离区永久删除前保留天数 |

默认只启用低风险类别。Task_Pool 和中间文件清理应默认关闭，需要用户显式勾选。

## 10. 前端工作台设计方向

入口建议放在“运行维护 / 存储治理”，而不是放在资产扫描、生产准备或数据分发按钮旁边。

页面结构建议：

1. 存储概览
   - 按磁盘卷展示总容量、已用、剩余、压力等级；
   - 展示系统可治理目录的估算占用；
   - 单独标注“受保护源数据”和“可清理派生数据”。

2. 清理类别
   - 任务日志；
   - 图像缓存；
   - 运行时临时目录；
   - Task_Pool materialize；
   - 隔离区。

3. 清理计划
   - 预计释放空间；
   - 文件数量；
   - 数据库记录数量；
   - 风险等级；
   - 样例路径；
   - 受保护跳过项。

4. 执行与审计
   - 后台任务进度；
   - 单项失败列表；
   - 释放空间统计；
   - 可下载 JSON 报告。

界面文案必须明确区分：

- “源数据，不会删除”；
- “缓存，可重建”；
- “运行临时目录，任务结束后可清”；
- “正式成果，不在本功能删除”。

## 11. 建议接口

后续实现可采用以下接口：

```text
GET  /maintenance/storage/overview
POST /maintenance/storage-cleanup/plan
POST /maintenance/storage-cleanup/runs
GET  /maintenance/storage-cleanup/runs/{run_id}
GET  /maintenance/storage-cleanup/runs/{run_id}/items
POST /maintenance/storage-cleanup/quarantine/purge-plan
POST /maintenance/storage-cleanup/quarantine/purge
```

`plan` 接口只返回计划，不创建删除动作。`runs` 接口基于某次计划执行，并创建后台任务。

## 12. 实施阶段

### 阶段 0：文档维护

- 固化清理边界；
- 确认不清源压缩包、不清 GF3 `_geo`、不清 DEM、精轨和正式成果；
- 后续设计和编码必须引用本文档。

### 阶段 1：低风险清理

- 存储概览；
- 任务日志清理；
- 旧任务记录清理；
- 无引用缓存 dry-run；
- 清理任务审计报告。

### 阶段 2：缓存治理

- WebP 缓存计划；
- 旧版本缓存清理；
- 数据库 `preview_cache_*` 同步；
- 缓存按需重建入口。

### 阶段 3：运行时治理

- `production_runtime`、IDL、PyINT、WSL job、GF3 runtime 清理；
- 运行任务保护；
- 隔离区机制。

### 阶段 4：Task_Pool materialize 治理

- D-InSAR / SBAS Task_Pool 目录识别；
- 与生产批次、生产运行、结果 catalog 关联；
- 可重建性验证；
- 用户确认后清理或隔离。

### 阶段 5：成果归档专项

- 不纳入普通清理；
- 单独设计结果产品归档、下线、删除和恢复流程。

## 13. 验收标准

后续实现完成后，至少满足：

1. dry-run 不改文件、不改业务表；
2. 清理计划能解释每一项为什么可清；
3. 源压缩包、GF3 `_geo`、DEM、精轨池和正式成果不会出现在普通清理执行项中；
4. 删除 WebP 缓存后，数据库状态不会继续显示 `READY`；
5. 活动任务相关目录不会被清理；
6. 所有删除动作有审计记录；
7. 清理失败不会导致整批数据库状态不一致；
8. 前端能展示释放空间、失败项和跳过原因。

## 14. 与现有文档的关系

- 三类数据本机生产边界以 `THREE_SENSOR_LOCAL_PRODUCTION_CONTRACT_20260616.md` 为准。
- 源压缩包管理和按需 materialize 以 `UNC_SOURCE_ARCHIVE_AND_MATERIALIZE_DESIGN_20260615.md` 为准。
- 源压缩包完整性审计以 `SOURCE_ARCHIVE_INTEGRITY_AUDIT_20260620.md` 为准。
- D-InSAR Task_Pool 和中间文件治理参考 `DINSAR_TASK_POOL_THREE_ENGINE_REFACTOR_20260614.md`。
- 正式成果包和 result catalog 以 `PRODUCTION_RESULTS_MULTI_ENGINE_DESIGN_20260423.md` 为准。

本文档只定义存储压力治理边界，不替代源数据、生产准备、结果管理或完整性审计文档。
