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
  
  slc_sen_cat.py projectName date
  
  slc_sen_cat.py ChangningT55S1A 20180517
  
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
    down_dir    = scratchDir + '/' + projectName + "/DOWNLOAD"
    opod_dir = projectDir + '/OPOD'

    if not os.path.isdir(slc_dir):
        os.mkdir(slc_dir)
    if not os.path.isdir(opod_dir):
        os.mkdir(opod_dir)

    work_dir = slc_dir + '/' + date
    if not os.path.isdir(work_dir):
        os.mkdir(work_dir)

    os.chdir(work_dir)

    t_date = 't_' + date  

    call_str = 'ls ' + down_dir + '/S1*' + date + '* > ' + t_date
    os.system(call_str)
    start_swath = templateDict['start_swath']
    end_swath = templateDict['end_swath']
    k_swath = ut.get_sardata_swath(start_swath,end_swath)
    
    call_str = 'grep ' + date + ' ' + t_date + ' >  t1_' + date 
    os.system(call_str)
    t1_date = 't1_' + date
    raw_files = ut.read_txt2list(t1_date)
    raw_files = sorted(raw_files)
    satellite = get_satellite(str(raw_files[0]))
    orbit_file = ut.download_s1_orbit(date,opod_dir,satellite=satellite)

    for i in range(len(raw_files)):
        zipfile_ref=str(raw_files[i])
        outfile_name=zipfile_ref.split('/')[-1].split('.')[0]
        burst_number_table_ref=outfile_name + '.burst_number_table'
        call_str = 'S1_BURST_tab_from_zipfile ' + t1_date + ' ' + str(raw_files[0]) + ' - 1'
        os.system(call_str)
        
        call_str = call_str = 'grep ' + outfile_name + ' ' + 't1_' + date + ' >  t2_'+date  
        os.system(call_str)
      
        t2_date = 't2_' + date       
        #call_str = 'S1_import_SLC_from_zipfiles ' + t2_date + ' ' + burst_number_table_ref + ' vv 0 ' + k_swath
        call_str = 'S1_import_SLC_from_zipfiles ' + t2_date + ' ' + burst_number_table_ref + ' vv 0 ' + k_swath + ' ' + opod_dir + ' 1 1 '
        os.system(call_str)

        os.chdir(work_dir)
        #call_str = "rename vv.slc.iw1 IW1_" + str(i+1) + ".slc * "
        call_str = "rename 's/vv.slc.iw1/IW1_" + str(i+1) + ".slc/g' *"
        os.system(call_str)
        call_str = "rename 's/vv.slc.iw2/IW2_" + str(i+1) + ".slc/g' *"
        #call_str = "rename vv.slc.iw2 IW2_" + str(i+1) + ".slc * "
        os.system(call_str)
        call_str = "rename 's/vv.slc.iw3/IW3_" + str(i+1) + ".slc/g' *"
        #call_str = "rename vv.slc.iw3 IW3_" + str(i+1) + ".slc * "
        os.system(call_str)
        SLC_Tab = work_dir + '/' + date + '_' + str(i+1) + '_SLC_Tab'
        SLC_list = sorted(glob.glob(work_dir + '/*IW?_' + str(i+1) + '.slc'))
        SLC_par_list = sorted(glob.glob(work_dir + '/*IW?_' + str(i+1) + '.slc.par'))
        TOP_par_list = sorted(glob.glob(work_dir + '/*IW?_' + str(i+1) + '.slc.TOPS_par'))

        for kk in range(len(SLC_list)):
          call_str = 'echo ' + SLC_list[kk] + ' ' + SLC_par_list[kk] + ' ' + TOP_par_list[kk] + ' >> ' + SLC_Tab
          os.system(call_str)

     
    SLC_Tab = work_dir + '/' + date + '_SLC_Tab'
    SLC_list = len(raw_files)
    SLC_tab1 = date + '_' + '1_SLC_Tab' 
    SLC_tab2 = date + '_' + '2_SLC_Tab'
    #call_str = 'grep "IW*" ' + date +'_1_SLC_Tab | wc -l'
    n_swath = len(open(SLC_tab1, 'r').readlines())
    for kk in range(n_swath):
        call_str = 'echo ' + date + '.IW' + str(kk+1) + '.slc   ' + date + '.IW' + str(kk+1) + '.slc.par   ' + date + '.IW' + str(kk+1) + '.slc.TOPS_par  ' + ' >> ' + SLC_Tab
        os.system(call_str)

    call_str = 'SLC_cat_S1_TOPS  ' + SLC_tab1 + ' ' + SLC_tab2 + ' ' + SLC_Tab
    os.system(call_str) 
    SLC_list = sorted(glob.glob(work_dir + '/*IW?.slc'))
    SLC_par_list = sorted(glob.glob(work_dir + '/*IW?.slc.par'))
    TOP_par_list = sorted(glob.glob(work_dir + '/*IW?.slc.TOPS_par'))
    for kk in range(len(SLC_list)):
        BURST = SLC_par_list[kk].replace('slc.par','burst.par')
        call_str = 'SLC_burst_corners ' + SLC_par_list[kk] + ' ' +  TOP_par_list[kk] + ' > ' +BURST
        os.system(call_str)
    print("Down to SLC for %s is done! " % date)
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])




        
     
         
         
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 



    
    


    
