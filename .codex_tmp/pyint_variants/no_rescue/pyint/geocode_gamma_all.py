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
import time
import glob
import argparse

import subprocess
from pyint import _utils as ut

def work(data0):
    cmd = data0[0]
    err_file = data0[1]
    p = subprocess.run(cmd, shell=False,stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    stdout = p.stdout
    stderr = p.stderr
    
    if type(stderr) == bytes:
        aa=stderr.decode("utf-8")
    else:
        aa = stderr
    
    if aa:
        print(aa)
        str0 = cmd[0] + ' ' + cmd[1] + ' ' + cmd[2] + ' '  + '\n'
        with open(err_file, 'a') as f:
            f.write(str0)
            f.write(aa)
            f.write('\n')

    return 
#########################################################################

INTRODUCTION = '''
-------------------------------------------------------------------  
       Geocode products for one project using GAMMA.
       由模板参数 geocode_products 控制产品类型 (hyp3,licsbas,pot).
       一次调用自动处理所有选中的产品类型.
'''

EXAMPLE = '''
    Usage: 
            geocode_gamma_all.py projectName
            geocode_gamma_all.py projectName --parallel 4
            geocode_gamma_all.py projectName --parallel 4 --ifgramList-txt /test/ifgram_list.txt
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Geocode interferograms for one project using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('--parallel', dest='parallelNumb', type=int, default=1, help='Enable parallel processing and Specify the number of processors.')
    parser.add_argument('--ifgarmList-txt', dest='ifgarmListTxt', help='provided ifgram_list_txt. default: using ifgram_list.txt under projectName folder.')
    
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
    ifgDir = scratchDir + '/' + projectName + '/ifgrams'
    potDir = scratchDir + '/' + projectName + '/offsets'
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']

    # ===== 解析产品类型 =====
    products_str = templateDict.get('geocode_products', 'hyp3,licsbas')
    products = set(p.strip().lower() for p in products_str.split(','))
    need_ifg = ('hyp3' in products) or ('licsbas' in products)
    need_hyp3 = 'hyp3' in products
    need_pot = 'pot' in products

    print(f'geocode_products: {products_str}')
    
    if inps.ifgarmListTxt: ifgramList_txt = inps.ifgarmListTxt
    else: ifgramList_txt = scratchDir + '/' + projectName + '/ifgram_list.txt'
    ifgList0 = ut.read_txt2array(ifgramList_txt)
#    ifgList = ifgList0[:,0]
    if len(ifgList0)==3:
       ifgList=ifgList0[0]
       ifgList=[ifgList]     
    else:
        ifgList=ifgList0[:,0] 
    
    err_txt = scratchDir + '/' + projectName + '/geocode_gamma_all.err'
    if os.path.isfile(err_txt): os.remove(err_txt)
    
    data_para = []
    skip_count = 0
    for i in range(len(ifgList)):
        pair_i = ifgList[i]
        cmd0 = ['geocode_gamma.py', projectName, pair_i]
        data0 = [cmd0, err_txt]

        # 根据 geocode_products 组合检查是否已完成
        ifg_done = True
        pot_done = True

        if need_ifg:
            ifg_dir_i = ifgDir + '/' + pair_i
            if os.path.isdir(ifg_dir_i):
                # 基础产品检查: unw.bmp
                unw_bmp = ifg_dir_i + '/geo_' + pair_i + '_' + rlks + 'rlks.diff_filt.unw.bmp'
                if not (os.path.isfile(unw_bmp) and os.path.getsize(unw_bmp) > 0):
                    ifg_done = False
                # hyp3 额外检查: los_disp
                if need_hyp3 and ifg_done:
                    disp_f = ifg_dir_i + '/geo_' + pair_i + '_' + rlks + 'rlks.los_disp'
                    lv_f   = ifg_dir_i + '/lv_theta'
                    if not (os.path.isfile(disp_f) and os.path.getsize(disp_f) > 0):
                        ifg_done = False
                    if not (os.path.isfile(lv_f) and os.path.getsize(lv_f) > 0):
                        ifg_done = False
            else:
                ifg_done = True  # 目录不存在时不需要处理, geocode_gamma.py 会打 WARNING

        if need_pot:
            pot_dir_i = potDir + '/' + pair_i
            if os.path.isdir(pot_dir_i):
                geo_mag_bmp = pot_dir_i + '/geo_' + pair_i + '.disp_map.mag.bmp'
                lv_f = pot_dir_i + '/lv_theta'
                if not (os.path.isfile(geo_mag_bmp) and os.path.getsize(geo_mag_bmp) > 0):
                    pot_done = False
                if not (os.path.isfile(lv_f) and os.path.getsize(lv_f) > 0):
                    pot_done = False
            else:
                pot_done = True  # 目录不存在时不需要处理

        if ifg_done and pot_done:
            skip_count += 1
        else:
            data_para.append(data0)

    total = len(ifgList)
    todo = len(data_para)
    print(f'Geocode: {total} pairs total, {skip_count} done, {todo} to process')
    print(f'Parallel processors: {inps.parallelNumb}')
    print('=' * 60)

    if todo > 0:
        ut.parallel_process(data_para, work, n_jobs=inps.parallelNumb, use_kwargs=False)

    print("Geocode products for project %s is done! " % projectName)
    ut.print_process_time(start_time, time.time())
    
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
