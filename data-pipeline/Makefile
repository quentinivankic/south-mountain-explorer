# Trekdex trail-data pipeline (spec §1). Orchestrates:
#   download -> stage -> conflate -> build -> tile -> publish
# per region, plus wave targets. The licensing gate (`gate`) is a HARD
# dependency of every build target: no region tiles without it passing.
#
# Most geo steps shell out to external tools (osmium, ogr2ogr, duckdb,
# tippecanoe, wrangler). `make doctor` reports what's installed.

PY        ?= python3
REGION    ?= new-zealand
RAW       := raw
STAGING   := staging
DIST      := dist
REGIONS   := $(DIST)/regions
REGISTRY  := sources/registry.json
CFG       := config/regions.json

# Sources + countries for the active region, read from config/regions.json.
REGION_SOURCES   := $(shell $(PY) -c "import json;print(' '.join(json.load(open('$(CFG)'))['regions']['$(REGION)']['sources']))" 2>/dev/null)
REGION_COUNTRIES := $(shell $(PY) -c "import json;print(' '.join(json.load(open('$(CFG)'))['regions']['$(REGION)']['countries']))" 2>/dev/null)
REGION_TRUST     := $(shell $(PY) -c "import json;print(json.load(open('$(CFG)'))['regions']['$(REGION)']['region_trust'])" 2>/dev/null)

.PHONY: help doctor gate test download stage conflate build tile attributions publish \
        region wave1 wave2 wave3 clean

help:
	@echo "Trekdex trail-data pipeline"
	@echo "  make doctor            - check tool availability"
	@echo "  make test              - run pure-logic unit tests (no geo deps)"
	@echo "  make gate              - fail-closed licensing gate for REGION=$(REGION)"
	@echo "  make region REGION=... - full pipeline for one region"
	@echo "  make wave1|wave2|wave3 - build a rollout wave (spec §6)"
	@echo "  Active region sources: $(REGION_SOURCES) [$(REGION_COUNTRIES)] trust=$(REGION_TRUST)"

doctor:
	@echo "tool availability:"
	@for t in $(PY) duckdb tippecanoe ogr2ogr osmium pmtiles node wrangler; do \
	  printf "  %-12s " $$t; command -v $$t >/dev/null 2>&1 && echo present || echo MISSING; done

# ---- gates + tests (run these anywhere; no geo deps) ----

test:
	$(PY) -m pytest tests/ -q || $(PY) -m unittest discover -s tests -v

# Fail-closed licensing gate (spec §2, §9). MUST pass before any build.
gate:
	@echo ">> licensing gate: REGION=$(REGION) sources=[$(REGION_SOURCES)]"
	$(PY) sources/validate_registry.py --registry $(REGISTRY) --require $(REGION_SOURCES)

# ---- per-region pipeline ----

download:
	@echo ">> download raw sources for $(REGION)"
	cd sources/downloaders && $(PY) osm_nz.py
	cd sources/downloaders && $(PY) nz_doc.py
	@echo "   (nz_linz optional: set LINZ_API_KEY and run nz_linz.py)"

stage:
	@echo ">> stage: osmium extract trails/areas + ogr2ogr -> GeoParquet"
	@echo "   osmium tags-filter $(RAW)/osm/new-zealand-latest.osm.pbf w/highway=path,footway,track,bridleway ..."
	@echo "   (see README §Build for exact osmium/ogr2ogr invocations)"

conflate:
	@echo ">> conflate OSM <-> authoritative (spec §5)"
	$(PY) conflation/match.py --osm $(STAGING)/osm/$(REGION)_trails.geojson \
	  --authoritative $(STAGING)/nz_doc/tracks.geojson \
	  --boundaries $(STAGING)/nz_doc/pcl.geojson \
	  --source-id doc --out $(STAGING)/$(REGION)_matches.json

build: gate
	@echo ">> build normalized trail + area layers (DuckDB) + Bucket B flags"
	$(PY) build/confidence.py --trails $(STAGING)/osm/$(REGION)_trails.geojson \
	  --matches $(STAGING)/$(REGION)_matches.json --region-trust $(REGION_TRUST) \
	  --out $(STAGING)/$(REGION)_trails_flagged.geojson
	@echo ">> inclusion guard (informal/abandoned must survive; no baked score)"
	$(PY) qa/assert_inclusion.py --trails $(STAGING)/$(REGION)_trails_flagged.geojson

attributions: gate
	@mkdir -p $(DIST)
	$(PY) build/attributions.py --registry $(REGISTRY) --region $(REGION) \
	  --sources $(REGION_SOURCES) --countries $(REGION_COUNTRIES) \
	  --out $(DIST)/attributions.$(REGION).json

tile: build attributions
	@mkdir -p $(REGIONS)
	bash build/tiles/build_tiles.sh $(REGION) \
	  $(STAGING)/$(REGION)_trails_flagged.geojson \
	  $(STAGING)/areas/$(REGION)_areas.geojson \
	  $(REGIONS)/$(REGION).pmtiles

publish: tile
	@echo ">> publish $(REGIONS)/$(REGION).pmtiles to R2 (needs wrangler + creds)"
	wrangler r2 object put trekdex-areas-dev/regions/$(REGION).pmtiles \
	  --file $(REGIONS)/$(REGION).pmtiles

region: gate download stage conflate build attributions tile
	@echo ">> region $(REGION) built: $(REGIONS)/$(REGION).pmtiles"

# ---- waves (spec §6) ----
wave1:
	$(MAKE) region REGION=new-zealand
	@echo "wave1: add western/central + northern europe, GB, AU, JP, US/CA as configured"

wave2:
	@echo "wave2 regions not yet configured (spec §6.8-13)"

wave3:
	@echo "wave3 regions deferred / heavy QA (spec §6.14-17)"

clean:
	rm -rf $(STAGING) $(DIST)/regions/*.pmtiles
