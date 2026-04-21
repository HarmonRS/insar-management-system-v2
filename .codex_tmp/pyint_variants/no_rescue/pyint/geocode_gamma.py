#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ### 
###  Copy Right (c): 2017-2019, Yunmeng Cao                   ###  
###  Author: Yunmeng Cao                                      ###                                                          
###  Contact : ymcmrs@gmail.com                               ###  
#################################################################
import os
import sys  
import argparse
import numpy as np

from pyint import _utils as ut


def sanitize_gamma_float(filepath, valid_max=1e6):
    """清理 GAMMA 浮点数据文件中的无效值 (NaN/Inf/极端值 → 0.0)"""
    data = np.fromfile(filepath, dtype=np.float32)
    bad_mask = ~np.isfinite(data) | (np.abs(data) > valid_max)
    n_bad = int(np.sum(bad_mask))
    if n_bad > 0:
        data[bad_mask] = 0.0
        data.tofile(filepath)
        print(f'  [sanitize] {os.path.basename(filepath)}: '
              f'清理 {n_bad} 个无效像素')
        
def geocode(inFile, outFile, UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp='0'):
    
    if '.unw' in os.path.basename(inFile):
        call_str = 'geocode_back ' + inFile + ' ' + nWidth + ' ' + UTMTORDC + ' ' + outFile + ' ' + nWidthUTMDEM + ' ' + nLineUTMDEM + ' ' + geo_interp + ' 0'
    elif '.amp' in os.path.basename(inFile):
        call_str = 'geocode_back ' + inFile + ' ' + nWidth + ' ' + UTMTORDC + ' ' + outFile + ' ' + nWidthUTMDEM + ' ' + nLineUTMDEM + ' ' + geo_interp + ' 0'
    elif '.cor' in os.path.basename(inFile):
        call_str = 'geocode_back ' + inFile + ' ' + nWidth + ' ' + UTMTORDC + ' ' + outFile + ' ' + nWidthUTMDEM + ' ' + nLineUTMDEM + ' ' + geo_interp + ' 0'
    elif '.dem' in os.path.basename(inFile):
        call_str = 'geocode_back ' + inFile + ' ' + nWidth + ' ' + UTMTORDC + ' ' + outFile + ' ' + nWidthUTMDEM + ' ' + nLineUTMDEM + ' ' + geo_interp + ' 0'
    else:
        call_str = 'geocode_back ' + inFile + ' ' + nWidth + ' ' + UTMTORDC + ' ' + outFile + ' ' + nWidthUTMDEM + ' ' + nLineUTMDEM+ ' ' + geo_interp + ' 1'

    os.system(call_str)
    
    return
    
INTRODUCTION = '''
-------------------------------------------------------------------  
 Convert radar-coordinates products into geo-coordinates using GAMMA.

 由模板参数 geocode_products 控制产品类型 (逗号分隔多选):
   hyp3    : 基础产品 + dispmap(LOS/vert) + wrapped_phase + look_vector
   licsbas : 基础产品 + look_vector
   pot     : Pixel Offset Tracking 位移图 (两步地理编码)

 基础产品 (hyp3/licsbas 均需): amp, corr, unw, diff_filt, diff, dem
 一次调用自动处理所有选中的产品类型。
'''

EXAMPLE = '''
    Usage: 
            geocode_gamma.py projectName Mdate-Sdate
            geocode_gamma.py PacayaT163TsxHhA 20150102-20150601

    Template parameter:
            geocode_products = hyp3,licsbas      (default)
            geocode_products = pot
            geocode_products = hyp3,licsbas,pot
-------------------------------------------------------------------  
'''

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Geocode radar-coordinate products using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('pair',help='Master-Slave, e.g., 20150101-20150106.')
    
    inps = parser.parse_args()
    return inps


