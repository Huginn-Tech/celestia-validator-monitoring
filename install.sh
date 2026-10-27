#!/usr/bin/env bash
# install.sh: installs celestia-validator-monitoring on this host, or removes it.
#
#   sudo ./install.sh --inventory FILE --host NAME [--ask-vault-pass] [--yes] [--check] [--verbose]
#   sudo ./install.sh --uninstall [--purge] [--inventory FILE] --host NAME [--yes] [--check] [--verbose]
#   sudo ./install.sh --edit-vault FILE
#
# Run it on each host, from a copy of this repository, with the inventory all hosts share.
# Every install is pre-checked and reversible. Steps: preflight checks and a dry run of
# ansible/site.yml for this host only, with a short plan; a confirmation (skipped with
# --yes); the real run; celestia-mon-doctor; and the command that removes it again.
#
# Nothing is installed with apt or pip: the uv binary pinned in versions.yml (its sha256
# checked) builds a throwaway virtualenv from the host's own python3, under
# /var/tmp/celestia-mon-install, and installs ansible-core and every package it needs from
# ansible/requirements.lock, each file checked against the sha256 there (--require-hashes).
# The vault password, and with --edit-vault ansible-vault's decrypted copy of the file, go to
# /run/celestia-mon-install instead, in memory (/run is a tmpfs), never to the disk. Both
# directories are root's only and are removed when the script ends, also when it fails.
#
# --uninstall does the same with ansible/uninstall.yml, which reads no inventory settings:
# it needs neither the inventory nor the vault password. It removes the services, their
# binaries and the ufw rules the install added, and keeps the data, the settings and the
# system users, so a reinstall picks up where it left off. --uninstall --purge deletes
# those too, after a confirmation of its own that names them.
#
# --edit-vault opens an inventory's vault.yml in ansible-vault from the same throwaway
# virtualenv: it creates the file when it is missing, encrypts it when it is still plain text,
# and edits it when it is encrypted. Nothing is installed.

set -euo pipefail
export LC_ALL=C.UTF-8
unset PYTHONPATH PYTHONHOME

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
work=/var/tmp/celestia-mon-install
secrets=/run/celestia-mon-install
python=/usr/bin/python3

usage() {
  cat <<'EOF'
Usage:
  sudo ./install.sh --inventory FILE --host NAME [--ask-vault-pass] [--yes] [--check] [--verbose]
  sudo ./install.sh --uninstall [--purge] [--inventory FILE] --host NAME [--yes] [--check] [--verbose]
  sudo ./install.sh --edit-vault FILE

Installs celestia-validator-monitoring on this machine as inventory host NAME, or
removes it with --uninstall. Run it on each host in turn. Every install is pre-checked
and reversible: a dry run shows the plan before anything changes, and --uninstall
removes it again.

  --inventory FILE   the inventory all hosts share (a hosts.yml, or its directory);
                     not needed with --uninstall
  --host NAME        the inventory host this machine is (a host name, not a group)
  --ask-vault-pass   ask for the ansible-vault password, when this host's inventory
                     files hold vault-encrypted values (not needed with --uninstall)
  --yes              apply the plan without asking
  --check            dry run only: show the plan and change nothing
  --uninstall        remove celestia-mon's services, binaries and the ufw rules it
                     added; keep its data, settings and users for a reinstall
  --purge            with --uninstall: also delete the data (/var/lib/celestia-mon),
                     the settings (/etc/celestia-mon) and the celestia-mon-* users
                     and groups, after a second confirmation
  --verbose          show Ansible's whole output (every task, result and diff) instead of
                     one line per part; an install keeps it in /var/log/celestia-mon either way
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
# refuse a group name given as --host.
run_playbook() { # LOG PLAYBOOK [ansible-playbook arguments]
  local log=$1 book=$2 dry=0 arg
  shift 2
  for arg in "$@"; do [[ $arg != --check ]] || dry=1; done
  (cd "$repo/ansible" && "$work/venv/bin/ansible-playbook" "${inv_args[@]}" --limit "$host" \
    -e ansible_connection=local -e ansible_become=false -e "celestia_mon_install_host=$host" \
    "${vault[@]}" "$@" "$book") 2>&1 | tee "$log" |
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
elif [[ -z $host || ($mode == install && -z $inventory) ]]; then
  usage >&2
  exit 2
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
[[ $host =~ ^[A-Za-z0-9._-]+$ ]] || die "--host takes one inventory host name: $host"
# uninstall.yml reads no inventory settings, so --uninstall runs with this host's name as the
# whole inventory: it needs neither the inventory file nor the vault password.
if [[ $mode == install ]]; then
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

# The secrets (receiver tokens, the dead-man URL, the Grafana password) live in the inventory's
# vault.yml files, which must be encrypted with ansible-vault, never left in plain text.
if [[ -n $inv_dir ]]; then
  while IFS= read -r -d '' vault_file; do
    [[ $(head -c 15 -- "$vault_file") == "\$ANSIBLE_VAULT;" ]] ||
      die "$vault_file is not encrypted: encrypt it with ansible-vault encrypt $vault_file, then run again with --ask-vault-pass"
    vault_mode=$(stat -L -c %a -- "$vault_file")
    (((8#$vault_mode & 8#044) == 0)) || die "$vault_file is readable by group or other: chmod 600 $vault_file"
  done < <(find -L "$inv_dir/host_vars" "$inv_dir/group_vars" -type f \( -name vault.yml -o -name vault.yaml \) -print0 2>/dev/null)
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
  /var/lib/celestia-mon   Prometheus's data and Grafana's database
  /etc/celestia-mon       celestia-mon's settings
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
    echo "password and datasource, the API passwords); a reinstall writes them again from the inventory."
    echo "Kept for a reinstall: /var/lib/celestia-mon (data), /etc/celestia-mon (the other settings) and the celestia-mon-* users."
    printf 'To delete them too:\n  sudo %q --uninstall --purge --host %q\n' "$repo/install.sh" "$host"
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
  echo "If it does not arrive within a minute, check their settings in the vault."
fi

step "celestia-mon-doctor on $host"
# doctor exits 0 when no check failed, also with WARN lines, which are worth fixing too.
set +e
/usr/local/bin/celestia-mon-doctor | tee "$work/doctor.log"
status=${PIPESTATUS[0]}
set -e
case $status in
  0)
    if grep -q '^WARN' "$work/doctor.log"; then
      echo "doctor: no check failed, but read the WARN lines above (each ends with its fix)."
    else
      echo "doctor: every check passed."
    fi
    ;;
  1) echo "doctor: fix the FAIL lines above (each ends with the fix), then run: sudo celestia-mon-doctor" ;;
  *) echo "doctor exited with status $status." ;;
esac

echo "The whole output of this install: $log_file"

step "To remove celestia-mon from $host again"
# The removal needs no inventory and no vault password.
printf '  sudo %q --uninstall --host %q\n' "$repo/install.sh" "$host"
echo "  removes the services, their binaries and the ufw rules the install added, and keeps the data,"
echo "  settings and users for a reinstall; add --purge to delete those too."
