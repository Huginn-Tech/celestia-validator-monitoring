#!/usr/bin/env bash
# The survey case of the doctor job in .github/workflows/ci.yml, run from the repo root: doctor
# --survey on stand-ins for each kind of host, one after the other on this runner.
set -euo pipefail
. tools/ci/lib.sh
# Stand-ins for the binaries, which the survey finds by name in each unit's ExecStart.
bin=/opt/ci-survey/bin
sudo mkdir -p "$bin"
for b in celestia-appd fibre tmkms cosmovisor; do
  printf '%s\n' '#!/bin/sh' 'exec sleep infinity' | sudo tee "$bin/$b" >/dev/null
  sudo chmod 0755 "$bin/$b"
done
unit() { # name, unit lines: writes and starts the unit
  local name=$1
  shift
  printf '%s\n' '[Service]' "$@" | sudo tee "/etc/systemd/system/$name.service" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl start "$name"
}
drop() { # names: stops and removes the units
  local name
  for name in "$@"; do
    sudo systemctl stop "$name"
    sudo rm "/etc/systemd/system/$name.service"
  done
  sudo systemctl daemon-reload
}
survey() { # the survey as root; it exits 0
  run sudo bash tools/doctor.sh --survey
  test "$rc" = 0 || { echo "the survey exited $rc"; exit 1; }
}
has() { # text the survey's output must hold
  grep -qF -- "$1" <<<"$out" || { echo "the survey does not say: $1"; exit 1; }
}
sudo ip link add ci0 type dummy
sudo ip addr add 10.255.2.2/24 dev ci0
wg=false
if sudo ip link add dev wg-ci type wireguard 2>/dev/null; then
  sudo ip addr add 10.255.3.2/32 dev wg-ci
  wg=true
else
  echo "::warning::this runner has no WireGuard module, so the WireGuard-first order of the addresses is not checked"
fi

echo "::group::a Fibre host that reads a node on another host"
fh="$RUNNER_TEMP/fibre"
mkdir -p "$fh"
sudo mount -t tmpfs -o size=1m ci-fibre "$fh"
sudo mkdir -p "$fh/config"
printf '%s\n' 'server_listen_address = "0.0.0.0:7981"' 'app_grpc_address = "10.255.2.5:9090"' \
  'signer_grpc_address = "10.255.2.1:26669"' 'signer_grpc_ca_file = "ca.crt"' 'signer_grpc_cert_file = "c.crt"' \
  'signer_grpc_key_file = "c.key"' | sudo tee "$fh/config/server_config.toml" >/dev/null
unit ci-fibre "ExecStart=$bin/fibre start --home $fh"
survey
echo "::endgroup::"
grep -Eq '^Groups: fibre \(and monitor, when the monitor runs on this box too\)$' <<<"$out"
line=$(grep -E '^  addresses ' <<<"$out")
grep -qF '10.255.2.2 (ci0, private)' <<<"$line"
if grep -qE 'docker|veth|127\.0\.0\.1' <<<"$line"; then echo "the survey offers loopback or a container bridge"; exit 1; fi
if [ "$wg" = true ]; then
  grep -Eq '^      celestia_mon_private_ip: 10\.255\.3\.2  # wg-ci, WireGuard; also on this box: .*10\.255\.2\.2 \(ci0\)' <<<"$out"
fi
ip=$(sed -n 's/^      celestia_mon_private_ip: \([^ ]*\)  # .*/\1/p' <<<"$out")
test -n "$ip"
has "      celestia_mon_fibre_mount: $fh"
has '      celestia_mon_fibre_public_endpoint: "<public host or IP>:7981"'
has '      celestia_mon_fibre_port: 7981'
has '      celestia_mon_units: {appd: celestia-appd, fibre: ci-fibre, tmkms: tmkms}  # the whole map'
has '      # Fibre reads the chain at 10.255.2.5:9090 (another box) and signs through 10.255.2.1:26669 (mTLS)'
has 'Fibre sends no metrics yet: add "--otel-endpoint http://127.0.0.1:9915" to ExecStart in ci-fibre.service'
has 'Fibre reads the chain at 10.255.2.5:9090, a node on another box. On that box, [grpc] address in app.toml must be "10.255.2.5:9090"'
has "only this box may reach that port: \"sudo ufw allow from $ip to any port 9090 proto tcp\""
if grep -qF 'without mTLS' <<<"$out"; then echo "the survey finds an mTLS signer without mTLS"; exit 1; fi
if grep -qE '^ *celestia_mon_node_metrics_port' <<<"$out"; then echo "the survey sets a node port on a host without a node"; exit 1; fi
test ! -e /etc/celestia-mon
drop ci-fibre

echo "::group::one box: a node and the Fibre that reads it on loopback"
nh="$RUNNER_TEMP/node"
mkdir -p "$nh/config" "$nh/data" "$RUNNER_TEMP/rpc"
sudo mount -t tmpfs -o size=1m ci-data "$nh/data"
printf '%s\n' 'priv_validator_laddr = ""' '[instrumentation]' 'prometheus = true' 'prometheus_listen_addr = ":26661"' \
  >"$nh/config/config.toml"
