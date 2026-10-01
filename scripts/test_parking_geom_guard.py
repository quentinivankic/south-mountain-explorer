#!/usr/bin/env python3
"""Focused tests for the persistent parking sweep writer gate."""
from __future__ import annotations

import asyncio
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOOLS = HERE / "parking-adjud" / "tools"
for value in (str(HERE), str(TOOLS)):
    if value not in sys.path:
        sys.path.insert(0, value)

import _parking_geom_guard as guard  # noqa: E402
import _seed_constants as seed_constants  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


add_parking = _load(HERE / "add-parking.py", "guard_test_add_parking")
build_sidecar = _load(HERE / "build-parking-verdicts.py", "guard_test_build_sidecar")
publish_areas = _load(
    ROOT / "trailforge" / "serve" / "publish_areas.py",
    "guard_test_publish_areas",
)
degenerate_writer = _load(
    HERE / "sweep-degenerate-trails.py", "guard_test_degenerate_writer",
)
name_writer = _load(HERE / "sweep-geom-names.py", "guard_test_name_writer")
nonhiking_writer = _load(
    HERE / "sweep-nonhiking-trails.py", "guard_test_nonhiking_writer",
)
difficulty_writer = _load(
    HERE / "recompute-difficulty.py", "guard_test_difficulty_writer",
)
merge_writer = _load(
    HERE / "merge-published-geom.py", "guard_test_merge_writer",
)
federal_writer = _load(
    HERE / "sweep-federal-parking.py", "guard_test_federal_writer",
)
boundary_writer = _load(
    HERE / "backfill-area-boundary-ids.py", "guard_test_boundary_writer",
)
elevation_writer = _load(
    ROOT / "trailforge" / "serve" / "add-elevation.py",
    "guard_test_elevation_writer",
)
to_app_writer = _load(
    ROOT / "trailforge" / "serve" / "to_app_json.py",
    "guard_test_to_app_writer",
)
build_counts_writer = _load(
    HERE / "build-trail-counts.py", "guard_test_build_counts_writer",
)

LAT = 33.5
LON = -111.9


