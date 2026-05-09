"""
Pydantic Schema 定义。
所有 API 请求/响应模型均在此文件中定义。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from ..config import read_int_env

MAX_SCAN_DIRECTORY_COUNT = read_int_env(
    "MAX_SCAN_DIRECTORY_COUNT",
    64,
    minimum=1,
    maximum=500,
)
MAX_SCAN_PATH_LENGTH = read_int_env(
    "MAX_SCAN_PATH_LENGTH",
    2048,
    minimum=64,
    maximum=32767,
)


def _normalize_directory_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")

    normalized: List[str] = []
    for raw in value:
        path = str(raw or "").strip()
        if not path:
            continue
        if len(path) > MAX_SCAN_PATH_LENGTH:
            raise ValueError(
                f"{field_name} contains a path longer than {MAX_SCAN_PATH_LENGTH} characters."
            )
        if path not in normalized:
            normalized.append(path)

    if len(normalized) > MAX_SCAN_DIRECTORY_COUNT:
        raise ValueError(
            f"{field_name} exceeds max directory count ({MAX_SCAN_DIRECTORY_COUNT})."
        )
    return normalized


class HazardPoint(BaseModel):
    id: int
    tybh: str
    hazard_type: Optional[str] = None
    hazard_name: Optional[str] = None
    city: Optional[str] = None
    county: Optional[str] = None
    township: Optional[str] = None
    longitude: float
    latitude: float

    model_config = ConfigDict(from_attributes=True)


class DinsarResult(BaseModel):
    id: int
    product_id: Optional[str] = None
    compat_result_id: Optional[int] = None
    product_family: Optional[str] = None
    name: str
    task_name: Optional[str] = None
    task_alias: Optional[str] = None
    pair_key: Optional[str] = None
    stack_key: Optional[str] = None
    pair_uid: Optional[str] = None
    run_key: Optional[str] = None
    network_run_id: Optional[str] = None
    network_edge_id: Optional[int] = None
    policy_version: Optional[str] = None
    selection_strategy: Optional[str] = None
    engine_code: Optional[str] = None
    file_path: str
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    coverage_polygon: Optional[Union[List, dict]] = None
    is_cached: bool

    ai_score: Optional[float] = None
    user_label: Optional[int] = None
    ai_report: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ScanRequest(BaseModel):
    """API 请求体模型，用于触发数据扫描。"""
    radar_data_directories: List[str] = Field(default_factory=list)
    orbit_data_directory: Optional[str] = None
    dinsar_results_directories: List[str] = Field(default_factory=list)

    @field_validator("radar_data_directories", mode="before")
    @classmethod
    def _validate_radar_dirs(cls, value: Any) -> List[str]:
        return _normalize_directory_list(value, "radar_data_directories")

    @field_validator("dinsar_results_directories", mode="before")
    @classmethod
    def _validate_dinsar_dirs(cls, value: Any) -> List[str]:
        return _normalize_directory_list(value, "dinsar_results_directories")

    @field_validator("orbit_data_directory", mode="before")
    @classmethod
    def _validate_orbit_dir(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if len(normalized) > MAX_SCAN_PATH_LENGTH:
            raise ValueError(
                f"orbit_data_directory is longer than {MAX_SCAN_PATH_LENGTH} characters."
            )
        return normalized


class ManagedRootInfo(BaseModel):
    id: int
    root_code: str
    root_role: str
    display_name: str
    path: str
    path_kind: str
    source_kind: str
    source_ref: Optional[str] = None
    scan_mode: str
    owner_engine: Optional[str] = None
    enabled: bool
    exists_flag: bool
    metadata_json: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ScanCursorInfo(BaseModel):
    id: int
    root_ref_id: int
    cursor_key: str
    cursor_type: str
    scan_scope: str
    status: str
    last_scan_started_at: Optional[datetime] = None
    last_scan_finished_at: Optional[datetime] = None
    last_seen_mtime: Optional[float] = None
    last_seen_entry_count: Optional[int] = None
    last_seen_fingerprint: Optional[str] = None
    last_error: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RadarData(BaseModel):
    """雷达数据的数据模型 (Pydantic)，用于 API 输入和输出。"""
    id: int
    satellite: str
    imaging_date: str
    imaging_mode: str
    orbit_direction: Optional[str] = None
    polarization: str
    satellite_mode: Optional[str] = None
    receiving_station: Optional[str] = None
    orbit_circle: Optional[str] = None
    scene_center_lon: Optional[float] = None
    scene_center_lat: Optional[float] = None
    acquisition_time_utc: Optional[str] = None
    product_type: Optional[str] = None
    source_product_token: Optional[str] = None
    image_data_type: Optional[str] = None
    image_data_format: Optional[str] = None
    product_variant: Optional[str] = None
    product_level: Optional[str] = None
    product_unique_id: Optional[str] = None
    satellite_family: Optional[str] = None
    look_direction: Optional[str] = None
    geocoded_flag: Optional[bool] = None
    insar_source_ready: bool = False
    insar_source_reason: Optional[str] = None
    file_path: str
    has_orbit_data: bool
    orbit_file_path: Optional[str] = None
    is_envi_processed: bool = False
    coverage_polygon: List[Tuple[float, float]]

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    preview_cache_status: str = "NONE"
    preview_cache_version: Optional[str] = None
    preview_cache_updated_at: Optional[datetime] = None
    preview_cache_error: Optional[str] = None
    stack_plan_id: Optional[str] = None
    stack_plan_item_id: Optional[int] = None
    stack_scene_rank: Optional[int] = None
    stack_group_key: Optional[str] = None
    stack_key: Optional[str] = None
    stack_common_aoi_coverage_ratio: Optional[float] = None
    stack_coverage_consistency_ratio: Optional[float] = None
    stack_threshold_satisfied: Optional[bool] = None
    stack_selection_mode: Optional[str] = None
    stack_network_edge_count: Optional[int] = None
    stack_network_warnings: Optional[List[str]] = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def coverage_bbox(self) -> Tuple[float, float, float, float]:
        """A computed property to provide the bbox tuple, used by existing logic."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


