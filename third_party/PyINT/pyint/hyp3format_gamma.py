#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ### 
###  Copy Right (c): 2017-2019, Yunmeng Cao                   ###  
###  Author: Yunmeng Cao                                      ###
###  Modified: 2026, Z. Zou - HyP3 UTM GeoTIFF output        ###
###  Contact : ymcmrs@gmail.com                               ###  
#################################################################

import numpy as np
import os
import sys  
import argparse
import time
from datetime import datetime

from pyint import _utils as ut


INTRODUCTION = '''
-------------------------------------------------------------------  
   将 geocode_gamma.py 已地理编码的 GAMMA 二进制产品转换为
   ASF HyP3 兼容的 UTM GeoTIFF 格式, 用于 MintPy 时间序列分析。

   本脚本 **不做任何地理编码或计算**, 仅执行:
     1. data2geotiff  : GAMMA 二进制 (EQA) → GeoTIFF (EQA)
     2. gdalwarp      : GeoTIFF (EQA) → GeoTIFF (UTM)
     3. rasterio      : 生成水体掩膜

   所有地理编码产品由 geocode_gamma.py 提供:
     amp, corr, dem, unw_phase, wrapped_phase,
     los_disp, vert_disp, lv_theta, lv_phi
'''

EXAMPLE = '''
    Usage: 
            hyp3format_gamma.py projectName ifgPair
            hyp3format_gamma.py shanghaiT171F128S1A 20241105-20241117
            hyp3format_gamma.py shanghaiT171F128S1A 20241105-20241117 --output-dir /path/to/output
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(
        description='Convert geocoded GAMMA binaries to HyP3 UTM GeoTIFF.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=INTRODUCTION + '\n' + EXAMPLE)
    parser.add_argument('projectName', help='项目名称')
    parser.add_argument('ifgPair', help='干涉图对 (如 20241105-20241117)')
    parser.add_argument('--output-dir', dest='output_dir', default=None,
                        help='输出目录. 默认: projectDir/Hyp3Products/')
    inps = parser.parse_args()
    return inps


def get_utm_epsg(lon, lat):
    """根据中心经纬度自动计算 UTM 投影的 EPSG 代码"""
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def run_cmd(cmd, desc=''):
    """执行 shell 命令并检查返回值"""
    if desc:
        print(f"  [{desc}] {cmd}")
    else:
        print(f"  {cmd}")
    ret = os.system(cmd)
    if ret != 0:
        print(f"  WARNING: 返回非零退出码 {ret}")
    return ret


def main(argv):
    
    start_time = time.time()
    inps = cmdLineParse() 
    projectName = inps.projectName
    ifgPair = inps.ifgPair
    
    Mdate = ifgPair.split('-')[0]
    Sdate = ifgPair.split('-')[1]
    
    # 验证日期并计算时间基线
    d1 = datetime.strptime(Mdate, '%Y%m%d')
    d2 = datetime.strptime(Sdate, '%Y%m%d')
    interval = abs((d2 - d1).days)
    
    # 读取模板参数
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + '/' + projectName + '.template'
    templateDict = ut.update_template(templateFile)
    
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    
    # 项目目录
    projectDir = scratchDir + '/' + projectName
    ifgDir = projectDir + '/ifgrams'
    demDir = projectDir + '/DEM'
    
    workDir = ifgDir + '/' + ifgPair
    if not os.path.isdir(workDir):
        print(f"ERROR: 干涉图目录不存在: {workDir}")
        sys.exit(1)
    
    # 输出目录
    outputDir = inps.output_dir or (projectDir + '/Hyp3Products')
    productDir = os.path.join(outputDir, ifgPair)
    os.makedirs(productDir, exist_ok=True)
    
    # ============================================================
    # DEM 参数 (直接引用, 不复制不删除)
    # ============================================================
    DEMpar = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.par'
    
    nWidthDEM = ut.read_gamma_par(DEMpar, 'read', 'width')
    nLineDEM  = ut.read_gamma_par(DEMpar, 'read', 'nlines')
    
    # 自动检测 UTM 投影
    corner_lat = float(ut.read_gamma_par(DEMpar, 'read', 'corner_lat').split()[0])
    corner_lon = float(ut.read_gamma_par(DEMpar, 'read', 'corner_lon').split()[0])
    post_lat = float(ut.read_gamma_par(DEMpar, 'read', 'post_lat').split()[0])
    post_lon = float(ut.read_gamma_par(DEMpar, 'read', 'post_lon').split()[0])
    center_lat = corner_lat + float(nLineDEM) / 2 * post_lat
    center_lon = corner_lon + float(nWidthDEM) / 2 * post_lon
    utm_epsg = get_utm_epsg(center_lon, center_lat)
    
    # HyP3 文件名前缀 (含 YYYYMMDDTHHMMSS, 兼容 MintPy 日期解析)
    hyp3_prefix = f"{projectName}_{Mdate}T000000_{Sdate}T000000"
    
    print("\n" + "=" * 70)
    print(f"HyP3 格式转换: {projectName} / {ifgPair}")
    print(f"时间基线: {interval} 天, DEM: {nWidthDEM}×{nLineDEM}")
    print(f"中心: {center_lat:.4f}°N, {center_lon:.4f}°E → EPSG:{utm_epsg}")
    print(f"输出: {productDir}")
    print("=" * 70)
    
    # ============================================================
    # 已地理编码 (EQA) GAMMA 二进制文件 (由 geocode_gamma.py 生成)
    # ============================================================
    geo_unw         = workDir + '/geo_' + ifgPair + '_' + rlks + 'rlks.diff_filt.unw'
    geo_wrapped_pha = workDir + '/geo_' + ifgPair + '_' + rlks + 'rlks.diff_filt.pha'
    geo_los_disp    = workDir + '/geo_' + ifgPair + '_' + rlks + 'rlks.los_disp'
    geo_vert_disp   = workDir + '/geo_' + ifgPair + '_' + rlks + 'rlks.vert_disp'
    geo_amp         = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.amp'
    geo_hgt         = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.hgt'
    lv_theta        = workDir + '/lv_theta'
    lv_phi          = workDir + '/lv_phi'
    
    # 相干性可能用 Pair 或 masterDate 命名
    geo_cor = workDir + '/geo_' + ifgPair + '_' + rlks + 'rlks.diff_filt.cor'
    if not os.path.exists(geo_cor):
        geo_cor = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.diff_filt.cor'
    
    # ============================================================
    # data2geotiff (EQA binary → EQA GeoTIFF) → gdalwarp (→ UTM)
    # ============================================================
    print("\n--- GAMMA 二进制 → EQA GeoTIFF → UTM GeoTIFF ---")
    
    product_map = [
        ('amp',            geo_amp,          'bilinear'),
        ('corr',           geo_cor,          'bilinear'),
        ('dem',            geo_hgt,          'bilinear'),
        ('unw_phase',      geo_unw,          'bilinear'),
        ('wrapped_phase',  geo_wrapped_pha,  'bilinear'),
        ('los_disp',       geo_los_disp,     'bilinear'),
        ('vert_disp',      geo_vert_disp,    'bilinear'),
        ('lv_theta',       lv_theta,         'bilinear'),
        ('lv_phi',         lv_phi,           'bilinear'),
    ]
    
    generated = {}
    
    for name, geo_bin, interp in product_map:
        utm_tif = os.path.join(productDir, f'{hyp3_prefix}_{name}.tif')
        
        # 跳过已存在且非空的输出
        if os.path.exists(utm_tif) and os.path.getsize(utm_tif) > 0:
            print(f"  {name}: 已存在，跳过")
            generated[name] = utm_tif
            continue
        
        if not os.path.exists(geo_bin):
            print(f"  {name}: 源文件不存在 ({os.path.basename(geo_bin)})，跳过")
            continue
        
        eqa_tif = workDir + '/' + name + '_hyp3_eqa.tif'
        
        # GAMMA 二进制 → EQA GeoTIFF
        run_cmd(f'data2geotiff {DEMpar} {geo_bin} 2 {eqa_tif}', f'{name} → EQA')
        
        if not os.path.exists(eqa_tif):
            print(f"  WARNING: {name} EQA GeoTIFF 生成失败")
            continue
        
        # EQA → UTM (gdalwarp)
        run_cmd(f'gdalwarp -t_srs EPSG:{utm_epsg} -r {interp} '
                f'-co COMPRESS=LZW -overwrite {eqa_tif} {utm_tif}',
                f'{name} → UTM')
        
        # 清理 EQA 中间文件
        if os.path.exists(eqa_tif):
            os.remove(eqa_tif)
        
        if os.path.exists(utm_tif) and os.path.getsize(utm_tif) > 0:
            generated[name] = utm_tif
    
    # ============================================================
    # 生成水体掩膜 (相干性阈值)
    # ============================================================
    print("\n--- 水体掩膜 ---")
    water_mask_tif = os.path.join(productDir, f'{hyp3_prefix}_water_mask.tif')
    corr_tif = generated.get('corr')
    
    if corr_tif and os.path.exists(corr_tif) and not os.path.exists(water_mask_tif):
        try:
            import rasterio
            with rasterio.open(corr_tif) as src:
                corr_data = src.read(1)
                mask = np.ones_like(corr_data, dtype=np.uint8)
                mask[corr_data < 0.05] = 0
                mask[np.isnan(corr_data)] = 0
                
                profile = src.profile.copy()
                profile.update(dtype=rasterio.uint8, count=1, compress='lzw', nodata=None)
                with rasterio.open(water_mask_tif, 'w', **profile) as dst:
                    dst.write(mask, 1)
            generated['water_mask'] = water_mask_tif
            print(f"  水体掩膜已生成")
        except Exception as e:
            print(f"  WARNING: 水体掩膜生成失败: {e}")
    elif os.path.exists(water_mask_tif):
        generated['water_mask'] = water_mask_tif
        print(f"  水体掩膜已存在，跳过")
    
    # ============================================================
    # 生成入射角图 (inc_map / inc_map_ell, 复制自 lv_theta)
    # ============================================================
    print("\n--- 入射角图 ---")
    inc_map_tif     = os.path.join(productDir, f'{hyp3_prefix}_inc_map.tif')
    inc_map_ell_tif = os.path.join(productDir, f'{hyp3_prefix}_inc_map_ell.tif')
    lv_theta_tif    = generated.get('lv_theta')
    
    if lv_theta_tif and os.path.exists(lv_theta_tif):
        if not os.path.exists(inc_map_tif):
            os.system(f'cp {lv_theta_tif} {inc_map_tif}')
            generated['inc_map'] = inc_map_tif
            print(f"  inc_map 已生成 (= lv_theta)")
        if not os.path.exists(inc_map_ell_tif):
            os.system(f'cp {lv_theta_tif} {inc_map_ell_tif}')
            generated['inc_map_ell'] = inc_map_ell_tif
            print(f"  inc_map_ell 已生成 (近似 lv_theta)")
    
    # ============================================================
    # 输出摘要
    # ============================================================
    print("\n" + "=" * 70)
    print(f"HyP3 格式转换完成: {ifgPair}")
    print(f"共 {len(generated)} 个产品:")
    for name, path in sorted(generated.items()):
        if os.path.exists(path):
            size_mb = os.path.getsize(path) / 1024 / 1024
            print(f"  {name:20s} ({size_mb:.1f} MB)")
    print("=" * 70)
    
    ut.print_process_time(start_time, time.time())
    sys.exit(0)


if __name__ == '__main__':
    main(sys.argv[:])
