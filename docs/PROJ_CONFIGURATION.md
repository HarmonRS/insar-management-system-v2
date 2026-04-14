# PROJ 数据库配置指南

## 问题背景

InSAR 管理系统使用地理空间库（GDAL、pyproj）进行坐标转换和空间查询。这些库依赖 PROJ 数据库来执行坐标系转换。

**版本冲突问题**：
- PostgreSQL PostGIS 扩展自带 PROJ 数据库 **v2**（旧版本）
- GDAL/pyproj 期望 PROJ 数据库 **v3+**（新版本）
- 版本不匹配会导致警告，甚至**坐标转换错误**

## 配置方式

### 方式 1：自动配置（推荐）

**不需要任何配置**，系统会自动使用 pyproj 的 PROJ 数据库。

启动后端时，会看到：
```
[*] Configuring PROJ database...
[*] Auto-detected PROJ_LIB: C:\Users\Administrator\.conda\envs\InSAR\lib\site-packages\pyproj\proj_dir\share\proj
[*] PROJ version: 9.2.0
```

### 方式 2：手动配置（高级）

如果需要使用自定义的 PROJ 数据库，可以在 `.env` 文件中配置：

```bash
# PROJ 数据库路径
PROJ_LIB=C:\path\to\your\proj\data
```

**配置优先级**：
1. `.env` 中的 `PROJ_LIB`（如果设置）
2. pyproj 自动检测（默认）

## 验证配置

### 方法 1：查看启动日志

启动后端时，检查日志输出：

```bash
# 正确配置（使用 pyproj）
[*] Auto-detected PROJ_LIB: ...pyproj\proj_dir\share\proj
[*] PROJ version: 9.2.0

# 错误配置（使用 PostgreSQL）
[*] Using PROJ_LIB from .env: C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.6\proj
```

### 方法 2：运行诊断脚本

```bash
# 激活 InSAR 环境
conda activate InSAR

# 运行诊断
python scripts/check_proj.py
```

诊断脚本会检查：
- 环境变量 `PROJ_LIB` 的值
- PROJ 数据库文件是否存在
- PROJ 版本信息
- 坐标转换功能是否正常

### 方法 3：测试坐标转换

```python
import pyproj

# WGS84 to Web Mercator
transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
x, y = transformer.transform(116.4, 39.9)  # 北京坐标
print(f"WGS84 (116.4, 39.9) -> Web Mercator ({x:.2f}, {y:.2f})")
# 预期输出: WGS84 (116.4, 39.9) -> Web Mercator (12957588.73, 4851421.18)
```

## 常见问题

### Q1: 为什么不能使用 PostgreSQL 的 PROJ 数据库？

**A**: PostgreSQL PostGIS 自带的 PROJ 数据库是 v2 版本，而 GDAL/pyproj 期望 v3+ 版本。使用旧版本可能导致：
- 坐标转换精度下降
- 某些坐标系不支持
- 空间查询结果错误

### Q2: 如何知道当前使用的是哪个 PROJ 数据库？

**A**: 查看后端启动日志，或运行 `python scripts/check_proj.py`。

### Q3: 删除数据库重建后，PROJ 警告又出现了怎么办？

**A**: 这是正常的。删除数据库后，PostGIS 扩展会重新安装，系统环境变量可能被重置。重启后端即可自动修复。

### Q4: 我想使用自定义的 PROJ 数据库，如何配置？

**A**: 在 `.env` 文件中设置 `PROJ_LIB` 环境变量：

```bash
PROJ_LIB=D:\custom\proj\data
```

确保该目录包含 `proj.db` 文件。

### Q5: 如何确认 PROJ 配置生效？

**A**: 执行雷达数据扫描操作，检查日志中是否还有 PROJ 警告：

```
GDAL signalled an error: err_no=1, msg='PROJ: proj_identify: ... DATABASE.LAYOUT.VERSION.MINOR = 2 ...'
```

如果没有此警告，说明配置生效。

## 影响范围

正确配置 PROJ 数据库对以下功能至关重要：

- ✅ **雷达数据扫描**：解析影像坐标系
- ✅ **空间查询**：隐患点与影像范围匹配
- ✅ **D-InSAR 处理**：地理配准和坐标转换
- ✅ **水体监测**：地理编码
- ✅ **地图显示**：坐标系转换（WGS84 ↔ Web Mercator）

## 技术细节

### PROJ 数据库版本对比

| 来源 | 版本 | 路径 | 推荐 |
|------|------|------|------|
| PostgreSQL PostGIS | v2 | `C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.6\proj` | ❌ |
| pyproj (conda) | v9.2 | `C:\Users\...\envs\InSAR\lib\site-packages\pyproj\proj_dir\share\proj` | ✅ |

### 环境变量优先级

1. 程序内设置的 `os.environ["PROJ_LIB"]`（最高优先级）
2. 系统环境变量 `PROJ_LIB`
3. pyproj 默认路径

我们的配置策略是在程序启动时**强制覆盖**环境变量，确保使用正确的 PROJ 数据库。

## 参考资料

- [PROJ 官方文档](https://proj.org/)
- [pyproj 文档](https://pyproj4.github.io/pyproj/)
- [PostGIS 文档](https://postgis.net/)
