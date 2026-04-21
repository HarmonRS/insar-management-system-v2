#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.1                      ### 
###  Copy Right (c): 2017-2019, Yunmeng Cao                   ###  
###  Author: Yunmeng Cao                                      ###
###  Modified: 2026, Z. Zou - HyP3 UTM batch conversion       ###
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
    """并行 worker: 执行单对 hyp3format_gamma.py"""
    cmd = data0[0]
    err_file = data0[1]
    p = subprocess.run(cmd, shell=False, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
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
   批量将 GAMMA 处理结果转换为 HyP3 UTM GeoTIFF 格式,
   支持并行处理。
'''

EXAMPLE = '''
    Usage: 
            hyp3format_gamma_all.py projectName
            hyp3format_gamma_all.py projectName --parallel 4
            hyp3format_gamma_all.py projectName --output-dir /path/to/output
            hyp3format_gamma_all.py projectName --parallel 4 --ifgramList-txt /test/ifgram_list.txt
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(
        description='批量转换 GAMMA 输出为 HyP3 UTM GeoTIFF 格式.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=INTRODUCTION + '\n' + EXAMPLE)

    parser.add_argument('projectName', help='项目名称')
    parser.add_argument('--parallel', dest='parallelNumb', type=int, default=1, 
                        help='并行处理器数量')
    parser.add_argument('--ifgramList-txt', dest='ifgarmListTxt', 
                        help='干涉图列表文件. 默认: projectName/ifgram_list.txt')
    parser.add_argument('--output-dir', dest='output_dir', default=None,
                        help='输出目录. 默认: projectDir/Hyp3Products/')
    
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
    ifgDir = projectDir + '/ifgrams'
    templateDict = ut.update_template(templateFile)
    masterDate = templateDict['masterDate']
    
    # 输出目录
    output_dir = inps.output_dir or (projectDir + '/Hyp3Products')
    os.makedirs(output_dir, exist_ok=True)
    
    # 读取干涉图列表
    if inps.ifgarmListTxt: 
        ifgramList_txt = inps.ifgarmListTxt
    else: 
        ifgramList_txt = projectDir + '/ifgram_list.txt'
    
    ifgList0 = ut.read_txt2array(ifgramList_txt)
    
    if len(ifgList0) == 3:
        ifgList = ifgList0[0]
        ifgList = [ifgList]      
    else:
        ifgList = ifgList0[:,0]   
    
    # 错误日志
    err_txt = projectDir + '/hyp3format_gamma_all.err'
    if os.path.isfile(err_txt): 
        os.remove(err_txt)
    
    # 构建并行命令
    data_para = []
    skip_count = 0
    for i in range(len(ifgList)):
        Pair = ifgList[i]
        
        # 检查输出是否已存在 (用 corr.tif 作为完成标记)
        pair_dir = output_dir + '/' + Pair
        # 匹配 HyP3 命名: {prefix}_corr.tif
        geo_file0 = pair_dir + '/' + projectName + '_' + Pair.replace('-', 'T000000_') + 'T000000_corr.tif'
        
        if os.path.isfile(geo_file0) and os.path.getsize(geo_file0) > 0:
            skip_count += 1
            continue
        
        cmd0 = ['hyp3format_gamma.py', projectName, Pair]
        if inps.output_dir:
            cmd0.extend(['--output-dir', inps.output_dir])
        
        data0 = [cmd0, err_txt]
        data_para.append(data0)
    
    print(f"HyP3 格式转换: 共 {len(ifgList)} 对, 跳过 {skip_count} 对, 待处理 {len(data_para)} 对")
    print(f"并行数: {inps.parallelNumb}, 输出目录: {output_dir}")
    
    # 并行执行
    ut.parallel_process(data_para, work, n_jobs=inps.parallelNumb, use_kwargs=False)
    
    print("HyP3 格式转换完成: project %s" % projectName)
    ut.print_process_time(start_time, time.time())
    
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])
