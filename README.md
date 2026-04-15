# InSAR 管理系统 v2 - 技术文档


## 文档导航

### 📖 用户文档
- [部署指南](#部署指南) - 系统安装、配置、启动
- [配置说明](#配置说明) - `.env` 配置项详解
- [功能说明](#功能说明) - 各模块功能介绍

### 🔧 开发文档
- [架构设计](#架构设计) - 系统架构和技术栈
- [开发规范](#开发规范) - 代码规范和最佳实践
- [API 文档](#api-文档) - RESTful API 接口

### 🔒 安全文档
- [安全审计](#安全审计) - 安全问题和修复记录
- [部署安全](#部署安全) - 生产环境安全配置

---

## 部署指南

### 系统要求

**硬件要求**：
- CPU：4 核及以上
- 内存：16GB 及以上
- 硬盘：500GB 及以上（SSD 推荐）

**软件要求**：
- 操作系统：Windows 10/11 或 Windows Server 2019+
- Python：3.9+ (推荐使用 Conda 环境)
- PostgreSQL：14+ (含 PostGIS 扩展)
- Nginx：1.20+
- IDL/ENVI：8.8+ / 5.6+ (可选，用于 D-InSAR 处理)
- Ollama：最新版 (可选，用于 AI 诊断)

### 快速开始

#### 1. 环境准备

```bash
# 创建 Conda 环境
conda create -n InSAR python=3.9
conda activate InSAR

# 安装依赖
pip install -r requirements.txt
```

#### 2. 数据库配置

```bash
# 创建数据库
createdb -U postgres insar_management

# 启用 PostGIS 扩展（系统会自动执行）
```

#### 3. 配置文件

复制 `.env.example` 为 `.env`，修改以下配置：

```bash
# 数据库连接
DATABASE_URL=postgresql+asyncpg://postgres:your_password@localhost:5432/insar_management

# 数据目录（根据实际环境修改）
MONITOR_RADAR_DIRS=\\\\server\\share\\RadarData
MONITOR_ORBIT_DIR=\\\\server\\share\\Orbit
MONITOR_DINSAR_DIRS=\\\\server\\share\\DinsarResults

# IDL/ENVI 路径（如果使用 D-InSAR 功能）
IDL_EXECUTABLE=C:\\Program Files\\Harris\\ENVI56\\IDL88\\bin\\bin.x86_64\\idl.exe
IDL_DINSAR_DEM_BASE_FILE=D:\\SRTM30m\\SRTMDEM_RSP_SARscape

# 服务端口
PORT=8000
```

#### 4. 启动系统

**方式 1：使用启动脚本（推荐）**
```bash
# Windows
start_system.bat
```

**方式 2：手动启动**
```bash
# 启动后端
python run_backend.py

# 启动 Nginx（另一个终端）
nginx.exe

# 启动 Job Worker（另一个终端）
python -m backend.app.job_worker
```

#### 5. 访问系统

打开浏览器访问：`http://localhost`

默认管理员账号：
- 用户名：`admin`
- 密码：`.env` 中的 `INIT_ADMIN_PASSWORD`

---

## 配置说明

### 核心配置

#### 数据库配置

```bash
# PostgreSQL 连接字符串
DATABASE_URL=postgresql+asyncpg://用户名:密码@主机:端口/数据库名
```

#### 数据目录配置

```bash
# 雷达数据监控目录（支持 UNC 路径，多个路径用逗号分隔）
MONITOR_RADAR_DIRS=\\\\server1\\share\\data,\\\\server2\\share\\data

# 轨道数据目录
MONITOR_ORBIT_DIR=\\\\server\\share\\orbit

# D-InSAR 结果目录
MONITOR_DINSAR_DIRS=\\\\server\\share\\results

# 水体监测结果目录
WATER_RESULTS_DIR=\\\\server\\share\\water
```

#### 服务配置

```bash
# 后端服务端口
PORT=8000

# 后端绑定地址（127.0.0.1 仅本地，0.0.0.0 允许外部访问）
BACKEND_BIND_HOST=127.0.0.1

# Ollama 服务地址（用于 AI 诊断）
OLLAMA_BASE_URL=http://127.0.0.1:11434

# Nginx 健康检查地址
NGINX_HEALTH_URL=http://127.0.0.1/
```

#### PROJ 数据库配置

```bash
# PROJ 数据库路径（用于地理空间坐标转换）
# 留空则自动使用 pyproj 的 PROJ 数据库（推荐）
# PROJ_LIB=C:\\path\\to\\proj\\data
```

**重要提示**：
- PostgreSQL PostGIS 的 PROJ 数据库版本较旧（v2），可能导致坐标转换错误
- 推荐使用 pyproj 的 PROJ 数据库（v9.2+）
- 详见：[PROJ 配置指南](#proj-配置指南)

#### IDL/ENVI 配置

```bash
# IDL 可执行文件路径
IDL_EXECUTABLE=C:\\Program Files\\Harris\\ENVI56\\IDL88\\bin\\bin.x86_64\\idl.exe

# ENVI Workbench 路径
IDL_WORKBENCH_PATH=C:\\Program Files\\Harris\\ENVI56\\IDL88\\bin\\bin.x86_64\\idlde.exe

# DEM 文件路径（D-InSAR 处理必需）
IDL_DINSAR_DEM_BASE_FILE=D:\\SRTM30m\\SRTMDEM_RSP_SARscape

# 工作目录
IDL_WORKER_RUNTIME_DIR=Z:\\Code\\Insar_management_system_v2\\backend\\runtime\\idl_worker

# 超时配置
IDL_WORKER_DEFAULT_TIMEOUT_SECONDS=14400
IDL_WORKER_MAX_TIMEOUT_SECONDS=43200
```

#### 解包配置

```bash
# 压缩包源目录（支持 UNC 路径）
UNPACK_SOURCE_DIRS=\\\\server\\share\\archives

# 解包目标目录
INSAR_STORAGE_DIRS=\\\\server\\share\\storage

# 最小剩余空间（GB）
UNPACK_MIN_DISK_SPACE_GB=50

# 扫描压缩包时的并发数
UNPACK_SCAN_WORKERS=4

# 多压缩包并行解包数（单包仍为单线程）
UNPACK_EXTRACT_WORKERS=4

# 解包后是否删除压缩包
UNPACK_DELETE_ARCHIVE=true
```

#### 解包任务说明

- 前端“解包”按钮和 `POST /api/unpack/run` 都会创建后台任务，实际执行依赖 `run_worker.py`。
- 后端当前加载的解包实现为 `scripts/unpack_archives_parallel.py`；旧的 `scripts/unpack_archives.py` 保留作历史实现参考，不再作为默认入口。
- 当前并发模型为“扫描并发 + 多压缩包并行解包”。单个 `.tar.gz` 仍按单包单线程处理，不会把一个压缩包拆成多核并行。
- `UNPACK_SCAN_WORKERS` 控制源目录扫描并发，适合目录层级深、来源路径多的场景。
- `UNPACK_EXTRACT_WORKERS` 控制同时解包的压缩包数量。建议先从 `4` 起步，根据源盘/目标盘吞吐逐步调整到 `6` 或 `8`；如果压缩包和目标目录都在同一台 NAS，上限通常受 I/O 而不是 CPU 限制。
- 并行解包会在任务内部做目标盘空间预留，避免多个压缩包同时判断“空间足够”后把盘写满。
- 运行日志与进度文件位于 `logs/tasks/unpacker/`；并行实现使用 `unpacker_parallel_YYYYMMDD.*` 文件名。

### 部署场景配置

#### 场景 1：本地开发

```bash
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/insar_management
BACKEND_BIND_HOST=127.0.0.1
PORT=8000
```

#### 场景 2：Docker 容器

```bash
DATABASE_URL=postgresql+asyncpg://postgres:password@db:5432/insar_management
BACKEND_BIND_HOST=0.0.0.0
PORT=8000
```

#### 场景 3：远程 Ollama

```bash
OLLAMA_BASE_URL=http://192.168.1.100:11434
```

#### 场景 4：客户现场

```bash
# 根据客户环境修改所有路径和地址
DATABASE_URL=postgresql+asyncpg://user:pass@192.168.1.10:5432/insar
MONITOR_RADAR_DIRS=D:\\InSAR_Data\\Radar
MONITOR_ORBIT_DIR=D:\\InSAR_Data\\Orbit
MONITOR_DINSAR_DIRS=D:\\InSAR_Data\\Results
IDL_EXECUTABLE=C:\\Program Files\\Harris\\ENVI56\\IDL88\\bin\\bin.x86_64\\idl.exe
IDL_DINSAR_DEM_BASE_FILE=D:\\DEM\\SRTM30m\\SRTMDEM_RSP_SARscape
BACKEND_BIND_HOST=127.0.0.1
PORT=8080
```

---

## 功能说明

### 数据管理

#### 雷达数据管理
- 自动扫描网络共享目录
- 解析影像元数据（时间、位置、极化方式）
- 生成预览缩略图
- 空间范围可视化

#### 轨道数据管理
- 轨道文件自动关联
- 时间窗口匹配

#### D-InSAR 结果管理
- 结果自动入库
- 形变图可视化
- 统计分析

### D-InSAR 处理

#### 自动化处理流程
1. 影像导入（SARscape Import）
2. 影像配准（Coregistration）
3. 干涉处理（Interferogram Generation）
4. 相位滤波（Goldstein Filter）
5. 相位解缠（Phase Unwrapping）
6. 相位转位移（Phase to Displacement）
7. 地理编码（Geocoding）

#### 批量处理
- 支持多对影像批量处理
- 进度实时显示
- 失败自动重试

### 水体监测

#### 单景预处理
- 多视处理（Multilooking）
- 地理编码（Geocoding）
- 辐射定标

#### 洪涝检测
- 两景配对
- 洪涝分类
- MRF 精化（可选）

### AI 诊断

#### 多模态分析
- 支持 Ollama 本地部署
- 推荐模型：Qwen2-VL、LLaVA、MiniCPM-V
- 自动分析形变图
- 生成诊断报告

#### Prompt 模板
- Quick：快速分析
- Standard：标准分析（推荐）
- Detailed：详细分析

### 隐患点管理

- Shapefile 导入
- 空间查询
- 与影像范围关联

### 系统管理

#### 用户管理
- 管理员/只读用户
- 权限控制
- 审计日志

#### 运维自检
- 数据库健康检查
- IDL/ENVI 状态检查
- Ollama 状态检查
- Nginx 状态检查
- 一致性检测

#### 日志管理
- 统一日志目录
- 日志查看
- 日志删除（仅管理员）

---

## 架构设计

### 技术栈

**后端**：
- FastAPI：Web 框架
- SQLAlchemy：ORM
- PostgreSQL + PostGIS：数据库
- asyncpg：异步数据库驱动
- httpx：HTTP 客户端
- Pydantic：数据验证

**前端**：
- React：UI 框架
- Leaflet：地图组件
- Chart.js：图表组件
- Axios：HTTP 客户端

**地理空间**：
- GDAL/OGR：地理数据处理
- pyproj：坐标转换
- Shapely：几何运算
- Rasterio：栅格数据处理

**IDL/ENVI**：
- envipyengine：Python 调用 ENVI
- SARscape：SAR 数据处理

**AI**：
- Ollama：本地 LLM 部署
- 多模态模型：Qwen2-VL、LLaVA、MiniCPM-V

### 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                        Nginx                             │
│                  (反向代理 + 静态文件)                    │
└────────────┬────────────────────────────────────────────┘
             │
             ├─────────────────────────────────────────────┐
             │                                             │
┌────────────▼──────────┐                    ┌────────────▼──────────┐
│   FastAPI Backend     │                    │   React Frontend      │
│   (RESTful API)       │                    │   (SPA)               │
└────────────┬──────────┘                    └───────────────────────┘
             │
             ├─────────────────────────────────────────────┐
             │                                             │
┌────────────▼──────────┐                    ┌────────────▼──────────┐
│  PostgreSQL + PostGIS │                    │   Job Worker          │
│  (数据存储)            │                    │   (后台任务)           │
└───────────────────────┘                    └────────────┬──────────┘
                                                          │
                                             ┌────────────▼──────────┐
                                             │   IDL/ENVI            │
                                             │   (D-InSAR 处理)      │
                                             └───────────────────────┘
```

### 数据流

#### 雷达数据扫描流程
```
用户触发扫描 → FastAPI → 扫描网络目录 → 解析元数据 →
生成缩略图 → 入库 → 返回结果
```

#### D-InSAR 处理流程
```
用户提交任务 → FastAPI → 创建 SystemTask → Job Worker →
调用 IDL/ENVI → 执行 SARscape 流程 → 更新进度 →
结果入库 → 通知前端
```

#### AI 诊断流程
```
用户创建诊断 → FastAPI → 创建 SystemTask → Job Worker →
加载影像 → 查询隐患点 → 调用 Ollama → 解析结果 →
保存报告 → 通知前端
```

---

## 开发规范

### 代码规范

#### Python
- 遵循 PEP 8
- 使用类型注解
- 函数/类添加 docstring
- 异步函数使用 `async/await`

#### JavaScript/React
- 使用 ES6+ 语法
- 组件使用函数式组件 + Hooks
- 使用 PropTypes 或 TypeScript

### Git 规范

#### 提交信息格式
```
<type>(<scope>): <subject>

<body>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

**type**：
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `refactor`: 代码重构
- `test`: 测试相关
- `chore`: 构建/工具相关

**scope**：
- `backend`: 后端
- `frontend`: 前端
- `db`: 数据库
- `config`: 配置
- `docs`: 文档

### 安全规范

1. **不允许硬编码**
   - 所有路径、IP、端口必须通过 `.env` 配置
   - 不允许在代码中硬编码敏感信息

2. **输入验证**
   - 所有用户输入必须验证
   - 防止 SQL 注入、XSS、路径遍历

3. **权限控制**
   - 敏感操作仅管理员可执行
   - API 端点添加权限检查

4. **日志脱敏**
   - 日志中不记录密码、Token
   - 敏感信息使用 `***` 替代

---

## PROJ 配置指南

### 问题背景

PostgreSQL PostGIS 扩展自带 PROJ 数据库 v2（旧版本），而 GDAL/pyproj 期望 PROJ v3+（新版本）。版本不匹配会导致：
- 坐标转换警告
- 坐标转换精度下降
- 某些坐标系不支持

### 解决方案

系统会自动使用 pyproj 的 PROJ 数据库（v9.2+），无需手动配置。

启动后端时，会看到：
```
[*] Configuring PROJ database...
[*] Auto-detected PROJ_LIB: C:\\Users\\...\\pyproj\\proj_dir\\share\\proj
[*] PROJ version: 9.2.0
```

### 手动配置（可选）

如果需要使用自定义 PROJ 数据库，在 `.env` 中设置：

```bash
PROJ_LIB=C:\\path\\to\\proj\\data
```

### 验证配置

运行诊断脚本：
```bash
conda activate InSAR
python scripts/check_proj.py
```

---

## 安全审计

### 审计记录

#### 2026-03-12：当前有效安全审计记录
- 已区分“开发机阶段可接受暴露”和“上线前必须修复的问题”
- 当前结论以 `docs/SECURITY_AUDIT_2026-03-12.md` 为准
- 详见：`docs/SECURITY_AUDIT_2026-03-12.md`

#### 历史审计归档
- 2026-03-04 安全审计：`docs/archive/SECURITY_AUDIT_2026-03-04.md`
- 2026-03-05 硬编码审计：`docs/archive/HARDCODE_AUDIT_2026-03-05.md`
- 其他过程性安全文档已统一整理到：`docs/archive/`

### 安全配置

#### 生产环境建议

1. **数据库安全**
   ```bash
   # 使用强密码
   DATABASE_URL=postgresql+asyncpg://user:strong_password@localhost:5432/insar

   # 限制数据库访问
   # 在 PostgreSQL pg_hba.conf 中配置
   ```

2. **CORS 配置**
   ```bash
   # 生产环境使用明确的域名
   CORS_ORIGINS=https://insar.yourdomain.com
   ```

3. **Cookie 安全**
   ```bash
   # HTTPS 环境启用 Secure 标志
   AUTH_COOKIE_SECURE=true
   ```

4. **登录限流**
   ```bash
   # 防止暴力破解
   AUTH_LOGIN_MAX_FAILURES=5
   AUTH_LOGIN_WINDOW_SECONDS=300
   AUTH_LOGIN_LOCK_SECONDS=600
   ```

5. **Nginx 安全头**
   ```nginx
   # 在 nginx.conf 中添加
   add_header X-Frame-Options "SAMEORIGIN";
   add_header X-Content-Type-Options "nosniff";
   add_header X-XSS-Protection "1; mode=block";
   add_header Content-Security-Policy "default-src 'self'";
   ```

---

## 部署检查清单

### 部署前检查

- [ ] 所有配置项在 `.env` 中设置
- [ ] 数据库连接测试通过
- [ ] 数据目录路径正确且可访问
- [ ] IDL/ENVI 路径正确（如果使用）
- [ ] DEM 文件存在（如果使用 D-InSAR）
- [ ] Ollama 服务运行（如果使用 AI 诊断）
- [ ] Nginx 配置正确
- [ ] 防火墙规则配置
- [ ] CORS 配置为明确域名
- [ ] Cookie Secure 标志启用（HTTPS）
- [ ] 管理员密码已修改

### 部署后验证

- [ ] 访问前端页面正常
- [ ] 管理员登录成功
- [ ] 运维自检全部通过
- [ ] 雷达数据扫描正常
- [ ] D-InSAR 处理测试通过（如果使用）
- [ ] AI 诊断测试通过（如果使用）
- [ ] 日志记录正常
- [ ] 性能测试通过

---

## 常见问题

### Q1: 启动时报 "数据库连接失败"

**A**: 检查：
1. PostgreSQL 服务是否运行
2. `.env` 中的 `DATABASE_URL` 是否正确
3. 数据库是否已创建
4. 用户名密码是否正确

### Q2: PROJ 警告重复出现

**A**:
1. 检查启动日志是否显示 `Auto-detected PROJ_LIB`
2. 运行 `python scripts/check_proj.py` 诊断
3. 如果问题持续，在 `.env` 中手动设置 `PROJ_LIB`

### Q3: IDL/ENVI 任务失败

**A**: 检查：
1. IDL_EXECUTABLE 路径是否正确
2. IDL_DINSAR_DEM_BASE_FILE 是否存在
3. 查看任务日志：`logs/tasks/envi/`
4. 检查 ENVI 许可证是否有效

### Q4: Ollama 连接失败

**A**: 检查：
1. Ollama 服务是否运行
2. `.env` 中的 `OLLAMA_BASE_URL` 是否正确
3. 防火墙是否阻止连接
4. 运行 `curl http://127.0.0.1:11434/api/tags` 测试

### Q5: 前端无法访问后端 API

**A**: 检查：
1. Nginx 是否运行
2. Nginx 配置中的 proxy_pass 是否正确
3. 后端服务是否运行
4. 防火墙规则

### Q6: 配对候选缓存已重建，但执行配对时仍然显示“候选 0，入选 0”

**A**: 先区分“缓存候选总数”和“本次查询候选数”。
1. 全量重建配对缓存只负责重建候选池，缓存里的总候选数不等于当前表单参数下的查询结果。
2. 正式入口已迁移到“生产规划 -> 配对规划”。“修复配对基础”会自动选择增量修复或全量重建，“强制全量重建”会重算全部候选池。
3. 如果缓存里有候选，但本次查询仍为 0，优先检查：
   - 当前是否仍勾选“仅使用有精轨数据的影像”
   - 时间基线、空间基线、重叠率阈值是否过严
   - AOI 或主从时间池是否把候选全部过滤掉
   - 卫星筛选是否与当前库中实际存在的卫星一致
4. 当前前端会在打开配对弹窗时自动清理数据库中已不存在的卫星筛选，避免界面只显示 A 星、请求却仍残留旧的 B 星筛选。
5. 如果需要底层状态，请在“运维自检”里查看配对系统状态；如果需要执行修复，请回到“生产规划 -> 配对规划”操作。

---
