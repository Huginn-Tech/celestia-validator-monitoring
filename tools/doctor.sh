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
# Prometheus and Alertmanager listen on 127.0.0.1 only, so checks 11 to 13, 15,
# 16, 18, 19, 21 and 22 run on the monitor host and ask them there, with the API
# password that only root can read (api_password_file).
#
# signer and fibre_enabled tell doctor the install type: with signer=local it
# never expects the remote signer's metrics, with fibre_enabled=false never
# Fibre's or the collector's.
#
# Roles: validator (the node the signer serves), backup_node (a key-less,
# synced celestia-appd node that can take the validator role over), fibre (a
# Fibre install, active or standby), kms and monitor. Which Fibre host is
# active is read from systemd, never from the settings: with two or more
# fibre_hosts, a stopped Fibre unit is a standby.
#
# Needs only bash, coreutils, util-linux (runuser, setsid), curl, ss and ip
# (iproute2), systemctl, grep, sed and awk.

set -u

declare -A C=(
  [roles]=""
  [inventory_host]=""
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
  [node_hosts]=""
  [prometheus_url]="http://127.0.0.1:9912"
  [alertmanager_url]="http://127.0.0.1:9913"
  [api_user]="celestia-mon"
  [api_password_file]="/etc/celestia-mon/api-password"
  [prometheus_data_dir]="/var/lib/celestia-mon/prometheus"
  [prometheus_retention_size]=""
  [signer_median_max]="0.010"
  [disk_min_free_ratio]="0.10"
  [node_behind_blocks]="20"
  [time_offset_max]="0.1"
  [head_wait]="30"
  [private_address_regex]='^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|127\.|::1$|f[cd][0-9a-fA-F]{2}:)'
)

