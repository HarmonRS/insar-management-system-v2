#! /usr/bin/env python
###########################################################
#  Project: PyINT                                         #     
#  Purpose: Interferograms process using python/GAMMA     #                                       
#  Author:  Yunmeng Cao                                   #
#  Created: Feb. 2017                                     #                                                         
#  Contact : ymcmrs@gmail.com                             #  
#  Copy Right (c): 2017-2019, Yunmeng Cao                 # 
###########################################################

import numpy as np
import os
import sys  
import subprocess
import time
import argparse

from pyint import _utils as ut


def _run_or_raise(call_str, stage):
    rc = os.system(call_str)
    if rc != 0:
        raise RuntimeError('%s failed with rc=%s: %s' % (stage, rc, call_str))
    return rc


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Interferograms processing using PyINT.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName', help='name of the project.')
    parser.add_argument('-g', '--generate', action='store_true', dest='generate_structure', \
                        help='Generate project directory structure if not exists.')

    inps = parser.parse_args()
    return inps


INTRODUCTION = '''
-----------------------------------------------------------------------------------

   Single or time-series of interferometry processing for satellite based 
   Synthetic  Aperture  Radar (SAR) images start from downloading data to 
   generate unwrapped-differential interferograms.
   
   Details please check: https://github.com/ymcmrs/PyINT
   
   General work-flow: 
   
   1) download data  :  download SLCs using SSARA/Scihub/ASF
                        SSARA: https://github.com/bakerunavco/SSARA
                        Scihub: Copernicus Open Access Hub
                        ASF: Alaska Satellite Facility (for Sentinel-1)
                        [You should provide Sensor, Track, Frame, or Time information in template]                     
   2) generate SLC   :  raw 2 slc (multi-frame processing is also supported)
                        [include orbit correction for S1,ASAR,ERS and burst-extraction for S1]                      
   3) generate DEM   :  reference image related geo-dem, rdc-dem, lookup table will be generated. 
                        [SRTM-1 will be downloaded and processed automatically if not provided]                       
   4) coregister SLC :  coregister SLCs to the reference SLC iamge.
                        [with assistant of DEM]  
   5) select pairs   :  select interferometric pairs for time-series processing.
                        [networks of sbas, sequential, delaunay, and stars are supported]                     
   6) interferometry :  generate unwrapped differential interferograms.
                        [include differential, unwrapping, and geocoding]
   7) offset tracking:  pixel offset tracking (POT) for displacement measurement.
                        [two-round estimation with GAMMA offset_pwr_tracking]
   8) GACOS correction: apply GACOS tropospheric atmospheric correction.
                        [requires GACOS ZTD data for master and slave dates]
                         
   Note: 
   
   i) Single interferogram processing please use slc2ifg.py or raw2ifg.py
  ii) Multi-processor parallel processing is supported, but keep in mind GAMMA calls multi-threads already.
 iii) ASF download supports both bounding box and Shapefile for spatial filtering.
  iv) GACOS correction requires geocoded interferograms (set geocode_all=1 first).
                      
'''

EXAMPLE = """Usage:
        
        pyintApp.py -h
        pyintApp.py projectName       #[projectName.template should be available in TEMPLATEDIR]
        pyintApp.py -g projectName    #Generate project directory structure if not exists

------------------------------------------------------------------------------------ 
"""

