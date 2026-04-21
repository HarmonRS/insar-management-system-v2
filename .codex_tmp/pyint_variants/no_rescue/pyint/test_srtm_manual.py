#!/usr/bin/env python
"""
测试手动处理 SRTM .hgt 文件的脚本

这个脚本演示如何在 Python < 3.12 环境下手动处理 SRTM 数据
"""

import os
import sys

print("=" * 80)
print("SRTM 手动处理功能测试")
print("=" * 80)

# 检查Python版本
print(f"\nPython版本: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

# 导入处理函数
from makedem import process_srtm_hgt_files, HAS_SRTM

print(f"\nsrtm库状态: {'已安装' if HAS_SRTM else '未安装 (需要Python >= 3.12)'}")

print("\n" + "=" * 80)
print("使用说明")
print("=" * 80)

print("""
步骤1: 下载SRTM .hgt文件
---------------------------
从以下网站下载所需区域的 .hgt 文件:

1. CSI-CGIAR SRTM (推荐,无需注册)
   https://srtm.csi.cgiar.org/
   
2. USGS EarthExplorer (需要注册)
   https://earthexplorer.usgs.gov/

文件命名规则:
- N39E116.hgt → 北纬39-40°, 东经116-117°
- N39W110.hgt → 北纬39-40°, 西经110-109°
- S10E120.hgt → 南纬10-9°, 东经120-121°

步骤2: 组织文件
---------------------------
将所有 .hgt 文件放在同一目录下:

mkdir -p ~/data/SRTM
mv N*.hgt S*.hgt ~/data/SRTM/

步骤3: 运行命令
---------------------------
使用以下命令生成DEM:

makedem.py -r 116/117/39/40 --dem-source srtm --srtm-data-dir ~/data/SRTM

或者直接调用Python函数:

from makedem import process_srtm_hgt_files

dem_file = process_srtm_hgt_files(
    west=116.0,
    south=39.0,
    east=117.0,
    north=40.0,
    srtm_data_dir='/path/to/srtm/data',
    save_path='/path/to/output'
)

""")

print("=" * 80)
print("示例: 为北京地区(116-117°E, 39-40°N)下载SRTM数据")
print("=" * 80)

print("""
需要下载的文件:
1. 访问 https://srtm.csi.cgiar.org/
2. 选择区域: 39-40°N, 116-117°E
3. 下载文件: N39E116.hgt

下载命令示例:
-------------""")
print(f"""
# 创建数据目录
mkdir -p ~/data/SRTM

# 假设已下载 N39E116.hgt 到 ~/Downloads
mv ~/Downloads/N39E116.hgt ~/data/SRTM/

# 生成DEM
makedem.py -r 116/117/39/40 --dem-source srtm --srtm-data-dir ~/data/SRTM

# 输出文件将保存为: SRTM_116_117_39_40.tif
""")

print("=" * 80)
print("注意事项")
print("=" * 80)

print("""
1. SRTM文件必须覆盖整个目标区域
   - 对于区域 116-117°E, 39-40°N
   - 需要 N39E116.hgt 文件

2. 文件格式
   - 支持 .hgt 文件 (未压缩)
   - 如果下载的是 .hgt.zip, 请先解压

3. 空数据区域
   - SRTM数据在海洋区域可能为空值
   - 程序会自动处理空值区域

4. Python版本兼容性
   - Python >= 3.12: 可使用 srtm 库(自动查询)
   - Python < 3.12: 使用手动处理方法(GDAL合并)
   - 两种方法结果相同,只是处理方式不同
""")

print("=" * 80)
