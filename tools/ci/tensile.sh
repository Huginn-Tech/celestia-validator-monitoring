#!/usr/bin/env bash
# The tensile job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
d="$RUNNER_TEMP/tensile"
v="$d/api/api/v1/validators"
mkdir -p "$v/celestiavalcons1ok" "$v/celestiavalcons1none" "$v/celestiavalcons1old"
cat >"$v/celestiavalcons1ok/status" <<'EOF'
{"host": "203.0.113.7:7980", "jailed": false, "bond_status": "BOND_STATUS_BONDED", "endpoint_state": "reachable",
 "reachable": true, "identity_status": "verified", "last_reachable_at": "2026-10-18T06:25:11.680342473Z",
 "last_endpoint_check": {"at": "2026-10-18T06:25:11.680342473Z", "outcome": "REACHABLE"},
 "obligations": {"served": 215, "broken": 0, "rate": {"num": 215, "den": 215, "value": 1}},
 "signing": {"assigned": 216, "signed": 215, "last_endorsed_at": "2026-10-18T06:20:27.865311985Z",
             "last_assigned_at": "2026-10-18T06:24:27.865311985Z"}}
EOF
cat >"$v/celestiavalcons1none/status" <<'EOF'
{"host": "", "jailed": false, "reachable": null, "identity_status": "", "last_endpoint_check": null}
EOF
python3 -m http.server 8766 --bind 127.0.0.1 --directory "$d/api" >/dev/null 2>&1 &
curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null \
  http://127.0.0.1:8766/api/v1/validators/celestiavalcons1ok/status
run() {
  printf '{"api": "%s", "validator": "%s", "listen": "127.0.0.1:9917", "interval_seconds": 60}' "$1" "$2" >"$d/config.json"
  python3 -I tools/celestia-mon-tensile --config "$d/config.json" --once 2>"$d/err"
}
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1ok)
printf '%s\n' "$m"
grep -qx 'celestia_mon_fibre_registered{endpoint="203.0.113.7:7980"} 1' <<<"$m"
grep -qx 'celestia_mon_fibre_reachable{endpoint="203.0.113.7:7980"} 1' <<<"$m"
grep -qx 'celestia_mon_fibre_identity_verified{endpoint="203.0.113.7:7980"} 1' <<<"$m"
grep -Eq '^celestia_mon_fibre_last_check_timestamp_seconds\{endpoint="203\.0\.113\.7:7980"\} 1792304711\.68' <<<"$m"
grep -qx 'celestia_mon_fibre_last_check_info{endpoint="203.0.113.7:7980",outcome="REACHABLE"} 1' <<<"$m"
grep -Eq '^celestia_mon_tensile_last_success_timestamp_seconds [1-9]' <<<"$m"
grep -Eq '^celestia_mon_fibre_last_assigned_timestamp_seconds 1792304667\.86' <<<"$m"
grep -Eq '^celestia_mon_fibre_last_endorsed_timestamp_seconds 1792304427\.86' <<<"$m"
grep -qx 'celestia_mon_fibre_blobs_assigned_24h 216' <<<"$m"
grep -qx 'celestia_mon_fibre_blobs_endorsed_24h 215' <<<"$m"
grep -qx 'celestia_mon_fibre_obligations_24h{outcome="served"} 215' <<<"$m"
grep -qx 'celestia_mon_fibre_obligations_24h{outcome="broken"} 0' <<<"$m"
if grep -q 'jailed\|bonded' <<<"$m"; then echo "Tensile is used for the outside check and the blob record only"; exit 1; fi
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1none)
grep -qx 'celestia_mon_fibre_registered{endpoint=""} 0' <<<"$m"
if grep -q '^celestia_mon_fibre_reachable' <<<"$m"; then echo "reachability without a registered host"; exit 1; fi
if grep -Eq '^celestia_mon_fibre_(last_assigned|last_endorsed|blobs|obligations)' <<<"$m"; then
  echo "a blob record without Tensile's signing object"; exit 1
