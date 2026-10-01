#!/usr/bin/env bash
# Colorado per-area adjudication pipeline (dossier + serves gate + foot-walk + OSM context).
# Env-configured tools write to a LOCAL working dir (fast); OSM extracts live on the NAS.
# Usage: ./run_co.sh <area-slug>
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: run_co.sh <canonical-area-slug>" >&2
    exit 2
fi
SLUG="$1"
if [[ -z "$SLUG" || ! "$SLUG" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    echo "area slug must be nonempty and canonical" >&2
    exit 2
fi

TOOLS="$(cd "$(dirname "$0")" && pwd)"
export PADJ_TMP="${PADJ_TMP:-$(cd "$TOOLS/.." && pwd)/work/co}"
export PADJ_PARKING_PBF="${PADJ_PARKING_PBF:-/mnt/raid/trekdex/parking-adjud/co/osm/colorado_parking.osm.pbf}"
export PADJ_US="${PADJ_US:-/mnt/raid/trekdex/parking-adjud/co/osm/colorado-latest.osm.pbf}"
CTX="${PADJ_CTX:-/mnt/raid/trekdex/parking-adjud/co/osm/ctx}"
PYTHON="${PADJ_PYTHON:-python3}"
DOSSIER_TOOL="${PADJ_DOSSIER_TOOL:-$TOOLS/dossier.py}"
FOOT_TOOL="${PADJ_FOOT_TOOL:-$TOOLS/foot_route_area.py}"
CONTEXT_TOOL="${PADJ_CONTEXT_TOOL:-$TOOLS/context_classify.py}"
MANIFEST_TOOL="${PADJ_MANIFEST_TOOL:-$TOOLS/dossier_output.py}"

mkdir -p "$PADJ_TMP"
CTX_PBF="$CTX/${SLUG}_ctx.osm.pbf"
if [[ ! -f "$CTX_PBF" || -L "$CTX_PBF" ]]; then
    echo "[$SLUG] NO REGULAR CTX PBF" >&2
    exit 3
fi

# This verified value is the only parent accepted after all producers finish.
PARENT="$("$PYTHON" "$MANIFEST_TOOL" parent-manifest-sha256 "$SLUG")"
if [[ "$PARENT" != "null" && ! "$PARENT" =~ ^[0-9a-f]{64}$ ]]; then
    echo "[$SLUG] invalid parent manifest status" >&2
    exit 4
fi

ln -sfn "$CTX_PBF" "$PADJ_TMP/${SLUG}_ctx.osm.pbf"

# pipefail preserves the producer status; grep also requires its final status line.
"$PYTHON" "$DOSSIER_TOOL" "$SLUG" 2>&1 \
    | grep -E '^context:' \
    | sed "s/^/[$SLUG] /"
"$PYTHON" "$FOOT_TOOL" "$SLUG" 2>&1 \
    | grep -E '^walk joined into dossier:' \
    | sed "s/^/[$SLUG] /"
"$PYTHON" "$CONTEXT_TOOL" "$SLUG" "$CTX_PBF" 2>&1 \
    | grep -E "^${SLUG}: " \
    | sed "s/^/[$SLUG] /"

COMMIT_JSON="$("$PYTHON" "$MANIFEST_TOOL" commit-generation "$SLUG" \
    --parent-manifest-sha256 "$PARENT")"
VERIFY_JSON="$("$PYTHON" "$MANIFEST_TOOL" verify-generation "$SLUG")"

manifest_sha256() {
    local value="$1"
    if [[ "$value" == *$'\n'* ]]; then
        return 1
    fi
    if [[ "$value" =~ \"manifest_sha256\":\"([0-9a-f]{64})\" ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

COMMIT_SHA="$(manifest_sha256 "$COMMIT_JSON")"
VERIFY_SHA="$(manifest_sha256 "$VERIFY_JSON")"
if [[ "$COMMIT_SHA" != "$VERIFY_SHA" ]]; then
    echo "[$SLUG] committed and verified generation hashes differ" >&2
    exit 5
fi
printf 'DONE generation=%s\n' "$VERIFY_SHA"
