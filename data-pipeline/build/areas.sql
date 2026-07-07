-- build/areas.sql — normalize named-area polygons from OSM (+ Overture +
-- authoritative) into one area layer (spec §3.2, §4.3 per-area confidence).
-- DuckDB + spatial extension.
--
-- Run via:
--   duckdb -c "SET VARIABLE osm_path='staging/osm/nz_areas.parquet'; \
--              SET VARIABLE auth_path='staging/nz_doc/pcl.parquet'; \
--              SET VARIABLE out_path='staging/areas/nz_areas_norm.parquet'; \
--              .read build/areas.sql"
--
-- authority_rank (spec §4.3): authoritative-public-domain/CC (PAD-US,
-- NPS, CDDA, DOC/LINZ, state AU) > OSM boundary=protected_area w/ WDPA
-- cross-ref > Overture > bare OSM landuse=forest. Higher rank wins on
-- conflict, but only if the source is redistributable (enforced upstream
-- by the licensing gate; this SQL only ranks).

INSTALL spatial; LOAD spatial;

COPY (
  WITH osm AS (
    SELECT
      osm_id,
      COALESCE(name, "name:en")                        AS name,
      CASE
        WHEN boundary = 'protected_area'  THEN 'osm_protected_area'
        WHEN boundary = 'national_park'   THEN 'osm_national_park'
        WHEN leisure  = 'nature_reserve'  THEN 'osm_nature_reserve'
        WHEN landuse  = 'forest'          THEN 'osm_forest'
      END                                              AS scheme,
      protect_class,
      protection_title,
      'osm'                                            AS source_id,
      -- rank: protected_area (with wdpa xref) > national_park/reserve > forest
      CASE
        WHEN boundary = 'protected_area' AND "ref:whon" IS NOT NULL THEN 40
        WHEN boundary = 'protected_area' THEN 35
        WHEN boundary = 'national_park'  THEN 30
        WHEN leisure  = 'nature_reserve' THEN 25
        WHEN landuse  = 'forest'         THEN 10
      END                                              AS authority_rank,
      FALSE                                            AS referential,
      geom
    FROM read_parquet(getvariable('osm_path'))
    WHERE boundary IN ('protected_area','national_park')
       OR leisure = 'nature_reserve'
       OR landuse = 'forest'
  ),
  auth AS (
    -- Authoritative DOC/LINZ boundaries: highest authority_rank; CC-BY,
    -- so redistributable. referential=false (NZ boundaries are precise;
    -- Chile/Argentina downloaders would set this true).
    SELECT
      NULL::BIGINT                                     AS osm_id,
      COALESCE(name, "Name", "label")                  AS name,
      'authoritative'                                  AS scheme,
      NULL                                             AS protect_class,
      NULL                                             AS protection_title,
      'nz_doc'                                         AS source_id,
      60                                               AS authority_rank,
      FALSE                                            AS referential,
      geom
    FROM read_parquet(getvariable('auth_path'))
  )
  SELECT * FROM osm
  UNION ALL BY NAME
  SELECT * FROM auth
) TO getvariable('out_path') (FORMAT parquet);

-- De-dup of overlapping OSM schemes (a park tagged both national_park and
-- nature_reserve) + OSM-vs-authoritative overlap is handled in a follow-up
-- geometry pass (conflation/match.py area mode); this SQL just normalizes
-- + ranks so that pass can prefer the higher authority_rank on overlap.
