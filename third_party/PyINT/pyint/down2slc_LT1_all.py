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
import getopt
import time
import glob
import argparse
import re

import subprocess
from pyint import _utils as ut

def get_LT1_date(raw_file):
    file0 = os.path.basename(raw_file)
    match = re.search(r'(20\d{6})', file0)
    if match:
        return match.group(1)
    return ''


def discover_lt1_inputs(down_dir):
    candidates = []
    for pattern in ('/LT1*.tar.gz', '/LT1*.tiff'):
        candidates.extend(glob.glob(down_dir + pattern))
    return sorted(set(candidates))

def work(data0):
    cmd = data0[0]
    err_txt = data0[1]
    p = subprocess.run(cmd, shell=False,stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    stdout = p.stdout
    stderr = p.stderr
    
    if type(stderr) == bytes:
        aa=stderr.decode("utf-8")
    else:
        aa = stderr

    if type(stdout) == bytes:
        bb=stdout.decode("utf-8", errors="replace")
    else:
        bb = stdout

    if p.returncode != 0:
        detail_parts = []
        if bb:
            detail_parts.append(bb)
        if aa:
            detail_parts.append(aa)
        detail = '\n'.join(part.strip() for part in detail_parts if part and part.strip())
        str0 = cmd[0] + ' ' + cmd[1] + ' ' + cmd[2] + '\n'
        with open(err_txt, 'a') as f:
            f.write(str0)
            if detail:
                f.write(detail)
                f.write('\n')
        raise RuntimeError(str0.strip() + ' failed with rc=' + str(p.returncode) + ('\n' + detail if detail else ''))

    if aa:
        str0 = cmd[0] + ' ' + cmd[1] + ' ' + cmd[2] + '\n'
        with open(err_txt, 'a') as f:
            f.write(str0)
            f.write(aa)
            f.write('\n')

    return 
#########################################################################

INTRODUCTION = '''
-------------------------------------------------------------------  
   
       Generate SLCs from Sentinel-1 raw dataset with orbit correction using GAMMA.
   
'''

EXAMPLE = '''
    Usage: 
            down2slc_LT1_all.py projectName
            down2slc_LT1_all.py projectName --parallel 4
 
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Generate SLCs from Sentinel-1 raw dataset with orbit correction using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('--parallel', dest='parallelNumb', type=int, default=1, help='Enable parallel processing and Specify the number of processors.')
    
    inps = parser.parse_args()
    return inps


def main(argv):
    start_time = time.time()
    inps = cmdLineParse() 
    projectName = inps.projectName
    scratchDir = os.getenv('SCRATCHDIR')
    projectDir = scratchDir + '/' + projectName 
    downDir    = scratchDir + '/' + projectName + "/DOWNLOAD"
    slcDir    = scratchDir + '/' + projectName + "/SLC"
    raw_file_list = discover_lt1_inputs(downDir)
    if len(raw_file_list) == 0:
        raise RuntimeError('No LT-1 inputs found under: ' + downDir)
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    

    date_list = []
    cat_list = []
    for kk in range(len(raw_file_list)):
        date0 = get_LT1_date(os.path.basename(raw_file_list[kk]))
        if not date0:
            continue
        if date0 not in date_list:
            date_list.append(date0)
            cat_list.append('0')
        else:
            cat_list[date_list.index(date0)]='1'        
#    date_list = set(date_list)
#    date_list = sorted(date_list)
    
    print('Date to be processed:')
    for k0 in date_list:
        print(k0)
    
    err_txt = scratchDir + '/' + projectName + '/down2slc_LT1_all.err'
    if os.path.isfile(err_txt): os.remove(err_txt)
    
    data_para = []
    for i in range(len(date_list)):
       if cat_list[i]=='0':
          cmd0 = ['down2slc_LT1.py',projectName,date_list[i]]
          work_dir = slcDir + '/'  + date_list[i]
          slc_file0 = work_dir + '/down2slc.dat'
          data0 = [cmd0,err_txt]
#          data_para.append(data0)
          k00 = 0
          if os.path.isfile(slc_file0):
             if os.path.getsize(slc_file0) > 0:
                 k00 = 1
             else:
                 k00 = 0
          if k00==0:
             data_para.append(data0)
       else:
          cmd0 = ['down2slc_cat_LT1.py',projectName,date_list[i]]
          work_dir = slcDir + '/'  + date_list[i]
          slc_file0 = work_dir + '/down2slc.dat'
          data0 = [cmd0,err_txt]
#          data_para.append(data0)
          #data_para.append(data0)
          k00 = 0
          if os.path.isfile(slc_file0):
             if os.path.getsize(slc_file0) > 0:
                 k00 = 1
             else:
                 k00 = 0
          if k00==0:
             data_para.append(data0)
    results = ut.parallel_process(data_para, work, n_jobs=inps.parallelNumb, use_kwargs=False)
    failures = [str(item) for item in results if isinstance(item, Exception)]
    if failures:
        raise RuntimeError('\n\n'.join(failures))
    os.chdir(downDir)
    print("Down to SLC for project %s is done! " % projectName)
    ut.print_process_time(start_time, time.time())
    
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
