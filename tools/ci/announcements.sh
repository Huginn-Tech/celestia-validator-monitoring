#!/usr/bin/env bash
# The announcements job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
d="$RUNNER_TEMP/ann"
mkdir -p "$d/cred"
token=ci-only-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
printf '%s' "$token" >"$d/cred/discord-token"
printf 'ci-api-login' >"$d/cred/api-password"
STUB_TOKEN=$token python3 tools/ci/discord_stub.py 8765 "$d/posts.jsonl" &
curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:8765/health
cat >"$d/config.json" <<EOF
{"host": "mon-1", "alertmanager_url": "http://127.0.0.1:8765", "api_user": "celestia-mon",
 "listen": "127.0.0.1:9916", "discord_api": "http://127.0.0.1:8765/api/v10", "interval_seconds": 1,
 "active_minutes": 30, "state_file": "$d/state.json",
 "channels": [{"id": "111111111111111111", "severity": "page"}, {"id": "222222222222222222", "severity": "message"}]}
EOF
once() { CREDENTIALS_DIRECTORY="$d/cred" python3 -I tools/celestia-mon-announcements --config "$d/config.json" --once 2>>"$d/log"; }
once
cat "$d/log"
test ! -e "$d/posts.jsonl"
grep -q '^channel 111111111111111111: reading from now on' "$d/log"
grep -q '^channel 222222222222222222: Discord answered 403 Missing Access' "$d/log"
once
once
test "$(wc -l <"$d/posts.jsonl")" = 2
# ...103 is an alert a webhook posted into the channel, not an announcement.
grep -q '^channel 111111111111111111: passed over 1 message(s) not posted by a followed channel' "$d/log"
python3 - "$d/posts.jsonl" <<'EOF'
import json, re, sys
alerts = [a for line in open(sys.argv[1]) for a in json.loads(line)]
assert [a["labels"]["announcement"] for a in alerts] == ["1300000000000000101", "1300000000000000102"], alerts
first, second = alerts
assert first["labels"] == {"alertname": "CelestiaAnnouncement", "severity": "page", "host": "mon-1",
                           "source": "Celestia #mocha-announcements", "announcement": "1300000000000000101"}, first
text = first["annotations"]["description"]
assert re.fullmatch(r"Mocha upgrades to v10\.5\.0 at \d{4}-\d\d-\d\d \d\d:\d\d UTC\. "
                    r"Details: https://docs\.celestia\.org/upgrade\?network=mocha&v=10\.5\.0", text), text
link = "https://discord.com/channels/638338779505229824/333333333333333333/5101"
assert first["annotations"]["link"] == first["generatorURL"] == link, first
assert second["labels"]["source"] == "Celestia #mainnet-announcements", second
assert second["annotations"]["description"] == "Mainnet notice\n\nRead #channel before the upgrade", second
print("posted:", json.dumps(alerts, indent=1))
EOF
# Odd messages and answers: a time too far off stays as written; a message that cannot be read
# and one Alertmanager refuses are passed over, so the one after them is still sent and the
# channel's place moves past all of them; a redirect is not followed (the token never reaches
# /followed); an answer over 1 MiB is not read.
jq --arg s "$d/odd-state.json" '.state_file = $s | .channels = [{"id": "666666666666666666", "severity": "page"},
  {"id": "777777777777777777", "severity": "page"}, {"id": "888888888888888888", "severity": "page"}]' \
  "$d/config.json" >"$d/odd.json"
odd() { CREDENTIALS_DIRECTORY="$d/cred" python3 -I tools/celestia-mon-announcements --config "$d/odd.json" --once 2>>"$d/odd.log"; }
odd
odd
cat "$d/odd.log"
test "$(wc -l <"$d/posts.jsonl")" = 4
tail -n 2 "$d/posts.jsonl" | jq -se '[.[][] | .labels.announcement] == ["1300000000000000201", "1300000000000000204"]'
tail -n 2 "$d/posts.jsonl" | jq -se '.[0][0].annotations.description == "Far off: <t:99999999999999999999:F>"'
grep -q '^channel 666666666666666666: message 1300000000000000202 cannot be read, passed over' "$d/odd.log"
grep -qF 'channel 666666666666666666: Alertmanager refused announcement 1300000000000000203 as invalid (400), passed over' "$d/odd.log"
grep -qF 'channel 666666666666666666: sent 2 announcement(s) to Alertmanager' "$d/odd.log"
jq -e '."666666666666666666" == "1300000000000000204"' "$d/odd-state.json"
grep -q '^channel 777777777777777777: Discord answered 302 Found, a redirect, which is not followed' "$d/odd.log"
test ! -e "$d/posts.jsonl.followed"
grep -qF "channel 888888888888888888: Discord's answer to /channels/888888888888888888 is not readable: the answer is larger than" "$d/odd.log"
odd
test "$(wc -l <"$d/posts.jsonl")" = 4
CREDENTIALS_DIRECTORY="$d/cred" python3 -I tools/celestia-mon-announcements --config "$d/config.json" 2>>"$d/log" &
pid=$!
for _ in $(seq 30); do
  m=$(curl -fsS http://127.0.0.1:9916/metrics || true)
  if grep -q '^celestia_mon_announcements_errors_total{channel="222222222222222222",reason="forbidden"}' <<<"$m"; then break; fi
  sleep 1
done
kill "$pid"
printf '%s\n' "$m"
grep -Eq '^celestia_mon_announcements_last_success_timestamp_seconds\{channel="111111111111111111"\} [1-9]' <<<"$m"
grep -Eq '^celestia_mon_announcements_last_success_timestamp_seconds\{channel="222222222222222222"\} 0' <<<"$m"
printf 'not-the-token' >"$d/cred/discord-token"
once
grep -q 'Discord refused the bot token (401)' "$d/log"
if grep -qF "$token" "$d/log"; then echo "the log holds the token"; exit 1; fi
