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
import getopt
import time
import glob
import argparse
import shutil

import subprocess
from pyint import _utils as ut

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


def ensure_master_rslc(projectName):
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict = ut.update_template(templateFile)
    master_date = templateDict['masterDate']
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']

    slc_root = scratchDir + '/' + projectName + '/SLC/' + master_date
    rslc_root = scratchDir + '/' + projectName + '/RSLC/' + master_date
    if not os.path.isdir(rslc_root):
        os.makedirs(rslc_root, exist_ok=True)

    src_slc = slc_root + '/' + master_date + '.slc'
    src_slc_par = slc_root + '/' + master_date + '.slc.par'
    src_amp = slc_root + '/' + master_date + '_' + rlks + 'rlks.amp'
    src_amp_par = slc_root + '/' + master_date + '_' + rlks + 'rlks.amp.par'

    dst_rslc = rslc_root + '/' + master_date + '.rslc'
    dst_rslc_par = rslc_root + '/' + master_date + '.rslc.par'
    dst_amp = rslc_root + '/' + master_date + '_' + rlks + 'rlks.amp'
    dst_amp_par = rslc_root + '/' + master_date + '_' + rlks + 'rlks.amp.par'

    if not os.path.isfile(src_slc) or not os.path.isfile(src_slc_par):
        raise FileNotFoundError('Master SLC is missing under SLC/' + master_date)

    if not os.path.isfile(dst_rslc):
        shutil.copyfile(src_slc, dst_rslc)
    if not os.path.isfile(dst_rslc_par):
        shutil.copyfile(src_slc_par, dst_rslc_par)

    if os.path.isfile(src_amp) and not os.path.isfile(dst_amp):
        shutil.copyfile(src_amp, dst_amp)
    if os.path.isfile(src_amp_par) and not os.path.isfile(dst_amp_par):
        shutil.copyfile(src_amp_par, dst_amp_par)

    if not os.path.isfile(dst_amp) or not os.path.isfile(dst_amp_par):
        cmd = [
            'multi_look',
            dst_rslc,
            dst_rslc_par,
            dst_amp,
            dst_amp_par,
            rlks,
            azlks,
        ]
        p = subprocess.run(cmd, shell=False, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        if p.returncode != 0:
            detail_parts = []
            if p.stdout:
                detail_parts.append(p.stdout.decode("utf-8", errors="replace"))
            if p.stderr:
                detail_parts.append(p.stderr.decode("utf-8", errors="replace"))
            detail = '\n'.join(part.strip() for part in detail_parts if part and part.strip())
            raise RuntimeError('multi_look master RSLC failed' + ('\n' + detail if detail else ''))

INTRODUCTION = '''
-------------------------------------------------------------------  
       Coregister all of the SLCs to the reference SAR image using GAMMA.
       [with assistant of DEM]
   
'''

EXAMPLE = '''
    Usage: 
            coreg_gamma_all.py projectName
            coreg_gamma_all.py projectName --parallel 4
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Coregister all of the SLCs to the reference SAR image using GAMMA.',\
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
    templateDir = os.getenv('TEMPLATEDIR')
    projectDir = scratchDir + '/' + projectName 
    slcDir    = scratchDir + '/' + projectName + '/SLC'
    rslcDir    = scratchDir + '/' + projectName + '/RSLC'   
    if not os.path.isdir(rslcDir): os.mkdir(rslcDir)
    ensure_master_rslc(projectName)
    
    if 'S1' in projectName: cmd_command = 'coreg_s1_gamma.py'
    else: cmd_command = 'coreg_gamma.py'
        
    err_txt = scratchDir + '/' + projectName + '/coreg_gamma_all.err'
    if os.path.isfile(err_txt): os.remove(err_txt)
    
    data_para = []
    #slc_list = [os.path.basename(fname) for fname in sorted(glob.glob(slcDir + '/*'))]
    master_date = ut.update_template(templateDir + "/" + projectName + ".template")['masterDate']
    slc_list = [item for item in ut.get_project_slcList(projectName) if item != master_date]
    for i in range(len(slc_list)):
        cmd0 = [cmd_command,projectName,slc_list[i]]
        data0 = [cmd0,err_txt]
        data_para.append(data0)
    
    results = ut.parallel_process(data_para, work, n_jobs=inps.parallelNumb, use_kwargs=False)
    failures = [str(item) for item in results if isinstance(item, Exception)]
    if failures:
        raise RuntimeError('\n\n'.join(failures))
    print("Coregister all of the SLCs %s is done! " % projectName)
    ut.print_process_time(start_time, time.time())
    
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
