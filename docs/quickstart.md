# Quickstart

This guide takes you from an empty inventory to a running dashboard, working alerts and a clean `doctor` report. celestia-validator-monitoring never changes, restarts or upgrades celestia-appd, Fibre or tmkms: where a setting must change, this guide and `doctor` tell you the line, and you apply it.

## 1. Requirements

- **Hosts:** Ubuntu 24.04 on x86_64 or arm64, in four roles:
  - `validator` runs celestia-appd;
  - `fibre` runs the Fibre server (it may be the validator host);
  - `kms` runs tmkms (optional);
  - `monitor` runs Prometheus, Alertmanager and Grafana.

  Use a separate host for `monitor` when you can. On a single-host setup it may be the same box; the dead-man service (step 6) tells you when that box dies.
- **A private network between the hosts:** RFC 1918, Tailscale (100.64.0.0/10), WireGuard or IPv6 ULA addresses. Everything celestia-validator-monitoring installs listens on these addresses only, and the playbook fails if a celestia-validator-monitoring service listens on a public one.
- **A control node:** any Linux or macOS machine with ansible-core 2.16 or later and Python 3 ([install guide](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html)). It needs SSH with sudo to the hosts in the inventory.
- **Outbound HTTPS** from the hosts to GitHub (release downloads), from the monitor also to dl.grafana.com (Grafana), your alert receivers and the dead-man service.
- **One validator per install.** To watch a testnet and a mainnet validator, run two installs with two inventories.

Default ports (all on the private address unless noted):

| Service | Host | Port |
|---|---|---|
| node_exporter | validator, fibre, kms if opted in | 9910 |
| OpenTelemetry collector | fibre | 9911 (metrics), 9915 on 127.0.0.1 (from Fibre) |
| Prometheus | monitor | 9912 |
| Alertmanager | monitor | 9913 |
| Grafana | monitor | 9914 |

Prometheus also scrapes celestia-appd's own metrics port (26660 by default) on the validator.

celestia-validator-monitoring runs next to a node_exporter, Prometheus, Alertmanager, Grafana or OpenTelemetry collector the host already has, and leaves it alone. Each celestia-validator-monitoring service has its own systemd unit and system user (`celestia-mon-node-exporter`, `celestia-mon-otelcol`, `celestia-mon-prometheus`, `celestia-mon-alertmanager`, `celestia-mon-grafana`), runs from `/opt/celestia-mon/<component>-<version>/`, keeps its settings under `/etc/celestia-mon/` and its data under `/var/lib/celestia-mon/`, and logs to the journal (for example `journalctl -u celestia-mon-grafana`). celestia-validator-monitoring puts nothing in `/usr/local/bin` except `celestia-mon-doctor`. The ports above are a block that common tools do not use; to move them, set `celestia_mon_ports` (step 3).

## 2. Prepare the validator

celestia-validator-monitoring needs two settings that it never changes itself: celestia-appd's metrics and, if you run Fibre, Fibre's telemetry endpoint. Change them yourself and restart each service at a moment you choose.

**celestia-appd metrics.** In the node's `config/config.toml`:

```toml
[instrumentation]
prometheus = true
prometheus_listen_addr = "<validator private address>:26660"
```

The default `prometheus = false` serves no metrics. The default `prometheus_listen_addr = ":26660"` listens on every address, so set the private address. The `namespace` can stay `cometbft` (the default) or be `celestia`; celestia-validator-monitoring handles both.

**Fibre telemetry** (only if you run Fibre). Fibre has no scrape endpoint; it pushes metrics over OTLP. Point it at the collector celestia-validator-monitoring installs on the Fibre host: add `--otel-endpoint http://127.0.0.1:9915` to the `ExecStart` line of Fibre's systemd unit, or set `FIBRE_OTEL_ENDPOINT=http://127.0.0.1:9915` in its environment. Use the base URL, without `/v1/metrics`. Then run `systemctl daemon-reload` and restart Fibre. Until the playbook has installed the collector, Fibre logs export errors; they stop once the collector runs.

**A firewall on the hosts.** When ufw is already active, the playbook allows the monitor's private address to the ports Prometheus scrapes on the other hosts: port 26660 on the validator and the node_exporter and OpenTelemetry collector ports (9910, 9911). With any other firewall, allow those yourself. celestia-validator-monitoring never enables, resets or reorders a firewall.

**A remote signer** needs nothing extra: with `priv_validator_laddr` set and `prometheus = true`, the node exports the signer metrics.

`doctor` checks both settings and prints the exact line to change. You can run it before the first install: copy `tools/doctor.sh` to the host and run `sudo bash doctor.sh`. Without a settings file it guesses the host's roles from its systemd units; `bash doctor.sh --help` lists the flags. After the install it is at `/usr/local/bin/celestia-mon-doctor` (step 8).

## 3. Write the inventory

Copy the example that matches your setup:

