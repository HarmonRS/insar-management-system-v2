"""D-InSAR workflow functions extracted from envi_service.py."""
from __future__ import annotations

import math
import os
import time
import defusedxml.ElementTree as ET
from glob import glob
from typing import Any, Dict, List, Optional

from .envi_service import (
    DEFAULT_TIMEOUT,
    DEM_BASE_FILE,
    RUNTIME_DIR,
    CUSTOM_TARGET_RESOLUTION_M,
    CUSTOM_FILTER_METHOD,
    CUSTOM_UNWRAP_COH_THRESHOLD,
    CUSTOM_GCP_COH_THRESHOLD,
    CUSTOM_GCP_NUMBER,
    CUSTOM_GEOCODING_COH_THRESHOLD,
    CUSTOM_GEOCODING_PIXEL_SIZE_M,
    _read_env,
    _normalize_path,
    _to_local_path,
    _collect_task_folders,
    _find_meta_files,
    _has_sml,
    _first_sml_base,
    _build_sarscapedata,
    _unwrap_sarscapedata,
    execute_envi_task,
    _write_progress,
)

# Stability check configuration
_STABILITY_INTERVAL = int(_read_env("ENVI_STABILITY_CHECK_INTERVAL", "15") or 15)
_STABILITY_ROUNDS = int(_read_env("ENVI_STABILITY_ROUNDS", "3") or 3)
_STABILITY_MAX_WAIT = int(_read_env("ENVI_STABILITY_MAX_WAIT", "3600") or 3600)


