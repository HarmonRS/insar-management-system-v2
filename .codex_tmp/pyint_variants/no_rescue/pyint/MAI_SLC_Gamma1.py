#! /usr/bin/env python
#'''
##################################################################################
#                                                                                #
#            Author:   Wei Chen                                              #
#            Email :   chenweicug@gmail.com                                          #
#            Date  :   May,25th 2021                                            #
#                                                                                #
#         Split beam of SLC: backward and forward SLC image generation           #
#                                                                                #
##################################################################################
#'''

import numpy as np
import os
import sys  
import subprocess
import getopt
import time
import glob
import argparse

from pyint import _utils as ut


def get_s1_date(raw_file):
    file0 = os.path.basename(raw_file)
    date = file0[17:25]
    return date

def get_satellite(raw_file):
    if 'S1A_IW_SLC_' in raw_file:
        s0 = 'A'
    else:
        s0 = 'B'
    
    return s0
        

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Generate SLC from Sentinel-1 raw data with orbit correction using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName', help='project name. e.g., ChangningT55S1A')
    parser.add_argument('Mdate',help='date to be processed. e.g., 20180101')
    parser.add_argument('Sdate',help='date to be processed. e.g., 20180113')
       
    inps = parser.parse_args()

    return inps


INTRODUCTION = '''
-------------------------------------------------------------------  

   Split beam of original SLC to generate sub-aperture SLC: backward- and forward-SLCs
'''

EXAMPLE = """Usage:
  
  MAI_SLC_Gamma.py projectName Mdate Sdate
  
  MAI_SLC_Gamma.py ChangningT55S1A 20180517 20180529
  
------------------------------------------------------------------- 
"""

def main(argv):
    inps = cmdLineParse() 
    projectName = inps.projectName
    Mdate = inps.Mdate
    Sdate = inps.Sdate
    scratchDir = os.getenv('SCRATCHDIR')
    projectDir = scratchDir + '/' + projectName 
    
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    
    slcDir =  projectDir + '/SLC'
    down_dir = projectDir + '/DOWNLOAD'
    workDir    = projectDir + '/MAI' 

    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    Mdate =  templateDict['masterDate']
    coregCoarse = templateDict['Coreg_Coarse']

    if not os.path.isdir(workDir):
        call_str="mkdir " + workDir
        os.system(call_str)
    if 'Squint_MAI' in templateDict: Squint = templateContents['Squint_MAI']
    else: Squint = '0.5'
    
    SslcDir = slcDir + "/" + Sdate
    MslcDir = slcDir + "/" + Mdate

    MslcImg = MslcDir + "/" + Mdate + ".slc"
    MslcPar = MslcDir + "/" + Mdate + ".slc.par"
    SslcImg = SslcDir + "/" + Sdate + ".slc"
    SslcPar = SslcDir + "/" + Sdate + ".slc.par"

# split slcs

    MFslcImg = workDir + "/" + Mdate + ".F.slc"
    MFslcPar = workDir + "/" + Mdate + ".F.slc.par"
    SFslcImg = workDir + "/" + Sdate + ".F.slc"
    SFslcPar = workDir + "/" + Sdate + ".F.slc.par"

    MBslcImg = workDir + "/" + Mdate + ".B.slc"
    MBslcPar = workDir + "/" + Mdate + ".B.slc.par"
    SBslcImg = workDir + "/" + Sdate + ".B.slc"
    SBslcPar = workDir + "/" + Sdate + ".B.slc.par"
    print(MFslcImg + " "+ MslcImg)
    
# Multi-aperture processing

    call_str = 'sbi_filt '+ MslcImg + ' ' + MslcPar + ' '+SslcPar + ' ' + MFslcImg + ' '+ MFslcPar + ' ' + MBslcImg + ' ' + MBslcPar + ' ' + Squint
    os.system(call_str)
    print(call_str)    

    call_str = 'sbi_filt '+ SslcImg + ' ' + SslcPar + ' '+MslcPar + ' ' + SFslcImg + ' '+ SFslcPar + ' ' + SBslcImg + ' ' + SBslcPar + ' ' + Squint
    os.system(call_str)
    
    sys.exit(1)

if __name__ == '__main__':
    main(sys.argv[:])
    
   
    
 