def _install_live(geom_dir: Path) -> Path:
    root = guard.transaction_root(geom_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    live = root / guard.LIVE_JOURNAL_NAME
    live.write_text("{}")
    os.chmod(live, 0o600)
    return live


@pytest.mark.parametrize("module,worker_name,extra_args", [
    (degenerate_writer, "_run", ["--index", "unused-index.json"]),
    (name_writer, "_run", ["--index", "unused-index.json"]),
    (nonhiking_writer, "_run", [
        "--index", "unused-index.json", "--sidecar", "unused-sidecar.json",
    ]),
    (difficulty_writer, "_run", []),
    (merge_writer, "_merge_locked", [
        "--index", "unused-index.json", "--artifacts-root", "unused-artifacts",
    ]),
    (federal_writer, "_run", []),
    (boundary_writer, "_run", ["--pbf", "unused-parks.pbf"]),
    (elevation_writer, "_run", []),
], ids=[
    "degenerate", "names", "nonhiking", "difficulty", "merge", "federal",
    "boundary-ids", "elevation",
])
def test_whole_document_read_phase_starts_under_common_geom_ex_lock(
        tmp_path, monkeypatch, module, worker_name, extra_args):
    geom = tmp_path / "geom"
    geom.mkdir()
    entries = guard.trusted_fs.resource_lock_entries(
        guard.ROOT,
        guard.writer_resource_modes(geom),
        guard.tr.resource_lock_path,
    )
    geom_lock = next(lock for lock, resource, _mode in entries
                     if resource == geom.resolve())

    def assert_locked(_args):
        contender = guard.trusted_fs.open_lock_file(geom_lock)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            contender.close()
        if worker_name == "_merge_locked":
            return 0, 0, []
        return 0

    monkeypatch.setattr(module, worker_name, assert_locked)
    result = module.main(["--geom-dir", str(geom), *extra_args])
    assert result == 0


@pytest.mark.parametrize("module,extra_args", [
    (federal_writer, []),
    (boundary_writer, ["--pbf", "unused-parks.pbf"]),
    (elevation_writer, []),
], ids=["federal", "boundary-ids", "elevation"])
def test_new_writer_dry_runs_do_not_take_or_create_geom_lease(
        tmp_path, monkeypatch, module, extra_args):
    geom = tmp_path / "missing-geom"

    def dry_run(args):
        assert args.dry_run is True
        assert not geom.exists()
        return 0

    def unexpected_guard(*_args, **_kwargs):
        pytest.fail("dry-run must not take the canonical geom guard")

    monkeypatch.setattr(module, "_run", dry_run)
    monkeypatch.setattr(module.geom_guard, "geom_writer", unexpected_guard)
    assert module.main([
        "--geom-dir", str(geom), "--dry-run", *extra_args,
    ]) == 0
    assert not geom.exists()


@pytest.mark.parametrize("script,args", [
    (HERE / "sweep-degenerate-trails.py", ["--index", "missing-index.json"]),
    (HERE / "sweep-geom-names.py", ["--index", "missing-index.json"]),
    (HERE / "sweep-nonhiking-trails.py", [
        "--index", "missing-index.json", "--sidecar", "missing-sidecar.json",
    ]),
    (HERE / "recompute-difficulty.py", []),
    (HERE / "merge-published-geom.py", [
        "--index", "missing-index.json", "--artifacts-root", "missing-artifacts",
    ]),
    (HERE / "sweep-federal-parking.py", []),
    (HERE / "backfill-area-boundary-ids.py", ["--pbf", "missing-parks.pbf"]),
    (ROOT / "trailforge" / "serve" / "add-elevation.py", []),
], ids=[
    "degenerate", "names", "nonhiking", "difficulty", "merge", "federal",
    "boundary-ids", "elevation",
])
def test_cli_geom_writers_refuse_live_journal_before_reading_inputs(
        tmp_path, script, args):
    geom = tmp_path / "geom"
    geom.mkdir()
    sentinel = geom / "sentinel.json"
    sentinel.write_text('{"parking":[{"lat":1,"lon":2}]}')
    before = sentinel.read_bytes()
    _install_live(geom)
    command = [
        sys.executable, str(script), "--geom-dir", str(geom), *args,
    ]
    result = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2, result.stderr
    assert "REFUSING:" in result.stderr
    assert "live parking sweep journal" in result.stderr
    assert sentinel.read_bytes() == before


def test_build_trail_counts_full_run_starts_under_common_geom_ex_lock(
        tmp_path, monkeypatch):
    geom = tmp_path / "geom"
    geom.mkdir()
    entries = guard.trusted_fs.resource_lock_entries(
        guard.ROOT,
        guard.writer_resource_modes(geom),
        guard.tr.resource_lock_path,
    )
    geom_lock = next(lock for lock, resource, _mode in entries
                     if resource == geom.resolve())

    def assert_locked(_args):
        contender = guard.trusted_fs.open_lock_file(geom_lock)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            contender.close()
        return 0

    monkeypatch.setattr(build_counts_writer, "GEOM_DIR", geom)
    monkeypatch.setattr(build_counts_writer, "_run", assert_locked)
    assert build_counts_writer.main([]) == 0


def test_build_trail_counts_outer_guard_reenters_low_level_geom_writer(
        tmp_path, monkeypatch):
    geom = tmp_path / "geom"
    geom.mkdir()
    monkeypatch.setattr(build_counts_writer, "GEOM_DIR", geom)
    monkeypatch.setattr(seed_constants, "GEOM_DIR", geom)

    def nested_write(_args):
        seed_constants.write_geom_file(
            area_id="fixture-area-az",
            name="Fixture Area",
            state="Arizona",
            center_lat=LAT,
            center_lon=LON,
            trail_count=0,
            total_mi=0.0,
            osm_relation_id=None,
            geom_trails=[],
            geom_bbox=None,
            cached_at="2026-09-30T00:00:00Z",
        )
        return 0

    monkeypatch.setattr(build_counts_writer, "_run", nested_write)
    assert build_counts_writer.main([]) == 0
    document = json.loads((geom / "fixture-area-az.json").read_text())
    assert document["id"] == "fixture-area-az"


def test_build_trail_counts_refuses_live_before_missing_index_read(
        tmp_path, monkeypatch, capsys):
    geom = tmp_path / "geom"
    geom.mkdir()
    sentinel = geom / "sentinel.json"
    sentinel.write_bytes(b'{"unchanged":true}')
    before = sentinel.read_bytes()
    _install_live(geom)
    monkeypatch.setattr(build_counts_writer, "GEOM_DIR", geom)
    monkeypatch.setattr(
        build_counts_writer, "INDEX_PATH", tmp_path / "missing-index.json",
    )

    assert build_counts_writer.main([]) == 2
    assert "live parking sweep journal" in capsys.readouterr().err
    assert sentinel.read_bytes() == before


def test_build_trail_counts_cache_only_does_not_take_geom_guard(monkeypatch):
    monkeypatch.setattr(
        build_counts_writer, "_run", lambda args: 0 if args.cache_only else 1,
    )

    def unexpected_guard(*_args, **_kwargs):
        pytest.fail("cache-only build must not take the canonical geom guard")

    monkeypatch.setattr(build_counts_writer.geom_guard, "geom_writer", unexpected_guard)
    assert build_counts_writer.main(["--cache-only"]) == 0


def test_seed_constants_write_geom_file_refuses_live_journal(
        tmp_path, monkeypatch):
    geom = tmp_path / "geom"
    geom.mkdir()
    sentinel = geom / "sentinel.json"
    sentinel.write_bytes(b'{"unchanged":true}')
    before = sentinel.read_bytes()
    _install_live(geom)
    monkeypatch.setattr(seed_constants, "GEOM_DIR", geom)

    with pytest.raises(guard.LiveSweepInProgress):
        seed_constants.write_geom_file(
            area_id="fixture-area-az",
            name="Fixture Area",
            state="Arizona",
            center_lat=LAT,
            center_lon=LON,
            trail_count=0,
            total_mi=0.0,
            osm_relation_id=None,
            geom_trails=[],
            geom_bbox=None,
            cached_at="2026-09-30T00:00:00Z",
        )
    assert sentinel.read_bytes() == before
    assert not (geom / "fixture-area-az.json").exists()


def _to_app_args(input_path: Path, output_path: Path, index_path: Path) -> list[str]:
    return [
        "--in", str(input_path),
        "--area-id", "fixture-area-az",
        "--name", "Fixture Area",
        "--state", "Arizona",
        "--center-lat", str(LAT),
        "--center-lon", str(LON),
        "--index", str(index_path),
        "--out", str(output_path),
    ]


def test_to_app_json_refuses_canonical_output_before_missing_input_read(
        tmp_path, monkeypatch, capsys):
    geom = tmp_path / "geom"
    geom.mkdir()
    output = geom / "fixture-area-az.json"
    output.write_bytes(b'{"unchanged":true}')
    before = output.read_bytes()
    _install_live(geom)
    monkeypatch.setattr(guard, "CANONICAL_GEOM_DIR", geom.resolve())

    result = to_app_writer.main(_to_app_args(
        tmp_path / "missing-input.geojson",
        output,
        tmp_path / "missing-index.json",
    ))
    assert result == 2
    assert "live parking sweep journal" in capsys.readouterr().err
    assert output.read_bytes() == before


def test_to_app_json_artifact_output_stays_unguarded_and_atomic(
        tmp_path, monkeypatch):
    geom = tmp_path / "geom"
    geom.mkdir()
    _install_live(geom)
    monkeypatch.setattr(guard, "CANONICAL_GEOM_DIR", geom.resolve())
    input_path = tmp_path / "input.geojson"
    input_path.write_text(json.dumps({
        "features": [{
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[-111.9, 33.5], [-111.901, 33.501]]],
            },
            "properties": {
                "name": "Fixture Trail", "kind": "trail", "length_mi": 0.1,
            },
        }],
    }))
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output = artifact_dir / "fixture-area-az.json"

    assert to_app_writer.main(_to_app_args(
        input_path, output, tmp_path / "missing-index.json",
    )) == 0
    assert json.loads(output.read_text())["id"] == "fixture-area-az"


