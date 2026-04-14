# PROJ 数据库版本冲突问题修复（2026-03-05）

## 问题描述

在扫描 D-InSAR 结果时出现 PROJ 错误：

```
PROJ: proj_identify: C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.6\proj\proj.db
contains DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 3 is expected.
It comes from another PROJ installation.
```

## 根本原因

1. **PROJ 版本冲突**：
   - PostgreSQL 17 自带的 PostGIS 3.6 包含旧版 PROJ 数据库（版本 2）
   - GDAL/rasterio 期望 PROJ 数据库版本 >= 3
   - 系统在坐标转换时（`Transformer.from_crs`）触发此警告

2. **环境变量优先级**：
   - Windows 系统可能将 PostgreSQL 的 PROJ 路径添加到 PATH
   - GDAL 默认搜索 PATH 中的 PROJ 数据库
   - 找到了 PostgreSQL 的旧版本而非 GDAL 自带的新版本

## 影响

- 🟡 **中等**：不会导致程序崩溃，但会产生大量警告日志
- 坐标转换仍然可以工作（PROJ 会回退到兼容模式）
- 日志污染，影响问题排查

## 解决方案

### 方案 1：设置 PROJ_LIB 环境变量（已实施）

**文件**：`run_backend.py`

**修改内容**：
```python
def main() -> None:
    # Load environment variables
    load_dotenv()

    # Fix PROJ database version conflict
    # PostgreSQL's PROJ database is outdated, use GDAL's bundled PROJ data
    # This prevents "DATABASE.LAYOUT.VERSION.MINOR = 2 whereas >= 3 is expected" error
    if "PROJ_LIB" not in os.environ:
        # Try to find GDAL's PROJ data directory
        try:
            from osgeo import gdal
            gdal_data = gdal.GetConfigOption("GDAL_DATA")
            if gdal_data:
                proj_lib = os.path.join(os.path.dirname(gdal_data), "proj")
                if os.path.exists(proj_lib):
                    os.environ["PROJ_LIB"] = proj_lib
                    print(f"[*] Set PROJ_LIB to: {proj_lib}")
        except Exception as e:
            print(f"[WARN] Could not auto-configure PROJ_LIB: {e}")

    port = int(os.getenv("PORT", 8000))
    # ... rest of the code
```

**工作原理**：
1. 在启动时检查 `PROJ_LIB` 环境变量是否已设置
2. 如果未设置，尝试从 GDAL 配置中获取 PROJ 数据目录
3. 设置 `PROJ_LIB` 指向 GDAL 自带的 PROJ 数据库

### 方案 2：手动设置环境变量（备选）

如果方案 1 不生效，可以在 `.env` 文件中手动设置：

```bash
# 找到 GDAL 的 PROJ 数据目录
python -c "from osgeo import gdal; import os; print(os.path.join(os.path.dirname(gdal.GetConfigOption('GDAL_DATA')), 'proj'))"

# 将输出路径添加到 .env
PROJ_LIB=C:\path\to\gdal\proj
```

或者在 PowerShell 启动脚本中设置：

```powershell
# scripts/start_app.ps1
$env:PROJ_LIB = "C:\path\to\gdal\proj"
```

### 方案 3：抑制 GDAL 警告（不推荐）

如果只是想隐藏警告而不解决根本问题：

```python
# 在 run_backend.py 中添加
from osgeo import gdal
gdal.SetConfigOption('CPL_LOG', 'OFF')  # 关闭所有 GDAL 日志
```

**不推荐原因**：
- 会隐藏所有 GDAL 错误和警告
- 不解决根本问题
- 可能掩盖其他重要错误

## 验证

### 1. 检查 PROJ_LIB 是否生效

启动后端后，查看日志：
```
[*] Set PROJ_LIB to: C:\Users\...\site-packages\osgeo\data\proj
```

### 2. 运行扫描任务

在前端触发 D-InSAR 结果扫描，观察日志：
- ✅ 应该不再出现 PROJ 警告
- ✅ Footprint 提取正常完成

### 3. 验证坐标转换

```python
from pyproj import Transformer
transformer = Transformer.from_crs("EPSG:32650", "EPSG:4326", always_xy=True)
x, y = transformer.transform(500000, 3000000)
print(f"Transformed: {x}, {y}")
# 应该不产生警告
```

## 前端锁定问题

**问题**：扫描任务运行时前端没有锁定

**可能原因**：
1. 扫描任务执行太快（< 3 秒），前端轮询间隔（3 秒）来不及捕获
2. 任务状态更新有延迟

**解决方案**：

### 方案 A：增加最小锁定时间（推荐）

在扫描任务开始时立即锁定，结束时延迟解锁：

```python
# job_handlers.py
async def _handle_scan_dinsar(job: SystemJobORM) -> None:
    if not job.task_id:
        raise ValueError("SCAN_DINSAR requires task_id for progress tracking.")

    await task_service.start_task(job.task_id, message="正在扫描 D-InSAR 结果...")

    # 确保任务至少运行 2 秒，让前端有时间捕获
    start_time = time.time()

    # ... 执行扫描 ...

    # 确保最小执行时间
    elapsed = time.time() - start_time
    if elapsed < 2.0:
        await asyncio.sleep(2.0 - elapsed)

    await task_service.complete_task(job.task_id, message="扫描完成")
```

### 方案 B：前端立即锁定（更好）

在提交扫描任务后立即锁定前端，不等待轮询：

```javascript
// DataManagementPanel.jsx
const handleScanDinsarResults = async () => {
  try {
    // 立即锁定前端
    onJobQueued?.(null, 'SCAN_DINSAR');

    const response = await scanDinsarResults(selectedDirs);
    const taskId = response.data.task_id;

    // 更新任务 ID
    onJobQueued?.(taskId, 'SCAN_DINSAR');

    addLog('info', `D-InSAR 结果扫描任务已提交: ${taskId}`);
  } catch (error) {
    // 解锁前端
    setIsGlobalLocked(false);
    addLog('error', `扫描失败: ${error.message}`);
  }
};
```

## 状态

- ✅ PROJ 环境变量修复已实施
- ⏳ 等待测试验证
- ⏳ 前端锁定问题待确认是否需要修复

## 下一步

1. 重启后端服务
2. 运行 D-InSAR 结果扫描
3. 观察日志，确认 PROJ 警告消失
4. 如果前端仍未锁定，实施方案 B

## 相关文档

- `SECURITY_FIX_PROGRESS.md` - 安全修复进度
- `HOTFIX_NGINX_BOM_2026-03-04.md` - Nginx BOM 修复
