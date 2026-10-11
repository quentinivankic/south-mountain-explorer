"""Controls for the deterministic, local-only run-35 replay generator."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

_HERE = Path(__file__).resolve().parent
_TESTDATA = _HERE / "testdata"
sys.path.insert(0, str(_TESTDATA))
import generate_mols_run35_root_cause_replay as generator  # noqa: E402

_ARCHIVE_TEXT = os.environ.get("TREKDEX_RUN35_ARCHIVE")
_ARCHIVE = Path(_ARCHIVE_TEXT) if _ARCHIVE_TEXT else None
_ARCHIVE_AVAILABLE = bool(_ARCHIVE and _ARCHIVE.is_dir())
_FIXTURE = _TESTDATA / generator.FIXTURE_NAME
_MANIFEST = _TESTDATA / generator.MANIFEST_NAME


class MolsRun35Generator(unittest.TestCase):
    def test_source_contract_is_exactly_six_local_json_files(self):
        self.assertEqual(generator._SOURCE_FILES, (
            "manifest.json",
            "data/source/mols-bjerge.relation-members.raw.json",
            "reports/assembly.json",
            "data/aoi/mols-bjerge.trails.geojson",
            "data/aoi/mols-bjerge.removed.geojson",
            "data/aoi/mols-bjerge.exact-area.geojson",
        ))
        source = Path(generator.__file__).read_text(encoding="utf-8")
        for forbidden in (
                "urllib", "requests", "http.client", "urlopen", "boto3",
                "subprocess", "socket"):
            self.assertNotIn(forbidden, source)

    def test_checked_in_fixture_is_canonical_and_manifest_bound(self):
        fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        raw = _FIXTURE.read_bytes()
        self.assertEqual(raw, generator.canonical_json_bytes(fixture))
        self.assertEqual(len(raw), manifest["output"]["bytes"])
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(), manifest["output"]["sha256"])
        self.assertEqual(set(manifest["source_files"]),
                         set(generator._SOURCE_FILES))
        self.assertEqual(manifest["source"]["attribution"],
                         generator.ATTRIBUTION)
        self.assertEqual(manifest["source"]["license"], generator.ODBL)

    @unittest.skipUnless(
        _ARCHIVE_AVAILABLE, "set TREKDEX_RUN35_ARCHIVE for integration check")
    def test_regeneration_without_manifest_update_is_byte_identical(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / generator.FIXTURE_NAME
            result = generator.generate(
                artifact_dir=_ARCHIVE,
                output=output,
                manifest_path=_MANIFEST,
            )
            self.assertEqual(output.read_bytes(), _FIXTURE.read_bytes())
            self.assertEqual(result, json.loads(_MANIFEST.read_text()))

    @unittest.skipUnless(
        _ARCHIVE_AVAILABLE, "set TREKDEX_RUN35_ARCHIVE for integration check")
    def test_source_identity_and_run_identity_tampering_fail_closed(self):
        documents, identities = generator._load_sources(_ARCHIVE)
        changed = copy.deepcopy(documents)
        changed["manifest.json"]["source_sha"] = "0" * 40
        with self.assertRaisesRegex(generator.FixtureError,
                                    "manifest identity"):
            generator.build_fixture(changed)

        with TemporaryDirectory() as temporary:
            false_manifest = Path(temporary) / generator.MANIFEST_NAME
            expected = json.loads(_MANIFEST.read_text())
            expected["source_files"]["manifest.json"] = {
                **identities["manifest.json"],
                "sha256": "0" * 64,
            }
            false_manifest.write_text(json.dumps(expected), encoding="utf-8")
            with self.assertRaisesRegex(generator.FixtureError,
                                        "identity changed"):
                generator.generate(
                    artifact_dir=_ARCHIVE,
                    output=Path(temporary) / generator.FIXTURE_NAME,
                    manifest_path=false_manifest,
                )


if __name__ == "__main__":
    unittest.main()
