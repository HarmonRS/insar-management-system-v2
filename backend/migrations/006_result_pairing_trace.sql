-- Migration: Result Product Pairing Trace Fields
-- Version: 6.0
-- Date: 2026-04-14
-- Purpose: Persist pairing trace on result catalog products.

ALTER TABLE IF EXISTS result_products
    ADD COLUMN IF NOT EXISTS pair_uid VARCHAR(64) NULL;

ALTER TABLE IF EXISTS result_products
    ADD COLUMN IF NOT EXISTS network_run_id VARCHAR(64) NULL;

ALTER TABLE IF EXISTS result_products
    ADD COLUMN IF NOT EXISTS network_edge_id INTEGER NULL;

ALTER TABLE IF EXISTS result_products
    ADD COLUMN IF NOT EXISTS policy_version VARCHAR(32) NULL;

ALTER TABLE IF EXISTS result_products
    ADD COLUMN IF NOT EXISTS selection_strategy VARCHAR(32) NULL;

CREATE INDEX IF NOT EXISTS idx_result_products_pair_uid
    ON result_products (pair_uid);

CREATE INDEX IF NOT EXISTS idx_result_products_network_run_id
    ON result_products (network_run_id);

CREATE INDEX IF NOT EXISTS idx_result_products_policy_version
    ON result_products (policy_version);

CREATE INDEX IF NOT EXISTS idx_result_products_selection_strategy
    ON result_products (selection_strategy);
