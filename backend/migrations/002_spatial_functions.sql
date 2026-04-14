-- Migration: PostGIS helper functions and views for InSAR workflows
-- Note: keep this file UTF-8 without BOM to avoid SQL parser issues.

CREATE EXTENSION IF NOT EXISTS postgis;

-- =====================================================
-- Function: find_dinsar_pairs
-- Purpose : Find candidate D-InSAR pairs with spatial/time/overlap constraints
-- =====================================================
CREATE OR REPLACE FUNCTION find_dinsar_pairs(
    p_time_baseline_min INTEGER,
    p_time_baseline_max INTEGER,
    p_spatial_baseline_max_meters NUMERIC,
    p_overlap_threshold NUMERIC,
    p_start_date TEXT DEFAULT NULL,
    p_aoi_geom GEOMETRY DEFAULT NULL,
    p_require_orbit_data BOOLEAN DEFAULT TRUE,
    p_require_same_imaging_mode BOOLEAN DEFAULT TRUE,
    p_require_same_polarization BOOLEAN DEFAULT TRUE,
    p_aoi_overlap_threshold NUMERIC DEFAULT NULL
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
        WHERE m.id < s.id
          AND m.orbit_direction = s.orbit_direction
          AND m.satellite = s.satellite
          AND (NOT p_require_orbit_data OR (m.has_orbit_data = true AND s.has_orbit_data = true))
          AND (
              NOT p_require_same_imaging_mode OR (
                  m.imaging_mode IS NOT NULL AND m.imaging_mode <> ''
                  AND s.imaging_mode IS NOT NULL AND s.imaging_mode <> ''
                  AND m.imaging_mode = s.imaging_mode
              )
          )
          AND (
              NOT p_require_same_polarization OR (
                  m.polarization IS NOT NULL AND m.polarization <> ''
                  AND s.polarization IS NOT NULL AND s.polarization <> ''
                  AND m.polarization = s.polarization
              )
          )
          AND (p_start_date IS NULL OR (m.imaging_date >= p_start_date AND s.imaging_date >= p_start_date))
          AND (p_aoi_geom IS NULL OR (ST_Intersects(m.geom, p_aoi_geom) AND ST_Intersects(s.geom, p_aoi_geom)))
          AND (
              p_aoi_geom IS NULL OR p_aoi_overlap_threshold IS NULL OR (
                  ST_Area(ST_Intersection(m.geom, p_aoi_geom)::geography) /
                      NULLIF(ST_Area(p_aoi_geom::geography), 0) >= p_aoi_overlap_threshold
                  AND ST_Area(ST_Intersection(s.geom, p_aoi_geom)::geography) /
                      NULLIF(ST_Area(p_aoi_geom::geography), 0) >= p_aoi_overlap_threshold
              )
          )
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

-- =====================================================
-- Function: calculate_coverage_overlap
-- Purpose : Compute overlap area and ratio between two images
-- =====================================================
CREATE OR REPLACE FUNCTION calculate_coverage_overlap(
    p_image1_id INTEGER,
    p_image2_id INTEGER
)
RETURNS TABLE (
    overlap_area NUMERIC,
    overlap_ratio NUMERIC,
    intersection_geom GEOMETRY
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ST_Area(ST_Intersection(r1.geom, r2.geom)::geography) AS overlap_area,
        ST_Area(ST_Intersection(r1.geom, r2.geom)::geography) /
            NULLIF(GREATEST(ST_Area(r1.geom::geography), ST_Area(r2.geom::geography)), 0) AS overlap_ratio,
        ST_Intersection(r1.geom, r2.geom) AS intersection_geom
    FROM radar_data r1
    CROSS JOIN radar_data r2
    WHERE r1.id = p_image1_id AND r2.id = p_image2_id;
END;
$$;

-- =====================================================
-- Function: find_common_overlap_area
-- Purpose : Find common overlap area among a set of images
-- =====================================================
CREATE OR REPLACE FUNCTION find_common_overlap_area(
    p_image_ids INTEGER[]
)
RETURNS TABLE (
    common_area NUMERIC,
    common_geom GEOMETRY
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_first_geom GEOMETRY;
    v_result_geom GEOMETRY;
BEGIN
    IF p_image_ids IS NULL OR array_length(p_image_ids, 1) IS NULL THEN
        RETURN;
    END IF;

    SELECT geom INTO v_first_geom
    FROM radar_data
    WHERE id = p_image_ids[1];

    v_result_geom := v_first_geom;

    FOR i IN 2..array_length(p_image_ids, 1) LOOP
        SELECT ST_Intersection(v_result_geom, geom) INTO v_result_geom
        FROM radar_data
        WHERE id = p_image_ids[i];

        IF v_result_geom IS NULL OR ST_IsEmpty(v_result_geom) THEN
            EXIT;
        END IF;
    END LOOP;

    IF v_result_geom IS NULL OR ST_IsEmpty(v_result_geom) THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT
        ST_Area(v_result_geom::geography) AS common_area,
        v_result_geom AS common_geom;
END;
$$;

-- =====================================================
-- View: radar_pairs_view (helper view)
-- =====================================================
CREATE OR REPLACE VIEW radar_pairs_view AS
SELECT
    row_number() OVER (ORDER BY m.id, s.id) AS id,
    m.id AS master_id,
    s.id AS slave_id,
    m.imaging_date AS master_date,
    s.imaging_date AS slave_date,
    CASE
        WHEN m.imaging_date ~ '^[0-9]{8}$' AND s.imaging_date ~ '^[0-9]{8}$'
            THEN ABS(to_date(s.imaging_date, 'YYYYMMDD') - to_date(m.imaging_date, 'YYYYMMDD'))
        ELSE NULL
    END AS time_baseline_days,
    ST_DistanceSphere(ST_Centroid(m.geom), ST_Centroid(s.geom)) AS spatial_baseline_meters,
    m.geom AS geom1,
    s.geom AS geom2,
    m.geom AS master_geom,
    s.geom AS slave_geom,
    ST_Area(ST_Intersection(m.geom, s.geom)::geography) AS overlap_area
FROM radar_data m
JOIN radar_data s ON ST_Intersects(m.geom, s.geom)
WHERE m.id < s.id;

-- =====================================================
-- Function: optimize_task_selection
-- Purpose : Select pairs with spatial coverage diversity
-- =====================================================
CREATE OR REPLACE FUNCTION optimize_task_selection(
    p_pair_ids INTEGER[],
    p_penalty_factor NUMERIC DEFAULT 0.3
)
RETURNS TABLE (
    selected_pair_id INTEGER,
    score NUMERIC
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_selected_ids INTEGER[];
    v_total_geom GEOMETRY := NULL;
    v_pair RECORD;
    v_inter_geom GEOMETRY;
    v_new_area NUMERIC;
    v_overlap_area NUMERIC;
    v_score NUMERIC;
BEGIN
    FOR v_pair IN
        SELECT id, geom1, geom2, overlap_area
        FROM radar_pairs_view
        WHERE id = ANY(p_pair_ids)
        ORDER BY overlap_area DESC
    LOOP
        v_inter_geom := ST_Intersection(v_pair.geom1, v_pair.geom2);

        IF v_total_geom IS NULL THEN
            v_total_geom := v_inter_geom;
            v_selected_ids := array_append(v_selected_ids, v_pair.id);
        ELSE
            v_new_area := ST_Area(ST_Difference(v_inter_geom, v_total_geom));
            v_overlap_area := ST_Area(ST_Intersection(v_inter_geom, v_total_geom));
            v_score := v_new_area - (v_overlap_area * p_penalty_factor);

            IF v_score > 0 THEN
                v_total_geom := ST_Union(v_total_geom, v_inter_geom);
                v_selected_ids := array_append(v_selected_ids, v_pair.id);
            END IF;
        END IF;
    END LOOP;

    RETURN QUERY
    SELECT unnest(v_selected_ids)::INTEGER AS selected_pair_id, 0 AS score;
END;
$$;

-- =====================================================
-- Function: find_hazard_points_in_area
-- Purpose : Find hazard points within a geometry
-- =====================================================
CREATE OR REPLACE FUNCTION find_hazard_points_in_area(
    p_area_geom GEOMETRY
)
RETURNS TABLE (
    id INTEGER,
    tybh TEXT,
    hazard_type TEXT,
    hazard_name TEXT,
    city TEXT,
    county TEXT,
    longitude NUMERIC,
    latitude NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        hp.id,
        hp.tybh,
        hp.hazard_type,
        hp.hazard_name,
        hp.city,
        hp.county,
        hp.longitude,
        hp.latitude
    FROM hazard_points hp
    WHERE ST_Covers(p_area_geom, hp.geom);
END;
$$;

-- =====================================================
-- Table + Function: spatial query logs
-- =====================================================
CREATE TABLE IF NOT EXISTS spatial_query_logs (
    id SERIAL PRIMARY KEY,
    query_type TEXT,
    execution_time_ms NUMERIC,
    result_count INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION log_spatial_query(
    p_query_type TEXT,
    p_execution_time_ms NUMERIC,
    p_result_count INTEGER
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO spatial_query_logs (query_type, execution_time_ms, result_count)
    VALUES (p_query_type, p_execution_time_ms, p_result_count);
END;
$$;

-- =====================================================
-- Indexes (redundant with ORM, kept for reference)
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_radar_data_geom ON radar_data USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_dinsar_results_geom ON dinsar_results USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_hazard_points_geom ON hazard_points USING GIST (geom);

-- =====================================================
-- Optional grants (adjust to your app user)
-- =====================================================
-- GRANT EXECUTE ON FUNCTION find_dinsar_pairs TO your_app_user;
-- GRANT EXECUTE ON FUNCTION calculate_coverage_overlap TO your_app_user;
-- GRANT SELECT ON radar_pairs_view TO your_app_user;
