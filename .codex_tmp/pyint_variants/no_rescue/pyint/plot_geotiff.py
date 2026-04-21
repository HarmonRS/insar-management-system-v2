#! /usr/bin/env python
import argparse
import os
import numpy as np
import matplotlib.pyplot as plt
import rasterio
from rasterio.plot import show
import matplotlib.colors as colors
from matplotlib import cm
import warnings
warnings.filterwarnings('ignore')

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='绘制GeoTIFF文件')
    
    # 必需参数
    parser.add_argument('-i', '--input', required=True, 
                       help='输入GeoTIFF文件路径')
    
    # 输出参数
    parser.add_argument('-o', '--output', 
                       help='输出图片文件路径 (默认: 输入文件名.png)')
    
    # 绘图参数
    parser.add_argument('--dpi', type=int, default=300,
                       help='输出图片分辨率 (默认: 300)')
    parser.add_argument('--cmap', default='viridis',
                       help='色彩映射 (默认: viridis)')
    parser.add_argument('--title', 
                       help='图片标题 (默认: 使用文件名)')
    parser.add_argument('--band', type=int, default=1,
                       help='要绘制的波段 (默认: 1)')
    
    # 显示参数
    parser.add_argument('--vmin', type=float,
                       help='颜色范围最小值')
    parser.add_argument('--vmax', type=float,
                       help='颜色范围最大值')
    parser.add_argument('--log', action='store_true',
                       help='使用对数颜色标尺')
    parser.add_argument('--percentile', type=float, nargs=2,
                       help='使用百分位数设置颜色范围，例如 --percentile 2 98')
    
    # 图片尺寸
    parser.add_argument('--width', type=float, default=10,
                       help='图片宽度 (英寸, 默认: 10)')
    parser.add_argument('--height', type=float, default=8,
                       help='图片高度 (英寸, 默认: 8)')
    
    # 其他选项
    parser.add_argument('--no-colorbar', action='store_true',
                       help='不显示颜色条')
    parser.add_argument('--show', action='store_true',
                       help='显示图片 (默认只保存)')
    
    return parser.parse_args()

def get_default_output_filename(input_file):
    """根据输入文件名生成默认输出文件名"""
    dir_name = os.path.dirname(input_file)
    base_name = os.path.basename(input_file)
    name, ext = os.path.splitext(base_name)
    return os.path.join(dir_name, f"{name}.png")

