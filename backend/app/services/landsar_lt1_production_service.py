from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings, split_env_paths
from ..dinsar_engines.landsar_engine import LandsarEngine
from ..models import RadarDataORM, ResultAssetORM, ResultCatalogStateORM, ResultProductORM, SARSceneGeoORM


LANDSAR_LT1_CATALOG = "lt1_landsar"
IMPORT_PROID = "100016"
ORBIT_PROID = "100206"
PROFILE_SCENE_IMPORT = "landsar.import.lt1.scene.v1"
PROFILE_STACK_IMPORT = "landsar.import.lt1.stack.v1"
PRODUCT_FAMILY_SCENE = "lt1_scene_import"
PRODUCT_FAMILY_STACK = "lt1_stack_import"


ProgressCallback = Callable[[Dict[str, Any]], None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_text(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_path(path: Any) -> str:
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(text))))


def _safe_key(value: Any, *, fallback: str = "lt1") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text[:96] or fallback


def _short_hash(values: Iterable[Any], *, length: int = 10) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value or "").encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()[:length]


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def _read_text_tail(path: str, *, max_chars: int = 12000) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as fp:
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            fp.seek(max(0, size - max_chars))
            return fp.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _file_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _media_type(path: str) -> tuple[Optional[str], Optional[str]]:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    media, _ = mimetypes.guess_type(path)
    if ext == "xml":
        media = "application/xml"
    elif ext == "json":
        media = "application/json"
    elif ext in {"tif", "tiff"}:
        media = "image/tiff"
    elif ext == "txt":
        media = "text/plain"
    return ext or None, media


def _success_in_logs(log_path: str, keywords: Iterable[str], exit_code: int) -> bool:
    content = _read_text_tail(log_path, max_chars=80000).lower()
    if any(str(keyword).lower() in content for keyword in keywords):
        return True
    return int(exit_code) == 0 and ("console success" in content or "success" in content)


def _run_console(
    console_path: str,
    param_file: str,
    log_path: str,
    *,
    cwd: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    started_at = _utc_now()
    env = dict(os.environ)
    configured_runtime_dirs: List[str] = []
    for env_name in ("LANDSAR_RUNTIME_PATHS", "LANDSAR_DLL_DIRS"):
        configured_runtime_dirs.extend(
            _norm_path(item)
            for item in str(os.environ.get(env_name, "") or "").split(os.pathsep)
            if _norm_path(item)
        )
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    prepend_dirs = [
        os.path.dirname(_norm_path(console_path)),
        _norm_path(getattr(settings, "LANDSAR_HOME", "")),
        *configured_runtime_dirs,
        os.path.join(system_root, "System32"),
        os.path.join(system_root, "SysWOW64"),
        system_root,
    ]
    current_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([path for path in prepend_dirs if path] + ([current_path] if current_path else []))
    command = [console_path, param_file]
    with open(log_path, "a", encoding="utf-8", errors="replace") as log_fp:
        log_fp.write(f"\n[{_utc_text(started_at)}] command: {' '.join(command)}\n")
        log_fp.flush()
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(60, int(timeout_seconds or 3600)),
                check=False,
            )
            returncode = int(proc.returncode)
        except subprocess.TimeoutExpired:
            returncode = -9
            log_fp.write(f"\n[{_utc_text()}] timeout after {int(timeout_seconds or 3600)} seconds\n")
        log_fp.write(f"\n[{_utc_text()}] returncode: {returncode}\n")
    return {
        "command": command,
        "returncode": returncode,
        "started_at": _utc_text(started_at),
        "finished_at": _utc_text(),
        "log_path": log_path,
        "stdout_tail": _read_text_tail(log_path),
    }


def generate_lt1_import_param_file(
    filepath: str,
    scene_dirs: List[str],
    export_dir: str,
    *,
    sat_mode: str = "MONO",
) -> str:
    if not scene_dirs:
        raise ValueError("scene_dirs cannot be empty.")
    mode = str(sat_mode or "MONO").strip().upper()
    lines = [
        "卫星数据导入LT-1",
        f"处理编号       {IMPORT_PROID}",
        "设置数据导入形式_0文件夹导入_1数据导入  文件夹导入",
        "读取成像参数文件_0否_1是 1",
        "读取SLC数据文件_0否_1是 1",
        "文件夹导入标识  TRUE",
        f"文件夹导入个数  {len(scene_dirs)}",
    ]
    for idx, scene_dir in enumerate(scene_dirs, 1):
        lines.append(f"文件夹{idx}路径  <{scene_dir}>")
    lines.extend(
        [
            "数据导入  FALSE",
            f"输入卫星数据格式  {mode}",
            "输入主影像成像参数文件路径  <>",
            "输入主影像SLC数据文件路径  <>",
            "输入主影像RPB数据文件路径  <>",
            "输入辅影像成像参数文件路径  <>",
            "输入辅影像SLC数据文件路径  <>",
            "输入辅影像RPB数据文件路径  <>",
            "设置数据导出目标路径_0原目录_1新目录  1",
            f"设置输出文件目录  <{export_dir}>",
        ]
    )
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8", newline="\n") as fp:
        fp.write("\n".join(lines) + "\n")
    return filepath


