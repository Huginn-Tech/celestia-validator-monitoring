#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329
# The helpers of the CI scripts that run doctor (tools/ci/doctor/*.sh) and of the rules job, sourced
# from the repo root:
#   . tools/ci/lib.sh
# It sets no shell option: each script keeps those of the step it came from.

# run COMMAND...: runs doctor, prints its output and keeps it in $out. Fails the script when doctor
# exits above 1 (1 is a FAIL line) or writes to stderr.
run() {
  rc=0
  out=$("$@" 2>stderr.txt) || rc=$?
  printf '%s\n' "$out"
  cat stderr.txt
  if [ "$rc" -gt 1 ] || [ -s stderr.txt ]; then
    echo "doctor exited $rc or wrote to stderr: $*"
    exit 1
  fi
}

says() { # check number, the text its line must contain
  line=$(grep -E "^[A-Z]+ +$1 " <<<"$out" || true)
  grep -qF -- "$2" <<<"$line" || { echo "check $1 does not say: $2"; exit 1; }
}

# serve BIND PORT DIR URL: python3's http.server on BIND:PORT for the files in DIR, in the background;
# returns once URL answers.
serve() {
  python3 -m http.server "$2" --bind "$1" --directory "$3" >/dev/null 2>&1 &
  curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null "$4"
}

# fetch_release NAME VERSION SHA256 FILE...: the FILEs of the Prometheus project's release NAME at
# VERSION (linux-amd64, checked against SHA256), into the current directory.
fetch_release() {
  local rel="$1-$2.linux-amd64"
  curl -fsSLO --retry 3 "https://github.com/prometheus/$1/releases/download/v$2/$rel.tar.gz"
  echo "$3  $rel.tar.gz" | sha256sum -c -
  shift 3
  tar -xzf "$rel.tar.gz" --strip-components=1 "${@/#/$rel/}"
}

# start_prometheus [FLAG...]: the downloaded ./prometheus on 127.0.0.1:9912 with prometheus.yml, data/
# and FLAGs, and a stand-in Alertmanager on 127.0.0.1:9913 serving am/ (am/-/ready made here, other
# answers written by the script); returns once both answer /-/ready.
start_prometheus() {
  ./prometheus --config.file=prometheus.yml --storage.tsdb.path=data "$@" \
    --web.listen-address=127.0.0.1:9912 >prometheus.log 2>&1 &
  touch am/-/ready
  python3 -m http.server 9913 --bind 127.0.0.1 --directory am >/dev/null 2>&1 &
  curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:9912/-/ready
  curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:9913/-/ready
}
