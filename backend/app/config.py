import os
from typing import Any, ClassVar

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_CURRENT_DIR)
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
_ENV_FILE_PATH = os.path.join(_PROJECT_ROOT, ".env")
_FRONTEND_ENV_FILE_PATH = os.path.join(_PROJECT_ROOT, "frontend", ".env")

_PROJECT_ENV_LOADED = False


def _windows_path_to_wsl_mount(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    drive, tail = os.path.splitdrive(os.path.normpath(text))
    if not drive:
        return text.replace("\\", "/")
    drive_letter = drive.rstrip(":").lower()
    normalized_tail = tail.replace("\\", "/")
    return f"/mnt/{drive_letter}/{normalized_tail}"


def _clean_path_text(value: str | None) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _infer_conda_env_name_from_python(path: str | None) -> str:
    text = _clean_path_text(path)
    marker = "/envs/"
    if not text or marker not in text:
        return ""
    tail = text.split(marker, 1)[1].strip("/")
    if not tail:
        return ""
    return tail.split("/", 1)[0].strip()


def _default_idl_runtime_dir() -> str:
    return os.path.join(_BACKEND_DIR, "runtime", "idl_worker")


def _resolve_idl_runtime_dir(value: str | None) -> str:
    fallback_dir = os.path.normpath(_default_idl_runtime_dir())
    text = _clean_path_text(value)
    if not text:
        return fallback_dir

    normalized = os.path.normpath(text)
    if normalized.startswith("\\\\"):
        return normalized

    drive, _tail = os.path.splitdrive(normalized)
    if drive:
        drive_root = drive + os.sep
        if not os.path.exists(drive_root):
            print(
                ">>> [Config] IDL_WORKER_RUNTIME_DIR root unavailable; "
                f"fallback to {fallback_dir} (configured: {normalized})"
            )
            return fallback_dir
        return normalized

    return os.path.normpath(os.path.abspath(normalized))


def _default_result_publish_root(project_root: str) -> str:
    normalized_root = os.path.normpath(project_root)
    drive, _tail = os.path.splitdrive(normalized_root)
    if drive:
        return os.path.join(drive + os.sep, "production_results")
    return os.path.join(normalized_root, "production_results")


def _read_env_pairs(env_path: str) -> dict[str, str]:
    env_map: dict[str, str] = {}
    if not os.path.isfile(env_path):
        return env_map
    with open(env_path, "r", encoding="utf-8-sig", errors="ignore") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            env_map[key] = value.strip().strip('"').strip("'")
    return env_map


def ensure_project_env_loaded(override: bool = False) -> str:
    global _PROJECT_ENV_LOADED

    if _PROJECT_ENV_LOADED and not override:
        return _ENV_FILE_PATH

    if not os.path.isfile(_ENV_FILE_PATH):
        _PROJECT_ENV_LOADED = True
        return _ENV_FILE_PATH

    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE_PATH, override=override)
    except Exception:
        for key, value in _read_env_pairs(_ENV_FILE_PATH).items():
            if override or key not in os.environ:
                os.environ[key] = value

    _PROJECT_ENV_LOADED = True
    return _ENV_FILE_PATH


