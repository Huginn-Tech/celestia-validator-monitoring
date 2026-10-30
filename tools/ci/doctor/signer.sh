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

# A plaintext privval gRPC beyond loopback: celestia-appd refuses it unless it is allowed, in
# config.toml or by the start flag, whose place in ExecStart does not matter; =false leaves it off.
home="$RUNNER_TEMP/val-open"
mkdir -p "$home/config"
echo 'priv_validator_grpc_laddr = "10.255.0.5:26669"' >"$home/config/config.toml"
node_start() { # the words after the node's command in ExecStart: runs doctor on that node
  printf '%s\n' '[Service]' "ExecStart=/bin/true $1" | sudo tee /etc/systemd/system/ci-appd.service >/dev/null
  sudo systemctl daemon-reload
  run sudo bash tools/doctor.sh --config /dev/null --roles backup_node --appd-unit ci-appd --node-home "$home" --data-mounts / --head-wait 2
}
node_start 'start --privval-grpc-allow-insecure'
grep -Eq '^FAIL +20 +signer binds +priv_validator_grpc_laddr = 10\.255\.0\.5:26669' <<<"$out"
says 20 'With --privval-grpc-allow-insecure in ExecStart and no mTLS, whoever reaches priv_validator_grpc_laddr'
says 20 'take --privval-grpc-allow-insecure out of ExecStart in ci-appd.service'
node_start 'start --privval-grpc-allow-insecure --home /ci'
grep -Eq '^FAIL +20 +signer binds ' <<<"$out"
node_start 'start --home /ci'
grep -Eq '^WARN +20 +signer binds ' <<<"$out"
# The same setting in config.toml, which keeps its own wording.
printf '%s\n' 'priv_validator_grpc_laddr = "10.255.0.5:26669"' 'priv_validator_grpc_allow_insecure = true' >"$home/config/config.toml"
node_start 'start --home /ci'
grep -Eq '^FAIL +20 +signer binds ' <<<"$out"
says 20 'With priv_validator_grpc_allow_insecure = true and no mTLS'
# A plaintext listener on loopback stays a warning, with the flag off or on.
serve 127.0.0.1 26670 "$RUNNER_TEMP/rpc" http://127.0.0.1:26670/status
echo 'priv_validator_grpc_laddr = "127.0.0.1:26670"' >"$home/config/config.toml"
node_start 'start --privval-grpc-allow-insecure=false'
grep -Eq '^WARN +20 +signer binds +priv_validator_grpc_laddr = 127\.0\.0\.1:26670' <<<"$out"
node_start 'start --privval-grpc-allow-insecure'
grep -Eq '^WARN +20 +signer binds ' <<<"$out"
# A plaintext listener doctor sees beyond loopback proves it is allowed, however it was set.
serve 10.255.0.2 26671 "$RUNNER_TEMP/rpc" http://10.255.0.2:26671/status
echo 'priv_validator_grpc_laddr = "10.255.0.2:26671"' >"$home/config/config.toml"
node_start 'start --home /ci'
grep -Eq '^FAIL +20 +signer binds ' <<<"$out"
says 20 'With a listener beyond loopback and no mTLS'

# A Fibre that cannot start, and its stand-in for an active Fibre, whose home (FIBRE_HOME) holds its
# server_config.toml. The flag after "fibre" reaches doctor as a word of ExecStart.
fh="$RUNNER_TEMP/fibre-signer"
mkdir -p "$fh/config"
fibre_start() { # the words after the stand-in's command in ExecStart
  printf '%s\n' '[Service]' "Environment=FIBRE_HOME=$fh" "ExecStart=/bin/sh -c 'exec sleep infinity' fibre $1" \
    | sudo tee /etc/systemd/system/ci-active.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl restart ci-active
}
fibre_only=(--config /dev/null --roles fibre --fibre-enabled true --fibre-unit ci-active --data-mounts / --head-wait 2)
# Only some of the three signer files: a standby warns, an active Fibre fails, each naming what is missing.
exec_some='ExecStart=/bin/true --signer-grpc-address 127.0.0.1:26669 --signer-grpc-cert-file /ci/c.crt'
exec_some+=' --signer-grpc-key-file /ci/c.key --otel-endpoint http://127.0.0.1:9915'
printf '%s\n' '[Service]' "$exec_some" | sudo tee /etc/systemd/system/ci-some.service >/dev/null
sudo systemctl daemon-reload
run sudo bash tools/doctor.sh --config /dev/null --roles fibre --fibre-enabled true --fibre-unit ci-some \
  --fibre-hosts fibre-a,fibre-b --data-mounts / --head-wait 2
