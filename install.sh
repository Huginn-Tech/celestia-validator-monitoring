#!/usr/bin/env bash
# install.sh: installs celestia-validator-monitoring on this host, or removes it.
#
#   sudo ./install.sh [--inventory PATH] [--host NAME] [--ask-vault-pass] [--yes] [--check] [--verbose]
#   sudo ./install.sh --uninstall [--purge] [--host NAME] [--yes] [--check] [--verbose]
#   sudo ./install.sh --survey
#   sudo ./install.sh --edit-secrets
#   sudo ./install.sh --edit-vault FILE
#
# Run it on each host, from a copy of this repository, with the inventory all hosts share:
# ~/celestia-mon-inventory of the user who runs sudo, unless --inventory names another. The
# host is the inventory host whose celestia_mon_private_ip is an address of this machine,
# unless --host names it. Every install is pre-checked and reversible. Steps: preflight checks
# and a dry run of ansible/site.yml for this host only, with a short plan; a confirmation
# (skipped with --yes); the real run; the command that removes it again; and what is still to
# do on this host, from celestia-mon-doctor.
#
# Nothing is installed with apt or pip: the uv binary pinned in versions.yml (its sha256
# checked) builds a throwaway virtualenv from the host's own python3, under
# /var/tmp/celestia-mon-install, and installs ansible-core and every package it needs from
# ansible/requirements.lock, each file checked against the sha256 there (--require-hashes).
# The vault password, and with --edit-vault ansible-vault's decrypted copy of the file (with
# --edit-secrets the copy being edited), go to /run/celestia-mon-install instead, in memory
# (/run is a tmpfs), never to the disk. Both directories are root's only and are removed when
# the script ends, also when it fails.
#
# The host is found, and the vault password asked for, after the virtualenv is built: its PyYAML
# reads hosts.yml and the host_vars and group_vars files that are not encrypted, without Ansible
# and without the password. The password is asked for when a file this host's run reads is
# encrypted with ansible-vault (with --ask-vault-pass, first, as before). A copy of the monitor's
# encrypted host_vars on another host stops the run: that host reads none of its secrets.
#
# The monitor's secrets (the receivers, the dead-man URL) live in the inventory's vault.yml, or
# outside the inventory in /etc/celestia-mon/secrets.yml (root's only), which --edit-secrets
# writes and the monitor's run reads as extra variables; both at once stop the run.
#
# --uninstall does the same with ansible/uninstall.yml, which reads no inventory settings:
# it needs neither the inventory nor the vault password, and takes the host's name from the
# doctor settings the install wrote. It removes the services, their binaries and the ufw rules
# the install added, and keeps the data, the settings and the system users, so a reinstall
# picks up where it left off. --uninstall --purge deletes those too, after a confirmation of
# its own that names them.
#
# --survey runs celestia-mon-doctor's survey from this copy (tools/doctor.sh --survey): it reads
# this host and prints its inventory entry and the steps that are the operator's. Nothing is
# downloaded, written or changed.
#
# --edit-vault opens an inventory's vault.yml in ansible-vault from the same throwaway
# virtualenv: it creates the file when it is missing, encrypts it when it is still plain text,
# and edits it when it is encrypted. --edit-secrets opens /etc/celestia-mon/secrets.yml, from a
# template when it is missing, in an editor, with nothing downloaded. Neither installs anything.

set -euo pipefail
export LC_ALL=C.UTF-8
unset PYTHONPATH PYTHONHOME

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
work=/var/tmp/celestia-mon-install
secrets=/run/celestia-mon-install
python=/usr/bin/python3
# The monitor's secrets outside the inventory (--edit-secrets).
secrets_file=/etc/celestia-mon/secrets.yml
# The Grafana role writes this file, root's only, when the inventory sets no admin password.
grafana_password_file=/etc/celestia-mon/grafana-admin-password

usage() {
  cat <<'EOF'
Usage:
  sudo ./install.sh [--inventory PATH] [--host NAME] [--ask-vault-pass] [--yes] [--check] [--verbose]
  sudo ./install.sh --uninstall [--purge] [--host NAME] [--yes] [--check] [--verbose]
  sudo ./install.sh --survey
  sudo ./install.sh --edit-secrets
  sudo ./install.sh --edit-vault FILE

Installs celestia-validator-monitoring on this machine, or removes it with --uninstall.
Run it on each host in turn, with the inventory every host shares. Every install is
pre-checked and reversible: a dry run shows the plan before anything changes, and
--uninstall removes it again.

  --inventory PATH   the inventory all hosts share (its directory, or the hosts.yml in it);
                     default: ~/celestia-mon-inventory of the user who runs sudo
  --host NAME        the inventory host this machine is (a host name, not a group);
                     default: the host whose celestia_mon_private_ip is an address of
                     this machine
  --ask-vault-pass   ask for the ansible-vault password first; without it, install.sh
                     asks when a file this host's run reads is vault-encrypted
  --yes              apply the plan without asking
  --check            dry run only: show the plan and change nothing
  --uninstall        remove celestia-mon's services, binaries and the ufw rules it
                     added; keep its data, settings and users for a reinstall. It
                     needs no inventory, and no --host on a host it installed on
  --purge            with --uninstall: also delete the data (/var/lib/celestia-mon),
                     the settings (/etc/celestia-mon) and the celestia-mon-* users
                     and groups, after a second confirmation
  --verbose          show Ansible's whole output (every task, result and diff) instead of
                     one line per part; an install keeps it in /var/log/celestia-mon either way
  --survey           read this machine and print its inventory entry and your steps on
                     it (celestia-mon-doctor --survey); nothing is downloaded or changed
  --edit-secrets     on the monitor host: create or edit its secrets (the receivers, the
                     dead-man URL) in /etc/celestia-mon/secrets.yml, root's only, which
                     its install reads (the editor is $EDITOR, else nano or vi), then exit
  --edit-vault FILE  create, encrypt or edit a vault.yml with ansible-vault (asks for
                     its password; the editor is $EDITOR, else nano or vi), then exit
EOF
}