def read_geotiff(file_path, band=1):
    """读取GeoTIFF文件"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    with rasterio.open(file_path) as src:
        # 检查波段数量
        if band > src.count:
            raise ValueError(f"文件只有 {src.count} 个波段，无法读取波段 {band}")
        
        data = src.read(band)
        profile = src.profile
        bounds = src.bounds
        crs = src.crs
        nodata = src.nodata
        
        # 如果有无效值，创建掩码
        if nodata is not None:
            data = np.ma.masked_where(data == nodata, data)
    
    return data, profile, bounds, crs, nodata

def plot_geotiff(args):
    """主绘图函数"""
    # 设置默认输出文件名
    if args.output is None:
        args.output = get_default_output_filename(args.input)
    
    # 设置默认标题
    if args.title is None:
        args.title = os.path.basename(args.input)
    
    print(f"读取文件: {args.input}")
    print(f"输出文件: {args.output}")
    print(f"使用波段: {args.band}")
    
    # 读取数据
    data, profile, bounds, crs, nodata = read_geotiff(args.input, args.band)
    
    print(f"数据形状: {data.shape}")
    print(f"数据类型: {data.dtype}")
    print(f"坐标参考系统: {crs}")
    print(f"数据范围: {bounds}")
    
    if nodata is not None:
        print(f"无效值: {nodata}")
        valid_data = data[~data.mask] if hasattr(data, 'mask') else data[data != nodata]
    else:
        valid_data = data.compressed() if hasattr(data, 'mask') else data.flatten()
    
    print(f"有效数据点数: {len(valid_data)}")
    print(f"数据范围: {valid_data.min():.6f} - {valid_data.max():.6f}")
    
    # 设置颜色范围
    if args.percentile:
        vmin = np.percentile(valid_data, args.percentile[0])
        vmax = np.percentile(valid_data, args.percentile[1])
        print(f"使用百分位数范围: {args.percentile[0]}% - {args.percentile[1]}%")
        print(f"对应数值范围: {vmin:.6f} - {vmax:.6f}")
    elif args.vmin is not None and args.vmax is not None:
        vmin, vmax = args.vmin, args.vmax
        print(f"使用指定范围: {vmin} - {vmax}")
    else:
        vmin, vmax = valid_data.min(), valid_data.max()
        print(f"使用数据范围: {vmin:.6f} - {vmax:.6f}")
    
    # 创建图形
    fig, ax = plt.subplots(1, 1, figsize=(args.width, args.height))
    
    # 选择颜色映射
    try:
        cmap = plt.get_cmap(args.cmap)
    except:
        print(f"警告: 色彩映射 '{args.cmap}' 不存在，使用默认的 'viridis'")
        cmap = plt.get_cmap('viridis')
    
    # 选择颜色标准化方式
    if args.log:
        norm = colors.LogNorm(vmin=vmin, vmax=vmax)
        print("使用对数颜色标尺")
        # 对于对数标准化，不能同时传递vmin/vmax参数
        show_kwargs = {'norm': norm, 'cmap': cmap}
    else:
        # 对于线性标准化，可以直接传递vmin/vmax参数
        show_kwargs = {'vmin': vmin, 'vmax': vmax, 'cmap': cmap}
    
    # 使用rasterio的show函数显示地理参考数据
    if crs is not None:
        with rasterio.open(args.input) as src:
            # 使用rasterio的show函数，它能够正确处理地理参考
            # 注意：show()返回的是Axes对象，不是mappable对象
            image_axes = show(
                (src, args.band), 
                ax=ax, 
                **show_kwargs
            )
            
            # 从axes对象中获取图像对象
            if hasattr(image_axes, 'images') and len(image_axes.images) > 0:
                im = image_axes.images[0]
            else:
                # 如果无法获取图像对象，创建一个ScalarMappable用于颜色条
                im = cm.ScalarMappable(norm=norm if args.log else colors.Normalize(vmin=vmin, vmax=vmax), 
                                     cmap=cmap)
                im.set_array([])  # 设置一个空数组
    else:
        # 如果没有地理参考，使用普通的imshow
        if args.log:
            im = ax.imshow(data, norm=norm, cmap=cmap)
        else:
            im = ax.imshow(data, vmin=vmin, vmax=vmax, cmap=cmap)
    
    # 设置标题和标签
    ax.set_title(args.title, fontsize=14, fontweight='bold')
    
    # 添加颜色条
    if not args.no_colorbar:
        # 确保im是一个mappable对象
        if not hasattr(im, 'set_array'):
            # 如果不是mappable对象，创建一个
            im = cm.ScalarMappable(norm=norm if args.log else colors.Normalize(vmin=vmin, vmax=vmax), 
                                 cmap=cmap)
            im.set_array([])  # 设置一个空数组
            
        cbar = plt.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label('值', rotation=270, labelpad=15)
    
    # 添加网格
    ax.grid(True, alpha=0.3)
    
    # 设置坐标轴标签
    if crs is not None:
        ax.set_xlabel('经度')
        ax.set_ylabel('纬度')
    else:
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
    
    # 保存图片
    plt.tight_layout()
    plt.savefig(args.output, dpi=args.dpi, bbox_inches='tight')
    print(f"图片已保存: {args.output}")
    
    # 显示图片
    if args.show:
        plt.show()
    
    plt.close()
    
    return args.output

def main():
    """主函数"""
    args = parse_arguments()
    
    try:
        output_file = plot_geotiff(args)
        print(f"成功生成图片: {output_file}")
    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        raise

if __name__ == "__main__":
    main()
