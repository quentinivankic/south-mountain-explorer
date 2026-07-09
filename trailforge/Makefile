# trailforge — System 3: global AllTrails-quality trails from OSM.
# Runs on a modest homelab (<=16 GB RAM): filter-first streaming, never a
# full-planet DB import. See HOMELAB.md for the full loop.

SHELL := /bin/bash
DATA  := data

# Geofabrik extract for the bootstrap phase (planet comes later):
EXTRACT ?= north-america
PLANET_URL  := https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf
EXTRACT_URL := https://download.geofabrik.de/$(EXTRACT)-latest.osm.pbf

.PHONY: help setup doctor download-extract download-planet prefilter aoi assemble golden verify-golden qa test

help:
	@echo "trailforge targets:"
	@echo "  setup             install deps (osmium-tool, pyosmium; Debian/Ubuntu apt)"
	@echo "  doctor            preflight: tools, disk, RAM (run before download-planet)"
	@echo "  download-extract  fetch Geofabrik EXTRACT=$(EXTRACT) -> $(DATA)/raw/"
	@echo "  download-planet   fetch the full planet (~80 GB) -> $(DATA)/raw/"
	@echo "  prefilter         stream-filter raw PBF -> $(DATA)/hiking.osm.pbf (IN=<file>)"
	@echo "  aoi               cut+export an AOI (NAME=sedona BBOX=lon,lat,lon,lat)"
	@echo "  assemble          assemble trails (NAME=sedona [AREA=\"south mountain\"] [MINLEN=0.1])"
	@echo "  golden            run the full golden-trail regression suite"
	@echo "  verify-golden     validate golden.json (SNAP=1 on homelab to snap coords to OSM)"
	@echo "  qa                serve the local viewer at http://localhost:8000"
	@echo "  test              run unit tests"

setup:
	sudo apt-get update && sudo apt-get install -y osmium-tool python3-pip
	pip3 install --user osmium shapely
	osmium --version

doctor:
	@bash tools/doctor.sh

$(DATA)/raw:
	mkdir -p $(DATA)/raw

download-extract: | $(DATA)/raw
	curl -fL --retry 3 -C - -o $(DATA)/raw/$(notdir $(EXTRACT)).osm.pbf $(EXTRACT_URL)

download-planet: | $(DATA)/raw
	curl -fL --retry 3 -C - -o $(DATA)/raw/planet.osm.pbf $(PLANET_URL)

# IN defaults to whatever single .pbf sits in data/raw/
IN ?= $(firstword $(wildcard $(DATA)/raw/*.osm.pbf))
prefilter:
	@test -n "$(IN)" || { echo "no input PBF — run make download-extract first"; exit 1; }
	bash extract/prefilter.sh "$(IN)" $(DATA)/hiking.osm.pbf

aoi:
	bash extract/aoi.sh

NAME ?= sedona
AREA ?=
MINLEN ?= 0.1
assemble:
	python3 assemble/assemble.py \
	  --in $(DATA)/aoi/$(NAME).osm.pbf \
	  --out $(DATA)/aoi/$(NAME).trails.geojson \
	  --min-length-mi $(MINLEN) \
	  $(if $(strip $(AREA)),--only-area "$(AREA)")

golden:
	python3 tools/run_golden.py --hiking $(DATA)/hiking.osm.pbf

verify-golden:
ifdef SNAP
	python3 tools/verify_golden.py --snap $(DATA)/hiking.osm.pbf
else
	python3 tools/verify_golden.py
endif

qa:
	python3 viewer/serve.py

test:
	python3 -m unittest discover -s tools -p 'test_*.py'
	python3 -m unittest discover -s assemble -p 'test_*.py'