die() {
  printf 'install.sh: %s\n' "$*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
}

join() { # TEXT... -> the texts joined with ", "
  local out="" x
  for x in "$@"; do out+="${out:+, }$x"; done
  printf '%s' "$out"
}

# Prints a value from versions.yml by its dotted key under celestia_mon_versions,
# for example uv.version or uv.sha256.x86_64. Fails when the key is missing.
pin() {
  awk -v want="$1" '
    /^[[:space:]]*(#|$)/ || /^---/ { next }
    {
      match($0, /^ */)
      depth = RLENGTH / 2
      line = substr($0, RLENGTH + 1)
      key = line
      sub(/:.*/, "", key)
      value = line
      if (!sub(/^[^:]*:[[:space:]]*/, "", value)) value = ""
      sub(/[[:space:]]+#.*/, "", value)
      gsub(/"/, "", value)
      path[depth] = key
      dotted = path[1]
      for (i = 2; i <= depth; i++) dotted = dotted "." path[i]
      if (depth >= 1 && dotted == want && value != "") { print value; found = 1; exit }
    }
    END { exit !found }
  ' "$repo/versions.yml"
}

fetch() { # URL FILE
  if command -v curl >/dev/null; then
    curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$2" "$1"
  elif command -v wget >/dev/null; then
    wget -q --https-only -O "$2" "$1"
  else
    die "needs curl or wget to download uv"
  fi
}

# Runs a playbook from ansible/ for this host only, on this machine, and keeps its full output in
# a log. The terminal shows the progress view of tools/install-output.awk (one line per part with
# what it changed, warnings and errors), or everything with --verbose. celestia_mon_install_host lets the playbook
# refuse a group name given as --host. An install also reads the monitor's secrets file, when there is one.
run_playbook() { # LOG PLAYBOOK [ansible-playbook arguments]
  local log=$1 book=$2 dry=0 arg
  shift 2
  for arg in "$@"; do [[ $arg != --check ]] || dry=1; done
  (cd "$repo/ansible" && "$work/venv/bin/ansible-playbook" "${inv_args[@]}" --limit "$host" \
    -e ansible_connection=local -e ansible_become=false -e "celestia_mon_install_host=$host" \
    "${vault[@]}" "${secret_vars[@]}" "$@" "$book") 2>&1 | tee "$log" |
    if $verbose; then cat; else awk -v dry="$dry" -f "$repo/tools/install-output.awk"; fi
}

# Keeps the full output of an install's dry run, run and doctor in /var/log/celestia-mon (root
# only), also when it failed: install.log, or check.log for --check; the next one replaces it.
# --uninstall keeps none, and --purge removes that directory.
logs=/var/log/celestia-mon
log_file=$logs/install.log
save_logs() {
  [[ $mode == install && -s $work/plan.log ]] || return 0
  if mkdir -p -- "$logs" && chmod 0700 -- "$logs"; then
    (umask 077 && cat -- "$work"/plan.log "$work"/run.log "$work"/doctor.log >"$log_file" 2>/dev/null) || true
  fi
}

# Stops after a failed playbook run. The progress view showed the failed task and its error; an
# install keeps the whole output in $logs, and a removal shows its last lines here.
run_failed() { # LOG MESSAGE
  if [[ $mode == install ]]; then
    die "$2 The whole output is in $log_file."
  fi
  $verbose || tail -n 40 -- "$1" >&2
  die "$2"
}

# Prints the changed= count of this host from a playbook log's recap.
changes() { # LOG
  awk -v h="$host" '
    $1 == h && $2 == ":" { for (i = 3; i <= NF; i++) if ($i ~ /^changed=/) { sub(/^changed=/, "", $i); n = $i } }
    END { print n + 0 }
  ' "$1"
}

# Prints the addresses of this machine, one a line, as the preflight reads them: lines such as
# "inet 10.0.0.5/24 ...", "inet6 fd00::5/64 ..." or "inet 10.0.0.5 peer 10.0.0.6/32 ...".
machine_addresses() {
  ip -o addr show 2>/dev/null | sed -nE 's/.* inet6? ([0-9A-Fa-f.:]+)[/ ].*/\1/p'
}

# Reads the inventory with the virtualenv's PyYAML, without Ansible and without the vault
# password: hosts.yml (every .yml, .yaml or .json file of an inventory directory) and the
# host_vars and group_vars next to it that are not encrypted. Prints tab-separated lines:
#   host NAME ADDRESS   every inventory host, with its celestia_mon_private_ip (- when none)
#   match NAME          a host whose celestia_mon_private_ip is an address of this machine
#   chosen NAME         the host this run is for: HOST, else the one host that matches
#   monitor NAME        the host of the monitor group
#   vault PATH          a file with vault-encrypted values that the run for the chosen host reads:
#                       on the monitor every one (it renders every host's settings), elsewhere
#                       its own, its groups' and those of the other hosts celestia-mon manages
#   foreign PATH        a vault-encrypted file of the monitor's on another host, which that
#                       host's run does not need
#   error PATH TEXT     a file it could not read
inventory_facts() { # HOST (or empty) ADDRESS...
  "$work/venv/bin/python" -I - "$inventory" "$@" <<'PY'
import ipaddress
import os
import sys

import yaml

inventory, chosen, here = sys.argv[1], sys.argv[2], sys.argv[3:]
ENCRYPTED = object()


def address(value):
    try:
        return ipaddress.ip_address(str(value).strip()).compressed
    except ValueError:
        return None


here = {address(a) for a in here} - {None}


class Tagged(str):
    """A value with a tag such as !vault or !unsafe: never an address."""
    vault = False


class Loader(yaml.SafeLoader):
    pass


def tagged(loader, suffix, node):
    value = Tagged(loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else "")
    value.vault = suffix == "vault"
    return value


Loader.add_multi_constructor("!", tagged)


def vaulted(value):
    if isinstance(value, Tagged):
        return value.vault
    if isinstance(value, dict):
        return any(vaulted(v) for v in value.values())
    if isinstance(value, list):
        return any(vaulted(v) for v in value)
    return False


def load(path):
    with open(path, "rb") as f:
        if f.read(15) == b"$ANSIBLE_VAULT;":
            return ENCRYPTED
        f.seek(0)
        return yaml.load(f, Loader=Loader)


hosts = {}      # name: {"address", "groups", "inline": a !vault value in its entry}
inline = set()  # groups whose vars in the inventory hold a !vault value
errors = []


def walk(name, group, parents):
    groups = parents + [name]
    if not isinstance(group, dict):
        return
    if vaulted(group.get("vars")):
        inline.add(name)
    members = group.get("hosts")
    for host, settings in members.items() if isinstance(members, dict) else []:
        entry = hosts.setdefault(str(host), {"address": None, "groups": {"all"}, "inline": False})
        entry["groups"].update(groups)
        if isinstance(settings, dict):
            ip = settings.get("celestia_mon_private_ip")
            if ip is not None and not isinstance(ip, Tagged):
                entry["address"] = str(ip)
            entry["inline"] = entry["inline"] or vaulted(settings)
    children = group.get("children")
    for child, sub in children.items() if isinstance(children, dict) else []:
        walk(str(child), sub, groups)


if os.path.isdir(inventory):
    base = inventory
    sources = [os.path.join(base, n) for n in sorted(os.listdir(base))
               if n.endswith((".yml", ".yaml", ".json")) and os.path.isfile(os.path.join(base, n))]
else:
    base, sources = os.path.dirname(inventory), [inventory]
for path in sources:
    try:
        data = load(path)
    except (OSError, yaml.YAMLError) as e:
        errors.append((path, " ".join(str(e).split())))
        continue
    if isinstance(data, dict):
        for name, group in data.items():
            walk(str(name), group, [] if name == "all" else ["all"])


def var_files(kind, name):
    """The files Ansible reads for host_vars/NAME or group_vars/NAME next to the inventory."""
    root = os.path.join(base, kind)
    found = [os.path.join(root, name + ext) for ext in ("", ".yml", ".yaml", ".json")
             if os.path.isfile(os.path.join(root, name + ext))]
    top = os.path.join(root, name)
    if os.path.isdir(top):
        for where, dirs, files in os.walk(top, followlinks=True):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            found += [os.path.join(where, f) for f in sorted(files) if not f.startswith(".") and not f.endswith("~")]
    return found


encrypted, lazy = [], []  # (kind, name, path): a whole file vault-encrypted; a plain file with a !vault value
for kind, names in (("host_vars", list(hosts)), ("group_vars", sorted({"all"}.union(*[e["groups"] for e in hosts.values()])))):
    for name in names:
        for path in var_files(kind, name):
            try:
                data = load(path)
            except (OSError, yaml.YAMLError) as e:
                errors.append((path, " ".join(str(e).split())))
                continue
            if data is ENCRYPTED:
                encrypted.append((kind, name, path))
                continue
            if isinstance(data, dict) and kind == "host_vars":
                ip = data.get("celestia_mon_private_ip")
                if ip is not None and not isinstance(ip, Tagged):
                    hosts[name]["address"] = str(ip)
            if vaulted(data):
                lazy.append((kind, name, path))

monitors = [h for h, e in hosts.items() if "monitor" in e["groups"]]
monitor = monitors[0] if len(monitors) == 1 else ""
matches = [h for h, e in hosts.items() if address(e["address"] or "") in here]
if not chosen and len(matches) == 1:
    chosen = matches[0]
everything = chosen == monitor or chosen not in hosts
own_groups = hosts[chosen]["groups"] if chosen in hosts else set()
# Hosts whose settings a run on another host reads (the monitor's address, each Fibre's endpoint).
managed = {h for h, e in hosts.items() if e["groups"] & {"validator", "backup_node", "fibre", "monitor"}}

out = [("host", h, e["address"] or "-") for h, e in hosts.items()]
out += [("match", h) for h in matches]
out += [("chosen", chosen), ("monitor", monitor)]
if chosen:
    for kind, name, path in encrypted:
        if kind == "host_vars" and name == monitor and not everything:
            out.append(("foreign", path))
        elif everything or (name in own_groups if kind == "group_vars" else (name == chosen or name in managed)):
            out.append(("vault", path))
    # Inline !vault values are decrypted only when a task reads them.
    for kind, name, path in lazy:
        if everything or name == chosen or (kind == "group_vars" and name in own_groups):
            out.append(("vault", path))
    out += [("vault", "%s (the entry of %s)" % (inventory, h)) for h, e in hosts.items()
            if e["inline"] and (everything or h == chosen)]
    out += [("vault", "%s (the vars of %s)" % (inventory, g)) for g in sorted(inline) if everything or g in own_groups]
out += [("error",) + e for e in errors]
for row in out:
    print("\t".join(row))
PY
}

# The template of /etc/celestia-mon/secrets.yml for --edit-secrets.
secrets_template() {
  cat <<'EOF'
---
# The secrets of the monitor host. Only this host needs them: its install (sudo ./install.sh)
# reads this file, and its settings win over the same settings in the inventory. Keep them
# here or in the inventory's vault.yml, not in both. Change it with: sudo ./install.sh --edit-secrets
# Only root reads it; sudo ./install.sh --uninstall keeps it, --uninstall --purge deletes it.

# Receivers: one list. Page alerts go to every receiver, message alerts to every receiver
# except PagerDuty, so list at least one of another type. Types: telegram, discord, slack,
# pagerduty, webhook (docs/alerts.md).
celestia_mon_receivers:
  - type: telegram
    bot_token: "000000000:REPLACE_ME"
    chat_id: -1000000000000
  # - type: pagerduty
  #   routing_key: "REPLACE_ME"

# Required: the https ping URL of your dead-man check, which alerts you when the pings stop.
celestia_mon_deadman_webhook_url: "https://deadman.example.invalid/ping/REPLACE_ME"

# Optional: Grafana's admin password, at least 16 characters. Without it, the first install
# generates one: sudo cat /etc/celestia-mon/grafana-admin-password
# celestia_mon_grafana_admin_password: "REPLACE_ME"

# Optional, with celestia_mon_announcements in the inventory (docs/alerts.md#celestiaannouncement):
# the token of the Discord bot that reads Celestia's announcements.
# celestia_mon_discord_bot_token: "REPLACE_ME"
EOF
}

inventory=""
host=""
mode=install
purge=false
ask_vault=false
yes=false
check=false
verbose=false
while (($# > 0)); do
  case $1 in
    --inventory)
      (($# >= 2)) || die "--inventory needs a file"
      inventory=$2
      shift 2
      ;;
    --host)
      (($# >= 2)) || die "--host needs an inventory host name"
      host=$2
      shift 2
      ;;
    --ask-vault-pass)
      ask_vault=true
      shift
      ;;
    --yes)
      yes=true
      shift
      ;;
    --check)
      check=true
      shift
      ;;
    --verbose)
      verbose=true
      shift
      ;;
    --uninstall)
      mode=uninstall
      shift
      ;;
    --survey)
      mode=survey
      shift
      ;;
    --edit-secrets)
      mode=edit-secrets
      shift
      ;;
    --edit-vault)
      (($# >= 2)) || die "--edit-vault needs a vault.yml file"
      mode=edit-vault
      vault_target=$2
      shift 2
      ;;
    --purge)
      purge=true
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done
if [[ $mode == edit-vault ]]; then
  if [[ -n $host || -n $inventory ]] || $yes || $check || $ask_vault || $purge; then
    die "--edit-vault takes only the vault file"
  fi
elif [[ $mode == survey || $mode == edit-secrets ]]; then
  if [[ -n $host || -n $inventory ]] || $yes || $check || $ask_vault || $purge || $verbose; then
    die "--$mode takes no other option"
  fi
fi
! $check || log_file=$logs/check.log
if $purge && [[ $mode != uninstall ]]; then
  die "--purge works only with --uninstall"
fi

((EUID == 0)) || die "run it as root: sudo ./install.sh ..."
if [[ $mode == edit-vault ]]; then
  [[ $(basename -- "$vault_target") =~ ^vault\.ya?ml$ ]] ||
    die "--edit-vault edits an inventory's vault.yml (host_vars/<monitor>/vault.yml): $vault_target"
  [[ -d $(dirname -- "$vault_target") ]] || die "no such directory: $(dirname -- "$vault_target")"
  # ansible-vault would write through a link to whatever file it names, as root.
  [[ ! -L $vault_target ]] || die "$vault_target is a symbolic link: name the vault file itself"
  [[ -t 0 ]] || die "--edit-vault needs a terminal"
  vault_target=$(realpath -m -- "$vault_target")
  host=edit-vault
fi
# Without --host, --uninstall removes celestia-mon from the host the install recorded in doctor's
# settings (inventory_host), else from this machine's name: uninstall.yml reads nothing else of it.
if [[ $mode == uninstall && -z $host ]]; then
  host=$(sed -n 's/^inventory_host=//p' /etc/celestia-mon/doctor.conf 2>/dev/null | head -n 1 || true)
  [[ -n $host ]] || host=$(hostname -s 2>/dev/null || true)
  [[ $host =~ ^[A-Za-z0-9._-]+$ ]] || host=this-host
fi
[[ -z $host || $host =~ ^[A-Za-z0-9._-]+$ ]] || die "--host takes one inventory host name: $host"
# uninstall.yml reads no inventory settings, so --uninstall runs with this host's name as the
# whole inventory: it needs neither the inventory file nor the vault password.
if [[ $mode == install ]]; then
  if [[ -z $inventory ]]; then
    home=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6 || true)
    inventory=${home:-/root}/celestia-mon-inventory
    [[ -e $inventory ]] ||
      die "no inventory at $inventory: put there the inventory all hosts share (its hosts.yml, with group_vars/ and" \
        "host_vars/ when it has them), or name it with --inventory. sudo ./install.sh --survey prints this host's entry for it."
  fi
  [[ -e $inventory ]] || die "inventory not found: $inventory"
  inventory=$(realpath -e -- "$inventory")
  inv_args=(-i "$inventory")
