"""
SQLAlchemy ORM 模型定义。
所有数据库表对应的 ORM 类均在此文件中定义。
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Float, JSON, Text,
    DateTime, func, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry

from ..database import Base


class RadarDataORM(Base):
    """
    SQLAlchemy ORM Model for storing radar data metadata in the database.
    """
    __tablename__ = "radar_data"

    id = Column(Integer, primary_key=True, index=True)
    unique_id = Column(String, unique=True, index=True)

    satellite = Column(String, index=True)
    imaging_date = Column(String, index=True)
    imaging_mode = Column(String)
    orbit_direction = Column(String, index=True, nullable=True)
    polarization = Column(String)
    satellite_mode = Column(String, nullable=True)
    receiving_station = Column(String, nullable=True)
    orbit_circle = Column(String, nullable=True)
    scene_center_lon = Column(Float, nullable=True)
    scene_center_lat = Column(Float, nullable=True)
    acquisition_time_utc = Column(String, nullable=True)
    product_type = Column(String, nullable=True)
    product_level = Column(String, nullable=True)
    product_unique_id = Column(String, nullable=True)
    file_path = Column(String, unique=True)
    has_orbit_data = Column(Boolean)
    orbit_file_path = Column(String, nullable=True)
    is_envi_processed = Column(Boolean, default=False)

    geom = Column(Geometry('POLYGON', srid=4326), index=True)
    coverage_polygon = Column(JSON)

    min_lon = Column(Float)
    min_lat = Column(Float)
    max_lon = Column(Float)
    max_lat = Column(Float)

    preview_cache_status = Column(String, nullable=False, default="NONE", server_default="NONE")
    preview_cache_version = Column(String, nullable=True)
    preview_cache_path = Column(String, nullable=True)
    preview_cache_updated_at = Column(DateTime, nullable=True)
    preview_cache_error = Column(Text, nullable=True)


class DinsarResultORM(Base):
    __tablename__ = 'dinsar_results'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    compat_product_id = Column(String(64), nullable=True)
    file_path = Column(String, unique=True)
    min_lon = Column(Float)
    min_lat = Column(Float)
    max_lon = Column(Float)
    max_lat = Column(Float)

    geom = Column(Geometry('POLYGON', srid=4326), index=True)
    coverage_polygon = Column(JSON)

    is_cached = Column(Boolean, default=False, nullable=False)

    ai_score = Column(Float, nullable=True)
    user_label = Column(Integer, nullable=True)
    ai_report = Column(Text, nullable=True)


class ResultProductORM(Base):
    __tablename__ = "result_products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String(64), unique=True, index=True, nullable=False)
    catalog_name = Column(String(32), index=True, nullable=False, default="dinsar")
    product_family = Column(String(32), index=True, nullable=True)
    product_type = Column(String(32), index=True, nullable=False, default="dinsar")
    display_name = Column(String(255), nullable=False)
    task_name = Column(String(255), index=True, nullable=True)
    task_alias = Column(String(255), index=True, nullable=True)
    pair_key = Column(String(128), index=True, nullable=True)
    stack_key = Column(String(128), index=True, nullable=True)
    pair_uid = Column(String(64), index=True, nullable=True)
    run_key = Column(String(128), index=True, nullable=True)
    network_run_id = Column(String(64), index=True, nullable=True)
    network_edge_id = Column(Integer, nullable=True)
    policy_version = Column(String(32), index=True, nullable=True)
    selection_strategy = Column(String(32), index=True, nullable=True)
    profile_code = Column(String(64), index=True, nullable=True)
    engine_code = Column(String(32), index=True, nullable=False)
    engine_version = Column(String(64), nullable=True)
    package_schema = Column(String(64), nullable=True)
    package_layout = Column(String(64), nullable=True)
    processor_code = Column(String(64), nullable=True)
    runtime_id = Column(String(64), nullable=True)
    status = Column(String(32), index=True, nullable=False, default="READY")
    health_status = Column(String(16), index=True, nullable=False, default="OK")

    publish_dir = Column(String, unique=True, nullable=False)
    manifest_path = Column(String, unique=True, nullable=False)
    source_primary_path = Column(String, nullable=True)
    native_output_dir = Column(String, nullable=True)
    preview_path = Column(String, nullable=True)
    primary_asset_path = Column(String, nullable=True)

    summary_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)

    ai_score = Column(Float, nullable=True)
    user_label = Column(Integer, nullable=True)

    min_lon = Column(Float, nullable=True)
    min_lat = Column(Float, nullable=True)
    max_lon = Column(Float, nullable=True)
    max_lat = Column(Float, nullable=True)
    geom = Column(Geometry("POLYGON", srid=4326), nullable=True)
    coverage_polygon = Column(JSON, nullable=True)

    produced_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    registered_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    profile = relationship(
        "DinsarProductProfileORM",
        back_populates="product",
        cascade="all, delete-orphan",
        uselist=False,
    )
    assets = relationship(
        "ResultAssetORM",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    issues = relationship(
        "ResultIssueORM",
        back_populates="product",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_result_products_catalog_status", "catalog_name", "status"),
        Index("idx_result_products_engine_dates", "engine_code", "published_at"),
    )


class DinsarProductProfileORM(Base):
    __tablename__ = "dinsar_product_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_ref_id = Column(
        Integer,
        ForeignKey("result_products.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    master_path = Column(String, nullable=True)
    slave_path = Column(String, nullable=True)
    master_satellite = Column(String, nullable=True)
    slave_satellite = Column(String, nullable=True)
    master_imaging_date = Column(String(8), index=True, nullable=True)
    slave_imaging_date = Column(String(8), index=True, nullable=True)
    master_imaging_mode = Column(String, nullable=True)
    slave_imaging_mode = Column(String, nullable=True)
    master_polarization = Column(String, nullable=True)
    slave_polarization = Column(String, nullable=True)
    orbit_direction = Column(String, index=True, nullable=True)
    time_baseline_days = Column(Integer, index=True, nullable=True)
    spatial_baseline_meters = Column(Float, index=True, nullable=True)

    grid_size_m = Column(Float, nullable=True)
    radar_wavelength = Column(Float, nullable=True)
    orbit_clip_margin = Column(Integer, nullable=True)
    bbox_margin = Column(Float, nullable=True)
    coherence_threshold = Column(Float, nullable=True)

    params_json = Column(JSON, nullable=True)
    metrics_json = Column(JSON, nullable=True)

    product = relationship("ResultProductORM", back_populates="profile")


class ResultAssetORM(Base):
    __tablename__ = "result_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_ref_id = Column(
        Integer,
        ForeignKey("result_products.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    asset_role = Column(String(32), index=True, nullable=False)
    asset_name = Column(String(255), nullable=False)
    relative_path = Column(String, nullable=False)
    absolute_path = Column(String, nullable=False)
    format = Column(String(32), nullable=True)
    media_type = Column(String(64), nullable=True)
    is_required = Column(Boolean, nullable=False, default=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    exists_flag = Column(Boolean, nullable=False, default=True, index=True)
    file_size = Column(BigInteger, nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    band_count = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    srid = Column(Integer, nullable=True)
    nodata = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    product = relationship("ResultProductORM", back_populates="assets")
    issues = relationship(
        "ResultIssueORM",
        back_populates="asset",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_result_assets_product_role", "product_ref_id", "asset_role"),
    )


class ResultIssueORM(Base):
    __tablename__ = "result_issues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_ref_id = Column(
        Integer,
        ForeignKey("result_products.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    asset_ref_id = Column(
        Integer,
        ForeignKey("result_assets.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    issue_code = Column(String(64), index=True, nullable=False)
    severity = Column(String(16), index=True, nullable=False, default="WARN")
    status = Column(String(16), index=True, nullable=False, default="OPEN")
    scope = Column(String(16), nullable=False, default="file")
    message = Column(Text, nullable=False)
    repair_action = Column(String(64), nullable=True)
    repair_payload = Column(JSON, nullable=True)
    detected_at = Column(DateTime, server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    product = relationship("ResultProductORM", back_populates="issues")
    asset = relationship("ResultAssetORM", back_populates="issues")


class ResultCatalogStateORM(Base):
    __tablename__ = "result_catalog_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    catalog_name = Column(String(32), unique=True, index=True, nullable=False)
    product_family = Column(String(32), index=True, nullable=True)
    storage_root = Column(String, nullable=False)
    status = Column(String(16), index=True, nullable=False, default="READY")
    needs_rebuild = Column(Boolean, nullable=False, default=False)
    manifest_count = Column(Integer, nullable=False, default=0)
    manifest_fingerprint = Column(String(64), nullable=True)
    db_count = Column(Integer, nullable=False, default=0)
    issue_count = Column(Integer, nullable=False, default=0)
    last_message = Column(Text, nullable=True)
    last_boot_check_at = Column(DateTime, nullable=True)
    last_full_rebuild_at = Column(DateTime, nullable=True)
    last_incremental_scan_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PairingCacheStateORM(Base):
    __tablename__ = "pairing_cache_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_scope = Column(String(32), unique=True, index=True, nullable=False, default="global")
    metric_version = Column(String(32), nullable=False, default="2026.04.v1")
    status = Column(String(16), index=True, nullable=False, default="DIRTY")
    scene_count = Column(Integer, nullable=False, default=0)
    pair_count = Column(Integer, nullable=False, default=0)
    dirty_scene_count = Column(Integer, nullable=False, default=0)
    last_full_rebuild_at = Column(DateTime, nullable=True)
    last_incremental_reconcile_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PairingDirtySceneORM(Base):
    __tablename__ = "pairing_dirty_scenes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_ref_id = Column(
        Integer,
        ForeignKey("radar_data.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    scene_uid = Column(String, index=True, nullable=False)
    reason = Column(String(64), nullable=False, default="scan")
    status = Column(String(16), index=True, nullable=False, default="PENDING")
    marked_at = Column(DateTime, server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_pairing_dirty_scenes_scene_status", "scene_ref_id", "status"),
    )


class PairingMetricCacheORM(Base):
    __tablename__ = "pairing_metric_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    master_scene_ref_id = Column(
        Integer,
        ForeignKey("radar_data.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    slave_scene_ref_id = Column(
        Integer,
        ForeignKey("radar_data.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    master_scene_uid = Column(String, index=True, nullable=False)
    slave_scene_uid = Column(String, index=True, nullable=False)
    pair_uid = Column(String, index=True, nullable=False)
    metric_version = Column(String(32), index=True, nullable=False, default="2026.04.v1")
    orientation_rule_version = Column(String(32), nullable=False, default="date_then_scene_uid_v1")
    time_baseline_days = Column(Integer, index=True, nullable=True)
    spatial_baseline_meters = Column(Float, index=True, nullable=True)
    scene_overlap_ratio = Column(Float, index=True, nullable=True)
    orbit_direction = Column(String, index=True, nullable=True)
    same_satellite = Column(Boolean, nullable=False, default=True)
    same_imaging_mode = Column(Boolean, nullable=False, default=True)
    same_polarization = Column(Boolean, nullable=False, default=True)
    master_imaging_date = Column(String(8), index=True, nullable=True)
    slave_imaging_date = Column(String(8), index=True, nullable=True)
    master_satellite = Column(String, index=True, nullable=True)
    slave_satellite = Column(String, index=True, nullable=True)
    master_imaging_mode = Column(String, nullable=True)
    slave_imaging_mode = Column(String, nullable=True)
    master_polarization = Column(String, nullable=True)
    slave_polarization = Column(String, nullable=True)
    master_file_path = Column(String, nullable=True)
    slave_file_path = Column(String, nullable=True)
    status = Column(String(16), index=True, nullable=False, default="READY")
    computed_at = Column(DateTime, server_default=func.now(), nullable=False)

    edges = relationship("PairingNetworkEdgeORM", back_populates="metric_cache")

    __table_args__ = (
        UniqueConstraint(
            "master_scene_ref_id",
            "slave_scene_ref_id",
            "metric_version",
            name="uq_pairing_metric_cache_pair_version",
        ),
        Index(
            "idx_pairing_metric_cache_metric_dates",
            "metric_version",
            "master_imaging_date",
            "slave_imaging_date",
        ),
    )


class PairingNetworkRunORM(Base):
    __tablename__ = "pairing_network_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    network_run_id = Column(String(64), unique=True, index=True, nullable=False)
    strategy = Column(String(32), index=True, nullable=False)
    policy_version = Column(String(32), index=True, nullable=False)
    request_hash = Column(String(64), index=True, nullable=True)
    request_params_json = Column(JSON, nullable=True)
    aoi_source = Column(String(32), nullable=True)
    aoi_hash = Column(String(64), index=True, nullable=True)
    aoi_summary_json = Column(JSON, nullable=True)
    candidate_count = Column(Integer, nullable=False, default=0)
    selected_edge_count = Column(Integer, nullable=False, default=0)
    warning_count = Column(Integer, nullable=False, default=0)
    status = Column(String(16), index=True, nullable=False, default="PENDING")
    fallback_used = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    edges = relationship(
        "PairingNetworkEdgeORM",
        back_populates="network_run",
        cascade="all, delete-orphan",
    )


class PairingNetworkEdgeORM(Base):
    __tablename__ = "pairing_network_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    network_run_ref_id = Column(
        Integer,
        ForeignKey("pairing_network_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    metric_cache_ref_id = Column(
        Integer,
        ForeignKey("pairing_metric_cache.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    edge_rank = Column(Integer, nullable=False, default=0)
    selection_reason = Column(String(64), nullable=True)
    selection_score = Column(Float, nullable=True)
    selection_meta_json = Column(JSON, nullable=True)
    is_reference_edge = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    network_run = relationship("PairingNetworkRunORM", back_populates="edges")
    metric_cache = relationship("PairingMetricCacheORM", back_populates="edges")

    __table_args__ = (
        UniqueConstraint(
            "network_run_ref_id",
            "metric_cache_ref_id",
            name="uq_pairing_network_edges_run_metric",
        ),
        Index("idx_pairing_network_edges_run_rank", "network_run_ref_id", "edge_rank"),
    )


class HazardPointORM(Base):
    __tablename__ = 'hazard_points'

    id = Column(Integer, primary_key=True, index=True)
    tybh = Column(String, unique=True, index=True)
    hazard_type = Column(String, index=True)
    hazard_name = Column(String, index=True)
    city = Column(String, index=True)
    county = Column(String, index=True)
    township = Column(String)
    longitude = Column(Float)
    latitude = Column(Float)

    geom = Column(Geometry('POINT', srid=4326), index=True)


class SystemTaskORM(Base):
    """
    持久化任务模型。
    用于跟踪后台任务（如扫描、拷贝、AI训练）的状态。
    """
    __tablename__ = "system_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, unique=True, index=True, nullable=False)
    task_type = Column(String, index=True, nullable=False)
    task_name = Column(String, index=True, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    progress = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    params = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    logs = relationship("TaskLogORM", back_populates="task", cascade="all, delete-orphan")


class TaskLogORM(Base):
    """
    任务日志模型。
    记录后台任务执行过程中产生的日志条目。
    """
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey('system_tasks.task_id'), index=True, nullable=False)
    log_level = Column(String, default="INFO")
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())

    task = relationship("SystemTaskORM", back_populates="logs")


class SystemJobORM(Base):
    """
    Persistent job queue entry for background workers.
    """
    __tablename__ = "system_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, unique=True, index=True, nullable=False)
    job_type = Column(String, index=True, nullable=False)
    status = Column(String, index=True, nullable=False, default="READY")
    priority = Column(Integer, default=0)
    payload = Column(JSON, nullable=True)

    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    next_run_at = Column(DateTime, server_default=func.now())

    locked_by = Column(String, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)

    workflow_run_id = Column(String, ForeignKey('workflow_runs.run_id'), nullable=True, index=True)
    workflow_step_id = Column(String, nullable=True, index=True)
    task_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        Index('ix_system_jobs_status_next', 'status', 'next_run_at'),
    )


class ScanStateORM(Base):
    __tablename__ = "scan_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_type = Column(String, index=True, nullable=False)
    root_path = Column(String, index=True, nullable=False)
    last_scan_mtime = Column(Float, default=0)
    last_scan_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('data_type', 'root_path', name='uq_scan_state'),
    )


class ManagedRootORM(Base):
    __tablename__ = "managed_roots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    root_code = Column(String(96), unique=True, index=True, nullable=False)
    root_role = Column(String(48), index=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    path = Column(String, index=True, nullable=False)
    path_kind = Column(String(24), nullable=False, default="windows")
    source_kind = Column(String(24), index=True, nullable=False, default="env")
    source_ref = Column(String(128), nullable=True)
    scan_mode = Column(String(32), nullable=False, default="directory_walk")
    owner_engine = Column(String(32), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    exists_flag = Column(Boolean, nullable=False, default=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    scan_cursors = relationship(
        "ScanCursorORM",
        back_populates="root",
        cascade="all, delete-orphan",
    )
    inventory_items = relationship(
        "PathInventoryORM",
        back_populates="root",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_managed_roots_role_enabled", "root_role", "enabled"),
    )


class ScanCursorORM(Base):
    __tablename__ = "scan_cursors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    root_ref_id = Column(
        Integer,
        ForeignKey("managed_roots.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    cursor_key = Column(String(64), nullable=False, default="default")
    cursor_type = Column(String(32), index=True, nullable=False, default="directory_walk")
    scan_scope = Column(String(32), nullable=False, default="root")
    status = Column(String(16), index=True, nullable=False, default="IDLE")
    last_scan_started_at = Column(DateTime, nullable=True)
    last_scan_finished_at = Column(DateTime, nullable=True)
    last_seen_mtime = Column(Float, nullable=True)
    last_seen_entry_count = Column(Integer, nullable=True)
    last_seen_fingerprint = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    root = relationship("ManagedRootORM", back_populates="scan_cursors")

    __table_args__ = (
        UniqueConstraint("root_ref_id", "cursor_key", name="uq_scan_cursor_root_key"),
    )


class PathInventoryORM(Base):
    __tablename__ = "path_inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    root_ref_id = Column(
        Integer,
        ForeignKey("managed_roots.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    relative_path = Column(String, nullable=False)
    path_type = Column(String(24), index=True, nullable=False, default="file")
    basename = Column(String(255), nullable=False)
    extension = Column(String(32), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    mtime = Column(Float, nullable=True)
    ctime = Column(Float, nullable=True)
    fingerprint = Column(String(64), nullable=True, index=True)
    status = Column(String(16), index=True, nullable=False, default="DISCOVERED")
    metadata_json = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime, server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime, server_default=func.now(), nullable=False)
    last_parsed_at = Column(DateTime, nullable=True)

    root = relationship("ManagedRootORM", back_populates="inventory_items")

    __table_args__ = (
        UniqueConstraint("root_ref_id", "relative_path", name="uq_path_inventory_root_relpath"),
        Index("idx_path_inventory_root_status", "root_ref_id", "status"),
    )


class WorkflowDefORM(Base):
    """Workflow definition (DAG template)."""
    __tablename__ = "workflow_defs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, index=True, nullable=False)
    version = Column(String, default="v1", nullable=False)
    description = Column(Text, nullable=True)
    definition = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('name', 'version', name='uq_workflow_def_name_version'),
    )


class WorkflowRunORM(Base):
    """Workflow run instance."""
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, unique=True, index=True, nullable=False)
    workflow_def_id = Column(Integer, ForeignKey('workflow_defs.id'), nullable=True)
    workflow_name = Column(String, index=True, nullable=False)
    status = Column(String, index=True, nullable=False, default="PENDING")
    params = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    created_by = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    steps = relationship("WorkflowStepORM", back_populates="run", cascade="all, delete-orphan")
    artifacts = relationship("WorkflowArtifactORM", back_populates="run", cascade="all, delete-orphan")


class WorkflowStepORM(Base):
    """Workflow step instance within a run."""
    __tablename__ = "workflow_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey('workflow_runs.run_id'), index=True, nullable=False)
    step_id = Column(String, nullable=False)
    step_name = Column(String, index=True, nullable=False)
    status = Column(String, index=True, nullable=False, default="PENDING")
    depends_on = Column(JSON, nullable=True)
    params = Column(JSON, nullable=True)
    outputs = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    run = relationship("WorkflowRunORM", back_populates="steps")

    __table_args__ = (
        UniqueConstraint('run_id', 'step_id', name='uq_workflow_step_run_step'),
    )


class WorkflowArtifactORM(Base):
    """Artifacts produced by workflow steps."""
    __tablename__ = "workflow_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey('workflow_runs.run_id'), index=True, nullable=False)
    step_id = Column(String, nullable=True, index=True)
    artifact_type = Column(String, index=True, nullable=False)
    path = Column(String, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    run = relationship("WorkflowRunORM", back_populates="artifacts")


class SystemWorkerHeartbeatORM(Base):
    """Worker heartbeat for ops health checks."""
    __tablename__ = "system_worker_heartbeats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String, unique=True, index=True, nullable=False)
    hostname = Column(String, nullable=True)
    pid = Column(Integer, nullable=True)
    note = Column(String, nullable=True)
    started_at = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DinsarTaskBatchORM(Base):
    __tablename__ = "dinsar_task_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    status = Column(String, index=True, nullable=False, default="PENDING")
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    items = relationship("DinsarTaskItemORM", back_populates="batch", cascade="all, delete-orphan")


class DinsarTaskItemORM(Base):
    __tablename__ = "dinsar_task_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, ForeignKey("dinsar_task_batches.batch_id"), index=True, nullable=False)

    task_name = Column(String, nullable=True)
    task_alias = Column(String, index=True, nullable=True)
    pair_key = Column(String(128), index=True, nullable=True)
    scene_pair_uid = Column(String(64), index=True, nullable=True)
    network_run_id = Column(String(64), index=True, nullable=True)
    network_edge_id = Column(Integer, nullable=True)
    policy_version = Column(String(32), index=True, nullable=True)
    selection_strategy = Column(String(32), index=True, nullable=True)
    master_path = Column(String, nullable=False)
    slave_path = Column(String, nullable=False)
    master_satellite = Column(String, nullable=True)
    master_imaging_date = Column(String, nullable=True)
    master_imaging_mode = Column(String, nullable=True)
    master_polarization = Column(String, nullable=True)
    slave_satellite = Column(String, nullable=True)
    slave_imaging_date = Column(String, nullable=True)
    slave_imaging_mode = Column(String, nullable=True)
    slave_polarization = Column(String, nullable=True)
    time_baseline_days = Column(Integer, nullable=True)
    spatial_baseline_meters = Column(Float, nullable=True)

    status = Column(String, index=True, nullable=False, default="PENDING")
    remark = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    batch = relationship("DinsarTaskBatchORM", back_populates="items")


class DinsarProductionRunORM(Base):
    __tablename__ = "dinsar_production_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, index=True, nullable=False)
    task_id = Column(String, index=True, nullable=True)
    workflow_run_id = Column(String, index=True, nullable=True)

    product_family = Column(String(32), index=True, nullable=True)
    engine_code = Column(String(32), index=True, nullable=False, default="sarscape")
    profile_code = Column(String(64), index=True, nullable=False, default="custom6")
    mode = Column(String(32), index=True, nullable=False, default="custom")
    source_root = Column(String, nullable=False)
    publish_root_dir = Column(String, nullable=True)
    status = Column(String(32), index=True, nullable=False, default="PENDING")
    cancel_requested = Column(Boolean, nullable=False, default=False)

    total_items = Column(Integer, nullable=False, default=0)
    completed_items = Column(Integer, nullable=False, default=0)
    failed_items = Column(Integer, nullable=False, default=0)
    skipped_items = Column(Integer, nullable=False, default=0)

    latest_message = Column(Text, nullable=True)
    params_json = Column(JSON, nullable=True)
    summary_json = Column(JSON, nullable=True)
    created_by = Column(String(128), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    items = relationship("DinsarProductionRunItemORM", back_populates="run", cascade="all, delete-orphan")


class DinsarProductionRunItemORM(Base):
    __tablename__ = "dinsar_production_run_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), ForeignKey("dinsar_production_runs.run_id"), index=True, nullable=False)

    order_index = Column(Integer, nullable=False, default=0)
    task_name = Column(String(255), nullable=True)
    task_alias = Column(String(255), index=True, nullable=True)
    pair_key = Column(String(128), index=True, nullable=True)
    pair_uid = Column(String(64), index=True, nullable=True)
    network_run_id = Column(String(64), index=True, nullable=True)
    network_edge_id = Column(Integer, nullable=True)
    policy_version = Column(String(32), index=True, nullable=True)
    selection_strategy = Column(String(32), index=True, nullable=True)

    source_task_dir = Column(String, nullable=False)
    results_root_dir = Column(String, nullable=False)
    status = Column(String(32), index=True, nullable=False, default="PENDING")
    current_step = Column(String(64), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    latest_run_key = Column(String(128), index=True, nullable=True)
    latest_output_dir = Column(String, nullable=True)
    latest_manifest_path = Column(String, nullable=True)
    latest_log_path = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    metrics_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    run = relationship("DinsarProductionRunORM", back_populates="items")
    executions = relationship("DinsarProductionExecutionORM", back_populates="item", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_dinsar_run_items_run_order", "run_id", "order_index"),
    )


class DinsarProductionExecutionORM(Base):
    __tablename__ = "dinsar_production_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(128), unique=True, index=True, nullable=False)
    run_id = Column(String(64), ForeignKey("dinsar_production_runs.run_id"), index=True, nullable=False)
    item_id = Column(Integer, ForeignKey("dinsar_production_run_items.id"), index=True, nullable=False)

    run_key = Column(String(128), index=True, nullable=False)
    status = Column(String(32), index=True, nullable=False, default="PENDING")
    output_dir = Column(String, nullable=False)
    manifest_path = Column(String, nullable=True)
    log_path = Column(String, nullable=True)
    subprocess_pid = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    metrics_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    item = relationship("DinsarProductionRunItemORM", back_populates="executions")

    __table_args__ = (
        Index("idx_dinsar_exec_run_item", "run_id", "item_id"),
    )


class PsTaskBatchORM(Base):
    __tablename__ = "ps_task_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    direction = Column(String, nullable=True)
    status = Column(String, index=True, nullable=False, default="PENDING")
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    items = relationship("PsTaskItemORM", back_populates="batch", cascade="all, delete-orphan")


class PsTaskItemORM(Base):
    __tablename__ = "ps_task_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String, ForeignKey("ps_task_batches.batch_id"), index=True, nullable=False)

    file_path = Column(String, nullable=False)
    satellite = Column(String, nullable=True)
    imaging_date = Column(String, nullable=True)
    polarization = Column(String, nullable=True)
    has_orbit_data = Column(Boolean, default=False)

    status = Column(String, index=True, nullable=False, default="PENDING")
    remark = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    batch = relationship("PsTaskBatchORM", back_populates="items")


class PsTimeseriesRunORM(Base):
    __tablename__ = "ps_timeseries_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, index=True, nullable=False)
    batch_id = Column(String, ForeignKey("ps_task_batches.batch_id"), index=True, nullable=False)

    product_family = Column(String(32), index=True, nullable=True)
    run_name = Column(String(255), nullable=False)
    catalog_name = Column(String(32), index=True, nullable=False, default="psinsar")
    stack_key = Column(String(128), index=True, nullable=True)
    mode = Column(String(32), nullable=False, default="sbas")
    engine_code = Column(String(32), index=True, nullable=False, default="isce2")
    processor_code = Column(String(64), nullable=False, default="isce2_stack_mintpy")
    runtime_id = Column(String(64), nullable=True)
    env_name = Column(String(128), nullable=True)
    wsl_distro = Column(String(128), nullable=True)

    status = Column(String(32), index=True, nullable=False, default="PENDING")
    task_id = Column(String, index=True, nullable=True)
    workflow_run_id = Column(String, index=True, nullable=True)

    direction = Column(String(64), nullable=True)
    stack_size = Column(Integer, nullable=False, default=0)
    reference_date = Column(String(8), index=True, nullable=True)
    water_mask_mode = Column(String(64), nullable=True)

    dem_path_windows = Column(String, nullable=True)
    dem_path_wsl = Column(String, nullable=True)
    orbit_pool_windows = Column(String, nullable=True)
    orbit_pool_wsl = Column(String, nullable=True)

    work_root_windows = Column(String, nullable=True)
    work_root_wsl = Column(String, nullable=True)
    publish_dir_windows = Column(String, nullable=True)
    publish_dir_wsl = Column(String, nullable=True)
    manifest_path_windows = Column(String, nullable=True)
    manifest_path_wsl = Column(String, nullable=True)

    params_json = Column(JSON, nullable=True)
    summary_json = Column(JSON, nullable=True)
    input_snapshot_json = Column(JSON, nullable=True)
    orbit_summary_json = Column(JSON, nullable=True)
    quality_summary_json = Column(JSON, nullable=True)

    error_message = Column(Text, nullable=True)
    created_by = Column(String(128), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    batch = relationship("PsTaskBatchORM")

    __table_args__ = (
        Index("idx_ps_timeseries_runs_batch_status", "batch_id", "status"),
        Index("idx_ps_timeseries_runs_catalog_created", "catalog_name", "created_at"),
    )


class AuthUserORM(Base):
    __tablename__ = "auth_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String, index=True, nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    last_login_at = Column(DateTime, nullable=True)

    sessions = relationship("AuthSessionORM", back_populates="user", cascade="all, delete-orphan")


class AuthSessionORM(Base):
    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    is_revoked = Column(Boolean, nullable=False, default=False, index=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("AuthUserORM", back_populates="sessions")


class AuthAuditLogORM(Base):
    __tablename__ = "auth_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("auth_users.id"), nullable=True, index=True)
    username = Column(String, nullable=True)
    action = Column(String, nullable=False, index=True)
    resource = Column(String, nullable=True, index=True)
    detail = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class AuthRateLimitORM(Base):
    """
    登录限流状态持久化表。
    每个 throttle_key（用户名+IP 组合）对应一行，记录失败时间戳列表和锁定截止时间。
    """
    __tablename__ = "auth_rate_limits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    throttle_key = Column(String, unique=True, index=True, nullable=False)
    failure_timestamps = Column(JSON, nullable=False, default=list)
    locked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Water body monitoring v2 (SARscape-based)
# ---------------------------------------------------------------------------

class SARSceneGeoORM(Base):
    """单景 SAR 影像地理编码结果（多视 + 地理编码 + 辐射定标）。"""
    __tablename__ = "sar_scene_geo"

    id = Column(Integer, primary_key=True, autoincrement=True)
    radar_data_id = Column(Integer, ForeignKey("radar_data.id"), index=True, nullable=False)
    geo_path = Column(String, nullable=True)       # 地理编码 dB 文件路径（ENVI 格式，无扩展名）
    pixel_size_m = Column(Float, nullable=True)    # 输出像素大小（m）
    status = Column(String, nullable=False, default="PENDING", index=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    radar_data = relationship("RadarDataORM", foreign_keys=[radar_data_id])
    pre_flood_detections = relationship(
        "FloodDetectionORM", foreign_keys="FloodDetectionORM.pre_scene_id", back_populates="pre_scene"
    )
    post_flood_detections = relationship(
        "FloodDetectionORM", foreign_keys="FloodDetectionORM.post_scene_id", back_populates="post_scene"
    )

    __table_args__ = (
        UniqueConstraint("radar_data_id", name="uq_sar_scene_geo_radar"),
    )


class FloodDetectionORM(Base):
    """洪涝检测结果（灾前 + 灾后两景配对）。"""
    __tablename__ = "flood_detections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pre_scene_id = Column(Integer, ForeignKey("sar_scene_geo.id"), index=True, nullable=False)
    post_scene_id = Column(Integer, ForeignKey("sar_scene_geo.id"), index=True, nullable=False)
    output_dir = Column(String, nullable=True)             # 输出目录
    classified_path = Column(String, nullable=True)        # 分类图路径（ENVI 格式）
    flood_area_km2 = Column(Float, nullable=True)          # 洪涝面积（km²）
    stable_water_area_km2 = Column(Float, nullable=True)   # 稳定水体面积（km²）
    status = Column(String, nullable=False, default="PENDING", index=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    pre_scene = relationship("SARSceneGeoORM", foreign_keys=[pre_scene_id], back_populates="pre_flood_detections")
    post_scene = relationship("SARSceneGeoORM", foreign_keys=[post_scene_id], back_populates="post_flood_detections")

    __table_args__ = (
        UniqueConstraint("pre_scene_id", "post_scene_id", name="uq_flood_detection_pair"),
    )


class WaterDetectionORM(Base):
    """水体检测结果（Otsu 自适应阈值 + DEM/坡度约束 + 形态学 + 连通分量过滤）。"""
    __tablename__ = "water_detections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(Integer, ForeignKey("sar_scene_geo.id"), nullable=True, index=True)
    input_path = Column(String, nullable=True)        # 输入 GeoTIFF 路径
    output_path = Column(String, nullable=True)       # 输出二值掩膜路径
    water_area_km2 = Column(Float, nullable=True)
    water_pixel_count = Column(Integer, nullable=True)
    otsu_threshold_db = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="PENDING", index=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GF3ProcessingORM(Base):
    """GF3 L1A→L2 处理记录（辐射定标 + RPC 几何校正）。"""
    __tablename__ = "gf3_processing"

    id = Column(Integer, primary_key=True, autoincrement=True)
    input_dir = Column(String, nullable=False)
    output_dir = Column(String, nullable=True)
    polarizations = Column(String, nullable=True)    # JSON 数组，如 '["VH","VV"]'
    l2_paths = Column(String, nullable=True)         # JSON 数组
    resolution = Column(Float, default=0.0002)
    status = Column(String, nullable=False, default="PENDING", index=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AiDiagnosisORM(Base):
    """AI 诊断记录表，存储所有 VLM 对 D-InSAR 结果的诊断报告。"""
    __tablename__ = "ai_diagnosis"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 关联信息
    result_id = Column(Integer, ForeignKey("dinsar_results.id", ondelete="CASCADE"), nullable=False, index=True)
    product_ref_id = Column(Integer, ForeignKey("result_products.id", ondelete="SET NULL"), nullable=True, index=True)
    product_id = Column(String(64), nullable=True, index=True)
    task_id = Column(String(50), nullable=True, index=True)  # 关联 system_tasks

    # 模型与配置
    model_name = Column(String(100), nullable=False)
    prompt_template = Column(String(50), nullable=False)  # 'quick'/'standard'/'detailed'/'custom'
    prompt_text = Column(Text, nullable=True)  # 实际使用的完整 prompt（审计用）

    # 诊断结果
    diagnosis_markdown = Column(Text, nullable=True)  # Markdown 格式报告
    risk_level = Column(String(20), nullable=True, index=True)  # 'LOW'/'MEDIUM'/'HIGH'
    confidence_score = Column(Float, nullable=True)  # 0.0-1.0 模型自评置信度

    # 上下文快照（避免关联查询）
    result_name = Column(String(255), nullable=True)
    date_range = Column(String(100), nullable=True)  # 如 '20240101-20240115'
    quality_score = Column(Float, nullable=True)  # 当时的 ai_score
    hazards_found = Column(Integer, default=0, nullable=False)
    hazards_snapshot = Column(JSON, nullable=True)  # [{name, type, location}]

    # 元数据
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    duration_seconds = Column(Float, nullable=True)  # 诊断耗时
    error_message = Column(Text, nullable=True)  # 失败时记录错误

    # 关系
    result = relationship("DinsarResultORM", backref="diagnoses")
    product = relationship("ResultProductORM", foreign_keys=[product_ref_id])

    __table_args__ = (
        Index("idx_ai_diagnosis_created_at_desc", created_at.desc()),
    )


