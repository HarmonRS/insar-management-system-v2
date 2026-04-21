#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ###
###  Pixel Offset Tracking (POT) for a single pair            ###
###  Based on GAMMA offset_pwr_tracking / offset_pwr_tracking2###
###  Author: ZYD / Cascade AI                                 ###
#################################################################

import os
import sys
import time
import argparse
import numpy as np

from pyint import _utils as ut


INTRODUCTION = '''
-------------------------------------------------------------------
  Pixel Offset Tracking (POT) for a single interferometric pair
  using GAMMA software.

  Two-round estimation approach (Greenland tracking demo):
    Round 1: Large search window for initial offset field
    Post-processing: Quality check, outlier removal, gap filling
    Round 2: Smaller window with conditioned Round 1 as initial
    Final: Convert pixel offsets to displacement in meters + Geocode
'''

EXAMPLE = '''
    Usage:
        POT_gamma.py projectName Mdate Sdate
        POT_gamma.py shanghaiT171F128S1A 20241105 20241117
-------------------------------------------------------------------
'''


def run_cmd(cmd_str):
    """执行 GAMMA 命令并打印"""
    print(f'  >> {cmd_str}')
    return os.system(cmd_str)


def sanitize_gamma_float(filepath, valid_max=1e6):
    """
    清理 GAMMA 浮点数据文件中的无效值。
    GAMMA 对无效像素写入特殊标记值（~3.4e38 / NaN / Inf），
    这些值无法被 single_class_mapping 的范围过滤器正确捕获。
    将 NaN / Inf / |val| > valid_max 的像素替换为 0.0。
    """
    data = np.fromfile(filepath, dtype=np.float32)
    bad_mask = ~np.isfinite(data) | (np.abs(data) > valid_max)
    n_bad = int(np.sum(bad_mask))
    if n_bad > 0:
        data[bad_mask] = 0.0
        data.tofile(filepath)
        print(f'  [sanitize] {os.path.basename(filepath)}: '
              f'清理 {n_bad} 个无效像素 (NaN/Inf/|val|>{valid_max})')


