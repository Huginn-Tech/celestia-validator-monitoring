#!/usr/bin/env bash
# The chain case of the doctor job in .github/workflows/ci.yml, run from the repo root: check 27 (the
# node Fibre reads), the node check 17 names, and the Fibre hosts check 25 names for the app gRPC.
set -euo pipefail
. tools/ci/lib.sh
if grep -qs '^ENABLED=yes' /etc/ufw/ufw.conf || systemctl is-active --quiet firewalld || systemctl is-active --quiet nftables; then
  echo "this job expects a runner with no firewall active at the start"
  exit 1
fi
fh="$RUNNER_TEMP/fibre"
cfg="$fh/config/server_config.toml"
mkdir -p "$fh/config"
# A running stand-in for an active Fibre, whose home (FIBRE_HOME) holds its server_config.toml.
printf '%s\n' '[Service]' "Environment=FIBRE_HOME=$fh" 'ExecStart=/bin/sleep infinity' \
  | sudo tee /etc/systemd/system/ci-fibre.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl start ci-fibre
tls='signer_grpc_ca_file = "ca.crt"
signer_grpc_cert_file = "c.crt"
signer_grpc_key_file = "c.key"'
args=(--config /dev/null --fibre-enabled true --fibre-unit ci-fibre --signer remote --data-mounts / --head-wait 2)
inv=(--inventory-host fibre-1 --node-addresses val-1=10.255.4.1,node-1=10.255.4.5 --fibre-addresses fibre-1=10.255.4.3,val-1=10.255.4.1)

echo "::group::a Fibre host without a node, at Fibre's default"
printf '%s\n' 'signer_grpc_address = "10.255.4.1:26669"' "$tls" >"$cfg"
run sudo bash tools/doctor.sh "${args[@]}" "${inv[@]}" --roles fibre
echo "::endgroup::"
grep -Eq '^FAIL +27 +fibre app node +Fibre.s default \(no app_grpc_address in ' <<<"$out"
says 27 'is 127.0.0.1:9090, but this host runs no celestia-appd node and nothing listens on port 9090 here: now, Fibre cannot read the chain'
says 27 '(the nodes of the inventory: val-1 at 10.255.4.1, node-1 at 10.255.4.5)'
says 27 'let only this host reach it: "sudo ufw allow from 10.255.4.3 to any port 9090 proto tcp"'
grep -Eq '^OK +17 +fibre signer +signer_grpc_address in .* is 10\.255\.4\.1:26669, an IP address, mTLS: Fibre signs through val-1$' <<<"$out"

echo "::group::the same as a standby"
sudo systemctl stop ci-fibre
run sudo bash tools/doctor.sh "${args[@]}" "${inv[@]}" --roles fibre --fibre-hosts fibre-1,val-1
echo "::endgroup::"
grep -Eq '^WARN +27 +fibre app node +standby: Fibre.s default ' <<<"$out"
says 27 'once it starts, Fibre cannot read the chain'
says 27 'and leave the unit stopped: it uses the new value when it next starts'
sudo systemctl start ci-fibre

echo "::group::a node of the inventory on another host, and an address that is none"
printf '%s\n' 'app_grpc_address = "10.255.4.5:9090"' 'signer_grpc_address = "10.255.4.8:26669"' "$tls" >"$cfg"
run sudo bash tools/doctor.sh "${args[@]}" "${inv[@]}" --roles fibre
echo "::endgroup::"
grep -Eq '^OK +27 +fibre app node +app_grpc_address in .* is 10\.255\.4\.5:9090: Fibre reads the node on node-1, another host' <<<"$out"
grep -Eq '^WARN +17 +fibre signer +signer_grpc_address in .* is 10\.255\.4\.8:26669 with mTLS, but no celestia-appd node' <<<"$out"
says 17 'no celestia-appd node of the inventory has that address (val-1 at 10.255.4.1, node-1 at 10.255.4.5)'
printf '%s\n' 'app_grpc_address = "10.255.4.9:9090"' 'signer_grpc_address = "10.255.4.1:26669"' "$tls" >"$cfg"
run sudo bash tools/doctor.sh "${args[@]}" "${inv[@]}" --roles fibre
grep -Eq '^WARN +27 +fibre app node +app_grpc_address in .* is 10\.255\.4\.9:9090, but no celestia-appd node of the inventory' <<<"$out"
says 27 'nothing watches the node this Fibre reads'
# Without the inventory's nodes doctor names none and judges none.
run sudo bash tools/doctor.sh "${args[@]}" --roles fibre
grep -Eq '^OK +27 +fibre app node +app_grpc_address in .* is 10\.255\.4\.9:9090, a node on another host' <<<"$out"
grep -Eq '^OK +17 +fibre signer +signer_grpc_address in .* is 10\.255\.4\.1:26669, an IP address, mTLS$' <<<"$out"

