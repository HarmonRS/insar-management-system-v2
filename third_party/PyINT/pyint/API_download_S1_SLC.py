#! /usr/bin/env python3
# -*- coding: utf-8 -*-

###########################################################################
# Header information 
###########################################################################

"""API_download_S1_SLC.py: Script to download Sentinel-1 SLC images from the ASF mirror
   Supports both bounding box and Shapefile for spatial filtering"""

__author__ = "Alexis Hrysiewicz"
__copyright__ = "Copyright 2022"
__credits__ = ["Alexis Hrysiewicz"]
__license__ = "GPL"
__version__ = "2.0.0"
__maintainer__ = "Alexis Hrysiewicz"
__status__ = "Production"
__date__ = "Jan. 2022"

###########################################################################
# Python packages
###########################################################################

import os 
import sys
import pandas as pd
import datetime
import os.path
import optparse

# 尝试导入 geopandas 和 shapely 用于 Shapefile 支持
try:
    import geopandas as gpd
    from shapely.geometry import box, Polygon, MultiPolygon
    HAS_SHAPEFILE_SUPPORT = True
except ImportError:
    HAS_SHAPEFILE_SUPPORT = False
    print("Warning: geopandas/shapely not installed. Shapefile support disabled.")
    print("Install with: pip install geopandas shapely")

###########################################################################
###########################################################################

class OptionParser (optparse.OptionParser):

    def check_required(self, opt):
        option = self.get_option(opt)
        if getattr(self.values, option.dest) is None:
            self.error("%s option not supplied" % option)

def bbox_from_shapefile(shapefile_path):
    """
    从 Shapefile 文件提取边界框
    
    Args:
        shapefile_path: Shapefile 文件路径（.shp）
    
    Returns:
        边界框字符串格式: "min_lon,min_lat,max_lon,max_lat"
    """
    try:
        # 读取 Shapefile
        gdf = gpd.read_file(shapefile_path)
        
        # 计算总边界框
        total_bounds = gdf.total_bounds
        # total_bounds = (minx, miny, maxx, maxy)
        
        # 转换为字符串格式
        bbox_str = f"{total_bounds[0]},{total_bounds[1]},{total_bounds[2]},{total_bounds[3]}"
        
        print(f"Shapefile bounding box: {bbox_str}")
        return bbox_str
    except Exception as e:
        print(f"Error reading shapefile: {str(e)}")
        return None

def check_slc_exists(slc_name, path_SLC, path_RSLC, acquisition_date):
    """
    检查 SLC 文件是否已存在
    
    Args:
        slc_name: SLC 文件名
        path_SLC: SLC 存储路径
        path_RSLC: RSLC 存储路径
        acquisition_date: 采集日期字符串
    
    Returns:
        bool: 文件是否已存在
    """
    # 检查 .zip 文件
    if os.path.exists(os.path.join(path_SLC, slc_name + '.zip')):
        return True
    
    # 检查 .rslc 文件
    try:
        datei = datetime.datetime.strptime(acquisition_date.split('.')[0], '%Y-%m-%dT%H:%M:%S').strftime("%Y%m%d")
        if os.path.exists(os.path.join(path_RSLC, datei + '.vv.rslc')):
            return True
    except:
        pass
    
    return False

def read_netrc(machine_name):
    """
    从 ~/.netrc 文件读取登录凭证
    
    Args:
        machine_name: 机器名称 (例如: urs.earthdata.nasa.gov)
    
    Returns:
        tuple: (username, password) 或 (None, None) 如果未找到
    """
    netrc_path = os.path.expanduser('~/.netrc')
    
    if not os.path.exists(netrc_path):
        return None, None
    
    try:
        with open(netrc_path, 'r') as f:
            lines = f.readlines()
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                # 查找 machine 行
                if line.startswith('machine ' + machine_name):
                    username = None
                    password = None
                    
                    # 查找接下来的几行中的 login 和 password
                    j = i + 1
                    while j < len(lines) and j < i + 5:  # 最多查找5行
                        next_line = lines[j].strip()
                        tokens = next_line.split()
                        
                        if len(tokens) >= 2:
                            if tokens[0] == 'login':
                                username = tokens[1]
                            elif tokens[0] == 'password':
                                password = tokens[1]
                        
                        # 如果找到下一个 machine，停止查找
                        if next_line.startswith('machine '):
                            break
                        
                        j += 1
                    
                    if username and password:
                        return username, password
                
                i += 1
    except Exception as e:
        print(f"Warning: Error reading .netrc file: {str(e)}")
    
    return None, None

