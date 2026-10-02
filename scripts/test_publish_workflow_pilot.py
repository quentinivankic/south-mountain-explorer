#!/usr/bin/env python3
"""Offline contract tests for the read-only Mols GitHub Actions pilot.

The helpers scope YAML mappings by indentation so assertions cannot be
satisfied by the generic publish job when they are meant for the pilot job.
"""
import re
from pathlib import Path

_WORKFLOW = (Path(__file__).resolve().parent.parent
             / ".github" / "workflows" / "trailforge-publish.yml")
_TEXT = _WORKFLOW.read_text(encoding="utf-8")
_INPUTS = _TEXT.split("    inputs:\n", 1)[1].split("\nconcurrency:", 1)[0]
_JOBS = _TEXT.split("\njobs:\n", 1)[1]
_PUBLISH = _JOBS.split("  publish:\n", 1)[1].split("\n  pilot:\n", 1)[0]
_PILOT = _JOBS.split("\n  pilot:\n", 1)[1]
_PILOT_STEPS = _PILOT.split("    steps:\n", 1)[1]


def _input_blocks():
    matches = list(re.finditer(r"^      ([a-z][a-z0-9_]*):\n", _INPUTS, re.M))
    blocks = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(_INPUTS)
        blocks[match.group(1)] = _INPUTS[match.end():end]
    return blocks


def _default(block):
    match = re.search(r"^        default: (.*)$", block, re.M)
    assert match, block
    return match.group(1)


def test_dispatch_has_exactly_ten_inputs_and_no_area_name_input():
    inputs = _input_blocks()
    assert len(inputs) <= 10
    assert len(inputs) == 10
    assert set(inputs) == {
        "extract", "state", "dry_run", "no_routes", "touch_report",
        "elevation", "region_code", "pilot_area_id", "pilot_bbox",
        "expected_osm_relation_id",
    }
    assert "pilot_area_name" not in inputs


def test_all_seven_generic_defaults_and_types_are_unchanged():
    inputs = _input_blocks()
    assert {_key: _default(inputs[_key]) for _key in (
        "extract", "state", "dry_run", "no_routes", "touch_report",
        "elevation", "region_code",
    )} == {
        "extract": '"north-america/us/arizona"',
        "state": '"Arizona"',
        "dry_run": "true",
        "no_routes": "false",
        "touch_report": "false",
        "elevation": "true",
        "region_code": '""',
    }
    for name in ("dry_run", "no_routes", "touch_report", "elevation"):
        assert "        type: boolean\n" in inputs[name]
    for name in ("extract", "state", "region_code"):
        assert "        type:" not in inputs[name]


def test_all_three_pilot_defaults_are_empty_strings():
    inputs = _input_blocks()
    for name in ("pilot_area_id", "pilot_bbox", "expected_osm_relation_id"):
        assert _default(inputs[name]) == '""'


def test_jobs_are_mutually_exclusive_and_permissions_are_exact():
    assert "if: ${{ github.event.inputs.pilot_area_id == '' }}" in _PUBLISH
    assert "if: ${{ github.event.inputs.pilot_area_id != '' }}" in _PILOT
    assert re.search(
        r"^    permissions:\n      contents: write\n      actions: write$",
        _PUBLISH, re.M)
    assert re.search(
        r"^    permissions:\n      contents: read$", _PILOT, re.M)
    assert "actions: write" not in _PILOT
    assert "GH_TOKEN" not in _PILOT
    assert "\npermissions:" not in _TEXT.split("\njobs:\n", 1)[0]


def test_pilot_maps_dispatch_inputs_to_environment_before_shell_use():
    env_block = _PILOT.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]
    for name in _input_blocks():
        assert f"github.event.inputs.{name}" in env_block
    assert "${{ github.event.inputs." not in _PILOT_STEPS


