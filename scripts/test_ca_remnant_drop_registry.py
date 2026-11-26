#!/usr/bin/env python3
"""Registry side of the Canada remnant_drop opt-in: validation (load_registry) and the ca-run-area.sh wiring
(registry-gated; absent = no flag). The engine is tested in trailforge/assemble/test_ca_remnant_drop.py and the
publisher in trailforge/serve/test_publish_remnant_drop.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402

ROW = {"name": "Long Ridge Trail", "source": "https://parks.example.gc.ca/long-ridge",
       "reason": "stray remnant of the signed ridge hike"}
SCRIPT = Path(ca.__file__).parent / "ca-run-area.sh"


def _load(tmp_path, *entries):
    return ca.load_registry(_registry(tmp_path, list(entries)))


def test_no_key_loads_unchanged(tmp_path):
    assert _load(tmp_path, _entry(0))


def test_valid_entry_loads(tmp_path):
    reg = _load(tmp_path, _entry(0, remnant_drop=[ROW, {**ROW, "name": "Other Trail", "max_mi": 0.3}]))
    assert reg["by_id"]["area-000-ca-ns"]["remnant_drop"][1]["max_mi"] == 0.3


@pytest.mark.parametrize("bad", [
    [], {}, "x", [{**ROW, "extra": 1}], [{**ROW, "source": "http://x.example/y"}],
    [{k: v for k, v in ROW.items() if k != "reason"}], [ROW, ROW], [{**ROW, "max_mi": 0.1}],
    [{**ROW, "max_mi": 0.6}], [{**ROW, "max_mi": "0.3"}], [{**ROW, "min_dist_m": 1}],
])
def test_malformed_remnant_drop_is_rejected(tmp_path, bad):
    with pytest.raises(ca.CaError, match="remnant_drop"):
        _load(tmp_path, _entry(0, remnant_drop=bad))


def test_way_backed_entry_is_accepted(tmp_path):
    way = _entry(0, remnant_drop=[ROW])
    way["way_id"] = 7
    del way["relation_id"]
    assert _load(tmp_path, way)


def _run(tmp_path, entry):
    """Run ca-run-area.sh with a stubbed python3/aoi.sh up to the publish step; return (rc, publish argv or None)."""
    area_id = "x-ca-bc"
    (tmp_path / "trailforge/golden").mkdir(parents=True)
    (tmp_path / "trailforge/extract").mkdir(parents=True)
    (tmp_path / "trailforge/data/aoi").mkdir(parents=True)
    (tmp_path / "trailforge/extract/aoi.sh").write_text("#!/bin/bash\nexit 0\n")
    (tmp_path / f"trailforge/data/aoi/{area_id}.removed.geojson").write_text("{}")
    (tmp_path / "trailforge/golden/ca-areas.json").write_text(json.dumps({"areas": [{"id": area_id, **entry}]}))
    work = tmp_path / "w" / area_id
    work.mkdir(parents=True)
    (work / "area.json").write_text(json.dumps({"id": area_id, "status": "ok", "cross_check": "registry-relation"}))
    (work / "index.json").write_text("[]")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    stub = bin_dir / "python3"
    stub.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{log}"\nexit 0\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "AREA_ID": area_id, "AREA_NAME": "X",
           "BBOX": "-123,50,-122,51", "W": str(tmp_path / "w"), "RELATION_ID": "7", "WAY_ID": ""}
    run = subprocess.run(["bash", str(SCRIPT)], env=env, cwd=tmp_path, capture_output=True, text=True)
    lines = [x for x in log.read_text().splitlines() if "serve/publish_areas.py" in x] if log.exists() else []
    return run, (lines[0] if lines else None), work


def test_run_area_passes_the_flags_only_when_listed(tmp_path):
    plain_run, plain, _ = _run(tmp_path / "plain", {})
    assert plain is not None and "--ca-remnant-drop" not in plain
    assert plain_run.returncode == 3                                     # stops at the missing geom (stubbed publisher)
    run, listed, work = _run(tmp_path / "listed", {"remnant_drop": [ROW]})
    assert listed is not None
    assert f"--ca-remnant-drop {work}/ca-remnant_drop.json" in listed
    assert f"--ca-remnant-drop-report {work}/ca-remnant-drop-report.json" in listed
    assert json.loads((work / "ca-remnant_drop.json").read_text()) == [ROW]
    assert "--region" not in listed and "--ca-whole-route" not in listed  # the assemble step is not involved


def test_run_area_refuses_an_empty_list(tmp_path):
    run, line, _ = _run(tmp_path, {"remnant_drop": []})
    assert run.returncode == 2 and "remnant_drop must be a non-empty list" in run.stderr
    assert line is None
