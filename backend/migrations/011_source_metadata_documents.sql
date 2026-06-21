-- Persist source XML/manifest documents and normalized scene geometry profiles.

CREATE TABLE IF NOT EXISTS source_metadata_documents (
    id SERIAL PRIMARY KEY,
    source_asset_id INTEGER NOT NULL REFERENCES source_product_assets(id) ON DELETE CASCADE,
    radar_data_id INTEGER NULL REFERENCES radar_data(id) ON DELETE SET NULL,
    satellite_family VARCHAR(32) NULL,
    source_format VARCHAR(32) NULL,
    document_type VARCHAR(32) NOT NULL,
    member_path TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    content_encoding VARCHAR(16) NOT NULL DEFAULT 'gzip',
    content_bytes BYTEA NOT NULL,
    content_size_bytes BIGINT NULL,
    archive_path TEXT NULL,
    archive_mtime DOUBLE PRECISION NULL,
    parser_version VARCHAR(32) NULL,
    parse_status VARCHAR(32) NOT NULL DEFAULT 'OK',
    parse_error TEXT NULL,
    extracted_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW(),
    CONSTRAINT uq_source_metadata_document_member UNIQUE (source_asset_id, document_type, member_path)
);

CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_source_asset_id
    ON source_metadata_documents (source_asset_id);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_radar_data_id
    ON source_metadata_documents (radar_data_id);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_satellite_family
    ON source_metadata_documents (satellite_family);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_source_format
    ON source_metadata_documents (source_format);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_document_type
    ON source_metadata_documents (document_type);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_content_sha256
    ON source_metadata_documents (content_sha256);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_parse_status
    ON source_metadata_documents (parse_status);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_asset_type
    ON source_metadata_documents (source_asset_id, document_type);
CREATE INDEX IF NOT EXISTS idx_source_metadata_documents_radar_type
    ON source_metadata_documents (radar_data_id, document_type);

CREATE TABLE IF NOT EXISTS sar_scene_geometry_profiles (
    id SERIAL PRIMARY KEY,
    source_asset_id INTEGER NOT NULL UNIQUE REFERENCES source_product_assets(id) ON DELETE CASCADE,
    radar_data_id INTEGER NULL UNIQUE REFERENCES radar_data(id) ON DELETE CASCADE,
    satellite_family VARCHAR(32) NULL,
    satellite VARCHAR(32) NULL,
    source_format VARCHAR(32) NULL,
    imaging_mode VARCHAR(64) NULL,
    polarization VARCHAR(64) NULL,
    orbit_direction VARCHAR(32) NULL,
    look_direction VARCHAR(32) NULL,
    absolute_orbit VARCHAR(64) NULL,
    relative_orbit VARCHAR(64) NULL,
    acquisition_start_time_utc TIMESTAMP NULL,
    acquisition_stop_time_utc TIMESTAMP NULL,
    scene_center_lon DOUBLE PRECISION NULL,
    scene_center_lat DOUBLE PRECISION NULL,
    footprint_geom GEOMETRY(POLYGON, 4326) NULL,
    footprint_polygon JSON NULL,
    swath_summary_json JSON NULL,
    burst_summary_json JSON NULL,
    incidence_angle_min DOUBLE PRECISION NULL,
    incidence_angle_max DOUBLE PRECISION NULL,
    doppler_summary_json JSON NULL,
    state_vector_summary_json JSON NULL,
    metadata_quality VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    production_readiness VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    readiness_reasons_json JSON NULL,
    parser_version VARCHAR(32) NULL,
    parsed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_source_asset_id
    ON sar_scene_geometry_profiles (source_asset_id);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_radar_data_id
    ON sar_scene_geometry_profiles (radar_data_id);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_satellite_family
    ON sar_scene_geometry_profiles (satellite_family);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_satellite
    ON sar_scene_geometry_profiles (satellite);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_source_format
    ON sar_scene_geometry_profiles (source_format);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_imaging_mode
    ON sar_scene_geometry_profiles (imaging_mode);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_polarization
    ON sar_scene_geometry_profiles (polarization);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_orbit_direction
    ON sar_scene_geometry_profiles (orbit_direction);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_look_direction
    ON sar_scene_geometry_profiles (look_direction);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_absolute_orbit
    ON sar_scene_geometry_profiles (absolute_orbit);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_relative_orbit
    ON sar_scene_geometry_profiles (relative_orbit);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_acquisition_start_time_utc
    ON sar_scene_geometry_profiles (acquisition_start_time_utc);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_footprint_geom
    ON sar_scene_geometry_profiles USING GIST (footprint_geom);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_metadata_quality
    ON sar_scene_geometry_profiles (metadata_quality);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_production_readiness
    ON sar_scene_geometry_profiles (production_readiness);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_family_date
    ON sar_scene_geometry_profiles (satellite_family, acquisition_start_time_utc);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_track
    ON sar_scene_geometry_profiles (satellite_family, relative_orbit, orbit_direction);
CREATE INDEX IF NOT EXISTS idx_sar_scene_geometry_profiles_readiness
    ON sar_scene_geometry_profiles (production_readiness, metadata_quality);

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS pair_aoi_overlap_ratio DOUBLE PRECISION NULL;
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS same_relative_orbit BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS master_relative_orbit VARCHAR(64) NULL;
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS slave_relative_orbit VARCHAR(64) NULL;
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS dinsar_quality_tier VARCHAR(16) NOT NULL DEFAULT 'C';
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS dinsar_quality_score DOUBLE PRECISION NULL;
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS dinsar_readiness VARCHAR(32) NOT NULL DEFAULT 'CANDIDATE';
ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS dinsar_reasons_json JSON NULL;

CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_same_relative_orbit
    ON pairing_metric_cache (same_relative_orbit);
CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_quality_tier
    ON pairing_metric_cache (dinsar_quality_tier);
CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_readiness
    ON pairing_metric_cache (dinsar_readiness);
