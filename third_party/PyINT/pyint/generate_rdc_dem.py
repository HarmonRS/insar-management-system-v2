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


def _run_or_warn(call_str, stage):
    rc = os.system(call_str)
    if rc != 0:
        print('WARNING: %s returned rc=%s; continuing with subsequent GAMMA refinement: %s' % (stage, rc, call_str))
    return rc


def _remove_files(paths):
    for path in paths:
        if os.path.isfile(path):
            os.remove(path)

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Generate radar-coordinates based DEM.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName', help='Name of project.')
    inps = parser.parse_args()
    return inps


INTRODUCTION = '''
-------------------------------------------------------------------  

   Generate radar-coordinates based DEM.
   [Geo-coordinates DEM can be downloaded automatically if not provided.]
'''

EXAMPLE = """Usage:
  
  generate_rdc_dem.py projectName
  
------------------------------------------------------------------- 
"""
    
def main(argv):
    
    inps = cmdLineParse() 
    projectName = inps.projectName

    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)

    Mdate = templateDict['masterDate']

    DEMDir = os.getenv('DEMDIR')
    
    processDir = scratchDir + '/' + projectName + "/ifgrams"
    slcDir     = scratchDir + '/' + projectName + "/SLC"
    Mdate = _resolve_existing_master_date(slcDir, Mdate)
    
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']

    if not os.path.isdir(processDir):
        os.makedirs(processDir)
        
    simDir = scratchDir + '/' + projectName + "/DEM" 
    workDir = simDir
    
    if 'DEM' in templateDict: 
        dem = templateDict['DEM'] 
        if not os.path.isfile(dem):
            dem = DEMDir + '/' + projectName + '/' + projectName + '.dem' 
            with open(templateFile, 'a') as stream:
                stream.write('DEM= ' + dem + '\n')
            templateDict['DEM'] = dem
    else: 
        dem = DEMDir + '/' + projectName + '/' + projectName + '.dem'
        with open(templateFile, 'a') as stream:
            stream.write('DEM = ' + dem + '\n')
    
    demPar = dem + ".par"
    
    if not os.path.isfile(dem):
        call_str = 'makedem_pyint.py ' + projectName
        _run_or_raise(call_str, 'makedem_pyint')
    
# Parameter setting for simPhase
    latovrSimphase = templateDict['dem_lat_ovr']
    lonovrSimphase = templateDict['dem_lon_ovr']
    rposSimphase = templateDict['Simphase_rpos']   
    azposSimphase = templateDict['Simphase_azpos']  
    rwinSimphase = templateDict['Simphase_rwin'] 
    azwinSimphase = templateDict['Simphase_azwin'] 
    #rwinSimphase = '128'
    #azwinSimphase = '128'

    threshSimphase = templateDict['Simphase_thresh']

#  Definition of file
    MslcDir     = slcDir  + '/' + Mdate     
    MslcImg     = MslcDir + '/' + Mdate + '.slc'
    MslcPar     = MslcDir + '/' + Mdate + '.slc.par'
    OFFSTD = workDir + '/' + Mdate + '_dem.off_std'
    

    BLANK       = workDir + '/' + Mdate + '.blk'
    MamprlksImg  = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp'
    MamprlksPar  = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp.par'


    UTMDEMpar   = simDir + '/'+ Mdate + '_'+ rlks + 'rlks.utm.dem.par'
    UTMDEM      = simDir + '/' + Mdate + '_'+ rlks + 'rlks.utm.dem'
    UTM2RDC     = simDir + '/' + Mdate + '_'+ rlks + 'rlks.utm_to_rdc0'
    SIMSARUTM   = simDir + '/' + Mdate + '_'+ rlks + 'rlks.sim_sar_utm'
    PIX         = simDir + '/' + Mdate + '_'+ rlks + 'rlks.pix'
    LSMAP       = simDir + '/' + Mdate + '_'+ rlks + 'rlks.ls_map'
    SIMSARRDC   = simDir + '/' + Mdate + '_'+ rlks + 'rlks.sim_sar_rdc'
    SIMDIFFpar  = simDir + '/' + Mdate + '_'+ rlks + 'rlks.diff_par'
    SIMOFFS     = simDir + '/' + Mdate + '_'+ rlks + 'rlks.offs'
    SIMSNR      = simDir + '/' + Mdate + '_'+ rlks + 'rlks.snr'
    SIMOFFSET   = simDir + '/' + Mdate + '_'+ rlks + 'rlks.offset'
    SIMCOFF     = simDir + '/' + Mdate + '_'+ rlks + 'rlks.coff'
    SIMCOFFSETS = simDir + '/' + Mdate + '_'+ rlks + 'rlks.coffsets'
    UTMTORDC    = simDir + '/' + Mdate + '_'+ rlks + 'rlks.UTM_TO_RDC'
    HGTSIM      = simDir + '/' + Mdate + '_'+ rlks + 'rlks.rdc.dem'
      
    if not (os.path.isdir(simDir)):
        os.makedirs(simDir)
        
    ut.createBlankFile(BLANK)

