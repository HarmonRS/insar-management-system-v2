#! /usr/bin/env python
#################################################################
###  This program is part of PyINT  v2.2                      ### 
###  Copy Right (c): 2017-2019, Chen Wei                 ###  
###  Author: chenwei                                   ###                                                          
###  Contact : ymcmrs@gmail.com                               ###  
#################################################################
import numpy as np
import os
import sys  
import subprocess
import getopt
import time
import glob
import argparse
import re
import shutil
import tarfile

from pyint import _orbit_bridge as orbit_bridge
from pyint import _utils as ut


def get_LT1_date(raw_file):
    file0 = os.path.basename(raw_file)
    match = re.search(r'(20\d{6})', file0)
    if match:
        return match.group(1)
    return ''

def get_satellite(raw_file):
    if 'LT1A_MONO_' in raw_file:
        s0 = 'A'
    else:
        s0 = 'B'
    return s0


def discover_lt1_inputs(down_dir, date):
    candidates = []
    for pattern in (
        down_dir + '/LT1*' + date + '*.tar.gz',
        down_dir + '/LT1*' + date + '*.tiff',
    ):
        candidates.extend(glob.glob(pattern))
    return sorted(set(candidates))


def write_input_list(list_path, paths):
    with open(list_path, 'w') as f:
        for path in paths:
            f.write(path + '\n')


def _run_checked(command, cwd=None):
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            'Command failed (%s): %s%s' % (
                result.returncode,
                ' '.join(command),
                ('\n' + detail) if detail else '',
            )
        )
    return result


def _format_state_vector_line(line):
    stripped = line.strip()
    parts = stripped.split()
    if not parts:
        return ''
    label = parts[0]
    if label.startswith('state_vector_position_') and len(parts) >= 4:
        return '%s %14.4f %14.4f %14.4f   m   m   m' % (
            label,
            float(parts[1]),
            float(parts[2]),
            float(parts[3]),
        )
    if label.startswith('state_vector_velocity_') and len(parts) >= 4:
        return '%s %13.5f %13.5f %13.5f   m/s m/s m/s' % (
            label,
            float(parts[1]),
            float(parts[2]),
            float(parts[3]),
        )
    return stripped


def _replace_state_vectors_from_update(slc_par_path, update_par_path):
    if not os.path.isfile(update_par_path):
        return
    with open(slc_par_path, 'r', encoding='utf-8', errors='ignore') as fp:
        base_lines = fp.read().splitlines()
    with open(update_par_path, 'r', encoding='utf-8', errors='ignore') as fp:
        update_state_lines = [
            _format_state_vector_line(line)
            for line in fp.read().splitlines()
            if 'state_vector' in line
        ]
    if not update_state_lines:
        return
    merged = [line for line in base_lines if 'state_vector' not in line]
    merged.extend(update_state_lines)
    with open(slc_par_path, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(merged) + '\n')


def _cleanup_paths(paths):
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
        except OSError:
            continue


def _resolve_lt1_input_scene(raw_path, work_dir):
    cleanup_paths = []
    raw_lower = raw_path.lower()
    if raw_lower.endswith('.tiff'):
        input_xml = re.sub(r'\.tiff$', '.meta.xml', raw_path, flags=re.IGNORECASE)
        if not os.path.isfile(input_xml):
            raise FileNotFoundError('LT-1 meta xml does not exist: ' + input_xml)
        return raw_path, input_xml, cleanup_paths

    if raw_lower.endswith('.tar.gz'):
        temp_dir = os.path.join(work_dir, 'tmp_data_dir')
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        cleanup_paths.append(temp_dir)
        with tarfile.open(raw_path, 'r:gz') as archive:
            member_names = archive.getnames()
            tiff_members = [name for name in member_names if name.lower().endswith('.tiff')]
            xml_members = [name for name in member_names if name.lower().endswith('meta.xml')]
            if not tiff_members or not xml_members:
                raise RuntimeError('LT-1 archive is missing .tiff or meta.xml: ' + raw_path)
            tiff_member = tiff_members[0]
            xml_member = xml_members[0]
            archive.extract(tiff_member, path=temp_dir)
            archive.extract(xml_member, path=temp_dir)
        return os.path.join(temp_dir, tiff_member), os.path.join(temp_dir, xml_member), cleanup_paths

    raise RuntimeError('Unsupported LT-1 input, only .tiff or .tar.gz are supported: ' + raw_path)


def cmdLineParse():
    parser = argparse.ArgumentParser(description='Generate SLC from LT1 raw data with orbit correction using GAMMA.',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=INTRODUCTION+'\n'+EXAMPLE)

    parser.add_argument('projectName', help='project name. e.g., ChangningT55S1A')
    parser.add_argument('date',help='date to be processed. e.g., 20180101')
    inps = parser.parse_args()

    return inps


