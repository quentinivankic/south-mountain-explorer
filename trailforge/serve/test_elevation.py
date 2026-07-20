#!/usr/bin/env python3
"""Unit tests for the pure elevation math (SPEC.md §6e). No network — the DEM
tile fetch is exercised only on the homelab. Run: python3 -m unittest
serve.test_elevation (or python3 serve/test_elevation.py)."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elevation as e  # noqa: E402


class TerrariumDecode(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(e.terrarium_decode(128, 0, 0), 0.0)      # sea level base
        self.assertEqual(e.terrarium_decode(129, 0, 0), 256.0)    # +256 m
        self.assertAlmostEqual(e.terrarium_decode(128, 0, 128), 0.5)  # +B/256


class Densify(unittest.TestCase):
    def test_spacing(self):
        # ~300 m straight segment -> ~11 pts at 30 m
        pts = e.densify([(40.0, -105.0), (40.0 + 300 / 111320, -105.0)], 30)
        self.assertTrue(10 <= len(pts) <= 12, len(pts))

    def test_short_segment_kept(self):
        self.assertEqual(len(e.densify([(0, 0)])), 1)   # degenerate -> unchanged


class Gain(unittest.TestCase):
    def test_dense_climb_is_accurate(self):
        # 200-pt monotonic 0->305 m (1000 ft); endpoint smoothing loss negligible
        climb = [305.0 * i / 199 for i in range(200)]
        self.assertTrue(970 <= e.gain_ft(climb) <= 1000, e.gain_ft(climb))

    def test_direction_invariant(self):
        # a trail stored DOWNHILL (summit->trailhead, the Humphreys bug) must
        # give the same gain as uphill — max(ascent, descent), not uphill-only.
        climb = [305.0 * i / 199 for i in range(200)]
        self.assertEqual(e.gain_ft(climb), e.gain_ft(list(reversed(climb))))
        self.assertTrue(970 <= e.gain_ft(list(reversed(climb))) <= 1000)

    def test_noise_does_not_inflate(self):
        # ±1.5 m jitter, net zero -> smoothing + floor keep gain tiny
        jit = [50 + 1.5 * math.sin(i * 1.3) + (0.8 if i % 3 == 0 else -0.4)
               for i in range(400)]
        self.assertLess(e.gain_ft(jit), 80)

    def test_flat_is_zero(self):
        self.assertEqual(e.gain_ft([100.0] * 50), 0.0)


class Difficulty(unittest.TestCase):
    def test_spec_backwards_cases_fixed(self):
        D = e.difficulty_label
        # a 2 mi / 2,000 ft climb must read Hard (was Moderate under length-only)
        self.assertEqual(D(2, 2000), "Hard")
        # a flat 5 mi path must NOT read Hard (was Hard under length-only)
        self.assertNotEqual(D(5, 0), "Hard")
        self.assertEqual(D(16, 4800), "Hard")   # Half Dome
        self.assertEqual(D(5, 1500), "Hard")    # Angels Landing
        self.assertEqual(D(1, 0), "Easy")

    def test_distance_floor(self):
        # a long FLAT walk still escalates on distance alone
        self.assertEqual(e.difficulty_label(12, 0), "Hard")
        self.assertEqual(e.difficulty_label(5, 0), "Moderate")

    def test_steepness_floor(self):
        D = e.difficulty_label
        # Acadia's Precipice: 966 ft in 0.67 mi (~1,440 ft/mi). The NPS rating
        # alone scored 36 -> "Easy"; the grade floor must lift it off Easy.
        self.assertNotEqual(D(0.67, 966), "Easy")
        # a very steep short scramble (>=1,500 ft/mi) reads Hard
        self.assertEqual(D(0.5, 900), "Hard")      # 1,800 ft/mi
        self.assertEqual(D(0.35, 656), "Hard")     # 1,874 ft/mi (Hurricane Crag)
        # a moderately steep short pitch (>=1,000, <1,500 ft/mi) reads Moderate
        self.assertEqual(D(0.6, 720), "Moderate")  # 1,200 ft/mi
        # the floor only RAISES: a gentle grade is untouched
        self.assertEqual(D(1.0, 300), "Easy")      # 300 ft/mi
        self.assertEqual(D(3.0, 100), "Easy")      # flat-ish nature trail

    def test_sac_override(self):
        self.assertEqual(e.difficulty_label(0.5, 0, sac="demanding_alpine_hiking"), "Hard")

    def test_no_dem_fallback(self):
        # gain_ft=None -> legacy length-only behaviour preserved
        self.assertEqual(e.difficulty_label(4.5, None), "Hard")
        self.assertEqual(e.difficulty_label(1.0, None), "Easy")
        self.assertEqual(e.difficulty_label(3.0, None), "Moderate")


class TrailGain(unittest.TestCase):
    def test_trail_gain_uses_sampler(self):
        # a synthetic linear ramp sampler: elevation rises with latitude
        class Ramp:
            def elevation(self, lat, lon):
                return lat * 111320  # 1 m per 1/111320 deg lat ≈ metres north
        # a ~2,000 m northward segment -> ~2,000 m climb -> ~6,562 ft. Long
        # enough (~67 densified pts) that endpoint smoothing loss is small.
        seg = [[0.0, 0.0], [2000 / 111320, 0.0]]
        g = e.trail_gain_ft([seg], Ramp())
        self.assertTrue(6300 <= g <= 6600, g)


class Profile(unittest.TestCase):
    """profile_ft() — the app's elevation-profile series. Direction is
    arbitrary by design (the app anchors on the hiker's position), so these
    assert SHAPE and SPACING, never "index 0 is the trailhead"."""

    class _Ramp:
        def elevation(self, lat, lon):
            return lat * 111320  # metres north — monotonic climb

    def _sampled(self, seg):
        return e.sample_segments([seg], self._Ramp())

    def test_monotonic_climb_yields_ascending_series(self):
        seg = [[0.0, 0.0], [2000 / 111320, 0.0]]
        p = e.profile_ft(self._sampled(seg))
        self.assertGreaterEqual(len(p), 8)
        self.assertEqual(p, sorted(p), p)              # ramp -> ascending
        self.assertGreater(p[-1] - p[0], 6000)         # ~2,000 m in feet

    def test_point_count_scales_with_length_and_caps(self):
        short = e.profile_ft(self._sampled([[0.0, 0.0], [200 / 111320, 0.0]]))
        self.assertEqual(len(short), 8)                # floor, not 1 sample
        # ~30 mi north — well past the 64-point cap.
        lon = [[0.0, 0.0], [48000 / 111320, 0.0]]
        self.assertEqual(len(e.profile_ft(self._sampled(lon), max_points=64)), 64)

    def test_evenly_spaced_by_distance(self):
        # On a linear ramp, even DISTANCE spacing means even ELEVATION steps.
        p = e.profile_ft(self._sampled([[0.0, 0.0], [3000 / 111320, 0.0]]))
        steps = [b - a for a, b in zip(p, p[1:])]
        self.assertLess(max(steps) - min(steps), max(steps) * 0.25, steps)

    def test_degenerate_inputs_return_empty(self):
        self.assertEqual(e.profile_ft([]), [])
        # A zero-length trail has no distance to spread samples over.
        self.assertEqual(e.profile_ft(self._sampled([[1.0, 1.0], [1.0, 1.0]])), [])

    def test_reversing_the_way_reverses_the_series(self):
        # OSM way direction is arbitrary; the series follows it. Documented
        # behaviour — the app must map position->index, not assume a start.
        fwd = e.profile_ft(self._sampled([[0.0, 0.0], [1500 / 111320, 0.0]]))
        rev = e.profile_ft(self._sampled([[1500 / 111320, 0.0], [0.0, 0.0]]))
        self.assertEqual(fwd, list(reversed(rev)))

    def test_process_area_bakes_profile(self):
        seg = [[0.0, 0.0], [900 / 111320, 0.0]]
        geom = {"trails": [{"id": "t1", "distanceMi": 0.6,
                            "difficulty": "Easy", "segments": [seg]}]}
        e.process_area(geom, self._Ramp())
        t = geom["trails"][0]
        self.assertIn("profileFt", t)
        self.assertGreaterEqual(len(t["profileFt"]), 8)
        # Gain still matches the standalone path — one DEM pass, same answer.
        self.assertEqual(t["gainFt"], int(e.trail_gain_ft([seg], self._Ramp())))


class ProcessArea(unittest.TestCase):
    """process_area() is the shared core folded into publish_areas.py so gain
    survives a republish. Testable with any object exposing .elevation()."""

    class _Ramp:
        def elevation(self, lat, lon):
            return lat * 111320  # metres north — monotonic climb

    def test_sets_gain_difficulty_and_total(self):
        # 0.4 mi over ~500 m of climb (~1,250 m/mi... well past the grade floor)
        seg = [[0.0, 0.0], [500 / 111320, 0.0]]
        geom = {"trails": [{"id": "t1", "distanceMi": 0.4,
                            "difficulty": "Easy", "segments": [seg]}]}
        changed, delta = e.process_area(geom, self._Ramp())
        t = geom["trails"][0]
        self.assertEqual(changed, 1)
        self.assertGreater(t["gainFt"], 1000)          # real gain baked in
        self.assertEqual(t["difficulty"], "Hard")       # steep+short -> floor
        self.assertEqual(geom["total_gain_ft"], t["gainFt"])
        self.assertEqual(delta, {"Easy->Hard": 1})      # label change recorded

    def test_bad_tile_skips_trail_not_area(self):
        # a sampler that always throws -> the trail keeps its length-based
        # difficulty and the area still gets a (zero) total_gain_ft; no crash.
        class Boom:
            def elevation(self, lat, lon):
                raise RuntimeError("no tile")
        geom = {"trails": [{"id": "t1", "distanceMi": 2.0,
                            "difficulty": "Moderate",
                            "segments": [[[0.0, 0.0], [0.01, 0.0]]]}]}
        changed, delta = e.process_area(geom, Boom())
        self.assertEqual(changed, 0)
        self.assertEqual(geom["trails"][0]["difficulty"], "Moderate")  # untouched
        self.assertEqual(geom["total_gain_ft"], 0)


if __name__ == "__main__":
    unittest.main()


class ChainSegmentsTests(unittest.TestCase):
    """`_chain_segments` orders a trail's segments end-to-end before the profile
    is flattened.

    OSM stores segments in arbitrary order AND arbitrary direction. Walking them
    as stored adds zero distance across a seam while stepping the full elevation
    difference — an infinitely steep wall that smoothing turns into a plausible
    cliff. South Mountain's National Trail shipped with a -82.6% grade this way.
    """

    def test_reorders_shuffled_segments(self):
        """Asserts CONTIGUITY, not a direction. `profile_ft` documents direction
        as deliberately arbitrary — the app orients the series from the user —
        so an east-to-west chain is just as correct as west-to-east. What must
        hold is that consecutive pieces touch."""
        a = ([(33.30, -112.10), (33.30, -112.09)], [1000, 1010])
        c = ([(33.30, -112.07), (33.30, -112.06)], [1020, 1030])
        out = e._chain_segments([c, a])          # stored backwards
        gap = e.haversine_m(out[0][0][-1][0], out[0][0][-1][1],
                            out[1][0][0][0], out[1][0][0][1])
        self.assertLess(gap, 3000, f"pieces must be chained end-to-end, gap {gap:.0f} m")
        joined = out[0][1] + out[1][1]
        self.assertIn(joined, ([1000, 1010, 1020, 1030], [1030, 1020, 1010, 1000]),
                      f"elevations must run monotonically along the chain, got {joined}")

    def test_flips_a_segment_stored_backwards(self):
        a = ([(33.30, -112.10), (33.30, -112.09)], [1000, 1010])
        # b runs east->west, so it must be reversed to follow a
        b = ([(33.30, -112.07), (33.30, -112.08)], [1030, 1020])
        out = e._chain_segments([a, b])
        self.assertEqual(out[1][1], [1020, 1030], "backwards segment must be flipped")

    def test_start_choice_avoids_stranding_an_arm(self):
        """Greedy from a MIDDLE segment strands one arm and leaves a long jump.
        Trying every start is what dropped National Trail's worst gap from
        15,148 m to 178 m."""
        west = ([(33.30, -112.20), (33.30, -112.19)], [900, 910])
        mid = ([(33.30, -112.15), (33.30, -112.14)], [950, 960])
        east = ([(33.30, -112.10), (33.30, -112.09)], [1000, 1010])
        out = e._chain_segments([mid, west, east])   # middle first
        lons = [s[0][0][1] for s in out]
        self.assertEqual(lons, sorted(lons) if lons[0] < lons[-1] else sorted(lons, reverse=True),
                         "chain must run monotonically along the trail")

    def test_single_segment_untouched(self):
        one = ([(33.30, -112.10), (33.30, -112.09)], [1000, 1010])
        self.assertEqual(e._chain_segments([one]), [one])

    def test_profile_spans_only_segment_length_not_gaps(self):
        """`distanceMi` excludes inter-segment gaps and the app labels the axis
        from it, so the series must not stretch past the trail's own length."""
        a = ([(33.30, -112.10), (33.30, -112.09)], [1000, 1000])
        far = ([(33.30, -111.50), (33.30, -111.49)], [1000, 1000])
        prof = e.profile_ft([a, far])
        self.assertTrue(all(p == 3280 or p == 3281 for p in prof),
                        f"flat pieces must stay flat, got {set(prof)}")


class ProfileGapsTests(unittest.TestCase):
    """`profile_and_gaps` reports WHERE a trail is discontinuous.

    A gap contributes no x to the series (distanceMi excludes gaps and the app
    labels the axis from it), so a discontinuity can only be MARKED, not spaced.
    These pin the contract the app renders against.
    """

    @staticmethod
    def _flat(pts):
        return (pts, [1500.0] * len(pts))

    def test_reports_index_and_metres(self):
        a = [(47.2635, -112.5382), (47.2700, -112.5300), (47.2800, -112.5200)]
        b = [(47.3142, -112.8218), (47.3200, -112.8100), (47.3300, -112.8000)]
        prof, gaps = e.profile_and_gaps([self._flat(a), self._flat(b)])
        self.assertEqual(len(gaps), 1)
        idx, metres = gaps[0]
        self.assertTrue(0 < idx < len(prof), f"index {idx} must fall inside the series")
        self.assertGreater(metres, 805, "must report the real separation")

    def test_contiguous_trail_reports_none(self):
        a = [(47.2635, -112.5382), (47.2700, -112.5300), (47.2800, -112.5200)]
        b = [(47.2800, -112.5200), (47.2900, -112.5100)]
        self.assertEqual(e.profile_and_gaps([self._flat(a), self._flat(b)])[1], [],
                         "a joined trail has no discontinuity to mark")

    def test_sub_threshold_scrap_ignored(self):
        """798 of 3,823 affected trails total under 0.5 mi of gap. Marking a
        300 m unmapped scrap implies a problem that is not there."""
        a = [(47.2635, -112.5382), (47.2700, -112.5300), (47.2800, -112.5200)]
        near = [(47.2830, -112.5170), (47.2900, -112.5100)]
        self.assertEqual(e.profile_and_gaps([self._flat(a), self._flat(near)])[1], [])

    def test_profile_ft_wrapper_still_returns_a_bare_series(self):
        a = [(47.2635, -112.5382), (47.2700, -112.5300)]
        out = e.profile_ft([self._flat(a)])
        self.assertIsInstance(out, list)
        self.assertTrue(all(isinstance(v, int) for v in out))
