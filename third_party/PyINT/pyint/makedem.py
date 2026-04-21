#! /usr/bin/env python
import os
import sys
import numpy as np
import argparse
import subprocess
import glob
from skimage import io

# 尝试导入 NASADEM 库
try:
    from NASADEM import NASADEM
    from rasters import RasterGrid
    HAS_NASADEM = True
except ImportError:
    HAS_NASADEM = False
    print("Warning: NASADEM library not installed. NASADEM download will not be available.")
    print("Install with: pip install nasadem rasters")

# 尝试导入 srtm 库
try:
    import srtm
    HAS_SRTM = True
except ImportError:
    HAS_SRTM = False
    print("Warning: srtm library not installed. SRTM download will not be available.")
    print("Install with: pip install srtm (requires Python >= 3.12)")
    print(f"Current Python version: {sys.version_info.major}.{sys.version_info.minor}")

resolutions = 30  # 90

def write_demrsc_file(FILE, Corner_LON, Corner_LAT, X_STEP, Y_STEP, WIDTH, LENGTH):
    """Write ROI_PAC DEM resource file (.dem.rsc)

    Args:
        FILE (str): Output resource file path
        Corner_LON (str): Corner longitude
        Corner_LAT (str): Corner latitude
        X_STEP (str): Longitude step size
        Y_STEP (str): Latitude step size
        WIDTH (str): Number of columns
        LENGTH (str): Number of lines
    """
    f = open(FILE, 'w')
    f.write('DATE12         111111-222222\n')
    f.write('FILE_LENGTH    ' + str(int(LENGTH)) + '\n')
    f.write('FILE_TYPE      .dem\n')
    f.write('PROCESSOR      roipac\n')
    f.write('PROJECTION     LATLON\n')
    f.write('RLOOKS         1\n')
    f.write('WIDTH          ' + str(int(WIDTH)) + '\n')
    f.write('XMAX           ' + str(int(int(WIDTH)-1)) + '\n')
    f.write('XMIN           0\n')
    f.write('X_FIRST        ' + str(float(Corner_LON)) + '\n')
    f.write('X_STEP         ' + str(float(X_STEP)) + '\n')
    f.write('X_UNIT         degrees\n')
    f.write('YMAX           ' + str(int(int(LENGTH)-1)) + '\n')
    f.write('YMIN           0\n')
    f.write('Y_FIRST        ' + str(float(Corner_LAT)) + '\n')
    f.write('Y_STEP         ' + str(float(Y_STEP)) + '\n')
    f.write('Y_UNIT         degrees\n')
    f.write('Z_OFFSET       0\n')
    f.write('Z_SCALE        1\n')
    f.close()

def write_dempar_file(FILE, Corner_LON, Corner_LAT, X_STEP, Y_STEP, WIDTH, LENGTH, DATA_FORMAT):
    """Write Gamma DEM parameter file (.dem.par)

    Args:
        FILE (str): Output parameter file path
        Corner_LON (str): Corner longitude
        Corner_LAT (str): Corner latitude
        X_STEP (str): Longitude step size
        Y_STEP (str): Latitude step size
        WIDTH (str): Number of columns
        LENGTH (str): Number of lines
        DATA_FORMAT (str): Data format (INTEGER*2 or REAL*4)
    """
    DEM_TYPE = 'Copernicus30'
    Proj = 'EQA'
    f = open(FILE, 'w')
    f.write("Gamma DIFF&GEO DEM/MAP parameter file\n")
    f.write("title:\tIMPORTED DEM FROM %s\n" % DEM_TYPE)
    f.write("DEM_projection:     %s\n" % Proj)
    f.write("data_format:        %s\n" % DATA_FORMAT)
    f.write("DEM_hgt_offset:          0.00000\n")
    f.write("DEM_scale:               1.00000\n")
    f.write("width:                %s\n" % WIDTH)
    f.write("nlines:               %s\n" % LENGTH)
    f.write("corner_lat:   %s  decimal degrees\n" % Corner_LAT)
    f.write("corner_lon:   %s  decimal degrees\n" % Corner_LON)
    f.write("post_lat:   %s  decimal degrees\n" % Y_STEP)
    f.write("post_lon:   %s  decimal degrees\n" % X_STEP)
    f.write("\n")
    f.write("ellipsoid_name: WGS 84\n")
    f.write("ellipsoid_ra:        6378137.000   m\n")
    f.write("ellipsoid_reciprocal_flattening:  298.2572236\n")
    f.write("\n")
    f.write("datum_name: WGS 1984\n")
    f.write("datum_shift_dx:              0.000   m\n")
    f.write("datum_shift_dy:              0.000   m\n")
    f.write("datum_shift_dz:              0.000   m\n")
    f.write("datum_scale_m:         0.00000e+00\n")
    f.write("datum_rotation_alpha:  0.00000e+00   arc-sec\n")
    f.write("datum_rotation_beta:   0.00000e+00   arc-sec\n")
    f.write("datum_rotation_gamma:  0.00000e+00   arc-sec\n")
    f.write("datum_country_list Global Definition, WGS84, World\n")
    f.write("\n")
    f.close()

