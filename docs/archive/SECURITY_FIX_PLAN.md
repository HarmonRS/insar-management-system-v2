# 安全问题分阶段修复计划

## 修复原则

1. **安全第一**：每个阶段修复后必须充分测试，确保不影响现有功能
2. **最小改动**：优先选择改动最小、风险最低的方案
3. **可回滚**：每个阶段独立提交，出问题可以快速回滚
4. **渐进式**：从简单到复杂，从低风险到高风险

---

## 第一阶段：明确 Bug 修复（低风险）

**目标**：修复明确的代码错误，不涉及架构变更

### 1.1 ENVI 主流程返回值缺失 ✅ 已完成

**文件**：`backend/app/services/envi_service.py:1630`

**修改**：
```python
# 修改前
if error:
    raise RuntimeError(error)

# 修改后
if error:
    raise RuntimeError(error)
return record  # 添加此行
```

**风险评估**：🟢 极低
- 只添加一行返回语句
- 不改变任何逻辑
- 修复了明确的 bug（成功时应该返回 record）

**测试计划**：
- [ ] 运行一个完整的 D-InSAR 工作流（dinsar_custom）
- [ ] 检查任务状态是否正确标记为 SUCCESS
- [ ] 检查日志是否包含 `workflow=dinsar_custom duration=XXXs`
- [ ] 验证前端是否正确显示任务完成

---

### 1.2 失败计数重复累加

**文件**：`backend/app/services/envi_service.py:1717, 1727`

**修改**：
```python
# 修改前（1717 行）
except OSError as e:
    task_failed += 1
    failed += 1  # 删除此行
    task_status = f"error: {e}"

# 修改后
except OSError as e:
    task_failed += 1
    task_status = f"error: {e}"

# 修改前（1727 行）
except OSError as e:
    task_failed += 1
    failed += 1  # 删除此行
    task_status = f"error: {e}"

# 修改后
except OSError as e:
    task_failed += 1
    task_status = f"error: {e}"

# 保留 1730 行的汇总
failed += task_failed  # 只在这里累加一次
```

**风险评估**：🟢 极低
- 只删除重复的累加语句
- 不改变业务逻辑
- 修复统计数据不准确的问题

**测试计划**：
- [ ] 运行 `extract_disp_results` 功能
- [ ] 故意触发一些失败（如权限问题）
- [ ] 验证返回的 `failed` 计数是否准确（不翻倍）

---

### 1.3 PowerShell 编码问题

**文件**：`scripts/start_app.ps1:388`

**修改**：
```powershell
# 修改前
$NewConfContent | Set-Content -LiteralPath "$NginxConfPath" -NoNewline

# 修改后
$NewConfContent | Set-Content -LiteralPath "$NginxConfPath" -NoNewline -Encoding UTF8
```

**风险评估**：🟢 极低
- 只添加编码参数
- 确保 nginx.conf 使用 UTF-8 编码
- 避免 PowerShell 5 默认 UTF-16 导致的问题

**测试计划**：
- [ ] 在 PowerShell 5 环境运行启动脚本
- [ ] 检查生成的 nginx.conf 文件编码（使用 `file` 命令或文本编辑器）
- [ ] 验证 Nginx 是否能正常启动和解析配置

---

**第一阶段提交**：
```
fix: 修复 ENVI 工作流返回值缺失和统计计数错误

- 修复 run_workflow 成功时不返回 record 的问题
- 修复 extract_disp_results 失败计数重复累加
- 修复 PowerShell 脚本 nginx.conf 编码问题

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

---

## 第二阶段：防御性增强（中风险）

**目标**：添加保护措施，不改变现有业务逻辑

### 2.1 AOI token 容量上限

**文件**：`backend/app/routers/dependencies.py`

**修改策略**：
1. 添加环境变量配置 `AOI_TOKEN_MAX_STORE_SIZE`（默认 1000）
2. 在 `_store_aoi_token` 中添加容量检查
3. 达到上限时删除最旧的 100 个 token

**实现**：
```python
# 在 dependencies.py 顶部添加
AOI_TOKEN_MAX_STORE_SIZE = read_int_env(
    "AOI_TOKEN_MAX_STORE_SIZE",
    1000,
    minimum=100,
    maximum=10000,
)

# 修改 _store_aoi_token 函数
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
            )[:100]
            for old_token, _ in oldest_tokens:
                _AOI_TOKEN_STORE.pop(old_token, None)
            # 记录日志
            print(f"[INFO] AOI token store reached limit, cleaned {len(oldest_tokens)} oldest tokens")

        _AOI_TOKEN_STORE[token] = {
            "aoi_wkt": aoi_wkt,
            "aoi_geojson": feature_collection,
            "expires_at": now + AOI_TOKEN_TTL_SECONDS,
        }
    return token
