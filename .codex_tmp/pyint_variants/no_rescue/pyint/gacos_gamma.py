#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ### 
###  Copy Right (c): 2017-2019, Yunmeng Cao                   ###  
###  Author: Yunmeng Cao                                      ###                                                          
###  Contact : ymcmrs@gmail.com                               ###  
#################################################################
"""
GACOS atmospheric correction for GAMMA interferograms.
This script applies GACOS-derived tropospheric corrections to unwrapped interferograms.

GACOS (Generic Atmospheric Correction Online Service) provides zenith total delay (ZTD)
maps that can be used to correct for atmospheric phase delays in InSAR data.

Usage:
    gacos_gamma.py projectName Mdate-Sdate
    gacos_gamma.py PacayaT163TsxHhA 20150102-20150601
"""

import numpy as np
import os
import sys
import argparse
import subprocess
import glob
import warnings
from pathlib import Path
from datetime import datetime
from scipy.interpolate import RegularGridInterpolator

from pyint import _utils as ut

warnings.filterwarnings('ignore', category=RuntimeWarning)

# Try to import AutoGACOS modules
try:
    from gacos import Downloader, Submitter, SarDataset
    AUTOGACOS_AVAILABLE = True
except Exception as _e:
    AUTOGACOS_AVAILABLE = False
    print(f"[DEBUG] AutoGACOS import failed: {type(_e).__name__}: {_e}")


INTRODUCTION = '''
-------------------------------------------------------------------  
       Apply GACOS atmospheric correction to interferograms.
       
       This script:
       1. Generates elevation angle file from GAMMA products
       2. Downloads GACOS ZTD data for master and slave dates
       3. Applies tropospheric correction to the interferogram
       
       Prerequisites:
       - GACOS data for the acquisition dates (or valid email for auto-download)
       - Geocoded interferogram from geocode_gamma.py
-------------------------------------------------------------------
'''

EXAMPLE = '''
    Usage: 
            gacos_gamma.py projectName Mdate-Sdate
            gacos_gamma.py projectName Mdate-Sdate --ztd-dir /path/to/gacos/data
            gacos_gamma.py PacayaT163TsxHhA 20150102-20150601
-------------------------------------------------------------------  
'''


class HEADER:
    """Header class for GACOS file format"""
    width = 0
    length = 0
    xfirst = 0.0
    yfirst = 0.0
    xstep = 0.0
    ystep = 0.0


def read_header(filename):
    """Read header information from GACOS .rsc file"""
    if not os.path.isfile(filename):
        print(filename + " file not exist")
        return None
    
    header = HEADER()
    with open(filename) as f:
        for line in f:
            data = line.split()
            if len(data) >= 2:
                if data[0] == "WIDTH":
                    header.width = int(data[1])
                if data[0] == "FILE_LENGTH":
                    header.length = int(data[1])
                if data[0] == "X_FIRST":
                    header.xfirst = float(data[1])
                if data[0] == "Y_FIRST":
                    header.yfirst = float(data[1])
                if data[0] == "X_STEP":
                    header.xstep = float(data[1])
                if data[0] == "Y_STEP":
                    header.ystep = float(data[1])
    return header


def cut_image2(filename, headername, yfirst_new, length_new, xfirst_new, width_new):
    """Cut GACOS ZTD image to match interferogram extent"""
    header = read_header(headername)
    if header is None:
        return None
    
    with open(filename, 'rb') as f:
        data0 = np.fromfile(f, dtype=np.float32)
        data = np.reshape(data0, (header.length, header.width))
    
    out = np.zeros((length_new, width_new), dtype=np.float32)
    
    for i in range(header.length):
        lat = header.yfirst + header.ystep * i
        row = int(round((lat - yfirst_new) / header.ystep))
        if row < 0 or row >= length_new:
            continue
        for j in range(header.width):
            lon = header.xfirst + header.xstep * j
            col = int(round((lon - xfirst_new) / header.xstep))
            if col < 0 or col >= width_new:
                continue
            out[row, col] = data[i, j]
    
    out = np.where(out == 0, np.nan, out)
    out.tofile(filename + ".cut")
    
    f = open(filename + ".cut.rsc", 'w')
    f.write("WIDTH " + str(width_new) + "\n")
    f.write("FILE_LENGTH " + str(length_new) + "\n")
    f.write("X_FIRST " + str(xfirst_new) + "\n")
    f.write("Y_FIRST " + str(yfirst_new) + "\n")
    f.write("X_STEP  " + str(header.xstep) + "\n")
    f.write("Y_STEP  " + str(header.ystep) + "\n")
    f.close()
    
    return filename + ".cut"


