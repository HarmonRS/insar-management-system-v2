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


def _run_or_raise(call_str, stage):
    rc = os.system(call_str)
    if rc != 0:
        raise RuntimeError('%s failed with rc=%s: %s' % (stage, rc, call_str))
    return rc


INTRODUCTION = '''
-------------------------------------------------------------------  
       Generate differential interferogram image from SLC using GAMMA.
   
'''

EXAMPLE = '''
    Usage: 
            diff_gamma.py projectName Mdate Sdate
            diff_gamma.py PacayaT163TsxHhA 20150102 20150601
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Coregister all of the SLCs to the reference SAR image using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('Mdate',help='Master date.')
    parser.add_argument('Sdate',help='Slave date.')
    
    inps = parser.parse_args()
    return inps


def main(argv):
    
    start_time = time.time()
    inps = cmdLineParse() 
    Mdate = inps.Mdate
    Sdate = inps.Sdate
    
    projectName = inps.projectName
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    masterDate = templateDict['masterDate']
    
    projectDir = scratchDir + '/' + projectName 
    demDir    = scratchDir + '/' + projectName  + '/DEM'
    
    slcDir    = scratchDir + '/' + projectName + '/SLC'
    rslcDir   = scratchDir + '/' + projectName + '/RSLC' 
    ifgDir = projectDir + '/ifgrams'
    if not os.path.isdir(ifgDir): os.mkdir(ifgDir)
    
    Pair = Mdate + '-' + Sdate
    workDir = ifgDir + '/' + Pair
    if not os.path.isdir(workDir): os.mkdir(workDir)
    
    #######################################################################
    Mamp     = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar  = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    Samp     = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SampPar  = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    
    Mrslc    = rslcDir  + '/' + Mdate + '/' + Mdate + '.rslc'
    MrslcPar = rslcDir  + '/' + Mdate + '/' + Mdate + '.rslc.par'
    Srslc    = rslcDir  + '/' + Sdate + '/' + Sdate + '.rslc'
    SrslcPar = rslcDir  + '/' + Sdate + '/' + Sdate + '.rslc.par'
    
    HGT      = demDir + '/' + masterDate + '_' + rlks + 'rlks.rdc.dem'
    
    MasterPar = rslcDir  + '/' + masterDate + '/' + masterDate + '.rslc.par'
    
    ################# copy file for parallel processing ##########################
    #Mamp     =   workDir + '/' + Mdate + '_' + rlks + 'rlks.amp'
    #MampPar  =   workDir + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    #Samp     =   workDir + '/' + Sdate + '_' + rlks + 'rlks.amp'
    #SampPar  =   workDir + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    
    #if not templateDict['diff_all_parallel'] == '1':   
        
    #    Mrslc    =   workDir + '/' + Mdate + '.rslc'
    #    MrslcPar =   workDir + '/' + Mdate + '.rslc.par'
    #    Srslc    =   workDir + '/' + Sdate + '.rslc'
    #    SrslcPar =   workDir + '/' + Sdate + '.rslc.par'   
    #    ut.copy_file(Mrslc0,Mrslc)
    #    ut.copy_file(MrslcPar0,MrslcPar)
    #    ut.copy_file(Srslc0,Srslc)
    #    ut.copy_file(SrslcPar0,SrslcPar)   
        
    #else:       
        
    #    Mrslc    =   Mrslc0
    #    MrslcPar =   MrslcPar0
    #    Srslc    =   Srslc0
    #    SrslcPar =   SrslcPar0
    #    HGT = HGT0
    #    MasterPar = MasterPar0
    
    #ut.copy_file(Mamp0,Mamp)
    #ut.copy_file(MampPar0,MampPar)
    #ut.copy_file(Samp0,Samp)
    #ut.copy_file(SampPar0,SampPar)
    
    #ut.copy_file(HGT0,HGT)
    #ut.copy_file(MasterPar0,MasterPar)
    
    ############################################################################    
        
    OFF = workDir + '/' +  Pair +'_' + rlks + 'rlks.off'   
    call_str = 'create_offset '+ MrslcPar + ' ' + SrslcPar + ' ' + OFF + ' 1 ' + rlks + ' ' + azlks +  ' 0'
    _run_or_raise(call_str, 'create_offset')
   
    SIM_UNW = workDir + '/' +  Pair + '.sim_unw'
    call_str = 'phase_sim_orb ' + MrslcPar + ' ' + SrslcPar + ' ' + OFF + ' ' + HGT + ' ' + SIM_UNW + ' ' + MasterPar + ' - - 1 1' 
    _run_or_raise(call_str, 'phase_sim_orb')
    
    DIFF_IFG = workDir + '/' +  Pair + '_' + rlks + 'rlks.diff'
    call_str = 'SLC_diff_intf ' + Mrslc + ' ' + Srslc + ' ' + MrslcPar + ' ' + SrslcPar + ' ' + OFF + ' ' + SIM_UNW + ' ' + DIFF_IFG + ' ' + rlks + ' ' + azlks + ' ' + templateDict['Igram_Spsflg'] + ' ' + templateDict['Igram_Azfflg'] + ' - 1 1'
    _run_or_raise(call_str, 'SLC_diff_intf')
    
    ##### filtering process & coherence estimation ###########
    DIFFFILT = workDir + '/' +  Pair + '_' + rlks + 'rlks.diff_filt'
    COHFILT = workDir + '/' +  Pair + '_' + rlks + 'rlks.diff_filt.cor'
    
    nWIDTH = ut.read_gamma_par(OFF, 'read', 'interferogram_width')
    call_str = 'adf ' + DIFF_IFG + ' ' + DIFFFILT + ' ' + COHFILT + ' ' + nWIDTH +  ' ' + templateDict['adf_alpha'] + ' - ' + templateDict['Igram_Cor_Win']
    _run_or_raise(call_str, 'adf')
    
    ################# coherence estimation #####################
    call_str = 'cc_wave ' + DIFFFILT + ' ' + Mamp + ' ' + Samp + ' ' + COHFILT + ' ' + nWIDTH + ' ' + templateDict['Igram_Cor_rwin'] + ' ' + templateDict['Igram_Cor_awin']
    _run_or_raise(call_str, 'cc_wave')
    
    
    ################ save images #####################
    call_str = 'rasmph_pwr ' +  DIFFFILT + ' ' + Mamp + ' ' + nWIDTH + ' - - - - - - - - - ' + COHFILT + ' - 0.1'
    _run_or_raise(call_str, 'rasmph_pwr_diff_filt')
    
    call_str = 'rasmph_pwr ' +  DIFF_IFG + ' ' + Mamp + ' ' + nWIDTH + ' - - - - - - - - - ' + COHFILT + ' - 0.1'
    _run_or_raise(call_str, 'rasmph_pwr_diff')
    
    call_str = 'rasdt_pwr ' + COHFILT + ' ' + Mamp + ' ' + nWIDTH + ' 1 0 1 1 0.1 1.0 1 ' 
    _run_or_raise(call_str, 'rasdt_pwr_coh')
    
    #os.remove(Mamp)
    #os.remove(MampPar)
    #os.remove(Samp)
    #os.remove(SampPar)
    
    #if not templateDict['diff_all_parallel'] == '1':   
    #    if os.path.isfile(Mrslc): os.remove(Mrslc)
    #    if os.path.isfile(Srslc):os.remove(Srslc)
    
    #    if os.path.isfile(HGT):os.remove(HGT)
    
    print("Subtraction of topography and flattening phase is done!")
    ut.print_process_time(start_time, time.time())
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])