def test_reentrant_geom_writer_keeps_both_leases_against_process(tmp_path):
    geom = tmp_path / "geom"
    geom.mkdir()
    probe = r'''
import fcntl
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import _parking_geom_guard as guard

geom = Path(sys.argv[2])
entries = guard.trusted_fs.resource_lock_entries(
    guard.ROOT, guard.writer_resource_modes(geom), guard.tr.resource_lock_path,
)
locks = {resource: lock_path for lock_path, resource, _mode in entries}
child = r"""
import fcntl
import sys

for path in sys.argv[1:]:
    with open(path, "r+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("blocked")
        else:
            print("acquired")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
"""
with guard.geom_writer(geom):
    with guard.geom_writer(geom):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                child,
                str(locks[geom.resolve()]),
                str(locks[guard.live_journal_path(geom)]),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            raise SystemExit(result.returncode)
        print(result.stdout, end="")
'''
    result = subprocess.run(
        [sys.executable, "-c", probe, str(HERE), str(geom)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["blocked", "blocked"]


def test_copied_context_cannot_reenter_an_expired_writer_lease(tmp_path):
    geom = tmp_path / "geom"
    geom.mkdir()
    with guard.geom_writer(geom):
        stale = copy_context()

    def reenter():
        with guard.geom_writer(geom):
            return "entered"

    entries = guard.trusted_fs.resource_lock_entries(
        guard.ROOT, guard.writer_resource_modes(geom),
        guard.tr.resource_lock_path,
    )
    geom_lock = next(
        lock for lock, resource, _mode in entries if resource == geom.resolve()
    )
    with guard.trusted_fs.open_lock_file(geom_lock) as contender:
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(guard.StaleWriterLease, match="expired"):
            stale.run(reenter)
        fcntl.flock(contender.fileno(), fcntl.LOCK_UN)


def test_async_child_cannot_outlive_parent_writer_lease(tmp_path):
    geom = tmp_path / "geom"
    geom.mkdir()

    async def scenario():
        release = asyncio.Event()

        async def child():
            await release.wait()
            with guard.geom_writer(geom):
                return "entered"

        with guard.geom_writer(geom):
            task = asyncio.create_task(child())
        release.set()
        with pytest.raises(guard.StaleWriterLease, match="expired"):
            await task

    asyncio.run(scenario())


def _configure_add_parking(tmp_path, monkeypatch, *, mutate_sidecar=False):
    geom = tmp_path / "geom"
    geom.mkdir()
    target = geom / "fixture-area-az.json"
    target.write_text(json.dumps({
        "id": "fixture-area-az",
        "bbox": [LON - 0.01, LAT - 0.01, LON + 0.01, LAT + 0.01],
        "trails": [{"id": "trail/1", "segments": [[[LAT, LON], [LAT, LON + 0.001]]]}],
    }))
    sidecar = tmp_path / "parking-verdicts.json"
    sidecar.write_text(json.dumps({"version": 1, "lots": {}}))

    class Local:
        def parking_elements(self, _bbox):
            return {"elements": [{
                "type": "way", "id": 1,
                "center": {"lat": LAT, "lon": LON},
                "tags": {"amenity": "parking", "name": "Fixture lot"},
            }]}

    monkeypatch.setattr(add_parking, "GEOM_DIR", geom)
    monkeypatch.setattr(
        add_parking, "geom_by_state", lambda: {"Arizona": [target]},
    )
    monkeypatch.setattr(
        add_parking, "_state_boundaries",
        lambda _files: ({"fixture-area-az": []}, 1, True),
    )
    changed = {"done": False}

    def parking_for_area(_geom, lots, _trailheads, **_kwargs):
        if mutate_sidecar and not changed["done"]:
            changed["done"] = True
            sidecar.write_text(json.dumps({
                "version": 1,
                "lots": {
                    "way/1": {
                        "verdict": "DROP", "reason": "too-far",
                        "lat": LAT, "lon": LON, "rings": [],
                        "osm": ["way/1"], "evidence": {"serves": "fixture"},
                    },
                },
            }))
        return lots

    monkeypatch.setattr(add_parking, "parking_for_area", parking_for_area)
    monkeypatch.setattr(add_parking, "check_golden", lambda _counts: True)
    return geom, target, sidecar, Local()


def test_add_parking_refuses_live_journal_at_commit_without_geom_write(
        tmp_path, monkeypatch):
    geom, target, sidecar, local = _configure_add_parking(tmp_path, monkeypatch)
    before = target.read_bytes()
    _install_live(geom)
    with pytest.raises(guard.LiveSweepInProgress):
        add_parking.process(
            ["az"], False, use_federal=False, local=local,
            verdicts_path=str(sidecar),
        )
    assert target.read_bytes() == before


def test_add_parking_stale_sidecar_cannot_resurrect_drop(
        tmp_path, monkeypatch):
    _geom, target, sidecar, local = _configure_add_parking(
        tmp_path, monkeypatch, mutate_sidecar=True,
    )
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="sidecar changed.*refusing all geom writes"):
        add_parking.process(
            ["az"], False, use_federal=False, local=local,
            verdicts_path=str(sidecar),
        )
    assert target.read_bytes() == before
    assert "parking" not in json.loads(target.read_text())


@pytest.mark.parametrize("raw", [
    b'{"version":1,"lots":{},"lots":{}}',
    b'{"version":1,"lots":{"way/1":{"verdict":"DROP"}}}',
])
def test_add_parking_refuses_existing_invalid_sidecar_before_geom_work(
        tmp_path, monkeypatch, raw):
    _geom, target, sidecar, local = _configure_add_parking(tmp_path, monkeypatch)
    sidecar.write_bytes(raw)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="existing parking verdict sidecar is invalid"):
        add_parking.process(
            ["az"], False, use_federal=False, local=local,
            verdicts_path=str(sidecar),
        )
    assert target.read_bytes() == before


