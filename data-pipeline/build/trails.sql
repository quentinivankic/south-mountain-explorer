-- build/trails.sql — normalize OSM trail ways into the shipped attribute
-- schema (spec §3.1, §4.1). DuckDB + spatial extension.
--
-- Run via:
--   duckdb -c "SET VARIABLE in_path='staging/osm/nz_trails.parquet'; \
--              SET VARIABLE out_path='staging/osm/nz_trails_norm.parquet'; \
--              .read build/trails.sql"
--
-- Input is the osmium/ogr2ogr export of trail ways (all OSM tags kept as
-- columns or a hstore/JSON `tags` map). This selects the §3.1 tags and
-- normalizes them into the Bucket A raw signals (§4.1). It carries EVERY
-- trail through — there is NO score filter here (§4.4, §7.1). The only
-- exclusion allowed anywhere is the licensing gate, which OSM (ODbL,
-- commercial_ok+redistribute_ok) always passes.

INSTALL spatial; LOAD spatial;

COPY (
  WITH src AS (
    SELECT * FROM read_parquet(getvariable('in_path'))
  )
  SELECT
    -- identity / provenance (Bucket A recency signals)
    osm_id,
    COALESCE(osm_version, 0)               AS osm_version,
    osm_timestamp,
    osm_uid,
    changeset,

    -- path type (Bucket A) — the core intrinsic signal
    highway,

    -- name / operator (ship the bool to save tile size; keep name for areas UI)
    name,
    (name IS NOT NULL AND length(trim(name)) > 0)         AS has_name,
    (operator IS NOT NULL AND length(trim(operator)) > 0) AS has_known_operator,

    -- restriction / quality raw signals (Bucket A), null-safe + lowercased
    lower(nullif(trim(informal), ''))          AS informal,
    lower(nullif(trim(access), ''))            AS access,
    lower(nullif(trim(sac_scale), ''))         AS sac_scale,
    lower(nullif(trim(trail_visibility), ''))  AS trail_visibility,
    lower(nullif(trim(surface), ''))           AS surface,

    -- lifecycle normalized enum from abandoned:/disused:/trail_status
    CASE
      WHEN lower(coalesce("abandoned:highway", '')) <> '' THEN 'abandoned'
      WHEN lower(coalesce(disused, '')) IN ('yes','true') THEN 'disused'
      WHEN lower(coalesce(trail_status, '')) = 'abandoned' THEN 'abandoned'
      WHEN lower(coalesce(trail_status, '')) = 'disused'   THEN 'disused'
      ELSE 'active'
    END                                        AS lifecycle,

    -- US-only TIGER-import review flag (null elsewhere) — Bucket A
    (lower(coalesce("tiger:reviewed", '')) = 'no')  AS tiger_unreviewed,

    geom
  FROM src
  WHERE
    -- §3.1 extract set: paths, footways, tracks, bridleways (+ lifecycle
    -- variants so abandoned/disused survive for on-device scoring).
    highway IN ('path','footway','track','bridleway')
    OR "abandoned:highway" IN ('path','footway','track','bridleway')
) TO getvariable('out_path') (FORMAT parquet);
