#!/usr/bin/env python3
"""Tests for scripts/ca_discover.py (Canada-only discovery + draft registry-row generator)."""
import json
import os
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_discover as disc  # noqa: E402
import ca_publish as ca  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium CLI not installed")
LIVE_ID = "gamma-national-park-ca-pe"


class Osm:
    """Tiny OSM XML builder (ids ascending per type, as osmium tags-filter expects)."""

    def __init__(self):
        self.nodes, self.ways, self.rels = [], [], []
        self.nid, self.wid = 1, 1

    def _node(self, lat, lon):
        self.nodes.append((self.nid, lat, lon))
        self.nid += 1
        return self.nid - 1

    def way(self, pts, tags, closed=False):
        refs = [self._node(lat, lon) for lat, lon in pts]
        if closed:
            refs.append(refs[0])
        self.ways.append((self.wid, refs, tags))
        self.wid += 1
        return self.wid - 1

    def square(self, lat0, lon0, lat1, lon1, tags=None):
        return self.way([(lat0, lon0), (lat0, lon1), (lat1, lon1), (lat1, lon0)], tags or {}, closed=True)

    def polygon_rel(self, rid, box, tags):
        self.rels.append((rid, [("w", self.square(*box), "outer")], {"type": "boundary", **tags}))

    def trail(self, name, lat, lon0, lon1, **tags):
        return self.way([(lat, lon0), (lat, (lon0 + lon1) / 2), (lat, lon1)], {"highway": "path", "name": name, **tags})

    def xml(self):
        def t(tags):
            return "".join(f"<tag k={quoteattr(k)} v={quoteattr(v)}/>" for k, v in tags.items())
        out = ["<?xml version='1.0' encoding='UTF-8'?>", "<osm version='0.6' generator='test'>"]
        out += [f"<node id='{i}' version='1' lat='{la}' lon='{lo}'/>" for i, la, lo in self.nodes]
        out += [f"<way id='{i}' version='1'>" + "".join(f"<nd ref='{r}'/>" for r in refs) + t(tags) + "</way>"
                for i, refs, tags in self.ways]
        kinds = {"w": "way", "n": "node", "r": "relation"}
        out += [f"<relation id='{i}' version='1'>" + "".join(
            f"<member type='{kinds[k]}' ref='{r}' role='{role}'/>" for k, r, role in mem) + t(tags)
            + "</relation>" for i, mem, tags in sorted(self.rels)]
        return "\n".join(out + ["</osm>"]) + "\n"


MI_LON = 0.025          # ~1.2 mi of longitude at lat 46
A_BOX = (46.10, -63.50, 46.15, -63.40)
B_BOX = (46.105, -63.495, 46.13, -63.30)
C_BOX = (46.135, -63.46, 46.145, -63.42)