```sh
cp -r ansible/inventory/example-single ansible/inventory/mine   # one host: validator and monitor, local key, Fibre off
cp -r ansible/inventory/example-split ansible/inventory/mine    # separate validator, KMS, Fibre and monitor hosts
```

In `hosts.yml`, put each host in its groups (`validator`, `fibre`, `kms`, `monitor`) and set its variables:

| Variable | Hosts | Meaning |
|---|---|---|
| `ansible_host` | all | The address Ansible connects to. |
| `celestia_mon_private_ip` | every host celestia-validator-monitoring installs on | Private address where celestia-validator-monitoring's services listen and Prometheus scrapes. Required. |
| `celestia_mon_data_mounts` | validator, fibre, kms | Mount points to watch for free space and read-only state, for example the node's data disk. |
| `celestia_mon_fibre_mount` | fibre | Mount point of the Fibre store. |
| `celestia_mon_fibre_public_endpoint` | fibre | The `host:port` you registered with `celestia-appd tx valaddr set-host`. `doctor` compares it with the on-chain registration. |
| `celestia_mon_kms_scrape` | kms | `true` installs node_exporter on the KMS host, so tmkms and its disks are watched. Default `false`: celestia-validator-monitoring installs nothing there. |
| `celestia_mon_node_home` | validator | celestia-appd home, for `doctor`. Default: found from the unit. |
| `celestia_mon_link_mbps` | validator, fibre, kms | Optional. The link speed in Mbit/s for the Link load panel. Set it when the host reports no NIC speed (common on VMs), has more than one physical NIC, or its plan is below the port speed. Default: the NIC's port speed. |

In `group_vars/all.yml`, set the shared settings:

| Variable | Meaning |
|---|---|
| `celestia_mon_signer` | `local` (key file on the node) or `remote` (tmkms). With `local`, the signer panels and RemoteSignerProblem are left out. |
| `celestia_mon_fibre_enabled` | `true` installs and renders everything Fibre-related. Keep `false` until app v10 is active on your network: before that, Fibre does not run and `x/valaddr` does not exist. |
| `celestia_mon_receivers` | Alert receivers for `page` and `message` (below). One list may be empty: its alerts then go to the other list's receivers. |
| `celestia_mon_deadman_webhook_url` | Heartbeat URL of your dead-man service (step 6). |
| `celestia_mon_grafana_admin_password` | Grafana's admin password. |

Optional settings, with their defaults, are in `ansible/roles/celestia_mon/defaults/main.yml`: systemd unit names (`celestia_mon_units`: `celestia-appd`, `fibre`, `tmkms`), the node's metrics and RPC ports (`celestia_mon_node_metrics_port: 26660`, `celestia_mon_node_rpc_port: 26657`), celestia-validator-monitoring's ports (`celestia_mon_ports`), alert thresholds (`celestia_mon_thresholds`, see [alerts.md](alerts.md)) and `celestia_mon_prometheus_retention` (30d). Overriding a map (`celestia_mon_units`, `celestia_mon_ports`, `celestia_mon_thresholds`) replaces the whole map, so list every key.

Each receiver is one of:

| `type` | Keys |
|---|---|
| `telegram` | `bot_token`, `chat_id` (numeric, for example `-1000000000000`) |
| `pagerduty` | `routing_key` |
| `slack` | `api_url`, optional `channel` |
| `webhook` | `url` |

`page` alerts need someone now; `message` alerts can wait for working hours. They can go to the same receiver.

