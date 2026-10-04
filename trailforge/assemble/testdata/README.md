# Assemble test fixtures

`mols-run33-curation-replay.json` is a compact, immutable, **test-only** projection of the Mols Bjerge QA artifact from GitHub Actions run `37036001231` at source SHA `145d9c1d92acc4565382fcc3685d5236d915e457`.

It contains only candidate names, assembly source, member IDs, decisive OSM source tags, indexed source/rendered coordinates, and the exact selected boundary needed to replay sequential Denmark curation and overlap assertions. It is not app Denmark output, is never read at runtime, and must not be copied under `public/` or iOS resources.

Geometry and tags are derived from OpenStreetMap data: © OpenStreetMap contributors. The fixture retains the archived run’s documented limitations: coordinate equality substitutes for unavailable original OSM node IDs, relation identities are synthetic because relation records were absent, and the source artifact predates signed-road restoration.

## Regeneration

The generator is local-only and has no credential or network access. Supply the root of the downloaded artifact named `mols-bjerge-qa-145d9c1d92acc4565382fcc3685d5236d915e457`. It requires these files:

- `README.md`
- `manifest.json`
- `reports/assembly.json`
- `data/aoi/mols-bjerge.raw.geojson`
- `data/aoi/mols-bjerge.trails.geojson`
- `data/aoi/mols-bjerge.areas.geojson`

From the repository root, regenerate and verify the existing manifest-bound bytes with:

```bash
PY=/Users/ivanquen/Documents/Kiro/.venvs/trekdex-parking-20260928/bin/python
"$PY" trailforge/assemble/testdata/generate_mols_run33_replay.py \
  --artifact-dir "/absolute/path/to/mols-bjerge-qa-145d9c1d92acc4565382fcc3685d5236d915e457" \
  --run-id 37036001231 \
  --source-sha 145d9c1d92acc4565382fcc3685d5236d915e457
```

`mols-run33-curation-replay.manifest.json` pins canonical hashes for every required source file plus the generated fixture hash, byte count, and projection counts. JSON source object-key order does not affect those hashes or output bytes. If an intentional generator/schema change alters the projection, rerun the same command with `--update-manifest`, inspect both diffs, then run:

```bash
"$PY" -m unittest trailforge/assemble/test_generate_mols_run33_replay.py
"$PY" -m unittest trailforge/assemble/test_mols_run33_replay.py
```