def run_dinsar_workflow(
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run D-InSAR metatask on Task_* folders.

    Smart chaining: for each Task_* folder, if master/slave lack .sml files,
    automatically run Import first, then proceed with D-InSAR.
    DEM path is read from .env (IDL_DINSAR_DEM_BASE_FILE).
    """
    root_dir = _to_local_path(root_dir)
    dem_base_file = DEM_BASE_FILE
    if not root_dir or not os.path.isdir(root_dir):
        raise ValueError(f"D-InSAR root directory does not exist: {root_dir}")
    if not dem_base_file:
        raise ValueError(
            "DEM path not configured. Set IDL_DINSAR_DEM_BASE_FILE in .env"
        )

    task_folders = _collect_task_folders(root_dir)
    log_lines: List[str] = [
        f"[envi] dinsar metatask",
        f"[envi] root_dir={root_dir}",
        f"[envi] dem={dem_base_file}",
        f"[envi] Task_* folders={len(task_folders)}",
    ]
    if not task_folders:
        return {
            "summary": {
                "task_folders": 0,
                "processed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_imported": 0,
            },
            "log_lines": log_lines,
        }

    processed = 0
    failed = 0
    skipped = 0
    auto_imported = 0

    for folder in task_folders:
        if num_to_process > 0 and processed >= num_to_process:
            log_lines.append(f"[envi] reached limit={num_to_process}")
            break
        task_name = os.path.basename(folder)
        master_dir = os.path.join(folder, "master")
        slave_dir = os.path.join(folder, "slave")

        if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
            skipped += 1
            log_lines.append(f"[skip] {task_name}: master/slave dir missing")
            continue

        # --- Smart chaining: auto-import if .sml missing ---
        for side, side_dir in [("master", master_dir), ("slave", slave_dir)]:
            if not _has_sml(side_dir):
                meta_files = _find_meta_files(side_dir)
                if not meta_files:
                    log_lines.append(
                        f"[warn] {task_name}/{side}: no .sml and no .meta.xml"
                    )
                    continue
                log_lines.append(
                    f"[auto-import] {task_name}/{side}: "
                    f"importing {len(meta_files)} file(s)"
                )
                for mf in meta_files:
                    start = time.time()
                    try:
                        execute_envi_task(
                            "SARsImportLuTan1",
                            {
                                "INPUT_FILE_LIST": [mf],
                                "ROOT_URI_FOR_OUTPUT": side_dir,
                            },
                        )
                        elapsed = round(time.time() - start, 1)
                        auto_imported += 1
                        log_lines.append(
                            f"[auto-import ok] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s)"
                        )
                    except Exception as exc:
                        elapsed = round(time.time() - start, 1)
                        log_lines.append(
                            f"[auto-import err] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s): {exc}"
                        )

        # After auto-import, check .sml again
        master_base = _first_sml_base(master_dir)
        slave_base = _first_sml_base(slave_dir)
        if not master_base or not slave_base:
            skipped += 1
            log_lines.append(
                f"[skip] {task_name}: still missing .sml after import "
                f"(master={'yes' if master_base else 'no'} "
                f"slave={'yes' if slave_base else 'no'})"
            )
            continue

        output_dir = os.path.join(folder, "dinsar_results")
        os.makedirs(output_dir, exist_ok=True)

        start = time.time()
        try:
            execute_envi_task(
                "SARsMetataskInSARDisplacementGeneration",
                {
                    "REFERENCE_SARSCAPEDATA": _build_sarscapedata(master_base),
                    "SECONDARY_SARSCAPEDATA": _build_sarscapedata(slave_base),
                    "DEM_SARSCAPEDATA": _build_sarscapedata(dem_base_file),
                    "OUTPUT_FOLDER": _normalize_path(output_dir),
                },
            )
            elapsed = round(time.time() - start, 1)
            processed += 1
            log_lines.append(f"[ok] dinsar {task_name} ({elapsed}s)")
        except Exception as exc:
            elapsed = round(time.time() - start, 1)
            failed += 1
            log_lines.append(
                f"[err] dinsar {task_name} failed ({elapsed}s): {exc}"
            )

    if failed > 0 and processed == 0 and len(task_folders) > 0:
        detail = "\n".join(log_lines[-20:])
        raise RuntimeError(
            f"All D-InSAR tasks failed. failed={failed}, "
            f"skipped={skipped}.\n{detail}"
        )

    return {
        "summary": {
            "task_folders": len(task_folders),
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "auto_imported": auto_imported,
        },
        "log_lines": log_lines,
    }


def _read_sml_parameter(sml_file: str, param_name: str) -> Optional[str]:
    """Read a parameter value from a SARscape .sml XML file."""
    sml_path = _to_local_path(sml_file)
    if not os.path.isfile(sml_path):
        return None
    try:
        tree = ET.parse(sml_path)
        root = tree.getroot()
        tag_upper = param_name.upper()
        for elem in root.iter():
            local_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_tag.upper() == tag_upper and elem.text:
                return elem.text.strip()
    except Exception as exc:
        print(f"[WARN] _read_sml: {exc}")
    return None


def _calculate_looks(
    master_sml: str,
    slave_sml: str,
    target_resolution: float,
) -> tuple:
    """Calculate range and azimuth looks from SML pixel spacing."""
    m_rg = _read_sml_parameter(master_sml, "PixelSpacingRg")
    m_az = _read_sml_parameter(master_sml, "PixelSpacingAz")
    m_inc = _read_sml_parameter(master_sml, "IncidenceAngle")
    s_rg = _read_sml_parameter(slave_sml, "PixelSpacingRg")
    s_az = _read_sml_parameter(slave_sml, "PixelSpacingAz")
    s_inc = _read_sml_parameter(slave_sml, "IncidenceAngle")

    if not all([m_rg, m_az, m_inc, s_rg, s_az, s_inc]):
        raise ValueError(
            "Cannot read pixel spacing / incidence angle from SML files. "
            f"master={master_sml} slave={slave_sml}"
        )

    m_rg_f, m_az_f, m_inc_f = float(m_rg), float(m_az), float(m_inc)
    s_rg_f, s_az_f, s_inc_f = float(s_rg), float(s_az), float(s_inc)

    avg_az = (m_az_f + s_az_f) / 2.0
    azimuth_looks = max(1, int(target_resolution / avg_az))

    m_ground_rg = m_rg_f / math.sin(math.radians(m_inc_f))
    s_ground_rg = s_rg_f / math.sin(math.radians(s_inc_f))
    avg_ground_rg = (m_ground_rg + s_ground_rg) / 2.0
    range_looks = max(1, int(target_resolution / avg_ground_rg))

    return range_looks, azimuth_looks


def _find_latest_sarscapedata(
    directory: str,
    pattern_fragment: str,
    log_lines: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Scan directory for the latest SARscape file matching a pattern."""
    pattern = os.path.join(directory, f"*{pattern_fragment}*.sml")
    candidates = sorted(glob(pattern), key=os.path.getmtime, reverse=True)
    if log_lines is not None:
        log_lines.append(f"[scan] pattern={pattern} found={len(candidates)}")
    if not candidates:
        return None
    sml_path = candidates[0]
    base = sml_path[:-4]  # strip .sml
    if log_lines is not None:
        log_lines.append(f"[scan] using: {os.path.basename(base)}")
    return _build_sarscapedata(base)


def _wait_files_stable(
    directory: str,
    log_lines: Optional[List[str]] = None,
) -> None:
    """Wait until all files in directory have stable sizes."""
    if not directory or not os.path.isdir(directory):
        return

    def _snapshot() -> Dict[str, int]:
        sizes: Dict[str, int] = {}
        try:
            for root, _dirs, files in os.walk(directory):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sizes[fp] = os.path.getsize(fp)
                    except OSError as exc:
                        print(f"[WARN] _snapshot getsize: {exc}")
        except Exception as exc:
            print(f"[WARN] _snapshot walk: {exc}")
        return sizes

    stable_count = 0
    prev = _snapshot()
    wait_start = time.time()

    while stable_count < _STABILITY_ROUNDS:
        elapsed = time.time() - wait_start
        if elapsed > _STABILITY_MAX_WAIT:
            if log_lines is not None:
                log_lines.append(
                    f"[stability] max wait {_STABILITY_MAX_WAIT}s reached, proceeding"
                )
            break
        time.sleep(_STABILITY_INTERVAL)
        cur = _snapshot()
        if cur == prev:
            stable_count += 1
        else:
            stable_count = 0
            prev = cur

    total_wait = round(time.time() - wait_start, 1)
    if log_lines is not None:
        log_lines.append(
            f"[stability] files stable after {total_wait}s "
            f"({stable_count}/{_STABILITY_ROUNDS} rounds, "
            f"{len(prev)} files)"
        )


def _wait_for_disp_stable(
    directory: str,
    log_lines: Optional[List[str]] = None,
) -> bool:
    """Wait for *_rsp_disp file to appear and stabilize."""
    if not directory or not os.path.isdir(directory):
        return False

    wait_start = time.time()
    disp_path = None
    while (time.time() - wait_start) < _STABILITY_MAX_WAIT:
        for f in os.listdir(directory):
            if f.endswith("_rsp_disp") and not f.endswith(".hdr") and not f.endswith(".sml"):
                disp_path = os.path.join(directory, f)
                break
        if disp_path:
            break
        if log_lines is not None and int(time.time() - wait_start) % 60 == 0:
            log_lines.append(
                f"[wait_disp] waiting for _rsp_disp file... "
                f"({int(time.time() - wait_start)}s)"
            )
        time.sleep(_STABILITY_INTERVAL)

    if not disp_path:
        if log_lines is not None:
            log_lines.append(
                f"[wait_disp] _rsp_disp file not found after "
                f"{int(time.time() - wait_start)}s"
            )
        return False

    if log_lines is not None:
        log_lines.append(
            f"[wait_disp] found {os.path.basename(disp_path)} "
            f"after {int(time.time() - wait_start)}s"
        )

    stable_count = 0
    prev_size = None
    while stable_count < _STABILITY_ROUNDS:
        if (time.time() - wait_start) > _STABILITY_MAX_WAIT:
            if log_lines is not None:
                log_lines.append("[wait_disp] max wait reached, proceeding")
            break
        time.sleep(_STABILITY_INTERVAL)
        try:
            cur_size = os.path.getsize(disp_path)
        except OSError:
            cur_size = -1
        if cur_size >= 0 and cur_size == prev_size:
            stable_count += 1
        else:
            stable_count = 0
            prev_size = cur_size

    total_wait = round(time.time() - wait_start, 1)
    if log_lines is not None:
        log_lines.append(
            f"[wait_disp] stable after {total_wait}s "
            f"(size={prev_size} bytes)"
        )
    return True


def _generate_gcps(
    coherence_file: str,
    output_shp: str,
    coh_threshold: float = 0.7,
    num_points: int = 100,
    log_lines: Optional[List[str]] = None,
) -> bool:
    """Generate GCP shapefile from coherence raster."""
    try:
        import numpy as np
        import rasterio
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError as exc:
        raise RuntimeError(
            "rasterio, geopandas, and shapely are required for GCP generation. "
            f"Missing: {exc}"
        ) from exc

    coh_path = _to_local_path(coherence_file)
    if not os.path.isfile(coh_path):
        for ext in [".hdr", ""]:
            candidate = coh_path + ext
            if os.path.isfile(candidate):
                coh_path = candidate
                break

    if not os.path.isfile(coh_path):
        if log_lines is not None:
            log_lines.append(f"[gcp] coherence file not found: {coh_path}")
        return False

    with rasterio.open(coh_path) as src:
        data = src.read(1)
        ns, nl = src.width, src.height

    grid_dim = math.ceil(math.sqrt(num_points))
    x_step = nl // grid_dim
    y_step = ns // grid_dim

    points = []
    for j in range(grid_dim):
        for i in range(grid_dim):
            x_start = i * y_step
            y_start = j * x_step
            x_end = min((i + 1) * y_step, ns)
            y_end = min((j + 1) * x_step, nl)
            if x_start >= ns or y_start >= nl:
                continue
            cell = data[y_start:y_end, x_start:x_end]
            max_val = float(np.nanmax(cell)) if cell.size > 0 else 0.0
            if max_val >= coh_threshold:
                idx = int(np.nanargmax(cell))
                cell_h, cell_w = cell.shape
                max_row = idx // cell_w
                max_col = idx % cell_w
                px_col = x_start + max_col
                px_row = y_start + max_row
                points.append((px_col, px_row))

    if not points:
        if log_lines is not None:
            log_lines.append(
                f"[gcp] no points found above threshold {coh_threshold}"
            )
        return False

    records = []
    for idx, (col, row) in enumerate(points):
        records.append({
            "SHP_ID": idx,
            "GCP_LABEL": f"GCP_{idx + 1}",
            "GCP_TYPE": "undefined",
            "GCP_COLUMN": float(col),
            "GCP_ROW": float(row),
            "GCP_OTHER_": "",
            "geometry": Point(float(col), float(row)),
        })

    gdf = gpd.GeoDataFrame(records)
    out_path = _to_local_path(output_shp)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    gdf.to_file(out_path, driver="ESRI Shapefile")

    if log_lines is not None:
        log_lines.append(f"[gcp] created {len(points)} GCPs -> {out_path}")
    return True


def _run_dinsar_custom_single(
    master_base: str,
    slave_base: str,
    dem_base: str,
    output_root: str,
    log_lines: List[str],
    job_id: Optional[str] = None,
    pair_index: int = 0,
    total_pairs: int = 0,
    pair_name: str = "",
) -> bool:
    """Execute the 6-step custom D-InSAR workflow for one pair."""
    master_sml = master_base + ".sml"
    slave_sml = slave_base + ".sml"

    master_sd = _build_sarscapedata(master_base)
    slave_sd = _build_sarscapedata(slave_base)
    dem_sd = _build_sarscapedata(dem_base)
    out_dir = _normalize_path(os.path.dirname(output_root))

    try:
        range_looks, azimuth_looks = _calculate_looks(
            master_sml, slave_sml, CUSTOM_TARGET_RESOLUTION_M
        )
        log_lines.append(
            f"[custom] looks: range={range_looks} azimuth={azimuth_looks} "
            f"(target_res={CUSTOM_TARGET_RESOLUTION_M}m)"
        )
    except Exception as exc:
        log_lines.append(f"[custom] looks calculation failed: {exc}")
        return False

    # === STEP 1: Interferogram Generation ===
    log_lines.append("[custom] step 1/6: Interferogram Generation")
    _write_progress(job_id, 1, 6, "Interferogram Generation", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    try:
        r1 = execute_envi_task(
            "SARsInSARInterferogramGeneration",
            {
                "REFERENCE_SARSCAPEDATA": master_sd,
                "SECONDARY_SARSCAPEDATA": slave_sd,
                "DEM_SARSCAPEDATA": dem_sd,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "RG_LOOKS_NBR": float(range_looks),
                "AZ_LOOKS_NBR": float(azimuth_looks),
                "COREGISTRATION_WITH_DEM": True,
            },
        )
        log_lines.append(f"[custom] step 1 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        log_lines.append(
            f"[custom] step 1 failed ({round(time.time() - start, 1)}s): {exc}"
        )
        return False

    s1_dint = _unwrap_sarscapedata(r1.get("DINT_SARSCAPEDATA"))
    s1_ref_pwr = _unwrap_sarscapedata(r1.get("REFERENCE_POWER_SARSCAPEDATA"))
    s1_sec_pwr = _unwrap_sarscapedata(r1.get("SECONDARY_POWER_SARSCAPEDATA"))
    s1_sint = _unwrap_sarscapedata(r1.get("SINT_SARSCAPEDATA"))
    s1_srdem = _unwrap_sarscapedata(r1.get("SRDEM_SARSCAPEDATA"))

    # === STEP 2: Filtering and Coherence ===
    log_lines.append("[custom] step 2/6: Filtering and Coherence")
    _write_progress(job_id, 2, 6, "Filtering and Coherence", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    try:
        r2 = execute_envi_task(
            "SARsInSARFilterAndCoherence",
            {
                "DINT_SARSCAPEDATA": s1_dint,
                "REFERENCE_SARSCAPEDATA": s1_ref_pwr,
                "SECONDARY_SARSCAPEDATA": s1_sec_pwr,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "FILTERING_METHOD": CUSTOM_FILTER_METHOD,
                "COHERENCE": True,
                "INTERF_FILT": True,
            },
        )
        log_lines.append(f"[custom] step 2 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        log_lines.append(
            f"[custom] step 2 failed ({round(time.time() - start, 1)}s): {exc}"
        )
        return False

    s2_fint = _unwrap_sarscapedata(r2.get("FINT_SARSCAPEDATA"))
    s2_cc = _unwrap_sarscapedata(r2.get("COHERENCE_SARSCAPEDATA"))

    # === STEP 3: Remove Residual Phase Frequency ===
    log_lines.append("[custom] step 3/6: Orbital Trend Removal")
    _write_progress(job_id, 3, 6, "Orbital Trend Removal", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    s3_rrpf = None
    try:
        r3 = execute_envi_task(
            "SARsInSARRemoveResidualPhaseFrequency",
            {
                "INTERFEROGRAM_SARSCAPEDATA": s2_fint,
                "COHERENCE_FILE_NAME": s2_cc,
                "ROOT_URI_FOR_OUTPUT": out_dir,
            },
        )
        s3_rrpf = _unwrap_sarscapedata(r3.get("RRPF_DINT_SARSCAPEDATA"))
        log_lines.append(f"[custom] step 3 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 3 engine error ({elapsed}s): {exc}")
        log_lines.append("[custom] step 3 scanning for generated RRPF file...")
        s3_rrpf = _find_latest_sarscapedata(
            _to_local_path(out_dir), "ISARRRPF", log_lines
        )

    if not s3_rrpf:
        log_lines.append("[custom] step 3 failed: no RRPF output found")
        return False

    # === STEP 4: Phase Unwrapping ===
    log_lines.append("[custom] step 4/6: Phase Unwrapping")
    _write_progress(job_id, 4, 6, "Phase Unwrapping", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    s4_upha = None
    try:
        r4 = execute_envi_task(
            "SARsInSARPhaseUnwrapping",
            {
                "INFILE_NAME": s3_rrpf,
                "COHERENCEFILE_NAME": s2_cc,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "UPHA_COH_THRESHOLD": CUSTOM_UNWRAP_COH_THRESHOLD,
            },
        )
        s4_upha = _unwrap_sarscapedata(r4.get("OUTFILE_NAME"))
        log_lines.append(f"[custom] step 4 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 4 engine error ({elapsed}s): {exc}")
        log_lines.append("[custom] step 4 scanning for generated UPHA file...")
        s4_upha = _find_latest_sarscapedata(
            _to_local_path(out_dir), "ISARPU", log_lines
        )

    if not s4_upha:
        log_lines.append("[custom] step 4 failed: no UPHA output found")
        return False

    # === STEP 5: Refinement and Reflattening ===
    log_lines.append("[custom] step 5a/6: GCP Generation")
    _write_progress(job_id, 5, 6, "GCP Generation + Refinement", out_dir, pair_index, total_pairs, pair_name)
    cc_url = s2_cc.get("url", "") if isinstance(s2_cc, dict) else str(s2_cc)
    cc_local = _to_local_path(cc_url)
    auto_gcp_shp = os.path.join(os.path.dirname(cc_local) or out_dir, "auto_gcp.shp")
    gcp_ok = _generate_gcps(
        coherence_file=cc_local,
        output_shp=auto_gcp_shp,
        coh_threshold=CUSTOM_GCP_COH_THRESHOLD,
        num_points=CUSTOM_GCP_NUMBER,
        log_lines=log_lines,
    )
    if not gcp_ok:
        log_lines.append("[custom] step 5a failed: GCP generation returned no points")
        return False

    log_lines.append("[custom] step 5b/6: Refinement and Reflattening")
    start = time.time()
    s5_upha = None
    try:
        r5 = execute_envi_task(
            "SARsInSARRefinementAndReflattening",
            {
                "INPUT_UPHA_FILE_NAME": s4_upha,
                "REFERENCE_SARSCAPEDATA": s1_ref_pwr,
                "SECONDARY_SARSCAPEDATA": s1_sec_pwr,
                "SLANT_RANGE_DEM_FILE_NAME": s1_srdem,
                "SYNTHETIC_FILE_NAME": s1_sint,
                "COHERENCE_FILE_NAME": s2_cc,
                "DEM_SARSCAPEDATA": dem_sd,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "REFINEMENT_GCP_FILE_NAME": _normalize_path(auto_gcp_shp),
            },
        )
        s5_upha = _unwrap_sarscapedata(r5.get("UPHA_REFLAT_SARSCAPEDATA"))
        log_lines.append(f"[custom] step 5b ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 5b engine error ({elapsed}s): {exc}")
        log_lines.append("[custom] step 5b scanning for generated REFLAT UPHA file...")
        s5_upha = _find_latest_sarscapedata(
            _to_local_path(out_dir), "ISARRF", log_lines
        )
        if not s5_upha:
            s5_upha = _find_latest_sarscapedata(
                _to_local_path(out_dir), "_reflat_upha", log_lines
            )

    if not s5_upha:
        log_lines.append("[custom] step 5b failed: no REFLAT UPHA output found")
        return False

    # === STEP 6: Phase to Displacement and Geocoding ===
    log_lines.append("[custom] step 6/6: Phase to Displacement + Geocoding")
    _write_progress(job_id, 6, 6, "Phase to Displacement + Geocoding", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    try:
        execute_envi_task(
            "SARsInSARPhaseToDisplacement",
            {
                "INPUT_SARSCAPEDATA": s5_upha,
                "COHERNCE_SARSCAPEDATA": s2_cc,
                "DEM_SARSCAPEDATA": dem_sd,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "COHERENCE_THRESHOLD": CUSTOM_GEOCODING_COH_THRESHOLD,
                "GEOCODE_RG_GRID_SIZE": CUSTOM_GEOCODING_PIXEL_SIZE_M,
                "GEOCODE_AZ_GRID_SIZE": CUSTOM_GEOCODING_PIXEL_SIZE_M,
            },
        )
        log_lines.append(f"[custom] step 6 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 6 engine error ({elapsed}s): {exc}")

    log_lines.append("[custom] step 6: waiting for _rsp_disp file...")
    disp_ok = _wait_for_disp_stable(_to_local_path(out_dir), log_lines)
    if not disp_ok:
        log_lines.append("[custom] step 6 failed: _rsp_disp never appeared")
        return False

    log_lines.append("[custom] final stability check on output directory...")
    _wait_files_stable(_to_local_path(out_dir), log_lines)

    _write_progress(job_id, 6, 6, "Completed", out_dir, pair_index, total_pairs, pair_name)
    return True


def run_dinsar_custom_workflow(
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run custom 6-step D-InSAR on Task_* folders."""
    root_dir = _to_local_path(root_dir)
    dem_base_file = DEM_BASE_FILE
    if not root_dir or not os.path.isdir(root_dir):
        raise ValueError(f"D-InSAR root directory does not exist: {root_dir}")
    if not dem_base_file:
        raise ValueError(
            "DEM path not configured. Set IDL_DINSAR_DEM_BASE_FILE in .env"
        )

    task_folders = _collect_task_folders(root_dir)
    log_lines: List[str] = [
        f"[envi] dinsar custom (6-step)",
        f"[envi] root_dir={root_dir}",
        f"[envi] dem={dem_base_file}",
        f"[envi] target_resolution={CUSTOM_TARGET_RESOLUTION_M}m",
        f"[envi] filter={CUSTOM_FILTER_METHOD}",
        f"[envi] unwrap_coh={CUSTOM_UNWRAP_COH_THRESHOLD}",
        f"[envi] gcp_coh={CUSTOM_GCP_COH_THRESHOLD} gcp_n={CUSTOM_GCP_NUMBER}",
        f"[envi] geocode_coh={CUSTOM_GEOCODING_COH_THRESHOLD} "
        f"geocode_px={CUSTOM_GEOCODING_PIXEL_SIZE_M}m",
        f"[envi] Task_* folders={len(task_folders)}",
    ]
    if not task_folders:
        return {
            "summary": {
                "task_folders": 0,
                "processed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_imported": 0,
            },
            "log_lines": log_lines,
        }

    processed = 0
    failed = 0
    skipped = 0
    auto_imported = 0
    effective_total = len(task_folders) if num_to_process <= 0 else min(num_to_process, len(task_folders))
    pair_counter = 0

    for folder in task_folders:
        if num_to_process > 0 and processed >= num_to_process:
            log_lines.append(f"[envi] reached limit={num_to_process}")
            break
        task_name = os.path.basename(folder)
        master_dir = os.path.join(folder, "master")
        slave_dir = os.path.join(folder, "slave")

        if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
            skipped += 1
            log_lines.append(f"[skip] {task_name}: master/slave dir missing")
            continue

        for side, side_dir in [("master", master_dir), ("slave", slave_dir)]:
            if not _has_sml(side_dir):
                meta_files = _find_meta_files(side_dir)
                if not meta_files:
                    log_lines.append(
                        f"[warn] {task_name}/{side}: no .sml and no .meta.xml"
                    )
                    continue
                log_lines.append(
                    f"[auto-import] {task_name}/{side}: "
                    f"importing {len(meta_files)} file(s)"
                )
                for mf in meta_files:
                    imp_start = time.time()
                    try:
                        execute_envi_task(
                            "SARsImportLuTan1",
                            {
                                "INPUT_FILE_LIST": [mf],
                                "ROOT_URI_FOR_OUTPUT": side_dir,
                            },
                        )
                        elapsed = round(time.time() - imp_start, 1)
                        auto_imported += 1
                        log_lines.append(
                            f"[auto-import ok] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s)"
                        )
                    except Exception as exc:
                        elapsed = round(time.time() - imp_start, 1)
                        log_lines.append(
                            f"[auto-import err] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s): {exc}"
                        )

        master_base = _first_sml_base(master_dir)
        slave_base = _first_sml_base(slave_dir)
        if not master_base or not slave_base:
            skipped += 1
            log_lines.append(
                f"[skip] {task_name}: still missing .sml after import "
                f"(master={'yes' if master_base else 'no'} "
                f"slave={'yes' if slave_base else 'no'})"
            )
            continue

        output_dir = os.path.join(folder, "dinsar_results")
        os.makedirs(output_dir, exist_ok=True)
        output_root = os.path.join(output_dir, "workflow")

        pair_start = time.time()
        pair_counter += 1
        log_lines.append(f"[custom] === {task_name} start ({pair_counter}/{effective_total}) ===")
        try:
            success = _run_dinsar_custom_single(
                master_base, slave_base, dem_base_file, output_root, log_lines,
                job_id=job_id,
                pair_index=pair_counter,
                total_pairs=effective_total,
                pair_name=task_name,
            )
        except Exception as exc:
            success = False
            log_lines.append(f"[custom] {task_name} crashed: {exc}")
        elapsed = round(time.time() - pair_start, 1)

        if success:
            processed += 1
            log_lines.append(f"[ok] custom dinsar {task_name} ({elapsed}s)")
        else:
            failed += 1
            log_lines.append(f"[err] custom dinsar {task_name} failed ({elapsed}s)")

        try:
            os.makedirs(RUNTIME_DIR, exist_ok=True)
            _interim_log = os.path.join(RUNTIME_DIR, "dinsar_custom_progress.log")
            with open(_interim_log, "w", encoding="utf-8") as _fp:
                _fp.write("\n".join(log_lines))
        except Exception as exc:
            print(f"[WARN] dinsar log write: {exc}")

    if failed > 0 and processed == 0 and len(task_folders) > 0:
        detail = "\n".join(log_lines[-20:])
        raise RuntimeError(
            f"All custom D-InSAR tasks failed. failed={failed}, "
            f"skipped={skipped}.\n{detail}"
        )

    return {
        "summary": {
            "task_folders": len(task_folders),
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "auto_imported": auto_imported,
        },
        "log_lines": log_lines,
    }


def inspect_dinsar(root_dir: str) -> Dict[str, Any]:
    """Pre-check D-InSAR readiness. Includes Import status detection."""
    root_dir = _to_local_path(root_dir)
    dem_base_file = DEM_BASE_FILE
    dem_ok = bool(
        dem_base_file
        and (os.path.isfile(dem_base_file) or os.path.isdir(dem_base_file))
    )

    result: Dict[str, Any] = {
        "workflow": "dinsar",
        "root_dir": root_dir,
        "exists": bool(root_dir and os.path.isdir(root_dir)),
        "ready": False,
        "summary": {},
        "warnings": [],
    }
    if not result["exists"]:
        result["warnings"].append("root_dir does not exist.")
        return result

    task_folders = _collect_task_folders(root_dir)
    ready_count = 0
    need_import_count = 0
    missing_structure = 0

    for folder in task_folders:
        master_dir = os.path.join(folder, "master")
        slave_dir = os.path.join(folder, "slave")
        if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
            missing_structure += 1
            continue
        master_has_sml = _has_sml(master_dir)
        slave_has_sml = _has_sml(slave_dir)
        if master_has_sml and slave_has_sml:
            ready_count += 1
        else:
            master_has_meta = bool(_find_meta_files(master_dir))
            slave_has_meta = bool(_find_meta_files(slave_dir))
            if (master_has_sml or master_has_meta) and (
                slave_has_sml or slave_has_meta
            ):
                need_import_count += 1
            else:
                missing_structure += 1

    result["summary"] = {
        "task_folder_count": len(task_folders),
        "ready_for_dinsar": ready_count,
        "need_import_first": need_import_count,
        "missing_structure": missing_structure,
        "dem_base_file": dem_base_file or "(not configured)",
        "dem_exists": dem_ok,
    }
    result["ready"] = (ready_count + need_import_count) > 0 and dem_ok
    if not dem_ok:
        result["warnings"].append(
            "DEM path not configured or does not exist. "
            "Set IDL_DINSAR_DEM_BASE_FILE in .env"
        )
    if len(task_folders) == 0:
        result["warnings"].append("No Task_* folders found.")
    if need_import_count > 0:
        result["warnings"].append(
            f"{need_import_count} folder(s) need Import first "
            f"(will be auto-imported during D-InSAR)."
        )
    return result
