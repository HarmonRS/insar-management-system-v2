#! /usr/bin/env python
#################################################################
###  从本地 FABDEM 瓦片库生成 GAMMA 格式 DEM                   ###
###  输入: 经纬度范围 + 本地 FABDEM ZIP 目录                    ###
###  输出: .dem + .dem.par (与 makedem.py 输出完全一致)          ###
###  Author: Cascade AI + zouyuandong                          ###
###  Date  : 2026-03-15                                        ###
#################################################################

import os
import sys
import re
import math
import glob
import zipfile
import tempfile
import shutil
import argparse
import subprocess
import numpy as np
from pathlib import Path


# ========================= GAMMA 参数文件写入 =========================

def write_dempar_file(filepath, corner_lon, corner_lat, post_lon, post_lat,
                      width, nlines, data_format='REAL*4'):
    """写入 GAMMA DEM 参数文件 (.dem.par)，与 makedem.py 格式完全一致"""
    with open(filepath, 'w') as f:
        f.write("Gamma DIFF&GEO DEM/MAP parameter file\n")
        f.write("title:\tIMPORTED DEM FROM FABDEM V1-2\n")
        f.write("DEM_projection:     EQA\n")
        f.write("data_format:        %s\n" % data_format)
        f.write("DEM_hgt_offset:          0.00000\n")
        f.write("DEM_scale:               1.00000\n")
        f.write("width:                %s\n" % str(int(width)))
        f.write("nlines:               %s\n" % str(int(nlines)))
        f.write("corner_lat:   %s  decimal degrees\n" % str(float(corner_lat)))
        f.write("corner_lon:   %s  decimal degrees\n" % str(float(corner_lon)))
        f.write("post_lat:   %s  decimal degrees\n" % str(float(post_lat)))
        f.write("post_lon:   %s  decimal degrees\n" % str(float(post_lon)))
        f.write("\n")
        f.write("ellipsoid_name: WGS 84\n")
        f.write("ellipsoid_ra:        6378137.000   m\n")
        f.write("ellipsoid_reciprocal_flattening:  298.2572236\n")
        f.write("\n")
        f.write("datum_name: WGS 1984\n")
        f.write("datum_shift_dx:              0.000   m\n")
        f.write("datum_shift_dy:              0.000   m\n")
        f.write("datum_shift_dz:              0.000   m\n")
        f.write("datum_scale_m:         0.00000e+00\n")
        f.write("datum_rotation_alpha:  0.00000e+00   arc-sec\n")
        f.write("datum_rotation_beta:   0.00000e+00   arc-sec\n")
        f.write("datum_rotation_gamma:  0.00000e+00   arc-sec\n")
        f.write("datum_country_list Global Definition, WGS84, World\n")
        f.write("\n")


# ========================= FABDEM 瓦片索引逻辑 =========================

def parse_fabdem_zip_name(zip_name):
    """解析 FABDEM ZIP 文件名，提取覆盖的经纬度范围

    示例: N30E120-N40E130_FABDEM_V1-2.zip → (lat_min=30, lon_min=120, lat_max=40, lon_max=130)
          S10W080-S00W070_FABDEM_V1-2.zip → (lat_min=-10, lon_min=-80, lat_max=0, lon_max=-70)
    """
    basename = Path(zip_name).stem  # N30E120-N40E130_FABDEM_V1-2
    match = re.match(
        r'([NS])(\d+)([EW])(\d+)-([NS])(\d+)([EW])(\d+)_FABDEM',
        basename
    )
    if not match:
        return None

    lat1 = int(match.group(2)) * (1 if match.group(1) == 'N' else -1)
    lon1 = int(match.group(4)) * (1 if match.group(3) == 'E' else -1)
    lat2 = int(match.group(6)) * (1 if match.group(5) == 'N' else -1)
    lon2 = int(match.group(8)) * (1 if match.group(7) == 'E' else -1)

    return {
        'lat_min': min(lat1, lat2),
        'lat_max': max(lat1, lat2),
        'lon_min': min(lon1, lon2),
        'lon_max': max(lon1, lon2),
    }


