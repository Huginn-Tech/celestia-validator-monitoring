#!/usr/bin/env bash
# The roles case of the doctor job in .github/workflows/ci.yml, run from the repo root.
set -e
. tools/ci/lib.sh
bash tools/doctor.sh --help >/dev/null
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
cat "$RUNNER_TEMP/doctor.conf"
run sudo bash tools/doctor.sh --config "$RUNNER_TEMP/doctor.conf" --head-wait 2
echo "::endgroup::"
