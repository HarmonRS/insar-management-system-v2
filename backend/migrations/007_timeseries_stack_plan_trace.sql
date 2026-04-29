-- Additive indexes for the phase-2 timeseries stack plan trace chain.
-- Tables/columns are created by SQLAlchemy metadata and db_maintenance missing-column repair.

CREATE INDEX IF NOT EXISTS idx_ps_task_batches_plan_id
    ON ps_task_batches (plan_id);

CREATE INDEX IF NOT EXISTS idx_ps_task_items_plan_item_ref_id
    ON ps_task_items (plan_item_ref_id);

CREATE INDEX IF NOT EXISTS idx_ps_timeseries_runs_plan_id
    ON ps_timeseries_runs (plan_id);

CREATE INDEX IF NOT EXISTS idx_timeseries_stack_plans_request_hash
    ON timeseries_stack_plans (request_hash);

CREATE INDEX IF NOT EXISTS idx_timeseries_stack_plan_items_radar_ref
    ON timeseries_stack_plan_items (radar_data_ref_id);
