#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

try:
    from .run_lt1_pyint_pipeline import (
        DEFAULT_DERAMP_MODE,
        DEFAULT_REFERENCE_MODE,
        DEFAULT_REFLATTEN_AZIMUTH_STEP,
        DEFAULT_REFLATTEN_COH_THRESHOLD,
        DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD,
        DEFAULT_REFLATTEN_MODEL,
        DEFAULT_REFLATTEN_RANGE_STEP,
        assert_output_sanity,
        assert_required_outputs,
        calculate_dem_oversampling,
        collect_expected_outputs,
        collect_output_sanity_checks,
        collect_stage_error_logs,
        copy_native_outputs,
        ensure_directory,
        export_standard_products,
        format_gamma_number,
        hardlink_or_copy,
        inspect_prepared_dem_path,
        load_json_file,
        load_pair_meta,
        load_shell_environment,
        normalize_date_text,
        parse_args,
        require_task_layout,
        rerun_pair_product_stages,
        run_gamma_reflatten,
        run_logged,
        safe_rmtree,
        validate_unit_interval,
        write_ifgram_list,
        write_text,
        write_wrapper_scripts,
    )
except ImportError:
    from run_lt1_pyint_pipeline import (
        DEFAULT_DERAMP_MODE,
        DEFAULT_REFERENCE_MODE,
        DEFAULT_REFLATTEN_AZIMUTH_STEP,
        DEFAULT_REFLATTEN_COH_THRESHOLD,
        DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD,
        DEFAULT_REFLATTEN_MODEL,
        DEFAULT_REFLATTEN_RANGE_STEP,
        assert_output_sanity,
        assert_required_outputs,
        calculate_dem_oversampling,
        collect_expected_outputs,
        collect_output_sanity_checks,
        collect_stage_error_logs,
        copy_native_outputs,
        ensure_directory,
        export_standard_products,
        format_gamma_number,
        hardlink_or_copy,
        inspect_prepared_dem_path,
        load_json_file,
        load_pair_meta,
        load_shell_environment,
        normalize_date_text,
        parse_args,
        require_task_layout,
        rerun_pair_product_stages,
        run_gamma_reflatten,
        run_logged,
        safe_rmtree,
        validate_unit_interval,
        write_ifgram_list,
        write_text,
        write_wrapper_scripts,
    )


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _normalize_runtime_path(value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        return Path()
    normalized = text.replace("\\", "/")
    if normalized.startswith("/mnt/") or normalized.startswith("/"):
        return Path(normalized).resolve()
    if _WINDOWS_DRIVE_RE.match(text):
        drive = text[0].lower()
        tail = text[2:].replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{drive}/{tail}").resolve()
    return Path(normalized).resolve()


def _build_s1_template_text(
    *,
    project_name: str,
    satellite: str,
    master_date: str,
    range_looks: int,
    azimuth_looks: int,
    target_grid_size_m: int,
    dem_lat_ovr: float,
    dem_lon_ovr: float,
    unwrap_coh_threshold: float,
    geo_interp: str,
    atmcor: bool,
    atmcor_use_for_disp: bool,
    reflatten: bool,
    reflatten_model: str,
    reflatten_coh_threshold: float,
    parallel_workers: int,
    unwrap: bool,
    geocode: bool,
    dem_mode: str,
    fabdem_root: str,
    prepared_dem_path: str,
    opentopo_dem_type: str,
    opentopo_api_key: str,
) -> str:
    prepared_dem = inspect_prepared_dem_path(prepared_dem_path) if dem_mode == "prepared_file" else {}
    lines = [
        f"# Auto-generated for {project_name}",
        f"satelite={satellite}",
        f"masterDate={master_date}",
        f"range_looks={int(range_looks)}",
        f"azimuth_looks={int(azimuth_looks)}",
        f"target_grid_size_m={int(target_grid_size_m or 0)}",
        f"dem_lat_ovr={format_gamma_number(dem_lat_ovr)}",
        f"dem_lon_ovr={format_gamma_number(dem_lon_ovr)}",
        "download_data=0",
        "raw2slc_all=1",
        f"raw2slc_all_parallel={int(parallel_workers)}",
        "extract_burst_all=1",
        f"extract_all_parallel={int(parallel_workers)}",
        "coreg_all=1",
        f"coreg_all_parallel={int(parallel_workers)}",
        "select_pairs=0",
        "diff_all=1",
        f"diff_all_parallel={int(parallel_workers)}",
        "pot_all=0",
        f"pot_all_parallel={int(parallel_workers)}",
        f"unwrap_all={1 if unwrap else 0}",
        f"unwrap_all_parallel={int(parallel_workers)}",
        f"unwrapThreshold={format_gamma_number(unwrap_coh_threshold)}",
        "make_mask=1",
        "auto_unw=1",
        "r_refer=-",
        "a_refer=-",
        f"atmcor_all={1 if atmcor else 0}",
        f"atmcor_all_parallel={int(parallel_workers)}",
        f"atmcor_use_for_disp={1 if (atmcor and atmcor_use_for_disp) else 0}",
        f"reflatten={1 if reflatten else 0}",
        f"reflatten_model={str(reflatten_model or DEFAULT_REFLATTEN_MODEL).strip().lower()}",
        f"reflatten_coh_threshold={format_gamma_number(reflatten_coh_threshold)}",
        f"geocode_all={1 if geocode else 0}",
        f"geocode_all_parallel={int(parallel_workers)}",
        f"geo_interp={str(geo_interp or '0').strip()}",
        "gacos_correction=0",
        "load_data=0",
        "geocode_products=hyp3,licsbas",
        "start_swath=1",
        "end_swath=3",
        "start_burst=1",
        "end_burst=20",
    ]
    if dem_mode == "local_fabdem" and fabdem_root:
        lines.append(f"fabdem_dir={fabdem_root}")
    else:
        lines.append("fabdem_dir=-")
    if dem_mode == "prepared_file" and prepared_dem.get("kind") == "gamma_ready":
        lines.append(f"DEM={prepared_dem['direct_dem_path']}")
    if dem_mode == "prepared_file" and prepared_dem.get("kind") == "source_dem":
        lines.append(f"prepared_dem_source={prepared_dem['source_dem_path']}")
    else:
        lines.append("prepared_dem_source=-")
    if dem_mode == "opentopo":
        lines.append(f"opentopo_dem_type={opentopo_dem_type or 'SRTMGL1'}")
        lines.append(f"opentopo_api_key={opentopo_api_key or '-'}")
    else:
        lines.append("opentopo_dem_type=-")
        lines.append("opentopo_api_key=-")
    return "\n".join(lines) + "\n"


def _resolve_s1_inputs(input_assets_payload: Dict[str, Any]) -> Dict[str, Any]:
    task_source = input_assets_payload.get("task_source") or {}
    prod = task_source.get("production_inputs") or {}
    orbits = input_assets_payload.get("orbits") or {}
    master_scene = _normalize_runtime_path(
        ((prod.get("master_scene") or {}).get("staged_path") or (prod.get("master_scene") or {}).get("path") or (prod.get("master_zip") or {}).get("staged_path") or (prod.get("master_zip") or {}).get("path") or "")
    )
    slave_scene = _normalize_runtime_path(
        ((prod.get("slave_scene") or {}).get("staged_path") or (prod.get("slave_scene") or {}).get("path") or (prod.get("slave_zip") or {}).get("staged_path") or (prod.get("slave_zip") or {}).get("path") or "")
    )
    master_eof = _normalize_runtime_path(
        ((orbits.get("master") or {}).get("staged_path") or (orbits.get("master") or {}).get("path") or "")
    )
    slave_eof = _normalize_runtime_path(
        ((orbits.get("slave") or {}).get("staged_path") or (orbits.get("slave") or {}).get("path") or "")
    )
    return {
        "master_scene": master_scene,
        "slave_scene": slave_scene,
        "master_eof": master_eof,
        "slave_eof": slave_eof,
    }


def main() -> int:
    args = parse_args()

    task_dir = Path(args.task_dir).resolve()
    project_dir = Path(args.project_dir).resolve()
    run_root = project_dir.parent
    template_root = Path(args.template_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    pyint_home = Path(args.pyint_home).resolve()
    pyint_app_script = Path(args.pyint_app_script).resolve()
    dem_root = Path(args.dem_root).resolve()
    input_assets_dir = Path(args.input_assets_dir).resolve() if args.input_assets_dir else None
    input_assets_json = Path(args.input_assets_json).resolve() if args.input_assets_json else None
    input_assets_payload = load_json_file(input_assets_json)
    dem_mode = str(args.dem_mode or "local_fabdem").strip().lower() or "local_fabdem"
    prepared_dem_info = inspect_prepared_dem_path(args.prepared_dem_path) if dem_mode == "prepared_file" else {}
    dem_oversampling = calculate_dem_oversampling(
        dem_resolution_m=float(args.dem_resolution_m),
        target_grid_size_m=float(args.target_grid_size_m or 0),
        dem_lat_ovr=float(args.dem_lat_ovr or 0.0),
        dem_lon_ovr=float(args.dem_lon_ovr or 0.0),
    )
    unwrap_coh_threshold = validate_unit_interval(args.unwrap_coh_threshold, "--unwrap-coh-threshold")
    coherence_mask_threshold = validate_unit_interval(args.coherence_mask_threshold, "--coherence-mask-threshold")
    reference_mode = str(args.reference_mode or DEFAULT_REFERENCE_MODE).strip().lower() or DEFAULT_REFERENCE_MODE
    deramp_mode = str(args.deramp_mode or DEFAULT_DERAMP_MODE).strip().lower() or DEFAULT_DERAMP_MODE
    reference_coh_threshold = validate_unit_interval(args.reference_coh_threshold, "--reference-coh-threshold")
    deramp_coh_threshold = validate_unit_interval(args.deramp_coh_threshold, "--deramp-coh-threshold")
    geo_interp = str(args.geo_interp or "1").strip()
    if geo_interp not in {"0", "1"}:
        raise ValueError("--geo-interp must be 0 or 1.")
    gamma_nodata_value = float(args.gamma_nodata_value)
    if not math.isfinite(gamma_nodata_value):
        raise ValueError("--gamma-nodata-value must be a finite number.")
    reflatten_model = str(args.reflatten_model or DEFAULT_REFLATTEN_MODEL).strip().lower()
    if reflatten_model == "linear":
        reflatten_model = "plane"
    if reflatten_model not in {"plane", "quadratic"}:
        raise ValueError("--reflatten-model must be plane or quadratic.")
    reflatten_coh_threshold = validate_unit_interval(args.reflatten_coh_threshold, "--reflatten-coh-threshold")
    reflatten_fallback_coh_threshold = validate_unit_interval(
        args.reflatten_fallback_coh_threshold,
        "--reflatten-fallback-coh-threshold",
    )
    reflatten_range_step = max(1, int(args.reflatten_range_step or DEFAULT_REFLATTEN_RANGE_STEP))
    reflatten_azimuth_step = max(1, int(args.reflatten_azimuth_step or DEFAULT_REFLATTEN_AZIMUTH_STEP))

    require_task_layout(task_dir)
    if not pyint_app_script.is_file():
        raise FileNotFoundError(f"pyintApp.py not found: {pyint_app_script}")
    if not input_assets_json or not input_assets_json.is_file():
        raise RuntimeError("Sentinel-1 PyINT runner requires --input-assets-json.")
    if dem_mode == "prepared_file" and not prepared_dem_info.get("kind"):
        raise RuntimeError(
            "Prepared DEM mode requires either a Gamma DEM with .par, "
            "or a source DEM with .xml/.hdr/.vrt sidecars."
        )

    if args.force:
        safe_rmtree(run_root)
        safe_rmtree(template_root)
        safe_rmtree(output_dir)

    if run_root.exists():
        raise RuntimeError(f"PyINT run root already exists, rerun with --force: {run_root}")

    pair_meta = load_pair_meta(task_dir)
    s1_inputs = _resolve_s1_inputs(input_assets_payload)
    master_scene = s1_inputs["master_scene"]
    slave_scene = s1_inputs["slave_scene"]
    master_eof = s1_inputs["master_eof"]
    slave_eof = s1_inputs["slave_eof"]
    if not (master_scene.is_file() or master_scene.is_dir()):
        raise FileNotFoundError(f"Sentinel-1 master scene not found: {master_scene}")
    if not (slave_scene.is_file() or slave_scene.is_dir()):
        raise FileNotFoundError(f"Sentinel-1 slave scene not found: {slave_scene}")
    if not master_eof.is_file():
        raise FileNotFoundError(f"Sentinel-1 master EOF not found: {master_eof}")
    if not slave_eof.is_file():
        raise FileNotFoundError(f"Sentinel-1 slave EOF not found: {slave_eof}")

    master_date = normalize_date_text(args.master_date) or normalize_date_text(pair_meta.get("master_imaging_date"))
    slave_date = normalize_date_text(args.slave_date) or normalize_date_text(pair_meta.get("slave_imaging_date"))
    if not master_date or not slave_date:
        raise RuntimeError("Unable to determine master/slave dates from pair metadata.")

    pair_name = f"{master_date}-{slave_date}"
    task_alias = str(args.task_alias or pair_meta.get("task_alias") or task_dir.name).strip() or task_dir.name
    pair_key = str(args.pair_key or pair_meta.get("pair_key") or "").strip()
    time_baseline_days = int(args.time_baseline_days or pair_meta.get("time_baseline_days") or 0)
    satellite = str(pair_meta.get("master_satellite") or "S1A").strip().upper() or "S1A"

    ensure_directory(run_root)
    ensure_directory(template_root)
    ensure_directory(output_dir)
    ensure_directory(dem_root)

    pyint_scripts_dir = pyint_home / "pyint"
    wrappers_dir = ensure_directory(run_root / "wrappers")
    write_wrapper_scripts(
        wrappers_dir=wrappers_dir,
        pyint_home=pyint_home,
        python_cmd=args.python,
        gamma_env_script=args.gamma_env_script,
    )
    for shim_name in ("python", "python3"):
        shim_path = wrappers_dir / shim_name
        write_text(
            shim_path,
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    f"exec '{args.python}' \"$@\"",
                    "",
                ]
            ),
        )
        shim_path.chmod(shim_path.stat().st_mode | 0o111)

    template_path = write_text(
        template_root / f"{args.project_name}.template",
        _build_s1_template_text(
            project_name=args.project_name,
            satellite=satellite,
            master_date=master_date,
            range_looks=args.range_looks,
            azimuth_looks=args.azimuth_looks,
            target_grid_size_m=int(args.target_grid_size_m or 0),
            dem_lat_ovr=dem_oversampling["dem_lat_ovr"],
            dem_lon_ovr=dem_oversampling["dem_lon_ovr"],
            unwrap_coh_threshold=unwrap_coh_threshold,
            geo_interp=geo_interp,
            atmcor=bool(args.atmcor),
            atmcor_use_for_disp=bool(args.atmcor_use_for_disp),
            reflatten=bool(args.reflatten),
            reflatten_model=reflatten_model,
            reflatten_coh_threshold=reflatten_coh_threshold,
            parallel_workers=args.parallel_workers,
            unwrap=bool(args.unwrap),
            geocode=bool(args.geocode),
            dem_mode=dem_mode,
            fabdem_root=str(args.fabdem_root or "").strip(),
            prepared_dem_path=str(args.prepared_dem_path or "").strip(),
            opentopo_dem_type=str(args.opentopo_dem_type or "SRTMGL1").strip(),
            opentopo_api_key=str(args.opentopo_api_key or "").strip(),
        ),
    )

    scratch_root = ensure_directory(project_dir.parent)
    archive_materialization: List[Dict[str, str]] = []
    env = os.environ.copy()
    if args.gamma_env_script:
        env.update(load_shell_environment(args.gamma_env_script, env))
    opod_dir = project_dir / "OPOD"
    env.update(
        {
            "SCRATCHDIR": str(scratch_root),
            "TEMPLATEDIR": str(template_root),
            "DEMDIR": str(dem_root),
            "OPOD_DIR": str(opod_dir),
            "PATH": f"{wrappers_dir}:{pyint_scripts_dir}:{env.get('PATH', '')}",
            "PYTHONPATH": f"{pyint_home}:{env.get('PYTHONPATH', '')}",
            "PYINT_LT1_PRECISE_ORBIT_ENABLED": "false",
        }
    )

    generate_stdout = run_root / "pyint_generate.stdout.log"
    generate_stderr = run_root / "pyint_generate.stderr.log"
    generate_result = run_logged(
        [str(wrappers_dir / "pyintApp.py"), "-g", args.project_name],
        env=env,
        cwd=scratch_root,
        stdout_path=generate_stdout,
        stderr_path=generate_stderr,
    )
    if generate_result.returncode != 0:
        raise RuntimeError(
            f"pyintApp.py -g failed with rc={generate_result.returncode}: "
            f"{(generate_result.stderr or generate_result.stdout or '').strip()}"
        )

    pyint_project_dir = project_dir
    ensure_directory(pyint_project_dir)
    opod_dir = ensure_directory(opod_dir)
    download_dir = ensure_directory(pyint_project_dir / "DOWNLOAD")
    ifgram_list_path = write_ifgram_list(pyint_project_dir / "ifgram_list.txt", master_date, slave_date, time_baseline_days)
    for role, src_path in (("master", master_scene), ("slave", slave_scene)):
        target_path = download_dir / src_path.name
        if src_path.is_dir():
            if target_path.exists():
                if target_path.is_dir():
                    safe_rmtree(target_path)
                else:
                    target_path.unlink()
            shutil.copytree(src_path, target_path)
            op = "copied_dir"
        else:
            op = hardlink_or_copy(src_path, target_path)
        archive_materialization.append(
            {
                "role": role,
                "source": str(src_path),
                "target": str(target_path),
                "operation": op,
            }
        )
    for role, src_path in (("master_orbit", master_eof), ("slave_orbit", slave_eof)):
        target_path = opod_dir / src_path.name
        op = hardlink_or_copy(src_path, target_path)
        archive_materialization.append(
            {
                "role": role,
                "source": str(src_path),
                "target": str(target_path),
                "operation": op,
            }
        )

    run_stdout = run_root / "pyint.stdout.log"
    run_stderr = run_root / "pyint.stderr.log"
    run_started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    run_result = run_logged(
        [str(wrappers_dir / "pyintApp.py"), args.project_name],
        env=env,
        cwd=scratch_root,
        stdout_path=run_stdout,
        stderr_path=run_stderr,
    )
    if run_result.returncode != 0:
        stage_error_logs = collect_stage_error_logs(pyint_project_dir)
        detail_text = (run_result.stderr or run_result.stdout or "").strip()
        if stage_error_logs:
            log_text = ", ".join(f"{name}={path}" for name, path in stage_error_logs.items())
            detail_text = f"{detail_text}\nStage logs: {log_text}" if detail_text else f"Stage logs: {log_text}"
        raise RuntimeError(f"pyintApp.py failed with rc={run_result.returncode}: {detail_text}")

    repair_summary: Dict[str, Any] = {
        "attempted": False,
        "attempt_count": 0,
        "max_attempts": 1,
    }
    outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
    try:
        assert_required_outputs(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        output_sanity_checks = collect_output_sanity_checks(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        assert_output_sanity(output_sanity_checks)
    except RuntimeError as exc:
        repair_summary = rerun_pair_product_stages(
            project_name=args.project_name,
            project_dir=pyint_project_dir,
            run_root=run_root,
            scratch_root=scratch_root,
            env=env,
            pair_name=pair_name,
            master_date=master_date,
            slave_date=slave_date,
            range_looks=args.range_looks,
            unwrap=bool(args.unwrap),
            atmcor=bool(args.atmcor),
            geocode=bool(args.geocode),
        )
        outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
        assert_required_outputs(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        output_sanity_checks = collect_output_sanity_checks(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        assert_output_sanity(output_sanity_checks)

    stage_error_logs = collect_stage_error_logs(pyint_project_dir)
    reflatten_summary: Dict[str, Any] = {
        "enabled": bool(args.reflatten),
        "applied": False,
        "reason": "",
        "model": reflatten_model,
        "coherence_threshold": reflatten_coh_threshold,
        "fallback_coherence_threshold": reflatten_fallback_coh_threshold,
        "range_step": reflatten_range_step,
        "azimuth_step": reflatten_azimuth_step,
    }
    if bool(args.reflatten) and bool(args.unwrap):
        reflatten_summary = run_gamma_reflatten(
            project_dir=pyint_project_dir,
            run_root=run_root,
            output_dir=output_dir,
            outputs=outputs,
            pair_name=pair_name,
            master_date=master_date,
            range_looks=args.range_looks,
            env=env,
            model=reflatten_model,
            coherence_threshold=reflatten_coh_threshold,
            fallback_coherence_threshold=reflatten_fallback_coh_threshold,
            range_step=reflatten_range_step,
            azimuth_step=reflatten_azimuth_step,
            geo_interp=geo_interp,
        )
        outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
        output_sanity_checks = collect_output_sanity_checks(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        assert_output_sanity(output_sanity_checks)
    elif bool(args.reflatten):
        reflatten_summary["reason"] = "unwrap disabled"
    else:
        reflatten_summary["reason"] = "disabled"

    copied_paths = copy_native_outputs(
        project_dir=pyint_project_dir,
        output_dir=output_dir,
        pair_name=pair_name,
        template_path=template_path,
        ifgram_list_path=ifgram_list_path,
        stdout_path=run_stdout,
        stderr_path=run_stderr,
    )
    standard_products = (
        export_standard_products(
            project_dir=pyint_project_dir,
            output_dir=output_dir,
            pair_name=pair_name,
            master_date=master_date,
            range_looks=args.range_looks,
            azimuth_looks=args.azimuth_looks,
            target_grid_size_m=int(args.target_grid_size_m or 0),
            coherence_mask_threshold=coherence_mask_threshold,
            reference_mode=reference_mode,
            reference_coh_threshold=reference_coh_threshold,
            deramp_mode=deramp_mode,
            deramp_coh_threshold=deramp_coh_threshold,
            atmcor_enabled=bool(args.atmcor),
            atmcor_use_for_disp=bool(args.atmcor_use_for_disp),
            reflatten_summary=reflatten_summary,
            gamma_nodata_value=gamma_nodata_value,
            outputs=outputs,
            env=env,
            run_root=run_root,
        )
        if bool(args.geocode)
        else {"enabled": False, "reason": "geocode disabled"}
    )

    summary = {
        "ok": True,
        "task_dir": str(task_dir),
        "task_alias": task_alias,
        "pair_key": pair_key,
        "project_name": args.project_name,
        "project_dir": str(pyint_project_dir),
        "run_root": str(run_root),
        "template_root": str(template_root),
        "output_dir": str(output_dir),
        "pyint_home": str(pyint_home),
        "pyint_app_script": str(pyint_app_script),
        "gamma_env_script": args.gamma_env_script,
        "dem": {
            "mode": dem_mode,
            "dem_root": str(dem_root),
            "fabdem_root": str(args.fabdem_root or "").strip(),
            "prepared_dem_path": str(args.prepared_dem_path or "").strip(),
            "prepared_dem_kind": str(prepared_dem_info.get("kind") or ""),
            "configured_resolution_m": float(args.dem_resolution_m),
            "oversampling": dem_oversampling,
            "opentopo_dem_type": str(args.opentopo_dem_type or "SRTMGL1").strip(),
            "opentopo_api_key_configured": bool(str(args.opentopo_api_key or "").strip()),
        },
        "orbit_policy": "require_eof",
        "precise_orbit_bridge": {"enabled": False, "mode": "not_applicable"},
        "input_assets_dir": str(input_assets_dir) if input_assets_dir else "",
        "input_assets_json": str(input_assets_json) if input_assets_json else "",
        "input_assets": input_assets_payload,
        "master_date": master_date,
        "slave_date": slave_date,
        "pair_name": pair_name,
        "time_baseline_days": time_baseline_days,
        "target_grid_size_m": int(args.target_grid_size_m or 0),
        "range_looks": int(args.range_looks),
        "azimuth_looks": int(args.azimuth_looks),
        "dem_resolution_m": float(args.dem_resolution_m),
        "dem_oversampling": dem_oversampling,
        "unwrap_coh_threshold": unwrap_coh_threshold,
        "coherence_quality_threshold": coherence_mask_threshold,
        "reference_mode": reference_mode,
        "reference_coh_threshold": reference_coh_threshold,
        "deramp_mode": deramp_mode,
        "deramp_coh_threshold": deramp_coh_threshold,
        "gamma_nodata_value": gamma_nodata_value,
        "geo_interp": geo_interp,
        "atmcor": bool(args.atmcor),
        "atmcor_use_for_disp": bool(args.atmcor_use_for_disp),
        "reflatten": bool(args.reflatten),
        "reflatten_model": reflatten_model,
        "reflatten_coh_threshold": reflatten_coh_threshold,
        "reflatten_fallback_coh_threshold": reflatten_fallback_coh_threshold,
        "reflatten_range_step": reflatten_range_step,
        "reflatten_azimuth_step": reflatten_azimuth_step,
        "parallel_workers": int(args.parallel_workers),
        "unwrap": bool(args.unwrap),
        "geocode": bool(args.geocode),
        "archives": {
            "master": [str(master_scene)],
            "slave": [str(slave_scene)],
        },
        "orbit_files": {
            "master": str(master_eof),
            "slave": str(slave_eof),
        },
        "archive_materialization": archive_materialization,
        "workspace_outputs": outputs,
        "output_sanity_checks": output_sanity_checks,
        "output_repair": repair_summary,
        "reflatten_summary": reflatten_summary,
        "copied_outputs": copied_paths,
        "standard_products": standard_products,
        "logs": {
            "generate_stdout": str(generate_stdout),
            "generate_stderr": str(generate_stderr),
            "run_stdout": str(run_stdout),
            "run_stderr": str(run_stderr),
            "stage_error_logs": stage_error_logs,
        },
        "started_at": run_started_at,
        "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    summary_path = output_dir / "pyint_run_summary.json"
    write_text(summary_path, json.dumps(summary, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