fi
# Out of the active set, jailed or not: Tensile's host comes from the chain's list of bonded
# providers, which has left the validator while its registration stands, so no registration is
# read, not a 0 that FibreDown would take for none. A bonded validator with no host has none.
mkdir -p "$v/celestiavalcons1jailed" "$v/celestiavalcons1unbonded" "$v/celestiavalcons1bonded"
cat >"$v/celestiavalcons1jailed/status" <<'EOF'
{"host": "", "jailed": true, "bond_status": "BOND_STATUS_UNBONDING", "reachable": null, "identity_status": "unknown", "last_endpoint_check": null}
EOF
cat >"$v/celestiavalcons1unbonded/status" <<'EOF'
{"host": "", "jailed": false, "bond_status": "BOND_STATUS_UNBONDED", "reachable": null, "identity_status": "unknown", "last_endpoint_check": null}
EOF
cat >"$v/celestiavalcons1bonded/status" <<'EOF'
{"host": "", "jailed": false, "bond_status": "BOND_STATUS_BONDED", "reachable": null, "identity_status": "", "last_endpoint_check": null}
EOF
for out in celestiavalcons1jailed celestiavalcons1unbonded; do
  m=$(run http://127.0.0.1:8766/api/v1 "$out")
  grep -Eq '^celestia_mon_tensile_last_success_timestamp_seconds [1-9]' <<<"$m"
  if grep -q '^celestia_mon_fibre_registered' <<<"$m"; then echo "$out out of the active set read as not registered"; exit 1; fi
done
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1bonded)
grep -qx 'celestia_mon_fibre_registered{endpoint=""} 0' <<<"$m"
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1unknown)
grep -qx 'celestia_mon_tensile_last_success_timestamp_seconds 0.0' <<<"$m"
grep -qx 'celestia_mon_tensile_errors_total{reason="not_found"} 1' <<<"$m"
grep -q 'check celestia_mon_tensile.validator' "$d/err"
m=$(run http://127.0.0.1:9/api/v1 celestiavalcons1ok)
grep -qx 'celestia_mon_tensile_errors_total{reason="network"} 1' <<<"$m"
# A redirect is not followed: http.server answers a directory named without its slash with
# 301, and the directory's index holds a good status.
mkdir -p "$v/celestiavalcons1moved/status"
cp "$v/celestiavalcons1ok/status" "$v/celestiavalcons1moved/status/index.html"
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1moved)
grep -qx 'celestia_mon_tensile_errors_total{reason="http"} 1' <<<"$m"
grep -q 'Tensile answered 301 for .*, a redirect, which is not followed' "$d/err"
if grep -q '^celestia_mon_fibre_registered' <<<"$m"; then echo "the redirect was followed"; exit 1; fi
# An answer over 1 MiB is not read.
mkdir -p "$v/celestiavalcons1big"
{ printf '{"host": "203.0.113.7:7980", "padding": "'; head -c 2000000 /dev/zero | tr '\0' x; printf '"}'; } >"$v/celestiavalcons1big/status"
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1big)
grep -qx 'celestia_mon_tensile_last_success_timestamp_seconds 0.0' <<<"$m"
grep -q 'the answer is larger than 1048576 bytes' "$d/err"
# Times that cannot exist (30 February, month 13, a number), counts that are no numbers and a
# lone surrogate in the host are left out or shown plainly, never a failed scrape.
mkdir -p "$v/celestiavalcons1odd"
cat >"$v/celestiavalcons1odd/status" <<'EOF'
{"host": "203.0.113.7:7980\ud800", "reachable": true, "identity_status": "verified",
 "last_endpoint_check": {"at": "2026-02-30T06:25:11Z", "outcome": "REACHABLE"},
 "obligations": {"served": 3, "broken": "many"},
 "signing": {"assigned": 4, "signed": "x", "last_endorsed_at": "2026-13-01T00:00:00Z", "last_assigned_at": 7}}
