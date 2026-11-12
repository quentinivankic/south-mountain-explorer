#!/bin/bash
# Xcode Run Script phase for the app target (see ios/project.yml
# postBuildScripts). Its one declared output is BuildStamp.plist inside the
# app bundle ($SCRIPT_OUTPUT_FILE_0), so the build re-signs the app when it
# changes.
#
# Debug builds (local simulator builds and the PR unit test build), and
# Release builds made with TREKDEX_STAMP_COMMIT=YES (local phone builds from
# scripts/install-device.sh): writes the short git SHA, plus "-dirty" when
# tracked files have uncommitted changes, as SourceCommit. Settings shows it
# after the build number, so a phone shows which commit it runs.
#
# Any other Release build (TestFlight and App Store archives) gets no stamp
# file; the build number there comes from ios-testflight.yml
# (github.run_number). Info.plist is never edited here. Never fails the
# build: without git, no stamp.
set -uo pipefail

OUT="${SCRIPT_OUTPUT_FILE_0:-}"
if [ -z "$OUT" ]; then
  echo "warning: stamp-local-build: no declared output file, nothing written"
  exit 0
fi

# Release stamps only when asked (scripts/install-device.sh passes
# TREKDEX_STAMP_COMMIT=YES for local phone builds) and never on GitHub
# Actions, so a TestFlight archive never carries a stamp.
if [ "${CONFIGURATION:-}" != "Debug" ]; then
  if [ "${TREKDEX_STAMP_COMMIT:-}" != "YES" ] || [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    rm -f "$OUT"
    echo "stamp-local-build: ${CONFIGURATION:-unknown} build, no stamp"
    exit 0
  fi
fi

REPO="${SRCROOT:-.}"
if ! SHA=$(git -C "$REPO" rev-parse --short=9 HEAD 2>/dev/null); then
  rm -f "$OUT"
  echo "stamp-local-build: no git repo at $REPO, no stamp"
  exit 0
fi
# --no-optional-locks: never take the index lock from inside a build.
if [ -n "$(git --no-optional-locks -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  SHA="$SHA-dirty"
fi

mkdir -p "$(dirname "$OUT")"
TMP="$OUT.tmp.$$"
cat > "$TMP" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>SourceCommit</key>
	<string>$SHA</string>
</dict>
</plist>
EOF
# Replace only when the content changed, so an unchanged commit does not
# force a re-sign on every build.
if cmp -s "$TMP" "$OUT"; then
  rm -f "$TMP"
else
  mv -f "$TMP" "$OUT"
fi
echo "stamp-local-build: commit $SHA"
