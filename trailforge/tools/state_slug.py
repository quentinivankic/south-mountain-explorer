#!/usr/bin/env python3
"""Map a Geofabrik US state slug to/from the index's display name, or list all
US state slugs. Used by the batch publish workflow so it can turn a slug list
into the exact `--state` names publish_areas expects.

    python3 tools/state_slug.py all              -> alabama,alaska,arizona,...
    python3 tools/state_slug.py name new-mexico  -> New Mexico
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import _seed_constants as c  # noqa: E402


def us_states() -> list[tuple[str, str]]:
    """(slug, display-name) for every US state — the exact names in the index,
    slugged the way Geofabrik names its us/ subregions."""
    return [(name.lower().replace(" ", "-"), name)
            for code, name in c.STATE_NAMES.items()
            if code not in c.COUNTRY_CODES and "-" not in code]


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: state_slug.py all | name <slug>")
    cmd = sys.argv[1]
    if cmd == "all":
        print(",".join(slug for slug, _ in us_states()))
    elif cmd == "name" and len(sys.argv) >= 3:
        slug = sys.argv[2].strip()
        for s, n in us_states():
            if s == slug:
                print(n)
                return 0
        # unknown slug: fall back to title-case so the caller still gets a name
        print(" ".join(w.capitalize() for w in slug.split("-")))
    else:
        sys.exit("usage: state_slug.py all | name <slug>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