def test_add_parking_rechecks_every_geom_source_before_first_write(
        tmp_path, monkeypatch):
    geom, target, sidecar, local = _configure_add_parking(tmp_path, monkeypatch)
    skipped = geom / "a-skipped-area-az.json"
    skipped.write_text(json.dumps({"id": "a-skipped-area-az", "trails": []}))
    monkeypatch.setattr(
        add_parking, "geom_by_state",
        lambda: {"Arizona": [skipped, target]},
    )
    target_before = target.read_bytes()
    mutated = {"done": False}

    def mutate_captured_source(_geom, lots, _trailheads, **_kwargs):
        if not mutated["done"]:
            mutated["done"] = True
            skipped.write_text(json.dumps({
                "id": "a-skipped-area-az", "trails": [], "drift": True,
            }))
        return lots

    monkeypatch.setattr(add_parking, "parking_for_area", mutate_captured_source)
    with pytest.raises(RuntimeError, match="canonical geom changed before parking commit"):
        add_parking.process(
            ["az"], False, use_federal=False, local=local,
            verdicts_path=str(sidecar),
        )
    assert target.read_bytes() == target_before
    assert json.loads(skipped.read_text())["drift"] is True


def test_publish_areas_refuses_canonical_live_journal_before_input_reads(
        tmp_path, monkeypatch, capsys):
    geom = tmp_path / "geom"
    geom.mkdir()
    _install_live(geom)
    monkeypatch.setattr(guard, "CANONICAL_GEOM_DIR", geom.resolve())
    result = publish_areas.main([
        "--trails", str(tmp_path / "missing-trails.json"),
        "--hiking", str(tmp_path / "missing-hiking.pbf"),
        "--index", str(tmp_path / "missing-index.json"),
        "--out-dir", str(geom),
        "--state", "Arizona",
    ])
    assert result == 2
    assert "REFUSING:" in capsys.readouterr().err


