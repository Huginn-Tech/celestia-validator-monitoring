#!/usr/bin/env bash
# celestia-mon-doctor: read-only checks for the hosts of a Celestia validator.
#
# It changes nothing on the host. Each check prints a line (check 12 one per group):
#   OK       the check passed
#   NOT YET  metrics that appear only after an event; the line names the event
#   FAIL     the check failed; the line ends with the fix to apply
#   WARN     worth fixing, but not counted as a failure
#   INFO     a measurement to read, never a failure
#   SKIP     the check does not apply on this host, or cannot run yet
# Exit code: 0 when no check failed, 1 when one did, 2 on a usage error.
#
# Settings are read from /etc/celestia-mon/doctor.conf (key=value lines, written by
# install.sh) and can be given or overridden as flags: "--node-rpc-url URL"
# sets node_rpc_url. Without a config file the defaults below apply and the
# host's roles are guessed from its systemd units. Run it as root
# (sudo celestia-mon-doctor) so it can read the node's config and see which process
# owns each listening socket.
#
# celestia-appd (checks 4 and 5) runs as the node's service user. A
# multiplexer build of it unpacks any missing embedded binaries into
# <node home>/bin, as the node itself does at every start.
#
# Prometheus and Alertmanager listen on 127.0.0.1 only, so checks 11 to 13, 15
# and 16 run on the monitor host and ask them there.
#
# signer and fibre_enabled tell doctor the install type: with signer=local it
# never expects the remote signer's metrics, with fibre_enabled=false never
# Fibre's or the collector's.
#
# Needs only bash, coreutils, util-linux (runuser), curl, ss, systemctl, grep,
# sed and awk.

set -u

declare -A C=(
  [roles]=""
  [signer]=""
  [fibre_enabled]=""
  [appd_unit]="celestia-appd"
  [fibre_unit]="fibre"
  [node_rpc_url]="http://127.0.0.1:26657"
  [node_metrics_url]="http://127.0.0.1:26660/metrics"
  [node_home]=""
  [appd_bin]=""
  [data_mounts]=""
  [fibre_mount]=""
  [fibre_public_endpoint]=""
  [fibre_port]=""
  [fibre_otel_endpoint]="http://127.0.0.1:9915"
  [fibre_hosts]=""
  [prometheus_url]="http://127.0.0.1:9912"
  [alertmanager_url]="http://127.0.0.1:9913"
  [prometheus_data_dir]="/var/lib/celestia-mon/prometheus"
  [prometheus_retention_size]=""
  [signer_median_max]="0.010"
  [disk_min_free_ratio]="0.10"
  [time_offset_max]="0.1"
  [head_wait]="30"
  [private_address_regex]='^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|127\.|::1$|f[cd][0-9a-fA-F]{2}:)'
)