def download_slc(url, username, password, path_SLC):
    """
    下载单个 SLC 文件
    
    Args:
        url: 下载 URL
        username: ASF 用户名
        password: ASF 密码
        path_SLC: 存储路径
    
    Returns:
        bool: 下载是否成功
    """
    # 如果提供了用户名和密码，使用它们
    if username and password:
        cmd = f'wget -c --http-user={username} --http-password={password} "{url}" -P {path_SLC}'
    else:
        # 否则使用 .netrc 文件
        # --auth-no-challenge: 立即发送认证信息，不等待服务器挑战
        # --netrc: 从 ~/.netrc 读取认证信息
        cmd = f'wget -c --auth-no-challenge --netrc "{url}" -P {path_SLC}'
    
    return os.system(cmd) == 0

def convert_yyyymmdd_to_iso(date_str):
    """
    将 YYYYMMDD 格式转换为 ISO 格式 (YYYY-MM-DDTHH:MM:SS)
    
    Args:
        date_str: 日期字符串 (YYYYMMDD)
    
    Returns:
        str: ISO 格式日期字符串 (YYYY-MM-DDT00:00:00)
    """
    if len(date_str) == 8:
        year = date_str[0:4]
        month = date_str[4:6]
        day = date_str[6:8]
        return f"{year}-{month}-{day}T00:00:00"
    else:
        # 如果已经是其他格式，直接返回
        return date_str

###########################################################################
###########################################################################

if len(sys.argv) < 3:
    prog = os.path.basename(sys.argv[0])
    print("="*80)
    print("Sentinel-1 SLC Downloader from ASF")
    print("="*80)
    print("\nUsage examples:")
    print("\n1. Using bounding box:")
    print("   python3 " + prog + " -u username -p password -s . -r . \\")
    print("       -b -10.78,51.27,-5.03,55.70 \\")
    print("       -i 20170101 -j 20231231 \\")
    print(f"       -o 1 -f a -q IW -m csv -w n")
    print("\n2. Using Shapefile (new feature):")
    print("   python3 " + prog + " -u username -p password -s . -r . \\")
    print("       --shp study_area.shp \\")
    print("       -i 20170101 -j 20231231 \\")
    print(f"       -o 1 -f a -q IW -m csv -w n")
    print("\nOptions:")
    print("  -u, --username       ASF username (optional, can use .netrc)")
    print("  -p, --password       ASF password (optional, can use .netrc)")
    print("  -n, --netrc          Machine name in ~/.netrc (default: urs.earthdata.nasa.gov)")
    print("  -s, --path_SLC       Output directory for SLC files")
    print("  -r, --path_RSLC      Directory to check for processed RSLC files")
    print("  -b, --bbox           Bounding box (min_lon,min_lat,max_lon,max_lat)")
    print("  --shp                Shapefile path (.shp) for spatial filtering")
    print("  -i, --date_start     Start date (YYYYMMDD)")
    print("  -j, --date_end       End date (YYYYMMDD)")
    print("  -o, --orbit_relative Relative orbit number (optional)")
    print("  --frame              Frame number (optional)")
    print("  -f, --flight_direction Flight direction (a=ascending, d=descending, or all)")
    print("  -q, --q_acqui_mode   Acquisition mode (IW, EW, SM, or all)")
    print("  -m, --mode           Output format (csv or kml)")
    print("  -w, --write          Download files? (Y/N)")
    print("  --parallel           Number of parallel downloads (default: 1, serial)")
    print("\nNote: Shapefile support requires geopandas and shapely")
    print("="*80)
    sys.exit(-1)
