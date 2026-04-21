#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ###
###  Phase Bias Correction for GAMMA format interferograms     ###
###  Author: ZYD / Cascade AI                                 ###
#################################################################

import numpy as np
import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import rasterio
from rasterio.transform import Affine
from rasterio.crs import CRS

from pyint import _utils as ut


INTRODUCTION = '''
-------------------------------------------------------------------  
       Apply phase bias correction to interferograms.
       This script integrates the phase bias correction algorithm from:
       InSAR_PhaseBias_Correction/ directory
       
       Based on the paper:
       "Correcting InSAR phase bias caused by atmospheric delays 
        and other error sources using multiple aperture interferometry"
       (Reference: 10.1016/j.remote.2022.100013)
   
       Algorithm Steps:
       1. Read interferogram data (PhaseBias_01_Read_Data.py)
       2. Calculate loop closures (PhaseBias_02_Loop_Closures.py)
       3. Estimate calibration parameters an (PhaseBias_03_calibration_pars.py)
       4. Inversion for phase bias terms (PhaseBias_04_Inversion.py)
       5. Apply correction (PhaseBias_05_Correction.py)
   
'''

EXAMPLE = '''
    Usage: 
            phasebias_correction_gamma.py projectName [options]
            phasebias_correction_gamma.py PacayaT163TsxHhA --interval 12
            phasebias_correction_gamma.py PacayaT163TsxHhA --interval 12 --nlook 10
            phasebias_correction_gamma.py PacayaT163TsxHhA --interval 24 --num-a 2
-------------------------------------------------------------------  
'''

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Apply phase bias correction to interferograms.',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)
    parser.add_argument('projectName', help='projectName for processing.')
    parser.add_argument('--interval', type=int, default=12,
                        help='Data acquisition interval in days (6, 12, or 24). [default: 12]')
    parser.add_argument('--nlook', type=int, default=10,
                        help='Number of looks for multilooking. [default: 10]')
    parser.add_argument('--num-a', type=int, default=2,
                        help='Number of calibration parameters to estimate. [default: 2]')
    parser.add_argument('--start', type=str, default=None,
                        help='Start date (YYYYMMDD). If not specified, will use all available data.')
    parser.add_argument('--end', type=str, default=None,
                        help='End date (YYYYMMDD). If not specified, will use all available data.')
    parser.add_argument('--estimate-an', dest='estimate_an', action='store_true',
                        help='Estimate calibration parameters from data instead of using defaults.')
    parser.add_argument('--max-con', type=int, default=5,
                        help='Maximum number of connections to correct. [default: 5]')
    parser.add_argument('--parallel', type=int, default=8,
                        help='Number of parallel workers for data conversion. [default: 8]')
    parser.add_argument('--skip-convert', action='store_true',
                        help='Skip data conversion if GeoTIFF already exists.')

    inps = parser.parse_args()
    return inps


def find_date_range(ifgDir, interval=12):
    """Find the start and end dates from interferogram directory
    
    Args:
        ifgDir: interferogram directory
        interval: temporal baseline in days
    
    Returns:
        tuple: (start_date, end_date) in YYYYMMDD format
    """
    dates = []
    
    all_dirs = sorted([d for d in os.listdir(ifgDir) if os.path.isdir(os.path.join(ifgDir, d))])
    
    for dirname in all_dirs:
        if '-' in dirname:
            date_pair = dirname.split('-')
        elif '_' in dirname:
            date_pair = dirname.split('_')
        else:
            continue
        
        if len(date_pair) == 2:
            try:
                date1 = date_pair[0]
                date2 = date_pair[1]
                
                d1 = datetime.strptime(date1, '%Y%m%d')
                d2 = datetime.strptime(date2, '%Y%m%d')
                days = abs((d2 - d1).days)
                
                if days == interval:
                    dates.extend([date1, date2])
            except:
                continue
    
    if dates:
        dates = sorted(set(dates))
        return dates[0], dates[-1]
    
    return None, None


# =====================================================================
# GAMMA 二进制 → GeoTIFF 转换函数
# =====================================================================

def parse_utm_dem_par(par_file):
    """解析 GAMMA utm.dem.par 文件，提取地理编码参数"""
    params = {}
    with open(par_file) as f:
        for line in f:
            if ':' in line:
                key, val = line.split(':', 1)
                key = key.strip()
                val = val.strip().split()[0]  # 取第一个字段
                params[key] = val
    return {
        'width':      int(params['width']),
        'nlines':     int(params['nlines']),
        'corner_lat': float(params['corner_lat']),
        'corner_lon': float(params['corner_lon']),
        'post_lat':   float(params['post_lat']),
        'post_lon':   float(params['post_lon']),
    }


