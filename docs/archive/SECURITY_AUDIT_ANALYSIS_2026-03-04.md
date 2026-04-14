# 安全审计分析与修复方案（2026-03-04）

## 审计概述

本文档针对 `SECURITY_AUDIT_2026-03-04.md` 中提出的安全问题进行详细分析，并提供具体的修复方案。

审计发现：
- 🔴 高危问题：3 个
- 🟡 中危问题：4 个
- 🟢 低危问题：1 个

---

## 1. 高危问题分析与修复方案

### 1.1 ENVI 主流程成功后不返回结果

**问题描述**：
- `envi_service.py:run_workflow()` 在成功执行后不返回 `record` 字典
- `envi_runner_cli.py` 期望打印 JSON 结果，但 `run_workflow()` 在成功时返回 `None`
- `job_handlers.py:_run_envi_workflow_job()` 尝试将返回值作为字典使用，导致崩溃

**根本原因**：
```python
# envi_service.py:1630
if error:
    raise RuntimeError(error)
# 缺少: return record
```

**影响等级**：🔴 高危
- 所有 ENVI 工作流任务在成功执行后会被误判为失败
- 下游代码尝试访问 `run_meta.get('workflow')` 时会抛出 `AttributeError`

**修复方案**：
```python
# envi_service.py:1630 之后添加
if error:
    raise RuntimeError(error)
return record  # 添加此行
```

**验证方法**：
1. 运行一个 D-InSAR 工作流任务
2. 检查任务状态是否正确标记为 SUCCESS
3. 检查日志中是否包含 `workflow=dinsar_custom duration=XXXs`

---

### 1.2 只读用户可通过 GET 触发写操作

**问题描述**：
多个 GET 端点在只读操作中触发了数据库写入和高开销计算：

1. **`GET /radar-data/{data_id}/thumb`** (radar.py:622)
   - 调用 `_get_cached_radar_preview()` → `_build_radar_preview_cache()`
   - 触发预览图生成（GDAL 处理）并写入数据库

2. **`GET /radar-data/imaging-dates`** (radar.py:609)
   - 虽然是只读查询，但在 `_build_radar_preview_cache()` 中会更新数据库

3. **`GET /dinsar-results/{result_id}/preview`** (dinsar.py:214)
   - 类似问题，按需构建预览缓存

**根本原因**：
```python
# radar.py:365
if settings.RADAR_PREVIEW_BUILD_ON_DEMAND:
    build_status = await _build_radar_preview_cache(record, db, force=False)
    # 内部会执行 db.add(record) 和 db.commit()
```

**影响等级**：🔴 高危
- 只读账号可以触发大量 GDAL 处理任务，导致 CPU/内存耗尽（DoS）
- 违反最小权限原则，读账号不应有写权限
- 可能导致数据库锁竞争

**修复方案**：

**方案 A：权限分离（推荐）**
```python
# 在 dependencies.py 中添加权限检查
def require_write_permission(current_user: User = Depends(get_current_user)):
    if current_user.role == "readonly":
        raise HTTPException(
            status_code=403,
            detail="此操作需要写权限"
        )
    return current_user

# 在 radar.py 中修改端点
@router.get("/radar-data/{data_id}/thumb")
async def get_radar_data_thumb_endpoint(
    data_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_write_permission)  # 添加权限检查
):
    # 仅当用户有写权限时才允许按需构建
    ...
```

**方案 B：配置控制**
```python
# 在 .env 中添加
RADAR_PREVIEW_BUILD_ON_DEMAND_READONLY=false

# 在 radar.py 中修改
if settings.RADAR_PREVIEW_BUILD_ON_DEMAND:
    if current_user.role == "readonly" and not settings.RADAR_PREVIEW_BUILD_ON_DEMAND_READONLY:
        # 只返回已有缓存，不触发构建
        raise HTTPException(status_code=404, detail="预览图未生成")
    build_status = await _build_radar_preview_cache(record, db, force=False)
```

**方案 C：改为 POST 端点（最符合 RESTful 原则）**
```python
# 将按需构建改为显式 POST 操作
@router.post("/radar-data/{data_id}/build-preview")
async def build_radar_preview_endpoint(
    data_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_write_permission)
):
    # 显式构建预览图
    ...

# GET 端点只返回已有缓存
@router.get("/radar-data/{data_id}/thumb")
async def get_radar_data_thumb_endpoint(data_id: int, db: AsyncSession = Depends(get_db)):
    # 不触发构建，只返回已有文件
    if not os.path.exists(geo_cache_path):
        raise HTTPException(status_code=404, detail="预览图未生成，请先调用构建接口")
    return FileResponse(geo_cache_path, ...)
```

