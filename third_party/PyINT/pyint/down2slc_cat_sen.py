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
import subprocess
import getopt
import time
import glob
import argparse
import csv
from pathlib import Path

from pyint import _utils as ut


def get_s1_date(raw_file):
    file0 = os.path.basename(raw_file)
    date = file0[17:25]
    return date

def get_s1_time(raw_file):
    file0 = os.path.basename(raw_file)
    times= file0[26:32]
    return times

def get_satellite(raw_file):
    name = os.path.basename(str(raw_file))
    if name.startswith('S1') and len(name) >= 3:
        s0 = name[2]
    else:
        s0 = 'A'
    
    return s0
        

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Generate SLC from Sentinel-1 raw data with orbit correction using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName', help='project name. e.g., ChangningT55S1A')
    parser.add_argument('date',help='date to be processed. e.g., 20180101')
       
    inps = parser.parse_args()

    return inps


INTRODUCTION = '''
-------------------------------------------------------------------  

   Cat two SLC from Sentinel-1 raw data using S1_import_SLC_from_zipfiles and SLC_cat_S1_TOPS with orbit correction.
   [Precise orbit data will be downloaded automatically]
'''

EXAMPLE = """Usage:
  
  down2slc_sen.py projectName date
  
  down2slc_sen.py ChangningT55S1A 20180517
  
------------------------------------------------------------------- 
"""

def main(argv):
    
    inps = cmdLineParse() 
    projectName = inps.projectName
    date = inps.date
    scratchDir = os.getenv('SCRATCHDIR')
    projectDir = scratchDir + '/' + projectName 
    templateDir = os.getenv('TEMPLATEDIR')      
    templateFile = templateDir + "/" + projectName + ".template" 
    templateDict=ut.update_template(templateFile) 
    rlks = templateDict['range_looks']   
    slc_dir =  projectDir + '/SLC'
    down_dir = projectDir + '/DOWNLOAD'
    #opod_dir = projectDir + '/OPOD'
    opod_dir = os.getenv('OPOD_DIR')
    

    #if not os.path.isdir(opod_dir):
    #    os.mkdir(opod_dir)
    
    if opod_dir and not os.path.isdir(opod_dir):
        os.makedirs(opod_dir, exist_ok=True)
    if not os.path.isdir(slc_dir):
        os.mkdir(slc_dir)
        
    work_dir = slc_dir + '/' + date
    if not os.path.isdir(work_dir):
        os.mkdir(work_dir)
    
    os.chdir(work_dir)
    
    t_date = 't_' + date
    
    start_swath = templateDict['start_swath']
    end_swath = templateDict['end_swath']
    
