#!/usr/bin/env bash
# celestia-mon-doctor: read-only checks for the hosts of a Celestia validator.
#
# It changes nothing on the host. Each check prints a line (check 12 one per group; check 10
# also one per md RAID array; check 23 a second one for the governance votes):
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
# Prometheus and Alertmanager listen on 127.0.0.1 only, so checks 11 to 13, 18, 19
# and 21 to 24 run on the monitor host and ask them there, with the API password
# that only root can read (api_password_file). Check 24 also asks
# celestia-mon-blackbox there (blackbox_url). Numbers 15 and 16 are not used: each
# check keeps its number, which the docs cite.
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
# Checks 1, 8, 20 and 25 read where each port listens (ss), the connections to it now, whether
# the host has a Tailscale interface (ip) and, while ufw is enabled, its rules (ufw status, as
# root); doctor never connects to those ports and never changes a firewall rule. Checks 17 and
# 27 read which node Fibre signs through and reads, and name it from node_addresses.
#
# With --survey it runs no check: it reads what the host runs and prints the host's inventory
# groups, its entry for hosts.yml and the steps on the Celestia side (see survey below).
#
# Needs only bash, coreutils, util-linux (runuser, setsid, findmnt), curl, ss and ip
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
  [node_addresses]=""
  [fibre_addresses]=""
  [prometheus_url]="http://127.0.0.1:9912"
  [alertmanager_url]="http://127.0.0.1:9913"
  [blackbox_url]="http://127.0.0.1:9919"
  [api_user]="celestia-mon"
  [api_password_file]="/etc/celestia-mon/api-password"
  [gov_page]=""
  [signer_median_max]="0.010"
  [disk_min_free_ratio]="0.10"
  [node_behind_blocks]="20"
  [time_offset_max]="0.1"
  [mdstat]="/proc/mdstat"
  [head_wait]="30"
  [private_address_regex]='^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|127\.|::1$|f[cd][0-9a-fA-F]{2}:)'
)

