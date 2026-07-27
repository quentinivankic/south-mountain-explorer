"""Tests for the ~zero-length trail gate. Run: python3 trailforge/serve/test_degenerate.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import degenerate as dg  # noqa: E402


def T(name, segments, mi=None):
    t = {"name": name, "segments": segments}
    if mi is not None:
        t["distanceMi"] = mi
    return t


# A real trail, ~0.5 mi north-south.
REAL = T("Real Trail", [[[40.0000, -105.0], [40.0072, -105.0]]], 0.5)


def test_a_point_trail_with_no_neighbour_is_isolated():
    # start == end: exactly the Grayling Unit artifact (Gorget/Tabitha/West Gobblers).
    stub = T("Gorget Trail", [[[41.0, -84.0], [41.0, -84.0]]], 0.0)
    assert dg.classify([REAL, stub]) == [dg.KEEP, "isolated"]


def test_a_stub_hanging_off_one_trail_is_a_spur():
    # Shares REAL's northern endpoint; the other end dangles a node away
    # (0.0001 deg latitude is ~11 m, so still under the 0.01 mi / ~16 m floor).
    stub = T("Stub", [[[40.0072, -105.0], [40.0073, -105.0]]], 0.0)
    assert dg.classify([REAL, stub]) == [dg.KEEP, "spur"]


def test_a_stub_joining_two_trails_is_a_connector_and_is_KEPT():
    # THE case that makes this a connectivity rule rather than a length filter:
    # a ~zero-length way with both ends on other trails IS the junction. The
    # 0.11 mi "(Nordic)" link in a ski loop is the real-world example.
    other = T("Other Trail", [[[40.0080, -105.0], [40.0150, -105.0]]], 0.5)
    link = T("(Nordic)", [[[40.0072, -105.0], [40.0080, -105.0]]], 0.0)
    assert dg.classify([REAL, other, link]) == [dg.KEEP, dg.KEEP, dg.KEEP]


def test_no_usable_geometry_is_dropped():
    assert dg.classify([REAL, T("Ghost", [])]) == [dg.KEEP, "noseg"]
    assert dg.classify([REAL, T("OnePoint", [[[41.0, -84.0]]])]) == [dg.KEEP, "noseg"]


def test_a_short_but_real_trail_survives():
    # 0.1-0.3 mi holds genuine preserve loops. Dropping under 0.15 mi was measured
    # to empty 6 areas and gut 30%+ of the trails in 31 more — hence 0.01 mi.
    short = T("Susquehanna Wetland Trail", [[[40.0, -105.0], [40.0018, -105.0]]], 0.12)
    assert dg.classify([short]) == [dg.KEEP]


def test_prune_never_empties_an_area():
    # An area of nothing but stubs has a BOUNDARY problem, not a trail problem.
    # Emptying it would make it vanish from the app; _MIN_AREA_MI is that gate.
    stubs = [T("A", [[[41.0, -84.0], [41.0, -84.0]]], 0.0),
             T("B", [[[42.0, -85.0], [42.0, -85.0]]], 0.0)]
    kept, why = dg.prune(stubs)
    assert len(kept) == 2 and why == {}, (kept, why)


def test_prune_reports_what_it_removed():
    stub = T("Gorget Trail", [[[41.0, -84.0], [41.0, -84.0]]], 0.0)
    kept, why = dg.prune([REAL, stub])
    assert [t["name"] for t in kept] == ["Real Trail"]
    assert dict(why) == {"isolated": 1}, why


def test_area_miles_sums_the_displayed_per_trail_values():
    # Must equal the sum of the trail rows the user sees, not a fresh haversine.
    a, b = T("A", [[[40.0, -105.0], [40.0072, -105.0]]], 1.24), \
        T("B", [[[41.0, -105.0], [41.0072, -105.0]]], 2.32)
    # Same `round(sum, 1)` as to_app_json.convert, ties included, so the two
    # cannot disagree on an area's total.
    assert dg.area_miles([a, b]) == round(1.24 + 2.32, 1) == 3.6
    # No distanceMi -> fall back to geometry rather than counting it as zero.
    assert dg.area_miles([T("C", [[[40.0, -105.0], [40.0072, -105.0]]])]) > 0.4


def test_junction_tolerance_is_metres_not_exact_nodes():
    # 4 dp is ~11 m. Two endpoints that near each other are treated as one
    # junction, so a sloppily-drawn connection is not mistaken for an isolated
    # stub. This is the deliberate conservative reading: it drops 251 trails
    # nationally where exact node identity would drop 289.
    # These two coordinates are ~6.7 m apart and are NOT the same node, but they
    # round to the same one, so the link counts as attached at that end.
    other = T("Other", [[[40.00734, -105.0], [40.0150, -105.0]]], 0.5)
    link = T("Link", [[[40.0072, -105.0], [40.00728, -105.0]]], 0.0)
    assert other["segments"][0][0] != link["segments"][0][1]
    assert dg.classify([REAL, other, link])[2] == dg.KEEP
    assert dg.trail_miles(link) < 0.01


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
