#!/usr/bin/env bash
# The doctor-monitor job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
. tools/ci/lib.sh
dir="$RUNNER_TEMP/monitor"
mkdir -p "$dir/data" "$dir/am/-"
cd "$dir"
fetch_release prometheus "$PROMETHEUS_VERSION" "$PROMETHEUS_SHA256" prometheus
cat > prometheus.yml <<'EOF'
global:
  scrape_interval: 5s
  evaluation_interval: 5s
rule_files:
  - rules.yml
  - pages.yml
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["127.0.0.1:9912"]
  - job_name: closed
    static_configs:
      - targets: ["127.0.0.1:9"]
EOF
cat > rules.yml <<'EOF'
groups:
  - name: ci
    rules:
      - alert: CiPresent
        expr: 'up{job="prometheus"} == 1 and on (instance) prometheus_ready > bool 0'
      - alert: CiMissing
        expr: >-
          sum by (host) (rate(ci_missing_total{reason!~"a\"b|}"}[5m] offset 1h))
          > on (host) group_left (role) max by (host, role) (ci_missing_info) * 1e6
      - alert: CiOther
        expr: >-
          label_replace(vector(1), "dst", "$1", "src", "(.*)") unless on () absent(prometheus_build_info)
          or ci_missing_gauge @ start() < +Inf or count(ci:missing:rate5m) > 0
          or quantile_over_time(0.5, ci_missing_q[10m:1m]) > 0
EOF
# The page rules of each doctor run below: no metric (check 12 still finds 8 names), never firing.
# $labels.<name> in a summary marks a label the alert carries besides its rule's, as on an install:
# host for one that comes from a host's series, endpoint for FibreDown's check from outside.
cat > pages-1.yml <<'EOF'
groups:
  - name: pages
    rules:
      - {alert: NodeNotAdvancing, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: NodePeersLow, expr: vector(1) == 0, labels: {severity: message}}
      - {alert: NodePeersLow, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: ValidatorNotSigning, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: FibreDown, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: DiskLow, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: MonitorCannotReach, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: GovVoteMissing, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.proposal_id }}"}}
EOF
cat > pages-2.yml <<'EOF'
groups:
  - name: pages
    rules:
      - {alert: NodeNotAdvancing, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: NodePeersLow, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: ValidatorNotSigning, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: FibreDown, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: DiskLow, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: ValidatorSigningTwice, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: FibreNotServing, expr: vector(1) == 0, labels: {severity: page, reason: not_ready}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: FibreNotServing, expr: vector(1) == 0, labels: {severity: message, reason: missed_uploads}}
      - {alert: MonitorCannotReach, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: GovVoteMissing, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.proposal_id }}"}}
EOF
cat > pages-3.yml <<'EOF'
groups:
  - name: pages
    rules:
      - {alert: NodeNotAdvancing, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: NodePeersLow, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: ValidatorNotSigning, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: FibreDown, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: FibreDown, expr: vector(1) == 0, labels: {severity: page, check: reachability}, annotations: {summary: "{{ $labels.endpoint }}"}}
      - {alert: DiskLow, expr: vector(1) == 0, labels: {severity: page}, annotations: {summary: "{{ $labels.host }}"}}
      - {alert: MonitorCannotReach, expr: vector(1) == 0, labels: {severity: page}}
EOF
# pages FILE N: Prometheus runs FILE's page rules in place of the last ones, N of them with severity page.
pages() {
  cp "$1" pages.yml
  curl -fsS -X POST http://127.0.0.1:9912/-/reload
  curl -fsS 'http://127.0.0.1:9912/api/v1/rules?type=alert' |
    jq -e --argjson n "$2" '[.data.groups[].rules[] | select(.labels.severity == "page")] | length == $n' >/dev/null ||
    { echo "Prometheus did not load the $2 page rules of $1"; exit 1; }
}
cp pages-1.yml pages.yml
start_prometheus --web.enable-lifecycle
# /-/ready answers before the first scrape: wait until both targets have been scraped once.
for _ in $(seq 60); do
  if curl -fsS 'http://127.0.0.1:9912/api/v1/query?query=count(up)' | grep -q '"value":[[0-9.]*,"2"]'; then break; fi
  sleep 1
