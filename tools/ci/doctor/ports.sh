#!/usr/bin/env bash
# The ports case of the doctor job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
. tools/ci/lib.sh
if grep -qs '^ENABLED=yes' /etc/ufw/ufw.conf || systemctl is-active --quiet firewalld || systemctl is-active --quiet nftables; then
  echo "this job expects a runner with no firewall active at the start"
  exit 1
fi
sudo ip addr add 10.255.1.1/32 dev lo
home="$RUNNER_TEMP/node"
mkdir -p "$home/config" "$RUNNER_TEMP/www"
cat >"$home/config/config.toml" <<'EOF'
priv_validator_laddr = "tcp://10.255.1.1:26658"
priv_validator_grpc_laddr = "127.0.0.1:26669"

[rpc]
laddr = "tcp://127.0.0.1:26657"
pprof_laddr = "100.100.1.1:6060"

[instrumentation]
prometheus = true
prometheus_listen_addr = ":26660"
namespace = "cometbft"
EOF
cat >"$home/config/app.toml" <<'EOF'
[api]
enable = true
address = "tcp://10.255.1.1:1317"

[grpc]
enable = true
address = "0.0.0.0:9090"
EOF
echo 'cometbft_consensus_height 1' >"$RUNNER_TEMP/www/metrics"
python3 -m http.server 26660 --bind 0.0.0.0 --directory "$RUNNER_TEMP/www" >/dev/null 2>&1 &
python3 -m http.server 9090 --bind 0.0.0.0 --directory "$RUNNER_TEMP/www" >/dev/null 2>&1 &
python3 -m http.server 26658 --bind 10.255.1.1 --directory "$RUNNER_TEMP/www" >/dev/null 2>&1 &
for u in http://127.0.0.1:26660 http://127.0.0.1:9090 http://10.255.1.1:26658; do
  curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null "$u/metrics"
done
printf '%s\n' '[Service]' 'ExecStart=/bin/true' | sudo tee /etc/systemd/system/ci-root.service >/dev/null
printf '%s\n' '[Service]' 'User=nobody' 'ExecStart=/bin/true' | sudo tee /etc/systemd/system/ci-user.service >/dev/null
sudo systemctl daemon-reload
args=(--config /dev/null --roles validator --signer remote --fibre-enabled false --node-home "$home" --data-mounts / --head-wait 2)

echo "::group::no firewall active"
run sudo bash tools/doctor.sh "${args[@]}" --appd-unit ci-root
echo "::endgroup::"
grep -Eq '^FAIL +1 +node metrics +enabled at http://127\.0\.0\.1:26660/metrics, but prometheus_listen_addr = ":26660"' <<<"$out"
grep -Eq '^WARN +20 +signer binds ' <<<"$out"
says 20 'priv_validator_laddr = tcp://10.255.1.1:26658 (10.255.1.1:26658): no firewall is active, so every host'
says 20 'priv_validator_grpc_laddr = 127.0.0.1:26669 (nothing listens on it now), without mTLS'
says 20 'serve priv_validator_grpc_laddr with mTLS: set priv_validator_grpc_cert_file'
says 20 'then restart celestia-appd at a moment you choose, with tmkms running'
says 20 'let only the KMS host (tmkms) reach port 26658: "sudo ufw allow from <KMS host address> to any port 26658 proto tcp"'
says 20 'ufw is not active:'
grep -Eq '^FAIL +25 +node ports ' <<<"$out"
says 25 '[grpc] address = 0.0.0.0:9090 (0.0.0.0:9090) is on every interface, and no firewall is active'
says 25 '[api] address = tcp://10.255.1.1:1317 (nothing listens on it now): no firewall is active'
says 25 '[rpc] pprof_laddr = 100.100.1.1:6060 (nothing listens on it now) is on a Tailscale address'
says 25 'ok: [rpc] laddr = tcp://127.0.0.1:26657 (nothing listens on it now)'
says 25 'set address = "127.0.0.1:9090" when only the Fibre on this host reads it'
says 25 'let only the Fibre hosts reach port 9090: "sudo ufw allow from <Fibre host address> to any port 9090 proto tcp"'
grep -Eq '^WARN +26 +service users +ci-root\.service runs as root \(no User= in the unit\)' <<<"$out"
run sudo bash tools/doctor.sh "${args[@]}" --appd-unit ci-user
grep -Eq '^OK +26 +service users +ci-user\.service runs as nobody' <<<"$out"

echo "::group::ufw enabled"
sudo ufw allow 22/tcp
sudo ufw allow 9090/tcp
sudo ufw allow from 10.255.1.9 to any port 26658 proto tcp
sudo ufw allow from 10.255.1.9 to any port 26660 proto tcp
printf '%s\n' '[ci-web]' 'title=CI web' 'description=A stand-in app profile' 'ports=8080/tcp' \
  | sudo tee /etc/ufw/applications.d/ci-web >/dev/null
