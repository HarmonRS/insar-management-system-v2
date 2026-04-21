#!/usr/bin/env python3
"""
将 HDF5 文件的坐标系统从 UTM 转换为 WGS84 地理坐标
同时保持与 MintPy tsview.py 的兼容性
"""

import h5py
import numpy as np
import copy
import argparse
import os

def utm_to_wgs84(easting, northing, zone, northern=True):
    """
    将 UTM 坐标转换为 WGS84 大地坐标
    """
    # UTM 参数
    a = 6378137.0  # WGS84 长半轴
    e = 0.081819190842622  # WGS84 第一偏心率
    k0 = 0.9996  # 比例因子

    # 中央经线
    lon0 = (zone - 1) * 6 - 180 + 3

    # 调整东伪偏移
    x = easting - 500000.0

    # 如果是南半球，调整北伪偏移
    if not northern:
        y = northing - 10000000.0
    else:
        y = northing

    # M = y / k0
    mu = y / (a * (1 - e**2 / 4 - 3 * e**4 / 64 - 5 * e**6 / 256) * k0)

    e1 = (1 - np.sqrt(1 - e**2)) / (1 + np.sqrt(1 - e**2))

    # 纬度计算
    N1 = a / np.sqrt(1 - e**2 * np.sin(mu)**2)
    T1 = np.tan(mu)**2
    C1 = e1**2 * np.cos(mu)**2
    R1 = a * (1 - e**2) / (1 - e**2 * np.sin(mu)**2)**(3/2)
    D = x / (N1 * k0)

    lat = mu - N1 * np.tan(mu) / R1 * (D**2 / 2 + (5 + 3 * T1 + 10 * C1 - 4 * C1**2 - 9 * e1**2) * D**4 / 24 + (61 + 90 * T1 + 298 * C1 + 45 * T1**2 - 252 * e1**2 - 3 * C1**2) * D**6 / 720)

    # 经度计算
    lon = lon0 * np.pi / 180 + D * (1 + (1 + 3 * e1**2 + 2 * e1**4) * D**2 / 6 + (2 - e1**2) * D**4 / 120) / np.cos(mu)

    # 转换为角度
    lat_deg = np.degrees(lat)
    lon_deg = np.degrees(lon)

    return lon_deg, lat_deg


