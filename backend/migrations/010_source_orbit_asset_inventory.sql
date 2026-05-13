-- Migration: Source product and orbit asset inventory
-- Version: 10.0
-- Date: 2026-05-12
-- Purpose: Add first-class source product assets, orbit assets, scene-orbit bindings,
--          inventory state, and radar_data compatibility fields.

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS source_product_ref_id INTEGER NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS source_archive_asset_id INTEGER NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS selected_orbit_asset_id INTEGER NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS orbit_binding_status VARCHAR(32) NOT NULL DEFAULT 'UNBOUND';

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS orbit_binding_reason TEXT NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS acquisition_start_time_utc TIMESTAMP NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS acquisition_stop_time_utc TIMESTAMP NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS absolute_orbit VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS relative_orbit VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS source_format VARCHAR(32) NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS metadata_json JSON NULL;

CREATE TABLE IF NOT EXISTS source_product_assets (
    id SERIAL PRIMARY KEY,
    asset_uid VARCHAR(128) NOT NULL UNIQUE,
    logical_product_uid VARCHAR(128) NULL,
    satellite_family VARCHAR(32) NULL,
    satellite VARCHAR(32) NULL,
    source_format VARCHAR(32) NOT NULL,
    product_type VARCHAR(64) NULL,
    product_level VARCHAR(64) NULL,
    imaging_mode VARCHAR(64) NULL,
    polarization VARCHAR(64) NULL,
    absolute_orbit VARCHAR(64) NULL,
    relative_orbit VARCHAR(64) NULL,
    orbit_direction VARCHAR(32) NULL,
    acquisition_start_time_utc TIMESTAMP NULL,
    acquisition_stop_time_utc TIMESTAMP NULL,
    imaging_date VARCHAR(8) NULL,
    root_ref_id INTEGER NULL REFERENCES managed_roots(id) ON DELETE SET NULL,
    root_path VARCHAR NULL,
    file_path VARCHAR NOT NULL UNIQUE,
    archive_path VARCHAR NULL,
    path_kind VARCHAR(24) NULL,
    file_name VARCHAR(255) NULL,
    file_stem VARCHAR(255) NULL,
    file_ext VARCHAR(32) NULL,
    size_bytes BIGINT NULL,
    mtime_epoch DOUBLE PRECISION NULL,
    checksum_sha256 VARCHAR(64) NULL,
    checksum_status VARCHAR(32) NOT NULL DEFAULT 'NOT_COMPUTED',
    parser_name VARCHAR(64) NULL,
    parser_version VARCHAR(32) NULL,
    parse_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    parse_error TEXT NULL,
    parsed_at TIMESTAMP NULL,
    metadata_json JSON NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    missing_since TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orbit_assets (
    id SERIAL PRIMARY KEY,
    orbit_uid VARCHAR(128) NOT NULL UNIQUE,
    satellite_family VARCHAR(32) NULL,
    satellite VARCHAR(32) NULL,
    orbit_type VARCHAR(64) NOT NULL,
    native_format VARCHAR(32) NOT NULL,
    quality_class VARCHAR(32) NOT NULL DEFAULT 'unknown',
    root_ref_id INTEGER NULL REFERENCES managed_roots(id) ON DELETE SET NULL,
    root_path VARCHAR NULL,
    file_path VARCHAR NOT NULL UNIQUE,
    file_name VARCHAR(255) NULL,
    file_stem VARCHAR(255) NULL,
    file_ext VARCHAR(32) NULL,
    size_bytes BIGINT NULL,
    mtime_epoch DOUBLE PRECISION NULL,
    checksum_sha256 VARCHAR(64) NULL,
    checksum_status VARCHAR(32) NOT NULL DEFAULT 'NOT_COMPUTED',
    validity_start_time_utc TIMESTAMP NULL,
    validity_stop_time_utc TIMESTAMP NULL,
    generation_time_utc TIMESTAMP NULL,
    published_time_utc TIMESTAMP NULL,
    parser_name VARCHAR(64) NULL,
    parser_version VARCHAR(32) NULL,
    parse_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    parse_error TEXT NULL,
    parsed_at TIMESTAMP NULL,
    metadata_json JSON NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    missing_since TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scene_orbit_bindings (
    id SERIAL PRIMARY KEY,
    radar_data_id INTEGER NOT NULL REFERENCES radar_data(id) ON DELETE CASCADE,
    orbit_asset_id INTEGER NOT NULL REFERENCES orbit_assets(id) ON DELETE CASCADE,
    binding_role VARCHAR(32) NOT NULL DEFAULT 'primary_orbit',
    match_status VARCHAR(32) NOT NULL DEFAULT 'CANDIDATE',
    selection_status VARCHAR(32) NOT NULL DEFAULT 'CANDIDATE',
    selection_rank INTEGER NULL,
    priority_score DOUBLE PRECISION NULL,
    coverage_margin_before_seconds DOUBLE PRECISION NULL,
    coverage_margin_after_seconds DOUBLE PRECISION NULL,
    match_rule_version VARCHAR(64) NULL,
    match_reason TEXT NULL,
    selected_at TIMESTAMP NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_scene_orbit_binding_role UNIQUE (radar_data_id, orbit_asset_id, binding_role)
);

CREATE TABLE IF NOT EXISTS orbit_asset_derivatives (
    id SERIAL PRIMARY KEY,
    orbit_asset_id INTEGER NOT NULL REFERENCES orbit_assets(id) ON DELETE CASCADE,
    engine_code VARCHAR(32) NOT NULL,
    derivative_format VARCHAR(32) NOT NULL,
    derivative_role VARCHAR(64) NULL,
    pool_path VARCHAR NOT NULL,
    size_bytes BIGINT NULL,
    mtime_epoch DOUBLE PRECISION NULL,
    checksum_sha256 VARCHAR(64) NULL,
    generation_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    generation_error TEXT NULL,
    generated_at TIMESTAMP NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_orbit_asset_derivative_pool_path UNIQUE (orbit_asset_id, engine_code, derivative_format, pool_path)
);

CREATE TABLE IF NOT EXISTS asset_inventory_states (
    id SERIAL PRIMARY KEY,
    root_ref_id INTEGER NOT NULL REFERENCES managed_roots(id) ON DELETE CASCADE,
    inventory_type VARCHAR(32) NOT NULL,
    root_path VARCHAR NOT NULL,
    scan_mode VARCHAR(32) NOT NULL DEFAULT 'file_pool',
    status VARCHAR(32) NOT NULL DEFAULT 'NEVER_SCANNED',
    last_scan_started_at TIMESTAMP NULL,
    last_scan_finished_at TIMESTAMP NULL,
    last_seen_entry_count INTEGER NULL,
    last_asset_count INTEGER NULL,
    last_issue_count INTEGER NULL,
    parser_version VARCHAR(32) NULL,
    needs_rescan BOOLEAN NOT NULL DEFAULT TRUE,
    last_error TEXT NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_asset_inventory_state_root_type UNIQUE (root_ref_id, inventory_type)
);

CREATE TABLE IF NOT EXISTS asset_inventory_issues (
    id SERIAL PRIMARY KEY,
    root_ref_id INTEGER NULL REFERENCES managed_roots(id) ON DELETE SET NULL,
    inventory_type VARCHAR(32) NOT NULL,
    asset_ref_id INTEGER NULL REFERENCES source_product_assets(id) ON DELETE SET NULL,
    radar_data_id INTEGER NULL REFERENCES radar_data(id) ON DELETE SET NULL,
    orbit_asset_id INTEGER NULL REFERENCES orbit_assets(id) ON DELETE SET NULL,
    severity VARCHAR(16) NOT NULL DEFAULT 'warning',
    issue_code VARCHAR(64) NOT NULL,
    issue_message TEXT NULL,
    source_path VARCHAR NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP NULL,
    metadata_json JSON NULL
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_radar_data_source_product_ref_id'
    ) THEN
        ALTER TABLE radar_data
            ADD CONSTRAINT fk_radar_data_source_product_ref_id
            FOREIGN KEY (source_product_ref_id)
            REFERENCES source_product_assets(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_radar_data_source_archive_asset_id'
    ) THEN
        ALTER TABLE radar_data
            ADD CONSTRAINT fk_radar_data_source_archive_asset_id
            FOREIGN KEY (source_archive_asset_id)
            REFERENCES source_product_assets(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_radar_data_selected_orbit_asset_id'
    ) THEN
        ALTER TABLE radar_data
            ADD CONSTRAINT fk_radar_data_selected_orbit_asset_id
            FOREIGN KEY (selected_orbit_asset_id)
            REFERENCES orbit_assets(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_radar_data_source_product_ref
    ON radar_data (source_product_ref_id);

CREATE INDEX IF NOT EXISTS idx_radar_data_source_archive_asset
    ON radar_data (source_archive_asset_id);

CREATE INDEX IF NOT EXISTS idx_radar_data_selected_orbit_asset
    ON radar_data (selected_orbit_asset_id);

CREATE INDEX IF NOT EXISTS idx_radar_data_orbit_binding_status
    ON radar_data (orbit_binding_status);

CREATE INDEX IF NOT EXISTS idx_radar_data_acquisition_start
    ON radar_data (acquisition_start_time_utc);

CREATE INDEX IF NOT EXISTS idx_radar_data_absolute_orbit
    ON radar_data (absolute_orbit);

CREATE INDEX IF NOT EXISTS idx_radar_data_relative_orbit
    ON radar_data (relative_orbit);

CREATE INDEX IF NOT EXISTS idx_radar_data_source_format
    ON radar_data (source_format);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_asset_uid
    ON source_product_assets (asset_uid);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_family_date
    ON source_product_assets (satellite_family, imaging_date);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_satellite
    ON source_product_assets (satellite);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_source_format
    ON source_product_assets (source_format);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_parse_status
    ON source_product_assets (parse_status);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_root_active
    ON source_product_assets (root_ref_id, is_active);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_logical_product
    ON source_product_assets (logical_product_uid);

CREATE INDEX IF NOT EXISTS idx_source_product_assets_file_path
    ON source_product_assets (file_path);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_orbit_uid
    ON orbit_assets (orbit_uid);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_family_sat_window
    ON orbit_assets (satellite_family, satellite, validity_start_time_utc, validity_stop_time_utc);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_orbit_type
    ON orbit_assets (orbit_type);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_native_format
    ON orbit_assets (native_format);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_quality_class
    ON orbit_assets (quality_class);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_parse_status
    ON orbit_assets (parse_status);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_root_active
    ON orbit_assets (root_ref_id, is_active);

CREATE INDEX IF NOT EXISTS idx_orbit_assets_file_path
    ON orbit_assets (file_path);

CREATE INDEX IF NOT EXISTS idx_scene_orbit_bindings_radar
    ON scene_orbit_bindings (radar_data_id);

CREATE INDEX IF NOT EXISTS idx_scene_orbit_bindings_orbit
    ON scene_orbit_bindings (orbit_asset_id);

CREATE INDEX IF NOT EXISTS idx_scene_orbit_bindings_match_status
    ON scene_orbit_bindings (match_status);

CREATE INDEX IF NOT EXISTS idx_scene_orbit_bindings_selection_status
    ON scene_orbit_bindings (selection_status);

CREATE INDEX IF NOT EXISTS idx_scene_orbit_bindings_scene_selected
    ON scene_orbit_bindings (radar_data_id, selection_status);

CREATE INDEX IF NOT EXISTS idx_orbit_asset_derivatives_asset_engine
    ON orbit_asset_derivatives (orbit_asset_id, engine_code);

CREATE INDEX IF NOT EXISTS idx_orbit_asset_derivatives_pool_path
    ON orbit_asset_derivatives (pool_path);

CREATE INDEX IF NOT EXISTS idx_orbit_asset_derivatives_generation_status
    ON orbit_asset_derivatives (generation_status);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_states_type_status
    ON asset_inventory_states (inventory_type, status);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_states_needs_rescan
    ON asset_inventory_states (needs_rescan);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_issues_open
    ON asset_inventory_issues (status, severity);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_issues_root_type
    ON asset_inventory_issues (root_ref_id, inventory_type);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_issues_asset
    ON asset_inventory_issues (asset_ref_id);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_issues_orbit
    ON asset_inventory_issues (orbit_asset_id);

CREATE INDEX IF NOT EXISTS idx_asset_inventory_issues_radar
    ON asset_inventory_issues (radar_data_id);

UPDATE radar_data
SET
    orbit_binding_status = CASE
        WHEN has_orbit_data IS TRUE AND NULLIF(orbit_file_path, '') IS NOT NULL THEN 'MATCHED'
        WHEN has_orbit_data IS TRUE THEN 'MATCHED'
        ELSE COALESCE(NULLIF(orbit_binding_status, ''), 'UNBOUND')
    END
WHERE orbit_binding_status IS NULL
   OR orbit_binding_status = ''
   OR (has_orbit_data IS TRUE AND orbit_binding_status = 'UNBOUND');

UPDATE radar_data
SET absolute_orbit = COALESCE(NULLIF(absolute_orbit, ''), NULLIF(orbit_circle, ''))
WHERE absolute_orbit IS NULL OR absolute_orbit = '';

UPDATE radar_data
SET source_format = COALESCE(
    NULLIF(source_format, ''),
    CASE
        WHEN upper(replace(replace(replace(COALESCE(satellite, ''), '-', ''), '_', ''), ' ', '')) LIKE 'S1%%'
             AND lower(COALESCE(file_path, '')) LIKE '%%.zip' THEN 'S1_ZIP'
        WHEN upper(replace(replace(replace(COALESCE(satellite, ''), '-', ''), '_', ''), ' ', '')) LIKE 'S1%%'
             AND lower(COALESCE(file_path, '')) LIKE '%%.safe' THEN 'S1_SAFE_DIR'
        WHEN upper(replace(replace(replace(COALESCE(satellite, ''), '-', ''), '_', ''), ' ', '')) LIKE 'LT1%%' THEN 'LT1_DIR'
        WHEN upper(replace(replace(replace(COALESCE(satellite, ''), '-', ''), '_', ''), ' ', '')) LIKE 'GF3%%' THEN 'GF3_DIR'
        ELSE NULL
    END
)
WHERE source_format IS NULL OR source_format = '';
