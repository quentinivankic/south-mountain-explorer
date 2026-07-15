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


if __name__ == "__main__":
    unittest.main()
