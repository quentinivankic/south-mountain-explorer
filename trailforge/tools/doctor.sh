#!/usr/bin/env bash
# trailforge preflight — check the homelab is ready BEFORE an 80 GB download.
# Non-fatal: prints a report and a single PASS/WARN verdict.
set -uo pipefail

ok=0; warn=0
say()  { printf "  %-22s %s\n" "$1" "$2"; }
good() { say "$1" "OK — $2"; ok=$((ok+1)); }
bad()  { say "$1" "WARN — $2"; warn=$((warn+1)); }

echo "trailforge doctor"
echo "-----------------"

# tools
for t in osmium python3 curl; do
  if command -v "$t" >/dev/null 2>&1; then good "$t" "$($t --version 2>&1 | head -1)"
  else bad "$t" "not found (make setup)"; fi
done
if python3 -c "import osmium" 2>/dev/null; then good "pyosmium" "importable"
else bad "pyosmium" "pip install osmium"; fi
if python3 -c "import shapely" 2>/dev/null; then good "shapely" "importable"
else bad "shapely" "pip install shapely"; fi

# disk: planet PBF ~80GB + subset + working room → want ~200GB free
avail_kb=$(df -Pk . | awk 'NR==2{print $4}')
avail_gb=$((avail_kb/1024/1024))
if [ "$avail_gb" -ge 200 ]; then good "disk (free)" "${avail_gb} GB (planet-ready)"
elif [ "$avail_gb" -ge 20 ]; then bad "disk (free)" "${avail_gb} GB — ok for a Geofabrik extract, not the full planet"
else bad "disk (free)" "${avail_gb} GB — too low"; fi

# RAM: streaming design targets <=16GB; just report it
if [ -r /proc/meminfo ]; then
  ram_gb=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024 / 1024 ))
  say "RAM (total)" "${ram_gb} GB (streaming design targets <=16)"
fi

echo "-----------------"
if [ "$warn" -eq 0 ]; then echo "verdict: PASS ($ok checks) — ready for make download-*"
else echo "verdict: $warn warning(s), $ok ok — review above before a big download"; fi
