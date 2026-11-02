#!/usr/bin/env python3
"""Tests for scripts/ca_trail_filter.py (the Canada post-assemble vocabulary/exclusion filter)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_trail_filter as flt  # noqa: E402


def _f(name, mi=2.0, network="", kind="trail"):
    return {"type": "Feature", "geometry": None,
            "properties": {"name": name, "length_mi": mi, "network": network, "kind": kind}}


def _names(features):
    return [f["properties"]["name"] for f in features]


def _cats(removed):
    return {f["properties"]["name"]: f["properties"]["removed_category"] for f in removed}


@pytest.mark.parametrize("name,network,category", [
    ("Trans Canada Trail", "", "ca-long-distance"),
    ("Great Trail", "", "ca-long-distance"),
    ("Sentier Transcanadien", "", "ca-long-distance"),
    ("Sentier Trans-Canada", "", "ca-long-distance"),
    ("Bruce Trail - Main", "", "ca-long-distance"),
    ("Rideau Trail", "", "ca-long-distance"),
    ("Great Divide Trail", "", "ca-long-distance"),
    ("Confederation Trail (all spurs)", "", "ca-long-distance"),
    ("Sentier international des Appalaches", "", "ca-long-distance"),
    ("Waterfront Trail", "", "ca-long-distance"),
    ("Coastal Link", "nwn", "ca-long-distance"),
    ("Some Leg", "rwn", "ca-long-distance"),
    ("Cross Country Ski Loop", "", "ca-winter-only"),
    ("Sentier de ski de fond", "", "ca-winter-only"),
    ("Snowshoe Route", "", "ca-winter-only"),
    ("Piste de motoneige", "", "ca-winter-only"),
    ("Fat Bike Loop", "", "ca-winter-only"),
    ("ATV Trail", "", "ca-motorised"),
    ("Sentier VTT du Lac", "", "ca-motorised"),
    ("OHV Connector", "", "ca-motorised"),
    ("Quad Trail", "", "ca-motorised"),
    ("Mountain Bike Park", "", "ca-bike"),
    ("MTB Flow", "", "ca-bike"),
    ("Piste cyclable du Lac", "", "ca-bike"),
    ("Old Portage", "", "ca-portage"),
    ("Portage Lac Rond", "", "ca-portage"),
    ("Shore Road", "", "ca-road-name"),
    ("Chemin du Lac", "", "ca-road-name"),
    ("Rue Principale", "", "ca-road-name"),
    ("Route 6", "", "ca-road-name"),
    ("Montée Saint-Jean", "", "ca-road-name"),
])
def test_vocabulary_drops_by_category(name, network, category):
    kept, removed = flt.filter_features([_f(name, network=network)], {})
    assert kept == [] and _cats(removed) == {name: category}
    assert removed[0]["properties"]["removed_reason"]


@pytest.mark.parametrize("name", [
    "Skyline Trail", "Cape Smokey Trail", "Mill Pond Loop", "Old Mill Road Trail", "Sentier de la Montée du Mont-Royal",
    "Boardwalk Trail", "Chemin du Roy Sentier", "Tlaqatik", "Sentier des Chutes", "Cabot Trail Lookout",
    "Skiff Lake Trail", "Rodney Point", "Mount Pisgah", "Bike Lake Trail Walk",
])
def test_real_hiking_names_are_kept(name):
    kept, removed = flt.filter_features([_f(name)], {})
    assert _names(kept) == [name] and removed == [], removed


def test_unnamed_empty_and_sans_nom_are_dropped_and_never_waivable():
    entry = {"keep_trails": [{"name": "Unnamed 12", "source": "https://x.example", "reason": "r"}]}
    feats = [_f("Unnamed 123"), _f(""), _f("Sans nom 4"), _f("Real Trail")]
    kept, removed = flt.filter_features(feats, entry)
    assert _names(kept) == ["Real Trail"]
    assert set(_cats(removed).values()) == {"ca-unnamed"} and len(removed) == 3


def test_stubs_under_0_6_mi_drop_unless_the_registry_waives_them():
    feats = [_f("Short Walk", 0.59), _f("Fine Walk", 0.6), _f("Waived Walk", 0.2)]
    kept, removed = flt.filter_features(feats, {})
    assert _names(kept) == ["Fine Walk"] and _cats(removed) == {"Short Walk": "ca-fragment", "Waived Walk": "ca-fragment"}
    waiver = {"keep_trails": [{"name": "Waived Walk", "source": "https://parks.example/walk", "reason": "official page"}]}
    kept, removed = flt.filter_features(feats, waiver)
    assert _names(kept) == ["Fine Walk", "Waived Walk"] and _names(removed) == ["Short Walk"]


def test_waiver_keeps_a_vocabulary_hit_but_the_other_rules_still_apply():
    entry = {"keep_trails": [{"name": "Ski Hill Loop", "source": "https://parks.example/x", "reason": "summer hike"}]}
    kept, removed = flt.filter_features([_f("Ski Hill Loop"), _f("Ski Hill Trail")], entry)
    assert _names(kept) == ["Ski Hill Loop"] and _cats(removed) == {"Ski Hill Trail": "ca-winter-only"}


def test_case_duplicates_keep_the_longest():
    feats = [_f("LAKE TRAIL", 1.0), _f("Lake Trail", 2.5), _f("Other", 3.0)]
    kept, removed = flt.filter_features(feats, {})
    assert _names(kept) == ["Lake Trail", "Other"] and _cats(removed) == {"LAKE TRAIL": "ca-case-duplicate"}
    kept, removed = flt.filter_features([_f("Same", 1.0), _f("Same", 1.5)], {})
    assert len(kept) == 2 and removed == []                          # identical names are the assembler's business


def test_exclude_trails_select_by_exact_name_and_fail_closed_when_nothing_matches():
    entry = {"exclude_trails": [{"id": "n:Spur", "name": "Spur", "reason": "part of Ridge"},
                                {"id": "n*:Loop", "name": "Loop", "reason": "fitness"}]}
    feats = [_f("Spur"), _f("Loop"), _f("Loop"), _f("Ridge")]
    kept, removed = flt.filter_features(feats, entry)
    assert _names(kept) == ["Ridge"] and len(removed) == 3
    assert removed[0]["properties"]["removed_category"] == "ca-curated-exclude"
    assert removed[0]["properties"]["curated_exclude_id"] == "n:Spur"
    with pytest.raises(flt.FilterError, match="matches no assembled trail"):
        flt.filter_features([_f("Ridge")], entry)
    with pytest.raises(flt.FilterError, match="unsupported exclude selector"):
        flt.filter_features([_f("Ridge")], {"exclude_trails": [{"id": "r123", "name": "X", "reason": "r"}]})


def test_name_fixes_rename_in_place_keep_osm_name_and_fail_closed():
    fix = {"osm_name": "Cape Smokey Trail", "name": "Cape Smokey Trail", "source": "https://x.example", "reason": "r"}
    with pytest.raises(flt.FilterError, match="nothing to fix"):
        flt.validate_fixes([fix])
    fix["name"] = "Cape Smokey Lookoff"
    flt.validate_fixes([fix])
    feats = [_f("Cape Smokey Trail"), _f("Other")]
    rows = flt.apply_fixes(feats, [fix])
    assert feats[0]["properties"]["name"] == "Cape Smokey Lookoff"
    assert feats[0]["properties"]["osm_name"] == "Cape Smokey Trail" and rows[0]["name"] == "Cape Smokey Lookoff"
    with pytest.raises(flt.FilterError, match="matches 0"):
        flt.apply_fixes([_f("Other")], [fix])
    with pytest.raises(flt.FilterError, match="equals another trail's name"):
        flt.apply_fixes([_f("Cape Smokey Trail"), _f("Cape Smokey Lookoff")], [fix])
    for bad in ([], [{**fix, "source": "http://x.example"}], [{**fix, "extra": 1}], [fix, fix],
                [{**fix, "name": " padded "}], [{**fix, "name": "x" * 121}]):
        with pytest.raises(flt.FilterError):
            flt.validate_fixes(bad)


def _run(tmp_path, entry, feats, removed=()):
    reg, trails, rem, rep = (tmp_path / n for n in ("reg.json", "trails.geojson", "removed.geojson", "rep.json"))
    reg.write_text(json.dumps({"areas": [{"id": "a-ca-ns", **entry}]}), encoding="utf-8")
    trails.write_text(json.dumps({"type": "FeatureCollection", "features": feats, "coverage": {"k": 1}}), encoding="utf-8")
    rem.write_text(json.dumps({"type": "FeatureCollection", "features": list(removed)}), encoding="utf-8")
    code = flt.main(["--registry", str(reg), "--area-id", "a-ca-ns", "--trails", str(trails),
                     "--removed", str(rem), "--report", str(rep)])
    return code, trails, rem, rep


def test_cli_rewrites_trails_appends_to_removed_and_reports(tmp_path):
    fix = {"osm_name": "Ridge", "name": "Ridge Walk", "source": "https://x.example", "reason": "r"}
    prior = {"type": "Feature", "geometry": None, "properties": {"name": "Noise", "removed_category": "min-length"}}
    code, trails, rem, rep = _run(tmp_path, {"trail_name_fixes": [fix]},
                                  [_f("Ridge"), _f("Trans Canada Trail")], [prior])
    assert code == 0
    fc = json.loads(trails.read_text())
    assert _names(fc["features"]) == ["Ridge Walk"] and fc["coverage"] == {"k": 1}
    assert _names(json.loads(rem.read_text())["features"]) == ["Noise", "Trans Canada Trail"]
    report = json.loads(rep.read_text())
    assert report["kept"] == 1 and report["removed_counts"] == {"ca-long-distance": 1}
    assert report["renamed"][0]["name"] == "Ridge Walk"


def test_cli_fails_closed_and_leaves_files_untouched(tmp_path, capsys):
    entry = {"exclude_trails": [{"id": "n:Ghost", "name": "Ghost", "reason": "r"}]}
    feats = [_f("Ridge")]
    code, trails, rem, _ = _run(tmp_path, entry, feats)
    assert code == 2 and "SAFETY BLOCK" in capsys.readouterr().err
    assert _names(json.loads(trails.read_text())["features"]) == ["Ridge"]
    assert json.loads(rem.read_text())["features"] == []
    reg = tmp_path / "reg.json"
    assert flt.main(["--registry", str(reg), "--area-id", "nope-ca-ns", "--trails", str(trails),
                     "--removed", str(rem), "--report", str(tmp_path / "r2.json")]) == 2