INTRODUCTION = '''
-------------------------------------------------------------------  

   Generate SLC from Sentinel-1 raw data using S1_import_SLC_from_zipfiles with orbit correction.
   [Precise orbit data will be downloaded automatically]
'''

EXAMPLE = """Usage:
  
  down2slc_sen.py projectName date 
  
  down2slc_sen.py ChangningT55S1A 20180517 
  
------------------------------------------------------------------- 
"""

def main(argv):
    
    inps = cmdLineParse() 
    projectName = inps.projectName
    date = inps.date
    scratchDir = os.getenv('SCRATCHDIR')
    projectDir = scratchDir + '/' + projectName 
    
    templateDir = os.getenv('TEMPLATEDIR')
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict=ut.update_template(templateFile)
    
    slc_dir =  projectDir + '/SLC'
    down_dir = projectDir + '/DOWNLOAD'

    if not os.path.isdir(slc_dir):
        os.mkdir(slc_dir)
        
    work_dir = slc_dir + '/' + date
    if not os.path.isdir(work_dir):
        os.mkdir(work_dir)

    os.chdir(work_dir)
    
    t_date = 't_' + date

    input_files = discover_lt1_inputs(down_dir, date)
    write_input_list(t_date, input_files)
    raw_files = ut.read_txt2list(t_date)
    if len(raw_files) == 0:
        raise RuntimeError('No LT-1 inputs found for date: ' + date)
    satellite = get_satellite(str(raw_files[0]))

    zipfile_ref=str(raw_files[0])
    outfile_name=zipfile_ref.split('/')[-1].split('.')[0]
    outfile_name=zipfile_ref.split('/')[-1]
    print(outfile_name)

    cleanup_paths = []
    try:
        input_tiff, input_xml, cleanup_paths = _resolve_lt1_input_scene(zipfile_ref, work_dir)
        slc_path = work_dir + '/' + date + '.slc'
        slc_par_path = work_dir + '/' + date + '.slc.par'
        update_path = work_dir + '/' + date + '.slc.update'
        update_par_path = work_dir + '/' + date + '.slc.update.par'
        _run_checked(
            ['par_LT1_SLC', input_tiff, input_xml, slc_par_path, slc_path],
            cwd=work_dir,
        )
        if not os.path.isfile(slc_path) or not os.path.isfile(slc_par_path):
            raise RuntimeError('LT-1 import produced no SLC outputs for date: ' + date)

        bridge_targets = [slc_par_path]
        ysli_command = shutil.which('par_LT1_SLC_YSLi')
        if ysli_command:
            _run_checked(
                [ysli_command, input_tiff, input_xml, update_path, update_par_path, '0'],
                cwd=work_dir,
            )
            if not os.path.isfile(update_path) or not os.path.isfile(update_par_path):
                raise RuntimeError('LT-1 import produced no update SLC outputs for date: ' + date)
            _replace_state_vectors_from_update(slc_par_path, update_par_path)
            bridge_targets.append(update_par_path)
        else:
            print('WARNING: par_LT1_SLC_YSLi is not available; using par_LT1_SLC outputs only.')

        bridge_result = orbit_bridge.apply_precise_orbit(
            date,
            bridge_targets,
            work_dir=work_dir,
            operation_tag='lt1_import',
        )
        if bridge_result.get('stdout'):
            print(bridge_result['stdout'])
        if bridge_result.get('stderr'):
            print(bridge_result['stderr'])
    finally:
        _cleanup_paths(cleanup_paths)
    

    SLC_Tab = work_dir + '/' + date+'_SLC_Tab'
    SLC_list = sorted(glob.glob(work_dir + '/*.slc')) 
    SLC_par_list = sorted(glob.glob(work_dir + '/*.slc.par')) 
    if len(SLC_list) == 0 or len(SLC_par_list) == 0:
        raise RuntimeError('No LT-1 SLC outputs were generated for date: ' + date)
    if len(SLC_list) != len(SLC_par_list):
        raise RuntimeError('LT-1 SLC and SLC parameter counts do not match for date: ' + date)
   
    if os.path.isfile(SLC_Tab):
        os.remove(SLC_Tab)    
   
    with open(SLC_Tab, 'w') as fp:
        for kk in range(len(SLC_list)):
            fp.write(SLC_list[kk] + ' ' + SLC_par_list[kk] + '\n')
    with open(work_dir + '/down2slc.dat', 'w') as f:
        f.write('ok\n')
    print("Down to SLC for %s is done! " % date)
    sys.exit(0)
    
if __name__ == '__main__':
    main(sys.argv[:])    