def make_correction(phsfilename, ztd1filename, ztd2filename, elevfilename, wavelength=None):
    """
    Apply GACOS tropospheric correction to interferogram.
    
    Parameters:
    -----------
    phsfilename : str
        Path to unwrapped phase file
    ztd1filename : str
        Path to GACOS ZTD file for master date
    ztd2filename : str
        Path to GACOS ZTD file for slave date
    elevfilename : str
        Path to elevation angle file
    wavelength : float, optional
        Radar wavelength in meters. If None, read from template.
    """
    header = read_header(phsfilename + ".rsc")
    if header is None:
        print("Error reading phase header file")
        return None
    
    # Cut ZTD files to match interferogram extent
    if not os.path.isfile(ztd1filename + ".cut"):
        cut_image2(ztd1filename, ztd1filename + ".rsc", header.yfirst, header.length, header.xfirst, header.width)
    if not os.path.isfile(ztd2filename + ".cut"):
        cut_image2(ztd2filename, ztd2filename + ".rsc", header.yfirst, header.length, header.xfirst, header.width)
    
    # Read phase data
    with open(phsfilename, 'rb') as f:
        data = np.fromfile(f, dtype=np.float32)
        phase = np.reshape(data, [header.length, header.width])
    
    # Read ZTD data
    with open(ztd1filename + ".cut", 'rb') as f:
        data = np.fromfile(f, dtype=np.float32)
        ztd1 = np.reshape(data, [header.length, header.width])
    
    with open(ztd2filename + ".cut", 'rb') as f:
        data = np.fromfile(f, dtype=np.float32)
        ztd2 = np.reshape(data, [header.length, header.width])
    
    # Read elevation angle data
    with open(elevfilename, 'rb') as f:
        data = np.fromfile(f, dtype=np.float32)
        elev = np.reshape(data, [header.length, header.width])
    
    # Calculate tropospheric phase delay
    # ZTD difference to phase: phase = 4*pi*dZTD / (wavelength * sin(elevation))
    # Default wavelength factor for Sentinel-1: 0.0044138251819503 = 4*pi/lambda
    if wavelength:
        ztd_factor = 4 * np.pi / wavelength
    else:
        ztd_factor = 1 / 0.0044138251819503  # Default factor
    
    dztd = ztd2 - ztd1
    dztd = dztd / 0.0044138251819503  # Convert ZTD to phase
    dztd = dztd / np.sin(elev)  # Incidence angle correction
    
    # Apply correction
    index = np.where(phase == 0)
    phase[index] = np.nan
    phasemean = np.nanmean(phase)
    print("Phase std before correction: " + str(np.nanstd(phase)))
    
    phase = phase - phasemean
    phase = phase - dztd  # Apply correction
    phase[index] = np.nan
    phasemean = np.nanmean(phase)
    print("Phase std after correction: " + str(np.nanstd(phase)))
    
    phase = phase - phasemean
    phase[index] = 0
    phase.tofile(phsfilename + ".gacos")
    
    return phsfilename + ".gacos"


def generate_elevation_angle(workDir, Mdate, Sdate, MampPar, offpar, dempar, dem, rlks):
    """
    Generate elevation angle file from GAMMA products.
    
    The elevation angle (90 - incidence angle) is needed for 
    projecting ZTD to line-of-sight phase delay.
    """
    os.chdir(workDir)
    
    # Generate look vector (incidence angle)
    call_str = "look_vector " + MampPar + " " + offpar + " " + dempar + " " + dem + " lv_theta lv_phi"
    os.system(call_str)
    
    # Get DEM parameters
    call_str = "grep 'corner_lat:' " + dempar + " | awk '{print $2}' "
    North = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'corner_lon:' " + dempar + " | awk '{print $2}' "
    West = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'post_lat:' " + dempar + " | awk '{print $2}' "
    posty = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'post_lon:' " + dempar + " | awk '{print $2}' "
    postx = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'width:' " + dempar + " | awk '{print $2}' "
    width = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'nlines:' " + dempar + " | awk '{print $2}' "
    length = subprocess.getstatusoutput(call_str)[1]
    
    South = str(round(float(North) + (float(length) - 1) * float(posty), 7))
    East = str(round(float(West) + (float(width) - 1) * float(postx), 7))
    
    # Convert to elevation angle
    call_str = "swap_bytes lv_theta lv_theta.phase_swap 4 > dinsar.log "
    os.system(call_str)
    
    call_str = "gmt xyz2grd lv_theta.phase_swap -Glv_theta.grd -Ddegree/degree/cm/1/0/=/= -R" + West + "/" + East + "/" + South + "/" + North + " -I" + postx + " -ZTLf -N0"
    os.system(call_str)
    
    # Elevation angle = 90 - incidence angle
    call_str = "gmt grdmath 90 lv_theta.grd 3.1415926 DIV 180 MUL SUB = lv_theta_final.grd"
    os.system(call_str)
    call_str = "gmt grdmath 90 lv_theta_final.grd SUB = lv_elev.grd"
    os.system(call_str)
    
    # Resample to 3 arc-seconds
    call_str = "gmt grdsample lv_elev.grd -Glv_elev_3c.grd -I3c"
    os.system(call_str)
    
    # Convert to binary
    call_str = "gmt grd2xyz lv_elev_3c.grd -ZTLf -N0 > " + Mdate + '-' + Sdate + '.gacos.elev'
    os.system(call_str)
    
    return workDir + '/' + Mdate + '-' + Sdate + '.gacos.elev'


