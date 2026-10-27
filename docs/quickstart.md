# Quickstart

This guide takes you from an empty inventory to a running dashboard, working alerts and a clean `doctor` report. You run one command on each host, on the host itself. Every install is pre-checked and reversible: `install.sh` checks the host, shows what it would change, changes it only after you confirm, and `install.sh --uninstall` removes it again (step 14). celestia-validator-monitoring never changes, restarts or upgrades celestia-appd, Fibre or tmkms: where a setting must change, this guide and `doctor` tell you the line, and you apply it.

## 1. Requirements

- **Hosts:** Ubuntu 24.04 with systemd, on x86_64 or arm64, in these roles (a host may have several):
  - `validator` runs celestia-appd, the node the signer normally connects to (exactly one);
  - `backup_node` runs a synced celestia-appd without a key that can take over signing (optional, any number);
  - `fibre` has a Fibre install, active or a stopped standby (it may be the validator host); systemd says which one runs;
  - `kms` runs tmkms (optional; nothing is installed there unless you opt in);
  - `monitor` runs Prometheus, Alertmanager and Grafana (exactly one).

  A monitor host of its own keeps alerting when a validator or Fibre host dies ([security.md](security.md#where-to-run-the-monitor)). The monitor may share a host, as on the Fibre host in the [mainnet layout](#mainnet-layout); the dead-man service (step 8) then tells you when that host dies.
- **A private network between the hosts:** RFC 1918, Tailscale (100.64.0.0/10), WireGuard or IPv6 ULA addresses. Everything celestia-validator-monitoring installs listens on these addresses or on 127.0.0.1, and the install fails if one of its services listens on a public address.
- **Root on each host** (`sudo`), with the `python3` and `curl` (or `wget`) that Ubuntu ships. Nothing is installed with apt or pip: `install.sh` downloads the [uv](https://github.com/astral-sh/uv) binary pinned in `versions.yml`, checks its sha256, and uses it to install the pinned ansible-core and every package it needs, from `ansible/requirements.lock` with the sha256 of each file, into a temporary directory under `/var/tmp/celestia-mon-install`, which it deletes when it ends, also on failure (the vault password goes to `/run/celestia-mon-install`, in memory, deleted the same way). `/var/tmp` must not be mounted `noexec` and needs 1 GiB free during the run.
- **Outbound HTTPS** from every host to `github.com` and `release-assets.githubusercontent.com` (uv and the release downloads), `pypi.org` and `files.pythonhosted.org` (ansible-core); from the monitor also to `dl.grafana.com` (Grafana), your alert receivers and the dead-man service, and to the public RPCs in `celestia_mon_signal`, if you list any ([upgrade signalling](#upgrade-signalling)).
- **Free disk** for the releases a host gets, while they are downloaded and unpacked: about 100 MiB on a validator, backup node or KMS host, 1.2 GiB on a Fibre host (the OpenTelemetry collector), 2.2 GiB on the monitor (2.3 GiB with Fibre on). The install checks it.
- **Room for Prometheus's data on the monitor:** retention size x 1.5 + 2 GiB on the disk of `/var/lib/celestia-mon/prometheus`, 14 GiB with the default `celestia_mon_prometheus_retention_size` of 8 GB. The retention size bounds only Prometheus's finished blocks: its write-ahead log, its head chunks and the new blocks a compaction writes before it deletes the old ones come on top. The install stops with the numbers when the disk cannot hold it (what Prometheus keeps there already counts, so a re-run on a grown install passes), and `doctor` check 15 repeats the check later. Lower the retention size or give the data its own disk if it does not fit.
- **One validator per install.** To watch a testnet and a mainnet validator, run two installs with two inventories.

Default ports:

| Service | Host | Port |
|---|---|---|
| node_exporter | validator, backup_node, fibre, kms if opted in | 9910 on the private address |
| OpenTelemetry collector | fibre, active and standby | 9911 on the private address (metrics), 9915 on 127.0.0.1 (from Fibre) |
| Prometheus | monitor | 9912 on 127.0.0.1 |
| Alertmanager | monitor | 9913 on 127.0.0.1 |
| Grafana | monitor | 9914 on 127.0.0.1 (or `celestia_mon_grafana_listen`) |
| celestia-mon-announcements, with Celestia's announcements on | monitor | 9916 on 127.0.0.1 |
| celestia-mon-tensile, with the outside check on | monitor | 9917 on 127.0.0.1 |
| celestia-mon-signal, with upgrade signalling on | monitor | 9918 on 127.0.0.1 |
| celestia-mon-blackbox, with Fibre on | monitor | 9919 on 127.0.0.1 |

Prometheus also scrapes celestia-appd's own metrics port (26660 by default) on the validator and on each backup node, and with Fibre on it asks each Fibre host's own port (7980 by default, on its private address) for Fibre's [upload readiness](#upload-readiness) through celestia-mon-blackbox. Prometheus, Alertmanager and celestia-mon-blackbox listen on loopback only and ask for a login on every request: user `celestia-mon` and a password the install generates on the monitor, in `/etc/celestia-mon/api-password` (readable by root and Prometheus only), so no other account on the monitor can read them or silence alerts. doctor and amtool use it. Grafana reads Prometheus with a login of its own (user `grafana`, `/etc/celestia-mon/grafana-api-password`), which Alertmanager refuses. Grafana listens on loopback too by default, and you reach all three through an SSH tunnel (step 7).

celestia-validator-monitoring runs next to a node_exporter, Prometheus, Alertmanager, Grafana or OpenTelemetry collector the host already has, and leaves it alone. Each of its services has its own systemd unit and system user (`celestia-mon-node-exporter`, `celestia-mon-otelcol`, `celestia-mon-prometheus`, `celestia-mon-alertmanager`, `celestia-mon-grafana`, and with Fibre on `celestia-mon-blackbox`, a blackbox_exporter), runs from `/opt/celestia-mon/<component>-<version>/`, keeps its settings under `/etc/celestia-mon/` and its data under `/var/lib/celestia-mon/`, and logs to the journal (for example `journalctl -u celestia-mon-grafana`). Nothing goes to `/usr/local/bin` except `celestia-mon-doctor`. The ports above are a block that common tools do not use; to move them, set `celestia_mon_ports` (step 3).

Every unit runs with `Nice=10`, `CPUWeight=20` and `IOWeight=20` (systemd's defaults are 0, 100 and 100), with no CPU quota, and with a soft memory target and a hard limit well above it: above `MemoryHigh` the kernel reclaims the service's memory and slows it down; at `MemoryMax` the kernel ends the service and systemd restarts it. The defaults are sized from the measured memory of these services at this package's scale (a few thousand series), and CI fails when a service uses more than half its `MemoryHigh`. `sudo celestia-mon-doctor` (check 14) shows each unit's use and warns above 80% of `MemoryHigh`.

| Service | MemoryHigh | MemoryMax |
|---|---|---|
| node_exporter | 128M | 256M |
| OpenTelemetry collector | 384M | 1G |
| Prometheus | 2G | 4G |
| Alertmanager | 128M | 256M |
| Grafana | 512M | 1G |
| blackbox_exporter (celestia-mon-blackbox) | 128M | 256M |
| celestia-mon-announcements, celestia-mon-tensile, celestia-mon-signal | 64M each | 128M each |

**What the priorities do and do not do.** `Nice`, `CPUWeight` and `IOWeight` lower the services' share only while they compete with celestia-appd, Fibre or tmkms for CPU or disk; on an idle host they change nothing, and they reserve nothing. They lower the impact on the validator's disk; they do not guarantee there is none. `IOWeight` (the cgroup's `io.weight`) has no effect with the `none` I/O scheduler that NVMe disks commonly use, unless the kernel's io.cost controller is configured; check a disk's scheduler with `cat /sys/block/<disk>/queue/scheduler`. Where the monitor shares a disk with the validator or Fibre, measure the impact of Prometheus's compactions on the first deploy (step 9, check 16).

## 2. Prepare the validator

celestia-validator-monitoring reads celestia-appd's metrics, which are off by default. Change the setting yourself and restart celestia-appd at a moment you choose. In the node's `config/config.toml`:

```toml
[instrumentation]
prometheus = true
prometheus_listen_addr = "<validator private address>:26660"
```

The default `prometheus = false` serves no metrics. The default `prometheus_listen_addr = ":26660"` listens on every address, so set the private address. The `namespace` can stay `cometbft` (the default) or be `celestia`; celestia-validator-monitoring handles both. Make the same change on each backup node, with that node's private address.

**A remote signer** needs nothing extra: with `priv_validator_laddr` set and `prometheus = true`, the node exports the signer metrics. Bind the signer ports to a private address too: `priv_validator_laddr` to the VLAN or WireGuard address tmkms dials, and `priv_validator_grpc_laddr`, which Fibre signs through, to 127.0.0.1 or, with mTLS, to the private address Fibre dials; never to `0.0.0.0`. celestia-appd does not authenticate the signer that connects to `priv_validator_laddr`, and ufw does not filter a Tailscale interface ([security.md](security.md#tailnet-policy)). `doctor` check 20 shows where both listen.

**A firewall on the hosts.** When ufw is already active on a host, the install adds rules that let the monitor's private address reach the ports Prometheus scrapes there: 26660 on the validator and backup nodes, and the node_exporter and collector ports (9910, 9911). Each rule carries the comment `celestia-mon`, is listed in the plan before you confirm, and is recorded in `/var/lib/celestia-mon/firewall-added.json`. A source that already has a rule for the port is left alone and not recorded, and the uninstall deletes only recorded rules. With any other firewall, allow those ports yourself. With Fibre on, the monitor also connects to Fibre's own port (7980) on each Fibre host's private address, for its [upload readiness](#upload-readiness); no rule is added for it, because Fibre takes uploads on that port from anywhere, but a firewall that opens it on the public interface only must let the monitor's private address in too. On a Tailscale network, ufw does not see tailnet traffic; the tailnet policy is the filter ([security.md](security.md#tailnet-policy)).

`doctor` checks these settings and prints the exact line to change. You can run it before the install: copy `tools/doctor.sh` to the host and run `sudo bash doctor.sh`. Without a settings file it guesses the host's roles from its systemd units; `bash doctor.sh --help` lists the flags.

## 3. Write the inventory

One inventory describes every host, and every host gets a copy of it. It holds no secrets: those go into one vault file that only the monitor host gets. On your workstation, in a clone of this repository, start from the example that matches your setup:

```sh
cp -r ansible/inventory/example-single ~/celestia-mon-inventory   # one host: validator and monitor, local key, Fibre off
cp -r ansible/inventory/example-split ~/celestia-mon-inventory    # separate validator, KMS, Fibre and monitor hosts
cp -r ansible/inventory/example-mainnet ~/celestia-mon-inventory  # validator with a standby Fibre; Fibre host with a backup node and the monitor
```

In `hosts.yml`, put each host in its groups (`validator`, `backup_node`, `fibre`, `kms`, `monitor`; step 1) and set its variables. The host names are yours to choose; you pass the name to `install.sh --host` on that host. The groups name where each role is installed. Which node signs and which Fibre runs are read from the node metrics and from systemd, so after a tmkms repoint or a Fibre failover the inventory stays as it is ([mainnet layout](#mainnet-layout)).

| Variable | Hosts | Meaning |
|---|---|---|
| `celestia_mon_private_ip` | every host celestia-validator-monitoring installs on | Private address where its services listen and Prometheus scrapes. Required, and it must be an address of that host: the install checks it. |
| `celestia_mon_data_mounts` | validator, backup_node, fibre, kms | Mount points to watch for free space and read-only state, for example the node's data disk. |
| `celestia_mon_fibre_mount` | fibre | Mount point of the Fibre store, on the standby too. |
| `celestia_mon_fibre_public_endpoint` | fibre | The `host:port` that `celestia-appd tx valaddr set-host` registers while this host's Fibre is the active one. `doctor` accepts a registration of any Fibre host's endpoint, and on a host with both the validator and a Fibre install it checks that the registration names this host exactly while its Fibre runs. |
| `celestia_mon_kms_scrape` | kms | `true` installs node_exporter on the KMS host, so tmkms and its disks are watched. Default `false`: nothing is installed there. |
| `celestia_mon_node_home` | validator, backup_node | celestia-appd home, for `doctor`. Default: found from the unit. |
| `celestia_mon_link_mbps` | validator, backup_node, fibre, kms | Optional. The link speed in Mbit/s for the dashboard's Network peak. Set it when the host reports no NIC speed (common on VMs), has more than one physical NIC, or its plan is below the port speed. Default: the NIC's port speed. |
| `celestia_mon_firewall_sources` | validator, backup_node, fibre, kms | Optional. The addresses ufw lets reach this host's scraped ports. Default: the monitor's `celestia_mon_private_ip`. |

In `group_vars/all.yml`, set the shared settings:

| Variable | Meaning |
|---|---|
| `celestia_mon_signer` | `local` (key file on the node) or `remote` (tmkms). With `local`, the signer panels and RemoteSignerProblem are left out. |
| `celestia_mon_fibre_enabled` | `true` installs and renders everything Fibre-related. Keep `false` until app v10 is active on your network: before that, Fibre does not run and `x/valaddr` does not exist. |

Optional settings, with their defaults, are in `ansible/roles/celestia_mon/defaults/main.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `celestia_mon_units` | `celestia-appd`, `fibre`, `tmkms` | systemd unit names of the Celestia services. |
| `celestia_mon_node_metrics_port`, `celestia_mon_node_rpc_port` | 26660, 26657 | celestia-appd's metrics and RPC ports. |
| `celestia_mon_fibre_port` | 7980 | Fibre's own port, where the monitor asks each Fibre host for its [upload readiness](#upload-readiness). Set it for a Fibre host whose Fibre listens on another port. |
| `celestia_mon_ports` | 9910 to 9919 | celestia-validator-monitoring's ports (table in step 1). |
| `celestia_mon_thresholds` | see [alerts.md](alerts.md) | Alert thresholds. |
| `celestia_mon_prometheus_retention` | `30d` | How long Prometheus keeps data. |
| `celestia_mon_prometheus_retention_size` | `8GB` | How much data Prometheus keeps; the oldest goes first when either limit is reached. Its disk needs this x 1.5 + 2 GiB (step 1). |
| `celestia_mon_grafana_listen` | `127.0.0.1` | The address Grafana listens on, on the monitor (step 7). Another value must be a private address of the monitor host. |
| `celestia_mon_grafana_clients` | `[]` | With a private `celestia_mon_grafana_listen`: the private address of each machine that opens Grafana there, one address each, no prefix, none a host of the inventory (step 7). Grafana exchanges network traffic with nothing else but loopback and its own address. |
| `celestia_mon_nice`, `celestia_mon_cpu_weight`, `celestia_mon_io_weight` | 10, 20, 20 | CPU and disk priority of every unit. |
| `celestia_mon_memory` | table in step 1 | `high` and `max` per service (`node_exporter`, `otelcol`, `prometheus`, `alertmanager`, `grafana`, `announcements`, `tensile`, `signal`, `blackbox`). |
| `celestia_mon_signal` | `{}` (off) | Upgrade signalling, on the monitor host: the RPCs to read x/signal through and the validator's operator address ([below](#upgrade-signalling)). |
| `celestia_mon_validator_name` | `""` | The validator's name at the top of the dashboard, for example its moniker: up to 13 characters at full size; 14 to 17 characters make the top strip's values a little smaller; a longer name stops the dashboard build. Empty: the dashboard reads "Validator" under the network's name. |
| `celestia_mon_signer_multi_node` | `false` | With a remote signer: `true` when tmkms is set up to sign through several nodes at once (several `[[validator]]` entries). Two signing nodes are then normal: the dashboard reads "Two nodes" instead of the amber "Two signers" it shows when two nodes sign for 3 minutes, and [ValidatorSigningTwice](alerts.md#validatorsigningtwice) is left out. |

Overriding a map (`celestia_mon_units`, `celestia_mon_ports`, `celestia_mon_thresholds`, `celestia_mon_memory`) replaces the whole map, so list every key.

**Secrets.** The monitor's settings with secrets live in `host_vars/<monitor>/vault.yml` (`host_vars/node-1/vault.yml`, `host_vars/mon-1/vault.yml` or `host_vars/fibre-1/vault.yml` in the examples; rename the directory with the host):

| Variable | Meaning |
|---|---|
| `celestia_mon_receivers` | One list of alert receivers (below). |
| `celestia_mon_deadman_webhook_url` | Heartbeat URL of your dead-man service (step 8). Required, `https` (plain `http` only to the monitor itself). |
| `celestia_mon_grafana_admin_password` | Grafana's admin password, at least 16 characters (for example from `openssl rand -base64 18`). |

Each receiver is one of the types below. List any number, several of one type if you like:

| `type` | Keys | Gets page alerts | Gets message alerts |
|---|---|---|---|
| `pagerduty` | `routing_key` (an Events API v2 integration key) | yes | no |
| `telegram` | `bot_token`, `chat_id` (numeric, for example `-1000000000000`) | yes | yes |
| `discord` | `webhook_url` | yes | yes |
| `slack` | `api_url`, optional `channel` | yes | yes |
| `webhook` | `url` | yes | yes |

`page` alerts need someone now; `message` alerts can wait for working hours. Page alerts go to every receiver, message alerts to every receiver except PagerDuty, so list at least one receiver that is not PagerDuty. Without PagerDuty, page alerts still reach your chat channels. The separate `page` and `message` lists of earlier versions are refused. A `url`, `api_url` or `webhook_url` must be `https`; plain `http` only to the monitor itself or to an address of your private network, such as a relay there, because the URL holds the receiver's secret. [alerts.md](alerts.md#where-alerts-go) has an example.

Replace the placeholders, then encrypt the file with [ansible-vault](https://docs.ansible.com/ansible/latest/vault_guide/vault_encrypting_content.html). On the monitor host, `install.sh` does it with the same checked Ansible it installs with, and removes that Ansible again afterwards: `sudo ./install.sh --edit-vault <inventory>/host_vars/mon-1/vault.yml` encrypts a plain file, opens an encrypted one for editing, and creates a missing one. On your workstation, use any Ansible install, or none through uv:

```sh
ansible-vault encrypt ~/celestia-mon-inventory/host_vars/mon-1/vault.yml
uvx --from ansible-core ansible-vault encrypt ~/celestia-mon-inventory/host_vars/mon-1/vault.yml   # the same, without installing Ansible
```

Encrypt the whole file, not single values in it: `install.sh` refuses to run while a `vault.yml` in the inventory's `host_vars` or `group_vars` is not an encrypted file, or while group or others can read it (`chmod 600` it after you copy it to the host). `sudo ./install.sh --edit-vault <file>` (or `ansible-vault edit`) changes it later. Only the monitor host gets this file (step 4): the other hosts need none of its values, and an encrypted file they cannot open would stop their install.

## 4. Copy the repository to each host

Pack the release or commit you reviewed, and note its sha256:

```sh
git archive --format=tar.gz --prefix=celestia-validator-monitoring/ -o celestia-validator-monitoring.tar.gz <tag or commit>
sha256sum celestia-validator-monitoring.tar.gz
```

Copy it and the inventory to each host. The monitor gets the vault file, the other hosts do not:

```sh
scp celestia-validator-monitoring.tar.gz <host>:
rsync -a --exclude vault.yml ~/celestia-mon-inventory/ <host>:celestia-mon-inventory/   # validator, Fibre, KMS hosts
rsync -a ~/celestia-mon-inventory/ <monitor>:celestia-mon-inventory/                     # the monitor
```

On the host, check the archive against the sha256 from your workstation and unpack it:

```sh
sha256sum celestia-validator-monitoring.tar.gz
tar -xzf celestia-validator-monitoring.tar.gz
cd celestia-validator-monitoring
```

Optionally, sign the archive instead of comparing hashes by eye. With your SSH key (a FIDO2 key works too) on the workstation:

```sh
ssh-keygen -Y sign -f ~/.ssh/id_ed25519_sk -n file celestia-validator-monitoring.tar.gz   # writes celestia-validator-monitoring.tar.gz.sig
```

Copy the `.sig` file along. On the host, put one line `<your name> <your public key>` into `~/.ssh/allowed_signers` once, then:

```sh
ssh-keygen -Y verify -f ~/.ssh/allowed_signers -I <your name> -n file -s celestia-validator-monitoring.tar.gz.sig < celestia-validator-monitoring.tar.gz
```

`install.sh` runs the repository copy and the inventory as root, so it refuses to run when a file there belongs to a user other than root and you, or is writable by every user.

## 5. Install, host by host

On each host, from the unpacked repository:

```sh
sudo ./install.sh --inventory ~/celestia-mon-inventory/hosts.yml --host val-1
```

On the monitor, whose vault file is encrypted, add `--ask-vault-pass`:

```sh
sudo ./install.sh --inventory ~/celestia-mon-inventory/hosts.yml --host mon-1 --ask-vault-pass
```

Go in this order: the validator host and each backup node (and the KMS host, if `celestia_mon_kms_scrape: true`), then the Fibre hosts and the monitor host, then point Fibre at its collector on each Fibre host (step 6). On a KMS host without `celestia_mon_kms_scrape`, `install.sh` reports that it installs nothing there.

`install.sh`:

1. **checks the host**, changing nothing: Ubuntu 24.04 with systemd, run as root, `celestia_mon_private_ip` (and on the monitor `celestia_mon_grafana_listen`) is an address of this host, celestia-validator-monitoring's ports are free (or held by its own services), and there is enough free disk, on the monitor including the room for Prometheus's data (step 1). A failed check stops it before any change and names the fix. It also reports the memory available now against the services' memory targets and limits.
2. **plans:** a dry run of the whole install for this host only, with a short summary: the services and their users, the addresses and ports they listen on, the directories, the disk and memory they need, and each ufw rule it would add (`ufw rule:  allow from <monitor address> to port <port>/tcp (comment celestia-mon)`). On a Fibre host it also says whether the Fibre unit already sends its metrics to the collector, and if not, the exact change for you to make (step 6). It ends with "The dry run finds nothing to change." or "The dry run reports N change(s)".
3. **asks** `Apply this to <host>? [y/N]`. Anything but `y` or `yes` stops it with nothing changed.
4. **installs**, and reports the number of changes, or "No changes: <host> already matches the inventory."
5. **runs `celestia-mon-doctor`** and prints its result (step 9). Before Fibre points at its collector, and before every host is installed, some checks fail; `install.sh` still ends successfully.
6. **prints the command that removes it again** (step 14).

`install.sh` installs ansible-core and every package it needs from `ansible/requirements.lock`, which pins each one with the sha256 of its files, and refuses to run when the lock does not match the `ansible_core` pin in `versions.yml`. The lock is made in GitHub Actions by the `lock` workflow (`.github/workflows/lock.yml`), never on a workstation.

| Flag | Meaning |
|---|---|
| `--inventory FILE` | The shared inventory: its `hosts.yml`, or the directory. |
| `--host NAME` | This host's name in the inventory (not a group). |
| `--ask-vault-pass` | Ask for the vault password, on the host whose `host_vars` hold the encrypted file. |
| `--check` | Dry run only: the checks and the plan, nothing changed. |
| `--yes` | Apply the plan without asking. |
| `--uninstall` | Remove celestia-validator-monitoring's services, binaries and recorded ufw rules from this host, and keep its data, settings and users (step 14). |
| `--purge` | With `--uninstall`: also delete the data, the settings and the users, after a second confirmation (step 14). |

Run `install.sh` again on each host after every inventory change; a host with nothing to change says so. If the install stops with an error, fix the cause and run it again: it continues where it stopped.

## 6. Point Fibre at its collector

Only if you run Fibre. Fibre has no scrape endpoint; it pushes metrics over OTLP to the collector installed on the Fibre host, which takes them on `127.0.0.1:9915` (`celestia_mon_ports.otelcol_otlp`). celestia-validator-monitoring never edits or restarts Fibre, so this step is yours:

1. Read the `Fibre:` line of the install plan on the Fibre host. It reads Fibre's unit and says whether it already sends to the collector; if not, it names the exact change: `--otel-endpoint http://127.0.0.1:9915` in the unit's `ExecStart`, or `FIBRE_OTEL_ENDPOINT=http://127.0.0.1:9915` where the unit already sets that variable (an `EnvironmentFile=` or `Environment=`). Use the base URL, without `/v1/metrics`.
2. Make the change in the unit (`sudo systemctl edit --full <fibre unit>`, or the environment file).
3. At a moment you choose, apply it: `sudo systemctl daemon-reload && sudo systemctl restart <fibre unit>`. On a standby Fibre host, run only `sudo systemctl daemon-reload` and leave the unit stopped: it uses the new endpoint when it next starts, so its metrics flow the moment it is promoted. The plan says which case applies.
4. Check it: `sudo celestia-mon-doctor` on the Fibre host (check 7) confirms the endpoint, and on the monitor check 13 confirms that Fibre's metrics arrive, with a sample from each active Fibre host under 3 minutes old (a standby sends none).

## 7. Open Grafana

Grafana listens on the monitor's loopback address, 127.0.0.1, by default, so no network reaches it. Open it through an SSH tunnel from your machine:

```sh
ssh -N -L 9914:127.0.0.1:9914 <user>@<monitor>
```

Then open `http://localhost:9914`. Log in as `admin` with `celestia_mon_grafana_admin_password`. Grafana reads that password only on its first start; change it later in Grafana (Profile, Change password). The same tunnel can carry Prometheus and Alertmanager, which listen on the monitor's loopback only:

```sh
ssh -N -L 9914:127.0.0.1:9914 -L 9912:127.0.0.1:9912 -L 9913:127.0.0.1:9913 <user>@<monitor>
```

Then also `http://localhost:9912/alerts` (Prometheus) and `http://localhost:9913` (Alertmanager), as user `celestia-mon` with the password from `sudo cat /etc/celestia-mon/api-password` on the monitor; your browser asks for it. Forward 9912 and 9913 only while you use them: while the tunnel is open and the browser remembers the login, other web pages in that browser can send requests to them too.

Two optional alternatives:

- **A private address.** Set `celestia_mon_grafana_listen` to a private address of the monitor (a Tailscale, WireGuard or LAN address) and `celestia_mon_grafana_clients` to the private address of each machine you open Grafana from (for example `[10.20.0.50]`, your workstation's address on that network), and run `install.sh` on the monitor again; then open `http://<that address>:9914` from one of those machines. Grafana's unit exchanges network traffic with nothing else but loopback and its own address, so a Grafana login cannot reach the validator or another host through a datasource. The install stops when the address is not one of the monitor's or the list is empty, and Grafana never listens on `0.0.0.0`. With ufw active on the monitor, allow your workstation to port 9914 yourself (`sudo ufw allow from <addr> to any port 9914 proto tcp`). Tailscale is never required.
- **A reverse proxy.** Keep `127.0.0.1` and run the proxy on the monitor itself, in front of `http://127.0.0.1:9914`, with its own TLS.

The dashboard is **Celestia validator** in the **Celestia validator** folder. [panels.md](panels.md) explains each panel. It is generated from your inventory, so groups that do not apply are left out and the threshold lines match the alerts. Do not edit it in Grafana; change the inventory and run `install.sh` on the monitor again.

## 8. Set up the dead-man service

The Watchdog alert is always firing. Alertmanager sends it every 1 to 2 minutes to a dead-man service, which alerts you when the heartbeats stop: when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network. The heartbeat also stops for an hour after a page or message notification failed to go out, so a broken alert channel (an expired bot token, a removed chat) reaches you through the dead-man service.

1. Choose any dead-man (heartbeat) service that gives you a URL to ping and alerts you when pings stop. Alertmanager pings it with an HTTP POST.
2. Set its period to **5 minutes or more**, so a ping that arrives two minutes apart never trips it.
3. Connect it to **PagerDuty and to email or another channel than your alert chat**. A dead monitor host, or a whole data centre gone, then still rings your phone, and when the alert chat is what broke, the dead-man service is the only one left to tell you.
4. Put the URL in `celestia_mon_deadman_webhook_url` in the vault file and run `install.sh` on the monitor again.
5. Test it: on the monitor, run `sudo systemctl stop celestia-mon-alertmanager`, wait until the dead-man service alerts you, then `sudo systemctl start celestia-mon-alertmanager`.

The URL is required: the install on the monitor stops when it is empty, still the example's placeholder, or not `https` (plain `http` only to the monitor itself, such as a local relay), because without it nothing tells you that monitoring itself is down. Keep it in the vault: anyone who has it can keep the heartbeat green.

## 9. Run doctor

On every host celestia-validator-monitoring is installed on, the monitor included:

```sh
sudo celestia-mon-doctor
```

It changes nothing. It reads `/etc/celestia-mon/doctor.conf`, which the install writes; any setting can also be given as a flag (`celestia-mon-doctor --help`). Without a settings file it guesses the host's roles from its units; on a backup node, pass `--roles backup_node`. The settings include the install type: with `signer=local` doctor never expects the remote signer's metrics, and with `fibre_enabled=false` never Fibre's or the collector's. Each check prints one line (check 12 one line per group), starting with its status:

| Status | Meaning |
|---|---|
| `OK` | The check passed. |
| `NOT YET` | Metrics that appear only after an event; the line names the event. |
| `FAIL` | The check failed; the line ends with the fix to apply. |
| `WARN` | Worth fixing, but not counted as a failure. |
| `INFO` | A measurement to read, never a failure. |
| `SKIP` | The check does not apply on this host, or cannot run yet. |

The exit code is 1 when a check failed.

| # | Host | Check |
|---|---|---|
| 1 | validator, backup_node | celestia-appd metrics are enabled and served. Warns when they listen on every interface (`prometheus_listen_addr` with an empty host or `0.0.0.0`) or on a public address, and fails then when no firewall is active (ufw, firewalld or nftables): whoever reaches the port can read the metrics and hold its few connections (3 by default), which blinds the signing alerts. The fix binds them to the private address the monitor scrapes, never `0.0.0.0`. |
| 2 | validator, backup_node | The signer setting matches `celestia_mon_signer`; with a remote signer, the median signing latency is at most 10 ms (skipped until 50 signatures after a start). With `priv_validator_laddr` empty, celestia-appd signs with its local key file, so doctor reads that key's voting power from the node's `/status`: above 0 it fails on a backup node, and on a validator set to `signer: remote`, because two signers of one validator double-sign. A jailed or unbonded validator's key also has voting power 0, so on a backup node doctor prints the key's address to compare with check 4 on the validator, and on a backup node that is also the monitor it fails when Prometheus saw that address as the validator's in the last 30 days. Otherwise `INFO` on a backup node: whether `priv_validator_laddr` is set (with it set, celestia-appd exits at start when no signer connects). |
| 3 | validator, backup_node | Node progress: the height advances and `catching_up=false`. This shows that the node advances, not that it is at the network's head. |
| 4 | validator | The validator's consensus address, from the node's `/status`. |
| 5 | validator | With Fibre on: the Fibre host registered in `x/valaddr`, compared with `celestia_mon_fibre_public_endpoint` of every Fibre host (any of them may be the registered one). When the validator host also has a Fibre install, the registration must name this host while its Fibre runs, and must not while it is the standby. |
| 6 | fibre | The Fibre unit is active and its port (7980 by default) listens. With two or more Fibre hosts, a stopped unit with a closed port is `OK` (standby); a port that listens while the unit is stopped is a warning, and so is a stopped unit that is enabled, which a reboot would start next to the active Fibre. |
| 7 | fibre | Fibre's OTLP endpoint points at the local collector, on a standby too, so its metrics flow the moment it starts; the fix for a standby never starts it. |
| 8 | all | The `celestia-mon-*` services listen on private addresses only (Grafana on 127.0.0.1 or `celestia_mon_grafana_listen`), Prometheus and Alertmanager on loopback only; other software on the host is not checked. Reports whether a firewall is active. |
| 9 | all | Time sync is active and the clock offset is small. |
| 10 | all | Free space on the data mounts; the Fibre mount is not read-only. |
| 11 | monitor | Prometheus and Alertmanager answer on 127.0.0.1, and every scrape target is up. A down target means its collector or exporter is broken; it is listed with its error and the fix for that job. |
| 12 | monitor | Every metric the alert rules read: `OK` when it has series, `NOT YET` with the event that makes it appear (a first signing failure, a first upload, the validator's first own signature, its first missed signature), `WARN` when it is missing although its source is up (most likely a renamed metric), `FAIL` when its source is broken (a target down, or Fibre not pushing). `signer` and `fibre_enabled` decide which metrics are expected at all. |
| 13 | monitor | Fibre pipeline, with Fibre on: Prometheus has fresh Fibre series from each Fibre host whose unit is active, a sample under 3 minutes old; a standby needs only its collector up. The collector takes OTLP only on 127.0.0.1 at `celestia_mon_ports.otelcol_otlp`, so this shows that the unit's endpoint matches it end to end, not just that the port is open. |
| 14 | all | Unit memory: each `celestia-mon-*` unit's use against its `MemoryHigh` and `MemoryMax`; warns above 80% of `MemoryHigh`. |
| 15 | monitor | Monitor disk: what Prometheus holds plus the free space on its disk is at least the retention size x 1.5 + 2 GiB, the same rule as the install's check. |
| 16 | monitor | Compaction I/O (`INFO`): write latency and busy time of each Celestia host's disks around Prometheus's last compaction, next to the hour before. |
| 17 | fibre | Fibre's signer address (`--signer-grpc-address` or `signer_grpc_address`) is an IP address and port: Fibre resolves a host name at every dial, and without mTLS it accepts only a loopback IP, not `localhost`. On a host with a celestia-appd node, a signer on this host (127.0.0.1, or with mTLS this host's own address) must match the node's one privval gRPC listener (`priv_validator_grpc_laddr`) in address, port and mode: it cannot be both plaintext on loopback and mTLS for a Fibre on another host, and an mTLS signer needs the node's mTLS address. A mismatch fails for an active Fibre and warns for a standby ([mainnet layout](#mainnet-layout)). With mTLS, it warns when group or other can read Fibre's client key. |
| 18 | monitor | Node lag: every celestia-appd node's height against the highest one, and which node signs. Warns when a node is more than `node_behind_blocks` (20) behind, or reports no height: a backup node that lags cannot take over signing. |
| 19 | monitor | Fibre active, with Fibre on: which Fibre host runs Fibre, read from systemd. `FAIL` when none does (FibreDown pages after 10 minutes), `WARN` when two or more do (two Fibre instances must never be live at once). |
| 20 | validator, backup_node | Signer binds: where `priv_validator_laddr` (tmkms) and `priv_validator_grpc_laddr` (Fibre) listen, from `config.toml` and the listening sockets; doctor never connects to them. Warns on every interface: celestia-appd does not authenticate the signer that connects to `priv_validator_laddr`, and ufw does not filter a Tailscale interface. Fails on a public address, or on a privval gRPC beyond loopback with `priv_validator_grpc_allow_insecure = true`. On a backup node it notes an empty `priv_validator_grpc_laddr`: after a tmkms repoint, no Fibre can sign through that node until it is set. |
| 21 | monitor | Silences: warns on each active silence in Alertmanager that holds back a page alert (NodeNotAdvancing, ValidatorNotSigning, FibreDown, DiskLow, and UpgradeSignalMissing with upgrade signalling on) or ends more than 4 hours from now, with its matchers, author, end and ID: while it holds an alert back, nothing is sent and the dead-man heartbeat stays green. A host silence counts for its length only. |
| 22 | monitor | Dead-man heartbeat: Alertmanager sent the Watchdog to the dead-man service in the last 10 minutes, and none of those sends failed (`FAIL`). `WARN` when none went out (the Watchdog stops for an hour after a failed page or message notification); `NOT YET` in the first 10 minutes after Prometheus or Alertmanager starts. |
| 23 | monitor | Upgrade signalling, with `celestia_mon_signal` set (`SKIP` without): celestia-mon-signal is up and has read an RPC (`/abci_info`, x/signal and the validator's entry, which needs an operator address that decodes) in the last 15 minutes. Prints the last reading: the RPC, the app version, the share of the voting power that signalled the next version against the 5/6 needed, your validator's signal, and a scheduled upgrade. `WARN` while validators signal the next version and yours has not, and while the reading comes from another RPC than the first one listed; `FAIL` when the service is down or has read no RPC for 15 minutes, with the reasons of its failed reads. |
| 24 | monitor | [Upload readiness](#upload-readiness), with Fibre on: asks each Fibre host's port, through celestia-mon-blackbox, whether Fibre is ready now. `OK` when every Fibre that runs is Ready; a stopped standby does not answer, as expected. `FAIL` when one is Not ready: Fibre reports that at least one of its own checks fails, so uploads to it may fail, and the fix names the `journalctl` line that says which check. `WARN` when one is Not reported (Fibre older than v10.5.0) or when the monitor cannot reach the port of an active Fibre, so its readiness is not checked. |

Right after an install, doctor on the monitor can take up to a minute while Prometheus starts and scrapes each target once. On the first deploy, check 12 is how the Fibre metric names get confirmed: any metric it lists as `WARN` needs a look. Run doctor again after every celestia-app or Fibre upgrade and every config change.

**Measure the impact of compactions on the first deploy.** Prometheus compacts its data about every 2 hours, the first time about 3 hours after it starts, and a compaction reads and writes on the disk of its data directory. Where the monitor shares a disk with the validator or Fibre, `Nice` and `IOWeight` lower that load's priority but do not guarantee it has no impact (step 1). Once Prometheus has run for a few hours, run `sudo celestia-mon-doctor` on the monitor and read check 16: the peak write latency and busy time of the worst disk per Celestia host in the 10 minutes around the last compaction, with the hour before in brackets. A clear rise on a validator or Fibre disk means Prometheus competes with it: put Prometheus's data (`/var/lib/celestia-mon/prometheus`) on another disk, or the monitor on another host. To look at it yourself, the Disk latency charts in the dashboard's "Servers: restarts, disk activity and clocks" row show each server's disks over time ([Node Exporter Full](https://grafana.com/grafana/dashboards/1860-node-exporter-full/) shows each disk), and these queries work in Grafana's Explore (T is the time of the last compaction):

```promql
max_over_time(timestamp(increase(prometheus_tsdb_compactions_total{job="prometheus"}[2m]) > 0)[6h:1m])
rate(node_disk_write_time_seconds_total{job="node"}[1m]) / rate(node_disk_writes_completed_total{job="node"}[1m])
rate(node_disk_io_time_seconds_total{job="node"}[1m])
```

The first gives T, the second the average write latency per disk, the third the share of time each disk was busy.

## 10. Check that alerts reach you

The first install on the monitor, and every later run that changes Alertmanager's receivers, sends one test message (`NotificationTest`) to the chat channels and webhooks; `install.sh` says so at the end. It does not reach PagerDuty, and it resolves after 5 minutes. To test both severities, PagerDuty included, send a test alert of each on the monitor and check that it arrives where you expect: the page alert at every receiver, PagerDuty included, the message alert at every receiver except PagerDuty. Alertmanager's `amtool` is in `/opt/celestia-mon/alertmanager-<version>/`, with the Alertmanager version from `versions.yml`. Run it as root (`sudo -i`): its login to Alertmanager is in a file only root can read.

```sh
amtool=/opt/celestia-mon/alertmanager-<version>/amtool
am="--alertmanager.url=http://127.0.0.1:9913 --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml"
$amtool $am alert add alertname=CelestiaMonTest severity=page host=test --annotation=summary='celestia-validator-monitoring test'
$amtool $am alert add alertname=CelestiaMonTest severity=message host=test --annotation=summary='celestia-validator-monitoring test'
```

Each resolves by itself after 5 minutes. [alerts.md](alerts.md) describes every alert and what to check first.

## 11. Subscribe to network notices

Upgrades are the most common reason a node stops at a height or crash loops. Celestia announces upgrades and incidents in its announcement channels on Discord, and per network on [status.celestia.org](https://status.celestia.org/).

celestia-validator-monitoring can pass the Discord announcements on to your receivers, PagerDuty included, as [CelestiaAnnouncement](alerts.md#celestiaannouncement) alerts: follow Celestia's announcement channels from a channel on your own Discord server, give a bot of yours read access to it, and set `celestia_mon_announcements` (inventory) and `celestia_mon_discord_bot_token` (vault) on the monitor. [alerts.md](alerts.md#celestiaannouncement) has the steps. Otherwise subscribe on status.celestia.org for the networks you run, or route its feed ([RSS](https://status.celestia.org/feed.rss), [Atom](https://status.celestia.org/feed.atom)) into the same channel as your alerts. See also Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page.

The upgrade itself is read from the chain when you set `celestia_mon_signal` ([upgrade signalling](#upgrade-signalling)): a page while validators signal a version yours has not, and a message when the upgrade is scheduled and when it is less than a day away.

## 12. Before planned restarts and upgrades

Before you restart or upgrade celestia-appd, Fibre, tmkms or a whole host, silence that host's alerts on the monitor, as root (`sudo -i`), so the planned downtime pages nobody:

```sh
amtool=/opt/celestia-mon/alertmanager-<version>/amtool
am="--alertmanager.url=http://127.0.0.1:9913 --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml"
$amtool $am silence add host=<host> --duration=30m --comment="planned upgrade of <host>"
```

It prints the silence's ID. Every alert labelled `host=<host>` is held back until the silence ends; the Watchdog keeps running. NodeNotAdvancing, ValidatorNotSigning and FibreDown watch the whole install and name no host, so a host silence does not cover them: when the work stops what they watch on every host (Fibre stopped with no standby started, the only node restarted, tmkms restarted or repointed), silence them by name as well, for example `silence add alertname=FibreDown --duration=30m --comment="planned Fibre stop"`. When the work is done and the dashboard looks healthy again, end the silence rather than wait for it:

```sh
$amtool $am silence query host=<host>   # lists the silences for the host, with their IDs
$amtool $am silence expire <ID>
```

Alerts that are still true after the silence ends are sent then. [alerts.md](alerts.md#before-planned-restarts-and-upgrades) has the same commands. `sudo celestia-mon-doctor` on the monitor (check 21) lists every active silence that holds back a page alert or ends more than 4 hours from now.

## 13. Monthly drill

Once a month, check that every path still works:

1. **Dead-man:** stop Alertmanager on the monitor until the dead-man service alerts you through PagerDuty and its other channel, then start it again (step 8).
2. **Alert channels:** send a test alert of each severity (step 10) and check that both arrive.
3. **Hosts:** `sudo celestia-mon-doctor` on every host (step 9).
4. **Drift:** `sudo ./install.sh --check --inventory ~/celestia-mon-inventory/hosts.yml --host <name>` on every host (add `--ask-vault-pass` on the monitor). "The dry run finds nothing to change." means the host still matches the inventory.

## 14. Upgrade or remove

**Upgrade.** Copy the new release to each host as in step 4 and run `install.sh` there. The dry run names each new version it would install ("Would install ..."); the service restarts on the new version, and the old version's directory is removed once the new one runs. Data and settings stay. The install records the sha256 of each binary it installs (`SHA256SUMS` next to them) and installs again any binary that no longer matches; an install without that file, made by an earlier version, downloads its binaries once more, and a service restarts only when its binary changed.

**Remove.** On each host:

```sh
sudo ./install.sh --uninstall --host <name>
```

It needs neither the inventory nor the vault password, and it shows its plan and asks first as well (`--check` shows the plan only, `--yes` skips the question). It stops, disables and removes every `celestia-mon-*` unit, removes `/opt/celestia-mon` (the binaries), `/var/cache/celestia-mon`, `/usr/local/bin/celestia-mon-doctor` and the settings files that hold secrets (Alertmanager's settings with the receivers and the dead-man URL, Grafana's admin password and its datasource, the API passwords, the Discord bot token, and celestia-mon-signal's settings with its RPC URLs; a reinstall writes them again, with new API passwords), and deletes the ufw rules the install recorded in `/var/lib/celestia-mon/firewall-added.json`: each one only while ufw still lists it with the recorded source, port, protocol and the comment `celestia-mon`. A rule that was there before the install was never recorded and stays. It keeps Prometheus's data and Grafana's database (`/var/lib/celestia-mon`), the other settings (`/etc/celestia-mon`) and the `celestia-mon-*` system users and groups, so a reinstall picks up where it left off. It prints the command that deletes those too:

```sh
sudo ./install.sh --uninstall --purge --host <name>
```

`--purge` asks a second time, naming the paths and the users it deletes for good (`--yes` skips both questions). Nothing else is touched: celestia-appd, Fibre, tmkms, other services and other firewall rules stay as they are. Two settings you made yourself stay too: celestia-appd's `[instrumentation]` section, and Fibre's `--otel-endpoint`; with the collector gone, Fibre logs export errors until you remove that setting and restart Fibre.

An install made before celestia-validator-monitoring recorded its ufw rules has no `firewall-added.json`, so its uninstall keeps those rules. List them with `sudo ufw status | grep celestia-mon` and delete each one by hand: `sudo ufw delete allow from <address> to any port <port> proto tcp`.

## Mainnet layout

`ansible/inventory/example-mainnet` describes a validator with a backup node and a standby Fibre, signing through tmkms:

| Host | Groups | Runs |
|---|---|---|
| `val-1` | `validator`, `fibre` | celestia-appd without a key; tmkms on `kms-1` signs through it. A standby Fibre: installed, stopped and disabled, port 7980 closed (its signer: below). |
| `fibre-1` | `fibre`, `backup_node`, `monitor` | The active Fibre; a synced celestia-appd without a key that can take over signing; Prometheus, Alertmanager and Grafana. |
| `kms-1` | `kms` | tmkms only; nothing is installed there. |

Run `install.sh` on `val-1` and on `fibre-1` (with `--ask-vault-pass`, step 5); `kms-1` needs nothing. Set `[instrumentation]` on both nodes (step 2). Both Fibre hosts get the collector, the standby too; on `val-1`, point the standby's OTLP endpoint at it and leave the unit stopped (step 6).

**Backup node.** A synced celestia-appd without a key, ready to take the validator role over. tmkms connects to one node at a time; moving it is a change of its `addr` and a restart of tmkms. While the backup node is not the signer's, keep its `priv_validator_laddr` empty: with it set, celestia-appd exits at start when no signer connects (`doctor` check 2 reports it as `INFO`). With it empty, the node signs with its local key file (`config/priv_validator_key.json`), so that file must never be the validator's key: a home copied from the validator still holds it, and the node then double-signs next to tmkms. `doctor` check 2 reads the key's voting power from the node's `/status` and fails when it is above 0. Voting power 0 does not clear the key: a jailed validator's key has none either and signs again after unjail. Check 2 prints the key's address to compare with check 4 on `val-1`, and on `fibre-1`, which is also the monitor, it fails when Prometheus saw that address as the validator's in the last 30 days. Its metrics are scraped like the validator's: its card in the dashboard's Servers band reads "Backup, in sync · 28 peers" (its peer count) while it can take over (amber "Node behind · 35 blocks" while it lags), NodeBehind sends a message when it lags for 10 minutes, and `doctor` check 18 on the monitor shows its lag.

**Standby Fibre.** Every host with a Fibre install is in the `fibre` group, and systemd says which one runs. A stopped Fibre unit is a standby: it raises no alert, reads "Standby, stopped" in its card in the dashboard's Servers band while the other Fibre runs, and is `OK` in `doctor` check 6. Keep the standby unit disabled (`systemctl disable <fibre unit>`), so a reboot does not start it next to the active one; check 6 warns while it is enabled. Its OTLP endpoint must still point at its collector (check 7).

**The standby's signer.** Fibre signs through the validator node's privval gRPC, and celestia-appd has one such listener (`priv_validator_grpc_laddr`), either mTLS or plaintext. On `val-1` it serves the active Fibre on `fibre-1` with mTLS on `val-1`'s private address, so nothing answers a plaintext signer on 127.0.0.1 there. Two setups work:

- The standby dials the same mTLS address as the active Fibre, `<val-1 private address>:26669`, with a client certificate of its own from the same CA (`--signer-grpc-ca-file`, `--signer-grpc-cert-file`, `--signer-grpc-key-file`, or `signer_grpc_*` in its `server_config.toml`). Nothing changes at promotion, so prefer this one.
- The standby keeps 127.0.0.1:26669 in plaintext, and at promotion you set `val-1`'s `priv_validator_grpc_laddr` to `127.0.0.1:26669` without the TLS files and restart celestia-appd before you start the standby. The restart is a short gap in the validator's signing.

`doctor` check 17 on `val-1` warns while the standby's signer does not match the node's listener. Keep each Fibre's client key `0600`, owned by Fibre's user (check 17 warns otherwise).

**When a role moves.** Neither move needs an inventory change or a new install.

| Event | Alerts | Dashboard |
|---|---|---|
| Fibre failover: Fibre stops on `fibre-1`, the standby on `val-1` starts, and `x/valaddr` is pointed at `val-1` | FibreDown pages only when no Fibre unit has been active for 10 minutes, so a failover within that time pages nobody. FibreBothActive sends a message when both run for 5 minutes. FibreNotServing watches whichever host's unit is active. | The role band's "Fibre active on" changes from `fibre-1` to `val-1`; in the Servers band, Fibre reads "Active, serving" on `val-1` and "Standby, stopped" on `fibre-1`. During the gap the Fibre tile reads "Not running" in amber (red only if FibreDown pages), and with the outside check "Fibre from outside" reads "Wrong host" until `x/valaddr` names `val-1`. The Fibre panels show `val-1`'s series. |
| Validator host down, tmkms pointed at the backup node on `fibre-1` | ValidatorNotSigning pages about 2 minutes after the signatures stop (its first step: `journalctl -u tmkms` on the KMS host) and resolves once `fibre-1` signs. ServiceDown sends messages for `val-1`. NodeNotAdvancing stays quiet while `fibre-1` advances. Until Fibre's signer follows (below), FibreNotServing (`uploads_failing`) sends a message for `fibre-1`. | Servers band: `val-1`'s card reads "Not reporting", `fibre-1`'s "Backup, in sync · 28 peers" until it signs. The Validator tile reads "Not seen" until `fibre-1` signs; then Signing through and the signer's card name `fibre-1`, the role band marks the move, and Blocks since signature drops back to 0. |
| tmkms pointed at the other node while both run | ValidatorNotSigning pages when no node has signed for more than 10 blocks for 2 minutes; silence it by name for a planned repoint (step 12). If both nodes keep signing, ValidatorSigningTwice pages about 3 minutes after the second one started. | Signing through reads "Switching" for up to 2 minutes, then names the new node, and the role band marks the move. If both nodes keep signing, it reads "Two signers": amber while ValidatorSigningTwice is pending, red once it pages. |
| `fibre-1` down: the active Fibre, the backup node and the monitor at once | Nothing on `fibre-1` can alert: the dead-man service pages (step 8), and so does the external check on Fibre's port (below) while Fibre stays unreachable. | None until `fibre-1` is back. |

**After a tmkms repoint, repoint Fibre's signer too.** Fibre signs through the privval gRPC of the node tmkms serves. When tmkms moves from `val-1` to the backup node on `fibre-1`, the active Fibre on `fibre-1` still dials `val-1`, and every signature fails while its unit stays active and port 7980 answers. Point its `--signer-grpc-address` (or `signer_grpc_address`) at the backup node's privval gRPC and restart Fibre: `127.0.0.1:26669` without the `signer_grpc_*` files when the backup node serves it in plaintext on loopback, or the backup node's mTLS address with the same client certificate. Set the backup node's `priv_validator_grpc_laddr` ahead of time, because celestia-appd reads it only at start; `doctor` check 20 on the backup node says when it is empty.

**Registering the Fibre host.** `celestia-appd tx valaddr set-host` is signed by the validator's account key, which also controls the validator's staking settings. Keep that key on your workstation, on a Ledger, and never import it on a server:

```sh
celestia-appd tx valaddr set-host <host:port> --from <validator account key> --node <RPC of a synced node>
```

To register without the Ledger during a failover, grant an ops key this one message once, from the Ledger, and let the ops key send it. Try the grant once before you rely on it:

```sh
celestia-appd tx authz grant <ops key address> generic --msg-type=/celestia.valaddr.v1.MsgSetFibreProviderInfo --from <validator account key>
celestia-appd tx valaddr set-host <host:port> --from <validator account address> --generate-only > set-host.json
celestia-appd tx authz exec set-host.json --from <ops key> --node <RPC of a synced node>
```

The fixes `doctor` prints (checks 5 and 19) name the same commands.

**External check on Fibre's port.** celestia-validator-monitoring watches Fibre from inside the hosts; it asks Fibre for its readiness on the private address only, never at its public endpoint. Add a TCP check on the registered `host:port` (port 7980 by default) in an external uptime service, wired to PagerDuty and set to alert after 10 minutes of failure, the delay FibreDown uses. It pages when the monitor host is gone with Fibre, and it catches what the hosts cannot see: a closed firewall, a routing fault, a registration that names the wrong host. After a Fibre failover, point the check at the endpoint now registered. [Tensile](https://tensile.huginn.tech) shows your Fibre server from outside as well.

**KMS standby.** A standby KMS host (tmkms installed without the key, disabled) needs nothing from celestia-validator-monitoring; leave it out of the inventory. The signer's health is read from the node side: signing failures and signer latency (RemoteSignerProblem), missed signatures (SignaturesMissed) and signing that stopped (ValidatorNotSigning).

**DR site.** A disaster-recovery host at another site, which takes over only when the whole primary site is lost (the key loaded there as a key file, Fibre with a loopback signer), is a separate install: its own inventory in the `example-single` layout, with the host also in the `fibre` group, `celestia_mon_signer: local`, `celestia_mon_fibre_enabled: true`, and its own receivers and dead-man URL. Prepare the inventory and copy the repository there ahead of time, and run `install.sh` when you switch: until the key is loaded there, its node signs nothing and ValidatorNotSigning would page.

## Upload readiness

Fibre v10.5.0 and newer runs checks of its own (its app node, the Fibre module, its signer, whether the validator is active, its registration and its store) and reports the result on its own port through the standard gRPC health check: Ready when all of them pass, Not ready when one fails. With Fibre on, the monitor asks each Fibre host for it every 15 seconds through celestia-mon-blackbox, at the host's private address and `celestia_mon_fibre_port` (7980 by default), so Fibre must listen there (it listens on every address by default) and the network between the hosts must let the monitor reach that port.

Not ready means Fibre reports that at least one of its own checks fails; uploads to it may fail. Fibre's log names the failing check and what to do about it: `journalctl -u <fibre unit> | grep "health check failed"`. Fibre logs a check only when its result changes, so read the newest lines; it logs "health check passed" when that check passes again, and turns Ready by itself once all of them pass. A Fibre older than v10.5.0 reports no readiness (Not reported): upgrade Fibre to get it. A stopped standby does not answer, as expected. `doctor` check 24 on the monitor shows each Fibre host's readiness now. The probe reaches IPv4 addresses only: for a Fibre host whose `celestia_mon_private_ip` is an IPv6 address, readiness is not checked.

## Upgrade signalling

Optional, on the monitor. Celestia moves to a new app version once 5/6 of the voting power has signalled it in x/signal. celestia-mon-signal reads x/signal every minute through a node's CometBFT RPC: the voting power that signalled the next version, the upgrade once one is scheduled, and the version your validator signalled. [UpgradeSignalMissing](alerts.md#upgradesignalmissing) pages while validators signal a version yours has not; [UpgradeScheduled](alerts.md#upgradescheduled) sends a message when the upgrade is scheduled (again after 4 days while it waits, as on mainnet), and [UpgradeSoon](alerts.md#upgradesoon) one when it is less than a day away. It sends no transaction and needs no key. Set `celestia_mon_signal` on the monitor host, in its entry in `hosts.yml` or its `host_vars`:

| Key | Meaning |
|---|---|
| `chain_id` | The network's chain ID: `celestia` on mainnet, `mocha-5` on Mocha. An RPC that serves another network is skipped, so a mainnet install never reads a Mocha node, or the reverse. |
| `rpc` | CometBFT RPC URLs (port 26657 on a node), tried in order: the first one that answers for `chain_id` is read. Each is `https`, or plain `http` to the monitor itself or to a node named by its private address, never by a host name: the service reaches no other address of the private network ([security.md](security.md#what-it-exposes)). x/signal's state is the same on every synced node, so put a node of your own first and a public RPC after it, for the time your node is down. An RPC whose node is catching up, or has made no block for 2 minutes, is skipped too. The block time is measured over the last 1000 blocks the node keeps. A URL may hold a provider's key in its path, not in a login or a query string. |
| `validator` | The validator's operator address, `celestiavaloper1...`. The install checks it, its checksum included. |

In the [mainnet layout](#mainnet-layout), the backup node runs on the monitor host `fibre-1` and answers on its loopback; a public RPC from Celestia's [mainnet page](https://docs.celestia.org/operate/networks/mainnet-beta/) takes over while it is down:

```yaml
    fibre-1:
      celestia_mon_signal:
        chain_id: celestia
        rpc:
          - http://127.0.0.1:26657
          - https://rpc.celestia.pops.one
        validator: celestiavaloper1...
```

In the [Mocha layout](#mocha-layout) the monitor runs no node, and a node's RPC listens on its loopback by default (`laddr` in the `[rpc]` section of `config.toml`). Point `rpc` at a node RPC the monitor can reach, or at the public RPCs from Celestia's [Mocha page](https://docs.celestia.org/operate/networks/mocha-testnet/):

```yaml
    fibre-1:
      celestia_mon_signal:
        chain_id: mocha-5
        rpc:
          - https://rpc-mocha.pops.one
          - https://public-endpoint.celestia-mocha.quiknode.pro
        validator: celestiavaloper1...
```

A public RPC is someone else's node: the reads are public and need no key, but the upgrade alerts then depend on it, so list a node of your own first wherever the monitor can reach one. A node on another host needs its RPC on its private address (`laddr` in `[rpc]`) and a firewall or tailnet rule that lets the monitor reach port 26657 there; both are your changes ([security.md](security.md#tailnet-policy) has the grant). The RPC listens on one address only, so nothing answers on that node's 127.0.0.1 afterwards: `doctor` there follows `laddr`, and `celestia-appd` commands there need `--node http://<private address>:26657` (or `node` in its `client.toml`). When a URL holds a provider's key, set `celestia_mon_signal` in the monitor's vault file instead; the metrics and the log name each RPC by its scheme, host and port only. Run `install.sh` on the monitor after the change; `sudo celestia-mon-doctor` there (check 23) shows the first reading, and warns while it comes from another RPC than the first. To turn it off, remove `celestia_mon_signal` and run `install.sh` on the monitor again: the service is removed.

## Mocha layout

The layout celestia-validator-monitoring is developed and tested on, on the Mocha testnet (celestia-app and Fibre v10.4.0-mocha, tmkms v0.15 with a soft-signing key): three hosts, one Fibre host and no backup node.

| Host | Groups | Runs |
|---|---|---|
| validator | `validator` | celestia-appd, signing through tmkms over WireGuard. |
| KMS | `kms` | tmkms only; nothing is installed there. |
| Fibre | `fibre`, `monitor` | Fibre; Prometheus, Alertmanager and Grafana. |

Start from `example-split`: remove `mon-1`, put `fibre-1` in the `monitor` group with the vault file in `host_vars/fibre-1/`, and remove `celestia_mon_kms_scrape` from `kms-1`.

Three steps are the operator's on such a setup after the install (celestia-validator-monitoring changes none of them):

1. Point Fibre at the collector on the Fibre host, `--otel-endpoint http://127.0.0.1:9915`, and restart Fibre at a moment you choose (step 6). Until then `doctor` check 7 fails.
2. If Fibre sent its metrics to a collector of your own before, for example one on the validator host that Fibre reached over WireGuard on port 4319, remove that collector and its firewall rule once step 1 is done.
3. Bind celestia-appd's metrics to the validator's private address: a `prometheus_listen_addr` on `0.0.0.0:26660` listens on every interface (step 2). `doctor` check 1 warns until it is changed.