else:
    usage = "usage: %prog [options] "
    parser = OptionParser(usage=usage)
    parser.add_option("-u", "--username", action="store", type="string", default=None, 
                      help="ASF username (can be read from ~/.netrc)")
    parser.add_option("-p", "--password", action="store", type="string", default=None,
                      help="ASF password (can be read from ~/.netrc)")
    parser.add_option("-n", "--netrc", action="store", type="string", default='urs.earthdata.nasa.gov',
                      help="Machine name in ~/.netrc file (default: urs.earthdata.nasa.gov)")
    parser.add_option("-s", "--path_SLC", action="store", type="string", default='.')
    parser.add_option("-r", "--path_RSLC", action="store", type="string", default='.') # Only available for GAMMA stack
    parser.add_option("-b", "--bbox", action="store", type="string", default='-10.78,51.27,-5.03,55.70')
    parser.add_option("--shp", "--shapefile", action="store", type="string", default=None,
                      help="Shapefile path for spatial filtering")
    parser.add_option("-i", "--date_start", action="store", type="string", default='20170101')
    parser.add_option("-j", "--date_end", action="store", type="string", default='20240101')
    parser.add_option("-o", "--orbit_relative", action="store", type="float", default=None,
                      help="Relative orbit number (optional, if not specified will search all orbits)")
    parser.add_option("--frame", action="store", type="int", default=None,
                      help="Frame number (optional, if not specified will search all frames)")
    parser.add_option("-f", "--flight_direction", action="store", type="string", default='a',
                      help="Flight direction (a=ascending, d=descending, or 'all' for both)")
    parser.add_option("-q", "--q_acqui_mode", action="store", type="string", default='IW')
    parser.add_option("-m", "--mode", action="store", type="string", default='csv')
    parser.add_option("-w", "--write", action="store", type="string", default='n')
    parser.add_option("--parallel", action="store", type="int", default=1,
                      help="Number of parallel downloads (default: 1, serial)")
    (options, args) = parser.parse_args()

###########################################################################
# Main
###########################################################################

date_format = "%Y-%m-%d"

# 转换日期格式
date_start_iso = convert_yyyymmdd_to_iso(options.date_start)
date_end_iso = convert_yyyymmdd_to_iso(options.date_end)

# 处理用户名和密码：如果未提供，尝试从 .netrc 读取
if options.username is None or options.password is None:
    netrc_username, netrc_password = read_netrc(options.netrc)
    if netrc_username and netrc_password:
        if options.username is None:
            options.username = netrc_username
            print(f"Username read from ~/.netrc ({options.netrc})")
        if options.password is None:
            options.password = netrc_password
            print(f"Password read from ~/.netrc ({options.netrc})")
    else:
        if options.username is None:
            print("Error: Username not provided and not found in ~/.netrc")
            print("Please provide username with -u option or configure ~/.netrc")
            sys.exit(1)
        if options.password is None:
            print("Error: Password not provided and not found in ~/.netrc")
            print("Please provide password with -p option or configure ~/.netrc")
            sys.exit(1)

# 处理空间范围：优先使用 Shapefile，否则使用 bounding box
if options.shp:
    if not HAS_SHAPEFILE_SUPPORT:
        print("Error: Shapefile support requires geopandas and shapely")
        print("Install with: pip install geopandas shapely")
        sys.exit(1)
    
    print(f"Using Shapefile: {options.shp}")
    bbox = bbox_from_shapefile(options.shp)
    if bbox is None:
        print("Error: Failed to extract bounding box from shapefile")
        sys.exit(1)
else:
    bbox = options.bbox
    print(f"Using bounding box: {bbox}")

# 构建 API 查询命令
# 轨道号、frame 和飞行方向都是可选的
api_params = f"platform=s1&bbox={bbox}&start={date_start_iso}-UTC&end={date_end_iso}-UTC&processingLevel=SLC&maxResults=10000"

# 如果指定了轨道号，添加到查询参数
if options.orbit_relative is not None:
    api_params += f"&relativeOrbit={int(options.orbit_relative)}"

# 如果指定了 frame 号，添加到查询参数
if hasattr(options, 'frame') and options.frame is not None:
    api_params += f"&frame={options.frame}"

# 如果指定了飞行方向且不是 'all'，添加到查询参数
if options.flight_direction and options.flight_direction.upper() != 'ALL':
    api_params += f"&flightDirection={options.flight_direction.upper()}"

# 添加输出格式
api_params += f"&output={options.mode.upper()}"

cmd1 = f'curl "https://api.daac.asf.alaska.edu/services/search/param?{api_params}" > SLC_list.{options.mode.lower()}'

print(f"Querying ASF API for SLC data...")
print(f"  Bounding Box: {bbox}")
print(f"  Date Range: {options.date_start} to {options.date_end}")
if options.orbit_relative is not None:
    print(f"  Orbit: {options.orbit_relative}")
else:
    print(f"  Orbit: All")
if hasattr(options, 'frame') and options.frame is not None:
    print(f"  Frame: {options.frame}")
else:
    print(f"  Frame: All")
if options.flight_direction and options.flight_direction.upper() != 'ALL':
    print(f"  Flight Direction: {options.flight_direction.upper()}")
else:
    print(f"  Flight Direction: All")
print(f"  Mode: {options.q_acqui_mode.upper()}")
print()

# 执行查询
if options.mode.upper() == 'KML':
    os.system(cmd1)