class RadarDataPage(BaseModel):
    items: List[RadarData]
    total: int
    limit: int
    offset: int
    has_more: bool


class DinsarResultPage(BaseModel):
    items: List[DinsarResult]
    total: int
    limit: int
    offset: int
    has_more: bool


class PairingRequest(BaseModel):
    """D-InSAR 配对请求的参数模型（增强版 v2.0）"""
    # === 时空约束（保留） ===
    time_baseline_min: int = Field(default=1, ge=0, le=3650)
    time_baseline_max: int = Field(default=90, ge=1, le=3650)
    overlap_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    spatial_baseline_max_meters: int = Field(default=3000, ge=0, le=100000)
    coverage_diversity_penalty: float = Field(default=0.3, ge=0.0, le=1.0)
    require_same_imaging_mode: bool = True
    require_same_polarization: bool = True
    aoi_overlap_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_temporal_baseline_days: Optional[int] = Field(default=None, ge=1, le=3650)
    pair_footprint_overlap_min_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    footprint_center_distance_max_meters: Optional[int] = Field(default=None, ge=0, le=100000)

    # === 双池日期（新增） ===
    master_date_from: Optional[str] = Field(default=None, pattern=r'^\d{8}$|^$')
    master_date_to: Optional[str] = Field(default=None, pattern=r'^\d{8}$|^$')
    slave_date_from: Optional[str] = Field(default=None, pattern=r'^\d{8}$|^$')
    slave_date_to: Optional[str] = Field(default=None, pattern=r'^\d{8}$|^$')

    # === 配对策略（新增） ===
    strategy: str = Field(default="all", pattern=r'^(all|sbas|sequential|star)$')
    num_connections: int = Field(default=1, ge=1, le=10)
    reference_image_id: Optional[int] = None

    # === 多卫星支持（新增） ===
    allowed_satellites: Optional[List[str]] = None
    cross_satellite_pairing: bool = False

    # === 向后兼容（保留） ===
    start_date: Optional[str] = Field(default=None, pattern=r'^\d{8}$|^$')

    @model_validator(mode='before')
    @classmethod
    def _apply_aliases(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if normalized.get('max_temporal_baseline_days') not in (None, ''):
            normalized['time_baseline_max'] = normalized['max_temporal_baseline_days']
        if normalized.get('pair_footprint_overlap_min_ratio') not in (None, ''):
            normalized['overlap_threshold'] = normalized['pair_footprint_overlap_min_ratio']
        if normalized.get('footprint_center_distance_max_meters') not in (None, ''):
            normalized['spatial_baseline_max_meters'] = normalized['footprint_center_distance_max_meters']
        return normalized

    @field_validator(
        'master_date_from',
        'master_date_to',
        'slave_date_from',
        'slave_date_to',
        'start_date',
        mode='before',
    )
    @classmethod
    def _normalize_optional_date_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator('aoi_overlap_threshold', mode='before')
    @classmethod
    def _normalize_aoi_overlap_threshold(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            value = text
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value
        return None if numeric <= 0 else numeric

    @field_validator('allowed_satellites', mode='before')
    @classmethod
    def _normalize_allowed_satellites(cls, value):
        if value in (None, '', []):
            return None
        if not isinstance(value, list):
            return value
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or None

    @field_validator('master_date_to')
    @classmethod
    def validate_master_date_range(cls, v, info):
        if v and info.data.get('master_date_from'):
            if v < info.data['master_date_from']:
                raise ValueError('master_date_to must >= master_date_from')
        return v

    @field_validator('slave_date_to')
    @classmethod
    def validate_slave_date_range(cls, v, info):
        if v and info.data.get('slave_date_from'):
            if v < info.data['slave_date_from']:
                raise ValueError('slave_date_to must >= slave_date_from')
        return v


class RadarPair(BaseModel):
    """单个雷达干涉对的数据模型。"""
    master: RadarData
    slave: RadarData
    task_name: str
    task_alias: Optional[str] = None
    pair_key: Optional[str] = None
    pair_uid: Optional[str] = None
    metric_cache_ref_id: Optional[int] = None
    network_run_id: Optional[str] = None
    network_edge_id: Optional[int] = None
    policy_version: Optional[str] = None
    selection_strategy: Optional[str] = None
    selection_score: Optional[float] = None
    selection_reason: Optional[str] = None
    time_baseline_days: int
    spatial_baseline_meters: float
    scene_center_distance_meters: Optional[float] = None


class PairingResponse(BaseModel):
    """配对结果的响应模型，包含配对列表和可选的 AOI GeoJSON。"""
    pairs: List[RadarPair]
    aoi_geojson: Optional[dict] = None
    warnings: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    degraded: bool = False
    policy_version: Optional[str] = None
    network_run_id: Optional[str] = None
    candidate_count: int = 0
    selected_edge_count: int = 0


class PsRequest(BaseModel):
    """PS-InSAR 时序分析数据准备的请求模型。"""
    initial_overlap_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    final_overlap_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    time_baseline_min: int = Field(default=1, ge=0, le=3650)
    time_baseline_max: int = Field(default=90, ge=1, le=3650)
    spatial_baseline_max_meters: int = Field(default=3000, ge=0, le=100000)
    network_overlap_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    num_connections: int = Field(default=1, ge=1, le=10)


class TimeseriesStackPlanItem(BaseModel):
    id: int
    plan_ref_id: int
    radar_data_ref_id: Optional[int] = None
    scene_rank: int
    file_path: str
    satellite: Optional[str] = None
    imaging_date: Optional[str] = None
    imaging_mode: Optional[str] = None
    polarization: Optional[str] = None
    has_orbit_data: bool
    selection_meta_json: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TimeseriesStackPlan(BaseModel):
    id: int
    plan_id: str
    strategy: str
    request_hash: Optional[str] = None
    request_params_json: Optional[Dict[str, Any]] = None
    aoi_source: Optional[str] = None
    aoi_hash: Optional[str] = None
    aoi_summary_json: Optional[Dict[str, Any]] = None
    direction: Optional[str] = None
    scene_count: int
    stack_key: Optional[str] = None
    group_key: Optional[str] = None
    status: str
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TimeseriesStackPlanEdge(BaseModel):
    id: int
    plan_ref_id: int
    master_plan_item_ref_id: Optional[int] = None
    slave_plan_item_ref_id: Optional[int] = None
    metric_cache_ref_id: Optional[int] = None
    master_scene_ref_id: Optional[int] = None
    slave_scene_ref_id: Optional[int] = None
    edge_rank: int
    master_imaging_date: Optional[str] = None
    slave_imaging_date: Optional[str] = None
    temporal_baseline_days: Optional[int] = None
    spatial_baseline_meters: Optional[float] = None
    scene_center_distance_meters: Optional[float] = None
    perpendicular_baseline_meters: Optional[float] = None
    scene_overlap_ratio: Optional[float] = None
    pair_aoi_overlap_ratio: Optional[float] = None
    selection_reason: Optional[str] = None
    selection_score: Optional[float] = None
    selection_meta_json: Optional[Dict[str, Any]] = None
    enabled: bool = True
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TimeseriesStackPlanDetail(TimeseriesStackPlan):
    items: List[TimeseriesStackPlanItem] = Field(default_factory=list)
    edges: List[TimeseriesStackPlanEdge] = Field(default_factory=list)


class TaskInfo(BaseModel):
    """任务信息的 Pydantic 模型，用于 API 返回。"""
    task_id: str
    task_type: str
    task_name: str
    status: str
    progress: int
    message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AuthUserInfo(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AuthAuditLogInfo(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    resource: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RadarPreviewStatusInfo(BaseModel):
    radar_id: int
    status: str
    cache_version: Optional[str] = None
    cache_updated_at: Optional[datetime] = None
    has_geo_cache: bool = False
    has_raw_cache: bool = False
    source_found: bool = False
    fallback_in_use: bool = False
    message: Optional[str] = None
    error: Optional[str] = None


class DinsarTaskBatch(BaseModel):
    batch_id: str
    name: Optional[str] = None
    status: str
    total_items: int
    completed_items: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DinsarTaskItem(BaseModel):
    id: int
    batch_id: str
    task_name: Optional[str] = None
    task_alias: Optional[str] = None
    pair_key: Optional[str] = None
    scene_pair_uid: Optional[str] = None
    network_run_id: Optional[str] = None
    network_edge_id: Optional[int] = None
    policy_version: Optional[str] = None
    selection_strategy: Optional[str] = None
    master_path: str
    slave_path: str
    master_satellite: Optional[str] = None
    master_imaging_date: Optional[str] = None
    master_imaging_mode: Optional[str] = None
    master_polarization: Optional[str] = None
    slave_satellite: Optional[str] = None
    slave_imaging_date: Optional[str] = None
    slave_imaging_mode: Optional[str] = None
    slave_polarization: Optional[str] = None
    time_baseline_days: Optional[int] = None
    spatial_baseline_meters: Optional[float] = None
    scene_center_distance_meters: Optional[float] = None
    status: str
    remark: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PsTaskBatch(BaseModel):
    batch_id: str
    name: Optional[str] = None
    direction: Optional[str] = None
    plan_id: Optional[str] = None
    plan_strategy: Optional[str] = None
    status: str
    total_items: int
    completed_items: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PsTaskItem(BaseModel):
    id: int
    batch_id: str
    plan_item_ref_id: Optional[int] = None
    file_path: str
    satellite: Optional[str] = None
    imaging_date: Optional[str] = None
    polarization: Optional[str] = None
    has_orbit_data: bool
    status: str
    remark: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PsTimeseriesRun(BaseModel):
    run_id: str
    batch_id: str
    plan_id: Optional[str] = None
    plan_strategy: Optional[str] = None
    product_family: Optional[str] = None
    run_name: str
    catalog_name: str
    stack_key: Optional[str] = None
    mode: str
    engine_code: str
    processor_code: str
    runtime_id: Optional[str] = None
    env_name: Optional[str] = None
    wsl_distro: Optional[str] = None
    status: str
    task_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    direction: Optional[str] = None
    stack_size: int
    reference_date: Optional[str] = None
    water_mask_mode: Optional[str] = None
    dem_path_windows: Optional[str] = None
    dem_path_wsl: Optional[str] = None
    orbit_pool_windows: Optional[str] = None
    orbit_pool_wsl: Optional[str] = None
    work_root_windows: Optional[str] = None
    work_root_wsl: Optional[str] = None
    publish_dir_windows: Optional[str] = None
    publish_dir_wsl: Optional[str] = None
    manifest_path_windows: Optional[str] = None
    manifest_path_wsl: Optional[str] = None
    params_json: Optional[Dict[str, Any]] = None
    summary_json: Optional[Dict[str, Any]] = None
    input_snapshot_json: Optional[Dict[str, Any]] = None
    orbit_summary_json: Optional[Dict[str, Any]] = None
    quality_summary_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ============ AI 诊断 Schema ============


# ============ 水体检测 Schema ============


class WaterDetectRequest(BaseModel):
    """水体检测请求"""
    scene_id: Optional[int] = Field(None, description="SARSceneGeoORM 主键（可选）")
    input_path: Optional[str] = Field(None, description="直接指定 GeoTIFF 路径（可选）")


class WaterDetectResponse(BaseModel):
    """水体检测响应"""
    id: int
    scene_id: Optional[int] = None
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    water_area_km2: Optional[float] = None
    water_pixel_count: Optional[int] = None
    otsu_threshold_db: Optional[float] = None
    status: str
    error_msg: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ============ GF3 处理 Schema ============


class GF3ProcessRequest(BaseModel):
    """GF3 L1A→L2 处理请求"""
    input_dir: str = Field(..., description="GF3 L1A 数据目录路径")
    resolution: float = Field(default=0.0002, ge=0.00001, le=0.01, description="输出分辨率（度）")


class GF3ProcessResponse(BaseModel):
    """GF3 处理响应"""
    id: int
    input_dir: str
    output_dir: Optional[str] = None
    polarizations: Optional[str] = None
    l2_paths: Optional[str] = None
    resolution: float = 0.0002
    status: str
    error_msg: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ============ AI 诊断 Schema ============


class AiDiagnosisCreate(BaseModel):
    """创建 AI 诊断请求"""
    result_id: int = Field(..., description="D-InSAR 结果 ID")
    model_name: str = Field(..., description="模型名称，如 llama3.2-vision")
    prompt_template: str = Field(..., description="Prompt 模板名称：quick/standard/detailed")
    custom_prompt: Optional[str] = Field(None, description="自定义 Prompt（覆盖模板）")


class AiDiagnosisResponse(BaseModel):
    """AI 诊断响应"""
    id: int
    result_id: int
    product_ref_id: Optional[int] = None
    product_id: Optional[str] = None
    task_id: Optional[str] = None
    model_name: str
    prompt_template: str
    prompt_text: Optional[str] = None
    diagnosis_markdown: Optional[str] = None
    risk_level: Optional[str] = None
    confidence_score: Optional[float] = None
    result_name: Optional[str] = None
    date_range: Optional[str] = None
    quality_score: Optional[float] = None
    hazards_found: int = 0
    hazards_snapshot: Optional[List[Dict[str, Any]]] = None
    created_at: datetime
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AiDiagnosisListResponse(BaseModel):
    """AI 诊断列表响应"""
    items: List[AiDiagnosisResponse]
    total: int
    page: int
    page_size: int
