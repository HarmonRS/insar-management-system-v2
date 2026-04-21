#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.2                      ### 
###  Copy Right (c): 2017-2019, Chen Wei                 ###  
###  Author: chenwei                                   ###                                                          
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
import re

from pyint import _orbit_bridge as orbit_bridge
from pyint import _utils as ut


def get_LT1_date(raw_file):
    file0 = os.path.basename(raw_file)
    date = file0[41:48]
    return date

def get_satellite(raw_file):
    if 'LT1A_MONO_' in raw_file:
        s0 = 'A'
    else:
        s0 = 'B'
    return s0


def discover_lt1_inputs(down_dir, date):
    candidates = []
    for pattern in (
        down_dir + '/LT1*' + date + '*.tar.gz',
        down_dir + '/LT1*' + date + '*.tiff',
    ):
        candidates.extend(glob.glob(pattern))
    return sorted(set(candidates))


def write_input_list(list_path, paths):
    with open(list_path, 'w') as f:
        for path in paths:
            f.write(path + '\n')


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Generate SLC from LT1 raw data with orbit correction using GAMMA.',\
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
  
  down2slc_LT1.py projectName date 
  
  down2slc_LT1.py ChangningT55S1A 20180517 
  
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

    if not os.path.isdir(slc_dir):
        os.mkdir(slc_dir)
        
    work_dir = slc_dir + '/' + date
    if not os.path.isdir(work_dir):
        os.mkdir(work_dir)

    os.chdir(work_dir)
    
    t_date = 't_' + date

    input_files = discover_lt1_inputs(down_dir, date)
    write_input_list(t_date, input_files)
    raw_files = ut.read_txt2list(t_date)
    if len(raw_files) == 0:
        raise RuntimeError('No LT-1 inputs found for date: ' + date)
    satellite = get_satellite(str(raw_files[0]))
    raw_file_list = list(raw_files)

  

    file_num=len(raw_files)
    for kk in range(file_num):

           zipfile_ref=str(raw_files[kk])
           outfile_name=zipfile_ref.split('/')[-1]
           print(outfile_name)
           before_slc = set(glob.glob(work_dir + '/*.slc'))
           before_slc_par = set(glob.glob(work_dir + '/*.slc.par'))
           before_update = set(glob.glob(work_dir + '/*.slc.update'))
           before_update_par = set(glob.glob(work_dir + '/*.slc.update.par'))
           call_str = "echo   " + zipfile_ref  + "  >date"
           if os.system(call_str) != 0:
               raise RuntimeError('Failed to materialize LT-1 input list for date: ' + date)
           call_str = 'LT1_import_SLC_from_zipfiles1 date   0 '
           rc = os.system(call_str)
           if rc != 0:
               raise RuntimeError('LT1_import_SLC_from_zipfiles1 failed for date %s with rc=%s' % (date, rc))
           after_slc = set(glob.glob(work_dir + '/*.slc'))
           after_slc_par = set(glob.glob(work_dir + '/*.slc.par'))
           after_update = set(glob.glob(work_dir + '/*.slc.update'))
           after_update_par = set(glob.glob(work_dir + '/*.slc.update.par'))
           new_slc = sorted(after_slc - before_slc)
           new_slc_par = sorted(after_slc_par - before_slc_par)
           new_update = sorted(after_update - before_update)
           new_update_par = sorted(after_update_par - before_update_par)
           if not new_slc or not new_slc_par:
               raise RuntimeError('LT-1 import produced no SLC outputs for date: ' + date)
           if len(new_update) != len(new_update_par):
               raise RuntimeError('LT-1 import produced mismatched update SLC outputs for date: ' + date)
           bridge_targets = sorted((after_slc_par - before_slc_par) | (after_update_par - before_update_par))
           if bridge_targets:
               bridge_result = orbit_bridge.apply_precise_orbit(
                   date,
                   bridge_targets,
                   work_dir=work_dir,
                   operation_tag='lt1_import',
               )
               if bridge_result.get('stdout'):
                   print(bridge_result['stdout'])
               if bridge_result.get('stderr'):
                   print(bridge_result['stderr'])
    

    SLC_Tab = work_dir + '/' + date+'_SLC_Tab'
    SLC_Tab_update = work_dir + '/' + date+'_update_SLC_Tab'
    SLC_update_list = sorted(glob.glob(work_dir + '/*.slc.update'))
    SLC_update_par_list = sorted(glob.glob(work_dir + '/*.slc.update.par'))
    SLC_list = sorted(glob.glob(work_dir + '/*.slc')) 
    SLC_par_list = sorted(glob.glob(work_dir + '/*.slc.par')) 
    if len(SLC_list) == 0 or len(SLC_par_list) == 0:
        raise RuntimeError('No LT-1 SLC outputs were generated for date: ' + date)
    if len(SLC_list) != len(SLC_par_list):
        raise RuntimeError('LT-1 SLC and SLC parameter counts do not match for date: ' + date)
    if len(SLC_update_list) != len(SLC_update_par_list):
        raise RuntimeError('LT-1 update SLC and parameter counts do not match for date: ' + date)
   
    if os.path.isfile(SLC_Tab):
        os.remove(SLC_Tab)    
   
    for kk in range(len(SLC_list)):
        call_str = 'echo ' + SLC_list[kk] + ' ' + SLC_par_list[kk]  + ' >> ' + SLC_Tab
        if os.system(call_str) != 0:
            raise RuntimeError('Failed to write SLC tab for date: ' + date)
        call_str = 'echo ' + SLC_update_list[kk] + ' ' + SLC_update_par_list[kk]  + ' >> ' + SLC_Tab_update
        if os.system(call_str) != 0:
            raise RuntimeError('Failed to write update SLC tab for date: ' + date)
    call_str = 'SLC_cat_list.py ' + SLC_Tab_update + ' ' + date + '.slc ' + date + '.slc.par '
    if os.system(call_str) != 0:
        raise RuntimeError('SLC_cat_list.py failed for date: ' + date)
    if not os.path.isfile(work_dir + '/' + date + '.slc.par'):
        raise RuntimeError('Final LT-1 concatenated SLC parameter file is missing for date: ' + date)
    final_bridge_result = orbit_bridge.apply_precise_orbit(
        date,
        [work_dir + '/' + date + '.slc.par'],
        work_dir=work_dir,
        operation_tag='slc_cat_final',
    )
    if final_bridge_result.get('stdout'):
        print(final_bridge_result['stdout'])
    if final_bridge_result.get('stderr'):
        print(final_bridge_result['stderr'])
    with open(work_dir + '/down2slc.dat', 'w') as f:
        f.write('ok\n')
    print("Down to SLC for %s is done! " % date)
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