```

**风险评估**：🟡 中等
- 添加了新的容量限制逻辑
- 可能影响高并发场景下的 AOI 查询
- 但不改变现有 API 接口

**测试计划**：
- [ ] 创建 1000+ 个不同的 AOI token
- [ ] 验证内存占用是否稳定（不超过预期）
- [ ] 检查日志是否有 token 清理记录
- [ ] 验证被清理的 token 是否无法再使用
- [ ] 验证新创建的 token 仍然可用

---

### 2.2 AOI 解析异常处理

**文件**：`backend/app/routers/dependencies.py`

**修改策略**：
在所有 AOI 解析函数中添加统一的异常处理

**实现位置**：
- `_parse_geojson_to_wkt` 调用处
- `_parse_shapefile_to_wkt` 调用处
- 文件上传端点

**实现**：
```python
# 在相关端点中添加 try-except
try:
    aoi_wkt = _parse_geojson_to_wkt(geojson_data)
except (ValueError, KeyError, TypeError) as e:
    raise HTTPException(
        status_code=400,
        detail=f"GeoJSON 格式错误: {str(e)}"
    )
except Exception as e:
    logger.error(f"AOI 解析失败: {e}", exc_info=True)
    raise HTTPException(
        status_code=500,
        detail="AOI 解析失败，请联系管理员"
    )
```

**风险评估**：🟡 中等
- 改变了错误响应码（500 → 400）
- 可能影响前端错误处理逻辑
- 但提升了 API 语义正确性

**测试计划**：
- [ ] 上传无效的 GeoJSON 文件
- [ ] 验证是否返回 400 而非 500
- [ ] 验证错误消息是否清晰
- [ ] 验证前端是否能正确处理 400 错误

---

### 2.3 路径归属判断优化

**文件**：`backend/app/services/data_service.py:530`

**修改策略**：
添加辅助函数 `is_subpath`，使用 `os.path.commonpath` 进行准确判断

**实现**：
```python
# 在 data_service.py 顶部添加辅助函数
def is_subpath(child: str, parent: str) -> bool:
    """检查 child 是否是 parent 的子路径"""
    try:
        child_abs = os.path.abspath(child)
        parent_abs = os.path.abspath(parent)
        # 确保父路径以分隔符结尾，避免前缀误匹配
        if not parent_abs.endswith(os.sep):
            parent_abs += os.sep
        if not child_abs.endswith(os.sep) and os.path.isdir(child_abs):
            child_abs += os.sep
        # 使用 commonpath 判断
        common = os.path.commonpath([child_abs, parent_abs])
        return common == parent_abs.rstrip(os.sep)
    except (ValueError, TypeError):
        return False

# 修改 530 行
# 修改前
if not any(file_path_abs.startswith(root) for root in radar_roots):
    continue

# 修改后
if not any(is_subpath(file_path_abs, root) for root in radar_roots):
    continue
```

**风险评估**：🟡 中等
- 改变了路径判断逻辑
- 可能影响文件扫描结果
- 但修复了潜在的安全问题

**测试计划**：
- [ ] 在 `MONITOR_RADAR_DIRS` 旁边创建 `MONITOR_RADAR_DIRS_backup` 目录
- [ ] 放入测试文件
- [ ] 运行数据扫描
- [ ] 验证 backup 目录的文件不会被误判为合法

---

**第二阶段提交**：
```
feat: 添加 AOI token 容量限制和异常处理增强

- 添加 AOI_TOKEN_MAX_STORE_SIZE 配置（默认 1000）
- 达到上限时自动清理最旧的 token
- 统一 AOI 解析异常处理（400 vs 500）
- 优化路径归属判断，避免前缀误匹配

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

---

## 第三阶段：安全加固（高风险）

**目标**：解决权限和安全问题，可能涉及架构调整

### 3.1 解包安全校验增强

**文件**：`scripts/unpack_archives.py`

**修改策略**：
1. 修改 `_is_safe_tar_member` 接收 member 对象而非字符串
2. 添加符号链接和硬链接检查
3. 使用逐个提取替代 `extractall`

