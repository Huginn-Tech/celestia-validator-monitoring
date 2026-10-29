# Reference

The details behind the [quickstart](quickstart.md): every setting, the ports and files, how `install.sh` works, the optional alerts, upgrades and removal. [doctor.md](doctor.md) has `doctor`'s checks, [layouts.md](layouts.md) the layouts.

## Settings

The inventory is one folder, `~/celestia-mon-inventory` by default: `hosts.yml` puts each box in its groups and holds its settings, `group_vars/all.yml` the shared ones. A setting of one box may also live in `host_vars/<name>.yml`. The box names are yours to choose. Which node signs and which Fibre runs are read from the node metrics and from systemd, never from the inventory, so after a tmkms repoint or a Fibre failover the inventory stays as it is ([layouts.md](layouts.md#when-a-role-moves)).

### Each box

| Setting | Boxes | Meaning |
|---|---|---|
| `celestia_mon_private_ip` | every box you install on | The private address where its services listen and Prometheus scrapes: a WireGuard, VLAN or Tailscale address, or `127.0.0.1` when everything runs on one box. Required, and it must be an address of that box: the install checks it, and `install.sh` finds the box by it. |
| `celestia_mon_data_mounts` | every box you install on | The mount points DiskLow and the dashboard watch for free space and read-only state: the node's data disk, tmkms's state, and on a monitor of its own the disk of Prometheus's data (`/var/lib/celestia-mon`). Default `[/]`. Each entry must be a mount point, as `findmnt -T <path>` names the one that holds a path: the install stops on any other path and prints the list to set, because node_exporter reports each disk by its mount point and a directory on another disk would match nothing. |
| `celestia_mon_units` | a box whose units have other names | This box's systemd unit names, without `.service`: `{appd: celestia-appd, fibre: fibre, tmkms: tmkms}` by default. Set the whole map: it replaces the default one. It applies to that box only; to rename a unit on every box, set it under `all: vars:` or in `group_vars/all.yml`. The install stops when a unit this box runs (celestia-appd on a node box, Fibre on a Fibre box, tmkms on a scraped KMS box) is not loaded in systemd, names the loaded units that run that binary (`celestia-appd`, or `cosmovisor` for it, `fibre`, `tmkms`) and prints the map to set. The alerts and the dashboard watch each box under its own names. |
| `celestia_mon_fibre_mount` | fibre | Mount point of the Fibre store, on the standby too. |
| `celestia_mon_fibre_public_endpoint` | fibre | The `host:port` that `celestia-appd tx valaddr set-host` registers while this box's Fibre is the active one. `doctor` accepts a registration of any Fibre box's endpoint, and on a box with both the validator and a Fibre install it checks that the registration names this box exactly while its Fibre runs. |
| `celestia_mon_kms_scrape` | kms | `true` installs node_exporter on the KMS box, so tmkms and its disks are watched; that box then needs `celestia_mon_private_ip` too. Default `false`: nothing is installed there. |
| `celestia_mon_node_home` | validator, backup_node | celestia-appd home, for `doctor`. Default: found from the unit. |
| `celestia_mon_node_metrics_port`, `celestia_mon_node_rpc_port` | validator, backup_node | celestia-appd's metrics and RPC ports, 26660 and 26657 by default. Set them in the entry of a box whose node uses other ports; the other boxes keep theirs. `doctor` reads the RPC where `[rpc] laddr` in the node's `config.toml` points. |
| `celestia_mon_fibre_port` | fibre | Fibre's own port, 7980 by default, where the monitor asks each Fibre box for its [upload readiness](#upload-readiness). Set it for a box whose Fibre listens on another port. |
| `celestia_mon_link_mbps` | every box you install on | Optional. The link speed in Mbit/s for the dashboard's Network peak. Set it when the box reports no NIC speed (common on VMs), has more than one physical NIC, or its plan is below the port speed. Default: the NIC's port speed. |
| `celestia_mon_firewall_sources` | validator, backup_node, fibre, kms | Optional. The addresses ufw lets reach this box's scraped ports. Default: the monitor's `celestia_mon_private_ip`. |

### Shared

In `group_vars/all.yml`:

| Setting | Meaning |
|---|---|
| `celestia_mon_signer` | `local` (the key file on the node) or `remote` (tmkms). With `local`, the signer panels and RemoteSignerProblem are left out. |
| `celestia_mon_fibre_enabled` | `true` when the validator runs Fibre: installs and renders everything Fibre-related. `false` when it runs none. |

### On the monitor

In the monitor's entry or its `host_vars`:

| Setting | Meaning |
|---|---|
| `celestia_mon_signal` | Upgrade signalling and the validator's governance votes ([below](#upgrade-signalling)). Default `{}` (off). |
| `celestia_mon_tensile` | The outside check of the Fibre endpoint ([below](#outside-check-of-the-fibre-endpoint)): `{api: "<Tensile's API for your network>", validator: "<consensus address>"}`. Default `{}` (off). |
| `celestia_mon_announcements` | Celestia's announcements as alerts ([below](#celestias-announcements)): `[{channel_id: "<channel ID>", severity: page}]`. Default `[]` (off). |
| `celestia_mon_grafana_listen` | The address Grafana listens on, `127.0.0.1` by default ([Grafana access](#grafana-access)). Another value must be a private address of the monitor. |
| `celestia_mon_grafana_clients` | With a private `celestia_mon_grafana_listen`: the private address of each machine that opens Grafana there, one address each, no prefix, none a box of the inventory. Grafana exchanges network traffic with nothing else but loopback and its own address. |
| `celestia_mon_prometheus_retention` | How long Prometheus keeps data, `30d` by default. |
| `celestia_mon_prometheus_retention_size` | How much data Prometheus keeps, `8GB` by default; the oldest goes first when either limit is reached. Its disk needs this x 1.5 + 2 GiB ([disk](#disk-and-memory)). |

Two of them take an address of your validator, each in its own form:

| Address | Form | Used by | Where to find it |
|---|---|---|---|
| Operator address | `celestiavaloper1...` | `celestia_mon_signal.validator` | Your validator's page on any explorer shows it. The install checks it, its checksum included. |
| Consensus address | `celestiavalcons1...` | `celestia_mon_tensile.validator` | `sudo celestia-mon-doctor` on the validator box prints it in check 4. |

### Optional, with defaults

Their defaults are in `ansible/roles/celestia_mon/defaults/main.yml`:

| Setting | Default | Meaning |
|---|---|---|
| `celestia_mon_ports` | 9910 to 9919 | celestia-validator-monitoring's ports ([table](#ports)). |
| `celestia_mon_thresholds` | see [alerts.md](alerts.md) | Alert thresholds. |
| `celestia_mon_nice`, `celestia_mon_cpu_weight`, `celestia_mon_io_weight` | 10, 20, 20 | CPU and disk priority of every unit. |
| `celestia_mon_memory` | [table](#disk-and-memory) | `high` and `max` per service (`node_exporter`, `otelcol`, `prometheus`, `alertmanager`, `grafana`, `announcements`, `tensile`, `signal`, `blackbox`). |
| `celestia_mon_validator_name` | `""` | The validator's name at the top of the dashboard, for example its moniker: up to 13 characters at full size; 14 to 17 characters make the top strip's values a little smaller; a longer name stops the dashboard build. Empty: the dashboard reads "Validator" under the network's name. |
| `celestia_mon_signer_multi_node` | `false` | With a remote signer: `true` when tmkms is set up to sign through several nodes at once (several `[[validator]]` entries). Two signing nodes are then normal: the dashboard reads "Two nodes" instead of the amber "Two signers" it shows when two nodes sign for 3 minutes, and [ValidatorSigningTwice](alerts.md#validatorsigningtwice) is left out. |

Overriding a map (`celestia_mon_units`, `celestia_mon_ports`, `celestia_mon_thresholds`, `celestia_mon_memory`) replaces the whole map, so list every key.

### Secrets

The monitor's secrets stay on the monitor, in one of two places, never both:

- **`/etc/celestia-mon/secrets.yml`**, outside the inventory. `sudo ./install.sh --edit-secrets` on the monitor opens it in your editor (`$EDITOR`, else `$VISUAL`, nano or vi; sudo resets `EDITOR` unless you pass it: `sudo EDITOR=vim ./install.sh --edit-secrets`), from a template when it is missing. The editor works on a copy in `/run/celestia-mon-install`, in memory, and the file is replaced only when the copy changed; it belongs to root and only root reads it. The monitor's install reads it, prints `The monitor's secrets: /etc/celestia-mon/secrets.yml`, and its values win over the same settings in the inventory. The inventory then holds no secret and is the same on every box. The install stops when the file is on a box that is not the monitor, does not belong to root, or is readable by group or other, or when the inventory also has a `vault.yml`.
- **An encrypted `vault.yml`** in `host_vars/<monitor>/` of the inventory, as the examples ship it. `sudo ./install.sh --edit-vault ~/celestia-mon-inventory/host_vars/mon-1/vault.yml` creates a missing file, encrypts a plain one and opens an encrypted one for editing, with the same checked Ansible it installs with. On a workstation, any Ansible install works, or none through uv: `uvx --from ansible-core ansible-vault encrypt <file>`. Encrypt the whole file, not single values in it: `install.sh` refuses a `vault.yml` in `host_vars` or `group_vars` that is not an encrypted file, or that group or others can read (`chmod 600` it). The monitor's install asks for the vault password. Copy this file to the monitor only: a copy on another box stops that box's install (`this host does not need .../vault.yml: delete it from this copy`).

| Setting | Meaning |
|---|---|
| `celestia_mon_receivers` | One list of alert receivers (below). |
| `celestia_mon_deadman_webhook_url` | The URL of your dead-man check ([quickstart step 1](quickstart.md#1-what-you-need)). Required, `https` (plain `http` only to the monitor itself, such as a local relay). The install on the monitor stops when it is empty, still the example's placeholder, or not `https`: without it nothing tells you that monitoring itself is down. |
| `celestia_mon_grafana_admin_password` | Optional: Grafana's admin password, at least 16 characters (for example from `openssl rand -base64 18`). Unset, the first install generates 32 letters and digits into `/etc/celestia-mon/grafana-admin-password` (root only), once, and the end of each install prints `sudo cat` for it. Grafana reads the password only at its first start: a password set before and removed later stops the install, because a generated one would not open Grafana's existing admin account. |
| `celestia_mon_discord_bot_token` | With `celestia_mon_announcements`: the token of the Discord bot that reads Celestia's announcements. |

A `celestia_mon_signal` whose RPC URL holds a provider's key belongs here too.

Each receiver is one of these types; list any number, several of one type if you like:

| `type` | Keys | Gets page alerts | Gets message alerts |
|---|---|---|---|
| `pagerduty` | `routing_key` (an Events API v2 integration key) | yes | no |
| `telegram` | `bot_token`, `chat_id` (numeric, for example `-1000000000000`) | yes | yes |
| `discord` | `webhook_url` | yes | yes |
| `slack` | `api_url`, optional `channel` | yes | yes |
| `webhook` | `url` | yes | yes |

`page` alerts need someone now; `message` alerts can wait for working hours. Page alerts go to every receiver, message alerts to every receiver except PagerDuty, so list at least one receiver that is not PagerDuty. Without PagerDuty, page alerts still reach your chat channels. The separate `page` and `message` lists of earlier versions are refused. A `url`, `api_url` or `webhook_url` must be `https`; plain `http` only to the monitor itself or to an address of your private network, such as a relay there, because the URL holds the receiver's secret. [alerts.md](alerts.md#where-alerts-go) has an example.

## Ports, files and resources

### Ports

| Service | Box | Port |
|---|---|---|
| node_exporter | validator, backup_node, fibre, kms if opted in, monitor | 9910 on the private address |
| OpenTelemetry collector | fibre, active and standby | 9911 on the private address (metrics), 9915 on 127.0.0.1 (from Fibre) |
| Prometheus | monitor | 9912 on 127.0.0.1 |
| Alertmanager | monitor | 9913 on 127.0.0.1 |
| Grafana | monitor | 9914 on 127.0.0.1 (or `celestia_mon_grafana_listen`) |
| celestia-mon-announcements, with Celestia's announcements on | monitor | 9916 on 127.0.0.1 |
| celestia-mon-tensile, with the outside check on | monitor | 9917 on 127.0.0.1 |
| celestia-mon-signal, with upgrade signalling on | monitor | 9918 on 127.0.0.1 |
| celestia-mon-blackbox, with Fibre on | monitor | 9919 on 127.0.0.1 |

The install fails if one of its services listens on a public address. Prometheus also scrapes celestia-appd's own metrics port (26660 by default) on the validator and on each backup node, and with Fibre on it asks each Fibre box's own port (7980 by default, on its private address) for Fibre's [upload readiness](#upload-readiness) through celestia-mon-blackbox. Prometheus, Alertmanager and celestia-mon-blackbox listen on loopback only and ask for a login on every request: user `celestia-mon` and a password the install generates on the monitor, in `/etc/celestia-mon/api-password` (readable by root and Prometheus only), so no other account on the monitor can read them or silence alerts. doctor and amtool use it, and so does celestia-mon-announcements, with Celestia's announcements on, to post each announcement to Alertmanager. Grafana reads Prometheus with a login of its own (user `grafana`, `/etc/celestia-mon/grafana-api-password`), which Alertmanager refuses. Grafana listens on loopback too by default, and you reach Grafana, Prometheus and Alertmanager through an SSH tunnel ([Grafana access](#grafana-access)). The ports are a block that common tools do not use; to move them, set `celestia_mon_ports`.

### Firewall rules

When ufw is already active on a box, the install adds rules that let the monitor's private address reach the ports Prometheus scrapes there: 26660 on the validator and backup nodes, and the node_exporter and collector ports (9910, 9911). Each rule carries the comment `celestia-mon`, is listed in the plan before you confirm (`ufw rule:  allow from <monitor address> to port <port>/tcp (comment celestia-mon)`), and is recorded in `/var/lib/celestia-mon/firewall-added.json`. A source that already has a rule for the port is left alone and not recorded, and the uninstall deletes only recorded rules. With any other firewall, allow those ports yourself. With Fibre on, the monitor also connects to Fibre's own port (7980) on each Fibre box's private address, for its [upload readiness](#upload-readiness); no rule is added for it, because Fibre takes uploads on that port from anywhere, but a firewall that opens it on the public interface only must let the monitor's private address in too. On a Tailscale network, ufw does not see tailnet traffic; the tailnet policy is the filter ([security.md](security.md#tailnet-policy)). The other ports of the validator are yours to lock: [security.md](security.md#ports-on-the-validator-who-connects-to-what).

### Services and files

celestia-validator-monitoring runs next to a node_exporter, Prometheus, Alertmanager, Grafana or OpenTelemetry collector the box already has, and leaves it alone. Each of its services has its own systemd unit and system user (`celestia-mon-node-exporter`, `celestia-mon-otelcol`, `celestia-mon-prometheus`, `celestia-mon-alertmanager`, `celestia-mon-grafana`, and with Fibre on `celestia-mon-blackbox`, a blackbox_exporter), runs from `/opt/celestia-mon/<component>-<version>/`, keeps its settings under `/etc/celestia-mon/` and its data under `/var/lib/celestia-mon/`, and logs to the journal (for example `journalctl -u celestia-mon-grafana`). Nothing goes to `/usr/local/bin` except `celestia-mon-doctor`.

Files the install writes for itself:

| File | What |
|---|---|
| `/etc/celestia-mon/doctor.conf` | `doctor`'s settings, from the inventory. |
| `/etc/celestia-mon/api-password`, `/etc/celestia-mon/grafana-api-password` | The logins of Prometheus's and Alertmanager's APIs (monitor). |
| `/etc/celestia-mon/grafana-admin-password` | Grafana's admin password, when the install generated it (monitor). |
| `/etc/celestia-mon/secrets.yml` | The monitor's secrets, written by `--edit-secrets` (monitor). |
| `/var/lib/celestia-mon/install-id` | The install this box belongs to ([one box, one install](#one-box-one-install)). |
| `/var/lib/celestia-mon/firewall-added.json` | The ufw rules the install added. |
| `/var/log/celestia-mon/install.log` | The whole output of the last install: dry run, run and `doctor`'s report (`check.log` for `--check`; root only). |

### Disk and memory

- **Free disk** for the releases a box gets, while they are downloaded and unpacked: about 100 MiB on a validator, backup node or KMS box, 1.2 GiB on a Fibre box (the OpenTelemetry collector), 2.2 GiB on the monitor (2.3 GiB with Fibre on). The install checks it.
- **Room for Prometheus's data on the monitor:** retention size x 1.5 + 2 GiB on the disk of `/var/lib/celestia-mon/prometheus`, 14 GiB with the default `celestia_mon_prometheus_retention_size` of 8 GB. The retention size bounds only Prometheus's finished blocks: its write-ahead log, its head chunks and the new blocks a compaction writes before it deletes the old ones come on top. The install stops with the numbers when the disk cannot hold it (what Prometheus keeps there already counts, so a re-run on a grown install passes), and every run of `install.sh` on the monitor checks it again. Lower the retention size or give the data its own disk if it does not fit.
- **`/var/tmp`** must not be mounted `noexec` and needs 1 GiB free while `install.sh` runs ([below](#how-installsh-runs-ansible)).

Every unit runs with `Nice=10`, `CPUWeight=20` and `IOWeight=20` (systemd's defaults are 0, 100 and 100), with no CPU quota, and with a soft memory target and a hard limit well above it: above `MemoryHigh` the kernel reclaims the service's memory and slows it down; at `MemoryMax` the kernel ends the service and systemd restarts it. The defaults are sized from the measured memory of these services at this package's scale (a few thousand series), and CI fails when a service uses more than half its `MemoryHigh`. `sudo celestia-mon-doctor` (check 14) shows each unit's use and warns above 80% of `MemoryHigh`. The install reports the memory available now against these targets and limits.

| Service | MemoryHigh | MemoryMax |
|---|---|---|
| node_exporter | 128M | 256M |
| OpenTelemetry collector | 384M | 1G |
| Prometheus | 2G | 4G |
| Alertmanager | 128M | 256M |
| Grafana | 512M | 1G |
| blackbox_exporter (celestia-mon-blackbox) | 128M | 256M |
| celestia-mon-announcements, celestia-mon-tensile, celestia-mon-signal | 64M each | 128M each |

**What the priorities do and do not do.** `Nice`, `CPUWeight` and `IOWeight` lower the services' share only while they compete with celestia-appd, Fibre or tmkms for CPU or disk; on an idle box they change nothing, and they reserve nothing. They lower the impact on the validator's disk; they do not guarantee there is none. `IOWeight` (the cgroup's `io.weight`) has no effect with the `none` I/O scheduler that NVMe disks commonly use, unless the kernel's io.cost controller is configured; check a disk's scheduler with `cat /sys/block/<disk>/queue/scheduler`. Where the monitor shares a disk with the validator or Fibre, measure the impact of Prometheus's [compactions](#compactions-on-a-shared-disk) on the first deploy.

### Outbound connections

Every box reaches `github.com` and `release-assets.githubusercontent.com` (uv and the release downloads), `pypi.org` and `files.pythonhosted.org` (ansible-core) over HTTPS while `install.sh` runs. The monitor also reaches `dl.grafana.com` (Grafana), your alert receivers and the dead-man service, and, when you turn them on, Tensile, Discord and the public RPCs in `celestia_mon_signal`.

## Install details

### Flags

```
sudo ./install.sh [--inventory PATH] [--host NAME] [--ask-vault-pass] [--yes] [--check] [--verbose]
sudo ./install.sh --uninstall [--purge] [--host NAME] [--yes] [--check] [--verbose]
sudo ./install.sh --survey
sudo ./install.sh --edit-secrets
sudo ./install.sh --edit-vault FILE
```

| Flag | Meaning |
|---|---|
| `--inventory PATH` | The inventory all boxes share: its directory, or the `hosts.yml` in it. Default: `~/celestia-mon-inventory` of the user who runs sudo. |
| `--host NAME` | This box's name in the inventory (not a group). Default: the box whose `celestia_mon_private_ip` is an address of this machine. |
| `--ask-vault-pass` | Ask for the vault password first. Without it, `install.sh` asks when a file this box's run reads is vault-encrypted. |
| `--check` | Dry run only: the checks and the plan, nothing changed. |
| `--yes` | Apply the plan without asking. |
| `--verbose` | Show Ansible's whole output (every task, result and diff) instead of one line per part; the log keeps it either way. |
| `--uninstall` | Remove the services, their binaries and the recorded ufw rules from this box, and keep its data, settings and users ([uninstall](#uninstall)). It needs no inventory, and no `--host` on a box it installed on. |
| `--purge` | With `--uninstall`: also delete the data, the settings and the users, after a second confirmation. |
| `--survey` | Read this box and print its inventory entry and your steps on it ([doctor.md](doctor.md#survey)); nothing is downloaded or changed. It takes no other option. |
| `--edit-secrets` | On the monitor: create or edit its [secrets](#secrets) in `/etc/celestia-mon/secrets.yml`, then exit. |
| `--edit-vault FILE` | Create, encrypt or edit an inventory's `vault.yml` with ansible-vault, then exit. |

The commands of earlier versions keep working: `--inventory`, `--host` and `--ask-vault-pass` behave as before.

### What a run does

1. **Finds the box.** It reads the inventory with the PyYAML of the throwaway virtualenv ([below](#how-installsh-runs-ansible)), without Ansible and without the vault password: `hosts.yml` (every `.yml`, `.yaml` or `.json` file of the folder) and the `host_vars` and `group_vars` that are not encrypted. The box is the inventory host whose `celestia_mon_private_ip` is an address of this machine (`ip -o addr show`); it prints `Installing as <name> (<address>) from <folder>`. With no match, or several, it stops and asks for `--host`. It asks for the vault password only when a file this box's run reads is vault-encrypted: on the monitor every one (it renders every box's settings), elsewhere its own `host_vars`, its groups' `group_vars`, and the `host_vars` of the other boxes celestia-validator-monitoring manages. A copy of the monitor's encrypted `host_vars` on another box stops the run, which reads none of those secrets. On the monitor it also reads `/etc/celestia-mon/secrets.yml`.
2. **Checks the box**, changing nothing (`checks (system, units, mounts, ports, disk, memory)` in the progress view): that the run targets one inventory host; the shared settings, one check per condition (the signer, `celestia_mon_fibre_enabled`, the unit names, exactly one validator and one monitor, a backup node that is not the validator, a fibre group when Fibre is on); that `celestia_mon_private_ip` is a private address of this box, and the monitor's a private address; that the box belongs to no other install; on the monitor, Grafana's listen address and clients; Ubuntu 24.04 with systemd on x86_64 or aarch64, run as root; that `celestia_mon_data_mounts` lists mount points; that systemd has loaded the unit files of the Celestia services here, with no change on disk it has not loaded yet (celestia-mon's own reload would load that change too); that each unit `celestia_mon_units` names for this box is loaded; that celestia-validator-monitoring's ports are free or held by its own services; and enough free disk, on the monitor including the room for Prometheus's data. It also reports the memory available now. A failed check prints its message, stops before any change and names the fix.
3. **Plans:** a dry run of the whole install for this box only, one line per part with what it would change (the checks, the install record, each service, `doctor`, the bind check), then a short summary: the box and its groups, the services and their users, the addresses and ports they listen on, the directories, the disk and memory they need, whether ufw is active, and each ufw rule it would add. On a Fibre box, a `Fibre:` line says whether Fibre's unit already sends its metrics to the collector, and if not, the exact change for you to make. It ends with `The dry run finds nothing to change.` or `The dry run reports N change(s)`.
4. **Asks** `Apply this to <name>? [y/N]`. Anything but `y` or `yes` stops it with nothing changed.
5. **Installs**, and reports the number of changes, or `No changes: <name> already matches the inventory.` The first install on the monitor, and every later run that changes Alertmanager's receivers, sends one test message (`NotificationTest`) to the chat receivers and webhooks; it does not reach PagerDuty, and it resolves after 5 minutes. While Grafana's admin password is the generated one, it prints `sudo cat /etc/celestia-mon/grafana-admin-password`.
6. **Prints the command that removes it again.**
7. **Runs `celestia-mon-doctor`** and shows under **Still to do on this box** its `FAIL`, `WARN` and `NOT YET` lines, each with its fix; some clear by themselves once the other boxes are installed ([quickstart step 6](quickstart.md#6-your-steps)). `install.sh` ends successfully either way. `doctor`'s whole report and the whole output of the install are in `/var/log/celestia-mon/install.log`.

Run `install.sh` again on each box after every inventory change; a box with nothing to change says so. If the install stops with an error, fix the cause and run it again: it continues where it stopped.

### One box, one install

A box belongs to one install: one inventory, one monitor. The install writes the monitor's private address to `/var/lib/celestia-mon/install-id` on every box it manages, and a later run from an inventory with another monitor stops before any change:

```
val-1 belongs to the install monitored from 10.0.0.3 (recorded in /var/lib/celestia-mon/install-id), and the monitor of this inventory is mon-2 at 10.9.0.3: one host can be in one celestia-mon install. ...
```

Two installs on one box would share the same `celestia-mon-*` units and ports, and the second would take over the first one's node_exporter. To watch a testnet and a mainnet validator, use separate boxes and two inventories. When the monitor really moved, delete the record (`sudo rm /var/lib/celestia-mon/install-id`) and run again. The first run of a release that has the record only writes it (`install record` in the plan). `--uninstall` keeps it, `--purge` deletes it.

### How install.sh runs Ansible

Nothing is installed with apt or pip. `install.sh` downloads the [uv](https://github.com/astral-sh/uv) binary pinned in `versions.yml`, checks its sha256, and uses it to install the pinned ansible-core and every package it needs, from `ansible/requirements.lock` with the sha256 of each file (wheels only), into a temporary directory under `/var/tmp/celestia-mon-install`, which it deletes when it ends, also on failure. The vault password, the decrypted copy `--edit-vault` opens and the copy `--edit-secrets` edits go to `/run/celestia-mon-install`, in memory, deleted the same way. `install.sh` refuses to run when the lock does not match the `ansible_core` pin in `versions.yml`. The lock is made in GitHub Actions by the `lock` workflow (`.github/workflows/lock.yml`), never on a workstation.

`install.sh` runs the package and the inventory as root, so it refuses to run when a file there belongs to a user other than root and you, or is writable by every user.

### Releases

Each release on the repository's Releases page carries two files: `celestia-validator-monitoring-<tag>.tar.gz`, the tagged commit as `git archive` makes it, which unpacks to `celestia-validator-monitoring-<tag>/`, and `SHA256SUMS`. GitHub Actions builds both from the tag (`.github/workflows/release.yml`). [Quickstart step 2](quickstart.md#2-get-the-package-on-each-box) downloads them.

### Install from a copy you pack yourself

To install a release or commit you reviewed in a clone of the repository, pack it on your workstation and note its sha256:

```sh
git archive --format=tar.gz --prefix=celestia-validator-monitoring/ -o celestia-validator-monitoring.tar.gz <tag or commit>
sha256sum celestia-validator-monitoring.tar.gz
```

Copy it to each box (`scp celestia-validator-monitoring.tar.gz <box>:`). On the box, check the archive against the sha256 from your workstation and unpack it:

```sh
sha256sum celestia-validator-monitoring.tar.gz
tar -xzf celestia-validator-monitoring.tar.gz
cd celestia-validator-monitoring
```

Optionally, sign the archive instead of comparing hashes by eye. With your SSH key (a FIDO2 key works too) on the workstation:

```sh
ssh-keygen -Y sign -f ~/.ssh/id_ed25519_sk -n file celestia-validator-monitoring.tar.gz   # writes celestia-validator-monitoring.tar.gz.sig
```

Copy the `.sig` file along. On the box, put one line `<your name> <your public key>` into `~/.ssh/allowed_signers` once, then:

```sh
ssh-keygen -Y verify -f ~/.ssh/allowed_signers -I <your name> -n file -s celestia-validator-monitoring.tar.gz.sig < celestia-validator-monitoring.tar.gz
```

## Grafana access

Grafana listens on the monitor's loopback address, 127.0.0.1, by default, so no network reaches it. Open it through an SSH tunnel from your machine:

```sh
ssh -N -L 9914:127.0.0.1:9914 <user>@<monitor>
```

Then open `http://localhost:9914` and log in as `admin`. Grafana reads its admin password only on its first start; change it later in Grafana (Profile, Change password). The same tunnel can carry Prometheus and Alertmanager, which listen on the monitor's loopback only:

```sh
ssh -N -L 9914:127.0.0.1:9914 -L 9912:127.0.0.1:9912 -L 9913:127.0.0.1:9913 <user>@<monitor>
```

Then also `http://localhost:9912/alerts` (Prometheus) and `http://localhost:9913` (Alertmanager), as user `celestia-mon` with the password from `sudo cat /etc/celestia-mon/api-password` on the monitor; your browser asks for it. Forward 9912 and 9913 only while you use them: while the tunnel is open and the browser remembers the login, other web pages in that browser can send requests to them too.

Two optional alternatives:

- **A private address.** Set `celestia_mon_grafana_listen` to a private address of the monitor (a Tailscale, WireGuard or LAN address) and `celestia_mon_grafana_clients` to the private address of each machine you open Grafana from (for example `[10.20.0.50]`, your workstation's address on that network), and run `install.sh` on the monitor again; then open `http://<that address>:9914` from one of those machines. Grafana's unit exchanges network traffic with nothing else but loopback and its own address, so a Grafana login cannot reach the validator or another box through a datasource. The install stops when the address is not one of the monitor's or the list is empty, and Grafana never listens on `0.0.0.0`. With ufw active on the monitor, allow your workstation to port 9914 yourself (`sudo ufw allow from <addr> to any port 9914 proto tcp`). Tailscale is never required.
- **A reverse proxy.** Keep `127.0.0.1` and run the proxy on the monitor itself, in front of `http://127.0.0.1:9914`, with its own TLS.

The dashboard is **Celestia validator** in the **Celestia validator** folder. [panels.md](panels.md) explains each panel. It is generated from your inventory, so groups that do not apply are left out and the threshold lines match the alerts. Do not edit it in Grafana; change the inventory and run `install.sh` on the monitor again.

## Upload readiness

Fibre v10.5.0 and newer runs checks of its own (its app node, the Fibre module, its signer, whether the validator is active, its registration and its store) and reports the result on its own port through the standard gRPC health check: Ready when all of them pass, Not ready when one fails. With Fibre on, the monitor asks each Fibre box for it every 15 seconds through celestia-mon-blackbox, at the box's private address and `celestia_mon_fibre_port` (7980 by default), so Fibre must listen there (it listens on every address by default) and the network between the boxes must let the monitor reach that port.

Not ready means Fibre reports that at least one of its own checks fails; uploads to it may fail. Fibre's log names the failing check and what to do about it: `journalctl -u <fibre unit> | grep "health check failed"`. Fibre logs a check only when its result changes, so read the newest lines; it logs "health check passed" when that check passes again, and turns Ready by itself once all of them pass. A Fibre older than v10.5.0 reports no readiness (Not reported): upgrade Fibre to get it. A stopped standby does not answer, as expected. `doctor` check 24 on the monitor shows each Fibre box's readiness now. The probe reaches IPv4 addresses only: for a Fibre box whose `celestia_mon_private_ip` is an IPv6 address, readiness is not checked.

## Optional alerts

Each is a setting on the monitor; run `install.sh` on the monitor after you set it, and remove the setting to turn it off again.

### Upgrade signalling

Celestia moves to a new app version once 5/6 of the voting power has signalled it in x/signal. celestia-mon-signal reads x/signal every minute through a node's CometBFT RPC: the voting power that signalled the next version, the upgrade once one is scheduled, and the version your validator signalled. [UpgradeSignalMissing](alerts.md#upgradesignalmissing) pages while validators signal a version yours has not; [UpgradeScheduled](alerts.md#upgradescheduled) sends a message when the upgrade is scheduled (again after 4 days while it waits, as on mainnet), and [UpgradeSoon](alerts.md#upgradesoon) one when it is less than a day away. Through the same RPCs it reads the governance proposals in their voting period and whether the validator account voted on each: [GovVoteMissing](alerts.md#govvotemissing) sends a message when one is less than 2 days from its end without that vote, and pages when less than 1 day is left, by default on mainnet only; an expedited 24-hour vote gets it 12 hours before its end, on mainnet as the page alone. It sends no transaction and needs no key. Set `celestia_mon_signal` on the monitor, in its entry in `hosts.yml` or its `host_vars`:

| Key | Meaning |
|---|---|
| `chain_id` | The network's chain ID: `celestia` on mainnet, `mocha-5` on Mocha. An RPC that serves another network is skipped, so a mainnet install never reads a Mocha node, or the reverse. |
| `rpc` | CometBFT RPC URLs (port 26657 on a node), tried in order: the first one that answers for `chain_id` is read. Each is `https`, or plain `http` to the monitor itself or to a node named by its private address, never by a host name: the service reaches no other address of the private network ([security.md](security.md#what-it-exposes)). x/signal's state is the same on every synced node, so put a node of your own first and a public RPC after it, for the time your node is down. An RPC whose node is catching up, or has made no block for 2 minutes, is skipped too. The block time is measured over the last 1000 blocks the node keeps. A URL may hold a provider's key in its path, not in a login or a query string. |
| `validator` | The validator's operator address, `celestiavaloper1...` ([where to find it](#on-the-monitor)). The install checks it, its checksum included. Its votes are read as the validator account's, `celestia1...` with the same bytes, the account the tally counts. |
| `gov` | Optional, `true` by default: read the governance proposals and the validator account's votes (GovVoteMissing). `false` leaves the votes out: no gov reads, no vote alerts, and the dashboard's answer reads "Network upgrade" instead of "Governance". |
| `gov_page` | Optional: `true` when GovVoteMissing also pages with less than a day left (on a vote of 2 days or less, the page instead of the message), `false` for the message only. By default `true` with `chain_id: celestia` and `false` on other networks, such as Mocha. |

In the [mainnet layout](layouts.md#mainnet-layout) the monitor `mon-1` runs no node, so it reads public RPCs from Celestia's [mainnet page](https://docs.celestia.org/operate/networks/mainnet-beta/) over https, and no box opens a port for it:

```yaml
    mon-1:
      celestia_mon_signal:
        chain_id: celestia
        rpc:
          - https://rpc.celestia.pops.one
          - https://public-celestia-rpc.numia.xyz
        validator: celestiavaloper1...
```

To read the backup node on `fibre-1` first, through WireGuard, three changes are yours: set `laddr = "tcp://10.20.0.2:26657"` (its WireGuard address) in the `[rpc]` section of its `config.toml` and restart it at a moment you choose; let the monitor in on `fibre-1`, `sudo ufw allow from 10.20.0.3 to any port 26657 proto tcp` (`mon-1`'s WireGuard address); and put `http://10.20.0.2:26657` first in `rpc`, the public RPCs after it. That opens the node's RPC to the monitor, which the [WireGuard-only link](security.md#wireguard-only-link) otherwise leaves closed.

In the [Mocha layout](layouts.md#mocha-layout) the monitor shares the Fibre box, which runs a node of its own: list that node's RPC on its loopback first (`laddr` in the `[rpc]` section of its `config.toml`), then public RPCs from Celestia's [Mocha page](https://docs.celestia.org/operate/networks/mocha-testnet/):

```yaml
    fibre-1:
      celestia_mon_signal:
        chain_id: mocha-5
        rpc:
          - http://127.0.0.1:26657
          - https://rpc-mocha.pops.one
          - https://public-endpoint.celestia-mocha.quiknode.pro
        validator: celestiavaloper1...
```

A public RPC is someone else's node: the reads are public and need no key, but the upgrade and vote alerts then depend on it, so list a node of your own first wherever the monitor can reach one. A node on another box needs its RPC on its private address (`laddr` in `[rpc]`) and a firewall or tailnet rule that lets the monitor reach port 26657 there; both are your changes ([security.md](security.md#tailnet-policy) has the grant). The RPC listens on one address only, so nothing answers on that node's 127.0.0.1 afterwards: `doctor` there follows `laddr`, and `celestia-appd` commands there need `--node http://<private address>:26657` (or `node` in its `client.toml`). When a URL holds a provider's key, set `celestia_mon_signal` in the monitor's [secrets](#secrets) instead; the metrics and the log name each RPC by its scheme, host and port only. `sudo celestia-mon-doctor` on the monitor (check 23) shows the first reading, and warns while it comes from another RPC than the first. Without `celestia_mon_signal`, the service is removed.

### Outside check of the Fibre endpoint

[Tensile](https://tensile.huginn.tech), a Fibre observer, dials the Fibre endpoint registered on chain from outside your networks. With `celestia_mon_tensile` set, celestia-mon-tensile on the monitor reads its verdict for your validator every minute: [FibreDown](alerts.md#fibredown) then also pages when the endpoint cannot be reached from outside, names another box than the one where Fibre runs, or is not registered, and [FibreNotServing](alerts.md#fibrenotserving) compares Fibre's uploads with the blobs Tensile saw. [TensileUnreadable](alerts.md#tensileunreadable) tells you when the check stops.

```yaml
    mon-1:
      celestia_mon_tensile:
        api: "https://tensile-mocha.huginn.tech/api/v1"   # Tensile's API for your network; this one is Mocha's
        validator: "celestiavalcons1..."                  # the consensus address: doctor check 4 on the validator box
```

Set `celestia_mon_fibre_public_endpoint` on every Fibre box as well.

### Celestia's announcements

celestia-mon-announcements passes the messages of Celestia's announcement channels on Discord to your receivers, PagerDuty included, as [CelestiaAnnouncement](alerts.md#celestiaannouncement) alerts: follow those channels from a channel on your own Discord server, give a bot of yours read access to it, and set `celestia_mon_announcements` (inventory) and `celestia_mon_discord_bot_token` (the monitor's [secrets](#secrets)). [alerts.md](alerts.md#celestiaannouncement) has the steps.

### Network notices

Upgrades are the most common reason a node stops at a height or crash loops. Celestia announces upgrades and incidents in its announcement channels on Discord, and per network on [status.celestia.org](https://status.celestia.org/). Without the announcements above, subscribe on status.celestia.org for the networks you run, or route its feed ([RSS](https://status.celestia.org/feed.rss), [Atom](https://status.celestia.org/feed.atom)) into the same channel as your alerts. See also Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page.

## Compactions on a shared disk

Prometheus compacts its data about every 2 hours, the first time about 3 hours after it starts, and a compaction reads and writes on the disk of its data directory. Where the monitor shares a disk with the validator or Fibre, `Nice` and `IOWeight` lower that load's priority but do not guarantee it has no impact ([above](#disk-and-memory)). Once Prometheus has run for a few hours on the first deploy, look at the Disk latency charts in the dashboard's "Servers: disks, restarts and clocks" row around the last compaction, against the hour before: they show each server's disks over time ([Node Exporter Full](https://grafana.com/grafana/dashboards/1860-node-exporter-full/) shows each disk). A clear rise on a validator or Fibre disk means Prometheus competes with it: put Prometheus's data (`/var/lib/celestia-mon/prometheus`) on another disk, or the monitor on another box. These queries work in Grafana's Explore (T is the time of the last compaction):

```promql
max_over_time(timestamp(increase(prometheus_tsdb_compactions_total{job="prometheus"}[2m]) > 0)[6h:1m])
rate(node_disk_write_time_seconds_total{job="node"}[1m]) / rate(node_disk_writes_completed_total{job="node"}[1m])
rate(node_disk_io_time_seconds_total{job="node"}[1m])
```

The first gives T, the second the average write latency per disk, the third the share of time each disk was busy.

## Upgrades

Get the new release on each box ([quickstart step 2](quickstart.md#2-get-the-package-on-each-box)) and run `sudo ./install.sh` from its folder; the inventory and the monitor's secrets stay where they are. The dry run names each new version it would install ("Would install ..."); the service restarts on the new version, and the old version's directory is removed once the new one runs. Data and settings stay. The install records the sha256 of each binary it installs (`SHA256SUMS` next to them) and installs again any binary that no longer matches; an install without that file, made by an earlier version, downloads its binaries once more, and a service restarts only when its binary changed. Run `doctor` again after every celestia-app or Fibre upgrade and every config change.

## Uninstall

On each box:

```sh
sudo ./install.sh --uninstall
```

It needs neither the inventory nor the vault password, and no `--host` on a box it installed on: it reads the box's name from `/etc/celestia-mon/doctor.conf`. It shows its plan and asks first as well (`--check` shows the plan only, `--yes` skips the question). It stops, disables and removes every `celestia-mon-*` unit, removes `/opt/celestia-mon` (the binaries), `/var/cache/celestia-mon`, `/usr/local/bin/celestia-mon-doctor` and the settings files that hold secrets (Alertmanager's settings with the receivers and the dead-man URL, Grafana's admin password and its datasource, the API passwords, the Discord bot token, and celestia-mon-signal's settings with its RPC URLs; a reinstall writes them again, with new API passwords), and deletes the ufw rules the install recorded in `/var/lib/celestia-mon/firewall-added.json`: each one only while ufw still lists it with the recorded source, port, protocol and the comment `celestia-mon`. A rule that was there before the install was never recorded and stays. It keeps Prometheus's data, Grafana's database and the install record (`/var/lib/celestia-mon`), the other settings (`/etc/celestia-mon`, with the monitor's `secrets.yml` and a generated Grafana password) and the `celestia-mon-*` system users and groups, so a reinstall picks up where it left off. It prints the command that deletes those too:

```sh
sudo ./install.sh --uninstall --purge
```

`--purge` asks a second time, naming the paths and the users it deletes for good (`--yes` skips both questions); it also deletes `/var/log/celestia-mon`. Nothing else is touched: celestia-appd, Fibre, tmkms, other services and other firewall rules stay as they are. Two settings you made yourself stay too: celestia-appd's `[instrumentation]` section, and Fibre's `--otel-endpoint`; with the collector gone, Fibre logs export errors until you remove that setting and restart Fibre.

An install made before celestia-validator-monitoring recorded its ufw rules has no `firewall-added.json`, so its uninstall keeps those rules. List them with `sudo ufw status | grep celestia-mon` and delete each one by hand: `sudo ufw delete allow from <address> to any port <port> proto tcp`.