def test_pilot_hard_fails_every_exact_denmark_input_and_branch_guard():
    required_guards = [
        '[[ "$GITHUB_REF_NAME" == "chat/denmark-trails" ]]',
        '[[ "$PILOT_DRY_RUN" == "true" ]]',
        '[[ "$PILOT_STATE" == "Denmark" ]]',
        '[[ "$PILOT_REGION_CODE" == "dk" ]]',
        '[[ "$PILOT_EXTRACT" == "europe/denmark" ]]',
        '[[ "$PILOT_NO_ROUTES" == "false" ]]',
        '[[ "$PILOT_TOUCH_REPORT" == "false" ]]',
        '[[ "$PILOT_ELEVATION" == "false" ]]',
        '[[ "$PILOT_AREA_ID" == "nationalpark-mols-bjerge-dk" ]]',
        '[[ "$PILOT_BBOX" == "10.40,56.08,10.90,56.35" ]]',
        '[[ "$PILOT_EXPECTED_RELATION_ID" =~ ^[0-9]+$ ]]',
        '[[ "$PILOT_EXPECTED_RELATION_ID" != "0" ]]',
    ]
    for guard in required_guards:
        assert guard in _PILOT
    assert 'SEED_CODE="${PILOT_REGION_CODE^^}"' in _PILOT
    assert '[[ "$SEED_CODE" == "DK" ]]' in _PILOT


def test_runner_local_copy_is_created_and_pipeline_never_runs_in_checkout():
    assert _PILOT.count("mktemp -d") == 2
    assert "${RUNNER_TEMP:?}/mols-bjerge-work." in _PILOT
    assert "${RUNNER_TEMP:?}/mols-bjerge-qa." in _PILOT
    assert 'git archive HEAD | tar -x -C "$PILOT_REPO"' in _PILOT
    assert 'cd "$PILOT_REPO"' in _PILOT
    assert _PILOT.count('cd "$PILOT_REPO/trailforge"') >= 4
    assert "working-directory: ${{ github.workspace }}" in _PILOT
    assert "../public/areas" not in _PILOT


def test_discovery_identity_and_one_row_index_are_structurally_verified():
    assert "scripts/seed-areas.py" in _PILOT
    assert '--dry-run "$PILOT_SEED_CODE"' in _PILOT
    assert '--discovery-report "$PILOT_QA_ROOT/discovery/report.json"' in _PILOT
    assert "validate_publish_pilot.py discovery" in _PILOT
    assert '--expected-osm-relation-id "$PILOT_EXPECTED_RELATION_ID"' in _PILOT
    assert '--selected-record-out "$PILOT_QA_ROOT/discovery/selected.json"' in _PILOT
    assert '--index-out "$PILOT_INDEX"' in _PILOT
    assert 'json.load(open(sys.argv[1]))["index_row"][1]' in _PILOT
    assert 'echo "PILOT_AREA_NAME=$AREA_NAME"' in _PILOT


def test_denmark_prefilter_aoi_exact_assembly_and_routes_are_preserved():
    assert "https://download.geofabrik.de/europe/denmark-latest.osm.pbf" in _PILOT
    assert "bash extract/prefilter.sh data/raw/denmark.osm.pbf data/hiking.osm.pbf" in _PILOT
    assert 'HIKING=data/hiking.osm.pbf NAME=mols-bjerge BBOX="$PILOT_BBOX"' in _PILOT
    for argument in (
        "--in data/aoi/mols-bjerge.osm.pbf",
        "--out data/aoi/mols-bjerge.trails.geojson",
        '--only-area "$PILOT_AREA_NAME"',
        "--require-exact-area",
        '--expected-area-relation-id "$PILOT_EXPECTED_RELATION_ID"',
        "--min-length-mi 0.1",
        "--per-area-merge",
        "--region dk",
        '--report-json "$PILOT_QA_ROOT/reports/assembly.json"',
    ):
        assert argument in _PILOT
    assert "--no-routes" not in _PILOT


