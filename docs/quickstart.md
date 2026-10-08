# Quickstart

This guide takes you from an empty inventory to a running dashboard, working alerts and a clean `doctor` report. Taut never changes, restarts or upgrades celestia-appd, Fibre or tmkms: where a setting must change, this guide and `doctor` tell you the line, and you apply it.

## 1. Requirements

- **Hosts:** Ubuntu 24.04 on x86_64 or arm64, in four roles:
  - `validator` runs celestia-appd;
  - `fibre` runs the Fibre server (it may be the validator host);
  - `kms` runs tmkms (optional);
  - `monitor` runs Prometheus, Alertmanager and Grafana.

  Use a separate host for `monitor` when you can. On a single-host setup it may be the same box; the dead-man service (step 6) tells you when that box dies.
- **A private network between the hosts:** RFC 1918, Tailscale (100.64.0.0/10), WireGuard or IPv6 ULA addresses. Everything Taut installs listens on these addresses only, and the playbook fails if a Taut service listens on a public one.
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

Taut runs next to a node_exporter, Prometheus, Alertmanager, Grafana or OpenTelemetry collector the host already has, and leaves it alone. Each Taut service has its own systemd unit and system user (`taut-node-exporter`, `taut-otelcol`, `taut-prometheus`, `taut-alertmanager`, `taut-grafana`), runs from `/opt/taut/<component>-<version>/`, keeps its settings under `/etc/taut/` and its data under `/var/lib/taut/`, and logs to the journal (for example `journalctl -u taut-grafana`). Taut puts nothing in `/usr/local/bin` except `taut-doctor`. The ports above are a block that common tools do not use; to move them, set `taut_ports` (step 3).

## 2. Prepare the validator

Taut needs two settings that it never changes itself: celestia-appd's metrics and, if you run Fibre, Fibre's telemetry endpoint. Change them yourself and restart each service at a moment you choose.

**celestia-appd metrics.** In the node's `config/config.toml`:

```toml
[instrumentation]
prometheus = true
prometheus_listen_addr = "<validator private address>:26660"
```

The default `prometheus = false` serves no metrics. The default `prometheus_listen_addr = ":26660"` listens on every address, so set the private address. The `namespace` can stay `cometbft` (the default) or be `celestia`; Taut handles both.

**Fibre telemetry** (only if you run Fibre). Fibre has no scrape endpoint; it pushes metrics over OTLP. Point it at the collector Taut installs on the Fibre host: add `--otel-endpoint http://127.0.0.1:9915` to the `ExecStart` line of Fibre's systemd unit, or set `FIBRE_OTEL_ENDPOINT=http://127.0.0.1:9915` in its environment. Use the base URL, without `/v1/metrics`. Then run `systemctl daemon-reload` and restart Fibre. Until the playbook has installed the collector, Fibre logs export errors; they stop once the collector runs.

**A firewall on the hosts.** When ufw is already active, the playbook allows the monitor's private address to the ports Prometheus scrapes on the other hosts: port 26660 on the validator and the node_exporter and OpenTelemetry collector ports (9910, 9911). With any other firewall, allow those yourself. Taut never enables, resets or reorders a firewall.

**A remote signer** needs nothing extra: with `priv_validator_laddr` set and `prometheus = true`, the node exports the signer metrics.

`doctor` checks both settings and prints the exact line to change. You can run it before the first install: copy `tools/doctor.sh` to the host and run `sudo bash doctor.sh`. Without a settings file it guesses the host's roles from its systemd units; `bash doctor.sh --help` lists the flags. After the install it is at `/usr/local/bin/taut-doctor` (step 8).

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
| `taut_private_ip` | every host Taut installs on | Private address where Taut's services listen and Prometheus scrapes. Required. |
| `taut_data_mounts` | validator, fibre, kms | Mount points to watch for free space and read-only state, for example the node's data disk. |
| `taut_fibre_mount` | fibre | Mount point of the Fibre store. |
| `taut_fibre_public_endpoint` | fibre | The `host:port` you registered with `celestia-appd tx valaddr set-host`. `doctor` compares it with the on-chain registration. |
| `taut_kms_scrape` | kms | `true` installs node_exporter on the KMS host, so tmkms and its disks are watched. Default `false`: Taut installs nothing there. |
| `taut_node_home` | validator | celestia-appd home, for `doctor`. Default: found from the unit. |
| `taut_link_mbps` | validator, fibre, kms | Optional. The link speed in Mbit/s for the Link load panel. Set it when the host reports no NIC speed (common on VMs), has more than one physical NIC, or its plan is below the port speed. Default: the NIC's port speed. |

In `group_vars/all.yml`, set the shared settings:

| Variable | Meaning |
|---|---|
| `taut_signer` | `local` (key file on the node) or `remote` (tmkms). With `local`, the signer panels and RemoteSignerProblem are left out. |
| `taut_fibre_enabled` | `true` installs and renders everything Fibre-related. Keep `false` until app v10 is active on your network: before that, Fibre does not run and `x/valaddr` does not exist. |
| `taut_receivers` | Alert receivers for `page` and `message` (below). One list may be empty: its alerts then go to the other list's receivers. |
| `taut_deadman_webhook_url` | Heartbeat URL of your dead-man service (step 6). |
| `taut_grafana_admin_password` | Grafana's admin password. |

