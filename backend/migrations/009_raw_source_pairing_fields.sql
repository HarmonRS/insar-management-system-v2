-- Migration: Raw source pairing readiness and center-distance metrics
-- Version: 9.0
-- Date: 2026-05-09
-- Purpose: Pair D-InSAR candidates from raw complex source products without requiring prebuilt SLC/envi_import folders.

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS satellite_family VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS source_product_token VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS image_data_type VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS image_data_format VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS product_variant VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS look_direction VARCHAR NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS geocoded_flag BOOLEAN NULL;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS insar_source_ready BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE IF EXISTS radar_data
    ADD COLUMN IF NOT EXISTS insar_source_reason TEXT NULL;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS scene_center_distance_meters DOUBLE PRECISION NULL;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS same_satellite_family BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS same_look_direction BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS master_satellite_family VARCHAR NULL;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS slave_satellite_family VARCHAR NULL;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS master_look_direction VARCHAR NULL;

ALTER TABLE IF EXISTS pairing_metric_cache
    ADD COLUMN IF NOT EXISTS slave_look_direction VARCHAR NULL;

ALTER TABLE IF EXISTS dinsar_task_items
    ADD COLUMN IF NOT EXISTS scene_center_distance_meters DOUBLE PRECISION NULL;

ALTER TABLE IF EXISTS dinsar_product_profiles
    ADD COLUMN IF NOT EXISTS scene_center_distance_meters DOUBLE PRECISION NULL;

CREATE INDEX IF NOT EXISTS idx_radar_data_satellite_family
    ON radar_data (satellite_family);

CREATE INDEX IF NOT EXISTS idx_radar_data_look_direction
    ON radar_data (look_direction);

CREATE INDEX IF NOT EXISTS idx_radar_data_insar_source_ready
    ON radar_data (insar_source_ready);

CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_center_distance
    ON pairing_metric_cache (scene_center_distance_meters);

CREATE INDEX IF NOT EXISTS idx_pairing_metric_cache_same_family
    ON pairing_metric_cache (same_satellite_family);

UPDATE radar_data
SET
    satellite_family = COALESCE(
        NULLIF(satellite_family, ''),
        CASE
            WHEN upper(replace(replace(replace(COALESCE(satellite, ''), '-', ''), '_', ''), ' ', '')) IN
                 ('LT1', 'LT1A', 'LT1B', 'LUTAN1', 'LUTAN1A', 'LUTAN1B')
                THEN 'LT1'
            WHEN upper(replace(replace(replace(COALESCE(satellite, ''), '-', ''), '_', ''), ' ', '')) IN
                 ('S1', 'S1A', 'S1B', 'S1C', 'SENTINEL1', 'SENTINEL1A', 'SENTINEL1B', 'SENTINEL1C')
                THEN 'S1'
            WHEN NULLIF(satellite, '') IS NOT NULL
                THEN upper(satellite)
            ELSE NULL
        END
    ),
    source_product_token = COALESCE(
        NULLIF(source_product_token, ''),
        CASE
            WHEN split_part(regexp_replace(COALESCE(file_path, ''), '^.*[\\/]', ''), '_', 1) LIKE 'LT1%%'
                THEN NULLIF(split_part(regexp_replace(COALESCE(file_path, ''), '^.*[\\/]', ''), '_', 9), '')
            WHEN split_part(regexp_replace(COALESCE(file_path, ''), '^.*[\\/]', ''), '_', 1) LIKE 'S1%%'
                THEN NULLIF(split_part(regexp_replace(COALESCE(file_path, ''), '^.*[\\/]', ''), '_', 3), '')
            ELSE NULL
        END
    ),
    image_data_type = COALESCE(NULLIF(image_data_type, ''), NULLIF(product_type, ''))
WHERE satellite_family IS NULL
   OR satellite_family = ''
   OR source_product_token IS NULL
   OR source_product_token = ''
   OR image_data_type IS NULL
   OR image_data_type = '';