def test_publisher_is_temporary_dry_run_with_structured_final_validation():
    assert "serve/publish_areas.py" in _PILOT
    assert "--hiking data/aoi/mols-bjerge.osm.pbf" in _PILOT
    assert "--state Denmark" in _PILOT
    assert '--index "$PILOT_INDEX"' in _PILOT
    assert '--out-dir "$PILOT_WORK_ROOT/publish-output"' in _PILOT
    assert '--report-json "$PILOT_QA_ROOT/reports/publish.json"' in _PILOT
    assert "--no-boundary-fetch" in _PILOT
    assert "--no-elevation" in _PILOT
    assert "--touch-report" not in _PILOT
    assert "validate_publish_pilot.py\" final" in _PILOT
    assert '--result-json "$PILOT_QA_ROOT/validation/final.json"' in _PILOT


def test_complete_sha_named_artifact_upload_is_fail_closed_and_always_runs():
    for filename in (
        "mols-bjerge.raw.geojson",
        "mols-bjerge.trails.geojson",
        "mols-bjerge.removed.geojson",
        "mols-bjerge.ingest-dropped.geojson",
        "mols-bjerge.areas.geojson",
        "mols-bjerge.curation.json",
        "mols-bjerge.curation-diff.json",
        "mols-bjerge.dropped-routes.geojson",
        "discovery/report.json",
        "discovery/selected.json",
        "reports/publish.json",
        "reports/publish.log",
        "viewer/index.html",
        "viewer/serve.py",
    ):
        assert filename in _PILOT
    assert "uses: actions/upload-artifact@v4" in _PILOT
    assert "name: mols-bjerge-qa-${{ github.sha }}" in _PILOT
    assert "path: ${{ env.PILOT_QA_ROOT }}" in _PILOT
    assert "if-no-files-found: error" in _PILOT
    upload = _PILOT.split("- name: Upload Mols Bjerge QA artifact", 1)[1]
    assert "if: ${{ always() }}" in upload.split("- name:", 1)[0]


def test_checkout_integrity_gate_is_last_and_checks_tracked_staged_untracked():
    step_names = re.findall(r"^      - name: (.+)$", _PILOT, re.M)
    assert step_names[-1] == "Prove tracked checkout stayed unchanged"
    last = _PILOT.rsplit("      - name:", 1)[1]
    assert "if: ${{ always() }}" in last
    assert "git diff --exit-code HEAD --" in last
    assert "git diff --cached --exit-code" in last
    assert "git status --porcelain --untracked-files=all" in last
    assert 'if [[ -n "$STATUS" ]]' in last


def test_pilot_contains_no_persistent_or_release_mutation_commands():
    forbidden = (
        "git commit", "git push", "git pull", "gh workflow run", "r2",
        "silhouettes-from-geom", "filter-ios-bundle", "testflight",
        "git add public/areas", "public/areas/geom", "sam deploy",
    )
    lowered = _PILOT.lower()
    for command in forbidden:
        assert command not in lowered


def test_generic_publish_commands_and_conditions_remain_present():
    assert "actions/checkout@v4" in _PUBLISH
    assert "${{ github.event.inputs.extract }}-latest.osm.pbf" in _PUBLISH
    assert 'REGION_FLAG=""' in _PUBLISH
    assert '[ -n "${{ github.event.inputs.region_code }}" ]' in _PUBLISH
    assert "--per-area-merge $REGION_FLAG" in _PUBLISH
    assert "serve/publish_areas.py" in _PUBLISH
    assert "--index ../public/areas/index.json" in _PUBLISH
    assert "--out-dir ../public/areas/geom $FLAGS" in _PUBLISH
    assert _PUBLISH.count("if: ${{ github.event.inputs.dry_run == 'false' }}") == 4
    assert "git push" in _PUBLISH
    assert "gh workflow run sync-geom-to-r2.yml" in _PUBLISH


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} passed")
