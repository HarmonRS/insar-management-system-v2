"""
检查 PROJ 数据库配置和版本
"""
import os
import sys

print("=" * 60)
print("PROJ 数据库诊断")
print("=" * 60)

# 1. 检查环境变量
print("\n1. 环境变量检查:")
proj_lib = os.environ.get("PROJ_LIB")
print(f"   PROJ_LIB = {proj_lib}")
if proj_lib:
    print(f"   路径存在: {os.path.exists(proj_lib)}")
    if os.path.exists(proj_lib):
        proj_db = os.path.join(proj_lib, "proj.db")
        print(f"   proj.db 存在: {os.path.exists(proj_db)}")

# 2. 检查 GDAL 配置
print("\n2. GDAL 配置:")
try:
    from osgeo import gdal
    gdal_data = gdal.GetConfigOption("GDAL_DATA")
    print(f"   GDAL_DATA = {gdal_data}")

    if gdal_data:
        proj_lib_auto = os.path.join(os.path.dirname(gdal_data), "proj")
        print(f"   自动推断 PROJ_LIB = {proj_lib_auto}")
        print(f"   路径存在: {os.path.exists(proj_lib_auto)}")
        if os.path.exists(proj_lib_auto):
            proj_db = os.path.join(proj_lib_auto, "proj.db")
            print(f"   proj.db 存在: {os.path.exists(proj_db)}")
except ImportError:
    print("   GDAL 未安装")

# 3. 检查 PostgreSQL PROJ
print("\n3. PostgreSQL PostGIS PROJ:")
pg_proj = r"C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.6\proj\proj.db"
print(f"   路径: {pg_proj}")
print(f"   存在: {os.path.exists(pg_proj)}")

# 4. 检查 PROJ 版本
print("\n4. PROJ 库版本:")
try:
    import pyproj
    print(f"   pyproj 版本: {pyproj.__version__}")
    print(f"   PROJ 版本: {pyproj.proj_version_str}")
    print(f"   PROJ 数据目录: {pyproj.datadir.get_data_dir()}")
except ImportError:
    print("   pyproj 未安装")

# 5. 测试坐标转换
print("\n5. 坐标转换测试:")
try:
    import pyproj
    # WGS84 to Web Mercator
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(116.4, 39.9)  # 北京坐标
    print(f"   WGS84 (116.4, 39.9) -> Web Mercator ({x:.2f}, {y:.2f})")
    print("   ✅ 坐标转换正常")
except Exception as e:
    print(f"   ❌ 坐标转换失败: {e}")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
