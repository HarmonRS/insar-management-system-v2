#! /usr/bin/env python
#'''
##################################################################################
#                                                                                #
#            Author:   Yun-Meng Cao                                              #
#            Email :   ymcmrs@gmail.com                                          #
#            Date  :   June, 2019                                                #
#                                                                                #
#           Generate SLC from SAR_IMS_P1 data                                    #
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
        f.close
        
def rm(TXT):
    call_str = 'rm ' + TXT
    os.system(call_str)        

def usage():
    print('''
******************************************************************************************************
 
                 Generate SLC and SLC_par file for ERS/ENVISAT  (SAR_IMS_1P format)

   usage:
   
            Down2SLC_ERS.py ProjectName DownName 
      
      e.g.  Down2SLC_ERS.py CotopaxiT120ERSA 910101
      
*******************************************************************************************************
    ''')   
    
def main(argv):
    
    if len(sys.argv)==3:
        projectName = sys.argv[1]
        Date  = sys.argv[2]
    else:
        usage();sys.exit(1)
         
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    
    projectDir = scratchDir + '/' + projectName 
    downDir    = scratchDir + '/' + projectName + "/DOWNLOAD"
    slcDir     = scratchDir + '/' + projectName + '/SLC'
    workflow   = downDir + '/' + Date
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    if not os.path.isdir(slcDir):
        call_str= 'mkdir ' +slcDir
        os.system(call_str)
    os.chdir(workflow)
    tempfile=workflow + '/' + 'workreport'
    if os.path.isfile(tempfile):
        tempDict=ut.update_template(tempfile)
        CEOS_SAR_leader = tempDict['Pdi_L10ProductFileName02'].split('"')[1]
        CEOS_raw_data   = tempDict['Pdi_L10ProductFileName03'].split('"')[1]  
        Date0 = Date
#        if len(Date)==6:
#           Date0 = Date
#        elif len(Date)==8:
#           Date0 = Date[2:8]
        #Date0=Date
#        else:
#           print('The input Date is invalid.')
#           sys.exit(1)
    
        #FileDir = downDir + '/' + downName
        SAR_par='palsar_' +Date0 + '.par'
        PROC_par='p' + Date0 + '.slc.par'
        raw_out=Date0 + '.raw'
        plot_data=Date0 +'.mlcc'
        rspec_data=Date0 +'.rspec' 
        doppler_data=Date0 + '.dop'
        rc_data=Date0 + '.rc'
        autof_data=Date0 +'.autof'
        slc_data=Date0 +'.slc'
        slc_par=Date0 +'.slc.par' 
        call_str = 'PALSAR_proc  ' + CEOS_SAR_leader + ' ' + SAR_par +' '+ PROC_par + ' '+  CEOS_raw_data + ' ' + raw_out + ' 0 0'
        os.system(call_str)
        palsar_ant_data='/home/chen/Software/InSAR/GAMMA/GAMMA_SOFTWARE-20180704/MSP/sensors/palsar_ant_20061024.dat'
        call_str = 'cp ' + palsar_ant_data + ' .'
        os.system(call_str)
        call_str = 'PALSAR_antpat' + ' ' + SAR_par +' '+ PROC_par + ' ' + palsar_ant_data + ' palsar_antpat_msp.dat'
        os.system(call_str)
        call_str = 'doppler ' + SAR_par +' '+ PROC_par + ' ' +  raw_out + ' ' + plot_data
        os.system(call_str)
        call_str = 'doppler ' + SAR_par +' '+ PROC_par + ' ' +  raw_out + ' ' + doppler_data 
        os.system(call_str)
        call_str = 'rspec_IQ ' + SAR_par +' '+ PROC_par + ' ' + raw_out + ' ' + rspec_data 
        os.system(call_str)
        #call_str = 'rspec_JERS ' + SAR_par +' '+ PROC_par + ' ' + CEOS_raw_data + ' ' + rspec_data + ' - - - - - -'
        #os.system(call_str)
        call_str = 'pre_rc ' + SAR_par +' '+ PROC_par + ' ' + raw_out + ' ' + rc_data 
        os.system(call_str)
        call_str = 'autof ' + SAR_par +' '+ PROC_par + ' ' + rc_data + ' ' + autof_data +' 5.0'
        os.system(call_str)
        call_str = 'autof ' + SAR_par +' '+ PROC_par + ' ' + rc_data + ' ' + autof_data +' 5.0'
        os.system(call_str)
        call_str = 'az_proc ' + SAR_par +' '+ PROC_par + ' ' + rc_data + ' ' + slc_data +' 16384'
        os.system(call_str)
        call_str = 'par_MSP ' + SAR_par +' '+ PROC_par + ' ' + slc_par
        os.system(call_str)
    else:
        print('No data is found for date:' + Date)
        sys.exit(1)
        
    call_str ="rename 's/VV.SLC/slc/g' *"
    os.system(call_str)
    
    Date0 = Date
    
    dataDir = slcDir + '/' + Date0
    if not os.path.isdir(dataDir):
        call_str = 'mkdir ' + dataDir
        print('Generate SLC dir for date: ' + Date0)
        os.system(call_str)
    call_str = 'mv ' + Date0 + '.slc* ' + dataDir
    os.system(call_str)      
    print("Down to SLC for %s is done! " % Date)
    SslcImg = dataDir + '/'+ Date0 + '.slc'
    SslcPar = dataDir + '/'+ Date0 + '.slc.par'    
    
    SamprlksImg = dataDir + '/'+ Date0 +  '_' + rlks + 'rlks' + '.amp'
    SamprlksPar = dataDir + '/'+ Date0 +  '_' + rlks + 'rlks' + '.amp.par'
    call_str = 'multi_look ' + SslcImg + ' ' + SslcPar + ' ' + SamprlksImg + ' ' + SamprlksPar + ' ' + rlks + ' ' + azlks
    os.system(call_str) 
    
    nWidth = ut.read_gamma_par(SamprlksPar, 'read', 'range_samples')
    call_str = 'raspwr ' + SamprlksImg + ' ' + str(nWidth)
    os.system(call_str)
    sys.exit(1)
    
if __name__ == '__main__':
    main(sys.argv[:])    
    
    
    
    
    
    