EOF
m=$(run http://127.0.0.1:8766/api/v1 celestiavalcons1odd)
printf '%s\n' "$m"
grep -qx 'celestia_mon_fibre_reachable{endpoint="203.0.113.7:7980?"} 1' <<<"$m"
grep -qx 'celestia_mon_fibre_blobs_endorsed_24h 0' <<<"$m"
grep -qx 'celestia_mon_fibre_obligations_24h{outcome="broken"} 0' <<<"$m"
if grep -Eq '^celestia_mon_fibre_(last_check_timestamp|last_assigned_timestamp|last_endorsed_timestamp)' <<<"$m"; then
  echo "a time that cannot exist was served"; exit 1
fi
# Without last_assigned_at, the time of the newest assigned blob is the time of Tensile's newest
# block (/tip) at the read at which the number of assigned blobs rose, only while that block is
# under 2 minutes older than Tensile's clock at that read and the one before. The service reads
# every second here. Written aside and moved, so it never reads half a file: a read that fails
# drops the number.
tip() {
  printf '{"height": 100, "block_time": "%s", "server_time": "2026-10-18T06:25:20Z"}' "$1" >"$d/api/api/v1/tip.new"
  mv "$d/api/api/v1/tip.new" "$d/api/api/v1/tip"
}
old() {
  printf '{"host": "203.0.113.7:7980", "reachable": true, "identity_status": "verified", "signing": {"assigned": %s, "signed": 0}}' \
    "$1" >"$v/celestiavalcons1old/status.new"
  mv "$v/celestiavalcons1old/status.new" "$v/celestiavalcons1old/status"
}
tip 2026-10-18T06:25:15Z
old 216
printf '{"api": "http://127.0.0.1:8766/api/v1", "validator": "celestiavalcons1old", "listen": "127.0.0.1:9917", "interval_seconds": 1}' \
  >"$d/config.json"
python3 -I tools/celestia-mon-tensile --config "$d/config.json" 2>"$d/err" &
svc=$!
scrape() { curl -fsS http://127.0.0.1:9917/metrics 2>/dev/null || true; }
wait_for() {
  for _ in $(seq 30); do grep -Eq "$1" <<<"$(scrape)" && return 0; sleep 1; done
  scrape; echo "no line matching $1"; return 1
}
wait_for '^celestia_mon_fibre_blobs_assigned_24h 216$'
sleep 3
if grep -q '^celestia_mon_fibre_last_assigned' <<<"$(scrape)"; then echo "a blob time without a rise"; exit 1; fi
# Tensile catches up after its own outage (its newest block 10 minutes old): the blobs it
# finds meanwhile are not new, and the first read in step again only notes the number.
tip 2026-10-18T06:15:20Z
sleep 3
old 230
wait_for '^celestia_mon_fibre_blobs_assigned_24h 230$'
tip 2026-10-18T06:25:15Z
sleep 3
if grep -q '^celestia_mon_fibre_last_assigned' <<<"$(scrape)"; then echo "a Tensile catch-up taken for new blobs"; exit 1; fi
# A rise between two reads in step sets the time: Tensile's newest block then, 06:25:15.
old 231
wait_for '^celestia_mon_fibre_last_assigned_timestamp_seconds 1792304715\.0$'
# A read that fails, in each way it can, drops the number: blobs found after it may have settled
# while Tensile was not read, so the first good read only notes them, and the time stays. The next
# rise sets it, to Tensile's newest block then. was is the seconds of that time past 06:25.
n=232
was=15
for reason in not_found network format; do
  case $reason in
    not_found) rm "$v/celestiavalcons1old/status" ;;
    network) printf 'no JSON' >"$v/celestiavalcons1old/status" ;;
    format) printf '{}' >"$v/celestiavalcons1old/status" ;;
  esac
  wait_for "^celestia_mon_tensile_errors_total\{reason=\"$reason\"\} [1-9]"
  tip "2026-10-18T06:25:$((was + 1))Z"
  old "$n"
  wait_for "^celestia_mon_fibre_blobs_assigned_24h $n\$"
  sleep 3
  grep -qx "celestia_mon_fibre_last_assigned_timestamp_seconds 17923047$was\.0" <<<"$(scrape)" ||
    { echo "blobs found after a failed read ($reason) taken for new ones"; exit 1; }
  old "$((n + 1))"
  was=$((was + 1))
  wait_for "^celestia_mon_fibre_last_assigned_timestamp_seconds 17923047$was\.0\$"
  n=$((n + 2))
done
kill "$svc"
