#!/usr/bin/env python3
"""Offline tests for seed-areas.py's opt-in discovery report."""
import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parent / "seed-areas.py"
_SPEC = importlib.util.spec_from_file_location("seed_areas_discovery_report", _SCRIPT)
sa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sa)


def _run(argv, elements, *, excludes=""):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = root / "discovery.json"
        include_path = root / "seeds-include.txt"
        exclude_path = root / "seeds-exclude.txt"
        index_path = root / "index.json"
        cache_path = root / "cache.json"
        include_path.write_text("", encoding="utf-8")
        exclude_path.write_text(excludes, encoding="utf-8")
        expanded = [str(report) if value == "REPORT" else value for value in argv]
        with (
            patch.object(sa, "SEED_INCLUDE", include_path),
            patch.object(sa, "SEED_EXCLUDE", exclude_path),
            patch.object(sa, "INDEX_PATH", index_path),
            patch.object(sa, "CACHE_PATH", cache_path),
            patch.object(sa, "fetch_overpass", return_value={"elements": elements}),
            patch.object(
                sa,
                "fetch_region_bbox",
                return_value=(54.0, 7.0, 58.0, 13.0),
            ),
            patch.object(sys, "argv", ["seed-areas.py", *expanded]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            sa.main()
        payload = json.loads(report.read_text()) if report.exists() else None
        return payload, stdout.getvalue(), stderr.getvalue(), index_path.exists(), cache_path.exists()


def _element(osm_type, osm_id, name, *, lat=56.2, lon=10.6):
    return {
        "type": osm_type,
        "id": osm_id,
        "tags": {"name": name, "protect_class": "2"},
        "center": {"lat": lat, "lon": lon},
    }


def test_relation_id_survives_filter_exclude_and_slug_dedup_path():
    elements = [
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
        _element("relation", 9999999, "Nationalpark Mols Bjerge"),
        _element("relation", 1234, "Excluded Park"),
    ]
    payload, stdout, stderr, index_exists, cache_exists = _run(
        ["--dry-run", "--discovery-report", "REPORT", "DK"],
        elements,
        excludes="eXcLuDeD pArK\n",
    )

    assert payload == {
        "schema_version": 1,
        "requested_region_codes": ["DK"],
        "candidate_count": 1,
        "candidates": [{
            "index_row": [
                "nationalpark-mols-bjerge-dk",
                "Nationalpark Mols Bjerge",
                "Denmark",
                56.2,
                10.6,
            ],
            "osm_relation_id": 7046785,
        }],
        "attribution": "© OpenStreetMap contributors",
    }
    assert "Nationalpark Mols Bjerge" in stdout
    assert "3 candidates, 3 passed quality filter" in stderr
    assert not index_exists
    assert not cache_exists


def test_zero_candidates_write_explicit_empty_report():
    payload, stdout, _, index_exists, cache_exists = _run(
        ["--dry-run", "--discovery-report", "REPORT", "DK"],
        [],
    )

    assert payload["candidate_count"] == 0
    assert payload["candidates"] == []
    assert stdout == ""
    assert not index_exists
    assert not cache_exists


def test_way_candidate_has_no_relation_id():
    payload, _, _, _, _ = _run(
        ["--dry-run", "--discovery-report", "REPORT", "DK"],
        [_element("way", 222, "Nationalpark Mols Bjerge")],
    )

    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["osm_relation_id"] is None


def test_discovery_report_requires_dry_run():
    with TemporaryDirectory() as tmp:
        report = Path(tmp) / "discovery.json"
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", [
                "seed-areas.py", "--discovery-report", str(report), "DK"
            ]),
            redirect_stderr(stderr),
        ):
            try:
                sa.main()
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("expected argparse to reject a write-mode report")
        assert "--discovery-report requires --dry-run" in stderr.getvalue()
        assert not report.exists()


def test_omitting_report_preserves_console_only_dry_run():
    payload, stdout, _, index_exists, cache_exists = _run(
        ["--dry-run", "DK"],
        [_element("relation", 7046785, "Nationalpark Mols Bjerge")],
    )

    assert payload is None
    assert stdout.startswith("  Nationalpark Mols Bjerge")
    assert not index_exists
    assert not cache_exists
