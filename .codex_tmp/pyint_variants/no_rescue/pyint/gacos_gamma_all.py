#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ### 
###  Copy Right (c): 2017-2019, Yunmeng Cao                   ###  
###  Author: Yunmeng Cao                                      ###                                                          
###  Contact : ymcmrs@gmail.com                               ###  
#################################################################

import numpy as np
import os
import sys  
import time
import glob
import argparse
import subprocess

from pyint import _utils as ut


def work(data0):
    """Worker function for parallel processing"""
    cmd = data0[0]
    err_file = data0[1]
    p = subprocess.run(cmd, shell=False, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    stdout = p.stdout
    stderr = p.stderr
    
    if type(stderr) == bytes:
        aa = stderr.decode("utf-8")
    else:
        aa = stderr
    
    if aa:
        print(aa)
        str0 = cmd[0] + ' ' + cmd[1] + ' ' + cmd[2] + '\n'
        with open(err_file, 'a') as f:
            f.write(str0)
            f.write(aa)
            f.write('\n')

    return 


INTRODUCTION = '''
-------------------------------------------------------------------  
       Apply GACOS atmospheric correction to all interferograms
       for one project using GAMMA.
       
       This script processes all interferogram pairs listed in 
       ifgram_list.txt and applies GACOS tropospheric correction.
-------------------------------------------------------------------
'''

EXAMPLE = '''
    Usage: 
            gacos_gamma_all.py projectName
            gacos_gamma_all.py projectName --parallel 4
            gacos_gamma_all.py projectName --parallel 4 --ztd-dir /path/to/gacos
            gacos_gamma_all.py projectName --parallel 4 --ifgramList-txt /test/ifgram_list.txt
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Apply GACOS atmospheric correction to all interferograms for one project.',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=INTRODUCTION + '\n' + EXAMPLE)

    parser.add_argument('projectName', help='projectName for processing.')
    parser.add_argument('--parallel', dest='parallelNumb', type=int, default=1, 
                        help='Enable parallel processing and Specify the number of processors.')
    parser.add_argument('--ifgarmList-txt', dest='ifgarmListTxt', 
                        help='provided ifgram_list_txt. default: using ifgram_list.txt under projectName folder.')
    parser.add_argument('--ztd-dir', dest='ztdDir', 
                        help='Directory containing GACOS ZTD files. Default: projectName/GACOS')
    parser.add_argument('--email', dest='email', 
                        help='Email for GACOS auto-download.')
    parser.add_argument('--auto-download', dest='autoDownload', action='store_true',
                        help='Automatically submit and download missing GACOS data.')
    parser.add_argument('--email-user', dest='emailUser', help='Email username for IMAP download.')
    parser.add_argument('--email-pass', dest='emailPass', help='Email password for IMAP download.')
    parser.add_argument('--email-host', dest='emailHost', default='imap.gmail.com', help='IMAP host.')
    parser.add_argument('--email-port', dest='emailPort', type=int, default=None, help='IMAP port.')
    parser.add_argument('--email-ssl', dest='emailSsl', action='store_true', default=True, help='Use SSL.')
    parser.add_argument('--wait-hours', dest='waitHours', type=int, default=24,
                        help='Max hours to wait for GACOS email.')
    parser.add_argument('--skip-existing', dest='skipExisting', action='store_true',
                        help='Skip pairs that already have corrected output.')
    
    inps = parser.parse_args()
    return inps


def check_gacos_data_availability(dates, gacos_dir, bounds):
    """
    Check if GACOS ZTD data is available for all dates.
    
    Parameters:
    -----------
    dates : list
        List of acquisition dates (YYYYMMDD format)
    gacos_dir : str
        Directory containing GACOS data
    bounds : tuple
        Bounding box (West, South, East, North)
    
    Returns:
    --------
    missing_dates : list
        List of dates that don't have GACOS data
    """
    missing_dates = []
    
    for date in dates:
        # Check for ZTD files
        ztd_pattern = os.path.join(gacos_dir, date + "*.ztd.tif")
        ztd_files = glob.glob(ztd_pattern)
        
        if not ztd_files:
            # Also check for .ztd format
            ztd_pattern = os.path.join(gacos_dir, date + "*.ztd")
            ztd_files = glob.glob(ztd_pattern)
        
        if not ztd_files:
            missing_dates.append(date)
    
    return missing_dates


def get_project_dates(projectName, scratchDir):
    """
    Get all acquisition dates from the project.
    
    Returns:
    --------
    dates : list
        List of unique acquisition dates
    """
    slcDir = scratchDir + '/' + projectName + "/SLC"
    rslcDir = scratchDir + '/' + projectName + "/RSLC"
    
    dates = set()
    
    # Check SLC directory
    if os.path.exists(slcDir):
        for item in os.listdir(slcDir):
            if os.path.isdir(os.path.join(slcDir, item)):
                if len(item) == 8 and item.isdigit():
                    dates.add(item)
    
    # Check RSLC directory
    if os.path.exists(rslcDir):
        for item in os.listdir(rslcDir):
            if os.path.isdir(os.path.join(rslcDir, item)):
                if len(item) == 8 and item.isdigit():
                    dates.add(item)
    
    return sorted(list(dates))


def main(argv):
    start_time = time.time()
    inps = cmdLineParse()
    projectName = inps.projectName
    
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    projectDir = scratchDir + '/' + projectName
    ifgDir = scratchDir + '/' + projectName + '/ifgrams'
    
    templateDict = ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    
    # GACOS data directory (CLI > template > default)
    if inps.ztdDir:
        gacosDir = inps.ztdDir
    elif templateDict.get('gacos_dir', '').strip():
        gacosDir = templateDict['gacos_dir'].strip()
    else:
        gacosDir = projectDir + '/GACOS'
    
    if not os.path.exists(gacosDir):
        os.makedirs(gacosDir)
        print(f"Created GACOS directory: {gacosDir}")
    
    # 从模板读取邮箱配置（CLI 优先于模板）
    gacos_email = (inps.email or templateDict.get('gacos_email', '').strip()) or None
    gacos_email_user = (getattr(inps, 'emailUser', None) or templateDict.get('gacos_email_user', '').strip()) or None
    gacos_email_pass = (getattr(inps, 'emailPass', None) or templateDict.get('gacos_email_pass', '').strip()) or None
    gacos_email_host = (getattr(inps, 'emailHost', None) or templateDict.get('gacos_email_host', 'imap.163.com').strip())
    gacos_email_port = getattr(inps, 'emailPort', None) or int(templateDict.get('gacos_email_port', '993').strip())
    gacos_email_ssl = templateDict.get('gacos_email_ssl', '1').strip() == '1'
    gacos_check_interval = int(templateDict.get('gacos_check_interval', '60').strip())
    
    # 如果有用户名和密码，自动启用 auto-download
    auto_download = getattr(inps, 'autoDownload', False) or (gacos_email_user and gacos_email_pass)
    
    # Key files
    demDir = projectDir + '/DEM'
    slcDir = projectDir + '/SLC'
    dempar = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.par'
    slcpar = slcDir + '/' + masterDate + '/' + masterDate + '.slc.par'
    figdir = projectDir + '/figure'
    
    # Import pure Python correction functions from gacos_gamma
    from pyint.gacos_gamma import (parse_dem_par, resample_ztd_to_dem,
                                    find_existing_ztd, apply_gacos_correction_python,
                                    submit_gacos_request, auto_gacos_workflow)
    
    # Get interferogram list
    if inps.ifgarmListTxt:
        ifgramList_txt = inps.ifgarmListTxt
    else:
        ifgramList_txt = projectDir + '/ifgram_list.txt'
    
    if not os.path.isfile(ifgramList_txt):
        print(f"Error: Interferogram list not found: {ifgramList_txt}")
        sys.exit(1)
    
    ifgList0 = ut.read_txt2array(ifgramList_txt)
    
    import numpy as np
    ifgList0 = np.atleast_2d(np.array(ifgList0))
    ifgList = list(ifgList0[:, 0])
    
    # Collect all unique dates
    all_dates = set()
    for ifg in ifgList:
        mdate = ut.yyyymmdd(ifg.split('-')[0])
        sdate = ut.yyyymmdd(ifg.split('-')[1])
        all_dates.add(mdate)
        all_dates.add(sdate)
    all_dates = sorted(list(all_dates))
    
    # Read DEM parameters
    dem_info = parse_dem_par(dempar)
    
    print("="*60)
    print("GACOS Atmospheric Correction (Pure Python, ZTD Pre-cache)")
    print(f"Project: {projectName}")
    print(f"Interferograms: {len(ifgList)}, Dates: {len(all_dates)}")
    print(f"DEM: {dem_info['width']}×{dem_info['nlines']}")
    print(f"GACOS dir: {gacosDir}")
    print("="*60)
    
    # Check GACOS data availability using updated find_existing_ztd
    print("\nChecking GACOS data availability...")
    missing_dates = []
    ztd_paths = {}  # {date: ztd_file_path}
    for d in all_dates:
        ztd = find_existing_ztd(d, gacosDir)
        if ztd:
            ztd_paths[d] = ztd
        else:
            missing_dates.append(d)
    
    if missing_dates:
        print(f"\n⚠ GACOS data missing for {len(missing_dates)} dates: {missing_dates[:5]}...")
        print(f"ZTD available: {len(ztd_paths)}/{len(all_dates)} dates")
        
        # 自动提交/下载缺失的 GACOS 数据
        if gacos_email:
            # 从 DEM 参数计算研究区边界框
            North = dem_info['corner_lat']
            West = dem_info['corner_lon']
            South = North + (dem_info['nlines'] - 1) * dem_info['post_lat']
            East = West + (dem_info['width'] - 1) * dem_info['post_lon']
            bounds = (West, South, East, North)
            
            # 获取 SAR 采集时间（GACOS 要求非零）
            acq_time = templateDict.get('gacos_acq_time', '').strip()
            if not acq_time and os.path.isfile(slcpar):
                try:
                    with open(slcpar) as f:
                        for line in f:
                            if line.startswith('center_time:'):
                                secs = float(line.split()[1])
                                h = int(secs // 3600)
                                m = int((secs % 3600) // 60)
                                acq_time = f"{h:02d}:{m:02d}"
                                print(f"从 SLC par 读取采集时间: {acq_time} UTC")
                                break
                except Exception:
                    pass
            if not acq_time:
                acq_time = '10:00'
                print(f"使用默认采集时间: {acq_time} UTC")
            
            if auto_download and gacos_email_user and gacos_email_pass:
                # 完整自动流程：提交 → 等待邮件 → 下载
                email_config = {
                    'username': gacos_email_user, 'password': gacos_email_pass,
                    'host': gacos_email_host, 'port': gacos_email_port, 'ssl': gacos_email_ssl
                }
                wait_hours = getattr(inps, 'waitHours', 24)
                ztd_files = auto_gacos_workflow(
                    dates=missing_dates, bounds=bounds, gacos_dir=gacosDir,
                    email=gacos_email, email_config=email_config,
                    acquisition_time=acq_time,
                    wait_for_email=True, max_wait_hours=wait_hours,
                    check_interval=gacos_check_interval
                )
                # 重新检查已下载的日期
                for d in missing_dates[:]:
                    ztd = find_existing_ztd(d, gacosDir)
                    if ztd:
                        ztd_paths[d] = ztd
                        missing_dates.remove(d)
            else:
                # 仅提交请求，不等待下载
                submit_gacos_request(
                    dates=missing_dates, bounds=bounds,
                    email=gacos_email, gacos_dir=gacosDir,
                    acquisition_time=acq_time
                )
                print(f"\nGACOS request submitted for {len(missing_dates)} dates.")
                print("Please wait for email notification, then re-run this script.")
                if not ztd_paths:
                    print("No ZTD data available yet. Exiting.")
                    sys.exit(0)
        else:
            print("\nTip: 在模板中配置 gacos_email 可自动提交 GACOS 请求")
            print("     配置 gacos_email_user + gacos_email_pass 可自动轮询下载")
        
        if missing_dates:
            print(f"\n仍有 {len(missing_dates)} 个日期缺失 ZTD，相关干涉对将被跳过")
    
    print(f"\nZTD available: {len(ztd_paths)}/{len(all_dates)} dates")
    
    # Pre-cache all ZTD resampled to DEM grid (each date only once)
    print(f"\nPre-loading {len(ztd_paths)} ZTD files to DEM grid...")
    t0 = time.time()
    ztd_cache = {}
    for i, (d, ztd_file) in enumerate(sorted(ztd_paths.items())):
        ztd_rsc = ztd_file + '.rsc'
        ztd_cache[d] = resample_ztd_to_dem(ztd_file, ztd_rsc, dem_info)
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(ztd_paths)}] {d}")
    t_cache = time.time() - t0
    mem_gb = sum(v.nbytes for v in ztd_cache.values()) / 1024**3
    print(f"  Pre-load done: {t_cache:.1f}s, ~{mem_gb:.2f} GB")
    
    # Read incidence angle and wavelength from SLC par
    import numpy as np
    inc_angle_deg = 39.0
    wavelength = 0.0554657595
    if os.path.isfile(slcpar):
        with open(slcpar) as f:
            for line in f:
                if line.startswith('incidence_angle:'):
                    inc_angle_deg = float(line.split(':')[1].strip().split()[0])
                if line.startswith('radar_frequency:'):
                    freq = float(line.split(':')[1].strip().split()[0])
                    wavelength = 299792458.0 / freq
    cos_inc = np.cos(np.radians(inc_angle_deg))
    ztd_to_phase = 4.0 * np.pi / wavelength
    
    print(f"\n入射角: {inc_angle_deg:.4f}°, 波长: {wavelength:.10f} m")
    
    # Process each pair
    print(f"\nProcessing {len(ifgList)} interferograms...\n")
    
    ok_count = 0
    skip_count = 0
    err_count = 0
    errors = []
    plot_count = 0
    MAX_PLOTS = 5  # only plot first 5 pairs
    
    for i, ifg in enumerate(ifgList):
        mdate = ut.yyyymmdd(ifg.split('-')[0])
        sdate = ut.yyyymmdd(ifg.split('-')[1])
        pair = mdate + '-' + sdate
        
        workDir = ifgDir + '/' + pair
        unw_file = workDir + '/geo_' + pair + '_' + rlks + 'rlks.diff_filt.unw'
        out_file = workDir + '/geo_' + pair + '_' + rlks + 'rlks.diff_filt.unw.gacos'
        
        # Skip if already exists
        if inps.skipExisting and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
            skip_count += 1
            continue
        
        # Skip if input missing
        if not os.path.isfile(unw_file):
            err_count += 1
            errors.append(f"{pair}: geo_unw not found")
            continue
        
        # Skip if ZTD missing
        if mdate not in ztd_cache or sdate not in ztd_cache:
            err_count += 1
            errors.append(f"{pair}: ZTD missing for {mdate if mdate not in ztd_cache else sdate}")
            continue
        
        try:
            # Read unwrapped phase
            unw_data = np.fromfile(unw_file, dtype=np.float32).reshape(
                dem_info['nlines'], dem_info['width'])
            
            # Compute phase correction from cached ZTD
            dztd = ztd_cache[sdate] - ztd_cache[mdate]
            phase_correction = dztd * ztd_to_phase / cos_inc
            
            # Valid pixel mask (exclude zeros, NaN, GAMMA artifacts/unwrap errors)
            PHASE_THRESH = 1000.0
            valid = ((unw_data != 0)
                     & np.isfinite(unw_data)
                     & (np.abs(unw_data) < PHASE_THRESH)
                     & np.isfinite(phase_correction))
            n_valid = np.sum(valid)
            
            if n_valid == 0:
                err_count += 1
                errors.append(f"{pair}: no valid pixels")
                continue
            
            std_before = float(np.std(unw_data[valid]))
            
            # Apply correction (preserve original data, only modify valid pixels)
            corrected = unw_data.copy()
            corrected[valid] = unw_data[valid] - phase_correction[valid]
            
            # Demean (only valid pixels)
            mean_val = np.mean(corrected[valid])
            corrected[valid] -= mean_val
            
            std_after = float(np.std(corrected[valid]))
            
            # Save
            corrected.astype(np.float32).tofile(out_file)
            
            # Generate BMP preview using rasdt_pwr
            geo_amp = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.amp'
            if os.path.isfile(geo_amp):
                call_str = ('rasdt_pwr ' + out_file + ' ' + geo_amp + ' '
                            + str(dem_info['width'])
                            + ' - - - - -3.14 3.14 1 rmg.cm')
                os.system(call_str)
            
            reduction = (1 - std_after / std_before) * 100 if std_before > 0 else 0
            ok_count += 1
            
            # Plot first N pairs
            do_plot = (plot_count < MAX_PLOTS)
            if do_plot:
                from pyint.gacos_gamma import plot_gacos_comparison
                plot_gacos_comparison(unw_data, phase_correction, corrected,
                                      valid, dem_info, std_before, std_after,
                                      pair, figdir)
                plot_count += 1
            
            if ok_count <= 5 or ok_count % 50 == 0:
                print(f"  [{i+1}/{len(ifgList)}] {pair}: ✅ std {std_before:.2f}->{std_after:.2f} ({reduction:.1f}%)")
        
        except Exception as e:
            err_count += 1
            errors.append(f"{pair}: {str(e)}")
            if err_count <= 3:
                print(f"  [{i+1}/{len(ifgList)}] {pair}: ❌ {e}")
    
    elapsed = time.time() - start_time
    
    # Summary
    print("\n" + "="*60)
    print("GACOS Atmospheric Correction Summary")
    print("="*60)
    print(f"  Total: {len(ifgList)}")
    print(f"  Success: {ok_count}")
    print(f"  Skipped: {skip_count}")
    print(f"  Failed: {err_count}")
    
    if errors:
        err_txt = projectDir + '/gacos_gamma_all.err'
        with open(err_txt, 'w') as f:
            for e in errors:
                f.write(e + '\n')
        print(f"\n  Error log: {err_txt}")
        for e in errors[:5]:
            print(f"    {e}")
    
    print(f"\n  Time: {elapsed/60:.1f} min")
    if plot_count > 0:
        print(f"  Comparison plots: {figdir}/GACOS_comparison_*.png")
    print("="*60)
    
    sys.exit(0)


if __name__ == '__main__':
    main(sys.argv[:])
