-- Migration: Pairing Task Trace Fields
-- Version: 5.0
-- Date: 2026-04-14
-- Purpose: Add pairing network trace fields to D-InSAR task items.

ALTER TABLE IF EXISTS dinsar_task_items
    ADD COLUMN IF NOT EXISTS scene_pair_uid VARCHAR(64) NULL;

ALTER TABLE IF EXISTS dinsar_task_items
    ADD COLUMN IF NOT EXISTS network_run_id VARCHAR(64) NULL;

ALTER TABLE IF EXISTS dinsar_task_items
    ADD COLUMN IF NOT EXISTS network_edge_id INTEGER NULL;

ALTER TABLE IF EXISTS dinsar_task_items
    ADD COLUMN IF NOT EXISTS policy_version VARCHAR(32) NULL;

ALTER TABLE IF EXISTS dinsar_task_items
    ADD COLUMN IF NOT EXISTS selection_strategy VARCHAR(32) NULL;

CREATE INDEX IF NOT EXISTS idx_dinsar_task_items_scene_pair_uid
    ON dinsar_task_items (scene_pair_uid);

CREATE INDEX IF NOT EXISTS idx_dinsar_task_items_network_run_id
    ON dinsar_task_items (network_run_id);

CREATE INDEX IF NOT EXISTS idx_dinsar_task_items_policy_version
    ON dinsar_task_items (policy_version);

CREATE INDEX IF NOT EXISTS idx_dinsar_task_items_selection_strategy
    ON dinsar_task_items (selection_strategy);
