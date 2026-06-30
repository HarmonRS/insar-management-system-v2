# 结果提取与用户权限审计（2026-06-30）

## 1. 审计范围

本次审计聚焦“结果提取”相关入口和用户权限边界，覆盖：

- 前端结果提取工作台：`frontend/src/ResultExtractionPanel.jsx`
- D-InSAR 结果管理页：`frontend/src/DinsarProductsPanel.jsx`
- D-InSAR 结果导出接口：`POST /api/dinsar-results/export`
- D-InSAR 生产结果提取与登记接口：`POST /api/idl/extract-disp`
- 用户与权限模型：`auth_users.role`、全局认证守卫、用户管理页

本次文档只记录审计结论和后续设计约束，不包含代码修复。

## 2. 当前实现事实

### 2.1 两条“提取”链路

当前系统里“结果提取”实际包含两种不同语义：

1. **生产结果入库**
   - 前端入口：`DinsarProductsPanel.jsx`
   - 后端入口：`POST /api/idl/extract-disp`
   - 后台任务：`EXTRACT_DINSAR_PRODUCTS`
   - 作用：从生产目录提取 D-InSAR 位移结果，发布成标准结果包，并重建结果 catalog。

2. **成果交付导出**
   - 前端入口：`ResultExtractionPanel.jsx`
   - 后端入口：`POST /api/dinsar-results/export`
   - 作用：从已经登记的 D-InSAR catalog 中选择成果，复制到服务器指定交付目录。

这两条链路目前在产品文案上都叫“提取”，容易让用户混淆“入库”和“交付”。

### 2.2 当前接入状态

`ResultExtractionPanel.jsx` 中的通道状态：

| 通道 | 当前状态 | 说明 |
| --- | --- | --- |
| D-InSAR 结果 | 已接入 | 支持查询已登记结果并导出到服务器目录 |
| SBAS-InSAR 结果 | 半接入 | 可读取目录样例，但统一提取接口未实现 |
| LT-1 正射结果 | 占位 | 单景/正射结果 catalog 与导出链路未完成 |
| Sentinel-1 正射结果 | 占位 | 生产和导出链路未完成 |
| GF3 SARscape `_geo` | 占位 | 登记/标准化思路存在，统一导出接口未完成 |

## 3. 当前权限模型

系统当前只有两类角色：

- `admin`
- `viewer`

定义位置：`backend/app/auth_service.py`

全局认证守卫位于 `backend/app/routers/dependencies.py`：

- `GET / HEAD / OPTIONS` 默认视为只读操作，登录用户可访问。
- 除少数显式安全 POST 外，非只读请求要求 `admin`。
- 非管理员执行写操作会被拒绝，返回 `403 Read-only account cannot perform this operation.`

前端在 `App.jsx` 中把非管理员账号映射为 `readOnly`：

- viewer 可以浏览结果、查看任务、查看 catalog。
- viewer 不能提交生产、扫描、提取、导出、删除、修改。
- admin 拥有所有写权限，包括生产、扫描、结果入库、结果导出、用户管理和运维配置。

后端没有依赖前端按钮禁用来保护写操作。`/api/idl/extract-disp` 和 `/api/dinsar-results/export` 都显式要求 `admin`，这一点是正确的。

## 4. 审计发现

### P1：成果交付权限与系统管理员权限耦合过重

当前只有 `admin` 能执行成果导出，但 `admin` 同时拥有用户管理、系统配置、生产扫描、删除记录等高权限。

从业务职责看，成果交付导出不应天然等同于系统管理员权限。后续应拆出更细的权限，例如：

- `operator`：可提交生产任务、结果入库、目录重建。
- `exporter`：可导出已登记成果到受控交付目录。
- `viewer`：只读浏览、预览、查询。
- `admin`：用户管理、系统配置、根目录维护、许可证和高风险运维。

### P1：结果导出是同步请求，存在 504 风险

`POST /api/dinsar-results/export` 在请求线程内执行文件复制，最多允许 500 个结果 ID。成果文件较大或目标目录较慢时，容易再次触发前端或 Nginx 超时。

后续应改成后台任务：

- 接口只创建任务并返回 `task_id`。
- 文件复制由 worker 执行。
- 前端通过任务中心/结果提取工作台展示进度、成功数、失败数和目标目录。

### P1：生产结果入库缺少显式操作审计

`/api/dinsar-results/export` 已写入 `dinsar_results_exported` 审计日志。

`/api/idl/extract-disp` 当前会创建系统任务，但缺少独立的操作审计记录。它会改变结果 catalog，应记录：

