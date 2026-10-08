# Alerts

Taut has six alerts and a heartbeat. Each alert names the host it is about (the Watchdog excepted) and carries a severity:

- **page**: someone should look now;
- **message**: look during working hours;
- **heartbeat**: the Watchdog, sent only to your dead-man service.

| Alert | Severity | Catches |
|---|---|---|
| [ServiceDown](#servicedown) | page or message | celestia-appd, Fibre, tmkms or a collector down, not loaded or crash looping |
| [NodeNotAtHead](#nodenotathead) | page | Node stuck, out of sync or without peers, or the chain halted |
| [ValidatorNotSigning](#validatornotsigning) | page | Node up but its key does not sign |
| [RemoteSignerProblem](#remotesignerproblem) | message | Remote signer too slow, or unable to sign proposals |
| [FibreNotServing](#fibrenotserving) | message | Fibre running but not serving uploads, or sending no metrics |
| [DiskLow](#disklow) | page or message | A watched disk filling up or turned read-only |
| [Watchdog](#watchdog) | heartbeat | Monitoring itself down (through the dead-man service) |

**One cause, one notification.** While NodeNotAtHead or a page-level ServiceDown fires, ValidatorNotSigning, RemoteSignerProblem and FibreNotServing are held back. While the Fibre unit is down, FibreNotServing is held back.

**Thresholds** below are the defaults. Change them in `taut_thresholds` in your inventory (list every key) and run the playbook again; the dashboard's threshold lines follow.

| Key | Default | Used by |
|---|---|---|
| `not_signing_blocks` | 50 | ValidatorNotSigning |
| `signer_median_seconds` | 0.010 | RemoteSignerProblem |
| `fibre_stale_uploads` | 10 | FibreNotServing |
| `fibre_stale_min_seconds` | 3600 | FibreNotServing |
| `disk_page_free_ratio` | 0.03 | DiskLow (page) |
| `disk_message_free_ratio` | 0.10 | DiskLow (message) |
| `crashloop_restarts` | 2 | ServiceDown (crash loop) |

To see what is firing now, open Prometheus (`http://<monitor private address>:9191/alerts`) or Alertmanager (`:9093`).

## ServiceDown

**What it means.** A Celestia service or one of Taut's collectors is down. The summary says which case.

**Severity.** page or message, by case:

| Case | Fires after | Severity |
|---|---|---|
| celestia-appd's metrics endpoint is unreachable | 3 min | page |
| The celestia-appd or tmkms unit is not active | 2 min | page |
| The Fibre unit is not active | 2 min | message |
| node_exporter or the OpenTelemetry collector is unreachable | 3 min | message |
| systemd restarted a unit more than 2 times in 30 minutes (crash loop) | at once | page for celestia-appd and tmkms, message for Fibre |
| A configured unit is not loaded at all | 2 min | as for the unit |

tmkms is watched only when its host has `taut_kms_scrape: true`. The crash loop rule counts only the restarts systemd makes under `Restart=`. While node_exporter is unreachable, the service and disk alerts for that host cannot fire.

**Check first.**

1. On the host in the alert, `systemctl status <unit>` and `journalctl -u <unit> -n 100`. After an upgrade or a config change, the last lines usually name the cause: a binary that does not match the network's app version, a config key, a full or read-only disk.
2. For "is not loaded": the unit was stopped and disabled, or `taut_units` names a different unit than the one that runs the service. A stopped, disabled unit has no metrics at all. Correct `taut_units` if needed and run the playbook again.
3. For "is unreachable": from the monitor, `curl -s http://<host private address>:<port>/metrics | head`. If that fails while the service runs, check its listen address (for celestia-appd, `prometheus_listen_addr` in `config.toml`), a firewall on the host, and the private network between the hosts. `sudo taut-doctor` on the host checks the listen addresses and the node's metrics.

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#troubleshooting), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## NodeNotAtHead

**What it means.** The node's consensus height has not changed for 5 minutes. The node is stuck (often at an upgrade height), has lost sync or its peers, or the whole network has halted. Celestia's docs ask validators to alert when the node can no longer stay at the head of the chain.

**Severity.** page.

**Check first.**

1. [status.celestia.org](https://status.celestia.org/) and an explorer. If the whole chain stopped, coordinate with the other validators instead of fixing locally.
2. `journalctl -u celestia-appd -n 200` on the validator: an upgrade height that needs a new binary, a consensus failure, an app hash mismatch.
3. Peers and sync: the "Node at chain head" panel shows new blocks, block sync and peers; `sudo taut-doctor` (check 3) shows the height and `catching_up`.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## ValidatorNotSigning

**What it means.** The chain moves, but the validator's own key has not signed for more than 50 blocks, for 3 minutes. Causes: the remote signer or its link is down, the node runs with the wrong key, the validator is jailed or out of the active set, or the KMS is not compatible after an upgrade. It also fires when the node has not signed once since it started.

**Severity.** page.

**Check first.**

1. Is the validator jailed or out of the active set? Look it up in an explorer or with `celestia-appd query staking validator <celestiavaloper address>`. If it is jailed, find out why before you unjail.
2. With a remote signer: tmkms status and log on the KMS host (`journalctl -u tmkms -n 100`), the link between the validator and the KMS host, and the "Signing failures by reason" panel.
3. The key: `sudo taut-doctor` (check 4) prints the consensus address the node signs with, from its `/status`. Compare it with your validator's consensus address on chain. With a remote signer, do not use `celestia-appd comet show-address` for this: it reads the local key file, not the signer.

## RemoteSignerProblem

Only with `taut_signer: remote`.

**What it means.** One of two things, named in the summary:

- **Slower than 10 ms:** the median of the last 50 signatures has been above 10 ms for 15 minutes. Celestia requires the median signing latency to stay at or below 10 ms.
- **Fails to sign proposals:** proposal signing failed in the last 6 hours for a reason other than a timeout or a lost connection. The KMS still signs votes, so ValidatorNotSigning stays quiet, but it cannot propose. Link blips are left out because every failed signature is retried up to 50 times and each attempt counts. The alert fires on the second failed proposal.

**Severity.** message.

**Check first.**

1. The "Signer latency vs 10 ms" and "Signing failures by reason" panels: which message type is slow or failing, and why.
2. Latency: the network path between the validator and the KMS host (round trip, same datacenter or not) and the load on the KMS host. Celestia recommends keeping the signer close, for example by colocation.
3. Proposal failures: the tmkms log on the KMS host around the failed proposals, and whether the tmkms version and its chain settings match the network's current app version (often the cause after an upgrade).

**Links.** [celestia-app v10 release notes: KMS requirements](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#key-management-systems-kms), [Fibre server guide: signing](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#signing), [tmkms](https://github.com/iqlusioninc/tmkms).

## FibreNotServing

Only with `taut_fibre_enabled: true`.

**What it means.** Fibre runs but is not doing its job. The `reason` label says how:

- `stale`: for 10 minutes, the time since the last successful upload has been longer than 10 uploads take at this server's 7-day average rate, and longer than 1 hour. Before the first upload after a start, the time counts from Fibre's start. The alert is silent when the server had no upload in 7 days. After a burst of uploads followed by a network-wide lull, it can fire until the burst leaves the 7-day window. These values are starting points until real mainnet traffic exists; tune `fibre_stale_uploads` and `fibre_stale_min_seconds` if needed.
- `no_telemetry`: the Fibre unit has been active for 30 minutes but no Fibre metrics arrive. Fibre's OTLP endpoint is not set, or the collector is not receiving.

Possible causes: the host is not registered or not reachable from outside, the validator is not bonded, the node is not synced, the signer cannot sign raw bytes, or the listener died while the process kept running.

**Severity.** message. Not serving Fibre carries no on-chain penalty.

**Check first.**

1. For `no_telemetry`: `sudo taut-doctor` on the Fibre host (check 7) and `systemctl status taut-otelcol`. Fibre needs `--otel-endpoint http://127.0.0.1:4318` or `FIBRE_OTEL_ENDPOINT`.
2. Registration and reachability: `sudo taut-doctor` on the validator host (check 5) shows the registered `host:port`; on the Fibre host, check 6 shows the unit and its listening port. From outside, a TCP connection to the registered `host:port` must succeed; [Tensile](https://tensile.huginn.tech) and [Valopers](https://testnet.celestia.valopers.com/fibre/providers) show your server from outside. If the port does not answer while the unit is active, read `journalctl -u <fibre unit> -n 100` and restart Fibre at a moment you choose.
3. The validator is bonded and the node is synced (Fibre logs "derived storage budget is 0" when it finds no stake), and the signer supports `SignRawBytes` (`raw_bytes` failures in the "Signing failures by reason" panel).

**Links.** [Fibre server guide: server runs but no uploads arrive](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#server-runs-but-no-uploads-arrive), [registration](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#registration), [tracing and metrics](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#tracing--metrics).

## DiskLow

**What it means.** A disk you listed in `taut_data_mounts` or `taut_fibre_mount` is filling up or turned read-only. A node whose data disk turns read-only stops working, and the validator can be jailed for downtime.

**Severity.** page or message, by disk and condition:

| Disk | Condition | Severity |
|---|---|---|
| Data mounts on the validator and KMS hosts | read-only, or less than 3% free | page |
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

## Watchdog

**What it means.** The Watchdog always fires. Alertmanager sends it every 1 to 2 minutes, only to the dead-man service in `taut_deadman_webhook_url`, which alerts you when the heartbeats stop. You never receive it from Taut itself. If the dead-man service alerts you, monitoring is down and no other Taut alert can reach you.

**Severity.** heartbeat.

**Check first.**

1. Is the monitor host up and reachable? If the monitor is also the validator host, the validator may be down too.
2. On the monitor, `systemctl status prometheus alertmanager` and `journalctl -u alertmanager -n 100` (failed webhook calls to the dead-man URL show there).
3. Outbound HTTPS from the monitor to the dead-man service, and the URL in `taut_deadman_webhook_url`.

To test the chain end to end, stop Alertmanager on the monitor for longer than the dead-man period, then start it again (see the [quickstart](quickstart.md#6-set-up-the-dead-man-service)).