elif options.mode.upper() == 'CSV':
    os.system(cmd1)
else:
    print('Error: Please select a correct mode (csv or kml)...')
    sys.exit(1)

# 根据波束模式过滤列表
print(f"Filtering results by acquisition mode: {options.q_acqui_mode.upper()}")
if os.path.exists("SLC_list.csv"):
    os.rename("SLC_list.csv","SLC_list_orig.csv")
    h = 0 
    fout = open("SLC_list.csv",'w')
    total_count = 0
    matched_count = 0
    
    with open("SLC_list_orig.csv") as fi:
        for li in fi: 
            total_count += 1
            if h > 0:
                if options.q_acqui_mode.upper() == 'all':
                   fout.write(li)
                   matched_count += 1
                elif options.q_acqui_mode.upper() in li:
                   fout.write(li)
                   matched_count += 1  
            else:
                fout.write(li)
            h = h + 1
    
    fout.close()
    os.remove("SLC_list_orig.csv")
    
    print(f"  Total results: {total_count - 1}")
    print(f"  Filtered results: {matched_count}")
else:
    print("Warning: SLC_list.csv not found")
    sys.exit(1)

# 下载文件
if options.mode.upper() == 'CSV' and options.write.upper() == 'Y':
    try:
        listSLC = pd.read_csv("SLC_list.csv")
    except Exception as e:
        print(f"Error reading SLC_list.csv: {str(e)}")
        sys.exit(1)
    
    print(f"\nFound {len(listSLC)} SLC files to process")
    print("="*80)
    
    # 创建输出目录
    if not os.path.exists(options.path_SLC):
        os.makedirs(options.path_SLC)
        print(f"Created output directory: {options.path_SLC}")
    
    # 检查是否有并行参数
    parallel_numb = getattr(options, 'parallel', 1)
    if parallel_numb is None:
        parallel_numb = 1
    
    # 准备下载数据列表
    download_tasks = []
    skipped_tasks = []
    
    for h, slci in enumerate(listSLC['Granule Name']):
        url = listSLC['URL'][h]
        acquisition_date = listSLC['Acquisition Date'][h]
        
        # 检查文件是否已存在
        if check_slc_exists(slci, options.path_SLC, options.path_RSLC, acquisition_date):
            skipped_tasks.append((h+1, slci, "Already exists"))
        else:
            download_tasks.append((h+1, slci, url, acquisition_date))
    
    print(f"Files to download: {len(download_tasks)}")
    print(f"Files skipped (already exists): {len(skipped_tasks)}")
    
    # 并行下载函数
    def download_worker(task):
        idx, slci, url, acquisition_date = task
        print(f"\n[{idx}/{len(listSLC)}] Downloading: {slci}")
        
        if download_slc(url, options.username, options.password, options.path_SLC):
            print(f"  Status: Download SUCCESS")
            return (idx, slci, "SUCCESS")
        else:
            print(f"  Status: Download FAILED")
            return (idx, slci, "FAILED")
    
    # 执行下载
    downloaded = 0
    failed = 0
    
    if parallel_numb > 1 and len(download_tasks) > 0:
        # 并行下载
        from multiprocessing.pool import ThreadPool
        print(f"\nStarting parallel download with {parallel_numb} workers...")
        
        pool = ThreadPool(parallel_numb)
        results = pool.map(download_worker, download_tasks)
        pool.close()
        pool.join()
        
        # 统计结果
        for result in results:
            if result[2] == "SUCCESS":
                downloaded += 1
            else:
                failed += 1
    elif len(download_tasks) > 0:
        # 串行下载
        print("\nStarting serial download...")
        for task in download_tasks:
            result = download_worker(task)
            if result[2] == "SUCCESS":
                downloaded += 1
            else:
                failed += 1
    
    # 打印摘要
    print("\n" + "="*80)
    print("DOWNLOAD SUMMARY")
    print("="*80)
    print(f"Total SLC files: {len(listSLC)}")
    print(f"Downloaded: {downloaded}")
    print(f"Skipped (already exists): {len(skipped_tasks)}")
    print(f"Failed: {failed}")
    print("="*80)
    
    if failed > 0:
        print(f"\nWarning: {failed} downloads failed. Check the output above for details.")
        sys.exit(1)
    
elif options.mode.upper() == 'KML' and options.write.upper() == 'Y':
    print('Please, select CSV mode to download..')
elif options.write.upper() != 'Y':
    print(f"\nSLC list saved to: SLC_list.csv")
    print("To download files, re-run with -w Y option")