def convert_to_gamma(input_file, output_name, byteorder='big', processor='gamma'):
    """Convert TIF file to Gamma or ROI_PAC format (.dem and .dem.par/.dem.rsc)

    Args:
        input_file (str): Input TIF file path
        output_name (str): Output name (without extension)
        byteorder (str): Byte order ('big' or 'little')
        processor (str): Processor type ('gamma' or 'roi_pac')
    """
    processor_name = 'Gamma' if processor == 'gamma' else 'ROI_PAC'
    print(f"\n开始转换到 {processor_name} 格式: {input_file}")
    
    # 读取 DEM 数据 - 优先使用 GDAL（更稳定），然后是 rasterio
    dem_data = None
    
    # 方法1: 使用 GDAL（最稳定）
    try:
        from osgeo import gdal
        print("  尝试使用 GDAL 读取数据...")
        ds = gdal.Open(input_file)
        if ds is not None:
            band = ds.GetRasterBand(1)
            dem_data = band.ReadAsArray()
            print(f"  ✓ 使用 GDAL 读取成功")
            ds = None  # 关闭文件
    except Exception as e:
        print(f"  ✗ GDAL 读取失败: {e}")
    
    # 方法2: 如果GDAL失败，尝试rasterio
    if dem_data is None:
        try:
            import rasterio
            print("  尝试使用 rasterio 读取数据...")
            with rasterio.open(input_file) as src:
                dem_data = src.read(1)
                print(f"  ✓ 使用 rasterio 读取成功")
        except Exception as e:
            print(f"  ✗ rasterio 读取失败: {e}")
    
    # 方法3: 最后尝试skimage
    if dem_data is None:
        try:
            print("  尝试使用 skimage 读取数据...")
            dem_data = io.imread(input_file)
            print(f"  ✓ 使用 skimage 读取成功")
        except Exception as e:
            print(f"  ✗ skimage 读取失败: {e}")
    
    # 如果所有方法都失败
    if dem_data is None:
        raise ValueError(f"无法读取DEM文件: {input_file}，所有读取方法都失败了")
    
    # 确定数据格式
    if dem_data.dtype == 'float32':
        DATA_FORMAT = 'REAL*4'
    else:
        DATA_FORMAT = 'INTEGER*2'
    
    # 字节序转换
    if not sys.byteorder == byteorder:
        dem_data.byteswap(True)
    
    # 输出文件路径
    dem_file = output_name + '.dem'
    if processor == 'gamma':
        dem_par_file = output_name + '.dem.par'
    else:
        dem_par_file = output_name + '.dem.rsc'
    
    # 写入二进制 DEM 数据
    dem_data.tofile(dem_file)
    
    # 使用 gdalinfo 获取地理信息
    info_file = 'temp_gdalinfo.txt'
    cmd = f"gdalinfo {input_file} > {info_file}"
    os.system(cmd)
    
    # 解析地理信息
    Corner_LON = None
    Corner_LAT = None
    Post_LON = None
    Post_LAT = None
    WIDTH = None
    FILE_LENGTH = None
    
    with open(info_file, 'r') as f:
        for line in f:
            if 'Origin =' in line:
                parts = line.split('Origin =')[1].strip().split('(')[1].split(')')[0].split(',')
                Corner_LON = parts[0]
                Corner_LAT = parts[1]
            elif 'Pixel Size ' in line:
                parts = line.split('Pixel Size =')[1].strip().split('(')[1].split(')')[0].split(',')
                Post_LON = parts[0]
                Post_LAT = parts[1]
            elif 'Size is' in line:
                parts = line.split('Size is')[1].strip().split(',')
                WIDTH = parts[0]
                FILE_LENGTH = parts[1]
    
    # 删除临时文件
    if os.path.exists(info_file):
        os.remove(info_file)
    
    # 写入参数文件
    if processor == 'gamma':
        write_dempar_file(dem_par_file, Corner_LON, Corner_LAT, Post_LON, Post_LAT, WIDTH, FILE_LENGTH, DATA_FORMAT)
    else:
        write_demrsc_file(dem_par_file, Corner_LON, Corner_LAT, Post_LON, Post_LAT, WIDTH, FILE_LENGTH)
    
    print(f"{byteorder} endian {dem_file} and {dem_par_file} are generated.")
    print(f"{processor_name} 格式转换完成!")

def get_sufix(STR):
    """Get file extension"""
    n = len(STR.split('.'))
    SUFIX = STR.split('.')[n-1]
    return SUFIX

def read_region(STR):
    """Parse region string 'west/east/south/north'"""
    WEST = STR.split('/')[0]
    EAST = STR.split('/')[1].split('/')[0]
    SOUTH = STR.split(EAST+'/')[1].split('/')[0]
    NORTH = STR.split(EAST+'/')[1].split('/')[1]
    WEST = float(WEST)
    SOUTH = float(SOUTH)
    EAST = float(EAST)
    NORTH = float(NORTH)
    return WEST, SOUTH, EAST, NORTH

def cmd_init(lon, lat, save_path):
    s_lon = str(abs(lon))
    s_lat = str(abs(lat))
    if abs(lon) < 10:
        s_lon = "00" + str(abs(lon))
    elif abs(lon) < 100:
        s_lon = "0" + str(abs(lon))
    if abs(lat) < 10:
        s_lat = "0" + str(abs(lat))
    if lon < 0:
        c_lon = "W" + str(s_lon)
    else:
        c_lon = "E" + str(s_lon)
    if lat < 0:
        c_lat = "S" + str(s_lat)
    else:
        c_lat = "N" + str(s_lat)
    cmd = "aws s3 cp --no-sign-request" + " s3://copernicus-dem-{0}m/Copernicus_DSM_COG_{1}_{2}_00_{3}_00_DEM/ {4} --recursive".format(
        str(resolutions), str(int(resolutions / 3)), str(c_lat), str(c_lon), save_path)
    return cmd

def get_remote_file(lon, lat, save_path, max_retries=3):
    """Get Copernicus Dem by lon and lat with retry mechanism

    Args:
        lon (number): lontitude
        lat (number): latitude
        save_path (str): directory to save data
        max_retries (int): maximum retry attempts
    """
    lon = int(lon)
    lat = int(lat)
    cmd = cmd_init(lon, lat, save_path)
    
    for attempt in range(max_retries):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                print(f"✓ Successfully downloaded: lon={lon}, lat={lat}")
                return True
            else:
                if attempt < max_retries - 1:
                    print(f"✗ Failed (attempt {attempt+1}/{max_retries}): lon={lon}, lat={lat}, retrying...")
                    import time
                    time.sleep(2)
                else:
                    print(f"✗ Failed after {max_retries} attempts: lon={lon}, lat={lat}")
                    return False
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                print(f"⏱ Timeout (attempt {attempt+1}/{max_retries}): lon={lon}, lat={lat}, retrying...")
                import time
                time.sleep(2)
            else:
                print(f"✗ Timeout after {max_retries} attempts: lon={lon}, lat={lat}")
                return False
        except Exception as e:
            print(f"✗ Exception occurred for lon={lon}, lat={lat}: {e}")
            return False
    
    return False