def build_fixture(root: Path) -> dict:
    o = Osm()
    # A: provincial, 4 trails; Lake Trail is also inside live C (claimed there first)
    o.polygon_rel(100, A_BOX, {"boundary": "protected_area", "name": "Alpha Provincial Park",
                               "protection_title": "Provincial Park"})
    o.trail("Ridge Trail", 46.11, -63.49, -63.49 + MI_LON)
    o.trail("Bluff Trail", 46.12, -63.49, -63.473)
    o.trail("Lake Trail", 46.14, -63.45, -63.43)
    o.trail("Pine Trail", 46.147, -63.49, -63.47)
    # B: regional, overlaps A (Ridge, Bluff); its own free trails are MTB by name or tag
    o.polygon_rel(200, B_BOX, {"boundary": "protected_area", "name": "Beta Regional Park",
                               "operator": "Capital Regional District"})
    o.trail("Creek Trail", 46.12, -63.38, -63.38 + MI_LON)
    o.trail("Jump Line", 46.115, -63.38, -63.38 + MI_LON)
    o.trail("Rocky", 46.125, -63.38, -63.38 + MI_LON, **{"mtb:scale": "2"})
    # C: the live area
    o.polygon_rel(300, C_BOX, {"boundary": "national_park", "name": "Gamma National Park"})
    # D: municipal way polygon; Summit Trail is in the live Gamma geom (name + extent dedupe)
    o.square(46.30, -63.30, 46.33, -63.20, {"leisure": "park", "name": "Delta Park"})
    for lat, name in ((46.31, "Summit Trail"), (46.32, "Valley Trail"), (46.325, "Falls Trail")):
        o.trail(name, lat, -63.29, -63.29 + MI_LON)
    # E: numbered names
    o.polygon_rel(500, (46.40, -63.70, 46.45, -63.60), {"boundary": "protected_area",
                                                         "name": "Echo Wilderness Area"})
    for lat, name in ((46.41, "12"), (46.415, "RC100"), (46.42, "Fox Trail"), (46.43, "Owl Trail"),
                      (46.44, "Elk Trail")):
        o.trail(name, lat, -63.69, -63.69 + MI_LON)
    # F: ski pistes dominate the miles
    o.polygon_rel(600, (46.20, -63.00, 46.25, -62.90), {"boundary": "protected_area",
                                                         "name": "Foxtrot Nature Reserve"})
    for lat, name in ((46.21, "Birch Trail"), (46.22, "Cedar Trail"), (46.23, "Maple Trail")):
        o.trail(name, lat, -62.99, -62.99 + MI_LON)
    for lat in (46.205, 46.215, 46.225, 46.235, 46.245):
        o.trail(f"Piste {lat}", lat, -62.99, -62.92, **{"piste:type": "nordic"})
    # G: private
    o.polygon_rel(700, (46.50, -63.20, 46.55, -63.10), {"boundary": "protected_area",
                                                         "name": "Golf Private Reserve", "access": "private"})
    for lat, name in ((46.51, "North Trail"), (46.52, "South Trail"), (46.53, "West Trail")):
        o.trail(name, lat, -63.19, -63.19 + MI_LON)
    # H: outside the province polygon
    o.polygon_rel(800, (46.20, -64.40, 46.25, -64.30), {"boundary": "protected_area",
                                                         "name": "Hotel Provincial Park"})
    for lat, name in ((46.21, "Hotel Trail"), (46.22, "Motel Trail"), (46.23, "Inn Trail")):
        o.trail(name, lat, -64.39, -64.39 + MI_LON)
    o.polygon_rel(900, (46.0, -63.9, 46.6, -62.7), {"boundary": "administrative", "admin_level": "4",
                                                     "ISO3166-2": "CA-PE", "name": "Prince Edward Island"})
    # I: already in the registry, held
    o.polygon_rel(1000, (46.52, -62.95, 46.57, -62.85), {"boundary": "protected_area",
                                                          "name": "India Provincial Park"})
    for lat, name in ((46.53, "Aspen Trail"), (46.54, "Larch Trail"), (46.55, "Spruce Trail")):
        o.trail(name, lat, -62.94, -62.94 + MI_LON)
    pbf_dir = root / "p1"
    pbf_dir.mkdir()
    (pbf_dir / "prince-edward-island.osm").write_text(o.xml(), encoding="utf-8")
    base = json.loads(ca.REGISTRY_PATH.read_text(encoding="utf-8"))
    reg = {k: v for k, v in base.items() if k != "areas"}
    reg["areas"] = [
        {"id": LIVE_ID, "province": "pe", "state": "Canada", "relation_id": 300, "name": "Gamma National Park",
         "bbox": [-63.51, 46.09, -63.37, 46.2], "center": [46.14, -63.44], "tier": 1, "hold": None, "notes": ""},
        {"id": "india-provincial-park-ca-pe", "province": "pe", "state": "Canada", "relation_id": 1000,
         "name": "India Provincial Park", "bbox": None, "center": None, "tier": 2, "hold": "few-trails",
         "notes": ""}]
    (root / "registry.json").write_text(json.dumps(reg), encoding="utf-8")
    geoms = root / "geom"
    geoms.mkdir()
    (geoms / f"{LIVE_ID}.json").write_text(json.dumps({"trails": [
        {"name": "Lake Trail", "segments": [[[46.14, -63.45], [46.14, -63.43]]]},
        {"name": "Summit Trail", "segments": [[[46.31, -63.285], [46.31, -63.27]]]}]}), encoding="utf-8")
    return {"pbf_dir": pbf_dir, "registry": root / "registry.json", "geoms": geoms}


def _run(fx: dict, root: Path, out: str = "out") -> int:
    return disc.main(["--pbf-dir", str(fx["pbf_dir"]), "--cache-dir", str(root / "cache"),
                      "--out-dir", str(root / out), "--provinces", "pe", "--registry", str(fx["registry"]),
                      "--live-geom-dir", str(fx["geoms"])])


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("disc")
    fx = build_fixture(root)
    assert _run(fx, root) == 0
    out = root / "out"
    cands = {c["name"]: c for c in json.loads((out / "candidates.json").read_text())["candidates"]}
    rows = {r["name"]: r for r in json.loads((out / "draft-rows.json").read_text())["areas"]}
    return {"root": root, "fx": fx, "out": out, "cands": cands, "rows": rows}


def test_rows_only_for_new_clean_passers(result):
    assert sorted(result["rows"]) == ["Alpha Provincial Park", "Echo Wilderness Area", "Foxtrot Nature Reserve"]
    c = result["cands"]
    assert c["Gamma National Park"]["status"] == "live"
    assert c["India Provincial Park"]["status"] == "registry-held:few-trails"
    assert c["India Provincial Park"]["clean"]["pass"] and c["India Provincial Park"]["draft"] == "-"