else
  inventory=""
  ask_vault=false
  inv_args=(-i "$host,")
fi
arch=$(uname -m)
case $arch in
  x86_64 | aarch64) ;;
  *) die "unsupported architecture $arch: celestia-mon runs on x86_64 and aarch64" ;;
esac
[[ -x $python ]] || die "$python is missing (Ubuntu 24.04 ships it)"
# ansible/requirements.lock is resolved for Python 3.12 and later (Ubuntu 24.04's python3).
"$python" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || die "the Ansible lock needs Python 3.12 or later at $python"

# Ansible runs everything in the repository and the inventory as root, so no other user may
# change them: each file must belong to root or to the user who runs sudo, none may be
# writable by every user, and a group-writable one only by root's group or that user's group.
# find -L follows symlinks, so the file or directory a link points to is checked the same way
# (Ansible follows links in the inventory and the roles); a link that points nowhere is skipped.
inv_dir=""
if [[ -d $inventory ]]; then
  inv_dir=$inventory
elif [[ -n $inventory ]]; then
  inv_dir=$(dirname -- "$inventory")
fi
checked=("$repo")
[[ -z $inv_dir ]] || checked+=("$inv_dir")
unsafe=$(find -L "${checked[@]}" -xdev ! -type l \( -perm -0002 -o \( ! -user 0 ! -user "${SUDO_UID:-0}" \) \
  -o \( -perm -0020 ! -group 0 ! -group "${SUDO_GID:-0}" \) \) -print -quit 2>/dev/null || true)
