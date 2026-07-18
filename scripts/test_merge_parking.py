#!/usr/bin/env python3
"""Regression: a whole-US republish (merge-published-geom.py) must PRESERVE the
parking layer add-parking.py wrote, not wipe it with the parking-less artifact
geom. File-I/O only — no network."""
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_MERGE = os.path.join(_HERE, "merge-published-geom.py")


def test_merge_preserves_parking():
    with tempfile.TemporaryDirectory() as td:
        geom_dir = os.path.join(td, "geom")
        art_dir = os.path.join(td, "artifacts", "region1")
        os.makedirs(geom_dir)
        os.makedirs(art_dir)
        slug = "aravaipa-canyon-wilderness-az"
        parking = [{"lat": 32.881532, "lon": -110.437342, "source": "blm", "trailhead": True}]

        # Shipped geom (has parking from add-parking.py).
        json.dump({"id": slug, "trail_count": 3, "total_mi": 5.0,
                   "trails": [{"id": "t1"}], "parking": parking},
                  open(os.path.join(geom_dir, f"{slug}.json"), "w"))
        # Fresh artifact from a republish — rebuilt from assembly, NO parking.
        json.dump({"id": slug, "trail_count": 3, "total_mi": 5.1,
                   "trails": [{"id": "t1"}]},
                  open(os.path.join(art_dir, f"{slug}.json"), "w"))

        index = os.path.join(td, "index.json")
        json.dump([[slug, "Aravaipa Canyon Wilderness", "Arizona", 32.9, -110.4, 3, 5.0]],
                  open(index, "w"))

        r = subprocess.run(
            [sys.executable, _MERGE, "--artifacts-root", os.path.join(td, "artifacts"),
             "--geom-dir", geom_dir, "--index", index],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

        merged = json.load(open(os.path.join(geom_dir, f"{slug}.json")))
        assert merged.get("parking") == parking, f"parking wiped: {merged.get('parking')}"
        assert merged["total_mi"] == 5.1, "artifact geom (fresh trails/mi) must still win"


if __name__ == "__main__":
    test_merge_preserves_parking()
    print("ok  test_merge_preserves_parking")
    print("\n1 passed")