usage() {
  cat <<'EOF'
Usage: celestia-mon-doctor [--config FILE] [--KEY VALUE ...]

Read-only checks for a Celestia validator, Fibre, KMS or monitor host.
Settings come from /etc/celestia-mon/doctor.conf (KEY=VALUE lines) and can be
overridden with flags (--node-rpc-url URL sets node_rpc_url).

  roles                  comma-separated: validator, fibre, kms, monitor
                         (default: guessed from the units on this host)
  signer                 local or remote; with local, no remote signer metrics
                         are expected (default: not set, not compared)
  fibre_enabled          true or false; with false, no Fibre or collector
                         metrics are expected (default: true if the Fibre
                         unit exists)
  appd_unit, fibre_unit  systemd unit names (default: celestia-appd, fibre)
  node_rpc_url           default http://127.0.0.1:26657
  node_metrics_url       default http://127.0.0.1:26660/metrics
  node_home              celestia-appd home (default: the unit's --home,
                         CELESTIA_APP_HOME, DAEMON_HOME, or ~/.celestia-app
                         of the unit's user)
  appd_bin               celestia-appd binary (default: from the unit or PATH)
  data_mounts            comma-separated mount points to check for free space
  fibre_mount            Fibre store mount point
  fibre_public_endpoint  host:port that should be registered in x/valaddr
  fibre_port             Fibre listen port (default: the unit's
                         --server-listen-address, else server_listen_address
                         in Fibre's config/server_config.toml, else 7980)
  fibre_otel_endpoint    expected OTLP base URL (default http://127.0.0.1:9915)
  fibre_hosts            comma-separated inventory names of the Fibre hosts,
                         on the monitor (default: the collector targets)
  prometheus_url         celestia-mon's Prometheus, on the monitor host
                         (default http://127.0.0.1:9912)
  alertmanager_url       celestia-mon's Alertmanager, on the monitor host
                         (default http://127.0.0.1:9913)
  prometheus_data_dir    default /var/lib/celestia-mon/prometheus
  prometheus_retention_size  Prometheus's retention size, such as 8GB
                         (default: not compared)
  signer_median_max      seconds (default 0.010)
  disk_min_free_ratio    default 0.10
  time_offset_max        seconds (default 0.1)
  head_wait              seconds to wait for a new block (default 30)
  private_address_regex  addresses counted as private
EOF
}

set_opt() {
  if [[ -z ${C[$1]+set} ]]; then
    echo "celestia-mon-doctor: unknown setting: $1 (see --help)" >&2
    exit 2
  fi
  C[$1]=$2
}

load_conf() {
  local line key val
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    line=${line#"${line%%[![:space:]]*}"}
    [[ -z $line || $line == '#'* ]] && continue
    if [[ $line != *=* ]]; then
      echo "celestia-mon-doctor: $1: not a KEY=VALUE line: $line" >&2
      exit 2
    fi
    key=${line%%=*}
    key=${key//[[:space:]]/}
    val=${line#*=}
    if [[ $val == \"*\" ]]; then val=${val:1:${#val}-2}; fi
    set_opt "$key" "$val"
  done <"$1"
}

need_value() {
  if (($# < 2)); then
    echo "celestia-mon-doctor: $1 needs a value" >&2
    exit 2
  fi
}

# ---------------------------------------------------------------- helpers

failed=0
report() { # status, check number, check name, detail
  printf '%-7s %2s  %-14s  %s\n' "$1" "$2" "$3" "$4"
  if [[ $1 == FAIL ]]; then failed=1; fi
}

has_role() { [[ ",${C[roles]}," == *",$1,"* ]]; }
fibre_on() { [[ ${C[fibre_enabled],,} == true ]]; }
join() {
  local out="" x
  for x in "$@"; do out+="${out:+, }$x"; done
  printf '%s' "$out"
}
last_line() {
  local l
  l=$(grep . <<<"$1" | tail -n 1)
  printf '%s' "${l:0:200}"
}
ms() { awk -v s="$1" 'BEGIN { printf "%.1f", s * 1000 }'; }
# The API answers end without a newline; add one so `while read` loops see the last line.
http_get() {
  local rc=0
  curl -fsS -g --max-time 5 "$1" 2>/dev/null || rc=$?
  echo
  return "$rc"
}
url_hostport() {
  local u=${1#*://}
  printf '%s' "${u%%/*}"
}
url_port() { # URL -> its port, empty when it names none
  local hp
  hp=$(url_hostport "$1")
  hp=${hp##*]}
  if [[ $hp == *:* ]]; then printf '%s' "${hp##*:}"; fi
}
gib() { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1073741824 }'; }

unit_prop() { systemctl show -p "$2" --value "$1" 2>/dev/null; }
unit_loaded() { [[ $(unit_prop "$1" LoadState) == loaded ]]; }
unit_argv() { unit_prop "$1" ExecStart | sed -n 's/^.*argv\[]=\(.*\) ; ignore_errors=.*$/\1/p' | head -n 1; }
unit_path() { unit_prop "$1" ExecStart | sed -n 's/^{ path=\([^ ;]*\) ;.*$/\1/p' | head -n 1; }
unit_home() { # unit -> home directory of its User= (root when unset) from /etc/passwd
  local user
  user=$(unit_prop "$1" User)
  awk -F: -v u="${user:-root}" '$1 == u { print $6; exit }' /etc/passwd
}

ENV_VAL=""    # the value unit_env found
ENV_SRC=""    # where unit_env found it
ENV_UNREAD="" # an EnvironmentFile= unit_env could not read
unit_env() { # unit, variable -> true when the unit sets it; systemd lets EnvironmentFile= override Environment=
  local files f v
  ENV_VAL="" ENV_SRC="" ENV_UNREAD=""
  files=$(unit_prop "$1" EnvironmentFiles | sed -n 's/^\(.*\) (ignore_errors=[a-z]*)$/\1/p')
  while read -r f; do
    [[ -n $f ]] || continue
    if [[ ! -r $f ]]; then
      ENV_UNREAD=$f
      continue
    fi
    v=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}'"$2"'=//p' "$f" | tail -n 1)
    if [[ -n $v ]]; then
      ENV_VAL=$v
      ENV_SRC="$2 in $f"
    fi
  done <<<"$files"
  if [[ -z $ENV_VAL ]]; then
    ENV_VAL=$(unit_prop "$1" Environment | tr ' ' '\n' | sed -n 's/^"\{0,1\}'"$2"'=//p' | tail -n 1)
    ENV_SRC="$2 in Environment="
  fi
  ENV_VAL=${ENV_VAL%\"}
  ENV_VAL=${ENV_VAL#\"}
  ENV_VAL=${ENV_VAL%\'}
  ENV_VAL=${ENV_VAL#\'}
  [[ -n $ENV_VAL ]]
}

# systemctl show prints ExecStart as written, before systemd fills in $VAR and
# ${VAR}. unit_expand fills them in from the unit's environment ($HOME from
# the unit's user) and fails when one is not set there.
EXPANDED=""
unit_expand() { # unit, text -> true and EXPANDED set when every variable resolves
  local rest=$2 name val re='^([^$]*)([$][{]([A-Za-z_][A-Za-z0-9_]*)[}]|[$]([A-Za-z_][A-Za-z0-9_]*))(.*)$'
  EXPANDED=""
  while [[ $rest =~ $re ]]; do
    EXPANDED+=${BASH_REMATCH[1]}
    name=${BASH_REMATCH[3]}${BASH_REMATCH[4]}
    rest=${BASH_REMATCH[5]}
    val=""
    if unit_env "$1" "$name"; then
      val=$ENV_VAL
    elif [[ $name == HOME ]]; then
      val=$(unit_home "$1")
    fi
    [[ -n $val ]] || return 1
    EXPANDED+=$val
  done
  EXPANDED+=$rest
}

flag_value() { # argv string, flag name -> value of "--name value" or "--name=value"
  local -a args
  local i
  read -r -a args <<<"$1"
  for ((i = 0; i < ${#args[@]}; i++)); do
    case ${args[i]} in
      "--$2="*)
        printf '%s' "${args[i]#*=}"
        return 0
        ;;
      "--$2")
        if ((i + 1 < ${#args[@]})); then
          printf '%s' "${args[i + 1]}"
          return 0
        fi
        ;;
    esac
  done
  return 1
}

listeners() { # port -> local addresses of the TCP sockets listening on it
  ss -Htln 2>/dev/null | awk -v p=":$1" 'length($4) > length(p) && substr($4, length($4) - length(p) + 1) == p { print $4 }'
}

addr_host() { # local address as ss prints it -> the address without port, brackets, zone or ::ffff:
  local h=${1%:*}
  h=${h%%\%*}
  h=${h#\[}
  h=${h%\]}
  printf '%s' "${h#::ffff:}"
}

is_loopback() { # local address as ss prints it -> true for 127.0.0.0/8 and ::1
  local h
  h=$(addr_host "$1")
  [[ $h == 127.* || $h == ::1 ]]
}

addr_class() { # local address as ss prints it -> wildcard, private or public
  local h
  h=$(addr_host "$1")
  case $h in
    0.0.0.0 | '*' | ::) echo wildcard ;;
    *)
      if [[ $h =~ ${C[private_address_regex]} ]]; then echo private; else echo public; fi
      ;;
  esac
}

toml_get() { # file, section ("" = top level), key -> value without quotes
  awk -v sec="$2" -v key="$3" '
    /^[[:space:]]*\[/ { s = $0; gsub(/^[[:space:]]*\[+|\]+[[:space:]]*(#.*)?$/, "", s); next }
    s == sec && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      v = $0
      sub(/^[^=]*=[[:space:]]*/, "", v)
      sub(/[[:space:]]*(#.*)?$/, "", v)
      gsub(/"/, "", v)
      print v
      exit
    }' "$1"
}

NODE_HOME=""
NODE_USER=""
APPD=""
find_node() { # sets NODE_HOME, NODE_USER and APPD from the settings or the celestia-appd unit
  local unit=${C[appd_unit]} home path
  NODE_USER=$(unit_prop "$unit" User)
  NODE_HOME=${C[node_home]}
  if [[ -z $NODE_HOME ]] && home=$(flag_value "$(unit_argv "$unit")" home) && unit_expand "$unit" "$home"; then
    NODE_HOME=$EXPANDED
  fi
  if [[ -z $NODE_HOME ]] && unit_env "$unit" CELESTIA_APP_HOME; then NODE_HOME=$ENV_VAL; fi
  if [[ -z $NODE_HOME ]] && unit_env "$unit" DAEMON_HOME; then NODE_HOME=$ENV_VAL; fi
  if [[ -z $NODE_HOME ]]; then
    home=$(unit_home "$unit")
    NODE_HOME=${home:-/root}/.celestia-app
  fi
  APPD=${C[appd_bin]}
  path=$(unit_path "$unit")
  if [[ -z $APPD && ${path##*/} == celestia-appd && -x $path ]]; then APPD=$path; fi
  if [[ -z $APPD && -x $NODE_HOME/cosmovisor/current/bin/celestia-appd ]]; then
    APPD=$NODE_HOME/cosmovisor/current/bin/celestia-appd
  fi
  if [[ -z $APPD ]]; then APPD=$(command -v celestia-appd || true); fi
}

# Runs celestia-appd as the node's service user when doctor runs as root: the
# binary usually belongs to that user, and anything it writes into the node
# home stays owned by it. It starts from / so doctor's working directory does
# not matter.
as_node() {
  if [[ $(id -u) -eq 0 && -n $NODE_USER && $NODE_USER != root ]]; then
    (cd / && runuser -u "$NODE_USER" -- "$@")
  else
    "$@"
  fi
}

# celestia-appd writes default config files into a home that lacks them, so
# doctor runs it only against a home whose config files all exist.
home_ok() {
  local f
  for f in config.toml app.toml client.toml; do
    [[ -f $NODE_HOME/config/$f ]] || return 1
  done
}

# ------------------------------------------------------- validator checks

METRICS=""
check_metrics() {
  local name="node metrics" url=${C[node_metrics_url]} cfg prom="" laddr="" ns="" note="" a lhost wild="" bind
  local -a bad=()
  if ! has_role validator; then report SKIP 1 "$name" "not a validator host"; return; fi
  cfg=$NODE_HOME/config/config.toml
  if [[ -r $cfg ]]; then
    prom=$(toml_get "$cfg" instrumentation prometheus)
    laddr=$(toml_get "$cfg" instrumentation prometheus_listen_addr)
    ns=$(toml_get "$cfg" instrumentation namespace)
    if [[ $prom != true ]]; then
      report FAIL 1 "$name" "prometheus = ${prom:-unset} in $cfg. Fix: set \"prometheus = true\" under [instrumentation] in $cfg, then restart celestia-appd at a moment you choose"
      return
    fi
  else
    cfg="config.toml"
    note=" ($NODE_HOME/config/config.toml not readable: run as root or pass --node-home)"
  fi
  METRICS=$(http_get "$url")
  if [[ -z $METRICS ]]; then
    report FAIL 1 "$name" "no answer from $url$note. Fix: set prometheus = true and prometheus_listen_addr (now ${laddr:-unknown}) under [instrumentation] in $cfg so the node serves $url, then restart celestia-appd at a moment you choose"
    return
  fi
  if ! grep -qE '^(cometbft|celestia)_consensus_height' <<<"$METRICS"; then
    METRICS=""
    report FAIL 1 "$name" "$url has no cometbft_ or celestia_ metrics (namespace = ${ns:-unknown}). Fix: set namespace = \"cometbft\" under [instrumentation] in $cfg, then restart celestia-appd at a moment you choose"
    return
  fi
  # CometBFT's default prometheus_listen_addr is ":26660": an empty host, like
  # 0.0.0.0, means every interface.
  lhost=${laddr#*://}
  lhost=${lhost%:*}
  lhost=${lhost#\[}
  lhost=${lhost%\]}
  if [[ -n $laddr ]] && [[ -z $lhost || $lhost == 0.0.0.0 || $lhost == :: || $lhost == '*' ]]; then
    wild="prometheus_listen_addr = \"$laddr\" in $cfg"
  fi
  while read -r a; do
    [[ -n $a ]] || continue
    case $(addr_class "$a") in
      wildcard) [[ -n $wild ]] || wild="the port listens on $a" ;;
      public) bad+=("$a") ;;
    esac
  done <<<"$(listeners "$(url_port "$url")")"
  bind=$(url_hostport "$url")
  if [[ $bind == 127.* || $bind == localhost* || $bind == '[::1]'* ]]; then bind="<private address>:$(url_port "$url")"; fi
  if [[ -n $wild ]]; then
    report WARN 1 "$name" "enabled at $url, but $wild: celestia-appd serves its metrics on every interface of this host, and whether they can be reached from outside depends on the firewall (firewall: $(firewall_state)). Fix: bind it to the private address the monitor scrapes, prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg, then restart celestia-appd at a moment you choose; celestia-mon-doctor never edits the config"
  elif ((${#bad[@]} > 0)); then
    report WARN 1 "$name" "enabled at $url, but the port listens on $(join "${bad[@]}"), which is not a private address. Fix: set prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg (or block the port in a firewall), then restart celestia-appd at a moment you choose"
  else
    report OK 1 "$name" "enabled at $url$note"
  fi
}

check_signer() {
  local name="remote signer" cfg laddr median signer=${C[signer]} max=${C[signer_median_max]}
  if ! has_role validator; then report SKIP 2 "$name" "not a validator host"; return; fi
  cfg=$NODE_HOME/config/config.toml
  if [[ ! -r $cfg ]]; then
    report SKIP 2 "$name" "$cfg not readable (run as root or pass --node-home)"
    return
  fi
  laddr=$(toml_get "$cfg" "" priv_validator_laddr)
  if [[ -z $laddr ]]; then
    if [[ $signer == remote ]]; then
      report FAIL 2 "$name" "priv_validator_laddr is empty in $cfg, but celestia-mon is set to signer: remote. Fix: set celestia_mon_signer: local in the inventory and re-run install.sh on each host (or finish the remote signer setup)"
    else
      report SKIP 2 "$name" "local key (priv_validator_laddr is empty)"
    fi
    return
  fi
  if [[ $signer == local ]]; then
    report FAIL 2 "$name" "priv_validator_laddr = $laddr, but celestia-mon is set to signer: local. Fix: set celestia_mon_signer: remote in the inventory and re-run install.sh on each host"
    return
  fi
  if [[ -z $METRICS ]]; then
    report SKIP 2 "$name" "configured ($laddr); median not checked: no node metrics (check 1)"
    return
  fi
  median=$(awk '$1 ~ /^(cometbft|celestia)_privval_signing_latency_median_seconds($|[{])/ { print $NF; exit }' <<<"$METRICS")
  if [[ -z $median ]]; then
    report SKIP 2 "$name" "configured ($laddr); median signing latency not yet available (it appears after 50 signatures since the node started)"
    return
  fi
  if awk -v v="$median" -v m="$max" 'BEGIN { exit !(v + 0 <= m + 0) }'; then
    report OK 2 "$name" "$laddr, median signing latency $(ms "$median") ms (limit $(ms "$max") ms)"
  else
    report FAIL 2 "$name" "$laddr, median signing latency $(ms "$median") ms is above $(ms "$max") ms. Fix: cut the latency between the validator and the KMS host (network path, KMS host load); Celestia asks for a median of 10 ms or less"
  fi
}

STATUS=""
status_value() { # key -> value of "key":"value" or "key":value in the flattened /status reply
  sed -n 's/.*"'"$1"'":"\{0,1\}\([^",}]*\).*/\1/p' <<<"$STATUS" | head -n 1
}

# Shows that the node advances, not that it is at the network's head: an
# explorer or status.celestia.org shows that.
check_head() {
  local name="node progress" rpc=${C[node_rpc_url]%/} limit=${C[head_wait]} h0 h1 catching end
  if ! has_role validator; then report SKIP 3 "$name" "not a validator host"; return; fi
  STATUS=$(http_get "$rpc/status" | tr -d ' \t\r\n')
  h0=$(status_value latest_block_height)
  if [[ ! $h0 =~ ^[0-9]+$ ]]; then
    STATUS=""
    report FAIL 3 "$name" "no answer from $rpc/status. Fix: check \"systemctl status ${C[appd_unit]}\" and \"journalctl -u ${C[appd_unit]} -n 100\"; the URL must match [rpc] laddr in config.toml"
    return
  fi
  catching=$(status_value catching_up)
  if [[ $catching != false ]]; then
    report FAIL 3 "$name" "catching_up=$catching at height $h0. Fix: wait until the node has synced; if the height does not move, check peers and \"journalctl -u ${C[appd_unit]} -n 100\""
    return
  fi
  [[ $limit =~ ^[0-9]+$ ]] || limit=30
  end=$((SECONDS + limit))
  h1=$h0
  while ((h1 <= h0 && SECONDS < end)); do
    sleep 2
    h1=$(http_get "$rpc/status" | tr -d ' \t\r\n' | sed -n 's/.*"latest_block_height":"\{0,1\}\([0-9]*\).*/\1/p')
    [[ $h1 =~ ^[0-9]+$ ]] || h1=0
  done
  if ((h1 > h0)); then
    report OK 3 "$name" "height $h0 -> $h1, catching_up=false (the node advances; an explorer shows the network's head)"
  else
    report FAIL 3 "$name" "height stayed at $h0 for ${limit}s, as the NodeNotAdvancing alert reports. Fix: check status.celestia.org and an explorer first; if the whole chain stopped, coordinate with other validators instead of fixing locally. Otherwise check peers and \"journalctl -u ${C[appd_unit]} -n 100\" (a node often stops at an upgrade height)"
  fi
}

VALCONS=""
check_address() {
  local name="validator addr" hex power out
  if ! has_role validator; then report SKIP 4 "$name" "not a validator host"; return; fi
  if [[ -z $STATUS ]]; then report SKIP 4 "$name" "node RPC not answering (check 3)"; return; fi
  hex=$(sed -n 's/.*"validator_info":{"address":"\([0-9A-Fa-f]*\)".*/\1/p' <<<"$STATUS")
  power=$(status_value voting_power)
  if [[ -z $hex ]]; then
    report FAIL 4 "$name" "/status has no validator_info.address. Fix: check that ${C[appd_unit]} runs as the validator (key file or priv_validator_laddr) and read \"journalctl -u ${C[appd_unit]} -n 100\""
    return
  fi
  if [[ -z $APPD ]]; then
    report SKIP 4 "$name" "hex $hex; celestia-appd not found to convert it (pass --appd-bin)"
    return
  fi
  if ! home_ok; then
    report SKIP 4 "$name" "hex $hex; $NODE_HOME/config lacks config.toml, app.toml or client.toml (pass --node-home)"
    return
  fi
  # No timeout: the command is offline, and cutting it off while a multiplexer
  # build unpacks its embedded binaries would leave a truncated binary behind.
  out=$(as_node "$APPD" debug addr "$hex" --home "$NODE_HOME" 2>&1)
  VALCONS=$(awk '$1 == "Bech32" && $2 == "Con:" { print $3; exit }' <<<"$out")
  if [[ -z $VALCONS ]]; then
    report FAIL 4 "$name" "\"celestia-appd debug addr $hex\" gave no Bech32 Con line: $(last_line "$out"). Fix: run it by hand with --home $NODE_HOME, or pass --appd-bin"
    return
  fi
  report OK 4 "$name" "$VALCONS (hex $hex, voting power ${power:-unknown})"
}

check_registration() {
  local name="fibre registry" want=${C[fibre_public_endpoint]} out flat found host
  if ! has_role validator; then report SKIP 5 "$name" "not a validator host"; return; fi
  if ! fibre_on; then report SKIP 5 "$name" "Fibre is disabled"; return; fi
  if [[ -z $VALCONS ]]; then report SKIP 5 "$name" "consensus address unknown (check 4)"; return; fi
  out=$(as_node timeout 30 "$APPD" query valaddr provider "$VALCONS" --node "${C[node_rpc_url]}" --home "$NODE_HOME" --output json 2>&1)
  flat=$(tr -d ' \t\r\n' <<<"$out")
  found=$(sed -n 's/.*"found":\([a-z]*\).*/\1/p' <<<"$flat")
  host=$(sed -n 's/.*"host":"\([^"]*\)".*/\1/p' <<<"$flat")
  if [[ -z $found ]]; then
    report FAIL 5 "$name" "query failed: $(last_line "$out"). Fix: run \"celestia-appd query valaddr provider $VALCONS\" by hand; x/valaddr exists only from app v10, so keep celestia_mon_fibre_enabled: false until v10 is active on this network"
    return
  fi
  if [[ $found != true || -z $host ]]; then
    report FAIL 5 "$name" "no Fibre host registered for $VALCONS. Fix: celestia-appd tx valaddr set-host ${want:-<public-host>:7980} --from <validator-account-key>"
    return
  fi
  if [[ -z $want ]]; then
    report OK 5 "$name" "registered $host (no Fibre public endpoint configured to compare)"
  elif [[ $host == "$want" ]]; then
    report OK 5 "$name" "registered $host, as configured"
  else
    report FAIL 5 "$name" "registered $host, but the configured endpoint is $want. Fix: celestia-appd tx valaddr set-host $want --from <validator-account-key> (or correct celestia_mon_fibre_public_endpoint if $host is right)"
  fi
}

# ----------------------------------------------------------- Fibre checks

fibre_skip() { # check number, check name -> true (after a SKIP line) when Fibre checks do not apply
  if ! has_role fibre; then
    report SKIP "$1" "$2" "not a Fibre host"
    return 0
  fi
  if ! fibre_on; then
    report SKIP "$1" "$2" "Fibre is disabled"
    return 0
  fi
  return 1
}

fibre_home() { # unit, argv -> Fibre's home: --home, else FIBRE_HOME, else ~/.celestia-fibre of the unit's user
  local h
  if h=$(flag_value "$2" home) && unit_expand "$1" "$h"; then
    printf '%s' "$EXPANDED"
  elif unit_env "$1" FIBRE_HOME; then
    printf '%s' "$ENV_VAL"
  else
    h=$(unit_home "$1")
    printf '%s' "${h:-/root}/.celestia-fibre"
  fi
}

check_fibre_service() {
  local name="fibre service" unit=${C[fibre_unit]} state port=${C[fibre_port]} argv addr="" cfg note="" l
  if fibre_skip 6 "$name"; then return; fi
  if ! unit_loaded "$unit"; then
    report FAIL 6 "$name" "$unit.service not found. Fix: set celestia_mon_units.fibre (or --fibre-unit) to the name of Fibre's systemd unit"
    return
  fi
  state=$(unit_prop "$unit" ActiveState)
  if [[ $state != active ]]; then
    report FAIL 6 "$name" "$unit.service is $state. Fix: read \"journalctl -u $unit -n 100\", then \"systemctl start $unit\""
    return
  fi
  # Fibre listens on --server-listen-address, else server_listen_address in
  # <home>/config/server_config.toml, else 0.0.0.0:7980.
  if [[ -z $port ]]; then
    argv=$(unit_argv "$unit")
    if addr=$(flag_value "$argv" server-listen-address); then
      if unit_expand "$unit" "$addr"; then addr=$EXPANDED; fi
    else
      cfg=$(fibre_home "$unit" "$argv")/config/server_config.toml
      if [[ -r $cfg ]]; then
        addr=$(toml_get "$cfg" "" server_listen_address)
      else
        note=" ($cfg could not be read, so port 7980 was assumed; pass --fibre-port if Fibre listens elsewhere)"
      fi
    fi
    port=${addr##*:}
    port=${port:-7980}
  fi
  if [[ ! $port =~ ^[0-9]+$ ]]; then
    report SKIP 6 "$name" "$unit.service active; cannot tell the listen port from \"$addr\" (pass --fibre-port)"
    return
  fi
  l=$(listeners "$port")
  if [[ -z $l ]]; then
    report FAIL 6 "$name" "$unit.service is active but nothing listens on port $port$note. Fix: read \"journalctl -u $unit -n 100\", then restart Fibre (\"systemctl restart $unit\") at a moment you choose"
    return
  fi
  report OK 6 "$name" "$unit.service active, listening on ${l//$'\n'/, }"
}

check_fibre_otel() {
  local name="fibre otlp" unit=${C[fibre_unit]} want=${C[fibre_otel_endpoint]%/} got src="" unread="" port gport detail
  if fibre_skip 7 "$name"; then return; fi
  if ! unit_loaded "$unit"; then report SKIP 7 "$name" "$unit.service not found (check 6)"; return; fi
  # Fibre reads FIBRE_OTEL_ENDPOINT and lets --otel-endpoint override it.
  if got=$(flag_value "$(unit_argv "$unit")" otel-endpoint); then
    src="--otel-endpoint in ExecStart"
    if ! unit_expand "$unit" "$got"; then
      report SKIP 7 "$name" "$src is $got, whose variables are not set in the unit's Environment= or a readable EnvironmentFile=; check the value by hand"
      return
    fi
    got=$EXPANDED
  elif unit_env "$unit" FIBRE_OTEL_ENDPOINT; then
    got=$ENV_VAL
    src=$ENV_SRC
  else
    got=""
    if [[ -n $ENV_UNREAD ]]; then unread=" ($ENV_UNREAD not readable: run as root)"; fi
  fi
  got=${got%/}
  if [[ -z $got ]]; then
    report FAIL 7 "$name" "no OTLP endpoint set$unread, so Fibre sends no metrics. Fix: add \"--otel-endpoint $want\" to ExecStart in $unit.service (or Environment=FIBRE_OTEL_ENDPOINT=$want), run \"systemctl daemon-reload\", then \"systemctl restart $unit\" at a moment you choose"
    return
  fi
  port=$(url_port "$want")
  if [[ $got != "$want" && $got != "${want/127.0.0.1/localhost}" && $got != "${want/localhost/127.0.0.1}" ]]; then
    gport=$(url_port "$got")
    if [[ -n $port && $gport != "$port" ]]; then
      detail="$src is $got: port ${gport:-not set}, but the collector takes OTLP on port $port (celestia_mon_ports.otelcol_otlp)"
    else
      detail="$src is $got, not the local collector $want"
    fi
    report FAIL 7 "$name" "$detail. Fix: set it to $want (the base URL, without /v1/metrics), run \"systemctl daemon-reload\", then \"systemctl restart $unit\" at a moment you choose"
    return
  fi
  if [[ -z $(listeners "$port") ]]; then
    report FAIL 7 "$name" "$src is $got, but nothing listens on port $port. Fix: re-run install.sh on this host so the collector (celestia-mon-otelcol) is installed and running, then read \"journalctl -u celestia-mon-otelcol -n 100\""
    return
  fi
  report OK 7 "$name" "$src is $got, the collector's OTLP port (check 13 on the monitor shows that its metrics arrive)"
}

# ------------------------------------------------------ checks on every host

firewall_state() {
  if grep -qs '^ENABLED=yes' /etc/ufw/ufw.conf; then
    echo "ufw enabled"
  elif systemctl is-active --quiet firewalld 2>/dev/null; then
    echo "firewalld active"
  elif systemctl is-active --quiet nftables 2>/dev/null; then
    echo "nftables active"
  else
    echo "none active (celestia-mon relies on the private bind and never enables a firewall)"
  fi
}

# The systemd units celestia-mon installs. Check 8 looks only at the sockets of their
# main processes, so other software on the host (a node_exporter, Prometheus,
# Grafana or collector of its own) is left alone. Every one of them must listen
# on a private address; Prometheus and Alertmanager on 127.0.0.1 only.
CELESTIA_MON_UNITS=(celestia-mon-node-exporter celestia-mon-otelcol celestia-mon-prometheus celestia-mon-alertmanager celestia-mon-grafana)
CELESTIA_MON_LOOPBACK_UNITS=(celestia-mon-prometheus celestia-mon-alertmanager)

check_binds() {
  local name="private binds" fw unit pid addr detail
  local -a ok=() bad=() wide=()
  local -A unit_of=()
  fw=$(firewall_state)
  if [[ $(id -u) -ne 0 ]]; then
    report SKIP 8 "$name" "run as root to see which process owns each socket; firewall: $fw"
    return
  fi
  for unit in "${CELESTIA_MON_UNITS[@]}"; do
    pid=$(unit_prop "$unit" MainPID)
    if [[ $pid =~ ^[1-9][0-9]*$ ]]; then unit_of[$pid]=$unit; fi
  done
  # ss prints the owners of each socket as users:(("name",pid=N,fd=M),...).
  while read -r pid addr; do
    [[ -n $pid && -n ${unit_of[$pid]+set} ]] || continue
    unit=${unit_of[$pid]}
    if [[ $(addr_class "$addr") != private ]]; then
      bad+=("$unit $addr")
    elif [[ " ${CELESTIA_MON_LOOPBACK_UNITS[*]} " == *" $unit "* ]] && ! is_loopback "$addr"; then
      wide+=("$unit $addr")
    else
      ok+=("$unit $addr")
    fi
  done < <(ss -Htlnp 2>/dev/null | awk '{ s = $0; while (match(s, /pid=[0-9]+/)) { print substr(s, RSTART + 4, RLENGTH - 4), $4; s = substr(s, RSTART + RLENGTH) } }' | sort -u)
  if ((${#bad[@]} > 0)); then
    detail="$(join "${bad[@]}") not on a private address"
    if ((${#wide[@]} > 0)); then detail+="; $(join "${wide[@]}") not on 127.0.0.1"; fi
    report FAIL 8 "$name" "$detail; firewall: $fw. Fix: set celestia_mon_private_ip for this host to its private address and re-run install.sh on each host"
  elif ((${#wide[@]} > 0)); then
    report FAIL 8 "$name" "$(join "${wide[@]}") not on 127.0.0.1, where Prometheus and Alertmanager listen; firewall: $fw. Fix: re-run install.sh on this host"
  elif ((${#ok[@]} > 0)); then
    report OK 8 "$name" "$(join "${ok[@]}"); firewall: $fw"
  else
    report SKIP 8 "$name" "no celestia-mon service listens on this host; firewall: $fw"
  fi
}

timespan_seconds() { # systemd timespan such as +1.234ms, -312us or +1.002s -> absolute seconds
  awk -v t="$1" 'BEGIN {
    sub(/^[+-]/, "", t)
    if (sub(/ms$/, "", t)) d = 1000
    else if (sub(/us$/, "", t)) d = 1000000
    else if (sub(/s$/, "", t)) d = 1
    else exit
    if (t ~ /^[0-9.]+$/) print t / d
  }'
}

check_time() {
  local name="time sync" synced offset="" src="" max=${C[time_offset_max]}
  if ! command -v timedatectl >/dev/null 2>&1; then report SKIP 9 "$name" "timedatectl not found"; return; fi
  synced=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
  if command -v chronyc >/dev/null 2>&1; then
    offset=$(chronyc -n tracking 2>/dev/null | awk -F': *' '/^System time/ { split($2, f, " "); print f[1] }')
    src=chrony
  fi
  if [[ -z $offset ]]; then
    offset=$(timespan_seconds "$(LC_ALL=C timedatectl timesync-status 2>/dev/null | awk '$1 == "Offset:" { print $2 }')")
    src=systemd-timesyncd
  fi
  if [[ $synced != yes ]]; then
    report FAIL 9 "$name" "clock not synchronized (NTPSynchronized=${synced:-unknown}). Fix: \"timedatectl set-ntp true\" (or start chrony) and allow outgoing NTP (UDP 123)"
    return
  fi
  if [[ -z $offset ]]; then
    report OK 9 "$name" "synchronized (offset unknown)"
  elif awk -v o="$offset" -v m="$max" 'BEGIN { exit !(o + 0 <= m + 0) }'; then
    report OK 9 "$name" "synchronized by $src, offset $(ms "$offset") ms"
  else
    report FAIL 9 "$name" "synchronized by $src, but the offset is $(ms "$offset") ms (limit $(ms "$max") ms). Fix: check the time sources (\"chronyc sources\" or \"timedatectl timesync-status\") and that NTP servers are reachable"
  fi
}

disk_state() { # mount point, minimum free ratio ("" = none) -> "ok TEXT" or "bad TEXT"
  local size="" avail="" target="" opts
  read -r size avail target < <(df -P -B1 -- "$1" 2>/dev/null | awk 'NR == 2 { print $2, $4, $6 }')
  if [[ -z $size ]]; then
    echo "bad $1 not found"
    return
  fi
  opts=$(awk -v t="$target" '$2 == t { o = $4 } END { print o }' /proc/self/mounts)
  if [[ ,$opts, == *,ro,* ]]; then
    echo "bad $1 is read-only"
    return
  fi
  awk -v m="$1" -v s="$size" -v a="$avail" -v min="$2" 'BEGIN {
    r = (s > 0) ? a / s : 0
    printf "%s %s %.0f%% free (%.1f GiB)\n", (min != "" && r < min + 0) ? "bad" : "ok", m, r * 100, a / 1073741824
  }'
}

check_disks() {
  local name="disk space" min=${C[disk_min_free_ratio]} m line detail
  local -a mounts=() ok=() bad=()
  IFS=', ' read -r -a mounts <<<"${C[data_mounts]}"
  for m in "${mounts[@]}"; do
    [[ -n $m ]] || continue
    line=$(disk_state "$m" "$min")
    if [[ $line == bad* ]]; then bad+=("${line#bad }"); else ok+=("${line#ok }"); fi
  done
  if has_role fibre && fibre_on && [[ -n ${C[fibre_mount]} ]]; then
    # No percentage rule here: a Fibre store near its budget is normal.
    line=$(disk_state "${C[fibre_mount]}" "")
    if [[ $line == bad* ]]; then bad+=("Fibre ${line#bad }"); else ok+=("Fibre ${line#ok }"); fi
  fi
  if ((${#ok[@]} + ${#bad[@]} == 0)); then
    report SKIP 10 "$name" "no data mounts configured (celestia_mon_data_mounts or --data-mounts)"
  elif ((${#bad[@]} > 0)); then
    detail="$(join "${bad[@]}") (data mounts need $(awk -v r="$min" 'BEGIN { printf "%.0f", r * 100 }')% free)"
    if ((${#ok[@]} > 0)); then detail+="; ok: $(join "${ok[@]}")"; fi
    report FAIL 10 "$name" "$detail. Fix: free space or grow the disk; for a read-only disk read \"dmesg\" for I/O errors before remounting it read-write"
  else
    report OK 10 "$name" "$(join "${ok[@]}")"
  fi
}

# Memory of each celestia-mon unit against its MemoryHigh (above it the kernel
# reclaims the service's memory and slows it down) and MemoryMax (at it the
# service is killed and restarted). WARN above 80% of MemoryHigh.
check_memory() {
  local name="unit memory" unit cur line key
  local -a ok=() hot=() keys=()
  for unit in "${CELESTIA_MON_UNITS[@]}"; do
    unit_loaded "$unit" || continue
    cur=$(unit_prop "$unit" MemoryCurrent)
    if [[ ! $cur =~ ^[0-9]+$ ]]; then
      ok+=("$unit not running")
      continue
    fi
    line=$(awk -v u="$unit" -v c="$cur" -v p="$(unit_prop "$unit" MemoryPeak)" -v h="$(unit_prop "$unit" MemoryHigh)" \
      -v m="$(unit_prop "$unit" MemoryMax)" 'BEGIN {
      t = sprintf("%s %.0f MiB", u, c / 1048576)
      if (p ~ /^[0-9]+$/) t = t sprintf(" (peak %.0f MiB)", p / 1048576)
      if (h ~ /^[0-9]+$/ && h > 0) t = t sprintf(", %.0f%% of MemoryHigh %.0f MiB", 100 * c / h, h / 1048576)
      else t = t ", no MemoryHigh"
      if (m ~ /^[0-9]+$/) t = t sprintf(", MemoryMax %.0f MiB", m / 1048576)
      s = (h ~ /^[0-9]+$/ && c > 0.8 * h) ? "hot " : "ok "
      print s t
    }')
    if [[ $line == hot* ]]; then
      hot+=("${line#hot }")
      key=${unit#celestia-mon-}
      keys+=("celestia_mon_memory.${key//-/_}")
    else
      ok+=("${line#ok }")
    fi
  done
  if ((${#ok[@]} + ${#hot[@]} == 0)); then
    report SKIP 14 "$name" "no celestia-mon service on this host"
  elif ((${#hot[@]} > 0)); then
    line="$(join "${hot[@]}") above 80% of MemoryHigh"
    if ((${#ok[@]} > 0)); then line+="; ok: $(join "${ok[@]}")"; fi
    report WARN 14 "$name" "$line. Fix: raise high and max in $(join "${keys[@]}") in the inventory and re-run install.sh on this host; above MemoryHigh the kernel slows the service down, at MemoryMax it kills it"
  else
    report OK 14 "$name" "$(join "${ok[@]}")"
  fi
}

# --------------------------------------------------------- monitor checks

http_code() { curl -s -g -o /dev/null --max-time 5 -w '%{http_code}' "$1" 2>/dev/null; }
answer() { # HTTP status from http_code -> words for a report line
  if [[ $1 == 000 ]]; then printf 'no connection'; else printf 'HTTP %s' "$1"; fi
}

TARGETS_UP=0      # scrape targets whose last scrape worked
TARGETS_PENDING=0 # scrape targets not scraped yet
TARGETS_DOWN=()   # "job url (error)" of each target whose last scrape failed
read_targets() { # Prometheus URL -> TARGETS_UP, TARGETS_PENDING and TARGETS_DOWN
  local line pool url err re_url='"scrapeUrl":"([^"]*)"' re_health='"health":"([a-z]*)"' re_err='"lastError":"(([^"\\]|\\.)*)"'
  TARGETS_UP=0
  TARGETS_PENDING=0
  TARGETS_DOWN=()
  # One line per active target, starting at its scrapePool field.
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line == '"scrapePool":"'* && $line =~ $re_health ]] || continue
    case ${BASH_REMATCH[1]} in
      up) TARGETS_UP=$((TARGETS_UP + 1)) ;;
      down)
        pool=${line#'"scrapePool":"'}
        pool=${pool%%'"'*}
        url=""
        err=""
        if [[ $line =~ $re_url ]]; then url=${BASH_REMATCH[1]}; fi
        # Keep the last part of the error, such as "connection refused".
        if [[ $line =~ $re_err ]]; then err=${BASH_REMATCH[1]//'\"'/'"'}; fi
        TARGETS_DOWN+=("$pool $url${err:+ (${err##*: })}")
        ;;
      *) TARGETS_PENDING=$((TARGETS_PENDING + 1)) ;;
    esac
  done < <(http_get "$1/api/v1/targets?state=active" | sed 's/"scrapePool":/\n&/g')
}

target_fix() { # scrape job -> what to check when its target is down
  case $1 in
    celestia-app) printf '%s' "celestia-app: run celestia-mon-doctor on that validator host (check 1 shows the node's metrics setting)" ;;
    node) printf '%s' "node: on that host read \"systemctl status celestia-mon-node-exporter\" and \"journalctl -u celestia-mon-node-exporter -n 100\"" ;;
    otelcol) printf '%s' "otelcol: on that Fibre host read \"systemctl status celestia-mon-otelcol\" and \"journalctl -u celestia-mon-otelcol -n 100\"" ;;
    prometheus | alertmanager) printf '%s' "$1: read \"journalctl -u celestia-mon-$1 -n 100\" on this host" ;;
    *) printf '%s' "$1: run celestia-mon-doctor on that host (checks 1 and 8 show what listens where)" ;;
  esac
}

PROM_OK=0
check_stack() {
  local name="monitor stack" prom=${C[prometheus_url]%/} am=${C[alertmanager_url]%/} code end total t
  local -a fixes=()
  local -A seen=()
  if ! has_role monitor; then report SKIP 11 "$name" "not the monitor host"; return; fi
  # Prometheus answers 503 until it has loaded its data, as it does right after
  # an install; the first scrape of each target follows within one interval.
  end=$((SECONDS + 60))
  code=$(http_code "$prom/-/ready")
  while [[ $code == 503 ]] && ((SECONDS < end)); do
    sleep 2
    code=$(http_code "$prom/-/ready")
  done
  if [[ $code != 200 ]]; then
    report FAIL 11 "$name" "Prometheus is not ready at $prom ($(answer "$code")). Fix: read \"systemctl status celestia-mon-prometheus\" and \"journalctl -u celestia-mon-prometheus -n 100\"; it listens on 127.0.0.1 only, so run doctor on the monitor host"
    return
  fi
  PROM_OK=1
  code=$(http_code "$am/-/ready")
  if [[ $code != 200 ]]; then
    report FAIL 11 "$name" "Alertmanager is not ready at $am ($(answer "$code")), so no alert is sent. Fix: read \"systemctl status celestia-mon-alertmanager\" and \"journalctl -u celestia-mon-alertmanager -n 100\""
    return
  fi
  read_targets "$prom"
  # Prometheus lists no targets until its scrape manager's first reload, about
  # 5 s after it turns ready; each target is then "unknown" until its first scrape.
  while ((TARGETS_PENDING > 0 || TARGETS_UP + ${#TARGETS_DOWN[@]} == 0)) && ((SECONDS < end)); do
    sleep 2
    read_targets "$prom"
  done
  total=$((TARGETS_UP + TARGETS_PENDING + ${#TARGETS_DOWN[@]}))
  if ((total == 0)); then
    report FAIL 11 "$name" "Prometheus at $prom has no scrape targets. Fix: re-run install.sh on this host, then read \"journalctl -u celestia-mon-prometheus -n 100\""
  elif ((${#TARGETS_DOWN[@]} > 0)); then
    # A down target means its collector or exporter is broken, so every metric it serves is missing.
    for t in "${TARGETS_DOWN[@]}"; do
      t=${t%% *}
      if [[ -z ${seen[$t]+set} ]]; then
        seen[$t]=1
        fixes+=("$(target_fix "$t")")
      fi
    done
    t=$(printf '%s; ' "${fixes[@]}")
    report FAIL 11 "$name" "${#TARGETS_DOWN[@]} of $total scrape targets down: $(join "${TARGETS_DOWN[@]}"). Their collector or exporter is broken, so the metrics they serve are missing. Fix: $t\"curl -s <url> | head\" must answer from this host, so also check the private network and any firewall between the hosts"
  elif ((TARGETS_PENDING > 0)); then
    report WARN 11 "$name" "Prometheus and Alertmanager answer; $TARGETS_PENDING of $total scrape targets not scraped yet. Fix: run celestia-mon-doctor again in a minute"
  else
    report OK 11 "$name" "Prometheus at $prom and Alertmanager at $am answer; $total of $total scrape targets up"
  fi
}

# Reads Prometheus's rules API (JSON) and prints "<metric> <rule>" for every
# metric a loaded rule reads. The list comes from the rules Prometheus runs, so
# it follows the rules file without a copy here. Each "query" is Prometheus's
# own rendering of the expression, so only that form needs parsing: drop string
# literals, label matchers, ranges and grouping label lists, then keep the names
# that are not functions, aggregations, keywords, or units of numbers and durations.
rule_metrics() {
  grep -oE '"name":"[^"]*","query":"([^"\\]|\\.)*"' | awk '
    BEGIN { esc = sprintf("%c", 1) }
    {
      rule = $0
      sub(/^"name":"/, "", rule)
      sub(/".*$/, "", rule)
      q = $0
      sub(/^"name":"[^"]*","query":"/, "", q)
      sub(/"$/, "", q)
      # JSON escapes: mark escaped backslashes first, so \\" stays an escaped
      # quote inside a PromQL string. Prometheus writes < > & as unicode escapes.
      gsub(/\\\\/, esc, q)
      gsub(/\\"/, "\"", q)
      gsub(/\\u[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]/, " ", q)
      gsub("\"([^\"" esc "]|" esc ".)*\"", " ", q)
      q = " " q
      gsub(/[{][^}]*[}]/, " ", q)
      gsub(/\[[^[]*\]/, " ", q)
      gsub(/[^a-zA-Z0-9_:](by|without|on|ignoring|group_left|group_right) *[(][^)]*[)]/, " ", q)
      while (match(q, /[a-zA-Z_:][a-zA-Z0-9_:]*/)) {
        t = substr(q, RSTART, RLENGTH)
        before = substr(q, RSTART - 1, 1)
        q = substr(q, RSTART + RLENGTH)
        if (before ~ /[0-9.]/ || q ~ /^ *[(]/) continue
        if (tolower(t) ~ /^(and|or|unless|atan2|bool|offset|anchored|smoothed|inf|nan)$/) continue
        print t, rule
      }
    }'
}

prom_query() { # PromQL -> JSON reply of an instant query to Prometheus (newline-terminated), empty on error
  local rc=0
  curl -fsS -g --max-time 10 -G --data-urlencode "query=$1" "${C[prometheus_url]%/}/api/v1/query" 2>/dev/null || rc=$?
  echo
  return "$rc"
}
prom_vector() { # PromQL, label -> "<label value> <sample value>" for each series of the reply
  prom_query "$1" | sed 's/{"metric":/\n&/g' | sed -n 's/.*"'"$2"'":"\([^"]*\)".*"value":\[[^],]*,"\([^"]*\)"].*/\1 \2/p'
}
prom_value() { # PromQL -> the sample value of a one-series reply, empty when there is none
  prom_query "$1" | sed -n 's/.*"value":\[[^],]*,"\([^"]*\)"].*/\1/p' | head -n 1
}

# The scrape job a rule metric comes from: "fibre" for Fibre's own metrics, which
# Fibre pushes to the collector (job otelcol); empty when unknown.
metric_source() {
  case $1 in
    cometbft_*) echo celestia-app ;;
    node_*) echo node ;;
    fibre_*) echo fibre ;;
    otelcol_*) echo otelcol ;;
    prometheus_*) echo prometheus ;;
    alertmanager_*) echo alertmanager ;;
  esac
}

# The install type decides which metrics can exist at all.
metric_expected() {
  case $1 in
    cometbft_privval_*) [[ ${C[signer]} != local ]] ;;
    fibre_* | otelcol_*) fibre_on ;;
    *) true ;;
  esac
}

# The event after which a metric first appears, for the metrics that do not exist
# until it happens (celestia-core privval/metrics.go, celestia-app
# fibre/server_metrics.go, Alertmanager notify/metrics.go); empty for the others.
metric_event() {
  case $1 in
    cometbft_privval_signing_failures_total) echo "the first failed signing request" ;;
    cometbft_privval_signing_latency_median_seconds) echo "50 remote signatures since the node started" ;;
    cometbft_privval_signing_latency_*) echo "the first remote signature" ;;
    cometbft_consensus_validator_last_signed_height) echo "the validator's first own signature in a block; it must be bonded and in the active set" ;;
    fibre_server_upload_shard_rejected_total) echo "the first upload the storage limiter rejects" ;;
    fibre_server_upload_shard_last_success_timestamp_seconds) echo "the first accepted upload" ;;
    fibre_server_upload_shard_duration_seconds*) echo "the first upload" ;;
    fibre_server_download_shard_duration_seconds*) echo "the first download" ;;
    alertmanager_notifications_failed_total) echo "Alertmanager starts with a receiver of that integration" ;;
  esac
}

# Sorts every metric the rules read: present (OK); not expected for this install
# type; missing because its source is broken (FAIL: its scrape target is down, or
# Fibre does not push); missing until an event (NOT YET); or missing although its
# source is up (WARN: most likely a renamed metric). One line per group.
check_rule_metrics() {
  local name="rule metrics" prom=${C[prometheus_url]%/} pairs names present m total nmiss=0 src why ev job state pushing=false line
  local -a missing=() rules=() notyet=() broken=() notexp=()
  local -A jobs=()
  if ! has_role monitor; then report SKIP 12 "$name" "not the monitor host"; return; fi
  if ((!PROM_OK)); then report SKIP 12 "$name" "Prometheus not answering (check 11)"; return; fi
  pairs=$(http_get "$prom/api/v1/rules?exclude_alerts=true" | rule_metrics | sort -u)
  if [[ -z $pairs ]]; then
    report FAIL 12 "$name" "Prometheus at $prom has no rules loaded. Fix: read \"journalctl -u celestia-mon-prometheus -n 100\" for errors in the rule files, then re-run install.sh on this host"
    return
  fi
  names=$(cut -d ' ' -f 1 <<<"$pairs" | sort -u)
  total=$(grep -c . <<<"$names")
  # One query lists which of the names have series now.
  present=$(prom_query "count by (__name__) ({__name__=~\"$(paste -sd '|' <<<"$names")\"})")
  if [[ $present != *'"status":"success"'* ]]; then
    report FAIL 12 "$name" "the series query to $prom/api/v1/query failed. Fix: read \"journalctl -u celestia-mon-prometheus -n 100\""
    return
  fi
  present=$(grep -oE '"__name__":"[^"]*"' <<<"$present" | cut -d '"' -f 4)
  # A scrape job is healthy when one of its targets is up. Fibre's metrics also need
  # Fibre to push them; it reports its storage budget from its first push on.
  while read -r job state; do jobs[$job]=$state; done < <(prom_vector 'max by (job) (up)' job)
  if [[ -n $(prom_value 'count(fibre_server_upload_shard_budget_bytes)') ]]; then pushing=true; fi
  while read -r m; do
    nmiss=$((nmiss + 1))
    if ! metric_expected "$m"; then
      notexp+=("$m")
      continue
    fi
    src=$(metric_source "$m")
    why=""
    if [[ $src == fibre && ${jobs[otelcol]:-} != 1 ]]; then
      why="otelcol target not up: check 11"
    elif [[ $src == fibre && $pushing != true ]]; then
      why="Fibre is not pushing metrics: check 13"
    elif [[ -n $src && $src != fibre && ${jobs[$src]:-} != 1 ]]; then
      why="$src target not up: check 11"
    fi
    ev=$(metric_event "$m")
    if [[ -n $why ]]; then
      broken+=("$m ($why)")
    elif [[ -n $ev ]]; then
      notyet+=("$m (after $ev)")
    else
      mapfile -t rules < <(awk -v m="$m" '$1 == m { print $2 }' <<<"$pairs")
      missing+=("$m ($(join "${rules[@]}"))")
    fi
  done < <(grep -vxF -f <(printf '%s\n' "$present") <<<"$names")
  if ((nmiss == 0)); then
    report OK 12 "$name" "all $total metrics the alert rules read have series"
    return
  fi
  if ((total > nmiss || ${#notexp[@]} > 0)); then
    line="$((total - nmiss)) of $total metrics the alert rules read have series"
    if ((${#notexp[@]} > 0)); then
      line+="; not expected with signer ${C[signer]:-unset} and fibre_enabled ${C[fibre_enabled]}, so not counted: $(join "${notexp[@]}")"
    fi
    report OK 12 "$name" "$line"
  fi
  if ((${#notyet[@]} > 0)); then
    report "NOT YET" 12 "$name" "${#notyet[@]} of $total metrics appear only after an event, and their source is up: $(join "${notyet[@]}")"
  fi
  if ((${#missing[@]} > 0)); then
    report WARN 12 "$name" "${#missing[@]} of $total metrics the alert rules read have no series although their source is up (the rules that read each are in brackets): $(join "${missing[@]}"). Fix: check that the service still exports each under this name (curl the target's /metrics); a renamed metric leaves its rules blind"
  fi
  if ((${#broken[@]} > 0)); then
    report FAIL 12 "$name" "${#broken[@]} of $total metrics have no series because their source is broken: $(join "${broken[@]}"). Fix: apply the fix of the check named with each"
  fi
}

# Fibre's metrics end to end, on the monitor: from each Fibre host, Prometheus must
# have a sample under 3 minutes old of both storage gauges, which Fibre reports from
# its first push on. The collector takes OTLP only on 127.0.0.1 at
# celestia_mon_ports.otelcol_otlp, so fresh series with host=<Fibre host> show that
# Fibre on that host sends to that port, not just that the port is open.
check_fibre_pipeline() {
  local name="fibre pipeline" ep=${C[fibre_otel_endpoint]%/} unit=${C[fibre_unit]} h v detail fix
  local -a hosts=() ok=() bad=() nopush=()
  local -A ups=() budget=() occupancy=()
  if ! has_role monitor; then report SKIP 13 "$name" "not the monitor host"; return; fi
  if ! fibre_on; then report SKIP 13 "$name" "Fibre is disabled"; return; fi
  if ((!PROM_OK)); then report SKIP 13 "$name" "Prometheus not answering (check 11)"; return; fi
  while read -r h v; do ups[$h]=$v; done < <(prom_vector 'up{job="otelcol"}' host)
  while read -r h v; do budget[$h]=$v; done < <(prom_vector 'min by (host) (time() - timestamp(fibre_server_upload_shard_budget_bytes))' host)
  while read -r h v; do occupancy[$h]=$v; done < <(prom_vector 'min by (host) (time() - timestamp(fibre_server_upload_shard_occupancy_bytes))' host)
  IFS=', ' read -r -a hosts <<<"${C[fibre_hosts]}"
  if ((${#hosts[@]} == 0)); then mapfile -t hosts < <(printf '%s\n' "${!ups[@]}" | sort); fi
  for h in "${hosts[@]}"; do
    [[ -n $h ]] || continue
    if [[ -z ${ups[$h]:-} ]]; then
      bad+=("$h: Prometheus has no collector target for it (re-run install.sh on the monitor)")
    elif [[ ${ups[$h]} != 1 ]]; then
      bad+=("$h: its collector is down (check 11)")
    elif [[ -z ${budget[$h]:-} || -z ${occupancy[$h]:-} ]]; then
      bad+=("$h: Fibre is not pushing metrics (no fibre_server_upload_shard_budget_bytes or occupancy_bytes from it)")
      nopush+=("$h")
    else
      v=$(awk -v a="${budget[$h]}" -v b="${occupancy[$h]}" 'BEGIN { printf "%.0f", (a + 0 > b + 0) ? a : b }')
      [[ $v =~ ^[0-9]+$ ]] || v=0
      if ((v > 180)); then
        bad+=("$h: its newest Fibre sample is ${v}s old")
        nopush+=("$h")
      else
        ok+=("$h (newest sample ${v}s old)")
      fi
    fi
  done
  if ((${#ok[@]} + ${#bad[@]} == 0)); then
    report FAIL 13 "$name" "Fibre is on, but Prometheus has no collector target (job otelcol). Fix: put the Fibre host in the inventory's fibre group and re-run install.sh on the monitor"
  elif ((${#bad[@]} > 0)); then
    detail=$(join "${bad[@]}")
    if ((${#ok[@]} > 0)); then detail+="; ok: $(join "${ok[@]}")"; fi
    fix="the step named with each host"
    if ((${#nopush[@]} > 0)); then
      ep=${ep:-http://127.0.0.1:<otlp port>}
      fix="on $(join "${nopush[@]}"), add \"--otel-endpoint $ep\" to ExecStart in $unit.service (or Environment=FIBRE_OTEL_ENDPOINT=$ep), run \"systemctl daemon-reload\", then \"systemctl restart $unit\" at a moment you choose; celestia-mon-doctor on that host (check 7) shows what the unit sets now"
    fi
    report FAIL 13 "$name" "$detail. Fix: $fix"
  else
    report OK 13 "$name" "Prometheus has fresh Fibre metrics from $(join "${ok[@]}"); the collector takes OTLP only on 127.0.0.1:$(url_port "$ep"), so Fibre's endpoint matches it"
  fi
}

size_bytes() { # Prometheus size such as 8GB or 512MiB (base 1024) -> bytes; empty when it is not one
  awk -v s="$1" 'BEGIN {
    if (!match(s, /^[0-9]+(\.[0-9]+)?/)) exit
    n = substr(s, 1, RLENGTH)
    u = toupper(substr(s, RLENGTH + 1))
    sub(/IB$/, "B", u)
    p = index("KMGTPE", substr(u, 1, 1))
    if (u == "B") f = 1
    else if (length(u) == 2 && substr(u, 2, 1) == "B" && p > 0) f = 1024 ^ p
    else exit
    printf "%.0f\n", n * f
  }'
}

# retention_size bounds Prometheus's blocks only: the WAL, the head chunks and the
# space a compaction writes before it deletes the old blocks come on top. The
# install's preflight asks for retention_size * 1.5 + 2 GiB on the data mount;
# here what Prometheus holds now plus the free space must reach the same amount.
check_prometheus_disk() {
  local name="monitor disk" dir=${C[prometheus_data_dir]} rs=${C[prometheus_retention_size]} used avail="" mount="" size need text
  if ! has_role monitor; then report SKIP 15 "$name" "not the monitor host"; return; fi
  if [[ -z $dir || ! -d $dir ]]; then
    report SKIP 15 "$name" "Prometheus data directory ${dir:-(not set)} not found (pass --prometheus-data-dir)"
    return
  fi
  if [[ $(id -u) -ne 0 ]]; then
    report SKIP 15 "$name" "run as root to measure $dir"
    return
  fi
  # du's exit status is not used: Prometheus may delete a file while du walks the directory.
  used=$(du -sb -- "$dir" 2>/dev/null | awk 'END { print $1 }')
  read -r avail mount < <(df -P -B1 -- "$dir" 2>/dev/null | awk 'NR == 2 { print $4, $6 }')
  if [[ ! $used =~ ^[0-9]+$ || ! $avail =~ ^[0-9]+$ ]]; then
    report SKIP 15 "$name" "cannot read the size of $dir or the free space of its mount"
    return
  fi
  text="Prometheus holds $(gib "$used") GiB in $dir and $mount has $(gib "$avail") GiB free"
  size=$(size_bytes "$rs")
  if [[ -z $size || $size == 0 ]]; then
    report OK 15 "$name" "$text (no retention size to compare with; pass --prometheus-retention-size)"
    return
  fi
  need=$(awk -v s="$size" 'BEGIN { printf "%.0f", s * 1.5 + 2147483648 }')
  if ((used + avail >= need)); then
    report OK 15 "$name" "$text: room for retention_size $rs x 1.5 + 2 GiB = $(gib "$need") GiB"
  else
    report FAIL 15 "$name" "$text, $(gib "$((used + avail))") GiB in all, but Prometheus may need retention_size $rs x 1.5 + 2 GiB = $(gib "$need") GiB there: the WAL, head chunks and compaction space come on top of retention_size. Fix: free space on $mount, or lower celestia_mon_prometheus_retention_size and re-run install.sh on the monitor"
  fi
}

# Disk load on the Celestia hosts around Prometheus's last compaction, which runs
# about every 2 hours, read from Prometheus. The same queries work in Grafana's
# Explore; T is the time of the compaction:
#   T        max_over_time(timestamp(increase(prometheus_tsdb_compactions_total{job="prometheus"}[2m]) > 0)[6h:1m])
#   latency  rate(node_disk_write_time_seconds_total{job="node"}[1m]) / rate(node_disk_writes_completed_total{job="node"}[1m])
#   busy     rate(node_disk_io_time_seconds_total{job="node"}[1m])
# For each, the worst disk's peak per host in the 10 minutes from 3 minutes before T
# is set against its peak in the hour before that.
check_compaction() {
  local name="compaction io" t h v lat busy when
  local -a parts=()
  local -A lp=() lb=() bp=() bb=()
  if ! has_role monitor; then report SKIP 16 "$name" "not the monitor host"; return; fi
  if ((!PROM_OK)); then report SKIP 16 "$name" "Prometheus not answering (check 11)"; return; fi
  t=$(prom_value 'max_over_time(timestamp(increase(prometheus_tsdb_compactions_total{job="prometheus"}[2m]) > 0)[6h:1m])')
  t=${t%%.*}
  if [[ ! $t =~ ^[0-9]+$ ]]; then
    report SKIP 16 "$name" "no Prometheus compaction in the last 6 hours (the first comes about 3 hours after Prometheus starts, then one every 2 hours); run doctor again later"
    return
  fi
  when=$(date -u -d "@$t" '+%Y-%m-%d %H:%M UTC' 2>/dev/null)
  lat='rate(node_disk_write_time_seconds_total{job="node"}[1m]) / (rate(node_disk_writes_completed_total{job="node"}[1m]) > 0)'
  busy='rate(node_disk_io_time_seconds_total{job="node"}[1m])'
  while read -r h v; do lp[$h]=$v; done < <(prom_vector "max by (host) (max_over_time(($lat)[10m:15s] @ $((t + 420))))" host)
  while read -r h v; do lb[$h]=$v; done < <(prom_vector "max by (host) (max_over_time(($lat)[1h:15s] @ $((t - 180))))" host)
  while read -r h v; do bp[$h]=$v; done < <(prom_vector "max by (host) (max_over_time(($busy)[10m:15s] @ $((t + 420))))" host)
  while read -r h v; do bb[$h]=$v; done < <(prom_vector "max by (host) (max_over_time(($busy)[1h:15s] @ $((t - 180))))" host)
  while read -r h; do
    [[ -n $h ]] || continue
    parts+=("$h $(awk -v a="${lp[$h]:-}" -v b="${lb[$h]:-}" -v c="${bp[$h]:-}" -v d="${bb[$h]:-}" '
      function f(v, k, fmt) { return (v ~ /^[0-9.eE+-]+$/) ? sprintf(fmt, v * k) : "?" }
      BEGIN { printf "write latency %s ms (%s ms), busy %s%% (%s%%)", f(a, 1000, "%.1f"), f(b, 1000, "%.1f"), f(c, 100, "%.0f"), f(d, 100, "%.0f") }')")
  done < <(printf '%s\n' "${!lp[@]}" "${!bp[@]}" | sort -u)
  if ((${#parts[@]} == 0)); then
    report SKIP 16 "$name" "no disk data from the Celestia hosts (job node) around the compaction at $when"
    return
  fi
  report INFO 16 "$name" "last Prometheus compaction about $when; peak of the worst disk per Celestia host in the 10 minutes around it (in the hour before in brackets): $(join "${parts[@]}"). A clear rise on a validator or Fibre disk means Prometheus competes with it; IOWeight cannot stop that under the none I/O scheduler (common on NVMe) unless io.cost is set, so then put Prometheus's data (${C[prometheus_data_dir]}) on another disk"
}

# ------------------------------------------------------------------- main

conf=/etc/celestia-mon/doctor.conf
conf_given=false
overrides=()
while (($# > 0)); do
  case $1 in
    -h | --help)
      usage
      exit 0
      ;;
    --config=*)
      conf=${1#*=}
      conf_given=true
      shift
      ;;
    --config)
      need_value "$@"
      conf=$2
      conf_given=true
      shift 2
      ;;
    --*=*)
      opt=${1%%=*}
      overrides+=("${opt#--}" "${1#*=}")
      shift
      ;;
    --*)
      need_value "$@"
      overrides+=("${1#--}" "$2")
      shift 2
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -r $conf ]]; then
  load_conf "$conf"
elif [[ $conf_given == true ]]; then
  echo "celestia-mon-doctor: cannot read $conf" >&2
  exit 2
fi
for ((i = 0; i < ${#overrides[@]}; i += 2)); do
  key=${overrides[i]}
  set_opt "${key//-/_}" "${overrides[i + 1]}"
done

if [[ -z ${C[roles]} ]]; then
  roles=()
  if unit_loaded "${C[appd_unit]}"; then roles+=(validator); fi
  if unit_loaded "${C[fibre_unit]}"; then roles+=(fibre); fi
  if unit_loaded celestia-mon-prometheus; then roles+=(monitor); fi
  C[roles]=$(IFS=,; printf '%s' "${roles[*]}")
fi
if [[ -z ${C[fibre_enabled]} ]]; then
  if unit_loaded "${C[fibre_unit]}"; then C[fibre_enabled]=true; else C[fibre_enabled]=false; fi
fi

printf 'celestia-mon-doctor on %s (roles: %s; signer: %s; fibre_enabled: %s)\n' "${HOSTNAME:-$(uname -n)}" \
  "${C[roles]:-none}" "${C[signer]:-not set}" "${C[fibre_enabled]}"
if has_role validator; then find_node; fi

check_metrics
check_signer
check_head
check_address
check_registration
check_fibre_service
check_fibre_otel
check_binds
check_time
check_disks
check_stack
check_rule_metrics
check_fibre_pipeline
check_memory
check_prometheus_disk
check_compaction

if ((failed)); then
  echo "Some checks failed. celestia-mon-doctor changed nothing; apply the fixes above yourself."
  exit 1
fi
exit 0