def postprocess_offsets(offs_cpx, ccp, mli, width,
                        ccp_thresh, roff_min, roff_max,
                        azoff_min, azoff_max,
                        drange_thresh, dazimuth_thresh,
                        median_win, median_nmin,
                        prefix):
    """
    偏移量场后处理流程（参考 GAMMA Greenland tracking demo）:
      1) 提取距离向/方位向分量 + 清理 GAMMA no-data 标记值
      2) 初始掩膜: 互相关阈值 + 偏移量范围限制
      3) 中值滤波 + 偏差计算
      4) 偏差阈值精细掩膜
      5) 空洞填充
      6) 空间滤波 → 组合为 conditioned 复数偏移量

    返回: (condi, real_interp, imag_interp)
        - condi: fspf 平滑后的复数偏移量（供 Round 2 初始值）
        - real_interp / imag_interp: 填充后的偏移量（供最终米制转换）
    """
    real_file = prefix + '.real'
    imag_file = prefix + '.imag'

    # --- 1) 提取距离向 (real) 和方位向 (imag) ---
    run_cmd(f'cpx_to_real {offs_cpx} {real_file} {width} 0')
    run_cmd(f'cpx_to_real {offs_cpx} {imag_file} {width} 1')

    # 清理 GAMMA no-data 标记值（~3.4e38），避免污染后续掩膜和插值
    valid_max = max(abs(float(roff_max)), abs(float(roff_min)),
                    abs(float(azoff_max)), abs(float(azoff_min))) * 10
    sanitize_gamma_float(real_file, valid_max)
    sanitize_gamma_float(imag_file, valid_max)
    sanitize_gamma_float(ccp, 1.0)

    # --- 2) 初始掩膜: 互相关 + 偏移量范围 ---
    mask1 = prefix + '.mask1.bmp'
    real_m1 = prefix + '.real.masked1'
    imag_m1 = prefix + '.imag.masked1'

    run_cmd(f'single_class_mapping 3 '
            f'{ccp} {ccp_thresh} 1.0 '
            f'{real_file} {roff_min} {roff_max} '
            f'{imag_file} {azoff_min} {azoff_max} '
            f'{mask1} {width} 1 0 1 1')
    run_cmd(f'mask_class {mask1} {real_file} {real_m1} 0 1 1 1 0 0.0')
    run_cmd(f'mask_class {mask1} {imag_file} {imag_m1} 0 1 1 1 0 0.0')

    # 初始掩膜后 BMP
    run_cmd(f'rasdt_pwr {real_m1} {mli} {width} - - - - '
            f'{roff_min} {roff_max} 0 rmg.cm {real_m1}.bmp - - 24')
    run_cmd(f'rasdt_pwr {imag_m1} {mli} {width} - - - - '
            f'{azoff_min} {azoff_max} 0 rmg.cm {imag_m1}.bmp - - 24')

    # --- 3) 中值滤波 + 偏差 ---
    real_med = prefix + '.real.median'
    imag_med = prefix + '.imag.median'
    dreal = prefix + '.dreal'
    dimag = prefix + '.dimag'

    run_cmd(f'median_filter {real_m1} {real_med} {width} '
            f'{median_win} {median_win} {median_nmin}')
    run_cmd(f'lin_comb 2 {real_m1} {real_med} 0. 1. -1. '
            f'{dreal} {width} 1 0 1 1')
    run_cmd(f'median_filter {imag_m1} {imag_med} {width} '
            f'{median_win} {median_win} {median_nmin}')
    run_cmd(f'lin_comb 2 {imag_m1} {imag_med} 0. 1. -1. '
            f'{dimag} {width} 1 0 1 1')

    # --- 4) 偏差阈值精细掩膜 ---
    mask2 = prefix + '.mask2.bmp'
    real_masked = prefix + '.real.masked'
    imag_masked = prefix + '.imag.masked'

    run_cmd(f'single_class_mapping 5 '
            f'{dreal} -{drange_thresh} {drange_thresh} '
            f'{dimag} -{dazimuth_thresh} {dazimuth_thresh} '
            f'{ccp} {ccp_thresh} 1.0 '
            f'{real_file} {roff_min} {roff_max} '
            f'{imag_file} {azoff_min} {azoff_max} '
            f'{mask2} {width} 1 0 1 1 5')
    run_cmd(f'mask_class {mask2} {real_file} {real_masked} 0 1 1 1 0 0.0')
    run_cmd(f'mask_class {mask2} {imag_file} {imag_masked} 0 1 1 1 0 0.0')

    # 精细掩膜后 BMP
    run_cmd(f'rasdt_pwr {real_masked} {mli} {width} - - - - '
            f'{roff_min} {roff_max} 0 rmg.cm {real_masked}.bmp - - 24')
    run_cmd(f'rasdt_pwr {imag_masked} {mli} {width} - - - - '
            f'{azoff_min} {azoff_max} 0 rmg.cm {imag_masked}.bmp - - 24')

    # --- 5) 空洞填充 ---
    real_interp = prefix + '.real.interp'
    imag_interp = prefix + '.imag.interp'
    run_cmd(f'fill_gaps {real_masked} {width} {real_interp} 0 4 - 1')
    run_cmd(f'fill_gaps {imag_masked} {width} {imag_interp} 0 4 - 1')

    # 清理 fill_gaps 插值可能引入的 NaN/Inf/极端值
    sanitize_gamma_float(real_interp, valid_max)
    sanitize_gamma_float(imag_interp, valid_max)

    # 填充后 BMP
    run_cmd(f'rasdt_pwr {real_interp} {mli} {width} - - - - '
            f'{roff_min} {roff_max} 0 rmg.cm {real_interp}.bmp - - 24')
    run_cmd(f'rasdt_pwr {imag_interp} {mli} {width} - - - - '
            f'{azoff_min} {azoff_max} 0 rmg.cm {imag_interp}.bmp - - 24')

    # --- 6) 空间滤波 + 组合 conditioned ---
    real_fspf = prefix + '.real.fspf'
    imag_fspf = prefix + '.imag.fspf'
    run_cmd(f'fspf {real_interp} {real_fspf} {width} 2 2 2')
    run_cmd(f'fspf {imag_interp} {imag_fspf} {width} 2 2 2')

    condi = prefix + '.condi'
    run_cmd(f'real_to_cpx {real_fspf} {imag_fspf} {condi} {width} 0')

    return condi, real_interp, imag_interp