done
# Check 21 reads these through doctor's JSON reader: escaped quotes and braces, a regex
# matcher that admits the pages, a host silence that does not, an expired silence, one of
# GovVoteMissing for one proposal (a page where its page rule is loaded), and two of
# FibreNotServing, of whose reasons only not_ready pages (where that page rule is loaded).
mkdir -p am/api/v2
soon=$(date -u -d '+30 min' +%Y-%m-%dT%H:%M:%S.000Z)
cat > am/api/v2/silences <<EOF
[{"id":"00000000-0000-0000-0000-000000000001","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"ci","createdBy":"ci \"quoted\" {x}","endsAt":"2099-01-01T00:00:00.000Z",
  "matchers":[{"isEqual":true,"isRegex":true,"name":"severity","value":"page|message"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000002","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"planned","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"host","value":"val-1"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000003","status":{"state":"expired"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"old","createdBy":"op","endsAt":"2026-10-17T11:00:00.000Z",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"alertname","value":"FibreDown"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000004","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"voted elsewhere","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"alertname","value":"GovVoteMissing"},
              {"isEqual":true,"isRegex":false,"name":"proposal_id","value":"9"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000005","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"no blobs","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"alertname","value":"FibreNotServing"},
              {"isEqual":true,"isRegex":false,"name":"reason","value":"missed_uploads"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000006","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"fibre restart","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"alertname","value":"FibreNotServing"},
              {"isEqual":true,"isRegex":false,"name":"reason","value":"not_ready"}],"startsAt":"2026-10-17T10:00:00.000Z"}]
EOF

rc=0
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor --gov-page true 2>stderr.txt) || rc=$?
printf '%s\n' "$out"
cat stderr.txt
if [ "$rc" -ne 1 ] || [ -s stderr.txt ]; then
  echo "doctor exited $rc (expected 1: the closed target is down) or wrote to stderr"
  exit 1
fi
if ! grep -Eq '^FAIL +11 +monitor stack +1 of 2 scrape targets down: closed http://127\.0\.0\.1:9/metrics \(connection refused\)\.' <<<"$out"; then
  echo "check 11 did not report the closed target"
  exit 1
fi
line=$(grep -E '^WARN +12 +rule metrics +5 of 8 metrics ' <<<"$out" || true)
if [ -z "$line" ]; then
  echo "check 12 did not find exactly 8 metric names with 5 of them missing"
  exit 1
fi
for m in ci_missing_total ci_missing_info ci_missing_gauge ci:missing:rate5m ci_missing_q; do
  if ! grep -qF " $m (" <<<"$line"; then echo "check 12 does not list $m"; exit 1; fi
done
grep -qF ' ci_missing_total (CiMissing)' <<<"$line" || { echo "check 12 names the wrong rule for ci_missing_total"; exit 1; }
line=$(grep -E '^WARN +21 +silences +2 of 5 active silences ' <<<"$out" || true)
for want in '{severity=~"page|message"} by ci "quoted" {x} until 2099-01-01T00:00:00.000Z' \
            '(ID 00000000-0000-0000-0000-000000000001: holds back NodeNotAdvancing, NodePeersLow, ' \
            'ValidatorNotSigning, FibreDown, DiskLow, MonitorCannotReach, GovVoteMissing; ends in ' \
            ' hours), {alertname="GovVoteMissing", proposal_id="9"} by op until ' \
            '(ID 00000000-0000-0000-0000-000000000004: holds back GovVoteMissing); the others: {host="val-1"} by op'; do
  grep -qF -- "$want" <<<"$line" || { echo "check 21 did not report the page silence: $want"; exit 1; }
done
if grep -q '000000000003' <<<"$out"; then echo "check 21 listed an expired silence"; exit 1; fi
# With the page rules of a remote signer and Fibre on (pages-2.yml), ValidatorSigningTwice and
# FibreNotServing's not_ready page count too, and a silence of another FibreNotServing reason does
# not; with neither of them nor GovVoteMissing's page loaded (pages-3.yml), none of the three counts.
pages pages-2.yml 9
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor --gov-page true \
      --signer remote --fibre-enabled true 2>/dev/null) || true
line=$(grep -E '^WARN +21 +silences +3 of 5 active silences ' <<<"$out" || true)
for want in '(ID 00000000-0000-0000-0000-000000000001: holds back NodeNotAdvancing, NodePeersLow, ' \
            'ValidatorNotSigning, FibreDown, DiskLow, ValidatorSigningTwice, FibreNotServing, ' \
            'MonitorCannotReach, GovVoteMissing; ends in ' \
            '{alertname="FibreNotServing", reason="not_ready"} by op until ' \
            '(ID 00000000-0000-0000-0000-000000000006: holds back FibreNotServing); the others: {host="val-1"} by op' \
            ', {alertname="FibreNotServing", reason="missed_uploads"} by op until '; do
  grep -qF -- "$want" <<<"$line" || { echo "check 21 with a remote signer and Fibre: missing $want"; exit 1; }
done
pages pages-3.yml 7
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor \
      --signer remote 2>/dev/null) || true
want='(ID 00000000-0000-0000-0000-000000000001: holds back NodeNotAdvancing, NodePeersLow, ValidatorNotSigning, '
want+='FibreDown, DiskLow, MonitorCannotReach; ends in '
line=$(grep -E '^WARN +21 +silences +1 of 5 active silences ' <<<"$out" || true)
grep -qF -- "$want" <<<"$line" || { echo "check 21 counted a page alert the loaded rules do not have"; exit 1; }
grep -Eq '^SKIP +22 +dead-man ' <<<"$out" || { echo "check 22 did not skip without an alertmanager job"; exit 1; }
# A silence for one host holds back the page alerts that come from that host's series (NodePeersLow),
# not FibreDown's, whose alerts carry the endpoint and no host (pages-3.yml is still loaded).
cat > am/api/v2/silences <<EOF
[{"id":"00000000-0000-0000-0000-000000000007","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"planned","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":true,"name":"alertname","value":"FibreDown|NodePeersLow"},
              {"isEqual":true,"isRegex":false,"name":"host","value":"val-1"}],"startsAt":"2026-10-17T10:00:00.000Z"}]
