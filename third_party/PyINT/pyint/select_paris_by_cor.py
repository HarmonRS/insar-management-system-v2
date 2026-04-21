#! /usr/bin/env python
#################################################################
###  This program is part of PyINT                            ###   
###  Author: chen                                      ###                                                          
###  Contact : chenweicug@126.com                             ###  
#################################################################

import numpy as np
import os
import sys  
import getopt
import time
import glob
import argparse
from pyint import _utils as ut
import cv2
 

INTRODUCTION = '''
-------------------------------------------------------------------  
     compare two corherence for  SAR images for delete the bad images .
   
'''

EXAMPLE = '''
    Usage: 
            cor_correlation.py projectName Mdate Sdate
           cor_correlation.py PacayaT163TsxHhA 20150102 20150601 ifgrams_list.txt
-------------------------------------------------------------------  
'''
def cmdLineParse():
    parser = argparse.ArgumentParser(description='Coregister all of the SLCs to the reference SAR image using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('Mdate',help='Master date.')
    parser.add_argument('Sdate',help='Slave date.')
    parser.add_argument('ifgs', help='provided ifgram_list_txt. default: using ifgram_list.txt under projectName folder.')
    parser.add_argument('correlation_index', help='provided the accept correlation  for the file . default: 0.55.')

    inps = parser.parse_args()
    return inps


def main(argv):
    
    start_time = time.time()
    inps = cmdLineParse() 
    Mdate = inps.Mdate
    Sdate = inps.Sdate

    if inps.correlation_index:     Score = inps.correlation_index
    else:    Score = 0.55

    projectName = inps.projectName
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    
    projectDir = scratchDir + '/' + projectName 
    
    slcDir    = scratchDir + '/' + projectName + '/SLC'
    rslcDir   = scratchDir + '/' + projectName + '/RSLC' 
    ifgDir = projectDir + '/ifgrams'
    badifgDir = projectDir + '/bad_ifgrams'
    if not os.path.isdir(ifgDir): os.mkdir(ifgDir)
    if not os.path.isdir(badifgDir): os.mkdir(badifgDir)
    Pair = Mdate + '-' + Sdate
    workDir = ifgDir + '/' + Pair
    
    #######################################################################
    Mamp     = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar  = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    Samp     = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SampPar  = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    
    Mrslc    = rslcDir  + '/' + Mdate + '/' + Mdate + '.rslc'
    MrslcPar = rslcDir  + '/' + Mdate + '/' + Mdate + '.rslc.par'
    Srslc    = rslcDir  + '/' + Sdate + '/' + Sdate + '.rslc'
    SrslcPar = rslcDir  + '/' + Sdate + '/' + Sdate + '.rslc.par'

    if inps.ifgs: ifgramList_txt = inps.ifgs
    else: ifgramList_txt = scratchDir + '/' + projectName + '/ifgram_list.txt'
    MasterPar = rslcDir  + '/' + masterDate + '/' + masterDate + '.rslc.par'
    first_image= workDir  +  '/' + Pair + '_' + rlks + 'rlks.diff_filt.cor.bmp'
    ifgList0 = ut.read_txt2array(ifgramList_txt)
# 加载第一张图
    image1 = cv2.imread(first_image)
    ifgList0 = ut.read_txt2array(ifgramList_txt)
#    ifgList = ifgList0[:,0]
    if len(ifgList0)==3:
       ifgList=ifgList0[0]
       ifgList=[ifgList]
      
    else:
       # ifgList=ifgList0[:,0]   
        ifgList=ifgList0
    err_txt = scratchDir + '/' + projectName + '/cor_correlation_all.err'
    if os.path.isfile(err_txt): os.remove(err_txt)
    
    out_file='cor_correltion.txt'
    if os.path.isfile(out_file): os.remove(out_file)
    for i in range(len(ifgList)):
        m0 = ut.yyyymmdd(ifgList[i].split('-')[0])
        s0 = ut.yyyymmdd(ifgList[i].split('-')[1])
        Pair2 = m0 + '-' + s0
        workdir = ifgDir + '/' + Pair2
        # 加载第二张图片
        second_image = workdir +  '/' + Pair2 + '_' + rlks + 'rlks.diff_filt.cor.bmp'
        image2=cv2.imread(second_image)
        # 将图片转换为灰度图像
        gray_image1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
        gray_image2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
        # 计算两张图片之间的结构相似性指数（SSIM）

        ssim_score = cv2.matchTemplate(gray_image1, gray_image2, cv2.TM_CCOEFF_NORMED)
        score = ssim_score[0][0]

        if str(score) < Score:
                 print(m0, "-",  s0, "相关性得分：", ssim_score[0][0])
                 call_str=' mv  ' +  workdir + '  ' +  badifgDir + '/'
                 os.system(call_str)
        else:
                print("This pair", m0,'-', s0,"is accept")
        call_str = 'echo  ' + m0 + '-' +  s0 + '   ' + '     ' + str(score) + '>>' + out_file
        os.system(call_str)

    print("Delete the bad  interferograms for project %s is done! " % projectName)
    ut.print_process_time(start_time, time.time())
    sys.exit(1)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