def write_geotiff(data, out_path, geo_info, dtype='float32', nodata=None):
    """将 numpy 数组写入带地理信息的 GeoTIFF (rasterio)"""
    height, width = data.shape
    transform = Affine(
        geo_info['post_lon'], 0, geo_info['corner_lon'],
        0, geo_info['post_lat'], geo_info['corner_lat']
    )
    profile = {
        'driver': 'GTiff',
        'dtype': dtype,
        'width': width,
        'height': height,
        'count': 1,
        'crs': CRS.from_epsg(4326),
        'transform': transform,
        'compress': 'lzw',
        'tiled': True,
    }
    if nodata is not None:
        profile['nodata'] = nodata
    with rasterio.open(str(out_path), 'w', **profile) as dst:
        dst.write(data, 1)


def convert_one_pair(args):
    """转换单个干涉对: GAMMA 二进制 → GeoTIFF
    
    返回: (pair_name, True/False, message)
    """
    pair_dir, out_ifg_dir, geo_info, master_date, rlks = args
    pair_dir = Path(pair_dir)
    pair_name = pair_dir.name                          # e.g. 20241105-20241117
    d1, d2 = pair_name.split('-')
    pair_us = f'{d1}_{d2}'                             # 下划线分隔
    width  = geo_info['width']
    nlines = geo_info['nlines']

    out_dir = Path(out_ifg_dir) / pair_us
    out_dir.mkdir(parents=True, exist_ok=True)

    pha_tif = out_dir / f'{pair_us}.geo.diff_pha.tif'
    cc_tif  = out_dir / f'{pair_us}.geo.cc.tif'

    # 如果两个文件已存在则跳过
    if pha_tif.exists() and cc_tif.exists():
        return (pair_name, True, '已存在,跳过')

    try:
        # --- 缩缩相位: FCOMPLEX → phase ---
        diff_filt = pair_dir / f'geo_{pair_name}_{rlks}rlks.diff_filt'
        if not diff_filt.exists():
            return (pair_name, False, f'缺少 {diff_filt.name}')

        cpx = np.fromfile(str(diff_filt), dtype=np.complex64).reshape(nlines, width)
        phase = np.angle(cpx).astype(np.float32)
        phase[cpx == 0] = 0.0  # GAMMA 无数据区域设为 0
        del cpx
        write_geotiff(phase, pha_tif, geo_info, dtype='float32', nodata=0)
        del phase

        # --- 相干性: FLOAT → 0-255 uint8 ---
        cor_file = pair_dir / f'geo_{master_date}_{rlks}rlks.diff_filt.cor'
        if not cor_file.exists():
            # 尝试备选命名
            cor_file = pair_dir / f'{pair_name}_{rlks}rlks.diff_filt.cor'
        if not cor_file.exists():
            return (pair_name, False, f'缺少 cor 文件')

        cor = np.fromfile(str(cor_file), dtype=np.float32).reshape(nlines, width)
        # 清理无效值
        cor[~np.isfinite(cor)] = 0
        cor = np.clip(cor, 0, 1)
        cc_uint8 = (cor * 255).astype(np.uint8)
        del cor
        write_geotiff(cc_uint8, cc_tif, geo_info, dtype='uint8', nodata=0)
        del cc_uint8

        return (pair_name, True, 'OK')

    except Exception as e:
        return (pair_name, False, str(e))


def create_landmask(amp_file, geo_info, out_path):
    """从地理编码幅度图生成陆地掩膜 GeoTIFF"""
    width  = geo_info['width']
    nlines = geo_info['nlines']
    amp = np.fromfile(str(amp_file), dtype=np.float32).reshape(nlines, width)
    mask = np.zeros_like(amp, dtype=np.uint8)
    mask[amp > 0] = 1
    write_geotiff(mask, out_path, geo_info, dtype='uint8', nodata=0)
    print(f'  陆地掩膜已生成: {out_path}')
    print(f'  有效像素: {int(np.sum(mask))}/{mask.size} ({100*np.sum(mask)/mask.size:.1f}%)')


