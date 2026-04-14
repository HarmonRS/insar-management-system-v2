-- Migration: Pairing Refactor Foundation
-- Version: 4.0
-- Date: 2026-04-14
-- Purpose: Introduce durable pairing cache/state tables for the pairing refactor.

CREATE TABLE IF NOT EXISTS pairing_cache_state (
    id SERIAL PRIMARY KEY,
    cache_scope VARCHAR(32) NOT NULL,
    metric_version VARCHAR(32) NOT NULL DEFAULT '2026.04.v1',
    status VARCHAR(16) NOT NULL DEFAULT 'DIRTY',
    scene_count INTEGER NOT NULL DEFAULT 0,
    pair_count INTEGER NOT NULL DEFAULT 0,
    dirty_scene_count INTEGER NOT NULL DEFAULT 0,
    last_full_rebuild_at TIMESTAMP NULL,
    last_incremental_reconcile_at TIMESTAMP NULL,
    last_error TEXT NULL,
    updated_at TIMESTAMP NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pairing_cache_state_scope
    ON pairing_cache_state (cache_scope);

INSERT INTO pairing_cache_state (
    cache_scope,
    metric_version,
    status,
    scene_count,
    pair_count,
    dirty_scene_count
)
SELECT
    'global',
    '2026.04.v1',
    'DIRTY',
    0,
    0,
    0
WHERE NOT EXISTS (
    SELECT 1 FROM pairing_cache_state WHERE cache_scope = 'global'
);


CREATE TABLE IF NOT EXISTS pairing_dirty_scenes (
    id SERIAL PRIMARY KEY,
    scene_ref_id INTEGER NOT NULL REFERENCES radar_data(id) ON DELETE CASCADE,
    scene_uid VARCHAR NOT NULL,
    reason VARCHAR(64) NOT NULL DEFAULT 'scan',
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    marked_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP NULL
);

CREATE INDEX IF NOT EXISTS idx_pairing_dirty_scenes_scene_status
    ON pairing_dirty_scenes (scene_ref_id, status);

CREATE INDEX IF NOT EXISTS idx_pairing_dirty_scenes_uid_status
    ON pairing_dirty_scenes (scene_uid, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pairing_dirty_scenes_pending_unique
    ON pairing_dirty_scenes (scene_ref_id)
    WHERE status = 'PENDING';


CREATE TABLE IF NOT EXISTS pairing_metric_cache (
    id SERIAL PRIMARY KEY,
    master_scene_ref_id INTEGER NOT NULL REFERENCES radar_data(id) ON DELETE CASCADE,
    slave_scene_ref_id INTEGER NOT NULL REFERENCES radar_data(id) ON DELETE CASCADE,
    master_scene_uid VARCHAR NOT NULL,
    slave_scene_uid VARCHAR NOT NULL,
    pair_uid VARCHAR NOT NULL,
    metric_version VARCHAR(32) NOT NULL DEFAULT '2026.04.v1',
    orientation_rule_version VARCHAR(32) NOT NULL DEFAULT 'date_then_scene_uid_v1',
    time_baseline_days INTEGER NULL,
    spatial_baseline_meters DOUBLE PRECISION NULL,
    scene_overlap_ratio DOUBLE PRECISION NULL,
    orbit_direction VARCHAR NULL,
    same_satellite BOOLEAN NOT NULL DEFAULT TRUE,
    same_imaging_mode BOOLEAN NOT NULL DEFAULT TRUE,
    same_polarization BOOLEAN NOT NULL DEFAULT TRUE,
    master_imaging_date VARCHAR(8) NULL,
    slave_imaging_date VARCHAR(8) NULL,
    master_satellite VARCHAR NULL,
    slave_satellite VARCHAR NULL,
    master_imaging_mode VARCHAR NULL,
    slave_imaging_mode VARCHAR NULL,
    master_polarization VARCHAR NULL,
    slave_polarization VARCHAR NULL,
    master_file_path VARCHAR NULL,
    slave_file_path VARCHAR NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'READY',
    computed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pairing_metric_cache_pair_version
    ON pairing_metric_cache (master_scene_ref_id, slave_scene_ref_id, metric_version);

CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_pair_uid_version
    ON pairing_metric_cache (pair_uid, metric_version);

CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_metric_dates
    ON pairing_metric_cache (metric_version, master_imaging_date, slave_imaging_date);

CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_orbit_direction
    ON pairing_metric_cache (orbit_direction);


CREATE TABLE IF NOT EXISTS pairing_network_runs (
    id SERIAL PRIMARY KEY,
    network_run_id VARCHAR(64) NOT NULL,
    strategy VARCHAR(32) NOT NULL,
    policy_version VARCHAR(32) NOT NULL,
    request_hash VARCHAR(64) NULL,
    request_params_json JSON NULL,
    aoi_source VARCHAR(32) NULL,
    aoi_hash VARCHAR(64) NULL,
    aoi_summary_json JSON NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    selected_edge_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    created_by VARCHAR(64) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pairing_network_runs_run_id
    ON pairing_network_runs (network_run_id);

CREATE INDEX IF NOT EXISTS idx_pairing_network_runs_strategy_status
    ON pairing_network_runs (strategy, status);

CREATE INDEX IF NOT EXISTS idx_pairing_network_runs_request_hash
    ON pairing_network_runs (request_hash);


CREATE TABLE IF NOT EXISTS pairing_network_edges (
    id SERIAL PRIMARY KEY,
    network_run_ref_id INTEGER NOT NULL REFERENCES pairing_network_runs(id) ON DELETE CASCADE,
    metric_cache_ref_id INTEGER NOT NULL REFERENCES pairing_metric_cache(id) ON DELETE CASCADE,
    edge_rank INTEGER NOT NULL DEFAULT 0,
    selection_reason VARCHAR(64) NULL,
    selection_score DOUBLE PRECISION NULL,
    selection_meta_json JSON NULL,
    is_reference_edge BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pairing_network_edges_run_metric
    ON pairing_network_edges (network_run_ref_id, metric_cache_ref_id);

CREATE INDEX IF NOT EXISTS idx_pairing_network_edges_run_rank
    ON pairing_network_edges (network_run_ref_id, edge_rank);
