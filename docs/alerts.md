# Alerts

celestia-validator-monitoring has nine alerts and a heartbeat. Each alert names the host it is about (the Watchdog excepted) and carries a severity:

- **page**: someone should look now;
- **message**: look during working hours;
- **heartbeat**: the Watchdog, sent only to your dead-man service.

| Alert | Severity | Catches |
|---|---|---|
| [ServiceDown](#servicedown) | page or message | celestia-appd, Fibre, tmkms or a collector down, not loaded or crash looping |
| [NodeNotAdvancing](#nodenotadvancing) | page | No new block committed for 3 minutes: node stuck, out of sync or without peers, or the chain halted |
| [ValidatorNotSigning](#validatornotsigning) | page | Node up but its key does not sign |
| [RemoteSignerProblem](#remotesignerproblem) | message | Remote signer too slow, or unable to sign proposals |
| [FibreDown](#fibredown) | page | The Fibre unit down for 10 minutes, or the Fibre host unreachable for 10 minutes |
| [FibreNotServing](#fibrenotserving) | message | Fibre running but accepting no uploads, or sending no metrics |
| [DiskLow](#disklow) | page or message | A watched disk filling up or turned read-only |
| [MonitoringBroken](#monitoringbroken) | message | Prometheus failing to evaluate alert rules |
| [NotificationsFailing](#notificationsfailing) | message | Alertmanager unable to deliver through one of your receivers |
| [Watchdog](#watchdog) | heartbeat | Monitoring itself down, or an alert that could not be delivered (through the dead-man service) |

## Where alerts go

`celestia_mon_receivers`, in the vault file on the monitor, is one list of receivers: any number, several of one type allowed. The type decides what each receiver gets:

| Receiver `type` | Keys | page | message |
|---|---|---|---|
| `pagerduty` | `routing_key` (an Events API v2 integration key) | yes | no |
| `telegram` | `bot_token`, `chat_id` (numeric, for example `-1000000000000`) | yes | yes |
| `discord` | `webhook_url` | yes | yes |
| `slack` | `api_url`, optional `channel` | yes | yes |
| `webhook` | `url` | yes | yes |

Page alerts go to every receiver; message alerts go to every receiver except PagerDuty. Without a PagerDuty receiver, page alerts still reach your chat channels. The list needs at least one receiver that is not PagerDuty, or message alerts would go nowhere; the install refuses such a list, and it also refuses the separate `page` and `message` lists of earlier versions. The heartbeat goes only to the dead-man service in `celestia_mon_deadman_webhook_url`.

```yaml
celestia_mon_receivers:
  - type: pagerduty
    routing_key: "<integration key>"
  - type: telegram
    bot_token: "<bot token>"
    chat_id: -1000000000000
  - type: discord
    webhook_url: "<Discord webhook URL>"
```

**One cause, one notification.** While NodeNotAdvancing or a page-level ServiceDown fires, ValidatorNotSigning, RemoteSignerProblem and FibreNotServing are held back. While the Fibre unit or its collector (celestia-mon-otelcol) is down, FibreNotServing is held back, and FibreDown holds back the same host's FibreNotServing.

**Thresholds** below are the defaults. Change them in `celestia_mon_thresholds` in your inventory (list every key) and run `install.sh` on the monitor again; the dashboard's threshold lines follow. The durations named with each alert are fixed in the rules.

| Key | Default | Used by |
|---|---|---|
| `not_signing_blocks` | 10 | ValidatorNotSigning |
| `signer_median_seconds` | 0.010 | RemoteSignerProblem |
| `fibre_stale_uploads` | 10 | FibreNotServing |
| `fibre_stale_min_seconds` | 3600 | FibreNotServing |
| `disk_page_free_ratio` | 0.05 | DiskLow (page) |
| `disk_message_free_ratio` | 0.10 | DiskLow (message) |
| `crashloop_restarts` | 2 | ServiceDown (crash loop) |

To see what is firing now, open Prometheus (`http://127.0.0.1:9912/alerts`) or Alertmanager (`http://127.0.0.1:9913`). Both listen on the monitor's loopback only: use them on the monitor, or through an SSH tunnel from your machine (`ssh -N -L 9912:127.0.0.1:9912 -L 9913:127.0.0.1:9913 <user>@<monitor>`, then the same URLs with `localhost`).

## Before planned restarts and upgrades

Silence the host on the monitor first, so a planned restart pages nobody. `amtool` comes with Alertmanager, in `/opt/celestia-mon/alertmanager-<version>/` (the version in `versions.yml`):

```sh
amtool=/opt/celestia-mon/alertmanager-<version>/amtool
am=--alertmanager.url=http://127.0.0.1:9913
$amtool $am silence add host=<host> --duration=30m --comment="planned restart of <host>"
```

It prints the silence's ID. Every alert labelled with that host is held back until the silence ends; the Watchdog keeps running. When the work is done and the dashboard looks healthy again, end the silence early:

```sh
$amtool $am silence query host=<host>                        # lists the silences for the host
$amtool $am silence expire <ID>                              # ends one
$amtool $am silence expire $($amtool $am silence query -q host=<host>)   # ends all of them
```

## ServiceDown

**What it means.** A Celestia service or one of celestia-validator-monitoring's collectors is down. The summary says which case.

**Severity.** page or message, by case:

| Case | Fires after | Severity |
|---|---|---|
| celestia-appd's metrics endpoint is unreachable | 3 min | page |
| The celestia-appd or tmkms unit is not active | 2 min | page |
| The Fibre unit is not active | 2 min | message (FibreDown pages after 10 min) |
| node_exporter or the OpenTelemetry collector is unreachable | 3 min | message |
| systemd restarted a unit more than 2 times in 30 minutes (crash loop) | at once | page for celestia-appd and tmkms, message for Fibre |
| A configured unit is not loaded at all | 2 min | as for the unit |

tmkms is watched only when its host has `celestia_mon_kms_scrape: true`. The crash loop rule counts only the restarts systemd makes under `Restart=`. While node_exporter is unreachable, the service and disk alerts for that host cannot fire.

**Check first.**

1. On the host in the alert, `systemctl status <unit>` and `journalctl -u <unit> -n 100`. After an upgrade or a config change, the last lines usually name the cause: a binary that does not match the network's app version, a config key, a full or read-only disk.
2. For "is not loaded": the unit was stopped and disabled, or `celestia_mon_units` names a different unit than the one that runs the service. A stopped, disabled unit has no metrics at all. Correct `celestia_mon_units` if needed and run `install.sh` again on that host and on the monitor.
3. For "is unreachable": from the monitor, `curl -s http://<host private address>:<port>/metrics | head`. If that fails while the service runs, check its listen address (for celestia-appd, `prometheus_listen_addr` in `config.toml`), a firewall on the host, and the private network between the hosts. `sudo celestia-mon-doctor` on the host checks the listen addresses and the node's metrics; on the monitor, check 11 names each target that is down and what to fix.

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#troubleshooting), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## NodeNotAdvancing

**What it means.** The node has committed no new block for 3 minutes: its consensus height did not change in 2 minutes, and that held for 1 more. The node is stuck (often at an upgrade height), has lost sync or its peers, or the whole network has halted. The alert shows that the node does not advance; it cannot tell whether a node that advances is at the network's head. Compare its height with an explorer or [status.celestia.org](https://status.celestia.org/) for that.

**Severity.** page.

**Check first.**

1. [status.celestia.org](https://status.celestia.org/) and an explorer. If the whole chain stopped, coordinate with the other validators instead of fixing locally.
2. `journalctl -u celestia-appd -n 200` on the validator: an upgrade height that needs a new binary, a consensus failure, an app hash mismatch.
3. Peers and sync: the "Node progress" panel shows new blocks, block sync and peers; `sudo celestia-mon-doctor` (check 3) shows whether the height advances and `catching_up`.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## ValidatorNotSigning

**What it means.** The chain moves, but the validator's own key has not signed for more than 10 blocks, for 2 minutes. Causes: the remote signer or its link is down, the node runs with the wrong key, the validator is jailed or out of the active set, or the KMS is not compatible after an upgrade. It also fires when the node has not signed once since it started.

**Severity.** page.

**Check first.**

1. Is the validator jailed or out of the active set? Look it up in an explorer or with `celestia-appd query staking validator <celestiavaloper address>`. If it is jailed, find out why before you unjail.
2. With a remote signer: tmkms status and log on the KMS host (`journalctl -u tmkms -n 100`), the link between the validator and the KMS host, and the "Signing failures by reason" panel.
3. The key: `sudo celestia-mon-doctor` (check 4) prints the consensus address the node signs with, from its `/status`. Compare it with your validator's consensus address on chain. With a remote signer, do not use `celestia-appd comet show-address` for this: it reads the local key file, not the signer.

## RemoteSignerProblem

Only with `celestia_mon_signer: remote`.

**What it means.** One of two things, named in the summary:

- **Slower than 10 ms:** the median of the last 50 signatures has been above 10 ms for 15 minutes. Celestia requires the median signing latency to stay at or below 10 ms.
- **Fails to sign proposals:** proposal signing failed in the last 6 hours for a reason other than a timeout or a lost connection. The KMS still signs votes, so ValidatorNotSigning stays quiet, but it cannot propose. Link blips are left out because every failed signature is retried up to 50 times and each attempt counts. The alert fires on the second failed proposal.

**Severity.** message: slower signing is a performance problem. If it turns into missed signatures, ValidatorNotSigning pages.

**Check first.**

1. The "Signer latency vs 10 ms" and "Signing failures by reason" panels: which message type is slow or failing, and why.
2. Latency: the network path between the validator and the KMS host (round trip, same datacenter or not) and the load on the KMS host. Celestia recommends keeping the signer close, for example by colocation.
3. Proposal failures: the tmkms log on the KMS host around the failed proposals, and whether the tmkms version and its chain settings match the network's current app version (often the cause after an upgrade).

**Links.** [celestia-app v10 release notes: KMS requirements](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#key-management-systems-kms), [Fibre server guide: signing](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#signing), [tmkms](https://github.com/iqlusioninc/tmkms).

## FibreDown

Only with `celestia_mon_fibre_enabled: true`.

**What it means.** Fibre is down, not just idle. One of:

- the Fibre unit has not been active for 10 minutes, or node_exporter has listed no Fibre unit for 10 minutes (stopped and disabled, or `celestia_mon_units.fibre` names the wrong unit);
- when the monitor runs on another host: Prometheus has not reached node_exporter on the Fibre host for 10 minutes, so the host, its network or node_exporter is down.

ServiceDown sends a message about the unit after 2 minutes; FibreDown pages if it stays down, so a short restart pages nobody. When the Fibre host is the monitor itself, there is no host-down case: if that host dies, the dead-man service tells you ([Watchdog](#watchdog)). celestia-validator-monitoring does not probe Fibre's public port from outside; [Tensile](https://tensile.huginn.tech) shows your server from outside.

**Severity.** page.

**Check first.**

1. On the Fibre host, `systemctl status <fibre unit>` and `journalctl -u <fibre unit> -n 100`. `sudo celestia-mon-doctor` there (check 6) shows the unit and its listening port.
2. For "does not answer": is the Fibre host up and reachable over the private network? Then `systemctl status celestia-mon-node-exporter` on it, and from the monitor `curl -s http://<Fibre host private address>:9910/metrics | head`.
3. For a planned Fibre restart, silence the host first ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)).

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#troubleshooting).

## FibreNotServing

Only with `celestia_mon_fibre_enabled: true`.

**What it means.** Fibre runs but is not doing its job. The `reason` label says how:

- `stale`: for 10 minutes, the time since the last accepted upload has been longer than 10 uploads take at this server's 7-day average rate, and longer than 1 hour. Before the first accepted upload after a start, the time counts from Fibre's start. The alert is silent when the server had no upload in 7 days. A quiet network also makes the time grow: after a burst of uploads followed by a network-wide lull, it can fire until the burst leaves the 7-day window. To tell a quiet network from a fault, check on [Tensile](https://tensile.huginn.tech) whether the network is carrying Fibre uploads. These values are starting points until real mainnet traffic exists; tune `fibre_stale_uploads` and `fibre_stale_min_seconds` if needed.
- `no_telemetry`: the Fibre unit has been active for 30 minutes but no Fibre metrics arrive. Fibre's OTLP endpoint is not set, or the collector is not receiving.

Possible causes: the host is not registered or not reachable from outside, the validator is not bonded, the node is not synced, the signer cannot sign raw bytes, or the listener died while the process kept running.

**Severity.** message: a quiet network is not an outage, and not serving Fibre carries no on-chain penalty. FibreDown pages when Fibre itself is down.

**Check first.**

1. For `no_telemetry`: `sudo celestia-mon-doctor` on the Fibre host (check 7) shows where the unit sends its metrics, and check 13 on the monitor shows whether they arrive. Fibre needs `--otel-endpoint http://127.0.0.1:9915` or `FIBRE_OTEL_ENDPOINT` ([quickstart step 6](quickstart.md#6-point-fibre-at-its-collector)).
2. Registration and reachability: `sudo celestia-mon-doctor` on the validator host (check 5) shows the registered `host:port`; on the Fibre host, check 6 shows the unit and its listening port. From outside, a TCP connection to the registered `host:port` must succeed; [Tensile](https://tensile.huginn.tech) shows your server from outside. If the port does not answer while the unit is active, read `journalctl -u <fibre unit> -n 100` and restart Fibre at a moment you choose.
3. The validator is bonded and the node is synced (Fibre logs "derived storage budget is 0" when it finds no stake), and the signer supports `SignRawBytes` (`raw_bytes` failures in the "Signing failures by reason" panel).

**Links.** [Fibre server guide: server runs but no uploads arrive](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#server-runs-but-no-uploads-arrive), [registration](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#registration), [tracing and metrics](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#tracing--metrics).

## DiskLow

**What it means.** A disk you listed in `celestia_mon_data_mounts` or `celestia_mon_fibre_mount` is filling up or turned read-only. A node whose data disk turns read-only stops working, and the validator can be jailed for downtime.

**Severity.** page or message, by disk and condition:

| Disk | Condition | Severity |
|---|---|---|
| Data mounts on the validator and KMS hosts | read-only, or less than 5% free | page |
| Data mounts on every watched host | less than 10% free for 10 minutes | message |
| Data mounts on a Fibre-only host | read-only | message |
| Fibre store | read-only | message |
| Fibre store | the space the storage budget still needs is more than the free space, for 30 minutes | message |
| Fibre store, when Fibre reports a budget of 0 (no limit) or no budget | less than 10% free for 10 minutes | message |

The Fibre store has no percentage rule while a budget is set: Fibre keeps its occupancy near the budget at full load, which is normal.

**Check first.**

1. Read-only: `journalctl -k` or `dmesg` for I/O or filesystem errors. The disk may be failing; find out why before you remount it read-write.
2. Free space: `du -xh --max-depth=2 <mount> | sort -h | tail` shows what grew. On the node, check pruning in `app.toml` and blockstore compaction in `config.toml`.
3. Fibre store: the "Fibre disk" panel shows budget, occupancy, used and free space. Grow the disk or move other data off it. Celestia recommends separate disks for Fibre and celestia-app data.

**Links.** [celestia-app v10 release notes: Fibre](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#fibre), [blockstore compaction](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#blockstore-compaction).

## MonitoringBroken

**What it means.** Prometheus failed to evaluate one or more alert rules in the last 10 minutes, so some alerts may not fire. It fires at once and clears 10 minutes after the last failure. The alert names the monitor host.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-prometheus -n 100`: each "Evaluating rule failed" line names the rule and the error.
2. Prometheus's rules page on the monitor (`http://127.0.0.1:9912/rules`, through an SSH tunnel from your machine) shows the failing rule and its last error.
3. Free space for Prometheus's data (`df -h /var/lib/celestia-mon/prometheus`), and `sudo celestia-mon-doctor` on the monitor: checks 11 and 12 show whether Prometheus answers and which rule metrics have no series, and check 15 whether the disk holds what Prometheus needs (retention size x 1.5 + 2 GiB).

## NotificationsFailing

**What it means.** Alertmanager could not deliver a notification through one integration (Telegram, Discord, Slack or PagerDuty) in the last 15 minutes. There is one alert per integration, named in the summary, and it goes to the message receivers, so a broken integration is reported through the others. It fires at once and clears 15 minutes after the last failure. Failed webhook calls are left out: the dead-man webhook and receivers of type `webhook` share `integration="webhook"` in Alertmanager's metrics. The Watchdog also stops for an hour after such a failure, so the dead-man service reports it too, also when no other receiver works.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-alertmanager -n 100`: each failed notification names the receiver and the error, for example a rejected bot token, a removed chat, a deleted Discord or Slack webhook, or an invalid PagerDuty routing key.
2. Fix the receiver in the vault file (`ansible-vault edit`) and run `install.sh` on the monitor again.
3. Send a test alert of each severity ([quickstart step 10](quickstart.md#10-check-that-alerts-reach-you)) and check that it arrives everywhere you expect.

## Watchdog

**What it means.** The Watchdog always fires while monitoring works. Alertmanager sends it every 1 to 2 minutes, only to the dead-man service in `celestia_mon_deadman_webhook_url`, which alerts you when the heartbeats stop. You never receive it from celestia-validator-monitoring itself. The heartbeats stop:

- when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network;
- for one hour after a page or message notification failed (Telegram, Discord, Slack or PagerDuty), so a broken alert channel reaches you through the dead-man service. Failed calls to the dead-man URL itself do not count.

Connect the dead-man check to PagerDuty and to email or another channel than your alert chat. A dead monitor host, or a whole data centre gone, then still rings your phone, and when the alert chat is what broke, the dead-man service is the one left to tell you. If the dead-man service alerts you, assume that no other celestia-validator-monitoring alert can reach you until you find the cause.

Receivers of type `webhook` in `celestia_mon_receivers` are not covered: Alertmanager labels their failures the same way as the dead-man webhook's (`integration="webhook"`), so a failing webhook receiver does not stop the heartbeat.

**Severity.** heartbeat.

**Check first.**

1. Is the monitor host up and reachable? If the monitor is also the validator host, the validator may be down too.
2. On the monitor, `systemctl status celestia-mon-prometheus celestia-mon-alertmanager` and `journalctl -u celestia-mon-alertmanager -n 100`. A failed notification shows there with its receiver and error, for example a rejected bot token or a removed chat; so do failed webhook calls to the dead-man URL.
3. Outbound HTTPS from the monitor to the dead-man service and to the receivers, and the URL in `celestia_mon_deadman_webhook_url`.

The heartbeat resumes one hour after the last failed notification. To test the chain end to end, stop Alertmanager on the monitor for longer than the dead-man period, then start it again (see the [quickstart](quickstart.md#8-set-up-the-dead-man-service)).