def tile_name_for_coord(lat, lon):
    """根据经纬度生成 FABDEM 1°×1° 瓦片文件名

    参数 lat, lon 为瓦片左下角整数坐标
    示例: (31, 121) → 'N31E121_FABDEM_V1-2.tif'
          (-1, -70) → 'S01W070_FABDEM_V1-2.tif'
    """
    lat_prefix = 'N' if lat >= 0 else 'S'
    lon_prefix = 'E' if lon >= 0 else 'W'
    return f"{lat_prefix}{abs(lat):02d}{lon_prefix}{abs(lon):03d}_FABDEM_V1-2.tif"


def find_needed_tiles(west, south, east, north):
    """计算覆盖目标区域所需的全部 1°×1° 瓦片坐标列表

    返回: [(lat, lon), ...] 瓦片左下角坐标
    """
    # 向下取整到最近的整数度
    lat_start = math.floor(south)
    lat_end = math.ceil(north)  # 不含
    lon_start = math.floor(west)
    lon_end = math.ceil(east)

    tiles = []
    for lat in range(lat_start, lat_end):
        for lon in range(lon_start, lon_end):
            tiles.append((lat, lon))
    return tiles


def find_zip_for_tile(lat, lon, fabdem_dir):
    """查找包含指定 1°×1° 瓦片的 ZIP 文件

    FABDEM ZIP 按 10°×10° 分块，文件名编码了覆盖范围
    """
    fabdem_path = Path(fabdem_dir)
    zip_files = sorted(fabdem_path.glob('*_FABDEM_V1-2.zip'))

    for zf in zip_files:
        bounds = parse_fabdem_zip_name(zf.name)
        if bounds is None:
            continue
        if (bounds['lat_min'] <= lat < bounds['lat_max'] and
                bounds['lon_min'] <= lon < bounds['lon_max']):
            return zf
    return None


def extract_tiles_from_zips(tile_coords, fabdem_dir, extract_dir):
    """从 FABDEM ZIP 文件中提取所需的 1°×1° GeoTIFF 瓦片

    参数:
        tile_coords: [(lat, lon), ...] 需要提取的瓦片坐标
        fabdem_dir: FABDEM ZIP 文件所在目录
        extract_dir: 解压目标目录

    返回:
        提取成功的 GeoTIFF 文件路径列表
    """
    extract_path = Path(extract_dir)
    extract_path.mkdir(parents=True, exist_ok=True)

    # 按 ZIP 文件分组，避免重复打开同一个 ZIP
    zip_to_tiles = {}
    missing_tiles = []

    for lat, lon in tile_coords:
        zip_file = find_zip_for_tile(lat, lon, fabdem_dir)
        if zip_file is None:
            tile_name = tile_name_for_coord(lat, lon)
            print(f"  ⚠ 未找到包含 {tile_name} 的 ZIP 文件 (lat={lat}, lon={lon})")
            missing_tiles.append((lat, lon))
            continue

        zip_key = str(zip_file)
        if zip_key not in zip_to_tiles:
            zip_to_tiles[zip_key] = []
        zip_to_tiles[zip_key].append((lat, lon))

    # 逐个 ZIP 文件提取
    extracted_files = []
    for zip_path, coords in zip_to_tiles.items():
        zip_name = Path(zip_path).name
        print(f"  📦 从 {zip_name} 提取 {len(coords)} 个瓦片...")

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zip_contents = zf.namelist()

                for lat, lon in coords:
                    tile_name = tile_name_for_coord(lat, lon)

                    if tile_name in zip_contents:
                        zf.extract(tile_name, extract_dir)
                        out_file = extract_path / tile_name
                        if out_file.exists() and out_file.stat().st_size > 0:
                            extracted_files.append(str(out_file))
                            print(f"    ✓ {tile_name}")
                        else:
                            print(f"    ✗ {tile_name} 提取后为空")
                    else:
                        # 海洋区域可能没有对应瓦片（正常）
                        print(f"    - {tile_name} 不在 ZIP 中（可能是海洋区域）")
        except Exception as e:
            print(f"    ✗ 打开 {zip_name} 失败: {e}")

    if missing_tiles:
        print(f"\n  ⚠ 共 {len(missing_tiles)} 个瓦片未找到对应的 ZIP 文件")

    return extracted_files


