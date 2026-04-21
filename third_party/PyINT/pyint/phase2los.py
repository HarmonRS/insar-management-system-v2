#!/usr/bin/env python
#! /usr/bin/env python
#################################################################
###  This program is part of PyINT                            ### 
###  Copy Right (c): 2019, Wei Chen                           ###  
###  Author: Wei Chen                                      ###                                                          
###  Contact : chenweicug@126.com                             ###  
#################################################################
import numpy as np
import os
import sys  
import time
import glob
import argparse

import subprocess
from pyint import _utils as ut

INTRODUCTION = '''
-------------------------------------------------------------------  
       convert interferogram to los displacement and get the inc angle and azi angle.
   
'''

EXAMPLE = '''
    Usage: 
            phase2los.py projectName Mdate Sdate
            phase2los.py PacayaT163TsxHhA 20150102 20150601
-------------------------------------------------------------------  
'''

def cmdLineParse():
    parser = argparse.ArgumentParser(description='convert interferogram to los displacement and get the inc angle and azi angle.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)
    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('Mdate',help='Master date.')
    parser.add_argument('Sdate',help='Slave date.')
    
    inps = parser.parse_args()
    return inps



def main(argv):

    inps = cmdLineParse()    
    projectName = inps.projectName
    Mdate = inps.Mdate
    Sdate=  inps.Sdate
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    satelite = templateDict['satelite']
    rlks = templateDict['range_looks']

    projectDir = scratchDir + '/' + projectName    
    ifgDir = projectDir + '/ifgrams'
    simDir = scratchDir + '/' + projectName + "/DEM" 
    losDir = projectDir + '/LosResult'
    slcDir = projectDir + '/SLC'


    if not os.path.isdir(losDir): os.mkdir(losDir)
    
    Pair = Mdate + '-' + Sdate
    workDir = losDir + '/' + Pair
    if not os.path.isdir(workDir): os.mkdir(workDir)
    workdir = ifgDir + '/' + Pair
    UTMDEMpar   = simDir + '/'+ Mdate + '_'+ rlks + 'rlks.utm.dem.par'
    GeoUNW     =  workdir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.unw'
    GeoATMCOR_UNW = workDir + '/geo_' + Pair + '_' + rlks + 'rlks.diff_filt.atmcor.unw'
    call_str = 'data2geotiff ' + UTMDEMpar + ' ' + GeoATMCOR_UNW + ' 2 ' + GeoATMCOR_UNW +'.tif'
    os.system(call_str)
    call_str = 'data2geotiff ' + UTMDEMpar + ' ' + GeoUNW + ' 2 ' + GeoUNW +'.tif'
    os.system(call_str)
    call_str = 'gdal_translate -of GSBG ' + ' ' + GeoUNW +'.tif' + ' ' + workDir + '/' + GeoUNW  + '.grd'
    os.system(call_str)
    call_str = 'gdal_translate -of GSBG ' + ' ' + GeoATMCOR_UNW +'.tif' + ' ' + workDir + '/' + GeoATMCOR_UNW +'.grd'
    os.system(call_str)
    if satelite == 'CSK':
      wavelength = 0.0312283810417
    elif satelite == 'TSX':
      wavelength = 0.03106657823461874
    elif satelite == 'S1A':
      wavelength = 0.0554657647
    elif satelite == 'ALOS2': 
      wavelength = 0.2424525 
    elif satelite == 'ALOS':
      wavelength = 0.2360571
    elif satelite == 'ENVISAT':
      wavelength = 0.056
    call_str = 'grdmath ' + workDir + '/' + GeoUNW  + '.grd' + str(wavelength) + ' 1 3.141592653589 MUL DIV MUL = ' +  workDir + '/los.grd'
    os.system(call_str)
    call_str = 'grdmath ' + workDir + '/' + GeoATMCOR_UNW +'.grd' + str(wavelength) + ' 1 3.141592653589 MUL DIV MUL = ' +  workDir + '/los_atmcor.grd'
    os.system(call_str)
    print('the los displacement of ' + Pair + " has successfully done")
    print ("#####################run data2inc_azi###################")
    Mslcpar = slcDir  + '/' + Mdate + '/' + Mdate + '.slc.par' 
    UTMDEM   = simDir + '/'+ Mdate + '_'+ rlks + 'rlks.utm.dem'   
    OFF = workdir + '/' +  Pair +'_' + rlks + 'rlks.off' 
    os.chdir(workDir)
    call_str = 'data2inc_azi ' + Mslcpar + ' ' + OFF + ' ' + UTMDEMpar + ' ' + UTMDEM
    os.system(call_str)

if __name__ == '__main__':
    main(sys.argv[:])

