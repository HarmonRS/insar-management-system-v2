# 硬编码审计报告（2026-03-05）

## 审计目的

确保系统可以在不同环境（开发机、生产环境、客户现场）部署，所有配置必须通过 `.env` 文件管理，不允许硬编码。

## 审计范围

- 后端代码（`backend/`）
- 启动脚本（`run_backend.py`, `scripts/`）
- 配置文件（`.env`, `config.py`）

## 发现的硬编码问题

### 🔴 高优先级（必须修复）

#### 1. Ollama API 地址硬编码

**位置**：
- `backend/app/ai_service.py:231`
- `backend/app/services/health_service.py:115`

**问题**：
```python
# 硬编码 Ollama API 地址
resp = await client.get("http://127.0.0.1:11434/api/tags")
```

**影响**：
- 如果 Ollama 部署在其他机器或端口，无法连接
- 客户环境可能使用不同的 Ollama 地址

**修复方案**：
```python
# 从环境变量读取
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
```

**`.env` 配置**：
```bash
# Ollama 服务地址（基础 URL）
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

---

#### 2. Nginx 健康检查地址硬编码

**位置**：
- `backend/app/services/health_service.py:130`

**问题**：
```python
# 硬编码 Nginx 地址
resp = await client.get("http://127.0.0.1/")
```

**影响**：
- 如果 Nginx 监听其他端口或地址，健康检查失败
- 客户环境可能使用不同的 Nginx 配置

**修复方案**：
```python
# 从环境变量读取
NGINX_HEALTH_URL = os.getenv("NGINX_HEALTH_URL", "http://127.0.0.1/")
resp = await client.get(NGINX_HEALTH_URL)
```

**`.env` 配置**：
```bash
# Nginx 健康检查地址
NGINX_HEALTH_URL=http://127.0.0.1/
```

---

#### 3. 后端绑定地址硬编码

**位置**：
- `run_backend.py:29`

**问题**：
```python
# 硬编码绑定地址
bind_host = "127.0.0.1"
```

**影响**：
- 无法从外部访问（Docker 容器、远程部署）
- 某些部署场景需要绑定 `0.0.0.0`

**修复方案**：
```python
# 从环境变量读取
bind_host = os.getenv("BACKEND_BIND_HOST", "127.0.0.1")
```

**`.env` 配置**：
```bash
# 后端绑定地址（127.0.0.1 仅本地，0.0.0.0 允许外部访问）
BACKEND_BIND_HOST=127.0.0.1
```

---

### 🟡 中优先级（建议修复）

#### 4. PROJ 数据库路径检查脚本硬编码

**位置**：
- `scripts/check_proj.py:62`

**问题**：
```python
# 硬编码 PostgreSQL PROJ 路径
pg_proj = r"C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.6\proj\proj.db"
```

**影响**：
- 不同 PostgreSQL 版本路径不同
- 客户环境可能安装在其他位置

**修复方案**：
```python
# 从环境变量读取，或自动检测
pg_proj = os.getenv("POSTGRESQL_PROJ_PATH", r"C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.6\proj\proj.db")
```

---

### 🟢 低优先级（可选优化）

#### 5. 默认值中的硬编码

**位置**：
- `backend/app/config.py:33-34`

**问题**：
```python
OLLAMA_API_URL: str = "http://127.0.0.1:11434/api/generate"
DEFAULT_VLM_MODEL: str = "qwen3-vl:8b"
```

**说明**：
- 这些是默认值，已经通过 `os.getenv()` 读取
- 但默认值本身是硬编码的

**修复方案**：
- 保持现状（默认值是合理的）
- 或者在文档中明确说明这些默认值

---

## 修复优先级

| 优先级 | 问题 | 影响 | 修复难度 |
|--------|------|------|----------|
| 🔴 P0 | Ollama API 地址 | 高 | 低 |
| 🔴 P0 | Nginx 健康检查地址 | 中 | 低 |
| 🔴 P0 | 后端绑定地址 | 高 | 低 |
| 🟡 P1 | PROJ 路径检查 | 低 | 低 |
| 🟢 P2 | 默认值硬编码 | 低 | 无需修复 |

---

## 修复计划

### 阶段 1：立即修复（P0）

1. **修改 `backend/app/ai_service.py`**
   - 添加 `OLLAMA_BASE_URL` 环境变量
   - 修改 API 调用使用动态 URL

2. **修改 `backend/app/services/health_service.py`**
   - 添加 `OLLAMA_BASE_URL` 和 `NGINX_HEALTH_URL` 环境变量
   - 修改健康检查使用动态 URL

3. **修改 `run_backend.py`**
   - 添加 `BACKEND_BIND_HOST` 环境变量
   - 支持配置绑定地址

4. **更新 `.env` 文件**
   - 添加新的配置项和注释

5. **更新文档**
   - 在部署文档中说明这些配置项

### 阶段 2：建议修复（P1）

1. **修改 `scripts/check_proj.py`**
   - 支持从环境变量读取 PostgreSQL 路径
   - 或自动检测 PostgreSQL 安装路径

---

## 验证方法

### 1. 配置文件验证

检查 `.env` 文件是否包含所有必需的配置项：

```bash
# 必需配置
DATABASE_URL=...
IDL_EXECUTABLE=...
IDL_DINSAR_DEM_BASE_FILE=...

# 新增配置
OLLAMA_BASE_URL=http://127.0.0.1:11434
NGINX_HEALTH_URL=http://127.0.0.1/
BACKEND_BIND_HOST=127.0.0.1
```

### 2. 代码审计

运行以下命令检查是否还有硬编码：

```bash
# 检查绝对路径
grep -r "C:\\\\" backend/ --include="*.py"
grep -r "D:\\\\" backend/ --include="*.py"

# 检查 IP 地址
grep -r "127\.0\.0\.1" backend/ --include="*.py"
grep -r "localhost" backend/ --include="*.py"

# 检查端口号
grep -r ":11434" backend/ --include="*.py"
grep -r ":8000" backend/ --include="*.py"
```

### 3. 部署测试

在不同环境测试：

1. **本地开发环境**：使用默认配置
2. **Docker 容器**：修改 `BACKEND_BIND_HOST=0.0.0.0`
3. **远程 Ollama**：修改 `OLLAMA_BASE_URL=http://192.168.1.100:11434`
4. **自定义端口**：修改 `PORT=18000`

---

## 部署检查清单

在部署到客户环境前，确认：

- [ ] 所有路径配置在 `.env` 中
- [ ] 所有 IP 地址/端口配置在 `.env` 中
- [ ] 没有硬编码的绝对路径
- [ ] 没有硬编码的 IP 地址
- [ ] 没有硬编码的端口号
- [ ] 所有配置项都有默认值（合理的）
- [ ] 所有配置项都有注释说明
- [ ] 部署文档已更新

---

## 相关文档

- `.env` 配置文件
- `docs/PROJ_CONFIGURATION.md` - PROJ 数据库配置
- `docs/DEPLOYMENT.md` - 部署文档（待创建）

---

## 总结

**当前状态**：
- ✅ 大部分配置已通过 `.env` 管理
- ❌ 发现 3 个高优先级硬编码问题
- ⚠️ 需要立即修复以支持生产部署

**修复后**：
- ✅ 所有配置通过 `.env` 管理
- ✅ 支持多种部署场景
- ✅ 客户环境可自定义配置
- ✅ 无需修改代码即可部署