**推荐方案**：方案 C（最符合 RESTful 原则）

---

### 1.3 AOI token 内存存储无容量上限

**问题描述**：
- `_AOI_TOKEN_STORE` 是全局字典，无大小限制
- 每次查询 AOI 时会续期 token（`expires_at = now + AOI_TOKEN_TTL_SECONDS`）
- 恶意用户可以通过频繁查询不同 AOI 区域来填满内存

**根本原因**：
```python
# dependencies.py:131
_AOI_TOKEN_STORE: Dict[str, Dict[str, Any]] = {}  # 无容量限制

# dependencies.py:850
payload["expires_at"] = now + AOI_TOKEN_TTL_SECONDS  # 每次访问都续期
```

**影响等级**：🔴 高危
- 认证后的低权限账号可以制造内存泄漏
- 30 分钟 TTL + 续期机制 = 长期占用内存
- 可能导致进程 OOM 崩溃

**修复方案**：

**方案 A：添加容量上限（推荐）**
```python
# dependencies.py
AOI_TOKEN_MAX_STORE_SIZE = read_int_env(
    "AOI_TOKEN_MAX_STORE_SIZE",
    1000,  # 最多存储 1000 个 token
    minimum=100,
    maximum=10000,
)

async def _store_aoi_token(aoi_wkt: str, feature_collection: Dict[str, Any]) -> str:
    token = uuid.uuid4().hex
    now = time.monotonic()
    async with _AOI_TOKEN_LOCK:
        _cleanup_expired_aoi_tokens(now)

        # 检查容量上限
        if len(_AOI_TOKEN_STORE) >= AOI_TOKEN_MAX_STORE_SIZE:
            # 删除最旧的 token（按 expires_at 排序）
            oldest_tokens = sorted(
                _AOI_TOKEN_STORE.items(),
                key=lambda x: x[1]["expires_at"]
            )[:100]  # 删除最旧的 100 个
            for old_token, _ in oldest_tokens:
                _AOI_TOKEN_STORE.pop(old_token, None)

        _AOI_TOKEN_STORE[token] = {
            "aoi_wkt": aoi_wkt,
            "aoi_geojson": feature_collection,
            "expires_at": now + AOI_TOKEN_TTL_SECONDS,
        }
    return token
```

**方案 B：限制续期次数**
```python
# 在 token payload 中添加续期计数
_AOI_TOKEN_STORE[token] = {
    "aoi_wkt": aoi_wkt,
    "aoi_geojson": feature_collection,
    "expires_at": now + AOI_TOKEN_TTL_SECONDS,
    "renew_count": 0,  # 添加续期计数
    "max_renews": 5,   # 最多续期 5 次
}

# 在 _get_aoi_from_token 中检查
async def _get_aoi_from_token(aoi_token: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    ...
    payload = _AOI_TOKEN_STORE.get(normalized_token)
    if not payload:
        return None

    # 检查续期次数
    if payload.get("renew_count", 0) >= payload.get("max_renews", 5):
        # 达到续期上限，不再续期
        return payload.get("aoi_wkt"), payload.get("aoi_geojson")

    payload["expires_at"] = now + AOI_TOKEN_TTL_SECONDS
    payload["renew_count"] = payload.get("renew_count", 0) + 1
    return payload.get("aoi_wkt"), payload.get("aoi_geojson")
```

**方案 C：使用 Redis（生产环境推荐）**
```python
# 使用 Redis 替代内存字典，自动过期
import redis.asyncio as redis

_redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    decode_responses=False,
)

async def _store_aoi_token(aoi_wkt: str, feature_collection: Dict[str, Any]) -> str:
    token = uuid.uuid4().hex
    payload = {
        "aoi_wkt": aoi_wkt,
        "aoi_geojson": feature_collection,
    }
    await _redis_client.setex(
        f"aoi_token:{token}",
        AOI_TOKEN_TTL_SECONDS,
        json.dumps(payload)
    )
    return token
```

**推荐方案**：方案 A（短期）+ 方案 C（长期）

---

## 2. 中危问题分析与修复方案

### 2.1 extract_disp_results 失败计数重复累加

**问题描述**：
```python
# envi_service.py:1717, 1726, 1729
except OSError as e:
    task_failed += 1
    failed += 1  # 第一次累加
    task_status = f"error: {e}"
...
failed += task_failed  # 第二次累加（1729 行）
```

**影响等级**：🟡 中危
- 统计数据不准确，影响监控和告警
- 可能导致误报（失败数翻倍）