def main(argv):
    
    start_time = time.time()
    inps = cmdLineParse()
    projectName = inps.projectName
    templateDir = os.getenv('TEMPLATEDIR')
    scratchDir = os.getenv('SCRATCHDIR')
    workDir = scratchDir + '/' + projectName
    
    # Generate project directory structure if -g flag is set and directory doesn't exist
    if inps.generate_structure:
        if not os.path.isdir(workDir):
            print('Generating project directory structure for: %s' % projectName)
            os.makedirs(workDir, exist_ok=True)
            os.makedirs(workDir + '/DOWNLOAD', exist_ok=True)
            os.makedirs(workDir + '/SLC', exist_ok=True)
            os.makedirs(workDir + '/RSLC', exist_ok=True)
            os.makedirs(workDir + '/DEM', exist_ok=True)
            os.makedirs(workDir + '/ifgrams', exist_ok=True)
            print('Project directory structure created successfully.')
            print('Directory structure:')
            print('  %s/' % projectName)
            print('  ├── DOWNLOAD/')
            print('  ├── SLC/')
            print('  ├── RSLC/')
            print('  ├── DEM/')
            print('  └── ifgrams/')
            return
        else:
            print('Project directory already exists: %s' % workDir)
            print('Skipping directory structure generation.')
            return
    
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict = ut.update_template(templateFile)
    masterDate = templateDict['masterDate']
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    satelite = templateDict['satelite']
    HGTSIM = scratchDir + '/' + projectName + '/DEM/' + ut.yyyymmdd(masterDate) + '_' + rlks + 'rlks.rdc.dem' 
    DataDir = workDir + '/' + 'DOWNLOAD'
    DEMDir  =  workDir + '/' + 'DEM'
    os.chdir(scratchDir)
    if not os.path.isdir(workDir): 
       os.mkdir(workDir)
       os.chdir(workDir)
       os.mkdir(DataDir)
       os.mkdir(DEMDir)
       os.chdir(DataDir)
    else:
       os.chdir(DataDir)     
    ### download data
    if templateDict['download_data'] == '1':
        if templateDict['Data_Hub'] == 'SSARA':
           print('Start to download SAR data using SSARA...')
           call_str = 'ssara_federated_query.py -p ' +  templateDict['sensor'] + ' -r ' + templateDict['track'] + ' -f ' + templateDict['frame'] + ' -s ' + templateDict['start_time'] + ' -e ' + templateDict['end_time'] + ' --print --download --parallel ' + templateDict['down_parallel']
           print(call_str)
           _run_or_raise(call_str, 'download_data_ssara')
        elif templateDict['Data_Hub'] == 'Scihub':
           print('Start to downlad SAR data from Scihub')
           call_str = 'scihub_search_s1_data.py ' + projectName + ' -s ' + templateDict['start_time'] + ' -e ' + templateDict['end_time'] + ' -p ' + templateDict['producttype']  +' -r ' + templateDict['region_box'] + ' -n ' + templateDict['Orbit_number'] + ' -d ' + templateDict['Direction'] + ' -sm ' + templateDict['Sence_model'] + ' -o ' + templateDict['output_file']
           print(call_str)
           _run_or_raise(call_str, 'download_data_scihub')
        elif templateDict['Data_Hub'] == 'ASF':
           print('Start to download Sentinel-1 SLC data from ASF...')
           # 获取必要的参数
           path_SLC = DataDir
           path_RSLC = workDir + '/RSLC'
           date_start = templateDict.get('start_time', '20170101')
           date_end = templateDict.get('end_time', '20240101')
           
           # 构建基本命令
           call_str = 'API_download_S1_SLC.py -s ' + path_SLC + ' -r ' + path_RSLC + ' -i ' + date_start + ' -j ' + date_end
           
           # 添加用户名和密码（如果提供）
           if 'asf_username' in templateDict and templateDict['asf_username'].strip():
               call_str += ' -u ' + templateDict['asf_username']
           if 'asf_password' in templateDict and templateDict['asf_password'].strip():
               call_str += ' -p ' + templateDict['asf_password']
           
           # 添加边界框或Shapefile
           if 'shapefile' in templateDict and templateDict['shapefile'].strip():
               call_str += ' --shp ' + templateDict['shapefile']
           elif 'bbox' in templateDict and templateDict['bbox'].strip():
               call_str += ' -b ' + templateDict['bbox']
           elif 'asf_bbox' in templateDict and templateDict['asf_bbox'].strip():
               call_str += ' -b ' + templateDict['asf_bbox']
           
           # 添加轨道号（可选）
           if 'relative_orbit' in templateDict and templateDict['relative_orbit'].strip():
               call_str += ' -o ' + str(templateDict['relative_orbit'])
           elif 'track' in templateDict and templateDict['track'].strip():
               call_str += ' -o ' + str(templateDict['track'])
           
           # 添加帧号（可选）
           if 'frame' in templateDict and templateDict['frame'].strip():
               call_str += ' --frame ' + str(templateDict['frame'])
           
           # 添加飞行方向（可选，默认为升轨）
           if 'flight_direction' in templateDict:
               call_str += ' -f ' + templateDict['flight_direction']
           else:
               call_str += ' -f a'  # 默认升轨
           
           # 添加波束模式（默认IW）
           if 'acquisition_mode' in templateDict:
               call_str += ' -q ' + templateDict['acquisition_mode']
           else:
               call_str += ' -q IW'  # 默认IW模式
           
           # 输出格式和下载选项
           call_str += ' -m csv -w Y'
           
           # 添加并行下载参数
           if 'down_parallel' in templateDict and templateDict['down_parallel'].strip():
               call_str += ' --parallel ' + str(templateDict['down_parallel'])
           
           print(call_str)
           _run_or_raise(call_str, 'download_data_asf')
    ### raw 2 slc 
    if templateDict['raw2slc_all'] == '1':   # only for S1 data now
        print('Start to convert downloaded-raw data into SLC ...')
        print('Number of processor: %s' % str(templateDict['raw2slc_all_parallel']))
        if satelite=='S1A':
            call_str = 'down2slc_sen_all.py ' + projectName + ' --parallel ' + templateDict['raw2slc_all_parallel']
            _run_or_raise(call_str, 'raw2slc_s1')
        elif satelite=='ALOS':
            call_str = 'down2slc_alos_all.py ' + projectName + ' --parallel ' + templateDict['raw2slc_all_parallel']
            _run_or_raise(call_str, 'raw2slc_alos')
        elif satelite=='LT':
            call_str = 'down2slc_LT1_all.py ' + projectName + ' --parallel ' + templateDict['raw2slc_all_parallel']
            _run_or_raise(call_str, 'raw2slc_lt1')        
    ### extract bursts  
    if templateDict['extract_burst_all'] == '1':
        print('Start to extract common bursts ...')
        print('Number of processor: %s' % str(templateDict['extract_all_parallel']))
        call_str = 'extract_s1_bursts_all.py ' + projectName + ' --parallel ' + templateDict['extract_all_parallel']
        _run_or_raise(call_str, 'extract_burst_all')
        
    ### generate rdc_dem
    if not os.path.isfile(HGTSIM):
        print('Start to generate geometry file ...')
        call_str = 'makedem_pyint.py ' + projectName
        _run_or_raise(call_str, 'makedem_pyint')
        call_str = 'generate_rdc_dem.py ' + projectName 
        _run_or_raise(call_str, 'generate_rdc_dem')
    
    ### coreg SLC  
    if templateDict['coreg_all'] == '1':
        print('Start to coregister SLCs ...')
        print('Number of processor: %s' % str(templateDict['coreg_all_parallel']))
        call_str = 'coreg_gamma_all.py ' + projectName + ' --parallel ' + templateDict['coreg_all_parallel']
        _run_or_raise(call_str, 'coreg_all')
    
    ### select interferometric pairs 
    if templateDict['select_pairs'] == '1':
        print('Start to select interferometric pairs ...')
        print('Network selection method: %s' % templateDict['network_method'])
        #print('Meximum temporal baseline threshold: %s' % templateDict['max_tb'])
        #print('Meximum spatial baseline threshold: %s' % templateDict['max_sb'])
        call_str = 'select_pairs.py ' + projectName
        _run_or_raise(call_str, 'select_pairs')
    
    ### diff ifg
    if templateDict['diff_all'] == '1':
        print('Start to generate differential interferograms ...')
        print('Number of processor: %s' % str(templateDict['diff_all_parallel']))
        call_str = 'diff_gamma_all.py ' + projectName + ' --parallel ' + templateDict['diff_all_parallel']
        _run_or_raise(call_str, 'diff_all')
    
    ### Pixel Offset Tracking (POT)
    if templateDict['pot_all'] == '1':
        print('Start to run Pixel Offset Tracking (POT) ...')
        print('Number of processor: %s' % str(templateDict['pot_all_parallel']))
        call_str = 'POT_gamma_all.py ' + projectName + ' --parallel ' + templateDict['pot_all_parallel']
        _run_or_raise(call_str, 'pot_all')
    
    ### unw ifg
    if templateDict['unwrap_all'] == '1':
        print('Start to unwrap interferometric phases ...')
        print('Number of processor: %s' % str(templateDict['unwrap_all_parallel']))
        call_str = 'unwrap_gamma_all.py ' + projectName + ' --parallel ' + templateDict['unwrap_all_parallel']
        _run_or_raise(call_str, 'unwrap_all')

    ### atmcor ifg
    if templateDict['atmcor_all'] == '1':
        print('Start to correct atmospheric phase ...')
        print('Number of processor: %s' % str(templateDict['atmcor_all_parallel']))
        call_str = 'atm_correction_gamma_all.py ' + projectName + ' --parallel ' + templateDict['atmcor_all_parallel']
        _run_or_raise(call_str, 'atmcor_all')
    
    ### geocode ifg
    if templateDict['geocode_all'] == '1':
        print('Start to geocode Ifgs ...')
        print('Number of processor: %s' % str(templateDict['geocode_all_parallel']))
        call_str = 'geocode_gamma_all.py ' + projectName + ' --parallel ' + templateDict['geocode_all_parallel']
        _run_or_raise(call_str, 'geocode_all')
        
    ### Convert GAMMA outputs to LiCSBAS format
    if templateDict.get('gamma2licsbas_all', '0') == '1':
        print('Start to convert GAMMA outputs to LiCSBAS format ...')
        print('Number of processor: %s' % str(templateDict.get('gamma2licsbas_all_parallel', '1')))
        call_str = 'gamma2licsbas_gamma_all.py ' + projectName + ' --parallel ' + templateDict.get('gamma2licsbas_all_parallel', '1')
        _run_or_raise(call_str, 'gamma2licsbas_all')
    
    ### GACOS atmospheric correction (after geocoding)
    if templateDict['gacos_correction'] == '1':
        print('Start to apply GACOS atmospheric correction ...')
        gacos_dir = templateDict.get('gacos_dir', '').strip()
        gacos_email = templateDict.get('gacos_email', '').strip()
        
        call_str = 'gacos_gamma_all.py ' + projectName + ' --skip-existing'
        if gacos_dir:
            call_str += ' --ztd-dir ' + gacos_dir
        if gacos_email:
            call_str += ' --email ' + gacos_email
        print(call_str)
        _run_or_raise(call_str, 'gacos_correction')
        
    ### Convert GAMMA outputs to HyP3 UTM format
    if templateDict.get('hyp3format_all', '0') == '1':
        print('Start to convert GAMMA outputs to HyP3 UTM format ...')
        print('Number of processor: %s' % str(templateDict.get('hyp3format_all_parallel', '1')))
        call_str = 'hyp3format_gamma_all.py ' + projectName + ' --parallel ' + templateDict.get('hyp3format_all_parallel', '1')
        _run_or_raise(call_str, 'hyp3format_all')
    
    ### load data
    if templateDict['load_data'] == '1':
        print('Start to load data for mintPy time-series analysis ...')                                                   
        call_str = 'load_mintpy.py ' + projectName
        _run_or_raise(call_str, 'load_data')
    
    print("PyINT processing for project %s is done." % projectName)
    ut.print_process_time(start_time, time.time()) 
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
    
    
    
    
    
