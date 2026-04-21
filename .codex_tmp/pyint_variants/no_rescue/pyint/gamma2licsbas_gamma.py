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

from pyint import _utils as ut

    
INTRODUCTION = '''
-------------------------------------------------------------------  
   将 geocode_gamma.py 已地理编码的 GAMMA 二进制产品转换为
   LiCSBAS 所需的 GeoTIFF 格式, 并组织到 GEOC 目录结构。

   本脚本 **不做任何地理编码或计算**, 仅执行格式转换:
     1. data2geotiff  : GAMMA 二进制 (EQA) → GeoTIFF
     2. GMT grdmath   : 从 look_vector 计算 E/N/U 分量
     3. 文件复制      : 组织到 GEOC/{Pair}/ 目录

   所有地理编码产品由 geocode_gamma.py 提供:
     amp, corr, unw, diff_filt, diff, dem, lv_theta, lv_phi
'''

EXAMPLE = '''
    Usage: 
            gamma2licsbas_gamma.py projectName Mdate-Sdate
            gamma2licsbas_gamma.py PacayaT163TsxHhA 20150102-20150601
-------------------------------------------------------------------  
'''

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Convert geocoded GAMMA binaries to LiCSBAS GeoTIFF format.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('pair',help='Master-Slave, e.g., 20150101-20150106.')
    
    inps = parser.parse_args()
    return inps


