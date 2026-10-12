#!/usr/bin/env bash
# install.sh: installs celestia-validator-monitoring on this host, or removes it.
#
#   sudo ./install.sh --inventory FILE --host NAME [--ask-vault-pass] [--yes] [--check]
#   sudo ./install.sh --uninstall [--purge] [--inventory FILE] --host NAME [--yes] [--check]
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
# That directory is removed when the script ends, also when it fails.
#
# --uninstall does the same with ansible/uninstall.yml, which reads no inventory settings:
# it needs neither the inventory nor the vault password. It removes the services, their
# binaries and the ufw rules the install added, and keeps the data, the settings and the
# system users, so a reinstall picks up where it left off. --uninstall --purge deletes
# those too, after a confirmation of its own that names them.

set -euo pipefail
export LC_ALL=C.UTF-8
unset PYTHONPATH PYTHONHOME

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
work=/var/tmp/celestia-mon-install
python=/usr/bin/python3

usage() {
  cat <<'EOF'
Usage:
  sudo ./install.sh --inventory FILE --host NAME [--ask-vault-pass] [--yes] [--check]
  sudo ./install.sh --uninstall [--purge] [--inventory FILE] --host NAME [--yes] [--check]

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

# Runs a playbook from ansible/ for this host only, on this machine, and keeps its output in a log.
# celestia_mon_install_host lets the playbook refuse a group name given as --host.
run_playbook() { # LOG PLAYBOOK [ansible-playbook arguments]
  local log=$1 book=$2
  shift 2
  (cd "$repo/ansible" && "$work/venv/bin/ansible-playbook" "${inv_args[@]}" --limit "$host" \
    -e ansible_connection=local -e ansible_become=false -e "celestia_mon_install_host=$host" \
    "${vault[@]}" "$@" "$book") 2>&1 | tee "$log"
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
    --uninstall)
      mode=uninstall
      shift
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
if [[ -z $host || ($mode == install && -z $inventory) ]]; then
  usage >&2
  exit 2
fi
if $purge && [[ $mode != uninstall ]]; then
  die "--purge works only with --uninstall"
fi

((EUID == 0)) || die "run it as root: sudo ./install.sh ..."
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
# change them: each file must belong to root or to the user who runs sudo, and none may be
# writable by every user.
checked=("$repo")
if [[ -d $inventory ]]; then
  checked+=("$inventory")
elif [[ -n $inventory ]]; then
  checked+=("$(dirname -- "$inventory")")
fi
unsafe=$(find "${checked[@]}" -xdev ! -type l \( -perm -0002 -o \( ! -user 0 ! -user "${SUDO_UID:-0}" \) \) \
  -print -quit 2>/dev/null || true)
[[ -z $unsafe ]] ||
  die "$unsafe belongs to another user or is writable by every user: only root or the user running sudo may own these files (chown -R, chmod -R o-w)"

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

# The password stays in this shell: it is written to a file under $work below, never exported.
vault=()
vault_pass=""
if $ask_vault; then
  [[ -t 0 ]] || die "--ask-vault-pass needs a terminal"
  IFS= read -rs -p "Vault password: " vault_pass
  echo
fi

# A fresh directory only root can enter; the script stops rather than reuse one it did not create.
if [[ -e $work || -L $work ]]; then
  die "$work exists: another install.sh may be running. If none is, remove it (sudo rm -rf $work) and run again."
fi
mkdir -m 0700 -- "$work" || die "cannot create $work"
trap 'rm -rf -- "$work"' EXIT
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
  (umask 077 && printf '%s\n' "$vault_pass" >"$work/vault-pass")
  vault=(--vault-password-file "$work/vault-pass")
fi
unset vault_pass

book=site.yml
[[ $mode == install ]] || book=uninstall.yml
# uninstall.yml keeps the data, settings and users unless it is told to purge them.
purge_args=()
if $purge; then
  purge_args=(-e celestia_mon_uninstall_purge=true)
fi

step "Checking $host and planning the changes (dry run: nothing is changed)"
run_playbook "$work/plan.log" "$book" --check --diff -e "celestia_mon_plan_file=$work/plan.txt" "${purge_args[@]}" ||
  die "the checks or the dry run failed, and nothing was changed. The reason is above."

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
    die "the removal stopped with an error (above). Fix the cause and run install.sh --uninstall again."
  step "celestia-mon is removed from $host ($(changes "$work/run.log") change(s))."
  if ! $purge; then
    echo "Kept for a reinstall: /var/lib/celestia-mon (data), /etc/celestia-mon (settings) and the celestia-mon-* users."
    printf 'To delete them too:\n  sudo %q --uninstall --purge --host %q\n' "$repo/install.sh" "$host"
  fi
  exit 0
fi

step "Installing celestia-mon on $host"
run_playbook "$work/run.log" site.yml ||
  die "the install stopped with an error (above). Fix the cause and run install.sh again; it continues where it stopped."
made=$(changes "$work/run.log")
if ((made == 0)); then
  echo "No changes: $host already matches the inventory."
else
  echo "$made change(s) made."
fi

step "celestia-mon-doctor on $host"
status=0
/usr/local/bin/celestia-mon-doctor || status=$?
case $status in
  0) echo "doctor: every check passed." ;;
  1) echo "doctor: fix the FAIL lines above (each ends with the fix), then run: sudo celestia-mon-doctor" ;;
  *) echo "doctor exited with status $status." ;;
esac

step "To remove celestia-mon from $host again"
# The removal needs no inventory and no vault password.
printf '  sudo %q --uninstall --host %q\n' "$repo/install.sh" "$host"
echo "  removes the services, their binaries and the ufw rules the install added, and keeps the data,"
echo "  settings and users for a reinstall; add --purge to delete those too."
