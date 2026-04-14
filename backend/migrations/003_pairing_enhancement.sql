-- Migration: D-InSAR Pairing Enhancement
-- Version: 2.0
-- Date: 2026-03-08
-- Purpose: Add dual-pool pairing, multiple strategies, and multi-satellite support

-- =====================================================
-- Function: find_dinsar_pairs_v2 (Enhanced Version)
-- Purpose: Find D-InSAR pairs with dual-pool and multi-satellite support
-- =====================================================
CREATE OR REPLACE FUNCTION find_dinsar_pairs_v2(
    -- Time/Space constraints (existing)
    p_time_baseline_min INTEGER,
    p_time_baseline_max INTEGER,
    p_spatial_baseline_max_meters NUMERIC,
    p_overlap_threshold NUMERIC,
    p_aoi_geom GEOMETRY DEFAULT NULL,
    p_require_orbit_data BOOLEAN DEFAULT TRUE,
    p_require_same_imaging_mode BOOLEAN DEFAULT TRUE,
    p_require_same_polarization BOOLEAN DEFAULT TRUE,
    p_aoi_overlap_threshold NUMERIC DEFAULT NULL,

    -- Dual-pool date ranges (new)
    p_master_date_from TEXT DEFAULT NULL,
    p_master_date_to TEXT DEFAULT NULL,
    p_slave_date_from TEXT DEFAULT NULL,
    p_slave_date_to TEXT DEFAULT NULL,

    -- Multi-satellite support (new)
    p_allowed_satellites TEXT[] DEFAULT NULL,
    p_cross_satellite_pairing BOOLEAN DEFAULT FALSE
)
RETURNS TABLE (
    master_id INTEGER,
    slave_id INTEGER,
    master_imaging_date TEXT,
    slave_imaging_date TEXT,
    time_baseline_days INTEGER,
    spatial_baseline_meters NUMERIC,
    overlap_ratio NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH candidate_pairs AS (
        SELECT
            m.id AS master_id,
            s.id AS slave_id,
            m.imaging_date::text AS master_imaging_date,
            s.imaging_date::text AS slave_imaging_date,
            ABS(to_date(s.imaging_date, 'YYYYMMDD') - to_date(m.imaging_date, 'YYYYMMDD')) AS time_baseline_days,
            ST_DistanceSphere(ST_Centroid(m.geom), ST_Centroid(s.geom))::numeric AS spatial_baseline_meters,
            (
                ST_Area(ST_Intersection(m.geom, s.geom)::geography) /
                NULLIF(GREATEST(ST_Area(m.geom::geography), ST_Area(s.geom::geography)), 0)
            )::numeric AS overlap_ratio
        FROM radar_data m
        JOIN radar_data s ON ST_Intersects(m.geom, s.geom)
        WHERE m.id <> s.id
          -- Master must be earlier than or equal to slave
          AND m.imaging_date <= s.imaging_date

          -- Same orbit direction and satellite (unless cross-satellite allowed)
          AND m.orbit_direction = s.orbit_direction
          AND (p_cross_satellite_pairing OR m.satellite = s.satellite)

          -- Master pool date constraints
          AND (p_master_date_from IS NULL OR m.imaging_date >= p_master_date_from)
          AND (p_master_date_to IS NULL OR m.imaging_date <= p_master_date_to)

          -- Slave pool date constraints
          AND (p_slave_date_from IS NULL OR s.imaging_date >= p_slave_date_from)
          AND (p_slave_date_to IS NULL OR s.imaging_date <= p_slave_date_to)

          -- Satellite filter
          AND (p_allowed_satellites IS NULL OR m.satellite = ANY(p_allowed_satellites))
          AND (p_allowed_satellites IS NULL OR s.satellite = ANY(p_allowed_satellites))

          -- Orbit data requirement
          AND (NOT p_require_orbit_data OR (m.has_orbit_data = true AND s.has_orbit_data = true))

          -- Imaging mode consistency
          AND (
              NOT p_require_same_imaging_mode OR (
                  m.imaging_mode IS NOT NULL AND m.imaging_mode <> ''
                  AND s.imaging_mode IS NOT NULL AND s.imaging_mode <> ''
                  AND m.imaging_mode = s.imaging_mode
              )
          )

          -- Polarization consistency
          AND (
              NOT p_require_same_polarization OR (
                  m.polarization IS NOT NULL AND m.polarization <> ''
                  AND s.polarization IS NOT NULL AND s.polarization <> ''
                  AND m.polarization = s.polarization
              )
          )

          -- AOI intersection
          AND (p_aoi_geom IS NULL OR (ST_Intersects(m.geom, p_aoi_geom) AND ST_Intersects(s.geom, p_aoi_geom)))

          -- AOI overlap threshold
          AND (
              p_aoi_geom IS NULL OR p_aoi_overlap_threshold IS NULL OR (
                  ST_Area(ST_Intersection(m.geom, p_aoi_geom)::geography) /
                      NULLIF(ST_Area(p_aoi_geom::geography), 0) >= p_aoi_overlap_threshold
                  AND ST_Area(ST_Intersection(s.geom, p_aoi_geom)::geography) /
                      NULLIF(ST_Area(p_aoi_geom::geography), 0) >= p_aoi_overlap_threshold
              )
          )

          -- Valid date format
          AND (m.imaging_date ~ '^[0-9]{8}$' AND s.imaging_date ~ '^[0-9]{8}$')
    )
    SELECT
        cp.master_id,
        cp.slave_id,
        cp.master_imaging_date,
        cp.slave_imaging_date,
        cp.time_baseline_days,
        cp.spatial_baseline_meters,
        cp.overlap_ratio
    FROM candidate_pairs cp
    WHERE cp.time_baseline_days BETWEEN p_time_baseline_min AND p_time_baseline_max
      AND cp.spatial_baseline_meters <= p_spatial_baseline_max_meters
      AND cp.overlap_ratio >= p_overlap_threshold
    ORDER BY cp.overlap_ratio DESC;
END;
$$;

COMMENT ON FUNCTION find_dinsar_pairs_v2 IS
'Enhanced D-InSAR pairing function with dual-pool support, multiple strategies, and multi-satellite capability.
Backward compatible: when all date parameters are NULL, behaves like find_dinsar_pairs.';

-- =====================================================
-- Backward Compatibility Note
-- =====================================================
-- The original find_dinsar_pairs function is preserved unchanged.
-- Applications should migrate to find_dinsar_pairs_v2 for new features.
-- When all new parameters (master_date_from/to, slave_date_from/to,
-- allowed_satellites, cross_satellite_pairing) are NULL/default,
-- the behavior is equivalent to the original function.
