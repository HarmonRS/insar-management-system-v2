#! /usr/bin/env python
#################################################################
###  This program is part of PyINT                            ###   
###  Author: chen                                      ###                                                          
###  Contact : chenweicug@126.com                             ###  
#################################################################
 
import getopt
import time
import glob
import numpy as np
import os
import sys  
import subprocess
import time
import argparse
from pyint import _utils as ut


INTRODUCTION = '''
-------------------------------------------------------------------  
       Generate differential interferogram image from SLC using GAMMA.
   
'''

EXAMPLE = '''
    Usage: 
               scihub_search_s1_data.sh -options
               scihub_search_s1_data.sh -s 2015-08-01 -e NOW -r "123.0/-123.3/40.0/40.2" -d Descending -
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Coregister all of the SLCs to the reference SAR image using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName',help='projectName for processing.')
    parser.add_argument('-s', dest='Start_time', help='start_time [yyyy-mm-dd or NOW]')
    parser.add_argument('-e', dest='End_time', help='end_time [yyyy-mm-dd or NOW]')
    parser.add_argument('-r', dest='Region_box',help='region_box "lonW/lonE/latS/latN"')
    parser.add_argument('-p', dest='producttype', help='producttype "[SLC GRD OCN]"')
    parser.add_argument('-n', dest='Orbit_number',help='orbit_number [0-175]')
    parser.add_argument('-d', dest='Direction', help='direction [Ascending/Descending]')
    parser.add_argument('-sm',dest='Sence_model', help='sence model [IW/EW/SM]')
    parser.add_argument('-o', dest='output_file', help='output_file name')
    inps = parser.parse_args()
    return inps
def main(argv):
    
    start_time = time.time()
    inps = cmdLineParse()
    projectName = inps.projectName
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile) 
    projectDir = scratchDir + '/' + projectName
    dataDir = scratchDir + '/' + projectName + '/' +'DOWNLOAD'
    #dataDir="DOWNLOAD"
    if not os.path.isdir(dataDir):
           os.mkdir(dataDir)
    os.chdir(projectDir)
# The basic stuff that doesn't change^^
    search_query="https://scihub.copernicus.eu/dhus/search?q=platformname:Sentinel-1"

    if inps.output_file:
       Output_file=inps.output_file
    else:
       Output_file='search_result.txt'

# If we are searching based on starttime and endtime
    if inps.Start_time:
       starttime=inps.Start_time
    else:
       starttime='1990-01-01'
    if inps.End_time:
       endtime=inps.End_time
    else:
        endtime='NOW'
    search_query=search_query+" AND beginposition:[" + starttime + "T00:00:00.000Z TO "+ endtime + "T00:00:00.000Z]"
    #print(search_query)

    if inps.Region_box:
        Region=inps.Region_box
        compments=Region.split('/')
        lonW=compments[0]
        lonE=compments[1]
        latS=compments[2]
        latN=compments[3]
        lonw=float(lonW)
        lone=float(lonE)
        latn=float(latN)
        lats=float(latS)
        if lonW >=lonE:
      	    print("Error! Longitudes not in increasing order!")
      	    sys.exit(1)
        elif lats >= latn:
      	    print("Error! Latitudes not in increasing order!")
      	    sys.exit(1) 
        elif lats >= 90 or latn >= 90:
            print("Error! latS or latN not in bounds!")
            sys.exit(1)
    else:
        parser.print_usage()
        sys.exit(1)
# add the SAR covered area     
    search_query+=" AND footprint:\"intersects(POLYGON((" + lonW + " " + latN + "," + lonE + " " + latN + "," + lonE + " " + latS + "," + lonW + " " + latS + "," + lonW + " " + latN +")))\""

    if inps.Direction:
       direction=inps.Direction
    else:
       direction="Ascending"
# add the orbit direction
    search_query+=" AND orbitdirection:" + direction

    if inps.Sence_model:
       model=inps.Sence_model
    else:
       model='IW'
# add the sense operational mode
    search_query+=" AND sensoroperationalmode:"+ model

    if inps.Orbit_number:
       if int(inps.Orbit_number) >= 175:
           print("Error: orbit must be between 0 and 175")
           sys.exit(1)  
       else:
         orbit=inps.Orbit_number 
       #  add the relative orbit number
       search_query+=" AND relativeorbitnumber:" + orbit
    if inps.producttype:
       producttype=inps.producttype
    else:
       producttype='SLC' 
# add the orbit direction
    search_query+=" AND producttype:" + producttype    






# how many rows to display and where to start? 
# Max rows = 100 (slightly annoying rule from the Copernicus server)
    if os.path.isfile(Output_file): os.remove(Output_file)
    search_query0=search_query+"&start=0&rows=100" 
    print("the current search_query is: ",search_query0)
    #call_str="echo \"Input options:\" $@ > " +Output_file
    #os.system(call_str)
    call_str="echo \"wget --no-check-certificate --user=chenwei --password=cw1425 \""+ search_query0 + " >> " + Output_file
    os.system(call_str)
# Execute the search using wget
    search_query0="'"+search_query0+ "'"
    call_str="wget --no-check-certificate --user=chenwei --password=cw1425 " + search_query0  + " -O ->> " + Output_file
    os.system(call_str)
    call_str="`grep \'title>S1\' " + Output_file +" | wc -l`"
    num_results=os.system(call_str)
    print(num_results)
    if num_results >= 100 :
        print("We have 100 results... automatically searching for results #100-200")
        search_query1=search_query + "&start=100&rows=100"
        search_query1="'"+search_query1+ "'"
        call_str="wget --no-check-certificate --user=chenwei --password=cw1425 " + search_query1 + " -O ->> " + Output_file
        os.system(call_str)
        call_str="`grep \'title>S1\' " + Output_file +" | wc -l`"
        num_results=os.system(call_str) 
    elif num_results >= 200 :
        print("We have 200 results... automatically searching for results #100-200")
        search_query2=search_query + "&start=100&rows=100"
        search_query2="'"+search_query2+ "'"
        call_str="wget --no-check-certificate --user=chenwei --password=cw1425 " + search_query2 + " -O ->> " + Output_file
        os.system(call_str)
        call_str="`grep \'title>S1\' " + Output_file +" | wc -l`"
        num_results=os.system(call_str) 
    
# Displaying a summary of the results 
    call_str="grep \'title>S1\' " + Output_file   # displaying the results
    os.system(call_str)    
    print("number of total results is: ")
    call_str="grep \'title>S1\' " + Output_file + " | wc -l"
    os.system(call_str) 
    print("####################################################")
    print("the search has done and start to download")
    print("####################################################")
    id_results='uuid_file.txt'
    if os.path.isfile(id_results):
       os.remove(id_results)
    call_str="grep -E 'uuid|<title>S1' " + Output_file + " >> " + id_results
    os.system(call_str)
    call_str="sed -i  's/<str name=\"uuid\">//g' " + id_results
    os.system(call_str)
    call_str="sed -i  's/<title>//g' " + id_results
    os.system(call_str)
    call_str="sed -i  's/<\/title>//g' " + id_results
    os.system(call_str)
    call_str="sed -i  's/<\/str>//g' "+ id_results
    os.system(call_str)
    infopen = open(id_results,'r',encoding='utf-8')
    lines = infopen.readlines()
    count=int(len(lines)/2-1)
    for i in range(0,count,2):
        j=i+1
        title=lines[i].strip("\n")
        uuid=lines[j].strip("\n")
        url_address="\"https://scihub.copernicus.eu/dhus/odata/v1/Products('" + uuid + "')/\$value\""
        call_str= "wget -c --no-check-certificate --user=chenwei --password=cw1425 -O " +dataDir + "/" + title + ".zip " + url_address
        print(call_str)
        os.system(call_str)
        call_str=title+".zip download has done successfully!"
        print(call_str)
    print("#########################################################")
    print("#########all the data download successfully!#############")    
        
        
       

if __name__ == '__main__':
    main(sys.argv[:])