[[ -z $unsafe ]] ||
  die "$unsafe belongs to another user, or another user or group can write to it: only root or the user running sudo" \
    "may own these files and write to them (chown -R, chmod -R go-w)"

# The survey is celestia-mon-doctor's, from this copy, before anything is downloaded.
if [[ $mode == survey ]]; then
  exec bash "$repo/tools/doctor.sh" --survey
fi

# The secrets file is edited as a copy in memory (/run), so no editor's swap or backup file
# lands next to it, and replaces the file only when the copy changed.
if [[ $mode == edit-secrets ]]; then
  [[ -t 0 ]] || die "--edit-secrets needs a terminal"
  # A root-owned editor would write through a link to whatever file it names.
  [[ ! -L $secrets_file ]] || die "$secrets_file is a symbolic link: remove it, then run --edit-secrets again"
  [[ ! -e $secrets_file || -f $secrets_file ]] || die "$secrets_file is not a regular file"
  editor=${EDITOR:-${VISUAL:-}}
  [[ -n $editor ]] || editor=$(command -v nano || command -v vi || true)
  [[ -n $editor ]] || die "no editor found: set EDITOR"
  read -r -a editor_cmd <<<"$editor"
  if [[ -e $secrets || -L $secrets ]]; then
    die "$secrets exists: another install.sh may be running. If none is, remove it (sudo rm -rf $secrets) and run again."
  fi
  mkdir -m 0700 -- "$secrets" || die "cannot create $secrets"
  trap 'rm -rf -- "$secrets"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  draft=$secrets/secrets.yml
  (
    umask 077
    if [[ -f $secrets_file ]]; then cat -- "$secrets_file"; else secrets_template; fi >"$draft"
    cp -- "$draft" "$secrets/before"
  )
  step "Editing $secrets_file"
  "${editor_cmd[@]}" "$draft" || die "the editor stopped with an error; $secrets_file is as it was"
  if cmp -s -- "$draft" "$secrets/before"; then
    if [[ -f $secrets_file ]]; then echo "No change: $secrets_file is as it was."; else echo "Nothing saved: $secrets_file was not created."; fi
    exit 0
  fi
  [[ -d /etc/celestia-mon ]] || install -d -m 0755 -o root -g root /etc/celestia-mon
  if ! install -m 0600 -o root -g root -- "$draft" "$secrets_file.new" || ! mv -f -- "$secrets_file.new" "$secrets_file"; then
    die "could not write $secrets_file"
  fi
  printf 'Saved %s (root only). The install on this host reads it: sudo %q\n' "$secrets_file" "$repo/install.sh"
  exit 0