# ========================= 瓦片拼接 + GAMMA 格式转换（一步完成） =========================

def _parse_gdalinfo(gdalinfo_text):
    """从 gdalinfo 输出解析元数据"""
    width = nlines = None
    corner_lon = corner_lat = post_lon = post_lat = None
    for line in gdalinfo_text.splitlines():
        if 'Size is' in line:
            parts = line.split('Size is')[1].strip().split(',')
            width = int(parts[0].strip())
            nlines = int(parts[1].strip())
        elif 'Origin =' in line:
            parts = line.split('(')[1].split(')')[0].split(',')
            corner_lon = float(parts[0].strip())
            corner_lat = float(parts[1].strip())
        elif 'Pixel Size =' in line:
            parts = line.split('(')[1].split(')')[0].split(',')
            post_lon = float(parts[0].strip())
            post_lat = float(parts[1].strip())
    return width, nlines, corner_lon, corner_lat, post_lon, post_lat


def tiles_to_gamma_dem(tif_files, output_name, west, south, east, north, byteorder='big'):
    """从多个 GeoTIFF 瓦片生成 GAMMA 格式 DEM (.dem + .dem.par)

    流程: srtm2dem 逐个转换 → mosaic 合并（全部使用 GAMMA 原生工具）

    参数:
        tif_files: GeoTIFF 瓦片文件路径列表
        output_name: 输出文件名前缀（不含扩展名）
        west, south, east, north: 裁剪范围（度）
        byteorder: 字节序（未使用，srtm2dem 自动处理大端）
    返回:
        (dem_file, dem_par_file) 或 (None, None)
    """
    if not tif_files:
        print("  ✗ 没有可用的瓦片文件")
        return None, None

    Path(output_name).parent.mkdir(parents=True, exist_ok=True)
    dem_file = output_name + '.dem'
    dem_par_file = output_name + '.dem.par'

    # 临时目录放 /tmp/（本地盘），srtm2dem 单瓦片很快
    import tempfile as _tf
    _tmp_dir = _tf.mkdtemp(prefix='gamma_dem_conv_')

    # ---- 步骤1: srtm2dem 逐个将 GeoTIFF 转为 GAMMA 格式 ----
    n = len(tif_files)
    print(f"  [1/2] srtm2dem 转换 {n} 个瓦片...")
    gamma_tiles = []  # [(dem_path, dem_par_path), ...]

    for i, tif in enumerate(tif_files):
        tile_name = Path(tif).stem
        tile_dem = os.path.join(_tmp_dir, f'{tile_name}.dem')
        tile_par = os.path.join(_tmp_dir, f'{tile_name}.dem.par')

        # gflg=3: 不做大地水准面校正, NODATA 替换为 0.0
        cmd = f'srtm2dem {tif} {tile_dem} {tile_par} 3'
        ret = os.system(cmd + ' > /dev/null 2>&1')

        if ret == 0 and os.path.exists(tile_dem) and os.path.getsize(tile_dem) > 0:
            gamma_tiles.append((tile_dem, tile_par))
            print(f"    ✓ [{i+1}/{n}] {tile_name}")
        else:
            print(f"    ✗ [{i+1}/{n}] {tile_name} srtm2dem 失败")

    if not gamma_tiles:
        print("  ✗ 所有瓦片转换失败")
        shutil.rmtree(_tmp_dir, ignore_errors=True)
        return None, None

    print(f"  成功转换 {len(gamma_tiles)}/{n} 个瓦片")

    # ---- 步骤2: mosaic 合并所有 GAMMA DEM 瓦片 ----
    if len(gamma_tiles) == 1:
        # 只有一个瓦片，直接复制
        shutil.copy2(gamma_tiles[0][0], dem_file)
        shutil.copy2(gamma_tiles[0][1], dem_par_file)
        print(f"  [2/2] 单瓦片，直接输出")
    else:
        print(f"  [2/2] mosaic 合并 {len(gamma_tiles)} 个 GAMMA DEM...")
        # 构建 mosaic 命令: mosaic nfiles dem1 par1 dem2 par2 ... dem_out par_out mode format
        cmd_parts = ['mosaic', str(len(gamma_tiles))]
        for tile_dem, tile_par in gamma_tiles:
            cmd_parts.append(tile_dem)
            cmd_parts.append(tile_par)
        cmd_parts.extend([dem_file, dem_par_file, '1', '0'])
        # mode=1: 重叠区取平均, format=0: FLOAT

        cmd_str = ' '.join(cmd_parts)
        ret = os.system(cmd_str)

        if ret != 0 or not os.path.exists(dem_file) or os.path.getsize(dem_file) == 0:
            print(f"  ✗ mosaic 失败 (exit={ret})")
            shutil.rmtree(_tmp_dir, ignore_errors=True)
            return None, None

    # 清理临时目录
    shutil.rmtree(_tmp_dir, ignore_errors=True)

    size_mb = os.path.getsize(dem_file) / (1024 * 1024)
    print(f"  ✓ {dem_file} ({size_mb:.1f} MB)")
    print(f"  ✓ {dem_par_file}")

    return dem_file, dem_par_file


