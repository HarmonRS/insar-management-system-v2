#!/usr/bin/env python
"""
Geocode dolphin unwrapped phase results with gdal_translate multi-looking and optional visualization
Output file will have the same pixel dimensions as the input phase file
"""

import os
import argparse
import numpy as np
import rasterio
from osgeo import gdal, osr
import tempfile
import shutil
import subprocess
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

def gdal_translate_multilook(input_file, output_file, target_width, target_height, resample_alg='average'):
    """
    Apply multi-looking using gdal_translate with -outsize option
    """
    
    cmd = [
        'gdal_translate',
        '-outsize', str(target_width), str(target_height),
        '-r', resample_alg,
        '-co', 'COMPRESS=LZW',
        input_file,
        output_file
    ]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Error in gdal_translate: {result.stderr}")
        raise RuntimeError(f"gdal_translate failed with return code {result.returncode}")
    
    print(f"Multi-looking completed: {input_file} -> {output_file}")

def geocode_phase_file_same_size(phase_file, lon_file, lat_file, out_file, 
                                 output_srs=4326):
    """
    Geocode a phase file using corresponding lat, lon files while maintaining same pixel dimensions
    
    Parameters:
    -----------
    phase_file : str
        Input unwrapped phase file from dolphin
    lon_file : str
        Longitude file (should match phase file dimensions)
    lat_file : str
        Latitude file (should match phase file dimensions)
    out_file : str
        Output geocoded file
    output_srs : int
        Output spatial reference system EPSG code
    """
    
    # Template for VRT source description
    sourcexmltmpl = '''    <SimpleSource>
      <SourceFilename>{0}</SourceFilename>
      <SourceBand>{1}</SourceBand>
    </SimpleSource>'''
    
    # Create a temporary VRT file
    temp_dir = tempfile.mkdtemp()
    tempvrtname = os.path.join(temp_dir, 'geocode_temp.vrt')
    
    try:
        # Open input file to get dimensions
        with rasterio.open(phase_file) as src:
            x_size, y_size = src.width, src.height
            phase_transform = src.transform
            phase_crs = src.crs
        
        # Create VRT driver
        driver = gdal.GetDriverByName('VRT')
        tempds = driver.Create(tempvrtname, x_size, y_size, 0)
        
        # Add band to VRT
        tempds.AddBand(gdal.GDT_Float32)
        tempds.GetRasterBand(1).SetMetadata(
            {'source_0': sourcexmltmpl.format(phase_file, 1)}, 
            'vrt_sources'
        )
        
        # Set spatial reference (WGS84)
        sref = osr.SpatialReference()
        sref.ImportFromEPSG(4326)
        srswkt = sref.ExportToWkt()
        
        # Set geolocation metadata
        tempds.SetMetadata({
            'SRS': srswkt,
            'X_DATASET': lon_file,
            'X_BAND': '1',
            'Y_DATASET': lat_file,
            'Y_BAND': '1',
            'PIXEL_OFFSET': '0',
            'LINE_OFFSET': '0',
            'PIXEL_STEP': '1',
            'LINE_STEP': '1'
        }, 'GEOLOCATION')
        
        # Clean up
        tempds = None
        
        # Set output SRS
        out_sref = osr.SpatialReference()
        out_sref.ImportFromEPSG(output_srs)
        
        # Calculate output bounds from lon/lat files to maintain same pixel dimensions
        with rasterio.open(lon_file) as lon_src:
            lon_data = lon_src.read(1)
            lon_transform = lon_src.transform
        
        with rasterio.open(lat_file) as lat_src:
            lat_data = lat_src.read(1)
            lat_transform = lat_src.transform
        
        # Calculate approximate bounds
        valid_mask = ~(np.isnan(lon_data) | np.isnan(lat_data))
        if np.any(valid_mask):
            min_lon = np.min(lon_data[valid_mask])
            max_lon = np.max(lon_data[valid_mask])
            min_lat = np.min(lat_data[valid_mask])
            max_lat = np.max(lat_data[valid_mask])
            
            # Calculate approximate resolution to maintain same dimensions
            approx_res_x = (max_lon - min_lon) / x_size
            approx_res_y = (max_lat - min_lat) / y_size
            
            # Use the larger resolution to ensure we don't oversample
            target_res = max(approx_res_x, approx_res_y)
            
            # Set warp options with calculated resolution to maintain size
            warp_options = gdal.WarpOptions(
                format='GTiff',
                width=x_size,
                height=y_size,
                dstSRS=out_sref,
                resampleAlg='near',
                geoloc=True,
                creationOptions=['COMPRESS=LZW', 'TILED=YES']
            )
        else:
            # Fallback: use default warp options
            warp_options = gdal.WarpOptions(
                format='GTiff',
                dstSRS=out_sref,
                resampleAlg='near',
                geoloc=True,
                creationOptions=['COMPRESS=LZW', 'TILED=YES']
            )
        
        # Perform geocoding
        print(f"Geocoding {phase_file} to {out_file}")
        print(f"Target output dimensions: {x_size} x {y_size}")
        gdal.Warp(out_file, tempvrtname, options=warp_options)
        
        # Verify output dimensions match input
        with rasterio.open(out_file) as out_src:
            out_width, out_height = out_src.width, out_src.height
            if out_width == x_size and out_height == y_size:
                print(f"Success: Output dimensions match input ({x_size} x {y_size})")
            else:
                print(f"Warning: Output dimensions ({out_width} x {out_height}) differ from input ({x_size} x {y_size})")
        
    except Exception as e:
        print(f"Error during geocoding: {e}")
        raise
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir)

