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
from PIL import Image
from pylab import * 
import argparse

from pyint import _utils as ut


def _run_or_raise(call_str, stage):
    rc = os.system(call_str)
    if rc != 0:
        raise RuntimeError('%s failed with rc=%s: %s' % (stage, rc, call_str))
    return rc


INTRODUCTION = '''
-------------------------------------------------------------------  
       Unwrap differential interferogram using GAMMA.
       [Only support mcf, not implement branch_cut yet]
   
'''

EXAMPLE = '''
    Usage: 
            unwrap_gamma.py projectName Mdate Sdate
            unwrap_gamma.py PacayaT163TsxHhA 20150102 20150601
-------------------------------------------------------------------  
'''

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Unwrap differential interferogram using GAMMA-mcf method.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)
    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('Mdate',help='Master date.')
    parser.add_argument('Sdate',help='Slave date.')
    
    inps = parser.parse_args()
    return inps


def main(argv):
    
    inps = cmdLineParse() 
    Mdate = inps.Mdate
    Sdate = inps.Sdate
    
    projectName = inps.projectName
    Sdate = inps.Sdate
    Mdate = inps.Mdate
    
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    auto_unw = templateDict['auto_unw']
    init_flag = templateDict['init_flag']
    r_refer = templateDict['r_refer']
    a_refer = templateDict['a_refer']
    make_mask = templateDict['make_mask']
    processDir = scratchDir + '/' + projectName + "/PROCESS"
    slcDir     = scratchDir + '/' + projectName + "/SLC"
    rslcDir    = scratchDir + '/' + projectName + '/RSLC'
    ifgDir     = scratchDir + '/' + projectName + '/ifgrams'
    
    Pair = Mdate + '-' + Sdate
    workDir = ifgDir + '/' + Pair
    
    ################ copy file for parallel processing ###############
    Mamp0    = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar0 = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    Samp0    = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SampPar0 = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    
    
    Mamp    = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    Samp    = workDir + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SampPar = workDir + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    
    ut.copy_file(Mamp0,Mamp)
    ut.copy_file(Samp0,Samp)
    ut.copy_file(MampPar0,MampPar)
    ut.copy_file(SampPar0,SampPar)
    ###############################################################  

    nWidth = ut.read_gamma_par(MampPar, 'read', 'range_samples')
    nLine =  ut.read_gamma_par(MampPar, 'read', 'azimuth_lines')
    if auto_unw == '1':
        if str(r_refer).strip() in ('', '-'):
            r_refer = str(int(int(nWidth) / 2))
        if str(a_refer).strip() in ('', '-'):
            a_refer = str(int(int(nLine) / 2))
    unwrap_window = '0 0 ' + nWidth + ' ' + nLine
    
    CORMASK = workDir + '/' + Pair + '_' +rlks + 'rlks.diff_filt.cor'
    WRAPlks = workDir + '/' + Pair + '_' +rlks + 'rlks.diff_filt'
    UNWlks = workDir + '/' + Pair + '_' +rlks + 'rlks.diff_filt.unw'
    
    CORMASKbmp = CORMASK.replace('.diff_filt.cor','.diff_filt.cor_mask.bmp')
    
    if os.path.isfile(CORMASKbmp):
        os.remove(CORMASKbmp)
            
    call_str = 'rascc_mask ' + CORMASK + ' ' + Mamp + ' ' + nWidth + ' 1 1 0 1 1 ' + templateDict['unwrapThreshold'] + ' 0.0 0.1 0.9 1. .35 1 ' + CORMASKbmp   # based on int coherence
    _run_or_raise(call_str, 'rascc_mask')
   
    if auto_unw == '1': 
       if make_mask == "1":
           call_str = 'mcf ' + WRAPlks + ' ' + CORMASK + ' ' + CORMASKbmp + ' ' + UNWlks + ' ' + nWidth + ' ' + templateDict['mcf_triangular'] + ' ' + unwrap_window + ' ' + templateDict['unwrap_patr'] + ' ' + templateDict['unwrap_pataz'] +' - '+ r_refer + ' ' + a_refer + ' ' + init_flag
           print(call_str)
           _run_or_raise(call_str, 'mcf_masked')
       else:
            call_str = 'mcf ' + WRAPlks + ' ' + CORMASK + ' - ' + ' ' + UNWlks + ' ' + nWidth + ' ' + templateDict['mcf_triangular'] + ' ' + unwrap_window + ' ' + templateDict['unwrap_patr'] + ' ' + templateDict['unwrap_pataz'] + ' - ' + r_refer + ' ' + a_refer + '  ' + init_flag
            _run_or_raise(call_str, 'mcf_unmasked')
    else:
       im = array(Image.open(CORMASKbmp))
       imshow(im)
       print('Please select the reference points,max click 10 times')
       x =ginput(10)
       print ('you clicked:',x)
       ref_point=list(x[-1])
       r_init=int(ref_point[0])
       a_init=int(ref_point[1])
       if make_mask == '1':
           call_str = 'mcf ' + WRAPlks + ' ' + CORMASK + ' ' + CORMASKbmp + ' ' + UNWlks + ' ' + nWidth + ' ' + templateDict['mcf_triangular'] + ' ' + unwrap_window + ' ' + templateDict['unwrap_patr'] + ' ' + templateDict['unwrap_pataz'] + ' - ' + str(r_init) + ' ' + str(a_init) + ' ' + init_flag
           _run_or_raise(call_str, 'mcf_masked_manual')  
       else:
           call_str = 'mcf ' + WRAPlks + ' - ' + ' - ' + ' ' + UNWlks + ' ' + nWidth + ' ' + templateDict['mcf_triangular'] + ' ' + unwrap_window + ' ' + templateDict['unwrap_patr'] + ' ' + templateDict['unwrap_pataz'] + ' - ' + str(r_init) + ' ' + str(a_init) + '  ' + init_flag
           _run_or_raise(call_str, 'mcf_unmasked_manual')
    call_str = 'rasdt_pwr ' + UNWlks + ' ' + Mamp + ' ' + nWidth + ' 1 0 1 1 -3.14 3.14 1' 
    _run_or_raise(call_str, 'rasdt_pwr_unw')

    if os.path.isfile(Mamp):
        os.remove(Mamp)
    if os.path.isfile(Samp):
        os.remove(Samp)
    #os.remove(MampPar)
    #os.remove(SampPar)
    print("Uwrapping interferometric phase is done!")
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])