# ========================= 主流程 =========================

INTRODUCTION = '''
================================================================================
  make_local_dem.py — 从本地 FABDEM 瓦片库生成 GAMMA 格式 DEM

  功能:
    1. 根据经纬度范围自动查找所需的 FABDEM 1°×1° 瓦片
    2. 从 10°×10° ZIP 包中提取瓦片
    3. 使用 GDAL 拼接并裁剪到目标范围
    4. 转换为 GAMMA 格式 (.dem + .dem.par)

  输出与 makedem.py 完全一致，可直接用于 generate_rdc_dem.py
================================================================================
'''

EXAMPLE = """
用法:
  # 方式1: 指定经纬度范围
  make_local_dem.py -r 120/123/30/32 -f /mnt/ZYD/全球FABDEM -o output_dem

  # 方式2: 从 SLC 参数文件自动确定范围
  make_local_dem.py -s master.slc.par -f /mnt/ZYD/全球FABDEM -o output_dem

  # 方式3: 从 PyINT 模板文件读取（集成到工作流）
  make_local_dem.py --template shanghaiT171F128S1A -f /mnt/ZYD/全球FABDEM
"""


def cmdLineParse():
    parser = argparse.ArgumentParser(
        description='从本地 FABDEM 瓦片库生成 GAMMA 格式 DEM',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=INTRODUCTION + '\n' + EXAMPLE
    )

    parser.add_argument('-r', dest='region',
                        help='研究区域范围: west/east/south/north (如: 120/123/30/32)')
    parser.add_argument('-s', dest='slc_par',
                        help='SLC 参数文件路径（自动从中提取研究区域范围）')
    parser.add_argument('-f', '--fabdem-dir', dest='fabdem_dir', required=True,
                        help='本地 FABDEM ZIP 文件目录 (如: /mnt/ZYD/全球FABDEM)')
    parser.add_argument('-o', '--output', dest='output_name', default=None,
                        help='输出文件名前缀（不含扩展名）[默认: 工作目录/out]')
    parser.add_argument('--dir', dest='work_dir', default=None,
                        help='工作目录 [默认: 当前目录]')
    parser.add_argument('--template', dest='template_name', default=None,
                        help='PyINT 项目名（从模板文件读取 DEM 路径和 SLC 位置）')
    parser.add_argument('--byteorder', dest='byteorder', choices=['big', 'little'],
                        default='big',
                        help='输出 DEM 字节序 [默认: big (GAMMA 标准)]')
    parser.add_argument('--margin', dest='margin', type=float, default=1.0,
                        help='在 SLC 覆盖范围外扩展的余量（度）[默认: 1.0]')

    return parser.parse_args()


