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
import time
import glob
import argparse
import subprocess
from datetime import datetime

from pyint import _utils as ut


def find_ifgs_by_interval(ifgDir, interval=12):
    """Find all interferograms with specified temporal interval
    
    Args:
        ifgDir: interferogram directory
        interval: temporal baseline in days (6, 12, or 24)
    
    Returns:
        list: list of date pairs
    """
    ifgs = []
    
    all_dirs = sorted([d for d in os.listdir(ifgDir) if os.path.isdir(os.path.join(ifgDir, d))])
    
    for dirname in all_dirs:
        if '-' in dirname:
            dates = dirname.split('-')
        elif '_' in dirname:
            dates = dirname.split('_')
        else:
            continue
        
        if len(dates) == 2:
            try:
                date1 = dates[0]
                date2 = dates[1]
                
                d1 = datetime.strptime(date1, '%Y%m%d')
                d2 = datetime.strptime(date2, '%Y%m%d')
                days = abs((d2 - d1).days)
                
                if days == interval:
                    ifgs.append(dirname)
            except ValueError:
                continue
    
    return ifgs


INTRODUCTION = '''
-------------------------------------------------------------------  
       Apply phase bias correction to all interferograms with
       specified temporal interval in a project.
       
       This script runs the full PhaseBias correction pipeline:
       1. Read interferogram data
       2. Calculate loop closures
       3. Estimate calibration parameters (optional)
       4. Inversion for phase bias terms
       5. Apply correction
   
'''

EXAMPLE = '''
    Usage: 
            phasebias_correction_gamma_all.py projectName
            phasebias_correction_gamma_all.py projectName --interval 12
            phasebias_correction_gamma_all.py projectName --interval 12 --nlook 10
            phasebias_correction_gamma_all.py projectName --interval 24 --estimate-an
-------------------------------------------------------------------  
'''


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Apply phase bias correction to interferograms.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)
    
    parser.add_argument('projectName', help='projectName for processing.')
    parser.add_argument('--interval', type=int, default=12, 
                        help='Data acquisition interval in days (6, 12, or 24). [default: 12]')
    parser.add_argument('--nlook', type=int, default=10,
                        help='Number of looks for multilooking. [default: 10]')
    parser.add_argument('--num-a', type=int, default=2,
                        help='Number of calibration parameters to estimate. [default: 2]')
    parser.add_argument('--start', type=str, default=None,
                        help='Start date (YYYYMMDD). If not specified, will use all available data.')
    parser.add_argument('--end', type=str, default=None,
                        help='End date (YYYYMMDD). If not specified, will use all available data.')
    parser.add_argument('--estimate-an', dest='estimate_an', action='store_true',
                        help='Estimate calibration parameters from data instead of using defaults.')
    parser.add_argument('--max-con', type=int, default=5,
                        help='Maximum number of connections to correct. [default: 5]')
    
    inps = parser.parse_args()
    return inps


def main(argv):
    start_time = time.time()
    inps = cmdLineParse() 
    projectName = inps.projectName
    interval = inps.interval
    nlook = inps.nlook
    num_a = inps.num_a
    estimate_an = inps.estimate_an
    max_con = inps.max_con
    
    scratchDir = os.getenv('SCRATCHDIR')
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    projectDir = scratchDir + '/' + projectName
    ifgDir = scratchDir + '/' + projectName + '/ifgrams'
    
    templateDict = ut.update_template(templateFile)
    rlks = templateDict['range_looks']
    azlks = templateDict['azimuth_looks']
    
    # Check if interferograms with specified interval exist
    ifgList = find_ifgs_by_interval(ifgDir, interval)
    
    print("\n" + "="*80)
    print(f"Phase Bias Correction for Project: {projectName}")
    print(f"Temporal interval: {interval} days")
    print(f"Number of interferograms found: {len(ifgList)}")
    print("="*80)
    
    if len(ifgList) == 0:
        print(f"\nERROR: No interferograms found with {interval}-day interval!")
        print("Available interferograms:")
        all_dirs = sorted([d for d in os.listdir(ifgDir) if os.path.isdir(os.path.join(ifgDir, d))])
        for dirname in all_dirs[:10]:  # Show first 10
            if '-' in dirname:
                dates = dirname.split('-')
                if len(dates) == 2:
                    try:
                        d1 = datetime.strptime(dates[0], '%Y%m%d')
                        d2 = datetime.strptime(dates[1], '%Y%m%d')
                        days = abs((d2 - d1).days)
                        print(f"  {dirname}: {days} days")
                    except:
                        pass
        sys.exit(1)
    
    # Determine date range
    if inps.start and inps.end:
        start_date = inps.start
        end_date = inps.end
    else:
        # Extract dates from interferogram list
        dates = []
        for pair in ifgList:
            if '-' in pair:
                date_pair = pair.split('-')
                dates.extend(date_pair)
            elif '_' in pair:
                date_pair = pair.split('_')
                dates.extend(date_pair)
        
        dates = sorted(set(dates))
        start_date = dates[0]
        end_date = dates[-1]
    
    print(f"\nDate range: {start_date} to {end_date}")
    print(f"Multilooking factor: {nlook}")
    print(f"Number of calibration parameters: {num_a}")
    print(f"Estimate an from data: {estimate_an}")
    print(f"Max connections: {max_con}")
    
    # Check if correction has already been done
    outputDir = projectDir + '/PhaseBiasCorrection'
    configFile = outputDir + '/config.txt'
    
    if os.path.exists(configFile):
        print("\n" + "="*80)
        print("Found existing PhaseBiasCorrection directory")
        print("This script will process ALL interferograms together using the")
        print("InSAR_PhaseBias_Correction pipeline.")
        print("="*80)
    
    # Build command
    cmd_list = [
        'phasebias_correction_gamma.py',
        projectName,
        '--interval', str(interval),
        '--nlook', str(nlook),
        '--num-a', str(num_a),
        '--max-con', str(max_con)
    ]
    
    if start_date:
        cmd_list.extend(['--start', start_date])
    if end_date:
        cmd_list.extend(['--end', end_date])
    if estimate_an:
        cmd_list.append('--estimate-an')
    
    # Run the phase bias correction script
    print("\n" + "="*80)
    print("Running phase bias correction...")
    print("="*80)
    print(f"Command: {' '.join(cmd_list)}")
    
    try:
        result = subprocess.run(cmd_list, check=True, capture_output=False)
        print("\n" + "="*80)
        print("Phase bias correction completed successfully!")
        print(f"Output directory: {outputDir}")
        print(f"Corrected interferograms: {outputDir}/GEOC/")
        print("="*80)
    except subprocess.CalledProcessError as e:
        print("\n" + "="*80)
        print("ERROR: Phase bias correction failed!")
        print("="*80)
        sys.exit(1)
    
    ut.print_process_time(start_time, time.time())
    sys.exit(1)


if __name__ == '__main__':
    main(sys.argv[:])