printf '%s\n' '[grpc]' 'enable = true' >"$nh/config/app.toml"
cat >"$RUNNER_TEMP/rpc/status" <<'EOF'
{"jsonrpc": "2.0", "id": -1, "result": {"sync_info": {"latest_block_height": "100", "catching_up": false},
  "validator_info": {"address": "0123456789ABCDEF0123456789ABCDEF01234567", "voting_power": "10"}}}
EOF
serve 127.0.0.1 26657 "$RUNNER_TEMP/rpc" http://127.0.0.1:26657/status
fh1="$RUNNER_TEMP/fibre-one"
mkdir -p "$fh1/config"
echo 'signer_grpc_address = "127.0.0.1:26669"' >"$fh1/config/server_config.toml"
unit ci-node "ExecStart=$bin/celestia-appd start --home $nh"
unit ci-fibre "ExecStart=$bin/fibre start --home $fh1 --otel-endpoint http://127.0.0.1:9915"
survey
echo "::endgroup::"
has "  celestia-appd  ci-node.service (active), home $nh: validator, as it signs with its local key file, which has voting power 10"
grep -Eq '^Groups: validator, fibre \(and monitor, when the monitor runs on this box too\)$' <<<"$out"
has '      celestia_mon_private_ip: 127.0.0.1  # the node and Fibre here read each other on loopback, as on one box'
has "      celestia_mon_data_mounts: [$nh/data, /]"
has '      celestia_mon_node_metrics_port: 26661'
has '      celestia_mon_units: {appd: ci-node, fibre: ci-fibre, tmkms: tmkms}'
has "The node's metrics listen on every interface (prometheus_listen_addr = \":26661\"): set it to \"127.0.0.1:26661\""
has 'Fibre signs through 127.0.0.1:26669 without mTLS'
for no in 'Fibre sends no metrics' 'Fibre reads the node on this box at' 'celestia_mon_fibre_port' 'Only if a Fibre on another box'; do
  if grep -qF -- "$no" <<<"$out"; then echo "the survey says, where it should not: $no"; exit 1; fi
done
# The node's app gRPC on its private address only: the Fibre that reads loopback must follow it.
printf '%s\n' '[grpc]' 'enable = true' 'address = "10.255.2.2:9090"' >"$nh/config/app.toml"
survey
has 'Fibre reads the node on this box at 127.0.0.1:9090, but that node serves its app gRPC on 10.255.2.2:9090 ([grpc] address in'
has 'to "10.255.2.2:9090", run "systemctl daemon-reload" if you changed the unit, then restart Fibre at a moment you choose'
drop ci-node ci-fibre

echo "::group::a backup node under cosmovisor"
nh2="$RUNNER_TEMP/backup"
mkdir -p "$nh2/config"
echo 'priv_validator_laddr = ""' >"$nh2/config/config.toml"
printf '%s\n' '[grpc]' 'address = "localhost:9090"' >"$nh2/config/app.toml"
sed -i 's/"voting_power": "10"/"voting_power": "0"/' "$RUNNER_TEMP/rpc/status"
unit ci-cosmo 'Environment=DAEMON_NAME=celestia-appd' "Environment=DAEMON_HOME=$nh2" "ExecStart=$bin/cosmovisor run start"
survey
echo "::endgroup::"
has "  celestia-appd  ci-cosmo.service (active), home $nh2: backup_node, as priv_validator_laddr is empty and its local key has no voting power"
grep -Eq '^Groups: backup_node \(and monitor' <<<"$out"
has '      celestia_mon_units: {appd: ci-cosmo, fibre: fibre, tmkms: tmkms}'
has 'The node serves no metrics (prometheus = unset): under [instrumentation] in'
has 'Only if a Fibre on another box reads this node'
has "let only that box reach it: \"sudo ufw allow from <that box's private address> to any port 9090 proto tcp\""
if grep -qF 'celestia_mon_fibre_mount' <<<"$out"; then echo "the survey sets a Fibre mount on a host without Fibre"; exit 1; fi
drop ci-cosmo

echo "::group::the KMS host"
unit ci-kms "ExecStart=$bin/tmkms start -c /etc/tmkms/tmkms.toml"
survey
echo "::endgroup::"
has 'tmkms runs here: nothing is installed here; put this box in the kms group.'
if grep -qF 'celestia_mon_private_ip' <<<"$out"; then echo "the survey writes an entry for the KMS host"; exit 1; fi
drop ci-kms

echo "::group::a host that runs none of them, not as root"
run bash tools/doctor.sh --survey
echo "::endgroup::"
test "$rc" = 0
has 'Not run as root'
has 'Groups: monitor, if this box is the monitor (it runs no celestia-appd, Fibre or tmkms)'
has '      celestia_mon_data_mounts: [/]'
has 'Your steps on the Celestia side: none found.'
test ! -e /etc/celestia-mon