def main(argv):
    
    inps = cmdLineParse() 
    projectName = inps.projectName
    Pair = inps.pair
    
    scratchDir = os.getenv('SCRATCHDIR')
    ifgDir = scratchDir + '/' + projectName + "/ifgrams"
    demDir = scratchDir + '/' + projectName + "/DEM"
    workDir = ifgDir + '/' + Pair

    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    
    #################################################################
    # DEM 参数 (直接引用, 不复制不删除)
    #################################################################
    DEM_par = demDir + '/' + masterDate  + '_' + rlks + 'rlks.utm.dem.par'
    DEM = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem'
    DEM_tif = demDir + '/' + masterDate + '_' + rlks + 'rlks.utm.dem.tif'

    # look_vector 文件 (由 geocode_gamma.py 生成)
    lv_theta = workDir + '/' + 'lv_theta'
    lv_phi = workDir + '/' + 'lv_phi'
    lv_theta_tif = workDir + '/' + 'lv_theta.geo.tif'
    lv_phi_tif = workDir + '/' + 'lv_phi.geo.tif'
    lv_theta_grd = workDir + '/' + 'lv_theta.grd'
    lv_theta_grd_final = workDir + '/' + 'inc_deg.nc'
    lv_phi_grd = workDir + '/' + 'lv_phi.grd'  
    lv_phi_grd_final = workDir + '/' + 'azi_deg.nc'
    UE_grd = workDir + '/' + 'ue.grd'
    UN_grd = workDir + '/' + 'un.grd'
    UU_grd = workDir + '/' + 'uu.grd'
    UE_tif = workDir + '/' + 'ue.geo.E.tif'
    UN_tif = workDir + '/' + 'un.geo.N.tif'
    UU_tif = workDir + '/' + 'uu.geo.U.tif'

    if not os.path.exists(lv_theta) or not os.path.exists(lv_phi):
        print(f"WARNING: look_vector 文件不存在, 请先运行 geocode_gamma.py {projectName} {Pair}")
        print(f"  缺少: lv_theta={os.path.exists(lv_theta)}, lv_phi={os.path.exists(lv_phi)}")

    GEOC = scratchDir + '/' + projectName + '/GEOC'
    if not os.path.isdir(GEOC):
        os.mkdir(GEOC)
    Store_dir = GEOC + '/' + Pair
    if not os.path.isdir(Store_dir):
        os.mkdir(Store_dir)
    
    # 判断DEM_tif是否存在
    if not os.path.exists(DEM_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + DEM + ' 2 ' + DEM_tif
        os.system(call_str)
    else:
        print(f"File {DEM_tif} already exists, skipping generation.")
    
    # 判断lv_theta_tif是否存在
    if not os.path.exists(lv_theta_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + lv_theta + ' 2 ' + lv_theta_tif
        os.system(call_str)
    else:
        print(f"File {lv_theta_tif} already exists, skipping generation.")
    
    # 判断lv_phi_tif是否存在
    if not os.path.exists(lv_phi_tif):
        call_str = 'data2geotiff ' + DEM_par + ' '  + lv_phi + ' 2 ' + lv_phi_tif
        os.system(call_str)
    else:
        print(f"File {lv_phi_tif} already exists, skipping generation.")
    
    # 判断lv_theta_grd是否存在
    if not os.path.exists(lv_theta_grd):
        call_str = 'gdal_translate -of GSBG ' + lv_theta_tif + ' ' + lv_theta_grd 
        os.system(call_str)
    else:
        print(f"File {lv_theta_grd} already exists, skipping conversion.")
    
    # 判断lv_phi_grd是否存在
    if not os.path.exists(lv_phi_grd):
        call_str = 'gdal_translate -of GSBG ' + lv_phi_tif + ' ' + lv_phi_grd 
        os.system(call_str)
    else:
        print(f"File {lv_phi_grd} already exists, skipping conversion.")
    
    # 判断lv_theta_grd_final是否存在
    if not os.path.exists(lv_theta_grd_final):
        call_str = 'gmt grdmath 90 ' + lv_theta_grd + ' 3.1415926 DIV 180 MUL SUB = ' +  lv_theta_grd_final
        os.system(call_str)
    else:
        print(f"File {lv_theta_grd_final} already exists, skipping calculation.")
    
    # 判断lv_phi_grd_final是否存在
    if not os.path.exists(lv_phi_grd_final):
        call_str = 'gmt grdmath -180 ' + lv_phi_grd + ' 3.1415926 DIV 180 MUL SUB = ' +  lv_phi_grd_final
        os.system(call_str)
    else:
        print(f"File {lv_phi_grd_final} already exists, skipping calculation.")
    
    # 判断UE_grd是否存在
    if not os.path.exists(UE_grd):
        call_str = 'gmt grdmath ' + lv_phi_grd_final + ' COSD ' + lv_theta_grd_final + ' SIND MUL NEG = ' + UE_grd
        os.system(call_str)
    else:
        print(f"File {UE_grd} already exists, skipping calculation.")
    
    # 判断UN_grd是否存在
    if not os.path.exists(UN_grd):
        call_str = 'gmt grdmath ' + lv_phi_grd_final + ' SIND ' + lv_theta_grd_final + ' SIND MUL = ' + UN_grd
        os.system(call_str)
    else:
        print(f"File {UN_grd} already exists, skipping calculation.")
    
    # 判断UU_grd是否存在
    if not os.path.exists(UU_grd):
        call_str = 'gmt grdmath ' + lv_theta_grd_final + ' COSD = ' + UU_grd
        os.system(call_str)
    else:
        print(f"File {UU_grd} already exists, skipping calculation.")
    
    # 判断UE_tif是否存在
    if not os.path.exists(UE_tif):
        call_str = 'gdal_translate -of GTiff ' + UE_grd  + ' ' + UE_tif
        os.system(call_str)
    else:
        print(f"File {UE_tif} already exists, skipping conversion.")
    
    # 判断UN_tif是否存在
    if not os.path.exists(UN_tif):
        call_str = 'gdal_translate -of GTiff ' + UN_grd  + ' ' + UN_tif
        os.system(call_str)
    else:
        print(f"File {UN_tif} already exists, skipping conversion.")
    
    # 判断UU_tif是否存在
    if not os.path.exists(UU_tif):
        call_str = 'gdal_translate -of GTiff ' + UU_grd  + ' ' + UU_tif
        os.system(call_str)
    else:
        print(f"File {UU_tif} already exists, skipping conversion.")

##### get unw.tif
    GeoMamp    =  workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.amp'
    GeoMamp_tif    =  workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.amp.tif'
    GeoCOR    =  workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.diff_filt.cor'
    GeoCOR_tif =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.cor.tif'
    GeoUNW_tif =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw.tif'
    GeoUNW     =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw'
    GeoDIFF    =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt'
    GeoDIFF_tif    =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.tif'
    Geodiff   =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff'
    Geodiff_tif   =  workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff.tif'
    GeoATMCOR_UNW = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw.gacos'    
    GeoATMCOR_UNW_tif = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw.gacos.tif'
    Geodem =  workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.hgt'
    Geodem_tif =  workDir + '/geo_' + masterDate + '_' + rlks + 'rlks.hgt.tif'
    
    # 判断GeoUNW_tif是否存在
    if not os.path.exists(GeoUNW_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + GeoUNW + ' '  + ' 2 ' + GeoUNW_tif
        os.system(call_str)
    else:
        print(f"File {GeoUNW_tif} already exists, skipping conversion.")
    
    # 判断Geodem_tif是否存在
    if not os.path.exists(Geodem_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + Geodem + ' '  + ' 2 ' + Geodem_tif
        os.system(call_str)
    else:
        print(f"File {Geodem_tif} already exists, skipping conversion.")
    
    # 判断GeoCOR_tif是否存在
    if not os.path.exists(GeoCOR_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + GeoCOR + ' '  + ' 2 ' + GeoCOR_tif
        os.system(call_str)
    else:
        print(f"File {GeoCOR_tif} already exists, skipping conversion.")
    
    # 判断GeoMamp_tif是否存在
    if not os.path.exists(GeoMamp_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + GeoMamp + ' '  + ' 2 ' + GeoMamp_tif
        os.system(call_str)
    else:
        print(f"File {GeoMamp_tif} already exists, skipping conversion.")
    
    # 判断GeoDIFF_tif是否存在
    if not os.path.exists(GeoDIFF_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + GeoDIFF + ' '  + ' 2 ' + GeoDIFF_tif
        os.system(call_str)
    else:
        print(f"File {GeoDIFF_tif} already exists, skipping conversion.")
    
    # 判断Geodiff_tif是否存在
    if not os.path.exists(Geodiff_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' +Geodiff + ' '  + ' 2 ' + Geodiff_tif
        os.system(call_str)
    else:
        print(f"File {Geodiff_tif} already exists, skipping conversion.")
    
    # 判断GeoATMCOR_UNW_tif是否存在（GACOS校正后的解缠结果）
    if os.path.exists(GeoATMCOR_UNW) and not os.path.exists(GeoATMCOR_UNW_tif):
        call_str = 'data2geotiff ' + DEM_par + ' ' + GeoATMCOR_UNW + ' '  + ' 2 ' + GeoATMCOR_UNW_tif
        os.system(call_str)
    elif os.path.exists(GeoATMCOR_UNW_tif):
        print(f"File {GeoATMCOR_UNW_tif} already exists, skipping conversion.")
    
    GeoCOR_tif0 = Store_dir + '/' + Pair + '.geo.cc.tif'
    GeoUNW_tif0 = Store_dir + '/' + Pair + '.geo.unw.tif'
    UU_tif0 = GEOC + '/' + 'uu.geo.U.tif'
    UE_tif0 = GEOC + '/' + 'ue.geo.E.tif'
    UN_tif0 = GEOC + '/' + 'un.geo.N.tif'
    dem_tif0 = GEOC + '/' + masterDate + '.geo.hgt.tif'
    amp_tif0 = GEOC + '/' + masterDate + '.geo.mli.tif'
    GeoDIFF_tif0 = Store_dir + '/' + Pair + '.geo.diff_pha.tif'
    Geodiff_tif0 = Store_dir + '/' + Pair + '.geo.diff_unfiltered_pha.tif'
    GeoATMCOR_UNW_tif0 = Store_dir + '/' + Pair + '.geo.unw.gacos.tif'
    # 判断并复制文件，如果目标文件已存在则不复制 
    if not os.path.exists(GeoUNW_tif0):
        ut.copy_file(GeoUNW_tif, GeoUNW_tif0)
    else:
        print(f"File {GeoUNW_tif0} already exists, skipping copy.")
    
    if not os.path.exists(GeoCOR_tif0):
        ut.copy_file(GeoCOR_tif, GeoCOR_tif0)
    else:
        print(f"File {GeoCOR_tif0} already exists, skipping copy.")
    
    if not os.path.exists(UU_tif0):
        ut.copy_file(UU_tif, UU_tif0)
    else:
        print(f"File {UU_tif0} already exists, skipping copy.")
    
    if not os.path.exists(UE_tif0):
        ut.copy_file(UE_tif, UE_tif0)
    else:
        print(f"File {UE_tif0} already exists, skipping copy.")
    
    if not os.path.exists(UN_tif0):
        ut.copy_file(UN_tif, UN_tif0)
    else:
        print(f"File {UN_tif0} already exists, skipping copy.")
    if not os.path.exists(GeoDIFF_tif0):
        ut.copy_file(GeoDIFF_tif, GeoDIFF_tif0)
    else:
        print(f"File {GeoDIFF_tif0} already exists, skipping copy.")  
    if not os.path.exists(Geodiff_tif0):
        ut.copy_file(Geodiff_tif, Geodiff_tif0)
    else:
        print(f"File {Geodiff_tif0} already exists, skipping copy.")
    
    if os.path.exists(GeoATMCOR_UNW_tif) and not os.path.exists(GeoATMCOR_UNW_tif0):
        ut.copy_file(GeoATMCOR_UNW_tif, GeoATMCOR_UNW_tif0)
    elif os.path.exists(GeoATMCOR_UNW_tif0):
        print(f"File {GeoATMCOR_UNW_tif0} already exists, skipping copy.")
    
    if not os.path.exists(dem_tif0):
        ut.copy_file(Geodem_tif, dem_tif0)
    else:
        print(f"File {dem_tif0} already exists, skipping copy.")
    
    if not os.path.exists(amp_tif0):
        ut.copy_file(GeoMamp_tif, amp_tif0)
    else:
        print(f"File {amp_tif0} already exists, skipping copy.")
    
    print("Convert to LiCSBAS format is done!") 
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])
