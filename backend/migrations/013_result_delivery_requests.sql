CREATE TABLE IF NOT EXISTS result_delivery_requests (
    id SERIAL PRIMARY KEY,
    delivery_id VARCHAR(64) NOT NULL UNIQUE,
    owner_user_id INTEGER NULL REFERENCES auth_users(id) ON DELETE SET NULL,
    owner_username VARCHAR(64) NOT NULL,
    channel VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    package_mode VARCHAR(16) NOT NULL DEFAULT 'directory',
    item_count INTEGER NOT NULL DEFAULT 0,
    total_bytes BIGINT NOT NULL DEFAULT 0,
    copied_bytes BIGINT NOT NULL DEFAULT 0,
    delivery_root TEXT NOT NULL,
    delivery_dir TEXT NOT NULL,
    zip_path TEXT NULL,
    manifest_path TEXT NULL,
    expires_at TIMESTAMP NULL,
    task_id VARCHAR(128) NULL,
    job_id VARCHAR(128) NULL,
    error_message TEXT NULL,
    request_json JSON NULL,
    summary_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL
);

ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS owner_user_id INTEGER NULL REFERENCES auth_users(id) ON DELETE SET NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS owner_username VARCHAR(64) NOT NULL DEFAULT 'unknown';
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS channel VARCHAR(32) NOT NULL DEFAULT 'dinsar';
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'PENDING';
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS package_mode VARCHAR(16) NOT NULL DEFAULT 'directory';
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS item_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS total_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS copied_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS delivery_root TEXT NOT NULL DEFAULT '';
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS delivery_dir TEXT NOT NULL DEFAULT '';
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS zip_path TEXT NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS manifest_path TEXT NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS task_id VARCHAR(128) NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS job_id VARCHAR(128) NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS error_message TEXT NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS request_json JSON NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS summary_json JSON NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS started_at TIMESTAMP NULL;
ALTER TABLE result_delivery_requests ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL;

CREATE TABLE IF NOT EXISTS result_delivery_items (
    id SERIAL PRIMARY KEY,
    delivery_id VARCHAR(64) NOT NULL REFERENCES result_delivery_requests(delivery_id) ON DELETE CASCADE,
    source_product_id INTEGER NULL REFERENCES result_products(id) ON DELETE SET NULL,
    source_result_id INTEGER NULL REFERENCES dinsar_results(id) ON DELETE SET NULL,
    source_asset_id INTEGER NULL REFERENCES result_assets(id) ON DELETE SET NULL,
    display_name VARCHAR(255) NOT NULL,
    source_path TEXT NOT NULL,
    relative_path TEXT NULL,
    file_size BIGINT NOT NULL DEFAULT 0,
    checksum_sha256 VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    error_message TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS source_product_id INTEGER NULL REFERENCES result_products(id) ON DELETE SET NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS source_result_id INTEGER NULL REFERENCES dinsar_results(id) ON DELETE SET NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS source_asset_id INTEGER NULL REFERENCES result_assets(id) ON DELETE SET NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS display_name VARCHAR(255) NOT NULL DEFAULT 'result';
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS source_path TEXT NOT NULL DEFAULT '';
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS relative_path TEXT NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS file_size BIGINT NOT NULL DEFAULT 0;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS checksum_sha256 VARCHAR(64) NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'PENDING';
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS error_message TEXT NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_result_delivery_owner_status ON result_delivery_requests(owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_result_delivery_channel_status ON result_delivery_requests(channel, status);
CREATE INDEX IF NOT EXISTS idx_result_delivery_created ON result_delivery_requests(created_at);
CREATE INDEX IF NOT EXISTS ix_result_delivery_requests_delivery_id ON result_delivery_requests(delivery_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_requests_owner_user_id ON result_delivery_requests(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_requests_expires_at ON result_delivery_requests(expires_at);
CREATE INDEX IF NOT EXISTS ix_result_delivery_requests_task_id ON result_delivery_requests(task_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_requests_job_id ON result_delivery_requests(job_id);

CREATE INDEX IF NOT EXISTS idx_result_delivery_items_delivery_status ON result_delivery_items(delivery_id, status);
CREATE INDEX IF NOT EXISTS idx_result_delivery_items_product ON result_delivery_items(source_product_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_items_delivery_id ON result_delivery_items(delivery_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_items_source_result_id ON result_delivery_items(source_result_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_items_source_asset_id ON result_delivery_items(source_asset_id);