def get_region_from_slc_par(slc_par_file):
    """从 GAMMA SLC 参数文件提取研究区域范围"""
    corners_txt = 'corners_tmp.txt'
    call_str = f"SLC_corners {slc_par_file} > {corners_txt}"
    os.system(call_str)

    if not os.path.isfile(corners_txt):
        print(f"✗ SLC_corners 执行失败")
        return None

    with open(corners_txt, 'r') as f:
        lines = f.readlines()

    os.remove(corners_txt)

    # 解析 SLC_corners 输出（第 9-10 行包含 lat/lon 范围）
    try:
        lat_line = lines[8]
        lon_line = lines[9]
        min_lat = float(lat_line.split(':')[1].split('max.')[0].strip())
        max_lat = float(lat_line.split(':')[2].strip())
        min_lon = float(lon_line.split(':')[1].split('max.')[0].strip())
        max_lon = float(lon_line.split(':')[2].strip())
        return min_lon, min_lat, max_lon, max_lat
    except (IndexError, ValueError) as e:
        print(f"✗ 解析 SLC_corners 输出失败: {e}")
        return None


def get_region_from_template(project_name, margin=1.0):
    """从 PyINT 模板文件获取区域范围（通过 master SLC 参数文件）"""
    try:
        from pyint import _utils as ut
    except ImportError:
        print("✗ 无法导入 pyint._utils，请确保 PyINT 在 PYTHONPATH 中")
        return None, None

    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    if not scratchDir or not templateDir:
        print("✗ 环境变量 SCRATCHDIR 或 TEMPLATEDIR 未设置")
        return None, None

    template_file = Path(templateDir) / f"{project_name}.template"
    if not template_file.exists():
        print(f"✗ 模板文件不存在: {template_file}")
        return None, None

    template_dict = ut.update_template(str(template_file))
    master_date = template_dict['masterDate']
    slc_dir = Path(scratchDir) / project_name / 'SLC'
    master_slc_par = slc_dir / master_date / f"{master_date}.slc.par"

    if not master_slc_par.exists():
        print(f"✗ Master SLC 参数文件不存在: {master_slc_par}")
        return None, None

    region = get_region_from_slc_par(str(master_slc_par))
    if region is None:
        return None, None

    west, south, east, north = region
    # 外扩 margin 度并取整
    west = math.floor(west - margin)
    south = math.floor(south - margin)
    east = math.ceil(east + margin)
    north = math.ceil(north + margin)

    # 确定输出路径（使用模板中 DEM 路径或默认路径）
    dem_dir = os.getenv('DEMDIR', '')
    if 'DEM' in template_dict and template_dict['DEM'].strip():
        output_name = template_dict['DEM'].replace('.dem', '')
    else:
        dem_out_dir = Path(dem_dir) / project_name
        dem_out_dir.mkdir(parents=True, exist_ok=True)
        output_name = str(dem_out_dir / project_name)

    return (west, south, east, north), output_name