def test_every_row_is_held_and_loads_with_the_registry(result, tmp_path):
    rows = list(result["rows"].values())
    assert all(r["hold"] == disc.DRAFT_HOLD for r in rows)
    doc = json.loads(result["fx"]["registry"].read_text())
    doc["areas"] += rows
    path = tmp_path / "merged.json"
    path.write_text(json.dumps(doc))
    loaded = ca.load_registry(path)
    assert {r["id"] for r in rows} <= set(loaded["by_id"])
    # batch selection never picks a draft
    assert disc.DRAFT_HOLD in ca.HOLD_REASONS


def test_claim_dedupe_live_first_then_tier(result):
    c = result["cands"]
    alpha = {t["name"]: t for t in c["Alpha Provincial Park"]["trails"]}
    assert alpha["Lake Trail"]["claimed_by"] == LIVE_ID          # live polygon claims first
    assert alpha["Ridge Trail"]["claimed_by"] is None
    assert result["rows"]["Alpha Provincial Park"]["est_trails"] == 3
    beta = {t["name"]: t for t in c["Beta Regional Park"]["trails"]}
    assert beta["Ridge Trail"]["claimed_by"] == "r100"            # provincial before regional
    assert beta["Bluff Trail"]["claimed_by"] == "r100"
    delta = {t["name"]: t for t in c["Delta Park"]["trails"]}
    assert delta["Summit Trail"]["claimed_by"] == f"live-geom:{LIVE_ID}"
    assert not c["Delta Park"]["raw"]["pass"]


def test_one_trail_in_one_area_across_candidates(result):
    owners = {}
    for cand in result["cands"].values():
        if cand["status"] in ("live",) or cand["raw"]["pass"]:
            for t in cand["trails"]:
                if t["claimed_by"] is None:
                    assert t["name"] not in owners, (t["name"], owners.get(t["name"]), cand["name"])
                    owners[t["name"]] = cand["name"]


def test_mtb_numbered_ski_and_blocking_flags(result):
    c = result["cands"]
    beta = c["Beta Regional Park"]
    assert beta["raw"]["pass"] and not beta["clean"]["pass"] and "mtb" in beta["flags"]
    flags = {t["name"]: t["flags"] for t in beta["trails"]}
    assert flags["Jump Line"] == ["mtb"] and flags["Rocky"] == ["mtb"] and flags["Creek Trail"] == []
    echo = {t["name"]: t["flags"] for t in c["Echo Wilderness Area"]["trails"]}
    assert echo["12"] == ["numbered"] and echo["RC100"] == ["numbered"] and echo["Fox Trail"] == []
    assert "numbered" in c["Echo Wilderness Area"]["flags"]
    assert result["rows"]["Echo Wilderness Area"]["est_trails"] == 3
    assert "ski" in c["Foxtrot Nature Reserve"]["flags"]
    assert "ski" in result["rows"]["Foxtrot Nature Reserve"]["notes"]
    assert "private" in c["Golf Private Reserve"]["flags"]
    assert all(t["claimed_by"] is None for t in c["Golf Private Reserve"]["trails"])
    assert not c["Golf Private Reserve"]["raw"]["pass"]
    hotel = c["Hotel Provincial Park"]
    assert "outside-province" in hotel["flags"] and hotel["draft"] == "skipped:outside-province"


def test_bbox_and_centre_pass_the_prepare_and_centre_gates(result):
    for name, row in result["rows"].items():
        cand = result["cands"][name]
        boundary = cand["boundary_bbox"]
        rec = ca.check_area_geometry(row, boundary, "publish",
                                     {"tags": {"name": name, "type": "boundary"}, "count": 1, "bbox": boundary})
        assert rec["status"] == "ok", rec["problems"]
        assert row["bbox"] == ca.pad_bbox(boundary)
        pin = cand["pin"]
        assert pin["how"] == "trail-vertex" and pin["gate_offset_deg"] <= ca.CENTRE_GATE_DEG
        tb = pin["trail_bbox"]
        mid = ((tb[1] + tb[3]) / 2, (tb[0] + tb[2]) / 2)
        assert abs(mid[0] - row["center"][0]) <= 0.1 and abs(mid[1] - row["center"][1]) <= 0.1


