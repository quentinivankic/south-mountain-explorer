#!/usr/bin/env python3
"""Tests for the two Canada-only pin opt-ins (registry center_cluster, trail_id_pins). Each has a
control showing an entry without the opt-in behaves exactly as before."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
REASON = "two clusters; pin on the dominant one"


def _trail(name, pts, mi, tid=None):
    return {"id": tid or name.lower().replace(" ", "-"), "name": name, "distanceMi": mi,
            "difficulty": "Easy", "segments": [pts]}


# Two clusters 1 deg apart: west (1 mi) and east (3 mi). All-trails midpoint (45.2, -62.5) is empty ground.
WEST = _trail("West Trail", [[45.2, -63.0], [45.21, -62.99]], 1.0)
EAST_A = _trail("East A", [[45.2, -62.0], [45.22, -61.98]], 2.0)
EAST_B = _trail("East B", [[45.18, -62.02], [45.2, -62.0]], 1.0)
GEOM = {"id": "area-000-ca-ns", "trail_count": 3, "total_mi": 4.0, "trails": [WEST, EAST_A, EAST_B]}
PIN = [45.2, -62.0]
ROW = ["area-000-ca-ns", "Area 0", "Canada", PIN[0], PIN[1], 3, 4.0, 1000]
CLUSTER = {"trails": ["East A", "East B"], "reason": REASON}


def _plain(center=PIN):
    return {k: v for k, v in _entry(0, center=center).items()}


# ------------------------------------------------------------ center_cluster --
def test_center_cluster_measures_the_pin_against_the_chosen_cluster():
    with pytest.raises(ca.CaError, match="0.1 deg from the trails"):          # control: no opt-in refuses
        ca.check_new_row(ROW, _plain(), GEOM, "t")
    ca.check_new_row(ROW, _plain() | {"center_cluster": CLUSTER}, GEOM, "t")
    mid = ["area-000-ca-ns", "Area 0", "Canada", 45.2, -62.5, 3, 4.0, 1000]   # control: all-trails pin passes
    ca.check_new_row(mid, _plain([45.2, -62.5]), GEOM, "t")
    with pytest.raises(ca.CaError, match="0.1 deg from"):                      # opted in: the cluster rules
        ca.check_new_row(mid, _plain([45.2, -62.5]) | {"center_cluster": CLUSTER}, GEOM, "t")


def test_center_cluster_must_be_dominant_present_and_near_the_pin():
    west = _plain([45.2, -63.0]) | {"center_cluster": {"trails": ["West Trail"], "reason": REASON}}
    row = ["area-000-ca-ns", "Area 0", "Canada", 45.2, -63.0, 3, 4.0, 1000]
    with pytest.raises(ca.CaError, match="25% of the trail miles"):
        ca.check_new_row(row, west, GEOM, "t")
    gone = _plain() | {"center_cluster": {"trails": ["East A", "Missing"], "reason": REASON}}
    with pytest.raises(ca.CaError, match="'Missing' is in the geom 0 time"):
        ca.check_new_row(ROW, gone, GEOM, "t")
    # A cluster whose midpoint sits in a gap between its own trails: midpoint ok, no point near the pin.
    ring = {"id": "area-000-ca-ns", "trail_count": 2, "total_mi": 4.0, "trails": [
        _trail("N", [[45.4, -62.0], [45.41, -62.0]], 2.0), _trail("S", [[45.0, -62.0], [44.99, -62.0]], 2.0)]}
    entry = _plain([45.2, -62.0]) | {"center_cluster": {"trails": ["N", "S"], "reason": REASON}}
    with pytest.raises(ca.CaError, match="every center_cluster trail point"):
        ca.check_new_row(["area-000-ca-ns", "Area 0", "Canada", 45.2, -62.0, 2, 4.0, 1000], entry, ring, "t")
    ca.check_new_row(["area-000-ca-ns", "Area 0", "Canada", 45.2, -62.0, 2, 4.0, 1000], _plain(), ring, "t")


@pytest.mark.parametrize("bad", [
    {"trails": [], "reason": REASON}, {"trails": ["A", "A"], "reason": REASON}, {"trails": ["A"]},
    {"trails": ["A"], "reason": " "}, {"trails": [" "], "reason": REASON}, {"trails": "A", "reason": REASON},
    {"trails": ["A"], "reason": REASON, "extra": 1}, ["A"], True,
    {"trails": ["Riviere\u0300"], "reason": REASON},                        # not NFC
])
def test_center_cluster_registry_validation(tmp_path, bad):
    with pytest.raises(ca.CaError, match="center_cluster"):
        ca.load_registry(_registry(tmp_path, [_entry(0, center_cluster=bad)]))
    with pytest.raises(ca.CaError, match="center_cluster needs a registry center"):
        ca.load_registry(_registry(tmp_path, [_entry(0, bbox=None, center=None, center_cluster=CLUSTER)]))
    assert ca.load_registry(_registry(tmp_path, [_entry(0, center_cluster=CLUSTER)]))


@pytest.mark.parametrize("aid, cluster", [
    ("kluane-national-park-and-reserve-ca-yt", ["Cottonwood Trail", "Auriol Trail", "Shorty Creek Trail",
                                                "King's Throne Trail", "Saint Elias Lake Trail",
                                                "Dezadeash River Trail"]),
    ("kootenay-national-park-ca-bc", ["Rockwall Trail", "Floe Lake Trail", "Helmet Creek and Falls Trail",
                                      "Stanley Glacier Trail", "Numa Creek Trail", "Tumbling Creek Trail",
                                      "Marble Canyon to Ochre Beds Trail", "Marble Canyon",
                                      "Ochre Beds & Paint Pots Trail", "Fireweed Loops Trail"]),
    ("mount-robson-provincial-park-ca-bc", ["Berg Lake Trail", "Resplendent Mountain Trail",
                                            "Snowbird Pass Trail", "Resplendent via Patterson Route",
                                            "Cinnamon Peak Trail", "Hargreaves Lake Route", "Mumm Basin Trail",
                                            "Lookout Trail"]),
])
def test_center_cluster_fits_the_three_live_two_cluster_parks(aid, cluster):
    """Fixture opt-ins on the committed live geoms (registry untouched): a pin on the dominant cluster's
    midpoint passes only with the opt-in."""
    geom = json.loads((ROOT / f"public/areas/geom/{aid}.json").read_text(encoding="utf-8"))
    entry = ca.load_registry()["by_id"][aid]
    pts = [p for t in geom["trails"] if t["name"] in cluster for s in t["segments"] for p in s]
    pin = [round((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2, 3),
           round((min(p[1] for p in pts) + max(p[1] for p in pts)) / 2, 3)]
    row = [aid, entry.get("display_name") or entry["name"], "Canada", *pin, geom["trail_count"],
           geom["total_mi"], *([entry["relation_id"]] if "relation_id" in entry else [])]
    with pytest.raises(ca.CaError, match="0.1 deg from the trails"):
        ca.check_new_row(row, entry | {"center": pin}, geom, "t")
    ca.check_new_row(row, entry | {"center": pin, "center_cluster": {"trails": cluster, "reason": REASON}},
                     geom, "t")


# ------------------------------------------------------------- trail_id_pins --
LIVE_ID = "sentier-de-la-rivi-re-kouchibouguac"
NEW_ID = "sentier-de-la-riviere-kouchibouguac"
PINS = [{"name": "Kouchibouguac River", "id": LIVE_ID, "reason": "keep the live id across the fold change"}]


def _publish_dir(tmp_path, pins=PINS, live=True, new_ids=(NEW_ID, "bog-la-tourbiere"),
                 live_trails=((LIVE_ID, "Kouchibouguac River"), ("bog-la-tourbiere", "Bog / La Tourbiere"))):
    d = tmp_path
    (d / "area.json").write_text(json.dumps({"id": "area-000-ca-ns", "name": "Area 0"}))
    (d / "index.json").write_text(json.dumps([["area-000-ca-ns", "Area 0", "Canada", 45.2, -62.8, None, None, 1000]]))
    (d / "pub.json").write_text(json.dumps([["area-000-ca-ns", "Area 0", "Canada", 45.2, -62.8, 2, 3.0, 1000]]))
    geom = {"id": "area-000-ca-ns", "name": "Area 0", "trail_count": 2, "total_mi": 3.0, "trails": [
        _trail("Kouchibouguac River", [[45.2, -62.8], [45.21, -62.8]], 2.0, new_ids[0]),
        _trail("Bog / La Tourbière", [[45.2, -62.7], [45.21, -62.7]], 1.0, new_ids[1])]}
    (d / "geom.json").write_bytes(json.dumps(geom, ensure_ascii=True).encode())
    (d / "live").mkdir()
    if live:
        (d / "live/area-000-ca-ns.json").write_text(json.dumps({"id": "area-000-ca-ns", "trails": [
            {"id": i, "name": n} for i, n in live_trails]}))
    entry = _entry(0) if pins is None else _entry(0, trail_id_pins=pins)
    reg = _registry(d, [entry])
    return d, reg


def _finish(d, reg):
    argv = ["finish-publish", "--index", str(d / "index.json"), "--publish-index", str(d / "pub.json"),
            "--geom", str(d / "geom.json"), "--area-json", str(d / "area.json"), "--live-geom-dir", str(d / "live")]
    return ca.main(argv + (["--registry", str(reg)] if reg else []))


def test_trail_id_pin_keeps_the_live_id_and_nothing_else_changes(tmp_path):
    d, reg = _publish_dir(tmp_path)
    assert _finish(d, reg) == 0
    trails = json.loads((d / "geom.json").read_text())["trails"]
    assert [(t["id"], t["name"]) for t in trails] == [(LIVE_ID, "Kouchibouguac River"),
                                                     ("bog-la-tourbiere", "Bog / La Tourbière")]


@pytest.mark.parametrize("use_registry", [True, False])
def test_no_pin_leaves_the_geom_bytes_untouched(tmp_path, use_registry):
    d, reg = _publish_dir(tmp_path, pins=None)
    before = (d / "geom.json").read_bytes()
    assert _finish(d, reg if use_registry else None) == 0
    assert (d / "geom.json").read_bytes() == before


@pytest.mark.parametrize("label, kw, message", [
    ("area not live", {"live": False}, "needs a live area"),
    ("id not live", {"pins": [{**PINS[0], "id": "kouchibouguac-river"}]}, "is not a live trail id"),
    ("id live on another trail", {"pins": [{**PINS[0], "id": "bog-la-tourbiere"}]}, "not on 'Kouchibouguac River'"),
    ("no such trail", {"pins": [{**PINS[0], "name": "Nope"}]}, "matches 0 trail"),
    ("stale pin", {"new_ids": (LIVE_ID, "bog-la-tourbiere")}, "the pin is stale"),
    ("collision", {"new_ids": (NEW_ID, LIVE_ID + "-1"),
                   "live_trails": ((LIVE_ID, "Kouchibouguac River"),)}, "non-unique"),
])
def test_trail_id_pin_fails_closed(tmp_path, label, kw, message, capsys):
    d, reg = _publish_dir(tmp_path, **kw)
    before = (d / "geom.json").read_bytes()
    assert _finish(d, reg) == 1, label
    assert message in capsys.readouterr().err, label
    assert (d / "geom.json").read_bytes() == before


def test_trail_id_pin_folds_accents_when_matching_the_live_name(tmp_path):
    pins = [{"name": "Bog / La Tourbière", "id": "bog-la-tourbiere-old", "reason": "accent fix"}]
    d, reg = _publish_dir(tmp_path, pins=pins, live_trails=(("bog-la-tourbiere-old", "Bog / La Tourbiere"),))
    assert _finish(d, reg) == 0
    assert json.loads((d / "geom.json").read_text())["trails"][1]["id"] == "bog-la-tourbiere-old"


def test_finish_publish_refuses_an_area_missing_from_the_registry(tmp_path, capsys):
    d, _ = _publish_dir(tmp_path)
    other = _registry(tmp_path, [_entry(1)])
    assert _finish(d, other) == 1 and "not in the Canada registry" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [
    [], {"name": "A", "id": "a", "reason": "r"}, [{"name": "A", "id": "a"}],
    [{"name": "A", "id": "A-b", "reason": "r"}], [{"name": "A", "id": "a--b", "reason": "r"}],
    [{"name": "A", "id": "a" * 65, "reason": "r"}], [{"name": " ", "id": "a", "reason": "r"}],
    [{"name": "A", "id": "a", "reason": ""}], [{"name": "A", "id": "a", "reason": "r", "x": 1}],
    [{"name": "A", "id": "a", "reason": "r"}, {"name": "A", "id": "b", "reason": "r"}],
    [{"name": "A", "id": "a", "reason": "r"}, {"name": "B", "id": "a", "reason": "r"}],
])
def test_trail_id_pins_registry_validation(tmp_path, bad):
    with pytest.raises(ca.CaError, match="trail_id_pins"):
        ca.load_registry(_registry(tmp_path, [_entry(0, trail_id_pins=bad)]))
    assert ca.load_registry(_registry(tmp_path, [_entry(0, trail_id_pins=PINS)]))


def test_run_area_passes_the_registry_to_finish_publish():
    script = (ROOT / "scripts/ca-run-area.sh").read_text()
    assert '--area-json "$A/area.json" --registry "$ROOT/trailforge/golden/ca-areas.json"' in script
