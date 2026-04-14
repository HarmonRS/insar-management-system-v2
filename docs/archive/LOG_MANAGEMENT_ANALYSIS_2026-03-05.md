# 系统日志管理分析与优化方案（2026-03-05）

## 当前日志分布情况

### 1. 根目录日志（❌ 不规范）

```
Z:\Code\Insar_management_system_v2\
├── unpacker_activity.log      (34KB)  - 解包活动日志
└── unpacker_log.json          (8.6KB) - 解包结构化日志
```

**来源**：`scripts/unpack_archives.py`
**问题**：
- 直接保存在项目根目录，不专业
- 与代码文件混在一起
- 不便于管理和清理
- 可能被误提交到 Git

---

### 2. IDL/ENVI 工作日志

```
backend/runtime/idl_worker/
├── 20260225_022409_dinsar_custom.log
├── 20260225_051527_dinsar_custom.log
├── 20260304_091723_import.log
├── 20260304_091854_import.log
└── dinsar_custom_progress.log
```

**来源**：ENVI 工作流执行日志
**状态**：✅ 已规范化（在 `backend/runtime/` 下）

---

### 3. SARscape 工作日志

```
.idl/sarmap/sarscape-3-6_1_0-idl_8_8/
├── hwConfig.log
└── sarscape_work/
    ├── Process.log
    ├── Process_20260226165004.log
    ├── Process_20260226210207.log
    └── Process_20260227091802.log
```

**来源**：SARscape 软件自动生成
**状态**：✅ 可接受（第三方软件日志）

---

### 4. 应用日志（缺失）

**问题**：
- ❌ 没有统一的应用日志目录
- ❌ 没有日志轮转机制
- ❌ 没有日志级别配置
- ❌ 没有结构化日志

---

## 日志用途分析

### unpacker_activity.log

**用途**：记录解包脚本的活动日志（文本格式）

**内容示例**：
```
2026-03-04 20:53:15 - INFO - 开始扫描归档文件...
2026-03-04 20:53:15 - INFO - 找到 5 个归档文件
2026-03-04 20:53:16 - INFO - 解包完成: archive1.zip
```

**问题**：
- 保存在根目录
- 无日志轮转（会无限增长）
- 无日志级别控制

---

### unpacker_log.json

**用途**：记录解包脚本的结构化日志（JSON 格式）

**内容示例**：
```json
{
  "timestamp": "2026-03-04T20:53:15",
  "level": "INFO",
  "message": "解包完成",
  "archive": "archive1.zip",
  "files_extracted": 123,
  "duration_seconds": 5.2
}
```

**问题**：
- 保存在根目录
- 与 activity.log 重复
- 无清理机制

---

## 系统中的其他日志

### 1. 数据库日志
- **位置**：PostgreSQL 数据目录
- **管理**：由 PostgreSQL 管理

### 2. Nginx 日志
- **位置**：`nginx/logs/` (如果配置了)
- **管理**：由 Nginx 管理

### 3. 任务日志
- **位置**：数据库 `system_task` 表的 `logs` 字段
- **管理**：通过 API 查询

### 4. 进度文件
- **位置**：`backend/runtime/idl_worker/job_{job_id}_progress.json`
- **管理**：任务完成后自动清理

---

## 日志管理问题

### 问题 1：日志分散

**现状**：
- 根目录：unpacker 日志
- backend/runtime/：ENVI 日志
- .idl/：SARscape 日志
- 数据库：任务日志

**影响**：
- 难以统一查看
- 难以统一清理
- 难以统一备份

---

### 问题 2：无日志轮转

**现状**：
- unpacker_activity.log 会无限增长
- ENVI 日志会累积（每次执行生成新文件）

**影响**：
- 磁盘空间浪费
- 日志文件过大影响性能

---

### 问题 3：无统一日志框架

**现状**：
- unpacker 使用自定义日志
- 后端使用 print() 输出
- 没有统一的日志级别

**影响**：
- 日志格式不一致
- 难以过滤和搜索
- 难以集成日志分析工具

---

## 优化方案

### 方案 A：统一日志目录结构（推荐）

```
Z:\Code\Insar_management_system_v2\
├── logs/                           # 统一日志目录
│   ├── app/                        # 应用日志
│   │   ├── backend.log            # 后端主日志
│   │   ├── backend.log.1          # 轮转日志
│   │   ├── backend.log.2
│   │   └── ...
│   ├── tasks/                      # 任务日志
│   │   ├── envi/                  # ENVI 工作流日志
│   │   │   ├── 20260304_091723_import.log
│   │   │   └── ...
│   │   └── unpacker/              # 解包任务日志
│   │       ├── unpacker_20260304.log
│   │       └── ...
│   ├── access/                     # 访问日志
│   │   ├── nginx_access.log
│   │   └── api_access.log
│   └── error/                      # 错误日志
│       ├── nginx_error.log
│       └── api_error.log
├── backend/
└── frontend/
```

**优点**：
- 所有日志集中管理
- 便于备份和清理
- 便于配置 .gitignore

---

### 方案 B：使用 Python logging 模块

**配置文件**：`backend/app/logging_config.py`

