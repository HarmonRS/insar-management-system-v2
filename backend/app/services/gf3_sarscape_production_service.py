"""Run GF3 SARscape production and clean native intermediate files."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import settings, split_env_paths
from .gf3_native_inventory_service import (
    NATIVE_MANIFEST_NAME,
    POLARIZATION_PRIORITY,
    scan_gf3_sarscape_native_roots,
)
from .gf3_standardize_service import STANDARD_MANIFEST_NAME

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, str], None]

SUPPORTED_WRAPPER_INPUT_EXTS = (".tar.gz", ".tgz", ".meta.xml")
CLEANUP_MANIFEST_NAME = "gf3_cleanup_manifest.json"
INTERMEDIATE_DIR_NAMES = {
    ".gf3_extract",
    ".gf3_extract.tmp",
    "temp",
    "tmp",
    "work",
}
KEEP_FILE_NAMES = {
    NATIVE_MANIFEST_NAME,
    CLEANUP_MANIFEST_NAME,
    "gf3_sarscape_cli.log",
}
INTERMEDIATE_SUFFIXES = (
    ".par",
    ".par_command",
    ".trace",
    ".working",
    ".working_warning",
    ".workinggetcornerfromslantrangeimage_dem",
    ".txt",
    ".list",
    ".listhv",
    ".listunknown",
    ".shp",
    ".shx",
    ".dbf",
    ".prj",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp_path, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _safe_slug(value: Any, *, default: str = "unknown") -> str:
    text = _clean_text(value) or default
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text).strip("._-")
    return safe or default


def _resolve_existing_dirs(values: list[str] | tuple[str, ...] | None) -> tuple[list[Path], list[str]]:
    roots: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = _clean_text(raw)
        if not text:
            continue
        path = Path(os.path.normpath(text)).resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            roots.append(path)
        else:
            missing.append(str(path))
    return roots, missing


def _resolve_config_path(raw: Any) -> Path | None:
    text = _clean_text(raw)
    if not text:
        return None
    return Path(os.path.normpath(text)).resolve()


def _fallback_wrapper_exe() -> Path | None:
    candidate = (
        Path(settings.PROJECT_ROOT)
        / "third_party"
        / "GF3_L1A_To_L2_pipeline"
        / "dist"
        / "windows"
        / "gf3wrapper.exe"
    )
    return candidate.resolve() if candidate.is_file() else None


def _wrapper_exe_path(value: str | None = None) -> Path:
    configured = _resolve_config_path(value or settings.GF3_SARSCAPE_WRAPPER_EXE)
    path = configured or _fallback_wrapper_exe()
    if path is None:
        raise FileNotFoundError("GF3 SARscape wrapper is not configured.")
    if not path.is_file():
        raise FileNotFoundError(f"GF3 SARscape wrapper does not exist: {path}")
    return path


def _dem_path(value: str | None = None) -> Path:
    path = _resolve_config_path(value or settings.GF3_SARSCAPE_DEM_PATH or settings.GF3_GEO_DEM_PATH)
    if path is None:
        raise FileNotFoundError("GF3 SARscape DEM path is not configured.")
    if not path.exists():
        raise FileNotFoundError(f"GF3 SARscape DEM path does not exist: {path}")
    return path


def _idlrt_path(value: str | None = None) -> Path | None:
    path = _resolve_config_path(value or settings.GF3_SARSCAPE_IDLRT_PATH)
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(f"GF3 SARscape idlrt.exe does not exist: {path}")
    return path


def _requested_polarizations(value: str | None = None) -> list[str]:
    raw = _clean_text(value or settings.GF3_SARSCAPE_POLARIZATIONS or "HH,HV")
    items: list[str] = []
    for token in re.split(r"[,;\s]+", raw):
        pol = token.strip().upper()
        if not pol:
            continue
        if pol not in items:
            items.append(pol)
    return items or ["HH", "HV"]


def _archive_exts_for_wrapper(archive_exts: list[str] | tuple[str, ...] | None) -> list[str]:
    ordered: list[str] = []
    for raw_ext in archive_exts or []:
        ext = _clean_text(raw_ext).lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if ext in SUPPORTED_WRAPPER_INPUT_EXTS and ext not in ordered:
            ordered.append(ext)
    if not ordered:
        ordered.extend((".tar.gz", ".tgz"))
    return sorted(ordered, key=len, reverse=True)


def _input_ext(path: Path, exts: list[str]) -> str | None:
    name = path.name.lower()
    for ext in exts:
        if name.endswith(ext):
            return ext
    return None


def _scene_name_from_input(path: Path) -> str:
    name = path.name
    lower_name = name.lower()
    for ext in SUPPORTED_WRAPPER_INPUT_EXTS:
        if lower_name.endswith(ext):
            return name[: -len(ext)]
    return path.stem


def discover_gf3_sarscape_inputs(
    source_dirs: list[str] | tuple[str, ...] | None,
    *,
    archive_exts: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Find wrapper-supported GF3 source inputs in configured archive pools."""
    roots, missing_roots = _resolve_existing_dirs(source_dirs)
    exts = _archive_exts_for_wrapper(archive_exts)
    inputs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            ext = _input_ext(path, exts)
            if not ext:
                continue
            resolved = path.resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            inputs.append(
                {
                    "path": str(resolved),
                    "scene_name": _scene_name_from_input(path),
                    "ext": ext,
                    "source_root": str(root),
                }
            )

    inputs.sort(key=lambda item: str(item.get("path") or "").lower())
    return {
        "source_roots": [str(path) for path in roots],
        "missing_roots": missing_roots,
        "archive_exts": exts,
        "input_count": len(inputs),
        "inputs": inputs,
    }


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _completed_geo_product(scene_dir: Path, polarization: str) -> Path | None:
    lower_pol = polarization.lower()
    try:
        entries = list(scene_dir.iterdir())
    except OSError:
        return None

    for path in entries:
        name = path.name.lower()
        if not name.endswith(f"_{lower_pol}_geo.sml"):
            continue
        data_file = Path(str(path)[: -len(".sml")])
        if _is_nonempty_file(path) and _is_nonempty_file(data_file):
            return data_file
    return None