def test_outputs_are_reproducible_and_cache_is_reused(result):
    root, fx = result["root"], result["fx"]
    cache = sorted((root / "cache").iterdir())
    stamp = [p.stat().st_mtime_ns for p in cache]
    assert _run(fx, root, "out2") == 0
    assert sorted((root / "cache").iterdir()) == cache and [p.stat().st_mtime_ns for p in cache] == stamp
    names = sorted(p.name for p in result["out"].iterdir())
    assert names == ["candidates.json", "candidates.tsv", "draft-rows.json", "manifest.json", "summary.txt"]
    for n in names:
        assert (result["out"] / n).read_bytes() == (root / "out2" / n).read_bytes(), n
    manifest = json.loads((result["out"] / "manifest.json").read_text())
    assert manifest["inputs"][0]["file"] == "prince-edward-island.osm"
    assert manifest["inputs"][0]["sha256"] == disc.sha256_file(fx["pbf_dir"] / "prince-edward-island.osm")


def test_refuses_to_write_inside_the_input_dir(result):
    fx = result["fx"]
    before = sorted(p.name for p in fx["pbf_dir"].iterdir())
    rc = disc.main(["--pbf-dir", str(fx["pbf_dir"]), "--cache-dir", str(result["root"] / "cache"),
                    "--out-dir", str(fx["pbf_dir"] / "out"), "--provinces", "pe", "--registry", str(fx["registry"]),
                    "--live-geom-dir", str(fx["geoms"])])
    assert rc == 1 and sorted(p.name for p in fx["pbf_dir"].iterdir()) == before


def test_refuses_to_overwrite_a_hardlinked_file(result, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    other = tmp_path / "elsewhere.json"
    other.write_text("keep")
    os.link(other, out / "summary.txt")
    rc = disc.main(["--pbf-dir", str(result["fx"]["pbf_dir"]), "--cache-dir", str(result["root"] / "cache"),
                    "--out-dir", str(out), "--provinces", "pe", "--registry", str(result["fx"]["registry"]),
                    "--live-geom-dir", str(result["fx"]["geoms"])])
    assert rc == 1 and other.read_text() == "keep"
    with pytest.raises(disc.DiscoverError):
        disc.guard_target(other, [other])


def _geom_row(lines, centre, aid="x-ca-pe"):
    trails = [{"name": f"T{i}", "distanceMi": 1.0, "segments": [line]} for i, line in enumerate(lines)]
    geom = {"trails": trails, "trail_count": len(trails), "total_mi": float(len(trails))}
    entry = {"id": aid, "name": "X", "relation_id": 1, "center": centre}
    return [aid, "X", "Canada", centre[0], centre[1], len(trails), float(len(trails)), 1], entry, geom


def test_place_centre_passes_check_new_row_and_fails_closed():
    one = [[(46.0, -63.0), (46.0, -62.95)], [(46.02, -63.0), (46.02, -62.97)]]
    pin = disc.place_centre(one, [-63.1, 45.9, -62.9, 46.1])
    assert pin["how"] == "trail-vertex"
    ca.check_new_row(*_geom_row(one, pin["center"]), "test")
    two = [[(46.0, -63.0), (46.0, -62.99)], [(46.3, -63.0), (46.3, -62.99)]]
    pin2 = disc.place_centre(two, [-63.1, 45.9, -62.9, 46.4])
    assert pin2["how"] == "extent-midpoint" and pin2["center"] == [46.15, -62.995]
    ca.check_new_row(*_geom_row(two, pin2["center"]), "test")
    assert disc.place_centre(two, [-63.1, 46.27, -62.9, 46.4])["center"] is None


@pytest.mark.parametrize("name,flag", [("12", True), ("RC100", True), ("Piste 3", True), ("# 4 - 5", True),
                                       ("Trail 7", True), ("Fox Trail", False), ("Highway 2 Lookout", False),
                                       ("Lac 3 Montagnes", False)])
def test_numbered_names(name, flag):
    assert bool(disc.NUMBERED.fullmatch(name)) is flag


def test_polygon_name_flags():
    fold = disc.ctf.fold
    assert disc.POLY_CONTAINER.search(fold("Kananaskis Country Public Land Use Zone"))
    assert disc.POLY_OHV.search(fold("McLean Creek Off-Highway Vehicle Public Land Use Zone"))
    assert disc.POLY_OHV.search(fold("Cataract Creek Snow Vehicle Public Land Use Zone"))
    assert not disc.POLY_OHV.search(fold("Bluerock Wildland Provincial Park"))
    assert not disc.POLY_CONTAINER.search(fold("Bluerock Wildland Provincial Park"))


def test_floor_gate_and_slug():
    assert disc.floor_gate({"a": 1.3, "b": 1.2}) == (True, 2, 2.5)
    assert disc.floor_gate({"a": 1.0, "b": 1.0})[0] is False
    assert disc.floor_gate({"a": 0.6, "b": 0.6, "c": 0.6})[0] is True
    assert disc.floor_gate({"a": 3.0, "b": 0.59})[0] is False
    assert disc.slugify("Parc national du Mont-Mégantic") == "parc-national-du-mont-megantic"
    assert disc.slugify("Øvre Bjørn & Sons") == "ovre-bjorn-and-sons"
