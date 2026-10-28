#!/usr/bin/env bash
# The standby case of the doctor job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
. tools/ci/lib.sh
printf '%s\n' '[Service]' 'ExecStart=/bin/true --signer-grpc-address localhost:26669 --otel-endpoint http://127.0.0.1:9915' \
  | sudo tee /etc/systemd/system/ci-fibre.service >/dev/null
sudo systemctl daemon-reload

run sudo bash tools/doctor.sh --config /dev/null --roles fibre --fibre-enabled true --fibre-unit ci-fibre \
  --fibre-hosts fibre-a,fibre-b --data-mounts / --head-wait 2
grep -Eq '^OK +6 +fibre service +standby: ci-fibre\.service is inactive and port 7980 is closed' <<<"$out"
grep -Eq '^FAIL +17 +fibre signer +--signer-grpc-address in ExecStart is localhost:26669, not an IP address' <<<"$out"
grep -Eq '^FAIL +17 .*leave the unit stopped' <<<"$out"

run sudo bash tools/doctor.sh --config /dev/null --roles fibre --fibre-enabled true --fibre-unit ci-fibre \
  --fibre-hosts fibre-a --data-mounts / --head-wait 2
grep -Eq '^FAIL +6 +fibre service +ci-fibre\.service is inactive\.' <<<"$out"

home="$RUNNER_TEMP/backup-node"
mkdir -p "$home/config"
echo 'priv_validator_laddr = ""' >"$home/config/config.toml"
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^INFO +2 +remote signer +backup node: priv_validator_laddr is empty' <<<"$out"
echo 'priv_validator_laddr = "tcp://127.0.0.1:26658"' >"$home/config/config.toml"
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^INFO +2 +remote signer +backup node: priv_validator_laddr = tcp://127\.0\.0\.1:26658' <<<"$out"
if grep -Eq '^FAIL +2 ' <<<"$out"; then echo "check 2 failed on a backup node"; exit 1; fi
