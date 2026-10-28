#!/usr/bin/env bash
# The doctor-readiness job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
. tools/ci/lib.sh
dir="$RUNNER_TEMP/readiness"
mkdir -p "$dir/data" "$dir/am/-" "$dir/node"
cd "$dir"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 -subj /CN=fibre-ci \
  -keyout key.pem -out cert.pem 2>/dev/null
python "$GITHUB_WORKSPACE/tools/ci/grpc_health_stub.py" cert.pem key.pem >stub.log 2>&1 &
fetch_release blackbox_exporter "$BLACKBOX_EXPORTER_VERSION" "$BLACKBOX_EXPORTER_SHA256" blackbox_exporter
# The template holds no variable: it is the file the install writes.
cp "$GITHUB_WORKSPACE/ansible/roles/celestia_mon_blackbox/templates/blackbox.yml.j2" blackbox.yml
./blackbox_exporter --config.check --config.file=blackbox.yml
./blackbox_exporter --config.file=blackbox.yml --web.listen-address=127.0.0.1:9919 >blackbox.log 2>&1 &
curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:9919/-/healthy
for _ in $(seq 30); do grep -qx ready stub.log && break; sleep 1; done
grep -qx ready stub.log || { cat stub.log; exit 1; }

# The four states, as the alert rules and the dashboard read them.
probe() { curl -fsS "http://127.0.0.1:9919/probe?module=fibre_health&target=127.0.0.1:$1"; }
out=$(probe 7981)
printf '%s\n' "$out"
grep -qx 'probe_success 1' <<<"$out"
grep -qx 'probe_grpc_healthcheck_response{serving_status="SERVING"} 1' <<<"$out"
out=$(probe 7982)
grep -qx 'probe_success 0' <<<"$out"
grep -qx 'probe_grpc_healthcheck_response{serving_status="NOT_SERVING"} 1' <<<"$out"
grep -qx 'probe_grpc_healthcheck_response{serving_status="SERVING"} 0' <<<"$out"
out=$(probe 7983)
grep -qx 'probe_grpc_status_code 12' <<<"$out"
if grep -q '^probe_grpc_healthcheck_response{.*} 1$' <<<"$out"; then echo "a Fibre without readiness reads as answered"; exit 1; fi
out=$(probe 7984)
grep -qx 'probe_success 0' <<<"$out"
grep -qx 'probe_grpc_status_code 14' <<<"$out"

fetch_release prometheus "$PROMETHEUS_VERSION" "$PROMETHEUS_SHA256" prometheus
for h in a b c d e; do
  v=1
  if [ "$h" = e ]; then v=0; fi
  printf 'node_systemd_unit_state{host="fibre-%s",name="fibre.service",state="active"} %s\n' "$h" "$v"
done >node/metrics.txt
python3 -m http.server 9100 --bind 127.0.0.1 --directory node >/dev/null 2>&1 &
cat > prometheus.yml <<'EOF'
global:
  scrape_interval: 5s
scrape_configs:
  - job_name: node
    honor_labels: true
    metrics_path: /metrics.txt
    fallback_scrape_protocol: PrometheusText0.0.4
    static_configs:
      - targets: ["127.0.0.1:9100"]
  # As the install renders it (prometheus.yml.j2), for five Fibre hosts.
  - job_name: fibre_health
    metrics_path: /probe
    params:
      module: [fibre_health]
    static_configs:
      - {targets: ["127.0.0.1:7981"], labels: {host: fibre-a, role: fibre}}
      - {targets: ["127.0.0.1:7982"], labels: {host: fibre-b, role: fibre}}
      - {targets: ["127.0.0.1:7983"], labels: {host: fibre-c, role: fibre}}
      - {targets: ["127.0.0.1:7984"], labels: {host: fibre-d, role: fibre}}
      - {targets: ["127.0.0.1:7985"], labels: {host: fibre-e, role: fibre}}
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: "127.0.0.1:9919"
EOF
start_prometheus
for _ in $(seq 60); do
  if curl -fsS 'http://127.0.0.1:9912/api/v1/query?query=count(up==1)' | grep -q '"value":[[0-9.]*,"6"]'; then break; fi
  sleep 1
done

rc=0
out=$(bash "$GITHUB_WORKSPACE/tools/doctor.sh" --config /dev/null --roles monitor --fibre-enabled true 2>stderr.txt) || rc=$?
printf '%s\n' "$out"
cat stderr.txt
if [ "$rc" -ne 1 ] || [ -s stderr.txt ]; then
  echo "doctor exited $rc (expected 1: fibre-b is Not ready) or wrote to stderr"
  exit 1
fi
grep -Eq '^OK +11 +monitor stack +.* 6 of 6 scrape targets up' <<<"$out"
line=$(grep -E '^FAIL +24 +fibre ready +Not ready: fibre-b \(127\.0\.0\.1:7982\): ' <<<"$out")
for want in 'Fibre reports that at least one of its own checks fails, so uploads to it may fail;' \
            "Not checked: fibre-d: the monitor cannot reach Fibre's port at 127.0.0.1:7984 (no connection)" \
            'Not reported: fibre-c (127.0.0.1:7983): Fibre older than v10.5.0' \
            'Ready: fibre-a (127.0.0.1:7981)' \
            'standby (Fibre unit not active, port closed): fibre-e.' \
            "Fix: on fibre-b, \"journalctl -u fibre | grep 'health check failed' | tail -n 5\""; do
  grep -qF -- "$want" <<<"$line" || { echo "check 24 does not say: $want"; exit 1; }
done