sudo ufw allow ci-web
sudo ufw allow in on lo
sudo ufw allow from 10.255.0.0/16 to any port 26660 proto tcp comment celestia-mon
sudo ufw --force enable
sudo ufw status verbose
run sudo bash tools/doctor.sh "${args[@]}" --appd-unit ci-root
echo "::endgroup::"
if grep -E '^[A-Z]+ +(1|20|25) ' <<<"$out" | grep -E 'ci-web|Anywhere( \(v6\))? on lo|10\.255\.0\.0/16' >/dev/null; then
  echo "doctor reads the app profile, the rule on lo or the private range as letting anyone in"
  exit 1
fi
grep -Eq '^WARN +1 +node metrics +enabled at http://127\.0\.0\.1:26660/metrics, but prometheus_listen_addr = ":26660"' <<<"$out"
grep -Eq '^WARN +20 +signer binds +priv_validator_grpc_laddr = 127\.0\.0\.1:26669 \(nothing listens on it now\), without mTLS' <<<"$out"
says 20 'ok: priv_validator_laddr = tcp://10.255.1.1:26658 (10.255.1.1:26658); ufw lets in 10.255.1.9'
grep -Eq '^WARN +25 +node ports ' <<<"$out"
says 25 '[grpc] address = 0.0.0.0:9090 (0.0.0.0:9090) is on every interface (firewall: ufw enabled); ufw lets anyone reach it'
says 25 'ufw lets anyone reach it (9090/tcp ALLOW IN Anywhere'
says 25 'ok: [api] address = tcp://10.255.1.1:1317 (nothing listens on it now); no ufw rule lets another host reach it'
says 25 'then delete the rule that lets anyone in ("sudo ufw status numbered", then "sudo ufw delete <number>")'
if grep -qF 'ufw is not active' <<<"$out"; then echo "doctor says ufw is not active while it is"; exit 1; fi

echo "::group::a private range on the REST API"
sudo ufw allow from 10.254.0.0/16 to any port 1317 proto tcp
run sudo bash tools/doctor.sh "${args[@]}" --appd-unit ci-root
echo "::endgroup::"
says 25 '[api] address = tcp://10.255.1.1:1317 (nothing listens on it now); ufw lets every host in 10.254.0.0/16 reach it'
says 25 'reach port 1317: "sudo ufw allow from <address of that host> to any port 1317 proto tcp", then delete the rule for the range'
sudo ufw delete allow from 10.254.0.0/16 to any port 1317 proto tcp

echo "::group::a Tailscale interface"
sudo ip tuntap add dev tailscale0 mode tun
run sudo bash tools/doctor.sh "${args[@]}" --appd-unit ci-root
echo "::endgroup::"
grep -Eq '^FAIL +1 +node metrics +.*\(firewall: ufw enabled; Tailscale interface tailscale0\)' <<<"$out"
grep -Eq '^FAIL +25 +node ports ' <<<"$out"
says 25 '[grpc] address = 0.0.0.0:9090 (0.0.0.0:9090) is on every interface, and this host has a Tailscale interface (tailscale0)'

echo "::group::metrics on a Tailscale address"
sudo ip addr add 100.100.1.1/32 dev lo
serve 100.100.1.1 26661 "$RUNNER_TEMP/www" http://100.100.1.1:26661/metrics
sed -i 's/^prometheus_listen_addr = .*/prometheus_listen_addr = "100.100.1.1:26661"/' "$home/config/config.toml"
run sudo bash tools/doctor.sh "${args[@]}" --appd-unit ci-root --node-metrics-url http://100.100.1.1:26661/metrics
echo "::endgroup::"
grep -Eq '^WARN +1 +node metrics +enabled at http://100\.100\.1\.1:26661/metrics, but the port listens on 100\.100\.1\.1:26661, a Tailscale' <<<"$out"

echo "::group::a monitor-only host"
run sudo bash tools/doctor.sh --config /dev/null --roles monitor --fibre-enabled true --data-mounts / --head-wait 2
echo "::endgroup::"
for want in '^SKIP +1 +node metrics +not a validator or backup node host' \
            '^SKIP +6 +fibre service +not a Fibre host' \
            '^SKIP +17 +fibre signer +not a Fibre host' \
            '^SKIP +20 +signer binds +not a validator or backup node host' \
            '^SKIP +25 +node ports +not a validator or backup node host' \
            '^SKIP +26 +service users +no celestia-appd node or Fibre unit on this host'; do
  grep -Eq -- "$want" <<<"$out" || { echo "the monitor-only host does not say: $want"; exit 1; }
done
# 15 and 16 are retired numbers: no line carries them, and the checks around them keep theirs.
if grep -Eq '^(OK|WARN|FAIL|SKIP|INFO|NOT YET) +1[56] ' <<<"$out"; then echo "the monitor-only host printed a line numbered 15 or 16"; exit 1; fi
