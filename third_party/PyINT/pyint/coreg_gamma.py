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
import argparse
import re

from pyint import _utils as ut


def _resolve_existing_master_date(slc_dir, requested_date):
    if not os.path.isdir(slc_dir):
        return requested_date
    existing_dates = sorted(
        item for item in os.listdir(slc_dir)
        if re.match(r'^\d{8}$', item) and os.path.isdir(os.path.join(slc_dir, item))
    )
    if not existing_dates:
        return requested_date
    if requested_date in existing_dates:
        return requested_date
    if not requested_date or not re.match(r'^\d{8}$', str(requested_date)):
        resolved = existing_dates[0]
    else:
        resolved = min(existing_dates, key=lambda item: abs(int(item) - int(requested_date)))
    print('masterDate %s not found; using existing SLC date: %s' % (requested_date, resolved))
    return resolved


def _run_or_raise(call_str, stage):
    rc = os.system(call_str)
    if rc != 0:
        raise RuntimeError('%s failed with rc=%s: %s' % (stage, rc, call_str))
    return rc


def _safe_remove(path):
    if path and os.path.exists(path):
        os.remove(path)


def _copy_file(src, dst):
    ut.copy_file(src, dst)


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Coregister SM mode SLC to a reference SLC image using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName', help='Name of project.')
    parser.add_argument('sdate', help='date of the slave SLC image. [mater date is read from template]')
    inps = parser.parse_args()
    return inps


INTRODUCTION = '''
-------------------------------------------------------------------  
   Coregister SM mode SLC to a reference SLC image using GAMMA.
   [The reference date or master date will be read from the template file.]
'''

EXAMPLE = """Usage:
  
  coreg_gamma.py projectName Sdate
  
  coreg_gamma.py PacayaT163TsxHhA 20150102
------------------------------------------------------------------- 
"""
        
    
def main(argv):
    
    inps = cmdLineParse() 
    projectName = inps.projectName
    Sdate = inps.sdate
    
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    
    slcDir     = scratchDir + '/' + projectName + "/SLC"
    rslcDir     = scratchDir + '/' + projectName + "/RSLC"
    if not os.path.isdir(rslcDir): os.mkdir(rslcDir)
    #workDir    = processDir + '/' + igramDir   
    workDir = rslcDir + '/' + Sdate
    if not os.path.isdir(workDir): os.mkdir(workDir)
    
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    Mdate = _resolve_existing_master_date(slcDir, templateDict['masterDate'])
    IFGPair = Mdate + '-' + Sdate
    
    demDir = scratchDir + '/' + projectName + '/DEM' 
    
    SslcDir     = slcDir  + '/' + Sdate  
    Samp = slcDir  + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SampPar = slcDir  + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    Sramp = rslcDir  + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp'
    SrampPar = rslcDir  + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp.par'
    
    Sslc = slcDir  + '/' + Sdate + '/' + Sdate + '.slc'
    SslcPar = slcDir  + '/' + Sdate + '/' + Sdate + '.slc.par'
    
    SrslcDir = rslcDir + "/" + Sdate

    Srslc    = SrslcDir + "/" + Sdate + ".rslc"
    SrslcPar = SrslcDir + "/" + Sdate + ".rslc.par"
    
    Srslc0    = SrslcDir + "/" + Sdate + ".rslc0"
    SrslcPar0 = SrslcDir + "/" + Sdate + ".rslc0.par"

    if Mdate == Sdate:
        Mslc0 = slcDir + '/' + Mdate + '/' + Mdate + '.slc'
        MslcPar0 = slcDir + '/' + Mdate + '/' + Mdate + '.slc.par'
        ut.copy_file(Mslc0, Srslc)
        ut.copy_file(MslcPar0, SrslcPar)
        call_str = 'multi_look ' + Srslc + ' ' + SrslcPar + ' ' + Sramp + ' ' + SrampPar + ' ' + rlks + ' ' + azlks
        _run_or_raise(call_str, 'master_multi_look_rslc')
        print('Master date: copy SLC to RSLC done.')
        sys.exit(0)

