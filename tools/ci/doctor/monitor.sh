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
# $labels. in a summary marks an alert that carries its metric's labels too, as on an install.
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
pages pages-3.yml 6
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor \
      --signer remote 2>/dev/null) || true
want='(ID 00000000-0000-0000-0000-000000000001: holds back NodeNotAdvancing, NodePeersLow, ValidatorNotSigning, '
want+='FibreDown, DiskLow, MonitorCannotReach; ends in '
line=$(grep -E '^WARN +21 +silences +1 of 5 active silences ' <<<"$out" || true)
grep -qF -- "$want" <<<"$line" || { echo "check 21 counted a page alert the loaded rules do not have"; exit 1; }
grep -Eq '^SKIP +22 +dead-man ' <<<"$out" || { echo "check 22 did not skip without an alertmanager job"; exit 1; }