usage() {
  cat <<'EOF'
Usage: celestia-mon-doctor [--config FILE] [--KEY VALUE ...]

Read-only checks for a Celestia validator, backup node, Fibre, KMS or monitor
host. Settings come from /etc/celestia-mon/doctor.conf (KEY=VALUE lines) and
can be overridden with flags (--node-rpc-url URL sets node_rpc_url).

  roles                  comma-separated: validator, backup_node, fibre, kms,
                         monitor (default: guessed from the units on this
                         host; a backup node needs --roles backup_node)
  inventory_host         this host's name in the inventory, to find its own
                         entry in fibre_public_endpoint
  signer                local or remote; with local, no remote signer metrics
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
  fibre_public_endpoint  host:port that should be registered in x/valaddr; with
                         several Fibre hosts, comma-separated NAME=host:port,
                         one per host (any of them may be the registered one)
  fibre_port             Fibre listen port (default: the unit's
                         --server-listen-address, else server_listen_address
                         in Fibre's config/server_config.toml, else 7980)
  fibre_otel_endpoint    expected OTLP base URL (default http://127.0.0.1:9915)
  fibre_hosts            comma-separated inventory names of every Fibre host,
                         active or standby; with two or more, a stopped Fibre
                         unit is a standby (default: none; on the monitor, the
                         collector targets)
  node_hosts             comma-separated inventory names of the celestia-appd
                         nodes (validator and backup nodes), on the monitor
                         (default: the celestia-app targets)
  prometheus_url         celestia-mon's Prometheus, on the monitor host
                         (default http://127.0.0.1:9912)
  alertmanager_url       celestia-mon's Alertmanager, on the monitor host
                         (default http://127.0.0.1:9913)
  api_user, api_password_file  HTTP basic auth for those two (default
                         celestia-mon and /etc/celestia-mon/api-password,
                         readable by root; none when the file is missing)
  prometheus_data_dir    default /var/lib/celestia-mon/prometheus
  prometheus_retention_size  Prometheus's retention size, such as 8GB
                         (default: not compared)
  signer_median_max      seconds (default 0.010)
  disk_min_free_ratio    default 0.10
  node_behind_blocks     blocks a node may trail the highest node (default 20)
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
node_host() { has_role validator || has_role backup_node; }
fibre_on() { [[ ${C[fibre_enabled],,} == true ]]; }
# With two or more Fibre hosts one is active and the others are standbys, so a
# stopped Fibre unit is expected.
fibre_standby_layout() {
  local -a h=()
  local n=0 x
  IFS=', ' read -r -a h <<<"${C[fibre_hosts]}"
  for x in "${h[@]}"; do [[ -n $x ]] && n=$((n + 1)); done
  ((n >= 2))
}
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

# celestia-mon's Prometheus and Alertmanager ask for HTTP basic auth: user api_user, the
# password in api_password_file (root only). curl reads it from a config on a file
# descriptor, so it never shows in the process list, and gets it only for their URLs.
API_AUTH=""
api_auth_load() {
  local f=${C[api_password_file]} pw
  [[ -n $f && -r $f ]] || return 0
  pw=$(tr -d '\r\n' <"$f")
  [[ -n $pw ]] || return 0
  pw=${pw//\\/\\\\}
  pw=${pw//\"/\\\"}
  API_AUTH="user = \"${C[api_user]}:$pw\""
}
api_url() { # URL -> true for celestia-mon's Prometheus or Alertmanager
  local p=${C[prometheus_url]%/} a=${C[alertmanager_url]%/}
  [[ (-n $p && $1 == "$p"*) || (-n $a && $1 == "$a"*) ]]
}
mon_curl() { # URL, curl options -> curl's output, with the basic auth for celestia-mon's API URLs
  local url=$1
  shift
  if [[ -n $API_AUTH ]] && api_url "$url"; then
    curl -K <(printf '%s\n' "$API_AUTH") "$@" "$url"
  else
    curl "$@" "$url"
  fi
}

# The API answers end without a newline; add one so `while read` loops see the last line.
http_get() {
  local rc=0
  mon_curl "$1" -fsS -g --max-time 5 2>/dev/null || rc=$?
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
  local ah=${1%:*}
  ah=${ah%%\%*}
  ah=${ah#\[}
  ah=${ah%\]}
  printf '%s' "${ah#::ffff:}"
}

is_loopback() { # local address as ss prints it -> true for 127.0.0.0/8 and ::1
  local h
  h=$(addr_host "$1")
  [[ $h == 127.* || $h == ::1 ]]
}

host_address() { # IP address -> true when it is one of this host's own addresses
  ip -o addr show 2>/dev/null | awk -v a="$1" '{ sub(/\/.*/, "", $4); if ($4 == a) f = 1 } END { exit !f }'
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
# not matter. Its stdin is /dev/null and setsid gives it a session of its own
# without a controlling terminal, so the binary never gets the terminal doctor
# runs on (no keystrokes to read, no prompt to fake); the callers capture its
# output.
as_node() {
  if [[ $(id -u) -eq 0 && -n $NODE_USER && $NODE_USER != root ]]; then
    (cd / && runuser -u "$NODE_USER" -- setsid -w "$@" </dev/null)
  else
    setsid -w "$@" </dev/null
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
  local name="node metrics" url=${C[node_metrics_url]} cfg prom="" laddr="" ns="" note="" a lhost wild="" bind fw sev=WARN
  local -a bad=()
  if ! node_host; then report SKIP 1 "$name" "not a validator or backup node host"; return; fi
  # The address the monitor scrapes, which is where the metrics belong: this host's
  # private address, never 0.0.0.0 or an empty host (CometBFT's default ":26660").
  bind=$(url_hostport "$url")
  if [[ $bind == 127.* || $bind == localhost* || $bind == '[::1]'* ]]; then bind="<private address>:$(url_port "$url")"; fi
  cfg=$NODE_HOME/config/config.toml
  if [[ -r $cfg ]]; then
    prom=$(toml_get "$cfg" instrumentation prometheus)
    laddr=$(toml_get "$cfg" instrumentation prometheus_listen_addr)
    ns=$(toml_get "$cfg" instrumentation namespace)
    if [[ $prom != true ]]; then
      report FAIL 1 "$name" "prometheus = ${prom:-unset} in $cfg. Fix: set \"prometheus = true\" and prometheus_listen_addr = \"$bind\" (this host's private address, never 0.0.0.0 or an empty host) under [instrumentation] in $cfg, then restart celestia-appd at a moment you choose"
      return
    fi
  else
    cfg="config.toml"
    note=" ($NODE_HOME/config/config.toml not readable: run as root or pass --node-home)"
  fi
  METRICS=$(http_get "$url")
  if [[ -z $METRICS ]]; then
    report FAIL 1 "$name" "no answer from $url$note. Fix: set prometheus = true and prometheus_listen_addr = \"$bind\" (now ${laddr:-unknown}) under [instrumentation] in $cfg so the node serves $url, then restart celestia-appd at a moment you choose"
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
  # With no firewall active, nothing but the bind keeps the port off the networks of this host.
  fw=$(firewall_state)
  if [[ $fw == none* ]]; then sev=FAIL; fi
  if [[ -n $wild ]]; then
    report "$sev" 1 "$name" "enabled at $url, but $wild: celestia-appd serves its metrics on every interface of this host, a Tailscale interface included, which ufw does not filter (firewall: $fw). Whoever reaches the port can read the node's metrics and hold its few connections (max_open_connections, 3 by default), which blinds the signing alerts. Fix: bind it to the private address the monitor scrapes, prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg (never 0.0.0.0 or an empty host), then restart celestia-appd at a moment you choose; celestia-mon-doctor never edits the config"
  elif ((${#bad[@]} > 0)); then
    report "$sev" 1 "$name" "enabled at $url, but the port listens on $(join "${bad[@]}"), which is not a private address (firewall: $fw). Fix: set prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg (or block the port in a firewall), then restart celestia-appd at a moment you choose"
  else
    report OK 1 "$name" "enabled at $url$note"
  fi
}

signer_median() { # -> the node's median signing latency in seconds from METRICS, empty when absent
  awk '$1 ~ /^(cometbft|celestia)_privval_signing_latency_median_seconds($|[{])/ { print $NF; exit }' <<<"$METRICS"
}

STATUS=""
status_value() { # key -> value of "key":"value" or "key":value in the flattened /status reply
  sed -n 's/.*"'"$1"'":"\{0,1\}\([^",}]*\).*/\1/p' <<<"$STATUS" | head -n 1
}

# The node's /status, read once before check 2, which needs the voting power of the
# key the node signs with (validator_info), and check 3, which starts from its height.
read_status() {
  STATUS=$(http_get "${C[node_rpc_url]%/}/status" | tr -d ' \t\r\n')
  [[ $(status_value latest_block_height) =~ ^[0-9]+$ ]] || STATUS=""
}
status_address() { # -> the hex address of the key the node signs with (validator_info.address), empty when absent
  sed -n 's/.*"validator_info":{"address":"\([0-9A-Fa-f]*\)".*/\1/p' <<<"$STATUS"
}

# On a backup node that is also the monitor: true when Prometheus saw this hex address as the
# validator's in the last 30 days. celestia-core exports cometbft_consensus_validator_power only
# for the node's own key while that key is in the validator set, so the series names the
# validator's consensus key, also while the validator is jailed and its power reads 0.
validator_key_seen() { # hex address
  if [[ -z $1 ]] || ! has_role monitor; then return 1; fi
  prom_vector 'group by (validator_address) (last_over_time(cometbft_consensus_validator_power{job="celestia-app"}[30d]))' validator_address |
    cut -d ' ' -f 1 | grep -qixF -- "$1"
}

check_signer() {
  local name="remote signer" cfg laddr median signer=${C[signer]} max=${C[signer_median_max]} power key own hex
  if ! node_host; then report SKIP 2 "$name" "not a validator or backup node host"; return; fi
  cfg=$NODE_HOME/config/config.toml
  if [[ ! -r $cfg ]]; then
    report SKIP 2 "$name" "$cfg not readable (run as root or pass --node-home)"
    return
  fi
  laddr=$(toml_get "$cfg" "" priv_validator_laddr)
  # With priv_validator_laddr empty, celestia-appd signs with its local key file
  # (priv_validator_key_file, relative to the home; it creates a new key when the file is
  # missing). /status shows that key's voting power: above 0, this node signs for the
  # validator, and next to tmkms or another node with the same key that is a double sign.
  power=$(status_value voting_power)
  [[ $power =~ ^[0-9]+$ ]] || power=""
  key=$(toml_get "$cfg" "" priv_validator_key_file)
  key=${key:-config/priv_validator_key.json}
  [[ $key == /* ]] || key=$NODE_HOME/$key
  own="so this node signs with its local key file $key"
  hex=$(status_address)
  # A backup node is key-less while priv_validator_laddr is empty and its key file is not the
  # validator's. With priv_validator_laddr set, celestia-appd waits for a remote signer at
  # start and exits when none connects, so only the node tmkms points at has it. Voting power
  # 0 does not prove the key is not the validator's: a jailed or unbonded validator's key also
  # has none, and signs again after unjail.
  if ! has_role validator; then
    if [[ -z $laddr && -n $power && $power != 0 ]]; then
      report FAIL 2 "$name" "backup node: priv_validator_laddr is empty, $own, which has voting power $power: it is the validator's key, and two signers of one validator double-sign. Fix: stop celestia-appd here now (\"systemctl stop ${C[appd_unit]}\"), move $key off this host, and start it again only after that (it then creates a new key that is not in the validator set)"
    elif [[ -z $laddr && $power == 0 ]] && validator_key_seen "$hex"; then
      report FAIL 2 "$name" "backup node: priv_validator_laddr is empty, $own (address $hex). Its voting power is 0 now, but Prometheus saw this address as the validator's in the last 30 days (cometbft_consensus_validator_power), so it is the validator's consensus key and the validator is jailed or unbonded: after unjail, this node and tmkms double-sign. Fix: stop celestia-appd here now (\"systemctl stop ${C[appd_unit]}\"), move $key off this host, and start it again only after that (it then creates a new key that is not in the validator set)"
    elif [[ -z $laddr && $power == 0 ]]; then
      report INFO 2 "$name" "backup node: priv_validator_laddr is empty, so this node needs no signer to run; its local key file $key${hex:+ (address $hex)} is not in the validator set (voting power 0) now. That key must not be your validator's consensus key (compare the address with the hex that check 4 shows on the validator): a jailed or unbonded validator's key also has voting power 0, and signs again after unjail, next to tmkms (with priv_validator_laddr set, celestia-appd exits at start when no signer connects)"
    elif [[ -z $laddr ]]; then
      report INFO 2 "$name" "backup node: priv_validator_laddr is empty, $own; its voting power is unknown until the node RPC answers (check 3), and must be 0, or the node double-signs next to tmkms (with priv_validator_laddr set, celestia-appd exits at start when no signer connects)"
    else
      median=$(signer_median)
      report INFO 2 "$name" "backup node: priv_validator_laddr = $laddr, so this node signs through a remote signer, as after a tmkms repoint to it, and exits at its next start when no signer connects${median:+; median signing latency $(ms "$median") ms}"
    fi
    return
  fi
  if [[ -z $laddr ]]; then
    if [[ $signer == remote && -n $power && $power != 0 ]]; then
      report FAIL 2 "$name" "priv_validator_laddr is empty in $cfg, $own, which has voting power $power, but celestia-mon is set to signer: remote: if tmkms signs for this validator through another node, the two double-sign. Fix: if tmkms runs, stop celestia-appd here now (\"systemctl stop ${C[appd_unit]}\") and set priv_validator_laddr to the private address tmkms dials before you start it again; if this host signs with its key file on purpose, set celestia_mon_signer: local in the inventory and re-run install.sh on each host"
    elif [[ $signer == remote ]]; then
      report FAIL 2 "$name" "priv_validator_laddr is empty in $cfg, $own (voting power ${power:-unknown: check 3}), not through tmkms, but celestia-mon is set to signer: remote. Fix: finish the remote signer setup: set priv_validator_laddr to the private address tmkms dials and restart celestia-appd at a moment you choose, with tmkms already running (the node exits at start when no signer connects); if this validator signs with a local key file, set celestia_mon_signer: local in the inventory and re-run install.sh on each host"
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
  median=$(signer_median)
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

signer_port_state() { # address from config.toml -> "ok|wildcard|public TEXT": its listeners now, else the address itself
  local a=$1 hp h l where="" worst=ok
  if [[ $a == unix://* ]]; then
    echo "ok $a (a unix socket)"
    return
  fi
  hp=${a#tcp://}
  h=${hp%:*}
  h=${h#\[}
  h=${h%\]}
  while read -r l; do
    [[ -n $l ]] || continue
    where+="${where:+, }$l"
    case $(addr_class "$l") in
      public) worst=public ;;
      wildcard) [[ $worst == public ]] || worst=wildcard ;;
    esac
  done <<<"$(listeners "${hp##*:}")"
  if [[ -z $where ]]; then
    where="nothing listens on it now"
    case $h in
      '' | 0.0.0.0 | :: | '*') worst=wildcard ;;
      localhost) ;;
      *) [[ $h =~ ${C[private_address_regex]} ]] || worst=public ;;
    esac
  fi
  echo "$worst $a ($where)"
}

# Where the node's two signer ports listen, read from config.toml and ss: doctor never
# connects to them. celestia-appd does not authenticate the remote signer that connects to
# priv_validator_laddr, so whoever reaches that port while tmkms reconnects can take the
# signer's place (missed blocks, then jailing); ufw does not filter a Tailscale interface.
# priv_validator_grpc_laddr serves Fibre; celestia-appd refuses it beyond loopback without
# mTLS unless priv_validator_grpc_allow_insecure = true.
check_signer_binds() {
  local name="signer binds" cfg laddr g gh s note=""
  local -a fails=() warns=() oks=()
  if ! node_host; then report SKIP 20 "$name" "not a validator or backup node host"; return; fi
  cfg=$NODE_HOME/config/config.toml
  if [[ ! -r $cfg ]]; then
    report SKIP 20 "$name" "$cfg not readable (run as root or pass --node-home)"
    return
  fi
  laddr=$(toml_get "$cfg" "" priv_validator_laddr)
  g=$(toml_get "$cfg" "" priv_validator_grpc_laddr)
  if [[ -n $laddr ]]; then
    s=$(signer_port_state "$laddr")
    case ${s%% *} in
      public) fails+=("priv_validator_laddr = ${s#* } is on a public address") ;;
      wildcard) warns+=("priv_validator_laddr = ${s#* } is on every interface: celestia-appd does not authenticate the signer that connects, so whoever reaches the port while tmkms reconnects can take its place, and ufw does not filter a Tailscale interface") ;;
      *) oks+=("priv_validator_laddr = ${s#* }") ;;
    esac
  fi
  if [[ -n $g ]]; then
    s=$(signer_port_state "$g")
    gh=${g%:*}
    gh=${gh#\[}
    gh=${gh%\]}
    if [[ $(toml_get "$cfg" "" priv_validator_grpc_allow_insecure) == true && $gh != 127.* && $gh != ::1 ]]; then
      fails+=("priv_validator_grpc_laddr = ${s#* } with priv_validator_grpc_allow_insecure = true: whoever reaches it can ask for raw-bytes signatures with the validator key, no client certificate needed")
    else
      case ${s%% *} in
        public) warns+=("priv_validator_grpc_laddr = ${s#* } is on a public address (mTLS guards the signatures)") ;;
        wildcard) warns+=("priv_validator_grpc_laddr = ${s#* } is on every interface (mTLS guards the signatures)") ;;
        *) oks+=("priv_validator_grpc_laddr = ${s#* }") ;;
      esac
    fi
  elif ! has_role validator; then
    note="; priv_validator_grpc_laddr is empty: after a tmkms repoint to this node, a Fibre can sign through it only once that is set"
  fi
  if ((${#fails[@]} + ${#warns[@]} == 0)); then
    if ((${#oks[@]} == 0)); then
      report OK 20 "$name" "no signer port: priv_validator_laddr and priv_validator_grpc_laddr are empty$note"
    else
      report OK 20 "$name" "$(join "${oks[@]}")$note"
    fi
    return
  fi
  s="$(join "${fails[@]}" "${warns[@]}")${oks[*]:+; ok: $(join "${oks[@]}")}$note. Fix: in $cfg, set each to 127.0.0.1 or this host's private address that tmkms (priv_validator_laddr) or Fibre (priv_validator_grpc_laddr, with mTLS beyond loopback and priv_validator_grpc_allow_insecure = false) dials, never 0.0.0.0, an empty host or a public address, then restart celestia-appd at a moment you choose, with tmkms running"
  if ((${#fails[@]} > 0)); then report FAIL 20 "$name" "$s"; else report WARN 20 "$name" "$s"; fi
}

block_age() { # RFC 3339 UTC time such as 2026-10-17T10:00:00.123456789Z -> whole seconds since then, empty when unreadable
  local t=${1%%.*} ts
  t=${t%Z}
  [[ $t =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$ ]] || return 0
  ts=$(date -u -d "${t/T/ }" +%s 2>/dev/null) || return 0
  [[ $ts =~ ^[0-9]+$ ]] || return 0
  ts=$(($(date +%s) - ts))
  printf '%s' "$((ts < 0 ? 0 : ts))"
}

# Shows that the node advances, not that it is at the network's head: an
# explorer or status.celestia.org shows that. On a backup node it adds the age
# of the newest block; check 18 on the monitor counts the blocks it trails.
check_head() {
  local name="node progress" rpc=${C[node_rpc_url]%/} limit=${C[head_wait]} h0 h1 catching end s="" age
  if ! node_host; then report SKIP 3 "$name" "not a validator or backup node host"; return; fi
  # read_status fetched /status before check 2.
  h0=$(status_value latest_block_height)
  if [[ ! $h0 =~ ^[0-9]+$ ]]; then
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
    s=$(http_get "$rpc/status" | tr -d ' \t\r\n')
    h1=$(sed -n 's/.*"latest_block_height":"\{0,1\}\([0-9]*\).*/\1/p' <<<"$s")
    [[ $h1 =~ ^[0-9]+$ ]] || h1=0
  done
  if ((h1 > h0)) && ! has_role validator; then
    age=$(block_age "$(sed -n 's/.*"latest_block_time":"\([^"]*\)".*/\1/p' <<<"$s")")
    report OK 3 "$name" "backup node: height $h0 -> $h1, catching_up=false${age:+, newest block ${age}s old}; check 18 on the monitor shows how many blocks it trails the validator"
  elif ((h1 > h0)); then
    report OK 3 "$name" "height $h0 -> $h1, catching_up=false (the node advances; an explorer shows the network's head)"
  else
    report FAIL 3 "$name" "height stayed at $h0 for ${limit}s (NodeNotAdvancing alerts when no node of the install advances, NodeBehind when only this one stops). Fix: check status.celestia.org and an explorer first; if the whole chain stopped, coordinate with other validators instead of fixing locally. Otherwise check peers and \"journalctl -u ${C[appd_unit]} -n 100\" (a node often stops at an upgrade height)"
  fi
}

VALCONS=""
check_address() {
  local name="validator addr" hex power out
  if ! has_role validator; then report SKIP 4 "$name" "not a validator host"; return; fi
  if [[ -z $STATUS ]]; then report SKIP 4 "$name" "node RPC not answering (check 3)"; return; fi
  hex=$(status_address)
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

# The fix for a missing or wrong x/valaddr registration. set-host is signed by the validator's
# account key, which also controls the validator's staking settings: it belongs on the
# operator's workstation (a Ledger), never on a server. An ops key with an authz grant for
# MsgSetFibreProviderInfo can send it instead (docs/quickstart.md, Mainnet layout).
set_host_fix() { # host:port -> the fix, for a report line
  printf '%s' "register $1 from your workstation, never with a key on this server: celestia-appd tx valaddr set-host $1 --from <validator account key, on a Ledger> --node <RPC of a synced node>; with an authz grant for set-host to an ops key, add --generate-only and --from <validator account address>, and send the file with \"celestia-appd tx authz exec <file> --from <ops key>\""
}

check_registration() {
  local name="fibre registry" out flat found host e n ep list="" first="" count=0 matched=false match_name="" own="" here suggest
  local -a eps=()
  if ! has_role validator; then report SKIP 5 "$name" "not a validator host"; return; fi
  if ! fibre_on; then report SKIP 5 "$name" "Fibre is disabled"; return; fi
  if [[ -z $VALCONS ]]; then report SKIP 5 "$name" "consensus address unknown (check 4)"; return; fi
  out=$(as_node timeout 30 "$APPD" query valaddr provider "$VALCONS" --node "${C[node_rpc_url]}" --home "$NODE_HOME" --output json 2>&1)
  flat=$(tr -d ' \t\r\n' <<<"$out")
  found=$(sed -n 's/.*"found":\([a-z]*\).*/\1/p' <<<"$flat")
  host=$(sed -n 's/.*"host":"\([^"]*\)".*/\1/p' <<<"$flat")
  # fibre_public_endpoint is one host:port, or NAME=host:port for each Fibre host (active or
  # standby); the registration may name any of them.
  IFS=',' read -r -a eps <<<"${C[fibre_public_endpoint]}"
  for e in "${eps[@]}"; do
    e=${e//[[:space:]]/}
    [[ -n $e ]] || continue
    n=""
    ep=$e
    if [[ $e == *=* ]]; then
      n=${e%%=*}
      ep=${e#*=}
    fi
    count=$((count + 1))
    first=${first:-$ep}
    list+="${list:+, }$ep${n:+ ($n)}"
    if [[ $matched == false && -n $host && $host == "$ep" ]]; then
      matched=true
      match_name=$n
    fi
    if [[ -n $n && $n == "${C[inventory_host]}" ]]; then own=$ep; fi
  done
  case $count in
    0) suggest="<public-host>:7980" ;;
    1) suggest=$first ;;
    *) suggest="<host:port of the active Fibre host>" ;;
  esac
  if [[ -z $found ]]; then
    report FAIL 5 "$name" "query failed: $(last_line "$out"). Fix: run \"celestia-appd query valaddr provider $VALCONS\" by hand; x/valaddr exists only from app v10, so keep celestia_mon_fibre_enabled: false until v10 is active on this network"
    return
  fi
  if [[ $found != true || -z $host ]]; then
    report FAIL 5 "$name" "no Fibre host registered for $VALCONS. Fix: $(set_host_fix "$suggest")${list:+ (configured: $list)}"
    return
  fi
  # This host also has a Fibre install (active or standby): the registration must name it
  # exactly while its Fibre is the active one.
  if [[ -n $own ]] && has_role fibre && fibre_standby_layout; then
    here=$(unit_prop "${C[fibre_unit]}" ActiveState)
    if [[ $here == active && $host != "$own" ]]; then
      report FAIL 5 "$name" "Fibre is active on this host, but x/valaddr registers $host${match_name:+ ($match_name)}. Fix: $(set_host_fix "$own"); or stop Fibre here if the other host is the active one (check 19 on the monitor)"
      return
    fi
    if [[ $here != active && $host == "$own" ]]; then
      report FAIL 5 "$name" "x/valaddr registers this host's endpoint $own, but Fibre is ${here:-not active} here (standby). Fix: $(set_host_fix "<host:port of the active Fibre host>") (check 19 on the monitor shows which host is active; configured: $list)"
      return
    fi
  fi
  if ((count == 0)); then
    report OK 5 "$name" "registered $host (no Fibre public endpoint configured to compare)"
  elif [[ $matched == true ]]; then
    report OK 5 "$name" "registered $host, as configured${match_name:+ for $match_name}"
  elif ((count == 1)); then
    report FAIL 5 "$name" "registered $host, but the configured endpoint is $first. Fix: $(set_host_fix "$first") (or correct celestia_mon_fibre_public_endpoint if $host is right)"
  else
    report FAIL 5 "$name" "registered $host, which is none of the configured endpoints ($list). Fix: $(set_host_fix "$suggest") (or correct celestia_mon_fibre_public_endpoint of that host if $host is right)"
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

# With two or more Fibre hosts (fibre_hosts), one runs Fibre and the others keep a
# stopped standby: an inactive unit with a closed port is then expected, and the
# monitor (check 19) tells which host is active.
check_fibre_service() {
  local name="fibre service" unit=${C[fibre_unit]} state port=${C[fibre_port]} argv addr="" cfg note="" l="" closed boot off
  if fibre_skip 6 "$name"; then return; fi
  if ! unit_loaded "$unit"; then
    report FAIL 6 "$name" "$unit.service not found. Fix: set celestia_mon_units.fibre (or --fibre-unit) to the name of Fibre's systemd unit"
    return
  fi
  state=$(unit_prop "$unit" ActiveState)
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
  if [[ $port =~ ^[0-9]+$ ]]; then l=$(listeners "$port"); fi
  if [[ $state != active ]]; then
    if fibre_standby_layout && [[ $state == inactive || $state == failed ]]; then
      # An enabled standby starts at the next boot of this host, next to the active Fibre.
      boot=$(unit_prop "$unit" UnitFileState)
      if [[ -n $l ]]; then
        report WARN 6 "$name" "standby: $unit.service is $state, but port $port listens on ${l//$'\n'/, }, so a Fibre may run outside the unit; two Fibre instances must never be live at the same time. Fix: find the process with \"ss -tlnp | grep :$port\" and stop it"
      elif [[ $boot =~ ^(enabled|enabled-runtime|linked|linked-runtime|alias)$ ]]; then
        off="systemctl disable $unit"
        if [[ $boot == *-runtime ]]; then off="systemctl disable --runtime $unit"; fi
        report WARN 6 "$name" "standby: $unit.service is $state, but its unit file is $boot, so a reboot of this host starts it next to the active Fibre; two Fibre instances must never be live at the same time. Fix: \"$off\" (it neither starts nor stops the unit)"
      elif [[ $state == failed ]]; then
        report WARN 6 "$name" "standby: $unit.service is failed, so it last stopped with an error. Fix: read \"journalctl -u $unit -n 100\" so it starts cleanly when promoted, then \"systemctl reset-failed $unit\" (which does not start it)"
      else
        closed="its listen port is unknown (pass --fibre-port)"
        if [[ $port =~ ^[0-9]+$ ]]; then closed="port $port is closed$note"; fi
        report OK 6 "$name" "standby: $unit.service is inactive and $closed, as expected while another Fibre host is active (check 19 on the monitor shows which)"
      fi
    elif fibre_standby_layout; then
      report FAIL 6 "$name" "$unit.service is $state. Fix: read \"journalctl -u $unit -n 100\"; check 19 on the monitor shows whether another Fibre host is active before you start or stop Fibre here"
    else
      report FAIL 6 "$name" "$unit.service is $state. Fix: read \"journalctl -u $unit -n 100\", then \"systemctl start $unit\" (on a standby Fibre host, list every Fibre host in fibre_hosts so a stopped unit counts as the standby)"
    fi
    return
  fi
  if [[ ! $port =~ ^[0-9]+$ ]]; then
    report SKIP 6 "$name" "$unit.service active; cannot tell the listen port from \"$addr\" (pass --fibre-port)"
    return
  fi
  if [[ -z $l ]]; then
    report FAIL 6 "$name" "$unit.service is active but nothing listens on port $port$note. Fix: read \"journalctl -u $unit -n 100\", then restart Fibre (\"systemctl restart $unit\") at a moment you choose"
    return
  fi
  report OK 6 "$name" "$unit.service active, listening on ${l//$'\n'/, }"
}

# A standby's unit must point at the local collector too, so its metrics flow the
# moment it is promoted; the advice then never restarts (starts) a stopped unit.
check_fibre_otel() {
  local name="fibre otlp" unit=${C[fibre_unit]} want=${C[fibre_otel_endpoint]%/} got src="" unread="" port gport detail
  local apply="then \"systemctl restart $unit\" at a moment you choose" standby=""
  if fibre_skip 7 "$name"; then return; fi
  if ! unit_loaded "$unit"; then report SKIP 7 "$name" "$unit.service not found (check 6)"; return; fi
  if [[ $(unit_prop "$unit" ActiveState) != active ]]; then
    apply="and leave the unit stopped: it uses the new value when it next starts"
    standby="; the unit is not active (standby), so its metrics flow there once it starts"
  fi
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
    report FAIL 7 "$name" "no OTLP endpoint set$unread, so Fibre sends no metrics. Fix: add \"--otel-endpoint $want\" to ExecStart in $unit.service (or Environment=FIBRE_OTEL_ENDPOINT=$want), run \"systemctl daemon-reload\", $apply"
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
    report FAIL 7 "$name" "$detail. Fix: set it to $want (the base URL, without /v1/metrics), run \"systemctl daemon-reload\", $apply"
    return
  fi
  if [[ -z $(listeners "$port") ]]; then
    report FAIL 7 "$name" "$src is $got, but nothing listens on port $port. Fix: re-run install.sh on this host so the collector (celestia-mon-otelcol) is installed and running, then read \"journalctl -u celestia-mon-otelcol -n 100\""
    return
  fi
  if [[ -n $standby ]]; then
    report OK 7 "$name" "$src is $got, the collector's OTLP port$standby"
  else
    report OK 7 "$name" "$src is $got, the collector's OTLP port (check 13 on the monitor shows that its metrics arrive)"
  fi
}

# Fibre signs through the validator node's privval gRPC: --signer-grpc-address, else
# signer_grpc_address in <home>/config/server_config.toml, else 127.0.0.1:26669
# (celestia-app fibre/server_config.go). Fibre resolves a host name at every dial, and
# without mTLS it accepts only a loopback IP literal, not "localhost", so the address
# must be an IP: the validator's private address, or 127.0.0.1 for a signer on this
# host. Fibre uses mTLS only when all three signer_grpc_* files are set (a flag in
# ExecStart over the config file), and plaintext otherwise.
#
# celestia-appd has one privval gRPC listener (priv_validator_grpc_laddr), mTLS or
# plaintext. On a host with a celestia-appd node, a Fibre signer on this host (loopback, or
# with mTLS this host's own address) works only while that listener is at the same address
# (or a wildcard) and port, in the same mode: a plaintext loopback signer needs a plaintext
# listener, and then a Fibre on another host cannot sign through this node; an mTLS signer
# needs the node's mTLS listener. So a standby Fibre next to the validator node needs its own
# client certificate for the node's mTLS address, or the listener switched at its promotion.
# The check reads both configs and never connects to the port.
check_fibre_signer() {
  local name="fibre signer" unit=${C[fibre_unit]} argv addr src cfg ip apply home f v kf tls=false ncfg g gh gp target problem=""
  local mode when loop=false port ncert kind fix
  local -a files=()
  local re4='^([0-9]{1,3}(\.[0-9]{1,3}){3}):[0-9]+$' re6='^\[([0-9A-Fa-f:.]+)\]:[0-9]+$'
  if fibre_skip 17 "$name"; then return; fi
  if ! unit_loaded "$unit"; then report SKIP 17 "$name" "$unit.service not found (check 6)"; return; fi
  apply="then restart Fibre at a moment you choose"
  when="now"
  if [[ $(unit_prop "$unit" ActiveState) != active ]]; then
    apply="and leave the unit stopped: it uses the new value when it next starts"
    when="once it starts"
  fi
  argv=$(unit_argv "$unit")
  home=$(fibre_home "$unit" "$argv")
  cfg=$home/config/server_config.toml
  if addr=$(flag_value "$argv" signer-grpc-address); then
    src="--signer-grpc-address in ExecStart"
    if ! unit_expand "$unit" "$addr"; then
      report SKIP 17 "$name" "$src is $addr, whose variables are not set in the unit's Environment= or a readable EnvironmentFile=; check the value by hand"
      return
    fi
    addr=$EXPANDED
  else
    if [[ ! -r $cfg ]]; then
      report SKIP 17 "$name" "no --signer-grpc-address in ExecStart, and $cfg is not readable (run as root)"
      return
    fi
    src="signer_grpc_address in $cfg"
    addr=$(toml_get "$cfg" "" signer_grpc_address)
    if [[ -z $addr ]]; then
      addr=127.0.0.1:26669
      src="Fibre's default (no signer_grpc_address in $cfg)"
    fi
  fi
  ip=""
  if [[ $addr =~ $re4 || $addr =~ $re6 ]]; then ip=${BASH_REMATCH[1]}; fi
  if [[ -z $ip ]]; then
    report FAIL 17 "$name" "$src is $addr, not an IP address and port: Fibre resolves a host name at every dial, and without mTLS it accepts only a loopback IP such as 127.0.0.1, not \"localhost\". Fix: set it to the signer's IP and port (the validator node's private address with mTLS, or 127.0.0.1:26669 for a plaintext signer on this host), run \"systemctl daemon-reload\" if you changed the unit, $apply"
    return
  fi
  for f in ca cert key; do
    if v=$(flag_value "$argv" "signer-grpc-$f-file"); then
      if unit_expand "$unit" "$v"; then v=$EXPANDED; fi
    elif [[ -r $cfg ]]; then
      v=$(toml_get "$cfg" "" "signer_grpc_${f}_file")
    fi
    files+=("$v")
  done
  if [[ -n ${files[0]} && -n ${files[1]} && -n ${files[2]} ]]; then tls=true; fi
  if [[ $ip == 127.* || $ip == ::1 ]]; then loop=true; fi
  port=${addr##*:}
  if node_host && [[ -r $NODE_HOME/config/config.toml ]] && { [[ $loop == true ]] || { [[ $tls == true ]] && host_address "$ip"; }; }; then
    ncfg=$NODE_HOME/config/config.toml
    g=$(toml_get "$ncfg" "" priv_validator_grpc_laddr)
    g=${g#tcp://}
    gp=${g##*:}
    gh=${g%:*}
    gh=${gh#\[}
    gh=${gh%\]}
    ncert=$(toml_get "$ncfg" "" priv_validator_grpc_cert_file)
    target="<this host's private address>:$gp"
    # The fix for an mTLS signer; a plaintext loopback signer gets its own below.
    fix="set --signer-grpc-address in ExecStart (or signer_grpc_address in $cfg) to the node's privval gRPC"
    if [[ -z $g ]]; then
      problem="this node serves no privval gRPC (priv_validator_grpc_laddr is empty in $ncfg)"
      target="<this host's private address>:$port"
      if [[ $tls == true ]]; then target=$addr; fi
      fix="set priv_validator_grpc_laddr = \"$target\" in $ncfg with priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file (the CA that issued this Fibre's client certificate), then restart celestia-appd at a moment you choose, with tmkms running"
    elif [[ $gp != "$port" ]]; then
      problem="the node's privval gRPC is $g (priv_validator_grpc_laddr in $ncfg), another port"
      # A wildcard listener: the same address this Fibre dials now, on the node's port.
      v=$gh
      if [[ -z $v || $v == 0.0.0.0 || $v == :: ]]; then v=$ip; fi
      if [[ $v == *:* ]]; then v="[$v]"; fi
      fix+=" $v:$gp, $apply"
    elif [[ -n $gh && $gh != 0.0.0.0 && $gh != :: && $gh != "$ip" ]] &&
      ! [[ $loop == true && ($gh == 127.* || $gh == ::1 || $gh == localhost) ]]; then
      problem="the node's privval gRPC listens on $g only (priv_validator_grpc_laddr in $ncfg), not on $ip"
      target=$g
      fix+=" $g, $apply"
    elif [[ $tls == false && -n $ncert ]]; then
      problem="the node serves its privval gRPC on $g with mTLS (priv_validator_grpc_cert_file and the other two TLS files in $ncfg), so a plaintext dial fails"
    elif [[ $tls == true && -z $ncert ]]; then
      problem="the node serves its privval gRPC on $g in plaintext (no priv_validator_grpc_cert_file in $ncfg), so this Fibre's mTLS handshake fails"
      fix="either serve the node's privval gRPC with mTLS (priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file in $ncfg, the client CA being the one that issued this Fibre's certificate) and restart celestia-appd at a moment you choose, with tmkms running; or, for a plaintext signer on 127.0.0.1:$gp, remove the three signer_grpc_* files from this Fibre, $apply"
    fi
  fi
  if [[ -n $problem ]]; then
    kind="an mTLS signer on this host"
    if [[ $tls == false ]]; then
      kind="a plaintext loopback signer"
      fix="either give this Fibre a client certificate of its own from the CA the node trusts and point it at the node's mTLS address $target (--signer-grpc-address with --signer-grpc-ca-file, --signer-grpc-cert-file and --signer-grpc-key-file in ExecStart, or signer_grpc_* in $cfg), $apply; or, when this Fibre takes over, set the node's priv_validator_grpc_laddr to 127.0.0.1:$port without the TLS files and restart celestia-appd before Fibre starts"
    fi
    v="$src is $addr, $kind, but $problem: $when, every signature of this Fibre fails. celestia-appd has one privval gRPC listener, plaintext on loopback or mTLS on an address a Fibre on another host can reach, never both. Fix: $fix (check 20 shows where the node's signer ports listen)"
    if [[ $when == now ]]; then report FAIL 17 "$name" "$v"; else report WARN 17 "$name" "standby: $v"; fi
    return
  fi
  # The client key lets whoever reads it ask the node for raw-bytes signatures with the
  # validator key: payment promises, not votes (celestia-core prefixes them).
  kf=${files[2]}
  if [[ $tls == true && $kf != /* ]]; then kf=$home/$kf; fi
  if [[ $tls == true && -f $kf ]]; then
    mode=$(stat -c '%a' "$kf" 2>/dev/null)
    if [[ $mode =~ ^[0-7]+$ ]] && ((8#$mode & 8#044)); then
      report WARN 17 "$name" "$src is $addr with mTLS, but the client key $kf is readable by group or other (mode $mode, owner $(stat -c '%U:%G' "$kf" 2>/dev/null)): whoever reads it can ask the validator node for raw-bytes signatures. Fix: \"chmod 600 $kf\", owned by Fibre's user ($(unit_prop "$unit" User | grep . || echo root))"
      return
    fi
  fi
  if [[ $ip == 127.* || $ip == ::1 ]]; then
    report OK 17 "$name" "$src is $addr, the loopback signer on this host$([[ $tls == true ]] && printf ', mTLS')"
  else
    report OK 17 "$name" "$src is $addr, an IP address$([[ $tls == true ]] && printf ', mTLS')"
  fi
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
# on a private address; Prometheus, Alertmanager and celestia-mon-announcements on 127.0.0.1 only.
CELESTIA_MON_UNITS=(celestia-mon-node-exporter celestia-mon-otelcol celestia-mon-prometheus celestia-mon-alertmanager celestia-mon-grafana
  celestia-mon-announcements)
CELESTIA_MON_LOOPBACK_UNITS=(celestia-mon-prometheus celestia-mon-alertmanager celestia-mon-announcements)

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

http_code() { mon_curl "$1" -s -g -o /dev/null --max-time 5 -w '%{http_code}' 2>/dev/null; }
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
    celestia-app) printf '%s' "celestia-app: run celestia-mon-doctor on that validator or backup node host (check 1 shows the node's metrics setting)" ;;
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
  if [[ $code == 401 && -z $API_AUTH ]]; then
    report FAIL 11 "$name" "Prometheus at $prom asks for a login (HTTP 401), and doctor cannot read the API password in ${C[api_password_file]}. Fix: run it as root on the monitor host (sudo celestia-mon-doctor)"
    return
  elif [[ $code == 401 ]]; then
    report FAIL 11 "$name" "Prometheus at $prom rejects the API password in ${C[api_password_file]} (HTTP 401). Fix: re-run install.sh on this host, which writes that password's hash into Prometheus's web config"
    return
  fi
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
  mon_curl "${C[prometheus_url]%/}/api/v1/query" -fsS -g --max-time 10 -G --data-urlencode "query=$1" 2>/dev/null || rc=$?
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
# until it happens (celestia-core privval/metrics.go and consensus/state.go, celestia-app
# fibre/server_metrics.go, Alertmanager notify/metrics.go); empty for the others.
metric_event() {
  case $1 in
    cometbft_privval_signing_failures_total) echo "the first failed signing request" ;;
    cometbft_privval_signing_latency_median_seconds) echo "50 remote signatures since the node started" ;;
    cometbft_privval_signing_latency_*) echo "the first remote signature" ;;
    cometbft_consensus_validator_last_signed_height) echo "the validator's first own signature in a block; it must be bonded and in the active set" ;;
    cometbft_consensus_validator_missed_blocks) echo "the first block committed without the validator's signature since the node started" ;;
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

# Which Fibre hosts run Fibre now, read from systemd through node_exporter, never from
# the inventory: FIBRE_STATE[<host>] is active when the Fibre unit is active there, and
# standby when it is not, or when node_exporter answers but lists no such unit (it lists
# loaded units only, and systemd unloads a stopped unit that nothing references, such as a
# disabled standby). Hosts whose node_exporter is down have no entry.
declare -A FIBRE_STATE=()
fibre_states() {
  local h v
  local -A act=() up=()
  FIBRE_STATE=()
  while read -r h v; do act[$h]=$v; done < <(prom_vector "max by (host) (node_systemd_unit_state{job=\"node\",name=\"${C[fibre_unit]}.service\",state=\"active\"})" host)
  while read -r h v; do up[$h]=$v; done < <(prom_vector 'max by (host) (up{job="node"})' host)
  for h in "${!act[@]}" "${!up[@]}"; do
    if [[ ${act[$h]:-} == 1 ]]; then
      FIBRE_STATE[$h]=active
    elif [[ ${act[$h]:-} == 0 || ${up[$h]:-} == 1 ]]; then
      FIBRE_STATE[$h]=standby
    fi
  done
}

# Fibre's metrics end to end, on the monitor: from each Fibre host, Prometheus must
# have a sample under 3 minutes old of both storage gauges, which Fibre reports from
# its first push on. The collector takes OTLP only on 127.0.0.1 at
# celestia_mon_ports.otelcol_otlp, so fresh series with host=<Fibre host> show that
# Fibre on that host sends to that port, not just that the port is open. A standby
# (its Fibre unit not active) sends nothing, but its collector must be up, so its
# metrics flow the moment it is promoted.
check_fibre_pipeline() {
  local name="fibre pipeline" ep=${C[fibre_otel_endpoint]%/} unit=${C[fibre_unit]} h v detail fix sb=""
  local -a hosts=() ok=() bad=() nopush=() standby=()
  local -A ups=() budget=() occupancy=()
  if ! has_role monitor; then report SKIP 13 "$name" "not the monitor host"; return; fi
  if ! fibre_on; then report SKIP 13 "$name" "Fibre is disabled"; return; fi
  if ((!PROM_OK)); then report SKIP 13 "$name" "Prometheus not answering (check 11)"; return; fi
  fibre_states
  while read -r h v; do ups[$h]=$v; done < <(prom_vector 'up{job="otelcol"}' host)
  while read -r h v; do budget[$h]=$v; done < <(prom_vector 'min by (host) (time() - timestamp(fibre_server_upload_shard_budget_bytes))' host)
  while read -r h v; do occupancy[$h]=$v; done < <(prom_vector 'min by (host) (time() - timestamp(fibre_server_upload_shard_occupancy_bytes))' host)
  IFS=', ' read -r -a hosts <<<"${C[fibre_hosts]}"
  if ((${#hosts[@]} == 0)); then mapfile -t hosts < <(printf '%s\n' "${!ups[@]}" | sort); fi
  for h in "${hosts[@]}"; do
    [[ -n $h ]] || continue
    if [[ -z ${ups[$h]:-} ]]; then
      bad+=("$h: Prometheus has no collector target for it (re-run install.sh on the monitor)")
      continue
    elif [[ ${ups[$h]} != 1 ]]; then
      bad+=("$h: its collector is down (check 11)")
      continue
    fi
    v=""
    if [[ -n ${budget[$h]:-} && -n ${occupancy[$h]:-} ]]; then
      v=$(awk -v a="${budget[$h]}" -v b="${occupancy[$h]}" 'BEGIN { printf "%.0f", (a + 0 > b + 0) ? a : b }')
      [[ $v =~ ^[0-9]+$ ]] || v=0
    fi
    if [[ -n $v ]] && ((v <= 180)); then
      ok+=("$h (newest sample ${v}s old)")
    elif [[ ${FIBRE_STATE[$h]:-} == standby ]]; then
      standby+=("$h")
    elif [[ -z $v ]]; then
      bad+=("$h: Fibre is not pushing metrics (no fibre_server_upload_shard_budget_bytes or occupancy_bytes from it)")
      nopush+=("$h")
    else
      bad+=("$h: its newest Fibre sample is ${v}s old")
      nopush+=("$h")
    fi
  done
  if ((${#standby[@]} > 0)); then
    sb="; standby (Fibre unit not active, collector up, no Fibre metrics expected): $(join "${standby[@]}")"
  fi
  if ((${#ok[@]} + ${#bad[@]} + ${#standby[@]} == 0)); then
    report FAIL 13 "$name" "Fibre is on, but Prometheus has no collector target (job otelcol). Fix: put the Fibre host in the inventory's fibre group and re-run install.sh on the monitor"
  elif ((${#bad[@]} > 0)); then
    detail=$(join "${bad[@]}")
    if ((${#ok[@]} > 0)); then detail+="; ok: $(join "${ok[@]}")"; fi
    detail+=$sb
    fix="the step named with each host"
    if ((${#nopush[@]} > 0)); then
      ep=${ep:-http://127.0.0.1:<otlp port>}
      fix="on $(join "${nopush[@]}"), add \"--otel-endpoint $ep\" to ExecStart in $unit.service (or Environment=FIBRE_OTEL_ENDPOINT=$ep), run \"systemctl daemon-reload\", then \"systemctl restart $unit\" at a moment you choose; celestia-mon-doctor on that host (check 7) shows what the unit sets now"
    fi
    report FAIL 13 "$name" "$detail. Fix: $fix"
  elif ((${#ok[@]} == 0)); then
    report SKIP 13 "$name" "no Fibre unit is active, so no Fibre metrics are expected (check 19)$sb"
  else
    report OK 13 "$name" "Prometheus has fresh Fibre metrics from $(join "${ok[@]}"); the collector takes OTLP only on 127.0.0.1:$(url_port "$ep"), so Fibre's endpoint matches it$sb"
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

# Every celestia-appd node against the highest one, on the monitor: a backup node that
# trails is not ready to take the validator role over. Role-independent: the node that
# exports cometbft_consensus_validator_last_signed_height holds the signer now, wherever
# the inventory put it.
check_node_lag() {
  local name="node lag" limit=${C[node_behind_blocks]} h v max=0 smax=0 t lag fix
  local -a hosts=() ok=() behind=() nodata=()
  local -A height=() signs=()
  if ! has_role monitor; then report SKIP 18 "$name" "not the monitor host"; return; fi
  if ((!PROM_OK)); then report SKIP 18 "$name" "Prometheus not answering (check 11)"; return; fi
  [[ $limit =~ ^[0-9]+$ ]] || limit=20
  while read -r h v; do
    v=${v%%.*}
    if [[ $v =~ ^[0-9]+$ ]]; then height[$h]=$v; fi
  done < <(prom_vector 'max by (host) (cometbft_consensus_height{job="celestia-app"})' host)
  # The node that signed last holds the signer; after a tmkms repoint the old one may still
  # export its last value.
  while read -r h v; do
    v=${v%%.*}
    if [[ $v =~ ^[0-9]+$ ]]; then signs[$h]=$v; fi
  done < <(prom_vector 'max by (host) (cometbft_consensus_validator_last_signed_height{job="celestia-app"})' host)
  for v in "${height[@]}"; do
    if ((v > max)); then max=$v; fi
  done
  for v in "${signs[@]}"; do
    if ((v > smax)); then smax=$v; fi
  done
  IFS=', ' read -r -a hosts <<<"${C[node_hosts]}"
  if ((${#hosts[@]} == 0)); then mapfile -t hosts < <(printf '%s\n' "${!height[@]}" | sort); fi
  for h in "${hosts[@]}"; do
    [[ -n $h ]] || continue
    if [[ -z ${height[$h]:-} ]]; then
      nodata+=("$h")
      continue
    fi
    v=${height[$h]}
    lag=$((max - v))
    t="$h $v"
    if ((lag > 0)); then t+=" ($lag behind)"; fi
    if [[ -n ${signs[$h]:-} && ${signs[$h]} == "$smax" ]]; then t+=" (signs)"; fi
    if ((lag > limit)); then behind+=("$t"); else ok+=("$t"); fi
  done
  if ((${#ok[@]} + ${#behind[@]} + ${#nodata[@]} == 0)); then
    report SKIP 18 "$name" "Prometheus has no celestia-app target (node_hosts not set)"
    return
  fi
  t=""
  if ((${#ok[@]} > 0)); then t="highest height $max: $(join "${ok[@]}")"; fi
  if ((${#behind[@]} + ${#nodata[@]} == 0)); then
    report OK 18 "$name" "$t"
    return
  fi
  fix="apply check 11's fix for those celestia-app targets"
  if ((${#behind[@]} > 0)); then
    t+="${t:+; }more than $limit blocks behind the highest node ($max; the NodeBehind alert after 10 minutes of it): $(join "${behind[@]}")"
    fix="a node that trails cannot take the validator role over; on that host run celestia-mon-doctor (check 3) and check its peers, disk and \"journalctl -u ${C[appd_unit]} -n 100\""
  fi
  if ((${#nodata[@]} > 0)); then
    t+="${t:+; }no height from $(join "${nodata[@]}") (its celestia-app target is down or missing: check 11)"
  fi
  report WARN 18 "$name" "$t. Fix: $fix"
}

# Which Fibre host is active, on the monitor, from systemd (fibre_states): exactly one
# must be. None active is what the FibreDown alert pages for; two break the
# active-passive rule (two Fibre instances must never be live at the same time).
check_fibre_active() {
  local name="fibre active" unit=${C[fibre_unit]} h extra=""
  local -a hosts=() on=() off=() unknown=()
  if ! has_role monitor; then report SKIP 19 "$name" "not the monitor host"; return; fi
  if ! fibre_on; then report SKIP 19 "$name" "Fibre is disabled"; return; fi
  if ((!PROM_OK)); then report SKIP 19 "$name" "Prometheus not answering (check 11)"; return; fi
  fibre_states
  IFS=', ' read -r -a hosts <<<"${C[fibre_hosts]}"
  if ((${#hosts[@]} == 0)); then mapfile -t hosts < <(prom_vector 'up{job="otelcol"}' host | cut -d ' ' -f 1 | sort -u); fi
  for h in "${hosts[@]}"; do
    [[ -n $h ]] || continue
    case ${FIBRE_STATE[$h]:-} in
      active) on+=("$h") ;;
      standby) off+=("$h") ;;
      *) unknown+=("$h") ;;
    esac
  done
  if ((${#off[@]} > 0)); then extra+="; standby: $(join "${off[@]}")"; fi
  if ((${#unknown[@]} > 0)); then extra+="; no unit state from $(join "${unknown[@]}") (its node_exporter target is down or missing: check 11)"; fi
  if ((${#on[@]} + ${#off[@]} + ${#unknown[@]} == 0)); then
    report FAIL 19 "$name" "Fibre is on, but Prometheus knows no Fibre host (job otelcol). Fix: put the Fibre hosts in the inventory's fibre group and re-run install.sh on each"
  elif ((${#on[@]} + ${#off[@]} == 0)); then
    report FAIL 19 "$name" "cannot tell which Fibre host is active${extra}. Fix: apply check 11's fix for those node_exporter targets"
  elif ((${#on[@]} == 0)); then
    report FAIL 19 "$name" "no Fibre unit ($unit.service) is active on any Fibre host${extra}; the FibreDown alert pages after 10 minutes. Fix: start Fibre on one Fibre host (read \"journalctl -u $unit -n 100\" there, then \"systemctl start $unit\"), and if x/valaddr registers another host, point it at the one you started: $(set_host_fix "<its public host:port>") (check 5 on the validator shows the registration)"
  elif ((${#on[@]} > 1)); then
    report WARN 19 "$name" "Fibre is active on $(join "${on[@]}")${extra}: two Fibre instances must never be live at the same time. Fix: stop Fibre (\"systemctl stop $unit\") on every host but the one x/valaddr registers (check 5 on the validator shows it)"
  else
    report OK 19 "$name" "Fibre is active on ${on[0]}${extra}"
  fi
}

# The alerts of the rules with severity page (DiskLow also has message rules).
PAGE_ALERTS=(NodeNotAdvancing ValidatorNotSigning FibreDown DiskLow)

# Alertmanager's GET /api/v2/silences reply (JSON) -> one line per active silence: its ID,
# endsAt, createdBy and matchers, split by \034; the matchers are split by \036, each one
# name \037 operator \037 value. A small JSON reader, so values may hold any character.
silences_active() {
  awk '
    function value(v) {
      if (d == 2 && key[2] == "id") id = v
      else if (d == 2 && key[2] == "createdBy") by = v
      else if (d == 2 && key[2] == "endsAt") ends = v
      else if (d == 3 && key[2] == "status" && key[3] == "state") state = v
      else if (d == 4 && key[2] == "matchers") m[key[4]] = v
    }
    function opened() {
      if (d == 2 && obj[2]) { id = ""; by = ""; ends = ""; state = ""; ms = "" }
      if (d == 4 && obj[4] && key[2] == "matchers") { m["name"] = ""; m["value"] = ""; m["isRegex"] = "false"; m["isEqual"] = "true" }
    }
    function closed(op) {
      if (d == 4 && obj[4] && key[2] == "matchers") {
        op = (m["isEqual"] == "false") ? "!" : "="
        op = op ((m["isRegex"] == "true") ? "~" : ((op == "!") ? "=" : ""))
        ms = ms ((ms == "") ? "" : "\036") m["name"] "\037" op "\037" m["value"]
      }
      if (d == 2 && obj[2] && state == "active") print id "\034" ends "\034" by "\034" ms
    }
    { s = s $0 " " }
    END {
      n = length(s)
      for (i = 1; i <= n; i++) {
        c = substr(s, i, 1)
        if (q) {
          if (c == "\\") {
            i++
            e = substr(s, i, 1)
            t = t ((e == "\"" || e == "\\" || e == "/") ? e : "\\" e)
          } else if (c == "\"") {
            q = 0
            if (obj[d] && want) { key[d] = t; want = 0 } else value(t)
          } else t = t c
        } else if (c == "\"") { q = 1; t = "" }
        else if (c == "{" || c == "[") { d++; obj[d] = (c == "{"); key[d] = ""; want = obj[d]; opened() }
        else if (c == "}" || c == "]") { closed(); d--; want = 0 }
        else if (c == ",") want = obj[d]
        else if (c == ":") want = 0
        else if (c ~ /[-+.0-9a-zA-Z]/) {
          t = c
          while (i < n && substr(s, i + 1, 1) ~ /[-+.0-9a-zA-Z]/) { i++; t = t substr(s, i, 1) }
          value(t)
        }
      }
    }'
}

matcher_admits() { # operator, pattern, label value -> true when the matcher matches that value (absent label: "")
  local re="^($2)$"
  case $1 in
    '=') [[ $3 == "$2" ]] ;;
    '!=') [[ $3 != "$2" ]] ;;
    '=~') [[ $3 =~ $re ]] ;;
    '!~') ! [[ $3 =~ $re ]] ;;
    *) return 1 ;;
  esac
}

# The page alerts a silence holds back. NodeNotAdvancing, ValidatorNotSigning and FibreDown
# carry only alertname and severity, so each other matcher must match an absent label. DiskLow
# also carries host, mountpoint and the node_exporter labels, whose values are not known here:
# it counts when the silence names alertname or severity, so a host silence for planned work
# is listed only when it runs long.
silence_pages() { # matchers as silences_active prints them -> page alert names, comma-separated
  local -a ms=()
  local a m n o v hit named=false out=""
  IFS=$'\036' read -r -a ms <<<"$1"
  for m in "${ms[@]}"; do
    n=${m%%$'\037'*}
    if [[ $n == alertname || $n == severity ]]; then named=true; fi
  done
  for a in "${PAGE_ALERTS[@]}"; do
    if [[ $a == DiskLow && $named == false ]]; then continue; fi
    hit=true
    for m in "${ms[@]}"; do
      IFS=$'\037' read -r n o v <<<"$m"
      case $n in
        alertname) matcher_admits "$o" "$v" "$a" || hit=false ;;
        severity) matcher_admits "$o" "$v" page || hit=false ;;
        *) [[ $a == DiskLow ]] || matcher_admits "$o" "$v" "" || hit=false ;;
      esac
    done
    if [[ $hit == true ]]; then out+="${out:+, }$a"; fi
  done
  printf '%s' "$out"
}

silence_text() { # matchers as silences_active prints them -> {name="value", ...}, as amtool shows them
  local -a ms=() parts=()
  local m n o v
  IFS=$'\036' read -r -a ms <<<"$1"
  for m in "${ms[@]}"; do
    IFS=$'\037' read -r n o v <<<"$m"
    parts+=("$n$o\"$v\"")
  done
  printf '{%s}' "$(join "${parts[@]}")"
}

# Active silences, on the monitor. A silence holds back every alert it matches until it ends,
# and the dead-man heartbeat stays green meanwhile, so a mistyped matcher or a forgotten end
# hides the next outage. Warns on a silence that holds back a page alert or runs for more than
# 4 hours more, with its matchers, author and end. Reads GET /api/v2/silences; changes nothing.
check_silences() {
  local name="silences" am=${C[alertmanager_url]%/} reply code now id ends by ms end why t amtool
  local -a held=() other=()
  if ! has_role monitor; then report SKIP 21 "$name" "not the monitor host"; return; fi
  reply=$(mon_curl "$am/api/v2/silences" -sS -g --max-time 5 -w '\n%{http_code}' 2>/dev/null)
  code=${reply##*$'\n'}
  reply=${reply%$'\n'*}
  if [[ $code != 200 ]]; then
    report SKIP 21 "$name" "Alertmanager at $am answers $(answer "$code") for its silences (check 11)"
    return
  fi
  now=$(date +%s)
  while IFS=$'\034' read -r id ends by ms; do
    [[ -n $id ]] || continue
    t="$(silence_text "$ms") by ${by:-(no author)} until $ends"
    why=$(silence_pages "$ms")
    if [[ -n $why ]]; then why="holds back $why"; fi
    # Alertmanager writes the end in UTC, such as 2026-10-17T10:00:00.000Z.
    end=""
    if [[ $ends =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]+)?Z$ ]]; then
      end=$(date -u -d "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}" +%s 2>/dev/null)
    fi
    if [[ $end =~ ^[0-9]+$ ]] && ((end - now > 14400)); then
      why+="${why:+; }ends in $(( (end - now) / 3600 )) hours"
    fi
    if [[ -n $why ]]; then held+=("$t (ID $id: $why)"); else other+=("$t"); fi
  done < <(silences_active <<<"$reply")
  if ((${#held[@]} > 0)); then
    amtool=$(compgen -G '/opt/celestia-mon/alertmanager-*/amtool' | head -n 1)
    t="${#held[@]} of $((${#held[@]} + ${#other[@]})) active silences hold back a page alert or end more than 4 hours from now: $(join "${held[@]}")"
    if ((${#other[@]} > 0)); then t+="; the others: $(join "${other[@]}")"; fi
    report WARN 21 "$name" "$t. While a silence holds an alert back, nothing is sent for it, and the dead-man heartbeat stays green. Fix: end each one that is not for planned work now, as root: ${amtool:-<amtool>} --alertmanager.url=$am --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml silence expire <ID>; for planned work, silence for as long as the work takes (docs/alerts.md, Before planned restarts and upgrades)"
  elif ((${#other[@]} > 0)); then
    report OK 21 "$name" "${#other[@]} active silences, none on a page alert or ending more than 4 hours from now: $(join "${other[@]}")"
  else
    report OK 21 "$name" "no active silence"
  fi
}

num() { awk -v v="$1" 'BEGIN { printf "%.0f", v }'; }

# The dead-man heartbeat, on the monitor, from Alertmanager's own metrics in Prometheus
# (receiver_name, from --enable-feature=receiver-name-in-metrics; Alertmanager creates the
# series at start). While the Watchdog fires, the dead-man receiver sends every 1-2 minutes,
# so it must have sent in the last 10 minutes, and none of those sends may have failed.
check_deadman() {
  local name="dead-man" sent failed age
  if ! has_role monitor; then report SKIP 22 "$name" "not the monitor host"; return; fi
  if ((!PROM_OK)); then report SKIP 22 "$name" "Prometheus not answering (check 11)"; return; fi
  if [[ $(prom_value 'max(up{job="alertmanager"})') != 1 ]]; then
    report SKIP 22 "$name" "Prometheus has no Alertmanager target that is up (check 11)"
    return
  fi
  sent=$(prom_value 'sum(increase(alertmanager_notifications_total{job="alertmanager",receiver_name="deadman"}[10m]))')
  failed=$(prom_value 'sum(increase(alertmanager_notifications_failed_total{job="alertmanager",receiver_name="deadman"}[10m]))')
  if [[ -z $sent ]]; then
    report WARN 22 "$name" "Prometheus has no notification metrics of Alertmanager's dead-man receiver (alertmanager_notifications_total{receiver_name=\"deadman\"}), so doctor cannot tell whether heartbeats go out. Fix: re-run install.sh on this host: it writes the dead-man receiver from celestia_mon_deadman_webhook_url and starts Alertmanager with --enable-feature=receiver-name-in-metrics"
  elif awk -v f="$failed" 'BEGIN { exit !(f + 0 > 0) }'; then
    report FAIL 22 "$name" "heartbeats to the dead-man service failed in the last 10 minutes (about $(num "$failed") of $(num "$sent"); DeadmanFailing sends a message too). Until one gets through, the dead-man service cannot tell you that monitoring is down. Fix: read the error in \"journalctl -u celestia-mon-alertmanager -n 100\", check celestia_mon_deadman_webhook_url in the monitor's vault and that this host reaches the service, then re-run install.sh on this host"
  elif awk -v s="$sent" 'BEGIN { exit !(s + 0 > 0) }'; then
    report OK 22 "$name" "about $(num "$sent") heartbeats sent to the dead-man service in the last 10 minutes, none failed"
  else
    age=$(prom_value 'time() - max(process_start_time_seconds{job=~"prometheus|alertmanager"})')
    age=${age%%.*}
    if [[ $age =~ ^[0-9]+$ ]] && ((age < 600)); then
      report "NOT YET" 22 "$name" "no heartbeat sent yet: Prometheus or Alertmanager started ${age}s ago; the first heartbeat follows the Watchdog's first evaluation, then one every 1-2 minutes"
    else
      report WARN 22 "$name" "no heartbeat sent to the dead-man service in the last 10 minutes, so it alerts you once its period runs out: the Watchdog stops for an hour after a page or message notification failed (NotificationsFailing), or it does not reach the dead-man receiver. Fix: look for Watchdog and NotificationsFailing on Prometheus's alerts page (${C[prometheus_url]%/}/alerts, through an SSH tunnel), and read \"journalctl -u celestia-mon-alertmanager -n 100\""
    fi
  fi
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
if node_host; then
  find_node
  read_status
fi
if has_role monitor; then api_auth_load; fi

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
check_fibre_signer
check_node_lag
check_fibre_active
check_signer_binds
check_silences
check_deadman

if ((failed)); then
  echo "Some checks failed. celestia-mon-doctor changed nothing; apply the fixes above yourself."
  exit 1
fi
exit 0
