#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v1.0                      ### 
###  Copy Right (c): 2017, Yunmeng Cao                        ###  
###  Author: Yunmeng Cao                                      ###                                                          
###  Email : ymcmrs@gmail.com                                 ###
###  Univ. : Central South University & University of Miami   ###   
#################################################################

import numpy as np
import os
import sys  
import subprocess
import getopt
import time
import glob

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


def is_number(s):
    try:
        int(s)
        return True
    except ValueError:
        return False
    
def add_zero(s):
    if len(s)==1:
        s="000"+s
    elif len(s)==2:
        s="00"+s
    elif len(s)==3:
        s="0"+s
    return s

def usage():
    print('''
******************************************************************************************************
 
           Select interferometry pairs from time series SAR images
     
      usage:
   
            Generate_IfgDir.py ProjectName IFG_List
      
      e.g.  Generate_IfgDir.py PacayaT163TsxHhA ifg_list
          
            
*******************************************************************************************************
    ''')   

def main(argv):
    
    if len(sys.argv)==2:
        if argv[0] in ['-h','--help']: usage(); sys.exit(1)
        else: 
             projectName=sys.argv[1] 
    projectName=sys.argv[1]
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    processDir = scratchDir + '/' + projectName + "/ifgrams"
    root_dir=os.getcwd()
    os.chdir(processDir)
    call_str="find . -name '[0-9]*.unw' -print >unw_file"
    os.system(call_str)
    file_obj=open('unw_file')
    all_lines=file_obj.readlines()
    for line in all_lines:
        unwdate1=line.split("/")[2]
        unwdate2=line.split("/")[1]
        unwdate3=line.split("/")[0]
        str1=unwdate1.split(".")[0]
        str2=unwdate1.split(".")[1]
        str3=unwdate1.split(".")[2]
        unwdate4=str2 + '_' + str1 + '.' + str3
        line1=processDir + '/' + unwdate2+ '/' + unwdate4
        line2=processDir + '/'+ unwdate2 + '/' + unwdate1
        line2=line2.split('\n')[0]
        line1=line1.split('\n')[0]
        call_str='mv'+ ' ' + line2 + ' ' + line1
        os.system(call_str)
    call_str="find . -name '[0-9]*.cor' -print >cor_file"
    os.system(call_str)

    file_obj=open('cor_file')
    all_lines=file_obj.readlines()
    for line in all_lines:
        cordate1=line.split("/")[2]
        cordate2=line.split("/")[1]
        cordate3=line.split("/")[0]
        str1=cordate1.split(".")[0]
        str2=cordate1.split(".")[1]
        str3=cordate1.split(".")[2]
        sub1=str2.split('_')[1]
        cordate4=sub1 + '_' + str1 + '.' + str3
        line1=processDir + '/' + cordate2 + '/' + cordate4
        line1=line1.split('\n')[0]
        line2=processDir + '/'+ cordate2 + '/' + cordate1
        line2=line2.split('\n')[0]
        call_str='mv' + ' ' + line2 + ' ' + line1
        os.system(call_str)
    sys.exit(1)

if __name__ == '__main__':
    main(sys.argv[:])            
    
    

