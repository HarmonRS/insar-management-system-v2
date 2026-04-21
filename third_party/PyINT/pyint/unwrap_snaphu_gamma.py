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

import subprocess

import re



from pyint import _utils as ut



INTRODUCTION = '''

-------------------------------------------------------------------  

       Unwrap differential interferogram using SNAPHU.

   

'''



EXAMPLE = '''

    Usage: 

            unwrap_snaphu.py projectName Mdate Sdate

            unwrap_snaphu.py PacayaT163TsxHhA 20150102 20150601

-------------------------------------------------------------------  

'''



def cmdLineParse():

    parser = argparse.ArgumentParser(description='Unwrap differential interferogram using SNAPHU.',\

                                     formatter_class=argparse.RawTextHelpFormatter,\

                                     epilog=INTRODUCTION+'\n'+EXAMPLE)



    parser.add_argument('projectName', help='projectName for processing.')

    parser.add_argument('Mdate', help='Master date.')

    parser.add_argument('Sdate', help='Slave date.')

    

    inps = parser.parse_args()

    return inps



def run_command(cmd):

    print(f"Running: {cmd}")

    status = subprocess.call(cmd, shell=True)

    if status != 0:

        print(f"Error running command: {cmd}")

        sys.exit(1)



def extract_value(s):

    """Extract numeric value from string that may contain units"""

    # Remove any non-numeric characters except decimal point and minus sign

    return re.sub(r'[^\d\.\-]', '', s)