echo "::group::the flag over the config file"
printf '%s\n' '[Service]' "Environment=FIBRE_HOME=$fh" \
  "ExecStart=/bin/sh -c 'exec sleep infinity' fibre --app-grpc-address 10.255.4.1:9090" \
  | sudo tee /etc/systemd/system/ci-fibre.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart ci-fibre
run sudo bash tools/doctor.sh "${args[@]}" "${inv[@]}" --roles fibre
echo "::endgroup::"
says 27 '--app-grpc-address in ExecStart is 10.255.4.1:9090: Fibre reads the node on val-1, another host'
printf '%s\n' '[Service]' "Environment=FIBRE_HOME=$fh" 'ExecStart=/bin/sleep infinity' \
  | sudo tee /etc/systemd/system/ci-fibre.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart ci-fibre

echo "::group::a Fibre next to its node"
nh="$RUNNER_TEMP/node"
mkdir -p "$nh/config"
echo 'priv_validator_laddr = ""' >"$nh/config/config.toml"
printf '%s\n' 'signer_grpc_address = "10.255.4.1:26669"' "$tls" >"$cfg"
node=(--roles validator,fibre --node-home "$nh" --inventory-host val-1 --node-addresses val-1=10.255.4.1,node-1=10.255.4.5)
printf '%s\n' '[grpc]' 'enable = true' >"$nh/config/app.toml"
run sudo bash tools/doctor.sh "${args[@]}" "${node[@]}"
grep -Eq '^OK +27 +fibre app node +Fibre.s default .* is 127\.0\.0\.1:9090: Fibre reads the node on this host \(val-1\), ' <<<"$out"
says 27 ': Fibre reads the node on this host (val-1), whose [grpc] address is localhost:9090'
printf '%s\n' '[grpc]' 'enable = true' 'address = "10.255.4.1:9090"' >"$nh/config/app.toml"
run sudo bash tools/doctor.sh "${args[@]}" "${node[@]}"
grep -Eq '^FAIL +27 +fibre app node +Fibre.s default .* is 127\.0\.0\.1:9090, the node on this host \(val-1\), but ' <<<"$out"
says 27 'the node on this host (val-1), but that node serves its app gRPC on 10.255.4.1:9090 ([grpc] address in'
says 27 'set --app-grpc-address in ExecStart (or app_grpc_address in'
says 27 'to "10.255.4.1:9090", run "systemctl daemon-reload" if you changed the unit, then restart Fibre at a moment you choose'
printf '%s\n' '[grpc]' 'enable = false' >"$nh/config/app.toml"
run sudo bash tools/doctor.sh "${args[@]}" "${node[@]}"
echo "::endgroup::"
grep -Eq '^FAIL +27 +fibre app node +.*, but it serves no app gRPC \(\[grpc\] enable = false in ' <<<"$out"

echo "::group::check 25 names the Fibre hosts without a node of their own"
printf '%s\n' '[grpc]' 'enable = true' 'address = "0.0.0.0:9090"' >"$nh/config/app.toml"
run sudo bash tools/doctor.sh "${args[@]}" --fibre-enabled false --roles validator --node-home "$nh" --inventory-host val-1 \
  --node-addresses val-1=10.255.4.1,node-1=10.255.4.5 --fibre-addresses fibre-1=10.255.4.3,val-1=10.255.4.1
grep -Eq '^FAIL +25 +node ports +\[grpc\] address = 0\.0\.0\.0:9090 \(nothing listens on it now\) is on every interface' <<<"$out"
says 25 'let only the Fibre hosts without a node of their own that read this node (fibre-1 at 10.255.4.3) reach port 9090: '
says 25 'reach port 9090: "sudo ufw allow from 10.255.4.3 to any port 9090 proto tcp"'
run sudo bash tools/doctor.sh "${args[@]}" --fibre-enabled false --roles validator --node-home "$nh" --inventory-host val-1 \
  --node-addresses val-1=10.255.4.1,node-1=10.255.4.5 --fibre-addresses node-1=10.255.4.5,val-1=10.255.4.1
echo "::endgroup::"
says 25 'set address = "127.0.0.1:9090" (every Fibre host of the inventory has a node of its own to read)'
if grep -E '^[A-Z]+ +25 ' <<<"$out" | grep -qF 'reach port 9090'; then
  echo "check 25 names a host that may reach 9090 where every Fibre host reads its own node"
  exit 1
fi
