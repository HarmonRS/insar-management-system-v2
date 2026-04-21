#! /usr/bin/env python
#'''
##################################################################################
#                                                                                #
#            Author:   Yun-Meng Cao                                              #
#            Email :   ymcmrs@gmail.com                                          #
#            Date  :   February, 2017                                            #
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
from pyint import _utils as ut
import argparse



INTRODUCTION = '''
------------------------------------------------------------------------------------------  
       Split beam of original SLC to generate sub-aperture SLC: backward- and forward-SLCs.
   
'''

EXAMPLE = '''
    Usage: 
            MAI_SLC_gamma.py projectName Mdate Sdate
            MAI_SLC_gamma.py PacayaT163TsxHhA 20150102 20150601
------------------------------------------------------------------------------------------  
'''




def check_variable_name(path):
    s=path.split("/")[0]
    if len(s)>0 and s[0]=="$":
        p0=os.getenv(s[1:])
        path=path.replace(path.split("/")[0],p0)
    return path

def read_template(File, delimiter='='):
    '''Reads the template file into a python dictionary structure.
    Input : string, full path to the template file
    Output: dictionary, pysar template content
    Example:
        tmpl = read_template(KyushuT424F610_640AlosA.template)
        tmpl = read_template(R1_54014_ST5_L0_F898.000.pi, ':')
    '''
    template_dict = {}
    for line in open(File):
        line = line.strip()
        c = [i.strip() for i in line.split(delimiter, 1)]  #split on the 1st occurrence of delimiter
        if len(c) < 2 or line.startswith('%') or line.startswith('#'):
            next #ignore commented lines or those without variables
        else:
            atrName  = c[0]
            atrValue = str.replace(c[1],'\n','').split("#")[0].strip()
            atrValue = check_variable_name(atrValue)
            template_dict[atrName] = atrValue
    return template_dict

def ras2jpg(input, strTitle):
    call_str = "convert " + input + ".ras " + input + ".jpg"
    os.system(call_str)
    call_str = "convert " + input + ".jpg -resize 250 " + input + ".thumb.jpg"
    os.system(call_str)
    call_str = "convert " + input + ".jpg -resize 500 " + input + ".bthumb.jpg"
    os.system(call_str)
    call_str = "$INT_SCR/addtitle2jpg.pl " + input + ".thumb.jpg 14 " + strTitle
    os.system(call_str)
    call_str = "$INT_SCR/addtitle2jpg.pl " + input + ".bthumb.jpg 24 " + strTitle
    os.system(call_str)

def UseGamma(inFile, task, keyword):
    if task == "read":
        f = open(inFile, "r")
        while 1:
            line = f.readline()
            if not line: break
            if line.count(keyword) == 1:
                strtemp = line.split(":")
                value = strtemp[1].strip()
                return value
        print("Keyword " + keyword + " doesn't exist in " + inFile)
        f.close()


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Split beam of original SLC to generate sub-aperture SLC: backward- and forward-SLCs',\
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
    coregCoarse = templateContents['Coreg_Coarse']
    if 'Squint_MAI' in templateContents: Squint = templateContents['Squint_MAI']
    else: Squint = '0.5'



    projectDir = scratchDir + '/' + projectName
    demDir    = scratchDir + '/' + projectName  + '/DEM'

    slcDir    = scratchDir + '/' + projectName + '/SLC'
    rslcDir   = scratchDir + '/' + projectName + '/RSLC'
    ifgDir = projectDir + '/MAIifg'
    if not os.path.isdir(ifgDir): os.mkdir(ifgDir)

    Pair = Mdate + '-' + Sdate
    workDir = ifgDir + '/' + Pair
    if not os.path.isdir(workDir): os.mkdir(workDir)


# input slcs
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

    call_str = '$GAMMA_BIN/sbi_filt '+ MslcImg + ' ' + MrslcPar + ' '+SrslcPar + ' ' + MFslcImg + ' '+ MFslcPar + ' ' + MBslcImg + ' ' + MBslcPar + ' ' + Squint
    os.system(call_str)
    print(call_str)    

    call_str = '$GAMMA_BIN/sbi_filt '+ SslcImg + ' ' + SrslcPar + ' '+MrslcPar + ' ' + SFslcImg + ' '+ SFslcPar + ' ' + SBslcImg + ' ' + SBslcPar + ' ' + Squint
    os.system(call_str)
    
    sys.exit(1)

if __name__ == '__main__':
    main(sys.argv[:])
    










    
    
    