def main(argv):

    inps = cmdLineParse() 

    projectName = inps.projectName

    Mdate = inps.Mdate

    Sdate = inps.Sdate

    

    scratchDir = os.getenv('SCRATCHDIR')

    templateDir = os.getenv('TEMPLATEDIR')

    templateFile = templateDir + "/" + projectName + ".template"

    templateDict = ut.update_template(templateFile)

    rlks = templateDict['range_looks']

    azlks = templateDict['azimuth_looks']

    

    processDir = scratchDir + '/' + projectName + "/PROCESS"

    slcDir = scratchDir + '/' + projectName + "/SLC"

    rslcDir = scratchDir + '/' + projectName + '/RSLC'

    ifgDir = scratchDir + '/' + projectName + '/ifgrams'

    

    Pair = Mdate + '-' + Sdate

    workDir = ifgDir + '/' + Pair

    

    # Create working directory if not exists

    os.makedirs(workDir, exist_ok=True)

    

    # Copy required files for processing

    Mamp0 = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp'

    MampPar0 = rslcDir + '/' + Mdate + '/' + Mdate + '_' + rlks + 'rlks.amp.par'

    Samp0 = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp'

    SampPar0 = rslcDir + '/' + Sdate + '/' + Sdate + '_' + rlks + 'rlks.amp.par'

    

    Mamp = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp'

    MampPar = workDir + '/' + Mdate + '_' + rlks + 'rlks.amp.par'

    Samp = workDir + '/' + Sdate + '_' + rlks + 'rlks.amp'

    SampPar = workDir + '/' + Sdate + '_' + rlks + 'rlks.amp.par'

    off_par = workDir + '/' + Pair + '_' + rlks + 'rlks.off'

    ut.copy_file(Mamp0, Mamp)

    ut.copy_file(Samp0, Samp)

    ut.copy_file(MampPar0, MampPar)

    ut.copy_file(SampPar0, SampPar)

    

    # Define input/output files

    int_dir = workDir

    rootname = Pair + '_' + rlks + 'rlks'

    int_file = int_dir + '/' + rootname + '.diff_filt'

    cor_file = int_dir + '/' + rootname + '.diff_filt.cor'

    

    # Read parameters from par files

    width = ut.read_gamma_par(off_par, 'read', 'interferogram_azimuth_lines:')
    length = ut.read_gamma_par(off_par, 'read', 'interferogram_width:')
    #length = ut.read_gamma_par(MampPar, 'read', 'azimuth_lines')
    row = ut.read_gamma_par(off_par, 'read', 'interferogram_azimuth_lines:')
    col = ut.read_gamma_par(off_par, 'read', 'interferogram_width:')
    pos = ut.read_gamma_par(MampPar, 'read', 'sar_to_earth_center')
    earth = ut.read_gamma_par(MampPar, 'read', 'earth_radius_below_sensor')
    near = ut.read_gamma_par(MampPar, 'read', 'near_range_slc')
    dr =  ut.read_gamma_par(off_par, 'read', 'interferogram_range_pixel_spacing:')
    da_flight = ut.read_gamma_par(off_par, 'read', 'interferogram_azimuth_pixel_spacing:')
    rangeres = ut.read_gamma_par(MampPar, 'read', 'range_pixel_spacing')
    azres = ut.read_gamma_par(MampPar, 'read', 'azimuth_pixel_spacing')    
    nrange = ut.read_gamma_par(off_par, 'read', 'interferogram_range_looks:')
    nazi = ut.read_gamma_par(off_par, 'read', 'interferogram_azimuth_looks:')
    
   
    # Extract numeric values
    earth = extract_value(earth)
    pos = extract_value(pos)
    near = extract_value(near)    
    dr = extract_value(dr)
    da_flight = extract_value(da_flight)
    nrange = extract_value(nrange)
    nazi = extract_value(nazi)
    rangeres = extract_value(rangeres)
    azres = extract_value(azres)    
    
    alt = str(float(pos) - float(earth))
    da = str(float(da_flight) * float(earth) / (float(earth) + float(alt)))
    ncor = str(float(dr) * float(da) / (float(rangeres) * float(azres)))
    


    
  
  
  
   

    

    # Create SNAPHU configuration file

    conf_file = workDir + '/' + rootname + '.snaphuconf'

    with open(conf_file, 'w') as f:

        f.write(f"STATCOSTMODE         SMOOTH\n")

        f.write(f"INFILE               {rootname}.int\n")

        f.write(f"LINELENGTH           {width}\n")  # 使用原始宽度

        f.write(f"OUTFILE              {rootname}.unw\n")

        f.write(f"CORRFILE             {rootname}.cor\n")

        f.write(f"LOGFILE              {rootname}.snaphulog\n")

        f.write(f"\n")

        f.write(f"PIECEFIRSTROW        1\n")

        f.write(f"PIECEFIRSTCOL        1\n")

        f.write(f"PIECENROW            {length}\n")  # 使用原始长度

        f.write(f"PIECENCOL            {width}\n")   # 使用原始宽度

        f.write(f"ALTITUDE             {alt}\n")

        f.write(f"EARTHRADIUS          {earth}\n")

        f.write(f"NEARRANGE            {near}\n")

        f.write(f"BASELINE             0.000000\n")

        f.write(f"BASELINEANGLE_DEG    0.000000\n")

        f.write(f"TRANSMITMODE         REPEATPASS\n")

        f.write(f"DR                   {dr}\n")

        f.write(f"DA                   {da}\n")

        f.write(f"RANGERES             {rangeres}\n")

        f.write(f"AZRES                {azres}\n")

        f.write(f"LAMBDA               0.0556\n")

        f.write(f"NLOOKSRANGE          {nrange}\n")

        f.write(f"NLOOKSAZ             {nazi}\n")

        f.write(f"NLOOKSOTHER          1\n")

        f.write(f"NCORRLOOKS           {ncor}\n")

        f.write(f"\n")

        f.write(f"CONNCOMPFILE         {rootname}.byt\n")

        f.write(f"MAXNCOMPS            32\n")

        f.write(f"\n")

        f.write(f"INFILEFORMAT         COMPLEX_DATA\n")

        f.write(f"OUTFILEFORMAT        ALT_LINE_DATA\n")

        f.write(f"CORRFILEFORMAT       FLOAT_DATA\n")

        f.write(f"VERBOSE              FALSE\n")

    

    # Swap bytes for input files

    run_command(f"swap_bytes {int_file} {rootname}.int 4")

    run_command(f"swap_bytes {cor_file} {rootname}.cor 4")

    

    # Run SNAPHU

    run_command(f"snaphu -f {conf_file}")

    

    # Post-processing steps

    xmin = "0"

    ymin = "0"

    xmax = width

    ymax = length

    

    # 获取SNAPHU输出文件的尺寸

    unwrapped_file = f"{rootname}.unw"
    new_width = str(int(xmax)-int(xmin))

    # 计算填充量

    npad_bottom = str(int(length) - int(ymax))

    npad_right = str(int(width) - int(xmax))

    

    print(f"Original dimensions: {width}x{length}")

    print(f"SNAPHU output dimensions: {new_width}x{new_width}")

    print(f"Padding: bottom={npad_bottom}, right={npad_right}")

    

    # Zero padding

    unw_zero_file = rootname + "_zeropad.unw"

    mask_zero_file = rootname + "_mask_zeropad.unw"

    mask_zero_file_msk = rootname + "_mask_zeropad.msk"

    tobegeocodedm = rootname + "_masked.unw"

    unw_file = rootname + "_msk.unw"

    

    run_command(f"zeropad_msk {rootname}.unw {width} {ymin} {xmin} {npad_right} {npad_bottom} {unw_zero_file} rmg")

    

    # Phase and magnitude processing

    run_command(f"rmg2mag_phs {unw_zero_file} /dev/null phs {width}")

    run_command(f"swap_bytes phs phs4 4")

    run_command(f"cpx2mag_phs {rootname}.int pwr /dev/null {width}")

    run_command(f"mag_phs2rmg pwr phs {unw_zero_file} {width}")

    

    # Mask processing

    run_command(f"zeropad_msk {rootname}.byt {width} {ymin} {xmin} {npad_right} {npad_bottom} {mask_zero_file_msk} msk")

    run_command(f"cpx2mag_phs {rootname}.int pwr phs {width}")

    run_command(f"mag_phs2rmg pwr {mask_zero_file_msk} {mask_zero_file} {width}")

    

    # Combine results

    run_command(f"rmg2mag_phs {unw_zero_file} pwr phs1 {width}")

    run_command(f"rmg2mag_phs {mask_zero_file} /dev/null phs2 {width}")

    run_command(f"add_phs phs1 phs2 phs3 {width} {length} 0 1")

    run_command(f"add_phs pwr phs2 pwr3 {width} {length} 0 1")

    run_command(f"mag_phs2rmg pwr3 phs3 {tobegeocodedm} {width}")

    run_command(f"swap_bytes phs3 {unw_file} 4")

    

    # Clean up temporary files

    run_command("rm -rf pwr phs phs1 phs2 phs3 pwr3")

    run_command(f"mv phs4 {unw_zero_file}")

    

    # Remove copied files

    os.remove(Mamp)

    os.remove(Samp)

    

    print("Unwrapping with SNAPHU is done!")

    sys.exit(0)



if __name__ == '__main__':

    main(sys.argv[:])