def generate_lt1_orbit_param_file(
    filepath: str,
    xml_paths: List[str],
    orbit_dir: str,
    output_dir: str,
    *,
    xml_save_mode: int = 0,
    export_to_new: bool = False,
) -> str:
    if not xml_paths:
        raise ValueError("xml_paths cannot be empty.")
    lines = [
        "LT-1精密轨道数据导入",
        f"处理编号       {ORBIT_PROID}",
        f"输入数据个数  {len(xml_paths)}",
    ]
    for idx, xml_path in enumerate(xml_paths, 1):
        lines.append(f"输入数据{idx}的xml  <{xml_path}>")
    lines.extend(
        [
            f"输入精密轨道数据文件夹            <{orbit_dir}>",
            f"选择XML文件保存方式               {int(xml_save_mode)}",
            f"设置数据导出目录形式0原目录1新目录   {1 if export_to_new else 0}",
            f"输出更新处理后数据目录             <{output_dir}>",
        ]
    )
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8", newline="\n") as fp:
        fp.write("\n".join(lines) + "\n")
    return filepath


@dataclass(frozen=True)
class Lt1ImportRequest:
    scene_dirs: List[str]
    source_asset_ids: List[int]
    radar_data_ids: List[int]
    materialize_assets: bool = True
    materialize_overwrite: bool = False
    mode: str = "scene"
    task_name: Optional[str] = None
    sat_mode: str = "MONO"
    import_orbit: bool = False
    orbit_dir: Optional[str] = None
    timeout_seconds: int = 7200


