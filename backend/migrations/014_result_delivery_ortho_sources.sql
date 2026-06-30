ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS source_radar_data_id INTEGER NULL REFERENCES radar_data(id) ON DELETE SET NULL;
ALTER TABLE result_delivery_items ADD COLUMN IF NOT EXISTS source_scene_geo_id INTEGER NULL REFERENCES sar_scene_geo(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_result_delivery_items_source_radar_data_id ON result_delivery_items(source_radar_data_id);
CREATE INDEX IF NOT EXISTS ix_result_delivery_items_source_scene_geo_id ON result_delivery_items(source_scene_geo_id);
CREATE INDEX IF NOT EXISTS idx_result_delivery_items_radar ON result_delivery_items(source_radar_data_id);
CREATE INDEX IF NOT EXISTS idx_result_delivery_items_scene_geo ON result_delivery_items(source_scene_geo_id);