def main():
    parser = argparse.ArgumentParser(
        description='将 HDF5 文件转换为 WGS84 地理坐标（与 MintPy 兼容）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python convert_to_wgs84.py input.h5 -o output.h5

说明:
  脚本会将文件完全转换为 WGS84 地理坐标系统，
  同时设置正确的属性以保持与 MintPy 的兼容性。
        '''
    )
    parser.add_argument('input', help='输入 HDF5 文件路径')
    parser.add_argument('-o', '--output', help='输出 HDF5 文件路径（默认：输入文件名_WGS84.h5）')

    args = parser.parse_args()

    # 输入和输出文件
    input_file = args.input

    if args.output:
        output_file = args.output
    else:
        # 自动生成输出文件名
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_WGS84{ext}"

    print(f"正在读取文件: {input_file}")
    f_in = h5py.File(input_file, 'r')

    # 读取所有数据集
    datasets = {}
    for key in f_in.keys():
        datasets[key] = f_in[key][:]
        print(f"  读取数据集: {key}, 形状: {datasets[key].shape}")

    # 读取所有属性
    attrs = dict(f_in.attrs)
    print(f"\n共有 {len(attrs)} 个属性")

    # 解析 UTM 坐标信息
    x_first = float(attrs['X_FIRST'])
    y_first = float(attrs['Y_FIRST'])
    x_step = float(attrs['X_STEP'])
    y_step = float(attrs['Y_STEP'])
    width = int(attrs['WIDTH'])
    length = int(attrs['LENGTH'])
    utm_zone = attrs['UTM_ZONE']

    # 获取参考点位置（像素坐标）
    ref_x = int(attrs.get('REF_X', 0))
    ref_y = int(attrs.get('REF_Y', 0))

    # 转换 UTM Zone
    zone_num = int(''.join(filter(str.isdigit, utm_zone)))
    is_northern = 'N' in utm_zone.upper()

    print(f"\n原始坐标系统:")
    print(f"  UTM Zone: {utm_zone} (EPSG: {attrs['EPSG']})")
    print(f"  左上角 UTM: ({x_first}, {y_first})")
    print(f"  步长: X={x_step}m, Y={y_step}m")
    print(f"  参考点像素: ({ref_x}, {ref_y})")

    # 生成网格坐标
    x_coords = x_first + np.arange(width) * x_step
    y_coords = y_first + np.arange(length) * y_step

    # 转换为 WGS84
    print(f"\n正在转换为 WGS84...")
    lon_grid, lat_grid = np.meshgrid(x_coords, y_coords)
    wgs84_lon, wgs84_lat = utm_to_wgs84(lon_grid, lat_grid, zone_num, is_northern)

    # 计算新的坐标参数（注意：lon 是经度，lat 是纬度）
    # X 对应经度，Y 对应纬度
    lon_first = wgs84_lon[0, 0]
    lat_first = wgs84_lat[0, 0]
    lon_step = abs(wgs84_lon[0, 1] - wgs84_lon[0, 0]) if width > 1 else 0
    lat_step = abs(wgs84_lat[1, 0] - wgs84_lat[0, 0]) if length > 1 else 0

    # 计算参考点的经纬度
    # REF_X 对应 X 轴（经度），REF_Y 对应 Y 轴（纬度）
    ref_lon = wgs84_lon[ref_y, ref_x]  # 经度
    ref_lat = wgs84_lat[ref_y, ref_x]  # 纬度

    # 更新属性 - 完全转换为 WGS84
    attrs_new = copy.deepcopy(attrs)

    # 更新坐标系统相关属性
    attrs_new['EPSG'] = 4326  # WGS84
    attrs_new['X_FIRST'] = lon_first  # 经度
    attrs_new['Y_FIRST'] = lat_first  # 纬度
    attrs_new['X_STEP'] = lon_step
    attrs_new['Y_STEP'] = -lat_step  # 保持Y方向向下为负
    attrs_new['X_UNIT'] = 'degrees'
    attrs_new['Y_UNIT'] = 'degrees'

    # 更新参考坐标 - 现在是真正的经纬度
    attrs_new['REF_LON'] = ref_lon
    attrs_new['REF_LAT'] = ref_lat

    # 保留原始 UTM 信息作为备份（带 _ORIG 后缀）
    attrs_new['UTM_ZONE_ORIG'] = attrs_new.get('UTM_ZONE', '')
    attrs_new['EPSG_ORIG'] = attrs_new.get('EPSG', '')

    # 关键：移除或清空 UTM_ZONE 属性，让 MintPy 识别为地理坐标系统
    if 'UTM_ZONE' in attrs_new:
        del attrs_new['UTM_ZONE']

    # 更新角点坐标
    attrs_new['LAT_REF1'] = lat_first
    attrs_new['LAT_REF2'] = wgs84_lat[0, -1]
    attrs_new['LAT_REF3'] = wgs84_lat[-1, 0]
    attrs_new['LAT_REF4'] = wgs84_lat[-1, -1]
    attrs_new['LON_REF1'] = lon_first
    attrs_new['LON_REF2'] = wgs84_lon[0, -1]
    attrs_new['LON_REF3'] = wgs84_lon[-1, 0]
    attrs_new['LON_REF4'] = wgs84_lon[-1, -1]

    # 设置其他相关属性
    attrs_new['COORD_SYSTEM'] = 'GEO'  # 地理坐标系统

    print(f"\n新坐标系统 (WGS84):")
    print(f"  EPSG: {attrs_new['EPSG']}")
    print(f"  左上角: ({attrs_new['X_FIRST']:.6f}, {attrs_new['Y_FIRST']:.6f})")
    print(f"  步长: X={attrs_new['X_STEP']:.6f}°, Y={attrs_new['Y_STEP']:.6f}°")
    print(f"  参考点经纬度: ({attrs_new['REF_LON']:.6f}, {attrs_new['REF_LAT']:.6f})")
    print(f"  参考点像素: ({ref_x}, {ref_y})")

    # 创建新的 HDF5 文件
    print(f"\n正在写入文件: {output_file}")
    f_out = h5py.File(output_file, 'w')

    # 写入所有数据集
    for key, data in datasets.items():
        f_out.create_dataset(key, data=data)
        print(f"  写入数据集: {key}")

    # 写入所有属性
    for key, value in attrs_new.items():
        f_out.attrs[key] = value

    f_in.close()
    f_out.close()

    print(f"\n完成！转换后的文件已保存为: {output_file}")
    print("\n说明:")
    print("  - 文件已完全转换为 WGS84 地理坐标系统")
    print("  - 所有坐标属性已更新为经纬度")
    print("  - 原始 UTM 信息已保存为 _ORIG 后缀的属性")
    print("  - 应该与 MintPy tsview.py 兼容")


if __name__ == '__main__':
    main()