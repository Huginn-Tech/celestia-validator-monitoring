# Quickstart

This guide takes you from nothing to a running dashboard, working alerts and a clean `doctor` report. You run the same command on each box, on the box itself. Every install is pre-checked and reversible: `install.sh` checks the box, shows what it would change, changes it only after you confirm, and `install.sh --uninstall` removes it again. celestia-validator-monitoring never changes, restarts or upgrades celestia-appd, Fibre or tmkms: where a setting must change, the install and `doctor` tell you the line, and you apply it.

[reference.md](reference.md) has every setting, port and file, [doctor.md](doctor.md) every check, and [layouts.md](layouts.md) which box gets which role.

## 1. What you need

- **Boxes:** Ubuntu 24.04 with systemd, on x86_64 or arm64, with root (`sudo`) and the `python3` and `curl` that Ubuntu ships. Each box takes one or more roles, its groups in the inventory:

  | Group | What runs there |
  |---|---|
  | `validator` | celestia-appd, the node the signer normally connects to (exactly one) |
  | `backup_node` | a synced celestia-appd without a key that can take over signing (optional, any number) |
  | `fibre` | a Fibre install, active or a stopped standby; systemd says which one runs |
  | `kms` | tmkms (optional; nothing is installed there unless you opt in) |
  | `monitor` | Prometheus, Alertmanager and Grafana (exactly one) |

  A monitor on a box of its own keeps alerting when a validator or Fibre box dies, and may sit in another data centre as long as it reaches the other boxes over WireGuard ([security.md](security.md#where-to-run-the-monitor)). It may also share a box; the dead-man service below then tells you when that box dies. [layouts.md](layouts.md) shows the common layouts, from one box to Fibre and the node it reads on separate boxes.
- **A private address on each box:** WireGuard, a private VLAN, Tailscale (100.64.0.0/10) or an IPv6 ULA address; on a single box, `127.0.0.1`. Everything celestia-validator-monitoring installs listens on these addresses or on 127.0.0.1, and the install fails if one of its services listens on a public address.
- **Outbound HTTPS** while `install.sh` runs, and from the monitor to your receivers and the dead-man service ([reference.md](reference.md#outbound-connections) lists the hosts).
- **One alert receiver that is not PagerDuty:** a Telegram bot and chat, a Discord or Slack webhook, or a webhook of your own. Message alerts never go to PagerDuty; page alerts go to every receiver, so add PagerDuty as well if you want pages to ring your phone.
- **A dead-man URL.** The Watchdog alert is always firing. Alertmanager sends it every 1 to 2 minutes to a dead-man service, which alerts you when the heartbeats stop: when the monitor, Prometheus or Alertmanager is down, or the monitor has lost its network. The heartbeat also stops for an hour after a page or message notification failed to go out, so a broken alert channel (an expired bot token, a removed chat) reaches you through the dead-man service. Set one up before you start:
  1. Choose any dead-man (heartbeat) service that gives you a URL to ping and alerts you when pings stop. Alertmanager pings it with an HTTP POST.
  2. Set its period to **5 minutes or more**, so a ping that arrives two minutes apart never trips it.
  3. Connect it to **PagerDuty and to email or another channel than your alert chat**. A dead monitor box, or a whole data centre gone, then still rings your phone, and when the alert chat is what broke, the dead-man service is the only one left to tell you.

  The install on the monitor stops without this URL: without it nothing tells you that monitoring itself is down.

The install checks the rest itself: free disk, the room for Prometheus's data, free ports and a usable `/var/tmp` ([reference.md](reference.md#disk-and-memory)).

## 2. Get the package on each box

On each box you install on, as your own user, download a release from the repository's Releases page, check it and unpack it:

```sh
tag=vX.Y.Z   # the release you install
url=https://github.com/s0urledd/celestia-validator-monitoring/releases/download/$tag
curl -fsSLO "$url/celestia-validator-monitoring-$tag.tar.gz" && curl -fsSLO "$url/SHA256SUMS"
sha256sum -c SHA256SUMS   # must print: celestia-validator-monitoring-<tag>.tar.gz: OK
tar -xzf "celestia-validator-monitoring-$tag.tar.gz" && cd "celestia-validator-monitoring-$tag"
```

The KMS box needs nothing, unless you want its disks watched ([layouts.md](layouts.md#kms)). To install a commit you reviewed in a clone of the repository, pack it yourself: [reference.md](reference.md#install-from-a-copy-you-pack-yourself).

## 3. Describe your boxes

One inventory describes every box, and every box you install on gets the same copy of it in `~/celestia-mon-inventory`.

1. **Survey each box.** On each box, from the package:

   ```sh
   sudo ./install.sh --survey
   ```

   It reads what the box runs and prints its groups, its entry for `hosts.yml` (its private address, the mount points to watch, the Fibre store, and unit names or ports where they are not the defaults) and your steps on the Celestia side. It changes nothing and downloads nothing. The KMS box needs no survey: list it by its name in the `kms` group.
2. **Start from the example of your layout** ([layouts.md](layouts.md) names it), in the package on one box:

   ```sh
   cp -r ansible/inventory/example-split ~/celestia-mon-inventory
   rm -r ~/celestia-mon-inventory/host_vars   # the example's secrets file: the secrets go on the monitor (step 4)
   ```

3. **Fill it in.** In `~/celestia-mon-inventory/hosts.yml`, replace the example's entries with the ones the surveys printed, under `all: hosts:`, and put each box in its groups under `children:`. The box names are yours to choose. In `group_vars/all.yml`, set `celestia_mon_signer` (`local` or `remote`) and `celestia_mon_fibre_enabled` (`true` when the validator runs Fibre). Every setting is in [reference.md](reference.md#settings). Each box's `celestia_mon_private_ip` must be an address of that box: `install.sh` finds the box by it.
4. **Copy the folder to every box you install on**, for example from the box where you wrote it:

   ```sh
   rsync -a ~/celestia-mon-inventory/ <box>:celestia-mon-inventory/
   ```

   It holds no secret. Run `install.sh` again on every box after each change to it, with the new copy on each.

## 4. Add the secrets on the monitor

On the monitor only:

```sh
sudo ./install.sh --edit-secrets
```

It opens `/etc/celestia-mon/secrets.yml` in your editor (nano or vi; `sudo EDITOR=vim ./install.sh --edit-secrets` for another), from a template on the first run. Set:

- `celestia_mon_receivers`: your receivers ([the types](reference.md#secrets));
- `celestia_mon_deadman_webhook_url`: the dead-man URL from step 1.

Leave Grafana's admin password out: the first install generates one. The file belongs to root, only root reads it, and only the monitor's install reads it; the install stops when it is on another box. To keep the secrets in an encrypted `vault.yml` in the inventory instead, see [reference.md](reference.md#secrets).

## 5. Install on each box

On each box, from the package:

```sh
sudo ./install.sh
```

Any order works; with the monitor last, its first `doctor` run already sees the other boxes. On the KMS box nothing is installed unless you set `celestia_mon_kms_scrape: true` for it. `install.sh`:

1. **finds the box** in the inventory and prints `Installing as val-1 (10.10.0.1) from /home/<you>/celestia-mon-inventory`;
2. **checks the box**, changing nothing: its address, the unit names of the Celestia services, the mount points, free ports, free disk, and that it belongs to no other install. A failed check stops it before any change and names the fix;
3. **shows the plan:** the services and their users, the addresses and ports they listen on, the directories, the disk and memory they need, and each ufw rule it would add (only where ufw is already active, marked `celestia-mon`). On a Fibre box, the `Fibre:` line says whether Fibre already sends its metrics to the collector, and the exact change if not;
4. **asks** `Apply this to val-1? [y/N]`;
5. **installs.** On the monitor's first install it sends one test message to your chat receivers (not PagerDuty) and prints where Grafana's password is;
6. **prints the command that removes it again;**
7. **ends with `Still to do on this box`:** the `FAIL`, `WARN` and `NOT YET` lines of `doctor`, each with its fix.

The whole output is in `/var/log/celestia-mon/install.log`. If the install stops with an error, fix the cause and run it again: it continues where it stopped. The flags and the details are in [reference.md](reference.md#install-details).

## 6. Your steps

The survey and the end of each install name the changes on the Celestia side. Make each at a moment you choose: celestia-validator-monitoring never makes them.

**Node metrics,** on the validator and each backup node. celestia-appd's metrics are off by default. In the node's `config/config.toml`:

```toml
[instrumentation]
prometheus = true
prometheus_listen_addr = "<this box's private address>:26660"
```

Then restart celestia-appd. The default `prometheus_listen_addr = ":26660"` listens on every address, so set the private address. The `namespace` can stay `cometbft` (the default) or be `celestia`. A remote signer needs nothing extra: with `priv_validator_laddr` set and `prometheus = true`, the node exports the signer metrics.

**Fibre's metrics,** on each Fibre box. Fibre pushes its metrics over OTLP to the collector the install put on its box, at `127.0.0.1:9915`. Add `--otel-endpoint http://127.0.0.1:9915` to `ExecStart` in Fibre's unit (`sudo systemctl edit --full <fibre unit>`), or set `FIBRE_OTEL_ENDPOINT=http://127.0.0.1:9915` where the unit already sets that variable; use the base URL, without `/v1/metrics`. Then `sudo systemctl daemon-reload && sudo systemctl restart <fibre unit>`. On a standby Fibre box, run only `sudo systemctl daemon-reload` and leave the unit stopped: it uses the new endpoint when it next starts. The plan's `Fibre:` line names the exact change for each box.

**The node Fibre reads.** Fibre reads the chain through a node's app gRPC, at its `app_grpc_address` (`127.0.0.1:9090` by default), and cannot start while that node does not answer. Where Fibre reads the node on its own box, keep `[grpc] address = "127.0.0.1:9090"` in that node's `app.toml`. Where it reads a node on another box, that node serves its app gRPC on its private address and lets only the Fibre box in: [layouts.md](layouts.md) has the lines for each layout, and `doctor` check 27 on the Fibre box names the node it reads.

**Lock the validator's ports.** On the validator and each backup node, every port but p2p (26656) takes one source: `priv_validator_laddr` (26658) the KMS box, `priv_validator_grpc_laddr` (26669, always with mTLS) the Fibre boxes, the app gRPC (9090) 127.0.0.1 or the Fibre boxes that read this node, the metrics port the monitor, and the RPC (26657) and REST API (1317) 127.0.0.1 or one box. Bind each to the private address that box dials, or to 127.0.0.1, never to `0.0.0.0`, and let only that source in through the firewall. [security.md](security.md#ports-on-the-validator-who-connects-to-what) lists each setting and ufw command; `doctor` checks 20 and 25 show where each port listens and who can reach it.

Then run `doctor` on each box, the monitor included, until it is clean:

```sh
sudo celestia-mon-doctor
```

It changes nothing. Each line starts with `OK`, `NOT YET`, `FAIL`, `WARN`, `INFO` or `SKIP`, and a `FAIL` or `WARN` line ends with its fix. [doctor.md](doctor.md) explains each check.

### What doctor shows right after each box's install

Some lines clear by themselves once the other boxes are installed and your steps are made:

| Box | Line | Why | Clears |
|---|---|---|---|
| validator, backup node | `FAIL 1 node metrics` | The node's metrics are off, or `WARN` while they listen on every address. | After your change and the node's restart. |
| Fibre box | `FAIL 7 fibre otlp` | Fibre does not send its metrics to the collector yet. | After your change to Fibre's unit. |
| Fibre box | `FAIL 27 fibre app node` | Fibre reads `127.0.0.1:9090` on a box with no node. | After `app_grpc_address` names the node it reads. |
| monitor | `WARN 11 monitor stack`, "not scraped yet" | Prometheus has just started. | In a minute. |
| monitor | `FAIL 11 monitor stack`, "N of M scrape targets down" | A box is not installed yet, or a node's metrics are still off. | Once every box is installed and its metrics are on. |
| monitor | `NOT YET 12 rule metrics` | Metrics that appear only after an event: a first upload, a first missed signature, a governance proposal. | By themselves, with the event. They are expected. |
| monitor | `FAIL 12 rule metrics` | Their source is down (check 11) or Fibre does not push yet (check 13). | With checks 11 and 13. |
| monitor | `FAIL 13 fibre pipeline` | No Fibre metrics arrive yet. | After Fibre's endpoint is set (check 7 on the Fibre box). |
| monitor | `WARN 18 node lag` | A node reports no height yet. | Once its metrics are on. |
| monitor | `FAIL 19 fibre active`, "cannot tell which Fibre host is active" | A Fibre box is not installed yet. | After the Fibre boxes' installs. |
| monitor | `NOT YET 22 dead-man` | No heartbeat sent yet. | Within 10 minutes of the install. |

Right after an install, `doctor` on the monitor can take up to a minute while Prometheus starts and scrapes each target once. Any metric check 12 lists as `WARN` needs a look.

## 7. Open Grafana and test the alert paths

**Grafana** listens on the monitor's 127.0.0.1. Open it through an SSH tunnel from your machine:

```sh
ssh -N -L 9914:127.0.0.1:9914 <user>@<monitor>
```

Then open `http://localhost:9914` and log in as `admin`, with the password from `sudo cat /etc/celestia-mon/grafana-admin-password` on the monitor (or the one you set). The dashboard is **Celestia validator**; [panels.md](panels.md) explains each panel. A private address or a reverse proxy instead of the tunnel: [reference.md](reference.md#grafana-access).

**Alerts.** Send a test alert of each severity on the monitor, as root (`sudo -i`), and check that each arrives where you expect: the page alert at every receiver, PagerDuty included, the message alert at every receiver except PagerDuty. Each resolves by itself after 5 minutes.

```sh
amtool=$(ls /opt/celestia-mon/alertmanager-*/amtool)
am="--alertmanager.url=http://127.0.0.1:9913 --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml"
$amtool $am alert add alertname=CelestiaMonTest severity=page host=test --annotation=summary='celestia-validator-monitoring test'
$amtool $am alert add alertname=CelestiaMonTest severity=message host=test --annotation=summary='celestia-validator-monitoring test'
```

**Dead-man.** On the monitor, run `sudo systemctl stop celestia-mon-alertmanager`, wait until the dead-man service alerts you through PagerDuty and its other channel, then `sudo systemctl start celestia-mon-alertmanager`.

[alerts.md](alerts.md) describes every alert and what to check first. Where the monitor shares a disk with the validator or Fibre, check the impact of Prometheus's compactions once it has run for a few hours ([reference.md](reference.md#compactions-on-a-shared-disk)).

## Keep it running

**Before planned restarts and upgrades.** Before you restart or upgrade celestia-appd, Fibre, tmkms or a whole box, silence that box's alerts on the monitor, as root (`sudo -i`), so the planned downtime pages nobody:

```sh
amtool=$(ls /opt/celestia-mon/alertmanager-*/amtool)
am="--alertmanager.url=http://127.0.0.1:9913 --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml"
$amtool $am silence add host=<box> --duration=30m --comment="planned upgrade of <box>"
```

When you stop a node that a Fibre on another box reads, silence that Fibre box too: its Fibre cannot restart until the node is back. NodeNotAdvancing, ValidatorNotSigning and FibreDown name no box, so silence them by name as well when the work stops what they watch everywhere ([alerts.md](alerts.md#before-planned-restarts-and-upgrades) has the commands). When the work is done, end the silence: `$amtool $am silence query host=<box>` lists it with its ID, `$amtool $am silence expire <ID>` ends it. `doctor` check 21 on the monitor lists every active silence that holds back a page alert or ends more than 4 hours from now.

**Monthly drill.** Once a month:

1. **Dead-man:** stop Alertmanager on the monitor until the dead-man service alerts you, then start it again (step 7).
2. **Alert channels:** send a test alert of each severity (step 7) and check that both arrive.
3. **Boxes:** `sudo celestia-mon-doctor` on every box.
4. **Drift:** `sudo ./install.sh --check` on every box. "The dry run finds nothing to change." means the box still matches the inventory.

**Upgrade.** Get the new release on each box (step 2) and run `sudo ./install.sh` from its folder; the inventory and the secrets stay where they are. Run `doctor` again after every celestia-app or Fibre upgrade and every config change. Details: [reference.md](reference.md#upgrades).

**Remove.** On each box, `sudo ./install.sh --uninstall` removes the services, their binaries and the ufw rules the install added, and keeps the data, settings and users for a reinstall; `--uninstall --purge` deletes those too. Details: [reference.md](reference.md#uninstall).

## Mainnet layout

The mainnet layout, with a backup node, a standby Fibre and a monitor on a box of its own, is in [layouts.md](layouts.md#mainnet-layout).
