#!/usr/bin/env python3
"""
SBAS-InSAR时间序列数据提取脚本
从H5文件中提取时间序列数据、日期和基线信息
"""

import h5py
import numpy as np
import os
import argparse
from osgeo import gdal


def save_as_geotiff(data, output_file, nodata=-9999):
    """
    将数据保存为GeoTIFF格式

    Parameters:
    -----------
    data : numpy.ndarray
        要保存的数据（2D数组）
    output_file : str
        输出文件路径
    nodata : float
        无效值
    """
    # 设置无效值
    data_masked = np.where(np.isnan(data), nodata, data)

    # 获取数据形状
    rows, cols = data_masked.shape

    # 创建GeoTIFF驱动
    driver = gdal.GetDriverByName('GTiff')

    # 创建数据集
    dataset = driver.Create(output_file, cols, rows, 1, gdal.GDT_Float32)

    # 设置波段
    band = dataset.GetRasterBand(1)
    band.WriteArray(data_masked)
    band.SetNoDataValue(nodata)

    # 设置无数据值
    band.FlushCache()

    # 关闭数据集
    dataset = None

    print(f"  已保存: {output_file}")


def extract_timeseries(h5_file, output_dir='./output'):
    """
    提取SBAS-InSAR时间序列数据

    Parameters:
    -----------
    h5_file : str
        H5文件路径
    output_dir : str
        输出目录
    """

    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    # 读取H5文件
    print(f"正在读取文件: {h5_file}")
    with h5py.File(h5_file, 'r') as f:
        # 提取日期信息
        dates = f['date'][:]
        # 将bytes转为字符串
        dates_str = [d.decode('utf-8') if isinstance(d, bytes) else d for d in dates]
        print(f"时间序列长度: {len(dates_str)}")
        print(f"日期范围: {dates_str[0]} 到 {dates_str[-1]}")

        # 提取基线信息
        bperp = f['bperp'][:]
        print(f"基线形状: {bperp.shape}")

        # 提取时间序列数据
        timeseries = f['timeseries'][:]
        print(f"时间序列数据形状: {timeseries.shape} (时间, 行, 列)")

        # 保存日期信息
        date_file = os.path.join(output_dir, 'dates.txt')
        with open(date_file, 'w') as f_out:
            for i, date in enumerate(dates_str):
                f_out.write(f"{i+1} {date}\n")
        print(f"日期信息已保存到: {date_file}")

        # 保存基线信息
        bperp_file = os.path.join(output_dir, 'bperp.npy')
        np.save(bperp_file, bperp)
        print(f"基线信息已保存到: {bperp_file}")

        # 保存时间序列数据（numpy格式）
        timeseries_file = os.path.join(output_dir, 'timeseries.npy')
        np.save(timeseries_file, timeseries)
        print(f"时间序列数据已保存到: {timeseries_file}")

        # 保存每个时间点为单独的GeoTIFF文件
        print("\n正在保存每个时间点的GeoTIFF文件...")
        geotiff_dir = os.path.join(output_dir, 'geotiff')
        os.makedirs(geotiff_dir, exist_ok=True)

        for i, date in enumerate(dates_str):
            # 保存为GeoTIFF
            geotiff_file = os.path.join(geotiff_dir, f'timeseries_{date}.tif')
            save_as_geotiff(timeseries[i], geotiff_file)
        print(f"已保存 {len(dates_str)} 个时间点的GeoTIFF文件")

        # 计算并保存累积形变
        print("\n计算累积形变...")
        cumulative = np.cumsum(timeseries, axis=0)
        cumulative_file = os.path.join(output_dir, 'cumulative_deformation.npy')
        np.save(cumulative_file, cumulative)
        print(f"累积形变已保存到: {cumulative_file}")

        # 保存累积形变为GeoTIFF
        print("\n正在保存累积形变GeoTIFF文件...")
        for i, date in enumerate(dates_str):
            cumulative_file = os.path.join(geotiff_dir, f'cumulative_{date}.tif')
            save_as_geotiff(cumulative[i], cumulative_file)
        print(f"已保存 {len(dates_str)} 个累积形变GeoTIFF文件")

        # 保存统计信息
        stats_file = os.path.join(output_dir, 'statistics.txt')
        with open(stats_file, 'w') as f_out:
            f_out.write("SBAS-InSAR时间序列统计信息\n")
            f_out.write("=" * 50 + "\n\n")
            f_out.write(f"总时间点数: {len(dates_str)}\n")
            f_out.write(f"影像尺寸: {timeseries.shape[1]} 行 x {timeseries.shape[2]} 列\n")
            f_out.write(f"日期范围: {dates_str[0]} 到 {dates_str[-1]}\n\n")
            f_out.write("形变统计 (单位: mm):\n")
            f_out.write(f"  最小值: {np.nanmin(timeseries):.4f}\n")
            f_out.write(f"  最大值: {np.nanmax(timeseries):.4f}\n")
            f_out.write(f"  平均值: {np.nanmean(timeseries):.4f}\n")
            f_out.write(f"  标准差: {np.nanstd(timeseries):.4f}\n")
        print(f"统计信息已保存到: {stats_file}")

    print("\n数据提取完成！")
    print(f"所有数据已保存到目录: {output_dir}")


if __name__ == '__main__':
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='从SBAS-InSAR的H5文件中提取时间序列数据并保存为GeoTIFF格式'
    )
    parser.add_argument(
        'h5_file',
        type=str,
        help='输入的H5文件路径'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        default='./extracted_data',
        help='输出目录路径 (默认: ./extracted_data)'
    )

    args = parser.parse_args()

    # 执行提取
    extract_timeseries(args.h5_file, args.output)