#    if (start_swath == '1') and (end_swath == '1'):
#        k_swath = '1'
#    elif (start_swath == '2') and (end_swath == '2'):
#        k_swath = '2'
#    elif (start_swath == '3') and (end_swath == '3'):
#        k_swath = '3'
#    elif (start_swath == '1') and (end_swath == '2'):
#        k_swath = '4'
#    elif (start_swath == '2') and (end_swath == '3'):
#        k_swath = '5' 
#    elif (start_swath == '2') and (end_swath == '3'):
#        k_swath = '-' 
    k_swath = ut.get_sardata_swath(start_swath,end_swath)    
    
    raw_files = sorted(glob.glob(down_dir + '/S1*' + date + '*.zip') + glob.glob(down_dir + '/S1*' + date + '*.SAFE'))
    if not raw_files:
        raise FileNotFoundError('No Sentinel-1 scenes found for date ' + date)
    with open(t_date, 'w', encoding='utf-8') as f:
        for raw_file in raw_files:
            f.write(str(raw_file) + '\n')
    satellite = get_satellite(str(raw_files[0]))
    #orbit_file = ut.download_s1_orbit(date,opod_dir,satellite=satellite)
    raw_file_list = glob.glob(down_dir + '/S1*' + date + '*.zip') + glob.glob(down_dir + '/S1*' + date + '*.SAFE')
    file_num=len(raw_files)
    if raw_files and os.path.isdir(str(raw_files[0])):
        call_str = 'S1_BURST_tab_from_zipfile.py 3 --dir_ref_list  ' + t_date + '  --dir_list  ' + t_date
    else:
        call_str = 'S1_BURST_tab_from_zipfile.py 3 --zip_ref_list  ' + t_date + '  --zip_list  ' + t_date
    rc = os.system(call_str)
    if rc != 0:
        raise RuntimeError('S1_BURST_tab_from_zipfile.py failed with rc=' + str(rc))
    
    for kk in range(file_num):
        zipfile_ref=str(raw_files[kk])
        outfile_name=zipfile_ref.split('/')[-1].split('.')[0]
        burst_number_table_ref=outfile_name + '.BURST_tab' 
        #call_str = 'S1_BURST_tab_from_zipfile ' + t_date + ' ' + str(raw_files[kk]) + ' - 1'
        #os.system(call_str)
        #call_str = 'S1_import_SLC_from_zipfiles ' + t_date + ' ' + burst_number_table_ref + ' vv 0 ' + k_swath + ' ' + opod_dir + ' 1 1 '
        call_str = 'read_S1_TOPS_SLC.py  ' + zipfile_ref + ' --burst_sel  ' + burst_number_table_ref + ' --pol vv   --root_name ' + date + '  --sw_start  ' +  start_swath + ' --swn ' + end_swath + ' --OPOD_dir ' + opod_dir
        rc = os.system(call_str)
        if rc != 0:
            raise RuntimeError('read_S1_TOPS_SLC.py failed with rc=' + str(rc))
        os.chdir(work_dir)
        rename_rules = [
            ('vv.iw1.slc', 'iw1_' + str(kk) + '.slc'),
            ('vv.iw2.slc', 'iw2_' + str(kk) + '.slc'),
            ('vv.iw3.slc', 'iw3_' + str(kk) + '.slc'),
            ('vv.SLC_tab', 'SLC_tab'),
            ('vv.slc', 'slc'),
            ('tops_par', 'TOPS_par'),
        ]
        for src_token, dst_token in rename_rules:
            for candidate in list(Path(work_dir).iterdir()):
                if src_token not in candidate.name:
                    continue
                target = candidate.with_name(candidate.name.replace(src_token, dst_token))
                if target != candidate and not target.exists():
                    candidate.rename(target)
        SLC_Tab = work_dir + '/' + date +  '_SLC_Tab' +str(kk)
        SLC_list = sorted(glob.glob(work_dir + '/*iw*_' + str(kk) +'.slc')) 
        SLC_par_list = sorted(glob.glob(work_dir + '/*iw*_' + str(kk) +'.slc.par')) 
        TOP_par_list = sorted(glob.glob(work_dir + '/*iw*_' + str(kk) +'.slc.TOPS_par')) 
        if not TOP_par_list:
            TOP_par_list = sorted(glob.glob(work_dir + '/*iw*_' + str(kk) +'.slc.tops_par'))
        if (not SLC_list or not SLC_par_list or not TOP_par_list
                or len(SLC_list) != len(SLC_par_list)
                or len(SLC_list) != len(TOP_par_list)):
            raise RuntimeError('Sentinel-1 concatenated SLC generation did not produce expected IW products for ' + date)
         
       
        cat_str = 'touch slc_list slc_par_list top_par_list'
        os.system(cat_str)
        list_num=len(SLC_list)
        for tt in range(list_num):  
          call_str = 'echo ' + SLC_list[tt] + ' >> slc_list'
          os.system(call_str)
          call_str = 'echo ' + SLC_par_list[tt] + ' >> slc_par_list'
          os.system(call_str)
          call_str = 'echo ' + TOP_par_list[tt] + ' >> top_par_list'
          os.system(call_str)
        call_str = ' paste  slc_list slc_par_list top_par_list  > ' + SLC_Tab
        rc = os.system(call_str)
        if rc != 0:
            raise RuntimeError('paste SLC tab failed with rc=' + str(rc))
        call_str = 'rm slc_list slc_par_list top_par_list'
        os.system(call_str)
    SLC_list = sorted(glob.glob(work_dir + '/*iw*.slc')) 
    SLC_Tab = work_dir + '/' + date+'_SLC_Tab' 
    subswath=len(SLC_list)/2
    for kk in range(int(subswath)):
         swath=kk+1
         call_str = 'echo ' + date + '.IW' + str(swath)  + '.slc ' + date + '.IW' + str(swath)  + '.slc.par ' + date + '.IW' + str(swath)  + '.slc.TOPS_par ' + '>> ' + SLC_Tab
         os.system(call_str) 
    call_str = 'SLC_cat_S1_TOPS ' +SLC_Tab+'0 ' + SLC_Tab +'1 ' + SLC_Tab
    os.system(call_str)
   
    #call_str = "rename vv.slc.iw1 IW1.slc * "
    #os.system(call_str)
    #call_str = "rename 's/vv.slc.iw2/IW2.slc/g' *"
    #call_str = "rename vv.slc.iw2 IW2.slc * "
    #os.system(call_str)
    #call_str = "rename 's/vv.slc.iw3/IW3.slc/g' *"
    #call_str = "rename vv.slc.iw3 IW3.slc * "
    #os.system(call_str)
    #call_str = 'rm *.iw* *SLC_Tab*'
    #os.system(call_str)
 
    SLC_Tab = work_dir + '/' + date+'_SLC_Tab'
    SLC_list = sorted(glob.glob(work_dir + '/*IW*.slc')) 
    SLC_par_list = sorted(glob.glob(work_dir + '/*IW*.slc.par')) 
    TOP_par_list = sorted(glob.glob(work_dir + '/*IW*.slc.TOPS_par')) 
    if not TOP_par_list:
        TOP_par_list = sorted(glob.glob(work_dir + '/*IW*.slc.tops_par'))
   
    #call_str = 'rm *iw* *vv.SLC_tab'
    #os.system(call_str)
    if os.path.isfile(SLC_Tab):
        os.remove(SLC_Tab) 

    if (not SLC_list or not SLC_par_list or not TOP_par_list
            or len(SLC_list) != len(SLC_par_list)
            or len(SLC_list) != len(TOP_par_list)):
        raise RuntimeError('Sentinel-1 concatenated final SLC products are incomplete for ' + date)

    for kk in range(len(SLC_list)):
        call_str = 'echo ' + SLC_list[kk] + ' ' + SLC_par_list[kk] + ' ' + TOP_par_list[kk] + ' >> ' + SLC_Tab
        rc = os.system(call_str)
        if rc != 0:
            raise RuntimeError('failed to append concatenated SLC tab with rc=' + str(rc))
        
        BURST = SLC_par_list[kk].replace('slc.par','burst.par')
        call_str = 'SLC_burst_corners ' + SLC_par_list[kk] + ' ' +  TOP_par_list[kk] + ' > ' +BURST
        rc = os.system(call_str)
        if rc != 0:
            raise RuntimeError('SLC_burst_corners failed with rc=' + str(rc))
        call_str =  "echo 'SLC has  already down' >down2slc.dat"
        os.system(call_str)

    TSLC = work_dir + '/' + date + '.slc'
    TSLCPar = work_dir + '/' + date + '.slc.par'
    if not os.path.isfile(TSLC) or not os.path.isfile(TSLCPar):
        call_str = 'SLC_mosaic_ScanSAR ' + SLC_Tab + ' ' + TSLC + ' ' + TSLCPar + ' 10 2'
        rc = os.system(call_str)
        if rc != 0:
            raise RuntimeError('SLC_mosaic_ScanSAR failed with rc=' + str(rc))
    print("Down to SLC for %s is done! " % date)
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
    
    
    
    
    
