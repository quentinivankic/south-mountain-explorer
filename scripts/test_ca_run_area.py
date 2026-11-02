#!/usr/bin/env python3
"""Static contract tests for the Canada lane's shell script and workflows: they must run the GENERIC
engine path (no --region, no --dk-*, no Denmark validators or label), share the R2 sync lock, and never
import Denmark modules."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts/ca-run-area.sh").read_text()
PUBLISH = (ROOT / ".github/workflows/trailforge-publish-ca.yml").read_text()
SYNC = (ROOT / ".github/workflows/sync-ca-to-r2.yml").read_text()
NEW_FILES = ("scripts/ca_publish.py", "scripts/ca_trail_filter.py", "scripts/ca-run-area.sh",
             ".github/workflows/trailforge-publish-ca.yml", ".github/workflows/sync-ca-to-r2.yml")
SHARED_TRAILFORGE = ("trailforge/assemble", "trailforge/serve", "trailforge/extract", "trailforge/tools")


def _code(text):
    """Text without comment-only lines, so header comments may explain what is NOT used."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_run_area_script_is_the_generic_path_with_no_denmark_machinery():
    code = _code(SCRIPT)
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/ca-run-area.sh")]).returncode == 0
    for needle in ("bash extract/aoi.sh", "check-boundary-unique", "assemble/assemble.py",
                   "--only-area \"$AREA_NAME\"", "--expected-area-relation-id \"$RELATION_ID\"",
                   "--require-exact-area", "--min-length-mi 0.1 --per-area-merge", "ca_trail_filter.py",
                   "serve/publish_areas.py", "--state Canada", "--no-boundary-fetch", "--no-elevation",
                   "index-for-publish", "finish-publish"):
        assert needle in code, needle
    # The ONLY engine opt-in is the registry-gated id-pinned boundary: one --region, one flag, in one branch.
    assert code.count("--region") == 1 and "--region ca --boundary-by-id" in code
    assert "if [ -n \"$BOUNDARY_BY_ID\" ]" in code and "boundary_by_id" in code
    for forbidden in ("--dk-", "denmark", "Denmark", "validate_publish_pilot", "_pbf_discovery",
                      "build_relation_slice", "export_relation_members", "relation-authority",
                      "prefilter-authority", "--exact-boundary", "--expected-area-way-id", "AOI_RECEIPT",
                      "AOI_RELATION_MEMBERS", "AOI_WAY_TOPOLOGY", "mols", "dk_", "dk-", "PREFILTER_DK",
                      "--only-area \"$AREA_ID\""):
        assert forbidden not in code, forbidden
    assert "--dry-run" not in code
    assert "-ca-(ab|bc|mb|nb|nl|ns|on|pe|qc|sk|yt)$" in code


def test_run_area_script_pins_exactly_one_boundary_id_and_way_backed_areas_skip_the_relation_pin():
    assert "SAFETY BLOCK: exactly one of RELATION_ID and WAY_ID must be set" in SCRIPT
    env = {**os.environ, "AREA_ID": "x-ca-ns", "AREA_NAME": "X", "BBOX": "-63,45,-62,46", "W": "/nonexistent",
           "RELATION_ID": "", "WAY_ID": ""}
    both = subprocess.run(["bash", str(ROOT / "scripts/ca-run-area.sh")], env={**env, "RELATION_ID": "1", "WAY_ID": "2"},
                          capture_output=True, text=True)
    neither = subprocess.run(["bash", str(ROOT / "scripts/ca-run-area.sh")], env=env, capture_output=True, text=True)
    assert both.returncode == 2 and neither.returncode == 2
    assert "BOUNDARY_ARGS=()" in SCRIPT                      # way-backed: no assemble pin (the unique check is its guard)
    bad_id = subprocess.run(["bash", str(ROOT / "scripts/ca-run-area.sh")], env={**env, "AREA_ID": "x-ca", "RELATION_ID": "1"},
                            capture_output=True, text=True)
    assert bad_id.returncode != 0 and "SAFETY" not in bad_id.stderr     # bare -ca (California) fails the id regex