def main():
    args = cmdLineParse()

    # 确定工作目录
    if args.work_dir:
        work_dir = Path(args.work_dir)
    else:
        work_dir = Path.cwd()
    work_dir.mkdir(parents=True, exist_ok=True)

    # 确定 FABDEM 目录
    fabdem_dir = Path(args.fabdem_dir)
    if not fabdem_dir.is_dir():
        print(f"✗ FABDEM 目录不存在: {fabdem_dir}")
        sys.exit(1)

    zip_count = len(list(fabdem_dir.glob('*_FABDEM_V1-2.zip')))
    print(f"✓ FABDEM 目录: {fabdem_dir} ({zip_count} 个 ZIP 文件)")

    # 确定研究区域
    output_name = args.output_name
    region = None

    if args.template_name:
        # 从 PyINT 模板获取
        result = get_region_from_template(args.template_name, args.margin)
        if result[0] is not None:
            region = result[0]
            if output_name is None:
                output_name = result[1]
        else:
            print("✗ 无法从模板文件获取区域范围")
            sys.exit(1)

    elif args.slc_par:
        # 从 SLC 参数文件获取
        slc_region = get_region_from_slc_par(args.slc_par)
        if slc_region is None:
            sys.exit(1)
        west, south, east, north = slc_region
        west = math.floor(west - args.margin)
        south = math.floor(south - args.margin)
        east = math.ceil(east + args.margin)
        north = math.ceil(north + args.margin)
        region = (west, south, east, north)

    elif args.region:
        # 从命令行参数解析
        parts = args.region.split('/')
        if len(parts) != 4:
            print("✗ 区域格式错误，应为: west/east/south/north")
            sys.exit(1)
        west, east, south, north = [float(x) for x in parts]
        region = (west, south, east, north)

    else:
        print("✗ 必须指定研究区域: -r, -s 或 --template")
        sys.exit(1)

    if output_name is None:
        output_name = str(work_dir / 'out')

    # 确保输出目录存在
    Path(output_name).parent.mkdir(parents=True, exist_ok=True)

    west, south, east, north = region

    print(f"\n{'='*70}")
    print(f"  从本地 FABDEM 生成 GAMMA DEM")
    print(f"{'='*70}")
    print(f"  区域范围: {west}°E ~ {east}°E, {south}°N ~ {north}°N")
    print(f"  FABDEM 目录: {fabdem_dir}")
    print(f"  输出文件: {output_name}.dem / {output_name}.dem.par")
    print(f"  字节序: {args.byteorder}")
    print(f"{'='*70}\n")

    # 1. 计算所需瓦片
    tiles = find_needed_tiles(west, south, east, north)
    print(f"[1/4] 需要 {len(tiles)} 个 1°×1° 瓦片\n")

    # 2. 从 ZIP 中提取瓦片
    print(f"[2/4] 从 FABDEM ZIP 文件中提取瓦片...")
    temp_dir = tempfile.mkdtemp(prefix='fabdem_tiles_')
    try:
        tif_files = extract_tiles_from_zips(tiles, str(fabdem_dir), temp_dir)

        if not tif_files:
            print("\n✗ 没有提取到任何瓦片文件，请检查 FABDEM 目录和区域范围")
            shutil.rmtree(temp_dir, ignore_errors=True)
            sys.exit(1)

        print(f"\n  共提取 {len(tif_files)}/{len(tiles)} 个瓦片\n")

        # 3-4. 一步完成: VRT → ENVI 二进制 → GAMMA .dem
        print(f"[3/4] 拼接裁剪 + 转换 GAMMA 格式...")
        dem_file, dem_par_file = tiles_to_gamma_dem(
            tif_files, output_name, west, south, east, north, args.byteorder
        )

        if dem_file is None:
            print("\n✗ DEM 生成失败")
            shutil.rmtree(temp_dir, ignore_errors=True)
            sys.exit(1)

    finally:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)

    # 完成
    print(f"\n{'='*70}")
    print(f"  ✓ FABDEM → GAMMA DEM 转换完成!")
    print(f"{'='*70}")
    print(f"  DEM 文件:   {dem_file}")
    print(f"  参数文件:   {dem_par_file}")
    print(f"  字节序:     {args.byteorder} endian")
    print(f"  可直接用于: generate_rdc_dem.py")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    main()