**Keep secrets in ansible-vault.** Bot tokens, routing keys, webhook URLs, the dead-man URL and the Grafana password should not sit in plain text. Encrypt each value with [`ansible-vault encrypt_string`](https://docs.ansible.com/ansible/latest/vault_guide/vault_encrypting_content.html):

```sh
ansible-vault encrypt_string --ask-vault-pass --stdin-name bot_token
```

Paste the secret, end with Ctrl-D, and put the output in place of the plain value:

```yaml
celestia_mon_receivers:
  page:
    - type: telegram
      bot_token: !vault |
        $ANSIBLE_VAULT;1.1;AES256
        ...
      chat_id: -1000000000000
```

Use the same vault password for every value. The playbook never prints these values.

## 4. Run the playbook

Run it from the `ansible/` directory, so `ansible.cfg` is used:

```sh
cd ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook -i inventory/mine/hosts.yml site.yml --ask-vault-pass --check --diff   # preview, changes nothing
ansible-playbook -i inventory/mine/hosts.yml site.yml --ask-vault-pass
```

The playbook:

1. checks the inventory;
2. installs node_exporter on the validator, Fibre and (if opted in) KMS hosts, the OpenTelemetry collector on Fibre hosts, and `celestia-mon-doctor` on all of them;
3. installs Prometheus, Alertmanager and Grafana on the monitor, with the alert rules and the dashboard built from your inventory;
4. fails if any service celestia-validator-monitoring installed listens on a public address.

Binary versions are pinned in `versions.yml` and checksums are verified on download. Run the playbook again after every inventory change; a run with nothing to change reports no changes. If another process already listens on one of celestia-validator-monitoring's ports, the playbook stops before it starts that service, names the process and its PID, and says which `celestia_mon_ports` key to set.

## 5. Open Grafana

Grafana listens on the monitor's private address: `http://<monitor private address>:9914`. Log in as `admin` with `celestia_mon_grafana_admin_password`. Grafana reads that password only on its first start; change it later in Grafana (Profile, Change password).

With ufw active, allow your workstation's private address to port 9914 yourself (`ufw allow from <addr> to any port 9914 proto tcp`), or use the SSH tunnel below.

If you are not on the private network, use an SSH tunnel and open `http://localhost:9914`:

```sh
ssh -L 9914:<monitor private address>:9914 <user>@<monitor>
```

The dashboard is **celestia-validator-monitoring: Celestia validator** in the **celestia-validator-monitoring** folder. [panels.md](panels.md) explains each panel. The playbook generates the dashboard from your inventory, so groups that do not apply are left out and the threshold lines match the alerts. Do not edit it in Grafana; change the inventory and run the playbook again.

## 6. Set up the dead-man service

celestia-validator-monitoring's Watchdog alert is always firing. Alertmanager sends it every 1 to 2 minutes to a dead-man service, which alerts you when the heartbeats stop: when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network.

1. Choose any dead-man (heartbeat) service that gives you a URL to ping and alerts you when pings stop. Alertmanager pings it with an HTTP POST.
2. Set its period to **5 minutes or more**, so a ping that arrives two minutes apart never trips it.
3. Put the URL in `celestia_mon_deadman_webhook_url` (encrypted with ansible-vault) and run the playbook again.
4. Test it: on the monitor, run `sudo systemctl stop celestia-mon-alertmanager`, wait until the dead-man service alerts you, then `sudo systemctl start celestia-mon-alertmanager`.

Without a URL, the heartbeat is dropped and nothing tells you that monitoring itself is down.

## 7. Subscribe to network notices

Upgrades are the most common reason a node stops at a height or crash loops. Celestia announces upgrades and incidents per network on [status.celestia.org](https://status.celestia.org/). Subscribe there for the networks you run, or route its feed ([RSS](https://status.celestia.org/feed.rss), [Atom](https://status.celestia.org/feed.atom)) into the same channel as your alerts. See also Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page.

## 8. Run doctor

On each validator, Fibre and KMS host celestia-validator-monitoring installed on:

```sh
sudo celestia-mon-doctor
```

It changes nothing. It reads `/etc/celestia-mon/doctor.conf`, which the playbook writes; any setting can also be given as a flag (`celestia-mon-doctor --help`). Each check prints one line: `OK`, `FAIL` with the exact fix, `WARN`, or `SKIP` when the check does not apply on this host. The exit code is 1 when a check failed.

| # | Host | Check |
|---|---|---|
| 1 | validator | celestia-appd metrics are enabled and served; warns if the port listens on a public address. |
| 2 | validator | The signer setting matches `celestia_mon_signer`; with a remote signer, the median signing latency is at most 10 ms (skipped until 50 signatures after a start). |
| 3 | validator | The node is at the chain head: height advancing, `catching_up=false`. |
| 4 | validator | The validator's consensus address, from the node's `/status`. |
| 5 | validator | With Fibre on: the Fibre host registered in `x/valaddr`, compared with `celestia_mon_fibre_public_endpoint`. |
| 6 | fibre | The Fibre unit is active and its port (7980 by default) listens. |
| 7 | fibre | Fibre's OTLP endpoint points at the local collector. |
| 8 | all | celestia-validator-monitoring's services (the `celestia-mon-*` units) listen on private addresses only; other software on the host is not checked. Reports whether a firewall is active. |
| 9 | all | Time sync is active and the clock offset is small. |
| 10 | all | Free space on the data mounts; the Fibre mount is not read-only. |

Run it again after every celestia-app or Fibre upgrade and every config change.

## 9. Check that alerts reach you

On the monitor, send a test alert of each severity and check that it arrives where you expect. celestia-validator-monitoring's `amtool` is in `/opt/celestia-mon/alertmanager-<version>/`, with the Alertmanager version from `versions.yml`:

```sh
amtool=/opt/celestia-mon/alertmanager-<version>/amtool
$amtool alert add alertname=CelestiaMonTest severity=page host=test --annotation=summary='celestia-validator-monitoring test' \
  --alertmanager.url=http://<monitor private address>:9913
$amtool alert add alertname=CelestiaMonTest severity=message host=test --annotation=summary='celestia-validator-monitoring test' \
  --alertmanager.url=http://<monitor private address>:9913
```

Each resolves by itself after 5 minutes. [alerts.md](alerts.md) describes every alert and what to check first.