ensure_project_env_loaded()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    _CURRENT_DIR: ClassVar[str] = _CURRENT_DIR
    BACKEND_DIR: ClassVar[str] = _BACKEND_DIR
    PROJECT_ROOT: ClassVar[str] = _PROJECT_ROOT
    ENV_FILE_PATH: ClassVar[str] = _ENV_FILE_PATH
    FRONTEND_ENV_FILE_PATH: ClassVar[str] = _FRONTEND_ENV_FILE_PATH
    CACHE_DIR: ClassVar[str] = os.path.join(_BACKEND_DIR, "image_cache")
    DINSAR_CACHE_DIR: ClassVar[str] = os.path.join(CACHE_DIR, "dinsar")
    RADAR_RAW_CACHE_DIR: ClassVar[str] = os.path.join(CACHE_DIR, "radar_raw")
    RADAR_GEO_CACHE_DIR: ClassVar[str] = os.path.join(CACHE_DIR, "radar_geo")
    RADAR_CACHE_DIR: ClassVar[str] = RADAR_RAW_CACHE_DIR
    COLORMAPS_DIR: ClassVar[str] = os.path.join(_BACKEND_DIR, "colormaps")
    MODEL_PATH: ClassVar[str] = os.path.join(_BACKEND_DIR, "quality_model.pkl")

    DATABASE_URL: str = ""
    PORT: int = 18000
    BACKEND_BIND_HOST: str = "127.0.0.1"
    UVICORN_LOG_LEVEL: str = "info"
    PYTHON_PATH: str = ""
    CONDA_EXE: str = ""
    CONDA_ENV_NAME: str = ""
    NGINX_PATH: str = ""
    NGINX_HEALTH_URL: str = "http://127.0.0.1/"

    CORS_ORIGINS: str = "*"
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_STRICT_MODE: bool = False

    LICENSE_PATH: str = ""
    INIT_ADMIN_USERNAME: str = "admin"
    INIT_ADMIN_PASSWORD: str = ""
    INIT_ADMIN_RESET_PASSWORD: bool = False
    AUTH_SESSION_COOKIE_NAME: str = "ims_session"
    AUTH_COOKIE_SAMESITE: str = "lax"
    AUTH_PBKDF2_ITERATIONS: int = 240000
    TRUSTED_PROXY_IPS: str = "127.0.0.1"
    ALLOWED_EXPORT_DIRS: str = ""
    DB_SCHEMA_RESET_ON_MISMATCH: bool = False
    DB_SCHEMA_RESET_CONFIRM: bool = False

    UNPACK_SOURCE_DIRS: str = ""
    INSAR_STORAGE_DIRS: str = ""
    MONITOR_RADAR_DIRS: str = ""
    MONITOR_DINSAR_DIRS: str = ""

    HAZARD_POINTS_DIR: str = ""
    HAZARD_POINTS_FILENAME: str = "Point.shp"
    AOI_REGION_INDEX_FILE: str = ""
    AOI_REGION_GEOJSON_FILE: str = ""

    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_API_URL: str = "http://127.0.0.1:11434/api/generate"
    DEFAULT_VLM_MODEL: str = "qwen3-vl:8b"

    DINSAR_THUMBNAIL_MAX_SIZE: int = 1600
    DINSAR_FOOTPRINT_MAX_SIZE: int = 1000
    DINSAR_CACHE_WORKERS: int = 4

    RADAR_THUMBNAIL_MAX_SIZE: int = 1600
    RADAR_CACHE_WORKERS: int = 2
    RADAR_GEO_CACHE_WORKERS: int = 2
    RADAR_GEO_CACHE_VERSION: str = "b1"
    RADAR_GEO_CACHE_QUALITY: int = 84
    RADAR_PREVIEW_BUILD_ON_DEMAND: bool = True

    WATER_RESULTS_DIR: str = ""

    SRTM_DEM_DIR: str = ""
    GF3_GEO_DEM_PATH: str = ""
    GF3_SOURCE_DIRS: str = ""
    GF3_STORAGE_DIRS: str = ""

    MONITOR_ORBIT_DIR: str = ""
    ORBIT_POOL_ENVI: str = ""
    ORBIT_POOL_ISCE2: str = ""
    ORBIT_POOL_LANDSAR: str = ""
    ORBIT_QUARANTINE_DIR: str = ""

    IDL_EXECUTABLE: str = r"C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idl.exe"
    IDL_WORKBENCH_PATH: str = r"C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlde.exe"
    IDL_WORKER_RUNTIME_DIR: str = ""
    IDL_WORKER_DEFAULT_TIMEOUT_SECONDS: int = 14400
    IDL_WORKER_MAX_TIMEOUT_SECONDS: int = 43200
    IDL_JOB_RETRY_DELAY_SECONDS: int = 180
    IDL_DINSAR_DEM_BASE_FILE: str = ""
    IDL_DINSAR_CUSTOM_TARGET_RESOLUTION_M: float = 10.0
    IDL_DINSAR_CUSTOM_FILTER_METHOD: str = "GOLDSTEIN"
    IDL_DINSAR_CUSTOM_UNWRAP_COH_THRESHOLD: float = 0.05
    IDL_DINSAR_CUSTOM_GCP_COH_THRESHOLD: float = 0.7
    IDL_DINSAR_CUSTOM_GCP_NUMBER: int = 100
    IDL_DINSAR_CUSTOM_GEOCODING_COH_THRESHOLD: float = 0.0
    IDL_DINSAR_CUSTOM_GEOCODING_PIXEL_SIZE_M: float = 10.0
    ENVI_TASK_TIMEOUT_SECONDS: int = 300
    ENVI_FILE_STALE_SECONDS: int = 600
    ENVI_STABILITY_CHECK_INTERVAL: int = 15
    ENVI_STABILITY_ROUNDS: int = 3
    ENVI_STABILITY_MAX_WAIT: int = 3600
    ENVI_PER_TASK_TIMEOUT: int = 21600

    RESULT_PUBLISH_ROOT: str = ""
    DINSAR_PRODUCT_DIR: str = ""
    TIMESERIES_PRODUCT_DIR: str = ""
    PSINSAR_PRODUCT_DIR: str = ""
    RESULT_QUARANTINE_ROOT: str = ""
    RESULT_CATALOG_AUTO_REBUILD_ON_STARTUP: bool = True

    WSL_DISTRO: str = ""
    WSL_SHARED_CONDA_ENV: str = ""
    WSL_SHARED_PYTHON: str = ""
    WSL_BROKER_JOB_ROOT: str = ""
    ISCE2_RUNTIME_ID: str = ""
    PYINT_RUNTIME_ID: str = ""

    ISCE2_ENABLED: bool = False
    ISCE2_WSL_DISTRO: str = "Ubuntu-24.04"
    ISCE2_PYTHON: str = "/home/administrator/miniconda3/envs/isce2/bin/python"
    ISCE2_PROFILE: str = "lt1_stripmap"
    ISCE2_DEM_PATH: str = "D:\\SRTM30m\\SRTMDEM_RSP_SARscape.wgs84"
    ISCE2_WORK_ROOT: str = ""
    ISCE2_OUTPUT_ROOT: str = ""
    ISCE2_PER_TASK_TIMEOUT_SECONDS: int = 43200
    ISCE2_SMOKE_TEST_ENABLED: bool = False
    ISCE2_STRIPMAP_APP: str = (
        "/home/administrator/miniconda3/envs/isce2/lib/python3.11/"
        "site-packages/isce/applications/stripmapApp.py"
    )
    ISCE2_PIPELINE_SCRIPT: str = ""
    PYINT_ENABLED: bool = False
    PYINT_WSL_DISTRO: str = ""
    PYINT_WSL_PYTHON: str = ""
    PYINT_HOME: str = ""
    PYINT_APP_SCRIPT: str = ""
    PYINT_TEMPLATE_ROOT: str = ""
    PYINT_WORK_ROOT: str = ""
    PYINT_OUTPUT_ROOT: str = ""
    PYINT_DEM_ROOT: str = ""
    PYINT_DEM_MODE: str = "local_fabdem"
    PYINT_FABDEM_ROOT: str = ""
    PYINT_PREPARED_DEM_PATH: str = ""
    PYINT_DEM_RESOLUTION_M: float = 30.0
    PYINT_OPENTOPO_DEM_TYPE: str = "SRTMGL1"
    PYINT_OPENTOPO_API_KEY: str = ""
    PYINT_DEM_STRICT: bool = True
    PYINT_UNWRAP_COH_THRESHOLD: float = 0.05
    PYINT_PRODUCT_COH_THRESHOLD: float = 0.20
    PYINT_REFERENCE_MODE: str = "none"
    PYINT_REFERENCE_COH_THRESHOLD: float = 0.30
    PYINT_DERAMP_MODE: str = "none"
    PYINT_DERAMP_COH_THRESHOLD: float = 0.30
    PYINT_GAMMA_NODATA_VALUE: float = -9999.0
    PYINT_GEO_INTERP: str = "1"
    PYINT_ATMCOR_ENABLED: bool = False
    PYINT_ATMCOR_USE_FOR_DISP: bool = False
    PYINT_REFLATTEN_ENABLED: bool = True
    PYINT_REFLATTEN_MODEL: str = "plane"
    PYINT_REFLATTEN_COH_THRESHOLD: float = 0.70
    PYINT_REFLATTEN_FALLBACK_COH_THRESHOLD: float = 0.20
    PYINT_REFLATTEN_RANGE_STEP: int = 32
    PYINT_REFLATTEN_AZIMUTH_STEP: int = 32
    PYINT_ORBIT_POLICY: str = "require_txt"
    PYINT_ORBIT_POOL_TXT: str = ""
    PYINT_RECORD_INPUT_ASSETS: bool = True
    PYINT_LT1_PRECISE_ORBIT_ENABLED: bool = True
    PYINT_LT1_PRECISE_ORBIT_MODE: str = "replace"
    PYINT_LT1_PRECISE_ORBIT_STRICT: bool = True
    PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT: bool = False
    PYINT_LT1_PRECISE_ORBIT_BACKUP: bool = True
    PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE: int = 5
    PYINT_GAMMA_ENV_SCRIPT: str = ""
    PYINT_DEFAULT_TIMEOUT_SECONDS: int = 43200
    PYINT_SMOKE_TEST_ENABLED: bool = False
    JOB_WORKER_HEALTH_TIMEOUT: int = 60
    JOB_WORKER_JOB_HEARTBEAT_INTERVAL: float = 5.0
    JOB_WORKER_STALE_RECOVER_INTERVAL: float = 15.0
    JOB_WORKER_STALE_RUNNING_SECONDS: int = 300
    JOB_WORKER_HEARTBEAT_INTERVAL: float = 5.0

    TIMESERIES_ENABLED: bool = True
    TIMESERIES_WSL_DISTRO: str = ""
    TIMESERIES_PYTHON: str = ""
    TIMESERIES_ENV_NAME: str = ""
    TIMESERIES_WORK_ROOT: str = ""
    TIMESERIES_DEM_PATH: str = ""
    TIMESERIES_ORBIT_POOL_ISCE2: str = ""
    TIMESERIES_EXPERIMENT_ROOT: str = ""
    TIMESERIES_STACK_PREP_SCRIPT: str = ""
    TIMESERIES_MATERIALIZE_SCRIPT: str = ""
    TIMESERIES_PREPARE_DEM_SCRIPT: str = ""
    TIMESERIES_STACK_RUNNER_SCRIPT: str = ""
    TIMESERIES_MINTPY_SBAS_SCRIPT: str = ""
    TIMESERIES_EXPORT_PUBLISH_SCRIPT: str = ""
    TIMESERIES_STACK_WORKFLOW: str = "interferogram"
    TIMESERIES_DEFAULT_PROCESSOR_CODE: str = "isce2_stack_mintpy"
    TIMESERIES_WSL_STEP_TIMEOUT_SECONDS: int = 7200
    TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK: bool = True
    SARSCAPE_SBAS_PARAMETER_TEMPLATE_PATH: str = ""
    SARSCAPE_SBAS_ALLOW_EXECUTION: bool = False
    SARSCAPE_SBAS_DISCOVERY_TIMEOUT_SECONDS: int = 120
    SARSCAPE_SBAS_STEP_TIMEOUT_SECONDS: int = 21600

    @model_validator(mode="after")
    def _set_path_defaults(self) -> "Settings":
        backend_dir = type(self).BACKEND_DIR
        project_root = type(self).PROJECT_ROOT

        if not self.HAZARD_POINTS_DIR:
            object.__setattr__(self, "HAZARD_POINTS_DIR", os.path.join(backend_dir, "Point"))
        if not self.AOI_REGION_INDEX_FILE:
            object.__setattr__(
                self,
                "AOI_REGION_INDEX_FILE",
                os.path.join(backend_dir, "geojson", "层级映射.json"),
            )
        if not self.AOI_REGION_GEOJSON_FILE:
            object.__setattr__(
                self,
                "AOI_REGION_GEOJSON_FILE",
                os.path.join(backend_dir, "geojson", "全国行政区.geojson"),
            )
        if not self.WATER_RESULTS_DIR:
            object.__setattr__(
                self,
                "WATER_RESULTS_DIR",
                os.path.join(backend_dir, "water_results"),
            )
        if not self.SRTM_DEM_DIR:
            object.__setattr__(self, "SRTM_DEM_DIR", os.path.join(backend_dir, "dem_data"))
        if not self.GF3_STORAGE_DIRS:
            object.__setattr__(
                self,
                "GF3_STORAGE_DIRS",
                os.path.join(backend_dir, "gf3_results"),
            )
        if not self.ORBIT_QUARANTINE_DIR and self.MONITOR_ORBIT_DIR:
            object.__setattr__(
                self,
                "ORBIT_QUARANTINE_DIR",
                os.path.join(self.MONITOR_ORBIT_DIR, "_quarantine"),
            )
        object.__setattr__(
            self,
            "IDL_WORKER_RUNTIME_DIR",
            _resolve_idl_runtime_dir(self.IDL_WORKER_RUNTIME_DIR),
        )
        if not self.RESULT_PUBLISH_ROOT:
            object.__setattr__(
                self,
                "RESULT_PUBLISH_ROOT",
                _default_result_publish_root(project_root),
            )
        if not self.DINSAR_PRODUCT_DIR:
            object.__setattr__(
                self,
                "DINSAR_PRODUCT_DIR",
                os.path.join(self.RESULT_PUBLISH_ROOT, "dinsar"),
            )
        timeseries_product_dir = (
            _clean_path_text(self.TIMESERIES_PRODUCT_DIR)
            or _clean_path_text(self.PSINSAR_PRODUCT_DIR)
            or os.path.join(self.RESULT_PUBLISH_ROOT, "timeseries")
        )
        object.__setattr__(
            self,
            "TIMESERIES_PRODUCT_DIR",
            os.path.normpath(timeseries_product_dir),
        )
        if not self.PSINSAR_PRODUCT_DIR or _clean_path_text(self.PSINSAR_PRODUCT_DIR) != self.TIMESERIES_PRODUCT_DIR:
            object.__setattr__(
                self,
                "PSINSAR_PRODUCT_DIR",
                self.TIMESERIES_PRODUCT_DIR,
            )
        if not self.RESULT_QUARANTINE_ROOT:
            object.__setattr__(
                self,
                "RESULT_QUARANTINE_ROOT",
                os.path.join(self.RESULT_PUBLISH_ROOT, "_quarantine"),
            )
        if not self.ISCE2_WORK_ROOT:
            object.__setattr__(
                self,
                "ISCE2_WORK_ROOT",
                os.path.join(backend_dir, "runtime", "isce2_work"),
            )
        if not self.ISCE2_OUTPUT_ROOT:
            object.__setattr__(
                self,
                "ISCE2_OUTPUT_ROOT",
                self.DINSAR_PRODUCT_DIR,
            )
        if not self.ISCE2_PIPELINE_SCRIPT:
            local_pipeline = os.path.join(
                project_root,
                "backend",
                "app",
                "isce2_pipeline",
                "run_lt1_dinsar_pipeline.py",
            )
            object.__setattr__(
                self,
                "ISCE2_PIPELINE_SCRIPT",
                _windows_path_to_wsl_mount(local_pipeline),
            )
        if not self.WSL_DISTRO:
            fallback_distro = str(
                self.ISCE2_WSL_DISTRO
                or self.PYINT_WSL_DISTRO
                or "Ubuntu-24.04"
            ).strip()
            object.__setattr__(self, "WSL_DISTRO", fallback_distro)
        if not self.WSL_SHARED_CONDA_ENV:
            shared_conda_env = (
                _infer_conda_env_name_from_python(self.WSL_SHARED_PYTHON)
                or _infer_conda_env_name_from_python(self.ISCE2_PYTHON)
                or _infer_conda_env_name_from_python(self.PYINT_WSL_PYTHON)
                or "insar_wsl_v1"
            )
            object.__setattr__(self, "WSL_SHARED_CONDA_ENV", shared_conda_env)
        if not self.WSL_SHARED_PYTHON:
            shared_python = (
                _clean_path_text(self.ISCE2_PYTHON)
                or _clean_path_text(self.PYINT_WSL_PYTHON)
                or (
                    f"/home/administrator/miniconda3/envs/"
                    f"{self.WSL_SHARED_CONDA_ENV}/bin/python"
                )
            )
            object.__setattr__(self, "WSL_SHARED_PYTHON", shared_python)
        if not self.WSL_BROKER_JOB_ROOT:
            object.__setattr__(
                self,
                "WSL_BROKER_JOB_ROOT",
                os.path.join(backend_dir, "runtime", "wsl_jobs"),
            )
        if not self.ISCE2_RUNTIME_ID:
            object.__setattr__(self, "ISCE2_RUNTIME_ID", "isce2_runtime_v1")
        if not self.PYINT_RUNTIME_ID:
            object.__setattr__(self, "PYINT_RUNTIME_ID", "gamma_pyint_runtime_v1")
        if not self.PYINT_WSL_DISTRO:
            object.__setattr__(self, "PYINT_WSL_DISTRO", self.WSL_DISTRO or self.ISCE2_WSL_DISTRO)
        if not self.PYINT_WSL_PYTHON:
            object.__setattr__(self, "PYINT_WSL_PYTHON", self.WSL_SHARED_PYTHON or self.ISCE2_PYTHON)
        if not self.PYINT_HOME:
            object.__setattr__(
                self,
                "PYINT_HOME",
                os.path.join(project_root, "third_party", "PyINT"),
            )
        if not self.PYINT_APP_SCRIPT and self.PYINT_HOME:
            object.__setattr__(
                self,
                "PYINT_APP_SCRIPT",
                os.path.join(self.PYINT_HOME, "pyint", "pyintApp.py"),
            )
        if not self.PYINT_TEMPLATE_ROOT:
            object.__setattr__(
                self,
                "PYINT_TEMPLATE_ROOT",
                os.path.join(backend_dir, "runtime", "pyint_templates"),
            )
        if not self.PYINT_WORK_ROOT:
            object.__setattr__(
                self,
                "PYINT_WORK_ROOT",
                os.path.join(backend_dir, "runtime", "pyint_work"),
            )
        if not self.PYINT_OUTPUT_ROOT:
            object.__setattr__(
                self,
                "PYINT_OUTPUT_ROOT",
                self.DINSAR_PRODUCT_DIR,
            )
        if not self.PYINT_DEM_ROOT:
            object.__setattr__(
                self,
                "PYINT_DEM_ROOT",
                os.path.join(backend_dir, "runtime", "pyint_dem"),
            )
        pyint_dem_mode = str(self.PYINT_DEM_MODE or "local_fabdem").strip().lower() or "local_fabdem"
        if pyint_dem_mode not in {"local_fabdem", "opentopo", "prepared_file"}:
            pyint_dem_mode = "local_fabdem"
        object.__setattr__(self, "PYINT_DEM_MODE", pyint_dem_mode)
        object.__setattr__(self, "PYINT_DEM_RESOLUTION_M", max(0.1, float(self.PYINT_DEM_RESOLUTION_M or 30.0)))
        object.__setattr__(
            self,
            "PYINT_UNWRAP_COH_THRESHOLD",
            min(1.0, max(0.0, float(self.PYINT_UNWRAP_COH_THRESHOLD or 0.05))),
        )
        object.__setattr__(
            self,
            "PYINT_PRODUCT_COH_THRESHOLD",
            min(1.0, max(0.0, float(self.PYINT_PRODUCT_COH_THRESHOLD or 0.20))),
        )
        pyint_reference_mode = str(self.PYINT_REFERENCE_MODE or "none").strip().lower() or "none"
        if pyint_reference_mode not in {"none", "coh_median"}:
            pyint_reference_mode = "none"
        object.__setattr__(self, "PYINT_REFERENCE_MODE", pyint_reference_mode)
        object.__setattr__(
            self,
            "PYINT_REFERENCE_COH_THRESHOLD",
            min(1.0, max(0.0, float(self.PYINT_REFERENCE_COH_THRESHOLD or 0.30))),
        )
        pyint_deramp_mode = str(self.PYINT_DERAMP_MODE or "none").strip().lower() or "none"
        if pyint_deramp_mode not in {"none", "plane"}:
            pyint_deramp_mode = "none"
        object.__setattr__(self, "PYINT_DERAMP_MODE", pyint_deramp_mode)
        object.__setattr__(
            self,
            "PYINT_DERAMP_COH_THRESHOLD",
            min(1.0, max(0.0, float(self.PYINT_DERAMP_COH_THRESHOLD or 0.30))),
        )
        object.__setattr__(
            self,
            "PYINT_GAMMA_NODATA_VALUE",
            float(self.PYINT_GAMMA_NODATA_VALUE if self.PYINT_GAMMA_NODATA_VALUE is not None else -9999.0),
        )
        pyint_geo_interp = str(self.PYINT_GEO_INTERP or "0").strip()
        if pyint_geo_interp not in {"0", "1"}:
            pyint_geo_interp = "1"
        object.__setattr__(self, "PYINT_GEO_INTERP", pyint_geo_interp)
        pyint_reflatten_model = str(self.PYINT_REFLATTEN_MODEL or "plane").strip().lower() or "plane"
        if pyint_reflatten_model in {"linear"}:
            pyint_reflatten_model = "plane"
        if pyint_reflatten_model not in {"plane", "quadratic"}:
            pyint_reflatten_model = "plane"
        object.__setattr__(self, "PYINT_REFLATTEN_MODEL", pyint_reflatten_model)
        object.__setattr__(
            self,
            "PYINT_REFLATTEN_COH_THRESHOLD",
            min(1.0, max(0.0, float(self.PYINT_REFLATTEN_COH_THRESHOLD or 0.70))),
        )
        object.__setattr__(
            self,
            "PYINT_REFLATTEN_FALLBACK_COH_THRESHOLD",
            min(1.0, max(0.0, float(self.PYINT_REFLATTEN_FALLBACK_COH_THRESHOLD or 0.20))),
        )
        object.__setattr__(
            self,
            "PYINT_REFLATTEN_RANGE_STEP",
            max(1, int(self.PYINT_REFLATTEN_RANGE_STEP or 32)),
        )
        object.__setattr__(
            self,
            "PYINT_REFLATTEN_AZIMUTH_STEP",
            max(1, int(self.PYINT_REFLATTEN_AZIMUTH_STEP or 32)),
        )
        if not self.PYINT_OPENTOPO_DEM_TYPE:
            object.__setattr__(self, "PYINT_OPENTOPO_DEM_TYPE", "SRTMGL1")
        pyint_orbit_policy = str(self.PYINT_ORBIT_POLICY or "require_txt").strip().lower() or "require_txt"
        if pyint_orbit_policy not in {"validate_only", "require_txt", "stage_txt"}:
            pyint_orbit_policy = "require_txt"
        object.__setattr__(self, "PYINT_ORBIT_POLICY", pyint_orbit_policy)
        pyint_precise_orbit_mode = str(self.PYINT_LT1_PRECISE_ORBIT_MODE or "replace").strip().lower() or "replace"
        if pyint_precise_orbit_mode not in {"replace", "replace_and_validate"}:
            pyint_precise_orbit_mode = "replace"
        object.__setattr__(self, "PYINT_LT1_PRECISE_ORBIT_MODE", pyint_precise_orbit_mode)
        object.__setattr__(
            self,
            "PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE",
            max(1, int(self.PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE or 5)),
        )
        if not self.PYINT_ORBIT_POOL_TXT:
            object.__setattr__(self, "PYINT_ORBIT_POOL_TXT", self.ORBIT_POOL_ENVI)
        if not self.TIMESERIES_WSL_DISTRO:
            object.__setattr__(self, "TIMESERIES_WSL_DISTRO", self.WSL_DISTRO or self.ISCE2_WSL_DISTRO)
        if not self.TIMESERIES_ENV_NAME:
            object.__setattr__(
                self,
                "TIMESERIES_ENV_NAME",
                str(self.WSL_SHARED_CONDA_ENV or "insar_wsl_v1").strip() or "insar_wsl_v1",
            )
        if not self.TIMESERIES_PYTHON:
            shared_python = str(self.WSL_SHARED_PYTHON or "").strip()
            if shared_python:
                object.__setattr__(self, "TIMESERIES_PYTHON", shared_python)
            else:
                env_name = (
                    str(self.TIMESERIES_ENV_NAME or "insar_wsl_v1").strip()
                    or "insar_wsl_v1"
                )
                object.__setattr__(
                    self,
                    "TIMESERIES_PYTHON",
                    f"/home/administrator/miniconda3/envs/{env_name}/bin/python",
                )
        if not self.TIMESERIES_WORK_ROOT:
            object.__setattr__(
                self,
                "TIMESERIES_WORK_ROOT",
                os.path.join(backend_dir, "runtime", "timeseries_work"),
            )
        if not self.TIMESERIES_DEM_PATH:
            object.__setattr__(self, "TIMESERIES_DEM_PATH", self.ISCE2_DEM_PATH)
        if not self.TIMESERIES_ORBIT_POOL_ISCE2:
            object.__setattr__(self, "TIMESERIES_ORBIT_POOL_ISCE2", self.ORBIT_POOL_ISCE2)
        if not self.TIMESERIES_EXPERIMENT_ROOT:
            object.__setattr__(
                self,
                "TIMESERIES_EXPERIMENT_ROOT",
                os.path.join(project_root, "experiments", "isce2_sbas_timeseries"),
            )
        if not self.TIMESERIES_STACK_PREP_SCRIPT:
            object.__setattr__(
                self,
                "TIMESERIES_STACK_PREP_SCRIPT",
                os.path.join(
                    self.TIMESERIES_EXPERIMENT_ROOT,
                    "scripts",
                    "build_lt1_stack_prep.py",
                ),
            )
        if not self.TIMESERIES_MATERIALIZE_SCRIPT:
            object.__setattr__(
                self,
                "TIMESERIES_MATERIALIZE_SCRIPT",
                os.path.join(
                    self.TIMESERIES_EXPERIMENT_ROOT,
                    "scripts",
                    "materialize_lt1_stack_scenes.py",
                ),
            )
        if not self.TIMESERIES_PREPARE_DEM_SCRIPT:
            object.__setattr__(
                self,
                "TIMESERIES_PREPARE_DEM_SCRIPT",
                os.path.join(
                    self.TIMESERIES_EXPERIMENT_ROOT,
                    "scripts",
                    "prepare_lt1_stack_dem.py",
                ),
            )
        if not self.TIMESERIES_STACK_RUNNER_SCRIPT:
            object.__setattr__(
                self,
                "TIMESERIES_STACK_RUNNER_SCRIPT",
                os.path.join(
                    self.TIMESERIES_EXPERIMENT_ROOT,
                    "scripts",
                    "run_generated_stack_runfile_ubuntu2404.sh",
                ),
            )
        if not self.TIMESERIES_MINTPY_SBAS_SCRIPT:
            object.__setattr__(
                self,
                "TIMESERIES_MINTPY_SBAS_SCRIPT",
                os.path.join(
                    self.TIMESERIES_EXPERIMENT_ROOT,
                    "scripts",
                    "run_mintpy_sbas_unified_env_smoketest_ubuntu2404.sh",
                ),
            )
        if not self.TIMESERIES_EXPORT_PUBLISH_SCRIPT:
            object.__setattr__(
                self,
                "TIMESERIES_EXPORT_PUBLISH_SCRIPT",
                os.path.join(
                    self.TIMESERIES_EXPERIMENT_ROOT,
                    "scripts",
                    "export_mintpy_publish_products_unified_env_ubuntu2404.sh",
                ),
            )
        if not self.SARSCAPE_SBAS_PARAMETER_TEMPLATE_PATH:
            object.__setattr__(
                self,
                "SARSCAPE_SBAS_PARAMETER_TEMPLATE_PATH",
                os.path.join(
                    backend_dir,
                    "templates",
                    "sarscape_sbas_parameter_template.example.json",
                ),
            )
        return self

    @staticmethod
    def ensure_dirs() -> None:
        os.makedirs(Settings.CACHE_DIR, exist_ok=True)
        os.makedirs(Settings.DINSAR_CACHE_DIR, exist_ok=True)
        os.makedirs(Settings.RADAR_RAW_CACHE_DIR, exist_ok=True)
        os.makedirs(Settings.RADAR_GEO_CACHE_DIR, exist_ok=True)
        os.makedirs(Settings.COLORMAPS_DIR, exist_ok=True)
        os.makedirs(settings.IDL_WORKER_RUNTIME_DIR, exist_ok=True)
        os.makedirs(settings.RESULT_PUBLISH_ROOT, exist_ok=True)
        os.makedirs(settings.DINSAR_PRODUCT_DIR, exist_ok=True)
        os.makedirs(settings.TIMESERIES_PRODUCT_DIR, exist_ok=True)
        os.makedirs(settings.PSINSAR_PRODUCT_DIR, exist_ok=True)
        os.makedirs(settings.RESULT_QUARANTINE_ROOT, exist_ok=True)
        os.makedirs(settings.WSL_BROKER_JOB_ROOT, exist_ok=True)
        os.makedirs(settings.PYINT_TEMPLATE_ROOT, exist_ok=True)
        os.makedirs(settings.PYINT_WORK_ROOT, exist_ok=True)
        os.makedirs(settings.PYINT_OUTPUT_ROOT, exist_ok=True)
        os.makedirs(settings.PYINT_DEM_ROOT, exist_ok=True)
        os.makedirs(settings.TIMESERIES_WORK_ROOT, exist_ok=True)