def test_sidecar_builder_refuses_publish_while_live_journal_exists(
        tmp_path, monkeypatch, capsys):
    geom = tmp_path / "geom"
    geom.mkdir()
    sidecar = tmp_path / "parking-verdicts.json"
    _install_live(geom)
    monkeypatch.setattr(guard, "CANONICAL_GEOM_DIR", geom.resolve())
    monkeypatch.setattr(guard, "CANONICAL_SIDECAR", sidecar.resolve())

    @contextmanager
    def fake_snapshot(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(build_sidecar, "_validated_snapshot_scope", fake_snapshot)
    monkeypatch.setattr(
        build_sidecar, "_build_with_snapshot",
        lambda *_args: pytest.fail("builder read after persistent gate should refuse"),
    )
    assert build_sidecar.main([
        "--data-dir", str(tmp_path / "unused-data"), "--out", str(sidecar),
    ]) == 2
    assert "REFUSING sidecar publish" in capsys.readouterr().err
    assert not sidecar.exists()


@pytest.mark.parametrize("target_kind", ["geom", "transaction"])
def test_sidecar_builder_cannot_target_geom_or_sweep_control_state(
        tmp_path, monkeypatch, capsys, target_kind):
    geom = tmp_path / "geom"
    geom.mkdir()
    live = _install_live(geom)
    monkeypatch.setattr(guard, "CANONICAL_GEOM_DIR", geom.resolve())
    monkeypatch.setattr(
        guard, "CANONICAL_SIDECAR", (tmp_path / "parking-verdicts.json").resolve(),
    )
    target = (
        geom / "victim-area.json"
        if target_kind == "geom"
        else live.parent / "forged-output.json"
    )
    target.write_bytes(b"ORIGINAL")
    before = target.read_bytes()

    @contextmanager
    def forbidden_snapshot(*_args, **_kwargs):
        pytest.fail("forbidden sidecar destination reached source snapshot")
        yield  # pragma: no cover

    monkeypatch.setattr(
        build_sidecar, "_validated_snapshot_scope", forbidden_snapshot
    )
    assert build_sidecar.main([
        "--data-dir", str(tmp_path / "unused-data"), "--out", str(target),
    ]) == 1
    assert "may not target canonical geom" in capsys.readouterr().err
    assert target.read_bytes() == before
    assert live.exists()


def test_sidecar_builder_adds_live_resource_to_its_single_sorted_lease(
        tmp_path, monkeypatch):
    geom = tmp_path / "geom"
    geom.mkdir()
    sidecar = tmp_path / "parking-verdicts.json"
    monkeypatch.setattr(guard, "CANONICAL_GEOM_DIR", geom.resolve())
    monkeypatch.setattr(guard, "CANONICAL_SIDECAR", sidecar.resolve())
    captured = {}

    class Replay:
        @staticmethod
        def validated_store_snapshot(*args, **kwargs):
            captured.update(kwargs)

            @contextmanager
            def scope():
                yield object()

            return scope()

    monkeypatch.setattr(build_sidecar, "_replay_module", lambda: Replay)
    with build_sidecar._validated_snapshot_scope(
            str(tmp_path), output_path=str(sidecar), output_exclusive=True):
        pass
    assert captured["additional_resource_modes"] == ((
        guard.live_journal_path(geom), fcntl.LOCK_SH,
    ),)


def test_writer_lock_set_is_globally_sorted_collapsed_and_released(tmp_path):
    geom = tmp_path / "geom"
    geom.mkdir()
    sidecar = tmp_path / "parking-verdicts.json"
    sidecar.write_text("{}")
    resources = guard.writer_resource_modes(
        geom,
        ((sidecar, fcntl.LOCK_SH), (geom, fcntl.LOCK_SH)),
    )
    entries = guard.trusted_fs.resource_lock_entries(
        guard.ROOT, resources, guard.tr.resource_lock_path,
    )
    assert [str(entry[0]) for entry in entries] == sorted(
        str(entry[0]) for entry in entries
    )
    modes = {resource: mode for _lock, resource, mode in entries}
    assert modes[geom.resolve()] == fcntl.LOCK_EX
    assert modes[sidecar.resolve()] == fcntl.LOCK_SH
    assert modes[guard.live_journal_path(geom)] == fcntl.LOCK_SH

    geom_lock = next(lock for lock, resource, _mode in entries
                     if resource == geom.resolve())
    with guard.geom_writer(
            geom, additional_resource_modes=((sidecar, fcntl.LOCK_SH),)):
        contender = guard.trusted_fs.open_lock_file(geom_lock)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            contender.close()
    contender = guard.trusted_fs.open_lock_file(geom_lock)
    try:
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(contender.fileno(), fcntl.LOCK_UN)
    finally:
        contender.close()


def test_distinct_resources_cannot_collapse_to_one_lock_path(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    with pytest.raises(ValueError, match="distinct resources"):
        guard.trusted_fs.resource_lock_entries(
            tmp_path,
            ((first, fcntl.LOCK_SH), (second, fcntl.LOCK_EX)),
            lambda _root, _resource: tmp_path / "same.lock",
        )
