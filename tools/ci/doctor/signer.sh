#!/usr/bin/env bash
# The signer case of the doctor job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
. tools/ci/lib.sh
printf '%s\n' '[Service]' 'ExecStart=/bin/true --signer-grpc-address 127.0.0.1:26669 --otel-endpoint http://127.0.0.1:9915' \
  '[Install]' 'WantedBy=multi-user.target' | sudo tee /etc/systemd/system/ci-standby.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable ci-standby

run sudo bash tools/doctor.sh --config /dev/null --roles fibre --fibre-enabled true --fibre-unit ci-standby \
  --fibre-hosts fibre-a,fibre-b --data-mounts / --head-wait 2
grep -Eq '^WARN +6 +fibre service +standby: ci-standby\.service is inactive, but its unit file is enabled' <<<"$out"
grep -Eq '^WARN +17 +fibre signer +--signer-grpc-address in ExecStart is 127\.0\.0\.1:26669, a plaintext signer without mTLS' <<<"$out"
grep -Eq '^WARN +17 .*leave the unit stopped' <<<"$out"

sudo systemctl disable ci-standby
home="$RUNNER_TEMP/val"
mkdir -p "$home/config"
cat >"$home/config/config.toml" <<'EOF'
priv_validator_laddr = "tcp://0.0.0.0:26658"
priv_validator_grpc_laddr = "10.255.0.1:26669"
priv_validator_grpc_cert_file = "config/grpc.crt"
priv_validator_grpc_key_file = "config/grpc.key"
priv_validator_grpc_client_ca_file = "config/ca.crt"
EOF
run sudo bash tools/doctor.sh --config /dev/null --roles validator,fibre --signer remote --fibre-enabled true \
  --fibre-unit ci-standby --fibre-hosts fibre-a,fibre-b --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^OK +6 +fibre service +standby: ci-standby\.service is inactive' <<<"$out"
grep -Eq '^WARN +17 +fibre signer +standby: .*, a plaintext loopback signer, but .* listens on 10\.255\.0\.1:26669 only' <<<"$out"
grep -Eq '^FAIL +20 +signer binds +priv_validator_laddr = tcp://0\.0\.0\.0:26658 \(nothing listens on it now\) is on every interface' <<<"$out"
grep -Eq '^FAIL +20 .*:26658 \(nothing listens on it now\) is on every interface, and no firewall is active' <<<"$out"

home="$RUNNER_TEMP/backup"
mkdir -p "$home/config" "$RUNNER_TEMP/rpc"
echo 'priv_validator_laddr = ""' >"$home/config/config.toml"
cat >"$RUNNER_TEMP/rpc/status" <<'EOF'
{"jsonrpc": "2.0", "id": -1, "result": {"sync_info": {"latest_block_height": "100", "catching_up": false},
  "validator_info": {"address": "0123456789ABCDEF0123456789ABCDEF01234567", "voting_power": "10"}}}
EOF
serve 127.0.0.1 26657 "$RUNNER_TEMP/rpc" http://127.0.0.1:26657/status
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^FAIL +2 +remote signer +backup node: .*/config/priv_validator_key\.json, which has voting power 10' <<<"$out"
sed -i 's/"voting_power": "10"/"voting_power": "0"/' "$RUNNER_TEMP/rpc/status"
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^INFO +2 +remote signer +backup node: .* is not in the validator set \(voting power 0\)' <<<"$out"
grep -Eq '^OK +20 +signer binds +no signer port' <<<"$out"
# The same node with its RPC on a private address only ([rpc] laddr, for celestia-mon-signal
# on another host): doctor reads /status there, where its key has voting power 10, and not
# the stand-in still answering on 127.0.0.1.
sudo ip addr add 10.255.0.2/32 dev lo
mkdir -p "$RUNNER_TEMP/rpc-private"
sed 's/"voting_power": "0"/"voting_power": "10"/' "$RUNNER_TEMP/rpc/status" >"$RUNNER_TEMP/rpc-private/status"
serve 10.255.0.2 26657 "$RUNNER_TEMP/rpc-private" http://10.255.0.2:26657/status
printf '%s\n' 'priv_validator_laddr = ""' '[rpc]' 'laddr = "tcp://10.255.0.2:26657"' >"$home/config/config.toml"
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^FAIL +2 +remote signer +backup node: .*/config/priv_validator_key\.json, which has voting power 10' <<<"$out"
# A host with another chain's node on 127.0.0.1:26657 (its key with voting power 10): this
# node's RPC is on loopback at another port, which doctor follows, and finds voting power 0.
sed -i 's/"voting_power": "0"/"voting_power": "10"/' "$RUNNER_TEMP/rpc/status"
mkdir -p "$RUNNER_TEMP/rpc-port"
sed 's/"voting_power": "10"/"voting_power": "0"/' "$RUNNER_TEMP/rpc/status" >"$RUNNER_TEMP/rpc-port/status"
serve 127.0.0.1 26757 "$RUNNER_TEMP/rpc-port" http://127.0.0.1:26757/status
printf '%s\n' 'priv_validator_laddr = ""' '[rpc]' 'laddr = "tcp://127.0.0.1:26757"' >"$home/config/config.toml"
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^INFO +2 +remote signer +backup node: .* is not in the validator set \(voting power 0\)' <<<"$out"
# Where that other chain's node answers instead (laddr on the default port), its reply names
# another network than this home's genesis: doctor leaves its key and height out.
sed -i 's/"result": {/"result": {"node_info": {"network": "ci-other"}, /' "$RUNNER_TEMP/rpc/status"
printf '%s\n' '{"genesis_time": "2026-01-01T00:00:00Z", "chain_id": "ci-chain", "initial_height": "1"}' \
  >"$home/config/genesis.json"
printf '%s\n' 'priv_validator_laddr = ""' '[rpc]' 'laddr = "tcp://127.0.0.1:26657"' >"$home/config/config.toml"
run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --node-home "$home" --data-mounts / --head-wait 2
grep -Eq '^INFO +2 +remote signer +backup node: .*its voting power is unknown until the node RPC answers' <<<"$out"
grep -Eq '^FAIL +3 +node progress +http://127\.0\.0\.1:26657/status answers for ci-other, not for ci-chain' <<<"$out"
rm "$home/config/genesis.json"

# An mTLS standby Fibre on a host whose node serves its privval gRPC in plaintext on loopback.
exec_mtls='ExecStart=/bin/true --signer-grpc-address 127.0.0.1:26669 --signer-grpc-ca-file /ci/ca.crt'
exec_mtls+=' --signer-grpc-cert-file /ci/c.crt --signer-grpc-key-file /ci/c.key --otel-endpoint http://127.0.0.1:9915'
printf '%s\n' '[Service]' "$exec_mtls" | sudo tee /etc/systemd/system/ci-mtls.service >/dev/null
sudo systemctl daemon-reload
home="$RUNNER_TEMP/val-plain"
mkdir -p "$home/config"
echo 'priv_validator_grpc_laddr = "127.0.0.1:26669"' >"$home/config/config.toml"
run sudo bash tools/doctor.sh --config /dev/null --roles validator,fibre --signer remote --fibre-enabled true \
  --fibre-unit ci-mtls --fibre-hosts fibre-a,fibre-b --node-home "$home" --data-mounts / --head-wait 2
line=$(grep -E '^WARN +17 +fibre signer +standby: ' <<<"$out")
grep -qF 'an mTLS signer on this host, but the node serves its privval gRPC on 127.0.0.1:26669 in plaintext' <<<"$line"
