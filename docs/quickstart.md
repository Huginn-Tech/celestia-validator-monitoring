# Quickstart

This guide takes you from an empty inventory to a running dashboard, working alerts and a clean `doctor` report. You run one command on each host, on the host itself: `install.sh` checks the host, shows what it would change, and changes it only after you confirm. celestia-validator-monitoring never changes, restarts or upgrades celestia-appd, Fibre or tmkms: where a setting must change, this guide and `doctor` tell you the line, and you apply it.

## 1. Requirements

- **Hosts:** Ubuntu 24.04 with systemd, on x86_64 or arm64, in four roles:
  - `validator` runs celestia-appd;
  - `fibre` runs the Fibre server (it may be the validator host);
  - `kms` runs tmkms (optional; nothing is installed there unless you opt in);
  - `monitor` runs Prometheus, Alertmanager and Grafana.

  For mainnet, use a monitor host that is neither the validator nor the Fibre host ([security.md](security.md#where-to-run-the-monitor)). On a test setup the monitor may share a host; the dead-man service (step 8) tells you when that host dies.
- **A private network between the hosts:** RFC 1918, Tailscale (100.64.0.0/10), WireGuard or IPv6 ULA addresses. Everything celestia-validator-monitoring installs listens on these addresses or on 127.0.0.1, and the install fails if one of its services listens on a public address.
- **Root on each host** (`sudo`), with the `python3` and `curl` (or `wget`) that Ubuntu ships. Nothing is installed with apt or pip: `install.sh` downloads the [uv](https://github.com/astral-sh/uv) binary pinned in `versions.yml`, checks its sha256, and uses it to put the pinned ansible-core into a temporary directory under `/var/tmp/celestia-mon-install`, which it deletes when it ends, also on failure. `/var/tmp` must not be mounted `noexec` and needs 1 GiB free during the run.
- **Outbound HTTPS** from every host to `github.com` and `release-assets.githubusercontent.com` (uv and the release downloads), `pypi.org` and `files.pythonhosted.org` (ansible-core); from the monitor also to `dl.grafana.com` (Grafana), your alert receivers and the dead-man service.
- **Free disk** for the releases a host gets, while they are downloaded and unpacked: about 100 MiB on a validator or KMS host, 1.2 GiB on a Fibre host (the OpenTelemetry collector), 2.2 GiB on the monitor, plus room for Prometheus's data on the monitor (`celestia_mon_prometheus_retention_size`, 8 GB by default). The install checks it.
- **One validator per install.** To watch a testnet and a mainnet validator, run two installs with two inventories.

Default ports:

| Service | Host | Port |
|---|---|---|
| node_exporter | validator, fibre, kms if opted in | 9910 on the private address |
| OpenTelemetry collector | fibre | 9911 on the private address (metrics), 9915 on 127.0.0.1 (from Fibre) |
| Prometheus | monitor | 9912 on 127.0.0.1 |
| Alertmanager | monitor | 9913 on 127.0.0.1 |
| Grafana | monitor | 9914 on the private address |

Prometheus also scrapes celestia-appd's own metrics port (26660 by default) on the validator. Prometheus and Alertmanager have no login, so they listen on loopback only; Grafana reaches Prometheus there, and you reach both through an SSH tunnel (step 7).

celestia-validator-monitoring runs next to a node_exporter, Prometheus, Alertmanager, Grafana or OpenTelemetry collector the host already has, and leaves it alone. Each of its services has its own systemd unit and system user (`celestia-mon-node-exporter`, `celestia-mon-otelcol`, `celestia-mon-prometheus`, `celestia-mon-alertmanager`, `celestia-mon-grafana`), runs from `/opt/celestia-mon/<component>-<version>/`, keeps its settings under `/etc/celestia-mon/` and its data under `/var/lib/celestia-mon/`, and logs to the journal (for example `journalctl -u celestia-mon-grafana`). Nothing goes to `/usr/local/bin` except `celestia-mon-doctor`. The ports above are a block that common tools do not use; to move them, set `celestia_mon_ports` (step 3).

Every unit runs with `Nice=10`, `CPUWeight=20` and `IOWeight=20` (systemd's defaults are 0, 100 and 100) and a memory cap: above `MemoryHigh` the kernel slows the service down, at `MemoryMax` it stops it and systemd restarts it.

| Service | MemoryHigh | MemoryMax |
|---|---|---|
| node_exporter | 64M | 128M |
| OpenTelemetry collector | 256M | 512M |
| Prometheus | 1G | 2G |
| Alertmanager | 64M | 128M |
| Grafana | 256M | 512M |

These lower the services' priority when the host is busy; they do not reserve anything for celestia-appd, Fibre or tmkms. `IOWeight` takes effect only where the disk's I/O scheduler honours weights (BFQ, or the kernel's io.cost controller); with the `none` scheduler common on NVMe disks it changes nothing.

## 2. Prepare the validator

celestia-validator-monitoring reads celestia-appd's metrics, which are off by default. Change the setting yourself and restart celestia-appd at a moment you choose. In the node's `config/config.toml`:

```toml
[instrumentation]
prometheus = true
prometheus_listen_addr = "<validator private address>:26660"
```

The default `prometheus = false` serves no metrics. The default `prometheus_listen_addr = ":26660"` listens on every address, so set the private address. The `namespace` can stay `cometbft` (the default) or be `celestia`; celestia-validator-monitoring handles both.

**A remote signer** needs nothing extra: with `priv_validator_laddr` set and `prometheus = true`, the node exports the signer metrics.

**A firewall on the hosts.** When ufw is already active on a host, the install adds rules that let the monitor's private address reach the ports Prometheus scrapes there: 26660 on the validator, and the node_exporter and collector ports (9910, 9911). Each rule carries the comment `celestia-mon` and is listed in the plan before you confirm. With any other firewall, allow those ports yourself. On a Tailscale network, ufw does not see tailnet traffic; the tailnet policy is the filter ([security.md](security.md#tailnet-policy)).

`doctor` checks these settings and prints the exact line to change. You can run it before the install: copy `tools/doctor.sh` to the host and run `sudo bash doctor.sh`. Without a settings file it guesses the host's roles from its systemd units; `bash doctor.sh --help` lists the flags.

## 3. Write the inventory

One inventory describes every host, and every host gets a copy of it. It holds no secrets: those go into one vault file that only the monitor host gets. On your workstation, in a clone of this repository, start from the example that matches your setup:

```sh
cp -r ansible/inventory/example-single ~/celestia-mon-inventory   # one host: validator and monitor, local key, Fibre off
cp -r ansible/inventory/example-split ~/celestia-mon-inventory    # separate validator, KMS, Fibre and monitor hosts
```

In `hosts.yml`, put each host in its groups (`validator`, `fibre`, `kms`, `monitor`) and set its variables. The host names are yours to choose; you pass the name to `install.sh --host` on that host.

| Variable | Hosts | Meaning |
|---|---|---|
| `celestia_mon_private_ip` | every host celestia-validator-monitoring installs on | Private address where its services listen and Prometheus scrapes. Required, and it must be an address of that host: the install checks it. |
| `celestia_mon_data_mounts` | validator, fibre, kms | Mount points to watch for free space and read-only state, for example the node's data disk. |
| `celestia_mon_fibre_mount` | fibre | Mount point of the Fibre store. |
| `celestia_mon_fibre_public_endpoint` | fibre | The `host:port` you registered with `celestia-appd tx valaddr set-host`. `doctor` compares it with the on-chain registration. |
| `celestia_mon_kms_scrape` | kms | `true` installs node_exporter on the KMS host, so tmkms and its disks are watched. Default `false`: nothing is installed there. |
| `celestia_mon_node_home` | validator | celestia-appd home, for `doctor`. Default: found from the unit. |
| `celestia_mon_link_mbps` | validator, fibre, kms | Optional. The link speed in Mbit/s for the Link load panel. Set it when the host reports no NIC speed (common on VMs), has more than one physical NIC, or its plan is below the port speed. Default: the NIC's port speed. |
| `celestia_mon_firewall_sources` | validator, fibre, kms | Optional. The addresses ufw lets reach this host's scraped ports. Default: the monitor's `celestia_mon_private_ip`. |

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
| `celestia_mon_ports` | 9910 to 9915 | celestia-validator-monitoring's ports (table in step 1). |
| `celestia_mon_thresholds` | see [alerts.md](alerts.md) | Alert thresholds. |
| `celestia_mon_prometheus_retention` | `30d` | How long Prometheus keeps data. |
| `celestia_mon_prometheus_retention_size` | `8GB` | How much data Prometheus keeps; the oldest goes first when either limit is reached. |
| `celestia_mon_nice`, `celestia_mon_cpu_weight`, `celestia_mon_io_weight` | 10, 20, 20 | CPU and disk priority of every unit. |
| `celestia_mon_memory` | table in step 1 | `high` and `max` per service (`node_exporter`, `otelcol`, `prometheus`, `alertmanager`, `grafana`). |

Overriding a map (`celestia_mon_units`, `celestia_mon_ports`, `celestia_mon_thresholds`, `celestia_mon_memory`) replaces the whole map, so list every key.

**Secrets.** The monitor's settings with secrets live in `host_vars/<monitor>/vault.yml` (`host_vars/node-1/vault.yml` or `host_vars/mon-1/vault.yml` in the examples; rename the directory with the host):

| Variable | Meaning |
|---|---|
| `celestia_mon_receivers` | Alert receivers for `page` and `message` (below). One list may be empty: its alerts then go to the other list's receivers. |
| `celestia_mon_deadman_webhook_url` | Heartbeat URL of your dead-man service (step 8). |
| `celestia_mon_grafana_admin_password` | Grafana's admin password. |

Each receiver is one of:

| `type` | Keys |
|---|---|
| `telegram` | `bot_token`, `chat_id` (numeric, for example `-1000000000000`) |
| `pagerduty` | `routing_key` |
| `slack` | `api_url`, optional `channel` |
| `webhook` | `url` |

`page` alerts need someone now; `message` alerts can wait for working hours. They can go to the same receiver.

Replace the placeholders, then encrypt the file on your workstation with [ansible-vault](https://docs.ansible.com/ansible/latest/vault_guide/vault_encrypting_content.html), from any Ansible install, or without one through uv:

```sh
ansible-vault encrypt ~/celestia-mon-inventory/host_vars/mon-1/vault.yml
uvx --from ansible-core ansible-vault encrypt ~/celestia-mon-inventory/host_vars/mon-1/vault.yml   # the same, without installing Ansible
```

`ansible-vault edit` changes it later. Only the monitor host gets this file (step 4): the other hosts need none of its values, and an encrypted file they cannot open would stop their install.

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

Go in this order: the validator host (and the KMS host, if `celestia_mon_kms_scrape: true`), then the Fibre host and the monitor host, then point Fibre at its collector (step 6). On a KMS host without `celestia_mon_kms_scrape`, `install.sh` reports that it installs nothing there.

`install.sh`:

1. **checks the host**, changing nothing: Ubuntu 24.04 with systemd, run as root, `celestia_mon_private_ip` is an address of this host, celestia-validator-monitoring's ports are free (or held by its own services), and there is enough free disk. A failed check stops it before any change and names the fix. It also reports the memory available now against the services' caps.
2. **plans:** a dry run of the whole install for this host only, with a short summary: the services and their users, the addresses and ports they listen on, the directories, the disk and memory they need, and each ufw rule it would add (`ufw rule:  allow from <monitor address> to port <port>/tcp (comment celestia-mon)`). It ends with "The dry run finds nothing to change." or "The dry run reports N change(s)".
3. **asks** `Apply this to <host>? [y/N]`. Anything but `y` or `yes` stops it with nothing changed.
4. **installs**, and reports the number of changes, or "No changes: <host> already matches the inventory."
5. **runs `celestia-mon-doctor`** and prints its result (step 9). Before Fibre points at its collector, and before every host is installed, some checks fail; `install.sh` still ends successfully.
6. **prints the command that removes it again** (step 13).

| Flag | Meaning |
|---|---|
| `--inventory FILE` | The shared inventory: its `hosts.yml`, or the directory. |
| `--host NAME` | This host's name in the inventory (not a group). |
| `--ask-vault-pass` | Ask for the vault password, on the host whose `host_vars` hold the encrypted file. |
| `--check` | Dry run only: the checks and the plan, nothing changed. |
| `--yes` | Apply the plan without asking. |
| `--uninstall` | Remove celestia-validator-monitoring from this host (step 13). |

Run `install.sh` again on each host after every inventory change; a host with nothing to change says so. If the install stops with an error, fix the cause and run it again: it continues where it stopped.

## 6. Point Fibre at its collector

Only if you run Fibre. Fibre has no scrape endpoint; it pushes metrics over OTLP to the collector installed on the Fibre host. Add `--otel-endpoint http://127.0.0.1:9915` to the `ExecStart` line of Fibre's systemd unit, or set `FIBRE_OTEL_ENDPOINT=http://127.0.0.1:9915` in its environment. Use the base URL, without `/v1/metrics`. Then run `systemctl daemon-reload` and restart Fibre at a moment you choose. `doctor` on the Fibre host (check 7) confirms the setting.

## 7. Open Grafana

Grafana listens on the monitor's private address: `http://<monitor private address>:9914`. Log in as `admin` with `celestia_mon_grafana_admin_password`. Grafana reads that password only on its first start; change it later in Grafana (Profile, Change password).

With ufw active on the monitor, allow your workstation's private address to port 9914 yourself (`ufw allow from <addr> to any port 9914 proto tcp`), or use an SSH tunnel. The tunnel also reaches Prometheus and Alertmanager, which listen on the monitor's loopback only:

```sh
ssh -L 9914:<monitor private address>:9914 -L 9912:127.0.0.1:9912 -L 9913:127.0.0.1:9913 <user>@<monitor>
```

Then open `http://localhost:9914` (Grafana), `http://localhost:9912/alerts` (Prometheus) or `http://localhost:9913` (Alertmanager).

The dashboard is **celestia-mon: Celestia validator** in the **Celestia validator** folder. [panels.md](panels.md) explains each panel. It is generated from your inventory, so groups that do not apply are left out and the threshold lines match the alerts. Do not edit it in Grafana; change the inventory and run `install.sh` on the monitor again.

## 8. Set up the dead-man service

The Watchdog alert is always firing. Alertmanager sends it every 1 to 2 minutes to a dead-man service, which alerts you when the heartbeats stop: when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network. The heartbeat also stops for an hour after a page or message notification failed to go out, so a broken alert channel (an expired bot token, a removed chat) reaches you through the dead-man service.

1. Choose any dead-man (heartbeat) service that gives you a URL to ping and alerts you when pings stop. Alertmanager pings it with an HTTP POST.
2. Set its period to **5 minutes or more**, so a ping that arrives two minutes apart never trips it.
3. Have it notify you **by email and on a channel other than your alert chat**: when the alert chat is what broke, the dead-man service is the only one left to tell you.
4. Put the URL in `celestia_mon_deadman_webhook_url` in the vault file and run `install.sh` on the monitor again.
5. Test it: on the monitor, run `sudo systemctl stop celestia-mon-alertmanager`, wait until the dead-man service alerts you, then `sudo systemctl start celestia-mon-alertmanager`.

Without a URL, the heartbeat is dropped and nothing tells you that monitoring itself is down.

## 9. Run doctor

On every host celestia-validator-monitoring is installed on, the monitor included:

```sh
sudo celestia-mon-doctor
```

It changes nothing. It reads `/etc/celestia-mon/doctor.conf`, which the install writes; any setting can also be given as a flag (`celestia-mon-doctor --help`). Each check prints one line: `OK`, `FAIL` with the exact fix, `WARN`, or `SKIP` when the check does not apply on this host. The exit code is 1 when a check failed.

| # | Host | Check |
|---|---|---|
| 1 | validator | celestia-appd metrics are enabled and served; warns if the port listens on a public address. |
| 2 | validator | The signer setting matches `celestia_mon_signer`; with a remote signer, the median signing latency is at most 10 ms (skipped until 50 signatures after a start). |
| 3 | validator | The node is at the chain head: height advancing, `catching_up=false`. |
| 4 | validator | The validator's consensus address, from the node's `/status`. |
| 5 | validator | With Fibre on: the Fibre host registered in `x/valaddr`, compared with `celestia_mon_fibre_public_endpoint`. |
| 6 | fibre | The Fibre unit is active and its port (7980 by default) listens. |
| 7 | fibre | Fibre's OTLP endpoint points at the local collector. |
| 8 | all | The `celestia-mon-*` services listen on private addresses only, Prometheus and Alertmanager on loopback only; other software on the host is not checked. Reports whether a firewall is active. |
| 9 | all | Time sync is active and the clock offset is small. |
| 10 | all | Free space on the data mounts; the Fibre mount is not read-only. |
| 11 | monitor | Prometheus and Alertmanager answer on 127.0.0.1, and every scrape target is up; a down target is listed with its error. |
| 12 | monitor | Every metric the alert rules read has series in Prometheus; the missing ones are listed with the rules that read them. |

Right after an install, doctor on the monitor can take up to a minute while Prometheus starts and scrapes each target once. On the first deploy, check 12 is how the Fibre metric names get confirmed: a counter of failures can stay absent until the first failure, but any other name it lists needs a look. Run doctor again after every celestia-app or Fibre upgrade and every config change.

## 10. Check that alerts reach you

On the monitor, send a test alert of each severity and check that it arrives where you expect. Alertmanager's `amtool` is in `/opt/celestia-mon/alertmanager-<version>/`, with the Alertmanager version from `versions.yml`:

```sh
amtool=/opt/celestia-mon/alertmanager-<version>/amtool
$amtool alert add alertname=CelestiaMonTest severity=page host=test --annotation=summary='celestia-validator-monitoring test' \
  --alertmanager.url=http://127.0.0.1:9913
$amtool alert add alertname=CelestiaMonTest severity=message host=test --annotation=summary='celestia-validator-monitoring test' \
  --alertmanager.url=http://127.0.0.1:9913
```

Each resolves by itself after 5 minutes. [alerts.md](alerts.md) describes every alert and what to check first.

## 11. Subscribe to network notices

Upgrades are the most common reason a node stops at a height or crash loops. Celestia announces upgrades and incidents per network on [status.celestia.org](https://status.celestia.org/). Subscribe there for the networks you run, or route its feed ([RSS](https://status.celestia.org/feed.rss), [Atom](https://status.celestia.org/feed.atom)) into the same channel as your alerts. See also Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page.

## 12. Monthly drill

Once a month, check that every path still works:

1. **Dead-man:** stop Alertmanager on the monitor until the dead-man service alerts you by email and on its other channel, then start it again (step 8).
2. **Alert channels:** send a test alert of each severity (step 10) and check that both arrive.
3. **Hosts:** `sudo celestia-mon-doctor` on every host (step 9).
4. **Drift:** `sudo ./install.sh --check --inventory ~/celestia-mon-inventory/hosts.yml --host <name>` on every host (add `--ask-vault-pass` on the monitor). "The dry run finds nothing to change." means the host still matches the inventory.

## 13. Upgrade or remove

**Upgrade.** Copy the new release to each host as in step 4 and run `install.sh` there. The dry run names each new version it would install ("Would install ..."); the service restarts on the new version, and the old version's directory is removed once the new one runs. Data and settings stay.

**Remove.** On each host:

```sh
sudo ./install.sh --uninstall --host <name>
```

It needs neither the inventory nor the vault password, and it shows its plan and asks first as well (`--check` shows the plan only, `--yes` skips the question). It stops, disables and removes every `celestia-mon-*` unit, removes the `celestia-mon-*` users and groups, `/opt/celestia-mon`, `/etc/celestia-mon`, `/var/lib/celestia-mon` (Prometheus's data and Grafana's database), `/var/cache/celestia-mon` and `/usr/local/bin/celestia-mon-doctor`, and deletes the ufw rules with the comment `celestia-mon`. Nothing else is touched: celestia-appd, Fibre, tmkms, other services and other firewall rules stay as they are. Two settings you made yourself stay too: celestia-appd's `[instrumentation]` section, and Fibre's `--otel-endpoint`; with the collector gone, Fibre logs export errors until you remove that flag and restart Fibre.