settings = Settings()


def split_env_paths(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.replace(";", ",").split(",") if item.strip()]


def get_env_text(name: str, default: str | None = None) -> str | None:
    if hasattr(settings, name):
        value = getattr(settings, name)
        if value is not None:
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, str) and value == "":
                return default
            return str(value)

    value = os.getenv(name)
    if value is None:
        return default
    return value


def read_bool_env(name: str, default: bool = False) -> bool:
    if hasattr(settings, name):
        value = getattr(settings, name)
        if isinstance(value, bool):
            return value

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def read_int_env(name: str, default: int, minimum: int = 1, maximum: int = 2**31 - 1) -> int:
    if hasattr(settings, name):
        value = getattr(settings, name)
        if isinstance(value, int) and not isinstance(value, bool):
            return min(maximum, max(minimum, value))

    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _is_wsl_posix_path(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith("/home/") or text.startswith("/mnt/")


def _check_path(
    *,
    label: str,
    value: str,
    errors: list[str],
    warnings: list[str],
    required: bool = False,
    expect_file: bool | None = None,
) -> None:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        if required:
            errors.append(f"{label} 未配置。")
        return

    if _is_wsl_posix_path(text):
        if not text:
            warnings.append(f"{label} 为空。")
        return

    exists = os.path.exists(text)
    if not exists:
        target = "文件" if expect_file else "路径"
        warnings.append(f"{label} 指向的{target}不存在或当前机器不可访问: {text}")
        return

    if expect_file is True and not os.path.isfile(text):
        warnings.append(f"{label} 期望是文件，但当前不是文件: {text}")
    if expect_file is False and not os.path.isdir(text):
        warnings.append(f"{label} 期望是目录，但当前不是目录: {text}")


def validate_runtime_config() -> dict[str, Any]:
    ensure_project_env_loaded()

    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    if not os.path.isfile(_ENV_FILE_PATH):
        errors.append(f"根 .env 不存在: {_ENV_FILE_PATH}")
    else:
        try:
            with open(_ENV_FILE_PATH, "r", encoding="utf-8-sig") as stream:
                stream.read()
            info.append(f"根 .env: {_ENV_FILE_PATH}")
        except UnicodeDecodeError:
            warnings.append(f"根 .env 不是标准 UTF-8 编码，建议转为 UTF-8: {_ENV_FILE_PATH}")

    if os.path.isfile(_FRONTEND_ENV_FILE_PATH):
        root_env_map = _read_env_pairs(_ENV_FILE_PATH)
        frontend_env_map = _read_env_pairs(_FRONTEND_ENV_FILE_PATH)
        conflicting_vite_keys = [
            key
            for key in frontend_env_map
            if key.startswith("VITE_")
            and key in root_env_map
            and frontend_env_map[key] != root_env_map[key]
        ]
        if conflicting_vite_keys:
            warnings.append(
                "检测到根 .env 与 frontend/.env 存在冲突的 VITE_* 配置："
                + ", ".join(conflicting_vite_keys)
                + "。前端构建将优先采用根 .env。"
            )

    if not settings.DATABASE_URL:
        errors.append("DATABASE_URL 未配置。")
    else:
        info.append("DATABASE_URL 已配置。")

    _check_path(label="PYTHON_PATH", value=settings.PYTHON_PATH, errors=errors, warnings=warnings, expect_file=True)
    _check_path(label="NGINX_PATH", value=settings.NGINX_PATH, errors=errors, warnings=warnings, required=True, expect_file=True)
    _check_path(label="LICENSE_PATH", value=settings.LICENSE_PATH, errors=errors, warnings=warnings, expect_file=True)
    _check_path(label="IDL_EXECUTABLE", value=settings.IDL_EXECUTABLE, errors=errors, warnings=warnings, expect_file=True)
    _check_path(label="IDL_WORKBENCH_PATH", value=settings.IDL_WORKBENCH_PATH, errors=errors, warnings=warnings, expect_file=True)
    _check_path(label="GF3_GEO_DEM_PATH", value=settings.GF3_GEO_DEM_PATH, errors=errors, warnings=warnings, expect_file=True)
    _check_path(label="SRTM_DEM_DIR", value=settings.SRTM_DEM_DIR, errors=errors, warnings=warnings, expect_file=False)
    _check_path(label="WATER_RESULTS_DIR", value=settings.WATER_RESULTS_DIR, errors=errors, warnings=warnings, expect_file=False)
    _check_path(label="MONITOR_ORBIT_DIR", value=settings.MONITOR_ORBIT_DIR, errors=errors, warnings=warnings, expect_file=False)
    _check_path(label="ORBIT_POOL_ENVI", value=settings.ORBIT_POOL_ENVI, errors=errors, warnings=warnings, expect_file=False)
    _check_path(label="ORBIT_POOL_ISCE2", value=settings.ORBIT_POOL_ISCE2, errors=errors, warnings=warnings, expect_file=False)
    _check_path(label="RESULT_PUBLISH_ROOT", value=settings.RESULT_PUBLISH_ROOT, errors=errors, warnings=warnings, expect_file=False)
    _check_path(label="DINSAR_PRODUCT_DIR", value=settings.DINSAR_PRODUCT_DIR, errors=errors, warnings=warnings, expect_file=False)
    _check_path(
        label="TIMESERIES_PRODUCT_DIR",
        value=settings.TIMESERIES_PRODUCT_DIR,
        errors=errors,
        warnings=warnings,
        expect_file=False,
    )
    _check_path(label="RESULT_QUARANTINE_ROOT", value=settings.RESULT_QUARANTINE_ROOT, errors=errors, warnings=warnings, expect_file=False)

    for label, raw_value in (
        ("UNPACK_SOURCE_DIRS", settings.UNPACK_SOURCE_DIRS),
        ("INSAR_STORAGE_DIRS", settings.INSAR_STORAGE_DIRS),
        ("MONITOR_RADAR_DIRS", settings.MONITOR_RADAR_DIRS),
        ("MONITOR_DINSAR_DIRS", settings.MONITOR_DINSAR_DIRS),
        ("GF3_SOURCE_DIRS", settings.GF3_SOURCE_DIRS),
        ("GF3_STORAGE_DIRS", settings.GF3_STORAGE_DIRS),
    ):
        values = split_env_paths(raw_value)
        if not values:
            warnings.append(f"{label} 未配置。")
            continue
        for item in values:
            _check_path(label=label, value=item, errors=errors, warnings=warnings, expect_file=False)

    if settings.ISCE2_ENABLED:
        if not settings.ISCE2_WSL_DISTRO:
            errors.append("ISCE2_ENABLED=true 但 ISCE2_WSL_DISTRO 未配置。")
        if not settings.ISCE2_PYTHON:
            errors.append("ISCE2_ENABLED=true 但 ISCE2_PYTHON 未配置。")
        if not settings.ISCE2_PIPELINE_SCRIPT:
            errors.append("ISCE2_ENABLED=true 但 ISCE2_PIPELINE_SCRIPT 未配置。")
        if not settings.ORBIT_POOL_ISCE2:
            warnings.append("ISCE2_ENABLED=true 但 ORBIT_POOL_ISCE2 未配置。")

    if settings.PYINT_ENABLED:
        if not settings.PYINT_WSL_DISTRO:
            errors.append("PYINT_ENABLED=true but PYINT_WSL_DISTRO is not configured.")
        if not settings.PYINT_WSL_PYTHON:
            errors.append("PYINT_ENABLED=true but PYINT_WSL_PYTHON is not configured.")
        if not settings.PYINT_HOME:
            errors.append("PYINT_ENABLED=true but PYINT_HOME is not configured.")
        if not settings.PYINT_APP_SCRIPT:
            errors.append("PYINT_ENABLED=true but PYINT_APP_SCRIPT is not configured.")
        _check_path(
            label="PYINT_HOME",
            value=settings.PYINT_HOME,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="PYINT_APP_SCRIPT",
            value=settings.PYINT_APP_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="PYINT_TEMPLATE_ROOT",
            value=settings.PYINT_TEMPLATE_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="PYINT_WORK_ROOT",
            value=settings.PYINT_WORK_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="PYINT_OUTPUT_ROOT",
            value=settings.PYINT_OUTPUT_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="PYINT_DEM_ROOT",
            value=settings.PYINT_DEM_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        if settings.PYINT_DEM_MODE == "local_fabdem":
            _check_path(
                label="PYINT_FABDEM_ROOT",
                value=settings.PYINT_FABDEM_ROOT,
                errors=errors,
                warnings=warnings,
                expect_file=False,
            )
        elif settings.PYINT_DEM_MODE == "prepared_file":
            _check_path(
                label="PYINT_PREPARED_DEM_PATH",
                value=(
                    settings.PYINT_PREPARED_DEM_PATH
                    or settings.ISCE2_DEM_PATH
                    or settings.IDL_DINSAR_DEM_BASE_FILE
                ),
                errors=errors,
                warnings=warnings,
                expect_file=True,
            )
        _check_path(
            label="PYINT_ORBIT_POOL_TXT",
            value=settings.PYINT_ORBIT_POOL_TXT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="PYINT_GAMMA_ENV_SCRIPT",
            value=settings.PYINT_GAMMA_ENV_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )

    if settings.ISCE2_ENABLED or settings.PYINT_ENABLED:
        info.append(
            "WSL shared runtime: "
            f"distro={settings.WSL_DISTRO or '<empty>'}, "
            f"conda_env={settings.WSL_SHARED_CONDA_ENV or '<empty>'}, "
            f"isce2_runtime={settings.ISCE2_RUNTIME_ID}, "
            f"pyint_runtime={settings.PYINT_RUNTIME_ID}"
        )
        _check_path(
            label="WSL_BROKER_JOB_ROOT",
            value=settings.WSL_BROKER_JOB_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        if settings.ISCE2_PYTHON and settings.WSL_SHARED_PYTHON:
            isce_python = _clean_path_text(settings.ISCE2_PYTHON)
            shared_python = _clean_path_text(settings.WSL_SHARED_PYTHON)
            if isce_python != shared_python:
                warnings.append(
                    "ISCE2_PYTHON differs from WSL_SHARED_PYTHON. "
                    "Legacy execution path and new shared runtime are not aligned."
                )
        if settings.PYINT_WSL_PYTHON and settings.WSL_SHARED_PYTHON:
            pyint_python = _clean_path_text(settings.PYINT_WSL_PYTHON)
            shared_python = _clean_path_text(settings.WSL_SHARED_PYTHON)
            if pyint_python != shared_python:
                warnings.append(
                    "PYINT_WSL_PYTHON differs from WSL_SHARED_PYTHON. "
                    "Gamma/PyINT still depends on a legacy Python path override."
                )
        if settings.PYINT_GAMMA_ENV_SCRIPT:
            warnings.append(
                "PYINT_GAMMA_ENV_SCRIPT is still configured. "
                "Gamma runtime has not been fully migrated to the fixed profile model."
            )

    if settings.TIMESERIES_ENABLED:
        _check_path(
            label="TIMESERIES_PYTHON",
            value=settings.TIMESERIES_PYTHON,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_WORK_ROOT",
            value=settings.TIMESERIES_WORK_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="TIMESERIES_EXPERIMENT_ROOT",
            value=settings.TIMESERIES_EXPERIMENT_ROOT,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )
        _check_path(
            label="TIMESERIES_STACK_PREP_SCRIPT",
            value=settings.TIMESERIES_STACK_PREP_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_MATERIALIZE_SCRIPT",
            value=settings.TIMESERIES_MATERIALIZE_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_PREPARE_DEM_SCRIPT",
            value=settings.TIMESERIES_PREPARE_DEM_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_STACK_RUNNER_SCRIPT",
            value=settings.TIMESERIES_STACK_RUNNER_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_MINTPY_SBAS_SCRIPT",
            value=settings.TIMESERIES_MINTPY_SBAS_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_EXPORT_PUBLISH_SCRIPT",
            value=settings.TIMESERIES_EXPORT_PUBLISH_SCRIPT,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_DEM_PATH",
            value=settings.TIMESERIES_DEM_PATH,
            errors=errors,
            warnings=warnings,
            expect_file=True,
        )
        _check_path(
            label="TIMESERIES_ORBIT_POOL_ISCE2",
            value=settings.TIMESERIES_ORBIT_POOL_ISCE2,
            errors=errors,
            warnings=warnings,
            expect_file=False,
        )

    return {
        "ok": not errors,
        "env_file": _ENV_FILE_PATH,
        "frontend_env_file": _FRONTEND_ENV_FILE_PATH,
        "errors": errors,
        "warnings": warnings,
        "info": info,
    }


def export_launcher_config() -> dict[str, Any]:
    ensure_project_env_loaded()
    return {
        "env_file": _ENV_FILE_PATH,
        "python_path": settings.PYTHON_PATH,
        "conda_exe": settings.CONDA_EXE,
        "conda_env_name": settings.CONDA_ENV_NAME,
        "nginx_path": settings.NGINX_PATH,
        "backend_bind_host": settings.BACKEND_BIND_HOST,
        "port": int(settings.PORT),
        "uvicorn_log_level": settings.UVICORN_LOG_LEVEL,
    }