Optional settings, with their defaults, are in `ansible/roles/taut/defaults/main.yml`: systemd unit names (`taut_units`: `celestia-appd`, `fibre`, `tmkms`), the node's metrics and RPC ports (`taut_node_metrics_port: 26660`, `taut_node_rpc_port: 26657`), Taut's ports (`taut_ports`), alert thresholds (`taut_thresholds`, see [alerts.md](alerts.md)) and `taut_prometheus_retention` (30d). Overriding a map (`taut_units`, `taut_ports`, `taut_thresholds`) replaces the whole map, so list every key.

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
taut_receivers:
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
2. installs node_exporter on the validator, Fibre and (if opted in) KMS hosts, the OpenTelemetry collector on Fibre hosts, and `taut-doctor` on all of them;
3. installs Prometheus, Alertmanager and Grafana on the monitor, with the alert rules and the dashboard built from your inventory;
4. fails if any service Taut installed listens on a public address.

Binary versions are pinned in `versions.yml` and checksums are verified on download. Run the playbook again after every inventory change; a run with nothing to change reports no changes. If another process already listens on one of Taut's ports, the playbook stops before it starts that service, names the process and its PID, and says which `taut_ports` key to set.

## 5. Open Grafana

Grafana listens on the monitor's private address: `http://<monitor private address>:9914`. Log in as `admin` with `taut_grafana_admin_password`. Grafana reads that password only on its first start; change it later in Grafana (Profile, Change password).

With ufw active, allow your workstation's private address to port 9914 yourself (`ufw allow from <addr> to any port 9914 proto tcp`), or use the SSH tunnel below.

If you are not on the private network, use an SSH tunnel and open `http://localhost:9914`:

```sh
ssh -L 9914:<monitor private address>:9914 <user>@<monitor>
```

The dashboard is **Taut: Celestia validator** in the **Taut** folder. [panels.md](panels.md) explains each panel. The playbook generates the dashboard from your inventory, so groups that do not apply are left out and the threshold lines match the alerts. Do not edit it in Grafana; change the inventory and run the playbook again.

## 6. Set up the dead-man service

Taut's Watchdog alert is always firing. Alertmanager sends it every 1 to 2 minutes to a dead-man service, which alerts you when the heartbeats stop: when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network.

1. Choose any dead-man (heartbeat) service that gives you a URL to ping and alerts you when pings stop. Alertmanager pings it with an HTTP POST.
2. Set its period to **5 minutes or more**, so a ping that arrives two minutes apart never trips it.
3. Put the URL in `taut_deadman_webhook_url` (encrypted with ansible-vault) and run the playbook again.
4. Test it: on the monitor, run `sudo systemctl stop taut-alertmanager`, wait until the dead-man service alerts you, then `sudo systemctl start taut-alertmanager`.

Without a URL, the heartbeat is dropped and nothing tells you that monitoring itself is down.

## 7. Subscribe to network notices

Upgrades are the most common reason a node stops at a height or crash loops. Celestia announces upgrades and incidents per network on [status.celestia.org](https://status.celestia.org/). Subscribe there for the networks you run, or route its feed ([RSS](https://status.celestia.org/feed.rss), [Atom](https://status.celestia.org/feed.atom)) into the same channel as your alerts. See also Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page.

## 8. Run doctor

On each validator, Fibre and KMS host Taut installed on:

```sh
sudo taut-doctor
```

It changes nothing. It reads `/etc/taut/doctor.conf`, which the playbook writes; any setting can also be given as a flag (`taut-doctor --help`). Each check prints one line: `OK`, `FAIL` with the exact fix, `WARN`, or `SKIP` when the check does not apply on this host. The exit code is 1 when a check failed.

| # | Host | Check |
|---|---|---|
| 1 | validator | celestia-appd metrics are enabled and served; warns if the port listens on a public address. |
| 2 | validator | The signer setting matches `taut_signer`; with a remote signer, the median signing latency is at most 10 ms (skipped until 50 signatures after a start). |
| 3 | validator | The node is at the chain head: height advancing, `catching_up=false`. |
| 4 | validator | The validator's consensus address, from the node's `/status`. |
| 5 | validator | With Fibre on: the Fibre host registered in `x/valaddr`, compared with `taut_fibre_public_endpoint`. |
| 6 | fibre | The Fibre unit is active and its port (7980 by default) listens. |
| 7 | fibre | Fibre's OTLP endpoint points at the local collector. |
| 8 | all | Taut's services (the `taut-*` units) listen on private addresses only; other software on the host is not checked. Reports whether a firewall is active. |
| 9 | all | Time sync is active and the clock offset is small. |
| 10 | all | Free space on the data mounts; the Fibre mount is not read-only. |

Run it again after every celestia-app or Fibre upgrade and every config change.

## 9. Check that alerts reach you

On the monitor, send a test alert of each severity and check that it arrives where you expect. Taut's `amtool` is in `/opt/taut/alertmanager-<version>/`, with the Alertmanager version from `versions.yml`:

```sh
amtool=/opt/taut/alertmanager-<version>/amtool
$amtool alert add alertname=TautTest severity=page host=test --annotation=summary='Taut test' \
  --alertmanager.url=http://<monitor private address>:9913
$amtool alert add alertname=TautTest severity=message host=test --annotation=summary='Taut test' \
  --alertmanager.url=http://<monitor private address>:9913
```

Each resolves by itself after 5 minutes. [alerts.md](alerts.md) describes every alert and what to check first.
