-- Migration: Create st_intersection_agg function for efficient geometry intersection
-- This function reduces a set of geometries to their geometric intersection.
-- If no geometries are provided, it returns NULL.

-- Drop existing aggregate and function if they exist (for idempotency)
DROP AGGREGATE IF EXISTS st_intersection_agg(geometry);
DROP FUNCTION IF EXISTS st_intersection_agg(geometry, geometry);

-- Create the aggregation function
CREATE OR REPLACE FUNCTION st_intersection_agg(g1 geometry, g2 geometry)
RETURNS geometry AS
$$
    SELECT CASE WHEN g1 IS NULL THEN g2
                WHEN g2 IS NULL THEN g1
                ELSE ST_Intersection(g1, g2) END;
$$
LANGUAGE SQL;

COMMENT ON FUNCTION st_intersection_agg(geometry, geometry) IS
'Aggregates a set of geometries by computing the intersection pairwise. Returns NULL if input is empty.';

-- Create aggregate wrapper for single-argument usage in SQL queries
CREATE AGGREGATE st_intersection_agg(geometry) (
    SFUNC = st_intersection_agg,
    STYPE = geometry
);

COMMENT ON AGGREGATE st_intersection_agg(geometry) IS
'Aggregates a set of geometries by computing the intersection pairwise.';
