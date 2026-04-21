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

from pyint import _utils as ut


def get_s1_date(raw_file):
    file0 = os.path.basename(raw_file)
    date = file0[17:25]
    return date

def get_satellite(raw_file):
    if 'S1A_IW_SLC_' in raw_file:
        s0 = 'A'
    else:
        s0 = 'B'
    
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

   Generate SLC from Sentinel-1 raw data using S1_import_SLC_from_zipfiles with orbit correction.
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
    
    slc_dir =  projectDir + '/SLC'
    down_dir = projectDir + '/DOWNLOAD'
    #opod_dir = projectDir + '/OPOD'
    opod_dir = os.getenv('OPOD_DIR')
    if not os.path.isdir(slc_dir):
        os.mkdir(slc_dir)
   # if not os.path.isdir(opod_dir):
   #     os.mkdir(opod_dir)
        
    work_dir = slc_dir + '/' + date
    if not os.path.isdir(work_dir):
        os.mkdir(work_dir)
   #if not os.path.isdir(opod_dir):
    #    os.mkdir(opod_dir)


    call_str = " eof --save-dir " + opod_dir  + " -p " + down_dir
    os.system(call_str)

    os.chdir(work_dir)
    
    t_date = 't_' + date
    
    call_str = 'ls ' + down_dir + '/S1*' + date + '*.zip > ' + t_date
    os.system(call_str)
    
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
    
    raw_files = ut.read_txt2list(t_date)
    satellite = get_satellite(str(raw_files[0]))
    #orbit_file = ut.download_s1_orbit(date,opod_dir,satellite=satellite)
    zipfile_ref=str(raw_files[0])
    outfile_name=zipfile_ref.split('/')[-1].split('.')[0]
    burst_number_table_ref=outfile_name + '.BURST_tab' 
    
    call_str = 'S1_BURST_tab_from_zipfile.py 3 --zip_ref_list  ' + t_date + '  --zip_list  ' + t_date
    os.system(call_str)

   # call_str = 'S1_import_SLC_from_zipfiles ' + t_date + ' ' + burst_number_table_ref + ' vv 0 ' + k_swath
    call_str = 'read_S1_TOPS_SLC.py  ' + zipfile_ref + ' --burst_sel  ' + burst_number_table_ref + ' --pol vv   --root_name ' + date + '  --sw_start  ' +  start_swath + ' --swn ' + end_swath + ' --OPOD_dir ' + opod_dir
    os.system(call_str)
    
    os.chdir(work_dir)
    
    call_str = "rename 's/vv.iw1.slc/IW1.slc/g'  *"
    #call_str = "rename vv.slc.iw1 IW1.slc * "
    os.system(call_str)
    call_str = "rename 's/vv.iw2.slc/IW2.slc/g' *"
    #call_str = "rename vv.slc.iw2 IW2.slc * "
    os.system(call_str)
    call_str = "rename 's/vv.iw3.slc/IW3.slc/g' *"
    #call_str = "rename vv.slc.iw3 IW3.slc * "
    os.system(call_str)
    call_str = "rename 's/tops_par/TOPS_par/g' *.tops_par "
    os.system(call_str)

    SLC_Tab = work_dir + '/' + date+'_SLC_Tab'
    SLC_list = sorted(glob.glob(work_dir + '/*IW*.slc')) 
    SLC_par_list = sorted(glob.glob(work_dir + '/*IW*.slc.par')) 
    TOP_par_list = sorted(glob.glob(work_dir + '/*IW*.slc.TOPS_par')) 
   
    if os.path.isfile(SLC_Tab):
        os.remove(SLC_Tab)    
   
    for kk in range(len(SLC_list)):
        call_str = 'echo ' + SLC_list[kk] + ' ' + SLC_par_list[kk] + ' ' + TOP_par_list[kk] + ' >> ' + SLC_Tab
        os.system(call_str)
        
        BURST = SLC_par_list[kk].replace('slc.par','burst.par')
        call_str = 'SLC_burst_corners ' + SLC_par_list[kk] + ' ' +  TOP_par_list[kk] + ' > ' +BURST
        os.system(call_str)
        call_str =  "echo 'SLC has  already down' >down2slc.dat"
        os.system(call_str)
    print("Down to SLC for %s is done! " % date)
    sys.exit(1)
    
if __name__ == '__main__':
    main(sys.argv[:])    