fi

# The secrets (receiver tokens, the dead-man URL) live in the inventory's vault.yml files, which
# must be encrypted with ansible-vault, never left in plain text, or in /etc/celestia-mon/secrets.yml.
vault_files=()
if [[ -n $inv_dir ]]; then
  while IFS= read -r -d '' vault_file; do
    [[ $(head -c 15 -- "$vault_file") == "\$ANSIBLE_VAULT;" ]] ||
      die "$vault_file is not encrypted: encrypt it (sudo $repo/install.sh --edit-vault $vault_file), then run again"
    vault_mode=$(stat -L -c %a -- "$vault_file")
    (((8#$vault_mode & 8#044) == 0)) || die "$vault_file is readable by group or other: chmod 600 $vault_file"
    vault_files+=("$vault_file")
  done < <(find -L "$inv_dir/host_vars" "$inv_dir/group_vars" -type f \( -name vault.yml -o -name vault.yaml \) -print0 2>/dev/null)
fi
if [[ $mode == install ]] && [[ -e $secrets_file || -L $secrets_file ]]; then
  [[ ! -L $secrets_file && -f $secrets_file ]] || die "$secrets_file is not a regular file: remove it, then run --edit-secrets"
  secrets_mode=$(stat -c %a -- "$secrets_file")
  if [[ $(stat -c %u -- "$secrets_file") != 0 ]] || (((8#$secrets_mode & 8#077) != 0)); then
    die "$secrets_file must belong to root and be readable by root only: sudo chown root: $secrets_file && sudo chmod 600 $secrets_file"
  fi
  ((${#vault_files[@]} == 0)) ||
    die "the monitor's secrets are in $secrets_file and in ${vault_files[0]}: keep one. To keep $secrets_file, move what the vault" \
      "holds there (sudo ./install.sh --edit-secrets) and delete the vault file from the inventory; to keep the vault, delete" \
      "$secrets_file (sudo rm $secrets_file)."
fi

uv_version=$(pin uv.version) || die "versions.yml has no uv.version"
uv_sha256=$(pin "uv.sha256.$arch") || die "versions.yml has no uv.sha256.$arch"
ansible_core=$(pin ansible_core) || die "versions.yml has no ansible_core"
[[ $uv_sha256 =~ ^[0-9a-f]{64}$ ]] || die "versions.yml: uv.sha256.$arch is not a sha256"
[[ $ansible_core =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "versions.yml: ansible_core is not a version"

# ansible-core and every package it needs, each with the sha256 of its files on PyPI. The
# lock workflow (.github/workflows/lock.yml) generates it from versions.yml.
lock=$repo/ansible/requirements.lock
[[ -f $lock ]] || die "ansible/requirements.lock is missing"
grep -Eq "^ansible-core==${ansible_core//./[.]}( |;|\$)" "$lock" ||
  die "ansible/requirements.lock is not for ansible-core $ansible_core (versions.yml): generate it with the lock workflow (GitHub Actions > lock) and commit it"

# The password stays in this shell: it is written to a file under $secrets below, never exported.
vault=()
vault_pass=""
if $ask_vault; then
  [[ -t 0 ]] || die "--ask-vault-pass needs a terminal"
  IFS= read -rs -p "Vault password: " vault_pass
  echo
fi

# Fresh directories only root can enter; the script stops rather than reuse one it did not create.
for d in "$work" "$secrets"; do
  if [[ -e $d || -L $d ]]; then
    die "$d exists: another install.sh may be running. If none is, remove it (sudo rm -rf $d) and run again."
  fi
done
mkdir -m 0700 -- "$work" || die "cannot create $work"
if ! mkdir -m 0700 -- "$secrets"; then
  rm -rf -- "$work"
  die "cannot create $secrets"
fi
trap 'save_logs; rm -rf -- "$work" "$secrets"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
mount_options=$(findmnt -n -o OPTIONS --target "$work" || true)
if [[ ,$mount_options, == *,noexec,* ]]; then
  die "/var/tmp is mounted noexec, so uv and Ansible cannot run from $work"
fi
# uv, the virtualenv, and Ansible's temporary copy of each download (Grafana's is the largest).
need_mib=1024
[[ $mode == install ]] || need_mib=512
free_mib=$(df --output=avail -BM -- "$work" | awk 'NR == 2 { print $1 + 0 }') || free_mib=0
free_mib=${free_mib:-0}
((free_mib >= need_mib)) || die "/var/tmp has $free_mib MiB free, and install.sh needs $need_mib MiB there; free some space"

step "Preparing Ansible in $work (uv $uv_version, ansible-core $ansible_core)"
uv_tarball=uv-$arch-unknown-linux-gnu.tar.gz
fetch "https://github.com/astral-sh/uv/releases/download/$uv_version/$uv_tarball" "$work/$uv_tarball" ||
  die "could not download uv $uv_version from GitHub"
printf '%s  %s\n' "$uv_sha256" "$work/$uv_tarball" | sha256sum --check --status ||
  die "$uv_tarball does not match the sha256 in versions.yml"
tar -xzf "$work/$uv_tarball" -C "$work" --strip-components=1 --no-same-owner "uv-$arch-unknown-linux-gnu/uv" ||
  die "could not unpack $uv_tarball"
export UV_CACHE_DIR=$work/uv-cache UV_PYTHON_DOWNLOADS=never UV_NO_CONFIG=1 UV_NO_PROGRESS=1
"$work/uv" venv --quiet --python "$python" "$work/venv" || die "uv could not create a virtualenv from $python"
# Only the versions in the lock, and only files whose sha256 the lock lists: uv refuses
# anything else. Wheels only, so no package build code runs as root.
"$work/uv" pip install --quiet --python "$work/venv/bin/python" --require-hashes --only-binary :all: -r "$lock" ||
  die "could not install Ansible from ansible/requirements.lock: a download failed, or a file did not match its sha256 there (see above)"

# Which inventory host this machine is, and what of the inventory its run reads.
secret_vars=()
if [[ $mode == install ]]; then
  mapfile -t addresses < <(machine_addresses)
  declare -A address_of=()
  known=() matched=() needs_vault=() foreign=() unreadable=()
  chosen="" monitor_host=""
  # With --host the facts only add to the run: an inventory they cannot be read from runs as before.
  if ! inventory_facts "$host" "${addresses[@]}" >"$work/inventory-facts"; then
    [[ -n $host ]] || die "could not read the inventory $inventory (the error is above): fix it, or name this host with --host"
    : >"$work/inventory-facts"
  fi
  while IFS=$'\t' read -r kind name value; do
    case $kind in
      host)
        address_of[$name]=$value
        known+=("$name $value")
        ;;
      match) matched+=("$name (${address_of[$name]:-})") ;;
      chosen) chosen=$name ;;
      monitor) monitor_host=$name ;;
      vault) needs_vault+=("$name") ;;
      foreign) foreign+=("$name") ;;
      error) unreadable+=("$name: $value") ;;
    esac
  done <"$work/inventory-facts"
  if [[ -z $host ]]; then
    ((${#unreadable[@]} == 0)) || die "cannot read the inventory, ${unreadable[0]}"
    ((${#matched[@]} < 2)) ||
      die "several hosts of $inventory have an address of this machine as their celestia_mon_private_ip" \
        "($(join "${matched[@]}")): name the one this machine is with --host"
    [[ -n $chosen ]] ||
      die "no host of $inventory has an address of this machine (${addresses[*]:-none}) as its celestia_mon_private_ip" \
        "(the inventory: $(join "${known[@]}")). Set it to this machine's private address in this host's entry, or" \
        "name the host with --host. On the KMS host celestia-mon installs nothing, unless celestia_mon_kms_scrape is set."
    host=$chosen
    [[ $host =~ ^[A-Za-z0-9._-]+$ ]] || die "the inventory host $host has a name install.sh does not take: name it with letters, digits, . _ or -"
  fi
  if [[ -n ${address_of[$host]:-} && ${address_of[$host]} != - ]]; then
    echo "Installing as $host (${address_of[$host]}) from $inventory"
  else
    echo "Installing as $host from $inventory"
  fi
  # A copy of the monitor's vault on another host would stop Ansible there with a vault error,
  # though that host reads none of the monitor's secrets.
  if ((${#foreign[@]} > 0)) && ! $ask_vault; then
    die "this host does not need ${foreign[0]}: delete it from this copy. It holds the secrets of the monitor host" \
      "($monitor_host), which only the monitor's install reads."
  fi
  if ((${#needs_vault[@]} > 0)) && ! $ask_vault; then
    [[ -t 0 ]] || die "${needs_vault[0]} holds vault-encrypted values that the install of $host reads: run install.sh on a terminal," \
      "where it asks for the vault password"
    echo "${needs_vault[0]} holds vault-encrypted values that the install of $host reads."
    IFS= read -rs -p "Vault password: " vault_pass
    echo
    ask_vault=true
  fi
  # The monitor's secrets outside the inventory: read by the monitor's install only.
  if [[ -e $secrets_file ]]; then
    if [[ -n $monitor_host && $host != "$monitor_host" ]]; then
      die "$secrets_file holds the monitor's secrets, and this machine is $host, not the monitor host ($monitor_host):" \
        "its install reads none. Delete it here (sudo rm $secrets_file) and keep the secrets on $monitor_host only."
    fi
    secret_vars=(-e "@$secrets_file")
    echo "The monitor's secrets: $secrets_file"
  fi
fi

# Everything Ansible writes for itself stays under $work.
mkdir -p -- "$work/ansible"
export ANSIBLE_CONFIG=$repo/ansible/ansible.cfg
export ANSIBLE_ROLES_PATH=$repo/ansible/roles
export ANSIBLE_HOME=$work/ansible
export ANSIBLE_LOCAL_TEMP=$work/ansible/tmp
export ANSIBLE_REMOTE_TEMP=$work/ansible/remote-tmp
export PYTHONDONTWRITEBYTECODE=1
if $ask_vault; then
  # A plain file only root can read: not executable, so Ansible reads the password from it.
  (umask 077 && printf '%s\n' "$vault_pass" >"$secrets/vault-pass")
  vault=(--vault-password-file "$secrets/vault-pass")
fi
unset vault_pass

# ansible-vault asks for the password itself. sudo resets EDITOR unless it is kept, so nano
# or vi stands in. The file stays readable by its owner only. ansible-vault puts the decrypted
# copy it opens in the editor under ANSIBLE_LOCAL_TEMP, here in memory.
if [[ $mode == edit-vault ]]; then
  export ANSIBLE_LOCAL_TEMP=$secrets/tmp
  editor=${EDITOR:-${VISUAL:-}}
  [[ -n $editor ]] || editor=$(command -v nano || command -v vi || true)
  [[ -n $editor ]] || die "no editor found: set EDITOR"
  if [[ ! -e $vault_target ]]; then
    action=create
  elif [[ $(head -c 15 -- "$vault_target") == "\$ANSIBLE_VAULT;" ]]; then
    action=edit
  else
    action=encrypt
  fi
  step "ansible-vault $action $vault_target"
  (umask 077 && EDITOR=$editor "$work/venv/bin/ansible-vault" "$action" -- "$vault_target") ||
    die "ansible-vault $action stopped; $vault_target is as it was"
  chmod 0600 -- "$vault_target"
  if [[ $action == encrypt ]]; then
    printf 'Encrypted. Change it later with: sudo %q --edit-vault %q\n' "$repo/install.sh" "$vault_target"
  fi
  exit 0
fi

book=site.yml
[[ $mode == install ]] || book=uninstall.yml
# uninstall.yml keeps the data, settings and users unless it is told to purge them.
purge_args=()
if $purge; then
  purge_args=(-e celestia_mon_uninstall_purge=true)
fi

step "Checking $host and planning the changes (dry run: nothing is changed)"
run_playbook "$work/plan.log" "$book" --check --diff -e "celestia_mon_plan_file=$work/plan.txt" "${purge_args[@]}" ||
  run_failed "$work/plan.log" "the checks or the dry run failed, and nothing was changed. The reason is above."

step "Plan for $host"
if [[ ! -s $work/plan.txt ]]; then
  echo "celestia-mon installs nothing on $host (a kms host without celestia_mon_kms_scrape, or a host in no celestia-mon group)."
  exit 0
fi
cat "$work/plan.txt"
planned=$(changes "$work/plan.log")
if ((planned == 0)); then
  echo "The dry run finds nothing to change."
else
  echo "The dry run reports $planned change(s); the details are above."
fi

if $check; then
  step "Dry run only (--check): nothing was changed."
  exit 0
fi
if ! $yes; then
  [[ -t 0 ]] || die "no terminal to confirm on: pass --yes to apply the plan without asking"
  read -r -p "Apply this to $host? [y/N] " answer
  case $answer in
    [yY] | [yY][eE][sS]) ;;
    *)
      echo "Stopped: nothing was changed."
      exit 0
      ;;
  esac
  # --purge deletes what a plain --uninstall keeps, for good: it asks once more, naming it.
  if $purge; then
    cat <<'PURGE'

--purge also deletes, and this cannot be undone:
  /var/lib/celestia-mon   Prometheus's data, Grafana's database and the record of the install this host is in
  /etc/celestia-mon       celestia-mon's settings, the secrets of --edit-secrets among them
  the celestia-mon-* system users and groups
PURGE
    read -r -p "Delete them on $host too? [y/N] " answer
    case $answer in
      [yY] | [yY][eE][sS]) ;;
      *)
        echo "Stopped: nothing was changed. Without --purge, --uninstall keeps them."
        exit 0
        ;;
    esac
  fi
fi

if [[ $mode == uninstall ]]; then
  step "Removing celestia-mon from $host"
  run_playbook "$work/run.log" uninstall.yml "${purge_args[@]}" ||
    run_failed "$work/run.log" "the removal stopped with an error (above). Fix the cause and run install.sh --uninstall again."
  step "celestia-mon is removed from $host ($(changes "$work/run.log") change(s))."
  if ! $purge; then
    echo "Removed with the services: the settings files that hold secrets (Alertmanager's config, Grafana's admin"
    echo "password and datasource, the API passwords); a reinstall writes them again from the inventory and the secrets."
    echo "Kept for a reinstall: /var/lib/celestia-mon (data, and the record of the install this host is in),"
    echo "/etc/celestia-mon (the other settings, the secrets of --edit-secrets and a generated Grafana password among"
    echo "them) and the celestia-mon-* users."
    printf 'To delete them too:\n  sudo %q --uninstall --purge\n' "$repo/install.sh"
  fi
  exit 0
fi

step "Installing celestia-mon on $host"
run_playbook "$work/run.log" site.yml ||
  run_failed "$work/run.log" "the install stopped with an error (above). Fix the cause and run install.sh again; it continues where it stopped."
made=$(changes "$work/run.log")
if ((made == 0)); then
  echo "No changes: $host already matches the inventory."
else
  echo "$made change(s) made."
fi
# The Alertmanager role sends it when the run changed the receivers.
if awk '/^TASK \[celestia_mon_alertmanager : Post the test notification/ { t = 1; next }
        t && /^ok:/ { found = 1 } t && /^TASK / { t = 0 } END { exit !found }' "$work/run.log"; then
  echo "A test message went to the chat receivers (Telegram, Discord, Slack, webhooks; not PagerDuty)."
  echo "If it does not arrive within a minute, check their settings in the secrets or the vault."
fi
# The Grafana role reads the generated password only when celestia_mon_grafana_admin_password is unset.
if awk '/^TASK \[celestia_mon_grafana : Read the generated Grafana admin password/ { t = 1; next }
        t && /^ok:/ { found = 1 } t && /^TASK / { t = 0 } END { exit !found }' "$work/run.log"; then
  echo "Grafana's admin password (user admin), generated by the install: sudo cat $grafana_password_file"
fi

step "To remove celestia-mon from $host again"
# The removal needs no inventory, no vault password and no --host.
printf '  sudo %q --uninstall\n' "$repo/install.sh"
echo "  removes the services, their binaries and the ufw rules the install added, and keeps the data,"
echo "  settings and users for a reinstall; add --purge to delete those too."

# doctor exits 0 when no check failed, also with WARN lines, which are worth fixing too. Its whole
# report goes to the log; here only the lines that ask for something.
step "Still to do on this box"
set +e
/usr/local/bin/celestia-mon-doctor >"$work/doctor.log" 2>&1
status=$?
set -e
todo=$(grep -E '^(FAIL|WARN|NOT YET) ' "$work/doctor.log" || true)
if [[ -n $todo ]]; then
  printf '%s\n' "$todo"
  echo "Each line is a celestia-mon-doctor check and ends with its fix, for you to apply at a moment you choose;"
  echo "some clear by themselves once the other hosts are installed. Then run: sudo celestia-mon-doctor"
elif ((status == 0)); then
  echo "Nothing: every celestia-mon-doctor check passed."
fi
if ((status > 1)); then
  echo "celestia-mon-doctor exited with status $status:"
  tail -n 5 -- "$work/doctor.log"
fi
echo "doctor's whole report and the whole output of this install: $log_file"
