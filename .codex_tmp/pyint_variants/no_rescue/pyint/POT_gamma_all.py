#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ###
###  Batch Pixel Offset Tracking (POT) using GAMMA            ###
###  Author: ZYD / Cascade AI                                 ###
#################################################################

import numpy as np
import os
import sys
import getopt
import time
import glob
import argparse

import subprocess
from pyint import _utils as ut


def work(data0):
    cmd = data0[0]
    err_file = data0[1]
    p = subprocess.run(cmd, shell=False, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    stdout = p.stdout
    stderr = p.stderr

    if type(stderr) == bytes:
        aa = stderr.decode("utf-8")
    else:
        aa = stderr

    if aa:
        str0 = ' '.join(cmd) + '\n'
        with open(err_file, 'a') as f:
            f.write(str0)
            f.write(aa)
            f.write('\n')

    return

#########################################################################

INTRODUCTION = '''
-------------------------------------------------------------------
  Batch Pixel Offset Tracking for one project using GAMMA.

  Runs POT_gamma.py for each interferometric pair in parallel.
  Skips pairs that already have completed results (disp_map.mag.bmp).
'''

EXAMPLE = '''
    Usage:
        POT_gamma_all.py projectName
        POT_gamma_all.py projectName --parallel 4
        POT_gamma_all.py projectName --parallel 4 --ifgarmList-txt /test/ifgram_list.txt
-------------------------------------------------------------------
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(
        description='Batch Pixel Offset Tracking for one project using GAMMA.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=INTRODUCTION + '\n' + EXAMPLE)

    parser.add_argument('projectName', help='projectName for processing.')
    parser.add_argument('--parallel', dest='parallelNumb', type=int, default=1,
                        help='Enable parallel processing and specify the number of processors.')
    parser.add_argument('--ifgarmList-txt', dest='ifgarmListTxt',
                        help='Provided ifgram_list_txt. Default: using ifgram_list.txt under projectName folder.')

    inps = parser.parse_args()
    return inps


def main(argv):
    start_time = time.time()
    inps = cmdLineParse()
    projectName = inps.projectName
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    projectDir = scratchDir + '/' + projectName
    potDir = projectDir + '/offsets'
    if not os.path.isdir(potDir):
        os.mkdir(potDir)

    templateDict = ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']

    if inps.ifgarmListTxt:
        ifgramList_txt = inps.ifgarmListTxt
    else:
        ifgramList_txt = projectDir + '/ifgram_list.txt'

    ifgList0 = ut.read_txt2array(ifgramList_txt)

    if len(ifgList0) == 3:
        ifgList = ifgList0[0]
        ifgList = [ifgList]
    else:
        ifgList = ifgList0[:, 0]

    err_txt = projectDir + '/POT_gamma_all.err'
    if os.path.isfile(err_txt):
        os.remove(err_txt)

    data_para = []
    skip_count = 0
    for i in range(len(ifgList)):
        m0 = ut.yyyymmdd(ifgList[i].split('-')[0])
        s0 = ut.yyyymmdd(ifgList[i].split('-')[1])
        cmd0 = ['POT_gamma.py', projectName, m0, s0]

        # 检查完成标志: disp_map.mag.bmp
        Pair = ifgList[i]
        disp_bmp = potDir + '/' + Pair + '/' + Pair + '.disp_map.mag.bmp'

        k00 = 0
        if os.path.isfile(disp_bmp):
            if os.path.getsize(disp_bmp) > 0:
                k00 = 1
                skip_count += 1

        if k00 == 0:
            data0 = [cmd0, err_txt]
            data_para.append(data0)

    total = len(ifgList)
    todo = len(data_para)
    print(f'Pixel Offset Tracking: {total} pairs total, '
          f'{skip_count} already done, {todo} to process')
    print(f'Parallel processors: {inps.parallelNumb}')
    print(f'Output directory: {potDir}')
    print('=' * 60)

    if todo > 0:
        ut.parallel_process(data_para, work, n_jobs=inps.parallelNumb, use_kwargs=False)

    print("Pixel Offset Tracking for project %s is done! " % projectName)
    ut.print_process_time(start_time, time.time())

    sys.exit(0)


if __name__ == '__main__':
    main(sys.argv[:])