def get_file_dimensions(file_path):
    """
    Get dimensions of a raster file
    """
    with rasterio.open(file_path) as src:
        return src.width, src.height

def check_existing_multilook_files(phase_file, multilook_dir):
    """
    Check if downsampled lon/lat files already exist and match phase file dimensions
    """
    if not os.path.exists(multilook_dir):
        return None, None
    
    # Get phase file dimensions
    phase_width, phase_height = get_file_dimensions(phase_file)
    
    # Look for common downsampled file patterns
    possible_lon_files = [
        os.path.join(multilook_dir, "lon.multilook.tif"),
        os.path.join(multilook_dir, "lon.multilook.vrt"),
        os.path.join(multilook_dir, "lon.strided.tif"),
        os.path.join(multilook_dir, "lon.strided.vrt"),
        os.path.join(multilook_dir, "lon_downsampled.tif"),
        os.path.join(multilook_dir, "lon_downsampled.vrt"),
    ]
    
    possible_lat_files = [
        os.path.join(multilook_dir, "lat.multilook.tif"),
        os.path.join(multilook_dir, "lat.multilook.vrt"),
        os.path.join(multilook_dir, "lat.strided.tif"),
        os.path.join(multilook_dir, "lat.strided.vrt"),
        os.path.join(multilook_dir, "lat_downsampled.tif"),
        os.path.join(multilook_dir, "lat_downsampled.vrt"),
    ]
    
    # Check for existing files that match dimensions
    for lon_file in possible_lon_files:
        if os.path.exists(lon_file):
            try:
                lon_width, lon_height = get_file_dimensions(lon_file)
                if lon_width == phase_width and lon_height == phase_height:
                    # Found matching lon file, now check for matching lat file
                    for lat_file in possible_lat_files:
                        if os.path.exists(lat_file):
                            lat_width, lat_height = get_file_dimensions(lat_file)
                            if lat_width == phase_width and lat_height == phase_height:
                                print(f"Found existing downsampled files:")
                                print(f"  Longitude: {lon_file}")
                                print(f"  Latitude: {lat_file}")
                                return lon_file, lat_file
            except:
                # Skip files that can't be read
                continue
    
    return None, None

def create_phase_colormap():
    """
    Create a colormap suitable for phase data (cyclic colormap)
    """
    # Create a cyclic colormap for phase data
    colors = [
        (0, 0, 0.5),    # Dark blue
        (0, 0, 1),      # Blue
        (0, 1, 1),      # Cyan
        (0.5, 1, 0.5),  # Light green
        (1, 1, 0),      # Yellow
        (1, 0.5, 0),    # Orange
        (1, 0, 0),      # Red
        (0.5, 0, 0.5),  # Purple
        (0, 0, 0.5)     # Dark blue (back to start)
    ]
    
    return LinearSegmentedColormap.from_list('phase_cmap', colors, N=256)

