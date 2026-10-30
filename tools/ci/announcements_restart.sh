#!/usr/bin/env bash
# The restart step of the announcements job in .github/workflows/ci.yml, run from the repo root with
# the pins of versions.yml in the environment. A real Alertmanager takes two announcements while its
# receiver fails, and a restart loses them. celestia-mon-announcements posts them again at its next
# read, and the receiver gets each one once, also over one more restart.
set -euo pipefail
# shellcheck source=tools/ci/lib.sh
. tools/ci/lib.sh
d="$RUNNER_TEMP/ann-restart"
mkdir -p "$d/cred" "$d/bin"
(cd "$d/bin" && fetch_release alertmanager "$ALERTMANAGER_VERSION" "$ALERTMANAGER_SHA256" alertmanager)
token=ci-only-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
printf '%s' "$token" >"$d/cred/discord-token"
printf 'ci-api-login' >"$d/cred/api-password"
touch "$d/got" "$d/got.fail"
STUB_TOKEN=$token python3 tools/ci/discord_stub.py 8766 "$d/stub.jsonl" &
python3 tools/ci/receiver_stub.py 8767 "$d/got" &
curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:8766/health
curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:8767/
cat >"$d/config.json" <<EOF
{"host": "mon-1", "alertmanager_url": "http://127.0.0.1:9913", "api_user": "celestia-mon",
 "listen": "127.0.0.1:9916", "discord_api": "http://127.0.0.1:8766/api/v10", "interval_seconds": 1,
 "active_minutes": 30, "state_file": "$d/state.json",
 "channels": [{"id": "111111111111111111", "severity": "page"}]}
EOF
# The announcements' route of monitoring/alertmanager.yml.j2.
cat >"$d/alertmanager.yml" <<EOF
route:
  receiver: hook
  group_by: ['...']
  group_wait: 0s
  group_interval: 1m
  repeat_interval: 96h
receivers:
  - name: hook
    webhook_configs:
      - url: http://127.0.0.1:8767/
        send_resolved: false
EOF
trap 'tail -n 30 "$d/alertmanager.log" "$d/log"' EXIT
touch "$d/log"
start_alertmanager() {
  "$d/bin/alertmanager" --config.file="$d/alertmanager.yml" --storage.path="$d/data" \
    --web.listen-address=127.0.0.1:9913 --cluster.listen-address= >>"$d/alertmanager.log" 2>&1 &
  alertmanager=$!
  curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:9913/-/ready
}
stop_alertmanager() {
  kill "$alertmanager"
  wait "$alertmanager" || true
}
once() { CREDENTIALS_DIRECTORY="$d/cred" python3 -I tools/celestia-mon-announcements --config "$d/config.json" --once 2>>"$d/log"; }
# The announcements Alertmanager holds, and those the receiver got, each sorted.
both='["1300000000000000101","1300000000000000102"]'
held() { curl -fsS http://127.0.0.1:9913/api/v2/alerts | jq -c '[.[].labels.announcement] | sort'; }
got() { jq -sc '[.[].alerts[].labels.announcement] | sort' "$d/got"; }
wait_for() {
  for _ in $(seq 60); do
    if "$@"; then return 0; fi
    sleep 1
  done
  "$@"
}
tried() { test -s "$d/got.tries"; }
delivered() { test "$(got)" = "$both"; }

start_alertmanager
# The first read notes the newest message, the next posts both, and the receiver refuses them.
once
once
test "$(held)" = "$both"
wait_for tried
test "$(got)" = '[]'
# The restart loses them, and nothing brings them back but the next read.
stop_alertmanager
start_alertmanager
test "$(held)" = '[]'
rm "$d/got.fail"
once
test "$(held)" = "$both"
wait_for delivered
# Alertmanager holds them, then loses them again in a restart: the next reads post them, and its
# notification log, kept on disk, holds back a second notification.
once
stop_alertmanager
start_alertmanager
once
test "$(held)" = "$both"
sleep 5
delivered
stop_alertmanager