class LandsarLt1ProductionService:
    def __init__(self) -> None:
        self._engine = LandsarEngine()

    @property
    def publish_root(self) -> str:
        return _norm_path(os.path.join(settings.RESULT_PUBLISH_ROOT, LANDSAR_LT1_CATALOG))

    def _console_path(self) -> str:
        return _norm_path(getattr(settings, "LANDSAR_CONSOLE_EXE", ""))

    def _landsar_home(self) -> str:
        return _norm_path(getattr(settings, "LANDSAR_HOME", "")) or os.path.dirname(self._console_path())

    def check_capabilities(self) -> Dict[str, Any]:
        availability = self._engine.check_available()
        orbit_roots = [
            _norm_path(path)
            for path in split_env_paths(getattr(settings, "ORBIT_SOURCE_DIRS", ""))
            if _norm_path(path)
        ]
        return {
            "catalog_name": LANDSAR_LT1_CATALOG,
            "supported_profiles": [PROFILE_SCENE_IMPORT, PROFILE_STACK_IMPORT],
            "proids": {
                "import": IMPORT_PROID,
                "orbit": ORBIT_PROID,
            },
            "available": bool(getattr(availability, "available", False)),
            "status": getattr(availability, "status", "unknown"),
            "message": getattr(availability, "message", ""),
            "checks": getattr(availability, "checks", []),
            "console_path": self._console_path(),
            "landsar_home": self._landsar_home(),
            "publish_root": self.publish_root,
            "orbit_roots": orbit_roots,
        }

    def _normalize_request(self, payload: Dict[str, Any], *, allow_empty_scene_dirs: bool = False) -> Lt1ImportRequest:
        raw_dirs = payload.get("scene_dirs") or payload.get("source_dirs") or []
        if not isinstance(raw_dirs, list):
            raise ValueError("scene_dirs must be a list.")
        scene_dirs: List[str] = []
        for raw in raw_dirs:
            path = _norm_path(raw)
            if not path:
                continue
            if path not in scene_dirs:
                scene_dirs.append(path)
        if not scene_dirs and not allow_empty_scene_dirs:
            raise ValueError("scene_dirs cannot be empty.")
        for path in scene_dirs:
            if not os.path.isdir(path):
                raise FileNotFoundError(f"LT-1 scene directory not found: {path}")

        source_asset_ids = self._normalize_id_list(payload.get("source_asset_ids"))
        radar_data_ids = self._normalize_id_list(payload.get("radar_data_ids"))
        planned_count = len(scene_dirs) + len(source_asset_ids) + len(radar_data_ids)

        mode = str(payload.get("mode") or ("stack" if planned_count > 1 else "scene")).strip().lower()
        if mode not in {"scene", "stack"}:
            raise ValueError("mode must be scene or stack.")
        if mode == "scene" and planned_count != 1:
            raise ValueError("scene mode requires exactly one scene directory or source asset.")

        orbit_dir = _norm_path(payload.get("orbit_dir"))
        return Lt1ImportRequest(
            scene_dirs=scene_dirs,
            source_asset_ids=source_asset_ids,
            radar_data_ids=radar_data_ids,
            materialize_assets=bool(payload.get("materialize_assets", True)),
            materialize_overwrite=bool(payload.get("materialize_overwrite", False)),
            mode=mode,
            task_name=str(payload.get("task_name") or "").strip() or None,
            sat_mode=str(payload.get("sat_mode") or "MONO").strip().upper(),
            import_orbit=bool(payload.get("import_orbit")),
            orbit_dir=orbit_dir or None,
            timeout_seconds=max(60, int(payload.get("timeout_seconds") or 7200)),
        )

    def _normalize_id_list(self, value: Any) -> List[int]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ValueError("asset id fields must be lists.")
        normalized: List[int] = []
        for raw in value:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in normalized:
                normalized.append(parsed)
        return normalized

    def preview_import(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = self._normalize_request(payload, allow_empty_scene_dirs=True)
        scene_info = [self._inspect_scene_dir(path) for path in request.scene_dirs]
        orbit_dir = request.orbit_dir or self._default_orbit_dir()
        blockers: List[str] = []
        warnings: List[str] = []
        planned_scene_count = len(request.scene_dirs) + len(request.source_asset_ids) + len(request.radar_data_ids)
        if not planned_scene_count:
            blockers.append("No LT-1 scene directory or source asset was selected.")
        for item in scene_info:
            if not item["xml_count"]:
                blockers.append(f"Missing LT-1 XML in {item['path']}")
            if not item["slc_count"]:
                blockers.append(f"Missing LT-1 SLC TIFF in {item['path']}")
        if request.import_orbit and not orbit_dir:
            blockers.append("import_orbit is enabled but no orbit directory is configured.")
        elif request.import_orbit and orbit_dir and not os.path.isdir(orbit_dir):
            blockers.append(f"Orbit directory not found: {orbit_dir}")
        if request.mode == "stack" and planned_scene_count < 2:
            warnings.append("Stack mode normally expects at least two scenes.")
        return {
            "allow_submit": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "mode": request.mode,
            "profile_code": PROFILE_STACK_IMPORT if request.mode == "stack" else PROFILE_SCENE_IMPORT,
            "scene_count": planned_scene_count,
            "directory_scene_count": len(request.scene_dirs),
            "source_asset_count": len(request.source_asset_ids),
            "radar_data_count": len(request.radar_data_ids),
            "scenes": scene_info,
            "sat_mode": request.sat_mode,
            "import_orbit": request.import_orbit,
            "orbit_dir": orbit_dir,
            "publish_root": self.publish_root,
        }

    def _inspect_scene_dir(self, path: str) -> Dict[str, Any]:
        xmls: List[str] = []
        slcs: List[str] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    lower = entry.name.lower()
                    if lower.startswith("lt1") and lower.endswith(".xml"):
                        xmls.append(entry.path)
                    elif lower.startswith("lt1") and lower.endswith((".tif", ".tiff")):
                        slcs.append(entry.path)
        except OSError:
            pass
        return {
            "path": path,
            "name": os.path.basename(path),
            "xml_count": len(xmls),
            "slc_count": len(slcs),
            "sample_xml": xmls[0] if xmls else "",
            "sample_slc": slcs[0] if slcs else "",
        }

    def _default_orbit_dir(self) -> str:
        for raw in split_env_paths(getattr(settings, "ORBIT_SOURCE_DIRS", "")):
            path = _norm_path(raw)
            if path and os.path.isdir(path):
                return path
        return ""

    def _product_keys(self, request: Lt1ImportRequest) -> tuple[str, str, str]:
        profile = PROFILE_STACK_IMPORT if request.mode == "stack" else PROFILE_SCENE_IMPORT
        family = PRODUCT_FAMILY_STACK if request.mode == "stack" else PRODUCT_FAMILY_SCENE
        if request.mode == "scene":
            identity_key = _safe_key(os.path.basename(request.scene_dirs[0]))
        else:
            names = [_safe_key(os.path.basename(path)) for path in request.scene_dirs]
            identity_key = _safe_key(f"stack_{len(names)}_{names[0]}_{names[-1]}_{_short_hash(request.scene_dirs)}")
        return profile, family, identity_key

    def _ensure_runtime_ready(self) -> None:
        availability = self._engine.check_available()
        if not bool(getattr(availability, "available", False)):
            raise RuntimeError(getattr(availability, "message", "") or "LandSAR console is unavailable.")

    def _find_imported_xmls(self, input_data_dir: str) -> List[str]:
        matches: List[str] = []
        for root, _, files in os.walk(input_data_dir):
            for name in files:
                lower = name.lower()
                if lower.startswith("lt1") and lower.endswith("_slc.xml"):
                    matches.append(os.path.join(root, name))
        return sorted(matches)

    def _copy_scene_summary(self, request: Lt1ImportRequest, run_dir: str) -> str:
        target = os.path.join(run_dir, "source_scenes.json")
        payload = {
            "scene_count": len(request.scene_dirs),
            "scene_dirs": request.scene_dirs,
            "source_asset_ids": request.source_asset_ids,
            "radar_data_ids": request.radar_data_ids,
            "mode": request.mode,
            "sat_mode": request.sat_mode,
            "generated_at": _utc_text(),
        }
        _write_json(target, payload)
        return target

    async def find_produced_source_asset_map(
        self,
        db: AsyncSession,
        source_asset_ids: Iterable[int],
    ) -> Dict[int, Dict[str, Any]]:
        ids: List[int] = []
        for item in source_asset_ids:
            try:
                parsed = int(item or 0)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in ids:
                ids.append(parsed)
        if not ids:
            return {}
        result = await db.execute(
            select(RadarDataORM.source_product_ref_id, SARSceneGeoORM)
            .join(SARSceneGeoORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
            .where(
                RadarDataORM.source_product_ref_id.in_(ids),
                RadarDataORM.satellite_family == "LT1",
                SARSceneGeoORM.status == "DONE",
                SARSceneGeoORM.analysis_tif_path.isnot(None),
                SARSceneGeoORM.analysis_engine == "lt_gamma",
                SARSceneGeoORM.analysis_profile == "lt1_gamma_geocoded_mli",
            )
            .order_by(SARSceneGeoORM.updated_at.desc().nullslast(), SARSceneGeoORM.id.desc())
        )
        wanted = set(ids)
        produced: Dict[int, Dict[str, Any]] = {}
        for source_id, scene in result.all():
            try:
                parsed_source_id = int(source_id or 0)
            except (TypeError, ValueError):
                continue
            if parsed_source_id not in wanted or parsed_source_id in produced:
                continue
            produced[parsed_source_id] = {
                "product_db_id": scene.id,
                "scene_id": scene.id,
                "radar_data_id": scene.radar_data_id,
                "product_id": f"sar_scene_geo:{scene.id}",
                "product_family": "lt1_analysis_ready_geotiff",
                "engine_code": scene.analysis_engine,
                "profile_code": scene.analysis_profile,
                "analysis_tif_path": scene.analysis_tif_path,
                "analysis_dir": scene.analysis_dir,
                "analysis_preview_path": scene.analysis_preview_path,
                "published_at": scene.updated_at.isoformat() if scene.updated_at else None,
                "native_output_dir": scene.analysis_dir,
            }
        return produced

    async def decorate_source_asset_payloads(self, db: AsyncSession, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        produced = await self.find_produced_source_asset_map(
            db,
            [int(item.get("id") or 0) for item in items],
        )
        for item in items:
            marker = produced.get(int(item.get("id") or 0))
            item["lt1_image_produced"] = bool(marker)
            item["lt1_image_product"] = marker
            item["lt1_landsar_produced"] = bool(marker)
            item["lt1_landsar_product"] = marker
        return items

    def run_import(
        self,
        payload: Dict[str, Any],
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        original_source_asset_ids = self._normalize_id_list(payload.get("source_asset_ids"))
        original_radar_data_ids = self._normalize_id_list(payload.get("radar_data_ids"))
        prepared_scene_dirs = [
            _norm_path(path)
            for path in (payload.get("__prepared_scene_dirs") or [])
            if _norm_path(path)
        ]
        if prepared_scene_dirs:
            payload = {
                **payload,
                "scene_dirs": [*payload.get("scene_dirs", []), *prepared_scene_dirs],
                "source_asset_ids": [],
                "radar_data_ids": [],
            }
        request = self._normalize_request(payload, allow_empty_scene_dirs=True)
        if prepared_scene_dirs:
            request = replace(
                request,
                source_asset_ids=original_source_asset_ids,
                radar_data_ids=original_radar_data_ids,
            )
        if (request.source_asset_ids or request.radar_data_ids) and not request.scene_dirs:
            raise ValueError("Source assets must be materialized before LandSAR LT-1 import starts.")
        self._ensure_runtime_ready()

        profile, product_family, identity_key = self._product_keys(request)
        started_at = _utc_now()
        run_hash = _short_hash([started_at.isoformat(), *request.scene_dirs])
        run_key = f"run_{started_at.strftime('%Y%m%dT%H%M%SZ')}_{run_hash}"
        product_prefix = "lt1_stack" if request.mode == "stack" else "lt1_scene"
        readable_len = max(8, 64 - len(product_prefix) - len(run_hash) - 2)
        product_id = _safe_key(
            f"{product_prefix}_{identity_key[:readable_len]}_{run_hash}",
            fallback="lt1_product",
        )[:64]

        publish_dir = os.path.join(self.publish_root, product_family, identity_key, "runs", run_key)
        native_dir = os.path.join(publish_dir, "native")
        input_data_dir = os.path.join(native_dir, "Input_Data")
        param_dir = os.path.join(publish_dir, "params")
        log_dir = os.path.join(publish_dir, "logs")
        os.makedirs(input_data_dir, exist_ok=True)
        os.makedirs(param_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)
        self._copy_scene_summary(request, publish_dir)

        console_path = self._console_path()
        landsar_home = self._landsar_home()
        timeout = int(request.timeout_seconds or 7200)
        import_param = os.path.join(param_dir, f"{IMPORT_PROID}.txt")
        import_log = os.path.join(log_dir, f"{IMPORT_PROID}_console.log")
        generate_lt1_import_param_file(
            import_param,
            request.scene_dirs,
            input_data_dir,
            sat_mode=request.sat_mode,
        )
        self._emit(progress_callback, "import_started", progress=10, message="LandSAR LT-1 import started")
        import_run = _run_console(
            console_path,
            import_param,
            import_log,
            cwd=landsar_home,
            timeout_seconds=timeout,
        )
        import_ok = _success_in_logs(
            import_log,
            ["module [lt-1数据导入] success", "lt-1数据导入] success", "console success"],
            int(import_run["returncode"]),
        )
        imported_xmls = self._find_imported_xmls(input_data_dir)
        if not import_ok or not imported_xmls:
            raise RuntimeError(
                "LandSAR LT-1 import failed or produced no Input_Data XML. "
                f"returncode={import_run['returncode']}; log={import_log}"
            )

        orbit_result: Optional[Dict[str, Any]] = None
        orbit_dir = request.orbit_dir or self._default_orbit_dir()
        if request.import_orbit:
            if not orbit_dir or not os.path.isdir(orbit_dir):
                raise FileNotFoundError(f"Orbit directory not found: {orbit_dir or '<empty>'}")
            orbit_param = os.path.join(param_dir, f"{ORBIT_PROID}.txt")
            orbit_log = os.path.join(log_dir, f"{ORBIT_PROID}_console.log")
            generate_lt1_orbit_param_file(
                orbit_param,
                imported_xmls,
                orbit_dir,
                input_data_dir,
                xml_save_mode=0,
                export_to_new=False,
            )
            self._emit(progress_callback, "orbit_started", progress=60, message="LandSAR LT-1 orbit import started")
            orbit_run = _run_console(
                console_path,
                orbit_param,
                orbit_log,
                cwd=landsar_home,
                timeout_seconds=timeout,
            )
            orbit_ok = _success_in_logs(
                orbit_log,
                ["module [lt-1精密轨道数据导入] success", "精密轨道数据导入] success", "console success"],
                int(orbit_run["returncode"]),
            )
            if not orbit_ok:
                raise RuntimeError(
                    "LandSAR LT-1 precise orbit import failed. "
                    f"returncode={orbit_run['returncode']}; log={orbit_log}"
                )
            orbit_result = {
                **orbit_run,
                "param_file": orbit_param,
                "orbit_dir": orbit_dir,
                "xml_count": len(imported_xmls),
            }

        self._emit(progress_callback, "packaging_started", progress=85, message="Registering LT-1 product")
        manifest_path = self._build_manifest(
            request=request,
            product_id=product_id,
            profile=profile,
            product_family=product_family,
            identity_key=identity_key,
            run_key=run_key,
            publish_dir=publish_dir,
            native_dir=native_dir,
            input_data_dir=input_data_dir,
            started_at=started_at,
            import_result={**import_run, "param_file": import_param},
            orbit_result=orbit_result,
            imported_xmls=imported_xmls,
            materialized=payload.get("__materialized") or [],
            task_root=payload.get("__materialize_task_root"),
        )
        self._emit(progress_callback, "completed", progress=95, message="LT-1 LandSAR import completed")
        return {
            "product_id": product_id,
            "manifest_path": manifest_path,
            "publish_dir": publish_dir,
            "native_output_dir": native_dir,
            "input_data_dir": input_data_dir,
            "run_key": run_key,
            "profile_code": profile,
            "product_family": product_family,
            "imported_xml_count": len(imported_xmls),
            "import_orbit": bool(orbit_result),
        }

    def _emit(self, callback: Optional[ProgressCallback], event: str, **payload: Any) -> None:
        if not callable(callback):
            return
        try:
            callback({"event": event, **payload})
        except Exception:
            return

    def _collect_assets(self, publish_dir: str, native_dir: str, manifest_path: str) -> List[Dict[str, Any]]:
        assets: List[Dict[str, Any]] = []

        def add(path: str, role: str, *, primary: bool = False, required: bool = False) -> None:
            absolute = _norm_path(path)
            if not absolute:
                return
            try:
                relative = os.path.relpath(absolute, publish_dir)
            except ValueError:
                relative = absolute
            fmt, media = _media_type(absolute)
            assets.append(
                {
                    "role": role,
                    "name": os.path.basename(absolute),
                    "relative_path": relative,
                    "absolute_path": absolute,
                    "format": fmt,
                    "media_type": media,
                    "is_required": required,
                    "is_primary": primary,
                    "exists": os.path.isfile(absolute),
                    "file_size": _file_size(absolute),
                }
            )

        add(manifest_path, "manifest", required=True)
        source_summary = os.path.join(publish_dir, "source_scenes.json")
        add(source_summary, "source_summary", required=True)
        for folder in (os.path.join(publish_dir, "params"), os.path.join(publish_dir, "logs")):
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    add(path, "param" if name.lower().endswith(".txt") else "log", required=True)

        input_data_dir = os.path.join(native_dir, "Input_Data")
        primary_set = False
        for root, _, files in os.walk(input_data_dir):
            for name in sorted(files):
                lower = name.lower()
                path = os.path.join(root, name)
                if lower.endswith(".xml"):
                    add(path, "input_xml", primary=not primary_set, required=True)
                    primary_set = True
                elif lower.endswith((".tif", ".tiff")):
                    add(path, "input_tif", primary=not primary_set)
                    primary_set = True
                elif lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    add(path, "preview")
        return assets

    def _build_manifest(
        self,
        *,
        request: Lt1ImportRequest,
        product_id: str,
        profile: str,
        product_family: str,
        identity_key: str,
        run_key: str,
        publish_dir: str,
        native_dir: str,
        input_data_dir: str,
        started_at: datetime,
        import_result: Dict[str, Any],
        orbit_result: Optional[Dict[str, Any]],
        imported_xmls: List[str],
        materialized: List[Dict[str, Any]],
        task_root: Optional[str],
    ) -> str:
        manifest_path = os.path.join(publish_dir, "manifest.json")
        task_name = request.task_name or (
            os.path.basename(request.scene_dirs[0]) if request.mode == "scene" else f"LT-1 stack {len(request.scene_dirs)} scenes"
        )
        summary = {
            "scene_count": len(request.scene_dirs),
            "imported_xml_count": len(imported_xmls),
            "source_asset_ids": request.source_asset_ids,
            "radar_data_ids": request.radar_data_ids,
            "materialized_dirs": [item.get("scene_dir") for item in materialized if item.get("scene_dir")],
            "materialize_task_root": task_root,
            "input_data_dir": input_data_dir,
            "sat_mode": request.sat_mode,
            "import_orbit": bool(orbit_result),
            "orbit_dir": (orbit_result or {}).get("orbit_dir"),
            "import_returncode": import_result.get("returncode"),
            "orbit_returncode": (orbit_result or {}).get("returncode"),
        }
        payload: Dict[str, Any] = {
            "schema_version": "lt1_landsar.import.v1",
            "catalog_name": LANDSAR_LT1_CATALOG,
            "product_family": product_family,
            "product_type": "landsar_input_data",
            "product_id": product_id,
            "display_name": task_name,
            "task_name": task_name,
            "identity": {
                "mode": request.mode,
                "scene_key": identity_key if request.mode == "scene" else None,
                "stack_key": identity_key if request.mode == "stack" else None,
                "run_key": run_key,
            },
            "engine": {
                "code": "landsar",
                "console_path": self._console_path(),
                "home": self._landsar_home(),
            },
            "processor": {
                "code": "landsar.lt1.import",
                "profile_code": profile,
                "import_proid": IMPORT_PROID,
                "orbit_proid": ORBIT_PROID if orbit_result else None,
            },
            "run": {
                "started_at": _utc_text(started_at),
                "finished_at": _utc_text(),
                "status": "COMPLETED",
            },
            "source": {
                "scene_dirs": request.scene_dirs,
                "source_asset_ids": request.source_asset_ids,
                "radar_data_ids": request.radar_data_ids,
                "materialized": materialized,
                "native_output_dir": native_dir,
                "publish_dir": publish_dir,
            },
            "summary": summary,
            "execution": {
                "import": import_result,
                "orbit": orbit_result,
            },
            "assets": [],
        }
        _write_json(manifest_path, payload)
        payload["assets"] = self._collect_assets(publish_dir, native_dir, manifest_path)
        _write_json(manifest_path, payload)
        execution_manifest_path = os.path.join(publish_dir, "execution_manifest.json")
        shutil.copy2(manifest_path, execution_manifest_path)
        return manifest_path

    async def register_manifest(self, db: AsyncSession, manifest_path: str) -> Dict[str, Any]:
        normalized_manifest = _norm_path(manifest_path)
        with open(normalized_manifest, "r", encoding="utf-8") as fp:
            manifest = json.load(fp)

        product_id = str(manifest.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("manifest.product_id is required.")
        if str(manifest.get("catalog_name") or "") != LANDSAR_LT1_CATALOG:
            raise ValueError("manifest is not a LandSAR LT-1 product manifest.")

        await db.execute(delete(ResultProductORM).where(ResultProductORM.product_id == product_id))

        identity = manifest.get("identity") or {}
        processor = manifest.get("processor") or {}
        engine = manifest.get("engine") or {}
        source = manifest.get("source") or {}
        summary = manifest.get("summary") or {}
        run = manifest.get("run") or {}
        publish_dir = _norm_path(source.get("publish_dir")) or os.path.dirname(normalized_manifest)
        native_dir = _norm_path(source.get("native_output_dir"))
        assets = list(manifest.get("assets") or [])
        primary_asset_path = ""
        for asset in assets:
            if asset.get("is_primary"):
                primary_asset_path = _norm_path(asset.get("absolute_path"))
                break

        product = ResultProductORM(
            product_id=product_id,
            catalog_name=LANDSAR_LT1_CATALOG,
            product_family=str(manifest.get("product_family") or PRODUCT_FAMILY_SCENE),
            product_type=str(manifest.get("product_type") or "landsar_input_data"),
            display_name=str(manifest.get("display_name") or product_id),
            task_name=str(manifest.get("task_name") or ""),
            pair_key=None,
            stack_key=identity.get("stack_key"),
            run_key=identity.get("run_key"),
            profile_code=processor.get("profile_code"),
            engine_code=str(engine.get("code") or "landsar"),
            processor_code=str(processor.get("code") or "landsar.lt1.import"),
            package_schema=str(manifest.get("schema_version") or "lt1_landsar.import.v1"),
            package_layout="lt1_landsar_import",
            status="READY",
            health_status="OK",
            publish_dir=publish_dir,
            manifest_path=normalized_manifest,
            native_output_dir=native_dir,
            primary_asset_path=primary_asset_path or None,
            summary_json=summary,
            tags_json={
                "catalog_name": LANDSAR_LT1_CATALOG,
                "mode": identity.get("mode"),
                "import_orbit": bool(summary.get("import_orbit")),
            },
            produced_at=self._parse_time(run.get("finished_at")) or _utc_now(),
            published_at=_utc_now(),
        )
        db.add(product)
        await db.flush()

        for asset in assets:
            absolute = _norm_path(asset.get("absolute_path"))
            fmt = asset.get("format")
            media = asset.get("media_type")
            if not fmt or not media:
                fmt, media = _media_type(absolute)
            db.add(
                ResultAssetORM(
                    product_ref_id=product.id,
                    asset_role=str(asset.get("role") or "asset")[:32],
                    asset_name=str(asset.get("name") or os.path.basename(absolute) or "asset"),
                    relative_path=str(asset.get("relative_path") or ""),
                    absolute_path=absolute,
                    format=fmt,
                    media_type=media,
                    is_required=bool(asset.get("is_required")),
                    is_primary=bool(asset.get("is_primary")),
                    exists_flag=os.path.isfile(absolute),
                    file_size=_file_size(absolute),
                )
            )

        await self._update_catalog_state(db)
        await db.commit()
        return {
            "product_db_id": product.id,
            "product_id": product_id,
            "asset_count": len(assets),
            "manifest_path": normalized_manifest,
        }

    def _parse_time(self, value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None

    async def _update_catalog_state(self, db: AsyncSession) -> None:
        result = await db.execute(
            select(ResultCatalogStateORM).where(ResultCatalogStateORM.catalog_name == LANDSAR_LT1_CATALOG)
        )
        state = result.scalar_one_or_none()
        count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == LANDSAR_LT1_CATALOG)
        )
        db_count = int(count_result.scalar_one() or 0)
        if state is None:
            state = ResultCatalogStateORM(
                catalog_name=LANDSAR_LT1_CATALOG,
                product_family="lt1",
                storage_root=self.publish_root,
            )
            db.add(state)
        state.status = "READY"
        state.needs_rebuild = False
        state.storage_root = self.publish_root
        state.db_count = db_count
        state.manifest_count = db_count
        state.last_message = f"LandSAR LT-1 catalog ready: {db_count} products"
        state.last_incremental_scan_at = _utc_now()

    async def list_products(
        self,
        db: AsyncSession,
        *,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(500, int(limit or 100)))
        safe_offset = max(0, int(offset or 0))
        filters = [ResultProductORM.catalog_name == LANDSAR_LT1_CATALOG]
        if status:
            filters.append(ResultProductORM.status == str(status).strip().upper())
        if query:
            like = f"%{str(query).strip()}%"
            filters.append(
                or_(
                    ResultProductORM.product_id.ilike(like),
                    ResultProductORM.display_name.ilike(like),
                    ResultProductORM.task_name.ilike(like),
                    ResultProductORM.stack_key.ilike(like),
                    ResultProductORM.run_key.ilike(like),
                )
            )
        total_result = await db.execute(select(func.count(ResultProductORM.id)).where(*filters))
        total = int(total_result.scalar_one() or 0)
        rows_result = await db.execute(
            select(ResultProductORM)
            .where(*filters)
            .order_by(ResultProductORM.published_at.desc(), ResultProductORM.id.desc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        products = rows_result.scalars().all()
        return {
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "items": [self._serialize_product(product) for product in products],
        }

    async def get_product_detail(self, db: AsyncSession, *, product_id: int) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(ResultProductORM)
            .where(
                ResultProductORM.id == product_id,
                ResultProductORM.catalog_name == LANDSAR_LT1_CATALOG,
            )
        )
        product = result.scalar_one_or_none()
        if product is None:
            return None
        assets_result = await db.execute(
            select(ResultAssetORM)
            .where(ResultAssetORM.product_ref_id == product.id)
            .order_by(ResultAssetORM.is_primary.desc(), ResultAssetORM.asset_role.asc(), ResultAssetORM.id.asc())
        )
        detail = self._serialize_product(product)
        detail["assets"] = [self._serialize_asset(asset) for asset in assets_result.scalars().all()]
        return detail

    async def get_asset(self, db: AsyncSession, *, product_id: int, asset_id: int) -> Optional[ResultAssetORM]:
        product_result = await db.execute(
            select(ResultProductORM.id).where(
                ResultProductORM.id == product_id,
                ResultProductORM.catalog_name == LANDSAR_LT1_CATALOG,
            )
        )
        product_db_id = product_result.scalar_one_or_none()
        if product_db_id is None:
            return None
        asset_result = await db.execute(
            select(ResultAssetORM).where(
                ResultAssetORM.id == asset_id,
                ResultAssetORM.product_ref_id == product_db_id,
            )
        )
        return asset_result.scalar_one_or_none()

    def _serialize_product(self, product: ResultProductORM) -> Dict[str, Any]:
        return {
            "id": product.id,
            "product_id": product.product_id,
            "catalog_name": product.catalog_name,
            "product_family": product.product_family,
            "product_type": product.product_type,
            "display_name": product.display_name,
            "task_name": product.task_name,
            "stack_key": product.stack_key,
            "run_key": product.run_key,
            "profile_code": product.profile_code,
            "engine_code": product.engine_code,
            "status": product.status,
            "health_status": product.health_status,
            "publish_dir": product.publish_dir,
            "manifest_path": product.manifest_path,
            "native_output_dir": product.native_output_dir,
            "primary_asset_path": product.primary_asset_path,
            "summary": product.summary_json or {},
            "tags": product.tags_json or {},
            "produced_at": product.produced_at.isoformat() if product.produced_at else None,
            "published_at": product.published_at.isoformat() if product.published_at else None,
            "registered_at": product.registered_at.isoformat() if product.registered_at else None,
        }

    def _serialize_asset(self, asset: ResultAssetORM) -> Dict[str, Any]:
        return {
            "id": asset.id,
            "role": asset.asset_role,
            "name": asset.asset_name,
            "relative_path": asset.relative_path,
            "absolute_path": asset.absolute_path,
            "format": asset.format,
            "media_type": asset.media_type,
            "is_required": asset.is_required,
            "is_primary": asset.is_primary,
            "exists": asset.exists_flag,
            "file_size": asset.file_size,
        }


landsar_lt1_production_service = LandsarLt1ProductionService()
