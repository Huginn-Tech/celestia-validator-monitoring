#!/usr/bin/env bash
# celestia-mon-doctor: read-only checks for the hosts of a Celestia validator.
#
# It changes nothing on the host. Each check prints one line:
#   OK    the check passed
#   FAIL  the check failed; the line ends with the fix to apply
#   WARN  worth fixing, but not counted as a failure
#   SKIP  the check does not apply on this host, or cannot run yet
# Exit code: 0 when no check failed, 1 when one did, 2 on a usage error.
#
# Settings are read from /etc/celestia-mon/doctor.conf (key=value lines, written by the
# celestia-mon playbook) and can be given or overridden as flags: "--node-rpc-url URL"
# sets node_rpc_url. Without a config file the defaults below apply and the
# host's roles are guessed from its systemd units. Run it as root
# (sudo celestia-mon-doctor) so it can read the node's config and see which process
# owns each listening socket.
#
# celestia-appd (checks 4 and 5) runs as the node's service user. A
# multiplexer build of it unpacks any missing embedded binaries into
# <node home>/bin, as the node itself does at every start.
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
  [signer_median_max]="0.010"
  [disk_min_free_ratio]="0.10"
  [time_offset_max]="0.1"
  [head_wait]="30"
  [private_address_regex]='^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|127\.|::1$|f[cd][0-9a-fA-F]{2}:)'
)

usage() {
  cat <<'EOF'
Usage: celestia-mon-doctor [--config FILE] [--KEY VALUE ...]

Read-only checks for a Celestia validator, Fibre or KMS host. Settings come
from /etc/celestia-mon/doctor.conf (KEY=VALUE lines) and can be overridden with flags
(--node-rpc-url URL sets node_rpc_url).

  roles                  comma-separated: validator, fibre, kms, monitor
                         (default: guessed from the units on this host)
  signer                 local or remote (default: not compared)
  fibre_enabled          true or false (default: true if the Fibre unit exists)
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
  printf '%-4s %2s  %-14s  %s\n' "$1" "$2" "$3" "$4"
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
http_get() { curl -fsS -g --max-time 5 "$1" 2>/dev/null; }
url_hostport() {
  local u=${1#*://}
  printf '%s' "${u%%/*}"
}
url_port() {
  local hp
  hp=$(url_hostport "$1")
  printf '%s' "${hp##*:}"
}

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

addr_class() { # local address as ss prints it -> wildcard, private or public
  local h=${1%:*}
  h=${h%%\%*}
  h=${h#\[}
  h=${h%\]}
  h=${h#::ffff:}
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
  local name="node metrics" url=${C[node_metrics_url]} cfg prom="" laddr="" ns="" note="" a
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
  while read -r a; do
    [[ -z $a || $(addr_class "$a") == private ]] || bad+=("$a")
  done <<<"$(listeners "$(url_port "$url")")"
  if ((${#bad[@]} > 0)); then
    report WARN 1 "$name" "enabled at $url, but the port listens on $(join "${bad[@]}"), which is not a private address. Fix: set prometheus_listen_addr = \"$(url_hostport "$url")\" under [instrumentation] in $cfg (or block the port in a firewall), then restart celestia-appd at a moment you choose"
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
      report FAIL 2 "$name" "priv_validator_laddr is empty in $cfg, but celestia-mon is set to signer: remote. Fix: set celestia_mon_signer: local in the inventory and re-run the playbook (or finish the remote signer setup)"
    else
      report SKIP 2 "$name" "local key (priv_validator_laddr is empty)"
    fi
    return
  fi
  if [[ $signer == local ]]; then
    report FAIL 2 "$name" "priv_validator_laddr = $laddr, but celestia-mon is set to signer: local. Fix: set celestia_mon_signer: remote in the inventory and re-run the playbook"
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

check_head() {
  local name="node at head" rpc=${C[node_rpc_url]%/} limit=${C[head_wait]} h0 h1 catching end
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
    report OK 3 "$name" "height $h0 -> $h1, catching_up=false"
  else
    report FAIL 3 "$name" "height stayed at $h0 for ${limit}s. Fix: check status.celestia.org and an explorer first; if the whole chain stopped, coordinate with other validators instead of fixing locally. Otherwise check peers and \"journalctl -u ${C[appd_unit]} -n 100\" (a node often stops at an upgrade height)"
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
  local name="fibre otlp" unit=${C[fibre_unit]} want=${C[fibre_otel_endpoint]%/} got src="" unread="" port
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
    report FAIL 7 "$name" "no OTLP endpoint set$unread, so Fibre sends no metrics. Fix: add \"--otel-endpoint $want\" to ExecStart in $unit.service (or Environment=FIBRE_OTEL_ENDPOINT=$want), run \"systemctl daemon-reload\", then restart Fibre at a moment you choose"
    return
  fi
  if [[ $got != "$want" && $got != "${want/127.0.0.1/localhost}" && $got != "${want/localhost/127.0.0.1}" ]]; then
    report FAIL 7 "$name" "$src is $got, not the local collector $want. Fix: set it to $want (the base URL, without /v1/metrics), run \"systemctl daemon-reload\", then restart Fibre at a moment you choose"
    return
  fi
  port=$(url_port "$want")
  if [[ -z $(listeners "$port") ]]; then
    report FAIL 7 "$name" "$src is $got, but nothing listens on port $port. Fix: re-run the celestia-mon playbook so the collector (celestia-mon-otelcol) is installed and running, then read \"journalctl -u celestia-mon-otelcol -n 100\""
    return
  fi
  report OK 7 "$name" "$src is $got"
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
# Grafana or collector of its own) is left alone.
CELESTIA_MON_UNITS=(celestia-mon-node-exporter celestia-mon-otelcol celestia-mon-prometheus celestia-mon-alertmanager celestia-mon-grafana)

check_binds() {
  local name="private binds" fw unit pid addr
  local -a ok=() bad=()
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
    if [[ $(addr_class "$addr") == private ]]; then ok+=("$unit $addr"); else bad+=("$unit $addr"); fi
  done < <(ss -Htlnp 2>/dev/null | awk '{ s = $0; while (match(s, /pid=[0-9]+/)) { print substr(s, RSTART + 4, RLENGTH - 4), $4; s = substr(s, RSTART + RLENGTH) } }' | sort -u)
  if ((${#bad[@]} > 0)); then
    report FAIL 8 "$name" "$(join "${bad[@]}") not on a private address; firewall: $fw. Fix: set celestia_mon_private_ip for this host to its private address and re-run the celestia-mon playbook"
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
  C[roles]=$(IFS=,; printf '%s' "${roles[*]}")
fi
if [[ -z ${C[fibre_enabled]} ]]; then
  if unit_loaded "${C[fibre_unit]}"; then C[fibre_enabled]=true; else C[fibre_enabled]=false; fi
fi

printf 'celestia-mon-doctor on %s (roles: %s)\n' "${HOSTNAME:-$(uname -n)}" "${C[roles]:-none}"
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

if ((failed)); then
  echo "Some checks failed. celestia-mon-doctor changed nothing; apply the fixes above yourself."
  exit 1
fi
exit 0