def prepare_unw_for_gacos(workDir, unw, Mdate, Sdate, dempar, rlks):
    """
    Prepare unwrapped interferogram for GACOS correction.
    
    Converts GAMMA format to GACOS-compatible format with .rsc file.
    """
    os.chdir(workDir)
    
    # Get DEM parameters
    call_str = "grep 'corner_lat:' " + dempar + " | awk '{print $2}' "
    North = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'corner_lon:' " + dempar + " | awk '{print $2}' "
    West = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'post_lat:' " + dempar + " | awk '{print $2}' "
    posty = subprocess.getstatusoutput(call_str)[1]
    call_str = "grep 'post_lon:' " + dempar + " | awk '{print $2}' "
    postx = subprocess.getstatusoutput(call_str)[1]
    
    # Convert unw to grd
    call_str = "swap_bytes " + unw + " unw.phase_swap 4 > dinsar.log "
    os.system(call_str)
    
    # Get dimensions
    call_str = "gmt grdinfo unw_f.grd -C"
    result = subprocess.getstatusoutput(call_str)[1].split()
    if len(result) >= 12:
        Width = result[9]
        line = result[10]
        West = result[1]
        North = result[4]
    else:
        # Fallback to DEM parameters
        call_str = "grep 'width:' " + dempar + " | awk '{print $2}' "
        Width = subprocess.getstatusoutput(call_str)[1]
        call_str = "grep 'nlines:' " + dempar + " | awk '{print $2}' "
        line = subprocess.getstatusoutput(call_str)[1]
    
    ymax = str(int(round(float(line), 7)))
    xmax = str(int(round(float(Width), 7)))
    
    # Create .rsc file
    output = workDir + '/' + Mdate + '-' + Sdate + '.gacos.unw.rsc'
    if os.path.exists(output):
        os.remove(output)
    
    with open(output, 'a+') as f:
        f.write('WIDTH          ' + str(Width) + '\n')
        f.write('FILE_LENGTH          ' + str(line) + '\n')
        f.write('XMIN          1' + '\n')
        f.write('XMAX          ' + xmax + '\n')
        f.write('YMIN          1' + '\n')
        f.write('YMAX          ' + ymax + '\n')
        f.write('X_FIRST          ' + str(West) + '\n')
        f.write('Y_FIRST          ' + str(North) + '\n')
        f.write('X_STEP          8.33333333E-04' + '\n')
        f.write('Y_STEP          -8.33333333E-04' + '\n')
        f.write('X_UNIT          degrees' + '\n')
        f.write('Y_UNIT          degrees' + '\n')
        f.write('Z_OFFSET          0' + '\n')
        f.write('Z_SCALE          1' + '\n')
        f.write('PROJECTION       LATLON' + '\n')
        f.write('DATUM            WGS84' + '\n')
    
    return workDir + '/' + Mdate + '-' + Sdate + '.gacos.unw'


def parse_dem_par(dempar):
    """
    从 GAMMA DEM par 文件读取网格参数（纯 Python，无需 grep/awk）
    """
    info = {}
    with open(dempar) as f:
        for line in f:
            parts = line.split(':')
            if len(parts) >= 2:
                key = parts[0].strip()
                val = parts[1].strip().split()[0] if parts[1].strip() else ''
                info[key] = val
    return {
        'width': int(info['width']),
        'nlines': int(info['nlines']),
        'corner_lat': float(info['corner_lat']),
        'corner_lon': float(info['corner_lon']),
        'post_lat': float(info['post_lat']),
        'post_lon': float(info['post_lon']),
    }


def resample_ztd_to_dem(ztd_file, rsc_file, dem_info):
    """
    读取 ZTD 数据并双线性插值重采样到 DEM 网格
    修复: RegularGridInterpolator 要求坐标严格递增，
          ZTD 纬度为递减（北→南），需翻转
    """
    # 读取 RSC 头文件
    rsc = {}
    with open(rsc_file) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                rsc[parts[0]] = parts[1]

    ztd_w = int(rsc['WIDTH'])
    ztd_h = int(rsc['FILE_LENGTH'])
    ztd_x0 = float(rsc['X_FIRST'])
    ztd_y0 = float(rsc['Y_FIRST'])
    ztd_dx = float(rsc['X_STEP'])
    ztd_dy = float(rsc['Y_STEP'])

    raw = np.fromfile(ztd_file, dtype=np.float32).reshape(ztd_h, ztd_w)

    # 构建 ZTD 网格坐标
    ztd_lats = ztd_y0 + np.arange(ztd_h) * ztd_dy
    ztd_lons = ztd_x0 + np.arange(ztd_w) * ztd_dx

    # 翻转使坐标严格递增（RegularGridInterpolator 要求）
    if ztd_lats[0] > ztd_lats[-1]:
        ztd_lats = ztd_lats[::-1]
        raw = raw[::-1, :]
    if ztd_lons[0] > ztd_lons[-1]:
        ztd_lons = ztd_lons[::-1]
        raw = raw[:, ::-1]

    interp = RegularGridInterpolator(
        (ztd_lats, ztd_lons), raw,
        method='linear', bounds_error=False, fill_value=np.nan
    )

    # 构建 DEM 网格查询点
    dem_w = dem_info['width']
    dem_h = dem_info['nlines']
    dem_lats = dem_info['corner_lat'] + np.arange(dem_h) * dem_info['post_lat']
    dem_lons = dem_info['corner_lon'] + np.arange(dem_w) * dem_info['post_lon']

    # 逐行插值（节省内存）
    result = np.empty((dem_h, dem_w), dtype=np.float32)
    for i in range(dem_h):
        pts = np.column_stack([np.full(dem_w, dem_lats[i]), dem_lons])
        result[i] = interp(pts).astype(np.float32)

    return result


def convert_ztd_tif_to_binary(tif_file):
    """
    将 .ztd.tif (GeoTIFF) 转换为 .ztd (raw binary) + .ztd.rsc (header)
    返回 .ztd 文件路径
    """
    try:
        import rasterio
    except ImportError:
        from osgeo import gdal
        ds = gdal.Open(tif_file)
        gt = ds.GetGeoTransform()
        w, h = ds.RasterXSize, ds.RasterYSize
        data = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
        x0, y0, dx, dy = gt[0], gt[3], gt[1], gt[5]
        ds = None
    else:
        with rasterio.open(tif_file) as src:
            data = src.read(1).astype(np.float32)
            w, h = src.width, src.height
            x0 = src.transform[2]
            y0 = src.transform[5]
            dx = src.transform[0]
            dy = src.transform[4]

    ztd_file = tif_file.replace('.ztd.tif', '.ztd')
    rsc_file = tif_file.replace('.ztd.tif', '.ztd.rsc')

    data.tofile(ztd_file)
    with open(rsc_file, 'w') as f:
        f.write(f"WIDTH          {w}\n")
        f.write(f"FILE_LENGTH    {h}\n")
        f.write(f"X_FIRST        {x0}\n")
        f.write(f"Y_FIRST        {y0}\n")
        f.write(f"X_STEP         {dx}\n")
        f.write(f"Y_STEP         {dy}\n")
        f.write("X_UNIT         degrees\n")
        f.write("Y_UNIT         degrees\n")
        f.write("PROJECTION     LATLON\n")
        f.write("DATUM          WGS84\n")

    return ztd_file