```python
import logging
import logging.handlers
import os
from pathlib import Path

# 日志根目录
LOG_ROOT = Path(__file__).parent.parent.parent / "logs"
LOG_ROOT.mkdir(exist_ok=True)

# 应用日志目录
APP_LOG_DIR = LOG_ROOT / "app"
APP_LOG_DIR.mkdir(exist_ok=True)

# 任务日志目录
TASK_LOG_DIR = LOG_ROOT / "tasks"
TASK_LOG_DIR.mkdir(exist_ok=True)

# 日志配置
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "detailed": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": str(APP_LOG_DIR / "backend.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10MB
            "backupCount": 5,
            "encoding": "utf-8",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "detailed",
            "filename": str(APP_LOG_DIR / "error.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10MB
            "backupCount": 5,
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "": {  # root logger
            "level": "INFO",
            "handlers": ["console", "file", "error_file"],
        },
        "uvicorn": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False,
        },
        "sqlalchemy": {
            "level": "WARNING",
            "handlers": ["file"],
            "propagate": False,
        },
    },
}

def setup_logging():
    """初始化日志配置"""
    import logging.config
    logging.config.dictConfig(LOGGING_CONFIG)
```

**使用方式**：

```python
# backend/app/main.py
from .logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def startup_event():
    logger.info("应用启动")
```

---

### 方案 C：修复 unpacker 日志位置

**修改文件**：`scripts/unpack_archives.py`

```python
# 修改前
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
LOG_FILE = os.path.join(PROJECT_ROOT, "unpacker_log.json")
ACTIVITY_LOG = os.path.join(PROJECT_ROOT, "unpacker_activity.log")

# 修改后
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs", "tasks", "unpacker")
os.makedirs(LOG_DIR, exist_ok=True)

# 使用日期命名，便于清理
from datetime import datetime
log_date = datetime.now().strftime("%Y%m%d")
LOG_FILE = os.path.join(LOG_DIR, f"unpacker_{log_date}.json")
ACTIVITY_LOG = os.path.join(LOG_DIR, f"unpacker_{log_date}.log")
```

---

### 方案 D：添加日志清理机制

**定期清理脚本**：`scripts/cleanup_logs.py`

```python
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
LOG_ROOT = PROJECT_ROOT / "logs"

# 清理策略
CLEANUP_RULES = {
    "logs/app/*.log.*": 30,      # 应用日志保留 30 天
    "logs/tasks/envi/*.log": 7,  # ENVI 日志保留 7 天
    "logs/tasks/unpacker/*.log": 7,  # 解包日志保留 7 天
}

def cleanup_old_logs():
    """清理过期日志"""
    now = time.time()
    for pattern, days in CLEANUP_RULES.items():
        max_age = days * 86400  # 转换为秒
        for log_file in LOG_ROOT.glob(pattern):
            if log_file.is_file():
                age = now - log_file.stat().st_mtime
                if age > max_age:
                    print(f"删除过期日志: {log_file} (已存在 {age/86400:.1f} 天)")
                    log_file.unlink()

if __name__ == "__main__":
    cleanup_old_logs()
```

**添加到定时任务**：
```python
# backend/app/main.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def startup_event():
    # 每天凌晨 3 点清理日志
    scheduler.add_job(cleanup_old_logs, "cron", hour=3, minute=0)
    scheduler.start()
```

---

## 实施步骤

### 第一步：创建日志目录结构

```bash
cd Z:\Code\Insar_management_system_v2
mkdir -p logs/app
mkdir -p logs/tasks/envi
mkdir -p logs/tasks/unpacker
mkdir -p logs/access
mkdir -p logs/error
```

### 第二步：移动现有日志

```bash
# 移动 unpacker 日志
mv unpacker_activity.log logs/tasks/unpacker/unpacker_20260304.log
mv unpacker_log.json logs/tasks/unpacker/unpacker_20260304.json

# 移动 ENVI 日志（已经在正确位置）
# backend/runtime/idl_worker/ 保持不变
```

### 第三步：修改 unpacker 脚本

修改 `scripts/unpack_archives.py`，使用新的日志路径。

### 第四步：配置 Python logging

创建 `backend/app/logging_config.py`，配置统一日志。

### 第五步：更新 .gitignore

```gitignore
# 日志文件
logs/
*.log
*.log.*

# 但保留日志目录结构
!logs/.gitkeep
!logs/app/.gitkeep
!logs/tasks/.gitkeep
```

### 第六步：添加日志清理

创建 `scripts/cleanup_logs.py`，配置定时清理。

---

## 推荐配置

### 日志级别

```python
# 开发环境
LOG_LEVEL = "DEBUG"

# 生产环境
LOG_LEVEL = "INFO"

# 错误追踪
ERROR_LOG_LEVEL = "ERROR"
```

### 日志轮转

```python
# 按大小轮转
maxBytes = 10 * 1024 * 1024  # 10MB
backupCount = 5  # 保留 5 个备份

# 按时间轮转
when = "midnight"  # 每天午夜轮转
interval = 1  # 每 1 天
backupCount = 30  # 保留 30 天
```

### 日志保留

```python
# 应用日志：30 天
# 任务日志：7 天
# 错误日志：90 天
# 访问日志：30 天
```

---

## 总结

### 当前问题
1. ❌ unpacker 日志保存在根目录（不专业）
2. ❌ 日志分散在多个位置
3. ❌ 无统一日志框架
4. ❌ 无日志轮转和清理机制

### 推荐方案
1. ✅ 创建统一的 `logs/` 目录
2. ✅ 使用 Python logging 模块
3. ✅ 配置日志轮转（按大小或时间）
4. ✅ 添加定时清理机制
5. ✅ 更新 .gitignore

### 优先级
- **P0（立即）**：移动 unpacker 日志到 logs/ 目录
- **P1（本周）**：配置 Python logging 模块
- **P2（两周）**：添加日志轮转和清理
- **P3（一个月）**：集成日志分析工具（如 ELK）

---

## 下一步

需要我开始实施日志管理优化吗？我可以：
1. 创建日志目录结构
2. 修改 unpacker 脚本
3. 配置 Python logging
4. 添加日志清理脚本

还是你想先看看这个方案，再决定是否实施？
