"""Fail-closed source-integrity tests for the parking verdict sidecar."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
for path in (str(HERE), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import _parking_verdict_source as source  # noqa: E402
import dossier_output  # noqa: E402
import replay_trust  # noqa: E402
import test_parking_judge_tools as judge_tools  # noqa: E402


def _load_builder():
    path = HERE / "build-parking-verdicts.py"
    spec = importlib.util.spec_from_file_location(
        "parking_verdict_builder_integrity_tests", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_builder()
merge_drafts = judge_tools.merge_drafts
_UNSET = object()
_REAL_RESOLVER_DATA = merge_drafts.resolver.DATA


@pytest.fixture(autouse=True)
def _isolate_resolver_calibration_resource(tmp_path, monkeypatch):
    data = tmp_path / ".resolver-data"
    data.mkdir(mode=0o700, exist_ok=True)
    (data / "calibration.json").write_bytes(
        (_REAL_RESOLVER_DATA / "calibration.json").read_bytes()
    )
    monkeypatch.setattr(merge_drafts.resolver, "DATA", data)


def _configure(monkeypatch, *specs):
    """Configure (filename, key kind, default date[, dossier slug])."""
    stores = []
    key_kinds = {}
    for configured in specs:
        filename, key_kind, judged, *slug = configured
        dossier_slug = slug[0] if slug else None
        stores.append((filename, dossier_slug, judged))
        key_kinds[filename] = key_kind
    monkeypatch.setattr(builder, "STORES", stores)
    monkeypatch.setattr(builder, "STORE_KEY_KINDS", key_kinds)
    # Synthetic proofless stores must install a reviewed test baseline
    # explicitly; never infer trust from whatever happens to be in tmp_path.
    monkeypatch.setattr(builder, "LEGACY_ROW_BASELINE_PATH", None)
    monkeypatch.setattr(builder, "LEGACY_ROW_BASELINE_SHA256", None)
    monkeypatch.setattr(builder, "PUBLICATION_TRUST_ROOT_PATH", None)
    monkeypatch.setattr(builder, "PUBLICATION_TRUST_ROOT_SHA256", None)


def _row(aliases, verdict="KEEP", *, src=_UNSET, judged=_UNSET, fid=None):
    value = {
        "area": "test-area",
        "osm": list(aliases),
        "verdict": verdict,
        "prior": "surveyed",
        "confidence": "strong",
        "frames_used": ["z1", "z2", "z3"],
        "exists": {"call": "yes", "evidence": "Z2: striped parking"},
        "public": {"call": "yes", "evidence": "no restricted-access evidence"},
        "serves": {
            "call": "yes" if verdict != "DROP" else "no",
            "evidence": "trail entrance at the lot" if verdict != "DROP"
            else "mapped trail is over one mile away",
        },
        "tags_cited": {},
        "lat": 40.0,
        "lon": -105.5,
        "rings": [],
        "name": "Test Lot",
    }
    if src is not _UNSET:
        value["src"] = src
    if judged is not _UNSET:
        value["judged"] = judged
    if fid is not None:
        value["fid"] = fid
    return value


def _write_store(data_dir: Path, filename: str, values: dict) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / filename
    path.write_text(json.dumps(values))
    return path


def _install_test_baseline(
        data_dir: Path, monkeypatch, values_by_store: dict | None = None):
    """Install explicit baseline and missing empty floors for controlled fixtures."""
    specs = builder._store_specs()
    raw = {}
    for spec in specs:
        if values_by_store is not None and spec.filename in values_by_store:
            raw[spec.filename] = json.dumps(
                values_by_store[spec.filename], sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        else:
            raw[spec.filename] = (data_dir / spec.filename).read_bytes()
        floor_path = replay_trust._publication_floor_path(
            data_dir, spec.filename
        )
        if not floor_path.exists():
            floor_path.write_bytes(source.publication_floor_json_bytes(
                source.build_empty_publication_floor_document(spec.filename)
            ))
    dossiers = {
        path.name: path.read_bytes()
        for path in sorted(data_dir.glob("*_dossier.json"))
        if path.is_file() and not path.is_symlink()
    }
    document = source.build_legacy_baseline_document(specs, raw, dossiers)
    path = data_dir / (
        f"test-proofless-baseline-{document['baseline_sha256']}.json"
    )
    path.write_bytes(source.legacy_baseline_json_bytes(document))
    monkeypatch.setattr(builder, "LEGACY_ROW_BASELINE_PATH", str(path))
    monkeypatch.setattr(
        builder, "LEGACY_ROW_BASELINE_SHA256", document["baseline_sha256"]
    )
    store_image = {
        spec.filename: (data_dir / spec.filename).read_bytes()
        for spec in specs
    }
    proof_image = {}
    floor_image = {}
    for spec in specs:
        proof_path = replay_trust._publication_proof_path(
            data_dir, spec.filename
        )
        proof_image[spec.filename] = (
            proof_path.read_bytes() if proof_path.exists() else None
        )
        floor_image[spec.filename] = replay_trust._publication_floor_path(
            data_dir, spec.filename
        ).read_bytes()
    root_document = source.build_publication_trust_root_document(
        specs, store_image, proof_image, floor_image, path.name,
        path.read_bytes(), dossiers,
    )
    root_path = data_dir / "test-publication-trust-root-v1.json"
    root_bytes = source.publication_trust_root_json_bytes(root_document)
    root_path.write_bytes(root_bytes)
    monkeypatch.setattr(builder, "PUBLICATION_TRUST_ROOT_PATH", str(root_path))
    monkeypatch.setattr(
        builder, "PUBLICATION_TRUST_ROOT_SHA256",
        merge_drafts.resolver._sha(root_bytes),
    )
    return source.parse_legacy_baseline(
        path.read_bytes(), specs, document["baseline_sha256"]
    )


def _assert_cli_source_failure(data_dir: Path, label: str) -> None:
    sentinel = data_dir.parent / f"{label}-sentinel.json"
    absent = data_dir.parent / f"{label}-absent.json"
    sentinel_bytes = b"sentinel-sidecar-bytes\n"
    sentinel.write_bytes(sentinel_bytes)
    assert not absent.exists()

    assert builder.main([
        "--data-dir", str(data_dir), "--out", str(sentinel),
    ]) == 1
    assert sentinel.read_bytes() == sentinel_bytes
    assert builder.main([
        "--data-dir", str(data_dir), "--out", str(absent), "--check",
    ]) == 1
    assert not absent.exists()


def _assert_cli_success(data_dir: Path, label: str) -> Path:
    output = data_dir.parent / f"{label}-parking-verdicts.json"
    assert builder.main([
        "--data-dir", str(data_dir), "--out", str(output),
    ]) == 0
    assert builder.main([
        "--data-dir", str(data_dir), "--out", str(output), "--check",
    ]) == 0
    return output


@pytest.mark.parametrize(
    "crash_point",
    ["after-proof", "after-floor", "after-store", "before-archive", "archive-installed"],
)
def test_builder_blocks_every_live_publication_tail_until_exact_recovery(
        tmp_path, monkeypatch, crash_point):
    store, proof, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, f"sidecar-gate-{crash_point}"
    )
    _configure(
        monkeypatch,
        (store.name, source.OSM_KEY, None),
    )
    baseline_path = tmp_path / "test-proofless-source-baseline-v1.json"
    baseline_document = json.loads(baseline_path.read_bytes())
    trusted_root = tmp_path / "publication-trust-root-v1.json"
    trusted_root_before = trusted_root.read_bytes()
    monkeypatch.setattr(builder, "LEGACY_ROW_BASELINE_PATH", str(baseline_path))
    monkeypatch.setattr(
        builder, "LEGACY_ROW_BASELINE_SHA256",
        baseline_document["baseline_sha256"],
    )
    monkeypatch.setattr(builder, "PUBLICATION_TRUST_ROOT_PATH", str(trusted_root))
    monkeypatch.setattr(
        builder, "PUBLICATION_TRUST_ROOT_SHA256",
        merge_drafts.resolver._sha(trusted_root_before),
    )
    floor = merge_drafts.publication_floor_path(store)
    real_replace = merge_drafts.resolver._replace_verified_nofollow

    def crashing_replace(stage, target, expected_sha256, *,
                         retire_trusted_source=False):
        if retire_trusted_source:
            if crash_point == "before-archive":
                raise RuntimeError("simulated crash before publication archive")
            if crash_point == "archive-installed":
                real_replace(
                    stage, target, expected_sha256,
                    retire_trusted_source=False,
                )
                raise RuntimeError("simulated crash after publication archive")
        result = real_replace(
            stage, target, expected_sha256,
            retire_trusted_source=retire_trusted_source,
        )
        if crash_point == "after-proof" and Path(target) == proof:
            raise RuntimeError("simulated crash after publication proof")
        if crash_point == "after-floor" and Path(target) == floor:
            raise RuntimeError("simulated crash after publication floor")
        if crash_point == "after-store" and Path(target) == store:
            raise RuntimeError("simulated crash after publication store")
        return result

    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", crashing_replace
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main(arguments)
    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", real_replace
    )

    assert journal.is_file()
    _assert_cli_source_failure(tmp_path, f"live-{crash_point}")
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    archives = list((journal.parent / "Archive").glob(f"{store.name}.*.json"))
    assert len(archives) == 1
    assert trusted_root.read_bytes() == trusted_root_before

    # Completion intentionally advances live canonical inputs beyond the
    # reviewed root, so build remains blocked until a separate pin promotion.
    _assert_cli_source_failure(tmp_path, f"awaiting-root-review-{crash_point}")
    archived_journal = json.loads(archives[0].read_bytes())
    candidate = Path(archived_journal["paths"]["candidate"])
    candidate_bytes = candidate.read_bytes()
    trusted_root.write_bytes(candidate_bytes)
    monkeypatch.setattr(
        builder, "PUBLICATION_TRUST_ROOT_SHA256",
        merge_drafts.resolver._sha(candidate_bytes),
    )
    output = _assert_cli_success(tmp_path, f"recovered-{crash_point}")
    assert json.loads(output.read_text())["lots"]["way/1"]["verdict"] == "KEEP"


def test_builder_requires_complete_current_authority_proof_and_exact_closure(
        tmp_path, monkeypatch, capsys):
    store, proof_path, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "sidecar-proof-closure"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    _configure(
        monkeypatch,
        (store.name, source.OSM_KEY, "2026-09-29"),
    )
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    _assert_cli_success(tmp_path, "valid-proof")
    original_store = store.read_bytes()
    original_proof = proof_path.read_bytes()

    held_proof = tmp_path / "held-publication-proof.json"
    proof_path.replace(held_proof)
    _assert_cli_source_failure(tmp_path, "missing-proof")
    held_proof.replace(proof_path)

    proof_path.write_bytes(b"{malformed proof registry")
    _assert_cli_source_failure(tmp_path, "malformed-proof")
    proof_path.write_bytes(original_proof)

    registry = json.loads(original_proof)
    extra_body = {"version": 1, "kind": "unreferenced-test-proof"}
    extra_sha = merge_drafts.tr.sha256_json(extra_body)
    registry["proofs"][extra_sha] = {
        **extra_body, "proof_sha256": extra_sha,
    }
    proof_path.write_text(json.dumps(registry))
    _assert_cli_source_failure(tmp_path, "extra-proof")
    proof_path.write_bytes(original_proof)

    store_document = json.loads(original_store)
    source_key = next(iter(store_document))
    row = store_document[source_key]
    registry = json.loads(original_proof)
    old_proof_sha = row["publication_attestation"]["publication_proof_sha256"]
    forged_proof = copy.deepcopy(registry["proofs"][old_proof_sha])
    forged_proof["final_rows"]["1"]["serves"]["evidence"] = (
        "coherently rehashed but not sealed evidence"
    )
    proof_body = dict(forged_proof)
    proof_body.pop("proof_sha256")
    forged_proof_sha = merge_drafts.tr.sha256_json(proof_body)
    forged_proof["proof_sha256"] = forged_proof_sha
    attestation = row["publication_attestation"]
    attestation["publication_proof_sha256"] = forged_proof_sha
    attestation_body = dict(attestation)
    attestation_body.pop("attestation_sha256")
    attestation["attestation_sha256"] = merge_drafts.tr.sha256_json(
        attestation_body
    )
    store.write_text(json.dumps(store_document))
    proof_path.write_text(json.dumps({
        "version": 1,
        "proofs": {forged_proof_sha: forged_proof},
    }))
    # Advance the synthetic test pin so this case reaches the deeper proof
    # semantics rather than being stopped by the exact-byte root first.
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    _assert_cli_source_failure(tmp_path, "forged-proof")
    assert "publication proof final decision/authority row mismatch" in (
        capsys.readouterr().err
    )

    store.write_bytes(original_store)
    proof_path.write_bytes(original_proof)
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    _assert_cli_success(tmp_path, "restored-proof")


def test_proofless_legacy_row_requires_no_stray_attestation(
        tmp_path, monkeypatch):
    filename = "legacy-verdicts.json"
    _configure(
        monkeypatch,
        (filename, source.FID_KEY, "2026-08-01", "legacy-area"),
    )
    store = _write_store(
        tmp_path, filename, {"7": _row(["way/7"], fid=7)}
    )
    _install_test_baseline(tmp_path, monkeypatch)
    output = _assert_cli_success(tmp_path, "legacy-proofless")
    lot = next(iter(json.loads(output.read_text())["lots"].values()))
    assert lot["osm"] == ["way/7"]
    assert "7" not in lot["osm"]

    value = json.loads(store.read_text())
    value["7"]["publication_attestation"] = {"unexpected": True}
    store.write_text(json.dumps(value))
    _assert_cli_source_failure(tmp_path, "legacy-stray-attestation")


@pytest.mark.parametrize(
    "bad_key",
    ["way/0", "way/01", "WAY/1", "parking/1", "way/-1", " way/1", "1"],
)
def test_osm_store_rejects_noncanonical_source_keys(
        tmp_path, monkeypatch, bad_key):
    filename = "osm-verdicts.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {bad_key: _row(["way/9"])})
    _install_test_baseline(tmp_path, monkeypatch, {filename: {}})
    with pytest.raises(ValueError, match="noncanonical OSM source key"):
        builder.build(str(tmp_path))


def test_legacy_store_requires_canonical_key_fid_and_never_emits_fid_alias(
        tmp_path, monkeypatch):
    filename = "legacy-verdicts.json"
    _configure(
        monkeypatch,
        (filename, source.FID_KEY, "2026-08-01", "legacy-area"),
    )
    valid_values = {"7": _row(["node/70"], fid=7)}
    store = _write_store(tmp_path, filename, valid_values)
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert notes == [] and folded == []
    assert document["lots"]["node/70"]["osm"] == ["node/70"]

    for bad_key, bad_fid in (("07", 7), ("8", 7), ("7", True)):
        store.write_text(json.dumps({
            bad_key: _row(["node/70"], fid=bad_fid)
        }))
        _install_test_baseline(
            tmp_path, monkeypatch, {filename: valid_values}
        )
        with pytest.raises(ValueError, match="canonical decimal row fid"):
            builder.build(str(tmp_path))


def test_osm_source_key_must_be_attested_before_snapshot_or_terminal_validation(
        tmp_path, monkeypatch):
    filename = "osm-verdicts.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    valid = _row(["way/200", "way/300"])
    store = _write_store(tmp_path, filename, {"way/200": valid})
    baseline = _install_test_baseline(tmp_path, monkeypatch)

    copied = json.loads(store.read_text())
    copied["way/100"] = copy.deepcopy(valid)
    store.write_text(json.dumps(copied))
    _assert_cli_source_failure(tmp_path, "unattested-osm-key")

    spec = builder._store_specs()[0]
    with pytest.raises(ValueError, match="absent from the row-attested osm vector"):
        merge_drafts._validate_publication_after_images(
            store.read_bytes(),
            json.dumps({"version": 1, "proofs": {}}).encode(),
            source.publication_floor_json_bytes(
                source.build_empty_publication_floor_document(filename)
            ),
            filename,
            spec=spec,
            baseline=baseline,
        )


def test_existing_osm_alias_order_is_byte_stable_when_key_is_already_present(
        tmp_path, monkeypatch):
    filename = "osm-verdicts.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {
        "way/100": _row(["node/200", "way/100"]),
    })
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert notes == [] and folded == []
    assert document["lots"]["node/200"]["osm"] == ["node/200", "way/100"]


def test_fold_signature_uses_exact_effective_source_and_judged_defaults(
        tmp_path, monkeypatch):
    first_name = "first-osm.json"
    second_name = "second-osm.json"
    _configure(
        monkeypatch,
        (first_name, source.OSM_KEY, "2026-09-01"),
    )
    first_store = _write_store(tmp_path, first_name, {
        "way/100": _row(["node/900", "way/100"]),
        "way/200": _row(
            ["node/900", "way/200", "way/300"],
            src=first_name, judged="2026-09-01"
        ),
    })
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert notes == [] and len(folded) == 1
    assert document["lots"]["node/900"]["src"] == first_name
    assert document["lots"]["node/900"]["judged"] == "2026-09-01"

    first_store.write_text(json.dumps({
        "way/100": _row(["node/900", "way/100"]),
    }))
    _write_store(tmp_path, second_name, {
        "way/200": _row(["node/900", "way/200", "way/300"]),
    })
    _configure(
        monkeypatch,
        (first_name, source.OSM_KEY, "2026-09-01"),
        (second_name, source.OSM_KEY, "2026-09-02"),
    )
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert len(notes) == 1 and folded == []
    assert "different complete decision/authority provenance" in notes[0]

    second = json.loads((tmp_path / second_name).read_text())
    second["way/200"]["src"] = first_name
    second["way/200"]["judged"] = "2026-09-01"
    (tmp_path / second_name).write_text(json.dumps(second))
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert notes == [] and len(folded) == 1
    assert list(document["lots"]) == ["node/900"]


def test_replay_uses_identical_key_alias_and_provenance_normalization(
        tmp_path, monkeypatch):
    filename = "osm-verdicts.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {
        "way/100": _row(["node/900", "way/100"]),
        "way/200": _row(["node/900", "way/200", "way/300"]),
    })
    _install_test_baseline(tmp_path, monkeypatch)
    with replay_trust.validated_store_snapshot(
            tmp_path, builder._builder_configuration()) as snapshot:
        document, notes, folded = builder._build_with_snapshot(
            str(tmp_path), snapshot
        )
    assert notes == [] and len(folded) == 1
    sidecar = tmp_path / "parking-verdicts.json"
    sidecar.write_text(builder.serialize(document))
    monkeypatch.setattr(replay_trust, "_load_builder", lambda: builder)

    items, metadata, replayed = replay_trust.load_corpus(tmp_path, sidecar)
    assert metadata["source_rows"] == 2
    assert metadata["unique_clusters"] == 1
    assert metadata["folded_duplicates"] == 1
    assert replayed == document
    assert items[0]["published"]["osm"] == [
        "node/900", "way/100", "way/200", "way/300",
    ]


def test_snapshot_takes_all_canonical_shared_locks_in_global_sorted_order(
        tmp_path, monkeypatch):
    _configure(
        monkeypatch,
        ("z-store.json", source.OSM_KEY, "2026-09-29"),
        ("a-store.json", source.OSM_KEY, "2026-09-29"),
    )
    _write_store(tmp_path, "z-store.json", {})
    _write_store(tmp_path, "a-store.json", {})
    _install_test_baseline(tmp_path, monkeypatch)
    observed = []
    real_open = replay_trust.trusted_fs.open_lock_file

    def recording_open(path):
        observed.append(str(Path(path).resolve()))
        return real_open(path)

    monkeypatch.setattr(
        replay_trust.trusted_fs, "open_lock_file", recording_open
    )
    root_lock = replay_trust.tr.resource_lock_path(
        tmp_path, Path(builder.PUBLICATION_TRUST_ROOT_PATH)
    )
    with replay_trust.validated_store_snapshot(
            tmp_path, builder._builder_configuration()):
        assert judge_tools._other_process_can_take_shared_lock(root_lock)
        assert not judge_tools._other_process_can_take_exclusive_lock(root_lock)
    assert observed == sorted(observed)
    assert len(observed) == 11
    assert any("publication-trust-root-v1.json" in path for path in observed)
    assert any("a-store_publication_floor.json" in path for path in observed)
    assert any("z-store_publication_proofs.json" in path for path in observed)
    assert any("test-proofless-baseline" in path for path in observed)
    assert any("trekdex-dossier-inventory" in path for path in observed)


def test_snapshot_rejects_any_later_journal_before_reading_an_earlier_target(
        tmp_path, monkeypatch):
    _configure(
        monkeypatch,
        ("missing-first.json", source.OSM_KEY, "2026-09-29"),
        ("second.json", source.OSM_KEY, "2026-09-29"),
    )
    # The journal must win before either configured image is opened. These
    # existing files supply lockable configured paths only; their contents are
    # intentionally incompatible with the synthetic store set.
    monkeypatch.setattr(
        builder, "PUBLICATION_TRUST_ROOT_PATH",
        str(HERE / "parking-adjud" / "publication-trust-root-v1.json"),
    )
    monkeypatch.setattr(
        builder, "PUBLICATION_TRUST_ROOT_SHA256",
        "43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85",
    )
    monkeypatch.setattr(
        builder, "LEGACY_ROW_BASELINE_PATH",
        str(HERE / "parking-adjud" / "proofless-source-baseline-v1.json"),
    )
    monkeypatch.setattr(
        builder, "LEGACY_ROW_BASELINE_SHA256",
        "4a84784560ddde2a3867c9e0f1be5e303639a66dc28346b0c5faf56d1288c733",
    )
    second = _write_store(tmp_path, "second.json", {})
    proof = replay_trust._publication_proof_path(tmp_path, second.name)
    journal = merge_drafts._publication_transaction_paths(
        second, proof, "test"
    )["journal"]
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}")

    with pytest.raises(ValueError, match="live publication journal"):
        with replay_trust.validated_store_snapshot(
                tmp_path, builder._builder_configuration()):
            pass


@pytest.mark.parametrize("linked_target", ["store", "proof"])
def test_builder_nofollow_capture_rejects_linked_store_or_proof_without_output_change(
        tmp_path, monkeypatch, capsys, linked_target):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    filename = "osm-verdicts.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    values = {"way/1": _row(["way/1"])}
    outside_store = tmp_path / "outside-store.json"
    outside_store.write_text(json.dumps(values))
    store = data_dir / filename
    if linked_target == "store":
        store.symlink_to(outside_store)
        linked_path = store
    else:
        store.write_text(outside_store.read_text())
        outside_proof = tmp_path / "outside-proof.json"
        outside_proof.write_text(json.dumps({"version": 1, "proofs": {}}))
        linked_path = replay_trust._publication_proof_path(
            data_dir, filename
        )
        linked_path.symlink_to(outside_proof)
    _install_test_baseline(
        data_dir, monkeypatch, {filename: values}
    )
    _assert_cli_source_failure(data_dir, f"linked-{linked_target}")
    output = capsys.readouterr()
    assert "artifact path is a symlink (no-follow boundary)" in output.err
    assert linked_path.name in output.err


def test_public_builder_rejects_untyped_store_document_injection(
        tmp_path, monkeypatch):
    filename = "osm-verdicts.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    values = {"way/1": _row(["way/1"])}
    _write_store(tmp_path, filename, values)

    with pytest.raises(TypeError):
        builder.build(str(tmp_path), snapshot={filename: values})
    with pytest.raises(TypeError):
        source.ValidatedStoreSnapshot(
            tmp_path, (filename,), ((filename, b"{}"),),
            ((filename, None),), ((filename, b"{}"),),
        )


# ------------------------------------------------ publication-audit regressions


def _install_test_generation_for_dossier(
        data_dir: Path, slug: str) -> None:
    dossier = json.loads((data_dir / f"{slug}_dossier.json").read_bytes())
    fids = [facility["fid"] for facility in dossier["facilities"]]
    sidecars = {
        "serves2": {
            str(fid): {"served": False} for fid in fids
        },
        "context": {
            str(fid): {"category": "NEUTRAL"} for fid in fids
        },
        "walk": {
            str(fid): {"walk_m": None, "conn": "no route", "trail": None}
            for fid in fids
        },
    }
    for suffix, document in sidecars.items():
        (data_dir / f"{slug}_{suffix}.json").write_text(json.dumps(document))
    dossier_output.bootstrap_generation(data_dir, slug)


def _write_test_dossier(data_dir: Path, *, lat=40.0, slug="test-area") -> Path:
    path = data_dir / f"{slug}_dossier.json"
    path.write_text(json.dumps({
        "slug": slug,
        "facilities": [{
            "fid": 1,
            "lat": lat,
            "lon": -105.5,
            "rings": [],
            "osm": ["way/1"],
            "tags_union": {"name": "Captured Test Lot"},
        }],
    }))
    _install_test_generation_for_dossier(data_dir, slug)
    return path


def test_exact_legacy_baseline_rejects_row_new_key_missing_key_and_tamper(
        tmp_path, monkeypatch):
    filename = "legacy-osm.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-08-01"))
    original = {"way/1": _row(["way/1"])}
    store = _write_store(tmp_path, filename, original)
    _install_test_baseline(tmp_path, monkeypatch)
    _assert_cli_success(tmp_path, "exact-legacy-control")

    changed = copy.deepcopy(original)
    changed["way/1"]["verdict"] = "DROP"
    store.write_text(json.dumps(changed))
    _assert_cli_source_failure(tmp_path, "changed-legacy-row")

    added = copy.deepcopy(original)
    added["way/2"] = _row(["way/2"])
    store.write_text(json.dumps(added))
    _assert_cli_source_failure(tmp_path, "new-proofless-key")

    store.write_text("{}")
    _assert_cli_source_failure(tmp_path, "missing-legacy-key")

    store.write_text(json.dumps(original))
    baseline_path = Path(builder.LEGACY_ROW_BASELINE_PATH)
    tampered = json.loads(baseline_path.read_text())
    tampered["rows"][filename]["way/1"] = "f" * 64
    baseline_path.write_bytes(source.legacy_baseline_json_bytes(tampered))
    _assert_cli_source_failure(tmp_path, "tampered-baseline")


def test_current_row_stripping_and_proof_removal_fail_before_sidecar_output(
        tmp_path, monkeypatch):
    store, proof_path, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "current-strip-gate"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    _assert_cli_success(tmp_path, "current-strip-control")
    original_store = store.read_bytes()
    original_proof = proof_path.read_bytes()

    row_without_attestation = json.loads(original_store)
    next(iter(row_without_attestation.values())).pop("publication_attestation")
    store.write_text(json.dumps(row_without_attestation))
    _assert_cli_source_failure(tmp_path, "stripped-attestation")

    store.write_bytes(original_store)
    proof_path.write_text(json.dumps({"version": 1, "proofs": {}}))
    _assert_cli_source_failure(tmp_path, "stripped-proof")

    downgraded = json.loads(original_store)
    row = next(iter(downgraded.values()))
    row.pop("publication_attestation")
    row.pop("trust_resolution")
    row.pop("judge_provenance")
    row["verdict"] = "DROP"
    store.write_text(json.dumps(downgraded))
    proof_path.write_text(json.dumps({"version": 1, "proofs": {}}))
    _assert_cli_source_failure(tmp_path, "downgraded-verdict")

    store.write_bytes(original_store)
    proof_path.write_bytes(original_proof)
    _assert_cli_success(tmp_path, "restored-current-row")


def test_current_row_legitimately_overrides_a_baseline_key(
        tmp_path, monkeypatch):
    store, proof_path, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "current-over-baseline"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    legacy = {"way/1": _row(["way/1"], verdict="DROP", fid=1)}
    baseline = _install_test_baseline(
        tmp_path, monkeypatch, {store.name: legacy}
    )

    output = _assert_cli_success(tmp_path, "current-over-baseline")
    assert json.loads(output.read_text())["lots"]["way/1"]["verdict"] == "KEEP"
    merge_drafts._validate_publication_after_images(
        store.read_bytes(), proof_path.read_bytes(),
        merge_drafts.publication_floor_path(store).read_bytes(), store.name,
        spec=builder._store_specs()[0], baseline=baseline,
    )


def test_terminal_transition_rejects_exact_baseline_reversion_of_current_row(
        tmp_path, monkeypatch):
    store, proof_path, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "terminal-downgrade"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    current_bytes = store.read_bytes()
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    legacy = {"way/1": _row(["way/1"], verdict="DROP", fid=1)}
    baseline = _install_test_baseline(
        tmp_path, monkeypatch, {store.name: legacy}
    )
    legacy_bytes = json.dumps(legacy).encode()
    empty_proofs = json.dumps({"version": 1, "proofs": {}}).encode()

    # The immutable legacy artifact identifies this historical row exactly,
    # but a publication that already advanced the key may never revert to it.
    merge_drafts._validate_publication_after_images(
        legacy_bytes, empty_proofs,
        source.publication_floor_json_bytes(
            source.build_empty_publication_floor_document(store.name)
        ),
        store.name, spec=builder._store_specs()[0], baseline=baseline,
    )
    with pytest.raises(ValueError, match="may not be removed or downgraded"):
        replay_trust.validate_monotonic_publication_transition(
            store.name, current_bytes, legacy_bytes
        )


def test_builder_holds_shared_store_lock_through_write_check_and_failure_cleanup(
        tmp_path, monkeypatch):
    filename = "locked-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {"way/1": _row(["way/1"])})
    _install_test_baseline(tmp_path, monkeypatch)
    output = tmp_path / "locked-sidecar.json"
    assert builder.main([
        "--data-dir", str(tmp_path), "--out", str(output),
    ]) == 0
    lock_path = replay_trust.tr.resource_lock_path(
        tmp_path, tmp_path / filename
    )

    observed = []
    real_atomic = builder._atomic_write_text
    real_read = builder._read_text_nofollow

    def atomic_under_lock(*args, **kwargs):
        observed.append(("write", judge_tools._other_process_can_take_exclusive_lock(lock_path)))
        return real_atomic(*args, **kwargs)

    def check_under_lock(*args, **kwargs):
        observed.append(("check", judge_tools._other_process_can_take_exclusive_lock(lock_path)))
        return real_read(*args, **kwargs)

    monkeypatch.setattr(builder, "_atomic_write_text", atomic_under_lock)
    assert builder.main([
        "--data-dir", str(tmp_path), "--out", str(output),
    ]) == 0
    monkeypatch.setattr(builder, "_read_text_nofollow", check_under_lock)
    assert builder.main([
        "--data-dir", str(tmp_path), "--out", str(output), "--check",
    ]) == 0
    assert observed == [("write", False), ("check", False)]
    assert judge_tools._other_process_can_take_exclusive_lock(lock_path)

    sentinel = b"existing-target\n"
    output.write_bytes(sentinel)
    real_replace = builder.os.replace

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(builder.os, "replace", fail_replace)
    assert builder.main([
        "--data-dir", str(tmp_path), "--out", str(output),
    ]) == 1
    assert output.read_bytes() == sentinel
    assert not list(tmp_path.glob(f".{output.name}.tmp-*"))
    assert judge_tools._other_process_can_take_exclusive_lock(lock_path)
    monkeypatch.setattr(builder.os, "replace", real_replace)


def test_replay_holds_shared_store_lock_through_source_reconstruction(
        tmp_path, monkeypatch):
    filename = "replay-locked-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {"way/1": _row(["way/1"])})
    _install_test_baseline(tmp_path, monkeypatch)
    sidecar = _assert_cli_success(tmp_path, "replay-lock-control")
    lock_path = replay_trust.tr.resource_lock_path(
        tmp_path, tmp_path / filename
    )
    monkeypatch.setattr(replay_trust, "_load_builder", lambda: builder)
    real_normalize = builder._normalize_store_record
    observed = []

    def normalize_under_lock(*args, **kwargs):
        observed.append(
            judge_tools._other_process_can_take_exclusive_lock(lock_path)
        )
        return real_normalize(*args, **kwargs)

    monkeypatch.setattr(builder, "_normalize_store_record", normalize_under_lock)
    items, metadata, _document = replay_trust.load_corpus(tmp_path, sidecar)
    assert len(items) == 1 and metadata["source_rows"] == 1
    assert observed and observed == [False] * len(observed)
    assert judge_tools._other_process_can_take_exclusive_lock(lock_path)


def test_snapshot_rejects_linked_and_replaced_dossiers_without_output(
        tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    filename = "dossier-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(data_dir, filename, {"way/1": _row(["way/1"])})
    _install_test_baseline(data_dir, monkeypatch)
    outside = tmp_path / "outside-dossier.json"
    outside.write_text(json.dumps({"slug": "test-area", "facilities": []}))
    linked = data_dir / "test-area_dossier.json"
    linked.symlink_to(outside)
    _assert_cli_source_failure(data_dir, "linked-dossier")

    linked.rename(tmp_path / "linked-dossier-archive.json")
    dossier = _write_test_dossier(data_dir)
    real_read = merge_drafts.resolver._read_fd_bytes

    def read_then_mutate(fd):
        raw = real_read(fd)
        value = json.loads(dossier.read_text())
        value["facilities"][0]["lat"] += 0.01
        dossier.write_text(json.dumps(value))
        return raw

    monkeypatch.setattr(
        merge_drafts.resolver, "_read_fd_bytes", read_then_mutate
    )
    _assert_cli_source_failure(data_dir, "replaced-dossier")


def test_snapshot_rejects_malformed_dossier_geometry(
        tmp_path, monkeypatch):
    filename = "malformed-dossier-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {"way/1": _row(["way/1"])})
    _install_test_baseline(tmp_path, monkeypatch)
    dossier = _write_test_dossier(tmp_path)
    value = json.loads(dossier.read_text())
    value["facilities"][0]["rings"] = [[[40.0, -105.5, 3.0]]]
    dossier.write_text(json.dumps(value))
    _assert_cli_source_failure(tmp_path, "malformed-dossier")


def test_snapshot_capability_expires_at_context_exit_and_cannot_be_reused(
        tmp_path, monkeypatch):
    filename = "captured-dossier-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    row = _row(["way/1"])
    for field in ("lat", "lon", "rings", "name"):
        row.pop(field)
    _write_store(tmp_path, filename, {"way/1": row})
    dossier = _write_test_dossier(tmp_path, lat=40.0)
    _install_test_baseline(tmp_path, monkeypatch)
    with replay_trust.validated_store_snapshot(
            tmp_path, builder._builder_configuration()) as snapshot:
        before, notes, _folded = builder._build_with_snapshot(
            str(tmp_path), snapshot
        )
        assert notes == [] and before["lots"]["way/1"]["lat"] == 40.0

        # A same-UID actor that ignores the lock cannot alter captured bytes;
        # the capability still ceases to exist when this lease exits.
        live = json.loads(dossier.read_text())
        live["facilities"][0]["lat"] = 41.0
        dossier.write_text(json.dumps(live))
        returned = snapshot.dossier_bytes_by_name
        returned[dossier.name] = b"{}"
        documents = snapshot.store_bytes_by_name
        documents[filename] = b"{}"
        with pytest.raises(AttributeError, match="immutable"):
            snapshot._store_bytes = ()
        with pytest.raises(TypeError, match="may not be subclassed"):
            class ForgedSnapshot(source.ValidatedStoreSnapshot):
                pass
        after, notes, _folded = builder._build_with_snapshot(
            str(tmp_path), snapshot
        )
        assert notes == [] and after == before

    for operation in (
        lambda: snapshot.store_names,
        lambda: snapshot._store_bytes,
        lambda: snapshot.store_bytes_by_name,
        lambda: snapshot.proof_bytes_by_name,
        lambda: snapshot.floor_bytes_by_name,
        lambda: snapshot.dossier_bytes_by_name,
        lambda: snapshot.documents_for(tmp_path, (filename,)),
        lambda: snapshot.dossier_indexes_for(tmp_path, (filename,)),
        lambda: builder._build_with_snapshot(str(tmp_path), snapshot),
    ):
        with pytest.raises(ValueError, match="lease has expired"):
            operation()
    with pytest.raises(ValueError, match="dossier bytes"):
        builder.build(str(tmp_path))


def test_judge_provenance_only_difference_blocks_fold_and_replay(
        tmp_path, monkeypatch):
    filename = "provenance-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    provenance = {
        "model_id": "model-a",
        "model_family": "family-a",
        "decision_sha256": "a" * 64,
        "packet_sha256": "b" * 64,
        "prompt_sha256": "c" * 64,
        "evidence_sha256": "d" * 64,
    }
    first = _row(["node/900", "way/1"])
    second = _row(["node/900", "way/2"])
    first["judge_provenance"] = copy.deepcopy(provenance)
    second["judge_provenance"] = copy.deepcopy(provenance)
    store = _write_store(
        tmp_path, filename, {"way/1": first, "way/2": second}
    )
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert notes == [] and len(folded) == 1

    changed = json.loads(store.read_text())
    changed["way/2"]["judge_provenance"]["model_id"] = "model-b"
    store.write_text(json.dumps(changed))
    _install_test_baseline(tmp_path, monkeypatch)
    document, notes, folded = builder.build(str(tmp_path))
    assert len(notes) == 1 and folded == []
    assert "different complete decision/authority provenance" in notes[0]
    sidecar = tmp_path / "provenance-sidecar.json"
    sidecar.write_text(builder.serialize(document))
    monkeypatch.setattr(replay_trust, "_load_builder", lambda: builder)
    with pytest.raises(ValueError, match="source compiler rejected entries"):
        replay_trust.load_corpus(tmp_path, sidecar)


def test_plain_validation_callback_cannot_mutate_issued_decision():
    decision = _row(["way/1"])
    original = copy.deepcopy(decision)

    def mutating_validator(value):
        value["verdict"] = "DROP"
        value["osm"].append("way/2")
        return ["expected validation error"]

    errors = merge_drafts.tr._plain_errors(
        mutating_validator, decision, "test"
    )
    assert errors == ["test decision: expected validation error"]
    assert decision == original


def test_production_legacy_baseline_is_exact_and_deterministically_reproducible():
    specs = builder._store_specs()
    data_dir = Path(builder._DATA)
    raw = {
        spec.filename: (data_dir / spec.filename).read_bytes()
        for spec in specs
    }
    dossiers = {
        path.name: path.read_bytes()
        for path in sorted(data_dir.glob("*_dossier.json"))
    }
    regenerated = source.build_legacy_baseline_document(
        specs, raw, dossiers
    )
    baseline_path = Path(builder.LEGACY_ROW_BASELINE_PATH)
    baseline_bytes = baseline_path.read_bytes()
    assert baseline_bytes == source.legacy_baseline_json_bytes(regenerated)
    parsed = source.parse_legacy_baseline(
        baseline_bytes, specs, builder.LEGACY_ROW_BASELINE_SHA256
    )
    assert parsed.self_sha256 == (
        "4a84784560ddde2a3867c9e0f1be5e303639a66dc28346b0c5faf56d1288c733"
    )
    document = json.loads(baseline_bytes)
    assert document["configuration"]["source_row_count"] == 1273
    assert [entry["row_count"] for entry in document["configuration"]["stores"]] == [
        98, 84, 57, 57, 977,
    ]
    assert sum(len(rows) for rows in document["rows"].values()) == 1273


@pytest.mark.parametrize(
    "mutation",
    ["changed-alias", "changed-geometry", "additional", "removed"],
)
def test_legacy_baseline_binds_exact_dossier_inventory_and_bytes(
        tmp_path, monkeypatch, mutation):
    filename = "legacy-dossier-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-08-01"))
    row = _row(["way/1"])
    for field in ("lat", "lon", "rings", "name"):
        row.pop(field)
    _write_store(tmp_path, filename, {"way/1": row})
    dossier = tmp_path / "test-area_dossier.json"
    dossier.write_text(json.dumps({
        "slug": "test-area",
        "facilities": [{
            "fid": 1,
            "lat": 40.0,
            "lon": -105.5,
            "rings": [],
            "osm": ["way/1", "node/99"],
            "tags_union": {"name": "Baseline Lot"},
        }],
    }))
    _install_test_generation_for_dossier(tmp_path, "test-area")
    _install_test_baseline(tmp_path, monkeypatch)
    control = _assert_cli_success(tmp_path, f"dossier-{mutation}-control")
    assert json.loads(control.read_text())["lots"]["way/1"]["osm"] == [
        "way/1", "node/99",
    ]

    if mutation in {"changed-alias", "changed-geometry"}:
        changed = json.loads(dossier.read_text())
        if mutation == "changed-alias":
            changed["facilities"][0]["osm"].append("way/999")
        else:
            changed["facilities"][0]["lat"] = 40.25
        dossier.write_text(json.dumps(changed))
    elif mutation == "additional":
        (tmp_path / "extra-area_dossier.json").write_text(json.dumps({
            "slug": "extra-area", "facilities": [],
        }))
    else:
        dossier.rename(tmp_path / "held-test-area-dossier.json")

    _assert_cli_source_failure(tmp_path, f"dossier-{mutation}")


def test_current_row_ignores_unattested_dossier_alias_and_geometry(
        tmp_path, monkeypatch):
    store, _proof, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "current-dossier-injection"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    current = json.loads(store.read_text())
    row = current["way/1"]
    expected_lat = round(float(row["lat"]), 6)
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    (tmp_path / "injected-area_dossier.json").write_text(json.dumps({
        "slug": "injected-area",
        "facilities": [{
            "fid": 1,
            "lat": 45.0,
            "lon": -110.0,
            "rings": [[[45.0, -110.0], [45.1, -110.0], [45.0, -110.0]]],
            "osm": ["way/1", "node/999"],
            "tags_union": {"name": "Injected Lot"},
        }],
    }))
    _install_test_generation_for_dossier(tmp_path, "injected-area")
    # A reviewed root may intentionally add dossier bytes; even then current
    # attested rows remain self-contained and ignore those mutable semantics.
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})

    output = _assert_cli_success(tmp_path, "current-dossier-injection")
    published = json.loads(output.read_text())["lots"]["way/1"]
    assert published["osm"] == list(row["osm"])
    assert "node/999" not in published["osm"]
    assert published["lat"] == expected_lat
    assert published["name"] == row["name"]


@pytest.mark.parametrize(
    "mutation", ["missing", "malformed", "rolled-back", "tampered"],
)
def test_builder_rejects_missing_malformed_rolled_back_or_tampered_floor(
        tmp_path, monkeypatch, mutation):
    store, _proof, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, f"floor-{mutation}"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    _assert_cli_success(tmp_path, f"floor-{mutation}-control")
    floor_path = merge_drafts.publication_floor_path(store)

    if mutation == "missing":
        floor_path.rename(tmp_path / "held-publication-floor.json")
    elif mutation == "malformed":
        floor_path.write_bytes(b"{malformed floor")
    elif mutation == "rolled-back":
        floor_path.write_bytes(source.publication_floor_json_bytes(
            source.build_empty_publication_floor_document(store.name)
        ))
    else:
        tampered = json.loads(floor_path.read_text())
        tampered["entries"]["way/1"]["first_proof_sha256"] = "f" * 64
        floor_path.write_bytes(source.publication_floor_json_bytes(tampered))

    _assert_cli_source_failure(tmp_path, f"floor-{mutation}")


def test_floor_rejects_exact_legacy_restoration_after_proof_removal(
        tmp_path, monkeypatch):
    store, proof_path, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "floor-legacy-restoration"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    floor_path = merge_drafts.publication_floor_path(store)
    floor_before = floor_path.read_bytes()
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    legacy = {"way/1": _row(["way/1"], verdict="DROP", fid=1)}
    _install_test_baseline(tmp_path, monkeypatch, {store.name: legacy})

    store.write_text(json.dumps(legacy))
    proof_path.write_text(json.dumps({"version": 1, "proofs": {}}))
    assert floor_path.read_bytes() == floor_before
    _assert_cli_source_failure(tmp_path, "floor-exact-legacy-restoration")


@pytest.mark.parametrize("mutation", ["changed", "removed"])
def test_publication_floor_transition_rejects_append_only_conflict(mutation):
    spec = source.StoreSpec("store.json", source.OSM_KEY, None, None)
    entry = {
        "first_authority_kind": "machine_v2",
        "first_authority_sha256": "a" * 64,
        "first_attestation_sha256": "b" * 64,
        "first_proof_sha256": "c" * 64,
    }
    before = source.publication_floor_json_bytes(
        source.build_publication_floor_document("store.json", {"way/1": entry})
    )
    after_entries = {} if mutation == "removed" else {
        "way/1": {**entry, "first_proof_sha256": "d" * 64},
    }
    after = source.publication_floor_json_bytes(
        source.build_publication_floor_document("store.json", after_entries)
    )
    with pytest.raises(ValueError, match="not append-only"):
        replay_trust.validate_publication_floor_transition(spec, before, after)


def test_publication_floor_records_first_authority_and_proof_identity(
        tmp_path, monkeypatch):
    store, _proof, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "floor-first-identity"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    row = json.loads(store.read_text())["way/1"]
    spec = source.StoreSpec(store.name, source.OSM_KEY, None, None)
    floor = source.parse_publication_floor(
        merge_drafts.publication_floor_path(store).read_bytes(), spec
    )
    assert floor.entries_by_key == {
        "way/1": source.publication_floor_entry(
            row, replay_trust.current_authority(row)
        )
    }


def test_snapshot_holds_dossier_inventory_lock_after_capture(
        tmp_path, monkeypatch):
    filename = "dossier-lock-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {"way/1": _row(["way/1"])})
    _write_test_dossier(tmp_path)
    _install_test_baseline(tmp_path, monkeypatch)
    lock_path = replay_trust.tr.resource_lock_path(
        tmp_path, replay_trust.tr.dossier_resource_path(tmp_path)
    )
    with replay_trust.validated_store_snapshot(
            tmp_path, builder._builder_configuration()) as snapshot:
        assert snapshot.dossier_bytes_by_name
        assert not judge_tools._other_process_can_take_exclusive_lock(lock_path)
    assert judge_tools._other_process_can_take_exclusive_lock(lock_path)


def test_source_validation_rejects_incomplete_or_stale_generations(
        tmp_path, monkeypatch):
    filename = "generation-gate-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(tmp_path, filename, {"way/1": _row(["way/1"])})
    _write_test_dossier(tmp_path)
    _install_test_baseline(tmp_path, monkeypatch)
    _assert_cli_success(tmp_path, "generation-gate-control")

    manifest = dossier_output.generation_manifest_path(tmp_path, "test-area")
    held_manifest = tmp_path / "held-test-area-generation.json"
    manifest.rename(held_manifest)
    _assert_cli_source_failure(tmp_path, "generation-gate-missing-manifest")
    held_manifest.rename(manifest)

    context = tmp_path / "test-area_context.json"
    context_before = context.read_bytes()
    context.write_bytes(context_before + b" ")
    _assert_cli_source_failure(tmp_path, "generation-gate-stale-context")
    context.write_bytes(context_before)

    walk = tmp_path / "test-area_walk.json"
    held_walk = tmp_path / "held-test-area-walk.json"
    walk.rename(held_walk)
    _assert_cli_source_failure(tmp_path, "generation-gate-missing-member")
    held_walk.rename(walk)

    real_manifest = tmp_path / "real-test-area-generation.json"
    linked_manifest = tmp_path / "linked-test-area-generation.json"
    manifest.rename(real_manifest)
    manifest.symlink_to(real_manifest.name)
    _assert_cli_source_failure(tmp_path, "generation-gate-linked-manifest")
    manifest.rename(linked_manifest)
    real_manifest.rename(manifest)

    extra_manifest = tmp_path / "extra-area_generation.json"
    extra_manifest.write_bytes(manifest.read_bytes())
    _assert_cli_source_failure(tmp_path, "generation-gate-extra-manifest")
    extra_manifest.rename(tmp_path / "held-extra-area-generation.json")

    _assert_cli_success(tmp_path, "generation-gate-restored")


def _generation_quartet_documents(slug="test-area"):
    first_walk = {
        "walk_m": 12,
        "conn": "at/via trail or footway",
        "trail": "Test Trail",
        "snap_m": 3,
    }
    second_walk = {
        "walk_m": None,
        "conn": "no route",
        "trail": None,
        "snap_m": None,
    }
    return {
        "dossier": {
            "slug": slug,
            "facilities": [
                {"fid": 0, "name": "First", "walk": copy.deepcopy(first_walk)},
                {"fid": 2, "name": "Second", "walk": copy.deepcopy(second_walk)},
            ],
        },
        "serves": {
            "0": {"served": True},
            "2": {"served": False},
        },
        "context": {
            "0": {"category": "SUPPORT"},
            "2": {"category": "NEUTRAL"},
        },
        "walk": {
            "0": first_walk,
            "2": second_walk,
        },
    }


def _write_generation_quartet(root, slug="test-area", documents=None):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    documents = copy.deepcopy(
        documents if documents is not None
        else _generation_quartet_documents(slug)
    )
    basenames = {
        "dossier": f"{slug}_dossier.json",
        "serves": f"{slug}_serves2.json",
        "context": f"{slug}_context.json",
        "walk": f"{slug}_walk.json",
    }
    artifact_bytes = {}
    for key, basename in basenames.items():
        raw = json.dumps(
            documents[key], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        (root / basename).write_bytes(raw)
        artifact_bytes[key] = raw
    return documents, artifact_bytes


def _write_manifest_document(root, slug, document):
    path = dossier_output.generation_manifest_path(root, slug)
    path.write_bytes(dossier_output.tr.canonical_json(document) + b"\n")
    return path


def test_dossier_output_generation_manifest_canonical_strict_bytes(tmp_path):
    _documents, artifacts = _write_generation_quartet(tmp_path)
    document = dossier_output.build_generation_manifest(
        "test-area", "pipeline-v1", None, artifacts
    )
    raw = dossier_output.generation_manifest_json_bytes(document)

    assert raw == dossier_output.tr.canonical_json(document) + b"\n"
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert set(document) == {
        "version", "kind", "area", "origin",
        "parent_manifest_sha256", "artifacts",
    }
    assert set(document["artifacts"]) == {
        "dossier", "serves", "context", "walk",
    }
    assert "manifest_sha256" not in document
    assert dossier_output.parse_generation_manifest(
        raw, expected_area="test-area"
    ) == document

    malformed = (
        raw.replace(
            b'"area":"test-area"',
            b'"area":"test-area","area":"test-area"', 1,
        ),
        raw.replace(b'"version":1', b'"version":NaN', 1),
        b" " + raw,
        raw[:-1],
    )
    for candidate in malformed:
        with pytest.raises(ValueError, match="strict JSON|canonical"):
            dossier_output.parse_generation_manifest(
                candidate, expected_area="test-area"
            )


def test_dossier_output_generation_manifest_rejects_path_length_and_hash(
        tmp_path):
    _write_generation_quartet(tmp_path)
    dossier_output.bootstrap_generation(tmp_path, "test-area")
    manifest_path = dossier_output.generation_manifest_path(
        tmp_path, "test-area"
    )
    valid = json.loads(manifest_path.read_bytes())

    with pytest.raises(ValueError, match="canonical slug"):
        dossier_output.generation_manifest_path(tmp_path, "../test-area")

    cases = []
    extra_field = copy.deepcopy(valid)
    extra_field["unexpected"] = None
    cases.append((extra_field, "exact v1 schema"))
    wrong_kind = copy.deepcopy(valid)
    wrong_kind["kind"] = "other-kind"
    cases.append((wrong_kind, "kind is not supported"))
    wrong_origin = copy.deepcopy(valid)
    wrong_origin["origin"] = ["pipeline-v1"]
    cases.append((wrong_origin, "origin is not supported"))
    bootstrap_parent = copy.deepcopy(valid)
    bootstrap_parent["parent_manifest_sha256"] = "a" * 64
    cases.append((bootstrap_parent, "null parent"))
    bad_path = copy.deepcopy(valid)
    bad_path["artifacts"]["walk"]["basename"] = "../test-area_walk.json"
    cases.append((bad_path, "basename"))
    wrong_area = copy.deepcopy(valid)
    wrong_area["area"] = "other-area"
    cases.append((wrong_area, "area does not match"))
    negative_length = copy.deepcopy(valid)
    negative_length["artifacts"]["context"]["byte_length"] = -1
    cases.append((negative_length, "byte_length"))
    boolean_length = copy.deepcopy(valid)
    boolean_length["artifacts"]["context"]["byte_length"] = True
    cases.append((boolean_length, "byte_length"))
    wrong_length = copy.deepcopy(valid)
    wrong_length["artifacts"]["context"]["byte_length"] += 1
    cases.append((wrong_length, "byte length"))
    uppercase_hash = copy.deepcopy(valid)
    uppercase_hash["artifacts"]["walk"]["sha256"] = "A" * 64
    cases.append((uppercase_hash, "lowercase SHA-256"))
    wrong_hash = copy.deepcopy(valid)
    wrong_hash["artifacts"]["walk"]["sha256"] = "0" * 64
    cases.append((wrong_hash, "hash does not match"))

    for candidate, message in cases:
        _write_manifest_document(tmp_path, "test-area", candidate)
        with pytest.raises(ValueError, match=message):
            dossier_output.verify_generation(tmp_path, "test-area")


def test_dossier_output_generation_rejects_missing_and_old_manifest(tmp_path):
    _documents, artifacts = _write_generation_quartet(tmp_path)
    with pytest.raises(FileNotFoundError, match="manifest is missing"):
        dossier_output.verify_generation(tmp_path, "test-area")

    old = dossier_output.build_generation_manifest(
        "test-area", "pipeline-v1", None, artifacts
    )
    old["version"] = 0
    _write_manifest_document(tmp_path, "test-area", old)
    with pytest.raises(ValueError, match="version must be exactly 1"):
        dossier_output.verify_generation(tmp_path, "test-area")


def test_dossier_output_generation_rejects_duplicate_and_nonfinite_artifacts(
        tmp_path):
    _write_generation_quartet(tmp_path)
    context_path = tmp_path / "test-area_context.json"
    context_path.write_bytes(b'{"0":{},"0":{},"2":{}}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        dossier_output.bootstrap_generation(tmp_path, "test-area")
    assert not dossier_output.generation_manifest_path(
        tmp_path, "test-area"
    ).exists()

    for raw in (
        b'{"0":{"score":NaN},"2":{}}',
        b'{"0":{"score":1e9999},"2":{}}',
    ):
        _write_generation_quartet(tmp_path)
        context_path.write_bytes(raw)
        with pytest.raises(ValueError, match="nonfinite JSON number"):
            dossier_output.bootstrap_generation(tmp_path, "test-area")


def test_dossier_output_generation_capture_rejects_symlink_artifact(tmp_path):
    root = tmp_path / "symlink-artifact"
    _write_generation_quartet(root)
    context_path = root / "test-area_context.json"
    target = root / "captured-context.json"
    context_path.rename(target)
    context_path.symlink_to(target.name)

    with pytest.raises(OSError):
        dossier_output.bootstrap_generation(root, "test-area")
    assert not dossier_output.generation_manifest_path(
        root, "test-area"
    ).exists()


def test_dossier_output_generation_rejects_fid_sets_and_walk_mismatch(tmp_path):
    cases = []
    boolean_fid = _generation_quartet_documents()
    boolean_fid["dossier"]["facilities"][0]["fid"] = True
    cases.append(("boolean-fid", boolean_fid, "nonnegative integer"))
    negative_fid = _generation_quartet_documents()
    negative_fid["dossier"]["facilities"][0]["fid"] = -1
    cases.append(("negative-fid", negative_fid, "nonnegative integer"))
    duplicate_fid = _generation_quartet_documents()
    duplicate_fid["dossier"]["facilities"][1]["fid"] = 0
    cases.append(("duplicate-fid", duplicate_fid, "duplicated"))
    missing_key = _generation_quartet_documents()
    missing_key["context"].pop("2")
    cases.append(("missing-key", missing_key, "fid set"))
    noncanonical_key = _generation_quartet_documents()
    noncanonical_key["serves"]["02"] = noncanonical_key["serves"].pop("2")
    cases.append(("noncanonical-key", noncanonical_key, "decimal fid"))
    walk_mismatch = _generation_quartet_documents()
    walk_mismatch["dossier"]["facilities"][0]["walk"]["walk_m"] = 13
    cases.append(("walk-mismatch", walk_mismatch, "embedded walk"))

    for dirname, documents, message in cases:
        root = tmp_path / dirname
        _write_generation_quartet(root, documents=documents)
        with pytest.raises(ValueError, match=message):
            dossier_output.bootstrap_generation(root, "test-area")
        assert not dossier_output.generation_manifest_path(
            root, "test-area"
        ).exists()


def test_dossier_output_generation_bootstrap_is_idempotent_without_rewrites(
        tmp_path, monkeypatch):
    _documents, artifact_bytes = _write_generation_quartet(tmp_path)
    artifact_paths = {
        key: tmp_path / basename
        for key, basename in {
            "dossier": "test-area_dossier.json",
            "serves": "test-area_serves2.json",
            "context": "test-area_context.json",
            "walk": "test-area_walk.json",
        }.items()
    }
    writes = []
    real_atomic = dossier_output.trusted_fs.atomic_write_bytes

    def observe_atomic(path, data):
        writes.append(Path(path).name)
        return real_atomic(path, data)

    monkeypatch.setattr(
        dossier_output.trusted_fs, "atomic_write_bytes", observe_atomic
    )
    first = dossier_output.bootstrap_generation(tmp_path, "test-area")
    manifest_path = dossier_output.generation_manifest_path(
        tmp_path, "test-area"
    )
    manifest_before = manifest_path.read_bytes()
    second = dossier_output.bootstrap_generation(tmp_path, "test-area")

    assert writes == ["test-area_generation.json"]
    assert manifest_path.read_bytes() == manifest_before
    assert first["source_generation"] == second["source_generation"]
    assert {
        key: path.read_bytes() for key, path in artifact_paths.items()
    } == artifact_bytes
    assert first["source_generation"].keys() == {
        "manifest_path", "manifest_sha256", "artifact_sha256",
    }
    with pytest.raises(TypeError):
        first["dossier"]["slug"] = "changed"
    with pytest.raises(AttributeError):
        first["dossier"]["facilities"].append({})

    changed_context = copy.deepcopy(_generation_quartet_documents()["context"])
    changed_context["0"]["note"] = "not the bootstrapped image"
    (tmp_path / "test-area_context.json").write_bytes(json.dumps(
        changed_context, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    with pytest.raises(ValueError, match="differs from bootstrap image"):
        dossier_output.bootstrap_generation(tmp_path, "test-area")
    assert manifest_path.read_bytes() == manifest_before
    assert writes == ["test-area_generation.json"]


def test_dossier_output_generation_commit_is_manifest_last_and_parent_cas(
        tmp_path, monkeypatch):
    _documents, artifact_bytes = _write_generation_quartet(tmp_path)
    writes = []
    real_atomic = dossier_output.trusted_fs.atomic_write_bytes

    def observe_atomic(path, data):
        assert Path(path).name == "test-area_generation.json"
        for key, basename in {
                "dossier": "test-area_dossier.json",
                "serves": "test-area_serves2.json",
                "context": "test-area_context.json",
                "walk": "test-area_walk.json"}.items():
            assert (tmp_path / basename).read_bytes() == artifact_bytes[key]
        writes.append(Path(path).name)
        return real_atomic(path, data)

    monkeypatch.setattr(
        dossier_output.trusted_fs, "atomic_write_bytes", observe_atomic
    )
    first = dossier_output.commit_generation(tmp_path, "test-area", None)
    manifest_path = dossier_output.generation_manifest_path(
        tmp_path, "test-area"
    )
    manifest_before = manifest_path.read_bytes()
    second = dossier_output.commit_generation(tmp_path, "test-area", None)
    assert writes == ["test-area_generation.json"]
    assert first["source_generation"] == second["source_generation"]

    changed_context = copy.deepcopy(_generation_quartet_documents()["context"])
    changed_context["0"]["note"] = "new generation"
    (tmp_path / "test-area_context.json").write_bytes(json.dumps(
        changed_context, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    with pytest.raises(ValueError, match="stale parent_manifest_sha256"):
        dossier_output.commit_generation(tmp_path, "test-area", None)
    assert manifest_path.read_bytes() == manifest_before


def test_dossier_output_generation_commit_rejects_revalidation_mismatch(
        tmp_path, monkeypatch):
    documents, _artifact_bytes = _write_generation_quartet(tmp_path)
    changed = copy.deepcopy(documents["context"])
    changed["0"]["note"] = "changed between captures"
    changed_raw = json.dumps(
        changed, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    context_reads = 0
    real_read = dossier_output.trusted_fs.read_regular_bytes

    with pytest.raises(ValueError, match="do not match expected hashes"):
        dossier_output.commit_generation(
            tmp_path, "test-area", None,
            expected_artifact_sha256={
                key: "0" * 64 for key in dossier_output.GENERATION_ARTIFACT_KEYS
            },
        )
    assert not dossier_output.generation_manifest_path(
        tmp_path, "test-area"
    ).exists()

    def changing_read(path, **kwargs):
        nonlocal context_reads
        if Path(path).name == "test-area_context.json":
            context_reads += 1
            if context_reads == 2:
                return changed_raw
        return real_read(path, **kwargs)

    monkeypatch.setattr(
        dossier_output.trusted_fs, "read_regular_bytes", changing_read
    )
    with pytest.raises(ValueError, match="quartet changed"):
        dossier_output.commit_generation(tmp_path, "test-area", None)
    assert not dossier_output.generation_manifest_path(
        tmp_path, "test-area"
    ).exists()


def test_dossier_output_generation_capture_reads_manifest_around_artifacts(
        tmp_path, monkeypatch):
    _write_generation_quartet(tmp_path)
    dossier_output.bootstrap_generation(tmp_path, "test-area")
    reads = []
    real_read = dossier_output.trusted_fs.read_regular_bytes

    def observe_read(path, **kwargs):
        reads.append(Path(path).name)
        return real_read(path, **kwargs)

    monkeypatch.setattr(
        dossier_output.trusted_fs, "read_regular_bytes", observe_read
    )
    captured = dossier_output.verify_generation(tmp_path, "test-area")
    assert reads == [
        "test-area_generation.json",
        "test-area_dossier.json",
        "test-area_serves2.json",
        "test-area_context.json",
        "test-area_walk.json",
        "test-area_generation.json",
    ]
    source_generation = captured["source_generation"]
    assert source_generation["manifest_path"] == str(
        dossier_output.generation_manifest_path(tmp_path, "test-area")
    )
    assert source_generation["manifest_sha256"] == dossier_output.hashlib.sha256(
        dossier_output.generation_manifest_path(
            tmp_path, "test-area"
        ).read_bytes()
    ).hexdigest()


def test_dossier_output_generation_uses_exclusive_and_shared_lock_modes(
        tmp_path, monkeypatch):
    _write_generation_quartet(tmp_path)
    modes = []
    real_flock = dossier_output.trusted_fs.fcntl.flock

    def observe_flock(fd, mode):
        if mode in (dossier_output.fcntl.LOCK_EX, dossier_output.fcntl.LOCK_SH):
            modes.append(mode)
        return real_flock(fd, mode)

    monkeypatch.setattr(dossier_output.trusted_fs.fcntl, "flock", observe_flock)
    bootstrapped = dossier_output.bootstrap_generation(tmp_path, "test-area")
    dossier_output.verify_generation(tmp_path, "test-area")
    dossier_output.commit_generation(
        tmp_path, "test-area",
        bootstrapped["source_generation"]["manifest_sha256"],
    )
    assert modes == [
        dossier_output.fcntl.LOCK_EX,
        dossier_output.fcntl.LOCK_SH,
        dossier_output.fcntl.LOCK_EX,
    ]


def test_dossier_output_generation_cli_uses_padj_tmp(
        tmp_path, monkeypatch, capsys):
    _write_generation_quartet(tmp_path)
    monkeypatch.setenv("PADJ_TMP", str(tmp_path))
    assert dossier_output.main(["bootstrap", "test-area"]) == 0
    parent = dossier_output.verify_generation(
        tmp_path, "test-area"
    )["source_generation"]["manifest_sha256"]
    assert dossier_output.main(["verify", "test-area"]) == 0
    assert dossier_output.main([
        "commit", "test-area", "--parent-manifest-sha256", parent,
    ]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all(
        set(json.loads(line)) == {
            "manifest_path", "manifest_sha256", "artifact_sha256",
        }
        for line in lines
    )


def test_dossier_output_all_production_generation_manifests_verify():
    data = HERE / "parking-adjud" / "data"
    areas = (
        "camels-hump-state-park-vt",
        "crawford-notch-state-park-nh",
        "echo-canyon-recreation-area-az",
        "grafton-notch-state-park-me",
        "griffith-park-ca",
        "monadnock-reservation-nh",
        "mount-major-state-forest-nh",
        "phoenix-mountains-preserve-az",
        "pinnacle-peak-park-az",
        "usery-mountain-regional-park-az",
        "zion-wilderness-ut",
    )
    assert tuple(
        path.name for path in sorted(data.glob("*_generation.json"))
    ) == tuple(f"{area}_generation.json" for area in areas)

    facility_count = 0
    for area in areas:
        captured = dossier_output.verify_generation(data, area)
        manifest_path = dossier_output.generation_manifest_path(data, area)
        manifest_raw = manifest_path.read_bytes()
        manifest = dossier_output.parse_generation_manifest(
            manifest_raw, expected_area=area
        )
        assert manifest["origin"] == "legacy-bootstrap-v1"
        assert manifest["parent_manifest_sha256"] is None
        assert captured["source_generation"]["manifest_sha256"] == (
            dossier_output.hashlib.sha256(manifest_raw).hexdigest()
        )
        facility_count += len(captured["dossier"]["facilities"])
    assert facility_count == 6197


def test_dossier_output_writer_locks_and_atomically_replaces(tmp_path, monkeypatch):
    dossier_path = tmp_path / "test-area_dossier.json"
    serves_path = tmp_path / "test-area_serves2.json"
    dossier_path.write_bytes(b"old dossier")
    serves_path.write_bytes(b"old serves")
    lock_path = dossier_output.tr.resource_lock_path(
        tmp_path, dossier_output.tr.dossier_resource_path(tmp_path)
    )
    observed = []
    real_atomic = dossier_output.trusted_fs.atomic_write_bytes

    def atomic_under_lock(path, data):
        observed.append(
            judge_tools._other_process_can_take_exclusive_lock(lock_path)
        )
        return real_atomic(path, data)

    monkeypatch.setattr(
        dossier_output.trusted_fs, "atomic_write_bytes", atomic_under_lock
    )
    dossier_output.write_generated_outputs(
        tmp_path, "test-area",
        {"slug": "test-area", "facilities": []},
        {"1": {"served": False}},
    )
    assert observed == [False, False]
    assert json.loads(dossier_path.read_text())["slug"] == "test-area"
    assert json.loads(serves_path.read_text())["1"]["served"] is False
    assert not list(tmp_path.glob(".*.tmp-*"))
    assert judge_tools._other_process_can_take_exclusive_lock(lock_path)


def test_dossier_output_atomic_failure_preserves_existing_bytes(
        tmp_path, monkeypatch):
    dossier_path = tmp_path / "test-area_dossier.json"
    serves_path = tmp_path / "test-area_serves2.json"
    dossier_path.write_bytes(b"old dossier")
    serves_path.write_bytes(b"old serves")
    real_replace = dossier_output.trusted_fs.os.replace

    def fail_dossier_replace(source_name, target_name, **kwargs):
        if target_name == dossier_path.name:
            raise OSError("simulated dossier replacement failure")
        return real_replace(source_name, target_name, **kwargs)

    monkeypatch.setattr(
        dossier_output.trusted_fs.os, "replace", fail_dossier_replace
    )
    with pytest.raises(OSError, match="simulated dossier replacement failure"):
        dossier_output.write_generated_outputs(
            tmp_path, "test-area",
            {"slug": "test-area", "facilities": []}, {},
        )
    assert dossier_path.read_bytes() == b"old dossier"
    assert serves_path.read_bytes() == b"old serves"
    assert not list(tmp_path.glob(".*.tmp-*"))


def test_two_data_dirs_targeting_one_output_share_exclusive_stale_writer_lock(
        tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    filename = "shared-output-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    _write_store(first, filename, {"way/1": _row(["way/1"])})
    _install_test_baseline(first, monkeypatch)
    output = tmp_path / "shared-output.json"
    first_lock = replay_trust.tr.resource_lock_path(first, output)
    second_lock = replay_trust.tr.resource_lock_path(second, output)
    assert first_lock == second_lock
    observed = []
    real_atomic = builder._atomic_write_text

    def atomic_under_output_lock(path, text):
        observed.append(
            judge_tools._other_process_can_take_exclusive_lock(first_lock)
        )
        return real_atomic(path, text)

    monkeypatch.setattr(builder, "_atomic_write_text", atomic_under_output_lock)
    assert builder.main([
        "--data-dir", str(first), "--out", str(output),
    ]) == 0
    assert observed == [False]
    assert judge_tools._other_process_can_take_exclusive_lock(first_lock)


def test_builder_rejects_output_collision_with_sources_and_lock_paths(
        tmp_path, monkeypatch):
    filename = "collision-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, "2026-09-29"))
    store = _write_store(tmp_path, filename, {"way/1": _row(["way/1"])})
    dossier = _write_test_dossier(tmp_path)
    _install_test_baseline(tmp_path, monkeypatch)
    floor = replay_trust._publication_floor_path(tmp_path, filename)
    proof = replay_trust._publication_proof_path(tmp_path, filename)
    baseline = Path(builder.LEGACY_ROW_BASELINE_PATH)
    store_lock = replay_trust.tr.resource_lock_path(tmp_path, store)
    generation_sources = [
        dossier_output.generation_manifest_path(tmp_path, "test-area"),
        tmp_path / "test-area_serves2.json",
        tmp_path / "test-area_context.json",
        tmp_path / "test-area_walk.json",
    ]
    targets = [
        store, dossier, floor, proof, baseline, store_lock,
        *generation_sources,
    ]
    before = {
        target: target.read_bytes() if target.exists() else None
        for target in targets
    }
    for target in targets:
        assert builder.main([
            "--data-dir", str(tmp_path), "--out", str(target), "--check",
        ]) == 1
        expected = before[target]
        if expected is None:
            if target == store_lock:
                assert target.read_bytes() == b""
            else:
                assert not target.exists()
        else:
            assert target.read_bytes() == expected


def test_production_floor_artifacts_are_exact_empty_bootstrap_documents():
    data_dir = Path(builder._DATA)
    observed = {}
    for spec in builder._store_specs():
        path = replay_trust._publication_floor_path(data_dir, spec.filename)
        raw = path.read_bytes()
        expected = source.publication_floor_json_bytes(
            source.build_empty_publication_floor_document(spec.filename)
        )
        assert raw == expected
        parsed = source.parse_publication_floor(raw, spec)
        assert parsed.keys == frozenset()
        observed[spec.filename] = parsed.self_sha256
    assert observed == {
        "phx_verdicts_osm.json": "0ee6bd90c9a797f606283183a20da128db79ee210a68bef7f5b7491e71fed211",
        "ne_verdicts_osm.json": "e3dfecbcf8513e26b950201286aa494cfa4f3a19a92e81f5a5e2d81b1a411254",
        "zion-wilderness-ut_verdicts2.json": "6165ef438e82c791972ace115e106de07c1811a9ee7f62ade05e1d97fc76e405",
        "griffith-park-ca_verdicts2.json": "46267586adf833239021602a20356df1a3e1720909229fdd0ff14980fbd16f2f",
        "co_verdicts_osm.json": "c5377b4afd23967bd8d44085abff0906057c1957bde3232619e04057e973b748",
    }


# ------------------------------------------------ publication trust root


def test_production_publication_trust_root_is_exact_and_code_pinned():
    specs = builder._store_specs()
    data_dir = Path(builder._DATA)
    stores = {
        spec.filename: (data_dir / spec.filename).read_bytes()
        for spec in specs
    }
    proofs = {}
    floors = {}
    for spec in specs:
        proof = replay_trust._publication_proof_path(data_dir, spec.filename)
        proofs[spec.filename] = proof.read_bytes() if proof.exists() else None
        floors[spec.filename] = replay_trust._publication_floor_path(
            data_dir, spec.filename
        ).read_bytes()
    dossiers = {
        path.name: path.read_bytes()
        for path in sorted(data_dir.glob("*_dossier.json"))
    }
    baseline_path = Path(builder.LEGACY_ROW_BASELINE_PATH)
    document = source.build_publication_trust_root_document(
        specs, stores, proofs, floors, baseline_path.name,
        baseline_path.read_bytes(), dossiers,
    )
    expected = source.publication_trust_root_json_bytes(document)
    root_path = Path(builder.PUBLICATION_TRUST_ROOT_PATH)
    assert root_path.name == "publication-trust-root-v1.json"
    assert root_path.read_bytes() == expected
    assert builder.PUBLICATION_TRUST_ROOT_SHA256 == (
        "43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85"
    )
    assert merge_drafts.resolver._sha(expected) == (
        builder.PUBLICATION_TRUST_ROOT_SHA256
    )
    parsed = source.parse_publication_trust_root(
        expected, specs, builder.PUBLICATION_TRUST_ROOT_SHA256,
        baseline_path.name,
    )
    assert parsed.generation == 1
    assert parsed.parent_root_sha256 is None
    assert all(value is None for value in (
        entry["proof_registry_sha256"]
        for entry in parsed.stores_by_name.values()
    ))
    source.validate_publication_trust_root_image(
        parsed, specs, stores, proofs, floors, baseline_path.name,
        baseline_path.read_bytes(), dossiers,
    )
    successor = source.build_publication_trust_root_successor(
        parsed, specs[-1].filename, b"next-store", b"next-proof", b"next-floor"
    )
    original = json.loads(expected)
    assert successor["generation"] == 2
    assert successor["parent_root_sha256"] == parsed.exact_sha256
    assert successor["legacy_baseline"] == original["legacy_baseline"]
    assert successor["dossiers"] == original["dossiers"]
    assert {
        name: entry for name, entry in successor["stores"].items()
        if name != specs[-1].filename
    } == {
        name: entry for name, entry in original["stores"].items()
        if name != specs[-1].filename
    }
    assert "root_sha256" not in original
    assert b"public/areas/parking-verdicts.json" not in expected


def test_publication_trust_root_rejects_duplicate_keys_and_noncanonical_bytes():
    spec = source.StoreSpec("store.json", source.OSM_KEY, None, None)
    store = b"{}"
    floor = source.publication_floor_json_bytes(
        source.build_empty_publication_floor_document(spec.filename)
    )
    baseline_document = source.build_legacy_baseline_document(
        (spec,), {spec.filename: store}, {}
    )
    baseline = source.legacy_baseline_json_bytes(baseline_document)
    document = source.build_publication_trust_root_document(
        (spec,), {spec.filename: store}, {spec.filename: None},
        {spec.filename: floor}, "baseline.json", baseline, {},
    )
    canonical = source.publication_trust_root_json_bytes(document)
    duplicate = canonical.replace(
        b'{\n  "dossiers"', b'{\n  "version": 1,\n  "dossiers"', 1
    )
    for raw, message in (
        (duplicate, "duplicate JSON key"),
        (b" " + canonical, "noncanonical"),
    ):
        with pytest.raises(ValueError, match=message):
            source.parse_publication_trust_root(
                raw, (spec,), merge_drafts.resolver._sha(raw),
                "baseline.json",
            )


def test_root_tamper_fails_before_sidecar_output_changes(
        tmp_path, monkeypatch):
    filename = "root-tamper-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, None))
    _write_store(tmp_path, filename, {})
    _install_test_baseline(tmp_path, monkeypatch)
    root_path = Path(builder.PUBLICATION_TRUST_ROOT_PATH)
    root_path.write_bytes(b" " + root_path.read_bytes())
    _assert_cli_source_failure(tmp_path, "root-tamper")


def test_coherently_rewritten_root_is_rejected_by_unchanged_code_pin(
        tmp_path, monkeypatch):
    filename = "coherent-rewrite-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, None))
    store = _write_store(tmp_path, filename, {})
    _install_test_baseline(tmp_path, monkeypatch)
    reviewed_pin = builder.PUBLICATION_TRUST_ROOT_SHA256

    rewritten = {"way/1": _row(["way/1"])}
    store.write_text(json.dumps(rewritten))
    _install_test_baseline(tmp_path, monkeypatch)
    assert builder.PUBLICATION_TRUST_ROOT_SHA256 != reviewed_pin
    monkeypatch.setattr(
        builder, "PUBLICATION_TRUST_ROOT_SHA256", reviewed_pin
    )
    _assert_cli_source_failure(tmp_path, "coherent-root-rewrite")


def test_complete_store_proof_floor_rollback_is_rejected_by_root(
        tmp_path, monkeypatch):
    store, proof, journal, arguments = judge_tools._real_publication_case(
        tmp_path, monkeypatch, "complete-rooted-rollback"
    )
    assert merge_drafts.main(arguments) == 0
    assert not journal.exists()
    _configure(monkeypatch, (store.name, source.OSM_KEY, "2026-09-29"))
    _install_test_baseline(tmp_path, monkeypatch, {store.name: {}})
    _assert_cli_success(tmp_path, "rooted-rollback-control")

    store.write_bytes(b"{}")
    held_proof = tmp_path / "rolled-back-publication-proof.json"
    proof.rename(held_proof)
    floor = merge_drafts.publication_floor_path(store)
    floor.write_bytes(source.publication_floor_json_bytes(
        source.build_empty_publication_floor_document(store.name)
    ))
    _assert_cli_source_failure(tmp_path, "complete-rooted-rollback")


def test_null_proof_registry_root_accepts_only_absence(
        tmp_path, monkeypatch):
    filename = "null-proof-store.json"
    _configure(monkeypatch, (filename, source.OSM_KEY, None))
    _write_store(tmp_path, filename, {})
    _install_test_baseline(tmp_path, monkeypatch)
    _assert_cli_success(tmp_path, "null-proof-absent-control")

    proof = replay_trust._publication_proof_path(tmp_path, filename)
    proof.write_bytes(merge_drafts.resolver._json_bytes({
        "version": 1, "proofs": {},
    }))
    _assert_cli_source_failure(tmp_path, "null-proof-present")


@pytest.mark.parametrize(
    "filename", ["../store.json", "/tmp/store.json", "nested/store.json"]
)
def test_publication_artifact_path_derivation_rejects_injected_names(filename):
    with pytest.raises(ValueError, match="noncanonical"):
        source.publication_artifact_filenames(filename)


def test_source_generation_portable_boundary_is_exact_and_strict(tmp_path):
    _write_generation_quartet(tmp_path)
    capture = dossier_output.bootstrap_generation(tmp_path, "test-area")
    portable = dossier_output.portable_source_generation(capture, "test-area")
    assert portable == dossier_output.validate_source_generation(
        portable, "test-area"
    )
    assert portable["manifest_path"] == "test-area_generation.json"
    assert set(portable) == {
        "manifest_path", "manifest_sha256", "artifact_sha256",
    }
    assert set(portable["artifact_sha256"]) == {
        "dossier", "serves", "context", "walk",
    }

    malformed_values = []
    for key in portable:
        malformed = copy.deepcopy(portable)
        malformed.pop(key)
        malformed_values.append(malformed)
    extra = copy.deepcopy(portable)
    extra["extra"] = True
    malformed_values.append(extra)
    absolute = copy.deepcopy(portable)
    absolute["manifest_path"] = str(tmp_path / "test-area_generation.json")
    malformed_values.append(absolute)
    uppercase = copy.deepcopy(portable)
    uppercase["manifest_sha256"] = uppercase["manifest_sha256"].upper()
    malformed_values.append(uppercase)
    missing_artifact = copy.deepcopy(portable)
    missing_artifact["artifact_sha256"].pop("walk")
    malformed_values.append(missing_artifact)
    extra_artifact = copy.deepcopy(portable)
    extra_artifact["artifact_sha256"]["extra"] = "f" * 64
    malformed_values.append(extra_artifact)
    bad_artifact = copy.deepcopy(portable)
    bad_artifact["artifact_sha256"]["context"] = "F" * 64
    malformed_values.append(bad_artifact)

    for malformed in malformed_values:
        with pytest.raises(ValueError):
            dossier_output.validate_source_generation(malformed, "test-area")