def _scene_complete(scene_dir: Path, polarizations: list[str]) -> bool:
    if not scene_dir.is_dir():
        return False
    return all(_completed_geo_product(scene_dir, pol) is not None for pol in polarizations)


def _missing_geo_polarizations(scene_dir: Path, polarizations: list[str]) -> list[str]:
    if not scene_dir.is_dir():
        return list(polarizations)
    return [pol for pol in polarizations if _completed_geo_product(scene_dir, pol) is None]


def _compact_failure_text(text: str, *, max_chars: int = 1000) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = " | ".join(lines)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _read_failure_file(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _scene_failure_hint(scene_dir: Path) -> str | None:
    work_dir = scene_dir / "work"
    candidates = [
        work_dir / "Process.working_error",
        work_dir / "Process.trace_cerr.txt",
        scene_dir / "gf3_sarscape_cli.log",
    ]
    for path in candidates:
        text = _read_failure_file(path)
        if text:
            compact = _compact_failure_text(text)
            if compact:
                return f"{path.name}: {compact}"

    process_log = work_dir / "Process.log"
    text = _read_failure_file(process_log)
    if not text:
        return None
    important = [
        line.strip()
        for line in text.splitlines()
        if "ERROR" in line.upper() or "[EC:" in line.upper() or "PARAMETER FILE READ ERROR" in line.upper()
    ]
    if important:
        return f"{process_log.name}: {_compact_failure_text(chr(10).join(important[-8:]))}"
    return None


def _format_missing_output_error(scene_dir: Path, polarizations: list[str], returncode: int) -> str:
    missing = _missing_geo_polarizations(scene_dir, polarizations)
    missing_text = ",".join(missing) if missing else "unknown"
    if returncode == 0:
        base = f"wrapper returned 0 but required _geo outputs are missing: {missing_text}"
    else:
        base = f"wrapper return code {returncode}; missing _geo outputs: {missing_text}"
    hint = _scene_failure_hint(scene_dir)
    return f"{base}; {hint}" if hint else base


def _emit_log(callback: LogCallback | None, level: str, message: str) -> None:
    if callback:
        callback(level, message)


def _emit_progress(callback: ProgressCallback | None, progress: int, message: str) -> None:
    if callback:
        callback(max(0, min(100, int(progress))), message)


def _run_wrapper_command(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds if timeout_seconds and timeout_seconds > 0 else None,
        check=False,
    )


def _log_completed_process_output(
    completed: subprocess.CompletedProcess[str],
    *,
    log_callback: LogCallback | None,
    line_limit: int = 300,
) -> list[str]:
    output = completed.stdout or ""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return []

    clipped = False
    display_lines = lines
    if len(lines) > line_limit:
        clipped = True
        head_count = max(1, line_limit // 2)
        tail_count = max(1, line_limit - head_count)
        display_lines = lines[:head_count] + [f"... clipped {len(lines) - line_limit} wrapper log lines ..."] + lines[-tail_count:]

    for line in display_lines:
        _emit_log(log_callback, "INFO", f"[gf3wrapper] {line}")
    if clipped:
        _emit_log(log_callback, "WARNING", f"GF3 wrapper output was clipped to {line_limit} log lines.")
    return lines[-20:]


def run_gf3_sarscape_production(
    *,
    source_dirs: list[str] | None = None,
    native_root: str | None = None,
    wrapper_exe: str | None = None,
    dem_path: str | None = None,
    idlrt_path: str | None = None,
    polarizations: str | None = None,
    archive_exts: list[str] | None = None,
    max_archives_per_run: int | None = None,
    timeout_seconds: int | None = None,
    keep_extracted: bool | None = None,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the external GF3 SARscape wrapper for pending raw archives."""
    source_dirs = source_dirs if source_dirs is not None else split_env_paths(settings.GF3_ARCHIVE_SOURCE_DIRS)
    native_roots = split_env_paths(settings.GF3_SARSCAPE_NATIVE_DIRS)
    native_root_text = _clean_text(native_root or (native_roots[0] if native_roots else ""))
    if not source_dirs:
        raise ValueError("GF3_ARCHIVE_SOURCE_DIRS is not configured.")
    if not native_root_text:
        raise ValueError("GF3_SARSCAPE_NATIVE_DIRS is not configured.")
    native_root_path = Path(os.path.normpath(native_root_text)).resolve()

    native_root_path.mkdir(parents=True, exist_ok=True)
    wrapper_path = _wrapper_exe_path(wrapper_exe)
    dem = _dem_path(dem_path)
    idlrt = _idlrt_path(idlrt_path)
    pols = _requested_polarizations(polarizations)
    pol_text = ",".join(pols)
    ext_config = archive_exts if archive_exts is not None else split_env_paths(settings.GF3_ARCHIVE_EXTS)
    discovery = discover_gf3_sarscape_inputs(source_dirs, archive_exts=ext_config)
    inputs = discovery.get("inputs") or []
    max_to_process = int(max_archives_per_run or 0)
    timeout = int(timeout_seconds or 0)
    keep = bool(settings.GF3_SARSCAPE_KEEP_EXTRACTED if keep_extracted is None else keep_extracted)

    _emit_log(log_callback, "INFO", f"GF3 SARscape source roots: {source_dirs}")
    _emit_log(log_callback, "INFO", f"GF3 SARscape native root: {native_root_path}")
    _emit_log(log_callback, "INFO", f"GF3 SARscape wrapper: {wrapper_path}")
    _emit_log(log_callback, "INFO", f"GF3 SARscape DEM: {dem}")
    _emit_log(log_callback, "INFO", f"GF3 SARscape polarizations: {pol_text}")

    if not inputs:
        _emit_progress(progress_callback, 100, "GF3 SARscape production found no supported inputs.")
        return {
            "ok": True,
            "found_count": 0,
            "processed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "deferred_count": 0,
            "missing_roots": discovery.get("missing_roots") or [],
            "results": [],
        }

    configured_runtime_dir = _clean_text(getattr(settings, "GF3_SARSCAPE_RUNTIME_DIR", ""))
    runtime_dir = (
        Path(os.path.normpath(configured_runtime_dir)).resolve()
        if configured_runtime_dir
        else native_root_path / ".gf3_runtime"
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "gf3wrapper.json"
    env = os.environ.copy()
    if idlrt is not None:
        env["IDLRT_PATH"] = str(idlrt)

    processed = 0
    skipped = 0
    failed = 0
    deferred = 0
    results: list[dict[str, Any]] = []
    total = len(inputs)

    for idx, input_info in enumerate(inputs):
        input_path = Path(str(input_info.get("path") or "")).resolve()
        scene_name = str(input_info.get("scene_name") or _scene_name_from_input(input_path))
        scene_dir = native_root_path / scene_name
        progress = 5 + int((idx / max(total, 1)) * 60)
        _emit_progress(progress_callback, progress, f"GF3 SARscape checking {idx + 1}/{total}: {scene_name}")

        if _scene_complete(scene_dir, pols):
            skipped += 1
            results.append(
                {
                    "scene_name": scene_name,
                    "input_path": str(input_path),
                    "scene_dir": str(scene_dir),
                    "status": "skipped_complete",
                }
            )
            continue

        if max_to_process > 0 and processed + failed >= max_to_process:
            deferred += 1
            results.append(
                {
                    "scene_name": scene_name,
                    "input_path": str(input_path),
                    "scene_dir": str(scene_dir),
                    "status": "deferred",
                }
            )
            continue

        cmd = [
            str(wrapper_path),
            "-config",
            str(config_path),
            "-input",
            str(input_path),
            "-output",
            str(native_root_path),
            "-dem",
            str(dem),
            "-pol",
            pol_text,
            f"-keep-extracted={str(keep).lower()}",
        ]
        if idlrt is not None:
            cmd.extend(["-idlrt", str(idlrt)])

        _emit_log(log_callback, "INFO", f"GF3 SARscape processing {scene_name}: {input_path}")
        started = time.monotonic()
        try:
            completed = _run_wrapper_command(
                cmd,
                cwd=wrapper_path.parent,
                env=env,
                timeout_seconds=timeout,
            )
            output_tail = _log_completed_process_output(completed, log_callback=log_callback)
            elapsed_seconds = round(time.monotonic() - started, 3)
            output_complete = _scene_complete(scene_dir, pols)
            if completed.returncode == 0 and output_complete:
                processed += 1
                status = "processed"
                error = None
            elif output_complete:
                processed += 1
                status = "processed_with_warning"
                error = f"wrapper return code {completed.returncode}"
                _emit_log(log_callback, "WARNING", f"GF3 wrapper returned {completed.returncode}, but output is complete: {scene_name}")
            else:
                failed += 1
                status = "failed"
                error = _format_missing_output_error(scene_dir, pols, int(completed.returncode))
                _emit_log(log_callback, "ERROR", f"GF3 SARscape failed for {scene_name}: {error}")

            results.append(
                {
                    "scene_name": scene_name,
                    "input_path": str(input_path),
                    "scene_dir": str(scene_dir),
                    "status": status,
                    "returncode": int(completed.returncode),
                    "elapsed_seconds": elapsed_seconds,
                    "output_complete": output_complete,
                    "error": error,
                    "output_tail": output_tail,
                }
            )
        except subprocess.TimeoutExpired as exc:
            failed += 1
            _emit_log(log_callback, "ERROR", f"GF3 SARscape timed out for {scene_name}: {exc}")
            results.append(
                {
                    "scene_name": scene_name,
                    "input_path": str(input_path),
                    "scene_dir": str(scene_dir),
                    "status": "failed",
                    "error": f"timeout after {timeout}s",
                }
            )
        except Exception as exc:
            failed += 1
            _emit_log(log_callback, "ERROR", f"GF3 SARscape exception for {scene_name}: {exc}")
            results.append(
                {
                    "scene_name": scene_name,
                    "input_path": str(input_path),
                    "scene_dir": str(scene_dir),
                    "status": "failed",
                    "error": str(exc),
                }
            )

    _emit_progress(progress_callback, 70, "GF3 SARscape production stage finished.")
    return {
        "ok": failed == 0,
        "found_count": total,
        "processed_count": processed,
        "skipped_count": skipped,
        "failed_count": failed,
        "deferred_count": deferred,
        "native_root": str(native_root_path),
        "missing_roots": discovery.get("missing_roots") or [],
        "results": results,
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _entry_size(path: Path) -> int:
    try:
        if path.is_file():
            return int(path.stat().st_size)
        if path.is_dir():
            total = 0
            for current, _dir_names, file_names in os.walk(path):
                for file_name in file_names:
                    file_path = Path(current) / file_name
                    try:
                        total += int(file_path.stat().st_size)
                    except OSError:
                        continue
            return total
    except OSError:
        return 0
    return 0


def _is_final_geo_asset_file(path: Path) -> bool:
    name = path.name.lower()
    if name in KEEP_FILE_NAMES or name.endswith(".log"):
        return True
    return (
        name.endswith("_geo")
        or name.endswith("_geo.hdr")
        or name.endswith("_geo.sml")
        or name.endswith("_geo.ovr")
        or name.endswith("_geo.aux.xml")
        or name.endswith("_geo.kml")
        or name.endswith("_geo_ql.tif")
        or name.endswith("_geo_ql.kml")
    )


def _is_known_intermediate_file(path: Path) -> bool:
    name = path.name.lower()
    if _is_final_geo_asset_file(path):
        return False
    if any(token in name for token in ("_slc", "_ml", "_filt")):
        return True
    if name.endswith(INTERMEDIATE_SUFFIXES):
        return True
    return False


def _standard_manifest_path(scene_manifest: dict[str, Any], storage_root: Path) -> Path:
    batch_name = _safe_slug(scene_manifest.get("batch_name") or (scene_manifest.get("metadata") or {}).get("imaging_date"), default="unknown_batch")
    scene_name = _safe_slug(scene_manifest.get("scene_name"), default="unknown_scene")
    return storage_root / batch_name / scene_name / STANDARD_MANIFEST_NAME


def _standard_manifest_allows_cleanup(scene_manifest: dict[str, Any], storage_root: Path) -> bool:
    manifest = _read_json(_standard_manifest_path(scene_manifest, storage_root))
    if not manifest:
        return False
    return str(manifest.get("status") or "").upper() == "DONE"


def _assert_safe_delete(target: Path, scene_dir: Path, native_roots: list[Path]) -> None:
    resolved_target = target.resolve()
    resolved_scene = scene_dir.resolve()
    if resolved_target == resolved_scene:
        raise RuntimeError(f"Refusing to delete scene directory itself: {resolved_target}")
    if not _is_relative_to(resolved_target, resolved_scene):
        raise RuntimeError(f"Refusing to delete outside scene directory: {resolved_target}")
    if not any(_is_relative_to(resolved_target, root) for root in native_roots):
        raise RuntimeError(f"Refusing to delete outside GF3 native roots: {resolved_target}")


def _cleanup_scene_intermediates(
    scene_manifest: dict[str, Any],
    *,
    native_roots: list[Path],
    dry_run: bool,
) -> dict[str, Any]:
    scene_dir = Path(str(scene_manifest.get("native_dir") or "")).resolve()
    if not scene_dir.is_dir():
        return {
            "scene_name": scene_manifest.get("scene_name"),
            "scene_dir": str(scene_dir),
            "status": "skipped",
            "reason": "scene directory missing",
            "deleted_entries": [],
            "bytes_deleted": 0,
        }
    if not any(_is_relative_to(scene_dir, root) for root in native_roots):
        return {
            "scene_name": scene_manifest.get("scene_name"),
            "scene_dir": str(scene_dir),
            "status": "skipped",
            "reason": "scene directory is outside configured native roots",
            "deleted_entries": [],
            "bytes_deleted": 0,
        }

    candidates: list[Path] = []
    for entry in sorted(scene_dir.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_dir() and entry.name.lower() in INTERMEDIATE_DIR_NAMES:
            candidates.append(entry)
        elif entry.is_file() and _is_known_intermediate_file(entry):
            candidates.append(entry)

    deleted_entries: list[dict[str, Any]] = []
    bytes_deleted = 0
    errors: list[dict[str, str]] = []

    for target in candidates:
        try:
            _assert_safe_delete(target, scene_dir, native_roots)
            size = _entry_size(target)
            bytes_deleted += size
            deleted_entries.append(
                {
                    "path": str(target),
                    "type": "directory" if target.is_dir() else "file",
                    "size": size,
                }
            )
            if not dry_run:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        except Exception as exc:
            errors.append({"path": str(target), "error": str(exc)})

    status = "cleaned" if deleted_entries and not errors else ("error" if errors else "nothing_to_delete")
    cleanup_manifest = {
        "schema": "gf3_sarscape_cleanup.v1",
        "generated_at": _utc_now(),
        "dry_run": dry_run,
        "scene_name": scene_manifest.get("scene_name"),
        "scene_dir": str(scene_dir),
        "status": status,
        "bytes_deleted": bytes_deleted,
        "deleted_entries": deleted_entries,
        "errors": errors,
        "retention_policy": {
            "keep": "final *_geo native assets, quicklooks, manifests, and logs",
            "delete": "extract/temp/work directories and slc/ml/filt intermediate files",
        },
    }
    if not dry_run:
        _write_json(scene_dir / CLEANUP_MANIFEST_NAME, cleanup_manifest)
    return cleanup_manifest


def cleanup_gf3_sarscape_native_pool(
    *,
    native_dirs: list[str] | None = None,
    storage_root: str | None = None,
    require_standardized: bool = True,
    dry_run: bool = False,
    max_scenes: int | None = None,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Delete intermediate SARscape files while keeping final native _geo assets."""
    native_dirs = native_dirs if native_dirs is not None else split_env_paths(settings.GF3_SARSCAPE_NATIVE_DIRS)
    native_roots, missing_roots = _resolve_existing_dirs(native_dirs)
    if not native_roots:
        raise ValueError("GF3_SARSCAPE_NATIVE_DIRS has no accessible directories.")
    storage = Path(os.path.normpath(storage_root or settings.GF3_STORAGE_DIRS)).resolve() if storage_root or settings.GF3_STORAGE_DIRS else None

    inventory = scan_gf3_sarscape_native_roots([str(root) for root in native_roots], write_manifest=not dry_run)
    scenes = inventory.get("scenes") or []
    max_count = int(max_scenes or 0)
    cleaned = 0
    skipped = 0
    error_count = 0
    bytes_deleted = 0
    scene_results: list[dict[str, Any]] = []

    for idx, scene_manifest in enumerate(scenes):
        _emit_progress(
            progress_callback,
            5 + int((idx / max(len(scenes), 1)) * 90),
            f"GF3 native cleanup checking {idx + 1}/{len(scenes)}: {scene_manifest.get('scene_name')}",
        )
        scene_status = str(scene_manifest.get("status") or "")
        if scene_status != "NATIVE_READY":
            skipped += 1
            scene_results.append(
                {
                    "scene_name": scene_manifest.get("scene_name"),
                    "status": "skipped",
                    "reason": f"native status is {scene_status or 'UNKNOWN'}",
                }
            )
            continue
        if require_standardized:
            if storage is None:
                skipped += 1
                scene_results.append(
                    {
                        "scene_name": scene_manifest.get("scene_name"),
                        "status": "skipped",
                        "reason": "GF3_STORAGE_DIRS is not configured",
                    }
                )
                continue
            if not _standard_manifest_allows_cleanup(scene_manifest, storage):
                skipped += 1
                scene_results.append(
                    {
                        "scene_name": scene_manifest.get("scene_name"),
                        "status": "skipped",
                        "reason": "standard GeoTIFF manifest is not DONE",
                    }
                )
                continue
        if max_count > 0 and cleaned >= max_count:
            skipped += 1
            scene_results.append(
                {
                    "scene_name": scene_manifest.get("scene_name"),
                    "status": "skipped",
                    "reason": "max_scenes limit reached",
                }
            )
            continue

        result = _cleanup_scene_intermediates(scene_manifest, native_roots=native_roots, dry_run=dry_run)
        scene_results.append(result)
        bytes_deleted += int(result.get("bytes_deleted") or 0)
        if result.get("status") == "error":
            error_count += 1
        if result.get("status") in {"cleaned", "nothing_to_delete"}:
            cleaned += 1
            _emit_log(
                log_callback,
                "INFO",
                f"GF3 cleanup {result.get('status')}: {result.get('scene_name')} bytes={result.get('bytes_deleted')}",
            )

    _emit_progress(progress_callback, 100, "GF3 native cleanup finished.")
    return {
        "ok": error_count == 0,
        "dry_run": dry_run,
        "native_roots": [str(root) for root in native_roots],
        "missing_roots": missing_roots,
        "scene_count": len(scenes),
        "cleaned_scene_count": cleaned,
        "skipped_scene_count": skipped,
        "error_scene_count": error_count,
        "bytes_deleted": bytes_deleted,
        "scenes": scene_results,
    }