def geocode_pot(projectName, Pair, templateDict):
    """POT 位移图地理编码: 偏移量几何 → MLI 几何 → EQA 几何"""
    scratchDir = os.getenv('SCRATCHDIR')
    projectDir = scratchDir + '/' + projectName
    potDir = projectDir + '/offsets'
    workDir = potDir + '/' + Pair
    demDir = projectDir + '/DEM'
    rslcDir = projectDir + '/RSLC'
    slcDir = projectDir + '/SLC'

    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    pot_disp_max = float(templateDict.get('pot_disp_max', '10'))

    Mdate = Pair.split('-')[0]

    if not os.path.isdir(workDir):
        print(f'ERROR: POT 目录不存在: {workDir}')
        return

    # ===== 公共文件 =====
    MampPar = rslcDir + '/' + masterDate + '/' + masterDate + '_' + rlks + 'rlks.amp.par'
    Mamp    = rslcDir + '/' + masterDate + '/' + masterDate + '_' + rlks + 'rlks.amp'
    UTMTORDC  = demDir + '/' + masterDate + '_' + rlks + 'rlks.UTM_TO_RDC'
    UTMDEMpar = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.par'
    UTMDEM    = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem'
    SLCpar = slcDir + '/' + Mdate + '/' + Mdate + '.slc.par'

    if not os.path.isfile(UTMTORDC) or not os.path.isfile(UTMDEMpar):
        print('  WARNING: POT 地理编码跳过 — 缺少 UTM_TO_RDC 或 utm.dem.par')
        return
    if not os.path.isfile(MampPar):
        print('  WARNING: POT 地理编码跳过 — 缺少 master amp.par')
        return

    mli_width  = ut.read_gamma_par(MampPar, 'read', 'range_samples')
    mli_nlines = ut.read_gamma_par(MampPar, 'read', 'azimuth_lines')
    eqa_width  = ut.read_gamma_par(UTMDEMpar, 'read', 'width')
    eqa_nlines = ut.read_gamma_par(UTMDEMpar, 'read', 'nlines')

    # ===== POT 特有文件 =====
    MLI_pot_par = workDir + '/' + Mdate + '.mli_pot.par'
    MLI_pot     = workDir + '/' + Mdate + '.mli_pot'
    disp_map    = workDir + '/' + Pair + '.disp_map'

    if not os.path.isfile(disp_map):
        print(f'  WARNING: disp_map 不存在: {disp_map}')
        return

    # 偏移量几何宽度
    off_width = ut.read_gamma_par(MLI_pot_par, 'read', 'range_samples')
    disp_max_m = pot_disp_max * 10

    # ===== Step A: 偏移量几何 → MLI 几何 (rdc_trans) =====
    print('  [POT] Step A: 偏移量几何 → MLI 几何')
    mli_to_pot_lt = workDir + '/mli_to_pot.lt'
    disp_map_mli  = workDir + '/' + Pair + '.disp_map_mli'

    if not os.path.isfile(mli_to_pot_lt):
        os.system(f'rdc_trans {MampPar} 0.1 {MLI_pot_par} {mli_to_pot_lt}')

    if not os.path.isfile(disp_map_mli):
        os.system(f'geocode_back {disp_map} {off_width} {mli_to_pot_lt} '
                  f'{disp_map_mli} {mli_width} {mli_nlines} 1 1')

    # ===== Step B: MLI 几何 → EQA 几何 =====
    print('  [POT] Step B: MLI 几何 → EQA 几何')
    geo_disp = workDir + '/geo_' + Pair + '.disp_map'

    if not os.path.isfile(geo_disp):
        os.system(f'geocode_back {disp_map_mli} {mli_width} {UTMTORDC} '
                  f'{geo_disp} {eqa_width} {eqa_nlines} 1 1')

    # ===== Step C: 提取地理编码位移分量 =====
    print('  [POT] Step C: 提取位移分量 (real/imag/mag)')
    geo_real = geo_disp + '.real'
    geo_imag = geo_disp + '.imag'
    geo_mag  = geo_disp + '.mag'

    if not os.path.isfile(geo_real):
        os.system(f'cpx_to_real {geo_disp} {geo_real} {eqa_width} 0')
        sanitize_gamma_float(geo_real, disp_max_m)
    if not os.path.isfile(geo_imag):
        os.system(f'cpx_to_real {geo_disp} {geo_imag} {eqa_width} 1')
        sanitize_gamma_float(geo_imag, disp_max_m)
    if not os.path.isfile(geo_mag):
        os.system(f'cpx_to_real {geo_disp} {geo_mag} {eqa_width} 3')
        sanitize_gamma_float(geo_mag, disp_max_m)

    # ===== Step D: 地理编码 MLI 背景 =====
    geo_mli = workDir + '/geo_' + masterDate + '.mli'
    if not os.path.isfile(geo_mli):
        os.system(f'geocode_back {Mamp} {mli_width} {UTMTORDC} '
                  f'{geo_mli} {eqa_width} {eqa_nlines} 5 0')

    # ===== Step E: 地理编码 CCP (互相关系数) =====
    # 自动查找最终 ccp 文件
    ccp_files = sorted([f for f in os.listdir(workDir) if f.startswith(Pair + '.ccp')])
    if ccp_files:
        ccp_final = workDir + '/' + ccp_files[-1]
        geo_ccp = workDir + '/geo_' + Pair + '.ccp'
        if not os.path.isfile(geo_ccp):
            # ccp 在偏移量几何, 需要两步
            ccp_mli = workDir + '/' + Pair + '.ccp_mli'
            os.system(f'geocode_back {ccp_final} {off_width} {mli_to_pot_lt} '
                      f'{ccp_mli} {mli_width} {mli_nlines} 1 0')
            os.system(f'geocode_back {ccp_mli} {mli_width} {UTMTORDC} '
                      f'{geo_ccp} {eqa_width} {eqa_nlines} 1 0')

    # ===== Step F: 视角矢量 (look_vector) =====
    print('  [POT] Step F: 视角矢量')
    lv_theta = workDir + '/lv_theta'
    lv_phi   = workDir + '/lv_phi'
    OFFpar = workDir + '/' + Pair + '.off'

    if not os.path.isfile(lv_theta) or not os.path.isfile(lv_phi):
        if os.path.isfile(SLCpar) and os.path.isfile(OFFpar):
            os.system(f'look_vector {SLCpar} {OFFpar} {UTMDEMpar} {UTMDEM} {lv_theta} {lv_phi}')

    # ===== BMP 可视化 =====
    print('  [POT] 生成 BMP')
    disp_max_str = str(pot_disp_max)
    os.system(f'rasdt_pwr {geo_mag} {geo_mli} {eqa_width} - - - - '
              f'-{disp_max_str} {disp_max_str} 1 rmg.cm {geo_mag}.bmp - - 24')
    os.system(f'rasdt_pwr {geo_real} {geo_mli} {eqa_width} - - - - '
              f'-{disp_max_str} {disp_max_str} 1 rmg.cm {geo_real}.bmp - - 24')
    os.system(f'rasdt_pwr {geo_imag} {geo_mli} {eqa_width} - - - - '
              f'-{disp_max_str} {disp_max_str} 1 rmg.cm {geo_imag}.bmp - - 24')

    print('  [POT] 地理编码完成!')