def create_simple_visualization(phase_file, output_file, viz_dir):
    """
    Create a simple visualization of the geocoded output
    """
    os.makedirs(viz_dir, exist_ok=True)
    
    # Create phase colormap
    phase_cmap = create_phase_colormap()
    
    # Read the input and output files
    with rasterio.open(phase_file) as src:
        phase_data = src.read(1)
        phase_width, phase_height = src.width, src.height
    
    with rasterio.open(output_file) as src:
        output_data = src.read(1)
        output_width, output_height = src.width, src.height
    
    # Calculate statistics for display
    phase_stats = {
        'min': np.nanmin(phase_data),
        'max': np.nanmax(phase_data),
        'mean': np.nanmean(phase_data)
    }
    
    output_stats = {
        'min': np.nanmin(output_data),
        'max': np.nanmax(output_data),
        'mean': np.nanmean(output_data)
    }
    
    # Create figure with subplots
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle('Input vs Output Comparison', fontsize=16, fontweight='bold')
    
    # Original phase
    im1 = axes[0].imshow(phase_data, cmap=phase_cmap,
                        vmin=phase_stats['min'], vmax=phase_stats['max'])
    axes[0].set_title(f'Original Phase\nDimensions: {phase_width} x {phase_height}')
    axes[0].set_xlabel('Range')
    axes[0].set_ylabel('Azimuth')
    plt.colorbar(im1, ax=axes[0], label='Phase (rad)')
    
    # Geocoded output
    im2 = axes[1].imshow(output_data, cmap=phase_cmap,
                        vmin=output_stats['min'], vmax=output_stats['max'])
    axes[1].set_title(f'Geocoded Phase\nDimensions: {output_width} x {output_height}')
    axes[1].set_xlabel('Longitude')
    axes[1].set_ylabel('Latitude')
    plt.colorbar(im2, ax=axes[1], label='Phase (rad)')
    
    # Add statistics text
    stats_text1 = f"Min: {phase_stats['min']:.3f}\nMax: {phase_stats['max']:.3f}\nMean: {phase_stats['mean']:.3f}"
    axes[0].text(0.02, 0.98, stats_text1, transform=axes[0].transAxes, 
                verticalalignment='top', fontsize=10,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    
    stats_text2 = f"Min: {output_stats['min']:.3f}\nMax: {output_stats['max']:.3f}\nMean: {output_stats['mean']:.3f}"
    axes[1].text(0.02, 0.98, stats_text2, transform=axes[1].transAxes, 
                verticalalignment='top', fontsize=10,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    
    plt.tight_layout()
    plt.savefig(os.path.join(viz_dir, 'input_output_comparison.png'), dpi=300, bbox_inches='tight')
    plt.close(fig)
    
    print(f"Visualization saved to: {os.path.join(viz_dir, 'input_output_comparison.png')}")

def main():
    parser = argparse.ArgumentParser(
        description='Geocode dolphin unwrapped phase results with gdal_translate multi-looking and optional visualization. Output will have same pixel dimensions as input.'
    )
    parser.add_argument('-i', '--input', required=True,
                       help='Input dolphin unwrapped phase file (GeoTIFF)')
    parser.add_argument('--lon', required=True,
                       help='Input longitude file (e.g., lon.rdr.full.vrt)')
    parser.add_argument('--lat', required=True,
                       help='Input latitude file (e.g., lat.rdr.full.vrt)')
    parser.add_argument('-o', '--output', default='unwrapped_phase_geocoded.tif',
                       help='Output geocoded file path')
    parser.add_argument('--multilook-dir', default='multilook_geoms',
                       help='Directory for multi-looked geometry files')
    parser.add_argument('--viz-dir', default='visualizations',
                       help='Directory for visualization images')
    parser.add_argument('--output-srs', type=int, default=4326,
                       help='Output SRS EPSG code (default: 4326 for WGS84)')
    parser.add_argument('--resample-alg', default='average',
                       choices=['near', 'average', 'bilinear', 'cubic', 'cubicspline', 'lanczos', 'mode'],
                       help='Resampling algorithm for multi-looking (default: average)')
    parser.add_argument('--no-cleanup', action='store_true',
                       help='Keep temporary multi-looked files')
    parser.add_argument('--force-multilook', action='store_true',
                       help='Force multi-looking even if downsampled files exist')
    
    # Visualization options
    viz_group = parser.add_mutually_exclusive_group()
    viz_group.add_argument('--no-viz', action='store_true',
                          help='Skip visualization')
    viz_group.add_argument('--viz', action='store_true',
                          help='Create visualization (default)')
    
    args = parser.parse_args()
    
    # Default to visualization if no option is specified
    if not (args.no_viz or args.viz):
        args.viz = True
    
    # Check if input files exist
    for f in [args.input, args.lon, args.lat]:
        if not os.path.exists(f):
            print(f"Error: Input file does not exist: {f}")
            return 1
    
    # Get dimensions from phase file
    print("Getting input file dimensions...")
    phase_width, phase_height = get_file_dimensions(args.input)
    lon_width, lon_height = get_file_dimensions(args.lon)
    lat_width, lat_height = get_file_dimensions(args.lat)
    
    print(f"Phase file dimensions: {phase_width} x {phase_height}")
    print(f"Longitude file dimensions: {lon_width} x {lon_height}")
    print(f"Latitude file dimensions: {lat_width} x {lat_height}")
    
    # Check if multi-looking is needed or if files already exist
    if (phase_width, phase_height) == (lon_width, lon_height) == (lat_width, lat_height):
        print("Files already have matching dimensions. Skipping multi-looking.")
        lon_multilook = args.lon
        lat_multilook = args.lat
    elif not args.force_multilook:
        # Check if downsampled files already exist
        existing_lon, existing_lat = check_existing_multilook_files(args.input, args.multilook_dir)
        if existing_lon and existing_lat:
            print("Using existing downsampled files.")
            lon_multilook = existing_lon
            lat_multilook = existing_lat
        else:
            # Need to create downsampled files
            print("Downsampled files not found or don't match. Creating new ones...")
            # Step 1: Apply multi-looking to lon/lat files using gdal_translate
            print("Step 1: Applying multi-looking to lon/lat files using gdal_translate...")
            
            # Create output directory
            os.makedirs(args.multilook_dir, exist_ok=True)
            
            # Generate output file paths
            lon_basename = os.path.splitext(os.path.basename(args.lon))[0]
            lat_basename = os.path.splitext(os.path.basename(args.lat))[0]
            
            lon_multilook = os.path.join(args.multilook_dir, f"{lon_basename}_multilook.tif")
            lat_multilook = os.path.join(args.multilook_dir, f"{lat_basename}_multilook.tif")
            
            # Apply multi-looking using gdal_translate
            try:
                gdal_translate_multilook(
                    args.lon, lon_multilook, phase_width, phase_height, args.resample_alg
                )
                gdal_translate_multilook(
                    args.lat, lat_multilook, phase_width, phase_height, args.resample_alg
                )
            except Exception as e:
                print(f"Multi-looking failed: {e}")
                return 1
    else:
        # Force multi-looking even if files exist
        print("Forcing multi-looking...")
        # Step 1: Apply multi-looking to lon/lat files using gdal_translate
        print("Step 1: Applying multi-looking to lon/lat files using gdal_translate...")
        
        # Create output directory
        os.makedirs(args.multilook_dir, exist_ok=True)
        
        # Generate output file paths
        lon_basename = os.path.splitext(os.path.basename(args.lon))[0]
        lat_basename = os.path.splitext(os.path.basename(args.lat))[0]
        
        lon_multilook = os.path.join(args.multilook_dir, f"{lon_basename}_multilook.tif")
        lat_multilook = os.path.join(args.multilook_dir, f"{lat_basename}_multilook.tif")
        
        # Apply multi-looking using gdal_translate
        try:
            gdal_translate_multilook(
                args.lon, lon_multilook, phase_width, phase_height, args.resample_alg
            )
            gdal_translate_multilook(
                args.lat, lat_multilook, phase_width, phase_height, args.resample_alg
            )
        except Exception as e:
            print(f"Multi-looking failed: {e}")
            return 1
    
    # Step 2: Geocode the phase file using (multi-looked) lon/lat
    print("Step 2: Geocoding phase file (maintaining same dimensions)...")
    
    # Create output directory if needed
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    
    try:
        geocode_phase_file_same_size(
            phase_file=args.input,
            lon_file=lon_multilook,
            lat_file=lat_multilook,
            out_file=args.output,
            output_srs=args.output_srs
        )
        
        print(f"\nGeocoding completed successfully!")
        print(f"Geocoded phase file saved to: {args.output}")
        
        # Display some info about the output file
        with rasterio.open(args.output) as src:
            print(f"Output file info:")
            print(f"  Dimensions: {src.width} x {src.height}")
            print(f"  CRS: {src.crs}")
            if src.res:
                print(f"  Resolution: {src.res[0]:.6f} x {src.res[1]:.6f} degrees")
        
    except Exception as e:
        print(f"Geocoding failed: {e}")
        return 1
    
    # Step 3: Create visualization if requested
    if args.viz:
        print("Step 3: Creating visualization...")
        try:
            create_simple_visualization(args.input, args.output, args.viz_dir)
        except Exception as e:
            print(f"Visualization failed: {e}")
    else:
        print("Skipping visualization as requested.")
    
    # Clean up temporary files if requested
    if not args.no_cleanup and (lon_multilook != args.lon or lat_multilook != args.lat):
        # Only clean up if we created new files and they're not the original ones
        if (lon_multilook != args.lon and lat_multilook != args.lat and 
            os.path.exists(args.multilook_dir) and 
            (lon_multilook.startswith(args.multilook_dir) or 
             lat_multilook.startswith(args.multilook_dir))):
            print("Cleaning up temporary files...")
            shutil.rmtree(args.multilook_dir)
    else:
        print(f"Multi-looked files kept in: {args.multilook_dir}")
    
    return 0

if __name__ == "__main__":
    exit(main())