UPDATE radar_data
SET
    insar_source_ready = (
        geom IS NOT NULL
        AND imaging_date ~ '^[0-9]{8}$'
        AND NULLIF(orbit_direction, '') IS NOT NULL
        AND NULLIF(imaging_mode, '') IS NOT NULL
        AND NULLIF(polarization, '') IS NOT NULL
        AND NULLIF(satellite_family, '') IS NOT NULL
        AND geocoded_flag IS DISTINCT FROM TRUE
        AND (
            upper(COALESCE(NULLIF(image_data_type, ''), NULLIF(product_type, ''), '')) = 'COMPLEX'
            OR upper(COALESCE(NULLIF(source_product_token, ''), '')) IN ('SLC', 'SSC')
            OR upper(COALESCE(NULLIF(product_variant, ''), '')) IN ('SLC', 'SSC')
        )
    ),
    insar_source_reason = CASE
        WHEN (
            geom IS NOT NULL
            AND imaging_date ~ '^[0-9]{8}$'
            AND NULLIF(orbit_direction, '') IS NOT NULL
            AND NULLIF(imaging_mode, '') IS NOT NULL
            AND NULLIF(polarization, '') IS NOT NULL
            AND NULLIF(satellite_family, '') IS NOT NULL
            AND geocoded_flag IS DISTINCT FROM TRUE
            AND (
                upper(COALESCE(NULLIF(image_data_type, ''), NULLIF(product_type, ''), '')) = 'COMPLEX'
                OR upper(COALESCE(NULLIF(source_product_token, ''), '')) IN ('SLC', 'SSC')
                OR upper(COALESCE(NULLIF(product_variant, ''), '')) IN ('SLC', 'SSC')
            )
        )
            THEN NULL
        ELSE concat_ws(
            ';',
            CASE WHEN geom IS NULL THEN 'missing_footprint' END,
            CASE WHEN imaging_date IS NULL OR imaging_date !~ '^[0-9]{8}$' THEN 'missing_date' END,
            CASE WHEN NULLIF(orbit_direction, '') IS NULL THEN 'missing_orbit_direction' END,
            CASE WHEN NULLIF(imaging_mode, '') IS NULL THEN 'missing_imaging_mode' END,
            CASE WHEN NULLIF(polarization, '') IS NULL THEN 'missing_polarization' END,
            CASE WHEN NULLIF(satellite_family, '') IS NULL THEN 'missing_satellite_family' END,
            CASE WHEN geocoded_flag IS TRUE THEN 'geocoded_product' END,
            CASE WHEN NOT (
                upper(COALESCE(NULLIF(image_data_type, ''), NULLIF(product_type, ''), '')) = 'COMPLEX'
                OR upper(COALESCE(NULLIF(source_product_token, ''), '')) IN ('SLC', 'SSC')
                OR upper(COALESCE(NULLIF(product_variant, ''), '')) IN ('SLC', 'SSC')
            ) THEN 'not_complex_source' END
        )
    END;

UPDATE pairing_metric_cache pmc
SET
    scene_center_distance_meters = COALESCE(pmc.scene_center_distance_meters, pmc.spatial_baseline_meters),
    master_satellite_family = COALESCE(pmc.master_satellite_family, m.satellite_family),
    slave_satellite_family = COALESCE(pmc.slave_satellite_family, s.satellite_family),
    master_look_direction = COALESCE(pmc.master_look_direction, m.look_direction),
    slave_look_direction = COALESCE(pmc.slave_look_direction, s.look_direction),
    same_satellite_family = (
        NULLIF(COALESCE(m.satellite_family, m.satellite), '') IS NOT NULL
        AND NULLIF(COALESCE(s.satellite_family, s.satellite), '') IS NOT NULL
        AND COALESCE(m.satellite_family, m.satellite) = COALESCE(s.satellite_family, s.satellite)
    ),
    same_look_direction = (
        NULLIF(m.look_direction, '') IS NULL
        OR NULLIF(s.look_direction, '') IS NULL
        OR m.look_direction = s.look_direction
    )
FROM radar_data m, radar_data s
WHERE pmc.master_scene_ref_id = m.id
  AND pmc.slave_scene_ref_id = s.id
  AND (
      (pmc.scene_center_distance_meters IS NULL AND pmc.spatial_baseline_meters IS NOT NULL)
      OR (pmc.master_satellite_family IS NULL AND m.satellite_family IS NOT NULL)
      OR (pmc.slave_satellite_family IS NULL AND s.satellite_family IS NOT NULL)
      OR (pmc.master_look_direction IS NULL AND m.look_direction IS NOT NULL)
      OR (pmc.slave_look_direction IS NULL AND s.look_direction IS NOT NULL)
      OR pmc.same_satellite_family IS DISTINCT FROM (
          NULLIF(COALESCE(m.satellite_family, m.satellite), '') IS NOT NULL
          AND NULLIF(COALESCE(s.satellite_family, s.satellite), '') IS NOT NULL
          AND COALESCE(m.satellite_family, m.satellite) = COALESCE(s.satellite_family, s.satellite)
      )
      OR pmc.same_look_direction IS DISTINCT FROM (
          NULLIF(m.look_direction, '') IS NULL
          OR NULLIF(s.look_direction, '') IS NULL
          OR m.look_direction = s.look_direction
      )
  );

UPDATE pairing_cache_state
SET
    metric_version = '2026.05.raw.v1',
    status = CASE WHEN status = 'REBUILDING' THEN status ELSE 'DIRTY' END,
    last_error = NULL,
    updated_at = NOW()
WHERE metric_version IS DISTINCT FROM '2026.05.raw.v1';