def apply_gacos_correction_python(geo_unw_file, ztd1_file, ztd1_rsc, ztd2_file, ztd2_rsc,
                                   dempar, slcpar=None, wavelength=None, out_file=None,
                                   figdir=None, pair_str=None):
    """
    纯 Python GACOS 大气校正（不依赖 GMT）
    修复原 make_correction 的所有 Bug:
      - 极端值掩码（GAMMA 无效标记）
      - sin/cos 弧度转换
      - cut_image2 慢速循环 → scipy 向量化重采样
      - 增加校正前后对比图

    参数:
    -----
    geo_unw_file : str  地理编码后的解缠相位文件
    ztd1_file    : str  Master 日期 ZTD 二进制文件
    ztd1_rsc     : str  Master 日期 ZTD RSC 头文件
    ztd2_file    : str  Slave 日期 ZTD 二进制文件
    ztd2_rsc     : str  Slave 日期 ZTD RSC 头文件
    dempar       : str  DEM 参数文件
    slcpar       : str  SLC 参数文件（读取入射角，可选）
    wavelength   : float 雷达波长（m），None则从slcpar读取
    out_file     : str  输出文件路径（默认 geo_unw_file + '.gacos'）
    figdir       : str  对比图保存目录（None则不绘图）
    pair_str     : str  干涉对名称如 '20241105-20241117'

    返回: 输出文件路径
    """
    # 1. 读取 DEM 参数
    dem = parse_dem_par(dempar)
    dem_w, dem_h = dem['width'], dem['nlines']

    # 2. 读取入射角和波长
    inc_angle_deg = 39.0  # 默认值
    if slcpar and os.path.isfile(slcpar):
        with open(slcpar) as f:
            for line in f:
                if line.startswith('incidence_angle:'):
                    inc_angle_deg = float(line.split(':')[1].strip().split()[0])
                if line.startswith('radar_frequency:') and wavelength is None:
                    freq = float(line.split(':')[1].strip().split()[0])
                    wavelength = 299792458.0 / freq

    if wavelength is None:
        wavelength = 0.0554657595  # Sentinel-1 C-band 默认值

    cos_inc = np.cos(np.radians(inc_angle_deg))
    ztd_to_phase = 4.0 * np.pi / wavelength

    print(f"  入射角: {inc_angle_deg:.4f}°, cos={cos_inc:.6f}")
    print(f"  波长: {wavelength:.10f} m")

    # 3. 读取地理编码后的解缠相位
    unw_data = np.fromfile(geo_unw_file, dtype=np.float32).reshape(dem_h, dem_w)

    # 4. 重采样 ZTD 到 DEM 网格
    print("  重采样 ZTD Master...")
    ztd_m = resample_ztd_to_dem(ztd1_file, ztd1_rsc, dem)
    print("  重采样 ZTD Slave...")
    ztd_s = resample_ztd_to_dem(ztd2_file, ztd2_rsc, dem)

    # 5. 计算 LOS 相位校正量
    dztd = ztd_s - ztd_m
    phase_correction = dztd * ztd_to_phase / cos_inc

    # 6. 构建有效像素掩码
    #    排除: 零值 | NaN | GAMMA 伪影/解缠错误（|x| >= 1000 rad ≈ 4.4m LOS）
    PHASE_THRESH = 1000.0
    valid = ((unw_data != 0)
             & np.isfinite(unw_data)
             & (np.abs(unw_data) < PHASE_THRESH)
             & np.isfinite(phase_correction))
    n_valid = np.sum(valid)
    n_total = dem_w * dem_h
    print(f"  有效像素: {n_valid}/{n_total} ({n_valid/n_total*100:.1f}%)")

    if n_valid == 0:
        print("  错误: 无有效像素，跳过校正")
        return None

    # 7. 统计校正前
    std_before = float(np.std(unw_data[valid]))

    # 8. 应用校正（保留原始数据结构，仅修改有效像素）
    corrected = unw_data.copy()
    corrected[valid] = unw_data[valid] - phase_correction[valid]

    # 去均值（仅对有效像素）
    mean_val = np.mean(corrected[valid])
    corrected[valid] -= mean_val

    std_after = float(np.std(corrected[valid]))
    reduction = (1 - std_after / std_before) * 100 if std_before > 0 else 0

    print(f"  校正前 std: {std_before:.3f} rad")
    print(f"  校正后 std: {std_after:.3f} rad")
    print(f"  std 降低: {reduction:.1f}%")

    # 9. 保存
    if out_file is None:
        out_file = geo_unw_file + '.gacos'
    corrected.astype(np.float32).tofile(out_file)
    print(f"  输出: {out_file}")

    # 10. 绘制对比图
    if figdir:
        plot_gacos_comparison(unw_data, phase_correction, corrected, valid,
                              dem, std_before, std_after, pair_str, figdir)

    return out_file


