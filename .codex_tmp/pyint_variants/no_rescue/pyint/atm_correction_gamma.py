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
    masterDate = templateDict['masterDate']
    demDir    = scratchDir + '/' + projectName  + '/DEM'
    Pair = Mdate + '-' + Sdate
    workDir = ifgDir + '/' + Pair
    
    ################ prepare file for parallel processing ###############
    HGTSIM      = demDir + '/' + masterDate + '_' + rlks + 'rlks.rdc.dem'   
    Mamp    = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    Samp    = workDir + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SampPar = workDir + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    diff_par = workDir + '/' + Mdate + '_' + Sdate + '_diff_par'
    UNWlks = workDir + '/' + Pair + '_' +rlks + 'rlks.diff_filt.unw'
    CORMASK = workDir + '/' + Pair + '_' +rlks + 'rlks.diff_filt.cor'
    ATM_PHASE = workDir + '/' + Pair + '_' +rlks + 'rlks.atm_phae'
    ATMCOR_UNW = workDir + '/' + Pair + '_' +rlks + 'rlks.diff_filt.atmcor.unw'
  
    nWidth = ut.read_gamma_par(MampPar, 'read', 'range_samples')
    nLine =  ut.read_gamma_par(MampPar, 'read', 'azimuth_lines')
   ###############################################################  
    call_str = "create_diff_par " + MampPar + " " + SampPar + " " + diff_par + " 1 0 "
    os.system(call_str)
    call_str = "atm_mod_2d " + UNWlks + " " + HGTSIM + " " + CORMASK + " " + diff_par + " " + " - 0 a0 a1 sigma sigma_h s1"
    os.system(call_str)
    call_str = "atm_sim_2d " + diff_par + " " +  HGTSIM + " a0 a1  " + ATM_PHASE
    os.system(call_str)
    call_str = "sub_phase " + UNWlks + " " + ATM_PHASE + " " + diff_par + " " + ATMCOR_UNW + " 0 0 0 " 
    os.system(call_str)
    call_str = 'rasrmg ' + ATMCOR_UNW + ' ' + Mamp + ' ' + nWidth + ' - - - - - - - - - - ' 
    os.system(call_str)
    
    print("Correct atmospheric phase is done!")
    sys.exit(1)

if __name__ == '__main__':
    main(sys.argv[:])
