#!/usr/bin/env python3
"""seeds-include.txt effectiveness + safety-floor tests (change #3, no
network). The include path is factored around passes_safety_floor(): an
include-listed name bypasses ONLY the name/protect-class quality check, never
the private/red-flag floor. Exclude beats include."""
import importlib.util
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_scdir = Path(__file__).resolve().parent
_sc_spec = importlib.util.spec_from_file_location(
    "sc", _scdir / "_seed_constants.py")
sc = importlib.util.module_from_spec(_sc_spec)
_sc_spec.loader.exec_module(sc)

_sa_spec = importlib.util.spec_from_file_location(
    "sa", _scdir / "seed-areas.py")
sa = importlib.util.module_from_spec(_sa_spec)
_sa_spec.loader.exec_module(sa)


def _include_path_keeps(tags: dict) -> bool:
    """Replays seed-areas.fetch_state's include-branch decision on a tag
    dict: an included area is kept iff it has a non-empty name and clears the
    safety floor (no network, no quality/keyword requirement)."""
    name_lower = (tags.get("name") or "").strip().lower()
    if not name_lower:
        return False
    return sc.passes_safety_floor(tags)


# ---- include bypasses quality but is still kept only via the floor ----

def test_included_name_without_keyword_or_protect_class_is_kept():
    tags = {"name": "Tiny Local Loop"}  # no keyword, no protect_class
    assert sc.is_quality(tags) is False          # normal gate would drop it
    assert _include_path_keeps(tags) is True     # include path keeps it


# ---- safety floor still enforced for an included area ----

def test_included_name_with_access_private_is_dropped():
    assert _include_path_keeps(
        {"name": "Tiny Local Loop", "access": "private"}) is False


def test_included_name_with_ownership_private_is_dropped():
    assert _include_path_keeps(
        {"name": "Tiny Local Loop", "ownership": "private"}) is False


def test_included_name_with_red_flag_is_dropped():
    # description='mine', no government operator, no 'trail' in the name →
    # red_flag fires → floor rejects even though it's include-listed.
    tags = {"name": "Old Pit Area", "description": "mine"}
    assert sc.red_flag(tags) is not None         # guard: red flag really fires
    assert _include_path_keeps(tags) is False


def test_included_name_without_name_is_dropped():
    assert _include_path_keeps({"name": "   "}) is False


# ---- exclude beats include (precedence enforced in main after fetch_state) ----

def test_exclude_wins_over_include():
    name = "Tiny Local Loop"
    tags = {"name": name}  # no quality keyword or protect_class
    assert sa.is_quality(tags, code="AZ") is False

    overpass_data = {
        "elements": [{
            "type": "relation",
            "id": 123,
            "tags": tags,
            "center": {"lat": 33.4, "lon": -112.1},
        }],
    }
    stdout = io.StringIO()
    stderr = io.StringIO()

    with TemporaryDirectory() as tmp_dir:
        include_path = Path(tmp_dir) / "seeds-include.txt"
        exclude_path = Path(tmp_dir) / "seeds-exclude.txt"
        include_path.write_text("tInY lOcAl LoOp\n")
        exclude_path.write_text("TINY LOCAL LOOP\n")

        with (
            patch.object(sa, "SEED_INCLUDE", include_path),
            patch.object(sa, "SEED_EXCLUDE", exclude_path),
            patch.object(sa, "fetch_overpass", return_value=overpass_data),
            patch.object(
                sa, "fetch_region_bbox", return_value=(32.0, -115.0, 37.0, -109.0)),
            patch.object(sys, "argv", ["seed-areas.py", "--dry-run", "AZ"]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            sa.main()

    # fetch_state emitted the low-quality candidate only through the real,
    # case-insensitive include bypass. main then removed it through the real,
    # case-insensitive exclude check, so dry-run printed no candidate row.
    assert "1 candidates, 1 passed quality filter" in stderr.getvalue()
    assert stdout.getvalue() == ""


# ---- control: non-included low-quality area is still dropped normally ----

def test_non_included_low_quality_area_still_dropped():
    tags = {"name": "Tiny Local Loop"}
    # Not in any include set → normal quality gate applies → dropped.
    assert sc.is_quality(tags) is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