def get_remote_file_batch(west, east, south, north, save_path, num_workers=4):
    """Get Copernicus Dems by WESN extent with parallel download

    Args:
        west (number): western longitude boundary
        east (number): eastern longitude boundary
        south (number): southern latitude boundary
        north (number): northern latitude boundary
        save_path (str): directory to save data
        num_workers (int): number of parallel download workers (default: 4)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm
    
    west = int(west)
    east = int(east)
    south = int(south)
    north = int(north)
    
    # 生成所有需要下载的坐标对
    coords = [(lon, lat) for lon in range(west, east + 1) for lat in range(south, north + 1)]
    total_tiles = len(coords)
    
    print(f"\n{'='*80}")
    print(f"并行下载 Copernicus DEM 数据")
    print(f"{'='*80}")
    print(f"区域范围: {west}°E - {east}°E, {south}°N - {north}°N")
    print(f"总瓦片数: {total_tiles}")
    print(f"并行线程: {num_workers}")
    print(f"{'='*80}\n")
    
    success_count = 0
    failed_count = 0
    
    # 使用线程池并行下载
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        # 提交所有下载任务
        future_to_coord = {executor.submit(get_remote_file, lon, lat, save_path): (lon, lat) 
                          for lon, lat in coords}
        
        # 使用 tqdm 显示进度条
        with tqdm(total=total_tiles, desc="下载进度", unit="瓦片") as pbar:
            for future in as_completed(future_to_coord):
                coord = future_to_coord[future]
                try:
                    result = future.result()
                    if result:
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    print(f"\n✗ 下载失败 {coord}: {e}")
                    failed_count += 1
                
                pbar.update(1)
    
    # 打印统计信息
    print(f"\n{'='*80}")
    print(f"下载完成统计")
    print(f"{'='*80}")
    print(f"成功: {success_count}/{total_tiles} 瓦片")
    print(f"失败: {failed_count}/{total_tiles} 瓦片")
    if failed_count > 0:
        print(f"\n⚠️  有 {failed_count} 个瓦片下载失败,可能会影响 DEM 完整性")
    print(f"{'='*80}\n")

def download_nasadem(west, south, east, north, save_path, cell_size_deg=0.000277778):
    """Download NASADEM data using NASADEM library
    
    Args:
        west (float): western longitude boundary
        south (float): southern latitude boundary
        east (float): eastern longitude boundary
        north (float): northern latitude boundary
        save_path (str): directory to save data
        cell_size_deg (float): cell size in degrees (default: 0.000277778 ≈ 30m at equator)
    
    Returns:
        str: path to the downloaded DEM file, or None if failed
    """
    if not HAS_NASADEM:
        print("✗ NASADEM library not available. Cannot download NASADEM.")
        print("  Install with: pip install nasadem rasters")
        return None
    
    print(f"\n{'='*80}")
    print(f"下载 NASADEM 数据")
    print(f"{'='*80}")
    print(f"区域范围: {west}°E - {east}°E, {south}°N - {north}°N")
    print(f"分辨率: {cell_size_deg}° (约 {cell_size_deg * 111000:.0f} 米)")
    print(f"{'='*80}\n")
    
    try:
        from NASADEM.NASADEM import NASADEMConnection
        from rasters import RasterGrid
        import time
        
        start_time = time.time()
        
        # 定义目标区域
        geometry = RasterGrid.from_bbox(
            xmin=west, ymin=south, xmax=east, ymax=north,
            cell_size=cell_size_deg,
            crs="EPSG:4326"
        )
        
        print(f"RasterGrid 维度: {geometry.shape}")
        print(f"正在从NASA服务器下载高程数据...")
        print(f"提示: 首次下载需要从NASA服务器获取数据,可能需要几分钟时间...")
        
        # 创建NASA DEM连接并获取高程数据
        conn = NASADEMConnection()
        elevation = conn.elevation_m(geometry)
        
        elapsed_time = time.time() - start_time
        
        print(f"\n✓ 下载完成!")
        print(f"  高程范围: {float(elevation.min()):.1f} 到 {float(elevation.max()):.1f} 米")
        print(f"  数据维度: {elevation.shape}")
        print(f"  用时: {elapsed_time:.1f} 秒")
        
        # 保存为 GeoTIFF
        output_file = os.path.join(save_path, f"NASADEM_{west}_{east}_{south}_{north}.tif")
        print(f"\n保存到: {output_file}")
        
        # 检查elevation对象是否有save方法
        if hasattr(elevation, 'save'):
            elevation.save(output_file)
            print(f"✓ 文件保存成功: {output_file}")
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file) / (1024 * 1024)
                print(f"  文件大小: {file_size:.2f} MB")
            return output_file
        
        # 否则使用 rasterio 保存
        try:
            import rasterio
            from rasterio.crs import CRS
            from rasterio.transform import from_bounds
            
            # 获取数据数组
            if hasattr(elevation, 'read'):
                elev_array = elevation.read()
            else:
                elev_array = np.array(elevation)
            
            # 计算变换矩阵
            height, width = elev_array.shape
            transform = from_bounds(west, south, east, north, width, height)
            
            # 写入文件
            with rasterio.open(
                output_file,
                'w',
                driver='GTiff',
                height=height,
                width=width,
                count=1,
                dtype=elev_array.dtype,
                crs=CRS.from_epsg(4326),
                transform=transform,
                nodata=-32767,
                compress='LZW'
            ) as dst:
                dst.write(elev_array, 1)
            
            print(f"✓ 文件保存成功: {output_file}")
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file) / (1024 * 1024)
                print(f"  文件大小: {file_size:.2f} MB")
            return output_file
            
        except ImportError:
            print("⚠️  rasterio 未安装,尝试使用 GDAL...")
            
            # 使用 GDAL 创建 GeoTIFF
            width, height = geometry.width, geometry.height
            gdal_cmd = f"gdal_create -outsize {width} {height} -of GTiff -co COMPRESS=LZW -a_srs EPSG:4326 -a_ullr {west} {north} {east} {south} {output_file}"
            result = os.system(gdal_cmd)
            
            if result == 0 and os.path.exists(output_file):
                print(f"✓ 文件创建成功: {output_file}")
                return output_file
            else:
                print(f"✗ 文件创建失败")
                return None
    
    except Exception as e:
        print(f"\n✗ 下载失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def download_srtm(west, south, east, north, save_path, srtm_data_dir=None, cell_size=0.000277778):
    """Download SRTM data using srtm library (requires pre-downloaded .hgt files)
    
    Args:
        west (float): western longitude boundary
        south (float): southern latitude boundary
        east (float): eastern longitude boundary
        north (float): northern latitude boundary
        save_path (str): directory to save output DEM
        srtm_data_dir (str): directory containing SRTM .hgt files (required)
        cell_size (float): cell size in degrees (default: 0.000277778 ≈ 30m)
    
    Returns:
        str: path to the generated DEM file, or None if failed
    
    Note:
        This function requires SRTM .hgt files to be pre-downloaded.
        You can download SRTM data from:
        - https://srtm.csi.cgiar.org/
        - https://earthexplorer.usgs.gov/
        
        Python version requirement:
        - srtm library requires Python >= 3.12
        - For Python < 3.12, use process_srtm_hgt_files() instead
    """
    if not HAS_SRTM:
        print("✗ srtm library not available. Cannot use SRTM.")
        print("  Install with: pip install srtm (requires Python >= 3.12)")
        print(f"  Current Python version: {sys.version_info.major}.{sys.version_info.minor}")
        print("  Alternative: Use process_srtm_hgt_files() to manually process .hgt files")
        return None
    
    if srtm_data_dir is None or not os.path.isdir(srtm_data_dir):
        print("✗ SRTM data directory not provided or does not exist.")
        print("  Please download SRTM .hgt files and specify the directory.")
        print("  Download sources:")
        print("    - https://srtm.csi.cgiar.org/")
        print("    - https://earthexplorer.usgs.gov/")
        return None
    
    print(f"\n{'='*80}")
    print(f"使用 SRTM 数据生成 DEM")
    print(f"{'='*80}")
    print(f"区域范围: {west}°E - {east}°E, {south}°N - {north}°N")
    print(f"SRTM数据目录: {srtm_data_dir}")
    print(f"分辨率: {cell_size}° (约 {cell_size * 111000:.0f} 米)")
    print(f"{'='*80}\n")
    
    try:
        import time
        from tqdm import tqdm
        
        start_time = time.time()
        
        # 创建 SrtmService
        service = srtm.SrtmService(srtm_data_dir, cache_size=100)
        print(f"✓ SrtmService 创建成功")
        
        # 生成坐标网格
        lats = np.arange(south, north + cell_size, cell_size)
        lons = np.arange(west, east + cell_size, cell_size)
        
        print(f"生成网格: {len(lats)} × {len(lons)} = {len(lats) * len(lons)} 个点")
        
        # 批量查询高程
        coords = [(lat, lon) for lat in lats for lon in lons]
        print(f"正在查询高程数据...")
        
        elevations = service.get_elevations_batch(coords, default=0)
        
        # 重塑为数组
        elevation_array = np.array(elevations, dtype=np.float32).reshape(len(lats), len(lons))
        
        # 替换0值为NaN(可能是缺失数据)
        elevation_array[elevation_array == 0] = np.nan
        
        elapsed_time = time.time() - start_time
        
        print(f"\n✓ 查询完成!")
        print(f"  有效数据: {np.sum(~np.isnan(elevation_array))} / {elevation_array.size} 点")
        if np.sum(~np.isnan(elevation_array)) > 0:
            print(f"  高程范围: {np.nanmin(elevation_array):.1f} 到 {np.nanmax(elevation_array):.1f} 米")
        print(f"  用时: {elapsed_time:.1f} 秒")
        
        # 保存为 GeoTIFF
        output_file = os.path.join(save_path, f"SRTM_{west}_{east}_{south}_{north}.tif")
        print(f"\n保存到: {output_file}")
        
        try:
            import rasterio
            from rasterio.crs import CRS
            from rasterio.transform import from_bounds
            
            height, width = elevation_array.shape
            transform = from_bounds(west, south, east, north, width, height)
            
            # 替换NaN为nodata值
            elevation_array[np.isnan(elevation_array)] = -32767
            
            with rasterio.open(
                output_file,
                'w',
                driver='GTiff',
                height=height,
                width=width,
                count=1,
                dtype=np.float32,
                crs=CRS.from_epsg(4326),
                transform=transform,
                nodata=-32767,
                compress='LZW'
            ) as dst:
                dst.write(elevation_array.astype(np.float32), 1)
            
            print(f"✓ 文件保存成功: {output_file}")
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file) / (1024 * 1024)
                print(f"  文件大小: {file_size:.2f} MB")
            return output_file
            
        except ImportError:
            print("⚠️  rasterio 未安装,无法保存文件")
            return None
    
    except Exception as e:
        print(f"\n✗ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def split_large_region(west, south, east, north, tile_size=1.0):
    """将大区域分割成小块
    
    Args:
        west, south, east, north: 区域边界
        tile_size: 分块大小（度），默认1°×1°
    
    Returns:
        list: 分块列表 [(west, south, east, north), ...]
    """
    tiles = []
    
    w = west
    while w < east:
        e = min(w + tile_size, east)
        
        s = south
        while s < north:
            n = min(s + tile_size, north)
            tiles.append((w, s, e, n))
            s += tile_size
        
        w += tile_size
    
    return tiles


def download_srtm_cgiar(west, south, east, north, save_path, api_key=None, num_workers=4, dem_type='SRTMGL1', auto_tile=True):
    """Download DEM data from OpenTopography automatically
    
    Args:
        west (float): western longitude boundary
        south (float): southern latitude boundary
        east (float): eastern longitude boundary
        north (float): northern latitude boundary
        save_path (str): directory to save output DEM
        api_key (str): OpenTopography API key (optional, will prompt if not provided)
        num_workers (int): number of parallel downloads (not used for API, kept for compatibility)
        dem_type (str): DEM type (default: 'SRTMGL1')
            - SRTMGL1: SRTM GL1 30m (recommended for SRTM)
            - SRTMGL3: SRTM GL3 90m
            - NASADEM: NASADEM 30m
            - COP30: Copernicus 30m
            - COP90: Copernicus 90m
        auto_tile (bool): 自动分块下载大区域
    
    Returns:
        str: path to the generated DEM file, or None if failed
    
    Note:
        OpenTopography API provides:
        - SRTMGL1: SRTM GL1 30m (recommended)
        - SRTMGL3: SRTM GL3 90m
        - NASADEM: NASADEM Global DEM 30m
        - COP30: Copernicus 30m
        - COP90: Copernicus 90m
        
        API Documentation: https://portal.opentopography.org/apidocs/
        Get API key: https://opentopography.org/myOpenTopo
    """
    # DEM类型信息
    dem_info = {
        'SRTMGL1': {'resolution': '30m', 'name': 'SRTM GL1'},
        'SRTMGL3': {'resolution': '90m', 'name': 'SRTM GL3'},
        'NASADEM': {'resolution': '30m', 'name': 'NASADEM'},
        'COP30': {'resolution': '30m', 'name': 'Copernicus 30m'},
        'COP90': {'resolution': '90m', 'name': 'Copernicus 90m'}
    }
    
    info = dem_info.get(dem_type, {'resolution': 'unknown', 'name': dem_type})
    
    print(f"\n{'='*80}")
    print(f"从 OpenTopography 自动下载 DEM 数据")
    print(f"{'='*80}")
    print(f"区域范围: {west}°E - {east}°E, {south}°N - {north}°N")
    print(f"数据源: {info['name']} ({info['resolution']} resolution)")
    print(f"{'='*80}\n")
    
    # 计算区域面积
    region_area = (east - west) * (north - south)
    
    # 如果区域较大，自动分块
    if auto_tile and region_area > 4:
        print(f"⚠️  区域较大 ({region_area:.1f} 平方度)，将自动分块下载以提高稳定性")
        
        # 计算分块
        tiles = split_large_region(west, south, east, north, tile_size=1.0)
        print(f"  将分成 {len(tiles)} 个 1°×1° 的区块下载\n")
        
        # 创建临时目录存放分块文件
        temp_dir = os.path.join(save_path, f"temp_tiles_{dem_type}_{west}_{east}_{south}_{north}")
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
        
        # 下载每个分块
        downloaded_files = []
        failed_tiles = []
        
        for i, (w, s, e, n) in enumerate(tiles, 1):
            print(f"\n{'='*80}")
            print(f"下载分块 {i}/{len(tiles)}: {w}°E-{e}°E, {s}°N-{n}°N")
            print(f"{'='*80}")
            
            tile_file = download_single_tile(w, s, e, n, temp_dir, api_key, dem_type, info)
            
            if tile_file:
                downloaded_files.append(tile_file)
                print(f"✓ 分块 {i}/{len(tiles)} 下载成功")
            else:
                failed_tiles.append((w, s, e, n))
                print(f"✗ 分块 {i}/{len(tiles)} 下载失败")
        
        # 如果有失败的分块
        if failed_tiles:
            print(f"\n⚠️  有 {len(failed_tiles)} 个分块下载失败")
            print("失败的分块:")
            for w, s, e, n in failed_tiles:
                print(f"  {w}°E-{e}°E, {s}°N-{n}°N")
        
        # 合并所有分块
        if downloaded_files:
            print(f"\n{'='*80}")
            print(f"合并 {len(downloaded_files)} 个分块...")
            print(f"{'='*80}\n")
            
            merged_file = merge_dem_tiles(downloaded_files, save_path, dem_type, west, east, south, north)
            
            # 清理临时文件
            import shutil
            try:
                shutil.rmtree(temp_dir)
                print("✓ 临时文件已清理")
            except:
                pass
            
            if merged_file:
                print(f"\n✓ 最终输出文件: {merged_file}")
                return merged_file
            else:
                print("✗ 合并失败")
                return None
        else:
            print("✗ 所有分块下载失败")
            return None
    
    # 小区域直接下载
    else:
        return download_single_tile(west, south, east, north, save_path, api_key, dem_type, info)


def download_single_tile(west, south, east, north, save_path, api_key, dem_type, dem_info):
    """下载单个DEM块
    
    Args:
        west, south, east, north: 区域边界
        save_path: 保存路径
        api_key: OpenTopography API key
        dem_type: DEM类型
        dem_info: DEM信息字典
    
    Returns:
        str: 下载的文件路径，失败返回None
    """
    try:
        import time
        import urllib.request
        import urllib.parse
        
        start_time = time.time()
        
        # OpenTopography API endpoint
        base_url = "https://portal.opentopography.org/API/globaldem"
        
        # 如果没有提供API key，提示用户获取
        if api_key is None:
            print("⚠️  需要OpenTopography API key才能下载SRTM数据")
            print("\n获取免费API key的步骤:")
            print("  1. 访问: https://opentopography.org/myOpenTopo")
            print("  2. 注册免费账户")
            print("  3. 在 'My Account' 页面请求API key")
            print("  4. 使用 --opentopo-api-key 参数提供API key")
            print("\n或者使用其他DEM数据源:")
            print("  - Copernicus (推荐): --dem-source copernicus")
            print("  - NASADEM: --dem-source nasadem")
            return None
        
        # 构建请求参数
        params = {
            'demtype': dem_type,
            'south': south,
            'north': north,
            'west': west,
            'east': east,
            'outputFormat': 'GTiff',
            'API_Key': api_key
        }
        
        # 构建完整URL
        url = base_url + '?' + urllib.parse.urlencode(params)
        
        # 输出文件名
        output_file = os.path.join(save_path, f"{dem_type}_{west}_{east}_{south}_{north}.tif")
        
        # 下载文件 - 使用curl以获得更好的稳定性
        max_retries = 3
        retry_count = 0
        download_success = False
        
        while retry_count < max_retries and not download_success:
            try:
                retry_count += 1
                print(f"正在从OpenTopography下载数据... (尝试 {retry_count}/{max_retries})")
                
                # 尝试使用 curl 下载（更稳定）
                try:
                    import subprocess
                    curl_cmd = ['curl', '-L', '-o', output_file, '-s', '--show-error', url]
                    result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=1800)  # 30分钟超时
                    
                    if result.returncode != 0:
                        raise Exception(f"curl下载失败: {result.stderr}")
                    
                    print("  ✓ curl下载完成")
                    download_success = True
                    
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    # 如果curl不可用或超时，使用urllib
                    print("  使用urllib下载...")
                    urllib.request.urlretrieve(url, output_file)
                    download_success = True
                    
            except urllib.error.HTTPError as e:
                print(f"\n✗ HTTP错误: {e.code} {e.reason}")
                if e.code == 401:
                    print("  API key无效或已过期")
                    print("  请获取新的API key: https://opentopography.org/myOpenTopo")
                elif e.code == 403:
                    print("  访问被拒绝，可能是:")
                    print("  - 请求区域过大")
                    print("  - API key配额已用完")
                    print("  - 需要注册获取免费API key")
                if retry_count < max_retries:
                    print(f"  将在3秒后重试...")
                    time.sleep(3)
                    continue
                return None
            except Exception as e:
                print(f"\n✗ 下载失败: {e}")
                if retry_count < max_retries:
                    print(f"  将在3秒后重试...")
                    time.sleep(3)
                    continue
                return None
        
        # 检查文件是否有效
        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file)
            
            # 如果文件很小，可能是错误消息
            if file_size < 1000:
                with open(output_file, 'r') as f:
                    error_msg = f.read()
                print(f"\n✗ 下载失败: {error_msg}")
                os.remove(output_file)
                return None
            
            elapsed_time = time.time() - start_time
            
            print(f"\n✓ 下载完成!")
            print(f"  输出文件: {output_file}")
            print(f"  文件大小: {file_size / (1024 * 1024):.2f} MB")
            print(f"  用时: {elapsed_time:.1f} 秒")
            
            # 验证文件完整性
            print("\n验证文件完整性...")
            
            try:
                import rasterio
                with rasterio.open(output_file) as src:
                    # 尝试读取多个位置的数据验证
                    print(f"  文件大小: {src.width} x {src.height} 像素")
                    
                    # 读取多个测试点
                    test_points = [
                        (0, 0, min(100, src.height), min(100, src.width)),  # 左上角
                        (max(0, src.height-100), max(0, src.width-100), src.height, src.width),  # 右下角
                        (src.height//2-50, src.width//2-50, src.height//2+50, src.width//2+50),  # 中间
                    ]
                    
                    for i, (row_start, col_start, row_end, col_end) in enumerate(test_points):
                        try:
                            window = ((row_start, row_end), (col_start, col_end))
                            test_data = src.read(1, window=window)
                            print(f"  ✓ 测试点 {i+1}/3 验证成功")
                        except Exception as e:
                            print(f"  ✗ 测试点 {i+1}/3 验证失败: {e}")
                            raise
                    
                    print(f"  ✓ 所有验证点通过，文件完整")
                    return output_file
                    
            except Exception as e:
                print(f"  ✗ 文件验证失败: {e}")
                print(f"  文件可能已损坏，正在删除...")
                try:
                    os.remove(output_file)
                except:
                    pass
                return None
        else:
            print("✗ 文件下载失败")
            return None
            
    except Exception as e:
        print(f"\n✗ 下载失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def merge_dem_tiles(tile_files, save_path, dem_type, west, east, south, north):
    """合并多个DEM分块文件
    
    Args:
        tile_files: 分块文件列表
        save_path: 保存路径
        dem_type: DEM类型
        west, east, south, north: 最终区域边界
    
    Returns:
        str: 合并后的文件路径，失败返回None
    """
    if not tile_files:
        print("✗ 没有文件需要合并")
        return None
    
    try:
        import rasterio
        from rasterio.merge import merge
        import time
        
        print(f"开始合并 {len(tile_files)} 个分块文件...")
        
        # 读取所有分块
        src_files_to_mosaic = []
        for tile_file in tile_files:
            src = rasterio.open(tile_file)
            src_files_to_mosaic.append(src)
        
        # 合并
        start_time = time.time()
        mosaic, out_trans = merge(src_files_to_mosaic)
        
        # 获取输出元数据
        out_meta = src_files_to_mosaic[0].meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": out_trans,
            "compress": "lzw"
        })
        
        # 输出文件名
        output_file = os.path.join(save_path, f"{dem_type}_{west}_{east}_{south}_{north}.tif")
        
        # 写入合并后的文件
        with rasterio.open(output_file, "w", **out_meta) as dest:
            dest.write(mosaic)
        
        # 关闭所有源文件
        for src in src_files_to_mosaic:
            src.close()
        
        elapsed_time = time.time() - start_time
        file_size = os.path.getsize(output_file) / (1024 * 1024)
        
        print(f"✓ 合并完成!")
        print(f"  输出文件: {output_file}")
        print(f"  文件大小: {file_size:.2f} MB")
        print(f"  用时: {elapsed_time:.1f} 秒")
        
        return output_file
        
    except ImportError:
        print("✗ rasterio 未安装，无法合并文件")
        print("  安装方法: pip install rasterio")
        return None
    except Exception as e:
        print(f"✗ 合并失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def download_srtm_cgiar_old(west, south, east, north, save_path, num_workers=4):
    """Download SRTM data from CSI-CGIAR (DEPRECATED - tiles not accessible)
    
    This function is kept as backup but CSI-CGIAR tile downloads are not reliable.
    Use download_srtm_cgiar() with OpenTopography API instead.
    """
    print("警告: CSI-CGIAR瓦片下载不可靠，建议使用OpenTopography API")
    return None

def process_srtm_hgt_files(west, south, east, north, srtm_data_dir, save_path, output_name=None):
    """Process SRTM .hgt files manually (alternative for Python < 3.12)
    
    Args:
        west (float): western longitude boundary
        south (float): southern latitude boundary
        east (float): eastern longitude boundary
        north (float): northern latitude boundary
        srtm_data_dir (str): directory containing SRTM .hgt files
        save_path (str): directory to save output DEM
        output_name (str): output filename (without extension)
    
    Returns:
        str: path to the generated DEM file, or None if failed
    
    Note:
        SRTM .hgt files are 1°×1° tiles with 1201×1201 pixels (3 arc-second) or
        3601×3601 pixels (1 arc-second). This function reads and merges them.
    """
    print(f"\n{'='*80}")
    print(f"手动处理 SRTM .hgt 文件")
    print(f"{'='*80}")
    print(f"区域范围: {west}°E - {east}°E, {south}°N - {north}°N")
    print(f"SRTM数据目录: {srtm_data_dir}")
    print(f"{'='*80}\n")
    
    try:
        import rasterio
        from rasterio.merge import merge
        from rasterio.crs import CRS
        import time
        
        start_time = time.time()
        
        # 查找需要的SRTM文件
        hgt_files = []
        for lat in range(int(south), int(north) + 1):
            for lon in range(int(west), int(east) + 1):
                # 构造文件名
                lat_prefix = 'N' if lat >= 0 else 'S'
                lon_prefix = 'E' if lon >= 0 else 'W'
                
                # SRTM文件名格式: N39E116.hgt
                filename = f"{lat_prefix}{abs(lat):02d}{lon_prefix}{abs(lon):03d}.hgt"
                filepath = os.path.join(srtm_data_dir, filename)
                
                if os.path.exists(filepath):
                    hgt_files.append(filepath)
                    print(f"  ✓ 找到文件: {filename}")
                else:
                    print(f"  ⚠ 文件缺失: {filename}")
        
        if not hgt_files:
            print("\n✗ 没有找到任何SRTM文件!")
            return None
        
        print(f"\n找到 {len(hgt_files)} 个SRTM文件")
        
        # 使用GDAL合并.hgt文件
        merged_file = os.path.join(save_path, f"SRTM_merged_{west}_{east}_{south}_{north}.tif")
        
        # 构建gdal_merge命令
        gdal_merge_cmd = ['gdal_merge.py', '-o', merged_file, '-of', 'GTiff', '-co', 'COMPRESS=LZW']
        gdal_merge_cmd.extend(hgt_files)
        
        print(f"\n使用GDAL合并文件...")
        result = subprocess.run(gdal_merge_cmd, capture_output=True, text=True)
        
        if result.returncode != 0 or not os.path.exists(merged_file):
            print(f"✗ GDAL合并失败: {result.stderr}")
            return None
        
        print(f"✓ 合并完成: {merged_file}")
        
        # 裁剪到目标范围
        if output_name is None:
            output_name = os.path.join(save_path, f"SRTM_{west}_{east}_{south}_{north}")
        
        output_file = output_name + '.tif'
        
        gdal_warp_cmd = [
            'gdalwarp',
            '-te', str(west), str(south), str(east), str(north),
            '-of', 'GTiff',
            '-co', 'COMPRESS=LZW',
            merged_file,
            output_file
        ]
        
        print(f"\n裁剪到目标范围...")
        result = subprocess.run(gdal_warp_cmd, capture_output=True, text=True)
        
        if result.returncode != 0 or not os.path.exists(output_file):
            print(f"✗ GDAL裁剪失败: {result.stderr}")
            return None
        
        elapsed_time = time.time() - start_time
        
        print(f"\n✓ SRTM DEM 生成完成!")
        print(f"  输出文件: {output_file}")
        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file) / (1024 * 1024)
            print(f"  文件大小: {file_size:.2f} MB")
        print(f"  用时: {elapsed_time:.1f} 秒")
        
        # 清理临时文件
        if os.path.exists(merged_file) and merged_file != output_file:
            os.remove(merged_file)
            print(f"  清理临时文件: {merged_file}")
        
        return output_file
    
    except ImportError:
        print("✗ rasterio 未安装,无法处理文件")
        return None
    except Exception as e:
        print(f"\n✗ 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def merge_tif_files(input_pattern, output_file):
    """合并多个 TIF 文件为一个文件

    Args:
        input_pattern (str): 输入 TIF 文件的匹配模式
        output_file (str): 输出合并后的 TIF 文件路径
    """
    # 查找所有匹配的 TIF 文件
    tif_files = sorted(glob.glob(input_pattern))
    
    if not tif_files:
        print(f"未找到匹配的 TIF 文件: {input_pattern}")
        return False
    
    print(f"\n{'='*80}")
    print(f"合并 DEM 瓦片")
    print(f"{'='*80}")
    print(f"找到 {len(tif_files)} 个 TIF 文件待合并")
    print(f"输出文件: {output_file}")
    print(f"{'='*80}\n")
    
    # 使用 gdal_merge.py 合并文件
    try:
        start_time = __import__('time').time()
        cmd = ["gdal_merge.py", "-o", output_file, "-co", "COMPRESS=LZW", "-co", "BIGTIFF=YES"] + tif_files
        print(f"执行合并命令...")
        
        # 使用 tqdm 显示进度
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        elapsed_time = __import__('time').time() - start_time
        
        if result.returncode == 0:
            # 检查输出文件大小
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file) / (1024 * 1024)  # MB
                print(f"\n✓ 成功合并文件!")
                print(f"  输出文件: {output_file}")
                print(f"  文件大小: {file_size:.2f} MB")
                print(f"  用时: {elapsed_time:.1f} 秒")
                print(f"{'='*80}\n")
                return True
        else:
            print(f"\n✗ 合并失败: {result.stderr}")
            print(f"{'='*80}\n")
            return False
    except Exception as e:
        print(f"\n✗ 合并过程中发生错误: {e}")
        print(f"{'='*80}\n")
        return False

def convert_format(input_file, output_file, format_type="GTiff", options=None):
    """转换 TIF 文件格式

    Args:
        input_file (str): 输入文件路径
        output_file (str): 输出文件路径
        format_type (str): 输出格式类型 (默认: GTiff)
        options (list): gdal_translate 的额外选项
    """
    if options is None:
        options = []
    
    try:
        cmd = ["gdal_translate", "-of", format_type] + options + [input_file, output_file]
        print(f"执行转换命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"成功转换文件到: {output_file}")
            return True
        else:
            print(f"转换失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"转换过程中发生错误: {e}")
        return False

def process_dem_files(save_path, west, east, south, north, merge=True, convert=True,
                      output_format="GTiff", convert_options=None, gamma=False, gamma_byteorder='big',
                      processor='gamma', output_name=None, num_workers=4,
                      srtm_data_dir=None, opentopo_api_key=None, opentopo_dem_type='SRTMGL1',
                      fabdem_dir=None):
    """处理下载的 DEM 文件：下载、合并和转换

    Args:
        save_path (str): 数据保存目录
        west (float): 西经边界
        east (float): 东经边界
        south (float): 南纬边界
        north (float): 北纬边界
        merge (bool): 是否合并文件
        convert (bool): 是否转换格式
        output_format (str): 输出格式
        convert_options (list): 转换选项
        gamma (bool): 是否转换为 Gamma/ROI_PAC 格式
        gamma_byteorder (str): 字节序 ('big' 或 'little')
        processor (str): 处理器类型 ('gamma' 或 'roi_pac')
        output_name (str): 输出文件名（不含扩展名）
        num_workers (int): 并行下载线程数 (default: 4)
        dem_source (str): DEM数据源 ('copernicus', 'nasadem' 或 'srtm', default: 'copernicus')
        srtm_data_dir (str): SRTM数据目录 (仅srtm源需要)
    """
    if convert_options is None:
        convert_options = ["-co", "COMPRESS=LZW", "-co", "TILED=YES"]
    
    # ============ 优先尝试本地 FABDEM 瓦片库 ============
    if fabdem_dir and os.path.isdir(fabdem_dir):
        print(f"\n{'='*80}")
        print(f"优先使用本地 FABDEM 瓦片库: {fabdem_dir}")
        print(f"{'='*80}")
        try:
            from make_local_dem import (find_needed_tiles, extract_tiles_from_zips,
                                        tiles_to_gamma_dem)
            import tempfile, shutil
            
            tiles = find_needed_tiles(west, south, east, north)
            print(f"需要 {len(tiles)} 个 1°×1° FABDEM 瓦片")
            
            temp_dir = tempfile.mkdtemp(prefix='fabdem_tiles_')
            try:
                tif_files = extract_tiles_from_zips(tiles, fabdem_dir, temp_dir)
                if tif_files:
                    # 一步完成: VRT → ENVI 二进制 → .dem（仅一次大文件写入）
                    out_name = output_name if output_name else os.path.join(save_path, 'out')
                    dem_file, dem_par_file = tiles_to_gamma_dem(
                        tif_files, out_name, west, south, east, north, gamma_byteorder)
                    if dem_file and dem_par_file:
                        print(f"\n✓ 本地 FABDEM 生成 GAMMA DEM 成功!")
                        print(f"  DEM: {dem_file}")
                        print(f"  PAR: {dem_par_file}")
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        return
                    else:
                        print("⚠ 本地 FABDEM 转换失败，回退到网络下载")
                else:
                    print("⚠ 未从本地 FABDEM 提取到瓦片，回退到网络下载")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        except ImportError as e:
            print(f"⚠ 无法导入 make_local_dem 模块 ({e})，回退到网络下载")
        except Exception as e:
            print(f"⚠ 本地 FABDEM 处理异常 ({e})，回退到网络下载")
    
    # ============ 回退: 网络下载 ============
    # 检查是否使用预下载的SRTM数据
    if srtm_data_dir and os.path.isdir(srtm_data_dir):
        print(f"\n{'='*80}")
        print(f"使用预下载的 SRTM 数据")
        print(f"{'='*80}")
        
        if HAS_SRTM:
            # Python >= 3.12, 使用srtm库
            dem_file = download_srtm(west, south, east, north, save_path, srtm_data_dir)
        else:
            # Python < 3.12, 使用手动处理方法
            print("注意: srtm库需要Python >= 3.12,使用手动处理方法")
            dem_file = process_srtm_hgt_files(west, south, east, north, srtm_data_dir, 
                                             save_path, output_name)
    else:
        # 自动从OpenTopography下载
        print(f"\n{'='*80}")
        print(f"开始下载 DEM 数据...")
        print(f"{'='*80}")
        print("自动从OpenTopography下载DEM数据")
        dem_file = download_srtm_cgiar(west, south, east, north, save_path, 
                                       api_key=opentopo_api_key, num_workers=num_workers,
                                       dem_type=opentopo_dem_type)
    
    if dem_file is None:
        print("✗ DEM 生成失败,退出")
        return
    
    current_file = dem_file
    
    # 生成输出文件名
    if output_name is None:
        output_name = os.path.join(save_path, f"DEM_W{int(west)}_E{int(east)}_S{int(south)}_N{int(north)}")
    
    # 转换到 Gamma/ROI_PAC 格式
    if gamma:
        print("\n开始转换到 {} 格式...".format(processor.upper()))
        convert_to_gamma(current_file, output_name, gamma_byteorder, processor)
    
    # 转换到其他格式（非 Gamma/ROI_PAC）
    if not gamma and convert:
        print("\n开始转换文件格式...")
        converted_file = os.path.join(save_path, f"final_DEM_W{int(west)}_E{int(east)}_S{int(south)}_N{int(north)}.tif")
        convert_format(current_file, converted_file, output_format, convert_options)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Download DEM data from OpenTopography and convert to Gamma/ROI_PAC format.',
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # 与 makedem.py 相同的参数
    parser.add_argument('-r', dest='region', help='Research region, west/east/south/north (e.g., 106/110/36/40)')
    parser.add_argument('-d', dest='dem', help='Raw dem file that used for further processing')
    parser.add_argument('-s', dest='par', help='SLC parameter file of SAR image used for determining research region')
    parser.add_argument('-p', dest='processor', help='Interferometry processor. [ gamma or roi_pac ] [default: gamma]')
    parser.add_argument('-o', dest='out', help='Output name of the generated DEM')
    parser.add_argument('--byteorder', dest='byteorder', choices=['big', 'little'],
                        help='Byteorder of the generated DEM: big or little. [default: big for gamma and little for roi_pac]')
    parser.add_argument('--dir', dest='PATH', help='Processing directory for generating DEM. [default: Current directory]')
    parser.add_argument('--num-workers', dest='num_workers', type=int, default=4,
                        help='Number of parallel download workers (used for batch processing). [default: 4]')
    parser.add_argument('--srtm-data-dir', dest='srtm_data_dir', default=None,
                        help='Directory containing SRTM .hgt files (optional).\n'
                             'If not provided, DEM data will be automatically downloaded from OpenTopography.\n'
                             'Download SRTM data from: https://srtm.csi.cgiar.org/')
    parser.add_argument('--opentopo-api-key', dest='opentopo_api_key', 
                        default='09ad77d34545607fdf5cb182b64ac64e',
                        help='OpenTopography API key for downloading DEM data.\n'
                             'Get a free API key at: https://opentopography.org/myOpenTopo\n'
                             'Default key is provided, but you can use your own key.')
    parser.add_argument('--opentopo-dem-type', dest='opentopo_dem_type', 
                        choices=['SRTMGL1', 'SRTMGL3', 'NASADEM', 'COP30', 'COP90'],
                        default='SRTMGL1',
                        help='DEM type for OpenTopography download. [default: SRTMGL1]\n'
                             '  - SRTMGL1: SRTM GL1 30m (recommended)\n'
                             '  - SRTMGL3: SRTM GL3 90m\n'
                             '  - NASADEM: NASADEM 30m\n'
                             '  - COP30: Copernicus 30m\n'
                             '  - COP90: Copernicus 90m')
    parser.add_argument('--fabdem-dir', dest='fabdem_dir', default=None,
                        help='Directory containing local FABDEM ZIP tiles (e.g., /mnt/ZYD/全球FABDEM).\n'
                             'If provided, local FABDEM tiles will be used FIRST before network download.\n'
                             'Falls back to OpenTopography download if local tiles are unavailable.')
    
    args = parser.parse_args()
    
    # 确定工作目录
    if args.PATH:
        workdir = args.PATH
    else:
        workdir = os.getcwd()
    
    os.chdir(workdir)
    
    # 确定处理器类型
    if args.processor:
        processor = args.processor
    else:
        processor = 'gamma'
    
    # 确定字节序
    if args.byteorder:
        Byteorder = args.byteorder
    else:
        if processor == 'gamma':
            Byteorder = 'big'
        else:
            Byteorder = 'little'
    
    # 确定输出名称
    if args.out:
        Name = args.out
    else:
        Name = "out"
    
    # 处理已有 DEM 文件的情况
    if args.dem:
        dem = args.dem
        print('Raw dem file is provided: %s.' % dem)
        
        # 转换为 TIF 格式（如果不是 TIF）
        SUFIX = get_sufix(dem)
        if SUFIX != 'tif':
            DTIF = dem.replace('.' + SUFIX, '.tif')
            call_str = f'gdal_translate {dem} -of GTiff {DTIF}'
            os.system(call_str)
            DEM = DTIF
        else:
            DEM = dem
        
        # 转换到 Gamma/ROI_PAC 格式
        convert_to_gamma(DEM, Name, Byteorder, processor)
        
        BB = Byteorder + ' endian'
        print('')
        print('%s %s and %s are generated.' % (BB, Name + '.dem', Name + ('.dem.par' if processor == 'gamma' else '.dem.rsc')))
        print('Congratulations! Done!')
        sys.exit(0)
    
    # 处理从 SLC 参数文件确定区域的情况
    if args.par:
        Par = args.par
        print("SLC_par file is provided: %s" % Par)
        print("DEM over research region will be downloaded automatically based on %s" % Par)
        print("DEM data will be downloaded from OpenTopography")
        call_str = "SLC_corners " + Par + " > corners.txt"
        os.system(call_str)
        
        File = open("corners.txt", "r")
        InfoLine = File.readlines()[8:10]
        File.close()
        
        MinLat = float(InfoLine[0].split(':')[1].split('  max. ')[0])
        MaxLat = float(InfoLine[0].split(':')[2])
        MinLon = float(InfoLine[1].split(':')[1].split('  max. ')[0])
        MaxLon = float(InfoLine[1].split(':')[2])
        
        north = int(MaxLat) + 2
        south = int(MinLat)
        east = int(MaxLon) + 2
        west = int(MinLon)
    elif args.region:
        region = args.region
        west, south, east, north = read_region(region)
    else:
        parser.print_usage()
        sys.exit(os.path.basename(sys.argv[0]) + ': error: research region, raw_demfile and SLC parameter file, at least one is needed.')
    
    print('Research region: %s(west)  %s(south)  %s(east)  %s(north)' % (west, south, east, north))
    print('>>> Ready to download DEM over research region.')
    
    # 处理数据（下载、合并和转换到 Gamma/ROI_PAC 格式）
    process_dem_files(
        save_path=workdir,
        west=west,
        east=east,
        south=south,
        north=north,
        merge=True,
        convert=False,
        gamma=True,
        gamma_byteorder=Byteorder,
        processor=processor,
        output_name=os.path.join(workdir, Name),
        num_workers=args.num_workers,
        srtm_data_dir=args.srtm_data_dir,
        opentopo_api_key=args.opentopo_api_key,
        opentopo_dem_type=args.opentopo_dem_type,
        fabdem_dir=args.fabdem_dir
    )