- 操作用户
- 源生产目录
- 目标发布目录
- 创建的 `task_id` / `job_id`
- 完成后的 processed/copied/failed/published/registered 数量

### P2：页面命名和工作流边界不清

当前“D-InSAR 结果提取与登记”和“结果提取工作台”容易混淆。

建议命名：

- “生产结果入库”：从生产目录提取并登记为系统 catalog。
- “成果交付导出”：从已登记 catalog 选择成果并复制到交付目录。

这两个动作应该放在同一结果管理域下，但用不同分区和不同权限提示。

### P2：占位通道需要降低可执行暗示

SBAS、LT-1 正射、Sentinel-1 正射、GF3 `_geo` 目前不应被呈现成可执行导出能力。

建议 UI 明确显示：

- `已接入`
- `目录可查，导出未接入`
- `规划中`
- `不可执行`

并隐藏或禁用导出按钮，避免用户误以为功能已经上线。

### P2：导出目录策略需要产品化

当前后端已有 `_validate_export_path()` 和 `ALLOWED_EXPORT_DIRS` 约束能力，但前端仍允许用户输入服务器绝对路径。

后续建议：

- 普通业务用户不输入任意服务器路径。
- 管理员在系统配置中维护“交付目录白名单”。
- 结果导出页只让用户选择白名单目录和子任务名。
- 审计记录保存最终解析后的服务器路径。

### P3：部分前端文案存在历史编码损坏

`DinsarProductsPanel.jsx`、`ResultExtractionPanel.jsx`、`UserAdminPanel.jsx` 等文件存在局部中文乱码。功能不一定受影响，但会降低维护性和产品可信度。

建议后续单独做一次 UTF-8 文案修复，不与权限重构混在同一次提交中。

## 5. 建议目标模型

### 5.1 功能分区

结果管理应拆成三个清晰分区：

1. **产品目录**
   - 查看已登记成果。
   - 预览、筛选、查看详情。
   - viewer 可访问。

2. **生产结果入库**
   - 从生产结果根目录扫描、提取、发布、登记。
   - operator/admin 可执行。

3. **成果交付导出**
   - 从 catalog 选择成果，导出到受控交付目录。
   - exporter/operator/admin 可执行。

### 5.2 权限矩阵建议

| 操作 | viewer | exporter | operator | admin |
| --- | --- | --- | --- | --- |
| 查看结果 catalog | yes | yes | yes | yes |
| 查看预览与详情 | yes | yes | yes | yes |
| 生产结果入库 | no | no | yes | yes |
| 目录重建/发布 | no | no | yes | yes |
| 成果交付导出 | no | yes | yes | yes |
| 生产任务提交 | no | no | yes | yes |
| 用户管理 | no | no | no | yes |
| 根目录/许可证/运维配置 | no | no | no | yes |

实现上可以先保留 `role` 字段，扩展角色枚举；长期可引入权限位表，避免角色继续膨胀。

## 6. 推荐实施顺序

### 阶段 1：修正产品语义和审计

- 页面文案区分“生产结果入库”和“成果交付导出”。
- `/api/idl/extract-disp` 增加操作审计。
- 结果提取工作台明确标注未接入通道。
- 修复相关页面乱码文案。

### 阶段 2：导出任务化

- 新增 `EXPORT_DINSAR_RESULTS` 后台任务类型。
- `/api/dinsar-results/export` 改为返回 `task_id`。
- 前端展示导出任务进度和失败明细。
- 导出结果保留 task log 和 audit log。

### 阶段 3：权限细分

- 扩展角色：`viewer/exporter/operator/admin`。
- 用户管理页支持新角色说明。
- 后端增加能力级依赖，例如 `require_capability("result.export")`。
- 所有高风险写操作按 capability 而不是只按 admin 判断。

### 阶段 4：交付目录白名单产品化

- 将 `ALLOWED_EXPORT_DIRS` 从环境变量能力升级为系统配置/受控根目录。
- 前端从白名单选择交付根目录。
- 用户只输入子目录名或交付批次名。

## 7. 验收标准

完成上述改造后，应满足：

1. viewer 能看结果，不能导出、不能入库。
2. exporter 能导出已登记成果，但不能提交生产、不能用户管理。
3. operator 能生产、入库、导出，但不能用户管理和系统配置。
4. admin 保留全部权限。
5. 所有入库和导出动作都有 task log 和 audit log。
6. 大批量导出不再产生 HTTP 504。
7. 未实现通道在 UI 上不会被误认为可执行功能。

