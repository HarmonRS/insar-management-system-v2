from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from shapely.geometry import box as shapely_box
from sqlalchemy import String, cast, delete, or_, select

from ..config import settings
from .admin_region_lookup_service import (
    admin_region_matches,
    lookup_admin_region_for_point,
    lookup_admin_region_geometry,
)


PRODUCT_DEFINITIONS = (
    {
        "key": "expert_geo_los_def_rate_tif",
        "label": "Expert Gamma geo_los_def_rate GeoTIFF",
        "role": "primary_geotiff",
        "relative_path": "publish/geotiff/geo_los_def_rate.tif",
    },
    {
        "key": "expert_geo_los_def_rate_rgb_tif",
        "label": "Expert Gamma geo_los_def_rate RGB GeoTIFF",
        "role": "primary_rgb_geotiff",
        "relative_path": "publish/geotiff/geo_los_def_rate_rgb.tif",
    },
    {
        "key": "expert_geo_los_def_rate_rgb_preview_png",
        "label": "Expert Gamma geo_los_def_rate RGB PNG preview",
        "role": "primary_geocoded_preview",
        "relative_path": "publish/geotiff/geo_los_def_rate_rgb_preview.png",
    },
    {
        "key": "expert_disp_point_txt",
        "label": "Expert Gamma disp_prt_2d point time series",
        "role": "monitor_points",
        "relative_path": "publish/points/disp_point.txt",
    },
    {
        "key": "los_rate_toward_m_per_year_hls_geo_preview_png",
        "label": "Expert HLS LOS velocity geocoded RGB preview, toward radar positive",
        "role": "primary_geocoded_preview",
        "relative_path": "publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png",
    },
    {
        "key": "los_rate_toward_m_per_year_hls_rgb_tif",
        "label": "Expert HLS LOS velocity geocoded RGB GeoTIFF, toward radar positive",
        "role": "primary_rgb_geotiff",
        "relative_path": "publish/geotiff/los_rate_toward_m_per_year.hls.geo_rgb.tif",
    },
    {
        "key": "los_rate_toward_m_per_year_hls_bmp",
        "label": "Expert HLS LOS velocity RDC browse BMP, toward radar positive",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_rate_toward_m_per_year.hls.bmp",
    },
    {
        "key": "los_sigma_m_per_year_cc_geo_preview_png",
        "label": "Expert CC LOS velocity sigma geocoded RGB preview",
        "role": "quality_geocoded_preview",
        "relative_path": "publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png",
    },
    {
        "key": "los_sigma_m_per_year_cc_rgb_tif",
        "label": "Expert CC LOS velocity sigma geocoded RGB GeoTIFF",
        "role": "quality_rgb_geotiff",
        "relative_path": "publish/geotiff/los_sigma_m_per_year.cc.geo_rgb.tif",
    },
    {
        "key": "los_sigma_m_per_year_cc_bmp",
        "label": "Expert CC LOS velocity sigma RDC browse BMP",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_sigma_m_per_year.cc.bmp",
    },
    {
        "key": "los_rate_toward_m_per_year_tif",
        "label": "LOS velocity GeoTIFF in meters per year, toward radar positive",
        "role": "primary_geotiff",
        "relative_path": "publish/geotiff/los_rate_toward_m_per_year.tif",
    },
    {
        "key": "los_rate_away_m_per_year_tif",
        "label": "LOS velocity GeoTIFF in meters per year, away from radar positive",
        "role": "alternate_geotiff",
        "relative_path": "publish/geotiff/los_rate_away_m_per_year.tif",
    },
    {
        "key": "los_sigma_m_per_year_tif",
        "label": "LOS velocity sigma GeoTIFF in meters per year",
        "role": "quality_geotiff",
        "relative_path": "publish/geotiff/los_sigma_m_per_year.tif",
    },
    {
        "key": "los_rate_toward_mm_per_year_geo_preview_png",
        "label": "LOS velocity geocoded preview, toward radar positive",
        "role": "primary_geocoded_preview",
        "relative_path": "publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png",
    },
    {
        "key": "los_rate_toward_mm_per_year_bmp",
        "label": "LOS velocity RDC processing preview, toward radar positive",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_rate_toward_mm_per_year.bmp",
    },
    {
        "key": "los_rate_toward_mm_per_year_tif",
        "label": "LOS velocity GeoTIFF, toward radar positive",
        "role": "primary_geotiff",
        "relative_path": "publish/geotiff/los_rate_toward_mm_per_year.tif",
    },
    {
        "key": "los_rate_away_mm_per_year_bmp",
        "label": "LOS velocity RDC processing preview, away from radar positive",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_rate_away_mm_per_year.bmp",
    },
    {
        "key": "los_rate_away_mm_per_year_tif",
        "label": "LOS velocity GeoTIFF, away from radar positive",
        "role": "alternate_geotiff",
        "relative_path": "publish/geotiff/los_rate_away_mm_per_year.tif",
    },
    {
        "key": "los_sigma_mm_per_year_geo_preview_png",
        "label": "LOS velocity sigma geocoded preview",
        "role": "quality_geocoded_preview",
        "relative_path": "publish/geotiff/los_sigma_mm_per_year.geo_preview.png",
    },
    {
        "key": "los_sigma_mm_per_year_bmp",
        "label": "LOS velocity sigma RDC processing preview",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_sigma_mm_per_year.bmp",
    },
    {
        "key": "los_sigma_mm_per_year_tif",
        "label": "LOS velocity sigma GeoTIFF",
        "role": "quality_geotiff",
        "relative_path": "publish/geotiff/los_sigma_mm_per_year.tif",
    },
    {
        "key": "ts_rate_rad_per_year_tif",
        "label": "Gamma ts_rate phase-rate GeoTIFF",
        "role": "gamma_phase_rate",
        "relative_path": "publish/geotiff/ts_rate_rad_per_year.tif",
    },
    {
        "key": "sigma_rate_rad_per_year_tif",
        "label": "Gamma sigma_rate GeoTIFF",
        "role": "gamma_sigma_rate",
        "relative_path": "publish/geotiff/sigma_rate_rad_per_year.tif",
    },
    {
        "key": "trial_summary_json",
        "label": "Trial summary JSON",
        "role": "summary",
        "relative_path": "publish/trial_summary.json",
    },
)


MONITOR_ARTIFACT_SUFFIXES = (
    ("timeseries_png", "Monitoring point curve", ".png"),
    ("timeseries_csv", "Monitoring point values", ".csv"),
    ("metadata_json", "Monitoring point metadata", ".json"),
)

DEFAULT_IPTA_MB_MODE = 0
IPTA_MB_MODE_DESCRIPTIONS = {
    0: "valid unwrapped phase values required in all layers",
    1: "allow missing unwrapped phase values with network connectivity",
    2: "allow missing unwrapped phase values without network connectivity requirement",
}
GAMMA_SBAS_FALLBACK_MIN_COMMON_OVERLAP_RATIO = 0.30

GAMMA_SBAS_FORBIDDEN_DEFAULT_TOOLS = {
    "LT1_precision_orbit.py",
    "SLC_coreg.py",
    "gc_map1",
    "phase_sim_orb",
    "SLC_diff_intf",
    "adf",
    "cc_wave",
    "mcf",
}

GAMMA_SBAS_MANUAL_QC_TOOLS = {
    "disSLC",
    "dismph_fft",
}

GAMMA_SBAS_BLOCKING_INTERACTIVE_TOOLS = {
    "disSLC",
    "dismph",
    "dismph_fft",
    "dispwr",
    "disras",
    "xterm",
    "display",
    "eog",
    "gwenview",
    "xdg-open",
}

GAMMA_SBAS_UNATTENDED_POLICY = (
    "Backend Gamma SBAS production is non-interactive. Expert manual display/QC "
    "commands are documented but are not executed by default; reviewable browse "
    "assets are produced by raster/export commands and the publish step."
)

GAMMA_SBAS_REQUIRED_STEP_TOOLS = {
    "02_import_lt1_slc": {"par_LT1_SLC", "ORB_filt_spline.py"},
    "03_reference_mli": {"multi_look", "ras_dB", "SLC_corners"},
    "04_dem_lookup": {"dem_import", "fill_gaps", "gc_map2", "pixel_area", "gc_map_fine", "geocode", "geocode_back"},
    "06_coregister_scenes": {"create_offset", "init_offset_orbit", "init_offset", "offset_pwr", "offset_fit", "SLC_interp"},
    "07_rmli_average": {"mk_mli_all", "ras_dB"},
    "08_diff_network": {"base_calc", "base_plot", "mk_diff_2d"},
    "09_filter_unwrap": {"mk_adf_2d", "ave_image", "rascc_mask", "mk_unw_2d"},
    "10_detrend_atm": {"create_diff_par", "quad_fit", "quad_sub", "atm_mod_2d", "fill_gaps", "atm_sim_2d", "sub_phase"},
    "11_sbas_inversion": {"mb", "real_to_cpx", "unw_model"},
    "12_outputs_points": {"replace_values", "mask_data", "dispmap", "ts_rate", "geocode_back", "data2geotiff", "disp_prt_2d"},
}

GAMMA_STAGE_PLAN = (
    {
        "stage_id": "prepare_slc",
        "label": "Prepare LT1 SLCs",
        "gamma_tools": ["par_LT1_SLC", "ORB_filt_spline.py", "SLC_corners"],
        "manual_qc_tools": ["disSLC", "dismph_fft"],
        "unattended_policy": GAMMA_SBAS_UNATTENDED_POLICY,
        "status": "PLANNED",
    },
    {
        "stage_id": "baseline_audit",
        "label": "Gamma baseline audit and itab approval",
        "gamma_tools": ["multi_look", "base_calc", "base_plot"],
        "status": "PENDING_REQUIRED_AUDIT",
    },
    {
        "stage_id": "coregistration",
        "label": "Stack co-registration",
        "gamma_tools": ["create_offset", "init_offset_orbit", "init_offset", "offset_pwr", "offset_fit", "SLC_interp"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "rdc_dem",
        "label": "RDC DEM and lookup table",
        "gamma_tools": ["dem_import", "fill_gaps", "gc_map2", "pixel_area", "create_diff_par", "offset_pwrm", "offset_fitm", "gc_map_fine", "geocode", "geocode_back"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "interferograms",
        "label": "Differential interferograms",
        "gamma_tools": ["base_calc", "base_plot", "mk_diff_2d", "mk_adf_2d", "ave_image", "rascc_mask", "mk_unw_2d"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "detrend_atm",
        "label": "Detrend and atmospheric phase correction",
        "gamma_tools": ["quad_fit", "quad_sub", "atm_mod_2d", "atm_sim_2d", "sub_phase"],
        "status": "PLANNED_AFTER_INTERFEROGRAMS",
    },
    {
        "stage_id": "ipta_timeseries",
        "label": "IPTA SBAS time-series inversion",
        "gamma_tools": ["mb", "real_to_cpx", "unw_model"],
        "status": "PLANNED_AFTER_DETREND_ATM",
    },
    {
        "stage_id": "publish_products",
        "label": "Geocode and publish products",
        "gamma_tools": ["geocode_back", "data2geotiff", "dispmap"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "monitor_points",
        "label": "Monitoring-point time-series extraction",
        "gamma_tools": [],
        "status": "PLANNED_AFTER_PRODUCTS",
    },
)

EXPERT_WORKSPACE_DIRS = (
    "RAW",
    "SLC",
    "dem",
    "rslc_prep",
    "mli_dir",
    "diff_dir",
    "diff1_dir",
    "sbas",
    "publish",
    "logs",
    "scripts",
    "state",
)

GAMMA_SBAS_WORKFLOW_STEPS = (
    {
        "id": "01_workspace_data",
        "name": "Directory and LT1 data preparation",
        "legacy_stage": "workspace",
        "script_name": "01_workspace_data.sh",
        "status": "PENDING",
        "expert_tools": ["mkdir", "ls"],
    },
    {
        "id": "02_import_lt1_slc",
        "name": "Import every LT1 SLC",
        "legacy_stage": "baseline_audit",
        "script_name": "02_import_lt1_slc.sh",
        "status": "PENDING",
        "expert_tools": ["par_LT1_SLC", "ORB_filt_spline.py", "SLC_corners"],
        "manual_qc_tools": ["disSLC", "dismph_fft"],
        "unattended_policy": GAMMA_SBAS_UNATTENDED_POLICY,
    },
    {
        "id": "03_reference_mli",
        "name": "Reference MLI and footprint checks",
        "legacy_stage": "baseline_audit",
        "script_name": "03_reference_mli.sh",
        "status": "PENDING",
        "expert_tools": ["multi_look", "grep", "ras_dB", "SLC_corners"],
    },
    {
        "id": "04_dem_lookup",
        "name": "DEM import and lookup table",
        "legacy_stage": "rdc_dem",
        "script_name": "04_dem_lookup.sh",
        "status": "PENDING",
        "expert_tools": ["dem_import", "fill_gaps", "gc_map2", "pixel_area", "gc_map_fine", "geocode"],
    },
    {
        "id": "05_coreg_prep",
        "name": "SLC coregistration preparation",
        "legacy_stage": "coregistration",
        "script_name": "05_coreg_prep.sh",
        "status": "PENDING",
        "expert_tools": ["cp", "rslc_tab"],
    },
    {
        "id": "06_coregister_scenes",
        "name": "Coregister every SLC to reference",
        "legacy_stage": "coregistration",
        "script_name": "06_coregister_scenes.sh",
        "status": "PENDING",
        "expert_tools": ["create_offset", "init_offset_orbit", "init_offset", "offset_pwr", "offset_fit", "SLC_interp"],
    },
    {
        "id": "07_rmli_average",
        "name": "RMLI stack and average intensity",
        "legacy_stage": "coregistration",
        "script_name": "07_rmli_average.sh",
        "status": "PENDING",
        "expert_tools": ["mk_mli_all", "grep", "ras_dB"],
    },
    {
        "id": "08_diff_network",
        "name": "Interferogram network and differential phase",
        "legacy_stage": "interferograms",
        "script_name": "08_diff_network.sh",
        "status": "PENDING",
        "expert_tools": ["base_calc", "base_plot", "mk_diff_2d"],
    },
    {
        "id": "09_filter_unwrap",
        "name": "Adaptive filtering, coherence mask and unwrap",
        "legacy_stage": "interferograms",
        "script_name": "09_filter_unwrap.sh",
        "status": "PENDING",
        "expert_tools": ["mk_adf_2d", "ave_image", "rascc_mask", "mk_unw_2d"],
    },
    {
        "id": "10_detrend_atm",
        "name": "Detrend and atmospheric correction",
        "legacy_stage": "quality_correction",
        "script_name": "10_detrend_atm.sh",
        "status": "PENDING",
        "optional": False,
        "expert_tools": ["quad_fit", "quad_sub", "atm_mod_2d", "atm_sim_2d", "sub_phase"],
    },
    {
        "id": "11_sbas_inversion",
        "name": "Gamma IPTA SBAS inversion",
        "legacy_stage": "ipta_timeseries",
        "script_name": "11_sbas_inversion.sh",
        "status": "PENDING",
        "expert_tools": ["mb", "real_to_cpx", "unw_model", "ts_rate"],
    },
    {
        "id": "12_outputs_points",
        "name": "Output, geocode and point time-series",
        "legacy_stage": "publish_products+monitor_points",
        "script_name": "12_outputs_points.sh",
        "status": "PENDING",
        "expert_tools": ["replace_values", "mask_data", "dispmap", "ts_rate", "rasdt_pwr", "geocode_back", "data2geotiff", "disp_prt_2d"],
    },
)

GAMMA_SBAS_EXPERT_DOCUMENT_STEPS = (
    {
        "id": "expert_01_workspace_data",
        "order": 1,
        "title": "Directory and LT1 data preparation",
        "document_section": "1. Directory and data preparation",
        "workflow_steps": ["01_workspace_data"],
        "implementation_status": "implemented",
        "commands": [
            "mkdir -p RAW SLC dem rslc_prep mli_dir diff_dir diff1_dir sbas",
            "ls RAW/<date>/*.tiff",
            "ls RAW/<date>/*.meta.xml",
        ],
    },
    {
        "id": "expert_02_import_slc",
        "order": 2,
        "title": "Import every LT1 SLC",
        "document_section": "2. Import LT1 SLC scenes",
        "workflow_steps": ["02_import_lt1_slc"],
        "implementation_status": "implemented",
        "commands": [
            "par_LT1_SLC <scene>.tiff <scene>.meta.xml <date>.slc.par <date>.slc 0",
            "cp <date>.slc.par <date>.slc.par.orig",
            "ORB_filt_spline.py <date>.slc.par.orig <date>.slc.par --ignore_start 3 --ignore_end 17 --degree 5",
            "SLC_corners <date>.slc.par",
            "disSLC <date>.slc <width> ...",
            "dismph_fft <date>.slc <width> ...",
        ],
    },
    {
        "id": "expert_03_reference_mli",
        "order": 3,
        "title": "Reference MLI and footprint checks",
        "document_section": "3. Reference multilook and range check",
        "workflow_steps": ["03_reference_mli"],
        "implementation_status": "implemented",
        "commands": [
            "multi_look <ref>.slc <ref>.slc.par <ref>_<rlks>_<azlks>.mli <ref>_<rlks>_<azlks>.mli.par <rlks> <azlks>",
            "grep range_samples <ref>.mli.par",
            "grep azimuth_lines <ref>.mli.par",
            "ras_dB <ref>.mli <width> ... gray.cm <ref>.mli.bmp",
            "SLC_corners <ref>.mli.par",
        ],
    },
    {
        "id": "expert_04_dem_lookup",
        "order": 4,
        "title": "DEM import and lookup table",
        "document_section": "4. DEM import and geocoding lookup table",
        "workflow_steps": ["04_dem_lookup"],
        "implementation_status": "implemented",
        "commands": [
            "dem_import <dem>.tif SRTM.dem SRTM.dem.par ...",
            "fill_gaps SRTM.dem <dem_width> SRTM_dem_fill",
            "gc_map2 <ref>.mli.par SRTM.dem.par SRTM_dem_fill <ref>_seg.dem_par <ref>_seg.dem <ref>.lt ...",
            "pixel_area <ref>.mli.par <ref>_seg.dem_par <ref>_seg.dem <ref>.lt ...",
            "create_diff_par <ref>.mli.par - <ref>.diff_par 1 0",
            "offset_pwrm <ref>.gamma0 <ref>.mli <ref>.diff_par ...",
            "offset_fitm <ref>.offs <ref>.snr <ref>.diff_par ...",
            "gc_map_fine <ref>.lt <dem_width> <ref>.diff_par <ref>.lt_fine 1",
            "geocode <ref>.lt_fine <ref>_seg.dem <dem_width> <ref>.hgt <mli_width> <mli_lines>",
            "geocode_back <ref>.mli <mli_width> <ref>.lt_fine <ref>.geo <dem_width> <dem_lines> 5 0",
        ],
    },
    {
        "id": "expert_05_coreg_prep",
        "order": 5,
        "title": "SLC coregistration preparation",
        "document_section": "5. SLC coregistration preparation",
        "workflow_steps": ["05_coreg_prep"],
        "implementation_status": "implemented",
        "commands": [
            "cp SLC/dates rslc_prep/dates",
            "cp <ref>.slc <ref>.rslc",
            "cp <ref>.slc.par <ref>.rslc.par",
        ],
    },
    {
        "id": "expert_06_coregister_scenes",
        "order": 6,
        "title": "Coregister every SLC to reference",
        "document_section": "6. Coregister scenes to reference geometry",
        "workflow_steps": ["06_coregister_scenes"],
        "implementation_status": "implemented",
        "commands": [
            "create_offset <ref>.rslc.par <date>.slc.par <ref>_<date>.off 1 <rlks> <azlks> 0",
            "init_offset_orbit <ref>.rslc.par <date>.slc.par <ref>_<date>.off",
            "init_offset <ref>.rslc <date>.slc <ref>.rslc.par <date>.slc.par <ref>_<date>.off <rlks> <azlks>",
            "offset_pwr <ref>.rslc <date>.slc <ref>.rslc.par <date>.slc.par <ref>_<date>.off ...",
            "offset_fit <ref>_<date>.offs <ref>_<date>.snr <ref>_<date>.off ...",
            "SLC_interp <date>.slc <ref>.rslc.par <date>.slc.par <ref>_<date>.off <date>.rslc <date>.rslc.par",
            "echo '<date>.rslc <date>.rslc.par' >> rslc_tab",
        ],
    },
    {
        "id": "expert_07_rmli_average",
        "order": 7,
        "title": "RMLI stack and average intensity",
        "document_section": "7. Generate RMLI and average intensity",
        "workflow_steps": ["07_rmli_average"],
        "implementation_status": "implemented",
        "commands": [
            "mk_mli_all rslc_tab . <rlks> <azlks> 1 1.0 0.4 mli.ave",
            "grep range_samples mli.ave.par",
            "grep azimuth_lines mli.ave.par",
            "ras_dB mli.ave <width> ... gray.cm mli.ave.bmp",
        ],
    },
    {
        "id": "expert_08_diff_network",
        "order": 8,
        "title": "Interferogram network and differential phase",
        "document_section": "8. Interferogram generation and differential interferometry",
        "workflow_steps": ["08_diff_network"],
        "implementation_status": "implemented",
        "commands": [
            "base_calc rslc_tab <ref>.rslc.par bprep_file itab 1 1 <bmin> <bmax> <tmin> <tmax> -",
            "base_plot rslc_tab <ref>.rslc.par itab bprep_file 1",
            "mk_diff_2d rslc_tab itab 0 <ref>.hgt - mli.ave mli_dir . <rlks> <azlks> 3 1 1 0 -u",
            "ls *.diff",
            "ls *.diff.bmp",
        ],
    },
    {
        "id": "expert_09_filter_unwrap",
        "order": 9,
        "title": "Adaptive filtering, coherence mask and unwrap",
        "document_section": "9. Adaptive filtering, coherence mask and phase unwrapping",
        "workflow_steps": ["09_filter_unwrap"],
        "implementation_status": "implemented",
        "commands": [
            "mk_adf_2d rslc_tab itab mli.ave . 5 0.6 32 8 -u",
            "ls *.adf.diff",
            "ls *.adf.cc",
            "ave_image cc.list <width> mean.cc",
            "rascc_mask mean.cc - <width> 1 1 - 1 1 <threshold>",
            "mk_unw_2d rslc_tab itab mli.ave . <threshold> 0 1 1 1 1 <r_seed> <a_seed> 1 -u",
            "mk_unw_2d rslc_tab itab mli.ave . - - 1 1 1 1 <r_seed> <a_seed> 1 mean.cc_mask.bmp -u",
        ],
    },
    {
        "id": "expert_10_detrend_atm",
        "order": 10,
        "title": "Detrend and atmospheric phase removal",
        "document_section": "10. Detrending and atmospheric phase removal",
        "workflow_steps": ["10_detrend_atm"],
        "implementation_status": "implemented",
        "commands": [
            "create_diff_par <pair>.off <pair>.off <pair>.diff_par 0 0",
            "quad_fit <pair>.adf.unw <pair>.diff_par 5 5 - - 3 <pair>.unw_linear",
            "quad_sub <pair>.adf.unw <pair>.diff_par <pair>.unw_sub_linear 0 0",
            "rasdt_pwr <pair>.unw_sub_linear mli.ave <width> 1 - 1 1 -6.28 6.28 1 rmg.cm ...",
            "atm_mod_2d <pair>.unw_sub_linear <ref>.hgt <pair>.adf.cc <pair>.diff_par - 0 <pair>.a0 <pair>.a1 ...",
            "fill_gaps <pair>.a0 <model_width> <pair>.a0_fill ...",
            "fill_gaps <pair>.a1 <model_width> <pair>.a1_fill ...",
            "atm_sim_2d <pair>.diff_par <ref>.hgt <pair>.a0_fill <pair>.a1_fill <pair>.atm_model",
            "sub_phase <pair>.unw_sub_linear <pair>.atm_model <pair>.diff_par <pair>.unw.atmsub 0",
        ],
    },
    {
        "id": "expert_11_sbas_inversion",
        "order": 11,
        "title": "SBAS inversion",
        "document_section": "11. SBAS inversion",
        "workflow_steps": ["11_sbas_inversion"],
        "implementation_status": "implemented",
        "commands": [
            "mb unw_atmsub_tab RMLI_tab itab - itab_ts ras/diff1 1 diff1.sigma_ts 1 - <r_ref> <a_ref> 15 15 0.0 mli.ave.par",
            "real_to_cpx - <pair>.unw.atmsub <pair>.unw.atmsub.cpx <width> 1",
            "unw_model <pair>.unw.atmsub.cpx <pair>.unw.atmsub_sim <pair>.unw.atmsub_1 <width> <r_ref> <a_ref>",
            "mb unw.atmsub_1_tab RMLI_tab itab - itab_ts ras/diff2 1 diff2.sigma_ts 0 - <r_ref> <a_ref> 15 15 0.0 mli.ave.par",
            "mb final_unw_tab RMLI_tab itab - itab_ts ras/diff 0 diff.sigma_ts 0 - <r_ref> <a_ref> 15 15 0.5 mli.ave.par",
        ],
    },
    {
        "id": "expert_12_outputs_points",
        "order": 12,
        "title": "Output, geocode and point time-series",
        "document_section": "12. Output, geocoding and point time-series",
        "workflow_steps": ["12_outputs_points"],
        "implementation_status": "implemented",
        "commands": [
            "replace_values diff.sigma_ts 0.5 0.0 diff.sigma_ts.masked <width> 1 2 0",
            "rasdt_pwr diff.sigma_ts.masked - <width> 1 0 1 1 0.0 1.5 1 cc.cm diff.sigma_ts.masked.bmp 1.0 0.35 8",
            "mask_data ras/diff_<date> <width> ras/diff_<date>.masked diff.sigma_ts.masked.bmp 0",
            "dispmap ras/<date>.disp.phase - mli.ave.par - ras/<date>.disp 0 0",
            "ts_rate disp.TS_tab RMLI_tab itab_ts - los_def_rate los_def_const los_def_sigma 0",
            "rasdt_pwr los_def_rate mli.ave <width> 1 0 1 1 -0.08 0.08 0 hls.cm los_def_rate.bmp 1.0 0.35 24",
            "geocode_back los_def_rate <width> <ref>.lt_fine geo_los_def_rate <dem_width> <dem_lines> 5 0",
            "data2geotiff <ref>_seg.dem_par geo_los_def_rate 2 geo_los_def_rate.tif",
            "geocode_back los_def_rate.bmp <width> <ref>.lt_fine geo_los_def_rate.bmp <dem_width> <dem_lines> 0 2",
            "data2geotiff <ref>_seg.dem_par geo_los_def_rate.bmp 0 geo_los_def_rate_rgb.tif",
            "disp_prt_2d disp.TS_tab RMLI_tab itab_ts - 3 disp_point_sel.txt <ref>.hgt los_def_rate diff.sigma_ts.masked items.txt disp_point.txt 3 1 0",
        ],
    },
)

LT1_SCENE_RE = re.compile(
    r"^(?P<satellite>LT1[AB])_"
    r"(?P<satellite_mode>[A-Z0-9]+)_"
    r"(?P<receiving_station>[A-Z0-9]+)_"
    r"(?P<imaging_mode>[A-Z0-9]+)_"
    r"(?P<absolute_orbit>\d+)_"
    r"E(?P<center_lon>-?\d+(?:\.\d+)?)_"
    r"N(?P<center_lat>-?\d+(?:\.\d+)?)_"
    r"(?P<date>\d{8})_"
    r"(?P<product_type>[A-Z0-9]+)_"
    r"(?P<polarization>[A-Z0-9]+)_",
    re.IGNORECASE,
)

S1_SOURCE_RE = re.compile(
    r"^(?P<satellite>S1[A-Z])_"
    r"(?P<mode>[A-Z0-9]+)_"
    r"(?P<product>[A-Z0-9]+)_+"
    r"(?P<class>[0-9A-Z]{4})_"
    r"(?P<start>\d{8}T\d{6}(?:\.\d+)?)_"
    r"(?P<stop>\d{8}T\d{6}(?:\.\d+)?)_"
    r"(?P<absolute_orbit>\d+)_"
    r"(?P<datatake>[0-9A-F]+)_"
    r"(?P<product_uid>[0-9A-F]+)"
    r"(?:\.SAFE|\.zip)?$",
    re.IGNORECASE,
)

S1_EOF_RE = re.compile(
    r"^(?P<satellite>S1[A-Z])_OPER_"
    r"(?P<orbit_type>AUX_[A-Z0-9]+)_"
    r"(?P<provider>[A-Z0-9]+)_"
    r"(?P<generation>\d{8}T\d{6})_"
    r"V(?P<valid_start>\d{8}T\d{6})_"
    r"(?P<valid_stop>\d{8}T\d{6})\.EOF$",
    re.IGNORECASE,
)

S1_GAMMA_SBAS_PLANNING_STEPS = (
    {
        "id": "01_s1_stack_assets",
        "name": "Sentinel-1 ZIP/SAFE and EOF stack audit",
        "status": "PENDING",
        "optional": False,
        "notes": ["Implemented as planning metadata; no Gamma commands are executed."],
    },
    {
        "id": "02_s1_tops_import",
        "name": "Sentinel-1 TOPS import and burst selection",
        "status": "PLANNED",
        "optional": False,
        "notes": ["Pending verified Gamma TOPS import script."],
    },
    {
        "id": "03_s1_sbas_workflow",
        "name": "Sentinel-1 Gamma SBAS workflow",
        "status": "PLANNED",
        "optional": False,
        "notes": ["Pending Sentinel-1 specific co-registration, interferogram and IPTA scripts."],
    },
)


class SbasInsarProductionService:
    _WORKFLOW_BASELINE_DONE_STATUSES = {
        "BASELINE_AUDIT_READY",
        "ITAB_APPROVED",
        "COREGISTRATION_SCRIPT_READY",
        "COREGISTRATION_RUNNING",
        "COREGISTRATION_READY",
        "RDC_DEM_SCRIPT_READY",
        "RDC_DEM_RUNNING",
        "RDC_DEM_READY",
        "INTERFEROGRAMS_SCRIPT_READY",
        "INTERFEROGRAMS_RUNNING",
        "INTERFEROGRAMS_READY",
        "DETREND_ATM_SCRIPT_READY",
        "DETREND_ATM_RUNNING",
        "DETREND_ATM_READY",
        "IPTA_TIMESERIES_SCRIPT_READY",
        "IPTA_TIMESERIES_RUNNING",
        "IPTA_TIMESERIES_READY",
        "PUBLISH_PRODUCTS_SCRIPT_READY",
        "PUBLISH_PRODUCTS_RUNNING",
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_COREG_DONE_STATUSES = {
        "COREGISTRATION_READY",
        "RDC_DEM_SCRIPT_READY",
        "RDC_DEM_RUNNING",
        "RDC_DEM_READY",
        "INTERFEROGRAMS_SCRIPT_READY",
        "INTERFEROGRAMS_RUNNING",
        "INTERFEROGRAMS_READY",
        "DETREND_ATM_SCRIPT_READY",
        "DETREND_ATM_RUNNING",
        "DETREND_ATM_READY",
        "IPTA_TIMESERIES_SCRIPT_READY",
        "IPTA_TIMESERIES_RUNNING",
        "IPTA_TIMESERIES_READY",
        "PUBLISH_PRODUCTS_SCRIPT_READY",
        "PUBLISH_PRODUCTS_RUNNING",
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_RDC_DEM_DONE_STATUSES = {
        "RDC_DEM_SCRIPT_READY",
        "RDC_DEM_RUNNING",
        "RDC_DEM_READY",
        "INTERFEROGRAMS_SCRIPT_READY",
        "INTERFEROGRAMS_RUNNING",
        "INTERFEROGRAMS_READY",
        "DETREND_ATM_SCRIPT_READY",
        "DETREND_ATM_RUNNING",
        "DETREND_ATM_READY",
        "IPTA_TIMESERIES_SCRIPT_READY",
        "IPTA_TIMESERIES_RUNNING",
        "IPTA_TIMESERIES_READY",
        "PUBLISH_PRODUCTS_SCRIPT_READY",
        "PUBLISH_PRODUCTS_RUNNING",
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_INTERFEROGRAMS_DONE_STATUSES = {
        "INTERFEROGRAMS_READY",
        "DETREND_ATM_SCRIPT_READY",
        "DETREND_ATM_RUNNING",
        "DETREND_ATM_READY",
        "IPTA_TIMESERIES_SCRIPT_READY",
        "IPTA_TIMESERIES_RUNNING",
        "IPTA_TIMESERIES_READY",
        "PUBLISH_PRODUCTS_SCRIPT_READY",
        "PUBLISH_PRODUCTS_RUNNING",
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_DETREND_DONE_STATUSES = {
        "DETREND_ATM_READY",
        "IPTA_TIMESERIES_SCRIPT_READY",
        "IPTA_TIMESERIES_RUNNING",
        "IPTA_TIMESERIES_READY",
        "PUBLISH_PRODUCTS_SCRIPT_READY",
        "PUBLISH_PRODUCTS_RUNNING",
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_IPTA_DONE_STATUSES = {
        "IPTA_TIMESERIES_READY",
        "PUBLISH_PRODUCTS_SCRIPT_READY",
        "PUBLISH_PRODUCTS_RUNNING",
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_PUBLISH_DONE_STATUSES = {
        "PRODUCTS_READY",
        "MONITOR_POINTS_SCRIPT_READY",
        "MONITOR_POINTS_RUNNING",
        "MONITOR_POINTS_READY",
    }
    _WORKFLOW_MONITOR_DONE_STATUSES = {
        "MONITOR_POINTS_READY",
    }

    def __init__(self) -> None:
        project_drive = Path(settings.PROJECT_ROOT).drive
        default_runtime_root = Path(f"{project_drive}\\production_runtime") if project_drive else Path(settings.PROJECT_ROOT) / "runtime"
        self.trial_root = Path(settings.GAMMA_SBAS_TRIAL_ROOT or default_runtime_root / "gamma_ipta_trials")
        self.production_root = Path(settings.GAMMA_SBAS_WORK_ROOT or default_runtime_root / "sbas_insar_work")
        self.product_root = Path(settings.GAMMA_SBAS_PRODUCT_ROOT or Path(settings.TIMESERIES_PRODUCT_DIR) / "sbas")

    def product_run_root(self) -> Path:
        return self.product_root / "runs"

    def product_run_dir(self, run_id: str) -> Path:
        clean_id = str(run_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("invalid run id")
        return self.product_run_root() / clean_id

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "workflow_code": "sbas_insar",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "implementation_state": "expert_manifest_script_runner_primary",
            "trial_root": str(self.trial_root),
            "production_root": str(self.production_root),
            "product_root": str(self.product_root),
            "min_common_overlap_ratio": self._effective_min_common_overlap_ratio(None),
            "workflow_runner": {
                "enabled": bool(settings.GAMMA_SBAS_ENABLED),
                "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
                "wsl_distro": settings.GAMMA_SBAS_WSL_DISTRO,
                "python": settings.GAMMA_SBAS_PYTHON,
                "env_script": settings.GAMMA_SBAS_ENV_SCRIPT,
                "work_root": settings.GAMMA_SBAS_WORK_ROOT,
                "product_root": settings.GAMMA_SBAS_PRODUCT_ROOT,
                "style": "expert_document_manifest_and_scripts",
            },
            "workflow_node_count": len(GAMMA_SBAS_WORKFLOW_STEPS),
            "supported_sensors": ["LT1", "S1"],
            "sensor_profiles": [
                {
                    "sensor_family": "LT1",
                    "profile_code": "lt1_gamma_sbas",
                    "execution_enabled": True,
                    "description": "LT-1 Gamma SBAS workflow generated from the expert command document.",
                },
                {
                    "sensor_family": "S1",
                    "profile_code": "s1_gamma_sbas",
                    "execution_enabled": False,
                    "description": "Sentinel-1 stack discovery and planning only; Gamma TOPS/SBAS execution is not enabled.",
                },
            ],
            "supported_products": [item["key"] for item in PRODUCT_DEFINITIONS],
            "run_submission": {
                "enabled": True,
                "execution_enabled": True,
                "status_after_submit": "WORKFLOW_READY",
                "description": "Creates the expert-document workspace, manifest, scripts, and a queued Gamma SBAS workflow runner job.",
            },
            "expert_workspace": {
                "schema": "insar.gamma-sbas-workflow/v1",
                "directories": list(EXPERT_WORKSPACE_DIRS),
                "steps": [dict(item) for item in GAMMA_SBAS_WORKFLOW_STEPS],
                "expert_document_steps": [dict(item) for item in GAMMA_SBAS_EXPERT_DOCUMENT_STEPS],
            },
            "baseline_audit": {
                "enabled": True,
                "default_rlks": 8,
                "default_azlks": 8,
                "default_max_delta_n": 1,
                "stage_status_after_success": "BASELINE_AUDIT_READY",
            },
            "coregistration": {
                "enabled": True,
                "execution_enabled": True,
                "execution_mode": "queued_background_task",
                "job_type": "SBAS_COREGISTRATION",
                "default_strategy": "expert_create_offset_init_offset_slc_interp",
                "requires_status": "ITAB_APPROVED",
            },
            "rdc_dem": {
                "enabled": True,
                "execution_enabled": True,
                "execution_mode": "queued_background_task",
                "job_type": "SBAS_RDC_DEM",
                "default_strategy": "expert_dem_import_gc_map2_pixel_area_gc_map_fine",
                "requires_status": "COREGISTRATION_READY",
            },
            "interferograms": {
                "enabled": True,
                "execution_enabled": True,
                "execution_mode": "queued_background_task",
                "job_type": "SBAS_INTERFEROGRAMS",
                "default_strategy": "expert_mk_diff_2d_mk_adf_2d_mk_unw_2d",
                "requires_status": "RDC_DEM_READY",
            },
            "detrend_atm": {
                "enabled": True,
                "execution_enabled": True,
                "execution_mode": "workflow_or_direct_stage",
                "default_strategy": "expert_quad_fit_quad_sub_atm_mod_2d_sub_phase",
                "requires_status": "INTERFEROGRAMS_READY",
                "stage_status_after_success": "DETREND_ATM_READY",
            },
            "ipta_timeseries": {
                "enabled": True,
                "execution_enabled": True,
                "execution_mode": "queued_background_task",
                "job_type": "SBAS_IPTA_TIMESERIES",
                "default_strategy": "expert_three_pass_mb_real_to_cpx_unw_model",
                "default_mb_mode": DEFAULT_IPTA_MB_MODE,
                "mb_mode_description": IPTA_MB_MODE_DESCRIPTIONS[DEFAULT_IPTA_MB_MODE],
                "requires_status": "DETREND_ATM_READY",
            },
            "publish_products": {
                "enabled": True,
                "execution_enabled": True,
                "requires_status": "IPTA_TIMESERIES_READY",
                "status_after_success": "PRODUCTS_READY",
                "default_strategy": "gamma_geocode_back_data2geotiff_los_sign_conversion",
                "geocoded_preview_source": "EPSG:4326 GeoTIFF",
            },
            "monitor_point_modes": ["auto_representative_points", "auto_low_sigma_high_rate", "manual_lonlat"],
            "default_los_convention": {
                "key": "los_rate_toward_mm_per_year",
                "description": "toward radar positive; away from radar negative",
                "gamma_dispmap_equivalent": "sflg=0",
            },
            "sign_conventions": [
                {
                    "key": "away_positive",
                    "formula": "phase_rate*wavelength/(4*pi)*1000",
                    "description": "away from radar positive; same sign as Gamma phase",
                },
                {
                    "key": "toward_positive",
                    "formula": "-phase_rate*wavelength/(4*pi)*1000",
                    "description": "toward radar positive; Gamma dispmap default sflg=0",
                },
            ],
            "next_enabled_operation": "gamma_ipta_timeseries_background_job",
        }

    def discover_stacks(
        self,
        *,
        sensor_family: str = "LT1",
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int = 3,
        require_orbits: bool = True,
        include_scenes: bool = False,
        limit: int = 30,
        platform: str | None = None,
        relative_orbit: str | None = None,
        orbit_direction: str | None = None,
        admin_region: str | None = None,
        discovery_mode: str = "strict",
        aoi_bbox: dict[str, Any] | None = None,
        min_aoi_coverage_ratio: float = 0.01,
        min_common_overlap_ratio: float | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        sensor_family = self._normalize_sensor_family(sensor_family)
        source_paths = self._resolve_source_roots(source_roots, sensor_family=sensor_family)
        orbit_paths = self._resolve_orbit_roots(orbit_roots, sensor_family=sensor_family)
        root_warnings = self._build_root_resolution_warnings(
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            source_paths=source_paths,
            orbit_paths=orbit_paths,
            sensor_family=sensor_family,
        )
        normalized_mode = self._normalize_discovery_mode(discovery_mode)
        min_aoi_coverage_ratio = max(0.0, min(1.0, float(min_aoi_coverage_ratio or 0.0)))
        min_common_overlap_ratio = self._effective_min_common_overlap_ratio(min_common_overlap_ratio)
        discovery_aoi = self._build_discovery_aoi(admin_region=admin_region, aoi_bbox=aoi_bbox)
        effective_mode = "aoi" if normalized_mode == "aoi" and discovery_aoi.get("geometry") is not None else "strict"
        cache_key = self._discovery_cache_key(
            source_paths=source_paths,
            orbit_paths=orbit_paths,
            sensor_family=sensor_family,
            min_scenes=min_scenes,
            require_orbits=require_orbits,
            include_scenes=include_scenes,
            limit=limit,
            platform=platform,
            relative_orbit=relative_orbit,
            orbit_direction=orbit_direction,
            admin_region=admin_region,
            discovery_mode=effective_mode,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
            strategy_version="gamma-overlap-substack-v4",
        )
        if not force_refresh:
            cached = self._read_discovery_cache(cache_key)
            if cached is not None:
                cached = dict(cached)
                cached["warnings"] = root_warnings
                return cached

        scenes: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        platform_filter = str(platform or "").strip().upper()
        rel_filter = str(relative_orbit or "").strip()
        direction_filter = str(orbit_direction or "").strip().upper()
        aoi_geometry = discovery_aoi.get("geometry") if effective_mode == "aoi" else None

        for root in source_paths:
            try:
                scene_iter = (
                    self._iter_s1_scene_sources(root)
                    if sensor_family == "S1"
                    else self._iter_lt1_scene_dirs(root)
                )
                for scene_source in scene_iter:
                    try:
                        scene = (
                            self._parse_s1_scene(scene_source, orbit_paths)
                            if sensor_family == "S1"
                            else self._parse_lt1_scene(scene_source, orbit_paths)
                        )
                    except Exception as exc:
                        errors.append({"scene_source": str(scene_source), "error": str(exc)})
                        continue
                    if platform_filter and scene.get("satellite") != platform_filter:
                        continue
                    if rel_filter and str(scene.get("relative_orbit") or "") != rel_filter:
                        continue
                    if direction_filter and str(scene.get("orbit_direction") or "").upper() != direction_filter:
                        continue
                    if aoi_geometry is not None:
                        scene = self._scene_with_aoi_metrics(scene, aoi_geometry)
                        if not scene.get("aoi_intersects"):
                            continue
                        if float(scene.get("aoi_overlap_ratio") or 0.0) < min_aoi_coverage_ratio:
                            continue
                    scenes.append(scene)
            except Exception as exc:
                errors.append({"source_root": str(root), "error": str(exc)})

        if sensor_family == "S1":
            scenes = self._dedupe_s1_scenes(scenes)

        grouped_initial: dict[str, list[dict[str, Any]]] = {}
        for scene in scenes:
            group_key = (
                self._aoi_stack_group_key(scene)
                if effective_mode == "aoi"
                else self._stack_group_key(scene)
            )
            grouped_initial.setdefault(group_key, []).append(scene)

        cluster_source = (
            "aoi_footprint_common_overlap"
            if effective_mode == "aoi"
            else "footprint_common_overlap"
        )
        candidate_scene_groups: list[dict[str, Any]] = []
        for observation_key, group_scenes in grouped_initial.items():
            candidate_scene_groups.extend(
                self._build_discovery_scene_groups(
                    observation_key=observation_key,
                    group_scenes=group_scenes,
                    discovery_mode=effective_mode,
                    require_orbits=require_orbits,
                    min_scenes=min_scenes,
                    min_common_overlap_ratio=min_common_overlap_ratio,
                    cluster_source=cluster_source,
                )
            )

        candidates = [
            self._build_stack_candidate(
                scene_group["scenes"],
                min_scenes=min_scenes,
                require_orbits=require_orbits,
                discovery_mode=effective_mode,
                aoi_summary=discovery_aoi.get("summary"),
                min_common_overlap_ratio=min_common_overlap_ratio,
            )
            for scene_group in candidate_scene_groups
            if scene_group.get("scenes")
        ]
        candidates = self._dedupe_stack_candidates(candidates)
        if admin_region and effective_mode != "aoi":
            candidates = [
                candidate for candidate in candidates
                if admin_region_matches(candidate.get("admin_region"), admin_region)
            ]
        self._annotate_stack_candidate_identity(
            candidates,
            existing_run_index=self._existing_run_identity_index(),
        )
        candidates.sort(
            key=lambda item: (
                int(item.get("status") != "READY"),
                -int(item.get("orbit_ready_scene_count") or 0),
                -int(item.get("scene_count") or 0),
                str(item.get("date_start") or ""),
            )
        )
        if not include_scenes:
            for candidate in candidates:
                candidate.pop("scenes", None)
        if limit > 0:
            candidates = candidates[:limit]

        snapshot = {
            "schema": "insar.sbas-stack-discovery/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "sensor_family": sensor_family,
            "source_roots": [str(path) for path in source_paths],
            "orbit_roots": [str(path) for path in orbit_paths],
            "min_scenes": min_scenes,
            "require_orbits": require_orbits,
            "discovery_mode": effective_mode,
            "requested_discovery_mode": normalized_mode,
            "aoi": discovery_aoi.get("summary"),
            "min_aoi_coverage_ratio": min_aoi_coverage_ratio,
            "min_common_overlap_ratio": min_common_overlap_ratio,
            "scene_count": len(scenes),
            "candidate_count": len(candidates),
            "errors": errors[:50],
            "warnings": root_warnings,
            "items": candidates,
        }
        snapshot_path = self._write_runtime_json(
            "discoveries",
            f"discovery_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json",
            snapshot,
        )
        snapshot["snapshot_path"] = str(snapshot_path)
        self._write_discovery_cache(cache_key, snapshot)
        return snapshot

    def audit_stack(
        self,
        stack_id: str,
        *,
        sensor_family: str = "LT1",
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int = 3,
        require_orbits: bool = True,
        discovery_mode: str = "strict",
        admin_region: str | None = None,
        aoi_bbox: dict[str, Any] | None = None,
        min_aoi_coverage_ratio: float = 0.01,
        min_common_overlap_ratio: float | None = None,
    ) -> dict[str, Any]:
        sensor_family = self._normalize_sensor_family(sensor_family)
        discovery = self.discover_stacks(
            sensor_family=sensor_family,
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=min_scenes,
            require_orbits=require_orbits,
            include_scenes=True,
            limit=0,
            discovery_mode=discovery_mode,
            admin_region=admin_region,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
        )
        candidate = next(
            (item for item in discovery.get("items", []) if item.get("stack_id") == stack_id),
            None,
        )
        if not candidate:
            raise FileNotFoundError(f"stack candidate not found: {stack_id}")

        usable_scenes = [
            scene for scene in candidate.get("scenes", [])
            if (scene.get("has_orbit") or not require_orbits)
        ]
        usable_scenes.sort(key=lambda item: str(item.get("date") or ""))
        duplicate_audit = self._duplicate_scene_date_audit(usable_scenes)
        pairs = self._build_adjacent_pairs(usable_scenes)
        blockers: list[str] = []
        warnings: list[str] = []
        for blocker in candidate.get("blockers") or []:
            text = str(blocker or "").strip()
            if text and text not in blockers:
                blockers.append(text)

        if len(usable_scenes) < min_scenes:
            blockers.append(
                f"Only {len(usable_scenes)} usable scenes; minimum required is {min_scenes}."
            )
        if require_orbits and candidate.get("missing_orbit_count"):
            orbit_label = "EOF" if sensor_family == "S1" else "TXT"
            warnings.append(
                f"{candidate.get('missing_orbit_count')} scenes are excluded because precise orbit {orbit_label} is missing."
            )
        if len(pairs) < max(0, len(usable_scenes) - 1):
            blockers.append("Adjacent pair network is not fully connected.")
        if duplicate_audit.get("has_duplicate_dates"):
            blockers.append(
                "Duplicate acquisition dates remain in the Gamma date-keyed stack; "
                "each executable SBAS stack must contain one scene per date."
            )
        for pair in pairs:
            if int(pair.get("delta_days") or 0) > 180:
                warnings.append(
                    f"Long temporal gap: {pair.get('master_date')} -> {pair.get('slave_date')} "
                    f"({pair.get('delta_days')} days)."
                )
        candidate_duplicate_audit = candidate.get("date_keyed_duplicate_audit") or {}
        if candidate_duplicate_audit.get("excluded_scene_count"):
            warnings.append(
                "Same-date LT1 scenes were reduced to one representative scene per date "
                "for the Gamma date-keyed expert workflow."
            )

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        ready_status = (
            "READY_FOR_S1_GAMMA_SBAS_PLANNING"
            if sensor_family == "S1"
            else "READY_FOR_GAMMA_BASELINE_AUDIT"
        )
        manifest = {
            "schema": "insar.gamma-ipta-sbas-stack-manifest/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "stack_id": stack_id,
            "sensor_family": sensor_family,
            "profile_code": "s1_gamma_sbas" if sensor_family == "S1" else "lt1_gamma_sbas",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "workflow": "Sentinel-1 Gamma SBAS planning" if sensor_family == "S1" else "LT1 Gamma SBAS expert command workflow",
            "status": ready_status if not blockers else "BLOCKED",
            "require_orbits": require_orbits,
            "min_scenes": min_scenes,
            "discovery_mode": candidate.get("discovery_mode") or discovery.get("discovery_mode") or "strict",
            "aoi": candidate.get("aoi") or discovery.get("aoi"),
            "common_overlap_ratio": candidate.get("common_overlap_ratio"),
            "min_common_overlap_ratio": discovery.get("min_common_overlap_ratio"),
            "scene_identity_hash": candidate.get("scene_identity_hash"),
            "scene_name_count": candidate.get("scene_name_count"),
            "scene_name_preview": candidate.get("scene_name_preview") or [],
            "scene_names": candidate.get("scene_names") or [],
            "date_sequence_hash": candidate.get("date_sequence_hash"),
            "same_date_sequence_candidate_count": candidate.get("same_date_sequence_candidate_count"),
            "same_date_sequence_distinct_scene_group_count": candidate.get("same_date_sequence_distinct_scene_group_count"),
            "existing_same_scene_runs": candidate.get("existing_same_scene_runs") or [],
            "stack": {
                key: candidate.get(key)
                for key in [
                    "satellite",
                    "satellite_mode",
                    "receiving_station",
                    "relative_orbit",
                    "orbit_direction",
                    "imaging_mode",
                    "polarization",
                    "center_bucket",
                    "reference_date",
                ]
            },
            "geographic_coverage": self._build_stack_geographic_coverage({"scenes": usable_scenes}),
            "scenes": usable_scenes,
            "excluded_scenes": [
                scene for scene in candidate.get("scenes", [])
                if scene not in usable_scenes
            ] + (candidate.get("date_keyed_excluded_scenes") or []),
            "date_keyed_duplicate_audit": candidate_duplicate_audit or duplicate_audit,
            "pair_network": {
                "strategy": "adjacent_temporal_initial",
                "gamma_baseline_status": "PENDING",
                "execution_enabled": sensor_family != "S1",
                "pairs": pairs,
            },
            "blockers": blockers,
            "warnings": sorted(set(warnings)),
            "execution_enabled": sensor_family != "S1",
            "next_stage": (
                "Sentinel-1 stack is ready for planning; Gamma TOPS/SBAS scripts are not enabled yet."
                if sensor_family == "S1"
                else "run the LT1 Gamma SBAS expert command workflow and review the generated command audit"
            ),
        }
        manifest_path = self._write_runtime_json(
            Path("stack_manifests") / stack_id,
            f"{timestamp}_stack_manifest.json",
            manifest,
        )
        pair_network_path = self._write_runtime_json(
            Path("stack_manifests") / stack_id,
            f"{timestamp}_pair_network.json",
            manifest["pair_network"],
        )
        return {
            "stack_id": stack_id,
            "status": manifest["status"],
            "manifest_path": str(manifest_path),
            "pair_network_path": str(pair_network_path),
            "manifest": manifest,
        }

    def create_run(
        self,
        stack_id: str,
        *,
        sensor_family: str = "LT1",
        run_label: str | None = None,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int = 3,
        require_orbits: bool = True,
        monitor_points: list[dict[str, Any]] | None = None,
        monitor_point_strategy: str = "auto_representative_points",
        discovery_mode: str = "strict",
        admin_region: str | None = None,
        aoi_bbox: dict[str, Any] | None = None,
        min_aoi_coverage_ratio: float = 0.01,
        min_common_overlap_ratio: float | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        sensor_family = self._normalize_sensor_family(sensor_family)
        audit = self.audit_stack(
            stack_id,
            sensor_family=sensor_family,
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=min_scenes,
            require_orbits=require_orbits,
            discovery_mode=discovery_mode,
            admin_region=admin_region,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
        )
        manifest = audit["manifest"]
        ready_statuses = {"READY_FOR_GAMMA_BASELINE_AUDIT", "READY_FOR_S1_GAMMA_SBAS_PLANNING"}
        if manifest.get("status") not in ready_statuses:
            blockers = "; ".join(str(item) for item in (manifest.get("blockers") or []) if item)
            raise ValueError(
                "stack manifest is not ready for run planning"
                + (f": {blockers}" if blockers else "")
            )

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        run_id = self._stable_id(f"{stack_id}|{timestamp}|{run_label or ''}")
        run_dir = self.production_root / "runs" / run_id
        work_dir = run_dir / "work"
        publish_dir = run_dir / "publish"
        log_dir = run_dir / "logs"
        for path in (work_dir, publish_dir, log_dir):
            path.mkdir(parents=True, exist_ok=True)
        expert_workspace = (
            self._ensure_s1_planning_workspace(run_dir)
            if sensor_family == "S1"
            else self._ensure_expert_workspace(run_dir)
        )

        monitor_config = self._build_monitor_point_config(
            monitor_points=monitor_points,
            strategy=monitor_point_strategy,
            stack_manifest=manifest,
        )
        run_manifest = {
            "schema": "insar.gamma-ipta-sbas-run/v1",
            "run_id": run_id,
            "run_label": run_label or None,
            "workflow_code": "sbas_insar",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "sensor_family": sensor_family,
            "profile_code": "s1_gamma_sbas" if sensor_family == "S1" else "lt1_gamma_sbas",
            "execution_mode": "s1_gamma_sbas_planning_only" if sensor_family == "S1" else "expert_manifest_script_workflow",
            "execution_enabled": sensor_family != "S1",
            "status": "S1_GAMMA_SBAS_PLANNED" if sensor_family == "S1" else "WORKFLOW_READY",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "stack_id": stack_id,
            "discovery_mode": manifest.get("discovery_mode"),
            "aoi": manifest.get("aoi"),
            "common_overlap_ratio": manifest.get("common_overlap_ratio"),
            "min_common_overlap_ratio": manifest.get("min_common_overlap_ratio"),
            "scene_identity_hash": manifest.get("scene_identity_hash"),
            "scene_name_count": manifest.get("scene_name_count"),
            "scene_name_preview": manifest.get("scene_name_preview") or [],
            "scene_names": manifest.get("scene_names") or [],
            "date_sequence_hash": manifest.get("date_sequence_hash"),
            "same_date_sequence_candidate_count": manifest.get("same_date_sequence_candidate_count"),
            "same_date_sequence_distinct_scene_group_count": manifest.get("same_date_sequence_distinct_scene_group_count"),
            "stack_manifest_path": audit["manifest_path"],
            "pair_network_path": audit["pair_network_path"],
            "workflow_manifest_path": str(run_dir / "manifest.json"),
            "workflow_state_path": str(run_dir / "state" / "step_status.json"),
            "work_root": str(work_dir),
            "publish_root": str(publish_dir),
            "log_root": str(log_dir),
            "expert_workspace": expert_workspace,
            "stack": manifest.get("stack") or {},
            "scene_count": len(manifest.get("scenes") or []),
            "pair_count": len(((manifest.get("pair_network") or {}).get("pairs")) or []),
            "next_stage": "implement_s1_gamma_sbas_scripts" if sensor_family == "S1" else "workflow",
            "requires_user_action": [
                *(
                    [
                        "Review Sentinel-1 stack grouping, EOF coverage, subswath/burst policy, and common overlap before enabling execution.",
                        "Implement and verify Sentinel-1 Gamma TOPS/SBAS scripts before submitting workflow jobs.",
                    ]
                    if sensor_family == "S1"
                    else [
                        "Review Gamma base_calc baseline table before approving final itab.",
                        "Confirm monitoring-point source: manual points, imported layer, or automatic sampler.",
                        "Confirm geocoded preview products are published from EPSG:4326 GeoTIFFs.",
                    ]
                ),
            ],
            "monitor_points": monitor_config,
            "planning_only": True,
            "legacy_dry_run_request": bool(dry_run),
        }
        command_manifest = self._build_command_manifest(run_manifest, manifest)
        workflow_manifest = (
            self._build_s1_workflow_manifest(run_dir, run_manifest, manifest)
            if sensor_family == "S1"
            else self._build_workflow_manifest(run_dir, run_manifest, manifest)
        )

        run_manifest_path = self._write_json(run_dir / "run_manifest.json", run_manifest)
        command_manifest_path = self._write_json(run_dir / "gamma_command_manifest.json", command_manifest)
        workflow_manifest_path = self._write_json(run_dir / "manifest.json", workflow_manifest)
        monitor_config_path = self._write_json(run_dir / "monitor_points.json", monitor_config)
        self._write_json(run_dir / "state" / "step_status.json", self._initial_workflow_state(run_manifest, workflow_manifest))
        self._write_json(run_dir / "stack_manifest.json", manifest)
        self._write_json(run_dir / "pair_network.json", manifest.get("pair_network") or {})

        index_item = {
            **self._build_run_card(run_dir, run_manifest),
            "run_manifest_path": str(run_manifest_path),
            "gamma_command_manifest_path": str(command_manifest_path),
            "workflow_manifest_path": str(workflow_manifest_path),
            "monitor_config_path": str(monitor_config_path),
        }
        return {
            "run": index_item,
            "manifest": run_manifest,
            "command_manifest": command_manifest,
            "workflow_manifest": workflow_manifest,
            "monitor_points": monitor_config,
        }

    def list_runs(self) -> dict[str, Any]:
        run_root = self.production_root / "runs"
        items: list[dict[str, Any]] = []
        if not run_root.exists():
            return {"items": items, "count": 0, "run_root": str(run_root)}

        for manifest_path in sorted(run_root.glob("*/run_manifest.json")):
            try:
                manifest = self._read_json(manifest_path)
                items.append(self._build_run_card(manifest_path.parent, manifest))
            except Exception as exc:
                items.append(
                    {
                        "run_id": manifest_path.parent.name,
                        "status": "RUN_MANIFEST_UNREADABLE",
                        "run_dir": str(manifest_path.parent),
                        "error": str(exc),
                    }
                )
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"items": items, "count": len(items), "run_root": str(run_root)}

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest = self._read_json(run_dir / "run_manifest.json")
        command_manifest = self._read_optional_json(run_dir / "gamma_command_manifest.json")
        workflow_manifest = self._read_optional_json(run_dir / "manifest.json")
        if workflow_manifest and not workflow_manifest.get("expert_document"):
            workflow_manifest["expert_document"] = {
                "schema": "insar.gamma-sbas-expert-document/v1",
                "source": "LT1_GAMMA_SBAS_逐命令处理流程.docx",
                "section_count": len(GAMMA_SBAS_EXPERT_DOCUMENT_STEPS),
                "steps": self._build_expert_document_step_manifest(workflow_manifest.get("steps") or []),
            }
        workflow_state = self._read_optional_json(run_dir / "state" / "step_status.json")
        monitor_points = self._read_optional_json(run_dir / "monitor_points.json")
        geographic_coverage = self._build_run_geographic_coverage(run_dir, manifest)
        return {
            "run": self._build_run_card(run_dir, manifest),
            "manifest": manifest,
            "command_manifest": command_manifest,
            "workflow_manifest": workflow_manifest,
            "workflow_state": workflow_state,
            "runtime_status": self._build_runtime_status(
                run_dir,
                manifest=manifest,
                workflow_manifest=workflow_manifest or {},
                workflow_state=workflow_state or {},
            ),
            "monitor_points": monitor_points,
            "geographic_coverage": geographic_coverage,
            "artifacts": self._build_run_artifacts(run_dir),
        }

    def _build_runtime_status(
        self,
        run_dir: Path,
        *,
        manifest: dict[str, Any],
        workflow_manifest: dict[str, Any],
        workflow_state: dict[str, Any],
    ) -> dict[str, Any]:
        steps = workflow_state.get("steps") or {}
        manifest_steps = workflow_manifest.get("steps") or []
        current_step = None
        for step in manifest_steps:
            step_id = str(step.get("id") or "")
            state = steps.get(step_id) or {}
            status = str(state.get("status") or step.get("status") or "").strip().upper()
            if status == "RUNNING":
                current_step = {
                    "id": step_id,
                    "name": state.get("name") or step.get("name") or step_id,
                    "status": status,
                    "started_at": state.get("started_at"),
                    "log": state.get("log") or step.get("log"),
                    "script": state.get("script") or step.get("script"),
                }
                break
        if current_step is None:
            for step in manifest_steps:
                step_id = str(step.get("id") or "")
                state = steps.get(step_id) or {}
                status = str(state.get("status") or step.get("status") or "").strip().upper()
                if status in {"FAILED", "PENDING", "SCRIPT_READY"}:
                    current_step = {
                        "id": step_id,
                        "name": state.get("name") or step.get("name") or step_id,
                        "status": status,
                        "started_at": state.get("started_at"),
                        "ended_at": state.get("ended_at"),
                        "log": state.get("log") or step.get("log"),
                        "script": state.get("script") or step.get("script"),
                    }
                    break

        workflow_summary = (
            self._summarize_workflow_state(workflow_manifest, workflow_state)
            if workflow_manifest and workflow_state
            else {}
        )
        recent_logs = self._recent_run_logs(run_dir)
        latest_log = recent_logs[0] if recent_logs else None
        run_status = str(manifest.get("status") or "UNKNOWN").strip().upper()
        return {
            "schema": "insar.sbas-runtime-status/v1",
            "run_id": manifest.get("run_id") or run_dir.name,
            "run_status": run_status,
            "active": "RUNNING" in run_status or bool(current_step and current_step.get("status") == "RUNNING"),
            "current_step": current_step,
            "workflow_updated_at": workflow_state.get("updated_at"),
            "workflow_summary": workflow_summary,
            "latest_log_updated_at": latest_log.get("modified_at") if latest_log else None,
            "recent_logs": recent_logs,
            "wsl": {
                "distro": settings.GAMMA_SBAS_WSL_DISTRO,
                "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
                "run_root": self._windows_path_to_wsl_mount(str(run_dir)),
            },
            "overlap_gate": {
                "common_overlap_ratio": manifest.get("common_overlap_ratio"),
                "min_common_overlap_ratio": manifest.get("min_common_overlap_ratio"),
                "passed": (
                    float(manifest.get("common_overlap_ratio") or 0.0)
                    >= float(manifest.get("min_common_overlap_ratio") or 0.0)
                ),
            },
        }

    def _recent_run_logs(self, run_dir: Path, *, limit: int = 8, tail_chars: int = 1600) -> list[dict[str, Any]]:
        log_dir = run_dir / "logs"
        if not log_dir.is_dir():
            return []
        files = [path for path in log_dir.glob("*") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
        logs: list[dict[str, Any]] = []
        for path in files[: max(1, int(limit))]:
            try:
                stat = path.stat()
                tail = self._tail_text(path.read_text(encoding="utf-8", errors="replace"), tail_chars)
                modified_at = datetime.utcfromtimestamp(stat.st_mtime).isoformat(timespec="seconds") + "Z"
                logs.append(
                    {
                        "name": path.name,
                        "relative_path": str(path.relative_to(run_dir)).replace("\\", "/"),
                        "size_bytes": stat.st_size,
                        "modified_at": modified_at,
                        "tail": tail,
                    }
                )
            except Exception as exc:
                logs.append(
                    {
                        "name": path.name,
                        "relative_path": str(path.relative_to(run_dir)).replace("\\", "/"),
                        "error": str(exc),
                    }
                )
        return logs

    async def delete_run_record(self, run_id: str, *, db: Any) -> dict[str, Any]:
        from ..models import (
            ResultAssetORM,
            ResultIssueORM,
            ResultProductORM,
            SystemJobORM,
            SystemTaskORM,
            TaskLogORM,
        )

        clean_id = str(run_id or "").strip()
        run_dir = self._resolve_run_dir(clean_id)
        manifest = self._read_json(run_dir / "run_manifest.json")
        run_ids = {
            clean_id,
            str(manifest.get("run_id") or "").strip(),
            str(manifest.get("workflow_run_id") or "").strip(),
        }
        run_ids = {item for item in run_ids if item}

        like_conditions = [
            cast(SystemTaskORM.params, String).ilike(f"%{item}%")
            for item in run_ids
        ]
        task_conditions = list(like_conditions)
        for item in run_ids:
            task_conditions.append(SystemTaskORM.task_name.ilike(f"%{item}%"))

        tasks = []
        if task_conditions:
            task_result = await db.execute(select(SystemTaskORM).where(or_(*task_conditions)))
            tasks = list(task_result.scalars().all())
        task_ids = sorted({str(task.task_id or "").strip() for task in tasks if str(task.task_id or "").strip()})

        job_conditions = [
            cast(SystemJobORM.payload, String).ilike(f"%{item}%")
            for item in run_ids
        ]
        for item in run_ids:
            job_conditions.append(SystemJobORM.workflow_run_id == item)
        if task_ids:
            job_conditions.append(SystemJobORM.task_id.in_(task_ids))

        jobs = []
        if job_conditions:
            job_result = await db.execute(select(SystemJobORM).where(or_(*job_conditions)))
            jobs = list(job_result.scalars().all())

        active_task_statuses = {"PENDING", "RUNNING"}
        active_job_statuses = {"READY", "PENDING", "RUNNING", "RETRY"}
        active_tasks = [
            task.task_id
            for task in tasks
            if str(task.status or "").strip().upper() in active_task_statuses
        ]
        active_jobs = [
            job.job_id
            for job in jobs
            if str(job.status or "").strip().upper() in active_job_statuses
        ]
        if active_tasks or active_jobs:
            raise ValueError(
                "Cannot delete an SBAS run with active task/job: "
                f"tasks={active_tasks or []}, jobs={active_jobs or []}"
            )

        product_conditions = [
            ResultProductORM.catalog_name == "sbas_insar",
            or_(
                *[
                    or_(
                        ResultProductORM.run_key == item,
                        ResultProductORM.product_id.ilike(f"%{item}%"),
                        ResultProductORM.manifest_path.ilike(f"%{item}%"),
                        ResultProductORM.publish_dir.ilike(f"%{item}%"),
                    )
                    for item in run_ids
                ]
            ),
        ]
        product_result = await db.execute(select(ResultProductORM).where(*product_conditions))
        products = list(product_result.scalars().all())
        product_ids = [product.id for product in products]
        if product_ids:
            await db.execute(delete(ResultIssueORM).where(ResultIssueORM.product_ref_id.in_(product_ids)))
            await db.execute(delete(ResultAssetORM).where(ResultAssetORM.product_ref_id.in_(product_ids)))
            await db.execute(delete(ResultProductORM).where(ResultProductORM.id.in_(product_ids)))

        job_ids = sorted({str(job.job_id or "").strip() for job in jobs if str(job.job_id or "").strip()})
        if job_ids:
            await db.execute(delete(SystemJobORM).where(SystemJobORM.job_id.in_(job_ids)))
        if task_ids:
            await db.execute(delete(TaskLogORM).where(TaskLogORM.task_id.in_(task_ids)))
            await db.execute(delete(SystemTaskORM).where(SystemTaskORM.task_id.in_(task_ids)))

        deleted_stack_files: list[str] = []
        for key in ("stack_manifest_path", "pair_network_path"):
            path = self._resolve_production_delete_path(manifest.get(key))
            if path is not None and path.is_file():
                path.unlink()
                deleted_stack_files.append(str(path))
                try:
                    parent = path.parent
                    stack_root = (self.production_root / "stack_manifests").resolve()
                    parent.relative_to(stack_root)
                    if parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
                except Exception:
                    pass

        shutil.rmtree(run_dir)
        await db.commit()
        return {
            "run_id": clean_id,
            "deleted": True,
            "run_dir_deleted": str(run_dir),
            "stack_files_deleted": deleted_stack_files,
            "tasks_deleted": len(task_ids),
            "jobs_deleted": len(job_ids),
            "products_deleted": len(product_ids),
        }

    def run_baseline_audit(
        self,
        run_id: str,
        *,
        execute: bool = True,
        rlks: int = 8,
        azlks: int = 8,
        max_delta_n: int = 1,
        timeout_seconds: int = 21600,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        if manifest.get("status") not in {
            "PLANNED_GAMMA_BASELINE_AUDIT",
            "WORKFLOW_READY",
            "WORKFLOW_RUNNING",
            "BASELINE_AUDIT_SCRIPT_READY",
            "BASELINE_AUDIT_FAILED",
            "BASELINE_AUDIT_READY",
        }:
            raise ValueError(f"run status does not allow baseline audit: {manifest.get('status')}")

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(azlks, default=8, minimum=1, maximum=64)
        max_delta_n = self._bounded_int(max_delta_n, default=1, minimum=1, maximum=100)
        timeout_seconds = self._bounded_int(timeout_seconds, default=21600, minimum=60, maximum=86400)

        script_path = self._write_baseline_audit_script(
            run_dir,
            stack_manifest=stack_manifest,
            rlks=rlks,
            azlks=azlks,
            max_delta_n=max_delta_n,
        )
        manifest["baseline_audit"] = {
            "script_path": str(script_path),
            "rlks": rlks,
            "azlks": azlks,
            "max_delta_n": max_delta_n,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if not execute:
            baseline_summary = self._build_baseline_summary(run_dir)
            if baseline_summary.get("adjacent_pair_count"):
                manifest["status"] = "BASELINE_AUDIT_READY"
                manifest["next_stage"] = "approve_itab"
                manifest["baseline_audit"]["summary"] = baseline_summary
                manifest["baseline_audit"]["approved_for_next_stage"] = False
                self._write_json(run_dir / "baseline_audit_summary.json", baseline_summary)
                self._write_json(run_dir / "pair_network_baseline_audit.json", baseline_summary.get("pair_network") or {})
                self._write_json(run_dir / "pair_network.json", baseline_summary.get("pair_network") or {})
            else:
                manifest["status"] = "BASELINE_AUDIT_SCRIPT_READY"
                manifest["next_stage"] = "execute_baseline_audit"
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_baseline(run_dir, manifest, baseline_summary if baseline_summary.get("adjacent_pair_count") else None)
            return self.get_run_detail(run_id)

        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        script_wsl = self._windows_path_to_wsl_mount(str(script_path))
        command = self._baseline_execution_command(str(script_wsl))
        completed = subprocess.run(
            command,
            cwd=str(run_dir),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        execution = {
            "started_at": started_at,
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "command": command,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }

        baseline_summary = self._build_baseline_summary(run_dir)
        manifest["baseline_audit"] = {
            **manifest["baseline_audit"],
            "execution": execution,
            "summary": baseline_summary,
        }
        if completed.returncode == 0 and baseline_summary.get("adjacent_pair_count"):
            manifest["status"] = "BASELINE_AUDIT_READY"
            manifest["next_stage"] = "approve_itab"
            manifest["baseline_audit"]["approved_for_next_stage"] = False
            self._write_json(run_dir / "baseline_audit_summary.json", baseline_summary)
            self._write_json(run_dir / "pair_network_baseline_audit.json", baseline_summary.get("pair_network") or {})
            self._write_json(run_dir / "pair_network.json", baseline_summary.get("pair_network") or {})
        else:
            manifest["status"] = "BASELINE_AUDIT_FAILED"
            manifest["next_stage"] = "fix_baseline_audit"

        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_baseline(run_dir, manifest, baseline_summary)
        return self.get_run_detail(run_id)

    def _baseline_execution_command(self, script_wsl: str) -> list[str]:
        return self._script_execution_command(script_wsl)

    def _script_execution_command(self, script_wsl: str) -> list[str]:
        if os.name != "nt":
            return ["bash", script_wsl]
        return [
            "wsl.exe",
            "-d",
            settings.WSL_DISTRO or settings.PYINT_WSL_DISTRO or "Ubuntu-24.04",
            "bash",
            script_wsl,
        ]

    def decide_itab(
        self,
        run_id: str,
        *,
        decision: str,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        if manifest.get("status") in {
            "COREGISTRATION_SCRIPT_READY",
            "COREGISTRATION_RUNNING",
            "COREGISTRATION_READY",
            "RDC_DEM_SCRIPT_READY",
            "RDC_DEM_RUNNING",
            "RDC_DEM_READY",
        }:
            existing_decision = ((manifest.get("baseline_audit") or {}).get("itab_decision") or {}).get("decision")
            if normalized_decision == "approve" and existing_decision == "approve":
                return self.get_run_detail(run_id)
        previous_status = str(manifest.get("status") or "").strip()
        if previous_status not in {
            "BASELINE_AUDIT_READY",
            "ITAB_APPROVED",
            "ITAB_REJECTED",
            "COREGISTRATION_SCRIPT_READY",
            "COREGISTRATION_RUNNING",
            "COREGISTRATION_READY",
            "RDC_DEM_SCRIPT_READY",
            "RDC_DEM_RUNNING",
            "RDC_DEM_READY",
        }:
            raise ValueError(f"run status does not allow itab decision: {manifest.get('status')}")

        baseline_summary = self._read_optional_json(run_dir / "baseline_audit_summary.json")
        if not baseline_summary or not baseline_summary.get("adjacent_pair_count"):
            raise ValueError("baseline audit summary is missing or empty")

        decided_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        decision_payload = {
            "schema": "insar.sbas-itab-decision/v1",
            "run_id": run_id,
            "decision": normalized_decision,
            "reviewer": str(reviewer or "system").strip()[:120],
            "note": str(note or "").strip()[:1000],
            "decided_at": decided_at,
            "baseline_summary": {
                "adjacent_pair_count": baseline_summary.get("adjacent_pair_count"),
                "max_abs_bperp_m": baseline_summary.get("max_abs_bperp_m"),
                "max_delta_days": baseline_summary.get("max_delta_days"),
            },
        }

        baseline_state = manifest.setdefault("baseline_audit", {})
        if normalized_decision == "approve":
            source_itab = run_dir / "work" / "gamma" / "diff" / "itab_adjacent"
            if not source_itab.is_file():
                raise FileNotFoundError(f"Gamma adjacent itab not found: {source_itab}")
            approved_itab = run_dir / "work" / "gamma" / "diff" / "itab_approved"
            shutil.copyfile(source_itab, approved_itab)
            self._write_json(run_dir / "itab_decision.json", decision_payload)
            baseline_state["approved_for_next_stage"] = True
            baseline_state["itab_decision"] = decision_payload
            baseline_state["approved_itab_path"] = str(approved_itab)
            if previous_status in {"RDC_DEM_SCRIPT_READY", "RDC_DEM_RUNNING", "RDC_DEM_READY"}:
                manifest["status"] = previous_status
                manifest["next_stage"] = "coregistration"
            elif previous_status in {"COREGISTRATION_SCRIPT_READY", "COREGISTRATION_RUNNING", "COREGISTRATION_READY"}:
                manifest["status"] = previous_status
                manifest["next_stage"] = self._next_stage_for_status(previous_status)
            else:
                manifest["status"] = "ITAB_APPROVED"
                manifest["next_stage"] = "coregistration"
        else:
            self._write_json(run_dir / "itab_decision.json", decision_payload)
            baseline_state["approved_for_next_stage"] = False
            baseline_state["itab_decision"] = decision_payload
            manifest["status"] = "ITAB_REJECTED"
            manifest["next_stage"] = "revise_pair_network"

        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_itab_decision(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_coregistration(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
        azlks: int = 8,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("Coregistration execution is not enabled yet; submit with execute=false.")
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        if manifest.get("status") not in {
            "ITAB_APPROVED",
            "COREGISTRATION_SCRIPT_READY",
            "COREGISTRATION_FAILED",
            "RDC_DEM_SCRIPT_READY",
            "RDC_DEM_READY",
        }:
            raise ValueError(f"run status does not allow coregistration preparation: {manifest.get('status')}")
        approved_itab = run_dir / "work" / "gamma" / "diff" / "itab_approved"
        if not approved_itab.is_file():
            raise FileNotFoundError(f"approved itab not found: {approved_itab}")
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        reference_date = str((stack_manifest.get("stack") or {}).get("reference_date") or "").strip()
        if reference_date not in {str(scene.get("date")) for scene in scenes}:
            reference_date = str(scenes[len(scenes) // 2].get("date"))

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(azlks, default=8, minimum=1, maximum=64)
        itab_rows = self._parse_itab(approved_itab)
        if not itab_rows:
            raise ValueError("approved itab is empty")

        script_path = self._write_coregistration_script(
            run_dir,
            scenes=scenes,
            reference_date=reference_date,
            rlks=rlks,
            azlks=azlks,
        )
        coregistration = {
            "schema": "insar.gamma-coregistration-stage/v1",
            "strategy": "common_reference_to_stack_reference_date",
            "script_path": str(script_path),
            "approved_itab_path": str(approved_itab),
            "reference_date": reference_date,
            "scene_count": len(scenes),
            "approved_pair_count": len(itab_rows),
            "rlks": rlks,
            "azlks": azlks,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "outputs": {
                "common_dir": str(run_dir / "work" / "gamma" / f"common_{reference_date}"),
                "slc_tab": str(run_dir / "work" / "gamma" / f"common_{reference_date}" / "SLC_tab"),
                "rmli_tab": str(run_dir / "work" / "gamma" / f"common_{reference_date}" / "RMLI_tab"),
            },
        }
        manifest["coregistration"] = coregistration
        if self._stage_execution_completed(manifest.get("rdc_dem")):
            manifest["status"] = "RDC_DEM_READY"
            manifest["next_stage"] = "execute_coregistration"
        else:
            manifest["status"] = "COREGISTRATION_SCRIPT_READY"
            manifest["next_stage"] = "execute_coregistration"
        self._write_json(run_dir / "coregistration_plan.json", coregistration)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_coregistration(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_coregistration(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        azlks: int = 8,
        timeout_seconds: int = 43200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "COREGISTRATION_READY":
            return self.get_run_detail(run_id)
        if status in {"ITAB_APPROVED", "COREGISTRATION_FAILED", "RDC_DEM_SCRIPT_READY", "RDC_DEM_READY"}:
            self.prepare_coregistration(run_id, execute=False, rlks=rlks, azlks=azlks)
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"COREGISTRATION_SCRIPT_READY", "COREGISTRATION_RUNNING", "RDC_DEM_READY"}:
            raise ValueError(f"run status does not allow coregistration execution: {manifest.get('status')}")

        coregistration = dict(manifest.get("coregistration") or {})
        script_path = Path(self._path_to_windows(str(coregistration.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"coregistration script not found: {script_path}")

        timeout_seconds = self._bounded_int(timeout_seconds, default=43200, minimum=60, maximum=172800)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))

        coregistration["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["coregistration"] = coregistration
        manifest["status"] = "COREGISTRATION_RUNNING"
        manifest["next_stage"] = "coregistration"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_coregistration(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_coregistration_summary(
                run_dir,
                reference_date=coregistration.get("reference_date"),
            )
            execution = {
                **coregistration.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            coregistration = {**coregistration, "execution": execution, "summary": summary}
            manifest["coregistration"] = coregistration
            manifest["status"] = "COREGISTRATION_FAILED"
            manifest["next_stage"] = "fix_coregistration"
            self._write_json(run_dir / "coregistration_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_coregistration(run_dir, manifest)
            raise

        summary = self._build_coregistration_summary(
            run_dir,
            reference_date=coregistration.get("reference_date"),
        )
        execution = {
            **coregistration.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        coregistration = {**coregistration, "execution": execution, "summary": summary}
        manifest["coregistration"] = coregistration
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "COREGISTRATION_READY"
            manifest["next_stage"] = "rdc_dem"
            if self._stage_execution_completed(manifest.get("rdc_dem")):
                manifest["status"] = "RDC_DEM_READY"
                manifest["next_stage"] = "interferograms"
        else:
            manifest["status"] = "COREGISTRATION_FAILED"
            manifest["next_stage"] = "fix_coregistration"

        self._write_json(run_dir / "coregistration_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_coregistration(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_rdc_dem(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("RDC DEM execution is submitted through the background job endpoint.")

        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "RDC_DEM_READY":
            return self.get_run_detail(run_id)
        if status not in {
            "BASELINE_AUDIT_READY",
            "ITAB_APPROVED",
            "COREGISTRATION_SCRIPT_READY",
            "COREGISTRATION_READY",
            "RDC_DEM_SCRIPT_READY",
            "RDC_DEM_FAILED",
        }:
            raise ValueError(f"run status does not allow RDC DEM preparation: {manifest.get('status')}")

        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        reference_date = str(
            ((manifest.get("coregistration") or {}).get("reference_date"))
            or ((manifest.get("coregistration") or {}).get("summary") or {}).get("reference_date")
            or (stack_manifest.get("stack") or {}).get("reference_date")
            or ""
        ).strip()
        if not reference_date:
            raise ValueError("RDC DEM requires a reference date")

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        rmli_path, rmli_par_path = self._find_reference_rmli_paths(run_dir, reference_date)
        if not rmli_path.is_file() or not rmli_par_path.is_file():
            raise FileNotFoundError(f"reference RMLI is missing for {reference_date}: {rmli_path}")

        dem_source = self._resolve_rdc_dem_source(stack_manifest)
        script_path = self._write_rdc_dem_script(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            dem_source=dem_source,
        )
        gamma_dem_dir = run_dir / "work" / "gamma" / "dem"
        rdc_dem = {
            "schema": "insar.gamma-rdc-dem-stage/v1",
            "strategy": "gamma_gc_map_fine_reference_geometry",
            "script_path": str(script_path),
            "reference_date": reference_date,
            "rlks": rlks,
            "dem_source": dem_source,
            "reference_rmli": {
                "mli": str(rmli_path),
                "mli_par": str(rmli_par_path),
            },
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "outputs": {
                "dem_dir": str(gamma_dem_dir),
                "utm_dem": str(gamma_dem_dir / f"{reference_date}_{rlks}rlks.utm.dem"),
                "utm_dem_par": str(gamma_dem_dir / f"{reference_date}_{rlks}rlks.utm.dem.par"),
                "lookup_table": str(gamma_dem_dir / f"{reference_date}_{rlks}rlks.UTM_TO_RDC"),
                "rdc_dem": str(gamma_dem_dir / f"{reference_date}_{rlks}rlks.rdc.dem"),
                "diff_par": str(gamma_dem_dir / f"{reference_date}_{rlks}rlks.diff_par"),
            },
        }
        manifest["rdc_dem"] = rdc_dem
        manifest["status"] = "RDC_DEM_SCRIPT_READY"
        manifest["next_stage"] = "execute_rdc_dem"
        self._write_json(run_dir / "rdc_dem_plan.json", rdc_dem)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_rdc_dem(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_rdc_dem(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        timeout_seconds: int = 43200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "RDC_DEM_READY":
            return self.get_run_detail(run_id)
        if status in {"BASELINE_AUDIT_READY", "ITAB_APPROVED", "COREGISTRATION_SCRIPT_READY", "COREGISTRATION_READY", "RDC_DEM_FAILED"}:
            self.prepare_rdc_dem(run_id, execute=False, rlks=rlks)
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"RDC_DEM_SCRIPT_READY", "RDC_DEM_RUNNING"}:
            raise ValueError(f"run status does not allow RDC DEM execution: {manifest.get('status')}")

        rdc_dem = dict(manifest.get("rdc_dem") or {})
        script_path = Path(self._path_to_windows(str(rdc_dem.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"RDC DEM script not found: {script_path}")

        reference_date = str(rdc_dem.get("reference_date") or "").strip()
        rlks = self._bounded_int(rdc_dem.get("rlks") or rlks, default=8, minimum=1, maximum=64)
        timeout_seconds = self._bounded_int(timeout_seconds, default=43200, minimum=60, maximum=172800)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))

        rdc_dem["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["rdc_dem"] = rdc_dem
        manifest["status"] = "RDC_DEM_RUNNING"
        manifest["next_stage"] = "rdc_dem"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_rdc_dem(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_rdc_dem_summary(
                run_dir,
                reference_date=reference_date,
                rlks=rlks,
                dem_source=rdc_dem.get("dem_source") or {},
            )
            execution = {
                **rdc_dem.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            rdc_dem = {**rdc_dem, "execution": execution, "summary": summary}
            manifest["rdc_dem"] = rdc_dem
            manifest["status"] = "RDC_DEM_FAILED"
            manifest["next_stage"] = "fix_rdc_dem"
            self._write_json(run_dir / "rdc_dem_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_rdc_dem(run_dir, manifest)
            raise

        summary = self._build_rdc_dem_summary(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            dem_source=rdc_dem.get("dem_source") or {},
        )
        execution = {
            **rdc_dem.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        rdc_dem = {**rdc_dem, "execution": execution, "summary": summary}
        manifest["rdc_dem"] = rdc_dem
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "RDC_DEM_READY"
            manifest["next_stage"] = "interferograms"
        else:
            manifest["status"] = "RDC_DEM_FAILED"
            manifest["next_stage"] = "fix_rdc_dem"

        self._write_json(run_dir / "rdc_dem_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_rdc_dem(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_interferograms(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
        azlks: int = 8,
        unwrap_threshold: float = 0.20,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("Interferogram execution is submitted through the background job endpoint.")

        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "INTERFEROGRAMS_READY":
            return self.get_run_detail(run_id)
        if status not in {
            "COREGISTRATION_READY",
            "RDC_DEM_READY",
            "INTERFEROGRAMS_SCRIPT_READY",
            "INTERFEROGRAMS_FAILED",
        }:
            raise ValueError(f"run status does not allow interferogram preparation: {manifest.get('status')}")

        rdc_dem_summary = ((manifest.get("rdc_dem") or {}).get("summary")) or self._read_optional_json(run_dir / "rdc_dem_summary.json") or {}
        if not rdc_dem_summary.get("ready"):
            raise ValueError("RDC DEM summary is not ready; run RDC DEM generation first")
        coreg_summary = ((manifest.get("coregistration") or {}).get("summary")) or self._read_optional_json(run_dir / "coregistration_summary.json") or {}
        if not coreg_summary.get("ready"):
            raise ValueError("coregistration summary is not ready; run common-reference coregistration first")

        reference_date = str(
            (manifest.get("rdc_dem") or {}).get("reference_date")
            or rdc_dem_summary.get("reference_date")
            or ((manifest.get("coregistration") or {}).get("reference_date"))
            or ((manifest.get("stack") or {}).get("reference_date"))
            or ""
        ).strip()
        if not reference_date:
            raise ValueError("interferogram stage requires a reference date")

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(azlks, default=8, minimum=1, maximum=64)
        unwrap_threshold = self._bounded_float(unwrap_threshold, default=0.20, minimum=0.01, maximum=0.95)
        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        approved_itab = common_dir / "itab_approved"
        if not approved_itab.is_file():
            approved_itab = run_dir / "work" / "gamma" / "diff" / "itab_approved"
        if not approved_itab.is_file():
            raise FileNotFoundError(f"approved itab not found: {approved_itab}")

        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        dates = self._stack_dates(stack_manifest)
        pair_plan = self._build_interferogram_pair_plan(
            run_dir,
            reference_date=reference_date,
            approved_itab=approved_itab,
            dates=dates,
            rlks=rlks,
        )
        if not pair_plan:
            raise ValueError("approved itab produced no interferogram pairs")

        script_path = self._write_interferogram_script(
            run_dir,
            reference_date=reference_date,
            pair_plan=pair_plan,
            rlks=rlks,
            azlks=azlks,
            unwrap_threshold=unwrap_threshold,
        )
        interferograms = {
            "schema": "insar.gamma-interferograms-stage/v1",
            "strategy": "approved_itab_common_reference_diff_unwrap",
            "script_path": str(script_path),
            "reference_date": reference_date,
            "rlks": rlks,
            "azlks": azlks,
            "unwrap_threshold": unwrap_threshold,
            "approved_itab_path": str(approved_itab),
            "pair_count": len(pair_plan),
            "pairs": pair_plan,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "outputs": {
                "diff_dir": str(common_dir / "diff"),
                "diff_tab": str(common_dir / "DIFF_tab"),
                "itab_common_ref": str(common_dir / "itab_common_ref"),
            },
        }
        manifest["interferograms"] = interferograms
        manifest["status"] = "INTERFEROGRAMS_SCRIPT_READY"
        manifest["next_stage"] = "execute_interferograms"
        self._write_json(run_dir / "interferogram_plan.json", interferograms)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_interferograms(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_interferograms(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        azlks: int = 8,
        unwrap_threshold: float = 0.20,
        timeout_seconds: int = 43200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "INTERFEROGRAMS_READY":
            return self.get_run_detail(run_id)
        if status in {"COREGISTRATION_READY", "RDC_DEM_READY", "INTERFEROGRAMS_FAILED"}:
            self.prepare_interferograms(
                run_id,
                execute=False,
                rlks=rlks,
                azlks=azlks,
                unwrap_threshold=unwrap_threshold,
            )
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"INTERFEROGRAMS_SCRIPT_READY", "INTERFEROGRAMS_RUNNING"}:
            raise ValueError(f"run status does not allow interferogram execution: {manifest.get('status')}")

        interferograms = dict(manifest.get("interferograms") or {})
        script_path = Path(self._path_to_windows(str(interferograms.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"interferogram script not found: {script_path}")

        reference_date = str(interferograms.get("reference_date") or "").strip()
        pair_plan = list(interferograms.get("pairs") or [])
        rlks = self._bounded_int(interferograms.get("rlks") or rlks, default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(interferograms.get("azlks") or azlks, default=8, minimum=1, maximum=64)
        timeout_seconds = self._bounded_int(timeout_seconds, default=43200, minimum=60, maximum=172800)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))

        interferograms["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["interferograms"] = interferograms
        manifest["status"] = "INTERFEROGRAMS_RUNNING"
        manifest["next_stage"] = "interferograms"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_interferograms(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_interferogram_summary(
                run_dir,
                reference_date=reference_date,
                pair_plan=pair_plan,
                rlks=rlks,
            )
            execution = {
                **interferograms.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            interferograms = {**interferograms, "execution": execution, "summary": summary}
            manifest["interferograms"] = interferograms
            manifest["status"] = "INTERFEROGRAMS_FAILED"
            manifest["next_stage"] = "fix_interferograms"
            self._write_json(run_dir / "interferogram_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_interferograms(run_dir, manifest)
            raise

        summary = self._build_interferogram_summary(
            run_dir,
            reference_date=reference_date,
            pair_plan=pair_plan,
            rlks=rlks,
        )
        execution = {
            **interferograms.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        interferograms = {**interferograms, "execution": execution, "summary": summary}
        manifest["interferograms"] = interferograms
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "INTERFEROGRAMS_READY"
            manifest["next_stage"] = "detrend_atm"
        else:
            manifest["status"] = "INTERFEROGRAMS_FAILED"
            manifest["next_stage"] = "fix_interferograms"

        self._write_json(run_dir / "interferogram_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_interferograms(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_detrend_atm(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
        reference_window: int = 16,
        coherence_min: float = 0.15,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        if execute:
            return self.execute_detrend_atm(
                run_id,
                rlks=rlks,
                reference_window=reference_window,
                coherence_min=coherence_min,
            )

        status = str(manifest.get("status") or "").strip()
        if status == "DETREND_ATM_READY":
            return self.get_run_detail(run_id)
        if status not in {"INTERFEROGRAMS_READY", "DETREND_ATM_SCRIPT_READY", "DETREND_ATM_FAILED"}:
            raise ValueError(f"run status does not allow detrend/atm preparation: {manifest.get('status')}")

        interferogram_summary = (
            ((manifest.get("interferograms") or {}).get("summary"))
            or self._read_optional_json(run_dir / "interferogram_summary.json")
            or {}
        )
        if not interferogram_summary.get("ready"):
            raise ValueError("interferogram summary is not ready; run differential interferograms first")

        reference_date = str(
            (manifest.get("interferograms") or {}).get("reference_date")
            or interferogram_summary.get("reference_date")
            or ((manifest.get("stack") or {}).get("reference_date"))
            or ""
        ).strip()
        if not reference_date:
            raise ValueError("detrend/atm stage requires a reference date")

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        reference_window = self._bounded_int(reference_window, default=16, minimum=1, maximum=256)
        coherence_min = self._bounded_float(coherence_min, default=0.15, minimum=0.0, maximum=1.0)
        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        diff_tab = common_dir / "DIFF_tab"
        itab = common_dir / "itab_common_ref"
        rmli_path, rmli_par_path = self._find_reference_rmli_paths(run_dir, reference_date)
        hgt_path = run_dir / "work" / "gamma" / "dem" / f"{reference_date}_{rlks}rlks.rdc.dem"
        if not hgt_path.is_file():
            hgt_path = run_dir / "work" / "gamma" / "dem" / f"{reference_date}_{rlks}rlks.hgt"
        for label, path in {
            "DIFF_tab": diff_tab,
            "itab_common_ref": itab,
            "reference_mli": rmli_path,
            "reference_mli_par": rmli_par_path,
            "rdc_dem_height": hgt_path,
        }.items():
            if not path.is_file() or path.stat().st_size <= 0:
                raise FileNotFoundError(f"{label} is missing or empty: {path}")

        pair_plan = self._detrend_pair_plan_from_diff_tab(diff_tab, rlks=rlks)
        if not pair_plan:
            raise ValueError("DIFF_tab produced no detrend/atm pair plan")
        reference_region = self._select_ipta_reference_region(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            reference_window=reference_window,
            geom_ref_mli_par=rmli_par_path,
        )
        script_path = self._write_detrend_atm_script(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            reference_window=reference_window,
            reference_region=reference_region,
            coherence_min=coherence_min,
            diff_tab=diff_tab,
            itab=itab,
            rmli_path=rmli_path,
            rmli_par_path=rmli_par_path,
            hgt_path=hgt_path,
            pair_plan=pair_plan,
        )
        detrend_atm = {
            "schema": "insar.gamma-detrend-atm-stage/v1",
            "strategy": "expert_quad_fit_quad_sub_atm_mod_2d_sub_phase",
            "script_path": str(script_path),
            "reference_date": reference_date,
            "rlks": rlks,
            "reference_window": reference_window,
            "reference_region": reference_region,
            "coherence_min": coherence_min,
            "pair_count": len(pair_plan),
            "pairs": pair_plan,
            "inputs": {
                "diff_tab": str(diff_tab),
                "itab": str(itab),
                "reference_mli": str(rmli_path),
                "reference_mli_par": str(rmli_par_path),
                "hgt": str(hgt_path),
            },
            "outputs": {
                "detrend_dir": str(common_dir / "detrend_atm"),
                "diff_atmsub_tab": str(common_dir / "DIFF_atmsub_tab"),
                "itab_atmsub": str(common_dir / "itab_atmsub"),
            },
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        manifest["detrend_atm"] = detrend_atm
        manifest["status"] = "DETREND_ATM_SCRIPT_READY"
        manifest["next_stage"] = "execute_detrend_atm"
        self._write_json(run_dir / "detrend_atm_plan.json", detrend_atm)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_detrend_atm(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_detrend_atm(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        reference_window: int = 16,
        coherence_min: float = 0.15,
        timeout_seconds: int = 43200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "DETREND_ATM_READY":
            return self.get_run_detail(run_id)
        if status in {"INTERFEROGRAMS_READY", "DETREND_ATM_FAILED"}:
            self.prepare_detrend_atm(
                run_id,
                execute=False,
                rlks=rlks,
                reference_window=reference_window,
                coherence_min=coherence_min,
            )
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"DETREND_ATM_SCRIPT_READY", "DETREND_ATM_RUNNING"}:
            raise ValueError(f"run status does not allow detrend/atm execution: {manifest.get('status')}")

        detrend_atm = dict(manifest.get("detrend_atm") or {})
        script_path = Path(self._path_to_windows(str(detrend_atm.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"detrend/atm script not found: {script_path}")

        reference_date = str(detrend_atm.get("reference_date") or "").strip()
        pair_plan = list(detrend_atm.get("pairs") or [])
        rlks = self._bounded_int(detrend_atm.get("rlks") or rlks, default=8, minimum=1, maximum=64)
        timeout_seconds = self._bounded_int(timeout_seconds, default=43200, minimum=60, maximum=172800)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))

        detrend_atm["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["detrend_atm"] = detrend_atm
        manifest["status"] = "DETREND_ATM_RUNNING"
        manifest["next_stage"] = "detrend_atm"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_detrend_atm(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_detrend_atm_summary(
                run_dir,
                reference_date=reference_date,
                pair_plan=pair_plan,
                rlks=rlks,
                inputs=detrend_atm.get("inputs") or {},
            )
            execution = {
                **detrend_atm.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            detrend_atm = {**detrend_atm, "execution": execution, "summary": summary}
            manifest["detrend_atm"] = detrend_atm
            manifest["status"] = "DETREND_ATM_FAILED"
            manifest["next_stage"] = "fix_detrend_atm"
            self._write_json(run_dir / "detrend_atm_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_detrend_atm(run_dir, manifest)
            raise

        summary = self._build_detrend_atm_summary(
            run_dir,
            reference_date=reference_date,
            pair_plan=pair_plan,
            rlks=rlks,
            inputs=detrend_atm.get("inputs") or {},
        )
        execution = {
            **detrend_atm.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        detrend_atm = {**detrend_atm, "execution": execution, "summary": summary}
        manifest["detrend_atm"] = detrend_atm
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "DETREND_ATM_READY"
            manifest["next_stage"] = "ipta_timeseries"
        else:
            manifest["status"] = "DETREND_ATM_FAILED"
            manifest["next_stage"] = "fix_detrend_atm"

        self._write_json(run_dir / "detrend_atm_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_detrend_atm(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_ipta_timeseries(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
        reference_window: int = 16,
        mb_mode: int = DEFAULT_IPTA_MB_MODE,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("IPTA time-series execution is submitted through the background job endpoint.")

        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        status = str(manifest.get("status") or "").strip()
        if status == "IPTA_TIMESERIES_READY":
            return self.get_run_detail(run_id)
        if status not in {"DETREND_ATM_READY", "IPTA_TIMESERIES_SCRIPT_READY", "IPTA_TIMESERIES_FAILED"}:
            raise ValueError(f"run status does not allow IPTA time-series preparation: {manifest.get('status')}")

        detrend_summary = (
            ((manifest.get("detrend_atm") or {}).get("summary"))
            or self._read_optional_json(run_dir / "detrend_atm_summary.json")
            or {}
        )
        if not detrend_summary.get("ready"):
            raise ValueError("detrend/atm summary is not ready; run expert section 10 first")

        reference_date = str(
            (manifest.get("detrend_atm") or {}).get("reference_date")
            or detrend_summary.get("reference_date")
            or ((manifest.get("stack") or {}).get("reference_date"))
            or ""
        ).strip()
        if not reference_date:
            raise ValueError("IPTA time-series stage requires a reference date")

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        reference_window = self._bounded_int(reference_window, default=16, minimum=1, maximum=256)
        mb_mode = self._normalize_ipta_mb_mode(mb_mode)
        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        diff_tab = common_dir / "DIFF_atmsub_tab"
        rmli_tab = common_dir / "RMLI_tab"
        itab = common_dir / "itab_atmsub"
        for label, path in {"DIFF_atmsub_tab": diff_tab, "RMLI_tab": rmli_tab, "itab_atmsub": itab}.items():
            if not path.is_file() or path.stat().st_size <= 0:
                raise FileNotFoundError(f"{label} is missing or empty: {path}")

        geom_ref_mli, geom_ref_mli_par = self._find_reference_rmli_paths(run_dir, reference_date)
        if not geom_ref_mli_par.is_file():
            raise FileNotFoundError(f"reference MLI parameter file is missing: {geom_ref_mli_par}")
        mb_ref_mli, mb_ref_mli_par = self._select_ipta_mb_reference_mli(
            run_dir,
            reference_date=reference_date,
            rmli_tab=rmli_tab,
        )
        if not mb_ref_mli_par.is_file():
            raise FileNotFoundError(f"IPTA mb reference MLI parameter file is missing: {mb_ref_mli_par}")

        reference_region = self._select_ipta_reference_region(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            reference_window=reference_window,
            geom_ref_mli_par=geom_ref_mli_par,
        )
        script_path = self._write_ipta_timeseries_script(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            reference_window=reference_window,
            diff_tab=diff_tab,
            rmli_tab=rmli_tab,
            itab=itab,
            geom_ref_mli_par=geom_ref_mli_par,
            mb_ref_mli_par=mb_ref_mli_par,
            reference_region=reference_region,
            mb_mode=mb_mode,
        )
        timeseries_dir = common_dir / "timeseries"
        ipta_timeseries = {
            "schema": "insar.gamma-ipta-timeseries-stage/v1",
            "strategy": "gamma_mb_ts_rate_atmsub_expert_section_10",
            "script_path": str(script_path),
            "reference_date": reference_date,
            "rlks": rlks,
            "reference_window": reference_window,
            "reference_region": reference_region,
            "mb_mode": mb_mode,
            "mb_mode_description": IPTA_MB_MODE_DESCRIPTIONS[mb_mode],
            "inputs": {
                "diff_tab": str(diff_tab),
                "diff_tab_source": "detrend_atm",
                "rmli_tab": str(rmli_tab),
                "itab": str(itab),
                "geometry_reference_mli": str(geom_ref_mli),
                "geometry_reference_mli_par": str(geom_ref_mli_par),
                "mb_reference_mli": str(mb_ref_mli),
                "mb_reference_mli_par": str(mb_ref_mli_par),
            },
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "outputs": {
                "timeseries_dir": str(timeseries_dir),
                "diff_ts_tab": str(timeseries_dir / "diff_ts.tab"),
                "itab_ts": str(timeseries_dir / "itab_ts"),
                "sigma_ts": str(timeseries_dir / "sigma_ts"),
                "hgt_correction": str(timeseries_dir / "hgt_correction"),
                "ts_rate": str(timeseries_dir / "ts_rate"),
                "ts_const": str(timeseries_dir / "ts_const"),
                "sigma_rate": str(timeseries_dir / "sigma_rate"),
            },
        }
        manifest["ipta_timeseries"] = ipta_timeseries
        manifest["status"] = "IPTA_TIMESERIES_SCRIPT_READY"
        manifest["next_stage"] = "execute_ipta_timeseries"
        self._write_json(run_dir / "ipta_timeseries_plan.json", ipta_timeseries)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_ipta_timeseries(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_ipta_timeseries(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        reference_window: int = 16,
        mb_mode: int = DEFAULT_IPTA_MB_MODE,
        timeout_seconds: int = 43200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        status = str(manifest.get("status") or "").strip()
        if status == "IPTA_TIMESERIES_READY":
            return self.get_run_detail(run_id)
        if status in {"DETREND_ATM_READY", "IPTA_TIMESERIES_FAILED"}:
            self.prepare_ipta_timeseries(
                run_id,
                execute=False,
                rlks=rlks,
                reference_window=reference_window,
                mb_mode=mb_mode,
            )
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"IPTA_TIMESERIES_SCRIPT_READY", "IPTA_TIMESERIES_RUNNING"}:
            raise ValueError(f"run status does not allow IPTA time-series execution: {manifest.get('status')}")

        ipta_timeseries = dict(manifest.get("ipta_timeseries") or {})
        if status == "IPTA_TIMESERIES_SCRIPT_READY" and ipta_timeseries.get("mb_mode") is None:
            self.prepare_ipta_timeseries(
                run_id,
                execute=False,
                rlks=rlks,
                reference_window=reference_window,
                mb_mode=mb_mode,
            )
            manifest = self._read_json(manifest_path)
            ipta_timeseries = dict(manifest.get("ipta_timeseries") or {})
        script_path = Path(self._path_to_windows(str(ipta_timeseries.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"IPTA time-series script not found: {script_path}")

        reference_date = str(ipta_timeseries.get("reference_date") or "").strip()
        rlks = self._bounded_int(ipta_timeseries.get("rlks") or rlks, default=8, minimum=1, maximum=64)
        mb_mode = self._normalize_ipta_mb_mode(ipta_timeseries.get("mb_mode", mb_mode))
        timeout_seconds = self._bounded_int(timeout_seconds, default=43200, minimum=60, maximum=172800)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))

        ipta_timeseries["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["ipta_timeseries"] = ipta_timeseries
        manifest["status"] = "IPTA_TIMESERIES_RUNNING"
        manifest["next_stage"] = "ipta_timeseries"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_ipta_timeseries(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_ipta_timeseries_summary(
                run_dir,
                reference_date=reference_date,
                rlks=rlks,
                inputs=ipta_timeseries.get("inputs") or {},
                reference_region=ipta_timeseries.get("reference_region") or {},
                mb_mode=mb_mode,
            )
            execution = {
                **ipta_timeseries.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            ipta_timeseries = {**ipta_timeseries, "execution": execution, "summary": summary}
            manifest["ipta_timeseries"] = ipta_timeseries
            manifest["status"] = "IPTA_TIMESERIES_FAILED"
            manifest["next_stage"] = "fix_ipta_timeseries"
            self._write_json(run_dir / "ipta_timeseries_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_ipta_timeseries(run_dir, manifest)
            raise

        summary = self._build_ipta_timeseries_summary(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            inputs=ipta_timeseries.get("inputs") or {},
            reference_region=ipta_timeseries.get("reference_region") or {},
            mb_mode=mb_mode,
        )
        execution = {
            **ipta_timeseries.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        ipta_timeseries = {**ipta_timeseries, "execution": execution, "summary": summary}
        manifest["ipta_timeseries"] = ipta_timeseries
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "IPTA_TIMESERIES_READY"
            manifest["next_stage"] = "publish_products"
        else:
            manifest["status"] = "IPTA_TIMESERIES_FAILED"
            manifest["next_stage"] = "fix_ipta_timeseries"

        self._write_json(run_dir / "ipta_timeseries_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_ipta_timeseries(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_publish_products(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("publish product execution is submitted through the workflow/background job path.")

        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        recovered = self._recover_workflow_resume_status(dict(manifest))
        if recovered.get("status") != manifest.get("status"):
            manifest = recovered
            self._write_json(manifest_path, manifest)
        status = str(manifest.get("status") or "").strip()
        if status in {"PRODUCTS_READY", "MONITOR_POINTS_SCRIPT_READY", "MONITOR_POINTS_RUNNING", "MONITOR_POINTS_READY"}:
            return self.get_run_detail(run_id)
        if status not in {"IPTA_TIMESERIES_READY", "PUBLISH_PRODUCTS_SCRIPT_READY", "PUBLISH_PRODUCTS_FAILED"}:
            raise ValueError(f"run status does not allow product publishing preparation: {manifest.get('status')}")

        ipta_summary = (
            ((manifest.get("ipta_timeseries") or {}).get("summary"))
            or self._read_optional_json(run_dir / "ipta_timeseries_summary.json")
            or {}
        )
        if not ipta_summary.get("ready"):
            raise ValueError("IPTA time-series summary is not ready; run IPTA inversion first")

        reference_date = str(
            (manifest.get("ipta_timeseries") or {}).get("reference_date")
            or ipta_summary.get("reference_date")
            or ((manifest.get("stack") or {}).get("reference_date"))
            or ""
        ).strip()
        if not reference_date:
            raise ValueError("publish products stage requires a reference date")

        rlks = self._bounded_int(rlks, default=settings.GAMMA_SBAS_DEFAULT_RLKS or 8, minimum=1, maximum=64)
        rmli_path, rmli_par_path = self._find_reference_rmli_paths(run_dir, reference_date)
        slc_par_path = run_dir / "work" / "gamma" / "slc" / f"{reference_date}.slc.par"
        if not slc_par_path.is_file():
            slc_par_path = rmli_par_path
        dem_par_path = run_dir / "work" / "gamma" / "dem" / f"{reference_date}_{rlks}rlks.utm.dem.par"
        lookup_path = run_dir / "work" / "gamma" / "dem" / f"{reference_date}_{rlks}rlks.UTM_TO_RDC"
        timeseries_dir = run_dir / "work" / "gamma" / f"common_{reference_date}" / "timeseries"
        for label, path in {
            "reference_mli": rmli_path,
            "reference_mli_par": rmli_par_path,
            "slc_par": slc_par_path,
            "utm_dem_par": dem_par_path,
            "lookup_table": lookup_path,
            "ts_rate": timeseries_dir / "ts_rate",
            "sigma_rate": timeseries_dir / "sigma_rate",
        }.items():
            if not path.is_file() or path.stat().st_size <= 0:
                raise FileNotFoundError(f"{label} is missing or empty: {path}")

        wavelength = self._resolve_radar_wavelength_m(slc_par_path, rmli_par_path)
        script_path = self._write_publish_products_script(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            timeseries_dir=timeseries_dir,
            rmli_path=rmli_path,
            rmli_par_path=rmli_par_path,
            slc_par_path=slc_par_path,
            dem_par_path=dem_par_path,
            lookup_path=lookup_path,
            wavelength=wavelength,
        )
        export_dir = run_dir / "publish" / "geotiff"
        publish_products = {
            "schema": "insar.gamma-sbas-publish-products-stage/v1",
            "strategy": "gamma_geocode_back_data2geotiff_los_sign_conversion",
            "script_path": str(script_path),
            "reference_date": reference_date,
            "rlks": rlks,
            "wavelength_m": wavelength,
            "los_sign_convention": {
                "default": "los_rate_toward_m_per_year",
                "toward_positive": "positive means motion toward radar",
                "away_positive": "positive means motion away from radar",
                "formulas": {
                    "away_m_per_year": "phase_rate_rad_per_year * wavelength / (4*pi)",
                    "toward_m_per_year": "-phase_rate_rad_per_year * wavelength / (4*pi)",
                    "away_mm_per_year": "phase_rate_rad_per_year * wavelength / (4*pi) * 1000",
                    "toward_mm_per_year": "-phase_rate_rad_per_year * wavelength / (4*pi) * 1000",
                },
            },
            "expert_color_conventions": {
                "velocity": "hls.cm with -0.08 to 0.08 m/year as in the expert document",
                "sigma": "cc.cm; production uses 0.0 to 0.06 m/year for LOS sigma-rate browse products",
                "phase_and_atmosphere": "rmg.cm with -6.28 to 6.28 radians for detrend/atmosphere browse products",
            },
            "inputs": {
                "timeseries_dir": str(timeseries_dir),
                "ts_rate": str(timeseries_dir / "ts_rate"),
                "sigma_rate": str(timeseries_dir / "sigma_rate"),
                "sigma_ts": str(timeseries_dir / "sigma_ts"),
                "hgt_correction": str(timeseries_dir / "hgt_correction"),
                "reference_mli": str(rmli_path),
                "reference_mli_par": str(rmli_par_path),
                "slc_par": str(slc_par_path),
                "utm_dem_par": str(dem_par_path),
                "lookup_table": str(lookup_path),
            },
            "outputs": {
                "export_dir": str(export_dir),
                "vector_dir": str(run_dir / "publish" / "vectors"),
                "point_vector_geojson_gz": str(run_dir / "publish" / "vectors" / "los_rate_points.geojson.gz"),
                "point_vector_summary": str(run_dir / "publish" / "vectors" / "los_rate_points_summary.json"),
                "product_summary": str(run_dir / "product_summary.json"),
                "quality_summary": str(run_dir / "quality_summary.json"),
            },
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        manifest["publish_products"] = publish_products
        manifest["status"] = "PUBLISH_PRODUCTS_SCRIPT_READY"
        manifest["next_stage"] = "execute_publish_products"
        self._write_json(run_dir / "publish_product_plan.json", publish_products)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_publish_products(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_publish_products(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        timeout_seconds: int = 7200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        recovered = self._recover_workflow_resume_status(dict(manifest))
        if recovered.get("status") != manifest.get("status"):
            manifest = recovered
            self._write_json(manifest_path, manifest)
        status = str(manifest.get("status") or "").strip()
        if status in {"PRODUCTS_READY", "MONITOR_POINTS_SCRIPT_READY", "MONITOR_POINTS_RUNNING", "MONITOR_POINTS_READY"}:
            return self.get_run_detail(run_id)
        if status in {"IPTA_TIMESERIES_READY", "PUBLISH_PRODUCTS_FAILED"}:
            self.prepare_publish_products(run_id, execute=False, rlks=rlks)
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"PUBLISH_PRODUCTS_SCRIPT_READY", "PUBLISH_PRODUCTS_RUNNING"}:
            raise ValueError(f"run status does not allow product publishing execution: {manifest.get('status')}")

        publish_products = dict(manifest.get("publish_products") or {})
        script_path = Path(self._path_to_windows(str(publish_products.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"publish products script not found: {script_path}")

        reference_date = str(publish_products.get("reference_date") or "").strip()
        rlks = self._bounded_int(publish_products.get("rlks") or rlks, default=8, minimum=1, maximum=64)
        timeout_seconds = self._bounded_int(timeout_seconds, default=7200, minimum=60, maximum=86400)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))
        publish_products["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["publish_products"] = publish_products
        manifest["status"] = "PUBLISH_PRODUCTS_RUNNING"
        manifest["next_stage"] = "publish_products"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_publish_products(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_publish_products_summary(
                run_dir,
                reference_date=reference_date,
                rlks=rlks,
                inputs=publish_products.get("inputs") or {},
                wavelength=publish_products.get("wavelength_m"),
            )
            execution = {
                **publish_products.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            publish_products = {**publish_products, "execution": execution, "summary": summary}
            manifest["publish_products"] = publish_products
            manifest["status"] = "PUBLISH_PRODUCTS_FAILED"
            manifest["next_stage"] = "fix_publish_products"
            self._write_json(run_dir / "publish_product_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_publish_products(run_dir, manifest)
            raise

        summary = self._build_publish_products_summary(
            run_dir,
            reference_date=reference_date,
            rlks=rlks,
            inputs=publish_products.get("inputs") or {},
            wavelength=publish_products.get("wavelength_m"),
        )
        execution = {
            **publish_products.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        publish_products = {**publish_products, "execution": execution, "summary": summary}
        manifest["publish_products"] = publish_products
        manifest["publish_artifacts"] = self._build_run_artifacts(run_dir)
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "PRODUCTS_READY"
            manifest["next_stage"] = "monitor_points"
        else:
            manifest["status"] = "PUBLISH_PRODUCTS_FAILED"
            manifest["next_stage"] = "fix_publish_products"

        self._write_json(run_dir / "publish_product_summary.json", summary)
        self._write_json(run_dir / "product_summary.json", summary.get("product_summary") or summary)
        self._write_json(run_dir / "quality_summary.json", summary.get("quality_summary") or {})
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_publish_products(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_monitor_points(
        self,
        run_id: str,
        *,
        execute: bool = False,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("monitor point execution is submitted through the workflow/background job path.")

        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(manifest)
        recovered = self._recover_workflow_resume_status(dict(manifest))
        if recovered.get("status") != manifest.get("status"):
            manifest = recovered
            self._write_json(manifest_path, manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "MONITOR_POINTS_READY":
            return self.get_run_detail(run_id)
        if status not in {"PRODUCTS_READY", "MONITOR_POINTS_SCRIPT_READY", "MONITOR_POINTS_FAILED"}:
            raise ValueError(f"run status does not allow monitor point preparation: {manifest.get('status')}")

        publish_summary = (
            ((manifest.get("publish_products") or {}).get("summary"))
            or self._read_optional_json(run_dir / "publish_product_summary.json")
            or {}
        )
        if not publish_summary.get("ready"):
            raise ValueError("published LOS products are not ready; run publish products first")

        reference_date = str(
            (manifest.get("publish_products") or {}).get("reference_date")
            or publish_summary.get("reference_date")
            or ((manifest.get("stack") or {}).get("reference_date"))
            or ""
        ).strip()
        rlks = self._bounded_int(
            (manifest.get("publish_products") or {}).get("rlks") or settings.GAMMA_SBAS_DEFAULT_RLKS,
            default=8,
            minimum=1,
            maximum=64,
        )
        rmli_path, rmli_par_path = self._find_reference_rmli_paths(run_dir, reference_date)
        slc_par_path = Path(self._path_to_windows(str(((manifest.get("publish_products") or {}).get("inputs") or {}).get("slc_par") or "")) or "")
        if not slc_par_path.is_file():
            slc_par_path = run_dir / "work" / "gamma" / "slc" / f"{reference_date}.slc.par"
        if not slc_par_path.is_file():
            slc_par_path = rmli_par_path
        dem_par_path = run_dir / "work" / "gamma" / "dem" / f"{reference_date}_{rlks}rlks.utm.dem.par"
        lookup_path = run_dir / "work" / "gamma" / "dem" / f"{reference_date}_{rlks}rlks.UTM_TO_RDC"
        timeseries_dir = run_dir / "work" / "gamma" / f"common_{reference_date}" / "timeseries"
        export_dir = run_dir / "publish" / "geotiff"
        point_dir = run_dir / "publish" / "monitor_points"
        for label, path in {
            "reference_mli_par": rmli_par_path,
            "slc_par": slc_par_path,
            "dem_par": dem_par_path,
            "lookup": lookup_path,
            "los_rate_toward_rdc": export_dir / "los_rate_toward_mm_per_year.rdc",
            "los_sigma_rdc": export_dir / "los_sigma_mm_per_year.rdc",
            "diff_ts_tab": timeseries_dir / "diff_ts.tab",
        }.items():
            if not path.is_file() or path.stat().st_size <= 0:
                raise FileNotFoundError(f"{label} is missing or empty: {path}")

        stack_manifest = self._read_optional_json(run_dir / "stack_manifest.json") or {}
        dates = self._stack_dates(stack_manifest)
        script_path = self._write_monitor_points_script(
            run_dir,
            reference_date=reference_date,
            dates=dates,
            timeseries_dir=timeseries_dir,
            export_dir=export_dir,
            point_dir=point_dir,
            rmli_par_path=rmli_par_path,
            slc_par_path=slc_par_path,
            dem_par_path=dem_par_path,
            lookup_path=lookup_path,
        )
        monitor_points = {
            "schema": "insar.gamma-sbas-monitor-points-stage/v1",
            "strategy": "sample_or_configured_points_from_gamma_diff_ts",
            "script_path": str(script_path),
            "reference_date": reference_date,
            "dates": dates,
            "inputs": {
                "timeseries_dir": str(timeseries_dir),
                "export_dir": str(export_dir),
                "monitor_config": str(run_dir / "monitor_points.json"),
                "reference_mli": str(rmli_path),
                "reference_mli_par": str(rmli_par_path),
                "slc_par": str(slc_par_path),
                "dem_par": str(dem_par_path),
                "lookup": str(lookup_path),
            },
            "outputs": {
                "point_dir": str(point_dir),
                "summary": str(run_dir / "monitor_points_summary.json"),
            },
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        manifest["monitor_point_products"] = monitor_points
        manifest["status"] = "MONITOR_POINTS_SCRIPT_READY"
        manifest["next_stage"] = "execute_monitor_points"
        self._write_json(run_dir / "monitor_points_plan.json", monitor_points)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_monitor_points(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_monitor_points(
        self,
        run_id: str,
        *,
        timeout_seconds: int = 1800,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        recovered = self._recover_workflow_resume_status(dict(manifest))
        if recovered.get("status") != manifest.get("status"):
            manifest = recovered
            self._write_json(manifest_path, manifest)
        status = str(manifest.get("status") or "").strip()
        if status == "MONITOR_POINTS_READY":
            return self.get_run_detail(run_id)
        if status in {"PRODUCTS_READY", "MONITOR_POINTS_FAILED"}:
            self.prepare_monitor_points(run_id, execute=False)
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"MONITOR_POINTS_SCRIPT_READY", "MONITOR_POINTS_RUNNING"}:
            raise ValueError(f"run status does not allow monitor point execution: {manifest.get('status')}")

        monitor_points = dict(manifest.get("monitor_point_products") or {})
        script_path = Path(self._path_to_windows(str(monitor_points.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"monitor point script not found: {script_path}")

        timeout_seconds = self._bounded_int(timeout_seconds, default=1800, minimum=60, maximum=86400)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))
        monitor_points["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["monitor_point_products"] = monitor_points
        manifest["status"] = "MONITOR_POINTS_RUNNING"
        manifest["next_stage"] = "monitor_points"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_monitor_points(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_monitor_points_summary(run_dir, monitor_points=monitor_points)
            execution = {
                **monitor_points.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            monitor_points = {**monitor_points, "execution": execution, "summary": summary}
            manifest["monitor_point_products"] = monitor_points
            manifest["status"] = "MONITOR_POINTS_FAILED"
            manifest["next_stage"] = "fix_monitor_points"
            self._write_json(run_dir / "monitor_points_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_monitor_points(run_dir, manifest)
            raise

        summary = self._build_monitor_points_summary(run_dir, monitor_points=monitor_points)
        execution = {
            **monitor_points.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        monitor_points = {**monitor_points, "execution": execution, "summary": summary}
        manifest["monitor_point_products"] = monitor_points
        manifest["publish_artifacts"] = self._build_run_artifacts(run_dir)
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "MONITOR_POINTS_READY"
            manifest["next_stage"] = "review_publish_products"
        else:
            manifest["status"] = "MONITOR_POINTS_FAILED"
            manifest["next_stage"] = "fix_monitor_points"

        self._write_json(run_dir / "monitor_points_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_monitor_points(run_dir, manifest)
        if manifest["status"] == "MONITOR_POINTS_READY":
            self.sync_product_package(run_id)
        return self.get_run_detail(run_id)

    def list_trial_runs(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if not self.trial_root.exists():
            return {"items": items, "count": 0, "trial_root": str(self.trial_root)}

        for summary_path in sorted(self.trial_root.glob("*/publish/trial_summary.json")):
            try:
                summary = self._read_json(summary_path)
                items.append(self._build_trial_card(summary_path.parent.parent, summary))
            except Exception as exc:
                items.append(
                    {
                        "trial_id": summary_path.parent.parent.name,
                        "status": "SUMMARY_UNREADABLE",
                        "summary_path": str(summary_path),
                        "error": str(exc),
                    }
                )

        items.sort(key=lambda item: str(item.get("generated_at") or ""), reverse=True)
        return {"items": items, "count": len(items), "trial_root": str(self.trial_root)}

    def get_trial_detail(self, trial_id: str) -> dict[str, Any]:
        trial_dir = self._resolve_trial_dir(trial_id)
        summary_path = trial_dir / "publish" / "trial_summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(f"trial summary not found: {summary_path}")

        summary = self._read_json(summary_path)
        artifacts = self._build_artifacts(trial_dir)
        return {
            "trial": self._build_trial_card(trial_dir, summary),
            "summary": summary,
            "artifacts": artifacts,
            "stage_contract": [
                "par_LT1_SLC",
                "ORB_filt_spline.py",
                "multi_look",
                "base_calc",
                "create_offset/init_offset_orbit/init_offset/offset_pwr/offset_fit/SLC_interp",
                "dem_import/fill_gaps/gc_map2/pixel_area/gc_map_fine",
                "mk_diff_2d",
                "mk_adf_2d",
                "mk_unw_2d",
                "quad_fit/quad_sub/atm_mod_2d/atm_sim_2d/sub_phase",
                "mb",
                "real_to_cpx",
                "unw_model",
                "ts_rate",
                "geocode_back",
                "data2geotiff",
                "dispmap",
                "disp_prt_2d",
                "monitoring point time series",
            ],
        }

    def resolve_artifact_path(self, trial_id: str, relative_path: str) -> Path:
        trial_dir = self._resolve_trial_dir(trial_id)
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("invalid artifact path")
        if not normalized.startswith("publish/"):
            raise ValueError("only published artifacts can be served")

        candidate = (trial_dir / normalized).resolve()
        trial_resolved = trial_dir.resolve()
        try:
            candidate.relative_to(trial_resolved)
        except ValueError as exc:
            raise ValueError("artifact path escapes trial root") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"artifact not found: {normalized}")
        return candidate

    def resolve_run_artifact_path(self, run_id: str, relative_path: str) -> Path:
        run_dir = self._resolve_run_dir(run_id)
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("invalid artifact path")
        allowed_paths = {item["relative_path"] for item in self._build_run_artifacts(run_dir)}
        if normalized not in allowed_paths:
            raise ValueError("run artifact is not published")

        candidate = (run_dir / normalized).resolve()
        run_resolved = run_dir.resolve()
        try:
            candidate.relative_to(run_resolved)
        except ValueError as exc:
            raise ValueError("artifact path escapes run root") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"artifact not found: {normalized}")
        return candidate

    def prepare_workflow(
        self,
        run_id: str,
        *,
        force: bool = False,
        rlks: int | None = None,
        azlks: int | None = None,
        mb_mode: int | None = None,
        reference_window: int | None = None,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        run_manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(run_manifest)
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        self._ensure_gamma_date_keyed_stack(stack_manifest)
        self._ensure_expert_workspace(run_dir)
        run_manifest = self._recover_workflow_resume_status(run_manifest)
        self._write_json(manifest_path, run_manifest)
        params = {
            "rlks": self._bounded_int(rlks or settings.GAMMA_SBAS_DEFAULT_RLKS, default=8, minimum=1, maximum=64),
            "azlks": self._bounded_int(azlks or settings.GAMMA_SBAS_DEFAULT_AZLKS, default=8, minimum=1, maximum=64),
            "mb_mode": self._normalize_ipta_mb_mode(mb_mode if mb_mode is not None else settings.GAMMA_SBAS_DEFAULT_MB_MODE),
            "reference_window": self._bounded_int(
                reference_window or settings.GAMMA_SBAS_DEFAULT_REFERENCE_WINDOW,
                default=16,
                minimum=1,
                maximum=256,
            ),
        }
        resume_stage_status = str(run_manifest.get("status") or "").strip()
        run_manifest["workflow"] = {
            **(run_manifest.get("workflow") or {}),
            "schema": "insar.gamma-sbas-workflow-binding/v1",
            "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
            "params": params,
            "force": bool(force),
            "prepared_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "manifest_path": str(run_dir / "manifest.json"),
            "state_path": str(run_dir / "state" / "step_status.json"),
            "resume_stage_status": resume_stage_status,
        }
        run_manifest["status"] = "WORKFLOW_READY"
        run_manifest["next_stage"] = "submit_workflow_job"

        workflow_manifest = self._build_workflow_manifest(run_dir, run_manifest, stack_manifest, params=params)
        self._write_json(run_dir / "manifest.json", workflow_manifest)
        state_path = run_dir / "state" / "step_status.json"
        if force or not state_path.is_file():
            self._write_json(state_path, self._initial_workflow_state(run_manifest, workflow_manifest))
        self._write_json(manifest_path, run_manifest)
        return self.get_run_detail(run_id)

    @classmethod
    def _recover_workflow_resume_status(cls, run_manifest: dict[str, Any]) -> dict[str, Any]:
        current_status = str(run_manifest.get("status") or "").strip()
        if current_status not in {"WORKFLOW_READY", "WORKFLOW_RUNNING", "WORKFLOW_FAILED", "WORKFLOW_PARTIAL"}:
            return run_manifest
        inferred_status = cls._infer_stage_status_from_manifest(run_manifest)
        if inferred_status:
            run_manifest["status"] = inferred_status
            run_manifest["next_stage"] = cls._next_stage_for_status(inferred_status)
            return run_manifest
        workflow = run_manifest.get("workflow") or {}
        candidates = [
            workflow.get("resume_stage_status"),
            workflow.get("previous_status"),
        ]
        for candidate in candidates:
            stage_status = str(candidate or "").strip()
            if stage_status and not stage_status.startswith("WORKFLOW_"):
                run_manifest["status"] = stage_status
                run_manifest["next_stage"] = cls._next_stage_for_status(stage_status)
                return run_manifest
        run_manifest["status"] = "PLANNED_GAMMA_BASELINE_AUDIT"
        run_manifest["next_stage"] = "baseline_audit"
        return run_manifest

    @staticmethod
    def _stage_execution_completed(stage: dict[str, Any] | None) -> bool:
        payload = stage or {}
        execution = payload.get("execution") or {}
        summary = payload.get("summary") or {}
        return (
            str(execution.get("status") or "").upper() == "COMPLETED"
            and int(execution.get("returncode") or 0) == 0
            and (summary.get("ready") is not False)
        )

    @classmethod
    def _infer_stage_status_from_manifest(cls, run_manifest: dict[str, Any]) -> str:
        if cls._stage_execution_completed(run_manifest.get("monitor_point_products")):
            return "MONITOR_POINTS_READY"
        if cls._stage_execution_completed(run_manifest.get("publish_products")):
            return "PRODUCTS_READY"
        if cls._stage_execution_completed(run_manifest.get("ipta_timeseries")):
            return "IPTA_TIMESERIES_READY"
        if cls._stage_execution_completed(run_manifest.get("detrend_atm")):
            return "DETREND_ATM_READY"
        if cls._stage_execution_completed(run_manifest.get("interferograms")):
            return "INTERFEROGRAMS_READY"
        if cls._stage_execution_completed(run_manifest.get("coregistration")):
            if cls._stage_execution_completed(run_manifest.get("rdc_dem")):
                return "RDC_DEM_READY"
            return "COREGISTRATION_READY"
        if cls._stage_execution_completed(run_manifest.get("rdc_dem")):
            return "RDC_DEM_READY"
        if (run_manifest.get("coregistration") or {}).get("script_path"):
            return "COREGISTRATION_SCRIPT_READY"
        if (run_manifest.get("baseline_audit") or {}).get("summary"):
            return "BASELINE_AUDIT_READY"
        if (run_manifest.get("baseline_audit") or {}).get("script_path"):
            return "BASELINE_AUDIT_SCRIPT_READY"
        return ""

    def _prepare_reusable_stage_scripts(
        self,
        run_id: str,
        run_dir: Path,
        run_manifest: dict[str, Any],
        params: dict[str, Any],
    ) -> None:
        status = str(run_manifest.get("status") or "").strip()
        if status in {"PLANNED_GAMMA_BASELINE_AUDIT", "WORKFLOW_READY", "BASELINE_AUDIT_FAILED", "BASELINE_AUDIT_READY"}:
            self.run_baseline_audit(
                run_id,
                execute=False,
                rlks=int(params.get("rlks") or 8),
                azlks=int(params.get("azlks") or 8),
                max_delta_n=1,
            )
            run_manifest = self._read_json(run_dir / "run_manifest.json")
            status = str(run_manifest.get("status") or "").strip()

        if status == "BASELINE_AUDIT_READY" and settings.GAMMA_SBAS_AUTO_APPROVE_ITAB:
            try:
                self.decide_itab(
                    run_id,
                    decision="approve",
                    reviewer="system",
                    note="Auto-approved for Gamma SBAS expert workflow after baseline audit summary was present.",
                )
                run_manifest = self._read_json(run_dir / "run_manifest.json")
                status = str(run_manifest.get("status") or "").strip()
            except Exception:
                pass

        if status in {
            "ITAB_APPROVED",
            "COREGISTRATION_FAILED",
            "COREGISTRATION_SCRIPT_READY",
            "RDC_DEM_SCRIPT_READY",
            "RDC_DEM_READY",
        }:
            try:
                self.prepare_coregistration(
                    run_id,
                    execute=False,
                    rlks=int(params.get("rlks") or 8),
                    azlks=int(params.get("azlks") or 8),
                )
                run_manifest = self._read_json(run_dir / "run_manifest.json")
                status = str(run_manifest.get("status") or "").strip()
            except Exception:
                pass

        if status in {
            "BASELINE_AUDIT_READY",
            "ITAB_APPROVED",
            "COREGISTRATION_SCRIPT_READY",
            "COREGISTRATION_READY",
            "RDC_DEM_FAILED",
            "RDC_DEM_SCRIPT_READY",
        }:
            try:
                self.prepare_rdc_dem(
                    run_id,
                    execute=False,
                    rlks=int(params.get("rlks") or 8),
                )
                run_manifest = self._read_json(run_dir / "run_manifest.json")
                status = str(run_manifest.get("status") or "").strip()
            except Exception:
                pass

        if status in {"RDC_DEM_READY", "INTERFEROGRAMS_FAILED", "INTERFEROGRAMS_SCRIPT_READY"}:
            try:
                self.prepare_interferograms(
                    run_id,
                    execute=False,
                    rlks=int(params.get("rlks") or 8),
                    azlks=int(params.get("azlks") or 8),
                    unwrap_threshold=0.20,
                )
                run_manifest = self._read_json(run_dir / "run_manifest.json")
                status = str(run_manifest.get("status") or "").strip()
            except Exception:
                pass

        if status in {"INTERFEROGRAMS_READY", "DETREND_ATM_FAILED", "DETREND_ATM_SCRIPT_READY"}:
            try:
                self.prepare_detrend_atm(
                    run_id,
                    execute=False,
                    rlks=int(params.get("rlks") or 8),
                    reference_window=int(params.get("reference_window") or 16),
                )
                run_manifest = self._read_json(run_dir / "run_manifest.json")
                status = str(run_manifest.get("status") or "").strip()
            except Exception:
                pass

        if status in {"DETREND_ATM_READY", "IPTA_TIMESERIES_FAILED", "IPTA_TIMESERIES_SCRIPT_READY"}:
            try:
                self.prepare_ipta_timeseries(
                    run_id,
                    execute=False,
                    rlks=int(params.get("rlks") or 8),
                    reference_window=int(params.get("reference_window") or 16),
                    mb_mode=int(params.get("mb_mode") or 0),
                )
            except Exception:
                pass
            run_manifest = self._read_json(run_dir / "run_manifest.json")
            status = str(run_manifest.get("status") or "").strip()

        if status in {"IPTA_TIMESERIES_READY", "PUBLISH_PRODUCTS_FAILED", "PUBLISH_PRODUCTS_SCRIPT_READY"}:
            try:
                self.prepare_publish_products(
                    run_id,
                    execute=False,
                    rlks=int(params.get("rlks") or 8),
                )
            except Exception:
                pass
            run_manifest = self._read_json(run_dir / "run_manifest.json")
            status = str(run_manifest.get("status") or "").strip()

        if status in {"PRODUCTS_READY", "MONITOR_POINTS_FAILED", "MONITOR_POINTS_SCRIPT_READY"}:
            try:
                self.prepare_monitor_points(run_id, execute=False)
            except Exception:
                pass

    def execute_workflow(
        self,
        run_id: str,
        *,
        from_step: str | None = None,
        to_step: str | None = None,
        only_steps: list[str] | None = None,
        force: bool = False,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        run_manifest = self._read_json(manifest_path)
        self._ensure_lt1_execution_enabled(run_manifest)
        if not (run_dir / "manifest.json").is_file():
            self.prepare_workflow(run_id, force=force)
            run_manifest = self._read_json(manifest_path)

        workflow_manifest = self._read_json(run_dir / "manifest.json")
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        previous_status = str(run_manifest.get("status") or "")
        run_manifest["status"] = "WORKFLOW_RUNNING"
        run_manifest["next_stage"] = "workflow"
        run_manifest["workflow"] = {
            **(run_manifest.get("workflow") or {}),
            "started_at": started_at,
            "previous_status": previous_status,
            "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
            "from_step": from_step,
            "to_step": to_step,
            "only_steps": only_steps or [],
            "force": bool(force),
        }
        self._write_json(manifest_path, run_manifest)

        execution_results = self._execute_expert_workflow_scripts(
            run_id,
            run_dir,
            workflow_manifest=workflow_manifest,
            from_step=from_step,
            to_step=to_step,
            only_steps=only_steps or [],
            force=force,
            timeout_seconds=timeout_seconds or settings.GAMMA_SBAS_STEP_TIMEOUT_SECONDS,
        )
        state = self._read_optional_json(run_dir / "state" / "step_status.json") or {}
        summary = self._summarize_workflow_state(workflow_manifest, state)
        returncode = 0 if summary.get("failed_count") == 0 else 1
        execution = {
            "started_at": started_at,
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "returncode": returncode,
            "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
            "distro": settings.GAMMA_SBAS_WSL_DISTRO,
            "mode": "expert_document_scripts",
            "results": execution_results,
            "summary": summary,
        }
        run_manifest = self._read_json(manifest_path)
        run_manifest["workflow"] = {
            **(run_manifest.get("workflow") or {}),
            "execution": execution,
            "summary": summary,
        }
        audit_summary = self._read_optional_json(run_dir / "expert_command_audit.json") or {}
        if returncode == 0 and summary.get("ready") and audit_summary.get("ready"):
            run_manifest["status"] = "WORKFLOW_COMPLETED"
            run_manifest["next_stage"] = "review_publish_products"
        elif returncode == 0:
            run_manifest["status"] = "WORKFLOW_PARTIAL"
            run_manifest["next_stage"] = "continue_workflow"
        else:
            run_manifest["status"] = "WORKFLOW_FAILED"
            run_manifest["next_stage"] = "fix_workflow"
        self._write_json(manifest_path, run_manifest)
        self._write_json(run_dir / "workflow_summary.json", summary)
        if run_manifest.get("status") == "WORKFLOW_COMPLETED":
            self.sync_product_package(run_id)
        return self.get_run_detail(run_id)

    @staticmethod
    def _workflow_runner_step_args(
        *,
        from_step: str | None,
        to_step: str | None,
        only_steps: list[str] | None,
    ) -> list[str]:
        args: list[str] = []
        if from_step:
            args.extend(["--from-step", str(from_step)])
        if to_step:
            args.extend(["--to-step", str(to_step)])
        if only_steps:
            args.extend(["--only-steps", ",".join(str(item) for item in only_steps if str(item).strip())])
        return args

    def _execute_workflow_bridge(
        self,
        run_id: str,
        run_dir: Path,
        *,
        workflow_manifest: dict[str, Any],
        from_step: str | None,
        to_step: str | None,
        only_steps: list[str],
        force: bool,
        timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        selected = self._select_workflow_steps(
            workflow_manifest.get("steps") or [],
            from_step=from_step,
            to_step=to_step,
            only_steps=only_steps,
        )
        state_path = run_dir / "state" / "step_status.json"
        state = self._read_optional_json(state_path) or self._initial_workflow_state(
            self._read_json(run_dir / "run_manifest.json"),
            workflow_manifest,
        )
        state.setdefault("steps", {})
        results: list[dict[str, Any]] = []

        for step in selected:
            step_id = str(step.get("id") or "")
            if not step.get("enabled"):
                result = self._workflow_step_result(step, status="PLANNED", skipped_reason="step planned but not enabled")
                state["steps"][step_id] = result
                results.append(result)
                continue
            previous = state["steps"].get(step_id) or {}
            if previous.get("status") == "COMPLETED" and not force:
                result = {**previous, "status": "SKIPPED", "skipped_reason": "already completed"}
                state["steps"][step_id] = previous
                results.append(result)
                continue

            started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            try:
                detail = self._execute_workflow_step_bridge(
                    run_id,
                    step_id,
                    timeout_seconds=timeout_seconds,
                )
                result = {
                    "id": step_id,
                    "name": step.get("name") or step_id,
                    "status": "COMPLETED",
                    "started_at": started_at,
                    "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "returncode": 0,
                    "detail": self._workflow_step_detail_summary(step_id, detail),
                }
            except Exception as exc:
                result = {
                    "id": step_id,
                    "name": step.get("name") or step_id,
                    "status": "FAILED",
                    "started_at": started_at,
                    "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "returncode": 1,
                    "error": str(exc),
                }
                state["steps"][step_id] = result
                state["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                self._write_json(state_path, state)
                results.append(result)
                break

            state["steps"][step_id] = result
            state["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            self._write_json(state_path, state)
            results.append(result)
        return results

    def _execute_expert_workflow_scripts(
        self,
        run_id: str,
        run_dir: Path,
        *,
        workflow_manifest: dict[str, Any],
        from_step: str | None,
        to_step: str | None,
        only_steps: list[str],
        force: bool,
        timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        selected = self._select_workflow_steps(
            workflow_manifest.get("steps") or [],
            from_step=from_step,
            to_step=to_step,
            only_steps=only_steps,
        )
        state_path = run_dir / "state" / "step_status.json"
        state = self._read_optional_json(state_path) or self._initial_workflow_state(
            self._read_json(run_dir / "run_manifest.json"),
            workflow_manifest,
        )
        state.setdefault("steps", {})
        results: list[dict[str, Any]] = []
        for step in selected:
            step_id = str(step.get("id") or "")
            if not step.get("enabled"):
                result = self._workflow_step_result(step, status="PLANNED", skipped_reason="step planned but not enabled")
                state["steps"][step_id] = result
                results.append(result)
                continue
            previous = state["steps"].get(step_id) or {}
            if previous.get("status") == "COMPLETED" and not force:
                result = {**previous, "status": "SKIPPED", "skipped_reason": "already completed"}
                state["steps"][step_id] = previous
                results.append(result)
                continue

            started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            state["steps"][step_id] = {
                "id": step_id,
                "name": step.get("name") or step_id,
                "enabled": bool(step.get("enabled")),
                "optional": bool(step.get("optional")),
                "status": "RUNNING",
                "started_at": started_at,
                "script": step.get("script"),
                "log": step.get("log"),
            }
            state["updated_at"] = started_at
            self._write_json(state_path, state)
            try:
                detail = self._execute_expert_workflow_step_script(run_dir, step, timeout_seconds=timeout_seconds)
                result = {
                    "id": step_id,
                    "name": step.get("name") or step_id,
                    "status": "COMPLETED",
                    "started_at": started_at,
                    "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "returncode": 0,
                    "detail": detail,
                }
            except Exception as exc:
                result = {
                    "id": step_id,
                    "name": step.get("name") or step_id,
                    "status": "FAILED",
                    "started_at": started_at,
                    "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "returncode": 1,
                    "error": str(exc),
                }
                state["steps"][step_id] = result
                state["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                self._write_json(state_path, state)
                results.append(result)
                break
            state["steps"][step_id] = result
            state["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            self._write_json(state_path, state)
            results.append(result)
        return results

    def _execute_expert_workflow_step_script(
        self,
        run_dir: Path,
        step: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        step_id = str(step.get("id") or "")
        script_path = Path(self._path_to_windows(str(step.get("script") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"expert workflow script not found for {step_id}: {script_path}")
        audit = self._audit_expert_step_script(step_id, script_path)
        if not audit.get("ready"):
            raise ValueError(f"expert command audit failed for {step_id}: {audit}")
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))
        completed = subprocess.run(
            command,
            cwd=str(run_dir),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        log_path = run_dir / "logs" / f"{step_id}.runner.log"
        log_text = "\n".join(
            [
                "$ " + " ".join(command),
                "",
                "STDOUT:",
                completed.stdout or "",
                "",
                "STDERR:",
                completed.stderr or "",
                "",
            ]
        )
        log_path.write_text(log_text, encoding="utf-8", newline="\n")
        if completed.returncode != 0:
            raise RuntimeError(
                f"expert workflow step {step_id} failed with rc={completed.returncode}: "
                f"{self._tail_text(completed.stderr or completed.stdout)}"
            )
        return {
            "step_id": step_id,
            "script": str(script_path),
            "command": command,
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
            "runner_log": str(log_path),
            "command_audit": audit,
        }

    def _execute_workflow_step_bridge(self, run_id: str, step_id: str, *, timeout_seconds: int) -> dict[str, Any]:
        self._restore_stage_status_for_workflow_step(run_id)
        if step_id in {"01_workspace_data"}:
            return self.get_run_detail(run_id)
        if step_id in {"01_import_slc", "02_import_lt1_slc", "03_reference_mli"}:
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            if status not in self._WORKFLOW_BASELINE_DONE_STATUSES:
                return self.run_baseline_audit(
                    run_id,
                    execute=True,
                    rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                    azlks=settings.GAMMA_SBAS_DEFAULT_AZLKS,
                    max_delta_n=1,
                    timeout_seconds=timeout_seconds,
                )
            return detail
        if step_id in {"02_coregister_stack", "05_coreg_prep", "06_coregister_scenes", "07_rmli_average"}:
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if self._stage_execution_completed(manifest.get("coregistration")):
                return detail
            run_dir = self._resolve_run_dir(run_id)
            approved_itab = run_dir / "work" / "gamma" / "diff" / "itab_approved"
            if not approved_itab.is_file() and settings.GAMMA_SBAS_AUTO_APPROVE_ITAB:
                self.decide_itab(
                    run_id,
                    decision="approve",
                    reviewer="system",
                    note="Auto-approved for Gamma SBAS expert workflow execution.",
                )
                detail = self.get_run_detail(run_id)
                status = str((detail.get("run") or {}).get("status") or "").strip()
            return self.execute_coregistration(
                run_id,
                rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                azlks=settings.GAMMA_SBAS_DEFAULT_AZLKS,
                timeout_seconds=timeout_seconds,
            )
        if step_id in {"03_prepare_dem", "04_dem_lookup"}:
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if self._stage_execution_completed(manifest.get("rdc_dem")):
                return detail
            if status not in {"COREGISTRATION_READY", "RDC_DEM_SCRIPT_READY", "RDC_DEM_RUNNING", "RDC_DEM_FAILED"}:
                pass
            return self.execute_rdc_dem(
                run_id,
                rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                timeout_seconds=timeout_seconds,
            )
        if step_id in {"04_build_network_diff", "08_diff_network", "09_filter_unwrap"}:
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if self._stage_execution_completed(manifest.get("interferograms")):
                return detail
            if not self._stage_execution_completed(manifest.get("coregistration")):
                self._execute_workflow_step_bridge(run_id, "07_rmli_average", timeout_seconds=timeout_seconds)
            detail = self.get_run_detail(run_id)
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if not self._stage_execution_completed(manifest.get("rdc_dem")):
                self._execute_workflow_step_bridge(run_id, "04_dem_lookup", timeout_seconds=timeout_seconds)
            return self.execute_interferograms(
                run_id,
                rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                azlks=settings.GAMMA_SBAS_DEFAULT_AZLKS,
                unwrap_threshold=0.20,
                timeout_seconds=timeout_seconds,
            )
        if step_id in {"05_detrend_atm", "10_detrend_atm"}:
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if self._stage_execution_completed(manifest.get("detrend_atm")):
                return detail
            if status in self._WORKFLOW_DETREND_DONE_STATUSES:
                return detail
            if status not in {"INTERFEROGRAMS_READY", "DETREND_ATM_SCRIPT_READY", "DETREND_ATM_RUNNING", "DETREND_ATM_FAILED"}:
                self._execute_workflow_step_bridge(run_id, "09_filter_unwrap", timeout_seconds=timeout_seconds)
            return self.execute_detrend_atm(
                run_id,
                rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                reference_window=settings.GAMMA_SBAS_DEFAULT_REFERENCE_WINDOW,
                timeout_seconds=timeout_seconds,
            )
        if step_id in {"06_sbas_inversion", "11_sbas_inversion"}:
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if self._stage_execution_completed(manifest.get("ipta_timeseries")):
                return detail
            if status in self._WORKFLOW_IPTA_DONE_STATUSES:
                return detail
            if status not in {"DETREND_ATM_READY", "IPTA_TIMESERIES_SCRIPT_READY", "IPTA_TIMESERIES_RUNNING", "IPTA_TIMESERIES_FAILED"}:
                self._execute_workflow_step_bridge(run_id, "10_detrend_atm", timeout_seconds=timeout_seconds)
            return self.execute_ipta_timeseries(
                run_id,
                rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                reference_window=settings.GAMMA_SBAS_DEFAULT_REFERENCE_WINDOW,
                mb_mode=settings.GAMMA_SBAS_DEFAULT_MB_MODE,
                timeout_seconds=timeout_seconds,
            )
        if step_id == "12_outputs_points":
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
            if self._stage_execution_completed(manifest.get("monitor_point_products")):
                return detail
            if status not in self._WORKFLOW_IPTA_DONE_STATUSES:
                detail = self._execute_workflow_step_bridge(run_id, "11_sbas_inversion", timeout_seconds=timeout_seconds)
                status = str((detail.get("run") or {}).get("status") or "").strip()
            if status not in self._WORKFLOW_PUBLISH_DONE_STATUSES:
                detail = self.execute_publish_products(
                    run_id,
                    rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                    timeout_seconds=min(timeout_seconds, 86400),
                )
                status = str((detail.get("run") or {}).get("status") or "").strip()
            if status not in self._WORKFLOW_MONITOR_DONE_STATUSES:
                return self.execute_monitor_points(
                    run_id,
                    timeout_seconds=min(timeout_seconds, 86400),
                )
            return detail
        if step_id == "07_publish_products":
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            if status in self._WORKFLOW_PUBLISH_DONE_STATUSES:
                return detail
            return self.execute_publish_products(
                run_id,
                rlks=settings.GAMMA_SBAS_DEFAULT_RLKS,
                timeout_seconds=min(timeout_seconds, 86400),
            )
        if step_id == "08_point_timeseries":
            detail = self.get_run_detail(run_id)
            status = str((detail.get("run") or {}).get("status") or "").strip()
            if status in self._WORKFLOW_MONITOR_DONE_STATUSES:
                return detail
            return self.execute_monitor_points(
                run_id,
                timeout_seconds=min(timeout_seconds, 86400),
            )
        return {"status": "planned_only", "step_id": step_id}

    @staticmethod
    def _workflow_step_detail_summary(step_id: str, detail: dict[str, Any]) -> dict[str, Any]:
        run = detail.get("run") if isinstance(detail, dict) else {}
        run = run if isinstance(run, dict) else {}
        summary: dict[str, Any] = {
            "step_id": step_id,
            "run_id": run.get("run_id"),
            "run_status": run.get("status"),
            "next_stage": run.get("next_stage"),
        }
        stage_by_step = {
            "01_workspace_data": "stack",
            "01_import_slc": "baseline_audit",
            "02_import_lt1_slc": "baseline_audit",
            "03_reference_mli": "baseline_audit",
            "02_coregister_stack": "coregistration",
            "05_coreg_prep": "coregistration",
            "06_coregister_scenes": "coregistration",
            "07_rmli_average": "coregistration",
            "03_prepare_dem": "rdc_dem",
            "04_dem_lookup": "rdc_dem",
            "04_build_network_diff": "interferograms",
            "08_diff_network": "interferograms",
            "09_filter_unwrap": "interferograms",
            "10_detrend_atm": "detrend_atm",
            "06_sbas_inversion": "ipta_timeseries",
            "11_sbas_inversion": "ipta_timeseries",
            "07_publish_products": "publish_products",
            "08_point_timeseries": "monitor_point_products",
            "12_outputs_points": "publish_products",
        }
        stage_key = stage_by_step.get(step_id)
        stage = run.get(stage_key) if stage_key else None
        if isinstance(stage, dict):
            execution = stage.get("execution") if isinstance(stage.get("execution"), dict) else {}
            stage_summary = stage.get("summary") if isinstance(stage.get("summary"), dict) else {}
            summary["stage"] = {
                "key": stage_key,
                "script_path": stage.get("script_path"),
                "reference_date": stage.get("reference_date"),
                "returncode": execution.get("returncode"),
                "execution_status": execution.get("status"),
                "ready": stage_summary.get("ready"),
                "outputs": stage.get("outputs") if isinstance(stage.get("outputs"), dict) else None,
            }
        return summary

    def _restore_stage_status_for_workflow_step(self, run_id: str) -> None:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        if manifest.get("status") != "WORKFLOW_RUNNING":
            return
        workflow = manifest.get("workflow") or {}
        stage_status = str(
            workflow.get("resume_stage_status")
            or workflow.get("previous_status")
            or ""
        ).strip()
        if not stage_status or stage_status == "WORKFLOW_READY":
            stage_status = "PLANNED_GAMMA_BASELINE_AUDIT"
        manifest["status"] = stage_status
        manifest["next_stage"] = self._next_stage_for_status(stage_status)
        self._write_json(manifest_path, manifest)

    @staticmethod
    def _next_stage_for_status(status: str) -> str:
        return {
            "PLANNED_GAMMA_BASELINE_AUDIT": "baseline_audit",
            "BASELINE_AUDIT_READY": "approve_itab",
            "ITAB_APPROVED": "coregistration",
            "COREGISTRATION_SCRIPT_READY": "execute_coregistration",
            "COREGISTRATION_READY": "rdc_dem",
            "RDC_DEM_SCRIPT_READY": "execute_rdc_dem",
            "RDC_DEM_READY": "interferograms",
            "INTERFEROGRAMS_SCRIPT_READY": "execute_interferograms",
            "INTERFEROGRAMS_READY": "detrend_atm",
            "DETREND_ATM_SCRIPT_READY": "execute_detrend_atm",
            "DETREND_ATM_READY": "ipta_timeseries",
            "IPTA_TIMESERIES_SCRIPT_READY": "execute_ipta_timeseries",
            "IPTA_TIMESERIES_READY": "publish_products",
            "PUBLISH_PRODUCTS_SCRIPT_READY": "execute_publish_products",
            "PRODUCTS_READY": "monitor_points",
            "MONITOR_POINTS_SCRIPT_READY": "execute_monitor_points",
            "MONITOR_POINTS_READY": "review_publish_products",
        }.get(str(status or "").strip(), "workflow")

    @staticmethod
    def _workflow_step_result(step: dict[str, Any], *, status: str, skipped_reason: str | None = None) -> dict[str, Any]:
        payload = {
            "id": step.get("id"),
            "name": step.get("name") or step.get("id"),
            "status": status,
            "started_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if skipped_reason:
            payload["skipped_reason"] = skipped_reason
        return payload

    @staticmethod
    def _select_workflow_steps(
        steps: list[dict[str, Any]],
        *,
        from_step: str | None,
        to_step: str | None,
        only_steps: list[str],
    ) -> list[dict[str, Any]]:
        only = {str(item).strip() for item in only_steps or [] if str(item).strip()}
        if only:
            return [step for step in steps if str(step.get("id") or "") in only]
        if not from_step and not to_step:
            return steps
        selected: list[dict[str, Any]] = []
        active = from_step is None
        for step in steps:
            step_id = str(step.get("id") or "")
            if step_id == from_step:
                active = True
            if active:
                selected.append(step)
            if step_id == to_step:
                break
        return selected

    def _resolve_source_roots(self, roots: list[str] | None, *, sensor_family: str = "LT1") -> list[Path]:
        sensor_family = self._normalize_sensor_family(sensor_family)
        raw_values = roots or self._default_source_roots(sensor_family)
        if not raw_values:
            raw_values = [r"D:\Sentinel1_Image_Pool"] if sensor_family == "S1" else [r"D:\LuTan1_Image_Pool"]
        return self._dedupe_existing_dirs(raw_values)

    def _resolve_orbit_roots(self, roots: list[str] | None, *, sensor_family: str = "LT1") -> list[Path]:
        sensor_family = self._normalize_sensor_family(sensor_family)
        raw_values = roots or self._default_orbit_roots(sensor_family)
        if not raw_values:
            raw_values = [r"D:\Sentinel1_Orbit_Pool"] if sensor_family == "S1" else [r"D:\orbit_pools\envi"]
        return self._dedupe_existing_dirs(raw_values)

    def _default_source_roots(self, sensor_family: str) -> list[str]:
        if sensor_family == "S1":
            return self._split_config_paths(
                settings.SENTINEL1_STORAGE_DIRS,
                settings.SOURCE_PRODUCT_DIRS,
            )
        return self._split_config_paths(settings.GAMMA_SBAS_SOURCE_ROOTS)

    def _default_orbit_roots(self, sensor_family: str) -> list[str]:
        if sensor_family == "S1":
            return self._split_config_paths(settings.ORBIT_SOURCE_DIRS)
        return self._split_config_paths(settings.GAMMA_SBAS_ORBIT_ROOTS)

    def _build_root_resolution_warnings(
        self,
        *,
        source_roots: list[str] | None,
        orbit_roots: list[str] | None,
        source_paths: list[Path],
        orbit_paths: list[Path],
        sensor_family: str = "LT1",
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        sensor_family = self._normalize_sensor_family(sensor_family)
        source_requested = source_roots or self._default_source_roots(sensor_family) or (
            [r"D:\Sentinel1_Image_Pool"] if sensor_family == "S1" else [r"D:\LuTan1_Image_Pool"]
        )
        orbit_requested = orbit_roots or self._default_orbit_roots(sensor_family) or (
            [r"D:\Sentinel1_Orbit_Pool"] if sensor_family == "S1" else [r"D:\orbit_pools\envi"]
        )

        source_missing = self._missing_root_values(source_requested)
        orbit_missing = self._missing_root_values(orbit_requested)
        if source_missing:
            warnings.append(
                {
                    "code": "SOURCE_ROOTS_NOT_FOUND",
                    "message": "Some configured SBAS source roots do not exist and were ignored.",
                    "requested_roots": source_requested,
                    "missing_roots": source_missing,
                    "resolved_roots": [str(path) for path in source_paths],
                }
            )
        if orbit_missing:
            warnings.append(
                {
                    "code": "ORBIT_ROOTS_NOT_FOUND",
                    "message": "Some configured SBAS orbit roots do not exist and were ignored.",
                    "requested_roots": orbit_requested,
                    "missing_roots": orbit_missing,
                    "resolved_roots": [str(path) for path in orbit_paths],
                }
            )
        if source_requested and not source_paths:
            warnings.append(
                {
                    "code": "NO_VALID_SOURCE_ROOTS",
                    "message": "No valid SBAS source roots were resolved; discovery will return no scenes.",
                    "requested_roots": source_requested,
                }
            )
        if orbit_requested and not orbit_paths:
            warnings.append(
                {
                    "code": "NO_VALID_ORBIT_ROOTS",
                    "message": "No valid SBAS orbit roots were resolved; orbit matching will be unavailable.",
                    "requested_roots": orbit_requested,
                }
            )
        return warnings

    @staticmethod
    def _discovery_cache_key(
        *,
        source_paths: list[Path],
        orbit_paths: list[Path],
        sensor_family: str,
        min_scenes: int,
        require_orbits: bool,
        include_scenes: bool,
        limit: int,
        platform: str | None,
        relative_orbit: str | None,
        orbit_direction: str | None,
        admin_region: str | None,
        discovery_mode: str,
        aoi_bbox: dict[str, Any] | None,
        min_aoi_coverage_ratio: float,
        min_common_overlap_ratio: float,
        strategy_version: str = "gamma-overlap-substack-v3",
    ) -> str:
        payload = {
            "source_paths": [os.path.normcase(str(path.resolve())) for path in source_paths],
            "orbit_paths": [os.path.normcase(str(path.resolve())) for path in orbit_paths],
            "sensor_family": str(sensor_family or "LT1").strip().upper(),
            "source_mtime_ns": [
                int(path.stat().st_mtime_ns) if path.exists() else 0
                for path in source_paths
            ],
            "orbit_mtime_ns": [
                int(path.stat().st_mtime_ns) if path.exists() else 0
                for path in orbit_paths
            ],
            "min_scenes": int(min_scenes),
            "require_orbits": bool(require_orbits),
            "include_scenes": bool(include_scenes),
            "limit": int(limit),
            "platform": str(platform or "").strip().upper(),
            "relative_orbit": str(relative_orbit or "").strip(),
            "orbit_direction": str(orbit_direction or "").strip().upper(),
            "admin_region": str(admin_region or "").strip(),
            "discovery_mode": str(discovery_mode or "strict").strip().lower(),
            "aoi_bbox": SbasInsarProductionService._normalize_bbox(aoi_bbox),
            "min_aoi_coverage_ratio": float(min_aoi_coverage_ratio),
            "min_common_overlap_ratio": float(min_common_overlap_ratio),
            "response_shape": "footprint_cluster_discovery_v3",
            "strategy_version": strategy_version,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def _discovery_cache_path(self, cache_key: str) -> Path:
        return self.production_root / "discoveries" / "cache" / f"{cache_key}.json"

    def _read_discovery_cache(self, cache_key: str) -> dict[str, Any] | None:
        path = self._discovery_cache_path(cache_key)
        if not path.is_file():
            return None
        try:
            payload = self._read_json(path)
        except Exception:
            return None
        payload["cache_hit"] = True
        payload["cache_path"] = str(path)
        return payload

    def _write_discovery_cache(self, cache_key: str, snapshot: dict[str, Any]) -> None:
        payload = {**snapshot, "cache_key": cache_key, "cache_hit": False}
        self._write_json(self._discovery_cache_path(cache_key), payload)

    @staticmethod
    def _split_config_paths(*values: str) -> list[str]:
        paths: list[str] = []
        for value in values:
            for item in str(value or "").replace(";", ",").split(","):
                text = item.strip().strip('"').strip("'")
                if text:
                    paths.append(text)
        return paths

    @staticmethod
    def _dedupe_existing_dirs(values: list[str]) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for value in values:
            for path in SbasInsarProductionService._existing_path_variants(value):
                key = os.path.normcase(str(path.resolve()))
                if key in seen:
                    continue
                seen.add(key)
                roots.append(path)
        return roots

    @staticmethod
    def _missing_root_values(values: list[str]) -> list[str]:
        missing: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip().strip('"').strip("'")
            if not text:
                continue
            key = os.path.normcase(text)
            if key in seen:
                continue
            seen.add(key)
            if not SbasInsarProductionService._existing_path_variants(text):
                missing.append(text)
        return missing

    @staticmethod
    def _existing_path_variants(value: str) -> list[Path]:
        text = str(value or "").strip().strip('"').strip("'")
        if not text:
            return []
        candidates = [Path(os.path.normpath(text))]
        wsl_path = SbasInsarProductionService._windows_path_to_wsl_mount(text)
        if wsl_path and wsl_path != text:
            candidates.append(Path(wsl_path))
        windows_path = SbasInsarProductionService._path_to_windows(text)
        if windows_path and windows_path != text:
            candidates.append(Path(os.path.normpath(windows_path)))

        existing: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_dir():
                existing.append(candidate)
        return existing

    def _iter_lt1_scene_dirs(self, root: Path):
        if root.name.upper().startswith("LT1") and self._looks_like_lt1_scene_dir(root):
            yield root
            return

        try:
            children = list(root.iterdir())
        except OSError:
            return

        for child in children:
            if child.is_dir() and child.name.upper().startswith("LT1") and self._looks_like_lt1_scene_dir(child):
                yield child

        # Some source roots may have one extra grouping level. Keep recursion shallow
        # to avoid walking runtime work directories by accident.
        for child in children:
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if child.name.upper().startswith(("LT1A", "LT1B")):
                continue
            try:
                for grandchild in child.iterdir():
                    if (
                        grandchild.is_dir()
                        and grandchild.name.upper().startswith("LT1")
                        and self._looks_like_lt1_scene_dir(grandchild)
                    ):
                        yield grandchild
            except OSError:
                continue

    @staticmethod
    def _looks_like_lt1_scene_dir(path: Path) -> bool:
        try:
            return any(path.glob("*.meta.xml")) and any(
                list(path.glob("*.tiff")) + list(path.glob("*.tif"))
            )
        except OSError:
            return False

    def _iter_s1_scene_sources(self, root: Path):
        if self._looks_like_s1_source(root):
            yield root
            return

        stack: list[tuple[Path, int]] = [(root, 0)]
        seen: set[str] = set()
        while stack:
            current, depth = stack.pop()
            key = os.path.normcase(str(current.resolve()))
            if key in seen:
                continue
            seen.add(key)
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                name = child.name
                if name.startswith((".", "_")):
                    continue
                if self._looks_like_s1_source(child):
                    yield child
                    continue
                if child.is_dir() and depth < 2:
                    stack.append((child, depth + 1))

    @staticmethod
    def _looks_like_s1_source(path: Path) -> bool:
        name = path.name
        upper = name.upper()
        if path.is_file() and upper.startswith("S1") and upper.endswith(".ZIP"):
            return S1_SOURCE_RE.match(name) is not None
        if path.is_dir() and upper.startswith("S1") and upper.endswith(".SAFE"):
            return S1_SOURCE_RE.match(name) is not None and (path / "manifest.safe").is_file()
        return False

    def _parse_s1_scene(self, source_path: Path, orbit_roots: list[Path]) -> dict[str, Any]:
        source_name = source_path.name
        filename_meta = self._parse_s1_scene_name(source_name)
        if not filename_meta:
            raise ValueError(f"Cannot parse Sentinel-1 source name: {source_name}")

        manifest_meta = self._parse_s1_manifest(source_path)
        meta = {**filename_meta, **{key: value for key, value in manifest_meta.items() if value not in (None, "", [])}}
        start_dt = meta.get("start_time_utc_dt")
        stop_dt = meta.get("stop_time_utc_dt") or start_dt
        date = str(meta.get("date") or "")[:8]
        satellite = str(meta.get("satellite") or "").upper()
        orbit_path = self._find_s1_orbit(orbit_roots, satellite, start_dt, stop_dt)

        bbox = meta.get("bbox")
        center_lon, center_lat = self._centroid_from_bbox(bbox)
        if meta.get("center_lon") is not None:
            center_lon = self._as_float(meta.get("center_lon"))
        if meta.get("center_lat") is not None:
            center_lat = self._as_float(meta.get("center_lat"))

        polarizations = meta.get("polarization_channels") or []
        polarization = "+".join(polarizations) if polarizations else str(meta.get("polarization") or "").upper() or None
        source_format = "S1_SAFE_DIR" if source_path.is_dir() else "S1_ZIP"
        source_windows = self._path_to_windows(str(source_path))
        source_wsl = self._windows_path_to_wsl_mount(str(source_path))
        return {
            "scene_name": source_path.name,
            "logical_product_uid": meta.get("logical_product_uid"),
            "scene_dir_windows": source_windows if source_path.is_dir() else None,
            "scene_dir_wsl": source_wsl if source_path.is_dir() else None,
            "source_windows": source_windows,
            "source_wsl": source_wsl,
            "source_format": source_format,
            "archive_windows": source_windows if source_path.is_file() else None,
            "archive_wsl": source_wsl if source_path.is_file() else None,
            "orbit_windows": self._path_to_windows(str(orbit_path)) if orbit_path else None,
            "orbit_wsl": self._windows_path_to_wsl_mount(str(orbit_path)) if orbit_path else None,
            "has_orbit": bool(orbit_path),
            "date": date,
            "satellite_family": "S1",
            "satellite": satellite,
            "satellite_mode": str(meta.get("product_type") or "").upper() or None,
            "receiving_station": None,
            "absolute_orbit": str(meta.get("absolute_orbit") or "") or None,
            "relative_orbit": str(meta.get("relative_orbit") or "") or None,
            "orbit_direction": str(meta.get("orbit_direction") or "").upper() or None,
            "imaging_mode": str(meta.get("imaging_mode") or "").upper() or None,
            "look_direction": None,
            "polarization": polarization,
            "product_type": str(meta.get("product_type") or "").upper() or None,
            "product_level": "L1",
            "source_product_token": meta.get("source_product_token"),
            "center_lon": center_lon,
            "center_lat": center_lat,
            "center_bucket": self._center_bucket(center_lon, center_lat),
            "bbox": bbox,
            "start_time_utc": self._datetime_to_iso(start_dt),
            "stop_time_utc": self._datetime_to_iso(stop_dt),
            "start_time_utc_dt": start_dt,
            "stop_time_utc_dt": stop_dt,
            "manifest_path": meta.get("manifest_path"),
            "polarization_channels": polarizations,
            "execution_note": "Sentinel-1 SBAS is discovery/planning only; Gamma TOPS execution is not enabled.",
        }

    @staticmethod
    def _parse_s1_scene_name(source_name: str) -> dict[str, Any]:
        base = SbasInsarProductionService._strip_s1_suffix(source_name)
        match = S1_SOURCE_RE.match(base)
        if not match:
            return {}
        data = match.groupdict()
        class_token = str(data.get("class") or "").upper()
        start_dt = SbasInsarProductionService._parse_s1_datetime(data.get("start"))
        stop_dt = SbasInsarProductionService._parse_s1_datetime(data.get("stop"))
        return {
            "logical_product_uid": base,
            "satellite": str(data.get("satellite") or "").upper(),
            "imaging_mode": str(data.get("mode") or "").upper(),
            "product_type": str(data.get("product") or "").upper(),
            "source_product_token": class_token,
            "polarization": class_token[-2:] if len(class_token) >= 2 else class_token,
            "absolute_orbit": str(data.get("absolute_orbit") or "").lstrip("0") or data.get("absolute_orbit"),
            "date": str(data.get("start") or "")[:8],
            "start_time_utc_dt": start_dt,
            "stop_time_utc_dt": stop_dt,
            "filename_datatake": str(data.get("datatake") or "").upper(),
            "filename_product_uid": str(data.get("product_uid") or "").upper(),
        }

    def _parse_s1_manifest(self, source_path: Path) -> dict[str, Any]:
        if source_path.is_dir():
            manifest_path = source_path / "manifest.safe"
            if not manifest_path.is_file():
                return {"manifest_parse_status": "MISSING"}
            try:
                data = manifest_path.read_bytes()
            except OSError as exc:
                return {"manifest_parse_status": "FAILED", "manifest_parse_error": str(exc)}
            return {
                "manifest_parse_status": "OK",
                "manifest_path": str(manifest_path),
                **self._parse_s1_manifest_bytes(data),
            }

        try:
            with zipfile.ZipFile(source_path) as archive:
                manifest_name = next(
                    (
                        name for name in archive.namelist()
                        if name.lower().endswith("/manifest.safe") or name.lower() == "manifest.safe"
                    ),
                    None,
                )
                if not manifest_name:
                    return {"manifest_parse_status": "MISSING"}
                return {
                    "manifest_parse_status": "OK",
                    "manifest_path": manifest_name,
                    **self._parse_s1_manifest_bytes(archive.read(manifest_name)),
                }
        except Exception as exc:
            return {"manifest_parse_status": "FAILED", "manifest_parse_error": str(exc)}

    def _parse_s1_manifest_bytes(self, data: bytes) -> dict[str, Any]:
        try:
            root = ET.fromstring(data)
        except Exception as exc:
            return {"manifest_parse_status": "FAILED", "manifest_parse_error": str(exc)}
        start_dt = self._parse_s1_datetime(self._first_text_by_local_name(root, {"startTime"}))
        stop_dt = self._parse_s1_datetime(self._first_text_by_local_name(root, {"stopTime"}))
        pols = [
            str(item).strip().upper()
            for item in self._texts_by_local_name(root, "transmitterReceiverPolarisation")
            if str(item).strip()
        ]
        polygon = self._s1_polygon_from_coordinates(self._first_text_by_local_name(root, {"coordinates"}))
        bbox = self._bbox_from_points(polygon)
        center_lon, center_lat = self._centroid_from_points(polygon)
        return {
            "start_time_utc_dt": start_dt,
            "stop_time_utc_dt": stop_dt,
            "product_type": self._clean_upper(self._first_text_by_local_name(root, {"productType"})),
            "imaging_mode": self._clean_upper(self._first_text_by_local_name(root, {"mode"})),
            "orbit_direction": self._clean_upper(self._first_text_by_local_name(root, {"pass"})),
            "polarization_channels": pols,
            "absolute_orbit": self._clean_text(self._first_text_by_local_name(root, {"orbitNumber"})),
            "relative_orbit": self._clean_text(self._first_text_by_local_name(root, {"relativeOrbitNumber"})),
            "bbox": bbox,
            "center_lon": center_lon,
            "center_lat": center_lat,
            "coverage_polygon": polygon,
        }

    @staticmethod
    def _first_text_by_local_name(root: ET.Element, names: set[str]) -> str | None:
        wanted = {name.lower() for name in names}
        for element in root.iter():
            tag = str(element.tag).split("}")[-1].lower()
            if tag not in wanted:
                continue
            text = (element.text or "").strip()
            if text:
                return text
        return None

    @staticmethod
    def _texts_by_local_name(root: ET.Element, name: str) -> list[str]:
        wanted = str(name or "").lower()
        values: list[str] = []
        for element in root.iter():
            tag = str(element.tag).split("}")[-1].lower()
            if tag != wanted:
                continue
            text = (element.text or "").strip()
            if text and text not in values:
                values.append(text)
        return values

    @staticmethod
    def _s1_polygon_from_coordinates(text: str | None) -> list[tuple[float, float]] | None:
        if not text:
            return None
        points: list[tuple[float, float]] = []
        for token in re.split(r"\s+", text.strip()):
            parts = [part for part in re.split(r"[,;]", token) if part]
            if len(parts) < 2:
                continue
            try:
                first = float(parts[0])
                second = float(parts[1])
            except ValueError:
                continue
            if abs(first) > 90.0 and abs(second) <= 90.0:
                lon, lat = first, second
            else:
                lon, lat = second, first
            points.append((lon, lat))
        if len(points) < 3:
            return None
        if points[0] != points[-1]:
            points.append(points[0])
        return points

    @staticmethod
    def _bbox_from_points(points: list[tuple[float, float]] | None) -> dict[str, float] | None:
        if not points:
            return None
        lons = [float(point[0]) for point in points]
        lats = [float(point[1]) for point in points]
        return {
            "min_lon": min(lons),
            "min_lat": min(lats),
            "max_lon": max(lons),
            "max_lat": max(lats),
        }

    @staticmethod
    def _centroid_from_points(points: list[tuple[float, float]] | None) -> tuple[float | None, float | None]:
        if not points:
            return None, None
        unique = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
        if not unique:
            return None, None
        return (
            sum(float(point[0]) for point in unique) / len(unique),
            sum(float(point[1]) for point in unique) / len(unique),
        )

    @staticmethod
    def _centroid_from_bbox(bbox: dict[str, Any] | None) -> tuple[float | None, float | None]:
        if not bbox:
            return None, None
        try:
            return (
                (float(bbox["min_lon"]) + float(bbox["max_lon"])) / 2,
                (float(bbox["min_lat"]) + float(bbox["max_lat"])) / 2,
            )
        except (KeyError, TypeError, ValueError):
            return None, None

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _clean_upper(value: Any) -> str | None:
        text = str(value or "").strip().upper()
        return text or None

    @staticmethod
    def _strip_s1_suffix(name: str) -> str:
        lower = str(name or "").lower()
        if lower.endswith(".zip"):
            return str(name)[:-4]
        if lower.endswith(".safe"):
            return str(name)[:-5]
        return str(name or "")

    @staticmethod
    def _parse_s1_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith("UTC="):
            text = text[4:]
        text = text.rstrip("Z")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y%m%dT%H%M%S.%f",
            "%Y%m%dT%H%M%S",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _datetime_to_iso(value: datetime | None) -> str | None:
        if not value:
            return None
        return value.isoformat(timespec="seconds") + "Z"

    def _find_s1_orbit(
        self,
        orbit_roots: list[Path],
        satellite: str,
        start_dt: datetime | None,
        stop_dt: datetime | None,
    ) -> Path | None:
        if not satellite or not start_dt:
            return None
        stop_dt = stop_dt or start_dt
        candidates: list[tuple[int, float, Path]] = []
        for root in orbit_roots:
            try:
                paths = root.rglob("S1*.EOF")
            except OSError:
                continue
            for path in paths:
                parsed = self._parse_s1_eof_name(path.name)
                if not parsed:
                    continue
                if parsed.get("satellite") != satellite:
                    continue
                valid_start = parsed.get("valid_start")
                valid_stop = parsed.get("valid_stop")
                if not valid_start or not valid_stop:
                    continue
                if valid_start <= start_dt and valid_stop >= stop_dt:
                    quality_rank = 0 if "POEORB" in str(parsed.get("orbit_type") or "") else 1
                    coverage_margin = (start_dt - valid_start).total_seconds() + (valid_stop - stop_dt).total_seconds()
                    candidates.append((quality_rank, -coverage_margin, path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
        return candidates[0][2]

    @staticmethod
    def _parse_s1_eof_name(name: str) -> dict[str, Any] | None:
        match = S1_EOF_RE.match(str(name or ""))
        if not match:
            return None
        data = match.groupdict()
        return {
            "satellite": str(data.get("satellite") or "").upper(),
            "orbit_type": str(data.get("orbit_type") or "").upper(),
            "valid_start": SbasInsarProductionService._parse_s1_datetime(data.get("valid_start")),
            "valid_stop": SbasInsarProductionService._parse_s1_datetime(data.get("valid_stop")),
            "generation": SbasInsarProductionService._parse_s1_datetime(data.get("generation")),
        }

    @staticmethod
    def _dedupe_s1_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_uid: dict[str, dict[str, Any]] = {}
        for scene in scenes:
            uid = str(scene.get("logical_product_uid") or scene.get("scene_name") or "").strip()
            if not uid:
                continue
            current = by_uid.get(uid)
            if current is None:
                by_uid[uid] = scene
                continue
            current_score = 2 if current.get("source_format") == "S1_SAFE_DIR" else 1
            scene_score = 2 if scene.get("source_format") == "S1_SAFE_DIR" else 1
            if scene_score > current_score:
                by_uid[uid] = scene
        return list(by_uid.values())

    @staticmethod
    def _normalize_sensor_family(value: Any) -> str:
        text = str(value or "LT1").strip().upper().replace("-", "")
        if text in {"S1", "S1A", "S1B", "S1C", "SENTINEL1", "SENTINEL1A", "SENTINEL1B", "SENTINEL1C"}:
            return "S1"
        return "LT1"

    @staticmethod
    def _ensure_lt1_execution_enabled(manifest: dict[str, Any]) -> None:
        raw_profile = str(manifest.get("profile_code") or "").strip().lower()
        sensor_family = SbasInsarProductionService._normalize_sensor_family(
            manifest.get("sensor_family") or ((manifest.get("stack") or {}).get("satellite"))
        )
        if raw_profile.startswith("s1_") or sensor_family == "S1" or manifest.get("execution_enabled") is False:
            raise ValueError(
                "Sentinel-1 Gamma SBAS is currently discovery/planning only; "
                "Gamma TOPS/SBAS execution scripts are not enabled."
            )

    def _parse_lt1_scene(self, scene_dir: Path, orbit_roots: list[Path]) -> dict[str, Any]:
        scene_name = scene_dir.name
        filename_meta = self._parse_lt1_scene_name(scene_name)
        meta_path = self._select_meta_file(scene_dir)
        tiff_path = self._select_tiff_file(scene_dir)
        xml_meta = self._parse_lt1_product_info(meta_path)
        meta = {**filename_meta, **{key: value for key, value in xml_meta.items() if value not in (None, "")}}

        date = str(meta.get("date") or "")[:8]
        satellite = str(meta.get("satellite") or "").upper()
        orbit_path = self._find_lt1_orbit(orbit_roots, satellite, date)
        center_lon = self._as_float(meta.get("center_lon"))
        center_lat = self._as_float(meta.get("center_lat"))
        return {
            "scene_name": scene_name,
            "scene_dir_windows": self._path_to_windows(str(scene_dir)),
            "scene_dir_wsl": self._windows_path_to_wsl_mount(str(scene_dir)),
            "tiff_windows": self._path_to_windows(str(tiff_path)),
            "tiff_wsl": self._windows_path_to_wsl_mount(str(tiff_path)),
            "meta_windows": self._path_to_windows(str(meta_path)),
            "meta_wsl": self._windows_path_to_wsl_mount(str(meta_path)),
            "orbit_windows": self._path_to_windows(str(orbit_path)) if orbit_path else None,
            "orbit_wsl": self._windows_path_to_wsl_mount(str(orbit_path)) if orbit_path else None,
            "has_orbit": bool(orbit_path),
            "date": date,
            "satellite": satellite,
            "satellite_mode": str(meta.get("satellite_mode") or "").upper() or None,
            "receiving_station": str(meta.get("receiving_station") or "").upper() or None,
            "absolute_orbit": str(meta.get("absolute_orbit") or "") or None,
            "relative_orbit": str(meta.get("relative_orbit") or "") or None,
            "orbit_direction": str(meta.get("orbit_direction") or "").upper() or None,
            "imaging_mode": str(meta.get("imaging_mode") or "").upper() or None,
            "look_direction": str(meta.get("look_direction") or "").upper() or None,
            "polarization": str(meta.get("polarization") or "").upper() or None,
            "product_type": str(meta.get("product_type") or "").upper() or None,
            "center_lon": center_lon,
            "center_lat": center_lat,
            "center_bucket": self._center_bucket(center_lon, center_lat),
            "bbox": meta.get("bbox"),
            "start_time_utc": meta.get("start_time_utc"),
            "stop_time_utc": meta.get("stop_time_utc"),
        }

    @staticmethod
    def _parse_lt1_scene_name(scene_name: str) -> dict[str, Any]:
        match = LT1_SCENE_RE.match(scene_name)
        if not match:
            return {}
        data = match.groupdict()
        return {
            "satellite": data.get("satellite", "").upper(),
            "satellite_mode": data.get("satellite_mode", "").upper(),
            "receiving_station": data.get("receiving_station", "").upper(),
            "imaging_mode": data.get("imaging_mode", "").upper(),
            "absolute_orbit": data.get("absolute_orbit"),
            "center_lon": data.get("center_lon"),
            "center_lat": data.get("center_lat"),
            "date": data.get("date"),
            "product_type": data.get("product_type", "").upper(),
            "polarization": data.get("polarization", "").upper(),
        }

    @staticmethod
    def _select_meta_file(scene_dir: Path) -> Path:
        candidates = sorted(scene_dir.glob("*.meta.xml"))
        if not candidates:
            raise FileNotFoundError(f"No LT1 meta XML found in {scene_dir}")
        return candidates[0]

    @staticmethod
    def _select_tiff_file(scene_dir: Path) -> Path:
        candidates = sorted(list(scene_dir.glob("*.tiff")) + list(scene_dir.glob("*.tif")))
        if not candidates:
            raise FileNotFoundError(f"No LT1 TIFF found in {scene_dir}")
        slc_candidates = [path for path in candidates if "_SLC_" in path.name.upper()]
        return slc_candidates[0] if slc_candidates else candidates[0]

    def _parse_lt1_product_info(self, meta_path: Path) -> dict[str, Any]:
        text = meta_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"<productInfo\b[^>]*>.*?</productInfo>", text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return {}
        root = ET.fromstring(match.group(0))
        corners: list[tuple[float, float]] = []
        for element in root.findall(".//sceneCornerCoord"):
            lat = self._as_float(self._child_text(element, "lat"))
            lon = self._as_float(self._child_text(element, "lon"))
            if lat is not None and lon is not None:
                corners.append((lon, lat))
        bbox = None
        if corners:
            lons = [item[0] for item in corners]
            lats = [item[1] for item in corners]
            bbox = {
                "min_lon": min(lons),
                "min_lat": min(lats),
                "max_lon": max(lons),
                "max_lat": max(lats),
            }
        center = root.find(".//sceneCenterCoord")
        return {
            "satellite": self._find_text(root, ".//missionInfo/mission"),
            "absolute_orbit": self._find_text(root, ".//missionInfo/absOrbit"),
            "relative_orbit": self._find_text(root, ".//missionInfo/relOrbit"),
            "orbit_direction": self._find_text(root, ".//missionInfo/orbitDirection"),
            "receiving_station": self._find_text(root, ".//generationInfo/receivingStation"),
            "imaging_mode": self._find_text(root, ".//acquisitionInfo/imagingMode"),
            "look_direction": self._find_text(root, ".//acquisitionInfo/lookDirection"),
            "polarization": (
                self._find_text(root, ".//acquisitionInfo/polarisationMode")
                or self._find_text(root, ".//acquisitionInfo/polarisationList/polLayer")
            ),
            "start_time_utc": self._find_text(root, ".//sceneInfo/start/timeUTC"),
            "stop_time_utc": self._find_text(root, ".//sceneInfo/stop/timeUTC"),
            "date": self._date_from_time(self._find_text(root, ".//sceneInfo/start/timeUTC")),
            "center_lon": self._child_text(center, "lon") if center is not None else None,
            "center_lat": self._child_text(center, "lat") if center is not None else None,
            "bbox": bbox,
        }

    @staticmethod
    def _find_text(root: ET.Element, path: str) -> str | None:
        element = root.find(path)
        if element is None or element.text is None:
            return None
        text = element.text.strip()
        return text or None

    @staticmethod
    def _child_text(root: ET.Element | None, name: str) -> str | None:
        if root is None:
            return None
        element = root.find(name)
        if element is None or element.text is None:
            return None
        text = element.text.strip()
        return text or None

    @staticmethod
    def _date_from_time(value: str | None) -> str | None:
        text = str(value or "").strip()
        if len(text) >= 10:
            return text[:10].replace("-", "")
        return None

    @staticmethod
    def _as_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _center_bucket(lon: float | None, lat: float | None) -> str:
        if lon is None or lat is None:
            return "UNKNOWN_CENTER"
        return f"E{lon:.1f}_N{lat:.1f}"

    @staticmethod
    def _windows_path_to_wsl_mount(path: str | None) -> str | None:
        text = str(path or "").strip()
        if not text:
            return None
        normalized_posix = text.replace("\\", "/")
        wsl_match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", normalized_posix)
        if wsl_match:
            return f"/mnt/{wsl_match.group(1).lower()}/{wsl_match.group(2)}"
        drive_match = re.match(r"^([a-zA-Z]):/(.*)$", normalized_posix)
        if drive_match:
            return f"/mnt/{drive_match.group(1).lower()}/{drive_match.group(2).lstrip('/')}"
        drive, tail = os.path.splitdrive(os.path.normpath(text))
        if not drive:
            return text.replace("\\", "/")
        return f"/mnt/{drive.rstrip(':').lower()}/{tail.replace(os.sep, '/').lstrip('/')}"

    @staticmethod
    def _path_to_windows(path: str | None) -> str | None:
        text = str(path or "").strip()
        if not text:
            return None
        normalized_posix = text.replace("\\", "/")
        wsl_match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", normalized_posix)
        if wsl_match:
            drive = wsl_match.group(1).upper()
            tail = wsl_match.group(2).replace("/", "\\")
            return f"{drive}:\\{tail}"
        return os.path.normpath(text)

    def _resolve_rdc_dem_source(self, stack_manifest: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        explicit_candidates = [
            ("PYINT_PREPARED_DEM_PATH", settings.PYINT_PREPARED_DEM_PATH),
            ("ISCE2_DEM_PATH", settings.ISCE2_DEM_PATH),
            ("IDL_DINSAR_DEM_BASE_FILE", settings.IDL_DINSAR_DEM_BASE_FILE),
        ]
        for label, raw_path in explicit_candidates:
            for candidate in self._gamma_dem_candidate_paths(raw_path):
                source = self._build_dem_source_record(candidate, source_label=label, stack_manifest=stack_manifest)
                if source:
                    return source
            if str(raw_path or "").strip():
                errors.append(f"{label} does not point to an existing Gamma DEM + .par pair: {raw_path}")

        cache_roots = [
            Path(settings.PYINT_DEM_ROOT),
            Path(settings.BACKEND_DIR) / "runtime" / "pyint_dem",
            Path(settings.BACKEND_DIR) / "runtime" / "pyint_dem_cache",
        ]
        stack_bbox = self._stack_bbox_union(stack_manifest)
        cached_sources: list[dict[str, Any]] = []
        for root in cache_roots:
            if not root.is_dir():
                continue
            for dem_path in root.glob("**/*.dem"):
                source = self._build_dem_source_record(
                    dem_path,
                    source_label=f"runtime_cache:{root.name}",
                    stack_manifest=stack_manifest,
                )
                if not source:
                    continue
                coverage = source.get("coverage") or {}
                if stack_bbox and not (
                    self._bbox_contains(coverage, stack_bbox, margin_degrees=0.05)
                    or self._bbox_contains_point(coverage, self._stack_center(stack_manifest), margin_degrees=0.05)
                ):
                    continue
                cached_sources.append(source)

        if cached_sources:
            cached_sources.sort(
                key=lambda item: self._dem_source_sort_key(item, stack_manifest)
            )
            selected = cached_sources[0]
            selected["selection_note"] = "Selected existing PyINT Gamma DEM cache covering the SBAS stack extent."
            return selected

        detail = "; ".join(errors) if errors else "no runtime Gamma DEM cache covers the selected stack"
        raise FileNotFoundError(
            "No usable Gamma DEM source was found for RDC DEM generation. "
            "Configure PYINT_PREPARED_DEM_PATH to a .dem file with .dem.par, "
            "or generate a PyINT Gamma DEM cache for this LT1 stack. "
            f"Details: {detail}"
        )

    def _resolve_expert_dem_import_source(self, stack_manifest: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        explicit_candidates = [
            ("GAMMA_SBAS_DEM_PATH", settings.GAMMA_SBAS_DEM_PATH),
            ("IDL_DINSAR_DEM_BASE_FILE", settings.IDL_DINSAR_DEM_BASE_FILE),
            ("ISCE2_DEM_PATH", settings.ISCE2_DEM_PATH),
            ("PYINT_PREPARED_DEM_PATH", settings.PYINT_PREPARED_DEM_PATH),
            ("TIMESERIES_DEM_PATH", settings.TIMESERIES_DEM_PATH),
        ]
        stack_bbox = self._stack_bbox_union(stack_manifest)
        if not stack_bbox:
            raise ValueError("Expert Gamma SBAS DEM selection requires auditable scene bbox coverage.")
        for label, raw_path in explicit_candidates:
            for candidate in self._expert_dem_import_candidate_paths(raw_path):
                if not candidate.is_file():
                    continue
                coverage = self._infer_dem_import_source_coverage(candidate)
                if stack_bbox and coverage.get("min_lon") is None:
                    errors.append(f"{label} coverage is not auditable for full stack bbox: {candidate}")
                    continue
                covers_stack_bbox = self._bbox_contains(coverage, stack_bbox, margin_degrees=0.05) if stack_bbox and coverage else None
                if stack_bbox and coverage and not covers_stack_bbox:
                    errors.append(f"{label} does not cover full stack bbox: {candidate}")
                    continue
                return {
                    "source_label": label,
                    "source_type": "dem_import_source",
                    "windows_path": str(candidate),
                    "wsl_path": self._windows_path_to_wsl_mount(str(candidate)),
                    "coverage": coverage,
                    "covers_stack_bbox": covers_stack_bbox,
                    "stack_bbox": stack_bbox,
                    "selection_note": "Selected DEM source for expert dem_import; runtime Gamma DEM cache is not accepted.",
                }
            if str(raw_path or "").strip():
                errors.append(f"{label} has no readable DEM import source: {raw_path}")
        detail = "; ".join(errors) if errors else "no configured DEM source"
        raise FileNotFoundError(
            "No usable DEM source was found for expert Gamma SBAS dem_import. "
            "Configure GAMMA_SBAS_DEM_PATH, IDL_DINSAR_DEM_BASE_FILE, ISCE2_DEM_PATH, PYINT_PREPARED_DEM_PATH, "
            "or TIMESERIES_DEM_PATH to a readable source raster covering the full stack bbox. "
            f"Details: {detail}"
        )

    def _materialize_expert_dem_import_source(self, run_dir: Path, dem_source: dict[str, Any]) -> dict[str, Any]:
        stack_bbox = self._normalize_bbox(dem_source.get("stack_bbox"))
        coverage = self._normalize_bbox(dem_source.get("coverage"))
        raw_path = str(dem_source.get("windows_path") or dem_source.get("wsl_path") or "").strip()
        source_path = Path(self._path_to_windows(raw_path) or raw_path)
        if not stack_bbox or not coverage or not source_path.is_file():
            return dem_source
        raster_suffix = source_path.suffix.lower()
        direct_geotiff_source = raster_suffix in {".tif", ".tiff"}
        if raster_suffix not in {"", ".tif", ".tiff", ".img", ".wgs84", ".vrt"}:
            return dem_source

        source_area = self._bbox_area(coverage)
        stack_area = self._bbox_area(stack_bbox)
        if direct_geotiff_source and source_area <= max(stack_area * 8.0, 2.0):
            return dem_source

        margin = 0.25
        clip_bbox = {
            "min_lon": max(float(coverage["min_lon"]), float(stack_bbox["min_lon"]) - margin),
            "min_lat": max(float(coverage["min_lat"]), float(stack_bbox["min_lat"]) - margin),
            "max_lon": min(float(coverage["max_lon"]), float(stack_bbox["max_lon"]) + margin),
            "max_lat": min(float(coverage["max_lat"]), float(stack_bbox["max_lat"]) + margin),
        }
        if not self._bbox_contains(clip_bbox, stack_bbox):
            raise ValueError(f"DEM crop bbox does not cover stack bbox: crop={clip_bbox}, stack={stack_bbox}")

        clip_path = run_dir / "dem" / "expert_dem_import_clip.tif"
        clip_meta_path = run_dir / "state" / "dem_import_clip.json"
        if clip_path.is_file():
            clip_coverage = self._dem_coverage_from_raster(clip_path)
            if clip_coverage.get("driver") == "GTiff" and self._bbox_contains(clip_coverage, stack_bbox, margin_degrees=0.02):
                clipped = {
                    **dem_source,
                    "source_type": "dem_import_source_clip",
                    "original_windows_path": str(source_path),
                    "original_wsl_path": self._windows_path_to_wsl_mount(str(source_path)),
                    "windows_path": str(clip_path),
                    "wsl_path": self._windows_path_to_wsl_mount(str(clip_path)),
                    "coverage": clip_coverage,
                    "clip_bbox": clip_bbox,
                    "selection_note": "Selected run-local DEM clip for expert dem_import to avoid full-raster Gamma memory allocation.",
                }
                self._write_json(clip_meta_path, clipped)
                return clipped

        self._crop_raster_dem_to_bbox(source_path, clip_path, clip_bbox)
        clip_coverage = self._dem_coverage_from_raster(clip_path)
        if not self._bbox_contains(clip_coverage, stack_bbox, margin_degrees=0.02):
            raise ValueError(
                "Run-local DEM clip does not cover stack bbox after raster crop: "
                f"clip_coverage={clip_coverage}, stack={stack_bbox}"
            )
        clipped = {
            **dem_source,
            "source_type": "dem_import_source_clip",
            "original_windows_path": str(source_path),
            "original_wsl_path": self._windows_path_to_wsl_mount(str(source_path)),
            "windows_path": str(clip_path),
            "wsl_path": self._windows_path_to_wsl_mount(str(clip_path)),
            "coverage": clip_coverage,
            "clip_bbox": clip_bbox,
            "source_area_sq_deg": source_area,
            "stack_area_sq_deg": stack_area,
            "selection_note": "Selected run-local DEM clip for expert dem_import to avoid full-raster Gamma memory allocation.",
        }
        self._write_json(clip_meta_path, clipped)
        return clipped

    @staticmethod
    def _crop_raster_dem_to_bbox(source_path: Path, clip_path: Path, bbox_lonlat: dict[str, float]) -> None:
        try:
            import rasterio  # type: ignore
            from rasterio.warp import transform_bounds  # type: ignore
            from rasterio.windows import Window, from_bounds  # type: ignore
        except Exception as exc:
            raise RuntimeError("rasterio is required to crop large Gamma SBAS DEM import sources") from exc

        clip_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = clip_path.with_suffix(f"{clip_path.suffix}.tmp")
        if tmp_path.exists():
            tmp_path.unlink()
        with rasterio.open(source_path) as src:
            if not src.crs:
                raise ValueError(f"DEM raster has no CRS and cannot be cropped by stack bbox: {source_path}")
            left = float(bbox_lonlat["min_lon"])
            bottom = float(bbox_lonlat["min_lat"])
            right = float(bbox_lonlat["max_lon"])
            top = float(bbox_lonlat["max_lat"])
            if not getattr(src.crs, "is_geographic", False):
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326",
                    src.crs,
                    left,
                    bottom,
                    right,
                    top,
                    densify_pts=21,
                )
            raw_window = from_bounds(left, bottom, right, top, transform=src.transform)
            col_off = max(0, int(math.floor(raw_window.col_off)))
            row_off = max(0, int(math.floor(raw_window.row_off)))
            col_end = min(src.width, int(math.ceil(raw_window.col_off + raw_window.width)))
            row_end = min(src.height, int(math.ceil(raw_window.row_off + raw_window.height)))
            width = col_end - col_off
            height = row_end - row_off
            if width <= 0 or height <= 0:
                raise ValueError(f"DEM crop window is empty for bbox {bbox_lonlat}: {source_path}")
            window = Window(col_off, row_off, width, height)
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                width=width,
                height=height,
                transform=src.window_transform(window),
                BIGTIFF="IF_SAFER",
            )
            with rasterio.open(tmp_path, "w", **profile) as dst:
                chunk_lines = 2048
                for local_row in range(0, height, chunk_lines):
                    rows = min(chunk_lines, height - local_row)
                    read_window = Window(col_off, row_off + local_row, width, rows)
                    write_window = Window(0, local_row, width, rows)
                    dst.write(src.read(window=read_window), window=write_window)
        tmp_path.replace(clip_path)

    def _expert_dem_import_candidate_paths(self, raw_path: str | None) -> list[Path]:
        text = str(raw_path or "").strip()
        if not text:
            return []
        win_text = self._path_to_windows(text) or text
        base = Path(win_text)
        suffixes = {".tif", ".tiff", ".dem", ".hgt", ".img", ".wgs84"}
        candidates: list[Path] = []
        if base.is_dir():
            for pattern in ("*.tif", "*.tiff", "*.dem", "*.hgt", "*.img", "*.wgs84"):
                candidates.extend(sorted(base.glob(pattern)))
        else:
            candidates.append(base)
            if base.suffix.lower() not in suffixes:
                candidates.extend(Path(f"{win_text}{suffix}") for suffix in (".tif", ".tiff", ".dem", ".wgs84"))
        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _infer_dem_import_source_coverage(self, path: Path) -> dict[str, Any]:
        par_candidates = [
            Path(f"{path}.par"),
            path.with_suffix(f"{path.suffix}.par") if path.suffix else Path(f"{path}.par"),
            path.with_suffix(".dem.par"),
            path.with_suffix(".par"),
        ]
        for par_path in par_candidates:
            params = self._parse_gamma_params(par_path)
            coverage = self._dem_coverage_from_params(params)
            if coverage.get("min_lon") is not None:
                coverage["coverage_source"] = str(par_path)
                return coverage
        raster_coverage = self._dem_coverage_from_raster(path)
        if raster_coverage.get("min_lon") is not None:
            return raster_coverage
        return {"coverage_source": "unavailable"}

    def _dem_coverage_from_raster(self, path: Path) -> dict[str, Any]:
        try:
            import rasterio  # type: ignore
            from rasterio.warp import transform_bounds  # type: ignore

            with rasterio.open(path) as dataset:
                bounds = dataset.bounds
                crs = dataset.crs
                if crs:
                    if crs.to_epsg() == 4326 or getattr(crs, "is_geographic", False):
                        min_lon, min_lat, max_lon, max_lat = bounds.left, bounds.bottom, bounds.right, bounds.top
                    else:
                        min_lon, min_lat, max_lon, max_lat = transform_bounds(
                            crs,
                            "EPSG:4326",
                            bounds.left,
                            bounds.bottom,
                            bounds.right,
                            bounds.top,
                            densify_pts=21,
                        )
                else:
                    min_lon, min_lat, max_lon, max_lat = bounds.left, bounds.bottom, bounds.right, bounds.top
                return {
                    "coverage_source": f"rasterio:{path}",
                    "min_lon": float(min_lon),
                    "min_lat": float(min_lat),
                    "max_lon": float(max_lon),
                    "max_lat": float(max_lat),
                    "width": int(dataset.width),
                    "nlines": int(dataset.height),
                    "driver": str(getattr(dataset, "driver", "") or ""),
                    "crs": str(crs) if crs else None,
                    "area_sq_deg": max(0.0, (float(max_lon) - float(min_lon)) * (float(max_lat) - float(min_lat))),
                }
        except Exception:
            pass

        try:
            from osgeo import gdal, osr  # type: ignore

            dataset = gdal.Open(str(path))
            if dataset is None:
                return {"coverage_source": "unavailable"}
            transform = dataset.GetGeoTransform(can_return_null=True)
            if not transform:
                return {"coverage_source": "unavailable"}
            width = int(dataset.RasterXSize)
            height = int(dataset.RasterYSize)
            corners = [
                (0, 0),
                (width, 0),
                (width, height),
                (0, height),
            ]
            points = [
                (
                    transform[0] + col * transform[1] + row * transform[2],
                    transform[3] + col * transform[4] + row * transform[5],
                )
                for col, row in corners
            ]
            projection = dataset.GetProjection()
            if projection:
                src = osr.SpatialReference()
                src.ImportFromWkt(projection)
                dst = osr.SpatialReference()
                dst.ImportFromEPSG(4326)
                transformer = osr.CoordinateTransformation(src, dst)
                transformed = []
                for x, y in points:
                    lon, lat, *_ = transformer.TransformPoint(float(x), float(y))
                    transformed.append((lon, lat))
                points = transformed
            lons = [float(item[0]) for item in points]
            lats = [float(item[1]) for item in points]
            min_lon, max_lon = min(lons), max(lons)
            min_lat, max_lat = min(lats), max(lats)
            return {
                "coverage_source": f"gdal:{path}",
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
                "width": width,
                "nlines": height,
                "crs": projection or None,
                "area_sq_deg": max(0.0, (max_lon - min_lon) * (max_lat - min_lat)),
            }
        except Exception:
            return {"coverage_source": "unavailable"}

    def _gamma_dem_candidate_paths(self, raw_path: str | None) -> list[Path]:
        text = str(raw_path or "").strip()
        if not text:
            return []
        win_text = self._path_to_windows(text) or text
        base = Path(win_text)
        candidates = [base]
        if base.suffix.lower() != ".dem":
            candidates.append(Path(f"{win_text}.dem"))
        if base.is_dir():
            candidates.extend(sorted(base.glob("*.dem")))
        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _build_dem_source_record(
        self,
        dem_path: Path,
        *,
        source_label: str,
        stack_manifest: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not dem_path.is_file():
            return None
        par_path = Path(f"{dem_path}.par")
        if not par_path.is_file():
            return None
        params = self._parse_gamma_params(par_path)
        width = self._as_int(params.get("width"))
        nlines = self._as_int(params.get("nlines"))
        coverage = self._dem_coverage_from_params(params)
        stack_bbox = self._stack_bbox_union(stack_manifest)
        return {
            "source_label": source_label,
            "windows_path": str(dem_path),
            "windows_par_path": str(par_path),
            "wsl_path": self._windows_path_to_wsl_mount(str(dem_path)),
            "wsl_par_path": self._windows_path_to_wsl_mount(str(par_path)),
            "data_format": params.get("data_format"),
            "width": width,
            "nlines": nlines,
            "coverage": coverage,
            "covers_stack_bbox": self._bbox_contains(coverage, stack_bbox, margin_degrees=0.05) if stack_bbox else None,
            "covers_stack_center": self._bbox_contains_point(coverage, self._stack_center(stack_manifest), margin_degrees=0.05),
            "stack_bbox": stack_bbox,
        }

    def _dem_source_sort_key(self, source: dict[str, Any], stack_manifest: dict[str, Any]) -> tuple[Any, ...]:
        path_text = str(source.get("windows_path") or source.get("wsl_path") or "").replace("\\", "/").lower()
        stack_satellite = str((stack_manifest.get("stack") or {}).get("satellite") or "").lower()
        same_family = bool(stack_satellite.startswith("lt1") and "/lt1_" in f"/{path_text}")
        return (
            0 if source.get("covers_stack_bbox") else 1,
            0 if same_family else 1,
            self._dem_center_distance(source.get("coverage") or {}, self._stack_center(stack_manifest)),
            str(source.get("windows_path") or source.get("wsl_path") or ""),
        )

    @staticmethod
    def _dem_center_distance(coverage: dict[str, Any], point: dict[str, float] | None) -> float:
        if not coverage or not point:
            return float("inf")
        try:
            lon = float(point["lon"])
            lat = float(point["lat"])
            center_lon = (float(coverage["min_lon"]) + float(coverage["max_lon"])) / 2
            center_lat = (float(coverage["min_lat"]) + float(coverage["max_lat"])) / 2
            return ((center_lon - lon) ** 2 + (center_lat - lat) ** 2) ** 0.5
        except (KeyError, TypeError, ValueError):
            return float("inf")

    def _find_reference_rmli_paths(self, run_dir: Path, reference_date: str) -> tuple[Path, Path]:
        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        rmli_tab = common_dir / "RMLI_tab"
        if rmli_tab.is_file():
            for line in rmli_tab.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                if Path(parts[0]).name == f"{reference_date}.mli":
                    return Path(self._path_to_windows(parts[0]) or parts[0]), Path(self._path_to_windows(parts[1]) or parts[1])
        return (
            run_dir / "work" / "gamma" / "mli" / f"{reference_date}.mli",
            run_dir / "work" / "gamma" / "mli" / f"{reference_date}.mli.par",
        )

    def _select_ipta_mb_reference_mli(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        rmli_tab: Path,
    ) -> tuple[Path, Path]:
        reference_dt = None
        try:
            reference_dt = datetime.strptime(reference_date, "%Y%m%d")
        except ValueError:
            pass

        candidates: list[tuple[tuple[Any, ...], Path, Path]] = []
        if rmli_tab.is_file():
            for index, line in enumerate(rmli_tab.read_text(encoding="utf-8", errors="ignore").splitlines()):
                parts = line.split()
                if len(parts) < 2:
                    continue
                mli = Path(self._path_to_windows(parts[0]) or parts[0])
                mli_par = Path(self._path_to_windows(parts[1]) or parts[1])
                date = mli.stem
                if date == reference_date:
                    continue
                if reference_dt is not None:
                    try:
                        delta_days = abs((datetime.strptime(date, "%Y%m%d") - reference_dt).days)
                    except ValueError:
                        delta_days = 999999
                else:
                    delta_days = index
                candidates.append(((delta_days, index), mli, mli_par))

        if candidates:
            _, mli, mli_par = sorted(candidates, key=lambda item: item[0])[0]
            return mli, mli_par
        return self._find_reference_rmli_paths(run_dir, reference_date)

    def _select_ipta_reference_region(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        rlks: int,
        reference_window: int,
        geom_ref_mli_par: Path,
    ) -> dict[str, Any]:
        params = self._parse_gamma_params(geom_ref_mli_par)
        width = self._as_int(params.get("range_samples"))
        lines = self._as_int(params.get("azimuth_lines"))
        if not width or not lines:
            raise ValueError(f"cannot parse reference geometry from {geom_ref_mli_par}")

        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        diff_tab = common_dir / "DIFF_tab"
        pair_paths = [
            Path(self._path_to_windows(row) or row)
            for row in self._read_text_rows(diff_tab)
        ]
        pair_paths = [path for path in pair_paths if path.is_file()]
        if not pair_paths:
            raise FileNotFoundError(f"DIFF_tab has no readable unwrapped interferograms: {diff_tab}")

        half = max(1, reference_window // 2)
        window = half * 2
        search_step = max(8, min(64, window * 2))
        center_x = width // 2
        center_y = lines // 2
        best: dict[str, Any] | None = None
        for y in range(half, max(half + 1, lines - half), search_step):
            for x in range(half, max(half + 1, width - half), search_step):
                metrics = self._score_ipta_reference_region(
                    pair_paths,
                    width=width,
                    lines=lines,
                    x=x,
                    y=y,
                    half=half,
                )
                score = (
                    metrics["min_valid_pixel_count"],
                    metrics["median_mean_coherence"],
                    metrics["total_valid_pixel_count"],
                    -abs(x - center_x) - abs(y - center_y),
                )
                if best is None or score > best["score"]:
                    best = {
                        **metrics,
                        "score": score,
                        "range_pixel": x,
                        "azimuth_line": y,
                    }

        if best is None:
            raise ValueError("could not select an IPTA reference region")
        return {
            "strategy": "auto_valid_unwrapped_high_coherence_window",
            "range_pixel": int(best["range_pixel"]),
            "azimuth_line": int(best["azimuth_line"]),
            "window_width": window,
            "window_height": window,
            "search_step": search_step,
            "pair_count": len(pair_paths),
            "min_valid_pixel_count": int(best["min_valid_pixel_count"]),
            "total_valid_pixel_count": int(best["total_valid_pixel_count"]),
            "median_mean_coherence": float(best["median_mean_coherence"]),
            "mean_coherence_by_pair": best["mean_coherence_by_pair"],
            "valid_pixel_count_by_pair": best["valid_pixel_count_by_pair"],
        }

    @staticmethod
    def _normalize_ipta_mb_mode(value: Any) -> int:
        try:
            mode = int(value)
        except (TypeError, ValueError):
            mode = DEFAULT_IPTA_MB_MODE
        if mode not in IPTA_MB_MODE_DESCRIPTIONS:
            mode = DEFAULT_IPTA_MB_MODE
        return mode

    def _resolve_radar_wavelength_m(self, *parameter_paths: Path) -> float:
        for path in parameter_paths:
            params = self._parse_gamma_params(path)
            radar_frequency = self._as_float(params.get("radar_frequency"))
            if radar_frequency and radar_frequency > 0:
                return 299792458.0 / radar_frequency
        return 0.23793052222222222

    def _score_ipta_reference_region(
        self,
        pair_paths: list[Path],
        *,
        width: int,
        lines: int,
        x: int,
        y: int,
        half: int,
    ) -> dict[str, Any]:
        y0 = max(0, y - half)
        y1 = min(lines, y + half)
        x0 = max(0, x - half)
        x1 = min(width, x + half)
        valid_counts: list[int] = []
        coherence_means: list[float] = []
        for unw_path in pair_paths:
            cor_path = unw_path.with_name(unw_path.name.replace(".diff_filt.unw", ".diff_filt.cor"))
            unw = self._read_gamma_float32_window(unw_path, width=width, lines=lines, x0=x0, x1=x1, y0=y0, y1=y1)
            cor = self._read_gamma_float32_window(cor_path, width=width, lines=lines, x0=x0, x1=x1, y0=y0, y1=y1)
            valid = [value for value in unw if math.isfinite(value) and value != 0.0]
            finite_cor = [value for value in cor if math.isfinite(value)]
            valid_counts.append(len(valid))
            coherence_means.append(sum(finite_cor) / len(finite_cor) if finite_cor else 0.0)
        sorted_coh = sorted(coherence_means)
        if sorted_coh:
            mid = len(sorted_coh) // 2
            median_coh = sorted_coh[mid] if len(sorted_coh) % 2 else (sorted_coh[mid - 1] + sorted_coh[mid]) / 2
        else:
            median_coh = 0.0
        return {
            "min_valid_pixel_count": min(valid_counts) if valid_counts else 0,
            "total_valid_pixel_count": sum(valid_counts),
            "median_mean_coherence": median_coh,
            "mean_coherence_by_pair": coherence_means,
            "valid_pixel_count_by_pair": valid_counts,
        }

    @staticmethod
    def _read_gamma_float32_window(
        path: Path,
        *,
        width: int,
        lines: int,
        x0: int,
        x1: int,
        y0: int,
        y1: int,
    ) -> list[float]:
        if not path.is_file():
            return []
        values: list[float] = []
        row_bytes = width * 4
        count = max(0, x1 - x0)
        with path.open("rb") as fh:
            for y in range(y0, y1):
                if y < 0 or y >= lines:
                    continue
                fh.seek(y * row_bytes + x0 * 4)
                chunk = fh.read(count * 4)
                if len(chunk) != count * 4:
                    continue
                values.extend(struct.unpack(f">{count}f", chunk))
        return values

    @staticmethod
    def _gamma_float32_stats(path: Path, *, width: int, lines: int) -> dict[str, Any]:
        if not path.is_file() or not width or not lines:
            return {"exists": path.is_file(), "valid_count": 0}
        try:
            import numpy as np
        except Exception as exc:
            return {"exists": True, "error": f"numpy unavailable: {exc}"}
        expected = width * lines
        try:
            data = np.fromfile(path, dtype=">f4", count=expected)
        except Exception as exc:
            return {"exists": True, "error": str(exc)}
        finite = data[np.isfinite(data)]
        nonzero = finite[finite != 0.0]
        sample = nonzero if nonzero.size else finite
        if sample.size == 0:
            return {
                "exists": True,
                "pixel_count": int(data.size),
                "valid_count": 0,
                "nonzero_count": 0,
            }
        percentiles = np.percentile(sample, [1, 5, 50, 95, 99])
        return {
            "exists": True,
            "pixel_count": int(data.size),
            "expected_pixel_count": int(expected),
            "valid_count": int(finite.size),
            "nonzero_count": int(nonzero.size),
            "min": float(np.nanmin(sample)),
            "p01": float(percentiles[0]),
            "p05": float(percentiles[1]),
            "median": float(percentiles[2]),
            "p95": float(percentiles[3]),
            "p99": float(percentiles[4]),
            "max": float(np.nanmax(sample)),
            "mean": float(np.nanmean(sample)),
            "std": float(np.nanstd(sample)),
        }

    @staticmethod
    def _parse_gamma_params(path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        params: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().split()[0] if value.strip() else ""
            if key:
                params[key] = value
        return params

    def _dem_coverage_from_params(self, params: dict[str, str]) -> dict[str, Any]:
        width = self._as_int(params.get("width"))
        nlines = self._as_int(params.get("nlines"))
        corner_lon = self._as_float(params.get("corner_lon"))
        corner_lat = self._as_float(params.get("corner_lat"))
        post_lon = self._as_float(params.get("post_lon"))
        post_lat = self._as_float(params.get("post_lat"))
        coverage: dict[str, Any] = {
            "width": width,
            "nlines": nlines,
            "corner_lon": corner_lon,
            "corner_lat": corner_lat,
            "post_lon": post_lon,
            "post_lat": post_lat,
        }
        if None in {width, nlines, corner_lon, corner_lat, post_lon, post_lat}:
            return coverage
        east = float(corner_lon) + float(post_lon) * int(width)
        south = float(corner_lat) + float(post_lat) * int(nlines)
        min_lon = min(float(corner_lon), east)
        max_lon = max(float(corner_lon), east)
        min_lat = min(float(corner_lat), south)
        max_lat = max(float(corner_lat), south)
        coverage.update(
            {
                "min_lon": min_lon,
                "max_lon": max_lon,
                "min_lat": min_lat,
                "max_lat": max_lat,
                "area_sq_deg": max(0.0, (max_lon - min_lon) * (max_lat - min_lat)),
            }
        )
        return coverage

    @staticmethod
    def _bbox_contains(
        outer: dict[str, Any] | None,
        inner: dict[str, Any] | None,
        *,
        margin_degrees: float = 0.0,
    ) -> bool:
        if not outer or not inner:
            return False
        try:
            return (
                float(outer["min_lon"]) <= float(inner["min_lon"]) + margin_degrees
                and float(outer["max_lon"]) >= float(inner["max_lon"]) - margin_degrees
                and float(outer["min_lat"]) <= float(inner["min_lat"]) + margin_degrees
                and float(outer["max_lat"]) >= float(inner["max_lat"]) - margin_degrees
            )
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _bbox_contains_point(
        outer: dict[str, Any] | None,
        point: dict[str, float] | None,
        *,
        margin_degrees: float = 0.0,
    ) -> bool:
        if not outer or not point:
            return False
        try:
            lon = float(point["lon"])
            lat = float(point["lat"])
            return (
                float(outer["min_lon"]) - margin_degrees <= lon <= float(outer["max_lon"]) + margin_degrees
                and float(outer["min_lat"]) - margin_degrees <= lat <= float(outer["max_lat"]) + margin_degrees
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _stack_center(self, stack_manifest: dict[str, Any]) -> dict[str, float] | None:
        scenes = stack_manifest.get("scenes") or []
        lons = [self._as_float(scene.get("center_lon")) for scene in scenes]
        lats = [self._as_float(scene.get("center_lat")) for scene in scenes]
        lons = [value for value in lons if value is not None]
        lats = [value for value in lats if value is not None]
        if lons and lats:
            return {"lon": sum(lons) / len(lons), "lat": sum(lats) / len(lats)}
        bbox = self._stack_bbox_union(stack_manifest)
        if not bbox:
            return None
        return {
            "lon": (bbox["min_lon"] + bbox["max_lon"]) / 2,
            "lat": (bbox["min_lat"] + bbox["max_lat"]) / 2,
        }

    def _stack_bbox_union(self, stack_manifest: dict[str, Any]) -> dict[str, float] | None:
        boxes = [
            scene.get("bbox") for scene in (stack_manifest.get("scenes") or [])
            if isinstance(scene.get("bbox"), dict)
        ]
        if not boxes:
            return None
        try:
            return {
                "min_lon": min(float(item["min_lon"]) for item in boxes),
                "min_lat": min(float(item["min_lat"]) for item in boxes),
                "max_lon": max(float(item["max_lon"]) for item in boxes),
                "max_lat": max(float(item["max_lat"]) for item in boxes),
            }
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_bbox(value: Any) -> dict[str, float] | None:
        if not isinstance(value, dict):
            return None
        try:
            min_lon = float(value["min_lon"])
            min_lat = float(value["min_lat"])
            max_lon = float(value["max_lon"])
            max_lat = float(value["max_lat"])
        except (KeyError, TypeError, ValueError):
            return None
        if min_lon >= max_lon or min_lat >= max_lat:
            return None
        return {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        }

    @classmethod
    def _bbox_to_geojson_feature(cls, bbox: dict[str, Any] | None, *, properties: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized = cls._normalize_bbox(bbox)
        if not normalized:
            return None
        min_lon = normalized["min_lon"]
        min_lat = normalized["min_lat"]
        max_lon = normalized["max_lon"]
        max_lat = normalized["max_lat"]
        return {
            "type": "Feature",
            "properties": properties or {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat],
                ]],
            },
        }

    @staticmethod
    def _point_to_geojson_feature(point: dict[str, Any] | None, *, properties: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not isinstance(point, dict):
            return None
        try:
            lon = float(point["lon"])
            lat = float(point["lat"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "type": "Feature",
            "properties": properties or {},
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
        }

    def _build_stack_geographic_coverage(self, stack_manifest: dict[str, Any]) -> dict[str, Any]:
        scenes = stack_manifest.get("scenes") or []
        usable_scenes = [
            scene for scene in scenes
            if isinstance(scene, dict) and isinstance(scene.get("bbox"), dict)
        ]
        bbox_union = self._stack_bbox_union(stack_manifest)
        bbox_intersection = self._bbox_intersection([scene.get("bbox") for scene in usable_scenes])
        center = self._stack_center(stack_manifest)
        union_feature = self._bbox_to_geojson_feature(
            bbox_union,
            properties={
                "role": "stack_bbox_union",
                "source": "lt1_scene_metadata",
                "scene_count": len(usable_scenes),
            },
        )
        intersection_feature = self._bbox_to_geojson_feature(
            bbox_intersection,
            properties={
                "role": "stack_bbox_intersection",
                "source": "lt1_scene_metadata",
                "scene_count": len(usable_scenes),
            },
        )
        center_feature = self._point_to_geojson_feature(
            center,
            properties={"role": "stack_center", "source": "scene_centers_or_bbox"},
        )
        scene_features: list[dict[str, Any]] = []
        for scene in usable_scenes:
            feature = self._bbox_to_geojson_feature(
                scene.get("bbox"),
                properties={
                    "role": "scene_bbox",
                    "scene_name": scene.get("scene_name"),
                    "date": scene.get("date"),
                    "satellite": scene.get("satellite"),
                    "relative_orbit": scene.get("relative_orbit"),
                },
            )
            if feature:
                scene_features.append(feature)
        overview_features = [
            item for item in [union_feature, intersection_feature, center_feature]
            if item
        ]
        return {
            "schema": "insar.sbas-geographic-coverage/v1",
            "crs": "EPSG:4326",
            "source": "lt1_scene_metadata",
            "bbox": bbox_union,
            "bbox_intersection": bbox_intersection,
            "center": center,
            "admin_region": lookup_admin_region_for_point(
                (center or {}).get("lon"),
                (center or {}).get("lat"),
            ),
            "scene_bbox_count": len(scene_features),
            "geojson": {
                "type": "FeatureCollection",
                "features": overview_features,
            },
            "scene_footprints_geojson": {
                "type": "FeatureCollection",
                "features": scene_features,
            },
        }

    def _build_run_geographic_coverage(self, run_dir: Path, run_manifest: dict[str, Any]) -> dict[str, Any]:
        stack_manifest = self._read_optional_json(run_dir / "stack_manifest.json")
        if not stack_manifest:
            stack_manifest_path = Path(str(run_manifest.get("stack_manifest_path") or ""))
            if stack_manifest_path.is_file():
                stack_manifest = self._read_optional_json(stack_manifest_path)
        stack_manifest = stack_manifest or {}
        coverage = self._build_stack_geographic_coverage(stack_manifest)
        rdc_dem = run_manifest.get("rdc_dem") or {}
        rdc_dem_summary = (
            (rdc_dem.get("summary") if isinstance(rdc_dem, dict) else None)
            or self._read_optional_json(run_dir / "rdc_dem_summary.json")
            or {}
        )
        dem_source = rdc_dem_summary.get("dem_source") or (rdc_dem.get("dem_source") if isinstance(rdc_dem, dict) else None) or {}
        dem_coverage = self._normalize_bbox(dem_source.get("coverage")) if isinstance(dem_source, dict) else None
        monitor_summary = (
            (run_manifest.get("monitor_point_products") or {}).get("summary")
            or self._read_optional_json(run_dir / "monitor_points_summary.json")
            or {}
        )
        monitor_points: list[dict[str, Any]] = []
        for item in monitor_summary.get("monitor_outputs") or []:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            lonlat = metadata.get("approx_lonlat") or {}
            try:
                lon = float(lonlat["lon"])
                lat = float(lonlat["lat"])
            except (KeyError, TypeError, ValueError):
                continue
            monitor_points.append(
                {
                    "point_id": item.get("point_id") or metadata.get("point_id"),
                    "lon": lon,
                    "lat": lat,
                    "selection": metadata.get("selection"),
                    "los_rate_toward_mm_per_year": metadata.get("los_rate_toward_mm_per_year"),
                    "los_sigma_mm_per_year": metadata.get("los_sigma_mm_per_year"),
                    "source": "monitor_points_summary",
                }
            )
        if not monitor_points:
            for item in monitor_summary.get("monitor_points") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    lon = float(item["lon"])
                    lat = float(item["lat"])
                except (KeyError, TypeError, ValueError):
                    continue
                monitor_points.append(
                    {
                        "point_id": item.get("point_id"),
                        "lon": lon,
                        "lat": lat,
                        "selection": item.get("selection"),
                        "los_rate_toward_mm_per_year": item.get("los_rate_toward_mm_per_year"),
                        "los_sigma_mm_per_year": item.get("los_sigma_mm_per_year"),
                        "source": "monitor_points_summary",
                    }
                )
        monitor_features = [
            feature for feature in (
                self._point_to_geojson_feature(
                    {"lon": point["lon"], "lat": point["lat"]},
                    properties={
                        "role": "monitor_point",
                        "point_id": point.get("point_id"),
                        "selection": point.get("selection"),
                        "los_rate_toward_mm_per_year": point.get("los_rate_toward_mm_per_year"),
                        "los_sigma_mm_per_year": point.get("los_sigma_mm_per_year"),
                    },
                )
                for point in monitor_points
            )
            if feature
        ]
        dem_feature = self._bbox_to_geojson_feature(
            dem_coverage,
            properties={
                "role": "dem_coverage",
                "source": "rdc_dem_summary",
                "covers_stack_bbox": dem_source.get("covers_stack_bbox"),
                "covers_stack_center": dem_source.get("covers_stack_center"),
            },
        )
        features = list((coverage.get("geojson") or {}).get("features") or [])
        if dem_feature:
            features.append(dem_feature)
        features.extend(monitor_features)
        coverage.update(
            {
                "source": "run_stack_manifest",
                "run_id": run_manifest.get("run_id") or run_dir.name,
                "stack_id": run_manifest.get("stack_id") or stack_manifest.get("stack_id"),
                "stack": stack_manifest.get("stack") or run_manifest.get("stack") or {},
                "date_start": min(self._stack_dates(stack_manifest), default=None),
                "date_end": max(self._stack_dates(stack_manifest), default=None),
                "dem_coverage": dem_coverage,
                "dem_covers_stack_bbox": dem_source.get("covers_stack_bbox"),
                "dem_covers_stack_center": dem_source.get("covers_stack_center"),
                "monitor_points": monitor_points,
                "geojson": {
                    "type": "FeatureCollection",
                    "features": features,
                },
            }
        )
        return coverage

    @staticmethod
    def _file_record(path: Path) -> dict[str, Any]:
        exists = path.is_file()
        return {
            "path": str(path),
            "exists": exists,
            "size_bytes": path.stat().st_size if exists else 0,
        }

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _find_lt1_orbit(orbit_roots: list[Path], satellite: str, date: str) -> Path | None:
        if not satellite or not date:
            return None
        name = f"{satellite}_GpsData_GAS_C_{date}.txt"
        for root in orbit_roots:
            candidates = [
                root / satellite / name,
                root / name,
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
        return None

    @staticmethod
    def _stack_group_key(scene: dict[str, Any]) -> str:
        parts = [
            scene.get("satellite"),
            scene.get("satellite_mode"),
            scene.get("receiving_station"),
            scene.get("relative_orbit"),
            scene.get("orbit_direction"),
            scene.get("imaging_mode"),
            scene.get("polarization"),
            scene.get("center_bucket"),
        ]
        return "|".join(str(part or "") for part in parts)

    @staticmethod
    def _aoi_stack_group_key(scene: dict[str, Any]) -> str:
        parts = [
            scene.get("satellite"),
            scene.get("satellite_mode"),
            scene.get("relative_orbit"),
            scene.get("orbit_direction"),
            scene.get("imaging_mode"),
            scene.get("polarization"),
        ]
        return "|".join(str(part or "") for part in parts)

    @staticmethod
    def _normalize_discovery_mode(value: str | None) -> str:
        text = str(value or "").strip().lower()
        return "aoi" if text == "aoi" else "strict"

    def _build_discovery_aoi(
        self,
        *,
        admin_region: str | None,
        aoi_bbox: dict[str, Any] | None,
    ) -> dict[str, Any]:
        bbox = self._normalize_bbox(aoi_bbox)
        if bbox:
            geometry = shapely_box(
                bbox["min_lon"],
                bbox["min_lat"],
                bbox["max_lon"],
                bbox["max_lat"],
            )
            return {
                "geometry": geometry,
                "summary": {
                    "match_status": "matched",
                    "source": "bbox",
                    "bbox": bbox,
                    "display_name": "Custom AOI bbox",
                },
            }

        region = lookup_admin_region_geometry(admin_region)
        if not region:
            return {"geometry": None, "summary": None}
        geometry = region.get("geometry")
        summary = {key: value for key, value in region.items() if key != "geometry"}
        if geometry is None or getattr(geometry, "is_empty", False):
            return {"geometry": None, "summary": summary}
        return {"geometry": geometry, "summary": summary}

    def _scene_with_aoi_metrics(self, scene: dict[str, Any], aoi_geometry: Any) -> dict[str, Any]:
        bbox = self._normalize_bbox(scene.get("bbox"))
        if not bbox:
            return {**scene, "aoi_intersects": False, "aoi_overlap_ratio": 0.0}
        scene_geometry = shapely_box(
            bbox["min_lon"],
            bbox["min_lat"],
            bbox["max_lon"],
            bbox["max_lat"],
        )
        try:
            intersects = bool(scene_geometry.intersects(aoi_geometry))
        except Exception:
            return {**scene, "aoi_intersects": False, "aoi_overlap_ratio": 0.0}
        if not intersects:
            return {**scene, "aoi_intersects": False, "aoi_overlap_ratio": 0.0}
        try:
            intersection_area = float(scene_geometry.intersection(aoi_geometry).area or 0.0)
            scene_area = float(scene_geometry.area or 0.0)
            aoi_area = float(getattr(aoi_geometry, "area", 0.0) or 0.0)
        except Exception:
            intersection_area = 0.0
            scene_area = 0.0
            aoi_area = 0.0
        return {
            **scene,
            "aoi_intersects": True,
            "aoi_overlap_ratio": intersection_area / scene_area if scene_area > 0 else 0.0,
            "aoi_covered_ratio": intersection_area / aoi_area if aoi_area > 0 else None,
        }

    @staticmethod
    def _bbox_area(value: dict[str, Any] | None) -> float:
        if not value:
            return 0.0
        try:
            width = float(value["max_lon"]) - float(value["min_lon"])
            height = float(value["max_lat"]) - float(value["min_lat"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        return width * height if width > 0 and height > 0 else 0.0

    def _build_discovery_scene_groups(
        self,
        *,
        observation_key: str,
        group_scenes: list[dict[str, Any]],
        discovery_mode: str,
        require_orbits: bool,
        min_scenes: int,
        min_common_overlap_ratio: float,
        cluster_source: str,
    ) -> list[dict[str, Any]]:
        mode = self._normalize_discovery_mode(discovery_mode)
        clusters = self._cluster_aoi_scenes(group_scenes)
        scene_groups: list[dict[str, Any]] = []
        seen_scene_keys: set[tuple[str, ...]] = set()

        for cluster_index, cluster in enumerate(clusters):
            primary_key = None
            if mode == "aoi" or len(clusters) > 1:
                primary_key = self._aoi_cluster_key(observation_key, cluster)
                if len(clusters) > 1:
                    primary_key = f"{primary_key}|cluster_{cluster_index + 1}"
            primary_scenes = self._prepare_candidate_group_scenes(
                cluster,
                cluster_key=primary_key,
                cluster_source=cluster_source,
                variant="primary_cluster",
            )
            primary_scenes = self._select_date_keyed_stack_scenes(primary_scenes)
            self._append_discovery_scene_group(
                scene_groups,
                seen_scene_keys,
                primary_scenes,
                variant="primary_cluster",
            )

            for subgroup_index, subgroup in enumerate(
                self._extract_common_overlap_subgroups(
                    cluster,
                    require_orbits=require_orbits,
                    min_scenes=min_scenes,
                    min_common_overlap_ratio=min_common_overlap_ratio,
                )
            ):
                subgroup_key = self._substack_group_key(observation_key, subgroup)
                subgroup_scenes = self._prepare_candidate_group_scenes(
                    subgroup,
                    cluster_key=subgroup_key,
                    cluster_source=cluster_source,
                    variant=f"common_overlap_substack_{subgroup_index + 1}",
                )
                self._append_discovery_scene_group(
                    scene_groups,
                    seen_scene_keys,
                    subgroup_scenes,
                    variant="common_overlap_substack",
                )

        return scene_groups

    def _append_discovery_scene_group(
        self,
        scene_groups: list[dict[str, Any]],
        seen_scene_keys: set[tuple[str, ...]],
        scenes: list[dict[str, Any]],
        *,
        variant: str,
    ) -> None:
        key = self._scene_identity_key(scenes)
        if not key or key in seen_scene_keys:
            return
        seen_scene_keys.add(key)
        scene_groups.append({"variant": variant, "scenes": scenes})

    def _prepare_candidate_group_scenes(
        self,
        scenes: list[dict[str, Any]],
        *,
        cluster_key: str | None,
        cluster_source: str,
        variant: str,
    ) -> list[dict[str, Any]]:
        prepared = []
        for scene in scenes:
            item = {
                **scene,
                "aoi_cluster_source": cluster_source,
                "discovery_group_variant": variant,
            }
            if cluster_key:
                item["aoi_cluster_key"] = cluster_key
            else:
                item.pop("aoi_cluster_key", None)
            prepared.append(item)
        return prepared

    @staticmethod
    def _scene_identity_key(scenes: list[dict[str, Any]]) -> tuple[str, ...]:
        values = [
            str(scene.get("scene_name") or scene.get("scene_dir_windows") or scene.get("date") or "").strip()
            for scene in scenes
        ]
        return tuple(sorted(value for value in values if value))

    @staticmethod
    def _hash_identity_values(values: list[str] | tuple[str, ...]) -> str | None:
        filtered = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not filtered:
            return None
        return hashlib.sha1("|".join(filtered).encode("utf-8", errors="ignore")).hexdigest()

    def _scene_identity_summary(self, scenes: list[dict[str, Any]]) -> dict[str, Any]:
        scene_names = self._scene_identity_key(scenes)
        dates = tuple(sorted(
            str(scene.get("date") or "").strip()
            for scene in scenes
            if str(scene.get("date") or "").strip()
        ))
        return {
            "scene_identity_key": scene_names,
            "scene_identity_hash": self._hash_identity_values(scene_names),
            "scene_name_count": len(scene_names),
            "scene_name_preview": list(scene_names[:3]),
            "scene_names": list(scene_names),
            "date_sequence_key": dates,
            "date_sequence_hash": self._hash_identity_values(dates),
        }

    def _annotate_stack_candidate_identity(
        self,
        candidates: list[dict[str, Any]],
        *,
        existing_run_index: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        by_date_sequence: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            identity = self._scene_identity_summary(candidate.get("scenes") or [])
            candidate["scene_identity_hash"] = identity["scene_identity_hash"]
            candidate["scene_name_count"] = identity["scene_name_count"]
            candidate["scene_name_preview"] = identity["scene_name_preview"]
            candidate["scene_names"] = identity["scene_names"]
            candidate["date_sequence_hash"] = identity["date_sequence_hash"]
            key = identity.get("date_sequence_hash")
            if key:
                by_date_sequence.setdefault(str(key), []).append(candidate)

        for group in by_date_sequence.values():
            group.sort(key=self._stack_candidate_rank, reverse=True)
            scene_hashes = {
                str(item.get("scene_identity_hash") or "")
                for item in group
                if item.get("scene_identity_hash")
            }
            for index, candidate in enumerate(group, start=1):
                siblings = [
                    {
                        "stack_id": item.get("stack_id"),
                        "scene_identity_hash": item.get("scene_identity_hash"),
                        "center_bucket": item.get("center_bucket"),
                        "center": item.get("center"),
                        "admin_region": item.get("admin_region"),
                        "common_overlap_ratio": item.get("common_overlap_ratio"),
                    }
                    for item in group
                    if item is not candidate
                ]
                candidate["same_date_sequence_candidate_count"] = len(group)
                candidate["same_date_sequence_rank"] = index
                candidate["same_date_sequence_distinct_scene_group_count"] = len(scene_hashes)
                candidate["same_date_sequence_siblings"] = siblings[:12]
                candidate["same_date_sequence_has_different_scene_groups"] = len(scene_hashes) > 1

        run_index = existing_run_index or {}
        for candidate in candidates:
            scene_hash = str(candidate.get("scene_identity_hash") or "")
            candidate["existing_same_scene_runs"] = run_index.get(scene_hash, []) if scene_hash else []

    def _existing_run_identity_index(self) -> dict[str, list[dict[str, Any]]]:
        run_root = self.production_root / "runs"
        index: dict[str, list[dict[str, Any]]] = {}
        if not run_root.exists():
            return index
        for manifest_path in sorted(run_root.glob("*/run_manifest.json")):
            try:
                manifest = self._read_json(manifest_path)
                stack_manifest = self._load_stack_manifest_for_run(manifest_path.parent, manifest)
                identity = self._scene_identity_summary(stack_manifest.get("scenes") or [])
                scene_hash = identity.get("scene_identity_hash")
                if not scene_hash:
                    continue
                stack = stack_manifest.get("stack") or manifest.get("stack") or {}
                coverage = self._build_stack_geographic_coverage(stack_manifest)
                index.setdefault(str(scene_hash), []).append(
                    {
                        "run_id": manifest.get("run_id") or manifest_path.parent.name,
                        "run_label": manifest.get("run_label"),
                        "status": manifest.get("status") or "UNKNOWN",
                        "created_at": manifest.get("created_at"),
                        "stack_id": manifest.get("stack_id") or stack_manifest.get("stack_id"),
                        "scene_count": manifest.get("scene_count") or identity.get("scene_name_count"),
                        "pair_count": manifest.get("pair_count"),
                        "center_bucket": stack.get("center_bucket"),
                        "date_start": coverage.get("date_start"),
                        "date_end": coverage.get("date_end"),
                    }
                )
            except Exception:
                continue
        for runs in index.values():
            runs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return index

    def _load_stack_manifest_for_run(self, run_dir: Path, run_manifest: dict[str, Any]) -> dict[str, Any]:
        stack_manifest = self._read_optional_json(run_dir / "stack_manifest.json")
        if stack_manifest:
            return stack_manifest
        stack_manifest_path = Path(str(run_manifest.get("stack_manifest_path") or ""))
        if stack_manifest_path.is_file():
            return self._read_optional_json(stack_manifest_path) or {}
        return {}

    def _substack_group_key(self, observation_key: str, scenes: list[dict[str, Any]]) -> str:
        digest_source = "|".join(self._scene_identity_key(scenes))
        digest = hashlib.sha1(digest_source.encode("utf-8", errors="ignore")).hexdigest()[:12]
        dates = [
            str(scene.get("date") or "").strip()
            for scene in scenes
            if str(scene.get("date") or "").strip()
        ]
        date_start = min(dates) if dates else "unknown"
        date_end = max(dates) if dates else "unknown"
        return f"{observation_key}|substack_{date_start}_{date_end}_{digest}"

    def _extract_common_overlap_subgroups(
        self,
        scenes: list[dict[str, Any]],
        *,
        require_orbits: bool,
        min_scenes: int,
        min_common_overlap_ratio: float,
    ) -> list[list[dict[str, Any]]]:
        threshold = max(0.0, float(min_common_overlap_ratio or 0.0))
        if threshold <= 0:
            return []
        usable = [
            scene for scene in scenes
            if scene.get("has_orbit") or not require_orbits
        ]
        usable = [
            scene for scene in usable
            if self._normalize_bbox(scene.get("bbox")) and str(scene.get("date") or "").strip()
        ]
        if len(usable) < min_scenes:
            return []

        subgroups: list[list[dict[str, Any]]] = []
        seen: set[tuple[str, ...]] = set()
        seeds = sorted(
            usable,
            key=lambda item: (
                str(item.get("date") or ""),
                float(item.get("center_lon") or 0.0),
                float(item.get("center_lat") or 0.0),
                str(item.get("scene_name") or ""),
            ),
        )
        for seed in seeds:
            subgroup = self._grow_common_overlap_subgroup(
                seed=seed,
                scenes=usable,
                min_common_overlap_ratio=threshold,
            )
            if len(subgroup) < min_scenes:
                continue
            if self._scene_common_overlap_ratio(subgroup) < threshold:
                continue
            key = self._scene_identity_key(subgroup)
            if not key or key in seen:
                continue
            seen.add(key)
            subgroups.append(subgroup)

        subgroups.sort(
            key=lambda items: (
                -len(items),
                -self._scene_common_overlap_ratio(items),
                self._subgroup_temporal_gap_score(items),
                self._scene_identity_key(items),
            )
        )
        return subgroups[:12]

    def _grow_common_overlap_subgroup(
        self,
        *,
        seed: dict[str, Any],
        scenes: list[dict[str, Any]],
        min_common_overlap_ratio: float,
    ) -> list[dict[str, Any]]:
        selected = [seed]
        selected_names = {str(seed.get("scene_name") or "")}

        while True:
            selected_dates = {str(scene.get("date") or "").strip() for scene in selected}
            best_scene: dict[str, Any] | None = None
            best_score: tuple[Any, ...] | None = None
            for scene in scenes:
                scene_name = str(scene.get("scene_name") or "")
                if scene_name in selected_names:
                    continue
                scene_date = str(scene.get("date") or "").strip()
                if not scene_date or scene_date in selected_dates:
                    continue
                trial = selected + [scene]
                ratio = self._scene_common_overlap_ratio(trial)
                if ratio < min_common_overlap_ratio:
                    continue
                score = (
                    len(trial),
                    ratio,
                    -self._scene_distance(seed, scene),
                    str(scene.get("date") or ""),
                    str(scene.get("scene_name") or ""),
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_scene = scene
            if best_scene is None:
                break
            selected.append(best_scene)
            selected_names.add(str(best_scene.get("scene_name") or ""))

        return sorted(selected, key=lambda item: (str(item.get("date") or ""), str(item.get("scene_name") or "")))

    def _scene_common_overlap_ratio(self, scenes: list[dict[str, Any]]) -> float:
        bbox_intersection = self._bbox_intersection([scene.get("bbox") for scene in scenes])
        bbox_union = self._stack_bbox_union({"scenes": scenes})
        union_area = self._bbox_area(bbox_union)
        if not bbox_intersection or union_area <= 0:
            return 0.0
        return self._bbox_area(bbox_intersection) / union_area

    def _scene_distance(self, first: dict[str, Any], second: dict[str, Any]) -> float:
        first_lon = self._as_float(first.get("center_lon")) or 0.0
        first_lat = self._as_float(first.get("center_lat")) or 0.0
        second_lon = self._as_float(second.get("center_lon")) or 0.0
        second_lat = self._as_float(second.get("center_lat")) or 0.0
        return math.hypot(first_lon - second_lon, first_lat - second_lat)

    def _subgroup_temporal_gap_score(self, scenes: list[dict[str, Any]]) -> int:
        gaps = self._temporal_gaps([
            str(scene.get("date") or "")
            for scene in scenes
            if str(scene.get("date") or "").strip()
        ])
        return max(gaps) if gaps else 0

    def _dedupe_stack_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_scene_key: dict[tuple[str, ...], dict[str, Any]] = {}
        for candidate in candidates:
            scene_key = self._scene_identity_key(candidate.get("scenes") or [])
            if not scene_key:
                continue
            current = by_scene_key.get(scene_key)
            if current is None or self._stack_candidate_rank(candidate) > self._stack_candidate_rank(current):
                by_scene_key[scene_key] = candidate

        by_stack_id: dict[str, dict[str, Any]] = {}
        for candidate in by_scene_key.values():
            stack_id = str(candidate.get("stack_id") or "")
            if not stack_id:
                continue
            current = by_stack_id.get(stack_id)
            if current is None or self._stack_candidate_rank(candidate) > self._stack_candidate_rank(current):
                by_stack_id[stack_id] = candidate
        return list(by_stack_id.values())

    @staticmethod
    def _stack_candidate_rank(candidate: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(candidate.get("status") == "READY"),
            int(candidate.get("usable_scene_count") or 0),
            float(candidate.get("common_overlap_ratio") or 0.0),
            -int(candidate.get("missing_orbit_count") or 0),
            -int(candidate.get("max_temporal_gap_days") or 0),
            str(candidate.get("date_start") or ""),
            str(candidate.get("stack_id") or ""),
        )

    def _cluster_aoi_scenes(self, scenes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        sorted_scenes = sorted(
            scenes,
            key=lambda item: (
                str(item.get("date") or ""),
                float(item.get("center_lon") or 0.0),
                float(item.get("center_lat") or 0.0),
            ),
        )
        clusters: list[dict[str, Any]] = []
        for scene in sorted_scenes:
            scene_bbox = self._normalize_bbox(scene.get("bbox"))
            if not scene_bbox:
                continue
            best_index: int | None = None
            best_score = -1.0
            for index, cluster in enumerate(clusters):
                candidate_intersection = self._bbox_intersection(
                    [cluster.get("bbox_intersection"), scene_bbox]
                )
                if not candidate_intersection:
                    continue
                score = self._bbox_area(candidate_intersection)
                if score > best_score:
                    best_index = index
                    best_score = score
            if best_index is None:
                clusters.append({"bbox_intersection": scene_bbox, "scenes": [scene]})
                continue
            cluster = clusters[best_index]
            cluster["bbox_intersection"] = self._bbox_intersection(
                [cluster.get("bbox_intersection"), scene_bbox]
            )
            cluster["scenes"].append(scene)

        return [cluster["scenes"] for cluster in clusters if cluster.get("scenes")]

    def _aoi_cluster_key(self, observation_key: str, scenes: list[dict[str, Any]]) -> str:
        bbox = self._bbox_intersection([scene.get("bbox") for scene in scenes])
        if bbox:
            lon = (bbox["min_lon"] + bbox["max_lon"]) / 2
            lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
            spatial_key = f"overlap_E{lon:.2f}_N{lat:.2f}"
        else:
            center = self._stack_center({"scenes": scenes}) or {}
            lon = self._as_float(center.get("lon"))
            lat = self._as_float(center.get("lat"))
            spatial_key = f"center_{self._center_bucket(lon, lat)}"
        return f"{observation_key}|{spatial_key}"

    def _select_date_keyed_stack_scenes(self, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Gamma expert scripts key SLC/RSLC products by date, so a run can use one scene per date."""
        duplicate_audit = self._duplicate_scene_date_audit(scenes)
        duplicate_dates = set(duplicate_audit.get("duplicate_dates") or [])
        if not duplicate_dates:
            return list(scenes)

        overlap = self._bbox_intersection([scene.get("bbox") for scene in scenes])
        if overlap:
            target_lon = (overlap["min_lon"] + overlap["max_lon"]) / 2
            target_lat = (overlap["min_lat"] + overlap["max_lat"]) / 2
        else:
            center = self._stack_center({"scenes": scenes}) or {}
            target_lon = self._as_float(center.get("lon")) or 0.0
            target_lat = self._as_float(center.get("lat")) or 0.0

        selected: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        by_date: dict[str, list[dict[str, Any]]] = {}
        for scene in scenes:
            date = str(scene.get("date") or "").strip()
            if not date:
                continue
            by_date.setdefault(date, []).append(scene)

        def score(scene: dict[str, Any]) -> tuple[float, float, str]:
            bbox = self._normalize_bbox(scene.get("bbox"))
            if bbox and overlap:
                common = self._bbox_intersection([bbox, overlap])
                overlap_area = self._bbox_area(common)
            else:
                overlap_area = 0.0
            lon = self._as_float(scene.get("center_lon")) or target_lon
            lat = self._as_float(scene.get("center_lat")) or target_lat
            center_distance = math.hypot(lon - target_lon, lat - target_lat)
            return (overlap_area, -center_distance, str(scene.get("scene_name") or ""))

        for date in sorted(by_date):
            candidates = by_date[date]
            winner = max(candidates, key=score)
            selected.append(
                {
                    **winner,
                    "date_keyed_scene_selected": True,
                    "same_date_scene_count": len(candidates),
                    "same_date_selection_policy": "max_common_overlap_then_nearest_cluster_center",
                }
            )
            for candidate in candidates:
                if candidate is winner:
                    continue
                excluded.append(
                    {
                        **candidate,
                        "date_keyed_scene_excluded": True,
                        "exclude_reason": "same_date_scene_not_selected_for_gamma_date_keyed_stack",
                        "selected_scene_name": winner.get("scene_name"),
                        "same_date_scene_count": len(candidates),
                    }
                )

        duplicate_audit["policy"] = "one_scene_per_date"
        duplicate_audit["selection_policy"] = "max_common_overlap_then_nearest_cluster_center"
        duplicate_audit["excluded_scene_count"] = len(excluded)
        for scene in selected:
            scene["date_keyed_duplicate_audit"] = duplicate_audit
            scene["date_keyed_excluded_scenes"] = excluded
        return selected

    @staticmethod
    def _duplicate_scene_date_audit(scenes: list[dict[str, Any]]) -> dict[str, Any]:
        by_date: dict[str, list[dict[str, Any]]] = {}
        for scene in scenes:
            date = str(scene.get("date") or "").strip()
            if date:
                by_date.setdefault(date, []).append(scene)
        duplicate_groups = []
        for date, items in sorted(by_date.items()):
            if len(items) <= 1:
                continue
            duplicate_groups.append(
                {
                    "date": date,
                    "count": len(items),
                    "scene_names": [str(item.get("scene_name") or "") for item in items],
                    "centers": [
                        {
                            "lon": item.get("center_lon"),
                            "lat": item.get("center_lat"),
                        }
                        for item in items
                    ],
                }
            )
        return {
            "has_duplicate_dates": bool(duplicate_groups),
            "duplicate_dates": [item["date"] for item in duplicate_groups],
            "duplicate_groups": duplicate_groups,
            "scene_count": len(scenes),
            "unique_date_count": len(by_date),
        }

    def _build_stack_candidate(
        self,
        scenes: list[dict[str, Any]],
        *,
        min_scenes: int,
        require_orbits: bool,
        discovery_mode: str = "strict",
        aoi_summary: dict[str, Any] | None = None,
        min_common_overlap_ratio: float | None = None,
    ) -> dict[str, Any]:
        scenes = sorted(scenes, key=lambda item: str(item.get("date") or ""))
        date_keyed_duplicate_audit = {}
        date_keyed_excluded_scenes: list[dict[str, Any]] = []
        for scene in scenes:
            if scene.get("date_keyed_duplicate_audit"):
                date_keyed_duplicate_audit = scene.get("date_keyed_duplicate_audit") or {}
            if scene.get("date_keyed_excluded_scenes"):
                date_keyed_excluded_scenes = scene.get("date_keyed_excluded_scenes") or []
        scenes = [
            {
                key: value
                for key, value in scene.items()
                if key not in {"date_keyed_duplicate_audit", "date_keyed_excluded_scenes"}
            }
            for scene in scenes
        ]
        first = scenes[0]
        orbit_ready = [scene for scene in scenes if scene.get("has_orbit")]
        usable = orbit_ready if require_orbits else scenes
        dates = [scene.get("date") for scene in scenes if scene.get("date")]
        usable_dates = [scene.get("date") for scene in usable if scene.get("date")]
        mode = self._normalize_discovery_mode(discovery_mode)
        group_key = (
            str(first.get("aoi_cluster_key") or "")
            or (self._aoi_stack_group_key(first) if mode == "aoi" else self._stack_group_key(first))
        )
        stack_id = self._stable_id(group_key)
        temporal_gaps = self._temporal_gaps(usable_dates)
        blockers: list[str] = []
        if len(usable) < min_scenes:
            blockers.append(f"usable_scene_count {len(usable)} < min_scenes {min_scenes}")
        if require_orbits and len(orbit_ready) < len(scenes):
            blockers.append("missing precise orbit for one or more scenes")
        usable_stack = {"scenes": usable}
        bbox_intersection = self._bbox_intersection([scene.get("bbox") for scene in usable])
        bbox_union = self._stack_bbox_union(usable_stack)
        common_overlap_ratio = (
            self._bbox_area(bbox_intersection) / self._bbox_area(bbox_union)
            if bbox_intersection and bbox_union and self._bbox_area(bbox_union) > 0
            else 0.0
        )
        if mode == "aoi" and usable and not bbox_intersection:
            blockers.append("no common overlap across usable scenes")
        if mode != "aoi" and usable and not bbox_intersection:
            blockers.append("no common overlap across usable scenes")
        if min_common_overlap_ratio > 0 and common_overlap_ratio < min_common_overlap_ratio:
            blockers.append(
                f"common_overlap_ratio {common_overlap_ratio:.3f} < min_common_overlap_ratio {min_common_overlap_ratio:.3f}"
            )
        center = self._stack_center(usable_stack)
        admin_region = lookup_admin_region_for_point(
            (center or {}).get("lon"),
            (center or {}).get("lat"),
        )
        aoi_overlap_values = [
            float(scene.get("aoi_overlap_ratio") or 0.0)
            for scene in usable
            if scene.get("aoi_overlap_ratio") is not None
        ]
        return {
            "stack_id": stack_id,
            "status": "READY" if not blockers else "BLOCKED",
            "blockers": blockers,
            "discovery_mode": mode,
            "aoi": aoi_summary,
            "group_key": group_key,
            "sensor_family": first.get("satellite_family") or self._normalize_sensor_family(first.get("satellite")),
            "hard_group_fields": [
                "satellite",
                "satellite_mode",
                "relative_orbit",
                "orbit_direction",
                "imaging_mode",
                "polarization",
                "footprint_common_overlap_cluster",
            ] if mode == "aoi" else [
                "satellite",
                "satellite_mode",
                "relative_orbit",
                "orbit_direction",
                "imaging_mode",
                "polarization",
                "footprint_common_overlap_cluster",
            ],
            "soft_group_fields": ["receiving_station", "center_bucket"],
            "grouping_strategy": first.get("aoi_cluster_source") or "footprint_common_overlap",
            "satellite": first.get("satellite"),
            "satellite_mode": first.get("satellite_mode"),
            "receiving_station": first.get("receiving_station"),
            "relative_orbit": first.get("relative_orbit"),
            "orbit_direction": first.get("orbit_direction"),
            "imaging_mode": first.get("imaging_mode"),
            "polarization": first.get("polarization"),
            "center_bucket": first.get("center_bucket"),
            "scene_count": len(scenes),
            "orbit_ready_scene_count": len(orbit_ready),
            "usable_scene_count": len(usable),
            "missing_orbit_count": len(scenes) - len(orbit_ready),
            "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None,
            "dates": dates,
            "usable_dates": usable_dates,
            "reference_date": usable_dates[len(usable_dates) // 2] if usable_dates else None,
            "temporal_gaps_days": temporal_gaps,
            "max_temporal_gap_days": max(temporal_gaps) if temporal_gaps else 0,
            "bbox": bbox_union,
            "bbox_intersection": bbox_intersection,
            "common_overlap_ratio": common_overlap_ratio,
            "min_common_overlap_ratio": min_common_overlap_ratio,
            "aoi_overlap_ratio_min": min(aoi_overlap_values) if aoi_overlap_values else None,
            "aoi_overlap_ratio_max": max(aoi_overlap_values) if aoi_overlap_values else None,
            "aoi_overlap_ratio_mean": (
                sum(aoi_overlap_values) / len(aoi_overlap_values)
                if aoi_overlap_values else None
            ),
            "center": center,
            "admin_region": admin_region,
            "scenes": scenes,
            "date_keyed_duplicate_audit": date_keyed_duplicate_audit or self._duplicate_scene_date_audit(scenes),
            "date_keyed_excluded_scenes": date_keyed_excluded_scenes,
        }

    @staticmethod
    def _stable_id(value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"sbas_{digest}"

    @staticmethod
    def _effective_min_common_overlap_ratio(value: Any) -> float:
        try:
            requested = float(value or 0.0)
        except (TypeError, ValueError):
            requested = 0.0
        try:
            configured = float(
                settings.GAMMA_SBAS_MIN_COMMON_OVERLAP_RATIO
                or GAMMA_SBAS_FALLBACK_MIN_COMMON_OVERLAP_RATIO
            )
        except (TypeError, ValueError):
            configured = GAMMA_SBAS_FALLBACK_MIN_COMMON_OVERLAP_RATIO
        requested = min(1.0, max(0.0, requested))
        configured = min(1.0, max(0.0, configured))
        return max(requested, configured)

    @staticmethod
    def _temporal_gaps(dates: list[str]) -> list[int]:
        parsed: list[datetime] = []
        for date in sorted(set(dates)):
            try:
                parsed.append(datetime.strptime(date, "%Y%m%d"))
            except ValueError:
                continue
        return [
            int((parsed[index + 1] - parsed[index]).days)
            for index in range(len(parsed) - 1)
        ]

    @staticmethod
    def _bbox_intersection(items: list[dict[str, Any] | None]) -> dict[str, float] | None:
        boxes = [item for item in items if item]
        if not boxes:
            return None
        min_lon = max(float(item["min_lon"]) for item in boxes)
        min_lat = max(float(item["min_lat"]) for item in boxes)
        max_lon = min(float(item["max_lon"]) for item in boxes)
        max_lat = min(float(item["max_lat"]) for item in boxes)
        if min_lon >= max_lon or min_lat >= max_lat:
            return None
        return {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        }

    @staticmethod
    def _build_adjacent_pairs(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pairs: list[dict[str, Any]] = []
        for index in range(len(scenes) - 1):
            master = scenes[index]
            slave = scenes[index + 1]
            delta_days = None
            try:
                delta_days = int(
                    (
                        datetime.strptime(str(slave.get("date")), "%Y%m%d")
                        - datetime.strptime(str(master.get("date")), "%Y%m%d")
                    ).days
                )
            except ValueError:
                pass
            pairs.append(
                {
                    "pair_index": index + 1,
                    "master_date": master.get("date"),
                    "slave_date": slave.get("date"),
                    "delta_days": delta_days,
                    "master_scene_name": master.get("scene_name"),
                    "slave_scene_name": slave.get("scene_name"),
                    "itab_row_initial": [index + 1, index + 2, index + 1, 1],
                    "gamma_baseline_status": "PENDING",
                }
            )
        return pairs

    def _write_runtime_json(self, relative_dir: str | Path, filename: str, payload: dict[str, Any]) -> Path:
        out_dir = self.production_root / relative_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return out_path

    @staticmethod
    def _copy_file_if_newer(source: Path, target: Path) -> bool:
        if not source.is_file():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            source_stat = source.stat()
            target_stat = target.stat() if target.exists() else None
            if (
                target_stat is not None
                and target_stat.st_size == source_stat.st_size
                and int(target_stat.st_mtime) >= int(source_stat.st_mtime)
            ):
                return False
            shutil.copy2(source, target)
            return True
        except OSError:
            shutil.copy2(source, target)
            return True

    @staticmethod
    def _copy_tree_files(source_dir: Path, target_dir: Path) -> tuple[int, int]:
        if not source_dir.is_dir():
            return 0, 0
        copied = 0
        skipped = 0
        for source in source_dir.rglob("*"):
            if not source.is_file():
                continue
            target = target_dir / source.relative_to(source_dir)
            if SbasInsarProductionService._copy_file_if_newer(source, target):
                copied += 1
            else:
                skipped += 1
        return copied, skipped

    def sync_product_package(self, run_id: str) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        product_dir = self.product_run_dir(run_dir.name)
        product_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        skipped = 0

        for relative in (
            "run_manifest.json",
            "stack_manifest.json",
            "pair_network.json",
            "workflow_summary.json",
            "monitor_points_summary.json",
            "product_summary.json",
            "quality_summary.json",
            "gamma_command_manifest.json",
            "expert_command_audit.json",
        ):
            if self._copy_file_if_newer(run_dir / relative, product_dir / relative):
                copied += 1
            else:
                skipped += 1

        for dirname in ("publish",):
            tree_copied, tree_skipped = self._copy_tree_files(run_dir / dirname, product_dir / dirname)
            copied += tree_copied
            skipped += tree_skipped

        for relative in (
            "diff_dir/bprep_file.png",
            "diff_dir/mean.cc_mask.bmp",
            "sbas/final_unw_tab",
        ):
            if self._copy_file_if_newer(run_dir / relative, product_dir / relative):
                copied += 1
            else:
                skipped += 1

        final_tab = run_dir / "sbas" / "final_unw_tab"
        diff_dir = run_dir / "diff_dir"
        pair_ids: list[str] = []
        if final_tab.is_file():
            for line in final_tab.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw_path = line.strip().split()[0] if line.strip() else ""
                if not raw_path:
                    continue
                name = Path(self._path_to_windows(raw_path) or raw_path).name
                pair_id = name.replace(".unw.atmsub_1", "").replace(".unw", "")
                if pair_id and pair_id not in pair_ids:
                    pair_ids.append(pair_id)
        qcs = [diff_dir / f"{pair_id}.adf.unw.bmp" for pair_id in pair_ids if (diff_dir / f"{pair_id}.adf.unw.bmp").is_file()]
        if not qcs and diff_dir.is_dir():
            qcs = sorted(diff_dir.glob("*.adf.unw.bmp"))
        if len(qcs) > 3:
            last_index = len(qcs) - 1
            indexes = sorted({round(index * last_index / 2) for index in range(3)})
            qcs = [qcs[index] for index in indexes]
        for source in qcs:
            target = product_dir / source.relative_to(run_dir)
            if self._copy_file_if_newer(source, target):
                copied += 1
            else:
                skipped += 1

        marker = {
            "schema": "insar.gamma-sbas-product-package/v1",
            "run_id": run_dir.name,
            "source_run_dir": str(run_dir),
            "product_run_dir": str(product_dir),
            "copied_files": copied,
            "skipped_files": skipped,
            "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        self._write_json(product_dir / "product_package_manifest.json", marker)
        return marker

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _write_script(path: Path, lines: list[str]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines)
        commands = SbasInsarProductionService._extract_shell_command_tokens(text)
        blocking_interactive = sorted(set(GAMMA_SBAS_BLOCKING_INTERACTIVE_TOOLS) & commands)
        if blocking_interactive:
            raise ValueError(
                "Refusing to write an interactive Gamma SBAS production script: "
                f"{path} contains {', '.join(blocking_interactive)}. "
                f"{GAMMA_SBAS_UNATTENDED_POLICY}"
            )
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
            return path
        except PermissionError:
            suffix = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            fallback = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
            fallback.write_text(text, encoding="utf-8", newline="\n")
            return fallback

    def _resolve_trial_dir(self, trial_id: str) -> Path:
        clean_id = str(trial_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("invalid trial id")
        trial_dir = (self.trial_root / clean_id).resolve()
        root_resolved = self.trial_root.resolve()
        try:
            trial_dir.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("trial id escapes trial root") from exc
        if not trial_dir.is_dir():
            raise FileNotFoundError(f"trial not found: {clean_id}")
        return trial_dir

    def _resolve_run_dir(self, run_id: str) -> Path:
        clean_id = str(run_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("invalid run id")
        run_dir = (self.production_root / "runs" / clean_id).resolve()
        root_resolved = (self.production_root / "runs").resolve()
        try:
            run_dir.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("run id escapes production root") from exc
        if not run_dir.is_dir():
            raise FileNotFoundError(f"run not found: {clean_id}")
        return run_dir

    def _resolve_production_delete_path(self, value: Any) -> Path | None:
        text = str(value or "").strip()
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute():
            path = self.production_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self.production_root.resolve())
        except ValueError as exc:
            raise ValueError(f"refusing to delete path outside SBAS production root: {resolved}") from exc
        return resolved

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        return self._read_json(path)

    def _ensure_expert_workspace(self, run_dir: Path) -> dict[str, Any]:
        created: dict[str, str] = {}
        for dirname in EXPERT_WORKSPACE_DIRS:
            path = run_dir / dirname
            path.mkdir(parents=True, exist_ok=True)
            created[dirname] = str(path)

        work_gamma = run_dir / "work" / "gamma"
        aliases = {
            "RAW": work_gamma / "raw",
            "SLC": work_gamma / "slc",
            "dem": work_gamma / "dem",
            "rslc_prep": work_gamma / "rslc_prep",
            "mli_dir": work_gamma / "mli",
            "diff_dir": work_gamma / "diff",
            "diff1_dir": work_gamma / "diff1",
            "sbas": work_gamma / "sbas",
        }
        for path in aliases.values():
            path.mkdir(parents=True, exist_ok=True)

        workspace = {
            "schema": "insar.gamma-sbas-expert-workspace/v1",
            "run_root": str(run_dir),
            "directories": created,
            "gamma_work_aliases": {key: str(value) for key, value in aliases.items()},
            "layout_source": "LT1_GAMMA_SBAS_expert_document",
        }
        self._write_json(run_dir / "workspace.json", workspace)
        return workspace

    def _ensure_s1_planning_workspace(self, run_dir: Path) -> dict[str, Any]:
        dirs = ("RAW", "orbits", "planning", "logs", "scripts", "state", "publish")
        created: dict[str, str] = {}
        for dirname in dirs:
            path = run_dir / dirname
            path.mkdir(parents=True, exist_ok=True)
            created[dirname] = str(path)
        workspace = {
            "schema": "insar.s1-gamma-sbas-planning-workspace/v1",
            "run_root": str(run_dir),
            "directories": created,
            "layout_source": "Sentinel-1 Gamma SBAS planning profile",
            "execution_enabled": False,
        }
        self._write_json(run_dir / "workspace.json", workspace)
        return workspace

    def _build_s1_workflow_manifest(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
        stack_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        steps = []
        for template in S1_GAMMA_SBAS_PLANNING_STEPS:
            steps.append(
                {
                    **dict(template),
                    "enabled": False,
                    "script": None,
                    "script_wsl": None,
                    "log": str(run_dir / "logs" / f"{template['id']}.log"),
                    "log_wsl": self._windows_path_to_wsl_mount(str(run_dir / "logs" / f"{template['id']}.log")),
                    "expert_tools": [],
                }
            )
        return {
            "schema": "insar.s1-gamma-sbas-workflow-planning/v1",
            "run_id": run_manifest.get("run_id") or run_dir.name,
            "workflow_code": "sbas_insar",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "profile_code": "s1_gamma_sbas",
            "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "run_root": str(run_dir),
            "run_root_wsl": self._windows_path_to_wsl_mount(str(run_dir)),
            "execution_enabled": False,
            "execution_blocker": "Sentinel-1 Gamma TOPS/SBAS scripts have not been verified.",
            "stack": stack_manifest.get("stack") or {},
            "scenes": stack_manifest.get("scenes") or [],
            "pair_network": stack_manifest.get("pair_network") or {},
            "directories": {
                key: str(run_dir / key)
                for key in ("RAW", "orbits", "planning", "logs", "scripts", "state", "publish")
            },
            "steps": steps,
            "expert_document": {
                "schema": "insar.s1-gamma-sbas-design/v1",
                "source": "docs/SENTINEL1_GAMMA_SBAS_NO_STITCH_DESIGN.md",
                "section_count": 0,
                "steps": [],
            },
        }

    def _build_workflow_manifest(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
        stack_manifest: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_params = {
            "rlks": settings.GAMMA_SBAS_DEFAULT_RLKS,
            "azlks": settings.GAMMA_SBAS_DEFAULT_AZLKS,
            "mb_mode": settings.GAMMA_SBAS_DEFAULT_MB_MODE,
            "reference_window": settings.GAMMA_SBAS_DEFAULT_REFERENCE_WINDOW,
            **(params or {}),
        }
        reference_date = str(
            ((run_manifest.get("coregistration") or {}).get("reference_date"))
            or ((run_manifest.get("stack") or {}).get("reference_date"))
            or ((stack_manifest.get("stack") or {}).get("reference_date"))
            or ""
        ).strip()
        script_records = self._materialize_workflow_scripts(
            run_dir,
            run_manifest=run_manifest,
            stack_manifest=stack_manifest,
            params=resolved_params,
            reference_date=reference_date,
        )
        steps: list[dict[str, Any]] = []
        for template in GAMMA_SBAS_WORKFLOW_STEPS:
            step_id = template["id"]
            script_record = script_records.get(step_id) or {}
            step_status = template.get("status") or "PENDING"
            enabled = step_status != "PLANNED"
            steps.append(
                {
                    "id": step_id,
                    "name": template["name"],
                    "status": step_status,
                    "enabled": enabled,
                    "optional": bool(template.get("optional")),
                    "legacy_stage": template.get("legacy_stage"),
                    "script": script_record.get("script"),
                    "script_wsl": script_record.get("script_wsl"),
                    "log": str(run_dir / "logs" / f"{step_id}.log"),
                    "log_wsl": self._windows_path_to_wsl_mount(str(run_dir / "logs" / f"{step_id}.log")),
                    "expert_tools": list(template.get("expert_tools") or []),
                    "notes": script_record.get("notes") or [],
                }
            )
        expert_steps = self._build_expert_document_step_manifest(steps)
        return {
            "schema": "insar.gamma-sbas-workflow/v1",
            "run_id": run_manifest.get("run_id") or run_dir.name,
            "workflow_code": "sbas_insar",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "runtime_id": settings.GAMMA_SBAS_RUNTIME_ID,
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "run_root": str(run_dir),
            "run_root_wsl": self._windows_path_to_wsl_mount(str(run_dir)),
            "state": {
                "step_status_path": self._windows_path_to_wsl_mount(str(run_dir / "state" / "step_status.json")),
                "step_status_path_windows": str(run_dir / "state" / "step_status.json"),
            },
            "params": resolved_params,
            "stack": stack_manifest.get("stack") or {},
            "scenes": stack_manifest.get("scenes") or [],
            "pair_network": stack_manifest.get("pair_network") or {},
            "directories": {
                dirname: str(run_dir / dirname)
                for dirname in EXPERT_WORKSPACE_DIRS
            },
            "directories_wsl": {
                dirname: self._windows_path_to_wsl_mount(str(run_dir / dirname))
                for dirname in EXPERT_WORKSPACE_DIRS
            },
            "steps": steps,
            "expert_document": {
                "schema": "insar.gamma-sbas-expert-document/v1",
                "source": "LT1_GAMMA_SBAS_逐命令处理流程.docx",
                "section_count": len(expert_steps),
                "steps": expert_steps,
            },
        }

    def _initial_workflow_state(self, run_manifest: dict[str, Any], workflow_manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "insar.gamma-sbas-step-status/v1",
            "run_id": run_manifest.get("run_id"),
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "steps": {
                str(step.get("id")): {
                    "id": step.get("id"),
                    "name": step.get("name"),
                    "status": "PENDING" if step.get("enabled") else "PLANNED",
                    "script": step.get("script_wsl") or step.get("script"),
                }
                for step in workflow_manifest.get("steps") or []
            },
        }

    @staticmethod
    def _build_expert_document_step_manifest(workflow_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        workflow_by_id = {str(step.get("id") or ""): step for step in workflow_steps}
        expert_steps: list[dict[str, Any]] = []
        for template in GAMMA_SBAS_EXPERT_DOCUMENT_STEPS:
            mapped_workflow_steps = []
            enabled = False
            optional = False
            planned = False
            scripts: list[str] = []
            logs: list[str] = []
            for workflow_step_id in template.get("workflow_steps") or []:
                workflow_step = workflow_by_id.get(str(workflow_step_id))
                if not workflow_step:
                    continue
                mapped_workflow_steps.append(
                    {
                        "id": workflow_step.get("id"),
                        "name": workflow_step.get("name"),
                        "status": workflow_step.get("status"),
                        "enabled": bool(workflow_step.get("enabled")),
                        "optional": bool(workflow_step.get("optional")),
                        "script": workflow_step.get("script"),
                        "script_wsl": workflow_step.get("script_wsl"),
                    }
                )
                enabled = enabled or bool(workflow_step.get("enabled"))
                optional = optional or bool(workflow_step.get("optional"))
                planned = planned or str(workflow_step.get("status") or "") == "PLANNED"
                if workflow_step.get("script"):
                    scripts.append(str(workflow_step.get("script")))
                if workflow_step.get("log"):
                    logs.append(str(workflow_step.get("log")))
            status = str(template.get("implementation_status") or "planned")
            if planned and status.startswith("implemented"):
                status = "planned"
            expert_steps.append(
                {
                    "id": template.get("id"),
                    "order": template.get("order"),
                    "title": template.get("title"),
                    "document_section": template.get("document_section"),
                    "implementation_status": status,
                    "workflow_steps": list(template.get("workflow_steps") or []),
                    "mapped_workflow_steps": mapped_workflow_steps,
                    "enabled": enabled,
                    "optional": optional,
            "command_count": len(template.get("commands") or []),
            "commands": list(template.get("commands") or []),
            "manual_qc_tools": list(template.get("manual_qc_tools") or []),
            "unattended_policy": template.get("unattended_policy"),
            "scripts": scripts,
            "logs": logs,
        }
    )
        return expert_steps

    def _summarize_workflow_state(self, workflow_manifest: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        state_steps = state.get("steps") or {}
        steps = []
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        planned_count = 0
        blocking_planned_count = 0
        for step in workflow_manifest.get("steps") or []:
            step_id = str(step.get("id") or "")
            record = state_steps.get(step_id) or {}
            status = str(record.get("status") or ("PLANNED" if not step.get("enabled") else "PENDING"))
            if status == "COMPLETED":
                completed_count += 1
            elif status == "FAILED":
                failed_count += 1
            elif status == "SKIPPED":
                skipped_count += 1
            elif status == "PLANNED":
                planned_count += 1
                if step.get("enabled") or not step.get("optional"):
                    blocking_planned_count += 1
            steps.append(
                {
                    "id": step_id,
                    "name": step.get("name"),
                    "enabled": bool(step.get("enabled")),
                    "optional": bool(step.get("optional")),
                    "status": status,
                    "returncode": record.get("returncode"),
                    "log": record.get("log") or step.get("log"),
                }
            )
        enabled_count = sum(1 for step in workflow_manifest.get("steps") or [] if step.get("enabled"))
        return {
            "schema": "insar.gamma-sbas-workflow-summary/v1",
            "run_id": workflow_manifest.get("run_id"),
            "step_count": len(steps),
            "enabled_count": enabled_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "planned_count": planned_count,
            "blocking_planned_count": blocking_planned_count,
            "ready": (
                enabled_count > 0
                and failed_count == 0
                and blocking_planned_count == 0
                and completed_count + skipped_count >= enabled_count
            ),
            "steps": steps,
        }

    def _materialize_workflow_scripts(
        self,
        run_dir: Path,
        *,
        run_manifest: dict[str, Any],
        stack_manifest: dict[str, Any],
        params: dict[str, Any],
        reference_date: str,
    ) -> dict[str, dict[str, Any]]:
        self._ensure_gamma_date_keyed_stack(stack_manifest)
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        if not scenes:
            raise ValueError("Gamma SBAS expert workflow requires at least one LT1 scene")
        if not reference_date or reference_date not in {str(scene.get("date") or "") for scene in scenes}:
            reference_date = str(scenes[len(scenes) // 2].get("date") or "").strip()
        if not reference_date:
            raise ValueError("Gamma SBAS expert workflow requires a reference date")

        rlks = self._bounded_int(params.get("rlks"), default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(params.get("azlks"), default=8, minimum=1, maximum=64)
        reference_window = self._bounded_int(params.get("reference_window"), default=16, minimum=1, maximum=256)
        dem_source = self._resolve_expert_dem_import_source(stack_manifest)
        dem_source = self._materialize_expert_dem_import_source(run_dir, dem_source)

        writers = {
            "01_workspace_data": self._write_expert_workspace_script,
            "02_import_lt1_slc": self._write_expert_import_slc_script,
            "03_reference_mli": self._write_expert_reference_mli_script,
            "04_dem_lookup": self._write_expert_dem_lookup_script,
            "05_coreg_prep": self._write_expert_coreg_prep_script,
            "06_coregister_scenes": self._write_expert_coregister_scenes_script,
            "07_rmli_average": self._write_expert_rmli_average_script,
            "08_diff_network": self._write_expert_diff_network_script,
            "09_filter_unwrap": self._write_expert_filter_unwrap_script,
            "10_detrend_atm": self._write_expert_detrend_atm_script,
            "11_sbas_inversion": self._write_expert_sbas_inversion_script,
            "12_outputs_points": self._write_expert_outputs_points_script,
        }
        context = {
            "run_dir": run_dir,
            "scenes": scenes,
            "reference_date": reference_date,
            "rlks": rlks,
            "azlks": azlks,
            "reference_window": reference_window,
            "dem_source": dem_source,
        }
        script_records: dict[str, dict[str, Any]] = {}
        for template in GAMMA_SBAS_WORKFLOW_STEPS:
            step_id = str(template.get("id") or "")
            writer = writers.get(step_id)
            if not writer:
                continue
            script_path = writer(**context)
            audit = self._audit_expert_step_script(step_id, script_path)
            script_records[step_id] = self._script_record(script_path, notes=audit.get("notes") or [])
            script_records[step_id]["command_audit"] = audit
        audit_summary = self._audit_expert_workflow_scripts(script_records)
        self._write_json(run_dir / "expert_command_audit.json", audit_summary)
        if not audit_summary.get("ready"):
            problems = "; ".join(audit_summary.get("problems") or [])
            raise ValueError(f"Gamma SBAS expert command audit failed: {problems}")
        return script_records

    def _script_record(self, path: Path, *, notes: list[str] | None = None) -> dict[str, Any]:
        return {
            "script": str(path),
            "script_wsl": self._windows_path_to_wsl_mount(str(path)),
            "notes": notes or [],
        }

    def _copy_script_alias(self, source: Path, target: Path) -> None:
        if not source.is_file():
            raise FileNotFoundError(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() == target.resolve():
            return
        target.write_text(source.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8", newline="\n")

    @staticmethod
    def _extract_shell_command_tokens(script_text: str) -> set[str]:
        tokens: set[str] = set()
        for line in script_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for separator in (";", "&&", "||"):
                stripped = stripped.replace(separator, "\n")
            for segment in stripped.splitlines():
                part = segment.strip()
                if not part or part.startswith("#"):
                    continue
                first_word = part.split(None, 1)[0]
                normalized_first_word = first_word.strip("'\"{}()")
                if normalized_first_word in {
                    "if",
                    "then",
                    "else",
                    "fi",
                    "for",
                    "while",
                    "do",
                    "done",
                    "{",
                    "}",
                    "local",
                    "test",
                    "echo",
                    "printf",
                    "cp",
                    "rm",
                    "mkdir",
                    "ln",
                    "cd",
                    "read",
                    "return",
                    "exit",
                    ":",
                    "source",
                    "set",
                }:
                    continue
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", part):
                    continue
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", part.split("$(", 1)[0]):
                    continue
                match = re.match(r'(?:"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"\s+)?([A-Za-z0-9_._-]+)', part)
                if match:
                    tokens.add(match.group(1))
        return tokens

    def _audit_expert_step_script(self, step_id: str, script_path: Path) -> dict[str, Any]:
        text = script_path.read_text(encoding="utf-8", errors="ignore") if script_path.is_file() else ""
        commands = self._extract_shell_command_tokens(text)
        required = set(GAMMA_SBAS_REQUIRED_STEP_TOOLS.get(step_id) or set())
        missing = sorted(required - commands)
        forbidden = sorted(set(GAMMA_SBAS_FORBIDDEN_DEFAULT_TOOLS) & commands)
        blocking_interactive = sorted(set(GAMMA_SBAS_BLOCKING_INTERACTIVE_TOOLS) & commands)
        manual_qc_tools = set()
        for template in GAMMA_SBAS_WORKFLOW_STEPS:
            if str(template.get("id") or "") == step_id:
                manual_qc_tools = set(template.get("manual_qc_tools") or [])
                break
        manual_qc_not_executed = sorted(manual_qc_tools - commands)
        ready = not missing and not forbidden and not blocking_interactive
        notes = []
        if ready:
            notes.append("Expert command audit passed.")
        if missing:
            notes.append("Missing required expert commands: " + ", ".join(missing))
        if forbidden:
            notes.append("Forbidden legacy commands present: " + ", ".join(forbidden))
        if blocking_interactive:
            notes.append("Blocking interactive commands present: " + ", ".join(blocking_interactive))
        if manual_qc_not_executed:
            notes.append(
                "Manual QC display commands intentionally not executed in unattended production: "
                + ", ".join(manual_qc_not_executed)
            )
        return {
            "schema": "insar.gamma-sbas-expert-step-command-audit/v1",
            "step_id": step_id,
            "script": str(script_path),
            "ready": ready,
            "commands": sorted(commands),
            "required_commands": sorted(required),
            "missing_required_commands": missing,
            "forbidden_commands": forbidden,
            "blocking_interactive_commands": blocking_interactive,
            "manual_qc_tools": sorted(manual_qc_tools),
            "manual_qc_tools_not_executed": manual_qc_not_executed,
            "unattended_policy": GAMMA_SBAS_UNATTENDED_POLICY,
            "notes": notes,
        }

    def _audit_expert_workflow_scripts(self, script_records: dict[str, dict[str, Any]]) -> dict[str, Any]:
        step_audits = {
            step_id: record.get("command_audit") or {}
            for step_id, record in script_records.items()
        }
        problems: list[str] = []
        for step_id, audit in step_audits.items():
            if not audit.get("ready"):
                missing = ", ".join(audit.get("missing_required_commands") or [])
                forbidden = ", ".join(audit.get("forbidden_commands") or [])
                interactive = ", ".join(audit.get("blocking_interactive_commands") or [])
                detail = "; ".join(
                    item
                    for item in [
                        f"missing={missing}" if missing else "",
                        f"forbidden={forbidden}" if forbidden else "",
                        f"interactive={interactive}" if interactive else "",
                    ]
                    if item
                )
                problems.append(f"{step_id}: {detail or 'command audit failed'}")
        ready = not problems and len(step_audits) >= len(GAMMA_SBAS_WORKFLOW_STEPS)
        if len(step_audits) < len(GAMMA_SBAS_WORKFLOW_STEPS):
            problems.append("not all expert workflow scripts were materialized")
            ready = False
        return {
            "schema": "insar.gamma-sbas-expert-workflow-command-audit/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "ready": ready,
            "step_count": len(step_audits),
            "expected_step_count": len(GAMMA_SBAS_WORKFLOW_STEPS),
            "problems": problems,
            "steps": step_audits,
            "forbidden_default_tools": sorted(GAMMA_SBAS_FORBIDDEN_DEFAULT_TOOLS),
            "blocking_interactive_tools": sorted(GAMMA_SBAS_BLOCKING_INTERACTIVE_TOOLS),
            "unattended_policy": GAMMA_SBAS_UNATTENDED_POLICY,
        }

    def _expert_script_header(self, run_dir: Path, *, reference_date: str, rlks: int, azlks: int) -> list[str]:
        env_script = (
            self._windows_path_to_wsl_mount(settings.GAMMA_SBAS_ENV_SCRIPT or settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        return [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            'RAW_DIR="${RUN_ROOT}/RAW"',
            'SLC_DIR="${RUN_ROOT}/SLC"',
            'DEM_DIR="${RUN_ROOT}/dem"',
            'RSLC_DIR="${RUN_ROOT}/rslc_prep"',
            'MLI_DIR="${RUN_ROOT}/mli_dir"',
            'DIFF_DIR="${RUN_ROOT}/diff_dir"',
            'DIFF1_DIR="${RUN_ROOT}/diff1_dir"',
            'SBAS_DIR="${RUN_ROOT}/sbas"',
            'PUBLISH_DIR="${RUN_ROOT}/publish"',
            'LOG_DIR="${RUN_ROOT}/logs"',
            'STATE_DIR="${RUN_ROOT}/state"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'AZLKS="{azlks}"',
            f'source "{env_script}" >/dev/null 2>&1',
            'mkdir -p "${RAW_DIR}" "${SLC_DIR}" "${DEM_DIR}" "${RSLC_DIR}" "${MLI_DIR}" "${DIFF_DIR}" "${DIFF1_DIR}" "${SBAS_DIR}" "${PUBLISH_DIR}" "${LOG_DIR}" "${STATE_DIR}"',
            "",
        ]

    @staticmethod
    def _bash_array(name: str, values: list[str]) -> list[str]:
        return [f"{name}=("] + [f'  "{value}"' for value in values] + [")"]

    @staticmethod
    def _unique_scene_dates(scenes: list[dict[str, Any]]) -> list[str]:
        dates: list[str] = []
        seen: set[str] = set()
        for scene in scenes:
            date = str(scene.get("date") or "").strip()
            if date and date not in seen:
                dates.append(date)
                seen.add(date)
        return dates

    @classmethod
    def _ensure_gamma_date_keyed_stack(cls, stack_manifest: dict[str, Any]) -> None:
        scenes = stack_manifest.get("scenes") or []
        audit = cls._duplicate_scene_date_audit(scenes)
        if not audit.get("has_duplicate_dates"):
            return
        examples: list[str] = []
        for group in audit.get("duplicate_groups") or []:
            names = [name for name in (group.get("scene_names") or []) if name]
            label = f"{group.get('date')}({group.get('count')})"
            if names:
                label = f"{label}: {', '.join(names[:3])}"
            examples.append(label)
        detail = "; ".join(examples[:5])
        raise ValueError(
            "Gamma SBAS expert workflow is date-keyed and cannot execute a stack with "
            f"multiple scenes on the same acquisition date. Rebuild the stack as one scene per date. {detail}"
        )

    def _write_expert_workspace_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'find "${RUN_ROOT}" -maxdepth 1 -type d -printf "%f\\n" | sort >"${STATE_DIR}/expert_workspace_dirs.txt"',
                ': >"${STATE_DIR}/scene_dates.txt"',
            ]
        )
        for scene in scenes:
            lines.append(f'echo "{scene.get("date")}" >>"${{STATE_DIR}}/scene_dates.txt"')
        lines.extend(
            [
                f'echo "{dem_source.get("wsl_path")}" >"${{STATE_DIR}}/dem_import_source.txt"',
                'test "$(wc -l <"${STATE_DIR}/scene_dates.txt")" -gt 0',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "01_workspace_data.sh", lines)

    def _write_expert_import_slc_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'run_scene() {',
                '  local date="$1"',
                '  local tiff="$2"',
                '  local meta="$3"',
                '  local slc="${SLC_DIR}/${date}.slc"',
                '  local par="${SLC_DIR}/${date}.slc.par"',
                '  local width=""',
                '  {',
                '    echo "== expert import LT1 SLC ${date} =="',
                '    test -r "${tiff}"',
                '    test -r "${meta}"',
                '    par_LT1_SLC "${tiff}" "${meta}" "${par}" "${slc}" 0',
                '    cp -f "${par}" "${par}.orig"',
                '    ORB_filt_spline.py "${par}.orig" "${par}" --ignore_start 3 --ignore_end 17 --degree 5',
                '    SLC_corners "${par}"',
                '    width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${par}")"',
                '    test -n "${width}"',
                '    echo "manual QC display commands are skipped in unattended production: disSLC dismph_fft"',
                '    test -s "${slc}"',
                '    test -s "${par}"',
                '    test -s "${par}.orig"',
                '  } >"${LOG_DIR}/${date}_expert_import_slc.log" 2>&1',
                '}',
                "",
            ]
        )
        for scene in scenes:
            lines.append(
                "run_scene "
                f'"{scene.get("date")}" '
                f'"{scene.get("tiff_wsl")}" '
                f'"{scene.get("meta_wsl")}"'
            )
        lines.extend(
            [
                ': >"${SLC_DIR}/SLC_tab"',
            ]
        )
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'printf "%s %s\\n" "${{SLC_DIR}}/{date}.slc" "${{SLC_DIR}}/{date}.slc.par" >>"${{SLC_DIR}}/SLC_tab"')
        lines.extend(
            [
                'test "$(wc -l <"${SLC_DIR}/SLC_tab")" -eq ' + str(len(scenes)),
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "02_import_lt1_slc.sh", lines)

    def _write_expert_reference_mli_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'REF_SLC="${SLC_DIR}/${REF_DATE}.slc"',
                'REF_PAR="${SLC_DIR}/${REF_DATE}.slc.par"',
                'REF_MLI="${MLI_DIR}/${REF_DATE}_${RLKS}_${AZLKS}.mli"',
                'REF_MLI_PAR="${MLI_DIR}/${REF_DATE}_${RLKS}_${AZLKS}.mli.par"',
                '{',
                '  echo "== expert reference MLI ${REF_DATE} =="',
                '  test -s "${REF_SLC}"',
                '  test -s "${REF_PAR}"',
                '  multi_look "${REF_SLC}" "${REF_PAR}" "${REF_MLI}" "${REF_MLI_PAR}" "${RLKS}" "${AZLKS}"',
                '  width="$(grep range_samples "${REF_MLI_PAR}" | awk \'{print $2; exit}\')"',
                '  lines="$(grep azimuth_lines "${REF_MLI_PAR}" | awk \'{print $2; exit}\')"',
                '  test -n "${width}"',
                '  test -n "${lines}"',
                '  ras_dB "${REF_MLI}" "${width}" 1 0 1 1 - - gray.cm "${REF_MLI}.bmp" 0 1',
                '  SLC_corners "${REF_MLI_PAR}"',
                '  cp -f "${REF_MLI}" "${MLI_DIR}/${REF_DATE}.mli"',
                '  cp -f "${REF_MLI_PAR}" "${MLI_DIR}/${REF_DATE}.mli.par"',
                '} >"${LOG_DIR}/${REF_DATE}_expert_reference_mli.log" 2>&1',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "03_reference_mli.sh", lines)

    def _write_expert_dem_lookup_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        dem_wsl = str(dem_source.get("wsl_path") or "").strip()
        if not dem_wsl:
            raise ValueError("expert DEM lookup requires a source DEM for dem_import")
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                f'DEM_SRC="{dem_wsl}"',
                'REF_MLI="${MLI_DIR}/${REF_DATE}.mli"',
                'REF_MLI_PAR="${MLI_DIR}/${REF_DATE}.mli.par"',
                'SRTM_DEM="${DEM_DIR}/SRTM.dem"',
                'SRTM_DEM_PAR="${DEM_DIR}/SRTM.dem.par"',
                'SRTM_DEM_FILL="${DEM_DIR}/SRTM_dem_fill"',
                'SEG_DEM_PAR="${DEM_DIR}/${REF_DATE}_seg.dem_par"',
                'SEG_DEM="${DEM_DIR}/${REF_DATE}_seg.dem"',
                'LT="${DEM_DIR}/${REF_DATE}.lt"',
                'LS_MAP="${DEM_DIR}/${REF_DATE}.ls_map"',
                'INC="${DEM_DIR}/${REF_DATE}.inc"',
                'PSI="${DEM_DIR}/${REF_DATE}.psi"',
                'PIX="${DEM_DIR}/${REF_DATE}.pix"',
                'GAMMA0="${DEM_DIR}/${REF_DATE}.gamma0"',
                'DIFF_PAR="${DEM_DIR}/${REF_DATE}.diff_par"',
                'OFFS="${DEM_DIR}/${REF_DATE}.offs"',
                'SNR="${DEM_DIR}/${REF_DATE}.snr"',
                'COFFS="${DEM_DIR}/${REF_DATE}.coffs"',
                'COFFSETS="${DEM_DIR}/${REF_DATE}.coffsets"',
                'LT_FINE="${DEM_DIR}/${REF_DATE}.lt_fine"',
                'HGT="${DEM_DIR}/${REF_DATE}.hgt"',
                'REF_GEO="${DEM_DIR}/${REF_DATE}.geo"',
                'BLANK="${DEM_DIR}/${REF_DATE}.blank"',
                '{',
                '  echo "== expert DEM import and lookup ${REF_DATE} =="',
                '  test -s "${DEM_SRC}"',
                '  test -s "${REF_MLI}"',
                '  test -s "${REF_MLI_PAR}"',
                '  dem_import "${DEM_SRC}" "${SRTM_DEM}" "${SRTM_DEM_PAR}" 0 1 0 - - - - - -',
                '  dem_width="$(awk \'$1 == "width:" {print $2; exit}\' "${SRTM_DEM_PAR}")"',
                '  dem_lines="$(awk \'$1 == "nlines:" {print $2; exit}\' "${SRTM_DEM_PAR}")"',
                '  mli_width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${REF_MLI_PAR}")"',
                '  mli_lines="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${REF_MLI_PAR}")"',
                '  test -n "${dem_width}"',
                '  test -n "${dem_lines}"',
                '  test -n "${mli_width}"',
                '  test -n "${mli_lines}"',
                '  fill_gaps "${SRTM_DEM}" "${dem_width}" "${SRTM_DEM_FILL}" 0 4 0',
                '  gc_map2 "${REF_MLI_PAR}" "${SRTM_DEM_PAR}" "${SRTM_DEM_FILL}" "${SEG_DEM_PAR}" "${SEG_DEM}" "${LT}" - - "${LS_MAP}" "${INC}" "${PSI}" "${PIX}" - 8 1',
                '  seg_width="$(awk \'$1 == "width:" {print $2; exit}\' "${SEG_DEM_PAR}")"',
                '  seg_lines="$(awk \'$1 == "nlines:" {print $2; exit}\' "${SEG_DEM_PAR}")"',
                '  test -n "${seg_width}"',
                '  test -n "${seg_lines}"',
                '  pixel_area "${REF_MLI_PAR}" "${SEG_DEM_PAR}" "${SEG_DEM}" "${LT}" "${LS_MAP}" "${INC}" "${PIX}" "${GAMMA0}" - - 1',
                '  : >"${BLANK}"',
                '  create_diff_par "${REF_MLI_PAR}" - "${DIFF_PAR}" 1 0 <"${BLANK}"',
                '  offset_pwrm "${GAMMA0}" "${REF_MLI}" "${DIFF_PAR}" "${OFFS}" "${SNR}" 256 256 "${DEM_DIR}/${REF_DATE}.offsets" 1 64 64 0.2',
                '  offset_fitm "${OFFS}" "${SNR}" "${DIFF_PAR}" "${COFFS}" "${COFFSETS}" 0.2 1',
                '  gc_map_fine "${LT}" "${seg_width}" "${DIFF_PAR}" "${LT_FINE}" 1',
                '  geocode "${LT_FINE}" "${SEG_DEM}" "${seg_width}" "${HGT}" "${mli_width}" "${mli_lines}"',
                '  geocode_back "${REF_MLI}" "${mli_width}" "${LT_FINE}" "${REF_GEO}" "${seg_width}" "${seg_lines}" 5 0',
                '  test -s "${LT_FINE}"',
                '  test -s "${HGT}"',
                '  test -s "${SEG_DEM_PAR}"',
                '} >"${LOG_DIR}/${REF_DATE}_expert_dem_lookup.log" 2>&1',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "04_dem_lookup.sh", lines)

    def _write_expert_coreg_prep_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        dates = self._unique_scene_dates(scenes)
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cp -f "${SLC_DIR}/SLC_tab" "${RSLC_DIR}/SLC_tab"',
                ': >"${RSLC_DIR}/dates"',
            ]
        )
        for date in dates:
            lines.append(f'echo "{date}" >>"${{RSLC_DIR}}/dates"')
        lines.extend(
            [
                'cp -f "${SLC_DIR}/${REF_DATE}.slc" "${RSLC_DIR}/${REF_DATE}.rslc"',
                'cp -f "${SLC_DIR}/${REF_DATE}.slc.par" "${RSLC_DIR}/${REF_DATE}.rslc.par"',
                ': >"${RSLC_DIR}/rslc_tab"',
                'printf "%s %s\\n" "${RSLC_DIR}/${REF_DATE}.rslc" "${RSLC_DIR}/${REF_DATE}.rslc.par" >>"${RSLC_DIR}/rslc_tab"',
                'test -s "${RSLC_DIR}/${REF_DATE}.rslc"',
                'test -s "${RSLC_DIR}/${REF_DATE}.rslc.par"',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "05_coreg_prep.sh", lines)

    def _write_expert_coregister_scenes_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        dates = self._unique_scene_dates(scenes)
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(self._bash_array("DATES", dates))
        lines.extend(
            [
                'REF_RSLC="${RSLC_DIR}/${REF_DATE}.rslc"',
                'REF_RSLC_PAR="${RSLC_DIR}/${REF_DATE}.rslc.par"',
                'coreg_scene() {',
                '  local date="$1"',
                '  local slc="${SLC_DIR}/${date}.slc"',
                '  local slc_par="${SLC_DIR}/${date}.slc.par"',
                '  local off="${RSLC_DIR}/${REF_DATE}_${date}.off"',
                '  local offs="${RSLC_DIR}/${REF_DATE}_${date}.offs"',
                '  local snr="${RSLC_DIR}/${REF_DATE}_${date}.snr"',
                '  local coffs="${RSLC_DIR}/${REF_DATE}_${date}.coffs"',
                '  local coffsets="${RSLC_DIR}/${REF_DATE}_${date}.coffsets"',
                '  local rslc="${RSLC_DIR}/${date}.rslc"',
                '  local rslc_par="${RSLC_DIR}/${date}.rslc.par"',
                '  {',
                '    echo "== expert coreg ${date} -> ${REF_DATE} =="',
                '    if [ "${date}" = "${REF_DATE}" ]; then echo "reference scene already prepared"; return; fi',
                '    test -s "${REF_RSLC}"',
                '    test -s "${REF_RSLC_PAR}"',
                '    test -s "${slc}"',
                '    test -s "${slc_par}"',
                '    create_offset "${REF_RSLC_PAR}" "${slc_par}" "${off}" 1 "${RLKS}" "${AZLKS}" 0',
                '    init_offset_orbit "${REF_RSLC_PAR}" "${slc_par}" "${off}"',
                '    init_offset "${REF_RSLC}" "${slc}" "${REF_RSLC_PAR}" "${slc_par}" "${off}" "${RLKS}" "${AZLKS}"',
                '    offset_pwr "${REF_RSLC}" "${slc}" "${REF_RSLC_PAR}" "${slc_par}" "${off}" "${offs}" "${snr}" 64 64 "${RSLC_DIR}/${REF_DATE}_${date}.offsets" 2 64 64 0.2',
                '    offset_fit "${offs}" "${snr}" "${off}" "${coffs}" "${coffsets}" 0.2 1',
                '    SLC_interp "${slc}" "${REF_RSLC_PAR}" "${slc_par}" "${off}" "${rslc}" "${rslc_par}"',
                '    test -s "${rslc}"',
                '    test -s "${rslc_par}"',
                '  } >"${LOG_DIR}/${REF_DATE}_${date}_expert_coreg.log" 2>&1',
                '}',
                'for date in "${DATES[@]}"; do coreg_scene "${date}"; done',
                ': >"${RSLC_DIR}/rslc_tab"',
                'for date in "${DATES[@]}"; do printf "%s %s\\n" "${RSLC_DIR}/${date}.rslc" "${RSLC_DIR}/${date}.rslc.par" >>"${RSLC_DIR}/rslc_tab"; done',
                'test "$(wc -l <"${RSLC_DIR}/rslc_tab")" -eq "${#DATES[@]}"',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "06_coregister_scenes.sh", lines)

    def _write_expert_rmli_average_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cd "${RSLC_DIR}"',
                'mk_mli_all rslc_tab . "${RLKS}" "${AZLKS}" 1 1.0 0.4 mli.ave',
                'width="$(grep range_samples mli.ave.par | awk \'{print $2; exit}\')"',
                'lines="$(grep azimuth_lines mli.ave.par | awk \'{print $2; exit}\')"',
                'test -n "${width}"',
                'test -n "${lines}"',
                'ras_dB mli.ave "${width}" 1 0 1 1 - - gray.cm mli.ave.bmp 0 1',
                'cp -f mli.ave "${MLI_DIR}/mli.ave"',
                'cp -f mli.ave.par "${MLI_DIR}/mli.ave.par"',
                'cp -f mli.ave.bmp "${MLI_DIR}/mli.ave.bmp" || true',
                ': >"${MLI_DIR}/RMLI_tab"',
                'while read -r rslc rslc_par; do',
                '  date="$(basename "${rslc}" .rslc)"',
                '  ln -sf "${RSLC_DIR}/${date}.rmli" "${MLI_DIR}/${date}.rmli"',
                '  ln -sf "${RSLC_DIR}/${date}.rmli.par" "${MLI_DIR}/${date}.rmli.par"',
                '  [ -f "${RSLC_DIR}/${date}.rmli.bmp" ] && ln -sf "${RSLC_DIR}/${date}.rmli.bmp" "${MLI_DIR}/${date}.rmli.bmp" || true',
                '  printf "%s %s\\n" "${MLI_DIR}/${date}.rmli" "${MLI_DIR}/${date}.rmli.par" >>"${MLI_DIR}/RMLI_tab"',
                'done < rslc_tab',
                'test "$(wc -l <"${MLI_DIR}/RMLI_tab")" -gt 0',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "07_rmli_average.sh", lines)

    def _write_expert_diff_network_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cd "${DIFF_DIR}"',
                'ln -sf "${RSLC_DIR}/rslc_tab" rslc_tab',
                'ln -sf "${MLI_DIR}/mli.ave" mli.ave',
                'ln -sf "${MLI_DIR}/mli.ave.par" mli.ave.par',
                'ln -sf "${DEM_DIR}/${REF_DATE}.hgt" "${REF_DATE}.hgt"',
                'base_calc rslc_tab "${RSLC_DIR}/${REF_DATE}.rslc.par" bprep_file itab 1 1 - - 1 3650 1',
                'base_plot rslc_tab "${RSLC_DIR}/${REF_DATE}.rslc.par" itab bprep_file 1',
                'mk_diff_2d rslc_tab itab 0 "${REF_DATE}.hgt" - mli.ave "${MLI_DIR}" . "${RLKS}" "${AZLKS}" 3 1 1 0 -u',
                'test -s itab',
                'ls *.diff > diff.list',
                'test -s diff.list',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "08_diff_network.sh", lines)

    def _write_expert_filter_unwrap_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cd "${DIFF_DIR}"',
                'width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${MLI_DIR}/mli.ave.par")"',
                'lines="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${MLI_DIR}/mli.ave.par")"',
                'test -n "${width}"',
                'test -n "${lines}"',
                'r_seed="$(( width / 2 ))"',
                'a_seed="$(( lines / 2 ))"',
                'mk_adf_2d rslc_tab itab mli.ave . 5 0.6 32 8 -u',
                'ls *.adf.cc > cc.list',
                'test -s cc.list',
                'ave_image cc.list "${width}" mean.cc',
                'rascc_mask mean.cc - "${width}" 1 1 - 1 1 0.20',
                'mk_unw_2d rslc_tab itab mli.ave . 0.20 0 1 1 1 1 "${r_seed}" "${a_seed}" 1 -u',
                'mk_unw_2d rslc_tab itab mli.ave . - - 1 1 1 1 "${r_seed}" "${a_seed}" 1 mean.cc_mask.bmp -u',
                ': > unw.list',
                'while read -r i1 i2 pair_idx use_flag; do',
                '  [ "${use_flag}" = "1" ] || continue',
                '  d1="$(awk -v n="${i1}" \'NR == n {print $1; exit}\' rslc_tab)"',
                '  d2="$(awk -v n="${i2}" \'NR == n {print $1; exit}\' rslc_tab)"',
                '  date1="$(basename "${d1}" .rslc)"',
                '  date2="$(basename "${d2}" .rslc)"',
                '  unw="${date1}_${date2}.adf.unw"',
                '  test -s "${unw}"',
                '  echo "${unw}" >> unw.list',
                'done < itab',
                'test -s unw.list',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "09_filter_unwrap.sh", lines)

    def _write_expert_detrend_atm_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cd "${DIFF_DIR}"',
                'width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${MLI_DIR}/mli.ave.par")"',
                'test -n "${width}"',
                'valid_float_count() {',
                '  local path="$1"',
                '  python - "${path}" <<\'PY\'',
                'import sys',
                'from pathlib import Path',
                'import numpy as np',
                '',
                'path = Path(sys.argv[1])',
                'if not path.is_file():',
                '    print(0)',
                '    raise SystemExit(0)',
                'data = np.fromfile(path, dtype=">f4")',
                'valid = np.isfinite(data) & (data != 0.0) & (np.abs(data) < 1.0e20)',
                'print(int(valid.sum()))',
                'PY',
                '}',
                ': > unw_atmsub_tab',
                'while read -r unw; do',
                '  test -s "${unw}"',
                '  pair="${unw%.adf.unw}"',
                '  off="${pair}.off"',
                '  diff_par="${pair}.diff_par"',
                '  create_diff_par "${off}" "${off}" "${diff_par}" 0 0',
                '  quad_fit "${unw}" "${diff_par}" 5 5 - - 3 "${pair}.unw_linear"',
                '  quad_sub "${unw}" "${diff_par}" "${pair}.unw_sub_linear" 0 0',
                '  rasdt_pwr "${pair}.unw_sub_linear" mli.ave "${width}" 1 - 1 1 -6.28 6.28 1 rmg.cm "${pair}.unw_sub_linear.bmp" 1.0 0.35 8 || true',
                '  unw_sub_linear_valid="$(valid_float_count "${pair}.unw_sub_linear")"',
                '  if [ "${unw_sub_linear_valid}" -le 0 ]; then',
                '    echo "no valid pixels in ${pair}.unw_sub_linear" >&2',
                '    exit 1',
                '  fi',
                '  selected_mfrac=""',
                '  for mfrac in 0.20 0.10 0.05; do',
                '    echo "atm_mod_2d_attempt pair=${pair} mfrac=${mfrac}"',
                '    rm -f "${pair}.a0" "${pair}.a1" "${pair}.atm_sigma" "${pair}.atm_sigma_h" "${pair}.atm_s1" "${pair}.a0_fill" "${pair}.a1_fill" "${pair}.atm_model" "${pair}.unw.atmsub"',
                '    atm_rc=0',
                '    atm_mod_2d "${pair}.unw_sub_linear" "${DEM_DIR}/${REF_DATE}.hgt" "${pair}.adf.cc" "${diff_par}" - 0 "${pair}.a0" "${pair}.a1" "${pair}.atm_sigma" "${pair}.atm_sigma_h" "${pair}.atm_s1" 512 512 64 64 7000 - 0.15 "${mfrac}" - - 1 || atm_rc=$?',
                '    if [ "${atm_rc}" -ne 0 ]; then',
                '      echo "atm_mod_2d failed pair=${pair} mfrac=${mfrac}" >&2',
                '      continue',
                '    fi',
                '    a0_valid="$(valid_float_count "${pair}.a0")"',
                '    a1_valid="$(valid_float_count "${pair}.a1")"',
                '    if [ "${a0_valid}" -le 0 ] && [ "${a1_valid}" -le 0 ]; then',
                '      echo "atm_mod_2d produced no nonzero model coefficients pair=${pair} mfrac=${mfrac} a0_valid=${a0_valid} a1_valid=${a1_valid}" >&2',
                '      continue',
                '    fi',
                '    patch_width="$(awk \'$1 == "offset_estimation_range_samples:" {print $2; exit}\' "${diff_par}")"',
                '    test -n "${patch_width}"',
                '    fill_gaps "${pair}.a0" "${patch_width}" "${pair}.a0_fill" 0 4 0',
                '    fill_gaps "${pair}.a1" "${patch_width}" "${pair}.a1_fill" 0 4 0',
                '    atm_sim_2d "${diff_par}" "${DEM_DIR}/${REF_DATE}.hgt" "${pair}.a0_fill" "${pair}.a1_fill" "${pair}.atm_model" -',
                '    atm_valid="$(valid_float_count "${pair}.atm_model")"',
                '    if [ "${atm_valid}" -le 0 ]; then',
                '      echo "atm_sim_2d produced no nonzero model pair=${pair} mfrac=${mfrac}" >&2',
                '      continue',
                '    fi',
                '    sub_phase "${pair}.unw_sub_linear" "${pair}.atm_model" "${diff_par}" "${pair}.unw.atmsub" 0 0 0',
                '    atmsub_valid="$(valid_float_count "${pair}.unw.atmsub")"',
                '    if [ "${atmsub_valid}" -le 0 ]; then',
                '      echo "sub_phase produced no valid pixels pair=${pair} mfrac=${mfrac}" >&2',
                '      continue',
                '    fi',
                '    selected_mfrac="${mfrac}"',
                '    echo "atm_correction_selected pair=${pair} mfrac=${selected_mfrac} unw_sub_linear_valid=${unw_sub_linear_valid} a0_valid=${a0_valid} a1_valid=${a1_valid} atm_valid=${atm_valid} atmsub_valid=${atmsub_valid}"',
                '    break',
                '  done',
                '  if [ -z "${selected_mfrac}" ]; then',
                '    echo "atmospheric correction failed for ${pair}; tried mfrac 0.20, 0.10, 0.05 and produced no valid ${pair}.unw.atmsub" >&2',
                '    exit 1',
                '  fi',
                '  test -s "${pair}.unw.atmsub"',
                '  echo "${DIFF_DIR}/${pair}.unw.atmsub" >> unw_atmsub_tab',
                'done < unw.list',
                'cp -f unw_atmsub_tab "${SBAS_DIR}/unw_atmsub_tab"',
                'cp -f itab "${SBAS_DIR}/itab"',
                'cp -f "${MLI_DIR}/RMLI_tab" "${SBAS_DIR}/RMLI_tab"',
                'test -s "${SBAS_DIR}/unw_atmsub_tab"',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "10_detrend_atm.sh", lines)

    def _write_expert_sbas_inversion_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        reference_dt = None
        try:
            reference_dt = datetime.strptime(reference_date, "%Y%m%d")
        except ValueError:
            reference_dt = None
        temporal_reference_date = reference_date
        temporal_candidates: list[tuple[int, str]] = []
        for scene in scenes:
            date = str(scene.get("date") or "").strip()
            if not date or date == reference_date:
                continue
            if reference_dt is not None:
                try:
                    delta = abs((datetime.strptime(date, "%Y%m%d") - reference_dt).days)
                except ValueError:
                    delta = 999999
            else:
                delta = len(temporal_candidates)
            temporal_candidates.append((delta, date))
        if temporal_candidates:
            temporal_reference_date = sorted(temporal_candidates, key=lambda item: (item[0], item[1]))[0][1]

        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cd "${SBAS_DIR}"',
                'mkdir -p ras',
                'width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${MLI_DIR}/mli.ave.par")"',
                'lines="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${MLI_DIR}/mli.ave.par")"',
                'test -n "${width}"',
                'test -n "${lines}"',
                f'REFERENCE_WINDOW="{reference_window}"',
                f'GEOM_REF_MLI_PAR="${{MLI_DIR}}/{reference_date}.rmli.par"',
                f'TREF_MLI_PAR="${{MLI_DIR}}/{temporal_reference_date}.rmli.par"',
                'test -s "${GEOM_REF_MLI_PAR}"',
                'test -s "${TREF_MLI_PAR}"',
                'cp -f "${MLI_DIR}/mli.ave.par" mli.ave.par',
                'rm -f diff1.sigma_ts diff2.sigma_ts diff.sigma_ts hgt_correction_1 itab_ts unw.atmsub_1_tab final_unw_tab',
                'rm -f ras/diff*.tab ras/diff*.diff ras/diff*.bmp',
                'WIDTH="${width}" LINES="${lines}" REFERENCE_WINDOW="${REFERENCE_WINDOW}" python - <<\'PY\' > reference_region.txt.tmp',
                'import os',
                'from pathlib import Path',
                'import numpy as np',
                '',
                'width = int(os.environ["WIDTH"])',
                'lines = int(os.environ["LINES"])',
                'requested_window = int(os.environ["REFERENCE_WINDOW"])',
                'candidate_windows = [16, 8, 4]',
                'center_x = width // 2',
                'center_y = lines // 2',
                'pairs = [Path(line.strip()) for line in Path("unw_atmsub_tab").read_text().splitlines() if line.strip()]',
                'if not pairs:',
                    '    raise SystemExit("unw_atmsub_tab is empty")',
                'valid_layers = []',
                'for path in pairs:',
                '    data = np.fromfile(path, dtype=">f4", count=width * lines)',
                '    if data.size != width * lines:',
                '        raise SystemExit(f"incomplete unwrapped phase file: {path}")',
                '    arr = data.reshape((lines, width))',
                '    valid = np.isfinite(arr) & (arr != 0.0) & (np.abs(arr) < 1.0e20)',
                '    valid_layers.append(valid)',
                '',
                'common_valid = np.logical_and.reduce(valid_layers)',
                '',
                'def window_sums(mask, window):',
                '    arr = mask.astype(np.uint8)',
                '    integral = np.pad(arr, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)',
                '    return integral[window:, window:] - integral[:-window, window:] - integral[window:, :-window] + integral[:-window, :-window]',
                '',
                'def best_complete_window(sums, expected, window):',
                '    complete = sums == expected',
                '    if not bool(complete.any()):',
                '        return None',
                '    half = window // 2',
                '    best = None',
                '    for y0 in range(complete.shape[0]):',
                '        xs = np.flatnonzero(complete[y0])',
                '        if xs.size == 0:',
                '            continue',
                '        y = y0 + half',
                '        distances = np.abs(xs + half - center_x) + abs(y - center_y)',
                '        idx = int(np.argmin(distances))',
                '        candidate = (int(distances[idx]), int(xs[idx] + half), int(y))',
                '        if best is None or candidate < best:',
                '            best = candidate',
                '    return best',
                '',
                'diagnostics = []',
                'for window in candidate_windows:',
                '    expected = window * window',
                '    sums = window_sums(common_valid, window)',
                '    max_valid = int(sums.max()) if sums.size else 0',
                '    diagnostics.append(f"{window}x{window}:max={max_valid}/{expected}")',
                '    best = best_complete_window(sums, expected, window)',
                '    if best is not None:',
                '        _, x, y = best',
                '        print(x, y, expected, expected * len(valid_layers), window)',
                '        break',
                'else:',
                '    raise SystemExit("no complete reference window found for allowed windows (minimum 4x4); tried " + ", ".join(diagnostics))',
                'PY',
                'mv -f reference_region.txt.tmp reference_region.txt',
                'read -r r_ref a_ref min_valid total_valid actual_reference_window < reference_region.txt',
                'echo "selected_reference_region range=${r_ref} azimuth=${a_ref} min_valid=${min_valid} total_valid=${total_valid} window=${actual_reference_window} requested_window=${REFERENCE_WINDOW} fallback_ladder=16,8,4"',
                'if [ "${actual_reference_window}" != "${REFERENCE_WINDOW}" ]; then',
                '  echo "reference_window_degraded from=${REFERENCE_WINDOW} to=${actual_reference_window}" >&2',
                'fi',
                'mb unw_atmsub_tab RMLI_tab itab - itab_ts ras/diff1 1 diff1.sigma_ts 1 hgt_correction_1 "${r_ref}" "${a_ref}" "${actual_reference_window}" "${actual_reference_window}" 1.0 "${GEOM_REF_MLI_PAR}" "${TREF_MLI_PAR}" 0',
                ': > unw.atmsub_1_tab',
                'while read -r unw; do',
                '  test -s "${unw}"',
                '  base="$(basename "${unw}" .unw.atmsub)"',
                '  sim="${unw}_sim"',
                '  test -s "${sim}"',
                '  real_to_cpx - "${unw}" "${base}.unw.atmsub.cpx" "${width}" 1',
                '  unw_model "${base}.unw.atmsub.cpx" "${sim}" "${base}.unw.atmsub_1" "${width}" "${r_ref}" "${a_ref}"',
                '  echo "${SBAS_DIR}/${base}.unw.atmsub_1" >> unw.atmsub_1_tab',
                'done < unw_atmsub_tab',
                'mb unw.atmsub_1_tab RMLI_tab itab - itab_ts ras/diff2 1 diff2.sigma_ts 0 - "${r_ref}" "${a_ref}" "${actual_reference_window}" "${actual_reference_window}" 1.0 "${GEOM_REF_MLI_PAR}" "${TREF_MLI_PAR}" 0',
                'cp -f unw.atmsub_1_tab final_unw_tab',
                'mb final_unw_tab RMLI_tab itab - itab_ts ras/diff 0 diff.sigma_ts 0 - "${r_ref}" "${a_ref}" "${actual_reference_window}" "${actual_reference_window}" 0.5 "${GEOM_REF_MLI_PAR}" "${TREF_MLI_PAR}" 0',
                'find ras -maxdepth 1 -type f -name "diff_*.diff" | sort > ras/diff.tab',
                'test -s diff.sigma_ts',
                'test -s itab_ts',
                'test -s ras/diff.tab',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "11_sbas_inversion.sh", lines)

    def _write_expert_outputs_points_script(
        self,
        *,
        run_dir: Path,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
        reference_window: int,
        dem_source: dict[str, Any],
    ) -> Path:
        lines = self._expert_script_header(run_dir, reference_date=reference_date, rlks=rlks, azlks=azlks)
        lines.extend(
            [
                'cd "${SBAS_DIR}"',
                'mkdir -p "${PUBLISH_DIR}/geotiff" "${PUBLISH_DIR}/points"',
                'width="$(awk \'$1 == "range_samples:" {print $2; exit}\' mli.ave.par)"',
                'dem_width="$(awk \'$1 == "width:" {print $2; exit}\' "${DEM_DIR}/${REF_DATE}_seg.dem_par")"',
                'dem_lines="$(awk \'$1 == "nlines:" {print $2; exit}\' "${DEM_DIR}/${REF_DATE}_seg.dem_par")"',
                'test -n "${width}"',
                'test -n "${dem_width}"',
                'test -n "${dem_lines}"',
                'az_lines="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' mli.ave.par)"',
                'test -n "${az_lines}"',
                'replace_values diff.sigma_ts 0.5 0.0 diff.sigma_ts.masked "${width}" 1 2 0',
                'rasdt_pwr diff.sigma_ts.masked - "${width}" 1 0 1 1 0.0 1.5 1 cc.cm diff.sigma_ts.masked.bmp 1.0 0.35 8',
                ': > disp.TS_tab',
                'while read -r item; do',
                '  date="$(basename "${item}")"',
                '  masked="ras/${date}.masked"',
                '  mask_data "${item}" "${width}" "${masked}" diff.sigma_ts.masked.bmp 0',
                '  dispmap "${masked}" - mli.ave.par - "ras/${date}.disp" 0 0',
                '  echo "${SBAS_DIR}/ras/${date}.disp" >> disp.TS_tab',
                'done < ras/diff.tab',
                'ts_rate disp.TS_tab RMLI_tab itab_ts - los_def_rate los_def_const los_def_sigma 0',
                'rasdt_pwr los_def_rate "${MLI_DIR}/mli.ave" "${width}" 1 0 1 1 -0.08 0.08 0 hls.cm los_def_rate.bmp 1.0 0.35 24',
                'geocode_back los_def_rate "${width}" "${DEM_DIR}/${REF_DATE}.lt_fine" geo_los_def_rate "${dem_width}" "${dem_lines}" 5 0',
                'data2geotiff "${DEM_DIR}/${REF_DATE}_seg.dem_par" geo_los_def_rate 2 "${PUBLISH_DIR}/geotiff/geo_los_def_rate.tif"',
                'geocode_back los_def_rate.bmp "${width}" "${DEM_DIR}/${REF_DATE}.lt_fine" geo_los_def_rate.bmp "${dem_width}" "${dem_lines}" 0 2',
                'data2geotiff "${DEM_DIR}/${REF_DATE}_seg.dem_par" geo_los_def_rate.bmp 0 "${PUBLISH_DIR}/geotiff/geo_los_def_rate_rgb.tif"',
                'python3 - "${width}" "${az_lines}" "${PUBLISH_DIR}/points/disp_point_sel.txt" "${PUBLISH_DIR}/points/disp_point_selection.json" <<\'PY\'',
                'import sys',
                'import json',
                'from datetime import datetime',
                'import numpy as np',
                '',
                'width = int(sys.argv[1])',
                'lines = int(sys.argv[2])',
                'selection_txt = sys.argv[3]',
                'selection_json = sys.argv[4]',
                'count = width * lines',
                'rate = np.fromfile("los_def_rate", dtype=">f4", count=count)',
                'sigma = np.fromfile("diff.sigma_ts.masked", dtype=">f4", count=count)',
                'count = min(rate.size, sigma.size, count)',
                'if count < width * lines:',
                '    lines = count // width',
                '    count = width * lines',
                'rate = rate[:count].reshape(lines, width)',
                'sigma = sigma[:count].reshape(lines, width)',
                'yy, xx = np.indices(rate.shape, dtype=np.float32)',
                'edge = (xx > width * 0.08) & (xx < width * 0.92) & (yy > lines * 0.08) & (yy < lines * 0.92)',
                'valid = np.isfinite(rate) & np.isfinite(sigma) & (rate != 0.0) & (sigma > 0.0) & edge',
                'abs_rate = np.abs(rate)',
                'definitions = [',
                '    ("toward_high_rate_low_sigma", "趋近雷达高形变低残差点", "rate > 0，且绝对速率位于高分位，残差低，用于检查明显正向形变区域。"),',
                '    ("away_high_rate_low_sigma", "远离雷达高形变低残差点", "rate < 0，且绝对速率位于高分位，残差低，用于检查明显负向形变区域。"),',
                '    ("high_abs_rate_low_sigma", "高绝对速率低残差点", "不区分正负，优先选择绝对速率高且残差低的有效点。"),',
                '    ("stable_low_sigma", "近零低残差代表点", "绝对速率位于低分位且残差低，用于对照相对稳定区域。"),',
                '    ("center_valid", "覆盖区中心有效点", "从有效像元中选取最接近雷达网格中心的点，用于空间位置对照。"),',
                ']',
                'selected = []',
                'min_dist2 = float(max(32, int(min(width, lines) * 0.08)) ** 2)',
                'def add_point(definition, candidate, score):',
                '    if not np.any(candidate):',
                '        return',
                '    filtered = candidate.copy()',
                '    for existing in selected:',
                '        filtered &= ((xx - float(existing["img_x"])) ** 2 + (yy - float(existing["img_y"])) ** 2) >= min_dist2',
                '    if not np.any(filtered):',
                '        filtered = candidate',
                '    safe_score = np.full(rate.shape, -np.inf, dtype=np.float64)',
                '    safe_score[filtered] = score[filtered]',
                '    if not np.any(np.isfinite(safe_score[filtered])):',
                '        return',
                '    y, x = np.unravel_index(int(np.nanargmax(safe_score)), safe_score.shape)',
                '    point = (int(x), int(y))',
                '    if not any(point[0] == item["img_x"] and point[1] == item["img_y"] for item in selected):',
                '        key, label, description = definition',
                '        selected.append({"img_x": point[0], "img_y": point[1], "selection_key": key, "selection_label": label, "selection_description": description})',
                'if np.any(valid):',
                '    abs_valid = abs_rate[valid]',
                '    sig_valid = sigma[valid]',
                '    high_abs = float(np.percentile(abs_valid, 85))',
                '    low_abs = float(np.percentile(abs_valid, 25))',
                '    low_sigma = float(np.percentile(sig_valid, 40))',
                '    low_sig = valid & (sigma <= low_sigma)',
                '    if not np.any(low_sig):',
                '        low_sig = valid',
                '    denom = np.maximum(sigma.astype(np.float64), 1.0e-6)',
                '    add_point(definitions[0], low_sig & (rate > 0.0) & (abs_rate >= high_abs), rate / denom)',
                '    add_point(definitions[1], low_sig & (rate < 0.0) & (abs_rate >= high_abs), -rate / denom)',
                '    add_point(definitions[2], low_sig & (abs_rate >= high_abs), abs_rate / denom)',
                '    add_point(definitions[3], low_sig & (abs_rate <= low_abs), 1.0 / ((abs_rate + 1.0) * denom))',
                '    cx, cy = (width - 1) / 2.0, (lines - 1) / 2.0',
                '    add_point(definitions[4], valid, -((xx - cx) ** 2 + (yy - cy) ** 2))',
                'if not selected:',
                '    key, label, description = definitions[4]',
                '    selected.append({"img_x": width // 2, "img_y": lines // 2, "selection_key": key, "selection_label": label, "selection_description": description})',
                'selected = selected[:5]',
                'for index, item in enumerate(selected, start=1):',
                '    item["selection_rank"] = index',
                'with open(selection_txt, "w", encoding="utf-8") as handle:',
                '    for item in selected:',
                '        handle.write(f"{item[\'img_x\']} {item[\'img_y\']}\\n")',
                'payload = {"schema": "insar.gamma-sbas-expert-monitor-point-selection/v1", "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "source": "auto_representative_points", "selection_count": len(selected), "strategy": "auto_representative_points", "strategy_note": "自动选取趋近/远离雷达高形变、绝对高形变、近零稳定和中心有效点；时序仍由 Gamma disp_prt_2d 输出。", "points": selected}',
                'with open(selection_json, "w", encoding="utf-8") as handle:',
                '    json.dump(payload, handle, ensure_ascii=False, indent=2)',
                'PY',
                'disp_prt_2d disp.TS_tab RMLI_tab itab_ts - 3 "${PUBLISH_DIR}/points/disp_point_sel.txt" "${DEM_DIR}/${REF_DATE}.hgt" los_def_rate diff.sigma_ts.masked "${PUBLISH_DIR}/points/items.txt" "${PUBLISH_DIR}/points/disp_point.txt" 3 1 0',
                'test -s "${PUBLISH_DIR}/geotiff/geo_los_def_rate.tif"',
                'test -s "${PUBLISH_DIR}/geotiff/geo_los_def_rate_rgb.tif"',
                'test -s "${PUBLISH_DIR}/points/items.txt"',
                'test -s "${PUBLISH_DIR}/points/disp_point.txt"',
                "",
            ]
        )
        return self._write_script(run_dir / "scripts" / "12_outputs_points.sh", lines)

    def _build_monitor_point_config(
        self,
        *,
        monitor_points: list[dict[str, Any]] | None,
        strategy: str,
        stack_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_points = [self._normalize_monitor_point(item, index) for index, item in enumerate(monitor_points or [])]
        if normalized_points:
            mode = "manual_lonlat"
            note = "Manual monitoring points are stored for extraction after geocoded products are available."
        else:
            mode = strategy or "auto_representative_points"
            if mode == "auto_low_sigma_high_rate":
                mode = "auto_representative_points"
            note = (
                "Automatic representative points are report-preview candidates until users provide "
                "a point layer or approve final monitoring locations."
            )
        return {
            "schema": "insar.sbas-monitor-points/v1",
            "mode": mode,
            "points": normalized_points,
            "auto_count": 5,
            "default_auto_strategy": {
                "key": "auto_representative_points",
                "selection": "away/toward/high-absolute-rate/stable/center valid pixels with low sigma and non-edge constraints",
                "usage": "preview candidates only; not a business monitoring network",
            },
            "reference_date": (stack_manifest.get("stack") or {}).get("reference_date"),
            "coordinate_system": "EPSG:4326 for manual lon/lat points; radar coordinates are derived during publishing",
            "note": note,
        }

    def _normalize_monitor_point(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        lon = self._as_float(item.get("lon") if item.get("lon") is not None else item.get("longitude"))
        lat = self._as_float(item.get("lat") if item.get("lat") is not None else item.get("latitude"))
        if lon is None or lat is None:
            raise ValueError(f"monitor point {index + 1} requires lon/lat")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(f"monitor point {index + 1} lon/lat out of range")
        point_id = str(item.get("point_id") or item.get("id") or f"manual_{index + 1:03d}").strip()
        if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", point_id):
            raise ValueError(f"monitor point {index + 1} has invalid point_id")
        return {
            "point_id": point_id,
            "lon": lon,
            "lat": lat,
            "label": str(item.get("label") or point_id).strip()[:120],
            "source": "manual_lonlat",
        }

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @staticmethod
    def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        if not math.isfinite(number):
            number = default
        return max(minimum, min(maximum, number))

    def _write_baseline_audit_script(
        self,
        run_dir: Path,
        *,
        stack_manifest: dict[str, Any],
        rlks: int,
        azlks: int,
        max_delta_n: int,
    ) -> Path:
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        if len(scenes) < 2:
            raise ValueError("baseline audit requires at least two scenes")
        reference_date = str((stack_manifest.get("stack") or {}).get("reference_date") or "").strip()
        if reference_date not in {str(scene.get("date")) for scene in scenes}:
            reference_date = str(scenes[len(scenes) // 2].get("date"))

        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "01_baseline_audit.sh"
        gamma_root = run_dir / "work" / "gamma"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        diff_dir = gamma_root / "diff"
        log_dir = run_dir / "logs"
        python_bin = settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )

        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'SLC_DIR="{self._windows_path_to_wsl_mount(str(slc_dir))}"',
            f'MLI_DIR="{self._windows_path_to_wsl_mount(str(mli_dir))}"',
            f'DIFF_DIR="{self._windows_path_to_wsl_mount(str(diff_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'ORBIT_SCRIPT="${{GAMMA_HOME:-/usr/local/GAMMA_SOFTWARE-20240627}}/ISP/scripts/LT1_precision_orbit.py"',
            f'RLKS="{rlks}"',
            f'AZLKS="{azlks}"',
            f'REF_DATE="{reference_date}"',
            f'MAX_DELTA_N="{max_delta_n}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'ORBIT_SCRIPT="${GAMMA_HOME}/ISP/scripts/LT1_precision_orbit.py"',
            'mkdir -p "${SLC_DIR}" "${MLI_DIR}" "${DIFF_DIR}" "${LOG_DIR}"',
            "",
            "run_scene() {",
            '  local date="$1"',
            '  local tiff="$2"',
            '  local meta="$3"',
            '  local orbit="$4"',
            '  local slc="${SLC_DIR}/${date}.slc"',
            '  local par="${SLC_DIR}/${date}.slc.par"',
            '  local log="${LOG_DIR}/${date}_slc_prepare.log"',
            '  {',
            '    echo "== ${date} SLC prepare =="',
            '    echo "tiff=${tiff}"',
            '    echo "meta=${meta}"',
            '    echo "orbit=${orbit}"',
            '    test -r "${tiff}"',
            '    test -r "${meta}"',
            '    test -r "${orbit}"',
            '    if [ ! -s "${slc}" ] || [ ! -s "${par}" ]; then',
            '      rm -f "${slc}" "${par}"',
            '      par_LT1_SLC "${tiff}" "${meta}" "${par}" "${slc}"',
            "    else",
            '      echo "SLC already exists, skipping par_LT1_SLC"',
            "    fi",
            '    if [ ! -s "${par}.before_precision_orbit" ]; then',
            '      cp -f "${par}" "${par}.before_precision_orbit"',
            '      "${PYTHON_BIN}" "${ORBIT_SCRIPT}" "${par}" "${orbit}"',
            "    else",
            '      echo "Precision-orbit backup exists, assuming orbit correction is already applied"',
            "    fi",
            '    test -s "${slc}"',
            '    test -s "${par}"',
            '    ls -lh "${slc}" "${par}" "${par}.before_precision_orbit"',
            '  } >"${log}" 2>&1',
            "}",
            "",
            "run_multilook() {",
            '  local date="$1"',
            '  local slc="${SLC_DIR}/${date}.slc"',
            '  local slc_par="${SLC_DIR}/${date}.slc.par"',
            '  local mli="${MLI_DIR}/${date}.mli"',
            '  local mli_par="${MLI_DIR}/${date}.mli.par"',
            '  local log="${LOG_DIR}/${date}_multi_look.log"',
            '  {',
            '    echo "== ${date} multi_look rlks=${RLKS} azlks=${AZLKS} =="',
            '    test -s "${slc}"',
            '    test -s "${slc_par}"',
            '    if [ ! -s "${mli}" ] || [ ! -s "${mli_par}" ]; then',
            '      multi_look "${slc}" "${slc_par}" "${mli}" "${mli_par}" "${RLKS}" "${AZLKS}"',
            "    else",
            '      echo "MLI already exists, skipping multi_look"',
            "    fi",
            '    ls -lh "${mli}" "${mli_par}"',
            '  } >"${log}" 2>&1',
            "}",
            "",
        ]
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(
                "run_scene "
                f'"{date}" '
                f'"{scene.get("tiff_wsl")}" '
                f'"{scene.get("meta_wsl")}" '
                f'"{scene.get("orbit_wsl")}"'
            )
        lines.extend(
            [
                "",
                ': >"${SLC_DIR}/SLC_tab"',
            ]
        )
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'printf "%s %s\\n" "${{SLC_DIR}}/{date}.slc" "${{SLC_DIR}}/{date}.slc.par" >>"${{SLC_DIR}}/SLC_tab"')
        lines.append("")
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'run_multilook "{date}"')
        lines.extend(
            [
                "",
                ': >"${MLI_DIR}/RMLI_tab"',
            ]
        )
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'printf "%s %s\\n" "${{MLI_DIR}}/{date}.mli" "${{MLI_DIR}}/{date}.mli.par" >>"${{MLI_DIR}}/RMLI_tab"')
        lines.extend(
            [
                "",
                'base_calc "${SLC_DIR}/SLC_tab" "${SLC_DIR}/${REF_DATE}.slc.par" "${DIFF_DIR}/bperp_all_pairs.txt" "${DIFF_DIR}/itab_all_pairs" 1 0 - - 1 3650 - >"${LOG_DIR}/base_calc_all_pairs.log" 2>&1',
                'base_calc "${SLC_DIR}/SLC_tab" "${SLC_DIR}/${REF_DATE}.slc.par" "${DIFF_DIR}/bperp_adjacent.txt" "${DIFF_DIR}/itab_adjacent" 1 0 - - 1 3650 "${MAX_DELTA_N}" >"${LOG_DIR}/base_calc_adjacent.log" 2>&1',
                'du -h "${SLC_DIR}"/* "${MLI_DIR}"/* "${DIFF_DIR}"/* | sort -h >"${LOG_DIR}/baseline_audit_inventory.txt"',
                'echo "baseline audit complete: ${DIFF_DIR}/bperp_adjacent.txt"',
                "",
            ]
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_coregistration_script(
        self,
        run_dir: Path,
        *,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
    ) -> Path:
        # Legacy bridge writer retained for old stage endpoints; the default LT1 Gamma SBAS
        # workflow now executes the expert-document scripts generated above.
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "02_coreg_common_ref.sh"
        gamma_root = run_dir / "work" / "gamma"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        diff_dir = gamma_root / "diff"
        common_dir = gamma_root / f"common_{reference_date}"
        common_rslc_dir = common_dir / "rslc"
        common_rmli_dir = common_dir / "rmli"
        log_dir = run_dir / "logs"
        python_bin = settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        source_itab = diff_dir / "itab_approved"
        if not source_itab.is_file():
            source_itab = common_dir / "itab_approved"
        dates = [str(scene.get("date") or "") for scene in scenes if scene.get("date")]
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'SLC_DIR="{self._windows_path_to_wsl_mount(str(slc_dir))}"',
            f'MLI_DIR="{self._windows_path_to_wsl_mount(str(mli_dir))}"',
            f'DIFF_DIR="{self._windows_path_to_wsl_mount(str(diff_dir))}"',
            f'COMMON_DIR="{self._windows_path_to_wsl_mount(str(common_dir))}"',
            f'COMMON_RSLC_DIR="{self._windows_path_to_wsl_mount(str(common_rslc_dir))}"',
            f'COMMON_RMLI_DIR="{self._windows_path_to_wsl_mount(str(common_rmli_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'AZLKS="{azlks}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'SLC_COREG="${GAMMA_HOME}/DIFF/scripts/SLC_coreg.py"',
            f'APPROVED_ITAB="{self._windows_path_to_wsl_mount(str(source_itab))}"',
            'test -s "${APPROVED_ITAB}"',
            'mkdir -p "${COMMON_RSLC_DIR}" "${COMMON_RMLI_DIR}" "${LOG_DIR}"',
            "",
            "DATES=(",
        ]
        lines.extend(f'  "{date}"' for date in dates)
        lines.extend(
            [
                ")",
                "",
                'REF_SLC="${SLC_DIR}/${REF_DATE}.slc"',
                'REF_PAR="${SLC_DIR}/${REF_DATE}.slc.par"',
                'REF_MLI_SRC="${MLI_DIR}/${REF_DATE}.mli"',
                'REF_MLI_PAR_SRC="${MLI_DIR}/${REF_DATE}.mli.par"',
                'REF_MLI="${COMMON_RMLI_DIR}/${REF_DATE}.mli"',
                'REF_MLI_PAR="${COMMON_RMLI_DIR}/${REF_DATE}.mli.par"',
                'test -s "${REF_MLI_SRC}"',
                'test -s "${REF_MLI_PAR_SRC}"',
                'cp -f "${REF_MLI_SRC}" "${REF_MLI}"',
                'cp -f "${REF_MLI_PAR_SRC}" "${REF_MLI_PAR}"',
                "",
                "coreg_to_ref() {",
                '  local date="$1"',
                '  local slc="${SLC_DIR}/${date}.slc"',
                '  local par="${SLC_DIR}/${date}.slc.par"',
                '  local rslc="${COMMON_RSLC_DIR}/${date}.rslc"',
                '  local rslc_par="${COMMON_RSLC_DIR}/${date}.rslc.par"',
                '  local rmli="${COMMON_RMLI_DIR}/${date}.mli"',
                '  local rmli_par="${COMMON_RMLI_DIR}/${date}.mli.par"',
                '  local gamma_off="${SLC_DIR}/${date}.slc.off"',
                '  local off="${COMMON_RSLC_DIR}/${date}_to_${REF_DATE}.off"',
                '  local base_mli="${MLI_DIR}/${date}.mli"',
                '  local base_mli_par="${MLI_DIR}/${date}.mli.par"',
                '  {',
                '    echo "== common-reference coreg ${date} -> ${REF_DATE} =="',
                '    test -s "${slc}"',
                '    test -s "${par}"',
                '    test -s "${REF_SLC}"',
                '    test -s "${REF_PAR}"',
                '    if [ "${date}" = "${REF_DATE}" ]; then',
                '      test -s "${REF_MLI}"',
                '      test -s "${REF_MLI_PAR}"',
                '      echo "reference date, no resampling needed"',
                '      return',
                '    fi',
                '    if [ ! -s "${rslc}" ] || [ ! -s "${rslc_par}" ] || [ ! -s "${rmli}" ] || [ ! -s "${rmli_par}" ] || [ ! -s "${off}" ]; then',
                '      rm -f "${rslc}" "${rslc_par}" "${rmli}" "${rmli_par}" "${off}"',
                '      if [ -s "${base_mli}" ] && [ -s "${base_mli_par}" ]; then',
                '        cp -f "${base_mli}" "${rmli}"',
                '        cp -f "${base_mli_par}" "${rmli_par}"',
                '      fi',
                '      "${PYTHON_BIN}" "${SLC_COREG}" \\',
                '        "${slc}" "${par}" \\',
                '        "${rslc}" "${rslc_par}" \\',
                '        "${rmli}" "${rmli_par}" \\',
                '        "${REF_SLC}" "${REF_PAR}" \\',
                '        0.1 "${RLKS}" "${AZLKS}" \\',
                '        --init_offset',
                '      test -s "${gamma_off}"',
                '      cp -f "${gamma_off}" "${off}"',
                "    else",
                '      echo "common-reference RSLC/coreg outputs already exist, skipping"',
                "    fi",
                '    test -s "${rslc}"',
                '    test -s "${rslc_par}"',
                '    test -s "${rmli}"',
                '    test -s "${rmli_par}"',
                '    test -s "${off}"',
                '    ls -lh "${rslc}" "${rslc_par}" "${rmli}" "${rmli_par}" "${off}" "${rslc}.coreg_quality"',
                '  } >"${LOG_DIR}/${date}_to_${REF_DATE}_common_coreg.log" 2>&1',
                "}",
                "",
                "slc_path() {",
                '  local date="$1"',
                '  if [ "${date}" = "${REF_DATE}" ]; then',
                '    printf "%s %s\\n" "${SLC_DIR}/${date}.slc" "${SLC_DIR}/${date}.slc.par"',
                "  else",
                '    printf "%s %s\\n" "${COMMON_RSLC_DIR}/${date}.rslc" "${COMMON_RSLC_DIR}/${date}.rslc.par"',
                "  fi",
                "}",
                "",
                "rmli_path() {",
                '  local date="$1"',
                '  if [ "${date}" = "${REF_DATE}" ]; then',
                '    printf "%s %s\\n" "${COMMON_RMLI_DIR}/${date}.mli" "${COMMON_RMLI_DIR}/${date}.mli.par"',
                "  else",
                '    printf "%s %s\\n" "${COMMON_RMLI_DIR}/${date}.mli" "${COMMON_RMLI_DIR}/${date}.mli.par"',
                "  fi",
                "}",
                "",
                'for date in "${DATES[@]}"; do',
                '  coreg_to_ref "${date}"',
                "done",
                "",
                ': >"${COMMON_DIR}/SLC_tab"',
                ': >"${COMMON_DIR}/RMLI_tab"',
                'for date in "${DATES[@]}"; do',
                '  slc_path "${date}" >>"${COMMON_DIR}/SLC_tab"',
                '  rmli_path "${date}" >>"${COMMON_DIR}/RMLI_tab"',
                "done",
                "",
                'cp -f "${APPROVED_ITAB}" "${COMMON_DIR}/itab_approved"',
                'du -h "${COMMON_DIR}"/* "${COMMON_RSLC_DIR}"/* "${COMMON_RMLI_DIR}"/* 2>/dev/null | sort -h >"${LOG_DIR}/coregistration_inventory.txt"',
                'echo "coregistration script complete: ${COMMON_DIR}"',
                "",
            ]
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_rdc_dem_script(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        rlks: int,
        dem_source: dict[str, Any],
    ) -> Path:
        # Legacy bridge writer retained for old stage endpoints; not used by the
        # default expert-document workflow.
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "03_prepare_rdc_dem.sh"
        gamma_root = run_dir / "work" / "gamma"
        common_dir = gamma_root / f"common_{reference_date}"
        dem_dir = gamma_root / "dem"
        log_dir = run_dir / "logs"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        dem_wsl = str(dem_source.get("wsl_path") or "").strip()
        dem_par_wsl = str(dem_source.get("wsl_par_path") or "").strip()
        if not dem_wsl or not dem_par_wsl:
            raise ValueError("RDC DEM source requires WSL dem and dem.par paths")

        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'COMMON_DIR="{self._windows_path_to_wsl_mount(str(common_dir))}"',
            f'DEM_DIR="{self._windows_path_to_wsl_mount(str(dem_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'DEM_SRC="{dem_wsl}"',
            f'DEM_SRC_PAR="{dem_par_wsl}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'mkdir -p "${DEM_DIR}" "${LOG_DIR}"',
            "",
            'REF_MLI=""',
            'REF_MLI_PAR=""',
            'if [ -s "${COMMON_DIR}/RMLI_tab" ]; then',
            '  REF_MLI="$(awk -v d="${REF_DATE}" \'$1 ~ d "\\\\.mli$" {print $1; exit}\' "${COMMON_DIR}/RMLI_tab")"',
            '  REF_MLI_PAR="$(awk -v d="${REF_DATE}" \'$2 ~ d "\\\\.mli\\\\.par$" {print $2; exit}\' "${COMMON_DIR}/RMLI_tab")"',
            "fi",
            'if [ -z "${REF_MLI}" ]; then',
            '  REF_MLI="${RUN_ROOT}/work/gamma/mli/${REF_DATE}.mli"',
            'fi',
            'if [ -z "${REF_MLI_PAR}" ]; then',
            '  REF_MLI_PAR="${RUN_ROOT}/work/gamma/mli/${REF_DATE}.mli.par"',
            "fi",
            "",
            'DEM_CLEAN="${DEM_DIR}/source_dem_clean.dem"',
            'DEM_CLEAN_PAR="${DEM_CLEAN}.par"',
            'UTMDEM_PAR="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.utm.dem.par"',
            'UTMDEM="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.utm.dem"',
            'UTM2RDC="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.utm_to_rdc0"',
            'SIMSARUTM="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.sim_sar_utm"',
            'PIX="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.pix"',
            'LSMAP="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.ls_map"',
            'SIMSARRDC="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.sim_sar_rdc"',
            'SIMDIFF_PAR="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.diff_par"',
            'SIMOFFS="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.offs"',
            'SIMSNR="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.snr"',
            'SIMOFFSET="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.offset"',
            'SIMCOFF="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.coff"',
            'SIMCOFFSETS="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.coffsets"',
            'UTM_TO_RDC_FINE="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.UTM_TO_RDC"',
            'HGT_RDC="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.rdc.dem"',
            'BLANK="${DEM_DIR}/${REF_DATE}.blank"',
            'OFFSTD="${DEM_DIR}/${REF_DATE}_dem.off_std"',
            "",
            "{",
            '  echo "== prepare RDC DEM for ${REF_DATE} =="',
            '  test -s "${REF_MLI}"',
            '  test -s "${REF_MLI_PAR}"',
            '  test -s "${DEM_SRC}"',
            '  test -s "${DEM_SRC_PAR}"',
            "",
            '  cp -f "${DEM_SRC_PAR}" "${DEM_CLEAN_PAR}"',
            '  dem_width="$(awk \'$1 == "width:" {print $2; exit}\' "${DEM_SRC_PAR}")"',
            '  dem_format="$(awk \'$1 == "data_format:" {print $2; exit}\' "${DEM_SRC_PAR}")"',
            '  if [ -z "${dem_width}" ]; then',
            '    echo "DEM width missing in ${DEM_SRC_PAR}"',
            "    exit 2",
            "  fi",
            '  if [ "${dem_format}" = "INTEGER*2" ]; then',
            '    dem_dtype="4"',
            "  else",
            '    dem_dtype="2"',
            "  fi",
            "",
            '  rm -f "${DEM_CLEAN}"',
            '  replace_values "${DEM_SRC}" -32767 0 "${DEM_CLEAN}" "${dem_width}" 2 "${dem_dtype}"',
            "",
            '  : >"${BLANK}"',
            "",
            '  gc_map1 "${REF_MLI_PAR}" - "${DEM_CLEAN_PAR}" "${DEM_CLEAN}" \\',
            '    "${UTMDEM_PAR}" "${UTMDEM}" "${UTM2RDC}" \\',
            '    1 1 "${SIMSARUTM}" - - - - "${PIX}" "${LSMAP}" - 3 128',
            "",
            '  utm_width="$(awk \'$1 == "width:" {print $2; exit}\' "${UTMDEM_PAR}")"',
            '  rdc_width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${REF_MLI_PAR}")"',
            '  rdc_lines="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${REF_MLI_PAR}")"',
            '  test -n "${utm_width}"',
            '  test -n "${rdc_width}"',
            '  test -n "${rdc_lines}"',
            "",
            '  geocode "${UTM2RDC}" "${SIMSARUTM}" "${utm_width}" "${SIMSARRDC}" \\',
            '    "${rdc_width}" "${rdc_lines}" 0 0 - - 2 64 1',
            "",
            '  create_diff_par "${REF_MLI_PAR}" "${REF_MLI_PAR}" "${SIMDIFF_PAR}" 1 <"${BLANK}"',
            "",
            '  if ! init_offsetm "${SIMSARRDC}" "${REF_MLI}" "${SIMDIFF_PAR}" 2 2 - -; then',
            '    echo "WARNING: init_offsetm returned non-zero; continuing with offset refinement"',
            "  fi",
            "",
            '  offset_pwrm "${SIMSARRDC}" "${REF_MLI}" "${SIMDIFF_PAR}" \\',
            '    "${SIMOFFS}" "${SIMSNR}" 256 256 "${SIMOFFSET}"',
            "",
            '  offset_fitm "${SIMOFFS}" "${SIMSNR}" "${SIMDIFF_PAR}" \\',
            '    "${SIMCOFF}" "${SIMCOFFSETS}" - >"${OFFSTD}"',
            "",
            '  gc_map_fine "${UTM2RDC}" "${utm_width}" "${SIMDIFF_PAR}" "${UTM_TO_RDC_FINE}" 1',
            "",
            '  geocode "${UTM_TO_RDC_FINE}" "${UTMDEM}" "${utm_width}" "${HGT_RDC}" \\',
            '    "${rdc_width}" "${rdc_lines}" 0 0 - - 2 64 1',
            "",
            '  test -s "${HGT_RDC}"',
            '  ls -lh "${HGT_RDC}" "${UTM_TO_RDC_FINE}" "${UTMDEM_PAR}"',
            '} >"${LOG_DIR}/${REF_DATE}_rdc_dem.log" 2>&1',
            "",
            'echo "RDC DEM complete: ${HGT_RDC}"',
            "",
        ]
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_interferogram_script(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        pair_plan: list[dict[str, Any]],
        rlks: int,
        azlks: int,
        unwrap_threshold: float,
    ) -> Path:
        # Legacy bridge writer retained for old stage endpoints; not used by the
        # default expert-document workflow.
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "04_diff_unwrap_common_ref.sh"
        gamma_root = run_dir / "work" / "gamma"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        common_dir = gamma_root / f"common_{reference_date}"
        dem_dir = gamma_root / "dem"
        diff_dir = common_dir / "diff"
        log_dir = run_dir / "logs"
        python_bin = settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'SLC_DIR="{self._windows_path_to_wsl_mount(str(slc_dir))}"',
            f'MLI_DIR="{self._windows_path_to_wsl_mount(str(mli_dir))}"',
            f'COMMON_DIR="{self._windows_path_to_wsl_mount(str(common_dir))}"',
            f'DEM_DIR="{self._windows_path_to_wsl_mount(str(dem_dir))}"',
            f'DIFF_DIR="{self._windows_path_to_wsl_mount(str(diff_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'AZLKS="{azlks}"',
            f'UNWRAP_THRESHOLD="{unwrap_threshold:.3f}"',
            'SPS_FLAG="${SPS_FLAG:-1}"',
            'AZF_FLAG="${AZF_FLAG:-0}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'mkdir -p "${DIFF_DIR}" "${LOG_DIR}"',
            'HGT="${DEM_DIR}/${REF_DATE}_${RLKS}rlks.rdc.dem"',
            "",
            "slc_for_date() {",
            '  local date="$1"',
            '  if [ "${date}" = "${REF_DATE}" ]; then',
            '    printf "%s %s\\n" "${SLC_DIR}/${date}.slc" "${SLC_DIR}/${date}.slc.par"',
            "  else",
            '    printf "%s %s\\n" "${COMMON_DIR}/rslc/${date}.rslc" "${COMMON_DIR}/rslc/${date}.rslc.par"',
            "  fi",
            "}",
            "",
            "mli_for_date() {",
            '  local date="$1"',
            '  if [ "${date}" = "${REF_DATE}" ]; then',
            '    printf "%s %s\\n" "${MLI_DIR}/${date}.mli" "${MLI_DIR}/${date}.mli.par"',
            "  else",
            '    printf "%s %s\\n" "${COMMON_DIR}/rmli/${date}.mli" "${COMMON_DIR}/rmli/${date}.mli.par"',
            "  fi",
            "}",
            "",
            "cc_stats() {",
            '  local cc="$1"',
            '  local out="$2"',
            '  "${PYTHON_BIN}" - "${cc}" >"${out}" <<\'PY\'',
            "import json",
            "import sys",
            "from pathlib import Path",
            "import numpy as np",
            "",
            "path = Path(sys.argv[1])",
            "data = np.fromfile(path, dtype='>f4')",
            "finite = data[np.isfinite(data)]",
            "nonzero = finite[finite != 0]",
            "payload = {",
            "    'path': str(path),",
            "    'pixels': int(data.size),",
            "    'finite_pixels': int(finite.size),",
            "    'nonzero_pixels': int(nonzero.size),",
            "}",
            "if finite.size:",
            "    payload.update({",
            "        'min': float(np.min(finite)),",
            "        'median': float(np.median(finite)),",
            "        'max': float(np.max(finite)),",
            "    })",
            "print(json.dumps(payload, ensure_ascii=False, indent=2))",
            "PY",
            "}",
            "",
            "diff_unwrap_pair() {",
            '  local master_date="$1"',
            '  local slave_date="$2"',
            '  local pair="${master_date}_${slave_date}"',
            '  local slc1 slc1_par slc2 slc2_par mli1 mli1_par mli2 mli2_par',
            '  read -r slc1 slc1_par < <(slc_for_date "${master_date}")',
            '  read -r slc2 slc2_par < <(slc_for_date "${slave_date}")',
            '  read -r mli1 mli1_par < <(mli_for_date "${master_date}")',
            '  read -r mli2 mli2_par < <(mli_for_date "${slave_date}")',
            "",
            '  local work_dir="${DIFF_DIR}/${pair}"',
            '  local off="${work_dir}/${pair}_${RLKS}rlks.off"',
            '  local sim_unw="${work_dir}/${pair}.sim_unw"',
            '  local diff="${work_dir}/${pair}_${RLKS}rlks.diff"',
            '  local diff_filt="${work_dir}/${pair}_${RLKS}rlks.diff_filt"',
            '  local cc="${work_dir}/${pair}_${RLKS}rlks.diff_filt.cor"',
            '  local mask="${work_dir}/${pair}_${RLKS}rlks.diff_filt.cor_mask.bmp"',
            '  local unw="${work_dir}/${pair}_${RLKS}rlks.diff_filt.unw"',
            '  local width lines r_ref a_ref',
            "",
            '  mkdir -p "${work_dir}"',
            '  width="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${mli1_par}")"',
            '  lines="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${mli1_par}")"',
            '  r_ref="$(( width / 2 ))"',
            '  a_ref="$(( lines / 2 ))"',
            "",
            "  {",
            '    echo "== differential unwrap ${pair} =="',
            '    echo "width=${width} lines=${lines} threshold=${UNWRAP_THRESHOLD}"',
            '    test -s "${slc1}"',
            '    test -s "${slc1_par}"',
            '    test -s "${slc2}"',
            '    test -s "${slc2_par}"',
            '    test -s "${mli1}"',
            '    test -s "${mli2}"',
            '    test -s "${HGT}"',
            "",
            '    create_offset "${slc1_par}" "${slc2_par}" "${off}" 1 "${RLKS}" "${AZLKS}" 0',
            '    phase_sim_orb "${slc1_par}" "${slc2_par}" "${off}" "${HGT}" "${sim_unw}" "${SLC_DIR}/${REF_DATE}.slc.par" - - 1 1',
            '    SLC_diff_intf "${slc1}" "${slc2}" "${slc1_par}" "${slc2_par}" "${off}" "${sim_unw}" \\',
            '      "${diff}" "${RLKS}" "${AZLKS}" "${SPS_FLAG}" "${AZF_FLAG}" - 1 1',
            '    adf "${diff}" "${diff_filt}" "${cc}" "${width}" 0.4 - 5',
            '    cc_wave "${diff_filt}" "${mli1}" "${mli2}" "${cc}" "${width}" 5 5',
            '    rasmph_pwr "${diff_filt}" "${mli1}" "${width}" - - - - rmg.cm "${diff_filt}.bmp" 1.0 0.35 8',
            '    rasdt_pwr "${cc}" "${mli1}" "${width}" 1 0 1 1 0.1 1.0 1 cc.cm "${cc}.bmp" 1.0 0.35 8',
            '    rascc_mask "${cc}" "${mli1}" "${width}" 1 1 0 1 1 "${UNWRAP_THRESHOLD}" 0.0 0.1 0.9 1 .35 1 "${mask}"',
            '    mcf "${diff_filt}" "${cc}" "${mask}" "${unw}" "${width}" 2 0 0 "${width}" "${lines}" 1 1 - "${r_ref}" "${a_ref}" 1',
            '    rasdt_pwr "${unw}" "${mli1}" "${width}" 1 0 1 1 -3.14 3.14 1 rmg.cm "${unw}.bmp" 1.0 0.35 8',
            '    ls -lh "${sim_unw}" "${diff}" "${diff_filt}" "${cc}" "${mask}" "${unw}"',
            '  } >"${LOG_DIR}/${pair}_diff_unwrap_common.log" 2>&1',
            "",
            (
                '  cc_stats "${cc}" "${LOG_DIR}/${pair}_diff_filt_cc_stats.json" || '
                'printf \'{"path":"%s","error":"cc_stats_failed"}\\n\' "${cc}" >"${LOG_DIR}/${pair}_diff_filt_cc_stats.json"'
            ),
            '  echo "completed ${pair}"',
            "}",
            "",
            "PAIR_ROWS=(",
        ]
        for pair in pair_plan:
            lines.append(
                "  "
                f'"{pair.get("master_date")} {pair.get("slave_date")} {pair.get("itab_row", [None, None, None, None])[2]}"'
            )
        lines.extend(
            [
                ")",
                "",
                'for row in "${PAIR_ROWS[@]}"; do',
                "  read -r master_date slave_date pair_index <<<\"${row}\"",
                '  diff_unwrap_pair "${master_date}" "${slave_date}"',
                "done",
                "",
                'DIFF_TAB="${COMMON_DIR}/DIFF_tab"',
                'ITAB="${COMMON_DIR}/itab_common_ref"',
                ': >"${DIFF_TAB}"',
                ': >"${ITAB}"',
            ]
        )
        for pair in pair_plan:
            pair_id = str(pair.get("pair_id") or "")
            itab_row = pair.get("itab_row") or []
            lines.append(f'echo "${{DIFF_DIR}}/{pair_id}/{pair_id}_${{RLKS}}rlks.diff_filt.unw" >>"${{DIFF_TAB}}"')
            if len(itab_row) >= 4:
                lines.append(f'echo "{itab_row[0]} {itab_row[1]} {itab_row[2]} {itab_row[3]}" >>"${{ITAB}}"')
        lines.extend(
            [
                "",
                'test "$(wc -l <"${DIFF_TAB}")" -eq "${#PAIR_ROWS[@]}"',
                'test "$(wc -l <"${ITAB}")" -eq "${#PAIR_ROWS[@]}"',
                'echo "Common-reference differential/unwrapped stack complete: ${COMMON_DIR}"',
                "",
            ]
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_ipta_timeseries_script(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        rlks: int,
        reference_window: int,
        diff_tab: Path,
        rmli_tab: Path,
        itab: Path,
        geom_ref_mli_par: Path,
        mb_ref_mli_par: Path,
        reference_region: dict[str, Any],
        mb_mode: int,
    ) -> Path:
        mb_mode = self._normalize_ipta_mb_mode(mb_mode)
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "05_mb_ts_rate.sh"
        gamma_root = run_dir / "work" / "gamma"
        common_dir = gamma_root / f"common_{reference_date}"
        timeseries_dir = common_dir / "timeseries"
        log_dir = run_dir / "logs"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'COMMON_DIR="{self._windows_path_to_wsl_mount(str(common_dir))}"',
            f'TS_DIR="{self._windows_path_to_wsl_mount(str(timeseries_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'REFERENCE_WINDOW="{reference_window}"',
            f'R_REF="{int(reference_region.get("range_pixel") or 0)}"',
            f'A_REF="{int(reference_region.get("azimuth_line") or 0)}"',
            f'MB_MODE="{mb_mode}"',
            f'DIFF_TAB="{self._windows_path_to_wsl_mount(str(diff_tab))}"',
            f'RMLI_TAB="{self._windows_path_to_wsl_mount(str(rmli_tab))}"',
            f'ITAB="{self._windows_path_to_wsl_mount(str(itab))}"',
            f'GEOM_REF_MLI_PAR="{self._windows_path_to_wsl_mount(str(geom_ref_mli_par))}"',
            f'REF_MLI_PAR="{self._windows_path_to_wsl_mount(str(mb_ref_mli_par))}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'mkdir -p "${TS_DIR}" "${LOG_DIR}"',
            "",
            'ITAB_TS="${TS_DIR}/itab_ts"',
            'DIFF_TS="${TS_DIR}/diff_ts"',
            'SIGMA_TS="${TS_DIR}/sigma_ts"',
            'HGT_OUT="${TS_DIR}/hgt_correction"',
            'RATE="${TS_DIR}/ts_rate"',
            'CONST="${TS_DIR}/ts_const"',
            'SIGMA_RATE="${TS_DIR}/sigma_rate"',
            'WIDTH="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${GEOM_REF_MLI_PAR}")"',
            'LINES="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${GEOM_REF_MLI_PAR}")"',
            'rm -f "${ITAB_TS}" "${DIFF_TS}.tab" "${DIFF_TS}"_*.diff "${DIFF_TS}"_*.diff_sim \\',
            '  "${SIGMA_TS}" "${HGT_OUT}" "${RATE}" "${CONST}" "${SIGMA_RATE}"',
            "",
            "{",
            '  echo "== Gamma mb time-series =="',
            '  echo "width=${WIDTH} lines=${LINES} ref_region=${R_REF},${A_REF}"',
            '  echo "geometry_reference=${GEOM_REF_MLI_PAR}"',
            '  echo "mb_reference=${REF_MLI_PAR}"',
            '  echo "mb_mode=${MB_MODE}"',
            '  test "${R_REF}" -gt 0',
            '  test "${A_REF}" -gt 0',
            '  test -s "${DIFF_TAB}"',
            '  test -s "${RMLI_TAB}"',
            '  test -s "${ITAB}"',
            '  test -s "${GEOM_REF_MLI_PAR}"',
            '  test -s "${REF_MLI_PAR}"',
            '  mb "${DIFF_TAB}" "${RMLI_TAB}" "${ITAB}" - \\',
            '    "${ITAB_TS}" "${DIFF_TS}" 1 "${SIGMA_TS}" 1 "${HGT_OUT}" \\',
            '    "${R_REF}" "${A_REF}" "${REFERENCE_WINDOW}" "${REFERENCE_WINDOW}" 1.0 "${GEOM_REF_MLI_PAR}" "${REF_MLI_PAR}" "${MB_MODE}"',
            '  test -s "${DIFF_TS}.tab"',
            '  test -s "${ITAB_TS}"',
            '  test -s "${SIGMA_TS}"',
            '  test -s "${HGT_OUT}"',
            '  ls -lh "${DIFF_TS}.tab" "${ITAB_TS}" "${SIGMA_TS}" "${HGT_OUT}"',
            "",
            '  echo "== Gamma ts_rate =="',
            '  ts_rate "${DIFF_TS}.tab" "${RMLI_TAB}" "${ITAB_TS}" \\',
            '    - "${RATE}" "${CONST}" "${SIGMA_RATE}" 1',
            '  test -s "${RATE}"',
            '  test -s "${CONST}"',
            '  test -s "${SIGMA_RATE}"',
            '  ls -lh "${RATE}" "${CONST}" "${SIGMA_RATE}"',
            '} >"${LOG_DIR}/mb_ts_rate.log" 2>&1',
            "",
            'echo "Gamma mb/ts_rate complete: ${TS_DIR}"',
            "",
        ]
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_detrend_atm_script(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        rlks: int,
        reference_window: int,
        reference_region: dict[str, Any],
        coherence_min: float,
        diff_tab: Path,
        itab: Path,
        rmli_path: Path,
        rmli_par_path: Path,
        hgt_path: Path,
        pair_plan: list[dict[str, Any]],
    ) -> Path:
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "05_detrend_atm.sh"
        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        detrend_dir = common_dir / "detrend_atm"
        log_dir = run_dir / "logs"
        env_script = (
            self._windows_path_to_wsl_mount(settings.GAMMA_SBAS_ENV_SCRIPT or settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        python_bin = settings.GAMMA_SBAS_PYTHON or settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        r_ref = int(reference_region.get("range_pixel") or 0)
        a_ref = int(reference_region.get("azimuth_line") or 0)
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'COMMON_DIR="{self._windows_path_to_wsl_mount(str(common_dir))}"',
            f'DETREND_DIR="{self._windows_path_to_wsl_mount(str(detrend_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'DIFF_TAB="{self._windows_path_to_wsl_mount(str(diff_tab))}"',
            f'ITAB="{self._windows_path_to_wsl_mount(str(itab))}"',
            f'MLI="{self._windows_path_to_wsl_mount(str(rmli_path))}"',
            f'MLI_PAR="{self._windows_path_to_wsl_mount(str(rmli_par_path))}"',
            f'HGT="{self._windows_path_to_wsl_mount(str(hgt_path))}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'REFERENCE_WINDOW="{reference_window}"',
            f'R_REF="{r_ref}"',
            f'A_REF="{a_ref}"',
            f'CC_MIN="{coherence_min:.6g}"',
            f'PYTHON_BIN="{python_bin}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'mkdir -p "${DETREND_DIR}" "${LOG_DIR}"',
            'WIDTH="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${MLI_PAR}")"',
            'LINES="$(awk \'$1 == "azimuth_lines:" {print $2; exit}\' "${MLI_PAR}")"',
            'test -n "${WIDTH}"',
            'test -n "${LINES}"',
            'test -s "${DIFF_TAB}"',
            'test -s "${ITAB}"',
            'test -s "${MLI}"',
            'test -s "${MLI_PAR}"',
            'test -s "${HGT}"',
            'ATMSUB_TAB="${COMMON_DIR}/DIFF_atmsub_tab"',
            'ITAB_ATMSUB="${COMMON_DIR}/itab_atmsub"',
            ': >"${ATMSUB_TAB}"',
            'cp -f "${ITAB}" "${ITAB_ATMSUB}"',
            "",
            "infer_model_width() {",
            '  local model_file="$1"',
            '  local byte_count',
            '  byte_count="$(wc -c <"${model_file}")"',
            '  local pixels=$((byte_count / 4))',
            '  if [ "${pixels}" -le 0 ]; then',
            '    echo 0',
            '    return',
            '  fi',
            '  "${PYTHON_BIN}" - "$pixels" "$WIDTH" "$LINES" <<\'PY\'',
            "import math, sys",
            "pixels = int(sys.argv[1])",
            "width = max(1, int(float(sys.argv[2])))",
            "lines = max(1, int(float(sys.argv[3])))",
            "target = width / lines",
            "best = None",
            "for w in range(1, int(math.sqrt(pixels)) + 2):",
            "    if pixels % w:",
            "        continue",
            "    for cand in (w, pixels // w):",
            "        h = pixels // cand",
            "        score = abs((cand / h) - target)",
            "        if best is None or score < best[0]:",
            "            best = (score, cand)",
            "print(best[1] if best else 0)",
            "PY",
            "}",
            "",
            "fill_model_if_possible() {",
            '  local in_file="$1"',
            '  local out_file="$2"',
            '  local model_width',
            '  model_width="$(infer_model_width "${in_file}")"',
            '  if [ "${model_width}" -gt 0 ]; then',
            '    if fill_gaps "${in_file}" "${model_width}" "${out_file}" 0 4 0 0; then',
            '      return',
            '    fi',
            '    echo "fill_gaps failed for ${in_file}; using raw model coefficients" >&2',
            '  else',
            '    echo "could not infer model width for ${in_file}; using raw model coefficients" >&2',
            '  fi',
            '  cp -f "${in_file}" "${out_file}"',
            "}",
            "",
            "run_pair() {",
            '  local pair="$1"',
            '  local unw="$2"',
            '  local cor="$3"',
            '  local off="$4"',
            '  local pair_dir="${DETREND_DIR}/${pair}"',
            '  local diff_par="${pair_dir}/${pair}.diff_par"',
            '  local linear="${pair_dir}/${pair}.unw_linear"',
            '  local sub_linear="${pair_dir}/${pair}.unw_sub_linear"',
            '  local a0="${pair_dir}/${pair}.a0"',
            '  local a1="${pair_dir}/${pair}.a1"',
            '  local a0_fill="${pair_dir}/${pair}.a0_fill"',
            '  local a1_fill="${pair_dir}/${pair}.a1_fill"',
            '  local sigma="${pair_dir}/${pair}.atm_sigma"',
            '  local sigma_h="${pair_dir}/${pair}.atm_sigma_h"',
            '  local s1="${pair_dir}/${pair}.atm_s1"',
            '  local atm_model="${pair_dir}/${pair}.atm_model"',
            '  local atmsub="${pair_dir}/${pair}_${RLKS}rlks.diff_filt.unw.atmsub"',
            '  local log="${LOG_DIR}/${pair}_detrend_atm.log"',
            '  mkdir -p "${pair_dir}"',
            '  {',
            '    echo "== detrend/atm ${pair} =="',
            '    echo "unw=${unw}"',
            '    echo "cor=${cor}"',
            '    echo "off=${off}"',
            '    test -s "${unw}"',
            '    test -s "${cor}"',
            '    test -s "${off}"',
            '    create_diff_par "${off}" "${off}" "${diff_par}" 0 0',
            '    quad_fit "${unw}" "${diff_par}" 5 5 - - 3 "${linear}"',
            '    quad_sub "${unw}" "${diff_par}" "${sub_linear}" 0 0',
            '    rasdt_pwr "${sub_linear}" "${MLI}" "${WIDTH}" 1 - 1 1 -6.28 6.28 1 rmg.cm "${sub_linear}.bmp" 1.0 0.35 24',
            '    atm_mod_2d "${sub_linear}" "${HGT}" "${cor}" "${diff_par}" - 0 "${a0}" "${a1}" "${sigma}" "${sigma_h}" "${s1}" 512 512 64 64 7000 - "${CC_MIN}" 0.20 "${R_REF}" "${A_REF}" 1',
            '    test -s "${a0}"',
            '    test -s "${a1}"',
            '    fill_model_if_possible "${a0}" "${a0_fill}"',
            '    fill_model_if_possible "${a1}" "${a1_fill}"',
            '    atm_sim_2d "${diff_par}" "${HGT}" "${a0_fill}" "${a1_fill}" "${atm_model}" -',
            '    sub_phase "${sub_linear}" "${atm_model}" "${diff_par}" "${atmsub}" 0 0 0',
            '    rasdt_pwr "${atmsub}" "${MLI}" "${WIDTH}" 1 - 1 1 -6.28 6.28 1 rmg.cm "${atmsub}.bmp" 1.0 0.35 24',
            '    test -s "${atmsub}"',
            '    printf "%s\\n" "${atmsub}" >>"${ATMSUB_TAB}"',
            '    ls -lh "${diff_par}" "${linear}" "${sub_linear}" "${a0}" "${a1}" "${atm_model}" "${atmsub}"',
            '  } >"${log}" 2>&1',
            "}",
            "",
        ]
        for pair in pair_plan:
            lines.append(
                "run_pair "
                f'"{pair.get("pair_id")}" '
                f'"{self._windows_path_to_wsl_mount(str(pair.get("unw") or ""))}" '
                f'"{self._windows_path_to_wsl_mount(str(pair.get("cor") or ""))}" '
                f'"{self._windows_path_to_wsl_mount(str(pair.get("offset") or ""))}"'
            )
        lines.extend(
            [
                "",
                'test "$(wc -l <"${ATMSUB_TAB}")" -eq "$(wc -l <"${DIFF_TAB}")"',
                'du -h "${DETREND_DIR}"/*/* "${ATMSUB_TAB}" "${ITAB_ATMSUB}" 2>/dev/null | sort -h >"${LOG_DIR}/detrend_atm_inventory.txt"',
                'echo "detrend/atm complete: ${ATMSUB_TAB}"',
                "",
            ]
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_publish_products_script(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        rlks: int,
        timeseries_dir: Path,
        rmli_path: Path,
        rmli_par_path: Path,
        slc_par_path: Path,
        dem_par_path: Path,
        lookup_path: Path,
        wavelength: float,
    ) -> Path:
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "07_publish_products.sh"
        export_dir = run_dir / "publish" / "geotiff"
        log_dir = run_dir / "logs"
        env_script = (
            self._windows_path_to_wsl_mount(settings.GAMMA_SBAS_ENV_SCRIPT or settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        python_bin = settings.GAMMA_SBAS_PYTHON or settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        tool_script = Path(settings.PROJECT_ROOT) / "deploy" / "wsl" / "runners" / "gamma_sbas_product_tools.py"
        phase_to_los = wavelength / (4.0 * math.pi)
        stack_manifest = self._read_optional_json(run_dir / "stack_manifest.json")
        stack_dates = self._stack_dates(stack_manifest)
        date_start = min(stack_dates, default="")
        date_end = max(stack_dates, default="")
        coverage = self._build_stack_geographic_coverage(stack_manifest)
        admin_region = coverage.get("admin_region") or {}
        admin_province = str(admin_region.get("province") or "").strip()
        admin_city = str(admin_region.get("city") or "").strip()
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'TS_DIR="{self._windows_path_to_wsl_mount(str(timeseries_dir))}"',
            f'EXPORT_DIR="{self._windows_path_to_wsl_mount(str(export_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'MLI="{self._windows_path_to_wsl_mount(str(rmli_path))}"',
            f'MLI_PAR="{self._windows_path_to_wsl_mount(str(rmli_par_path))}"',
            f'SLC_PAR="{self._windows_path_to_wsl_mount(str(slc_par_path))}"',
            f'DEM_PAR="{self._windows_path_to_wsl_mount(str(dem_par_path))}"',
            f'LOOKUP="{self._windows_path_to_wsl_mount(str(lookup_path))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'TOOL_SCRIPT="{self._windows_path_to_wsl_mount(str(tool_script))}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'WAVELENGTH="{wavelength:.12g}"',
            f'PHASE_TO_LOS="{phase_to_los:.12g}"',
            f'DATE_START="{date_start}"',
            f'DATE_END="{date_end}"',
            f'ADMIN_PROVINCE="{admin_province}"',
            f'ADMIN_CITY="{admin_city}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'VECTOR_DIR="${RUN_ROOT}/publish/vectors"',
            'mkdir -p "${EXPORT_DIR}" "${VECTOR_DIR}" "${LOG_DIR}"',
            "",
            'RDC_WIDTH="$(awk \'$1 == "range_samples:" {print $2; exit}\' "${MLI_PAR}")"',
            'GEO_WIDTH="$(awk \'$1 == "width:" {print $2; exit}\' "${DEM_PAR}")"',
            'GEO_LINES="$(awk \'$1 == "nlines:" {print $2; exit}\' "${DEM_PAR}")"',
            "",
            "geo_float() {",
            '  local in_file="$1"',
            '  local out_root="$2"',
            '  local geo_bin="${EXPORT_DIR}/${out_root}.geo"',
            '  local tif="${EXPORT_DIR}/${out_root}.tif"',
            '  geocode_back "${in_file}" "${RDC_WIDTH}" "${LOOKUP}" "${geo_bin}" "${GEO_WIDTH}" "${GEO_LINES}" 1 0',
            '  data2geotiff "${DEM_PAR}" "${geo_bin}" 2 "${tif}" - 1',
            '  if command -v gdalinfo >/dev/null 2>&1; then',
            '    gdalinfo "${tif}" >"${EXPORT_DIR}/${out_root}.gdalinfo.txt"',
            "  fi",
            "}",
            "",
            "geo_bmp_rgb() {",
            '  local in_bmp="$1"',
            '  local out_root="$2"',
            '  local geo_bmp="${EXPORT_DIR}/${out_root}.geo.bmp"',
            '  local rgb_tif="${EXPORT_DIR}/${out_root}.geo_rgb.tif"',
            '  local png="${EXPORT_DIR}/${out_root}.geo_preview.png"',
            '  geocode_back "${in_bmp}" "${RDC_WIDTH}" "${LOOKUP}" "${geo_bmp}" "${GEO_WIDTH}" "${GEO_LINES}" 0 2',
            '  data2geotiff "${DEM_PAR}" "${geo_bmp}" 0 "${rgb_tif}"',
            '  if command -v gdal_translate >/dev/null 2>&1; then',
            '    gdal_translate -of PNG -outsize 1400 0 "${rgb_tif}" "${png}" >/dev/null',
            "  fi",
            "}",
            "",
            "make_preview() {",
            '  local tif="$1"',
            '  local cmap="$2"',
            '  local png="$3"',
            '  local tmp_tif="${png%.png}.rgba.tif"',
            '  if command -v gdaldem >/dev/null 2>&1 && command -v gdal_translate >/dev/null 2>&1; then',
            '    gdaldem color-relief -alpha -nearest_color_entry "${tif}" "${cmap}" "${tmp_tif}"',
            '    gdal_translate -of PNG -outsize 1400 0 "${tmp_tif}" "${png}" >/dev/null',
            '    rm -f "${tmp_tif}"',
            "  else",
            '    "${PYTHON_BIN}" - "${tif}" "${png}" <<\'PY\'',
            "import sys",
            "from pathlib import Path",
            "import matplotlib",
            "matplotlib.use('Agg')",
            "import matplotlib.pyplot as plt",
            "import numpy as np",
            "try:",
            "    import rasterio",
            "    with rasterio.open(sys.argv[1]) as src:",
            "        arr = src.read(1)",
            "except Exception:",
            "    arr = np.fromfile(sys.argv[1], dtype='>f4')",
            "arr = np.where(np.isfinite(arr), arr, np.nan)",
            "plt.figure(figsize=(10, 7), dpi=140)",
            "plt.imshow(arr, cmap='RdYlBu_r')",
            "plt.colorbar(shrink=0.75)",
            "plt.axis('off')",
            "Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)",
            "plt.tight_layout(pad=0)",
            "plt.savefig(sys.argv[2], bbox_inches='tight', pad_inches=0.02)",
            "PY",
            "  fi",
            "}",
            "",
            'RATE_CMAP="${EXPORT_DIR}/los_rate_toward_mm_per_year.preview.cmap.txt"',
            'SIGMA_CMAP="${EXPORT_DIR}/los_sigma_mm_per_year.preview.cmap.txt"',
            'cat >"${RATE_CMAP}" <<\'EOF\'',
            "-100 49 54 149 255",
            "-75 69 117 180 255",
            "-50 116 173 209 255",
            "-25 224 243 248 255",
            "0 255 255 255 255",
            "25 254 224 144 255",
            "50 253 174 97 255",
            "75 215 48 39 255",
            "100 165 0 38 255",
            "nv 0 0 0 0",
            "EOF",
            'cat >"${SIGMA_CMAP}" <<\'EOF\'',
            "0 247 252 245 255",
            "5 229 245 249 255",
            "10 204 236 230 255",
            "20 153 216 201 255",
            "30 102 194 164 255",
            "45 44 162 95 255",
            "60 0 109 44 255",
            "90 84 39 136 255",
            "nv 0 0 0 0",
            "EOF",
            "",
            "{",
            '  echo "== publish Gamma SBAS products =="',
            '  echo "REF_DATE=${REF_DATE} RLKS=${RLKS}"',
            '  echo "RDC_WIDTH=${RDC_WIDTH} GEO_WIDTH=${GEO_WIDTH} GEO_LINES=${GEO_LINES}"',
            '  echo "WAVELENGTH=${WAVELENGTH} PHASE_TO_LOS=${PHASE_TO_LOS}"',
            '  test -s "${MLI}"',
            '  test -s "${MLI_PAR}"',
            '  test -s "${SLC_PAR}"',
            '  test -s "${DEM_PAR}"',
            '  test -s "${LOOKUP}"',
            '  test -s "${TS_DIR}/ts_rate"',
            '  test -s "${TS_DIR}/sigma_rate"',
            "",
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${TS_DIR}/ts_rate" "${EXPORT_DIR}/los_rate_m_per_year.rdc" "${PHASE_TO_LOS}"',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${TS_DIR}/sigma_rate" "${EXPORT_DIR}/los_sigma_m_per_year.rdc" "${PHASE_TO_LOS}"',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${EXPORT_DIR}/los_rate_m_per_year.rdc" "${EXPORT_DIR}/los_rate_away_m_per_year.rdc" 1.0',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${EXPORT_DIR}/los_rate_m_per_year.rdc" "${EXPORT_DIR}/los_rate_toward_m_per_year.rdc" -1.0',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${EXPORT_DIR}/los_rate_m_per_year.rdc" "${EXPORT_DIR}/los_rate_away_mm_per_year.rdc" 1000.0',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${EXPORT_DIR}/los_rate_m_per_year.rdc" "${EXPORT_DIR}/los_rate_toward_mm_per_year.rdc" -1000.0',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" phase-to-los "${EXPORT_DIR}/los_sigma_m_per_year.rdc" "${EXPORT_DIR}/los_sigma_mm_per_year.rdc" 1000.0',
            "",
            '  geo_float "${TS_DIR}/ts_rate" "ts_rate_rad_per_year"',
            '  geo_float "${TS_DIR}/sigma_rate" "sigma_rate_rad_per_year"',
            '  if [ -s "${TS_DIR}/sigma_ts" ]; then geo_float "${TS_DIR}/sigma_ts" "sigma_ts_rad"; fi',
            '  if [ -s "${TS_DIR}/hgt_correction" ]; then geo_float "${TS_DIR}/hgt_correction" "hgt_correction_m"; fi',
            '  geo_float "${EXPORT_DIR}/los_rate_away_m_per_year.rdc" "los_rate_away_m_per_year"',
            '  geo_float "${EXPORT_DIR}/los_rate_toward_m_per_year.rdc" "los_rate_toward_m_per_year"',
            '  geo_float "${EXPORT_DIR}/los_sigma_m_per_year.rdc" "los_sigma_m_per_year"',
            '  geo_float "${EXPORT_DIR}/los_rate_away_mm_per_year.rdc" "los_rate_away_mm_per_year"',
            '  geo_float "${EXPORT_DIR}/los_rate_toward_mm_per_year.rdc" "los_rate_toward_mm_per_year"',
            '  geo_float "${EXPORT_DIR}/los_sigma_mm_per_year.rdc" "los_sigma_mm_per_year"',
            "",
            '  rasdt_pwr "${EXPORT_DIR}/los_rate_toward_m_per_year.rdc" "${MLI}" "${RDC_WIDTH}" 1 0 1 1 -0.08 0.08 0 hls.cm "${EXPORT_DIR}/los_rate_toward_m_per_year.hls.bmp" 1.0 0.35 24',
            '  rasdt_pwr "${EXPORT_DIR}/los_rate_away_m_per_year.rdc" "${MLI}" "${RDC_WIDTH}" 1 0 1 1 -0.08 0.08 0 hls.cm "${EXPORT_DIR}/los_rate_away_m_per_year.hls.bmp" 1.0 0.35 24',
            '  rasdt_pwr "${EXPORT_DIR}/los_sigma_m_per_year.rdc" "${MLI}" "${RDC_WIDTH}" 1 0 1 1 0.0 0.06 1 cc.cm "${EXPORT_DIR}/los_sigma_m_per_year.cc.bmp" 1.0 0.35 8',
            '  rasdt_pwr "${EXPORT_DIR}/los_rate_away_mm_per_year.rdc" "${MLI}" "${RDC_WIDTH}" - - 4 4 -100 100 0 hls.cm "${EXPORT_DIR}/los_rate_away_mm_per_year.bmp" - - 24',
            '  rasdt_pwr "${EXPORT_DIR}/los_rate_toward_mm_per_year.rdc" "${MLI}" "${RDC_WIDTH}" - - 4 4 -100 100 0 hls.cm "${EXPORT_DIR}/los_rate_toward_mm_per_year.bmp" - - 24',
            '  rasdt_pwr "${EXPORT_DIR}/los_sigma_mm_per_year.rdc" "${MLI}" "${RDC_WIDTH}" - - 4 4 0 60 1 cc.cm "${EXPORT_DIR}/los_sigma_mm_per_year.bmp" - - 8',
            '  geo_bmp_rgb "${EXPORT_DIR}/los_rate_toward_m_per_year.hls.bmp" "los_rate_toward_m_per_year.hls"',
            '  geo_bmp_rgb "${EXPORT_DIR}/los_sigma_m_per_year.cc.bmp" "los_sigma_m_per_year.cc"',
            "",
            '  make_preview "${EXPORT_DIR}/los_rate_toward_mm_per_year.tif" "${RATE_CMAP}" "${EXPORT_DIR}/los_rate_toward_mm_per_year.geo_preview.png"',
            '  make_preview "${EXPORT_DIR}/los_sigma_mm_per_year.tif" "${SIGMA_CMAP}" "${EXPORT_DIR}/los_sigma_mm_per_year.geo_preview.png"',
            "",
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" export-points-geojson \\',
            '    --toward-tif "${EXPORT_DIR}/los_rate_toward_mm_per_year.tif" \\',
            '    --away-tif "${EXPORT_DIR}/los_rate_away_mm_per_year.tif" \\',
            '    --sigma-tif "${EXPORT_DIR}/los_sigma_mm_per_year.tif" \\',
            '    --output "${VECTOR_DIR}/los_rate_points.geojson.gz" \\',
            '    --summary-path "${VECTOR_DIR}/los_rate_points_summary.json" \\',
            '    --run-id "${RUN_ROOT##*/}" \\',
            '    --date-start "${DATE_START}" \\',
            '    --date-end "${DATE_END}" \\',
            '    --reference-date "${REF_DATE}" \\',
            '    --admin-province "${ADMIN_PROVINCE}" \\',
            '    --admin-city "${ADMIN_CITY}"',
            '  ls -lh "${EXPORT_DIR}"',
            '  ls -lh "${VECTOR_DIR}"',
            '} >"${LOG_DIR}/publish_products.log" 2>&1',
            "",
            'echo "Published Gamma SBAS products: ${EXPORT_DIR}"',
            "",
        ]
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _write_monitor_points_script(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        dates: list[str],
        timeseries_dir: Path,
        export_dir: Path,
        point_dir: Path,
        rmli_par_path: Path,
        slc_par_path: Path,
        dem_par_path: Path,
        lookup_path: Path,
    ) -> Path:
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "08_point_timeseries.sh"
        log_dir = run_dir / "logs"
        python_bin = settings.GAMMA_SBAS_PYTHON or settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        tool_script = Path(settings.PROJECT_ROOT) / "deploy" / "wsl" / "runners" / "gamma_sbas_product_tools.py"
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'TS_DIR="{self._windows_path_to_wsl_mount(str(timeseries_dir))}"',
            f'EXPORT_DIR="{self._windows_path_to_wsl_mount(str(export_dir))}"',
            f'POINT_DIR="{self._windows_path_to_wsl_mount(str(point_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'MLI_PAR="{self._windows_path_to_wsl_mount(str(rmli_par_path))}"',
            f'SLC_PAR="{self._windows_path_to_wsl_mount(str(slc_par_path))}"',
            f'DEM_PAR="{self._windows_path_to_wsl_mount(str(dem_par_path))}"',
            f'LOOKUP="{self._windows_path_to_wsl_mount(str(lookup_path))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'TOOL_SCRIPT="{self._windows_path_to_wsl_mount(str(tool_script))}"',
            f'REF_DATE="{reference_date}"',
            f'DATES="{",".join(dates)}"',
            'MONITOR_CONFIG="${RUN_ROOT}/monitor_points.json"',
            'SUMMARY="${RUN_ROOT}/monitor_points_summary.json"',
            "",
            'mkdir -p "${POINT_DIR}" "${LOG_DIR}"',
            "",
            "{",
            '  echo "== extract monitoring point time-series =="',
            '  test -s "${TS_DIR}/diff_ts.tab"',
            '  test -s "${EXPORT_DIR}/los_rate_toward_mm_per_year.rdc"',
            '  test -s "${EXPORT_DIR}/los_sigma_mm_per_year.rdc"',
            '  "${PYTHON_BIN}" "${TOOL_SCRIPT}" monitor-points \\',
            '    --monitor-config "${MONITOR_CONFIG}" \\',
            '    --timeseries-dir "${TS_DIR}" \\',
            '    --export-dir "${EXPORT_DIR}" \\',
            '    --point-dir "${POINT_DIR}" \\',
            '    --mli-par "${MLI_PAR}" \\',
            '    --slc-par "${SLC_PAR}" \\',
            '    --dem-par "${DEM_PAR}" \\',
            '    --lookup "${LOOKUP}" \\',
            '    --dates "${DATES}" \\',
            '    --reference-date "${REF_DATE}" \\',
            '    --summary-path "${SUMMARY}"',
            '} >"${LOG_DIR}/monitor_points.log" 2>&1',
            "",
            'echo "Monitoring point products complete: ${POINT_DIR}"',
            "",
        ]
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._write_script(script_path, lines)

    def _build_baseline_summary(self, run_dir: Path) -> dict[str, Any]:
        diff_dir = run_dir / "work" / "gamma" / "diff"
        all_pairs = self._parse_bperp_table(diff_dir / "bperp_all_pairs.txt")
        adjacent_pairs = self._parse_bperp_table(diff_dir / "bperp_adjacent.txt")
        itab_rows = self._parse_itab(diff_dir / "itab_adjacent")
        pair_network = {
            "strategy": "gamma_base_calc_adjacent",
            "gamma_baseline_status": "READY" if adjacent_pairs else "EMPTY",
            "pairs": [],
        }
        for index, pair in enumerate(adjacent_pairs):
            itab = itab_rows[index] if index < len(itab_rows) else None
            pair_network["pairs"].append(
                {
                    "pair_index": pair.get("pair_index"),
                    "master_date": pair.get("master_date"),
                    "slave_date": pair.get("slave_date"),
                    "delta_days": pair.get("delta_days"),
                    "bperp_m": pair.get("bperp_m"),
                    "itab_row": itab,
                    "gamma_baseline_status": "READY",
                }
            )
        bperps = [abs(float(item["bperp_m"])) for item in adjacent_pairs if item.get("bperp_m") is not None]
        gaps = [float(item["delta_days"]) for item in adjacent_pairs if item.get("delta_days") is not None]
        return {
            "schema": "insar.gamma-baseline-audit/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "all_pair_count": len(all_pairs),
            "adjacent_pair_count": len(adjacent_pairs),
            "max_abs_bperp_m": max(bperps) if bperps else None,
            "mean_abs_bperp_m": sum(bperps) / len(bperps) if bperps else None,
            "max_delta_days": max(gaps) if gaps else None,
            "all_pairs": all_pairs,
            "adjacent_pairs": adjacent_pairs,
            "itab_adjacent": itab_rows,
            "pair_network": pair_network,
            "outputs": {
                "bperp_all_pairs": str(diff_dir / "bperp_all_pairs.txt"),
                "bperp_adjacent": str(diff_dir / "bperp_adjacent.txt"),
                "itab_all_pairs": str(diff_dir / "itab_all_pairs"),
                "itab_adjacent": str(diff_dir / "itab_adjacent"),
            },
        }

    def _build_coregistration_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
    ) -> dict[str, Any]:
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        dates = [str(scene.get("date") or "") for scene in scenes if scene.get("date")]
        reference = str(reference_date or "").strip()
        if reference not in dates and dates:
            reference = str((stack_manifest.get("stack") or {}).get("reference_date") or "").strip()
        if reference not in dates and dates:
            reference = dates[len(dates) // 2]

        gamma_root = run_dir / "work" / "gamma"
        common_dir = gamma_root / f"common_{reference}"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        rslc_dir = common_dir / "rslc"
        rmli_dir = common_dir / "rmli"

        per_date: list[dict[str, Any]] = []
        missing_dates: list[str] = []
        for date in dates:
            if date == reference:
                required = {
                    "slc": slc_dir / f"{date}.slc",
                    "slc_par": slc_dir / f"{date}.slc.par",
                    "mli": mli_dir / f"{date}.mli",
                    "mli_par": mli_dir / f"{date}.mli.par",
                }
                role = "reference"
            else:
                required = {
                    "rslc": rslc_dir / f"{date}.rslc",
                    "rslc_par": rslc_dir / f"{date}.rslc.par",
                    "rmli": rmli_dir / f"{date}.mli",
                    "rmli_par": rmli_dir / f"{date}.mli.par",
                    "offset": rslc_dir / f"{date}_to_{reference}.off",
                }
                role = "secondary"
            missing = [name for name, path in required.items() if not path.is_file() or path.stat().st_size <= 0]
            if missing:
                missing_dates.append(date)
            per_date.append(
                {
                    "date": date,
                    "role": role,
                    "ready": not missing,
                    "missing": missing,
                    "quality_file": str(rslc_dir / f"{date}.rslc.coreg_quality") if date != reference else None,
                }
            )

        expected_secondary_count = max(0, len(dates) - (1 if reference in dates else 0))
        ready_secondary_count = len(
            [
                item for item in per_date
                if item.get("role") == "secondary" and item.get("ready")
            ]
        )
        slc_tab = common_dir / "SLC_tab"
        rmli_tab = common_dir / "RMLI_tab"
        itab_approved = common_dir / "itab_approved"
        required_tabs = {
            "slc_tab": slc_tab,
            "rmli_tab": rmli_tab,
            "itab_approved": itab_approved,
        }
        missing_tabs = [
            name for name, path in required_tabs.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        ready = not missing_dates and not missing_tabs and bool(dates)
        return {
            "schema": "insar.gamma-coregistration-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "scene_count": len(dates),
            "expected_secondary_count": expected_secondary_count,
            "ready_secondary_count": ready_secondary_count,
            "missing_dates": missing_dates,
            "missing_tabs": missing_tabs,
            "ready": ready,
            "per_date": per_date,
            "outputs": {
                "common_dir": str(common_dir),
                "rslc_dir": str(rslc_dir),
                "rmli_dir": str(rmli_dir),
                "slc_tab": str(slc_tab),
                "rmli_tab": str(rmli_tab),
                "itab_approved": str(itab_approved),
            },
        }

    def _build_rdc_dem_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
        rlks: int,
        dem_source: dict[str, Any],
    ) -> dict[str, Any]:
        reference = str(reference_date or "").strip()
        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        dem_dir = run_dir / "work" / "gamma" / "dem"
        prefix = f"{reference}_{rlks}rlks"
        required_outputs = {
            "utm_dem": dem_dir / f"{prefix}.utm.dem",
            "utm_dem_par": dem_dir / f"{prefix}.utm.dem.par",
            "lookup_table": dem_dir / f"{prefix}.UTM_TO_RDC",
            "rdc_dem": dem_dir / f"{prefix}.rdc.dem",
            "diff_par": dem_dir / f"{prefix}.diff_par",
        }
        optional_outputs = {
            "utm_to_rdc_initial": dem_dir / f"{prefix}.utm_to_rdc0",
            "sim_sar_rdc": dem_dir / f"{prefix}.sim_sar_rdc",
            "offset_std": dem_dir / f"{reference}_dem.off_std",
            "source_dem_clean": dem_dir / "source_dem_clean.dem",
            "source_dem_clean_par": dem_dir / "source_dem_clean.dem.par",
        }
        missing_outputs = [
            name for name, path in required_outputs.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]

        rmli_path, rmli_par_path = self._find_reference_rmli_paths(run_dir, reference)
        rmli_params = self._parse_gamma_params(rmli_par_path)
        utm_params = self._parse_gamma_params(required_outputs["utm_dem_par"])
        rdc_width = self._as_int(rmli_params.get("range_samples"))
        rdc_lines = self._as_int(rmli_params.get("azimuth_lines"))
        expected_rdc_bytes = (rdc_width * rdc_lines * 4) if rdc_width and rdc_lines else None
        rdc_size = required_outputs["rdc_dem"].stat().st_size if required_outputs["rdc_dem"].is_file() else None
        log_path = run_dir / "logs" / f"{reference}_rdc_dem.log"
        size_matches_reference_geometry = (
            expected_rdc_bytes is None
            or (rdc_size is not None and rdc_size == expected_rdc_bytes)
        )
        ready = not missing_outputs and bool(reference) and size_matches_reference_geometry
        return {
            "schema": "insar.gamma-rdc-dem-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "rlks": rlks,
            "ready": ready,
            "missing_outputs": missing_outputs,
            "dem_source": dem_source,
            "reference_rmli": {
                "mli": str(rmli_path),
                "mli_par": str(rmli_par_path),
                "range_samples": rdc_width,
                "azimuth_lines": rdc_lines,
            },
            "utm_dem": {
                "width": self._as_int(utm_params.get("width")),
                "nlines": self._as_int(utm_params.get("nlines")),
                "corner_lon": self._as_float(utm_params.get("corner_lon")),
                "corner_lat": self._as_float(utm_params.get("corner_lat")),
                "post_lon": self._as_float(utm_params.get("post_lon")),
                "post_lat": self._as_float(utm_params.get("post_lat")),
            },
            "rdc_dem": {
                "size_bytes": rdc_size,
                "expected_float32_bytes": expected_rdc_bytes,
                "size_matches_reference_geometry": size_matches_reference_geometry,
            },
            "outputs": {
                name: self._file_record(path)
                for name, path in {**required_outputs, **optional_outputs}.items()
            },
            "log": self._file_record(log_path),
            "log_tail": self._tail_text(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
        }

    def _build_interferogram_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
        pair_plan: list[dict[str, Any]],
        rlks: int,
    ) -> dict[str, Any]:
        reference = str(reference_date or "").strip()
        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        common_dir = run_dir / "work" / "gamma" / f"common_{reference}"
        diff_dir = common_dir / "diff"
        diff_tab = common_dir / "DIFF_tab"
        itab_common_ref = common_dir / "itab_common_ref"

        per_pair: list[dict[str, Any]] = []
        missing_pairs: list[str] = []
        for pair in pair_plan:
            pair_id = str(pair.get("pair_id") or "").strip()
            pair_dir = diff_dir / pair_id
            required_outputs = {
                "offset": pair_dir / f"{pair_id}_{rlks}rlks.off",
                "sim_unw": pair_dir / f"{pair_id}.sim_unw",
                "diff": pair_dir / f"{pair_id}_{rlks}rlks.diff",
                "diff_filt": pair_dir / f"{pair_id}_{rlks}rlks.diff_filt",
                "cor": pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.cor",
                "mask": pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.cor_mask.bmp",
                "unw": pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.unw",
            }
            missing = [
                name for name, path in required_outputs.items()
                if not path.is_file() or path.stat().st_size <= 0
            ]
            if missing:
                missing_pairs.append(pair_id)
            log_path = run_dir / "logs" / f"{pair_id}_diff_unwrap_common.log"
            cc_stats_path = run_dir / "logs" / f"{pair_id}_diff_filt_cc_stats.json"
            per_pair.append(
                {
                    **pair,
                    "ready": not missing,
                    "missing": missing,
                    "outputs": {name: self._file_record(path) for name, path in required_outputs.items()},
                    "log": self._file_record(log_path),
                    "cc_stats": self._read_optional_json(cc_stats_path) or self._file_record(cc_stats_path),
                }
            )

        diff_tab_rows = self._read_text_rows(diff_tab)
        itab_rows = self._parse_itab(itab_common_ref)
        missing_tabs = [
            name for name, path in {"diff_tab": diff_tab, "itab_common_ref": itab_common_ref}.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        ready_pair_count = len([item for item in per_pair if item.get("ready")])
        ready = (
            ready_pair_count == len(pair_plan)
            and not missing_pairs
            and not missing_tabs
            and len(diff_tab_rows) == len(pair_plan)
            and len(itab_rows) == len(pair_plan)
            and bool(pair_plan)
        )
        return {
            "schema": "insar.gamma-interferogram-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "rlks": rlks,
            "pair_count": len(pair_plan),
            "ready_pair_count": ready_pair_count,
            "missing_pairs": missing_pairs,
            "missing_tabs": missing_tabs,
            "diff_tab_row_count": len(diff_tab_rows),
            "itab_common_ref_row_count": len(itab_rows),
            "ready": ready,
            "per_pair": per_pair,
            "outputs": {
                "diff_dir": str(diff_dir),
                "diff_tab": self._file_record(diff_tab),
                "itab_common_ref": self._file_record(itab_common_ref),
            },
        }

    def _build_detrend_atm_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
        pair_plan: list[dict[str, Any]],
        rlks: int,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        reference = str(reference_date or "").strip()
        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        common_dir = run_dir / "work" / "gamma" / f"common_{reference}"
        detrend_dir = common_dir / "detrend_atm"
        diff_atmsub_tab = common_dir / "DIFF_atmsub_tab"
        itab_atmsub = common_dir / "itab_atmsub"
        rmli_par_path = Path(self._path_to_windows(str(inputs.get("reference_mli_par") or "")) or "")
        rmli_params = self._parse_gamma_params(rmli_par_path)
        width = self._as_int(rmli_params.get("range_samples"))
        lines = self._as_int(rmli_params.get("azimuth_lines"))
        expected_float32_bytes = (width * lines * 4) if width and lines else None

        per_pair: list[dict[str, Any]] = []
        missing_pairs: list[str] = []
        for pair in pair_plan:
            pair_id = str(pair.get("pair_id") or "").strip()
            pair_dir = detrend_dir / pair_id
            outputs = {
                "diff_par": pair_dir / f"{pair_id}.diff_par",
                "unw_linear": pair_dir / f"{pair_id}.unw_linear",
                "unw_sub_linear": pair_dir / f"{pair_id}.unw_sub_linear",
                "a0": pair_dir / f"{pair_id}.a0",
                "a1": pair_dir / f"{pair_id}.a1",
                "a0_fill": pair_dir / f"{pair_id}.a0_fill",
                "a1_fill": pair_dir / f"{pair_id}.a1_fill",
                "atm_model": pair_dir / f"{pair_id}.atm_model",
                "atmsub": pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.unw.atmsub",
                "atmsub_bmp": pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.unw.atmsub.bmp",
            }
            missing = [
                name for name, path in outputs.items()
                if not path.is_file() or path.stat().st_size <= 0
            ]
            if missing:
                missing_pairs.append(pair_id)
            atmsub_size = outputs["atmsub"].stat().st_size if outputs["atmsub"].is_file() else 0
            per_pair.append(
                {
                    **pair,
                    "ready": not missing,
                    "missing": missing,
                    "size_checks": {
                        "atmsub": {
                            "size_bytes": atmsub_size,
                            "expected_float32_bytes": expected_float32_bytes,
                            "size_matches_reference_geometry": (
                                expected_float32_bytes is None
                                or (atmsub_size > 0 and atmsub_size == expected_float32_bytes)
                            ),
                        }
                    },
                    "outputs": {name: self._file_record(path) for name, path in outputs.items()},
                    "log": self._file_record(run_dir / "logs" / f"{pair_id}_detrend_atm.log"),
                }
            )

        diff_rows = self._read_text_rows(diff_atmsub_tab)
        itab_rows = self._parse_itab(itab_atmsub)
        missing_tabs = [
            name for name, path in {"diff_atmsub_tab": diff_atmsub_tab, "itab_atmsub": itab_atmsub}.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        ready_pair_count = len(
            [
                item for item in per_pair
                if item.get("ready")
                and ((item.get("size_checks") or {}).get("atmsub") or {}).get("size_matches_reference_geometry")
            ]
        )
        ready = (
            ready_pair_count == len(pair_plan)
            and not missing_pairs
            and not missing_tabs
            and len(diff_rows) == len(pair_plan)
            and len(itab_rows) == len(pair_plan)
            and bool(pair_plan)
        )
        log_path = run_dir / "logs" / "detrend_atm_inventory.txt"
        return {
            "schema": "insar.gamma-detrend-atm-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "rlks": rlks,
            "ready": ready,
            "pair_count": len(pair_plan),
            "ready_pair_count": ready_pair_count,
            "missing_pairs": missing_pairs,
            "missing_tabs": missing_tabs,
            "diff_atmsub_tab_row_count": len(diff_rows),
            "itab_atmsub_row_count": len(itab_rows),
            "reference_geometry": {
                "mli_par": str(rmli_par_path) if str(rmli_par_path) else None,
                "range_samples": width,
                "azimuth_lines": lines,
                "expected_float32_bytes": expected_float32_bytes,
            },
            "inputs": {
                key: self._file_record(Path(self._path_to_windows(str(value)) or str(value)))
                for key, value in inputs.items()
                if value
            },
            "outputs": {
                "detrend_dir": str(detrend_dir),
                "diff_atmsub_tab": self._file_record(diff_atmsub_tab),
                "itab_atmsub": self._file_record(itab_atmsub),
            },
            "per_pair": per_pair,
            "log": self._file_record(log_path),
            "log_tail": self._tail_text(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
        }

    def _build_ipta_timeseries_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
        rlks: int,
        inputs: dict[str, Any],
        reference_region: dict[str, Any] | None = None,
        mb_mode: int = DEFAULT_IPTA_MB_MODE,
    ) -> dict[str, Any]:
        reference = str(reference_date or "").strip()
        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        mb_mode = self._normalize_ipta_mb_mode(mb_mode)
        common_dir = run_dir / "work" / "gamma" / f"common_{reference}"
        timeseries_dir = common_dir / "timeseries"
        required_outputs = {
            "diff_ts_tab": timeseries_dir / "diff_ts.tab",
            "itab_ts": timeseries_dir / "itab_ts",
            "sigma_ts": timeseries_dir / "sigma_ts",
            "hgt_correction": timeseries_dir / "hgt_correction",
            "ts_rate": timeseries_dir / "ts_rate",
            "ts_const": timeseries_dir / "ts_const",
            "sigma_rate": timeseries_dir / "sigma_rate",
        }
        missing_outputs = [
            name for name, path in required_outputs.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        diff_ts_rows = self._read_text_rows(required_outputs["diff_ts_tab"])
        itab_ts_rows = self._parse_itab(required_outputs["itab_ts"])
        rmli_par_path = Path(self._path_to_windows(str(inputs.get("geometry_reference_mli_par") or "")) or "")
        rmli_params = self._parse_gamma_params(rmli_par_path)
        width = self._as_int(rmli_params.get("range_samples"))
        lines = self._as_int(rmli_params.get("azimuth_lines"))
        expected_float32_bytes = (width * lines * 4) if width and lines else None
        size_checks = {}
        for key in ("sigma_ts", "hgt_correction", "ts_rate", "ts_const", "sigma_rate"):
            path = required_outputs[key]
            size = path.stat().st_size if path.is_file() else 0
            size_checks[key] = {
                "size_bytes": size,
                "expected_float32_bytes": expected_float32_bytes,
                "size_matches_reference_geometry": (
                    expected_float32_bytes is None
                    or (size > 0 and size == expected_float32_bytes)
                ),
            }
        log_path = run_dir / "logs" / "mb_ts_rate.log"
        ready = (
            not missing_outputs
            and bool(diff_ts_rows)
            and bool(itab_ts_rows)
            and all(item.get("size_matches_reference_geometry") for item in size_checks.values())
        )
        return {
            "schema": "insar.gamma-ipta-timeseries-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "rlks": rlks,
            "mb_mode": mb_mode,
            "mb_mode_description": IPTA_MB_MODE_DESCRIPTIONS[mb_mode],
            "ready": ready,
            "missing_outputs": missing_outputs,
            "diff_ts_row_count": len(diff_ts_rows),
            "itab_ts_row_count": len(itab_ts_rows),
            "reference_geometry": {
                "mli_par": str(rmli_par_path) if str(rmli_par_path) else None,
                "range_samples": width,
                "azimuth_lines": lines,
                "expected_float32_bytes": expected_float32_bytes,
            },
            "reference_region": reference_region or {},
            "inputs": {
                key: self._file_record(Path(self._path_to_windows(str(value)) or str(value)))
                for key, value in inputs.items()
                if value
            },
            "outputs": {
                "timeseries_dir": str(timeseries_dir),
                **{name: self._file_record(path) for name, path in required_outputs.items()},
            },
            "size_checks": size_checks,
            "log": self._file_record(log_path),
            "log_tail": self._tail_text(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
        }

    def _build_publish_products_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
        rlks: int,
        inputs: dict[str, Any],
        wavelength: Any,
    ) -> dict[str, Any]:
        reference = str(reference_date or "").strip()
        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        export_dir = run_dir / "publish" / "geotiff"
        rmli_par_path = Path(self._path_to_windows(str(inputs.get("reference_mli_par") or "")) or "")
        if not rmli_par_path.is_file():
            rmli_par_path = run_dir / "work" / "gamma" / "mli" / f"{reference}.mli.par"
        rmli_params = self._parse_gamma_params(rmli_par_path)
        width = self._as_int(rmli_params.get("range_samples"))
        lines = self._as_int(rmli_params.get("azimuth_lines"))
        expected_float32_bytes = (width * lines * 4) if width and lines else None
        required_outputs = {
            "los_rate_toward_m_per_year_rdc": export_dir / "los_rate_toward_m_per_year.rdc",
            "los_rate_away_m_per_year_rdc": export_dir / "los_rate_away_m_per_year.rdc",
            "los_sigma_m_per_year_rdc": export_dir / "los_sigma_m_per_year.rdc",
            "los_rate_toward_m_per_year_tif": export_dir / "los_rate_toward_m_per_year.tif",
            "los_rate_away_m_per_year_tif": export_dir / "los_rate_away_m_per_year.tif",
            "los_sigma_m_per_year_tif": export_dir / "los_sigma_m_per_year.tif",
            "los_rate_toward_m_per_year_hls_bmp": export_dir / "los_rate_toward_m_per_year.hls.bmp",
            "los_rate_toward_m_per_year_hls_rgb_tif": export_dir / "los_rate_toward_m_per_year.hls.geo_rgb.tif",
            "los_rate_toward_m_per_year_hls_geo_preview": export_dir / "los_rate_toward_m_per_year.hls.geo_preview.png",
            "los_sigma_m_per_year_cc_bmp": export_dir / "los_sigma_m_per_year.cc.bmp",
            "los_sigma_m_per_year_cc_rgb_tif": export_dir / "los_sigma_m_per_year.cc.geo_rgb.tif",
            "los_sigma_m_per_year_cc_geo_preview": export_dir / "los_sigma_m_per_year.cc.geo_preview.png",
            "los_rate_toward_mm_per_year_rdc": export_dir / "los_rate_toward_mm_per_year.rdc",
            "los_rate_away_mm_per_year_rdc": export_dir / "los_rate_away_mm_per_year.rdc",
            "los_sigma_mm_per_year_rdc": export_dir / "los_sigma_mm_per_year.rdc",
            "los_rate_toward_mm_per_year_tif": export_dir / "los_rate_toward_mm_per_year.tif",
            "los_rate_away_mm_per_year_tif": export_dir / "los_rate_away_mm_per_year.tif",
            "los_sigma_mm_per_year_tif": export_dir / "los_sigma_mm_per_year.tif",
            "los_rate_toward_mm_per_year_geo_preview": export_dir / "los_rate_toward_mm_per_year.geo_preview.png",
            "los_sigma_mm_per_year_geo_preview": export_dir / "los_sigma_mm_per_year.geo_preview.png",
            "los_rate_toward_mm_per_year_bmp": export_dir / "los_rate_toward_mm_per_year.bmp",
            "los_sigma_mm_per_year_bmp": export_dir / "los_sigma_mm_per_year.bmp",
            "ts_rate_rad_per_year_tif": export_dir / "ts_rate_rad_per_year.tif",
            "sigma_rate_rad_per_year_tif": export_dir / "sigma_rate_rad_per_year.tif",
        }
        optional_outputs = {
            "sigma_ts_rad_tif": export_dir / "sigma_ts_rad.tif",
            "hgt_correction_m_tif": export_dir / "hgt_correction_m.tif",
            "los_rate_m_per_year_tif": export_dir / "los_rate_m_per_year.tif",
            "los_rate_away_m_per_year_hls_bmp": export_dir / "los_rate_away_m_per_year.hls.bmp",
        }
        vector_dir = run_dir / "publish" / "vectors"
        vector_outputs = {
            "point_vector_geojson_gz": vector_dir / "los_rate_points.geojson.gz",
            "point_vector_summary": vector_dir / "los_rate_points_summary.json",
        }
        point_vector_summary = self._read_optional_json(vector_outputs["point_vector_summary"]) or {}
        missing_outputs = [
            name for name, path in required_outputs.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        rdc_size_checks = {}
        for key in (
            "los_rate_toward_m_per_year_rdc",
            "los_rate_away_m_per_year_rdc",
            "los_sigma_m_per_year_rdc",
            "los_rate_toward_mm_per_year_rdc",
            "los_rate_away_mm_per_year_rdc",
            "los_sigma_mm_per_year_rdc",
        ):
            path = required_outputs[key]
            size = path.stat().st_size if path.is_file() else 0
            rdc_size_checks[key] = {
                "size_bytes": size,
                "expected_float32_bytes": expected_float32_bytes,
                "size_matches_reference_geometry": (
                    expected_float32_bytes is None
                    or (size > 0 and size == expected_float32_bytes)
                ),
            }
        quality_stats = {}
        if width and lines:
            quality_stats = {
                "los_rate_toward_mm_per_year_rdc": self._gamma_float32_stats(
                    required_outputs["los_rate_toward_mm_per_year_rdc"],
                    width=width,
                    lines=lines,
                ),
                "los_rate_toward_m_per_year_rdc": self._gamma_float32_stats(
                    required_outputs["los_rate_toward_m_per_year_rdc"],
                    width=width,
                    lines=lines,
                ),
                "los_rate_away_mm_per_year_rdc": self._gamma_float32_stats(
                    required_outputs["los_rate_away_mm_per_year_rdc"],
                    width=width,
                    lines=lines,
                ),
                "los_rate_away_m_per_year_rdc": self._gamma_float32_stats(
                    required_outputs["los_rate_away_m_per_year_rdc"],
                    width=width,
                    lines=lines,
                ),
                "los_sigma_mm_per_year_rdc": self._gamma_float32_stats(
                    required_outputs["los_sigma_mm_per_year_rdc"],
                    width=width,
                    lines=lines,
                ),
                "los_sigma_m_per_year_rdc": self._gamma_float32_stats(
                    required_outputs["los_sigma_m_per_year_rdc"],
                    width=width,
                    lines=lines,
                ),
                "ts_rate_rad_per_year_rdc": self._gamma_float32_stats(
                    Path(self._path_to_windows(str(inputs.get("ts_rate") or "")) or ""),
                    width=width,
                    lines=lines,
                ),
                "sigma_rate_rad_per_year_rdc": self._gamma_float32_stats(
                    Path(self._path_to_windows(str(inputs.get("sigma_rate") or "")) or ""),
                    width=width,
                    lines=lines,
                ),
            }
        artifacts = self._build_run_artifacts(run_dir)
        log_path = run_dir / "logs" / "publish_products.log"
        ready = (
            not missing_outputs
            and all(item.get("size_matches_reference_geometry") for item in rdc_size_checks.values())
        )
        product_summary = {
            "schema": "insar.gamma-sbas-product-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "default_los_product": "los_rate_toward_m_per_year",
            "los_sign_convention": "toward radar positive; away from radar negative",
            "expert_color_conventions": {
                "velocity": "rasdt_pwr ... -0.08 0.08 ... hls.cm, geocoded RGB browse from Gamma BMP",
                "sigma": "rasdt_pwr ... cc.cm; production adapts the range to LOS sigma rate units",
                "phase_and_atmosphere": "rasdt_pwr ... -6.28 6.28 ... rmg.cm",
            },
            "geocoded_preview_rule": "primary web previews prefer expert Gamma geocoded RGB browse products; legacy PNG previews are retained for comparison",
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        }
        return {
            "schema": "insar.gamma-sbas-publish-products-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "rlks": rlks,
            "ready": ready,
            "missing_outputs": missing_outputs,
            "wavelength_m": self._as_float(wavelength),
            "reference_geometry": {
                "mli_par": str(rmli_par_path) if str(rmli_par_path) else None,
                "range_samples": width,
                "azimuth_lines": lines,
                "expected_float32_bytes": expected_float32_bytes,
            },
            "inputs": {
                key: self._file_record(Path(self._path_to_windows(str(value)) or str(value)))
                for key, value in inputs.items()
                if value and key != "timeseries_dir"
            },
            "outputs": {
                "export_dir": str(export_dir),
                "vector_dir": str(vector_dir),
                **{name: self._file_record(path) for name, path in {**required_outputs, **optional_outputs, **vector_outputs}.items()},
            },
            "point_vector_summary": point_vector_summary,
            "rdc_size_checks": rdc_size_checks,
            "quality_summary": quality_stats,
            "product_summary": product_summary,
            "log": self._file_record(log_path),
            "log_tail": self._tail_text(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
        }

    def _build_monitor_points_summary(
        self,
        run_dir: Path,
        *,
        monitor_points: dict[str, Any],
    ) -> dict[str, Any]:
        summary_path = run_dir / "monitor_points_summary.json"
        summary = self._read_optional_json(summary_path) or {}
        point_dir = run_dir / "publish" / "monitor_points"
        monitor_outputs = []
        if point_dir.is_dir():
            for metadata_path in sorted(point_dir.glob("*_metadata.json")):
                metadata = self._read_optional_json(metadata_path) or {}
                point_id = str(metadata.get("point_id") or metadata_path.name.replace("_metadata.json", ""))
                png_path = point_dir / f"{point_id}_timeseries.png"
                csv_path = point_dir / f"{point_id}_timeseries.csv"
                monitor_outputs.append(
                    {
                        "point_id": point_id,
                        "metadata": metadata,
                        "files": {
                            "png": self._file_record(png_path),
                            "csv": self._file_record(csv_path),
                            "metadata": self._file_record(metadata_path),
                        },
                    }
                )
        if not summary:
            summary = {
                "schema": "insar.gamma-sbas-monitor-points-summary/v1",
                "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "mode": ((self._read_optional_json(run_dir / "monitor_points.json") or {}).get("mode")),
                "reference_date": monitor_points.get("reference_date"),
            }
        summary["monitor_outputs"] = monitor_outputs
        summary["ready"] = bool(monitor_outputs) and all(
            (item.get("files") or {}).get("png", {}).get("exists")
            and (item.get("files") or {}).get("csv", {}).get("exists")
            and (item.get("files") or {}).get("metadata", {}).get("exists")
            for item in monitor_outputs
        )
        log_path = run_dir / "logs" / "monitor_points.log"
        summary["log"] = self._file_record(log_path)
        summary["log_tail"] = self._tail_text(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else ""
        return summary

    @staticmethod
    def _tail_text(value: Any, length: int = 4000) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        return text[-length:]

    @staticmethod
    def _read_text_rows(path: Path) -> list[str]:
        if not path.is_file():
            return []
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        ]

    def _detrend_pair_plan_from_diff_tab(self, diff_tab: Path, *, rlks: int) -> list[dict[str, Any]]:
        pairs: list[dict[str, Any]] = []
        for row in self._read_text_rows(diff_tab):
            unw = Path(self._path_to_windows(row.split()[0]) or row.split()[0])
            pair_dir = unw.parent
            name = unw.name
            suffix = f"_{rlks}rlks.diff_filt.unw"
            pair_id = name[:-len(suffix)] if name.endswith(suffix) else name.replace(".diff_filt.unw", "")
            parts = pair_id.split("_")
            master_date = parts[0] if len(parts) >= 2 else ""
            slave_date = parts[1] if len(parts) >= 2 else ""
            cor = pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.cor"
            offset = pair_dir / f"{pair_id}_{rlks}rlks.off"
            pairs.append(
                {
                    "pair_id": pair_id,
                    "master_date": master_date,
                    "slave_date": slave_date,
                    "unw": str(unw),
                    "cor": str(cor),
                    "offset": str(offset),
                    "expected_atmsub": str(
                        diff_tab.parent
                        / "detrend_atm"
                        / pair_id
                        / f"{pair_id}_{rlks}rlks.diff_filt.unw.atmsub"
                    ),
                }
            )
        return pairs

    @staticmethod
    def _parse_bperp_table(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                rows.append(
                    {
                        "pair_index": int(parts[0]),
                        "master_date": parts[1],
                        "slave_date": parts[2],
                        "bperp_m": float(parts[3]),
                        "delta_days": float(parts[4]),
                        "mjd1": float(parts[5]),
                        "mjd2": float(parts[6]),
                        "bperp1_m": float(parts[7]),
                        "bperp2_m": float(parts[8]) if len(parts) > 8 else None,
                    }
                )
            except ValueError:
                continue
        return rows

    @staticmethod
    def _parse_itab(path: Path) -> list[list[int]]:
        if not path.is_file():
            return []
        rows: list[list[int]] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                rows.append([int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])])
            except ValueError:
                continue
        return rows

    @staticmethod
    def _stack_dates(stack_manifest: dict[str, Any]) -> list[str]:
        return [
            str(scene.get("date") or "").strip()
            for scene in sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
            if str(scene.get("date") or "").strip()
        ]

    def _build_interferogram_pair_plan(
        self,
        run_dir: Path,
        *,
        reference_date: str,
        approved_itab: Path,
        dates: list[str],
        rlks: int,
    ) -> list[dict[str, Any]]:
        itab_rows = self._parse_itab(approved_itab)
        pair_plan: list[dict[str, Any]] = []
        common_dir = run_dir / "work" / "gamma" / f"common_{reference_date}"
        for row in itab_rows:
            if len(row) < 4:
                continue
            master_index = row[0] - 1
            slave_index = row[1] - 1
            if master_index < 0 or slave_index < 0 or master_index >= len(dates) or slave_index >= len(dates):
                continue
            master_date = dates[master_index]
            slave_date = dates[slave_index]
            pair_id = f"{master_date}_{slave_date}"
            pair_dir = common_dir / "diff" / pair_id
            pair_plan.append(
                {
                    "pair_id": pair_id,
                    "master_date": master_date,
                    "slave_date": slave_date,
                    "itab_row": row,
                    "pair_index": row[2],
                    "expected_unw": str(pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.unw"),
                    "expected_cor": str(pair_dir / f"{pair_id}_{rlks}rlks.diff_filt.cor"),
                    "log_path": str(run_dir / "logs" / f"{pair_id}_diff_unwrap_common.log"),
                }
            )
        return pair_plan

    def _refresh_command_manifest_after_baseline(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
        baseline_summary: dict[str, Any] | None,
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        for stage in stage_plan:
            if stage.get("stage_id") == "prepare_slc":
                stage["status"] = "COMPLETED" if baseline_summary else "SCRIPT_READY"
            if stage.get("stage_id") == "baseline_audit":
                if run_manifest.get("status") == "BASELINE_AUDIT_READY":
                    stage["status"] = "COMPLETED_PENDING_ITAB_APPROVAL"
                elif run_manifest.get("status") == "BASELINE_AUDIT_FAILED":
                    stage["status"] = "FAILED"
                else:
                    stage["status"] = "SCRIPT_READY"
        command_manifest["execution_enabled"] = True
        command_manifest["reason_execution_disabled"] = None
        command_manifest["stage_plan"] = stage_plan
        command_manifest["baseline_audit"] = run_manifest.get("baseline_audit")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_itab_decision(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "baseline_audit":
                if status == "ITAB_APPROVED":
                    stage["status"] = "COMPLETED_ITAB_APPROVED"
                elif status == "ITAB_REJECTED":
                    stage["status"] = "COMPLETED_ITAB_REJECTED"
            if stage.get("stage_id") == "coregistration":
                if status == "ITAB_APPROVED":
                    stage["status"] = "READY"
                elif status == "ITAB_REJECTED":
                    stage["status"] = "BLOCKED_PAIR_NETWORK_REJECTED"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["baseline_audit"] = run_manifest.get("baseline_audit")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_coregistration(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        for stage in stage_plan:
            if stage.get("stage_id") == "coregistration":
                if run_manifest.get("status") == "COREGISTRATION_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif run_manifest.get("status") == "COREGISTRATION_RUNNING":
                    stage["status"] = "RUNNING"
                elif run_manifest.get("status") == "COREGISTRATION_READY":
                    stage["status"] = "COMPLETED"
                elif run_manifest.get("status") == "COREGISTRATION_FAILED":
                    stage["status"] = "FAILED"
            if stage.get("stage_id") == "rdc_dem" and run_manifest.get("status") == "COREGISTRATION_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_rdc_dem(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "coregistration" and status in {
                "RDC_DEM_SCRIPT_READY",
                "RDC_DEM_RUNNING",
                "RDC_DEM_READY",
                "RDC_DEM_FAILED",
            }:
                stage["status"] = "COMPLETED"
            if stage.get("stage_id") == "rdc_dem":
                if status == "RDC_DEM_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif status == "RDC_DEM_RUNNING":
                    stage["status"] = "RUNNING"
                elif status == "RDC_DEM_READY":
                    stage["status"] = "COMPLETED"
                elif status == "RDC_DEM_FAILED":
                    stage["status"] = "FAILED"
                elif status in {"BASELINE_AUDIT_READY", "ITAB_APPROVED", "COREGISTRATION_SCRIPT_READY", "COREGISTRATION_READY"}:
                    stage["status"] = "READY"
            if stage.get("stage_id") == "interferograms" and status == "RDC_DEM_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["rdc_dem"] = run_manifest.get("rdc_dem")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_interferograms(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "rdc_dem" and status in {
                "INTERFEROGRAMS_SCRIPT_READY",
                "INTERFEROGRAMS_RUNNING",
                "INTERFEROGRAMS_READY",
                "INTERFEROGRAMS_FAILED",
            }:
                stage["status"] = "COMPLETED"
            if stage.get("stage_id") == "interferograms":
                if status == "INTERFEROGRAMS_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif status == "INTERFEROGRAMS_RUNNING":
                    stage["status"] = "RUNNING"
                elif status == "INTERFEROGRAMS_READY":
                    stage["status"] = "COMPLETED"
                elif status == "INTERFEROGRAMS_FAILED":
                    stage["status"] = "FAILED"
                elif status == "RDC_DEM_READY":
                    stage["status"] = "READY"
            if stage.get("stage_id") == "detrend_atm" and status == "INTERFEROGRAMS_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["rdc_dem"] = run_manifest.get("rdc_dem")
        command_manifest["interferograms"] = run_manifest.get("interferograms")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_detrend_atm(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "interferograms" and status in {
                "DETREND_ATM_SCRIPT_READY",
                "DETREND_ATM_RUNNING",
                "DETREND_ATM_READY",
                "DETREND_ATM_FAILED",
            }:
                stage["status"] = "COMPLETED"
            if stage.get("stage_id") == "detrend_atm":
                if status == "DETREND_ATM_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif status == "DETREND_ATM_RUNNING":
                    stage["status"] = "RUNNING"
                elif status == "DETREND_ATM_READY":
                    stage["status"] = "COMPLETED"
                elif status == "DETREND_ATM_FAILED":
                    stage["status"] = "FAILED"
                elif status == "INTERFEROGRAMS_READY":
                    stage["status"] = "READY"
            if stage.get("stage_id") == "ipta_timeseries" and status == "DETREND_ATM_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["rdc_dem"] = run_manifest.get("rdc_dem")
        command_manifest["interferograms"] = run_manifest.get("interferograms")
        command_manifest["detrend_atm"] = run_manifest.get("detrend_atm")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_ipta_timeseries(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "detrend_atm" and status in {
                "IPTA_TIMESERIES_SCRIPT_READY",
                "IPTA_TIMESERIES_RUNNING",
                "IPTA_TIMESERIES_READY",
                "IPTA_TIMESERIES_FAILED",
            }:
                stage["status"] = "COMPLETED"
            if stage.get("stage_id") == "ipta_timeseries":
                if status == "IPTA_TIMESERIES_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif status == "IPTA_TIMESERIES_RUNNING":
                    stage["status"] = "RUNNING"
                elif status == "IPTA_TIMESERIES_READY":
                    stage["status"] = "COMPLETED"
                elif status == "IPTA_TIMESERIES_FAILED":
                    stage["status"] = "FAILED"
                elif status == "DETREND_ATM_READY":
                    stage["status"] = "READY"
            if stage.get("stage_id") == "publish_products" and status == "IPTA_TIMESERIES_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["rdc_dem"] = run_manifest.get("rdc_dem")
        command_manifest["interferograms"] = run_manifest.get("interferograms")
        command_manifest["detrend_atm"] = run_manifest.get("detrend_atm")
        command_manifest["ipta_timeseries"] = run_manifest.get("ipta_timeseries")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_publish_products(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "ipta_timeseries" and status in {
                "PUBLISH_PRODUCTS_SCRIPT_READY",
                "PUBLISH_PRODUCTS_RUNNING",
                "PRODUCTS_READY",
                "PUBLISH_PRODUCTS_FAILED",
                "MONITOR_POINTS_SCRIPT_READY",
                "MONITOR_POINTS_RUNNING",
                "MONITOR_POINTS_READY",
            }:
                stage["status"] = "COMPLETED"
            if stage.get("stage_id") == "publish_products":
                if status == "PUBLISH_PRODUCTS_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif status == "PUBLISH_PRODUCTS_RUNNING":
                    stage["status"] = "RUNNING"
                elif status == "PRODUCTS_READY":
                    stage["status"] = "COMPLETED"
                elif status == "PUBLISH_PRODUCTS_FAILED":
                    stage["status"] = "FAILED"
                elif status == "IPTA_TIMESERIES_READY":
                    stage["status"] = "READY"
            if stage.get("stage_id") == "monitor_points" and status == "PRODUCTS_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["rdc_dem"] = run_manifest.get("rdc_dem")
        command_manifest["interferograms"] = run_manifest.get("interferograms")
        command_manifest["detrend_atm"] = run_manifest.get("detrend_atm")
        command_manifest["ipta_timeseries"] = run_manifest.get("ipta_timeseries")
        command_manifest["publish_products"] = run_manifest.get("publish_products")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_monitor_points(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "publish_products" and status in {
                "MONITOR_POINTS_SCRIPT_READY",
                "MONITOR_POINTS_RUNNING",
                "MONITOR_POINTS_READY",
                "MONITOR_POINTS_FAILED",
            }:
                stage["status"] = "COMPLETED"
            if stage.get("stage_id") == "monitor_points":
                if status == "MONITOR_POINTS_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif status == "MONITOR_POINTS_RUNNING":
                    stage["status"] = "RUNNING"
                elif status == "MONITOR_POINTS_READY":
                    stage["status"] = "COMPLETED"
                elif status == "MONITOR_POINTS_FAILED":
                    stage["status"] = "FAILED"
                elif status == "PRODUCTS_READY":
                    stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["publish_products"] = run_manifest.get("publish_products")
        command_manifest["monitor_point_products"] = run_manifest.get("monitor_point_products")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _build_command_manifest(self, run_manifest: dict[str, Any], stack_manifest: dict[str, Any]) -> dict[str, Any]:
        scenes = stack_manifest.get("scenes") or []
        pair_network = stack_manifest.get("pair_network") or {}
        sensor_family = self._normalize_sensor_family(
            run_manifest.get("sensor_family") or stack_manifest.get("sensor_family")
        )
        if sensor_family == "S1":
            return {
                "schema": "insar.gamma-command-manifest/v1",
                "run_id": run_manifest["run_id"],
                "engine": "gamma",
                "processor_code": "gamma_ipta_sbas",
                "profile_code": "s1_gamma_sbas",
                "execution_enabled": False,
                "reason_execution_disabled": "Sentinel-1 Gamma TOPS/SBAS execution scripts are not enabled yet.",
                "stage_plan": [dict(item) for item in S1_GAMMA_SBAS_PLANNING_STEPS],
                "inputs": {
                    "scene_count": len(scenes),
                    "scenes": [
                        {
                            "date": scene.get("date"),
                            "scene_name": scene.get("scene_name"),
                            "source_format": scene.get("source_format"),
                            "source_wsl": scene.get("source_wsl"),
                            "orbit_wsl": scene.get("orbit_wsl"),
                            "relative_orbit": scene.get("relative_orbit"),
                            "orbit_direction": scene.get("orbit_direction"),
                            "imaging_mode": scene.get("imaging_mode"),
                            "polarization": scene.get("polarization"),
                        }
                        for scene in scenes
                    ],
                    "pair_count": len(pair_network.get("pairs") or []),
                    "pair_network_strategy": pair_network.get("strategy"),
                },
                "expected_outputs": [],
                "next_manual_review": "Verify Sentinel-1 subswath/burst policy and implement dedicated Gamma TOPS/SBAS scripts before enabling execution.",
            }
        return {
            "schema": "insar.gamma-command-manifest/v1",
            "run_id": run_manifest["run_id"],
            "engine": "gamma",
            "processor_code": "gamma_ipta_sbas",
            "execution_enabled": True,
            "reason_execution_disabled": None,
            "stage_plan": [dict(item) for item in GAMMA_STAGE_PLAN],
            "expert_document_steps": [dict(item) for item in GAMMA_SBAS_EXPERT_DOCUMENT_STEPS],
            "inputs": {
                "scene_count": len(scenes),
                "scenes": [
                    {
                        "date": scene.get("date"),
                        "scene_name": scene.get("scene_name"),
                        "tiff_wsl": scene.get("tiff_wsl"),
                        "meta_wsl": scene.get("meta_wsl"),
                        "orbit_wsl": scene.get("orbit_wsl"),
                    }
                    for scene in scenes
                ],
                "pair_count": len(pair_network.get("pairs") or []),
                "pair_network_strategy": pair_network.get("strategy"),
            },
            "expected_outputs": [item["relative_path"] for item in PRODUCT_DEFINITIONS],
            "next_manual_review": "Run Gamma base_calc, inspect perpendicular/temporal baselines, then replace initial adjacent itab if needed.",
        }

    def _build_run_card(self, run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        stack = manifest.get("stack") or {}
        stack_manifest = self._load_stack_manifest_for_run(run_dir, manifest)
        identity = self._scene_identity_summary(stack_manifest.get("scenes") or [])
        stack_from_manifest = stack_manifest.get("stack") or {}
        try:
            coverage = self._build_run_geographic_coverage(run_dir, manifest)
        except Exception:
            coverage = {}
        return {
            "run_id": manifest.get("run_id") or run_dir.name,
            "run_label": manifest.get("run_label"),
            "status": manifest.get("status") or "UNKNOWN",
            "created_at": manifest.get("created_at"),
            "workflow_code": manifest.get("workflow_code"),
            "processor_code": manifest.get("processor_code"),
            "engine_code": manifest.get("engine_code"),
            "sensor_family": manifest.get("sensor_family") or self._normalize_sensor_family(stack.get("satellite")),
            "profile_code": manifest.get("profile_code"),
            "execution_enabled": manifest.get("execution_enabled", True),
            "stack_id": manifest.get("stack_id"),
            "scene_count": manifest.get("scene_count"),
            "pair_count": manifest.get("pair_count"),
            "next_stage": manifest.get("next_stage"),
            "discovery_mode": manifest.get("discovery_mode"),
            "aoi": manifest.get("aoi"),
            "common_overlap_ratio": manifest.get("common_overlap_ratio"),
            "min_common_overlap_ratio": manifest.get("min_common_overlap_ratio"),
            "scene_identity_hash": manifest.get("scene_identity_hash") or identity.get("scene_identity_hash"),
            "scene_name_count": manifest.get("scene_name_count") or identity.get("scene_name_count"),
            "scene_name_preview": manifest.get("scene_name_preview") or identity.get("scene_name_preview") or [],
            "scene_names": manifest.get("scene_names") or identity.get("scene_names") or [],
            "date_sequence_hash": manifest.get("date_sequence_hash") or identity.get("date_sequence_hash"),
            "platform": stack.get("satellite"),
            "relative_orbit": stack.get("relative_orbit"),
            "direction": stack.get("orbit_direction"),
            "polarization": stack.get("polarization"),
            "center_bucket": stack.get("center_bucket") or stack_from_manifest.get("center_bucket"),
            "reference_date": stack.get("reference_date"),
            "date_start": coverage.get("date_start"),
            "date_end": coverage.get("date_end"),
            "center": coverage.get("center"),
            "admin_region": coverage.get("admin_region"),
            "run_dir": str(run_dir),
        }

    def _build_run_artifacts(self, run_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for relative_path, label, role in [
            ("run_manifest.json", "SBAS run manifest", "run_manifest"),
            ("stack_manifest.json", "Stack manifest", "stack_manifest"),
            ("pair_network.json", "Initial pair network", "pair_network"),
            ("pair_network_baseline_audit.json", "Gamma baseline-audited pair network", "pair_network_baseline_audit"),
            ("baseline_audit_summary.json", "Gamma baseline audit summary", "baseline_audit_summary"),
            ("itab_decision.json", "Approved/rejected itab decision", "itab_decision"),
            ("coregistration_plan.json", "Coregistration stage plan", "coregistration_plan"),
            ("coregistration_summary.json", "Coregistration execution summary", "coregistration_summary"),
            ("rdc_dem_plan.json", "RDC DEM stage plan", "rdc_dem_plan"),
            ("rdc_dem_summary.json", "RDC DEM execution summary", "rdc_dem_summary"),
            ("interferogram_plan.json", "Interferogram stage plan", "interferogram_plan"),
            ("interferogram_summary.json", "Interferogram execution summary", "interferogram_summary"),
            ("detrend_atm_plan.json", "Detrend/atmospheric correction stage plan", "detrend_atm_plan"),
            ("detrend_atm_summary.json", "Detrend/atmospheric correction execution summary", "detrend_atm_summary"),
            ("ipta_timeseries_plan.json", "IPTA time-series stage plan", "ipta_timeseries_plan"),
            ("ipta_timeseries_summary.json", "IPTA time-series execution summary", "ipta_timeseries_summary"),
            ("publish_product_plan.json", "Publish product stage plan", "publish_product_plan"),
            ("publish_product_summary.json", "Publish product execution summary", "publish_product_summary"),
            ("product_summary.json", "Published SBAS product summary", "product_summary"),
            ("quality_summary.json", "Published SBAS quality summary", "quality_summary"),
            ("monitor_points_plan.json", "Monitoring-point extraction plan", "monitor_points_plan"),
            ("monitor_points_summary.json", "Monitoring-point extraction summary", "monitor_points_summary"),
            ("workflow_summary.json", "Gamma SBAS workflow summary", "workflow_summary"),
            ("gamma_command_manifest.json", "Gamma command manifest", "command_manifest"),
            ("monitor_points.json", "Monitoring-point configuration", "monitor_points"),
            ("scripts/01_baseline_audit.sh", "Gamma baseline audit script", "baseline_audit_script"),
            ("scripts/02_coreg_common_ref.sh", "Gamma common-reference coregistration script", "coregistration_script"),
            ("scripts/03_prepare_rdc_dem.sh", "Gamma RDC DEM script", "rdc_dem_script"),
            ("scripts/04_diff_unwrap_common_ref.sh", "Gamma differential interferogram script", "interferogram_script"),
            ("scripts/05_detrend_atm.sh", "Gamma detrend/atmospheric correction script", "detrend_atm_script"),
            ("scripts/05_mb_ts_rate.sh", "Gamma IPTA mb/ts_rate script", "ipta_timeseries_script"),
            ("scripts/07_publish_products.sh", "Gamma product publishing script", "publish_products_script"),
            ("scripts/08_point_timeseries.sh", "Monitoring-point time-series script", "monitor_points_script"),
            ("scripts/01_workspace_data.sh", "Expert section 1 workspace/data script", "expert_workflow_script"),
            ("scripts/02_import_lt1_slc.sh", "Expert section 2 LT1 SLC import script", "expert_workflow_script"),
            ("scripts/03_reference_mli.sh", "Expert section 3 reference MLI script", "expert_workflow_script"),
            ("scripts/04_dem_lookup.sh", "Expert section 4 DEM lookup script", "expert_workflow_script"),
            ("scripts/05_coreg_prep.sh", "Expert section 5 coregistration prep script", "expert_workflow_script"),
            ("scripts/06_coregister_scenes.sh", "Expert section 6 coregister scenes script", "expert_workflow_script"),
            ("scripts/07_rmli_average.sh", "Expert section 7 RMLI average script", "expert_workflow_script"),
            ("scripts/08_diff_network.sh", "Expert section 8 differential network script", "expert_workflow_script"),
            ("scripts/09_filter_unwrap.sh", "Expert section 9 filter and unwrap script", "expert_workflow_script"),
            ("scripts/10_detrend_atm.sh", "Expert section 10 detrend/ATM script", "expert_workflow_script"),
            ("scripts/11_sbas_inversion.sh", "Expert section 11 SBAS inversion script", "expert_workflow_script"),
            ("scripts/12_outputs_points.sh", "Expert section 12 outputs and points script", "expert_workflow_script"),
        ]:
            path = run_dir / relative_path
            if path.is_file():
                artifacts.append(
                    {
                        "key": Path(relative_path).stem,
                        "label": label,
                        "role": role,
                        "relative_path": relative_path,
                        "size_bytes": path.stat().st_size,
                    }
                )
        for item in PRODUCT_DEFINITIONS:
            if item["key"] == "trial_summary_json":
                continue
            path = run_dir / item["relative_path"]
            if path.is_file():
                artifacts.append(
                    {
                        **item,
                        "size_bytes": path.stat().st_size,
                    }
                )
        monitor_dir = run_dir / "publish" / "monitor_points"
        if monitor_dir.is_dir():
            for path in sorted(monitor_dir.iterdir()):
                if not path.is_file():
                    continue
                for suffix_key, label, ext in MONITOR_ARTIFACT_SUFFIXES:
                    if path.name.endswith(ext):
                        artifacts.append(
                            {
                                "key": f"monitor_{path.stem}_{suffix_key}",
                                "label": label,
                                "role": "monitor_point",
                                "relative_path": str(path.relative_to(run_dir)).replace("\\", "/"),
                                "size_bytes": path.stat().st_size,
                            }
                        )
                        break
        return artifacts

    def _build_trial_card(self, trial_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
        stack = summary.get("stack") or {}
        quality = summary.get("quality_stats") or {}
        exports = summary.get("exports") or {}
        monitor_points = summary.get("monitor_points") or []
        primary_rate_stats = quality.get("los_rate_toward_mm_per_year_rdc") or {}
        sigma_stats = quality.get("los_sigma_mm_per_year_rdc") or {}
        return {
            "trial_id": summary.get("trial_id") or trial_dir.name,
            "status": "TRIAL_READY",
            "generated_at": summary.get("generated_at"),
            "engine": summary.get("engine") or {},
            "stack": stack,
            "dates": stack.get("dates") or [],
            "reference_date": stack.get("reference_date"),
            "scene_count": len(stack.get("dates") or []),
            "platform": stack.get("platform"),
            "direction": stack.get("direction"),
            "relative_orbit": stack.get("relative_orbit"),
            "polarization": stack.get("polarization"),
            "mode": stack.get("mode"),
            "default_los_product": "los_rate_toward_mm_per_year",
            "los_sign_convention": (summary.get("radar") or {}).get("los_sign_convention"),
            "primary_rate_median_mm_year": primary_rate_stats.get("median"),
            "primary_rate_p01_mm_year": primary_rate_stats.get("p01"),
            "primary_rate_p99_mm_year": primary_rate_stats.get("p99"),
            "sigma_median_mm_year": sigma_stats.get("median"),
            "monitor_point_count": len(monitor_points),
            "export_count": len(exports),
            "trial_dir": str(trial_dir),
        }

    def _build_artifacts(self, trial_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for item in PRODUCT_DEFINITIONS:
            path = trial_dir / item["relative_path"]
            if path.is_file():
                artifacts.append(
                    {
                        **item,
                        "size_bytes": path.stat().st_size,
                    }
                )

        monitor_dir = trial_dir / "publish" / "monitor_points"
        if monitor_dir.is_dir():
            for path in sorted(monitor_dir.iterdir()):
                if not path.is_file():
                    continue
                for suffix_key, label, ext in MONITOR_ARTIFACT_SUFFIXES:
                    if path.name.endswith(ext):
                        artifacts.append(
                            {
                                "key": f"monitor_{path.stem}_{suffix_key}",
                                "label": label,
                                "role": "monitor_point",
                                "relative_path": str(path.relative_to(trial_dir)).replace("\\", "/"),
                                "size_bytes": path.stat().st_size,
                            }
                        )
                        break
        return artifacts


sbas_insar_production_service = SbasInsarProductionService()
