#!/bin/bash
# Build TrekDex (Release, development-signed) and install it on a phone,
# over the app that is already there. USB first, one retry, never uninstall.
#
# Usage:
#   scripts/install-device.sh [--dry-run] [--skip-build] [--allow-network] <device-udid>
#
#   <device-udid>    hardware UDID (00008xxx-xxxxxxxxxxxxxxxx) or the
#                    devicectl identifier. Run
#                    `xcrun devicectl list devices` to see them.
#   --dry-run        check the device, then print the build and install
#                    commands instead of running them.
#   --skip-build     install the app already built in the DerivedData dir.
#   --allow-network  allow Wi-Fi when the phone is not on USB. By default
#                    the script stops and asks for the cable, because
#                    network installs were the flaky path (devicectl
#                    "unavailable", RemotePairingError).
#
# Environment (optional):
#   TREKDEX_DEVICE_DERIVED_DATA  build dir (default: outside the repo, in
#                                ~/Library/Developer/Xcode/DerivedData)
#   TREKDEX_TEAM_ID              signing team (default 4278GL7BF4)
#   TREKDEX_DEVICECTL, TREKDEX_XCODEBUILD, TREKDEX_XCODEGEN
#                                replace the real tools (used by tests)
#
# The built app carries BuildStamp.plist with the git commit
# (scripts/stamp-local-build.sh, TREKDEX_STAMP_COMMIT=YES), so Settings >
# About > Build shows which commit is on the phone.
#
# Exit codes: 0 installed (or dry run done), 2 usage, 3 device not ready,
# 4 build failed, 5 install failed after the retry.
set -euo pipefail

BUNDLE_ID="com.southmountainexplorer.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
TEAM="${TREKDEX_TEAM_ID:-4278GL7BF4}"
DD="${TREKDEX_DEVICE_DERIVED_DATA:-$HOME/Library/Developer/Xcode/DerivedData/TrekDex-device-$(basename "$REPO")}"
APP="$DD/Build/Products/Release-iphoneos/SouthMountainExplorer.app"
RETRY_WAIT="${TREKDEX_RETRY_WAIT:-10}"

DRY_RUN=0
SKIP_BUILD=0
ALLOW_NETWORK=0
DEVICE=""

usage() {
  sed -n '5,17p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --allow-network) ALLOW_NETWORK=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "install-device: unknown option $1" >&2; usage >&2; exit 2 ;;
    *)
      if [ -n "$DEVICE" ]; then
        echo "install-device: only one device UDID, got $DEVICE and $1" >&2
        exit 2
      fi
      DEVICE="$1"
      ;;
  esac
  shift
done
if [ -z "$DEVICE" ]; then
  echo "install-device: missing <device-udid>" >&2
  usage >&2
  exit 2
fi

devicectl() {
  if [ -n "${TREKDEX_DEVICECTL:-}" ]; then "$TREKDEX_DEVICECTL" "$@"; else xcrun devicectl "$@"; fi
}
xcodebuild_cmd() { "${TREKDEX_XCODEBUILD:-xcodebuild}" "$@"; }
xcodegen_cmd() { "${TREKDEX_XCODEGEN:-xcodegen}" "$@"; }

# Print a command the way it would run (dry run), or run it.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf 'would run: %s' "${1%_cmd}"; shift; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# Find the phone in `devicectl list devices` and set DEVICE_ID (the
# devicectl identifier), TRANSPORT and DEVICE_LABEL. Simulators are skipped.
find_device() {
  local json="$TMP_DIR/devices.json"
  if ! devicectl list devices --timeout 60 --json-output "$json" >/dev/null 2>"$TMP_DIR/list.err"; then
    echo "install-device: devicectl list devices failed:" >&2
    cat "$TMP_DIR/list.err" >&2
    exit 3
  fi
  local line
  line=$(/usr/bin/python3 - "$json" "$DEVICE" <<'PY'
import json, sys
path, want = sys.argv[1], sys.argv[2].lower()
for d in json.load(open(path)).get("result", {}).get("devices", []):
    if d.get("visibilityClass") == "simulators":
        continue
    hw = d.get("hardwareProperties", {})
    ids = {str(d.get("identifier", "")).lower(), str(hw.get("udid", "")).lower()}
    if want in ids:
        c = d.get("connectionProperties", {})
        p = d.get("deviceProperties", {})
        # "-" for a missing value: read splits on tabs and would merge
        # an empty field into the next one.
        print("\t".join([
            str(d.get("identifier") or "-"),
            str(c.get("transportType") or "-"),
            str(c.get("pairingState") or "-"),
            "%s, %s, iOS %s" % (p.get("name", "?"), hw.get("marketingName", "?"), p.get("osVersionNumber", "?")),
        ]))
        break
PY
  )
  if [ -z "$line" ]; then
    echo "install-device: no paired phone with UDID $DEVICE. Plug it in, unlock it and tap Trust." >&2
    exit 3
  fi
  local pairing
  IFS=$'\t' read -r DEVICE_ID TRANSPORT pairing DEVICE_LABEL <<<"$line"
  echo "Device: $DEVICE_LABEL (id $DEVICE_ID), transport $TRANSPORT, $pairing"
  if [ "$pairing" != "paired" ]; then
    echo "install-device: the phone is not paired ($pairing). Unlock it and tap Trust." >&2
    exit 3
  fi
}