def cmdLineParse():
    parser = argparse.ArgumentParser(
        description='Pixel Offset Tracking for a single pair using GAMMA.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=INTRODUCTION + '\n' + EXAMPLE)

    parser.add_argument('projectName', help='projectName for processing.')
    parser.add_argument('Mdate', help='Master date.')
    parser.add_argument('Sdate', help='Slave date.')

    inps = parser.parse_args()
    return inps


def main(argv):

    start_time = time.time()
    inps = cmdLineParse()
    Mdate = inps.Mdate
    Sdate = inps.Sdate
    projectName = inps.projectName

    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + '/' + projectName + '.template'
    templateDict = ut.update_template(templateFile)

    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']

    # ========== POT 参数 ==========
    pot_rstep = templateDict['pot_rstep']
    pot_azstep = templateDict['pot_azstep']
    pot_rwin = templateDict['pot_rwin']
    pot_azwin = templateDict['pot_azwin']
    pot_rwin2 = templateDict['pot_rwin2']
    pot_azwin2 = templateDict['pot_azwin2']
    pot_ovr = templateDict['pot_ovr']
    pot_snr_thresh = templateDict['pot_snr_thresh']
    pot_ccp_thresh = templateDict['pot_ccp_thresh']
    pot_roff_min = templateDict['pot_roff_min']
    pot_roff_max = templateDict['pot_roff_max']
    pot_azoff_min = templateDict['pot_azoff_min']
    pot_azoff_max = templateDict['pot_azoff_max']
    pot_drange_thresh = templateDict['pot_drange_thresh']
    pot_dazimuth_thresh = templateDict['pot_dazimuth_thresh']
    pot_median_win = templateDict['pot_median_win']
    pot_median_nmin = templateDict['pot_median_nmin']
    pot_two_rounds = templateDict['pot_two_rounds']
    pot_geocode = templateDict['pot_geocode']
    pot_disp_max = templateDict['pot_disp_max']

    # ========== 目录 ==========
    projectDir = scratchDir + '/' + projectName
    rslcDir = projectDir + '/RSLC'
    demDir = projectDir + '/DEM'
    potDir = projectDir + '/offsets'
    if not os.path.isdir(potDir):
        os.mkdir(potDir)

    Pair = Mdate + '-' + Sdate
    workDir = potDir + '/' + Pair
    if not os.path.isdir(workDir):
        os.mkdir(workDir)

    # ========== 输入文件 ==========
    Mrslc = rslcDir + '/' + Mdate + '/' + Mdate + '.rslc'
    MrslcPar = rslcDir + '/' + Mdate + '/' + Mdate + '.rslc.par'
    Srslc = rslcDir + '/' + Sdate + '/' + Sdate + '.rslc'
    SrslcPar = rslcDir + '/' + Sdate + '/' + Sdate + '.rslc.par'

    slc_width = ut.read_gamma_par(MrslcPar, 'read', 'range_samples')
    off_width = str(int(int(slc_width) // int(pot_rstep)))

    print('=' * 60)
    print(f'Pixel Offset Tracking (POT): {Pair}')
    print(f'  SLC width       : {slc_width}')
    print(f'  Offset width    : {off_width}')
    print(f'  Step (r x az)   : {pot_rstep} x {pot_azstep}')
    print(f'  R1 window       : {pot_rwin} x {pot_azwin}')
    if pot_two_rounds == '1':
        print(f'  R2 window       : {pot_rwin2} x {pot_azwin2}')
    print(f'  Offset range    : [{pot_roff_min}, {pot_roff_max}] r  '
          f'[{pot_azoff_min}, {pot_azoff_max}] az')
    print('=' * 60)

    #######################################################################
    # Step 1: 生成偏移量几何下的 MLI（背景图 + 尺寸参考）
    #######################################################################
    print('\n[Step 1] 生成偏移量几何 MLI ...')
    MLI_pot = workDir + '/' + Mdate + '.mli_pot'
    MLI_pot_par = workDir + '/' + Mdate + '.mli_pot.par'

    run_cmd(f'multi_look {Mrslc} {MrslcPar} {MLI_pot} {MLI_pot_par} '
            f'{pot_rstep} {pot_azstep}')
    run_cmd(f'raspwr {MLI_pot} {off_width} - - - - 1. .2 - {MLI_pot}.bmp')

    #######################################################################
    # Step 2: 创建偏移量参数文件
    #######################################################################
    print('\n[Step 2] 创建偏移量参数文件 ...')
    OFF = workDir + '/' + Pair + '.off'
    run_cmd(f'create_offset {MrslcPar} {SrslcPar} {OFF} 1 {rlks} {azlks} 0')

    #######################################################################
    # Step 3: Round 1 — 初始偏移量估计（大窗口）
    #######################################################################
    print(f'\n[Step 3] Round 1 偏移量估计 ({pot_rwin}x{pot_azwin}) ...')
    r1_tag = f'{pot_rwin}x{pot_azwin}'
    r1_prefix = workDir + '/' + Pair + '.offs' + r1_tag
    offs_r1 = r1_prefix
    ccp_r1 = workDir + '/' + Pair + '.ccp' + r1_tag

    run_cmd(f'offset_pwr_tracking {Mrslc} {Srslc} {MrslcPar} {SrslcPar} '
            f'{OFF} {offs_r1} {ccp_r1} '
            f'{pot_rwin} {pot_azwin} - {pot_ovr} {pot_snr_thresh} '
            f'{pot_rstep} {pot_azstep}')

    #######################################################################
    # Step 4: Round 1 后处理
    #######################################################################
    print(f'\n[Step 4] Round 1 后处理 ...')
    r1_condi, _, _ = postprocess_offsets(
        offs_cpx=offs_r1, ccp=ccp_r1, mli=MLI_pot, width=off_width,
        ccp_thresh=pot_ccp_thresh,
        roff_min=pot_roff_min, roff_max=pot_roff_max,
        azoff_min=pot_azoff_min, azoff_max=pot_azoff_max,
        drange_thresh=pot_drange_thresh, dazimuth_thresh=pot_dazimuth_thresh,
        median_win=pot_median_win, median_nmin=pot_median_nmin,
        prefix=r1_prefix)

    # 默认使用 Round 1 结果
    final_prefix = r1_prefix
    final_ccp = ccp_r1
    final_off = OFF

    #######################################################################
    # Step 5-6: Round 2 — 精细偏移量估计（小窗口，可选）
    #######################################################################
    if pot_two_rounds == '1':
        print(f'\n[Step 5] Round 2 偏移量估计 ({pot_rwin2}x{pot_azwin2}) ...')
        OFF2 = workDir + '/' + Pair + '.off2'
        run_cmd(f'create_offset {MrslcPar} {SrslcPar} {OFF2} 1 {rlks} {azlks} 0')

        r2_tag = f'{pot_rwin2}x{pot_azwin2}'
        r2_prefix = workDir + '/' + Pair + '.offs' + r2_tag
        offs_r2 = r2_prefix
        ccp_r2 = workDir + '/' + Pair + '.ccp' + r2_tag

        run_cmd(f'offset_pwr_tracking2 {Mrslc} {Srslc} {MrslcPar} {SrslcPar} '
                f'{OFF2} {offs_r2} {ccp_r2} {OFF} {r1_condi} '
                f'{pot_rwin2} {pot_azwin2} - {pot_ovr} {pot_snr_thresh} '
                f'{pot_rstep} {pot_azstep}')

        print(f'\n[Step 6] Round 2 后处理 ...')
        _, _, _ = postprocess_offsets(
            offs_cpx=offs_r2, ccp=ccp_r2, mli=MLI_pot, width=off_width,
            ccp_thresh=pot_ccp_thresh,
            roff_min=pot_roff_min, roff_max=pot_roff_max,
            azoff_min=pot_azoff_min, azoff_max=pot_azoff_max,
            drange_thresh=pot_drange_thresh, dazimuth_thresh=pot_dazimuth_thresh,
            median_win=pot_median_win, median_nmin=pot_median_nmin,
            prefix=r2_prefix)

        final_prefix = r2_prefix
        final_ccp = ccp_r2
        final_off = OFF2

    #######################################################################
    # Step 7: 像素偏移量 → 米制位移量
    #######################################################################
    print('\n[Step 7] 像素偏移量转换为地面位移 (米) ...')

    # 用填充后（非 fspf 平滑）的偏移量组合复数，供 offset_tracking 使用
    final_real_interp = final_prefix + '.real.interp'
    final_imag_interp = final_prefix + '.imag.interp'
    final_offs_combined = final_prefix + '.offs_combined'
    run_cmd(f'real_to_cpx {final_real_interp} {final_imag_interp} '
            f'{final_offs_combined} {off_width} 0')

    disp_map = workDir + '/' + Pair + '.disp_map'
    run_cmd(f'offset_tracking {final_offs_combined} {final_ccp} '
            f'{MrslcPar} {final_off} {disp_map} - 2 {pot_ccp_thresh} 0')

    # 提取位移分量
    disp_real = disp_map + '.real'   # 地距向位移 (米)
    disp_imag = disp_map + '.imag'   # 方位向位移 (米)
    disp_mag = disp_map + '.mag'     # 位移幅值 (米)

    run_cmd(f'cpx_to_real {disp_map} {disp_real} {off_width} 0')
    run_cmd(f'cpx_to_real {disp_map} {disp_imag} {off_width} 1')
    run_cmd(f'cpx_to_real {disp_map} {disp_mag} {off_width} 3')

    # 清理 offset_tracking 输出中的 NaN/Inf/极端值
    disp_max_m = float(pot_disp_max) * 10
    sanitize_gamma_float(disp_real, disp_max_m)
    sanitize_gamma_float(disp_imag, disp_max_m)
    sanitize_gamma_float(disp_mag, disp_max_m)

    # BMP 可视化
    run_cmd(f'rasdt_pwr {disp_real} {MLI_pot} {off_width} - - - - '
            f'-{pot_disp_max} {pot_disp_max} 1 rmg.cm {disp_real}.bmp - - 24')
    run_cmd(f'rasdt_pwr {disp_imag} {MLI_pot} {off_width} - - - - '
            f'-{pot_disp_max} {pot_disp_max} 1 rmg.cm {disp_imag}.bmp - - 24')
    run_cmd(f'rasdt_pwr {disp_mag} {MLI_pot} {off_width} - - - - '
            f'-{pot_disp_max} {pot_disp_max} 1 rmg.cm {disp_mag}.bmp - - 24')

    # 注意: 地理编码已移至 geocode_gamma.py --type pot
    # 用法: geocode_gamma.py projectName Pair --type pot

    print(f"\nPixel Offset Tracking for {Pair} is done!")
    ut.print_process_time(start_time, time.time())
    sys.exit(0)


if __name__ == '__main__':
    main(sys.argv[:])