def test_run_area_boundary_by_id_is_registry_gated_and_relation_only(tmp_path):
    reg = tmp_path / "trailforge/golden"
    reg.mkdir(parents=True)
    (reg / "ca-areas.json").write_text(json.dumps({"areas": [
        {"id": "x-ca-ns", "boundary_by_id": True}, {"id": "y-ca-ns", "boundary_by_id": False}]}))
    env = {**os.environ, "AREA_ID": "x-ca-ns", "AREA_NAME": "X", "BBOX": "-63,45,-62,46", "W": str(tmp_path / "w"),
           "RELATION_ID": "", "WAY_ID": "5"}
    run = lambda e: subprocess.run(["bash", str(ROOT / "scripts/ca-run-area.sh")], env=e, cwd=tmp_path,
                                   capture_output=True, text=True)
    way = run(env)                                                      # opt-in on a way-backed area
    assert way.returncode == 2 and "boundary_by_id must be true on a relation-backed area" in way.stderr
    false = run({**env, "AREA_ID": "y-ca-ns", "WAY_ID": "", "RELATION_ID": "7"})   # false is not an opt-in value
    assert false.returncode == 2 and "boundary_by_id must be true" in false.stderr
    plain = run({**env, "AREA_ID": "z-ca-ns", "WAY_ID": "", "RELATION_ID": "7"})    # no opt-in: proceeds past the gate
    assert "SAFETY BLOCK" not in plain.stderr and "boundary_by_id must be" not in plain.stderr
@pytest.mark.parametrize("name", ["trailforge-publish-ca.yml", "sync-ca-to-r2.yml"])
def test_workflows_parse_and_are_dispatch_only(name):
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    assert list((doc.get("on") or doc.get(True))) == ["workflow_dispatch"] and doc["jobs"]


def test_publish_ca_workflow_contract():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PUBLISH)
    inputs = (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"areas", "province", "mode", "chunk_size"}
    assert inputs["mode"]["options"] == ["qualify", "publish"] and inputs["mode"]["default"] == "qualify"
    assert inputs["province"]["options"] == ["ab", "bc", "mb", "nb", "nl", "ns", "on", "pe", "qc", "sk", "yt"]
    assert inputs["chunk_size"]["default"] == "25"
    assert doc["concurrency"] == {"group": "trailforge-publish", "cancel-in-progress": False}
    assert len(doc["jobs"]) == 1 and next(iter(doc["jobs"].values()))["timeout-minutes"] == 240
    code = _code(PUBLISH)
    for forbidden in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID", "aws s3", "s3api", "--elevation ",
                      "gh workflow run", "--force", "PREFILTER_DK", "seed-areas", "denmark", "Denmark", "dk_publish",
                      "dk-run-area", "--region", "mode=update", "== 'update'"):
        assert forbidden not in code, forbidden
    assert '[[ "$RAW_CHUNK" =~ ^([1-9]|[1-3][0-9]|40)$ ]]' in PUBLISH
    assert "-ca-(ab|bc|mb|nb|nl|ns|on|pe|qc|sk|yt)" in PUBLISH and "-dk" not in _code(PUBLISH)
    assert 'test "$GITHUB_REF_NAME" = "main"' in PUBLISH
    assert 'test "$(git rev-parse origin/main)" = "$(git rev-parse HEAD^)"' in PUBLISH
    assert "verify-staged" in PUBLISH and "verify-commit" in PUBLISH
    assert PUBLISH.count("curl -fL") == 1 and "bash extract/prefilter.sh" in PUBLISH
    assert "pbf-url" in PUBLISH and "trailforge: publish ${AREA_COUNT} Canadian area(s) to app" in PUBLISH
    for step, cond in (("Install blessed geoms", "inputs.mode == 'publish' }}"),
                       ("Commit published data", "inputs.mode == 'publish' }}")):
        block = PUBLISH.split(f"- name: {step}", 1)[1].split("\n      - name:", 1)[0]
        assert f"if: ${{{{ {cond}" in block, step
    assert "git push" not in PUBLISH.split("- name: Commit published data", 1)[0]


def test_sync_ca_workflow_contract():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(SYNC)
    assert doc["concurrency"] == {"group": "sync-geom-to-r2", "cancel-in-progress": False}   # shared with DK + Mols
    inputs = (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"mode", "rollback_run_id"}                  # the area list is never an input
    assert inputs["mode"]["options"] == ["plan", "apply", "rollback"]
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    assert sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", SYNC))) == [
        "R2_ACCESS_KEY_ID", "R2_ACCOUNT_ID", "R2_SECRET_ACCESS_KEY"]
    assert "environment:" not in SYNC and "HMAC" not in SYNC
    code = _code(SYNC)
    for forbidden in ("dk_publish", "dk-r2", "Danish", "Denmark", "-update", "-remove"):
        assert forbidden not in code, forbidden
    assert "scripts/ca_publish.py derive-areas" in SYNC and "scripts/ca_publish.py build-candidates" in SYNC
    assert "ca-r2-snapshot-${{ github.run_id }}" in SYNC and "-n \"ca-r2-snapshot-$ROLLBACK_RUN_ID\"" in SYNC
    assert 'put "cand/$file" "$key" "$cc"' in SYNC and "cand/parking" not in SYNC
    assert SYNC.index("Snapshot live R2") < SYNC.index("Save snapshot + candidates") < SYNC.index("- name: Apply")
    assert "PARTIAL APPLY: dispatch mode=rollback with rollback_run_id=$GITHUB_RUN_ID" in SYNC
    assert 'test "$(cur "$key")" = "$(base "$key")"' in SYNC and "parking_after" in SYNC
    assert SYNC.count('test "$(cur parking.json)" = "$(base parking.json)"') == 1