def prepare_gamma_data_for_phasebias(ifgDir, demDir, masterDate, rlks,
                                      staging_dir, n_parallel=8,
                                      skip_existing=True):
    """将 GAMMA 格式地理编码干涉图转换为 PhaseBias 脚本期望的 GeoTIFF 格式
    
    目录结构:
      staging_dir/
        interferograms/
          YYYYMMDD_YYYYMMDD/
            YYYYMMDD_YYYYMMDD.geo.diff_pha.tif
            YYYYMMDD_YYYYMMDD.geo.cc.tif
        metadata/
          frame.geo.landmask.tif
    """
    staging = Path(staging_dir)
    ifg_out = staging / 'interferograms'
    meta_out = staging / 'metadata'
    ifg_out.mkdir(parents=True, exist_ok=True)
    meta_out.mkdir(parents=True, exist_ok=True)

    # 解析地理编码参数
    utm_par = Path(demDir) / f'{masterDate}_{rlks}rlks.utm.dem.par'
    if not utm_par.exists():
        raise FileNotFoundError(f'utm.dem.par 不存在: {utm_par}')
    geo_info = parse_utm_dem_par(str(utm_par))
    print(f'  地理编码参数: {geo_info["width"]}x{geo_info["nlines"]}, '
          f'corner=({geo_info["corner_lat"]:.4f}, {geo_info["corner_lon"]:.4f})')

    # 生成陆地掩膜
    landmask_tif = meta_out / 'frame.geo.landmask.tif'
    if not landmask_tif.exists():
        # 从任意干涉对目录中取地理编码幅度图
        first_pair = sorted(Path(ifgDir).iterdir())[0]
        amp_file = first_pair / f'geo_{masterDate}_{rlks}rlks.amp'
        if not amp_file.exists():
            print(f'  警告: 未找到幅度图 {amp_file}，将创建全 1 掩膜')
            mask = np.ones((geo_info['nlines'], geo_info['width']), dtype=np.uint8)
            write_geotiff(mask, landmask_tif, geo_info, dtype='uint8')
            print(f'  全 1 掩膜已生成: {landmask_tif}')
        else:
            create_landmask(amp_file, geo_info, landmask_tif)
    else:
        print(f'  陆地掩膜已存在: {landmask_tif}')

    # 收集需要转换的干涉对
    pair_dirs = sorted([d for d in Path(ifgDir).iterdir()
                        if d.is_dir() and '-' in d.name])
    print(f'  发现 {len(pair_dirs)} 个干涉对目录')

    # 并行转换
    tasks = [(str(pd), str(ifg_out), geo_info, masterDate, rlks) for pd in pair_dirs]

    done_ok = 0
    done_skip = 0
    done_fail = 0
    t0 = time.time()

    with ProcessPoolExecutor(max_workers=n_parallel) as pool:
        futures = {pool.submit(convert_one_pair, t): t[0] for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            pair_name, success, msg = fut.result()
            if success:
                if '跳过' in msg:
                    done_skip += 1
                else:
                    done_ok += 1
            else:
                done_fail += 1
                print(f'  ✗ {pair_name}: {msg}')

            if i % 50 == 0 or i == len(tasks):
                elapsed = time.time() - t0
                print(f'  进度: {i}/{len(tasks)}  '
                      f'(新转换={done_ok}, 跳过={done_skip}, '
                      f'失败={done_fail}, 耗时={elapsed:.0f}s)')

    print(f'\n  转换完成: 新转换={done_ok}, 跳过={done_skip}, 失败={done_fail}')
    if done_fail > 0:
        print(f'  警告: {done_fail} 个干涉对转换失败，将被 PhaseBias 脚本跳过')

    return str(staging)


# =====================================================================
# 配置文件生成
# =====================================================================

def create_config_file(output_path, root_path, interval, nlook, num_a,
                       start_date, end_date, estimate_an):
    """创建 PhaseBias 脚本的配置文件
    
    Args:
        output_path: 输出目录
        root_path: 包含 interferograms/ 和 metadata/ 子目录的根路径
    """
    config_content = f"""[DEFAULT]
# 包含 interferograms/ 和 metadata/ 子目录的根路径
root_path = {root_path}

output_path = {output_path}

LiCSAR_data=no
frame=NA

# Start and end dates
start={start_date}
end={end_date}

# Data acquisition interval: 6-day or 12-day or 24-day
interval={interval}

# Multilooking factor
nlook={nlook}

# Number of calibration parameters to estimate
num_a={num_a}

### an parameters
# Estimate from data or use default values
estimate_an_values={'yes' if estimate_an else 'no'}

# Using 6-days interval
a1_6_day=0.50
a2_6_day=0.36
a3_6_day=0.299
a4_6_day=0.2476

# Using 12-day interval
a1_12_day=0.494
a2_12_day=0.297
a3_12_day=0.24
a4_12_day=0.22

# Using 24-day interval (estimated)
a1_24_day=0.48
a2_24_day=0.28
a3_24_day=0.22
a4_24_day=0.20
"""
    config_file = os.path.join(output_path, 'config.txt')
    with open(config_file, 'w') as f:
        f.write(config_content)
    return config_file


def run_phasebias_scripts(phasebias_dir, output_path, script_name):
    """Run a PhaseBias script
    
    Args:
        phasebias_dir: directory containing PhaseBias scripts
        output_path: working directory
        script_name: name of the script to run
    """
    script_path = os.path.join(phasebias_dir, script_name)
    
    if not os.path.exists(script_path):
        print(f"ERROR: Script not found: {script_path}")
        return False
    
    print(f"\n{'='*80}")
    print(f"Running {script_name}...")
    print(f"{'='*80}")
    
    # Run the script
    original_dir = os.getcwd()
    os.chdir(output_path)
    
    try:
        import subprocess
        result = subprocess.run([sys.executable, script_path], 
                              capture_output=True, text=True, check=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"ERROR running {script_name}:")
        print(e.stdout)
        print(e.stderr)
        return False
    finally:
        os.chdir(original_dir)


def main(argv):

    start_time = time.time()
    inps = cmdLineParse()
    projectName = inps.projectName
    interval = inps.interval
    nlook = inps.nlook
    num_a = inps.num_a
    estimate_an = inps.estimate_an
    max_con = inps.max_con
    n_parallel = inps.parallel
    skip_convert = inps.skip_convert

    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')

    # PyINT 路径
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict = ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']

    ifgDir = scratchDir + '/' + projectName + "/ifgrams"
    demDir = scratchDir + '/' + projectName + "/DEM"

    # PhaseBias 脚本目录
    pyintDir = os.path.dirname(os.path.abspath(__file__))
    phasebiasDir = os.path.join(pyintDir, 'InSAR_PhaseBias_Correction')

    # 工作目录
    workDir = scratchDir + '/' + projectName + "/PhaseBiasCorrection"
    os.makedirs(workDir, exist_ok=True)
    os.makedirs(os.path.join(workDir, 'Data'), exist_ok=True)

    print("\n" + "="*80)
    print(f"Phase Bias Correction for {projectName}")
    print(f"Temporal interval: {interval} days")
    print(f"Multilooking factor: {nlook}")
    print(f"Number of calibration parameters: {num_a}")
    print(f"Estimate an from data: {estimate_an}")
    print(f"Max connections: {max_con}")
    print(f"Parallel workers: {n_parallel}")
    print("="*80)

    # 确定日期范围
    if inps.start and inps.end:
        start_date = inps.start
        end_date = inps.end
    else:
        print("\n自动检测日期范围...")
        start_date, end_date = find_date_range(ifgDir, interval)
        if not start_date or not end_date:
            print("ERROR: 无法从干涉图目录确定日期范围!")
            print("请指定 --start 和 --end 参数.")
            sys.exit(1)

    print(f"\n处理日期范围: {start_date} ~ {end_date}")

    # ====== Step 0: GAMMA 二进制 → GeoTIFF 转换 ======
    print("\n" + "="*80)
    print("Step 0: GAMMA 二进制 → GeoTIFF 数据转换")
    print("="*80)

    staging_dir = os.path.join(workDir, 'GEOC_staging')
    staging_root = prepare_gamma_data_for_phasebias(
        ifgDir, demDir, masterDate, str(rlks),
        staging_dir, n_parallel=n_parallel,
        skip_existing=skip_convert
    )

    # ====== 创建配置文件 ======
    print("\n创建配置文件...")
    # root_path 指向 staging 目录（包含 interferograms/ 和 metadata/）
    config_file = create_config_file(workDir, staging_root, interval, nlook,
                                     num_a, start_date, end_date, estimate_an)
    print(f"配置文件: {config_file}")

    # ====== 运行 PhaseBias 流水线 ======
    print("\n" + "="*80)
    print("运行 Phase Bias Correction 流水线")
    print("="*80)

    # Step 1: 读取数据
    if not run_phasebias_scripts(phasebiasDir, workDir, 'PhaseBias_01_Read_Data.py'):
        print("ERROR in Step 1: Read Data")
        sys.exit(1)

    # Step 2: 计算闭合环
    if not run_phasebias_scripts(phasebiasDir, workDir, 'PhaseBias_02_Loop_Closures.py'):
        print("ERROR in Step 2: Loop Closures")
        sys.exit(1)

    # Step 3: 估计标定参数（可选）
    if estimate_an:
        if not run_phasebias_scripts(phasebiasDir, workDir, 'PhaseBias_03_calibration_pars.py'):
            print("ERROR in Step 3: Calibration Parameters")
            sys.exit(1)

    # Step 4: 反演
    if not run_phasebias_scripts(phasebiasDir, workDir, 'PhaseBias_04_Inversion.py'):
        print("ERROR in Step 4: Inversion")
        sys.exit(1)

    # Step 5: 应用校正
    if not run_phasebias_scripts(phasebiasDir, workDir, 'PhaseBias_05_Correction.py'):
        print("ERROR in Step 5: Correction")
        sys.exit(1)

    print("\n" + "="*80)
    print("相位偏差校正完成!")
    print(f"输出目录: {workDir}")
    print("="*80)

    ut.print_process_time(start_time, time.time())


if __name__ == '__main__':
    main(sys.argv[:])