def plot_gacos_comparison(unw_before, correction, unw_after, valid_mask,
                          dem_info, std_before, std_after, pair_str, figdir):
    """
    绘制 GACOS 校正前后相位对比图（三子图）
    版本控制: 自动追加 _v1, _v2...
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    # 中文字体配置
    plt.rcParams['font.sans-serif'] = ['Noto Serif CJK SC', 'Noto Sans CJK SC',
                                        'AR PL UMing CN', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    figdir = Path(figdir)
    figdir.mkdir(parents=True, exist_ok=True)

    # 版本控制
    base_name = f"GACOS_comparison_{pair_str}" if pair_str else "GACOS_comparison"
    existing = sorted(figdir.glob(f"{base_name}_v*.png"))
    ver = int(existing[-1].stem.split('_v')[-1]) + 1 if existing else 1
    out_path = figdir / f"{base_name}_v{ver}.png"

    # 准备显示数据（仅显示有效像素）
    before_disp = np.full_like(unw_before, np.nan, dtype=np.float64)
    before_disp[valid_mask] = unw_before[valid_mask]
    corr_disp = np.full_like(correction, np.nan, dtype=np.float64)
    corr_disp[valid_mask] = correction[valid_mask]
    after_disp = np.full_like(unw_after, np.nan, dtype=np.float64)
    after_disp[valid_mask] = unw_after[valid_mask]

    # 色标范围：使用有效数据的 P2/P98
    vals_b = unw_before[valid_mask]
    vals_c = correction[valid_mask]
    vals_a = unw_after[valid_mask]
    vlim = max(abs(np.percentile(vals_b, 2)), abs(np.percentile(vals_b, 98)), 0.5)
    vlim_c = max(abs(np.percentile(vals_c, 2)), abs(np.percentile(vals_c, 98)), 0.5)
    vlim_a = max(abs(np.percentile(vals_a, 2)), abs(np.percentile(vals_a, 98)), 0.5)

    # 地理坐标范围
    w = dem_info['width']
    h = dem_info['nlines']
    extent = [dem_info['corner_lon'],
              dem_info['corner_lon'] + w * dem_info['post_lon'],
              dem_info['corner_lat'] + h * dem_info['post_lat'],
              dem_info['corner_lat']]

    # 降采样显示
    step = max(1, h // 2000)
    b_s = before_disp[::step, ::step]
    c_s = corr_disp[::step, ::step]
    a_s = after_disp[::step, ::step]

    # 使用带 NaN 灰色背景的 colormap
    cmap_phase = plt.cm.RdBu_r.copy()
    cmap_phase.set_bad(color='#E0E0E0')
    cmap_corr = plt.cm.coolwarm.copy()
    cmap_corr.set_bad(color='#E0E0E0')

    fig, axes = plt.subplots(1, 3, figsize=(18, 6), dpi=150)
    for ax in axes:
        ax.set_facecolor('#E0E0E0')

    im1 = axes[0].imshow(b_s, cmap=cmap_phase, vmin=-vlim, vmax=vlim,
                          extent=extent, aspect='auto', interpolation='nearest')
    axes[0].set_title(f'Unwrapped Phase (std={std_before:.2f} rad)', fontsize=11, fontweight='bold')
    plt.colorbar(im1, ax=axes[0], label='rad', shrink=0.8)

    im2 = axes[1].imshow(c_s, cmap=cmap_corr, vmin=-vlim_c, vmax=vlim_c,
                          extent=extent, aspect='auto', interpolation='nearest')
    axes[1].set_title('GACOS Correction', fontsize=11, fontweight='bold')
    plt.colorbar(im2, ax=axes[1], label='rad', shrink=0.8)

    im3 = axes[2].imshow(a_s, cmap=cmap_phase, vmin=-vlim_a, vmax=vlim_a,
                          extent=extent, aspect='auto', interpolation='nearest')
    axes[2].set_title(f'Corrected (std={std_after:.2f} rad)', fontsize=11, fontweight='bold')
    plt.colorbar(im3, ax=axes[2], label='rad', shrink=0.8)

    for ax in axes:
        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.tick_params(labelsize=9)

    reduction = (1 - std_after / std_before) * 100 if std_before > 0 else 0
    n_valid = np.sum(valid_mask)
    if pair_str:
        m, s = pair_str.split('-')
        fig.suptitle(f'GACOS: {m}-{s} | valid={n_valid:,} px | std {reduction:+.1f}%',
                     fontsize=13, fontweight='bold', y=1.02)
    plt.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print(f"  对比图: {out_path}")


def find_existing_ztd(date, gacos_dir):
    """
    Find existing GACOS ZTD file for a specific date.
    
    Returns:
    --------
    ztd_file : str or None
        Path to ZTD file (without extension), or None if not found
    """
    # Check for .ztd + .ztd.rsc format (preferred)
    ztd_pattern = os.path.join(gacos_dir, "**", date + "*.ztd")
    ztd_files = glob.glob(ztd_pattern, recursive=True)
    if ztd_files:
        for ztd_file in ztd_files:
            if ztd_file.endswith('.ztd.tif'):
                continue
            rsc_file = ztd_file + '.rsc'
            if os.path.exists(rsc_file):
                return ztd_file

    # Check for .ztd.tif format, auto-convert if needed
    ztd_pattern = os.path.join(gacos_dir, "**", date + "*.ztd.tif")
    tif_files = glob.glob(ztd_pattern, recursive=True)
    if tif_files:
        tif_file = tif_files[0]
        ztd_bin = tif_file.replace('.ztd.tif', '.ztd')
        rsc_file = tif_file.replace('.ztd.tif', '.ztd.rsc')
        if not os.path.exists(ztd_bin) or not os.path.exists(rsc_file):
            print(f"  Auto-converting {os.path.basename(tif_file)} -> .ztd + .rsc")
            convert_ztd_tif_to_binary(tif_file)
        if os.path.exists(ztd_bin) and os.path.exists(rsc_file):
            return ztd_bin

    return None


def submit_gacos_request(dates, bounds, email, gacos_dir, acquisition_time=None):
    """
    Submit GACOS data request for multiple dates.
    
    Parameters:
    -----------
    dates : list
        List of acquisition dates in YYYYMMDD format
    bounds : tuple
        Bounding box (West, South, East, North)
    email : str
        Email address for GACOS submission
    gacos_dir : str
        Directory to save GACOS data
    acquisition_time : str, optional
        Acquisition time in HH:MM format
    
    Returns:
    --------
    success : bool
        True if submission successful
    """
    if not AUTOGACOS_AVAILABLE:
        print("AutoGACOS module not available. Please download GACOS data manually.")
        return False
    
    try:
        from gacos import Submitter, SarDataset
        import pandas as pd
        
        # Create datetime index
        if acquisition_time:
            hour, minute = map(int, acquisition_time.split(':'))
        else:
            hour, minute = 10, 0  # Sentinel-1 升轨默认采集时间 ~10:00 UTC
        
        date_strings = [f"{d} {hour:02d}:{minute:02d}:00" for d in dates]
        date_times = pd.to_datetime(date_strings)
        
        # Create dataset
        dataset = SarDataset(bounds, date_times, gacos_dir)
        
        print(f"\nSubmitting GACOS request for {len(dates)} dates...")
        print(f"Bounds: W={bounds[0]:.4f}, S={bounds[1]:.4f}, E={bounds[2]:.4f}, N={bounds[3]:.4f}")
        print(f"Acquisition time: {hour:02d}:{minute:02d} UTC")
        print(f"Email: {email}")
        
        # Submit request
        submitter = Submitter(dataset, email)
        submitter.post_requests()
        
        if submitter.succeed:
            print(f"\nSuccessfully submitted {len(submitter.succeed)} requests.")
        if submitter.failed:
            print(f"\nFailed to submit {len(submitter.failed)} requests.")
        
        return len(submitter.succeed) > 0
        
    except Exception as e:
        print(f"Error in GACOS submission: {e}")
        import traceback
        traceback.print_exc()
        return False


def download_gacos_from_email(email_config, gacos_dir, bounds=None, times=None,
                              submit_time=None):
    """
    从邮箱检索 gacos2017@foxmail.com 发来的 GACOS 下载链接并下载数据。
    
    Parameters:
    -----------
    email_config : dict
        邮箱配置: username, password, host, port, ssl
    gacos_dir : str
        GACOS 数据保存目录
    bounds : tuple, optional
        边界框过滤 (West, South, East, North)
    times : list, optional
        采集时间过滤
    submit_time : str, optional
        提交时间（YYYY-MM-DD HH:MM:SS），仅检索此时间之后的邮件
    
    Returns:
    --------
    downloaded_files : list
        已下载的 ZTD 文件路径列表
    """
    if not AUTOGACOS_AVAILABLE:
        print("AutoGACOS module not available.")
        return []
    
    try:
        from gacos import Downloader, GACOSEmail
        
        # Step 1: 从邮箱检索 GACOS 下载链接
        print("  检索 gacos2017@foxmail.com 邮件中的下载链接...")
        
        email_retriever = GACOSEmail(
            username=email_config.get('username'),
            password=email_config.get('password'),
            host=email_config.get('host', 'imap.163.com'),
            port=email_config.get('port'),
            email_protocol=email_config.get('protocol', 'imap'),
            ssl=email_config.get('ssl', True),
            gacos_email='gacos2017@foxmail.com',
            start_date=submit_time
        )
        
        url_file = os.path.join(gacos_dir, 'gacos_urls.csv')
        email_retriever.retrieve_gacos_urls(url_file)
        
        if not os.path.exists(url_file):
            print("  未找到 GACOS 下载链接")
            return []
        
        # 检查 CSV 是否有内容
        import pandas as pd
        try:
            df = pd.read_csv(url_file)
            if len(df) == 0:
                print("  邮件中未发现新的 GACOS 数据链接")
                return []
            print(f"  找到 {len(df)} 个下载链接")
        except Exception:
            print("  URL 文件解析失败")
            return []
        
        # Step 2: 下载文件
        print("  下载 GACOS 数据...")
        
        dl = Downloader(
            url_file=url_file,
            output_dir=gacos_dir,
            bounds=bounds,
            times=times,
            keep_original=False
        )
        
        dl.download()
        
        # Step 3: 将下载的 .ztd.tif 转换为 .ztd + .rsc
        downloaded = []
        for tif_file in glob.glob(os.path.join(gacos_dir, "*.ztd.tif")):
            rsc_file = tif_file.replace('.ztd.tif', '.ztd.rsc')
            if not os.path.exists(rsc_file):
                ztd_file = convert_ztd_tif_to_binary(tif_file)
                if ztd_file:
                    downloaded.append(ztd_file)
                    print(f"  转换: {os.path.basename(tif_file)} → .ztd + .rsc")
            else:
                downloaded.append(tif_file.replace('.ztd.tif', '.ztd'))
        
        return downloaded
        
    except Exception as e:
        print(f"  邮箱检索/下载出错: {e}")
        import traceback
        traceback.print_exc()
        return []


def auto_gacos_workflow(dates, bounds, gacos_dir, email, email_config=None, 
                        acquisition_time=None, wait_for_email=False, max_wait_hours=24,
                        check_interval=60):
    """
    Complete automatic GACOS workflow: submit request, wait, and download.
    
    Parameters:
    -----------
    dates : list
        List of acquisition dates in YYYYMMDD format
    bounds : tuple
        Bounding box (West, South, East, North)
    gacos_dir : str
        Directory to save GACOS data
    email : str
        Email address for GACOS submission
    email_config : dict, optional
        Email configuration for downloading (username, password, host, etc.)
    acquisition_time : str, optional
        Acquisition time in HH:MM format
    wait_for_email : bool, optional
        Whether to wait for email notification before downloading
    max_wait_hours : int, optional
        Maximum hours to wait for email
    
    Returns:
    --------
    ztd_files : dict
        Dictionary mapping dates to ZTD file paths
    """
    import time as time_module
    from datetime import datetime, timedelta
    
    ztd_files = {}
    missing_dates = []
    
    # Step 1: Check existing files
    print("\n" + "="*60)
    print("Step 1: Checking existing GACOS data...")
    print("="*60)
    
    for date in dates:
        ztd_file = find_existing_ztd(date, gacos_dir)
        if ztd_file:
            ztd_files[date] = ztd_file
        else:
            missing_dates.append(date)
    
    if not missing_dates:
        print("\nAll required GACOS data already exists!")
        return ztd_files
    
    print(f"\nMissing GACOS data for {len(missing_dates)} dates:")
    for d in missing_dates:
        print(f"  - {d}")
    
    # Step 2: Submit request for missing dates
    print("\n" + "="*60)
    print("Step 2: Submitting GACOS requests...")
    print("="*60)
    
    submit_success = submit_gacos_request(
        dates=missing_dates,
        bounds=bounds,
        email=email,
        gacos_dir=gacos_dir,
        acquisition_time=acquisition_time
    )
    
    if not submit_success:
        print("\n提交失败（可能是重复提交或服务器繁忙），尝试从邮箱获取已有数据...")
    else:
        print("\nGACOS request submitted successfully!")
        print("You will receive an email with download links when data is ready.")
    
    # Step 3: Wait and download from email (if configured)
    if email_config and wait_for_email:
        print("\n" + "="*60)
        print("Step 3: 检查 gacos2017@foxmail.com 邮件并下载...")
        print("="*60)
        
        # 记录时间基准，仅检索此时间之后的 GACOS 邮件，过滤掉旧邮件
        if submit_success:
            submit_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        else:
            # 提交失败（重复提交），向前回溯 1 小时查找已有邮件
            submit_time_str = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        print(f"邮件过滤起始时间: {submit_time_str}")
        
        start_time = datetime.now()
        # 提交成功则等 3 分钟再检查；提交失败则立即检查邮箱
        first_wait = 180 if submit_success else 5
        retry_wait = 120   # 之后每 2 分钟
        attempt = 0
        
        # 首次等待
        print(f"\n提交完成，等待 {first_wait//60} 分钟后首次检查邮箱...")
        time_module.sleep(first_wait)
        
        while (datetime.now() - start_time) < timedelta(hours=max_wait_hours):
            attempt += 1
            elapsed = datetime.now() - start_time
            elapsed_min = int(elapsed.total_seconds() // 60)
            elapsed_sec = int(elapsed.total_seconds() % 60)
            print(f"\n[第 {attempt} 次检查] 已等待 {elapsed_min}m{elapsed_sec}s")
            
            downloaded = download_gacos_from_email(
                email_config=email_config,
                gacos_dir=gacos_dir,
                bounds=bounds,
                submit_time=submit_time_str
            )
            
            # 检查缺失日期是否已下载
            for date in missing_dates[:]:
                ztd_file = find_existing_ztd(date, gacos_dir)
                if ztd_file:
                    ztd_files[date] = ztd_file
                    missing_dates.remove(date)
                    print(f"  ✓ 已获取: {date}")
            
            if not missing_dates:
                print(f"\n所有 {len(dates)} 个日期的 GACOS 数据下载完成！")
                break
            
            print(f"  仍缺 {len(missing_dates)} 个日期，{retry_wait//60} 分钟后重试...")
            time_module.sleep(retry_wait)
        
        if missing_dates:
            print(f"\n超时！仍缺 {len(missing_dates)} 个日期的 GACOS 数据。")
    
    return ztd_files


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Apply GACOS atmospheric correction to GAMMA interferograms.',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=INTRODUCTION + '\n' + EXAMPLE)

    parser.add_argument('projectName', help='projectName for processing.')
    parser.add_argument('pair', help='Master-Slave, e.g., 20150101-20150106.')
    parser.add_argument('--ztd-dir', dest='ztdDir', help='Directory containing GACOS ZTD files.')
    parser.add_argument('--ztd1', dest='ztd1', help='GACOS ZTD file for master date.')
    parser.add_argument('--ztd2', dest='ztd2', help='GACOS ZTD file for slave date.')
    parser.add_argument('--email', dest='email', help='Email for GACOS auto-download.')
    parser.add_argument('--wavelength', dest='wavelength', type=float, help='Radar wavelength in meters.')
    
    # Email configuration for auto-download
    parser.add_argument('--email-user', dest='emailUser', help='Email username for downloading.')
    parser.add_argument('--email-pass', dest='emailPass', help='Email password for downloading.')
    parser.add_argument('--email-host', dest='emailHost', default='imap.gmail.com', help='Email IMAP host.')
    parser.add_argument('--email-port', dest='emailPort', type=int, default=993, help='Email IMAP port.')
    parser.add_argument('--email-ssl', dest='emailSsl', action='store_true', default=True, help='Use SSL for email.')
    
    # Auto-download options
    parser.add_argument('--auto-download', dest='autoDownload', action='store_true', 
                        help='Automatically submit request and download.')
    parser.add_argument('--wait-hours', dest='waitHours', type=int, default=24,
                        help='Maximum hours to wait for email notification.')
    
    inps = parser.parse_args()
    return inps


def main(argv):
    
    inps = cmdLineParse()
    projectName = inps.projectName
    Pair = inps.pair
    
    # Parse dates
    Mdate = ut.yyyymmdd(Pair.split('-')[0])
    Sdate = ut.yyyymmdd(Pair.split('-')[1])
    
    # Setup directories
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict = ut.update_template(templateFile)
    
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    
    ifgDir = scratchDir + '/' + projectName + "/ifgrams"
    demDir = scratchDir + '/' + projectName + "/DEM"
    slcDir = scratchDir + '/' + projectName + "/SLC"
    workDir = ifgDir + '/' + Pair
    
    # GACOS data directory
    if inps.ztdDir:
        gacosDir = inps.ztdDir
    else:
        gacosDir = scratchDir + '/' + projectName + "/GACOS"
    
    if not os.path.exists(gacosDir):
        os.makedirs(gacosDir)
    
    # Define input files
    dempar = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.par'
    slcpar = slcDir + '/' + masterDate + '/' + masterDate + '.slc.par'
    geo_unw = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw'
    out_file = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw.gacos'
    figdir = scratchDir + '/' + projectName + "/figure"
    
    # Check if geocoded interferogram exists
    if not os.path.isfile(geo_unw):
        print(f"Error: Geocoded interferogram not found: {geo_unw}")
        print("Please run geocode_gamma.py first.")
        sys.exit(1)
    
    print("="*60)
    print("GACOS Atmospheric Correction (Pure Python)")
    print(f"Project: {projectName}")
    print(f"Pair: {Pair}")
    print("="*60)
    
    # Step 1: Find GACOS ZTD files
    print("\nStep 1: Checking GACOS ZTD files...")
    
    ztd1 = None
    ztd2 = None
    
    if inps.ztd1 and inps.ztd2:
        ztd1 = inps.ztd1
        ztd2 = inps.ztd2
    else:
        ztd1 = find_existing_ztd(Mdate, gacosDir)
        ztd2 = find_existing_ztd(Sdate, gacosDir)
    
    if ztd1 and ztd2:
        print(f"  Master ZTD: {ztd1}")
        print(f"  Slave  ZTD: {ztd2}")
    elif inps.email:
        # Auto-download workflow (existing logic preserved)
        dem_info = parse_dem_par(dempar)
        North = dem_info['corner_lat']
        West = dem_info['corner_lon']
        South = North + (dem_info['nlines'] - 1) * dem_info['post_lat']
        East = West + (dem_info['width'] - 1) * dem_info['post_lon']
        bounds = (West, South, East, North)

        missing_dates = []
        if not ztd1: missing_dates.append(Mdate)
        if not ztd2: missing_dates.append(Sdate)
        
        if inps.autoDownload:
            email_config = None
            if inps.emailUser and inps.emailPass:
                email_config = {
                    'username': inps.emailUser, 'password': inps.emailPass,
                    'host': inps.emailHost, 'port': inps.emailPort, 'ssl': inps.emailSsl
                }
            ztd_files = auto_gacos_workflow(
                dates=missing_dates, bounds=bounds, gacos_dir=gacosDir,
                email=inps.email, email_config=email_config,
                wait_for_email=(email_config is not None), max_wait_hours=inps.waitHours
            )
            ztd1 = ztd1 or ztd_files.get(Mdate)
            ztd2 = ztd2 or ztd_files.get(Sdate)
        else:
            submit_gacos_request(dates=missing_dates, bounds=bounds,
                                 email=inps.email, gacos_dir=gacosDir)
            print(f"\nGACOS request submitted for: {missing_dates}")
            sys.exit(0)
    
    if ztd1 is None or ztd2 is None:
        print("\nError: GACOS ZTD files not found.")
        print(f"  Missing: {Mdate if not ztd1 else ''} {Sdate if not ztd2 else ''}")
        print(f"  Search dir: {gacosDir}")
        sys.exit(1)
    
    # Step 2: Apply GACOS correction (Pure Python, no GMT dependency)
    print("\nStep 2: Applying GACOS correction...")
    
    ztd1_rsc = ztd1 + '.rsc'
    ztd2_rsc = ztd2 + '.rsc'
    
    corrected_file = apply_gacos_correction_python(
        geo_unw_file=geo_unw,
        ztd1_file=ztd1, ztd1_rsc=ztd1_rsc,
        ztd2_file=ztd2, ztd2_rsc=ztd2_rsc,
        dempar=dempar,
        slcpar=slcpar,
        wavelength=inps.wavelength,
        out_file=out_file,
        figdir=figdir,
        pair_str=Pair
    )
    
    if corrected_file:
        # Step 3: Generate BMP preview using rasdt_pwr
        print("\nStep 3: Generating BMP preview...")
        geo_amp = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.amp'
        nWidthUTMDEM = str(parse_dem_par(dempar)['width'])
        
        if os.path.isfile(geo_amp):
            call_str = ('rasdt_pwr ' + corrected_file + ' ' + geo_amp + ' '
                        + nWidthUTMDEM + ' - - - - -3.14 3.14 1 rmg.cm')
            print(f"  {call_str}")
            os.system(call_str)
            bmp_file = corrected_file + '.bmp'
            if os.path.isfile(bmp_file):
                print(f"  BMP: {bmp_file}")
            else:
                print("  Warning: BMP file not generated")
        else:
            print(f"  Warning: geo_amp not found: {geo_amp}")
        
        print(f"\nGACOS atmospheric correction completed successfully!")
    else:
        print("\nGACOS correction failed!")
        sys.exit(1)
    
    sys.exit(0)


if __name__ == '__main__':
    main(sys.argv[:])