def main(argv):
    
    inps = cmdLineParse() 
    projectName = inps.projectName
    Pair = inps.pair
    
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + '/' + projectName + '.template'
    templateDict = ut.update_template(templateFile)

    # ===== 解析产品类型 =====
    products_str = templateDict.get('geocode_products', 'hyp3,licsbas')
    products = set(p.strip().lower() for p in products_str.split(','))
    need_ifg = ('hyp3' in products) or ('licsbas' in products)
    need_hyp3 = 'hyp3' in products
    need_licsbas = 'licsbas' in products
    need_pot = 'pot' in products

    print(f'[geocode_gamma] {projectName} / {Pair}')
    print(f'  geocode_products: {products_str}  →  ifg={need_ifg}, hyp3={need_hyp3}, licsbas={need_licsbas}, pot={need_pot}')

    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']

    ifgDir  = scratchDir + '/' + projectName + '/ifgrams'
    potDir  = scratchDir + '/' + projectName + '/offsets'
    demDir  = scratchDir + '/' + projectName + '/DEM'
    slcDir  = scratchDir + '/' + projectName + '/SLC'
    rslcDir = scratchDir + '/' + projectName + '/RSLC'

    # =========================================================
    #  IFG 地理编码 (hyp3 和/或 licsbas 需要)
    # =========================================================
    ifgWorkDir = ifgDir + '/' + Pair
    if need_ifg and os.path.isdir(ifgWorkDir):
        print('\n--- IFG 地理编码 ---')
        workDir = ifgWorkDir

        ######### copy common file for parallel processing #############
        Mamp0    = rslcDir + '/' + masterDate + '/' + masterDate + '_' + rlks + 'rlks.amp'
        MampPar0 = rslcDir + '/' + masterDate + '/' + masterDate + '_' + rlks + 'rlks.amp.par'
        Mamp     = workDir + '/' + masterDate + '_' + rlks + 'rlks.amp'
        MampPar  = workDir + '/' + masterDate + '_' + rlks + 'rlks.amp.par'
        ut.copy_file(Mamp0, Mamp)
        ut.copy_file(MampPar0, MampPar)

        #################################################################
        UNWIFG     = workDir + '/' + Pair + '_' + rlks + 'rlks.diff_filt.unw'
        ATMCOR_UNW = workDir + '/' + Pair + '_' + rlks + 'rlks.diff_filt.atmcor.unw'
        DIFFIFG    = workDir + '/' + Pair + '_' + rlks + 'rlks.diff_filt'
        diffifg    = workDir + '/' + Pair + '_' + rlks + 'rlks.diff'
        CORIFG     = workDir + '/' + Pair + '_' + rlks + 'rlks.diff_filt.cor'
        rdcdem     = demDir + '/' + masterDate + '_' + rlks + 'rlks.rdc.dem'

        GeoMamp       = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.amp'
        GeoCOR        = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.diff_filt.cor'
        GeoUNW        = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw'
        GeoDIFF       = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt'
        geodiff       = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff'
        GeoATMCOR_UNW = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.atmcor.unw'
        Geodem        = workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.hgt'

        UTMTORDC0  = demDir + '/' + masterDate + '_' + rlks + 'rlks.UTM_TO_RDC'
        UTMDEMpar0 = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.par'
        UTMTORDC   = workDir + '/' + masterDate + '_' + rlks + 'rlks.UTM_TO_RDC'
        UTMDEMpar  = workDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.par'
        ut.copy_file(UTMTORDC0, UTMTORDC)
        ut.copy_file(UTMDEMpar0, UTMDEMpar)

        nWidth       = ut.read_gamma_par(MampPar, 'read', 'range_samples')
        nWidthUTMDEM = ut.read_gamma_par(UTMDEMpar, 'read', 'width')
        nLineUTMDEM  = ut.read_gamma_par(UTMDEMpar, 'read', 'nlines')

        # --- 基础产品地理编码 (hyp3/licsbas 均需) ---
        geo_interp = templateDict['geo_interp']
        geocode(Mamp,    GeoMamp, UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp=geo_interp)
        geocode(CORIFG,  GeoCOR,  UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp=geo_interp)
        geocode(DIFFIFG, GeoDIFF, UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp=geo_interp)
        geocode(UNWIFG,  GeoUNW,  UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp=geo_interp)
        geocode(diffifg, geodiff, UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp=geo_interp)
        geocode(rdcdem,  Geodem,  UTMTORDC, nWidth, nWidthUTMDEM, nLineUTMDEM, geo_interp=geo_interp)

        Mdate  = Pair.split('-')[0]
        SLCpar = slcDir + '/' + Mdate + '/' + Mdate + '.slc.par'
        OFFpar = workDir + '/' + Pair + '_' + rlks + 'rlks.off'
        UTMDEM = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem'

        # --- hyp3 专属: dispmap (LOS/vert 位移场) ---
        if need_hyp3:
            print('  [hyp3] dispmap + geocode 位移场')
            los_disp_rdc  = workDir + '/' + Pair + '_' + rlks + 'rlks.los_disp'
            vert_disp_rdc = workDir + '/' + Pair + '_' + rlks + 'rlks.vert_disp'

            if os.path.isfile(UNWIFG) and os.path.isfile(SLCpar) and os.path.isfile(OFFpar):
                hgt_arg = rdcdem if os.path.isfile(rdcdem) else '-'
                if not os.path.isfile(los_disp_rdc):
                    os.system(f'dispmap {UNWIFG} {hgt_arg} {SLCpar} {OFFpar} {los_disp_rdc} 0')
                if not os.path.isfile(vert_disp_rdc):
                    os.system(f'dispmap {UNWIFG} {hgt_arg} {SLCpar} {OFFpar} {vert_disp_rdc} 1')

            geo_los  = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.los_disp'
            geo_vert = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.vert_disp'
            if os.path.isfile(los_disp_rdc) and not os.path.isfile(geo_los):
                os.system(f'geocode_back {los_disp_rdc} {nWidth} {UTMTORDC} {geo_los} {nWidthUTMDEM} {nLineUTMDEM} 1 0')
            if os.path.isfile(vert_disp_rdc) and not os.path.isfile(geo_vert):
                os.system(f'geocode_back {vert_disp_rdc} {nWidth} {UTMTORDC} {geo_vert} {nWidthUTMDEM} {nLineUTMDEM} 1 0')

        # --- hyp3 专属: 缠绕相位 ---
        if need_hyp3:
            print('  [hyp3] 提取缠绕相位')
            geo_wrapped_pha = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.pha'
            if os.path.isfile(GeoDIFF) and not os.path.isfile(geo_wrapped_pha):
                os.system(f'cpx_to_real {GeoDIFF} {geo_wrapped_pha} {nWidthUTMDEM} 4')

        # --- hyp3/licsbas 共需: look_vector ---
        if need_hyp3 or need_licsbas:
            print('  [hyp3/licsbas] look_vector')
            lv_theta = workDir + '/lv_theta'
            lv_phi   = workDir + '/lv_phi'
            if not os.path.isfile(lv_theta) or not os.path.isfile(lv_phi):
                if os.path.isfile(SLCpar) and os.path.isfile(OFFpar) and os.path.isfile(UTMDEMpar0) and os.path.isfile(UTMDEM):
                    os.system(f'look_vector {SLCpar} {OFFpar} {UTMDEMpar0} {UTMDEM} {lv_theta} {lv_phi}')

        # --- BMP 可视化 ---
        os.system('rasmph_pwr ' + GeoDIFF + ' ' + GeoMamp + ' ' + nWidthUTMDEM + ' - - - - ')
        os.system('raspwr ' + GeoMamp + ' ' + nWidthUTMDEM + ' - - - - - - - - - - ')
        os.system('rasdt_pwr ' + GeoUNW + ' ' + GeoMamp + ' ' + nWidthUTMDEM + ' - - - - -3.14 3.14 1 rmg.cm')

        if os.path.isfile(GeoATMCOR_UNW):
            os.system('rasdt_pwr ' + GeoATMCOR_UNW + ' ' + GeoMamp + ' ' + nWidthUTMDEM + ' - - - - -3.14 3.14 1 rmg.cm')

        if os.path.isfile(UTMTORDC):
            os.remove(UTMTORDC)
        if os.path.isfile(UTMDEMpar):
            os.remove(UTMDEMpar)

        print('  IFG 地理编码完成!')

    elif need_ifg:
        print(f'  WARNING: ifgrams/{Pair} 不存在, 跳过 IFG 地理编码')

    # =========================================================
    #  POT 地理编码 (pot 需要)
    # =========================================================
    potWorkDir = potDir + '/' + Pair
    if need_pot and os.path.isdir(potWorkDir):
        print('\n--- POT 地理编码 ---')
        geocode_pot(projectName, Pair, templateDict)
    elif need_pot:
        print(f'  WARNING: offsets/{Pair} 不存在, 跳过 POT 地理编码')

    print("\nGeocoding is done!") 
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])