### remove DEM look up table if it existed for considering gamma overlapping

    _remove_files([UTMDEM, UTMDEMpar, UTM2RDC, SIMSARUTM, PIX, LSMAP])

    nWidthUTMDEM0 = ut.read_gamma_par(demPar, 'read', 'width')
    DateFormat = ut.read_gamma_par(demPar, 'read', 'data_format:')
    
    if DateFormat == 'INTEGER*2':
        DF_type = '4'
    else:
        DF_type = '2'
    
        
    tmp_dem = dem + '_tmp'
    
    if not os.path.isfile(tmp_dem):
        call_str = 'replace_values ' + dem + ' -32767 0 ' + tmp_dem + ' ' + nWidthUTMDEM0 + ' 2 ' + DF_type
        _run_or_raise(call_str, 'replace_values_dem_voids')
        call_str = 'cp ' + tmp_dem + ' ' + dem
        _run_or_raise(call_str, 'copy_dem_without_voids')

    call_str = "multi_look " + MslcImg + " " + MslcPar + " " + MamprlksImg + " " + MamprlksPar + " " + rlks + " " + azlks
    _run_or_raise(call_str, 'multi_look_master_for_dem')

    def run_gc_map1(stage, lat_ovr, lon_ovr):
        call = 'gc_map1 ' + MamprlksPar + ' ' + '-' + ' ' + demPar + ' ' + dem + ' ' + UTMDEMpar + ' ' + UTMDEM + ' ' + UTM2RDC + ' ' + lat_ovr + ' ' + lon_ovr + ' ' + SIMSARUTM + ' - - - - ' + PIX + ' ' + LSMAP + ' - 3 128'
        _run_or_raise(call, stage)

    run_gc_map1('gc_map1_initial_dem_segment', latovrSimphase, lonovrSimphase)

    nWidthUTMDEM = ut.read_gamma_par(UTMDEMpar, 'read', 'width')
    nLinePWR1 = ut.read_gamma_par(MamprlksPar, 'read', 'azimuth_lines')
    nWidth = ut.read_gamma_par(MamprlksPar, 'read', 'range_samples')
   
    # 30 m DEM grids can be much sparser than LT-1 multi-look radar pixels.
    # Keep this in GAMMA by widening geocode's search radius for RDC filling.
    call_str = 'geocode ' + UTM2RDC + ' ' + SIMSARUTM + ' ' + nWidthUTMDEM + ' ' + SIMSARRDC + ' ' + nWidth + ' ' + nLinePWR1 + ' 0 0 - - 2 64 1'
    _run_or_raise(call_str, 'geocode_sim_sar_to_rdc')

    call_str = 'create_diff_par ' + MamprlksPar + ' ' + MamprlksPar + ' ' + SIMDIFFpar + ' 1 < ' + BLANK
    _run_or_raise(call_str, 'create_diff_par_sim')

    call_str = 'init_offsetm ' + SIMSARRDC + ' ' + MamprlksImg + ' ' + SIMDIFFpar + ' 2 2 ' + rposSimphase + ' ' + azposSimphase #+ ' - - - 512'
    _run_or_warn(call_str, 'init_offsetm_sim')

    call_str = 'offset_pwrm ' + SIMSARRDC + ' ' + MamprlksImg + ' ' + SIMDIFFpar + ' ' + SIMOFFS + ' ' + SIMSNR + ' ' + rwinSimphase + ' ' + azwinSimphase + ' ' + SIMOFFSET #+ ' - 128 128 ' + threshSimphase 
    _run_or_raise(call_str, 'offset_pwrm_sim')

    call_str = 'offset_fitm ' + SIMOFFS + ' ' + SIMSNR + ' ' + SIMDIFFpar + ' ' + SIMCOFF + ' ' + SIMCOFFSETS + ' - > ' + OFFSTD
    _run_or_raise(call_str, 'offset_fitm_sim')

    call_str = 'gc_map_fine ' + UTM2RDC + ' ' + nWidthUTMDEM + ' ' + SIMDIFFpar + ' ' + UTMTORDC + ' 1'
    #print(call_str)
    _run_or_raise(call_str, 'gc_map_fine')

    call_str = 'geocode ' + UTMTORDC + ' ' + UTMDEM + ' ' + nWidthUTMDEM + ' ' + HGTSIM + ' ' + nWidth + ' ' + nLinePWR1 + ' 0 0 - - 2 64 1'
    _run_or_raise(call_str, 'geocode_dem_to_rdc')


    required_outputs = [UTMDEMpar, UTMDEM, UTMTORDC, HGTSIM]
    missing = [path for path in required_outputs if not os.path.isfile(path)]
    if missing:
        raise RuntimeError('generate_rdc_dem is missing required outputs: ' + ', '.join(missing))

    print("Create DEM in Radar Coordinates is done!")

    sys.exit(0)

if __name__ == '__main__':
    main(sys.argv[:])