#####################################################
## copy all of the master files into slave folder for parallel processing
    remove_file = []
    Mslc0 = slcDir  + '/' + Mdate + '/' + Mdate + '.slc'
    MslcPar0 = slcDir  + '/' + Mdate + '/' + Mdate + '.slc.par'
    Mamp0 = slcDir  + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar0 = slcDir  + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    Mamp0_dem = demDir + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar0_dem = demDir + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    HGTSIM0      = demDir + '/' + Mdate + '_' + rlks + 'rlks.rdc.dem'
    if not os.path.isfile(HGTSIM0):
        call_str = 'generate_rdc_dem.py ' + projectName
        _run_or_raise(call_str, 'generate_rdc_dem')
    if not os.path.isfile(Mamp0) and os.path.isfile(Mamp0_dem):
        ut.copy_file(Mamp0_dem, Mamp0)
    if not os.path.isfile(MampPar0) and os.path.isfile(MampPar0_dem):
        ut.copy_file(MampPar0_dem, MampPar0)
    if not os.path.isfile(Mamp0) or not os.path.isfile(MampPar0):
        call_str = 'multi_look ' + Mslc0 + ' ' + MslcPar0 + ' ' + Mamp0 + ' ' + MampPar0 + ' ' + rlks + ' ' + azlks
        _run_or_raise(call_str, 'master_multi_look')
    if not os.path.isfile(Samp) or not os.path.isfile(SampPar):
        call_str = 'multi_look ' + Sslc + ' ' + SslcPar + ' ' + Samp + ' ' + SampPar + ' ' + rlks + ' ' + azlks
        _run_or_raise(call_str, 'slave_multi_look')
   
    Mslc = slcDir  + '/' + Sdate + '/' + Mdate + '.slc'
    MslcPar = slcDir  + '/' + Sdate + '/' + Mdate + '.slc.par'
    Mamp = slcDir  + '/' + Sdate + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MampPar = slcDir  + '/' + Sdate + '/' + Mdate + '_' + rlks + 'rlks.amp.par'
    HGTSIM  = slcDir  + '/' + Sdate + '/'  + Mdate + '_' + rlks + 'rlks.rdc.dem'
    
    ut.copy_file(HGTSIM0,HGTSIM)
    ut.copy_file(Mslc0,Mslc)
    ut.copy_file(MslcPar0,MslcPar)
    ut.copy_file(Mamp0,Mamp)
    ut.copy_file(MampPar0,MampPar)
    
#######################################################    
#   define process files #
    lt0 = workDir + "/lt0" 
    lt1 = workDir + "/lt1"
    mli0 = workDir + "/mli0" 
    diff0 = workDir + "/diff0" 
    offs0 = workDir + "/offs0"
    snr0 = workDir + "/snr0"
    offsets0 = workDir + "/offsets0"
    coffs0 = workDir + "/coffs0"
    coffsets0 = workDir + "/coffsets0"
    off = workDir + "/" + IFGPair + ".off"
    offs = workDir + "/offs"
    snr = workDir + "/snr"
    offsets = workDir + "/offsets"
    coffs = workDir + "/coffs"
    coffsets = workDir + "/coffsets"
    OFFSTD = workDir + "/" + IFGPair + ".off_std"