EOF
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^WARN +21 +silences +1 of 1 active silences ' <<<"$out" || true)
want='{alertname=~"FibreDown|NodePeersLow", host="val-1"} by op until '
grep -qF -- "$want" <<<"$line" || { echo "check 21 did not report the host silence: $want"; exit 1; }
want='(ID 00000000-0000-0000-0000-000000000007: holds back NodePeersLow). '
grep -qF -- "$want" <<<"$line" || { echo "check 21 counted a host silence for a page alert that names no host"; exit 1; }
# With no Prometheus to read the page rules from, check 21 still warns on a silence that runs long.
cat > am/api/v2/silences <<EOF
[{"id":"00000000-0000-0000-0000-000000000008","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"ci","createdBy":"op","endsAt":"2099-01-01T00:00:00.000Z",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"severity","value":"page"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000009","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"planned","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"host","value":"val-1"}],"startsAt":"2026-10-17T10:00:00.000Z"}]
EOF
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor --prometheus-url http://127.0.0.1:9 2>/dev/null) || true
grep -Eq '^FAIL +11 +monitor stack +Prometheus is not ready ' <<<"$out" || { echo "check 11 did not fail without Prometheus"; exit 1; }
line=$(grep -E '^WARN +21 +silences +1 of 2 active silences end more than 4 hours from now: ' <<<"$out" || true)
for want in '{severity="page"} by op until 2099-01-01T00:00:00.000Z (ID 00000000-0000-0000-0000-000000000008: ends in ' \
            ' hours); the others: {host="val-1"} by op until ' \
            '; which page alerts they hold back is not known: Prometheus not answering (check 11). '; do
  grep -qF -- "$want" <<<"$line" || { echo "check 21 without Prometheus: missing $want"; exit 1; }
done
# A silence that names a page alert only by a label its rule sets holds it back like one on
# alertname: check with FibreDown's reachability rule loaded (pages-3.yml), reason with
# FibreNotServing's not_ready rule (pages-2.yml), whose summaries read other labels.
cat > am/api/v2/silences <<EOF
[{"id":"00000000-0000-0000-0000-000000000010","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"ci","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"check","value":"reachability"}],"startsAt":"2026-10-17T10:00:00.000Z"},
 {"id":"00000000-0000-0000-0000-000000000011","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"ci","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"reason","value":"not_ready"}],"startsAt":"2026-10-17T10:00:00.000Z"}]
EOF
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^WARN +21 +silences +1 of 2 active silences ' <<<"$out" || true)
want='(ID 00000000-0000-0000-0000-000000000010: holds back FibreDown); the others: {reason="not_ready"} by op'
grep -qF -- "$want" <<<"$line" || { echo "check 21 missed a silence on a rule's check label: $want"; exit 1; }
pages pages-2.yml 9
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^WARN +21 +silences +1 of 2 active silences ' <<<"$out" || true)
want='(ID 00000000-0000-0000-0000-000000000011: holds back FibreNotServing); the others: {check="reachability"} by op'
grep -qF -- "$want" <<<"$line" || { echo "check 21 missed a silence on a rule's reason label: $want"; exit 1; }
# A short silence for one host names no page alert, which the line says of its length.
cat > am/api/v2/silences <<EOF
[{"id":"00000000-0000-0000-0000-000000000012","status":{"state":"active"},"updatedAt":"2026-10-17T10:00:00.000Z",
  "comment":"planned","createdBy":"op","endsAt":"$soon",
  "matchers":[{"isEqual":true,"isRegex":false,"name":"host","value":"val-1"}],"startsAt":"2026-10-17T10:00:00.000Z"}]
