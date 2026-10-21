# Alerts

celestia-validator-monitoring has twenty alerts and a heartbeat, and can pass Celestia's announcements on as alerts. Each carries a severity:

- **page**: someone should look now;
- **message**: look during working hours;
- **heartbeat**: the Watchdog, sent only to your dead-man service.

The dashboard calls a page alert **Urgent** (red) and a message alert **Warning** (amber).

Governance proposals and the validator's votes are not watched: follow them with a tool that alerts on proposals, or through Celestia's announcements ([CelestiaAnnouncement](#celestiaannouncement)).

The validator role can move to a backup node (tmkms pointed at it), and the Fibre role to a standby Fibre host. The alerts about those roles therefore watch the install as a whole and read which node signs and which Fibre runs from the metrics, never from the inventory: NodeNotAdvancing, ValidatorNotSigning, SignaturesMissed, ValidatorSigningTwice, FibreDown and FibreBothActive name no host, and neither do the Watchdog and the network upgrade alerts (UpgradeSignalMissing, UpgradeScheduled, UpgradeSoon). Every other alert names the host it is about.

| Alert | Severity | Catches |
|---|---|---|
| [ServiceDown](#servicedown) | message | celestia-appd, tmkms or a collector down or not loaded, or a Celestia service crash looping |
| [NodeNotAdvancing](#nodenotadvancing) | page | No node committed a new block for 3 minutes, or no node reports a height: nodes stuck, out of sync or without peers, celestia-appd down, or the chain halted |
| [NodeBehind](#nodebehind) | message | One node lags the others: a backup node that could not take over signing |
| [ValidatorNotSigning](#validatornotsigning) | page | The chain moves but the validator's key signs on no node |
| [SignaturesMissed](#signaturesmissed) | message | The validator signs, but misses more than 5% of the blocks of the last hour |
| [ValidatorSigningTwice](#validatorsigningtwice) | page | Two nodes keep getting the validator's signatures from a remote signer, for about 3 minutes: a double sign can follow (remote signer only) |
| [RemoteSignerProblem](#remotesignerproblem) | message | Remote signer too slow, or unable to sign proposals |
| [FibreDown](#fibredown) | page | No Fibre unit active on any Fibre host for 10 minutes; with the outside check, also the registered endpoint unreachable from outside, not registered, or not the host where Fibre runs |
| [FibreBothActive](#fibrebothactive) | message | Fibre active on more than one host for 5 minutes |
| [FibreNotServing](#fibrenotserving) | message | Fibre running but missing the uploads of blobs sent to the validator or not serving rows it signed for (with the outside check), failing every upload (often its signer), or sending no metrics |
| [DiskLow](#disklow) | page or message | A watched disk filling up or turned read-only |
| [UpgradeSignalMissing](#upgradesignalmissing) | page | Validators signal the next app version, and yours has not (only with upgrade signalling on) |
| [UpgradeScheduled](#upgradescheduled) | message, when scheduled (again after 4 days while it waits) | A network upgrade scheduled: version, height, when, and whether yours signalled (only with upgrade signalling on) |
| [UpgradeSoon](#upgradesoon) | message, sent once | The scheduled upgrade less than a day away (only with upgrade signalling on) |
| [MonitoringBroken](#monitoringbroken) | message | Prometheus failing to evaluate alert rules |
| [NotificationsFailing](#notificationsfailing) | message | Alertmanager unable to deliver through one of your receivers |
| [DeadmanFailing](#deadmanfailing) | message | Alertmanager unable to deliver the heartbeat to your dead-man service |
| [TensileUnreadable](#tensileunreadable) | message | The outside check of the Fibre endpoint stopped (only with the outside check on) |
| [SignalUnreadable](#signalunreadable) | message | Upgrade signalling no longer read (only with upgrade signalling on) |
| [AnnouncementsUnreadable](#announcementsunreadable) | message | Celestia's announcements no longer read (only with [CelestiaAnnouncement](#celestiaannouncement) on) |
| [CelestiaAnnouncement](#celestiaannouncement) | page or message, your choice | A new message in Celestia's announcement channels on Discord (optional) |
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

In Telegram and Discord each notification reads as text: the state (🔴 PAGE, 🟡 MESSAGE or ✅ RESOLVED), the alert and its host on the first line, then its summary and description (`monitoring/alertmanager-templates.tmpl`). Page alerts go to every receiver; message alerts go to every receiver except PagerDuty. Without a PagerDuty receiver, page alerts still reach your chat channels. The list needs at least one receiver that is not PagerDuty, or message alerts would go nowhere; the install refuses such a list, and it also refuses the separate `page` and `message` lists of earlier versions. The heartbeat goes only to the dead-man service in `celestia_mon_deadman_webhook_url`.

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

**One cause, one notification.** While NodeNotAdvancing fires, ValidatorNotSigning, SignaturesMissed, RemoteSignerProblem, FibreNotServing and NodeBehind are held back on every host. While ValidatorNotSigning fires, SignaturesMissed is held back. While FibreDown fires, FibreNotServing is held back. While a host's Fibre unit crash loops or its collector (celestia-mon-otelcol) is down, that host's FibreNotServing is held back.

**Thresholds** below are the defaults. Change them in `celestia_mon_thresholds` in your inventory (list every key) and run `install.sh` on the monitor again; the dashboard's threshold lines follow. The durations named with each alert are fixed in the rules.

| Key | Default | Used by |
|---|---|---|
| `not_signing_blocks` | 10 | ValidatorNotSigning |
| `signatures_missed_ratio` | 0.05 | SignaturesMissed |
| `node_behind_blocks` | 20 | NodeBehind |
| `signer_median_seconds` | 0.010 | RemoteSignerProblem |
| `disk_page_free_ratio` | 0.05 | DiskLow (page) |
| `disk_message_free_ratio` | 0.10 | DiskLow (message) |
| `crashloop_restarts` | 2 | ServiceDown (crash loop) |

To see what is firing now, open Prometheus (`http://127.0.0.1:9912/alerts`) or Alertmanager (`http://127.0.0.1:9913`). Both listen on the monitor's loopback only: use them on the monitor, or through an SSH tunnel from your machine (`ssh -N -L 9912:127.0.0.1:9912 -L 9913:127.0.0.1:9913 <user>@<monitor>`, then the same URLs with `localhost`). Both ask for a login, which your browser prompts for: user `celestia-mon` and the password the install generated on the monitor (`sudo cat /etc/celestia-mon/api-password`). Open the tunnel only while you use it: while it is open and the browser remembers the login, other web pages in that browser can send requests to these ports too.

## Before planned restarts and upgrades

Silence the host on the monitor first, so a planned restart pages nobody. `amtool` comes with Alertmanager, in `/opt/celestia-mon/alertmanager-<version>/` (the version in `versions.yml`). Run it as root (`sudo -i`): its login to Alertmanager is in a file only root can read.

```sh
amtool=/opt/celestia-mon/alertmanager-<version>/amtool
am="--alertmanager.url=http://127.0.0.1:9913 --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml"
$amtool $am silence add host=<host> --duration=30m --comment="planned restart of <host>"
```

It prints the silence's ID. Every alert labelled with that host is held back until the silence ends; the Watchdog keeps running. The alerts about the whole install name no host, so a host silence does not cover them. Silence them by name when the planned work stops what they watch everywhere: FibreDown when Fibre stops and no standby starts in its place, NodeNotAdvancing when the only node restarts (one celestia-appd host, no backup node), ValidatorNotSigning when the node the signer uses restarts, or tmkms restarts or is pointed at another node:

```sh
$amtool $am silence add alertname=FibreDown --duration=30m --comment="planned Fibre stop"
```

When the work is done and the dashboard looks healthy again, end the silence early:

```sh
$amtool $am silence query host=<host>                        # lists the silences for the host
$amtool $am silence query alertname=FibreDown                # or for an alert
$amtool $am silence expire <ID>                              # ends one
$amtool $am silence expire $($amtool $am silence query -q host=<host>)   # ends all of them
```

`sudo celestia-mon-doctor` on the monitor (check 21) lists every active silence that holds back a page alert or ends more than 4 hours from now, with its matchers, author, end and ID, so a mistyped or forgotten silence does not hide the next outage.

## ServiceDown

**What it means.** A Celestia service or one of celestia-validator-monitoring's collectors is down on one host. The summary says which case and which host.

**Severity.** message, in every case. The pages come from the alerts that watch the whole install (NodeNotAdvancing, ValidatorNotSigning, FibreDown), which follow the validator and Fibre roles when they move to another host: when the celestia-appd the signer uses stops, ValidatorNotSigning pages while a backup node keeps advancing, and NodeNotAdvancing pages when it was the only node.

| Case | Fires after |
|---|---|
| celestia-appd's metrics endpoint is unreachable (validator or backup node) | 3 min |
| The celestia-appd unit (validator or backup node) or the tmkms unit is not active | 2 min |
| node_exporter or the OpenTelemetry collector is unreachable | 3 min |
| systemd restarted celestia-appd, Fibre or tmkms more than 2 times in 30 minutes (crash loop) | at once |
| The celestia-appd unit (validator or backup node) or the tmkms unit is not loaded at all | 2 min |

A stopped Fibre unit is no ServiceDown: on a host with a standby Fibre, stopped is the normal state. [FibreDown](#fibredown) pages when no Fibre unit is active anywhere, and [FibreBothActive](#fibrebothactive) tells you when more than one is. tmkms is watched as a unit only when its host has `celestia_mon_kms_scrape: true`; without it nothing runs on the KMS host, and a tmkms that stopped shows on the validator side: [ValidatorNotSigning](#validatornotsigning) pages, and the signer's failures (`no_connection`) show on the dashboard. The crash loop rule counts only the restarts systemd makes under `Restart=`. While node_exporter is unreachable, the service and disk alerts for that host cannot fire.

**Check first.**

1. On the host in the alert, `systemctl status <unit>` and `journalctl -u <unit> -n 100`. After an upgrade or a config change, the last lines usually name the cause: a binary that does not match the network's app version, a config key, a full or read-only disk.
2. For "is not loaded": the unit was stopped and disabled, or `celestia_mon_units` names a different unit than the one that runs the service. A stopped, disabled unit has no metrics at all. Correct `celestia_mon_units` if needed and run `install.sh` again on that host and on the monitor.
3. For "is unreachable": from the monitor, `curl -s http://<host private address>:<port>/metrics | head`. If that fails while the service runs, check its listen address (for celestia-appd, `prometheus_listen_addr` in `config.toml`), a firewall on the host, and the private network between the hosts. `sudo celestia-mon-doctor` on the host checks the listen addresses and the node's metrics; on the monitor, check 11 names each target that is down and what to fix.

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#troubleshooting), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## NodeNotAdvancing

**What it means.** No node of this install, the validator node and the backup nodes, has committed a new block for 3 minutes: the highest height of all nodes did not change in 2 minutes, and that held for 1 more. It also fires when Prometheus has read no node's height for that long, so with one node a celestia-appd that is down pages here (after about 3 minutes). The nodes are stuck (often at an upgrade height), have lost sync or their peers, or the whole network has halted. While one node advances, the alert stays quiet: a node that stops alone is [NodeBehind](#nodebehind). The alert shows that nothing advances; it cannot tell whether a node that advances is at the network's head. Compare the height with an explorer or [status.celestia.org](https://status.celestia.org/) for that. It names no host.

**Severity.** page. While it fires, ValidatorNotSigning, SignaturesMissed, RemoteSignerProblem, FibreNotServing and NodeBehind are held back.

**Check first.**

1. [status.celestia.org](https://status.celestia.org/) and an explorer. If the whole chain stopped, coordinate with the other validators instead of fixing locally.
2. `systemctl status celestia-appd` and `journalctl -u celestia-appd -n 200` on each node: a stopped service, an upgrade height that needs a new binary, a consensus failure, an app hash mismatch.
3. Peers and sync: on the dashboard, the Consensus node row of each server's card shows whether the node signs, is in sync, behind (and by how many blocks), stalled, syncing or down, and its peers (with five servers or more, the Servers now table's Node and Peers columns); `sudo celestia-mon-doctor` (check 3) on each node shows whether its height advances and `catching_up`.

## NodeBehind

**What it means.** One node lags the others of this install: for 10 minutes it has been more than 20 blocks (`node_behind_blocks`) behind the highest node, or it has not advanced while another node did. This is how a backup node that could not take over signing is caught: after a tmkms repoint, a node that lags signs late or not at all. With one node it cannot fire. The alert names the host of the node that lags.

**Severity.** message. While NodeNotAdvancing fires, it is held back.

**Check first.**

1. The node's server card on the dashboard: its Consensus node row shows the blocks it is behind the highest node (or stalled, syncing, down) and its peers (with five servers or more, the Servers now table's Node and Peers columns); the "Block processing time (slowest 1%)" chart shows whether a node is too slow to keep up.
2. On that host, `sudo celestia-mon-doctor` (check 3: height and `catching_up`), then `journalctl -u celestia-appd -n 100`: block sync after a restart, too few peers, a full or slow disk.
3. On the monitor, `sudo celestia-mon-doctor` check 18 lists every node's height and lag, and which one signs.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## ValidatorNotSigning

**What it means.** The chain moves, but the validator's own key has not signed for more than 10 blocks, for 2 minutes, on any node of this install. Only the node the signer is connected to reports the last signed height, so the alert compares the highest height of all nodes with the highest last signed height, and it follows the signer from the validator node to a backup node. Causes: the remote signer or its link is down, the node the signer uses is down (its metrics go with it), the node runs with the wrong key, the validator is jailed or out of the active set, or the KMS is not compatible after an upgrade. It also fires when the node has not signed once since it started. When the validator node's host goes down and tmkms is pointed at a backup node, the alert resolves once that node signs. It names no host; with backup nodes, its description names each node Prometheus cannot reach, which may still be signing unseen. Signatures that go missing now and then, while others arrive, are [SignaturesMissed](#signaturesmissed).

**Severity.** page. While NodeNotAdvancing fires, it is held back. While it fires, SignaturesMissed is held back.

**Check first.** Never load the validator key on another machine because of this page alone: a node that Prometheus cannot reach may still be signing, and two signers of one key double-sign. When the description names a node Prometheus cannot reach, check on an explorer whether the validator misses blocks before you act.

1. With a remote signer, `journalctl -u tmkms -n 100` on the KMS host first: whether tmkms runs, which node it connects to, and why it fails. Then the link between that node and the KMS host, and the grey failure bars on the "Signing time" chart. If the node tmkms connects to is down, the way out is your tmkms repoint to a synced backup node ([mainnet layout](quickstart.md#mainnet-layout)); the Consensus node rows of the server cards show which backup node is in sync and how many blocks any other is behind.
2. Is the validator jailed or out of the active set? Look it up in an explorer or with `celestia-appd query staking validator <celestiavaloper address>`. If it is jailed, find out why before you unjail.
3. The key: `sudo celestia-mon-doctor` (check 4) on the validator host prints the consensus address the node signs with, from its `/status`. Compare it with your validator's consensus address on chain. With a remote signer, do not use `celestia-appd comet show-address` for this: it reads the local key file, not the signer.

## SignaturesMissed

**What it means.** The validator signs, but not every block: for 10 minutes, more than 5% (`signatures_missed_ratio`) of the blocks a node committed in the last hour carry no signature from the validator. Signatures that time out now and then, from load on the KMS host or loss on the link to it, show here, while ValidatorNotSigning stays quiet because the next signature arrives within 10 blocks. Each node that knows the validator's key counts the blocks committed without its signature, reading them from the chain's commits; the alert takes the highest share of any node, so it follows the signer after a repoint and names no host. The count starts at the first miss and starts over when the node restarts. The share says how often signatures are missed, not how close the validator is to being jailed.

**Severity.** message. When the signatures stop altogether, ValidatorNotSigning pages and this alert is held back; it is held back while NodeNotAdvancing fires too.

**Check first.**

1. With a remote signer, `journalctl -u tmkms -n 100` on the KMS host: timeouts and reconnects. The "Signing time" chart shows whether signatures come late (its lines) or fail (its grey bars).
2. The link between the node and the KMS host (packet loss, round trip) and the load on the KMS host; then the node itself: its peers and a slow disk ("Disk latency" panel), since a node that lags signs late.
3. How close to jailing: `celestia-appd query slashing signing-info <celestiavalcons address>` (`doctor` check 4 on the validator host prints the address) shows the blocks missed in the current window; `celestia-appd query slashing params` shows the window and the share it must sign.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/), [celestia-app v10 release notes: KMS requirements](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#key-management-systems-kms).

## ValidatorSigningTwice

Only with `celestia_mon_signer: remote`, and not with `celestia_mon_signer_multi_node: true`.

**What it means.** Two or more nodes of this install keep receiving votes or proposals signed with the validator's key from a remote signer; the description names them. Two signers with one key can sign two different votes for one height: a double sign, which the chain slashes and for which it tombstones the validator, so it can never sign again. Typically a second tmkms with the same key was started, for example on a standby KMS host. Each node counts only the signatures it got back from its signer, so a node that tmkms left counts none, although it keeps reporting the validator's signatures from the chain. A node counts once it has such signatures both in the last 2 minutes and in the 2 minutes before, so a tmkms repoint never matches, and the alert comes about 3 minutes after a second signer started. The alert names no host.

**Severity.** page: at any height where the two nodes vote differently, for a block and for nil for example, the validator double-signs.

**Check first.**

1. On each KMS host, `systemctl status tmkms` and `journalctl -u tmkms -n 50`: which tmkms runs and which node it connects to. Stop the one that should not sign.
2. On the dashboard, the Consensus node rows of the server cards show which nodes get the signatures now, and the Validator signs through lane under History shows since when.
3. If one tmkms signs through several nodes on purpose (several `[[validator]]` entries), set `celestia_mon_signer_multi_node: true` and run `install.sh` on the monitor: this alert is then left out.

**Links.** [tmkms](https://github.com/iqlusioninc/tmkms), [Cosmos SDK x/slashing](https://github.com/cosmos/cosmos-sdk/tree/main/x/slashing).

## RemoteSignerProblem

Only with `celestia_mon_signer: remote`.

**What it means.** One of two things, named in the summary:

- **Slower than 10 ms:** the median of the last 50 signatures has been above 10 ms for 15 minutes, on a node that got signatures from the remote signer in the last 5 minutes. Celestia requires the median signing latency to stay at or below 10 ms. A node that tmkms left keeps its last median; the alert ends for it within 5 minutes of the repoint.
- **Fails to sign proposals:** proposal signing failed in the last 6 hours for a reason other than a timeout or a lost connection. The KMS still signs votes, so ValidatorNotSigning stays quiet, but it cannot propose. Link blips are left out because every failed signature is retried up to 50 times and each attempt counts. The alert fires on the second failed proposal.

**Severity.** message: slower signing is a performance problem. When it turns into missed signatures, SignaturesMissed sends a message, and ValidatorNotSigning pages once no signature has arrived for more than 10 blocks.

**Check first.**

1. The "Signing time" chart: whether the median this alert watches is above its dashed 10 ms line, how slow the slowest vote signatures are, and whether signatures fail (its grey bars); the remote signer's card reads "Bad proposals" while proposals fail (with five servers or more, the Servers now table's Signer time column, on the node whose proposals fail).
2. Latency: the network path between the validator and the KMS host (round trip, same datacenter or not) and the load on the KMS host. Celestia recommends keeping the signer close, for example by colocation.
3. Proposal failures: the tmkms log on the KMS host around the failed proposals, and whether the tmkms version and its chain settings match the network's current app version (often the cause after an upgrade).

**Links.** [celestia-app v10 release notes: KMS requirements](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#key-management-systems-kms), [Fibre server guide: signing](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#signing), [tmkms](https://github.com/iqlusioninc/tmkms).

## FibreDown

Only with `celestia_mon_fibre_enabled: true`.

**What it means.** No Fibre runs anywhere: for 10 minutes no Fibre host (every host in the `fibre` group, active or standby) has reported the Fibre unit as active. Fibre is stopped or crashed on every host, the host of the active Fibre is down (its node_exporter goes with it) and no standby was started, or `celestia_mon_units.fibre` names the wrong unit. A stopped standby is normal, and a failover within 10 minutes pages nobody, so a short restart pages nobody either. The alert names no host; its description lists the Fibre hosts. When the monitor runs on the Fibre host and that host dies, the dead-man service tells you ([Watchdog](#watchdog)).

**From outside.** A running unit is not enough when nobody can reach it. With `celestia_mon_tensile` set on the monitor, FibreDown also pages, after 10 minutes, when:

- the endpoint registered on chain (`x/valaddr`) cannot be reached from outside: [Tensile](https://tensile.huginn.tech), a Fibre observer, dials it from outside your networks and needs TCP, TLS and your validator's identity to pass. This catches a closed port or firewall, an inbound outage at the provider, a routing or DNS fault, and a certificate your consensus key does not endorse. Only Tensile checks of the last 15 minutes count;
- Fibre runs on a host whose `celestia_mon_fibre_public_endpoint` is not the endpoint registered on chain: after a failover, `set-host` was not sent, and clients dial the old host;
- no Fibre host is registered on chain at all.

The `check` label tells the cases apart (none for the unit, `reachability`, `registration` with `reason` `wrong_host` or `not_registered`), and the dashboard names each in one word: "Not running", "Unreachable", "Wrong host", "Not registered".

celestia-mon-tensile on the monitor reads Tensile's verdict for your validator every minute and serves it as metrics; Tensile is used only for this check and for the blobs [FibreNotServing](#fibrenotserving) compares with Fibre's uploads. Set it up in the inventory, on the monitor host: `celestia_mon_tensile: {api: "https://tensile-mocha.huginn.tech/api/v1", validator: "<celestiavalcons1...>"}` (Tensile's API for your network; check 4 of `celestia-mon-doctor` on the validator prints the consensus address), and set `celestia_mon_fibre_public_endpoint` on every Fibre host. [TensileUnreadable](#tensileunreadable) tells you when the outside check stops.

**Severity.** page. While it fires, FibreNotServing is held back.

**Check first.**

1. On the monitor, `sudo celestia-mon-doctor` check 19 shows which Fibre host is active, if any, and check 11 whether each host's node_exporter answers.
2. On the Fibre host that should be active, `systemctl status <fibre unit>` and `journalctl -u <fibre unit> -n 100`. `sudo celestia-mon-doctor` there (check 6) shows the unit and its listening port. If that host is down, start the standby Fibre on another Fibre host and point `x/valaddr` at it with `celestia-appd tx valaddr set-host`, from your workstation or through an ops key with an authz grant, never with a key on a server ([mainnet layout](quickstart.md#mainnet-layout)); check 5 on the validator shows the registration. Before you start a standby, check 17 on its host shows whether its signer reaches the validator node.
3. For a planned Fibre stop with no standby started in its place, silence the alert by name first ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)): a host silence does not cover it.

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#troubleshooting).

## TensileUnreadable

Only with `celestia_mon_tensile` set and Fibre on.

**What it means.** For 15 minutes, then 10 more, celestia-mon-tensile has not read Tensile's API, Tensile has not checked your Fibre endpoint, or the service is down. The 15 minutes are FibreDown's: it counts only checks of the last 15 minutes, so from then on it cannot see an endpoint that is unreachable from outside. FibreNotServing sees no new blob and no new failed read meanwhile: `missed_uploads` keeps comparing the last blob Tensile reported, so a miss already seen still fires, but a later blob Fibre misses is not reported. The unit check still works. Tensile checks every endpoint about every 5 minutes, so the 15 minutes leave room for a late check.

**Severity.** message.

**Check first.** On the monitor, `journalctl -u celestia-mon-tensile -n 50`: a 404 means the validator address or the API's network is wrong in `celestia_mon_tensile`; a network error means the monitor cannot reach Tensile. If the service reads Tensile but its last check is old, Tensile itself is behind: look at its [status page](https://tensile.huginn.tech).

## FibreBothActive

Only with `celestia_mon_fibre_enabled: true`.

**What it means.** Fibre runs active-passive: one Fibre host serves, the others keep a stopped standby. For 5 minutes the Fibre unit has been active on more than one host, named in the description. Typically a standby was started and the old Fibre came back, or was never stopped.

**Severity.** message.

**Check first.**

1. On the validator host, `sudo celestia-mon-doctor` check 5 shows which host `x/valaddr` registers: that one stays active.
2. Stop Fibre on every other host (`sudo systemctl stop <fibre unit>`, and `sudo systemctl disable <fibre unit>` so it stays a standby after a reboot).
3. On the monitor, `sudo celestia-mon-doctor` check 19 confirms that one Fibre host is active.

## FibreNotServing

Only with `celestia_mon_fibre_enabled: true`; `missed_uploads` and `not_served` also need the outside check (`celestia_mon_tensile`).

**What it means.** Fibre runs but does not do its job. Only a host whose Fibre unit is active can fire it: a stopped standby sends no Fibre metrics and is not expected to. A network that sends no blobs never fires it: when no blob comes, nothing is missed. The `reason` label says how, and the dashboard says it in the words in brackets:

- `missed_uploads` ("Uploads missed"): a blob that assigned rows to your validator settled more than 5 minutes after Fibre last accepted an upload (or after Fibre started, if that is later), and that held for 5 minutes. A blob's publisher sends its rows to every validator the blob assigns rows to and skips none: it keeps sending to the slower ones after 2/3 of the voting power has signed, until the blob is in a block (celestia-app v10.4.0, `fibre/client_upload.go` and `fibre/client_put.go`), so a working Fibre has accepted that upload by the time the blob settles. [Tensile](https://tensile.huginn.tech) records which blobs assigned rows to your validator, and celestia-mon-tensile reads that every minute; the alert compares the newest with Fibre's own last accepted upload, taking Tensile's reading of a minute before, so that Fibre's report of the same upload (seconds later) has arrived. A blob that settled while no Fibre ran, or before Fibre restarted, does not count. It comes about 7 minutes after the missed blob settled, when Fibre accepted nothing in the 5 minutes before it. While Tensile is not read, the last blob it reported still counts ([TensileUnreadable](#tensileunreadable) tells you): an old reading can end the alert, never start it. From a Tensile that does not send the time of that blob (`signing.last_assigned_at`), celestia-mon-tensile takes the time of Tensile's newest block at the read at which the number of blobs assigned to your validator rose, while Tensile is in step with the chain: up to a minute late. Whether your signature made it into a blob is not checked: the blob settles once 2/3 of the voting power has signed, so a healthy validator is sometimes left out, although its Fibre still gets the upload.
- `uploads_failing` ("Uploads failing"): for 15 minutes Fibre has accepted no upload while uploads kept failing, so it signs nothing. Most often the signer: Fibre signs every accepted upload through the node at its signer address, and after tmkms is pointed at another node, a Fibre that still dials the old node fails every upload while its unit stays active and its port answers, so FibreDown and an external port check stay quiet. The dashboard's "Fibre signs through" turns amber as soon as Fibre's last signatures went through another node than the validator's, before any blob comes. No alert backs that amber: after a repoint on a network that sends no blob, Fibre's last signatures stay on the old node for up to an hour, even when Fibre already dials the right one. When blobs come and Fibre still dials the old node, this alert fires.
- `not_served` ("Rows not served"): Tensile read rows of a blob your validator signed for, while Fibre must still keep them (the retention window), and Fibre did not return them: a client would not get your share of that blob. Tensile reads each blob once, late in the window, from one place, so this comes hours after the blob, and one failed read alone can be a blip between Tensile and your host: the alert needs a second failed read within Tensile's last 24 hours. It fires at once at the read that makes two or more, and resolves an hour after the last such read.
- `no_telemetry` ("No Fibre data"): the Fibre unit has been active for 30 minutes but no Fibre metrics arrive. Fibre's OTLP endpoint is not set, or the collector is not receiving.

Without the outside check nothing on your hosts tells a network that sends no blobs from a Fibre that receives none, so no alert covers that case: the dashboard's Last accepted upload shows when Fibre last took one, and Tensile shows whether the network carries blobs. Set up `celestia_mon_tensile` to be told.

Possible causes: the host is not registered or not reachable from outside, the validator is not bonded, the node is not synced, the signer is unreachable or cannot sign raw bytes, or the listener died while the process kept running.

**Severity.** message: not serving Fibre carries no on-chain penalty. FibreDown pages when Fibre itself is down.

**Check first.**

1. For `missed_uploads`: Tensile's page for your validator lists the recent blobs. `journalctl -u <fibre unit> -n 100` shows the uploads Fibre received and why any failed; no upload lines at all point at the registration (`doctor` check 5 on the validator host) or at a port that publishers cannot reach.
2. For `not_served`: `journalctl -u <fibre unit>` for "failed to retrieve blob shard" (a store error; a shard Fibre does not hold is logged only at debug level), the Fibre store's disk (read-only, full, I/O errors in `journalctl -k`), and Failed clean-ups on the dashboard.
3. For `no_telemetry`: `sudo celestia-mon-doctor` on the Fibre host (check 7) shows where the unit sends its metrics, and check 13 on the monitor shows whether they arrive. Fibre needs `--otel-endpoint http://127.0.0.1:9915` or `FIBRE_OTEL_ENDPOINT` ([quickstart step 6](quickstart.md#6-point-fibre-at-its-collector)).
4. Registration and reachability: `sudo celestia-mon-doctor` on the validator host (check 5) shows the registered `host:port`; on the Fibre host, check 6 shows the unit and its listening port. From outside, a TCP connection to the registered `host:port` must succeed; [Tensile](https://tensile.huginn.tech) shows your server from outside. If the port does not answer while the unit is active, read `journalctl -u <fibre unit> -n 100` and restart Fibre at a moment you choose.
5. The validator is bonded and the node is synced (Fibre logs "derived storage budget is 0" when it finds no stake), and the signer supports `SignRawBytes` (`raw_bytes` failures add to the grey failure bars on the "Signing time" chart).
6. For `uploads_failing`: `journalctl -u <fibre unit> -n 100` names the error. If tmkms was pointed at another node, point Fibre's `--signer-grpc-address` (or `signer_grpc_address`) at that node's privval gRPC and restart Fibre ([mainnet layout](quickstart.md#mainnet-layout)); `sudo celestia-mon-doctor` checks 17 (Fibre's signer) and 20 (where the node's signer ports listen) show both ends.

**Links.** [Fibre server guide: server runs but no uploads arrive](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#server-runs-but-no-uploads-arrive), [registration](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#registration), [tracing and metrics](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#tracing--metrics).

## DiskLow

**What it means.** A disk you listed in `celestia_mon_data_mounts` or `celestia_mon_fibre_mount` is filling up or turned read-only. A node whose data disk turns read-only stops working, and the validator can be jailed for downtime; a backup node whose disk fills cannot take over signing.

**Severity.** page or message, by disk and condition:

| Disk | Condition | Severity |
|---|---|---|
| Data mounts on the celestia-appd hosts (validator and backup nodes) and the KMS host | read-only, or less than 5% free | page |
| Data mounts on every watched host | less than 10% free for 10 minutes | message |
| Data mounts on a Fibre host that runs no celestia-appd | read-only | message |
| Fibre store | read-only | message |
| Fibre store | the space the storage budget still needs is more than the free space, for 30 minutes | message |
| Fibre store, when Fibre reports a budget of 0 (no limit) or no budget | less than 10% free for 10 minutes | message |

The Fibre store has no percentage rule while a budget is set: Fibre keeps its occupancy near the budget at full load, which is normal. A Fibre store that is also a data mount of a celestia-appd host (a standby Fibre on the validator's disk, for example) follows the data mount rows instead, and keeps the budget row.

**Check first.**

1. Read-only: `journalctl -k` or `dmesg` for I/O or filesystem errors. The disk may be failing; find out why before you remount it read-write.
2. Free space: `du -xh --max-depth=2 <mount> | sort -h | tail` shows what grew. On the node, check pruning in `app.toml` and blockstore compaction in `config.toml`.
3. Fibre store: the "Fibre disk and budget" panel shows each Fibre store's free space against the room the storage budget still needs. Grow the disk or move other data off it. Celestia recommends separate disks for Fibre and celestia-app data.

**Links.** [celestia-app v10 release notes: Fibre](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#fibre), [blockstore compaction](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/docs/release-notes/release-notes.md#blockstore-compaction).

## UpgradeSignalMissing

Only with `celestia_mon_signal` set.

**What it means.** Celestia moves to a new app version through x/signal: validators signal the next version, and once 5/6 of the voting power has signalled it, anyone can schedule the upgrade, which then happens a fixed number of blocks later (about a week on mainnet). This alert says that such a round is open, some voting power has signalled the next version, and for 10 minutes your validator has not signalled it, or has signalled another version. Once the upgrade is scheduled, x/signal accepts no more signals: the page ends, and [UpgradeScheduled](#upgradescheduled) says that your validator did not signal. Your validator's signal is read from chain state, so a signal sent through an authz grant counts too. The alert names no host.

celestia-mon-signal on the monitor reads x/signal every minute through a node's CometBFT RPC and serves it as metrics: the app version, the voting power that signalled the next version against the 5/6 threshold, the scheduled upgrade with its height and the time until it at the measured block time, and the version your validator signalled. It sends no transaction and needs no key. Set it up in the inventory, on the monitor host ([quickstart](quickstart.md#upgrade-signalling)): `celestia_mon_signal: {chain_id: "celestia", rpc: ["<CometBFT RPC URL>", ...], validator: "<celestiavaloper1...>"}`. The RPCs are tried in order, and one that serves another network than `chain_id`, or whose node is catching up or has made no block for 2 minutes, is skipped. A collector or RPC outage of up to 7 days does not end the page: the upgrade alerts use the last reading of the past 7 days, and [SignalUnreadable](#signalunreadable) reports the outage.

**Severity.** page. Until your signal is on chain, PagerDuty keeps the incident open and the chat channels get the alert again every 4 hours.

**Check first.**

1. Upgrade celestia-appd on every node, the validator and each backup node, to a release that supports the version in the alert, at a moment you choose: Celestia asks validators to upgrade the binary before they signal. The release notes and Celestia's announcements ([CelestiaAnnouncement](#celestiaannouncement)) name the release.
2. Signal with the validator's account key, kept on your workstation or a Ledger as for `set-host` ([mainnet layout](quickstart.md#mainnet-layout)): `celestia-appd tx signal signal <version> --from <validator account key> --node <RPC of a synced node>`. An ops key can send it through an authz grant, with the same three commands as for `set-host` there: the grant with `--msg-type=/celestia.signal.v1.MsgSignalVersion`, then `celestia-appd tx signal signal <version> --from <validator account address> --generate-only > signal.json` and `celestia-appd tx authz exec signal.json --from <ops key>`.
3. `celestia-appd query signal tally <version> --node <RPC of a synced node>` shows the voting power that has signalled. The alert resolves a few minutes after your signal is in a block.

x/signal accepts a signal for any version at or above the current one, so one validator that signals early opens a round before a release exists. If Celestia's announcements name no release for the version yet, silence UpgradeSignalMissing until they do (`doctor` check 21 lists that silence while it holds the page back).

**Links.** [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/), [x/signal](https://github.com/celestiaorg/celestia-app/tree/v10.4.0/x/signal).

## UpgradeScheduled

Only with `celestia_mon_signal` set.

**What it means.** At least 5/6 of the voting power signalled the next app version, and the upgrade is scheduled at a height. The message names the version, the height, the time until it at the block time measured over the last 1000 blocks, and whether your validator signalled. Every node of this install must run a celestia-app release that supports that version before the height, or it stops there. It names no host.

**Severity.** message, sent when the upgrade is scheduled. Its "resolved" message means the chain has switched to the new version. On mainnet an upgrade is scheduled about a week ahead, so a message still firing after 4 days is sent once more, with the time left then.

**Check first.** Plan the binary upgrade of every node before the height, and the restart of each one with a host silence ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)). If the message says your validator did not signal, nothing can be signalled for this version any more; upgrade the nodes all the same. [UpgradeSoon](#upgradesoon) reminds you a day ahead.

## UpgradeSoon

Only with `celestia_mon_signal` set.

**What it means.** The scheduled upgrade is less than 24 hours away at the measured block time. A node that does not run a celestia-app release that supports the new version stops at the upgrade height, and with the validator node the validator stops signing. It names no host.

**Severity.** message, sent once. Once sent, it stays until the chain has switched, also when slower blocks move the time left back above 24 hours; its "resolved" message means the chain has switched to the new version.

**Check first.** On every node of this install, the validator and each backup node, confirm that the celestia-app release it runs supports the target version (`celestia-appd version`, and the release notes). After the height, the Consensus node rows of the server cards and NodeNotAdvancing show whether the nodes moved on.

## SignalUnreadable

Only with `celestia_mon_signal` set.

**What it means.** For 15 minutes, then 10 more, celestia-mon-signal has read none of the RPCs in `celestia_mon_signal.rpc`, or the service is down. The upgrade alerts keep their last reading meanwhile: a page that fires stays, but a new signalling round would not page, and a scheduled upgrade would not be announced.

**Severity.** message.

**Check first.** On the monitor, `journalctl -u celestia-mon-signal -n 50`: each line names the RPC that failed and why. An RPC that serves another network than `celestia_mon_signal.chain_id` is skipped (a Mocha RPC in a mainnet setting, or the reverse), and so is one whose node is catching up, has made no block for 2 minutes, or answers `/abci_info` at an older height than `/status` (`behind`: a node that is syncing, stalled, or halted at an upgrade height without the new release, or a stalled node among those behind a provider's URL). A `query` error is an x/signal query the node refused, or a signal store that does not hold the height of the read (behind a provider's URL, a node a block behind the one that gave the height): that read is skipped, never taken for a validator that has not signalled. A network error means the monitor cannot reach the RPC. A failed measurement of the block time (`block_time`) does not stop the reads: the one measured last stays. A service that does not start names its setting there too. Add a second RPC to the list so one outage does not stop the reads, and run `install.sh` on the monitor again.

## MonitoringBroken

**What it means.** Prometheus failed to evaluate one or more alert rules in the last 10 minutes, so some alerts may not fire. It fires at once and clears 10 minutes after the last failure. The alert names the monitor host.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-prometheus -n 100`: each "Evaluating rule failed" line names the rule and the error.
2. Prometheus's rules page on the monitor (`http://127.0.0.1:9912/rules`, through an SSH tunnel from your machine, as user `celestia-mon` with the password in `/etc/celestia-mon/api-password`) shows the failing rule and its last error.
3. Free space for Prometheus's data (`df -h /var/lib/celestia-mon/prometheus`), and `sudo celestia-mon-doctor` on the monitor: checks 11 and 12 show whether Prometheus answers and which rule metrics have no series, and check 15 whether the disk holds what Prometheus needs (retention size x 1.5 + 2 GiB).

## NotificationsFailing

**What it means.** Alertmanager could not deliver a page or message notification through one integration (Telegram, Discord, Slack, PagerDuty or a `webhook` receiver) in the last 15 minutes. There is one alert per integration, named in the summary, and it goes to the message receivers, so a broken integration is reported through the others. It fires at once and clears 15 minutes after the last failure. Failed heartbeats to the dead-man service are left out: they are [DeadmanFailing](#deadmanfailing). The Watchdog also stops for an hour after such a failure, so the dead-man service reports it too, also when no other receiver works.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-alertmanager -n 100`: each failed notification names the receiver and the error, for example a rejected bot token, a removed chat, a deleted Discord or Slack webhook, or an invalid PagerDuty routing key. A Telegram send that failed on the network (DNS, a timeout, TLS) is not in the journal: Alertmanager would log it with the request URL, which holds the bot token, so journald drops such lines. For Telegram, read `alertmanager_notifications_failed_total{integration="telegram"}` in Prometheus (its `reason` label) and this alert instead. If a journal of the monitor was ever shared, revoke the bot token in BotFather and put the new one in the vault file.
2. Fix the receiver in the vault file (`sudo ./install.sh --edit-vault <file>` on the monitor) and run `install.sh` on the monitor again; it sends a test message to the chat receivers.
3. Send a test alert of each severity ([quickstart step 10](quickstart.md#10-check-that-alerts-reach-you)) and check that it arrives everywhere you expect.

## DeadmanFailing

**What it means.** Alertmanager could not deliver the heartbeat to the dead-man service in `celestia_mon_deadman_webhook_url` in the last 15 minutes: the URL is wrong (a typo, a deleted check), the service rejects it, or the monitor cannot reach the service. It fires at once and clears 15 minutes after the last failure. The alert names the monitor host. A dead-man check that has never received a heartbeat may never alert, so a URL that was wrong from the start shows only here.

**Severity.** message. Failed heartbeats do not stop the Watchdog.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-alertmanager -n 100`: each failed heartbeat names the `deadman` receiver and the error, for example a 404 for a wrong URL. `sudo celestia-mon-doctor` there (check 22) counts the heartbeats sent and failed in the last 10 minutes.
2. Compare `celestia_mon_deadman_webhook_url` in the vault file with the ping URL your dead-man service shows, fix it, and run `install.sh` on the monitor again.
3. Outbound HTTPS from the monitor to the dead-man service.

## CelestiaAnnouncement

**What it means.** Celestia posted a new message in one of its announcement channels on Discord: usually a network upgrade with its height and time, or an incident. In Telegram and Discord the notification is the announcement itself: the channel it came from (for example `Celestia #mainnet-announcements`) as its title, then the message as text, role pings and Discord markup removed (up to 3500 characters). PagerDuty and webhooks also get a link to the original. Each announcement is sent once, as soon as it is read (within a minute). The chat channels get no "resolved" message for it; the PagerDuty incident closes by itself 30 minutes later.

It is optional. celestia-mon-announcements, a small service on the monitor host, reads one or more channels on your own Discord server that follow Celestia's announcement channels ("Follow" on the channel in Celestia's Discord), with a bot of yours, and posts each new message to Alertmanager. Only what a followed channel posted counts: messages that people, bots or webhooks write into that channel are passed over. The first read of a channel only notes its newest message, so nothing older is sent. Set it up on the monitor:

1. On your Discord server, create a channel and make it follow Celestia's mainnet and testnet announcement channels (one channel can follow several). Keep your alert receivers out of it: a `discord` receiver posts into a channel of its own.
2. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications) (or reuse one): turn on **Message Content Intent**, invite it to your server, and give it **View Channel** and **Read Message History** in that channel. Copy its token.
3. In the inventory, on the monitor host: `celestia_mon_announcements: [{channel_id: "<channel ID>", severity: page}]` (Developer Mode, then right-click the channel, Copy Channel ID). `page` reaches PagerDuty and every chat channel, `message` the chat channels only. A channel that follows only testnet announcements is best set to `message`, so routine testnet news does not page.
4. In the monitor's vault (`sudo ./install.sh --edit-vault <file>`): `celestia_mon_discord_bot_token: "<token>"`. Then run `install.sh` on the monitor.

**Severity.** The one you set per channel (`page` by default).

**Check first.** Read the announcement: plan the upgrade it names before its height, and silence that host's alerts for the restart ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)). Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page and [status.celestia.org](https://status.celestia.org/) carry the details.

## AnnouncementsUnreadable

**What it means.** celestia-mon-announcements has not read its Discord channel for 15 minutes, or the service is down, for 10 minutes more. New announcements would not reach you. It exists only when `celestia_mon_announcements` is set.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-announcements -n 50`. Each failed read names its cause and fix: Discord refused the token (401: a reset or wrong token, fix it in the vault), the bot may not read the channel (403: give it View Channel and Read Message History there), the channel is unknown (404: the ID, or the bot is not on that server), or Discord is unreachable.
2. `systemctl status celestia-mon-announcements` when the service is down.
3. After a fix in the vault, run `install.sh` on the monitor again.

## Watchdog

**What it means.** The Watchdog always fires while monitoring works. Alertmanager sends it every 1 to 2 minutes, only to the dead-man service in `celestia_mon_deadman_webhook_url`, which alerts you when the heartbeats stop. You never receive it from celestia-validator-monitoring itself. The heartbeats stop:

- when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network;
- for one hour after a page or message notification failed (Telegram, Discord, Slack, PagerDuty or a `webhook` receiver), so a broken alert channel reaches you through the dead-man service. Failed heartbeats to the dead-man URL itself do not count: [DeadmanFailing](#deadmanfailing) reports them.

Connect the dead-man check to PagerDuty and to email or another channel than your alert chat. A dead monitor host, or a whole data centre gone, then still rings your phone, and when the alert chat is what broke, the dead-man service is the one left to tell you. If the dead-man service alerts you, assume that no other celestia-validator-monitoring alert can reach you until you find the cause.

When the monitor runs on the Fibre host, as in the [mainnet layout](quickstart.md#mainnet-layout), a dead-man alert also means that the active Fibre and the backup node on that host are probably down, and nothing else can tell you so: start the standby Fibre on the validator host and send `set-host` ([FibreDown](#fibredown)).

**Severity.** heartbeat.

**Check first.**

1. Is the monitor host up and reachable? If the monitor is also the validator host, the validator may be down too.
2. On the monitor, `systemctl status celestia-mon-prometheus celestia-mon-alertmanager` and `journalctl -u celestia-mon-alertmanager -n 100`; `sudo celestia-mon-doctor` (check 22) shows whether heartbeats went out in the last 10 minutes. A failed notification shows there with its receiver and error, for example a rejected bot token or a removed chat (a Telegram failure on the network does not: see [NotificationsFailing](#notificationsfailing)); so do failed heartbeats to the dead-man URL.
3. Outbound HTTPS from the monitor to the dead-man service and to the receivers, and the URL in `celestia_mon_deadman_webhook_url`.

The heartbeat resumes one hour after the last failed notification. To test the chain end to end, stop Alertmanager on the monitor for longer than the dead-man period, then start it again (see the [quickstart](quickstart.md#8-set-up-the-dead-man-service)).