**实现**：
```python
def _is_safe_tar_member(member):
    """检查 tar 成员是否安全（包括符号链接检查）"""
    # 检查路径
    norm_name = os.path.normpath(member.name)
    if os.path.isabs(norm_name):
        return False
    if norm_name.startswith("..") or norm_name.startswith("../") or norm_name.startswith("..\\"):
        return False

    # 检查符号链接和硬链接
    if member.issym() or member.islnk():
        link_target = member.linkname
        if os.path.isabs(link_target) or link_target.startswith(".."):
            return False

    return True

def _validate_tar_members(tar_obj, archive_path):
    for member in tar_obj.getmembers():
        if not _is_safe_tar_member(member):
            raise IOError(f"unsafe tar entry detected: {member.name} in {archive_path}")

def _safe_extract_tar(tar_obj, output_dir):
    """安全地提取 tar 文件"""
    for member in tar_obj.getmembers():
        if not _is_safe_tar_member(member):
            raise IOError(f"unsafe tar entry: {member.name}")
        # 逐个提取
        tar_obj.extract(member, path=output_dir)

# 在 181 行替换
with tarfile.open(archive_path, "r:*") as tar:
    _validate_tar_members(tar, archive_path)
    _safe_extract_tar(tar, output_dir)  # 替换 tar.extractall
```

**风险评估**：🟡 中等
- 改变了解包逻辑
- 可能影响解包性能（逐个提取 vs 批量提取）
- 但提升了安全性

**测试计划**：
- [ ] 创建包含符号链接的 tar 文件
- [ ] 验证是否被拒绝解包
- [ ] 创建正常的 tar 文件
- [ ] 验证是否能正常解包
- [ ] 对比解包性能（前后差异）

---

### 3.2 只读用户权限控制（暂缓）

**状态**：⏸️ 暂缓到第四阶段

**原因**：
- 涉及权限系统重构
- 需要修改多个端点
- 可能影响前端调用逻辑
- 需要更充分的测试

**备选方案**：
- 方案 A：添加权限检查依赖
- 方案 B：配置控制（允许/禁止只读用户触发构建）
- 方案 C：改为 POST 端点（需要前端配合）

**决策点**：
- 是否有只读用户在使用系统？
- 只读用户是否需要查看预览图？
- 是否可以接受只读用户触发构建？

---

**第三阶段提交**：
```
security: 增强 tar 解包安全校验

- 添加符号链接和硬链接检查
- 使用逐个提取替代 extractall
- 防止目录逃逸和文件覆盖攻击

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

---

## 第四阶段：权限重构（待定）

**目标**：解决只读用户权限问题

**前置条件**：
- 第一、二、三阶段全部完成并稳定运行
- 明确只读用户的使用场景和需求
- 前端团队配合（如果需要修改 API 调用）

**待讨论问题**：
1. 系统中是否有只读用户？
2. 只读用户的典型使用场景是什么？
3. 是否可以接受只读用户无法查看预览图？
4. 是否需要前端配合修改？

---

## 修复时间表

| 阶段 | 预计时间 | 风险等级 | 依赖 |
|------|---------|---------|------|
| 第一阶段 | 1 天 | 🟢 低 | 无 |
| 第二阶段 | 2-3 天 | 🟡 中 | 第一阶段完成 |
| 第三阶段 | 2-3 天 | 🟡 中 | 第二阶段完成 |
| 第四阶段 | 待定 | 🔴 高 | 需求确认 |

---

## 回滚计划

每个阶段独立提交，出现问题时可以：

1. **快速回滚**：
   ```bash
   git revert <commit-hash>
   ```

2. **部分回滚**：
   如果只有某个修复有问题，可以单独回滚该文件：
   ```bash
   git checkout <previous-commit> -- <file-path>
   git commit -m "revert: 回滚 <file-path> 的修改"
   ```

3. **紧急回滚**：
   如果影响生产环境，立即回滚到上一个稳定版本：
   ```bash
   git reset --hard <last-stable-commit>
   ```

---

## 监控指标

修复后需要监控的关键指标：

### 第一阶段
- ENVI 工作流成功率（应该提升）
- 任务状态准确性
- 统计数据准确性

### 第二阶段
- AOI token 内存占用（应该稳定）
- 400 vs 500 错误比例（400 应该增加）
- 路径扫描结果准确性

### 第三阶段
- 解包成功率（应该不变）
- 解包性能（可能略微下降）
- 安全事件（应该为 0）

---

## 总结

这个分阶段修复计划：
- ✅ 从低风险到高风险渐进式修复
- ✅ 每个阶段独立可测试、可回滚
- ✅ 优先修复明确的 bug，暂缓架构性改动
- ✅ 保留了灵活性，可以根据实际情况调整

**建议**：先完成第一阶段，充分测试后再决定是否继续第二阶段。