def test_every_workflow_holding_r2_write_credentials_shares_the_sync_lock():
    for name in ("sync-ca-to-r2.yml", "sync-dk-to-r2.yml", "sync-mols-to-r2.yml"):
        text = (ROOT / ".github/workflows" / name).read_text()
        assert "group: sync-geom-to-r2" in text and "cancel-in-progress: false" in text, name
    assert "R2_ACCESS_KEY_ID" not in PUBLISH


def test_new_files_import_no_denmark_module_and_hardcode_no_denmark_path():
    for rel in ("scripts/ca_publish.py", "scripts/ca_trail_filter.py"):
        code = _code((ROOT / rel).read_text())
        for forbidden in ("import dk_", "from dk_", "dk_publish", "dk_names", "dk_trail_name_fix",
                          "denmark.osm.pbf", "Denmark", "validate_publish_pilot", "_pbf_discovery",
                          "FileProcessor"):      # ubuntu apt pyosmium is 3.7: no 4.x-only API
            assert forbidden not in code.replace("# Denmark", ""), (rel, forbidden)
    assert "import ca_trail_filter" in (ROOT / "scripts/ca_publish.py").read_text()


def test_lane_adds_no_reference_to_shared_trailforge_dirs_beyond_the_generic_entry_points():
    code = _code(SCRIPT)
    used = set(re.findall(r"\b(?:extract|assemble|serve|tools)/[\w.\-]+", code))
    assert used == {"extract/aoi.sh", "assemble/assemble.py", "serve/publish_areas.py"}


def _run_areas_step(tmp_path, mode):
    """Run the real per-area loop script under `bash -e` (as GitHub does) with stub scripts; area b fails."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PUBLISH)
    step = next(s for s in next(iter(doc["jobs"].values()))["steps"] if s.get("id") == "areas")
    work, temp = tmp_path / "repo", tmp_path / "tmp"
    (work / "scripts").mkdir(parents=True)
    (temp / "ca").mkdir(parents=True)
    ids = ["a-ca-ns", "b-ca-ns", "c-ca-ns"]
    (temp / "ca/selected.txt").write_text("\n".join(ids) + "\n")
    for i in ids:
        (temp / "ca" / i).mkdir()
        (temp / "ca" / i / "area.json").write_text(json.dumps(
            {"status": "ok", "name": i, "relation_id": 1, "bbox_arg": "0,0,1,1"}))
    (work / "scripts/ca-run-area.sh").write_text(
        'echo "pipeline $AREA_ID" >> "$W/calls.log"\n[ "$AREA_ID" = b-ca-ns ] && exit 1\nexit 0\n')
    (work / "scripts/ca_publish.py").write_text(
        "import os, sys\nopen(os.environ['RUNNER_TEMP'] + '/ca/calls.log', 'a')"
        ".write(' '.join(sys.argv[1:3]) + ' ' + sys.argv[3] + '\\n')\n")
    out = tmp_path / "gh_output"
    env = {**os.environ, "RUNNER_TEMP": str(temp), "MODE": mode, "GITHUB_OUTPUT": str(out)}
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, env=env, capture_output=True, text=True)
    return proc, (temp / "ca/calls.log").read_text().splitlines(), out.read_text()


def test_areas_loop_continues_past_failing_area_in_qualify(tmp_path):
    proc, calls, output = _run_areas_step(tmp_path, "qualify")
    assert proc.returncode == 0, proc.stderr
    assert {"pipeline a-ca-ns", "pipeline b-ca-ns", "pipeline c-ca-ns"} <= set(calls)
    assert any(c.startswith("mark-failed --area-id b-ca-ns") for c in calls)
    assert "failed=1" in output


def test_areas_loop_publish_refuses_when_any_area_failed(tmp_path):
    proc, calls, output = _run_areas_step(tmp_path, "publish")
    assert proc.returncode == 1 and "REFUSED" in proc.stdout
    assert "pipeline c-ca-ns" in calls and "failed=1" in output