usage() {
  cat <<'EOF'
Usage: celestia-mon-doctor [--config FILE] [--KEY VALUE ...]
       celestia-mon-doctor --survey [--KEY VALUE ...]

Read-only checks for a Celestia validator, backup node, Fibre, KMS or monitor
host. Settings come from /etc/celestia-mon/doctor.conf (KEY=VALUE lines) and
can be overridden with flags (--node-rpc-url URL sets node_rpc_url).

--survey runs no check: it finds the celestia-appd, Fibre and tmkms units of
this host by what they run, whatever their names, and prints the inventory
groups of this host, its entry for hosts.yml and the steps on the Celestia
side that are yours. It changes and writes nothing, and reads doctor.conf only
when --config names it.

  roles                  comma-separated: validator, backup_node, fibre, kms,
                         monitor (default: guessed from the units on this
                         host; a backup node needs --roles backup_node)
  inventory_host         this host's name in the inventory, to find its own
                         entry in fibre_public_endpoint, node_addresses and
                         fibre_addresses
  signer                local or remote; with local, no remote signer metrics
                         are expected (default: not set, not compared)
  fibre_enabled         true or false; with false, no Fibre or collector
                         metrics are expected (default: true if the Fibre
                         unit exists)
  appd_unit, fibre_unit  systemd unit names (default: celestia-appd, fibre)
  node_rpc_url           default http://127.0.0.1:26657; a loopback URL
                         follows [rpc] laddr in the node's config.toml when
                         that names another address of the host
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
  node_addresses         comma-separated NAME=address, the private address of
                         each celestia-appd node of the inventory: checks 17
                         and 27 name the node Fibre signs through and reads
                         (default: none, nothing named)
  fibre_addresses        the same for each Fibre host: check 25 names those
                         without a node of their own in its ufw command for
                         the app gRPC (default: none)
  prometheus_url         celestia-mon's Prometheus, on the monitor host
                         (default http://127.0.0.1:9912)
  alertmanager_url       celestia-mon's Alertmanager, on the monitor host
                         (default http://127.0.0.1:9913)
  blackbox_url           celestia-mon-blackbox, which asks the Fibre hosts for
                         their readiness, on the monitor host (default
                         http://127.0.0.1:9919)
  api_user, api_password_file  HTTP basic auth for those three (default
                         celestia-mon and /etc/celestia-mon/api-password,
                         readable by root; none when the file is missing)
  gov_page               true when GovVoteMissing pages (celestia_mon_gov_page),
                         so check 23 says when the page comes (default: not
                         set)
  signer_median_max      seconds (default 0.010)
  disk_min_free_ratio    default 0.10
  node_behind_blocks     blocks a node may trail the highest node (default 20)
  time_offset_max        seconds (default 0.1)
  mdstat                 the md software RAID status (default /proc/mdstat)
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
    # Settings that earlier installs wrote and doctor no longer reads, so their file still loads.
    case $key in signer_multi_node | prometheus_data_dir | prometheus_retention_size) continue ;; esac
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
  awk -v sec="$2" -v key="$3" -v q="'" '
    /^[[:space:]]*\[/ { s = $0; gsub(/^[[:space:]]*\[+|\]+[[:space:]]*(#.*)?$/, "", s); next }
    s == sec && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      v = $0
      sub(/^[^=]*=[[:space:]]*/, "", v)
      sub(/[[:space:]]*(#.*)?$/, "", v)
      # A TOML literal string stands in single quotes (q), a basic string in double ones.
      if (length(v) > 1 && substr(v, 1, 1) == q && substr(v, length(v), 1) == q) v = substr(v, 2, length(v) - 2)
      else gsub(/"/, "", v)
      print v
      exit
    }' "$1"
}

toml_has() { # file, section ("" = top level), key -> true when the file sets the key, also to ""
  awk -v sec="$2" -v key="$3" '
    /^[[:space:]]*\[/ { s = $0; gsub(/^[[:space:]]*\[+|\]+[[:space:]]*(#.*)?$/, "", s); next }
    s == sec && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" { f = 1; exit }
    END { exit !f }' "$1"
}

joins() { # separator, items -> the items joined by the separator
  local sep=$1 out="" x
  shift
  for x in "$@"; do out+="${out:+$sep}$x"; done
  printf '%s' "$out"
}

# node_addresses and fibre_addresses, which install.sh writes from the inventory: NAME=address
# of each celestia-appd node and of each Fibre host, its celestia_mon_private_ip.
addr_pairs() { # NAME=address list -> "NAME address" lines, one per entry
  local -a e=()
  local x
  IFS=',' read -r -a e <<<"$1"
  for x in "${e[@]}"; do
    x=${x//[[:space:]]/}
    if [[ $x == ?*=?* ]]; then printf '%s %s\n' "${x%%=*}" "${x#*=}"; fi
  done
}
node_named() { # address without port or brackets -> true and the inventory name of the node at that address
  local n a
  while read -r n a; do
    if [[ ${a,,} == "${1,,}" ]]; then
      printf '%s' "$n"
      return 0
    fi
  done < <(addr_pairs "${C[node_addresses]}")
  return 1
}
node_list() { # -> the nodes of node_addresses for a report line, such as "val-1 at 10.0.0.1, node-1 at 10.0.0.5"
  addr_pairs "${C[node_addresses]}" | awk '{ printf "%s%s at %s", (NR > 1 ? ", " : ""), $1, $2 }'
}
own_address() { # -> this host's address in fibre_addresses or node_addresses (inventory_host), else a placeholder
  local n a
  while read -r n a; do
    if [[ -n ${C[inventory_host]} && $n == "${C[inventory_host]}" ]]; then
      printf '%s' "$a"
      return
    fi
  done < <(addr_pairs "${C[fibre_addresses]}"; addr_pairs "${C[node_addresses]}")
  printf '%s' "<this host's private address>"
}
# The Fibre hosts that have no celestia-appd node of their own, so each reads a node on another
# host: fibre_addresses less this host and the names node_addresses lists, "NAME address" lines.
fibre_readers() {
  local n a nodes
  nodes=$(addr_pairs "${C[node_addresses]}" | cut -d ' ' -f 1)
  while read -r n a; do
    [[ $n != "${C[inventory_host]}" ]] || continue
    if ! grep -qxF -- "$n" <<<"$nodes"; then printf '%s %s\n' "$n" "$a"; fi
  done < <(addr_pairs "${C[fibre_addresses]}")
}

# ------------------------------------------------- who reaches a port

is_tailnet() { # address without port -> true in 100.64.0.0/10, where Tailscale gives its devices addresses
  [[ $1 =~ ^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\. ]]
}

lock_class() { # address without port -> loopback, private, tailnet (100.64.0.0/10), wildcard or public
  case $1 in
    '' | 0.0.0.0 | :: | '*') echo wildcard ;;
    localhost | 127.* | ::1) echo loopback ;;
    *)
      if is_tailnet "$1"; then
        echo tailnet
      elif [[ $1 =~ ${C[private_address_regex]} ]]; then
        echo private
      else
        echo public
      fi
      ;;
  esac
}

# Read once, in the main shell (not in $(...), which would lose them): FW, what firewall_state
# says; TS_IF, the host's Tailscale interface (tailscale0 by default), empty when it has none:
# tailscaled accepts what arrives there before ufw sees it; UFW, `ufw status verbose` while ufw is
# enabled, empty when it cannot be read (doctor not run as root).
FW="" TS_IF="" UFW="" NET_READ=false
net_read() {
  [[ $NET_READ == false ]] || return 0
  NET_READ=true
  FW=$(firewall_state)
  TS_IF=$(ip -o link show 2>/dev/null | awk -F': ' '$2 ~ /^tailscale/ { sub(/@.*/, "", $2); print $2; exit }')
  if [[ $FW == "ufw enabled" ]]; then
    UFW=$(LC_ALL=C ufw status verbose 2>/dev/null)
    if [[ $UFW != "Status: active"* ]]; then UFW=""; fi
  fi
}

# The ufw rules in UFW that let TCP port $1 in, one per line: "<kind> TAB <source> TAB <rule>".
# kind "any" when the rule lets anyone in (Anywhere, a source port only, a range that is not inside
# one private block, or ufw's default incoming policy allow); "range" for a range inside one of
# 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 127.0.0.0/8 or fc00::/7, such as the
# install writes for a range in celestia_mon_firewall_sources: every host in it gets in, but not
# anyone; else "one". `ufw status verbose` (get_status in ufw's backend_iptables.py) prints a
# rule as "<to> <ACTION IN> <from>[ (log)][ # comment]". <to> is
# "[<address> ]<ports>[/proto][ (<app profile>[ (v6)])][ (v6)]", or with no ports
# "Anywhere[/proto][ (v6)]" or "<address>[/proto]", then "[ on <interface>]": the IPv6 copy of
# "ufw allow OpenSSH" reads "22/tcp (OpenSSH (v6))", "ufw allow in on lo" reads "Anywhere on lo".
# <from> is "Anywhere[/proto][ (v6)]", "<address>[/proto]" or "[<address> ]<ports>[/proto]": a
# rule with no ports on <to> may print its protocol only there. A rule without ports covers every
# port. Rules on lo are left out, as only this host sends there; other interfaces stay in the
# rule text. DENY, REJECT, outgoing and routed rules are left out too.
ufw_allows() {
  [[ -n $UFW ]] || return 0
  awk -v p="$1" '
    function covers(spec,   n, parts, i, a) {
      n = split(spec, parts, ",")
      for (i = 1; i <= n; i++) {
        if (split(parts[i], a, ":") == 2) {
          if (p + 0 >= a[1] + 0 && p + 0 <= a[2] + 0) return 1
        } else if (parts[i] + 0 == p + 0) return 1
      }
      return 0
    }
    # The protocol after the last "/" of "22/tcp" or "10.0.0.4/tcp"; empty for "10.0.0.0/24".
    function proto_of(s,   v) {
      if (s !~ /\/[a-z][a-z0-9]*$/) return ""
      v = s
      sub(/^.*\//, "", v)
      return v
    }
    # True for an "address/prefix" range inside one private block.
    function private_range(s,   n) {
      n = substr(s, index(s, "/") + 1) + 0
      if (s ~ /^(10|127)\./) return n >= 8
      if (s ~ /^172\.(1[6-9]|2[0-9]|3[01])\./) return n >= 12
      if (s ~ /^192\.168\./) return n >= 16
      if (s ~ /^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\./) return n >= 10
      return tolower(s) ~ /^f[cd][0-9a-f][0-9a-f]:/ && n >= 7
    }
    BEGIN { portre = "^[0-9]+(:[0-9]+)?(,[0-9]+(:[0-9]+)?)*(/[a-z][a-z0-9]*)?$" }
    /^Default:/ { if ($0 ~ /allow \(incoming\)/) printf "any\tAnywhere\tufw default: allow (incoming)\n"; next }
    /^--/ { t = 1; next }
    !t || !match($0, / (ALLOW|LIMIT|DENY|REJECT)( (IN|OUT|FWD))? /) { next }
    {
      to = substr($0, 1, RSTART - 1)
      act = substr($0, RSTART + 1, RLENGTH - 2)
      from = substr($0, RSTART + RLENGTH)
      if (act !~ /^(ALLOW|LIMIT)( IN)?$/) next
      sub(/ # .*$/, "", from)
      sub(/ +\(log(-all)?\)$/, "", from)
      gsub(/^ +| +$/, "", to)
      gsub(/^ +| +$/, "", from)
      # Every " (v6)" first, the one inside an app profile too, then the profile, then the interface.
      x = to
      gsub(/ \(v6\)/, "", x)
      gsub(/ \([^)]*\)/, "", x)
      if (x ~ / on lo$/) next
      sub(/ on [^ ]+$/, "", x)
      n = split(x, w, " ")
      last = w[n]
      proto = proto_of(last)
      if (last ~ portre) {
        ports = last
        sub(/\/.*$/, "", ports)
        if (!covers(ports)) next
      }
      split(from, f, " ")
      src = f[1]
      if (proto == "") proto = proto_of(src)
      if (proto == "" && f[2] ~ portre) proto = proto_of(f[2])
      if (proto != "" && proto != "tcp") next
      if (src !~ portre) sub(/\/[a-z][a-z0-9]*$/, "", src)
      kind = "one"
      if (src == "Anywhere" || src ~ portre) {
        kind = "any"
        src = "Anywhere"
      } else if (src ~ /\/[0-9]+$/ && src !~ /\/(32|128)$/) kind = private_range(src) ? "range" : "any"
      printf "%s\t%s\t%s %s %s\n", kind, src, to, act, from
    }' <<<"$UFW"
}

ufw_open() { # TCP port -> the ufw rules that let anyone reach it, comma-separated, empty when none
  ufw_allows "$1" | awk -F '\t' '$1 == "any" { printf "%s%s", (n++ ? ", " : ""), $3 }'
}

port_peers() { # port -> the remote addresses (not loopback) connected to this host's port now, comma-separated
  local a h out=""
  while read -r a; do
    h=$(addr_host "$a")
    [[ -n $h && $h != 127.* && $h != ::1 && ", $out, " != *", $h, "* ]] || continue
    out+="${out:+, }$h"
  done < <(ss -Htn 2>/dev/null | awk -v p=":$1" '$1 == "ESTAB" && length($4) > length(p) && substr($4, length($4) - length(p) + 1) == p { print $5 }')
  printf '%s' "$out"
}

# Where one of the node's ports listens and who can connect to it, from the listening sockets (the
# setting itself while nothing listens), the connections now and ufw's rules; doctor never
# connects to the port. Every port of a node but p2p takes one source. On every interface or a
# public address the port fails with no firewall active or a Tailscale interface on the host
# (tailscaled accepts tailnet traffic before ufw sees it), and warns otherwise. On a Tailscale
# address (100.64.0.0/10) it warns: only the tailnet policy filters it. On a private address it
# warns with no firewall active. Beyond loopback it also warns when a ufw rule lets anyone, or
# every host of a range, reach it. A value without a port doctor can read warns: doctor cannot
# tell where it listens. Arguments: the setting as the report names it (such as "[grpc]
# address"), its value, its key, its section ("" = top level), its file, the value to set (with
# "<private address>"), who may connect, the placeholder for that source in the ufw command (or
# the sources, comma-separated, one ufw command each; empty when no other host needs the port, so
# the value to set is the fix), and optional words for the report line after where it listens
# (such as ", without mTLS"). Sets:
#   LOCK_SEV   OK, WARN or FAIL
#   LOCK_OPEN  wildcard or public when the port listens there, else empty
#   LOCK_TEXT  what doctor found, for the report line
#   LOCK_BIND  the change of the setting, when its address is the problem
#   LOCK_UFW   the ufw commands, when the check is not OK
LOCK_SEV="" LOCK_OPEN="" LOCK_TEXT="" LOCK_BIND="" LOCK_UFW=""
port_lock() {
  local label=$1 value=$2 key=$3 sec=$4 file=$5 want=$6 who=$7 src=$8 after=${9:-}
  local hp h port l where="" classes="" peers kind s rule del="" cmds=""
  local -a anyrules=() ranges=() ones=() srcs=()
  LOCK_SEV=OK LOCK_OPEN="" LOCK_BIND="" LOCK_UFW=""
  LOCK_TEXT="$label = $value"
  if [[ $value == unix://* ]]; then
    LOCK_TEXT+=" (a unix socket)$after"
    return
  fi
  hp=${value#*://}
  port=${hp##*:}
  h=${hp%:*}
  h=${h#\[}
  h=${h%\]}
  if [[ $hp != *:* || ! $port =~ ^[0-9]+$ ]]; then
    LOCK_SEV=WARN
    LOCK_TEXT+="$after: doctor reads no port in it, so it cannot tell where it listens or who reaches it"
    LOCK_BIND="in $file${sec:+ under [$sec]}, write $key with its port (<address>:<port>, after tcp:// where the setting has it)"
    return
  fi
  while read -r l; do
    [[ -n $l ]] || continue
    where+="${where:+, }$l"
    classes+=" $(lock_class "$(addr_host "$l")")"
  done <<<"$(listeners "$port")"
  if [[ -z $where ]]; then
    where="nothing listens on it now"
    classes=" $(lock_class "$h")"
  fi
  LOCK_TEXT+=" ($where)$after"
  # On loopback only, no other host reaches it.
  if [[ $classes != *wildcard* && $classes != *public* && $classes != *tailnet* && $classes != *private* ]]; then return; fi
  net_read
  if [[ $classes == *wildcard* || $classes == *public* ]]; then
    if [[ $classes == *wildcard* ]]; then
      LOCK_OPEN=wildcard
      LOCK_TEXT+=" is on every interface"
    else
      LOCK_OPEN=public
      LOCK_TEXT+=" is on a public address"
    fi
    if [[ $FW == none* ]]; then
      LOCK_SEV=FAIL
      LOCK_TEXT+=", and no firewall is active"
    elif [[ -n $TS_IF ]]; then
      LOCK_SEV=FAIL
      LOCK_TEXT+=", and this host has a Tailscale interface ($TS_IF), whose traffic ufw does not filter"
    else
      LOCK_SEV=WARN
      LOCK_TEXT+=" (firewall: $FW)"
    fi
    LOCK_BIND="in $file${sec:+ under [$sec]}, set $key = $want"
  elif [[ $classes == *tailnet* ]]; then
    LOCK_SEV=WARN
    LOCK_TEXT+=" is on a Tailscale address: ufw does not filter tailnet traffic, so only the tailnet policy decides who connects"
    LOCK_BIND="in $file${sec:+ under [$sec]}, set $key = $want, on a VLAN or WireGuard address (or grant the port to $who only in the tailnet policy)"
  elif [[ $FW == none* ]]; then
    LOCK_SEV=WARN
    LOCK_TEXT+=": no firewall is active, so every host that reaches that private address can connect"
  fi
  if [[ -n $UFW ]]; then
    while IFS=$'\t' read -r kind s rule; do
      if [[ $kind == any ]]; then
        anyrules+=("$rule")
      elif [[ $kind == range ]]; then
        if [[ " ${ranges[*]} " != *" $s "* ]]; then ranges+=("$s"); fi
      elif [[ -n $s && " ${ones[*]} " != *" $s "* ]]; then
        ones+=("$s")
      fi
    done < <(ufw_allows "$port")
    if ((${#anyrules[@]} > 0)); then
      [[ $LOCK_SEV == FAIL ]] || LOCK_SEV=WARN
      LOCK_TEXT+="; ufw lets anyone reach it ($(join "${anyrules[@]}"))"
      del=", then delete the rule that lets anyone in (\"sudo ufw status numbered\", then \"sudo ufw delete <number>\")"
    elif ((${#ranges[@]} > 0)); then
      # A private range is not anyone, but it is more than the one source the port takes.
      [[ $LOCK_SEV == FAIL ]] || LOCK_SEV=WARN
      LOCK_TEXT+="; ufw lets every host in $(join "${ranges[@]}") reach it"
      del=", then delete the rule for the range (\"sudo ufw status numbered\", then \"sudo ufw delete <number>\")"
    elif ((${#ones[@]} > 0)); then
      LOCK_TEXT+="; ufw lets in $(join "${ones[@]}")"
    else
      LOCK_TEXT+="; no ufw rule lets another host reach it"
    fi
  elif [[ $FW == "ufw enabled" ]]; then
    LOCK_TEXT+="; ufw's rules not read (run as root)"
  elif [[ $FW != none* && -z $LOCK_OPEN ]]; then
    LOCK_TEXT+="; firewall: $FW, whose rules doctor does not read"
  fi
  peers=$(port_peers "$port")
  if [[ -n $peers ]]; then LOCK_TEXT+="; connected now from $peers"; fi
  if [[ $LOCK_SEV != OK && -z $src ]]; then
    LOCK_BIND="in $file${sec:+ under [$sec]}, set $key = $want"
    LOCK_UFW=${del#, then }
  elif [[ $LOCK_SEV != OK ]]; then
    IFS=',' read -r -a srcs <<<"$src"
    for s in "${srcs[@]}"; do cmds+="${cmds:+ and }\"sudo ufw allow from $s to any port $port proto tcp\""; done
    LOCK_UFW="let only $who reach port $port: $cmds$del"
  fi
}

# The end of the fix of checks 20 and 25: the setting changes (then a restart of celestia-appd)
# and the ufw commands, each list joined by "; ".
lock_fix() { # restart words, setting changes (one string, "; "-joined), ufw commands (the same)
  local out=""
  if [[ -n $2 ]]; then out="$2, then restart celestia-appd at a moment you choose$1"; fi
  if [[ -n $3 ]]; then
    out+="${out:+; }$3 (look with \"sudo ss -tnp\" first: the source must be the address that host really connects from)"
    case $FW in
      none*) out+="; ufw is not active: add your SSH port, p2p (26656), your WireGuard UDP port for each peer when the hosts link over WireGuard (\"sudo ufw allow from <peer's public IP> to any port <WireGuard port> proto udp\") and the other rules of docs/security.md (\"Ports on the validator\") first, then turn it on yourself (\"sudo ufw enable\") and run install.sh again, which adds the monitor's rules" ;;
      "ufw enabled" | "") ;;
      *) out+="; those are ufw's commands: write the same rules for ${FW% active}" ;;
    esac
  fi
  printf '%s' "$out"
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
  local port open="" only del="" seen=false reach=false
  local -a bad=() tnet=()
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
  port=$(url_port "$url")
  while read -r a; do
    [[ -n $a ]] || continue
    seen=true
    if ! is_loopback "$a"; then reach=true; fi
    case $(addr_class "$a") in
      wildcard) [[ -n $wild ]] || wild="the port listens on $a" ;;
      public) bad+=("$a") ;;
      private) if is_tailnet "$(addr_host "$a")"; then tnet+=("$a"); fi ;;
    esac
  done <<<"$(listeners "$port")"
  # With nothing listening on the port here, prometheus_listen_addr says where it would (unknown
  # or an empty host: every interface).
  if [[ $seen == false && $(lock_class "$lhost") != loopback ]]; then reach=true; fi
  # With no firewall active, nothing but the bind keeps the port off the networks of this host,
  # and tailscaled accepts what arrives on its interface before ufw sees it.
  net_read
  fw=$FW
  if [[ -n $TS_IF ]]; then fw+="; Tailscale interface $TS_IF"; fi
  if [[ $FW == none* || -n $TS_IF ]]; then sev=FAIL; fi
  # A ufw rule matters only where another host can reach the port, never on loopback alone.
  if [[ $reach == true || -n $wild ]]; then open=$(ufw_open "$port"); fi
  only="; let only the monitor reach port $port: \"sudo ufw allow from <monitor address> to any port $port proto tcp\" (install.sh adds this rule while ufw is active)"
  if [[ -n $open ]]; then
    del="; delete the ufw rule that lets anyone in (\"sudo ufw status numbered\", then \"sudo ufw delete <number>\"): install.sh lets the monitor's address in with a rule of its own (comment celestia-mon)"
    open="; ufw lets anyone reach port $port ($open)"
  fi
  if [[ -n $wild ]]; then
    report "$sev" 1 "$name" "enabled at $url, but $wild: celestia-appd serves its metrics on every interface of this host, a Tailscale interface included, which ufw does not filter (firewall: $fw)$open. Whoever reaches the port can read the node's metrics and hold its few connections (max_open_connections, 3 by default), which blinds the signing alerts. Fix: bind it to the private address the monitor scrapes, prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg (never 0.0.0.0 or an empty host), then restart celestia-appd at a moment you choose$only$del; celestia-mon-doctor never edits the config"
  elif ((${#bad[@]} > 0)); then
    report "$sev" 1 "$name" "enabled at $url, but the port listens on $(join "${bad[@]}"), which is not a private address (firewall: $fw)$open. Fix: set prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg, then restart celestia-appd at a moment you choose$only$del"
  elif ((${#tnet[@]} > 0)); then
    report WARN 1 "$name" "enabled at $url$note, but the port listens on $(join "${tnet[@]}"), a Tailscale address: ufw does not filter tailnet traffic, so only the tailnet policy decides who reaches it$open. Fix: grant tcp:$port on this host to the monitor alone in the tailnet policy (docs/security.md, Tailnet policy), or move celestia_mon_private_ip and prometheus_listen_addr to this host's VLAN or WireGuard address$del"
  elif [[ -n $open ]]; then
    report WARN 1 "$name" "enabled at $url$note$open. Only the monitor scrapes it. Fix: ${del#; }"
  else
    report OK 1 "$name" "enabled at $url$note"
  fi
}

signer_median() { # -> the node's median signing latency in seconds from METRICS, empty when absent
  awk '$1 ~ /^(cometbft|celestia)_privval_signing_latency_median_seconds($|[{])/ { print $NF; exit }' <<<"$METRICS"
}

# CometBFT serves its RPC on one address, laddr in the [rpc] section of config.toml. A
# loopback node_rpc_url follows laddr, its port too: a node that serves its RPC on its private
# address only (for celestia-mon-signal on another host) answers nothing on 127.0.0.1, and on a
# host with several nodes another node can answer on the default port.
follow_rpc_laddr() {
  local cfg=$NODE_HOME/config/config.toml laddr
  [[ -r $cfg ]] || return 0
  is_loopback "$(url_hostport "${C[node_rpc_url]}")" || return 0
  laddr=$(toml_get "$cfg" rpc laddr)
  case $laddr in
    tcp://*) laddr=${laddr#tcp://} ;;
    *://*) return 0 ;;
  esac
  [[ $laddr == *:* ]] || return 0
  # An empty host, 0.0.0.0 or :: is every interface, loopback included.
  case $(addr_host "$laddr") in
    '' | 0.0.0.0 | :: | localhost) laddr=127.0.0.1:${laddr##*:} ;;
  esac
  C[node_rpc_url]=http://$laddr
}

home_chain() { # -> chain_id of the node home's genesis.json, empty when unreadable
  local g=$NODE_HOME/config/genesis.json
  [[ -r $g ]] || return 0
  grep -o -m 1 '"chain_id" *: *"[^"]*"' "$g" | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/'
}

STATUS="" STATUS_NETWORK=""
status_value() { # key -> value of "key":"value" or "key":value in the flattened /status reply
  sed -n 's/.*"'"$1"'":"\{0,1\}\([^",}]*\).*/\1/p' <<<"$STATUS" | head -n 1
}

# The node's /status, read once before check 2, which needs the voting power of the
# key the node signs with (validator_info), and check 3, which starts from its height.
read_status() {
  local net chain
  STATUS=$(http_get "${C[node_rpc_url]%/}/status" | tr -d ' \t\r\n')
  [[ $(status_value latest_block_height) =~ ^[0-9]+$ ]] || { STATUS=""; return; }
  # A node of another chain on this host can answer there: its key and height are not this
  # node's, so a reply for another network than the home's genesis is left out.
  net=$(status_value network)
  chain=$(home_chain)
  if [[ -n $net && -n $chain && $net != "$chain" ]]; then STATUS_NETWORK=$net STATUS=""; fi
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

# Where the node's two signer ports listen and who can reach them (port_lock): doctor never
# connects to them. celestia-appd does not authenticate the remote signer that connects to
# priv_validator_laddr, so whoever reaches that port while tmkms reconnects can take the
# signer's place (missed blocks, then jailing): only the KMS host may reach it, and it never
# belongs on a public address. priv_validator_grpc_laddr serves raw-bytes signatures with the
# validator key to Fibre: only the Fibre hosts may reach it, always with mTLS (the three
# priv_validator_grpc_*_file settings, all or none, or celestia-appd refuses to start). Without
# mTLS every account of the host can ask it for signatures, loopback being no boundary between
# them, and celestia-appd refuses it beyond loopback unless priv_validator_grpc_allow_insecure =
# true. celestia-app v10 serves 127.0.0.1:26669 when config.toml lacks the key (a home made
# before v10).
check_signer_binds() {
  local name="signer binds" cfg laddr g gdef="" s sev n f v note="" why="" mtls
  local -a fails=() warns=() oks=() sets=() ufws=()
  if ! node_host; then report SKIP 20 "$name" "not a validator or backup node host"; return; fi
  cfg=$NODE_HOME/config/config.toml
  if [[ ! -r $cfg ]]; then
    report SKIP 20 "$name" "$cfg not readable (run as root or pass --node-home)"
    return
  fi
  laddr=$(toml_get "$cfg" "" priv_validator_laddr)
  g=$(toml_get "$cfg" "" priv_validator_grpc_laddr)
  if [[ -z $g ]] && ! toml_has "$cfg" "" priv_validator_grpc_laddr && [[ -n $(listeners 26669) ]]; then
    g=127.0.0.1:26669
    gdef=", celestia-app's default, as $cfg does not set it"
  fi
  if [[ -n $laddr ]]; then
    port_lock priv_validator_laddr "$laddr" priv_validator_laddr "" "$cfg" \
      "\"tcp://<private address>:${laddr##*:}\" (the VLAN or WireGuard address tmkms dials)" "the KMS host (tmkms)" "<KMS host address>"
    sev=$LOCK_SEV
    if [[ $LOCK_OPEN == public ]]; then sev=FAIL; fi
    if [[ $sev != OK ]]; then
      why+=" celestia-appd does not authenticate the signer that connects to priv_validator_laddr, so whoever reaches it while tmkms reconnects can take tmkms's place (missed blocks, then jailing)."
    fi
    case $sev in
      FAIL) fails+=("$LOCK_TEXT") ;;
      WARN) warns+=("$LOCK_TEXT") ;;
      *) oks+=("$LOCK_TEXT") ;;
    esac
    if [[ -n $LOCK_BIND ]]; then sets+=("$LOCK_BIND"); fi
    if [[ -n $LOCK_UFW ]]; then ufws+=("$LOCK_UFW"); fi
  fi
  if [[ -n $g ]]; then
    n=0
    for f in cert key client_ca; do
      v=$(toml_get "$cfg" "" "priv_validator_grpc_${f}_file")
      if [[ -n $v ]]; then n=$((n + 1)); fi
    done
    v=""
    if ((n == 0)); then v=", without mTLS"; fi
    port_lock priv_validator_grpc_laddr "$g" priv_validator_grpc_laddr "" "$cfg" \
      "\"<private address>:${g##*:}\" (the address Fibre dials, named in the server certificate)" "the Fibre hosts" "<Fibre host address>" "$v$gdef"
    sev=$LOCK_SEV
    mtls="in $cfg, serve priv_validator_grpc_laddr with mTLS: set priv_validator_grpc_cert_file (a certificate whose SAN names the address Fibre dials), priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file (the CA that issues each Fibre's client certificate), and give each Fibre a client certificate from that CA (--signer-grpc-ca-file, --signer-grpc-cert-file and --signer-grpc-key-file in its ExecStart, or signer_grpc_* in its server_config.toml); if no Fibre signs through this node, empty it instead (priv_validator_grpc_laddr = \"\")"
    if ((n == 1 || n == 2)); then
      sev=FAIL
      why+=" Only $n of priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file are set, and celestia-appd refuses to start with only some of them."
      sets+=("in $cfg, set all three")
    elif ((n == 0)) && [[ $(toml_get "$cfg" "" priv_validator_grpc_allow_insecure) == true && $(lock_class "$(addr_host "${g#*://}")") != loopback ]]; then
      sev=FAIL
      why+=" With priv_validator_grpc_allow_insecure = true and no mTLS, whoever reaches priv_validator_grpc_laddr can ask for raw-bytes signatures with the validator key, no client certificate needed."
      sets+=("$mtls, and set priv_validator_grpc_allow_insecure = false")
    elif ((n == 0)); then
      if [[ $sev != FAIL ]]; then sev=WARN; fi
      why+=" Without mTLS, every account on this host can ask priv_validator_grpc_laddr for raw-bytes signatures with the validator key, no client certificate needed: loopback is no boundary between the accounts of a host."
      sets+=("$mtls")
    fi
    case $sev in
      FAIL) fails+=("$LOCK_TEXT") ;;
      WARN) warns+=("$LOCK_TEXT") ;;
      *) oks+=("$LOCK_TEXT") ;;
    esac
    if [[ -n $LOCK_BIND ]]; then sets+=("$LOCK_BIND"); fi
    if [[ -n $LOCK_UFW ]]; then ufws+=("$LOCK_UFW"); fi
  elif ! has_role validator; then
    note="; priv_validator_grpc_laddr is empty: after a tmkms repoint to this node, a Fibre can sign through it only once that is set (with mTLS)"
  fi
  if ((${#fails[@]} + ${#warns[@]} == 0)); then
    if ((${#oks[@]} == 0)); then
      report OK 20 "$name" "no signer port: priv_validator_laddr and priv_validator_grpc_laddr are empty$note"
    else
      report OK 20 "$name" "$(joins '; ' "${oks[@]}")$note"
    fi
    return
  fi
  s="$(joins '; ' "${fails[@]}" "${warns[@]}")${oks[*]:+; ok: $(joins '; ' "${oks[@]}")}$note.$why Fix: $(lock_fix ", with tmkms running" "$(joins '; ' "${sets[@]}")" "$(joins '; ' "${ufws[@]}")")"
  if ((${#fails[@]} > 0)); then report FAIL 20 "$name" "$s"; else report WARN 20 "$name" "$s"; fi
}

# The node's other ports (port_lock): the app gRPC that Fibre reads (app.toml [grpc] address,
# app_grpc_address in Fibre's settings), the RPC and the REST API, which nothing of this
# package needs beyond loopback (celestia-mon-signal may read the RPC from the monitor), and the
# RPC's gRPC, pprof and the trace pull address when set. The p2p port (26656) stays public, the
# metrics port is check 1's, the signer ports are check 20's. A missing key reads as the default
# address of the release (celestia-core v0.42.3, celestia-app's cosmos-sdk fork); only an
# explicit enable = false turns the app gRPC or the REST API off here.
check_node_ports() {
  local name="node ports" cfg app en v item label value key sec file want who src sev=OK note="" r gwant gwho gsrc
  local one="the one host that needs it (the monitor, for celestia-mon-signal)" onesrc="<address of that host>"
  local -a items=() bad=() oks=() sets=() ufws=() readers=() names=()
  if ! node_host; then report SKIP 25 "$name" "not a validator or backup node host"; return; fi
  cfg=$NODE_HOME/config/config.toml
  app=$NODE_HOME/config/app.toml
  if [[ ! -r $cfg ]]; then
    report SKIP 25 "$name" "$cfg not readable (run as root or pass --node-home)"
    return
  fi
  if [[ -r $app ]]; then
    if [[ $(toml_get "$app" grpc enable) == false ]]; then
      oks+=("app gRPC off ([grpc] enable = false)")
    else
      v=$(toml_get "$app" grpc address)
      v=${v:-localhost:9090}
      gwant="\"127.0.0.1:${v##*:}\" when only the Fibre on this host reads it (its app_grpc_address), else \"<private address>:${v##*:}\" (the address in the app_grpc_address of the Fibre hosts that read it)"
      gwho="the Fibre hosts"
      gsrc="<Fibre host address>"
      # With the inventory's Fibre hosts known, the ones without a node of their own read a node
      # on another host, maybe this one; the others read their own node on 127.0.0.1.
      if [[ -n ${C[fibre_addresses]} ]]; then
        mapfile -t readers < <(fibre_readers)
        if ((${#readers[@]} == 0)); then
          gwant="\"127.0.0.1:${v##*:}\" (every Fibre host of the inventory has a node of its own to read)"
          gwho=""
          gsrc=""
        else
          gsrc=""
          for r in "${readers[@]}"; do
            names+=("${r% *} at ${r#* }")
            gsrc+="${gsrc:+,}${r#* }"
          done
          gwho="the Fibre hosts without a node of their own that read this node ($(join "${names[@]}"))"
        fi
      fi
      items+=("[grpc] address|$v|address|grpc|$app|$gwant|$gwho|$gsrc")
    fi
    en=$(toml_get "$app" api enable)
    if [[ $en == false ]]; then
      oks+=("REST API off ([api] enable = false)")
    else
      v=$(toml_get "$app" api address)
      v=${v:-tcp://localhost:1317}
      items+=("[api] address|$v|address|api|$app|\"tcp://127.0.0.1:${v##*:}\", or \"tcp://<private address>:${v##*:}\" when one other host reads it|$one|$onesrc")
    fi
  else
    note="; $app not readable, so the app gRPC and the REST API were not checked"
  fi
  v=$(toml_get "$cfg" rpc laddr)
  v=${v:-tcp://127.0.0.1:26657}
  items+=("[rpc] laddr|$v|laddr|rpc|$cfg|\"tcp://127.0.0.1:${v##*:}\", or \"tcp://<private address>:${v##*:}\" when one other host reads it|$one|$onesrc")
  v=$(toml_get "$cfg" rpc grpc_laddr)
  if [[ -n $v ]]; then items+=("[rpc] grpc_laddr|$v|grpc_laddr|rpc|$cfg|\"tcp://127.0.0.1:${v##*:}\", or empty to turn it off|$one|$onesrc"); fi
  v=$(toml_get "$cfg" rpc pprof_laddr)
  if [[ -n $v ]]; then items+=("[rpc] pprof_laddr|$v|pprof_laddr|rpc|$cfg|\"127.0.0.1:${v##*:}\", or empty to turn it off|$one|$onesrc"); fi
  v=$(toml_get "$cfg" instrumentation trace_pull_address)
  if [[ -n $v ]]; then items+=("[instrumentation] trace_pull_address|$v|trace_pull_address|instrumentation|$cfg|\"127.0.0.1:${v##*:}\", or empty to turn it off|$one|$onesrc"); fi
  for item in "${items[@]}"; do
    IFS='|' read -r label value key sec file want who src <<<"$item"
    port_lock "$label" "$value" "$key" "$sec" "$file" "$want" "$who" "$src"
    case $LOCK_SEV in
      FAIL)
        sev=FAIL
        bad+=("$LOCK_TEXT")
        ;;
      WARN)
        if [[ $sev != FAIL ]]; then sev=WARN; fi
        bad+=("$LOCK_TEXT")
        ;;
      *) oks+=("$LOCK_TEXT") ;;
    esac
    if [[ -n $LOCK_BIND ]]; then sets+=("$LOCK_BIND"); fi
    if [[ -n $LOCK_UFW ]]; then ufws+=("$LOCK_UFW"); fi
  done
  if [[ $sev == OK ]]; then
    report OK 25 "$name" "$(joins '; ' "${oks[@]}")$note"
    return
  fi
  report "$sev" 25 "$name" "$(joins '; ' "${bad[@]}")${oks[*]:+; ok: $(joins '; ' "${oks[@]}")}$note. Every port of the node but p2p takes one source: whoever else reaches the app gRPC, the RPC or the REST API can query the chain through this node, send it transactions and load it. Fix: $(lock_fix "" "$(joins '; ' "${sets[@]}")" "$(joins '; ' "${ufws[@]}")")"
}

# celestia-appd and Fibre run as users of their own: run as root, a flaw in either that lets
# someone run code or read files gives them the whole host, and on a monitor host every alert
# secret and the power to silence alerts with it. Reads User= and DynamicUser= of the units.
check_service_users() {
  local name="service users" unit u
  local -a units=() root=() ok=() missing=()
  if node_host && [[ -n ${C[appd_unit]} ]]; then units+=("${C[appd_unit]}"); fi
  if has_role fibre && fibre_on && [[ -n ${C[fibre_unit]} ]]; then units+=("${C[fibre_unit]}"); fi
  if ((${#units[@]} == 0)); then
    report SKIP 26 "$name" "no celestia-appd node or Fibre unit on this host"
    return
  fi
  for unit in "${units[@]}"; do
    if ! unit_loaded "$unit"; then
      missing+=("$unit.service not found")
      continue
    fi
    u=$(unit_prop "$unit" User)
    if [[ $(unit_prop "$unit" DynamicUser) == yes ]]; then
      ok+=("$unit.service runs as a dynamic user")
    elif [[ -z $u || $u == root || $u == 0 ]]; then
      root+=("$unit.service")
    else
      ok+=("$unit.service runs as $u")
    fi
  done
  if ((${#root[@]} > 0)); then
    u="$(join "${root[@]}") runs as root (no User= in the unit)"
    if ((${#root[@]} > 1)); then u="$(join "${root[@]}") run as root (no User= in their units)"; fi
    if ((${#ok[@]} + ${#missing[@]} > 0)); then u+="; $(join "${ok[@]}" "${missing[@]}")"; fi
    report WARN 26 "$name" "$u: a flaw in a service run as root that lets someone run code or read files gives them this whole host$(has_role monitor && printf ', with every alert secret and the power to silence alerts'). Fix: give each a system user of its own (\"sudo useradd --system --home-dir <its home> --shell /usr/sbin/nologin <user>\"), give that user its home, data and keys (\"sudo chown -R <user>: <its home>\"; keys stay 0600), add User=<user> to the unit (\"sudo systemctl edit <unit>\"), then restart it at a moment you choose (celestia-appd with tmkms running)"
  elif ((${#ok[@]} > 0)); then
    report OK 26 "$name" "$(join "${ok[@]}" "${missing[@]}")"
  else
    report SKIP 26 "$name" "$(join "${missing[@]}") (checks 3 and 6)"
  fi
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
    if [[ -n $STATUS_NETWORK ]]; then
      report FAIL 3 "$name" "$rpc/status answers for $STATUS_NETWORK, not for $(home_chain) (the genesis in $NODE_HOME): another node answers there, so this node's RPC is not read. Fix: set celestia_mon_node_rpc_port in this host's inventory entry to the port of [rpc] laddr in $NODE_HOME/config/config.toml and re-run install.sh, or pass --node-rpc-url"
      return
    fi
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

# One setting of Fibre's start command: its flag in ExecStart, with the unit's variables filled
# in, over its key in <home>/config/server_config.toml, over celestia-app's default (flag > config
# file > default, fibre/cmd/start_cmd.go). Fibre writes that file at its first start, and a missing
# file leaves the defaults. Returns 1 when a variable of the flag is not set in the unit (FS_VAL is
# the flag's value as written), 2 when the file exists but cannot be read, or doctor is not root
# and so cannot tell.
FS_VAL="" FS_SRC=""
fibre_setting() { # unit, argv, config file, flag, key, default -> FS_VAL and FS_SRC
  local v
  FS_VAL="" FS_SRC=""
  if v=$(flag_value "$2" "$4"); then
    FS_VAL=$v
    FS_SRC="--$4 in ExecStart"
    unit_expand "$1" "$v" || return 1
    FS_VAL=$EXPANDED
    return 0
  fi
  if [[ -r $3 ]]; then
    FS_VAL=$(toml_get "$3" "" "$5")
    FS_SRC="$5 in $3"
    if [[ -z $FS_VAL ]]; then FS_SRC="Fibre's default (no $5 in $3)"; fi
  elif [[ -e $3 || $(id -u) -ne 0 ]]; then
    return 2
  else
    FS_SRC="Fibre's default ($3 does not exist; Fibre writes it at its first start)"
  fi
  FS_VAL=${FS_VAL:-$6}
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
# client certificate for the node's mTLS address. A plaintext signer warns even on loopback:
# the node's plaintext listener serves signatures to every account of its host (check 20), so
# every fix here leads to mTLS. The check reads both configs and never connects to the port.
# With node_addresses (the inventory's nodes), it names the node Fibre signs through, and warns
# when that address is no node of the inventory: celestia-mon then watches no node it signs
# through, or the address is mistyped.
check_fibre_signer() {
  local name="fibre signer" unit=${C[fibre_unit]} argv addr src cfg ip apply home f v kf tls=false ncfg g gh gp target problem=""
  local mode when loop=false port ncert kind fix via="" unknown=false
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
    report FAIL 17 "$name" "$src is $addr, not an IP address and port: Fibre resolves a host name at every dial, and without mTLS it accepts only a loopback IP such as 127.0.0.1, not \"localhost\". Fix: set it to the IP and port of the validator node's privval gRPC, with mTLS, run \"systemctl daemon-reload\" if you changed the unit, $apply"
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
      fix="serve the node's privval gRPC with mTLS (priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file in $ncfg, the client CA being the one that issued this Fibre's certificate) and restart celestia-appd at a moment you choose, with tmkms running"
    fi
  fi
  if [[ -n $problem ]]; then
    kind="an mTLS signer on this host"
    if [[ $tls == false ]]; then
      kind="a plaintext loopback signer"
      fix="give this Fibre a client certificate of its own from the CA the node trusts and point it at the node's mTLS address $target (--signer-grpc-address with --signer-grpc-ca-file, --signer-grpc-cert-file and --signer-grpc-key-file in ExecStart, or signer_grpc_* in $cfg), run \"systemctl daemon-reload\" if you changed the unit, $apply; if the node serves no mTLS there yet, set priv_validator_grpc_laddr = \"$target\" with priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file in $ncfg first, then restart celestia-appd at a moment you choose, with tmkms running"
    fi
    v="$src is $addr, $kind, but $problem: $when, every signature of this Fibre fails. celestia-appd has one privval gRPC listener, at one address and in one mode. Fix: $fix (check 20 shows where the node's signer ports listen)"
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
  # The inventory node at that address: this host's own for loopback or one of its addresses.
  if [[ $loop == true ]] || host_address "$ip"; then
    if ! node_host; then
      unknown=true
    else
      via=${C[inventory_host]}
    fi
  elif ! via=$(node_named "$ip"); then
    unknown=true
  fi
  if [[ -z ${C[node_addresses]} ]]; then unknown=false; fi
  if [[ $tls == false ]]; then
    report WARN 17 "$name" "$src is $addr, a plaintext signer without mTLS: the node's privval gRPC then serves raw-bytes signatures with the validator key to whoever reaches it, every account on its host included, no client certificate needed (check 20 on that node warns too). Fix: serve the node's privval gRPC with mTLS (priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file in its config.toml, then restart celestia-appd at a moment you choose, with tmkms running) and give this Fibre a client certificate from that CA (--signer-grpc-ca-file, --signer-grpc-cert-file and --signer-grpc-key-file in ExecStart, or signer_grpc_* in $cfg), run \"systemctl daemon-reload\" if you changed the unit, $apply"
  elif [[ $unknown == true ]]; then
    v="no celestia-appd node of the inventory has that address ($(node_list))"
    if [[ $loop == true ]] || host_address "$ip"; then v="this host runs no celestia-appd node of the inventory (roles: ${C[roles]})"; fi
    report WARN 17 "$name" "$src is $addr with mTLS, but $v: Fibre signs through a node celestia-mon does not watch, or the address is mistyped, and then every signature of this Fibre fails. Fix: set it to the private address of the node tmkms serves (its priv_validator_grpc_laddr: check 20 there), run \"systemctl daemon-reload\" if you changed the unit, $apply; if that node is not in the inventory, put its host in the backup_node group and run install.sh there"
  elif [[ $loop == true ]]; then
    report OK 17 "$name" "$src is $addr, the loopback signer on this host, mTLS${via:+: Fibre signs through $via}"
  else
    report OK 17 "$name" "$src is $addr, an IP address, mTLS${via:+: Fibre signs through $via}"
  fi
}

host_of() { # host:port, maybe after a scheme (tcp://, or a gRPC resolver such as dns:///) -> the host, without brackets
  local h=${1#*://}
  h=${h#/}
  h=${h%:*}
  h=${h#\[}
  printf '%s' "${h%\]}"
}
host_port() { # address, port -> address:port, an IPv6 address in brackets
  if [[ $1 == *:* ]]; then printf '[%s]:%s' "$1" "$2"; else printf '%s:%s' "$1" "$2"; fi
}
app_reach_here() { # host of the address Fibre reads, without port or brackets -> true when it is this host
  case $1 in
    '' | 0.0.0.0 | :: | localhost | 127.* | ::1) return 0 ;;
  esac
  host_address "$1"
}

# The app gRPC of the node on this host against the address a Fibre on this host dials: [grpc]
# address in app.toml (localhost:9090 when unset), off with enable = false. Dialing an empty host,
# 0.0.0.0 or localhost reaches loopback, and a listener on every interface (an empty host, 0.0.0.0
# or ::) takes loopback too. Sets APP_GRPC, the [grpc] address, and APP_DIAL, the address Fibre can
# dial to reach it.
APP_GRPC="" APP_DIAL=""
node_app_grpc() { # readable app.toml, host Fibre dials, its port -> 0 when the node's app gRPC listens there, 1 when not, 2 when it is off
  local gh nh=$2
  APP_GRPC="" APP_DIAL=""
  if [[ $(toml_get "$1" grpc enable) == false ]]; then return 2; fi
  APP_GRPC=$(toml_get "$1" grpc address)
  APP_GRPC=${APP_GRPC:-localhost:9090}
  gh=$(host_of "$APP_GRPC")
  case $gh in
    '' | 0.0.0.0 | :: | localhost) APP_DIAL=127.0.0.1 ;;
    *:*) APP_DIAL="[$gh]" ;;
    *) APP_DIAL=$gh ;;
  esac
  APP_DIAL+=":${APP_GRPC##*:}"
  if [[ ${APP_GRPC##*:} != "$3" ]]; then return 1; fi
  case $gh in '' | 0.0.0.0 | ::) return 0 ;; esac
  case $nh in
    '' | 0.0.0.0 | localhost) nh=127.0.0.1 ;;
    ::) nh=::1 ;;
  esac
  if [[ $gh == localhost ]]; then gh=127.0.0.1; fi
  [[ ${gh,,} == "${nh,,}" ]]
}

# Fibre reads the chain through one celestia-appd node's app gRPC: --app-grpc-address, else
# app_grpc_address in <home>/config/server_config.toml, else 127.0.0.1:9090 (celestia-app
# fibre/server_config.go). It dials it in plaintext and reads the chain ID there once when it
# starts, with no retry (fibre/server.go), so a Fibre whose node does not answer exits, and systemd
# restarts it. On a host without a celestia-appd node, an address of this host reaches nothing;
# on a host with one, it must be where that node's app gRPC listens ([grpc] address in app.toml,
# localhost:9090 when unset, the default of celestia-app's cosmos-sdk fork). A node on another host
# is named from node_addresses: an address that is no node of the inventory warns, as nothing then
# watches the node this Fibre depends on. As in check 17, a problem fails for an active Fibre and
# warns for a standby. doctor reads the configs and the listening sockets and never connects to
# the port.
check_fibre_app() {
  local name="fibre app node" unit=${C[fibre_unit]} argv home cfg addr src h port app l nodes
  local apply="then restart Fibre at a moment you choose" when="now" standby="" sev=FAIL via="" rc=0 fix reload why
  if fibre_skip 27 "$name"; then return; fi
  if ! unit_loaded "$unit"; then report SKIP 27 "$name" "$unit.service not found (check 6)"; return; fi
  if [[ $(unit_prop "$unit" ActiveState) != active ]]; then
    apply="and leave the unit stopped: it uses the new value when it next starts"
    when="once it starts"
    standby="standby: "
    sev=WARN
  fi
  argv=$(unit_argv "$unit")
  home=$(fibre_home "$unit" "$argv")
  cfg=$home/config/server_config.toml
  fibre_setting "$unit" "$argv" "$cfg" app-grpc-address app_grpc_address 127.0.0.1:9090 || rc=$?
  if ((rc == 1)); then
    report SKIP 27 "$name" "$FS_SRC is $FS_VAL, whose variables are not set in the unit's Environment= or a readable EnvironmentFile=; check the value by hand"
    return
  elif ((rc == 2)); then
    report SKIP 27 "$name" "no --app-grpc-address in ExecStart, and $cfg is not readable (run as root)"
    return
  fi
  addr=$FS_VAL
  src=$FS_SRC
  fix="set --app-grpc-address in ExecStart (or app_grpc_address in $cfg)"
  reload="run \"systemctl daemon-reload\" if you changed the unit, $apply"
  why="$when, Fibre cannot read the chain: it reads the chain ID there when it starts, with no retry, so it exits and systemd restarts it"
  # A gRPC target may name its resolver first (dns:///host:port).
  h=$(host_of "$addr")
  port=${addr##*:}
  if [[ $addr != *:* || ! $port =~ ^[0-9]+$ ]]; then
    report WARN 27 "$name" "$src is $addr: doctor reads no host and port in it, so it cannot tell which node Fibre reads. Fix: $fix to \"<private address of the node it reads>:9090\", $reload"
    return
  fi
  if app_reach_here "$h" && ! node_host; then
    nodes=$(node_list)
    l=$(listeners "$port")
    if [[ -n $l ]]; then
      report WARN 27 "$name" "${standby}$src is $addr, and port $port listens here (${l//$'\n'/, }), but this host is in no node group of the inventory (roles: ${C[roles]}): if a celestia-appd node runs here, nothing watches it. Fix: put this host in the backup_node group and re-run install.sh on it; or $fix to the private address of the node Fibre reads${nodes:+ ($nodes)}, $reload"
    else
      report "$sev" 27 "$name" "${standby}$src is $addr, but this host runs no celestia-appd node and nothing listens on port $port here: $why. Fix: $fix to the private address of the node it reads, \"<that address>:9090\"${nodes:+ (the nodes of the inventory: $nodes)}, $reload; on that node set [grpc] address = \"<its private address>:9090\" in app.toml, restart celestia-appd there at a moment you choose, and let only this host reach it: \"sudo ufw allow from $(own_address) to any port 9090 proto tcp\" (check 25 there shows who reaches it)"
    fi
    return
  fi
  if app_reach_here "$h"; then
    via=${C[inventory_host]:+ (${C[inventory_host]})}
    app=$NODE_HOME/config/app.toml
    if [[ ! -r $app ]]; then
      report SKIP 27 "$name" "$src is $addr, the node on this host$via; $app is not readable, so its [grpc] address was not compared (run as root or pass --node-home)"
      return
    fi
    node_app_grpc "$app" "$h" "$port" || rc=$?
    if ((rc == 2)); then
      report "$sev" 27 "$name" "${standby}$src is $addr, the node on this host$via, but it serves no app gRPC ([grpc] enable = false in $app): $why. Fix: under [grpc] in $app, set enable = true and address = \"127.0.0.1:$port\", then restart celestia-appd at a moment you choose (check 25 shows who else reaches it)"
    elif ((rc == 1)); then
      report "$sev" 27 "$name" "${standby}$src is $addr, the node on this host$via, but that node serves its app gRPC on $APP_GRPC ([grpc] address in $app), not there: $why. Fix: $fix to \"$APP_DIAL\", $reload"
    else
      report OK 27 "$name" "$src is $addr: Fibre reads the node on this host$via, whose [grpc] address is $APP_GRPC ($app)"
    fi
    return
  fi
  # A node on another host: an IP address is named from node_addresses, a host name only when it is
  # an inventory name (doctor resolves no name).
  if [[ $h =~ ^[0-9.]+$ || $h == *:* ]]; then
    via=$(node_named "$h")
    if [[ $(lock_class "$h") == public ]]; then
      report WARN 27 "$name" "$src is $addr${via:+, the node on $via}, a public address: the app link is plaintext gRPC, which belongs on the private network, and that node's app gRPC is then open beyond it. Fix: $fix to that node's private (VLAN or WireGuard) address, $reload; on that node set [grpc] address to the same address in app.toml, restart celestia-appd there at a moment you choose, and let only this host reach it: \"sudo ufw allow from $(own_address) to any port $port proto tcp\""
      return
    fi
  elif addr_pairs "${C[node_addresses]}" | cut -d ' ' -f 1 | grep -qxF -- "$h"; then
    via=$h
  fi
  if [[ -n $via ]]; then
    report OK 27 "$name" "$src is $addr: Fibre reads the node on $via, another host, over plaintext gRPC on the private network (check 25 on $via shows who reaches its app gRPC)"
  elif [[ -z ${C[node_addresses]} ]]; then
    report OK 27 "$name" "$src is $addr, a node on another host (with node_addresses, which install.sh writes, doctor names it)"
  elif [[ $h =~ ^[0-9.]+$ || $h == *:* ]]; then
    report WARN 27 "$name" "$src is $addr, but no celestia-appd node of the inventory has that address ($(node_list)): nothing watches the node this Fibre reads, so when it stops or falls behind only Fibre's own readiness shows it (FibreNotServing), or the address is mistyped. Fix: put that node's host in the inventory's backup_node group and run install.sh there, or $fix to the private address of a node of the inventory, $reload"
  else
    report WARN 27 "$name" "$src is $addr, a host name that names no node of the inventory ($(node_list)), and doctor resolves no name, so it cannot tell which node Fibre reads. Fix: $fix to that node's private IP address and port, $reload"
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
# on a private address; Prometheus, Alertmanager, celestia-mon-announcements, celestia-mon-tensile,
# celestia-mon-signal and celestia-mon-blackbox on 127.0.0.1 only.
CELESTIA_MON_UNITS=(celestia-mon-node-exporter celestia-mon-otelcol celestia-mon-prometheus celestia-mon-alertmanager celestia-mon-grafana
  celestia-mon-announcements celestia-mon-tensile celestia-mon-signal celestia-mon-blackbox)
CELESTIA_MON_LOOPBACK_UNITS=(celestia-mon-prometheus celestia-mon-alertmanager celestia-mon-announcements celestia-mon-tensile
  celestia-mon-signal celestia-mon-blackbox)

check_binds() {
  local name="private binds" fw unit pid addr detail r
  local -a ok=() bad=() wide=() open=()
  local -A unit_of=()
  net_read
  fw=$FW
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
      # Only the monitor scrapes these ports (and only you open Grafana): a ufw rule that lets
      # anyone in undoes the private bind.
      if ! is_loopback "$addr"; then
        r=$(ufw_open "${addr##*:}")
        if [[ -n $r ]]; then open+=("$unit $addr ($r)"); fi
      fi
    fi
  done < <(ss -Htlnp 2>/dev/null | awk '{ s = $0; while (match(s, /pid=[0-9]+/)) { print substr(s, RSTART + 4, RLENGTH - 4), $4; s = substr(s, RSTART + RLENGTH) } }' | sort -u)
  if ((${#bad[@]} > 0)); then
    detail="$(join "${bad[@]}") not on a private address"
    if ((${#wide[@]} > 0)); then detail+="; $(join "${wide[@]}") not on 127.0.0.1"; fi
    report FAIL 8 "$name" "$detail; firewall: $fw. Fix: set celestia_mon_private_ip for this host to its private address and re-run install.sh on each host"
  elif ((${#wide[@]} > 0)); then
    report FAIL 8 "$name" "$(join "${wide[@]}") not on 127.0.0.1, where Prometheus and Alertmanager listen; firewall: $fw. Fix: re-run install.sh on this host"
  elif ((${#open[@]} > 0)); then
    report WARN 8 "$name" "$(join "${ok[@]}"); but ufw lets anyone reach $(join "${open[@]}"); firewall: $fw. Fix: delete each such rule (\"sudo ufw status numbered\", then \"sudo ufw delete <number>\"): install.sh lets the monitor's address reach node_exporter and the collector with rules of its own (comment celestia-mon); for Grafana on a private address, let in only your workstation (\"sudo ufw allow from <workstation address> to any port <port> proto tcp\")"
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

# NTPSynchronized is the kernel's maximum error under 16 s (systemd's timedated), the test
# ClockNotSynced makes on node_timex_maxerror_seconds. The offset is the time daemon's own
# measurement: the kernel's offset stays 0 under chrony.
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
    report FAIL 9 "$name" "clock not synchronized (NTPSynchronized=${synced:-unknown}; after an hour ClockNotSynced sends a message). Fix: \"timedatectl set-ntp true\" (or start chrony) and allow outgoing NTP (UDP 123)"
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

# The md software RAID arrays in mdstat, one line each: "device state level need active map
# activity read-only", "-" where the array has none. need and active are the [2/1] of the status
# line, which node_exporter's mdadm collector exports and RaidDegraded compares; activity is a
# rebuild, resync or check in progress ("recovery=5.3%", "resync=DELAYED").
md_arrays() {
  awk '
    function flush() {
      if (dev != "") print dev, state, level, need, have, map, act, ro
      dev = ""
    }
    /^md[^ ]* : / {
      flush()
      dev = $1; state = $3; level = "-"; need = "-"; have = "-"; map = "-"; act = "-"; ro = "no"
      for (i = 4; i <= NF; i++) {
        if ($i ~ /^\((auto-)?read-only\)$/) ro = "yes"
        else if (level == "-" && $i ~ /^(raid[0-9]+|linear|multipath|faulty)$/) level = $i
      }
      next
    }
    /^[A-Za-z]/ { flush(); next }
    dev != "" && match($0, /\[[0-9]+\/[0-9]+\]/) {
      split(substr($0, RSTART + 1, RLENGTH - 2), f, "/")
      need = f[1]; have = f[2]
      if (match($0, /\[[U_]+\]/)) map = substr($0, RSTART, RLENGTH)
    }
    dev != "" && match($0, /(recovery|resync|check|reshape|repair) *= *[0-9.]+%/) {
      act = substr($0, RSTART, RLENGTH); gsub(/ /, "", act)
    }
    dev != "" && match($0, /(recovery|resync|reshape)=(DELAYED|PENDING)/) { act = substr($0, RSTART, RLENGTH) }
    END { flush() }
  ' "$1" 2>/dev/null
}

# md software RAID: one line per array in mdstat (none on a host without md), as check 10. An
# array missing a disk fails (RaidDegraded sends a message), one still rebuilding warns. It also
# fails for an array that mdstat lists but this host's node_exporter does not export: its mdadm
# collector reads the file whole, so one line it cannot parse leaves every array unwatched.
check_md() {
  local name="md RAID" arrays addr="" metrics="" exported="" note="" seen detail
  local dev state level need have map act ro
  [[ -r ${C[mdstat]} ]] || return 0
  arrays=$(md_arrays "${C[mdstat]}")
  [[ -n $arrays ]] || return 0
  if unit_loaded celestia-mon-node-exporter; then
    addr=$(flag_value "$(unit_argv celestia-mon-node-exporter)" web.listen-address) || addr=":9100"
    case ${addr%:*} in "" | 0.0.0.0 | "[::]") addr="127.0.0.1:${addr##*:}" ;; esac
    if metrics=$(curl -fsS -g --max-time 5 "http://$addr/metrics" 2>/dev/null) && [[ -n $metrics ]]; then
      exported=$(sed -n 's/^node_md_disks_required{.*device="\([^"]*\)".*/\1/p' <<<"$metrics")
    else
      metrics=""
      note="; node_exporter at $addr did not answer, so its metrics were not compared"
    fi
  else
    note="; this host runs no celestia-mon node_exporter, so RaidDegraded does not watch it"
  fi
  while read -r dev state level need have map act ro; do
    detail=$dev
    if [[ $level != - ]]; then detail+=" $level"; fi
    if [[ $need != - ]]; then detail+=" [$need/$have]"; fi
    if [[ $map != - ]]; then detail+=" $map"; fi
    if [[ $ro == yes ]]; then detail+=" read-only"; fi
    if [[ $act != - ]]; then detail+=", $act"; fi
    seen=""
    if [[ -n $metrics ]]; then
      if grep -qxF "$dev" <<<"$exported"; then seen=yes; else seen=no; fi
    fi
    if [[ $seen == no ]]; then
      report FAIL 10 "$name" "$detail, but node_exporter at $addr exports no node_md metrics for $dev, so RaidDegraded cannot see it. Fix: read \"journalctl -u celestia-mon-node-exporter | grep -i mdadm\" for the line it cannot parse"
    elif [[ $state == inactive ]]; then
      report INFO 10 "$name" "$detail: inactive (not started), which RaidDegraded does not watch$note. If it is an old disk's leftover that nothing uses, \"mdadm --stop /dev/$dev\" removes it"
    elif [[ $need == - ]]; then
      report OK 10 "$name" "$detail: no redundancy, so nothing to degrade$note"
    elif ((have >= need)); then
      report OK 10 "$name" "$detail$note"
    elif [[ $act == recovery=* || $act == reshape=* ]]; then
      report WARN 10 "$name" "$detail: missing a disk and rebuilding; RaidDegraded keeps firing until the rebuild has finished$note"
    elif [[ $ro == yes ]]; then
      report FAIL 10 "$name" "$detail: missing a disk$note. Fix: if it is an old disk's leftover that nothing mounts, stop it (\"mdadm --stop /dev/$dev\") and clear its member (\"mdadm --zero-superblock <partition>\"); if it is in use, \"mdadm --detail /dev/$dev\" shows which disk is missing"
    else
      report FAIL 10 "$name" "$detail: missing a disk$note. Fix: \"mdadm --detail /dev/$dev\" shows which disk failed or dropped out; replace it and add the new one (\"mdadm --manage /dev/$dev --add <partition>\")"
    fi
  done <<<"$arrays"
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
    signal) printf '%s' "signal: read \"systemctl status celestia-mon-signal\" and \"journalctl -u celestia-mon-signal -n 50\" on this host" ;;
    fibre_health) printf '%s' "fibre_health: read \"systemctl status celestia-mon-blackbox\" and \"journalctl -u celestia-mon-blackbox -n 50\" on this host" ;;
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
    celestia_mon_signal_* | celestia_mon_upgrade_* | celestia_mon_gov_* | celestia_mon_app_version | celestia_mon_chain_*) echo signal ;;
    probe_*) echo fibre_health ;;
  esac
}

# The install type decides which metrics can exist at all.
metric_expected() {
  case $1 in
    cometbft_privval_*) [[ ${C[signer]} != local ]] ;;
    fibre_* | otelcol_* | probe_*) fibre_on ;;
    *) true ;;
  esac
}

# The event after which a metric first appears, for the metrics that do not exist
# until it happens (celestia-core privval/metrics.go and consensus/state.go, celestia-app
# fibre/server_metrics.go, node_exporter's mdadm collector, Alertmanager notify/metrics.go,
# tools/celestia-mon-signal, tools/celestia-mon-tensile, Prometheus's ALERTS); empty for the others.
metric_event() {
  case $1 in
    cometbft_privval_signing_failures_total) echo "the first failed signing request" ;;
    cometbft_privval_signing_latency_median_seconds) echo "50 remote signatures since the node started" ;;
    cometbft_privval_signing_latency_*) echo "the first remote signature" ;;
    cometbft_consensus_validator_last_signed_height) echo "the validator's first own signature in a block; it must be bonded and in the active set" ;;
    cometbft_consensus_validator_missed_blocks) echo "the first block committed without the validator's signature since the node started" ;;
    fibre_server_upload_shard_last_success_timestamp_seconds) echo "the first accepted upload" ;;
    fibre_server_upload_shard_duration_seconds*) echo "the first upload" ;;
    node_md_disks | node_md_disks_required) echo "node_exporter finds an md software RAID array in /proc/mdstat; a host without one, or behind a hardware RAID controller, has none" ;;
    alertmanager_notifications_failed_total) echo "Alertmanager starts with a receiver of that integration" ;;
    celestia_mon_upgrade_version | celestia_mon_upgrade_height | celestia_mon_upgrade_eta_seconds) echo "a network upgrade is scheduled (x/signal)" ;;
    celestia_mon_gov_voting_start_timestamp_seconds | celestia_mon_gov_voting_end_timestamp_seconds | celestia_mon_gov_validator_voted)
      echo "a governance proposal in its voting period (x/gov)" ;;
    celestia_mon_fibre_last_assigned_timestamp_seconds) echo "a blob Tensile records for the validator (from a Tensile without last_assigned_at, the first since celestia-mon-tensile started)" ;;
    celestia_mon_fibre_obligations_24h) echo "Tensile's first answer with the validator's obligations" ;;
    ALERTS) echo "the first alert that is pending or firing" ;;
  esac
}

# Sorts every metric the rules read: present (OK); not expected for this install
# type; missing because its source is broken (FAIL: its scrape target is down, or
# Fibre does not push); missing until an event (NOT YET); or missing although its
# source is up (WARN: most likely a renamed metric). One line per group.
check_rule_metrics() {
  local name="rule metrics" prom=${C[prometheus_url]%/} pairs names present m total nmiss=0 src why ev job state pushing=false line
  local signal_read=false gov_read=false
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
  # celestia-mon-signal serves its reading only once it has read an RPC; before that, only its
  # last read time (0) and its errors.
  if awk -v t="$(prom_value 'max(celestia_mon_signal_last_success_timestamp_seconds)')" 'BEGIN { exit !(t + 0 > 0) }'; then
    signal_read=true
  fi
  # The same for the governance proposals, which it reads on their own (gov on): until it has, no
  # proposal can show, open or not.
  if awk -v t="$(prom_value 'max(celestia_mon_gov_last_success_timestamp_seconds)')" 'BEGIN { exit !(t + 0 > 0) }'; then
    gov_read=true
  fi
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
    elif [[ $src == signal && $signal_read != true ]]; then
      why="celestia-mon-signal has read no RPC since it started: check 23"
    elif [[ $m == celestia_mon_gov_* && $gov_read != true ]]; then
      why="celestia-mon-signal has read no governance proposals since it started: check 23"
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

# The end of an awk program that reads JSON, a small reader, so values may hold any character. The
# program's start defines value(v), called with each value that is not a key, opened(), called
# after each { or [, and closed(), called before each } or ]; d is the depth, obj[d] whether the
# container there is an object, and key[d] the key being read in it.
JSON_READER='
    BEGIN {
      while ((getline line) > 0) s = s line " "
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

# Prometheus's GET /api/v1/rules reply (JSON) -> one line per alerting rule with severity page, in
# the order Prometheus lists them (rule files by name, each group's rules as in its file): its name,
# its labels and the labels its annotations read ($labels.<name>), split by \034; the labels are
# split by \036, each one name \037 value, and so are the names read.
page_rules() {
  awk '
    function value(v) {
      if (d == 6 && key[6] == "name") name = v
      else if (d == 6 && key[6] == "type") type = v
      else if (d == 7 && key[6] == "labels") {
        if (key[7] == "severity") sev = v
        ls = ls ((ls == "") ? "" : "\036") key[7] "\037" v
      } else if (d == 7 && key[6] == "annotations") {
        while (match(v, /\$labels\.[a-zA-Z_][a-zA-Z0-9_]*/)) {
          l = substr(v, RSTART + 8, RLENGTH - 8)
          if (!index("\036" rd "\036", "\036" l "\036")) rd = rd ((rd == "") ? "" : "\036") l
          v = substr(v, RSTART + RLENGTH)
        }
      }
    }
    function opened() {
      if (d == 6 && obj[6] && key[2] == "groups" && key[4] == "rules") { name = ""; type = ""; sev = ""; ls = ""; rd = "" }
    }
    function closed() {
      if (d == 6 && obj[6] && key[2] == "groups" && key[4] == "rules" && type == "alerting" && sev == "page") {
        print name "\034" ls "\034" rd
      }
    }'"$JSON_READER"
}
# The page rules Prometheus has loaded, as page_rules prints them; check_silences reads them.
PAGE_RULES=()

# Alertmanager's GET /api/v2/silences reply (JSON) -> one line per active silence: its ID,
# endsAt, createdBy and matchers, split by \034; the matchers are split by \036, each one
# name \037 operator \037 value.
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
    }'"$JSON_READER"
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

# The page alerts a silence holds back, from the page rules Prometheus has loaded (PAGE_RULES). A
# matcher on alertname reads the rule's name, and one on a label the rule sets (severity, and a
# reason or check where it has one) reads that label's value. An alert also carries the labels its
# annotations read, whose values are not known here: it counts when the silence names alertname or
# severity, and its matchers on those labels are not read. One that reads host comes from a host's
# series and carries the other labels of its metrics too, so none of its matchers on a label the
# rule does not set is read: a host silence for planned work is listed only when it runs long. Each
# other matcher must match an absent label: a silence for one host does not hold back a page that
# names no host, such as FibreDown's, whose alerts carry at most the endpoint. Each alert is listed
# once, at its first rule held back.
silence_pages() { # matchers as silences_active prints them -> page alert names, comma-separated
  local -a ms=() kv=()
  local -A lab=() reads=() listed=()
  local r a l rd m n o v hit named=false out=""
  IFS=$'\036' read -r -a ms <<<"$1"
  for m in "${ms[@]}"; do
    n=${m%%$'\037'*}
    if [[ $n == alertname || $n == severity ]]; then named=true; fi
  done
  for r in "${PAGE_RULES[@]}"; do
    IFS=$'\034' read -r a l rd <<<"$r"
    if [[ -n ${listed[$a]+set} || (-n $rd && $named == false) ]]; then continue; fi
    lab=()
    IFS=$'\036' read -r -a kv <<<"$l"
    for m in "${kv[@]}"; do lab[${m%%$'\037'*}]=${m#*$'\037'}; done
    reads=()
    IFS=$'\036' read -r -a kv <<<"$rd"
    for m in "${kv[@]}"; do reads[$m]=1; done
    hit=true
    for m in "${ms[@]}"; do
      IFS=$'\037' read -r n o v <<<"$m"
      if [[ $n == alertname ]]; then matcher_admits "$o" "$v" "$a" || hit=false
      elif [[ -n ${lab[$n]+set} ]]; then matcher_admits "$o" "$v" "${lab[$n]}" || hit=false
      elif [[ -z ${reads[$n]+set} && -z ${reads[host]+set} ]]; then matcher_admits "$o" "$v" "" || hit=false; fi
    done
    if [[ $hit == true ]]; then
      listed[$a]=1
      out+="${out:+, }$a"
    fi
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
# hides the next outage. Warns on a silence that holds back a page alert (of an alert rule with
# severity page that Prometheus has loaded, so the list follows the install's rules) or runs for
# more than 4 hours more, with its matchers, author and end. Without the page rules (Prometheus
# not answering) it still lists the silences and warns on those that run long. Reads
# GET /api/v1/rules and GET /api/v2/silences; changes nothing.
check_silences() {
  local name="silences" prom=${C[prometheus_url]%/} am=${C[alertmanager_url]%/} reply code now id ends by ms end why t amtool unread=""
  local -a held=() other=()
  if ! has_role monitor; then report SKIP 21 "$name" "not the monitor host"; return; fi
  PAGE_RULES=()
  if ((!PROM_OK)); then
    unread="Prometheus not answering (check 11)"
  else
    mapfile -t PAGE_RULES < <(http_get "$prom/api/v1/rules?type=alert&exclude_alerts=true" | page_rules)
    if ((${#PAGE_RULES[@]} == 0)); then unread="Prometheus has no page alert rules loaded (check 12)"; fi
  fi
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
    if [[ -n $unread ]]; then
      t="${#held[@]} of $((${#held[@]} + ${#other[@]})) active silences end more than 4 hours from now: $(join "${held[@]}")"
    else
      t="${#held[@]} of $((${#held[@]} + ${#other[@]})) active silences hold back a page alert or end more than 4 hours from now: $(join "${held[@]}")"
    fi
    if ((${#other[@]} > 0)); then t+="; the others: $(join "${other[@]}")"; fi
    if [[ -n $unread ]]; then t+="; which page alerts they hold back is not known: $unread"; fi
    report WARN 21 "$name" "$t. While a silence holds an alert back, nothing is sent for it, and the dead-man heartbeat stays green. Fix: end each one that is not for planned work now, as root: ${amtool:-<amtool>} --alertmanager.url=$am --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml silence expire <ID>; for planned work, silence for as long as the work takes (docs/alerts.md, Before planned restarts and upgrades)"
  elif ((${#other[@]} > 0)) && [[ -n $unread ]]; then
    report SKIP 21 "$name" "${#other[@]} active silences, none ending more than 4 hours from now; which page alerts they hold back is not known: $unread: $(join "${other[@]}")"
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

pct() { awk -v a="$1" -v b="$2" 'BEGIN { if (b + 0 > 0) printf "%.1f", 100 * a / b; else printf "0" }'; }

signal_first_rpc() { # the first RPC in celestia-mon-signal's settings as the service names it (scheme://host[:port]), empty when unknown
  local url
  url=$(sed -n 's/^ *"rpc": *\["\([^"]*\)".*/\1/p' /etc/celestia-mon/signal/config.json 2>/dev/null | head -n 1)
  if [[ ${url,,} =~ ^(https?://[^/]+) ]]; then printf '%s' "${BASH_REMATCH[1]%:}"; fi
}

# Upgrade signalling, on the monitor, from celestia-mon-signal's metrics in Prometheus (job
# "signal", there only with celestia_mon_signal set). A full read takes the RPC's /abci_info, the
# x/signal queries and the validator's entry in the signal store, and the service starts only
# with an operator address that decodes to 20 bytes, so a recent read shows the whole path works.
# WARN while validators signal the next version and this validator has not (UpgradeSignalMissing),
# and while the reading comes from another RPC than the first one listed (a node of your own that
# the monitor cannot reach leaves the page to a public RPC unnoticed).
check_signal() {
  local name="upgrade signal" up last errs rpc first app next tally thr total own pend ver height eta line when warn=""
  if ! has_role monitor; then report SKIP 23 "$name" "not the monitor host"; return; fi
  if ((!PROM_OK)); then report SKIP 23 "$name" "Prometheus not answering (check 11)"; return; fi
  up=$(prom_value 'max(up{job="signal"})')
  if [[ -z $up ]]; then
    report SKIP 23 "$name" "upgrade signalling is off (celestia_mon_signal is not set)"
    return
  fi
  if [[ $up != 1 ]]; then
    report FAIL 23 "$name" "Prometheus cannot scrape celestia-mon-signal, so a signalling round would not page (SignalUnreadable sends a message). Fix: read \"systemctl status celestia-mon-signal\" and \"journalctl -u celestia-mon-signal -n 50\" on this host: an operator address that does not decode, or an rpc that is not an http or https URL or has a login or query, stops it at start; correct celestia_mon_signal and re-run install.sh on this host"
    return
  fi
  errs=$(prom_vector 'sum by (reason) (increase(celestia_mon_signal_errors_total{job="signal"}[15m]))' reason |
    awk '$2 + 0 > 0 { printf "%s%s %.0f", (n++ ? ", " : ""), $1, $2 }')
  last=$(prom_value 'max(celestia_mon_signal_last_success_timestamp_seconds{job="signal"})')
  if [[ -z $last ]] || awk -v l="$last" -v n="$(date +%s)" 'BEGIN { exit !(n - l > 900) }'; then
    if [[ -z $last || ${last%%.*} == 0 ]]; then
      when="no RPC read since the service started"
    else
      when="no RPC read for $((($(date +%s) - ${last%%.*}) / 60)) minutes"
    fi
    report FAIL 23 "$name" "$when${errs:+ (read errors in the last 15 minutes by reason: $errs)}, so a signalling round would not page. Fix: read \"journalctl -u celestia-mon-signal -n 50\" on this host: wrong_network is an RPC that serves another network than celestia_mon_signal.chain_id, behind one whose node is catching up, made no block for 2 minutes or answered an old height, network one this host cannot reach, http one that refused a call, query one that refused an x/signal query or whose store did not hold the height it gave, gov one that failed the Proposals query of x/gov; add a working RPC to celestia_mon_signal.rpc and re-run install.sh on this host"
    return
  fi
  rpc=$(prom_vector 'celestia_mon_signal_rpc_info{job="signal"}' rpc | cut -d ' ' -f 1 | head -n 1)
  app=$(prom_value 'max(celestia_mon_app_version{job="signal"})')
  next=$(prom_value 'max(celestia_mon_signal_next_version{job="signal"})')
  tally=$(prom_value 'max(celestia_mon_signal_tally_power{job="signal"})')
  thr=$(prom_value 'max(celestia_mon_signal_threshold_power{job="signal"})')
  total=$(prom_value 'max(celestia_mon_signal_total_power{job="signal"})')
  own=$(prom_value 'max(celestia_mon_signal_validator_version{job="signal"})')
  pend=$(prom_value 'max(celestia_mon_upgrade_pending{job="signal"})')
  line="read through ${rpc:-an RPC} $(( $(date +%s) - ${last%%.*} ))s ago: app version ${app:-?}; v${next:-?} signalled by $(pct "${tally:-0}" "${total:-0}")% of the voting power, an upgrade needs $(pct "${thr:-0}" "${total:-0}")%"
  if [[ ${own:-0} == 0 ]]; then
    line+="; your validator has not signalled"
  else
    line+="; your validator signalled v$own"
  fi
  if [[ $pend == 1 ]]; then
    ver=$(prom_value 'max(celestia_mon_upgrade_version{job="signal"})')
    height=$(prom_value 'max(celestia_mon_upgrade_height{job="signal"})')
    eta=$(prom_value 'max(celestia_mon_upgrade_eta_seconds{job="signal"})')
    line+="; upgrade to v${ver:-?} scheduled at height ${height:-?}"
    if [[ -n $eta ]]; then
      line+=", in about $(awk -v s="$eta" 'BEGIN { if (s >= 86400) printf "%.1f days", s / 86400; else if (s >= 3600) printf "%.0f hours", s / 3600; else printf "%.0f minutes", s / 60 }')"
    fi
  else
    line+="; no upgrade scheduled"
  fi
  line+="${errs:+; read errors in the last 15 minutes by reason: $errs}"
  first=$(signal_first_rpc)
  if [[ -n $first && -n $rpc && $rpc != "$first" ]]; then
    warn+=" The reading comes from $rpc, not from the first RPC listed, $first, so the upgrade alerts depend on a later one. Fix: \"journalctl -u celestia-mon-signal -n 50\" on this host names why $first fails: a node RPC that listens on 127.0.0.1 only (laddr in the [rpc] section of the node's config.toml), a firewall or tailnet policy between this host and that port, or a node that is down or catching up."
  fi
  if [[ $pend != 1 && ${own:-0} != "${next:-}" ]] && awk -v t="${tally:-0}" 'BEGIN { exit !(t + 0 > 0) }'; then
    warn+=" Validators signal v$next and your validator has not signalled it (UpgradeSignalMissing pages after 10 minutes). Fix: upgrade celestia-appd on every node to a release that supports v$next, then celestia-appd tx signal signal $next --from <validator account key> (docs/alerts.md, UpgradeSignalMissing)."
  fi
  if [[ -n $warn ]]; then
    report WARN 23 "$name" "$line.${warn%.}"
  else
    report OK 23 "$name" "$line"
  fi
}

# Governance votes, check 23's second line, on the monitor, from celestia-mon-signal's gov metrics
# (gov on, the default): the proposals in their voting period as the service read them last, and
# whether the validator account has voted on each. WARN while one has no vote from it, with when
# GovVoteMissing comes for it, as the rules have it: the message with less than 2 days left or half
# the vote, whichever is less, but not on a vote of 2 days or less where it pages; with gov_page the
# page with less than 1 day left or half the vote; each 10 minutes later (its for). FAIL when
# x/signal is read but the proposals have not been for 15 minutes. Silent where the first line
# already reports the service down or unread.
check_gov() {
  local name="gov votes" last sig errs voter id v end t n now s e half when
  local -a ids=() parts=() unvoted=()
  local -A starts=() ends=() votes=()
  if ! has_role monitor || ((!PROM_OK)); then return 0; fi
  if [[ $(prom_value 'max(up{job="signal"})') != 1 ]]; then return 0; fi
  now=$(date +%s)
  sig=$(prom_value 'max(celestia_mon_signal_last_success_timestamp_seconds{job="signal"})')
  if [[ -z $sig ]] || awk -v l="$sig" -v n="$now" 'BEGIN { exit !(n - l > 900) }'; then return 0; fi
  last=$(prom_value 'max(celestia_mon_gov_last_success_timestamp_seconds{job="signal"})')
  if [[ -z $last ]]; then
    report SKIP 23 "$name" "the governance votes are not read (celestia_mon_signal sets gov: false)"
    return 0
  fi
  if awk -v l="$last" -v n="$now" 'BEGIN { exit !(n - l > 900) }'; then
    errs=$(num "$(prom_value 'sum(increase(celestia_mon_signal_errors_total{job="signal",reason="gov"}[15m]))')")
    if [[ ${last%%.*} == 0 ]]; then t="no proposals read since the service started"; else t="no proposals read for $(((now - ${last%%.*}) / 60)) minutes"; fi
    report FAIL 23 "$name" "$t, while x/signal is read$([[ $errs != 0 ]] && printf ' (%s failed reads of them in the last 15 minutes)' "$errs"), so a missing vote on a proposal opened since would not $([[ ${C[gov_page],,} == true ]] && printf page || printf 'be sent'), and a vote cast since would not resolve GovVoteMissing. Fix: read \"journalctl -u celestia-mon-signal -n 50\" on this host: it names each RPC that failed the Proposals query of x/gov and why; add an RPC whose node answers it to celestia_mon_signal.rpc and re-run install.sh on this host"
    return 0
  fi
  voter=$(prom_vector 'celestia_mon_gov_voting_proposals{job="signal"}' voter | cut -d ' ' -f 1 | head -n 1)
  while read -r id v; do starts[$id]=$v; done < <(prom_vector 'max by (proposal_id) (celestia_mon_gov_voting_start_timestamp_seconds{job="signal"})' proposal_id)
  while read -r id v; do ends[$id]=$v; done < <(prom_vector 'max by (proposal_id) (celestia_mon_gov_voting_end_timestamp_seconds{job="signal"})' proposal_id)
  while read -r id v; do votes[$id]=$v; done < <(prom_vector 'max by (proposal_id) (celestia_mon_gov_validator_voted{job="signal"})' proposal_id)
  mapfile -t ids < <(printf '%s\n' "${!ends[@]}" | grep -E '^[0-9]+$' | sort -n)
  for id in "${ids[@]}"; do
    # The title as Prometheus's JSON carries it: & < > as unicode escapes, a quote or backslash
    # after a backslash.
    t=$(prom_query "max by (title) (celestia_mon_gov_proposal_info{job=\"signal\",proposal_id=\"$id\"})" |
      sed -n 's/.*"title":"\(\([^"\\]\|\\.\)*\)".*/\1/p' | head -n 1)
    t=$(sed 's/\\u0026/\&/g; s/\\u003c/</g; s/\\u003e/>/g; s/\\\(.\)/\1/g' <<<"$t")
    end=$(date -u -d "@${ends[$id]%%.*}" '+%Y-%m-%d %H:%M UTC' 2>/dev/null)
    if [[ ${votes[$id]:-0} == 1 ]]; then
      v="voted"
    else
      v="not voted"
      e=${ends[$id]%%.*}
      s=${starts[$id]:-0}
      half=$(((e - ${s%%.*}) / 2))
      when=""
      if [[ ${C[gov_page],,} != true ]] || ((half > 86400)); then
        when="the message from $(date -u -d "@$((e - (half < 172800 ? half : 172800) + 600))" '+%Y-%m-%d %H:%M UTC' 2>/dev/null)"
      fi
      if [[ ${C[gov_page],,} == true ]]; then
        when+="${when:+, }the page from $(date -u -d "@$((e - (half < 86400 ? half : 86400) + 600))" '+%Y-%m-%d %H:%M UTC' 2>/dev/null)"
      fi
      unvoted+=("$id (GovVoteMissing: $when)")
    fi
    parts+=("proposal $id${t:+ ($t)} ends ${end:-at an unknown time}, $v")
  done
  n=${#ids[@]}
  t="read $((now - ${last%%.*}))s ago for ${voter:-the validator account}: "
  if ((n == 0)); then t+="no proposal is in its voting period"; else t+="$(join "${parts[@]}")"; fi
  if ((${#unvoted[@]} > 0)); then
    report WARN 23 "$name" "$t. The validator account has not voted on proposal $(join "${unvoted[@]}"). Fix: vote from the validator account, abstain if you have no view: celestia-appd tx gov vote <proposal> yes|no|no_with_veto|abstain --from <validator account key>; a vote sent through an authz grant from that account counts, one from any other account does not (docs/alerts.md, GovVoteMissing)"
  else
    report OK 23 "$name" "$t"
  fi
}

probe_metric() { # blackbox /probe reply, series as blackbox prints it -> its value, empty when absent
  awk -v s="$2" '$1 == s { print $2; exit }' <<<"$1"
}

# Fibre's upload readiness, on the monitor. Fibre v10.5.0 and newer answers the standard gRPC
# health check on its own port: SERVING (Ready) when all of its own checks pass, NOT_SERVING
# (Not ready) when one fails. An older Fibre answers Unimplemented (gRPC code 12: Not reported),
# and a standby's port is closed. doctor asks celestia-mon-blackbox for each target of the scrape
# job fibre_health, as Prometheus does, and reads the answer now.
check_fibre_ready() {
  local name="fibre ready" bburl=${C[blackbox_url]%/} unit=${C[fibre_unit]} h addr line code out st why fix detail
  local re_host='"host":"([^"]*)"' re_instance='"instance":"([^"]*)"'
  local -a hosts=() ready=() notready=() noans=() notrep=() standby=() bberr=() parts=() nr=()
  local -A addr_of=()
  if ! has_role monitor; then report SKIP 24 "$name" "not the monitor host"; return; fi
  if ! fibre_on; then report SKIP 24 "$name" "Fibre is disabled"; return; fi
  if ((!PROM_OK)); then report SKIP 24 "$name" "Prometheus not answering (check 11)"; return; fi
  while IFS= read -r line; do
    [[ $line =~ $re_host ]] || continue
    h=${BASH_REMATCH[1]}
    [[ $line =~ $re_instance ]] && addr_of[$h]=${BASH_REMATCH[1]}
  done < <(prom_query 'max by (host, instance) (up{job="fibre_health"})' | sed 's/{"metric":/\n&/g')
  if ((${#addr_of[@]} == 0)); then
    report FAIL 24 "$name" "Fibre is on, but Prometheus has no readiness target (job fibre_health), so nothing reads whether Fibre is ready. Fix: re-run install.sh on this host"
    return
  fi
  # celestia-mon-blackbox takes the same API password as Prometheus (curl reads it from a file
  # descriptor, as mon_curl does).
  code=$(curl -K <(printf '%s\n' "$API_AUTH") -s -g -o /dev/null --max-time 5 -w '%{http_code}' "$bburl/-/healthy" 2>/dev/null)
  if [[ $code != 200 ]]; then
    report FAIL 24 "$name" "celestia-mon-blackbox does not answer at $bburl ($(answer "$code")), so Fibre's readiness is not checked. Fix: read \"systemctl status celestia-mon-blackbox\" and \"journalctl -u celestia-mon-blackbox -n 50\" on this host, then re-run install.sh on this host"
    return
  fi
  fibre_states
  mapfile -t hosts < <(printf '%s\n' "${!addr_of[@]}" | sort)
  for h in "${hosts[@]}"; do
    addr=${addr_of[$h]}
    out=$(curl -K <(printf '%s\n' "$API_AUTH") -fsS -g --max-time 10 -G --data-urlencode module=fibre_health \
      --data-urlencode "target=$addr" "$bburl/probe" 2>/dev/null) || out=""
    if [[ -z $out ]]; then
      bberr+=("$h")
      continue
    fi
    st=$(probe_metric "$out" probe_grpc_status_code)
    if [[ $(probe_metric "$out" 'probe_grpc_healthcheck_response{serving_status="SERVING"}') == 1 ]]; then
      ready+=("$h ($addr)")
    elif [[ $(probe_metric "$out" 'probe_grpc_healthcheck_response{serving_status="NOT_SERVING"}') == 1 ]]; then
      notready+=("$h ($addr)")
      nr+=("$h")
    elif [[ $st == 12 ]]; then
      notrep+=("$h ($addr)")
    elif [[ ${FIBRE_STATE[$h]:-} == standby ]]; then
      standby+=("$h")
    else
      if [[ $addr == "["* ]]; then
        why="the readiness probe cannot reach an IPv6 address yet"
      else
        case $st in
          14) why="no connection" ;;
          4) why="no answer within 5 seconds" ;;
          *) why="gRPC status ${st:-unknown}" ;;
        esac
      fi
      noans+=("$h: the monitor cannot reach Fibre's port at $addr ($why), so readiness is not checked")
    fi
  done
  if ((${#bberr[@]} > 0)); then
    report FAIL 24 "$name" "celestia-mon-blackbox did not answer the readiness probe of $(join "${bberr[@]}"). Fix: read \"journalctl -u celestia-mon-blackbox -n 50\" on this host, then re-run install.sh on this host"
    return
  fi
  if ((${#notready[@]} > 0)); then parts+=("Not ready: $(join "${notready[@]}"): Fibre reports that at least one of its own checks fails, so uploads to it may fail"); fi
  if ((${#noans[@]} > 0)); then parts+=("Not checked: $(join "${noans[@]}")"); fi
  if ((${#notrep[@]} > 0)); then parts+=("Not reported: $(join "${notrep[@]}"): Fibre older than v10.5.0 does not report readiness"); fi
  if ((${#ready[@]} > 0)); then parts+=("Ready: $(join "${ready[@]}")"); fi
  if ((${#standby[@]} > 0)); then parts+=("standby (Fibre unit not active, port closed): $(join "${standby[@]}")"); fi
  detail=$(printf '%s; ' "${parts[@]}")
  detail=${detail%; }
  if ((${#notready[@]} > 0)); then
    report FAIL 24 "$name" "$detail. Fix: on $(join "${nr[@]}"), \"journalctl -u $unit | grep 'health check failed' | tail -n 5\" names the failing check (app, fibre_module, signer, validator, registration or store) and what to do about it; Fibre turns Ready again by itself once that check passes"
  elif ((${#noans[@]} + ${#notrep[@]} > 0)); then
    fix=""
    if ((${#noans[@]} > 0)); then
      fix+="Fibre must listen on the address named (check 6 on that host shows where it listens), and any firewall or tailnet policy between the hosts must let the monitor reach that port; if Fibre uses another port there, set celestia_mon_fibre_port for that host and re-run install.sh on this host"
    fi
    if ((${#notrep[@]} > 0)); then
      fix+="${fix:+; }upgrade Fibre to v10.5.0 or newer to get its readiness"
    fi
    report WARN 24 "$name" "$detail. Fix: $fix"
  elif ((${#ready[@]} == 0)); then
    report SKIP 24 "$name" "no Fibre unit is active, so no readiness is expected (check 19); $detail"
  else
    report OK 24 "$name" "$detail"
  fi
}

# ------------------------------------------------------------------- survey

# --survey, on each host before its install: what the host runs and its place in the inventory.
# It finds the units that run celestia-appd (also through cosmovisor, with
# DAEMON_NAME=celestia-appd), Fibre and tmkms by their binary, whatever their names, reads their
# settings with the readers of the checks, and prints the groups of the host, its entry for
# hosts.yml and the steps on the Celestia side, which are the operator's. It runs no check and
# writes no file.

# Every service unit file and loaded service unit that runs one of them, one line each:
# "<appd|fibre|tmkms> <unit without .service> <ActiveState>". Template units are left out.
survey_units() {
  local -a units=()
  local unit path state kind
  mapfile -t units < <({
    systemctl list-unit-files --type=service --no-legend --plain 2>/dev/null
    systemctl list-units --type=service --all --no-legend --plain 2>/dev/null
  } | awk '$1 ~ /\.service$/ && $1 !~ /@\.service$/ { print $1 }' | sort -u)
  ((${#units[@]} > 0)) || return 0
  while read -r unit path state; do
    case ${path##*/} in
      celestia-appd) kind=appd ;;
      fibre) kind=fibre ;;
      tmkms) kind=tmkms ;;
      cosmovisor)
        if ! unit_env "$unit" DAEMON_NAME || [[ $ENV_VAL != celestia-appd ]]; then continue; fi
        kind=appd
        ;;
      *) continue ;;
    esac
    printf '%s %s %s\n' "$kind" "${unit%.service}" "$state"
  done < <(systemctl show -p Id -p ExecStart -p ActiveState -- "${units[@]}" 2>/dev/null | awk '
    function flush() { if (id != "" && path != "") print id, path, st; id = ""; path = ""; st = "" }
    /^Id=/ { id = substr($0, 4) }
    /^ActiveState=/ { st = substr($0, 13) }
    /^ExecStart=/ && path == "" && match($0, /path=[^ ;]*/) { path = substr($0, RSTART + 5, RLENGTH - 5) }
    /^$/ { flush() }
    END { flush() }')
}

SURVEY_UNITS=""
survey_pick() { # kind, preferred unit -> the unit of that kind: the preferred one while active, else an active one, else the preferred one, else the first
  awk -v k="$1" -v p="$2" '
    $1 != k { next }
    $2 == p { pref = 1; if ($3 == "active") pa = 1 }
    $3 == "active" && act == "" { act = $2 }
    first == "" { first = $2 }
    END { print (pa ? p : (act != "" ? act : (pref ? p : first))) }' <<<"$SURVEY_UNITS"
}
survey_also() { # kind, unit -> the other units of that kind, with their state
  awk -v k="$1" -v u="$2" '$1 == k && $2 != u { printf "%s%s.service (%s)", (n++ ? ", " : ""), $2, $3 }' <<<"$SURVEY_UNITS"
}
survey_state() { # unit -> its ActiveState
  awk -v u="$1" '$2 == u { print $3; exit }' <<<"$SURVEY_UNITS"
}

# The private addresses of this host, "address interface kind" lines: WireGuard interfaces first,
# then the other private addresses, Tailscale's last (100.64.0.0/10, where only the tailnet policy
# filters). Loopback and the bridges of containers and VMs are left out.
survey_addresses() {
  local wg ifc a r kind
  local -a out=()
  wg=" $(ip -o link show type wireguard 2>/dev/null | awk -F': ' '{ sub(/@.*/, "", $2); printf "%s ", $2 }')"
  while read -r ifc a; do
    ifc=${ifc%@*}
    a=${a%/*}
    [[ $ifc != lo && ! $ifc =~ ^(docker|br-|veth|virbr|lxc|cni|flannel) && $a != 127.* && $a =~ ${C[private_address_regex]} ]] || continue
    if [[ $wg == *" $ifc "* ]]; then
      r=0 kind=WireGuard
    elif [[ $ifc == tailscale* ]] || is_tailnet "$a"; then
      r=2 kind=Tailscale
    else
      r=1 kind=private
    fi
    out+=("$r $a $ifc $kind")
  done < <(ip -o addr show scope global 2>/dev/null | awk '{ print $2, $4 }')
  if ((${#out[@]} > 0)); then printf '%s\n' "${out[@]}" | sort -s -n -k1,1 | cut -d ' ' -f 2-; fi
}

mount_of() { # path -> the mount point that holds it (of its nearest existing parent)
  local p=$1
  while [[ -n $p && ! -e $p ]]; do p=${p%/*}; done
  findmnt -n -o TARGET -T "${p:-/}" 2>/dev/null | head -n 1
}

survey() {
  local me appd fibre tmkms also cfg="" app="" laddr="" power role="" why="" prom mladdr mhost mport=26660 ns bind g
  local fargv fhome fcfg fstate="" fapply fport="" fmount="" aaddr="" ah aport saddr="" stls="without mTLS" otel want
  local ip note kind ifc single=false m mon rc n f v where
  local -a cands=() mounts=() groups=() steps=() entry=() others=()
  SURVEY_UNITS=$(survey_units)
  appd=$(survey_pick appd "${C[appd_unit]}")
  fibre=$(survey_pick fibre "${C[fibre_unit]}")
  tmkms=$(survey_pick tmkms tmkms)
  me=${HOSTNAME:-$(uname -n)}
  me=${me%%.*}
  printf 'celestia-mon-doctor --survey on %s: what this box runs; nothing was changed.\n' "$me"
  if [[ $(id -u) -ne 0 ]]; then printf '%s\n' "Not run as root, so the node's and Fibre's settings may not be readable: run it with sudo."; fi
  if [[ -z $appd && -z $fibre && -n $tmkms ]]; then
    printf '\n  %-14s %s\n\n' tmkms "$tmkms.service ($(survey_state "$tmkms"))"
    printf '%s\n' "tmkms runs here: nothing is installed here; put this box in the kms group." \
      "In hosts.yml, list it under all: hosts: by its name alone ($me:) and under children: in kms. To watch its disks as well, give it celestia_mon_kms_scrape: true, celestia_mon_private_ip and celestia_mon_data_mounts."
    return
  fi

  # The node: validator or backup node, as check 2 tells them apart.
  if [[ -n $appd ]]; then
    C[appd_unit]=$appd
    find_node
    cfg=$NODE_HOME/config/config.toml
    app=$NODE_HOME/config/app.toml
    follow_rpc_laddr
    read_status
    power=$(status_value voting_power)
    if [[ ! -r $cfg ]]; then
      role="validator or backup_node"
      why="$cfg is not readable (run as root, or pass --node-home)"
    else
      laddr=$(toml_get "$cfg" "" priv_validator_laddr)
      if [[ -n $laddr ]]; then
        role=validator
        why="priv_validator_laddr = $laddr: tmkms signs through this node"
      elif [[ $power =~ ^[1-9][0-9]*$ ]]; then
        role=validator
        why="it signs with its local key file, which has voting power $power"
      elif [[ $power == 0 ]]; then
        role=backup_node
        why="priv_validator_laddr is empty and its local key has no voting power (a jailed or unbonded validator's key has none either: then it is the validator)"
      else
        role=backup_node
        why="priv_validator_laddr is empty and its RPC did not answer: it is the validator instead if it signs with its local key file"
      fi
    fi
    groups+=("$role")
  fi

  # Fibre: where it listens, which node it reads and which it signs through, and its OTLP endpoint.
  if [[ -n $fibre ]]; then
    C[fibre_unit]=$fibre
    groups+=(fibre)
    fstate=$(survey_state "$fibre")
    fapply="then restart Fibre at a moment you choose"
    if [[ $fstate != active ]]; then fapply="and leave the unit stopped: it uses the new value when it next starts"; fi
    fargv=$(unit_argv "$fibre")
    fhome=$(fibre_home "$fibre" "$fargv")
    fcfg=$fhome/config/server_config.toml
    fmount=$(mount_of "$fhome")
    if fibre_setting "$fibre" "$fargv" "$fcfg" server-listen-address server_listen_address 0.0.0.0:7980; then fport=${FS_VAL##*:}; fi
    if fibre_setting "$fibre" "$fargv" "$fcfg" app-grpc-address app_grpc_address 127.0.0.1:9090; then aaddr=$FS_VAL; fi
    if fibre_setting "$fibre" "$fargv" "$fcfg" signer-grpc-address signer_grpc_address 127.0.0.1:26669; then saddr=$FS_VAL; fi
    n=0
    for f in ca cert key; do
      rc=0
      fibre_setting "$fibre" "$fargv" "$fcfg" "signer-grpc-$f-file" "signer_grpc_${f}_file" "" || rc=$?
      if ((rc < 2)) && [[ -n $FS_VAL ]]; then n=$((n + 1)); fi
    done
    if ((n == 3)); then stls=mTLS; fi
    want=${C[fibre_otel_endpoint]%/}
    otel=""
    if otel=$(flag_value "$fargv" otel-endpoint); then
      if unit_expand "$fibre" "$otel"; then otel=$EXPANDED; fi
    elif unit_env "$fibre" FIBRE_OTEL_ENDPOINT; then
      otel=$ENV_VAL
    fi
    otel=${otel%/}
  fi
  if [[ -n $tmkms ]]; then groups+=(kms); fi
  if unit_loaded celestia-mon-prometheus; then groups+=(monitor); fi

  # The address the monitor reaches this host on: 127.0.0.1 where the node and Fibre read each
  # other on loopback with no remote signer, as on one box; else the first private address.
  mapfile -t cands < <(survey_addresses)
  if [[ -n $appd && -n $aaddr && -n $saddr ]] && app_reach_here "$(host_of "$aaddr")" && app_reach_here "$(host_of "$saddr")" &&
    [[ -z $laddr || $(lock_class "$(host_of "$laddr")") == loopback ]]; then
    single=true
  fi
  for v in "${cands[@]:1}"; do
    read -r m ifc kind <<<"$v"
    others+=("$m ($ifc)")
  done
  if ((${#cands[@]} == 0)); then
    ip=127.0.0.1
    note="no private address found: 127.0.0.1 works only when every box of the install is this one"
  elif [[ $single == true ]]; then
    ip=127.0.0.1
    note="the node and Fibre here read each other on loopback, as on one box: keep 127.0.0.1 when the monitor runs here too, else ${cands[0]%% *}"
  else
    read -r ip ifc kind <<<"${cands[0]}"
    note="$ifc, $kind${others[*]:+; also on this box: $(join "${others[@]}")}"
  fi

  # Data mounts: the mount point of the node's data, /, and on the monitor that of
  # /var/lib/celestia-mon, Prometheus's data.
  if [[ -n $appd ]]; then
    m=$(mount_of "$NODE_HOME/data")
    if [[ -n $m ]]; then mounts+=("$m"); fi
  fi
  if [[ " ${mounts[*]} " != *" / "* ]]; then mounts+=(/); fi
  mon=$(mount_of /var/lib/celestia-mon)
  if [[ -n $mon && " ${mounts[*]} " != *" $mon "* ]] && [[ ${#groups[@]} -eq 0 || " ${groups[*]} " == *" monitor "* ]]; then
    mounts+=("$mon")
  fi

  v=$ip
  if [[ $v == *:* ]]; then v="\"$v\""; fi
  entry+=("    $me:" "      celestia_mon_private_ip: $v  # $note" "      celestia_mon_data_mounts: [$(joins ', ' "${mounts[@]}")]")
  if [[ -n $mon && " ${mounts[*]} " != *" $mon "* ]]; then
    entry+=("      # when the monitor runs on this box, add $mon, the disk of /var/lib/celestia-mon (Prometheus's data)")
  fi
  if [[ -n $fibre ]]; then
    entry+=("      celestia_mon_fibre_mount: ${fmount:-<mount point of $fhome>}"
      "      celestia_mon_fibre_public_endpoint: \"<public host or IP>:${fport:-7980}\"  # what valaddr set-host registers while this box's Fibre is the active one")
    if [[ -n $fport && $fport != 7980 ]]; then entry+=("      celestia_mon_fibre_port: $fport"); fi
  fi
  if [[ -n $appd && -r $cfg ]]; then
    mladdr=$(toml_get "$cfg" instrumentation prometheus_listen_addr)
    # CometBFT's default ":26660" is every interface.
    mladdr=${mladdr:-:26660}
    mport=${mladdr##*:}
    if [[ ! $mport =~ ^[0-9]+$ ]]; then mport=26660; fi
    if [[ $mport != 26660 ]]; then entry+=("      celestia_mon_node_metrics_port: $mport"); fi
  fi
  if [[ -n $appd && ! -f $cfg ]]; then
    entry+=("      # celestia_mon_node_home: <the node's home>: doctor found no config/config.toml in $NODE_HOME")
  fi
  if [[ ${appd:-celestia-appd} != celestia-appd || ${fibre:-fibre} != fibre || ${tmkms:-tmkms} != tmkms ]]; then
    entry+=("      celestia_mon_units: {appd: ${appd:-celestia-appd}, fibre: ${fibre:-fibre}, tmkms: ${tmkms:-tmkms}}  # the whole map: it replaces the default one")
  fi
  if [[ -n $fibre ]]; then
    where="another box"
    if [[ -n $aaddr ]] && app_reach_here "$(host_of "$aaddr")"; then where="this box"; fi
    entry+=("      # Fibre reads the chain at ${aaddr:-<not read>} ($where) and signs through ${saddr:-<not read>} ($stls)")
  fi

  # Your steps: the node's metrics, Fibre's OTLP endpoint, the app gRPC Fibre reads, Fibre's mTLS.
  if [[ -n $appd && ! -r $cfg ]]; then
    steps+=("Run the survey as root: $cfg is not readable, so the node's metrics were not read")
  elif [[ -n $appd ]]; then
    prom=$(toml_get "$cfg" instrumentation prometheus)
    ns=$(toml_get "$cfg" instrumentation namespace)
    mhost=$(host_of "$mladdr")
    bind=$(host_port "$ip" "$mport")
    if [[ $prom != true ]]; then
      steps+=("The node serves no metrics (prometheus = ${prom:-unset}): under [instrumentation] in $cfg, set prometheus = true and prometheus_listen_addr = \"$bind\", then restart celestia-appd at a moment you choose")
    elif [[ -z $mhost || $mhost == 0.0.0.0 || $mhost == :: ]]; then
      steps+=("The node's metrics listen on every interface (prometheus_listen_addr = \"$mladdr\"): set it to \"$bind\" under [instrumentation] in $cfg, then restart celestia-appd at a moment you choose; only the monitor reaches that port")
    elif [[ $mhost != "$ip" && ! ($mhost == localhost && $ip == 127.0.0.1) ]]; then
      steps+=("The node's metrics listen on $mhost, not on $ip, the address of this entry: set prometheus_listen_addr = \"$bind\" under [instrumentation] in $cfg (or celestia_mon_private_ip to $mhost, if the monitor reaches this box there), then restart celestia-appd at a moment you choose")
    fi
    if [[ -n $ns && $ns != cometbft ]]; then
      steps+=("Set namespace = \"cometbft\" under [instrumentation] in $cfg (it is \"$ns\"), then restart celestia-appd at a moment you choose: the alerts read the cometbft_ metrics")
    fi
  fi
  if [[ -n $fibre ]]; then
    if [[ -z $otel ]]; then
      steps+=("Fibre sends no metrics yet: add \"--otel-endpoint $want\" to ExecStart in $fibre.service (or Environment=FIBRE_OTEL_ENDPOINT=$want), run \"systemctl daemon-reload\", $fapply. The collector the install puts on this box takes them there")
    elif [[ $otel != "$want" && $otel != "${want/127.0.0.1/localhost}" ]]; then
      steps+=("Fibre sends its metrics to $otel: set --otel-endpoint (or FIBRE_OTEL_ENDPOINT) in $fibre.service to $want, the collector the install puts on this box, run \"systemctl daemon-reload\", $fapply")
    fi
    if [[ -z $aaddr || -z $saddr ]]; then
      steps+=("Run the survey as root: $fcfg is not readable, so which node Fibre reads and signs through was not read")
    fi
  fi
  if [[ -n $aaddr ]]; then
    ah=$(host_of "$aaddr")
    aport=${aaddr##*:}
    if app_reach_here "$ah" && [[ -z $appd ]]; then
      steps+=("Fibre reads the chain at $aaddr, but no celestia-appd node runs on this box: set --app-grpc-address in ExecStart (or app_grpc_address in $fcfg) to \"<private address of the node it reads>:9090\", run \"systemctl daemon-reload\" if you changed the unit, $fapply. On that node, set [grpc] address = \"<its private address>:9090\" in app.toml, restart celestia-appd there at a moment you choose, and let only this box reach it: \"sudo ufw allow from $ip to any port 9090 proto tcp\"")
    elif app_reach_here "$ah" && [[ -r $app ]]; then
      rc=0
      node_app_grpc "$app" "$ah" "$aport" || rc=$?
      if ((rc == 2)); then
        steps+=("Fibre reads the node on this box at $aaddr, but that node serves no app gRPC ([grpc] enable = false in $app): under [grpc] set enable = true and address = \"127.0.0.1:$aport\", then restart celestia-appd at a moment you choose")
      elif ((rc == 1)); then
        steps+=("Fibre reads the node on this box at $aaddr, but that node serves its app gRPC on $APP_GRPC ([grpc] address in $app): set --app-grpc-address in ExecStart (or app_grpc_address in $fcfg) to \"$APP_DIAL\", run \"systemctl daemon-reload\" if you changed the unit, $fapply")
      fi
    elif ! app_reach_here "$ah"; then
      steps+=("Fibre reads the chain at $aaddr, a node on another box. On that box, [grpc] address in app.toml must be \"$(host_port "$ah" "$aport")\" (then restart celestia-appd there at a moment you choose), and only this box may reach that port: \"sudo ufw allow from $ip to any port $aport proto tcp\"")
    fi
  fi
  if [[ -n $saddr && $stls != mTLS ]]; then
    steps+=("Fibre signs through $saddr without mTLS, so the node's privval gRPC serves signatures with the validator key to whoever reaches it, every account on its box included: serve it with mTLS (priv_validator_grpc_cert_file, priv_validator_grpc_key_file and priv_validator_grpc_client_ca_file in that node's config.toml, then restart celestia-appd at a moment you choose, with tmkms running) and give this Fibre a client certificate from that CA (--signer-grpc-ca-file, --signer-grpc-cert-file and --signer-grpc-key-file in ExecStart, or signer_grpc_* in $fcfg), $fapply")
  fi
  # A node without a Fibre of its own may be the one a Fibre on another box reads.
  if [[ -n $appd && -z $fibre && -r $app && $ip != 127.0.0.1 && $(toml_get "$app" grpc enable) != false ]]; then
    g=$(toml_get "$app" grpc address)
    g=${g:-localhost:9090}
    if [[ $(lock_class "$(host_of "$g")") == loopback ]]; then
      steps+=("Only if a Fibre on another box reads this node (that box's survey says so): set [grpc] address = \"$(host_port "$ip" "${g##*:}")\" in $app (it is \"$g\", loopback), restart celestia-appd at a moment you choose, and let only that box reach it: \"sudo ufw allow from <that box's private address> to any port ${g##*:} proto tcp\"")
    fi
  fi

  printf '\nRead on this box:\n'
  if [[ -n $appd ]]; then
    also=$(survey_also appd "$appd")
    printf '  %-14s %s\n' celestia-appd "$appd.service ($(survey_state "$appd")), home $NODE_HOME: $role, as $why${also:+; also $also}"
  else
    printf '  %-14s %s\n' celestia-appd none
  fi
  if [[ -n $fibre ]]; then
    also=$(survey_also fibre "$fibre")
    printf '  %-14s %s\n' Fibre "$fibre.service ($fstate), home $fhome (on ${fmount:-unknown}), port ${fport:-unknown}; reads the chain at ${aaddr:-<not read>} ($where); signs through ${saddr:-<not read>} ($stls)${also:+; also $also}"
  else
    printf '  %-14s %s\n' Fibre none
  fi
  if [[ -n $tmkms ]]; then printf '  %-14s %s\n' tmkms "$tmkms.service ($(survey_state "$tmkms"))"; fi
  v=none
  if ((${#cands[@]} > 0)); then
    v=""
    for m in "${cands[@]}"; do
      read -r n ifc kind <<<"$m"
      v+="${v:+, }$n ($ifc, $kind)"
    done
  fi
  printf '  %-14s %s\n' addresses "$v"

  if ((${#groups[@]} == 0)); then
    printf '\nGroups: monitor, if this box is the monitor (it runs no celestia-appd, Fibre or tmkms)\n'
  elif [[ " ${groups[*]} " == *" monitor "* ]]; then
    printf '\nGroups: %s\n' "$(join "${groups[@]}")"
  else
    printf '\nGroups: %s (and monitor, when the monitor runs on this box too)\n' "$(join "${groups[@]}")"
  fi
  printf '\nEntry for hosts.yml, under all: hosts: (name the box as you like):\n'
  printf '%s\n' "${entry[@]}"
  if ((${#steps[@]} == 0)); then
    printf '\nYour steps on the Celestia side: none found.\n'
  else
    printf '\nYour steps on the Celestia side, at a moment you choose (celestia-mon never makes them):\n'
    n=0
    for v in "${steps[@]}"; do
      n=$((n + 1))
      printf '  %d. %s\n' "$n" "$v"
    done
  fi
  printf '\n%s\n' "Next: put the entry of every box in one hosts.yml in ~/celestia-mon-inventory (start from the example of your layout) and each box in its groups under children:, copy that folder to every box but the KMS box, then run on each: sudo ./install.sh"
}

# ------------------------------------------------------------------- main

conf=/etc/celestia-mon/doctor.conf
conf_given=false
survey_mode=false
overrides=()
while (($# > 0)); do
  case $1 in
    -h | --help)
      usage
      exit 0
      ;;
    --survey)
      survey_mode=true
      shift
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

# The survey reads the host, not the settings an earlier install wrote (unless --config names them).
if [[ $survey_mode == true && $conf_given == false ]]; then conf=""; fi
if [[ -n $conf && -r $conf ]]; then
  load_conf "$conf"
elif [[ $conf_given == true ]]; then
  echo "celestia-mon-doctor: cannot read $conf" >&2
  exit 2
fi
for ((i = 0; i < ${#overrides[@]}; i += 2)); do
  key=${overrides[i]}
  set_opt "${key//-/_}" "${overrides[i + 1]}"
done
if [[ $survey_mode == true ]]; then
  survey
  exit 0
fi

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
  follow_rpc_laddr
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
check_md
check_stack
check_rule_metrics
check_fibre_pipeline
check_memory
check_fibre_signer
check_node_lag
check_fibre_active
check_signer_binds
check_silences
check_deadman
check_signal
check_gov
check_fibre_ready
check_node_ports
check_service_users
check_fibre_app

if ((failed)); then
  echo "Some checks failed. celestia-mon-doctor changed nothing; apply the fixes above yourself."
  exit 1
fi
exit 0