**修复方案**：
```python
# envi_service.py:1717 和 1726 行，删除 failed += 1
except OSError as e:
    task_failed += 1
    # 删除: failed += 1
    task_status = f"error: {e}"

# 保留 1729 行的汇总
failed += task_failed  # 只在这里累加一次
```

---

### 2.2 AOI 文件解析异常未统一转为 4xx

**问题描述**：
```python
# dependencies.py:792, 800
# 解析 GeoJSON/Shapefile 时的异常直接抛出，返回 500
aoi_wkt = _parse_geojson_to_wkt(geojson_data)  # 可能抛出 ValueError
```

**影响等级**：🟡 中危
- 用户输入错误导致 500 错误，应该返回 400
- 错误语义不一致，影响 API 可用性

**修复方案**：
```python
# dependencies.py 中添加统一异常处理
try:
    aoi_wkt = _parse_geojson_to_wkt(geojson_data)
except (ValueError, KeyError, TypeError) as e:
    raise HTTPException(
        status_code=400,
        detail=f"GeoJSON 格式错误: {str(e)}"
    )
except Exception as e:
    logger.error(f"AOI 解析失败: {e}")
    raise HTTPException(
        status_code=500,
        detail="AOI 解析失败，请联系管理员"
    )
```

---

### 2.3 路径归属判断使用 startswith

**问题描述**：
```python
# data_service.py:530
if not any(file_path_abs.startswith(root) for root in radar_roots):
    continue
```

**影响等级**：🟡 中危
- 前缀误匹配：`/data/radar` 会匹配 `/data/radar_backup`
- 可能将不属于监控目录的文件误判为合法

**修复方案**：
```python
# 使用 os.path.commonpath 或规范化路径比较
def is_subpath(child: str, parent: str) -> bool:
    """检查 child 是否是 parent 的子路径"""
    try:
        child_abs = os.path.abspath(child)
        parent_abs = os.path.abspath(parent)
        common = os.path.commonpath([child_abs, parent_abs])
        return common == parent_abs
    except (ValueError, TypeError):
        return False

# data_service.py:530
if not any(is_subpath(file_path_abs, root) for root in radar_roots):
    continue
```

---

### 2.4 解包安全校验仅检查 member.name

**问题描述**：
```python
# unpack_archives.py:98-104
def _is_safe_tar_member(member_name):
    norm_name = os.path.normpath(member_name)
    if os.path.isabs(norm_name):
        return False
    if norm_name.startswith(".."):
        return False
    return True

# 但仍使用 extractall，未检查符号链接/硬链接
tar.extractall(path=tmp_dir)  # 181 行
```

**影响等级**：🟡 中危
- 符号链接可以指向任意路径（如 `/etc/passwd`）
- 硬链接可以覆盖系统文件
- 如果归档文件来自不可信来源，存在目录逃逸风险

**修复方案**：
```python
# unpack_archives.py
def _is_safe_tar_member(member):
    """检查 tar 成员是否安全（包括符号链接检查）"""
    # 检查路径
    norm_name = os.path.normpath(member.name)
    if os.path.isabs(norm_name):
        return False
    if norm_name.startswith("..") or norm_name.startswith("../") or norm_name.startswith("..\\"):
        return False

    # 检查符号链接
    if member.issym() or member.islnk():
        # 检查链接目标是否安全
        link_target = member.linkname
        if os.path.isabs(link_target) or link_target.startswith(".."):
            return False

    return True

def _validate_tar_members(tar_obj, archive_path):
    for member in tar_obj.getmembers():
        if not _is_safe_tar_member(member):  # 传入 member 对象而非 name
            raise IOError(f"unsafe tar entry detected: {member.name} in {archive_path}")

# 使用安全的逐个提取方式
def _safe_extract_tar(tar_obj, output_dir):
    """安全地提取 tar 文件"""
    for member in tar_obj.getmembers():
        if not _is_safe_tar_member(member):
            raise IOError(f"unsafe tar entry: {member.name}")

        # 逐个提取，避免 extractall
        tar_obj.extract(member, path=output_dir, filter='data')  # Python 3.12+
        # 或者对于旧版本：
        # tar_obj.extract(member, path=output_dir)

# 在 181 行替换
with tarfile.open(archive_path, "r:*") as tar:
    _validate_tar_members(tar, archive_path)
    _safe_extract_tar(tar, tmp_dir)  # 替换 tar.extractall
```

---

## 3. 低危问题分析与修复方案

### 3.1 启动脚本写 nginx.conf 未显式编码

**问题描述**：
```powershell
# start_app.ps1:388
$NewConfContent | Set-Content -LiteralPath "$NginxConfPath" -NoNewline
# 未指定 -Encoding UTF8
```