EOF
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
want='1 active silences, none naming a page alert or ending more than 4 hours from now (a silence for one host counts by its length only): {host="val-1"} by op'
line=$(grep -E '^OK +21 +silences ' <<<"$out" || true)
grep -qF -- "$want" <<<"$line" || { echo "check 21 did not count a host silence by its length: $want"; exit 1; }

# The jobs after the prometheus one: scrape JOBS writes them and Prometheus reloads; the stand-in
# Alertmanager on 9913 serves their metrics from am/.
base=$(sed '/job_name: closed/,$d' prometheus.yml)
scrape() {
  printf '%s\n%s\n' "$base" "$1" >prometheus.yml
  curl -fsS -X POST http://127.0.0.1:9912/-/reload
}
wait_for() { # PromQL that must have a result
  for _ in $(seq 60); do
    if curl -fsS -G --data-urlencode "query=$1" http://127.0.0.1:9912/api/v1/query | grep -q '"value":'; then return 0; fi
    sleep 1
  done
  echo "Prometheus has no result for $1"
  exit 1
}

# A failed heartbeat is the one FAIL of this run (check 9 reads the runner's clock), and it must
# reach the exit code: report sets the global failed, which no check may shadow.
printf '%s\n' '[]' >am/api/v2/silences
pages pages-3.yml 7
printf '%s\n' 'alertmanager_notifications_total{receiver_name="deadman"} 10' \
  'alertmanager_notifications_failed_total{receiver_name="deadman"} 1' >am/am.txt
scrape '  - job_name: alertmanager
    metrics_path: /am.txt
    fallback_scrape_protocol: PrometheusText0.0.4
    static_configs:
      - targets: ["127.0.0.1:9913"]'
wait_for 'alertmanager_notifications_failed_total{job="alertmanager",receiver_name="deadman"} == 1'
printf '%s\n' 'alertmanager_notifications_total{receiver_name="deadman"} 12' \
  'alertmanager_notifications_failed_total{receiver_name="deadman"} 5' >am/am.txt
wait_for 'alertmanager_notifications_failed_total{job="alertmanager",receiver_name="deadman"} == 5'
rc=0
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor --fibre-enabled false 2>/dev/null) || rc=$?
printf '%s\n' "$out"
grep -Eq '^FAIL +22 +dead-man +heartbeats to the dead-man service failed in the last 10 minutes' <<<"$out" ||
  { echo "check 22 did not fail on failed heartbeats"; exit 1; }
others=$(grep -E '^FAIL ' <<<"$out" | grep -Ev '^FAIL +(9|22) ' || true)
if [ -n "$others" ]; then echo "this run should fail on check 22 alone: $others"; exit 1; fi
if [ "$rc" -ne 1 ]; then echo "doctor exited $rc on a FAIL of check 22"; exit 1; fi
grep -qF 'Some checks failed.' <<<"$out" || { echo "doctor did not close with its failure line"; exit 1; }

# Check 12 on the metrics of celestia-mon-tensile: a target down, a service that has read nothing,
# a validator with no endpoint registered (no check of it yet), one with an endpoint but no check,
# and one out of the active set.
cat > pages-4.yml <<'EOF'
groups:
  - name: pages
    rules:
      - {alert: NodeNotAdvancing, expr: vector(1) == 0, labels: {severity: page}}
      - alert: CiTensile
        expr: >-
          celestia_mon_fibre_reachable == 0 or celestia_mon_fibre_last_check_timestamp_seconds == 0
          or celestia_mon_fibre_registered == 0 or celestia_mon_tensile_last_success_timestamp_seconds == 0
EOF
pages pages-4.yml 1
tensile_job() { # the target of the job tensile
  printf '%s\n' '  - job_name: tensile' '    metrics_path: /tensile.txt' '    fallback_scrape_protocol: PrometheusText0.0.4' \
    '    static_configs:' "      - targets: [\"$1\"]"
}
scrape "$(tensile_job 127.0.0.1:9)"
wait_for 'up{job="tensile"} == 0'
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^FAIL +12 ' <<<"$out" || true)
grep -qF 'celestia_mon_fibre_reachable (tensile target not up: check 11)' <<<"$line" ||
  { echo "check 12 did not fail on the down tensile target"; exit 1; }
