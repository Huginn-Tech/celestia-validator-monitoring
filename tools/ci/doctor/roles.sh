#!/usr/bin/env bash
# The roles case of the doctor job in .github/workflows/ci.yml, run from the repo root.
set -e
. tools/ci/lib.sh
bash tools/doctor.sh --help >/dev/null
# doctor sends the API password to Prometheus's and Alertmanager's URLs and paths under them only,
# not to another port that starts with the same digits, such as a node RPC on 127.0.0.1:9130.
eval "$(sed -n '/^api_url() {/,/^}/p' tools/doctor.sh)"
# shellcheck disable=SC2034 # C is read by the api_url just defined
declare -A C=([prometheus_url]=http://127.0.0.1:912 [alertmanager_url]=http://127.0.0.1:913/)
for u in http://127.0.0.1:912 http://127.0.0.1:912/-/ready http://127.0.0.1:913/api/v2/silences; do
  api_url "$u" || { echo "api_url refuses $u"; exit 1; }
done
for u in http://127.0.0.1:9130/status http://127.0.0.1:9120 http://127.0.0.1:91 http://127.0.0.1:912.evil/; do
  ! api_url "$u" || { echo "api_url accepts $u"; exit 1; }
done
for roles in validator backup_node fibre kms monitor validator,fibre backup_node,fibre,monitor; do
  echo "::group::roles=$roles (root)"
  run sudo bash tools/doctor.sh --config /dev/null --roles "$roles" --fibre-enabled true \
    --signer remote --data-mounts / --fibre-mount / --head-wait 2
  echo "::endgroup::"
done
echo "::group::no config, not root"
run bash tools/doctor.sh --data-mounts /,/does-not-exist
echo "::endgroup::"
echo "::group::every key in doctor.conf.j2"
sed -n 's/^\([a-z_]\{1,\}\)=.*/\1=/p' ansible/roles/celestia_mon/templates/doctor.conf.j2 >"$RUNNER_TEMP/doctor.conf"
echo 'roles=validator,fibre' >>"$RUNNER_TEMP/doctor.conf"
# and the keys of an earlier install's doctor.conf that doctor no longer reads
printf '%s=\n' signer_multi_node prometheus_data_dir prometheus_retention_size >>"$RUNNER_TEMP/doctor.conf"
cat "$RUNNER_TEMP/doctor.conf"
run sudo bash tools/doctor.sh --config "$RUNNER_TEMP/doctor.conf" --head-wait 2
echo "::endgroup::"
