-- Persist selected SBAS graph edges for time-series stack plans.

CREATE TABLE IF NOT EXISTS timeseries_stack_plan_edges (
    id SERIAL PRIMARY KEY,
    plan_ref_id INTEGER NOT NULL REFERENCES timeseries_stack_plans(id) ON DELETE CASCADE,
    master_plan_item_ref_id INTEGER NULL REFERENCES timeseries_stack_plan_items(id) ON DELETE SET NULL,
    slave_plan_item_ref_id INTEGER NULL REFERENCES timeseries_stack_plan_items(id) ON DELETE SET NULL,
    metric_cache_ref_id INTEGER NULL REFERENCES pairing_metric_cache(id) ON DELETE SET NULL,
    master_scene_ref_id INTEGER NULL REFERENCES radar_data(id) ON DELETE SET NULL,
    slave_scene_ref_id INTEGER NULL REFERENCES radar_data(id) ON DELETE SET NULL,
    edge_rank INTEGER NOT NULL DEFAULT 0,
    master_imaging_date VARCHAR(8) NULL,
    slave_imaging_date VARCHAR(8) NULL,
    temporal_baseline_days INTEGER NULL,
    spatial_baseline_meters DOUBLE PRECISION NULL,
    perpendicular_baseline_meters DOUBLE PRECISION NULL,
    scene_overlap_ratio DOUBLE PRECISION NULL,
    pair_aoi_overlap_ratio DOUBLE PRECISION NULL,
    selection_reason VARCHAR(64) NULL,
    selection_score DOUBLE PRECISION NULL,
    selection_meta_json JSON NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_timeseries_plan_edges_plan_rank
    ON timeseries_stack_plan_edges (plan_ref_id, edge_rank);

CREATE INDEX IF NOT EXISTS idx_timeseries_plan_edges_plan_enabled
    ON timeseries_stack_plan_edges (plan_ref_id, enabled);

CREATE INDEX IF NOT EXISTS idx_timeseries_plan_edges_plan_scenes
    ON timeseries_stack_plan_edges (plan_ref_id, master_scene_ref_id, slave_scene_ref_id);

CREATE INDEX IF NOT EXISTS idx_timeseries_plan_edges_metric_cache
    ON timeseries_stack_plan_edges (metric_cache_ref_id);