says 11 '"journalctl -u celestia-mon-tensile -n 50" on this host'
echo 'celestia_mon_tensile_last_success_timestamp_seconds 0' >am/tensile.txt
scrape "$(tensile_job 127.0.0.1:9913)"
wait_for 'celestia_mon_tensile_last_success_timestamp_seconds{job="tensile"} == 0'
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^FAIL +12 ' <<<"$out" || true)
want='celestia_mon_fibre_registered (celestia-mon-tensile has read no status from Tensile since it started'
grep -qF -- "$want" <<<"$line" || { echo "check 12 did not fail on a service that has read nothing"; exit 1; }
printf '%s\n' 'celestia_mon_tensile_last_success_timestamp_seconds 1700000000' 'celestia_mon_fibre_registered{endpoint=""} 0' >am/tensile.txt
wait_for 'max(celestia_mon_fibre_registered) == 0'
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^NOT YET +12 ' <<<"$out" || true)
for want in 'celestia_mon_fibre_reachable (after a Fibre host registered on chain (x/valaddr))' \
            'celestia_mon_fibre_last_check_timestamp_seconds (after a Fibre host registered on chain (x/valaddr))'; do
  grep -qF -- "$want" <<<"$line" || { echo "check 12 does not wait for a registration: $want"; exit 1; }
done
if grep -E '^(WARN|FAIL) +12 ' <<<"$out" | grep -qF celestia_mon_fibre_reachable; then
  echo "check 12 reports the check from outside before a Fibre host is registered"
  exit 1
fi
printf '%s\n' 'celestia_mon_tensile_last_success_timestamp_seconds 1700000000' \
  'celestia_mon_fibre_registered{endpoint="fibre.example.invalid:7980"} 1' >am/tensile.txt
wait_for 'max(celestia_mon_fibre_registered) == 1'
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^WARN +12 ' <<<"$out" || true)
grep -qF ' celestia_mon_fibre_reachable (CiTensile)' <<<"$line" ||
  { echo "check 12 does not warn of a registered endpoint with no check from outside"; exit 1; }
# Out of the active set the service reads Tensile and serves no registration: none of the three is a
# renamed metric.
echo 'celestia_mon_tensile_last_success_timestamp_seconds 1700000000' >am/tensile.txt
wait_for 'absent(celestia_mon_fibre_registered)'
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor 2>/dev/null) || true
line=$(grep -E '^NOT YET +12 ' <<<"$out" || true)
for want in 'celestia_mon_fibre_registered (after Tensile lists the validator in the active set)' \
            'celestia_mon_fibre_reachable (after Tensile lists the validator in the active set)' \
            'celestia_mon_fibre_last_check_timestamp_seconds (after Tensile lists the validator in the active set)'; do
  grep -qF -- "$want" <<<"$line" || { echo "check 12 does not wait for the active set: $want"; exit 1; }
done
if grep -E '^(WARN|FAIL) +12 ' <<<"$out" | grep -qF celestia_mon_fibre_; then
  echo "check 12 reports a validator out of the active set as a missing metric"
  exit 1
fi

# The store's histogram has no series until Fibre stores an upload, while Fibre pushes the rest.
cat > pages-5.yml <<'EOF'
groups:
  - name: pages
    rules:
      - {alert: NodeNotAdvancing, expr: vector(1) == 0, labels: {severity: page}}
      - {alert: CiStore, expr: fibre_server_store_put_duration_seconds_count == 0 or fibre_server_upload_shard_budget_bytes == 0}
EOF
pages pages-5.yml 1
echo 'fibre_server_upload_shard_budget_bytes 1' >am/otel.txt
scrape '  - job_name: otelcol
    metrics_path: /otel.txt
    fallback_scrape_protocol: PrometheusText0.0.4
    static_configs:
      - targets: ["127.0.0.1:9913"]'
wait_for 'fibre_server_upload_shard_budget_bytes{job="otelcol"} == 1'
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor --fibre-enabled true 2>/dev/null) || true
line=$(grep -E '^NOT YET +12 ' <<<"$out" || true)
grep -qF 'fibre_server_store_put_duration_seconds_count (after the first upload Fibre stores)' <<<"$line" ||
  { echo "check 12 does not wait for the first upload Fibre stores"; exit 1; }
