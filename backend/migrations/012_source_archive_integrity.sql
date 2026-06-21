ALTER TABLE source_product_assets
    ADD COLUMN IF NOT EXISTS archive_integrity_status VARCHAR(32) NOT NULL DEFAULT 'NOT_CHECKED';

ALTER TABLE source_product_assets
    ADD COLUMN IF NOT EXISTS archive_integrity_method VARCHAR(64) NULL;

ALTER TABLE source_product_assets
    ADD COLUMN IF NOT EXISTS archive_integrity_checked_at TIMESTAMP NULL;

ALTER TABLE source_product_assets
    ADD COLUMN IF NOT EXISTS archive_integrity_error TEXT NULL;

ALTER TABLE source_product_assets
    ADD COLUMN IF NOT EXISTS archive_integrity_version VARCHAR(32) NULL;

ALTER TABLE source_product_assets
    ADD COLUMN IF NOT EXISTS archive_integrity_member_count INTEGER NULL;

CREATE INDEX IF NOT EXISTS idx_source_product_assets_archive_integrity_status
    ON source_product_assets (archive_integrity_status);