# USB first: stop unless the phone is wired (or --allow-network), then
# prove devicectl can reach it.
check_device() {
  find_device
  if [ "$TRANSPORT" != "wired" ]; then
    if [ "$ALLOW_NETWORK" = 1 ]; then
      echo "Not on USB ($TRANSPORT); continuing because of --allow-network."
    else
      echo "install-device: the phone is not on USB (transport $TRANSPORT). Plug in the cable and run again, or pass --allow-network." >&2
      exit 3
    fi
  fi
  if ! devicectl device info details --device "$DEVICE_ID" --timeout 60 --quiet >/dev/null 2>"$TMP_DIR/details.err"; then
    echo "install-device: the phone is not reachable. Unlock it; if it says 4016, check the VPN." >&2
    cat "$TMP_DIR/details.err" >&2
    exit 3
  fi
  echo "Device reachable."
}

# Release, development-signed for the phone. Same overrides the device
# installs have used: Release in project.yml is Manual / Apple Distribution
# for TestFlight.
build_app() {
  if [ ! -f "$REPO/ios/SouthMountainExplorer/Resources/areas-index.json" ]; then
    echo "install-device: ios/SouthMountainExplorer/Resources/areas-index.json is missing; see ios/README.md step 2." >&2
    exit 4
  fi
  local log="$DD/install-device-build.log"
  [ "$DRY_RUN" = 1 ] || mkdir -p "$DD"
  echo "Building (Release, generic iOS, team $TEAM). Log: $log"
  if ! (cd "$REPO/ios" && run xcodegen_cmd generate); then
    echo "install-device: xcodegen generate failed" >&2
    exit 4
  fi
  local cmd=(xcodebuild_cmd build
    -project "$REPO/ios/SouthMountainExplorer.xcodeproj"
    -scheme SouthMountainExplorer
    -configuration Release
    -destination "generic/platform=iOS"
    -derivedDataPath "$DD"
    -allowProvisioningUpdates
    -allowProvisioningDeviceRegistration
    CODE_SIGN_STYLE=Automatic
    "CODE_SIGN_IDENTITY=Apple Development"
    "DEVELOPMENT_TEAM=$TEAM"
    PROVISIONING_PROFILE_SPECIFIER=
    TREKDEX_STAMP_COMMIT=YES)
  if [ "$DRY_RUN" = 1 ]; then
    run "${cmd[@]}"
    return 0
  fi
  if ! "${cmd[@]}" >"$log" 2>&1; then
    echo "install-device: build failed. Last lines of $log:" >&2
    tail -n 30 "$log" >&2
    exit 4
  fi
  echo "Build succeeded: $APP"
}

# Install over the existing app. One retry after a short wait, with the
# device checked again first. There is no uninstall anywhere in this script:
# devicectl install keeps the app's data.
install_app() {
  if [ "$DRY_RUN" = 0 ] && [ ! -d "$APP" ]; then
    echo "install-device: no built app at $APP (run without --skip-build)." >&2
    exit 5
  fi
  local attempt=1
  while :; do
    echo "Installing (attempt $attempt of 2) on $DEVICE_LABEL"
    if run devicectl device install app --device "$DEVICE_ID" --timeout 300 "$APP"; then
      return 0
    fi
    if [ "$attempt" -ge 2 ]; then
      echo "install-device: install failed twice. Unlock the phone, check the cable (and the VPN for 4016), then run again." >&2
      exit 5
    fi
    echo "Install failed; checking the phone again in ${RETRY_WAIT}s, then one retry."
    sleep "$RETRY_WAIT"
    check_device
    attempt=2
  done
}

show_result() {
  [ "$DRY_RUN" = 1 ] && return 0
  local commit
  commit=$(/usr/libexec/PlistBuddy -c "Print :SourceCommit" "$APP/BuildStamp.plist" 2>/dev/null || echo "none")
  echo "Built commit: $commit"
  devicectl device info apps --device "$DEVICE_ID" --bundle-id "$BUNDLE_ID" --timeout 60 || \
    echo "install-device: installed, but could not list the app afterwards." >&2
}

check_device
if [ "$SKIP_BUILD" = 0 ]; then
  build_app
fi
install_app
show_result
if [ "$DRY_RUN" = 1 ]; then
  echo "Dry run done: nothing built or installed."
else
  echo "Installed on $DEVICE_LABEL."
fi
