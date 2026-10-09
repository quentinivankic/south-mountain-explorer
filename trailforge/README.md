# trailforge — System 3

Clean-slate, planet-scale pipeline that extracts **AllTrails-quality trail
objects** from OpenStreetMap: one named trail, complete geometry from
trailhead to the payoff (bridge, summit, falls), correct length. Built to
run on a modest homelab (≤16 GB RAM) via filter-first streaming — the
planet is never loaded into RAM or a database.

Born from a measurable failure: the live app's *Devils Bridge Trail*
(Sedona) stops **838 ft short of the bridge** because earlier extractors
dropped `highway=steps` and grouped geometry strictly by way name.
`golden/golden.json` turns that bug — and 19 more failure modes like it —
into a permanent regression suite.

**Read `HOMELAB.md` first** — it's the full handoff: context, decisions,
the iteration loop, and what to build next. `SPEC.md` (research-derived)
defines the extraction tag set and assembly algorithm.

Systems 1 (`scripts/`, live app) and 2 (`data-pipeline/`) remain untouched
and running; trailforge replaces neither until it beats both.

## Quickstart (homelab)

```bash
make setup                     # osmium-tool + pyosmium + shapely
make download-extract          # north-america first; planet later
make prefilter                 # stream-filter -> data/hiking.osm.pbf
make aoi                       # Sedona bbox -> data/aoi/sedona.raw.geojson
make verify-golden             # golden.json structural check
make qa                        # viewer at http://localhost:8000/viewer/
```

`make aoi` uses the original generic contract: `osmium extract`, then a
`linestring,point` raw viewer export. It does not run Python, require pyosmium,
or create source-topology evidence. The Denmark publish pilot opts into the
provenance path by setting `AOI_RECEIPT`, `AOI_RELATION_MEMBERS`, and
`AOI_WAY_TOPOLOGY` together; `AOI_GITHUB_OUTPUT` is optional and may be set only
with that complete trio. Provenance mode checks `osmium`, Python, pyosmium, and
all three local exporter/receipt tools before extraction. It widens only the
pilot raw viewer export to `linestring,polygon,point`, records every AOI way and
relation, and records every exact-policy destination POI node with its complete
tags, normalized name, class, and coordinate from the extracted PBF. Each ledger creator immediately appends the
exact durable ledger SHA-256 and byte count to the producing step's
`GITHUB_OUTPUT`; the AOI step emits separate relation and topology identities,
then emits receipt/PBF trust only after all evidence exports complete. Final
validation compares those stage-owned ledger identities before parsing their
semantics.
