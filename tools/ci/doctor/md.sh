#!/usr/bin/env bash
# The md case of the doctor job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
. tools/ci/lib.sh
printf '%s\n' '[Service]' 'ExecStart=/bin/true --web.listen-address=127.0.0.1:19910' \
  | sudo tee /etc/systemd/system/celestia-mon-node-exporter.service >/dev/null
sudo systemctl daemon-reload
web="$RUNNER_TEMP/node-exporter"
mkdir -p "$web"
nohup python3 -m http.server 19910 --bind 127.0.0.1 --directory "$web" >"$RUNNER_TEMP/http.log" 2>&1 &
exported() { printf 'node_md_disks_required{device="%s"} 2\n' "$@" >"$web/metrics"; }

cat >"$RUNNER_TEMP/mdstat-1" <<'EOF'
Personalities : [raid1] [linear] [multipath] [raid0] [raid6] [raid5] [raid4] [raid10]
md2 : active raid1 nvme1n1p3[2] nvme0n1p3[0]
      1919309120 blocks super 1.2 [2/1] [U_]
      [=>...................]  recovery =  5.3% (101836416/1919309120) finish=150.2min speed=201652K/sec
      bitmap: 15/15 pages [60KB], 65536KB chunk

md1 : active raid1 nvme1n1p2[1](F) nvme0n1p2[0]
      1046528 blocks super 1.2 [2/1] [U_]

md0 : active raid1 nvme1n1p1[1] nvme0n1p1[0]
      33520640 blocks super 1.2 [2/2] [UU]
      [==>..................]  check = 12.3% (4123456/33520640) finish=3.1min speed=150000K/sec

md127 : active (auto-read-only) raid1 sdb1[1]
      976630464 blocks super 1.2 [2/1] [_U]

unused devices: <none>
EOF
exported md0 md1 md2 md127
curl -fsS --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:19910/metrics >/dev/null
run bash tools/doctor.sh --config /dev/null --data-mounts / --mdstat "$RUNNER_TEMP/mdstat-1" --head-wait 2
grep -Eq '^WARN +10 +md RAID +md2 raid1 \[2/1\] \[U_\], recovery=5\.3%: missing a disk and rebuilding' <<<"$out"
grep -Eq '^FAIL +10 +md RAID +md1 raid1 \[2/1\] \[U_\]: missing a disk\. Fix: "mdadm --detail /dev/md1"' <<<"$out"
grep -Eq '^OK +10 +md RAID +md0 raid1 \[2/2\] \[UU\], check=12\.3%$' <<<"$out"
grep -Eq '^FAIL +10 +md RAID +md127 raid1 \[2/1\] \[_U\] read-only: missing a disk\. Fix: if it is an old disk' <<<"$out"
grep -Eq '^FAIL +10 .*"mdadm --stop /dev/md127"' <<<"$out"
test "$(grep -Ec '^[A-Z ]+ +10 +md RAID ' <<<"$out")" = 4

cat >"$RUNNER_TEMP/mdstat-2" <<'EOF'
Personalities : [raid0] [raid1]
md0 : active raid1 sdb1[1] sda1[0]
      33520640 blocks super 1.2 [2/2] [UU]
      resync=DELAYED

md3 : active raid0 sdd1[1] sdc1[0]
      1953260544 blocks super 1.2 512k chunks

md126 : inactive sde1[0](S)
      976630488 blocks super 1.2

unused devices: <none>
EOF
exported md0 md126
run bash tools/doctor.sh --config /dev/null --data-mounts / --mdstat "$RUNNER_TEMP/mdstat-2" --head-wait 2
grep -Eq '^OK +10 +md RAID +md0 raid1 \[2/2\] \[UU\], resync=DELAYED$' <<<"$out"
grep -Eq '^FAIL +10 +md RAID +md3 raid0, but node_exporter at 127\.0\.0\.1:19910 exports no node_md metrics for md3' <<<"$out"
grep -Eq '^INFO +10 +md RAID +md126: inactive \(not started\)' <<<"$out"
exported md0 md3 md126
run bash tools/doctor.sh --config /dev/null --data-mounts / --mdstat "$RUNNER_TEMP/mdstat-2" --head-wait 2
grep -Eq '^OK +10 +md RAID +md3 raid0: no redundancy' <<<"$out"

run bash tools/doctor.sh --config /dev/null --data-mounts / --mdstat "$RUNNER_TEMP/no-mdstat" --head-wait 2
if grep -Eq ' md RAID ' <<<"$out"; then echo "an md RAID line without md arrays"; exit 1; fi