**影响等级**：🟢 低危
- PowerShell 5 默认使用 UTF-16 LE 编码
- Nginx 期望 UTF-8 编码，可能导致配置解析失败

**修复方案**：
```powershell
# start_app.ps1:388
$NewConfContent | Set-Content -LiteralPath "$NginxConfPath" -NoNewline -Encoding UTF8
```

---

## 4. 修复优先级建议

### P0（立即修复）
1. ✅ **1.1 ENVI 主流程返回值缺失** - 影响所有工作流任务
2. ✅ **1.2 只读用户触发写操作** - 权限绕过 + DoS 风险

### P1（本周内修复）
3. ✅ **1.3 AOI token 内存泄漏** - DoS 风险
4. ✅ **2.1 失败计数重复累加** - 影响监控准确性

### P2（两周内修复）
5. ✅ **2.2 AOI 解析异常处理** - API 可用性问题
6. ✅ **2.3 路径归属判断** - 潜在安全风险

### P3（一个月内修复）
7. ✅ **2.4 解包安全校验** - 取决于归档文件来源可信度
8. ✅ **3.1 PowerShell 编码** - 低影响，但易修复

---

## 5. 修复验证清单

### 1.1 ENVI 主流程返回值
- [ ] 运行 D-InSAR 工作流任务
- [ ] 检查任务状态是否为 SUCCESS
- [ ] 检查日志是否包含 `workflow=dinsar_custom`

### 1.2 只读用户权限
- [ ] 使用只读账号访问 `/radar-data/{id}/thumb`
- [ ] 验证是否返回 403 或 404（不触发构建）
- [ ] 使用写权限账号验证按需构建仍可用

### 1.3 AOI token 容量
- [ ] 创建 1000+ 个不同 AOI token
- [ ] 验证内存占用是否稳定
- [ ] 检查日志是否有 token 清理记录

### 2.1 失败计数
- [ ] 运行 `extract_disp_results` 并故意触发失败
- [ ] 验证返回的 `failed` 计数是否准确

### 2.2 AOI 解析异常
- [ ] 上传无效 GeoJSON 文件
- [ ] 验证是否返回 400 而非 500

### 2.3 路径归属判断
- [ ] 创建 `/data/radar_backup/test.tif` 文件
- [ ] 验证是否被误判为 `/data/radar` 的子路径

### 2.4 解包安全
- [ ] 创建包含符号链接的 tar 文件
- [ ] 验证是否被拒绝解包

### 3.1 PowerShell 编码
- [ ] 在 PowerShell 5 环境运行启动脚本
- [ ] 验证 nginx.conf 是否为 UTF-8 编码

---

## 6. 长期改进建议

### 6.1 引入速率限制
```python
# 使用 slowapi 限制 API 调用频率
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.get("/radar-data/{data_id}/thumb")
@limiter.limit("10/minute")  # 每分钟最多 10 次
async def get_radar_data_thumb_endpoint(...):
    ...
```

### 6.2 添加审计日志
```python
# 记录所有写操作和高开销操作
async def audit_log(
    user_id: int,
    action: str,
    resource: str,
    details: Dict[str, Any]
):
    await db.execute(
        insert(AuditLogORM).values(
            user_id=user_id,
            action=action,
            resource=resource,
            details=details,
            timestamp=datetime.utcnow(),
        )
    )
```

### 6.3 使用 Redis 替代内存缓存
- AOI token 存储
- 预览图构建队列
- 速率限制计数器

### 6.4 添加资源配额
```python
# 限制每个用户的资源使用
USER_QUOTA = {
    "readonly": {
        "max_concurrent_requests": 5,
        "max_aoi_tokens": 10,
        "max_preview_builds_per_hour": 0,
    },
    "user": {
        "max_concurrent_requests": 20,
        "max_aoi_tokens": 50,
        "max_preview_builds_per_hour": 100,
    },
    "admin": {
        "max_concurrent_requests": 100,
        "max_aoi_tokens": 500,
        "max_preview_builds_per_hour": 1000,
    },
}
```

---

## 7. 总结

本次审计发现了 **3 个高危问题**、**4 个中危问题**、**1 个低危问题**，主要集中在：
1. 函数返回值缺失导致任务失败
2. 权限控制不严格（读账号可触发写操作）
3. 资源无限制（内存泄漏风险）
4. 统计数据不准确
5. 异常处理不规范

建议按照优先级逐步修复，并在修复后进行充分的回归测试。长期来看，应该引入更完善的权限管理、资源配额、审计日志和速率限制机制。