grep -Eq '^WARN +17 +fibre signer +standby: --signer-grpc-address in ExecStart is 127\.0\.0\.1:26669, but only 2 of ' <<<"$out"
says 17 '(no signer_grpc_ca_file): Fibre exits when it starts, as they "must be set together"'
printf '%s\n' 'signer_grpc_address = "127.0.0.1:26669"' 'signer_grpc_cert_file = "c.crt"' >"$fh/config/server_config.toml"
fibre_start '--otel-endpoint http://127.0.0.1:9915'
run sudo bash tools/doctor.sh "${fibre_only[@]}"
grep -Eq '^FAIL +17 +fibre signer +signer_grpc_address in .* is 127\.0\.0\.1:26669, but only 1 of ' <<<"$out"
says 17 '(no signer_grpc_ca_file, signer_grpc_key_file)'
# Plaintext to another host: Fibre refuses it while signer_grpc_allow_insecure is off, the flag over
# the file, and the fix leads to mTLS, never to that setting.
printf '%s\n' 'signer_grpc_address = "10.255.0.7:26669"' >"$fh/config/server_config.toml"
run sudo bash tools/doctor.sh "${fibre_only[@]}"
grep -Eq '^FAIL +17 +fibre signer +signer_grpc_address in .* is 10\.255\.0\.7:26669, a plaintext signer beyond loopback' <<<"$out"
says 17 'Fibre exits when it starts, as that "is not localhost"'
says 17 'Fix: serve the node'
fibre_start '--signer-grpc-allow-insecure'
run sudo bash tools/doctor.sh "${fibre_only[@]}"
grep -Eq '^WARN +17 +fibre signer +signer_grpc_address in .* is 10\.255\.0\.7:26669, a plaintext signer without mTLS' <<<"$out"
printf '%s\n' 'signer_grpc_address = "10.255.0.7:26669"' 'signer_grpc_allow_insecure = true' >"$fh/config/server_config.toml"
fibre_start '--signer-grpc-allow-insecure=false'
run sudo bash tools/doctor.sh "${fibre_only[@]}"
grep -Eq '^FAIL +17 +fibre signer +signer_grpc_address in .* is 10\.255\.0\.7:26669, a plaintext signer beyond loopback' <<<"$out"
fibre_start '--otel-endpoint http://127.0.0.1:9915'
run sudo bash tools/doctor.sh "${fibre_only[@]}"
grep -Eq '^WARN +17 +fibre signer +signer_grpc_address in .* is 10\.255\.0\.7:26669, a plaintext signer without mTLS' <<<"$out"

# A config.toml without the key, from a home made on v8 or earlier: the node serves celestia-app's
# default, which check 17 reads as check 20 does, while something listens there.
home="$RUNNER_TEMP/val-old"
mkdir -p "$home/config"
echo 'priv_validator_laddr = ""' >"$home/config/config.toml"
printf '%s\n' 'signer_grpc_address = "127.0.0.1:26669"' >"$fh/config/server_config.toml"
beside=(--config /dev/null --roles "validator,fibre" --signer remote --fibre-enabled true --fibre-unit ci-active
  --node-home "$home" --data-mounts / --head-wait 2)
run sudo bash tools/doctor.sh "${beside[@]}"
grep -Eq '^FAIL +17 +fibre signer +.*, but this node serves no privval gRPC \(priv_validator_grpc_laddr is empty in ' <<<"$out"
serve 127.0.0.1 26669 "$RUNNER_TEMP/rpc" http://127.0.0.1:26669/status
run sudo bash tools/doctor.sh "${beside[@]}"
grep -Eq '^WARN +17 +fibre signer +signer_grpc_address in .* is 127\.0\.0\.1:26669, a plaintext signer without mTLS' <<<"$out"
if grep -Eq '^FAIL +17 ' <<<"$out"; then echo "check 17 failed on a node that serves its default"; exit 1; fi
sudo systemctl stop ci-active