##############################################
    
    call_str = "rdc_trans " + MampPar + " " + HGTSIM + " " + SampPar + " " + lt0
    _run_or_raise(call_str, 'rdc_trans')

    width_Mamp = ut.read_gamma_par(MampPar, 'read', 'range_samples')
    width_Samp = ut.read_gamma_par(SampPar, 'read', 'range_samples')
    line_Samp = ut.read_gamma_par(SampPar, 'read', 'azimuth_lines')

    call_str = "geocode " + lt0 + " " + Mamp + " " + width_Mamp + " " + mli0 + " " + width_Samp + " " + line_Samp + " 2 0"
    _run_or_raise(call_str, 'geocode_lt0')

    call_str = "create_diff_par " + SampPar + " - " + diff0 + " 1 0"
    _run_or_raise(call_str, 'create_diff_par')

    try:
        call_str = "init_offsetm " + mli0 + " " + Samp + " " + diff0 + " 1 1"
        _run_or_raise(call_str, 'init_offsetm')

        call_str = "offset_pwrm " + mli0 + " " + Samp + " " + diff0 + " " + offs0 + " " + snr0 + " 256 256 " + offsets0 + " 2 32 32"
        _run_or_raise(call_str, 'offset_pwrm')
      
        call_str = "offset_fitm " + offs0 + " " + snr0 + " " + diff0 + " " + coffs0 + " " + coffsets0 + " - 4"
        _run_or_raise(call_str, 'offset_fitm')

        call_str = "gc_map_fine " + lt0 + " " + width_Mamp + " " + diff0 + " " + lt1
        _run_or_raise(call_str, 'gc_map_fine')
    except RuntimeError as exc:
        print('ERROR: initial DEM-assisted offset refinement failed; aborting coregistration instead of falling back')
        print(str(exc))
        raise
    
    
    call_str = "SLC_interp_lt " + Sslc + " " + MslcPar + " " + SslcPar + " " + lt1 + " " + MampPar + " " + SampPar + " - " + Srslc0 + " " + SrslcPar0
    _run_or_raise(call_str, 'SLC_interp_lt_initial')


############################################     Refinement     ############################################
    try:
        call_str = "create_offset " + MslcPar + " " + SrslcPar0 + " " + off + " 1 - - 0"
        _run_or_raise(call_str, 'create_offset')

        call_str = "offset_pwr " + Mslc + " " + Srslc0 + " " + MslcPar + " " + SrslcPar0 + " " + off + " " + offs + " " + snr + " " + templateDict['rwin4cor'] + " " + templateDict['azwin4cor'] + " " + offsets + " 2 " + templateDict['rsample4cor'] + " " + templateDict['azsample4cor']
        _run_or_raise(call_str, 'offset_pwr_coarse')

        call_str = "offset_fit "  + offs + " " + snr + " " + off + " " + coffs + " " + coffsets + " - 3" 
        _run_or_raise(call_str, 'offset_fit_coarse')
        
        rfwin4cor = str(int(1/2*int(templateDict['rwin4cor'])))
        azfwin4cor = str(int(1/2*int(templateDict['azwin4cor'])))
        
        rfsample4cor = str(2*int(templateDict['rsample4cor']))
        azfsample4cor = str(2*int(templateDict['azsample4cor']))
        
        call_str = "offset_pwr " + Mslc + " " + Srslc0 + " " + MslcPar + " " + SrslcPar0 + " " + off + " " + offs + " " + snr + " " + rfwin4cor + " " + azfwin4cor + " " + offsets + " 2 " + rfsample4cor + " " + azfsample4cor
        _run_or_raise(call_str, 'offset_pwr_fine')
        
        call_str = "offset_fit "  + offs + " " + snr + " " + off + " " + coffs + " " + coffsets + " - 3 >" + OFFSTD 
        _run_or_raise(call_str, 'offset_fit_fine')
        
############################################     Resampling     ############################################    

        call_str = "SLC_interp_lt " + Sslc + " " + MslcPar + " " + SslcPar + " " + lt1 + " " + MampPar + " " + SampPar + " " + off + " " + Srslc + " " + SrslcPar
        _run_or_raise(call_str, 'SLC_interp_lt_final')
    except RuntimeError as exc:
        print('ERROR: offset refinement failed; aborting coregistration instead of promoting the provisional RSLC')
        print(str(exc))
        raise

    call_str = 'multi_look ' + Srslc + ' ' + SrslcPar + ' ' + Sramp + ' ' + SrampPar + ' ' + rlks + ' ' + azlks
    _run_or_raise(call_str, 'multi_look_rslc')

    nWidth = ut.read_gamma_par(SrampPar, 'read', 'range_samples')
    call_str = 'raspwr ' + Sramp + ' ' + nWidth 
    _run_or_raise(call_str, 'raspwr')

    for path in (
        lt0, lt1, mli0, diff0, offs0, snr0, offsets0, coffs0, coffsets0,
        off, offs, snr, offsets, coffs, coffsets, Srslc0, SrslcPar0,
        Mslc, MslcPar, Mamp, MampPar, HGTSIM,
    ):
        _safe_remove(path)

    print("Coregistration with DEM is done!")
 
    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])
