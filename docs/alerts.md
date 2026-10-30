# Alerts

celestia-validator-monitoring has twenty-five alerts and a heartbeat, and can pass Celestia's announcements on as alerts. Each carries a severity:

- **page**: someone should look now;
- **message**: look during working hours;
- **heartbeat**: the Watchdog, sent only to your dead-man service.

The dashboard calls a page alert **Urgent** (red) and a message alert **Warning** (amber).

With upgrade signalling on, the validator's votes on governance proposals are watched too: [GovVoteMissing](#govvotemissing) says when a proposal nears the end of its voting period without the validator's vote. Nothing is sent when a vote opens; what a proposal changes comes through its forum post and Celestia's announcements ([CelestiaAnnouncement](#celestiaannouncement)).

The validator role can move to a backup node (tmkms pointed at it), and the Fibre role to a standby Fibre host. The alerts about those roles therefore watch the install as a whole and read which node signs and which Fibre runs from the metrics, never from the inventory: NodeNotAdvancing, ValidatorNotSigning, SignaturesMissed, ValidatorSigningTwice, FibreDown and FibreBothActive name no host, and neither do the Watchdog, the network upgrade alerts (UpgradeSignalMissing, UpgradeScheduled, UpgradeSoon), GovVoteMissing, which names the proposal, and MonitorCannotReach's page, which is about every host at once. Every other alert names the host it is about; NodePeersLow's page reads from the metrics which node signs.

| Alert | Severity | Catches |
|---|---|---|
| [ServiceDown](#servicedown) | message | celestia-appd, tmkms or a collector down or not loaded, celestia-mon-blackbox down, node_exporter unable to read the disks, or a Celestia service crash looping |
| [NodeNotAdvancing](#nodenotadvancing) | page | No node committed a new block for 3 minutes, or no node reports a height: nodes stuck, out of sync or without peers, celestia-appd down, tmkms down with no other node following the chain, or the chain halted |
| [NodeBehind](#nodebehind) | message | One node lags the others: a backup node that could not take over signing, or the node tmkms left |
| [NodePeersLow](#nodepeerslow) | message; page for the node the validator signs through | A node with fewer than 2 peers for 5 minutes; the node the validator signs through with none for 1 minute: it receives no blocks or votes, and the validator misses every block |
| [ValidatorNotSigning](#validatornotsigning) | page | The chain moves but the validator's key signs on no node |
| [SignaturesMissed](#signaturesmissed) | message | The validator signs, but misses more than 5% of the blocks of the last hour |
| [ValidatorSigningTwice](#validatorsigningtwice) | page | Two nodes keep getting the validator's signatures from a remote signer, for about 3 minutes: a double sign can follow (remote signer only) |
| [RemoteSignerProblem](#remotesignerproblem) | message | Remote signer too slow, or unable to sign proposals |
| [FibreDown](#fibredown) | page | No Fibre unit that stays active on any Fibre host for 10 minutes, a crash loop included; with the outside check, also the registered endpoint unreachable from outside, not registered, or not the host where Fibre runs |
| [FibreBothActive](#fibrebothactive) | message | Fibre active on more than one host for 5 minutes |
| [FibreNotServing](#fibrenotserving) | message, or page when not ready for 10 minutes | Fibre running but missing the uploads of blobs sent to the validator or not serving rows it signed for (with the outside check), failing every upload that passes its checks (a signer that does not sign, a full storage budget, or a store that fails), reporting itself not ready for uploads, or sending no metrics |
| [DiskLow](#disklow) | page or message | A watched disk filling up or turned read-only |
| [RaidDegraded](#raiddegraded) | message, again once a day while it holds | An md software RAID array running on fewer disks than it needs for 5 minutes: a disk failed or dropped out, or a rebuild still runs |
| [ClockNotSynced](#clocknotsynced) | message | A server's clock not synchronised for an hour: chrony or systemd-timesyncd stopped correcting it |
| [MonitorCannotReach](#monitorcannotreach) | message; page when no host answers (monitor on a host of its own) | For 3 minutes the monitor has reached none of a host's metrics endpoints: the host is down, or the link between it and the monitor is |
| [UpgradeSignalMissing](#upgradesignalmissing) | message; page once more than 1/3 of the voting power has signalled | Validators signal the next app version, and yours has not (only with upgrade signalling on) |
| [UpgradeScheduled](#upgradescheduled) | message, when scheduled (again after 4 days while it waits) | A network upgrade scheduled: version, height, when, and whether yours signalled (only with upgrade signalling on) |
| [UpgradeSoon](#upgradesoon) | message, sent once | The scheduled upgrade less than a day away (only with upgrade signalling on) |
| [GovVoteMissing](#govvotemissing) | message; page with less than a day left (mainnet by default) | A governance proposal less than 2 days from the end of its voting period, and the validator has not voted on it (only with upgrade signalling on) |
| [MonitoringBroken](#monitoringbroken) | message | Prometheus failing to evaluate alert rules |
| [NotificationsFailing](#notificationsfailing) | message | Alertmanager unable to deliver through one of your receivers |
| [DeadmanFailing](#deadmanfailing) | message | Alertmanager unable to deliver the heartbeat to your dead-man service |
| [TensileUnreadable](#tensileunreadable) | message | The outside check of the Fibre endpoint stopped (only with the outside check on) |
| [SignalUnreadable](#signalunreadable) | message | Upgrade signalling or the governance votes no longer read (only with upgrade signalling on) |
| [AnnouncementsUnreadable](#announcementsunreadable) | message | Celestia's announcements no longer read (only with [CelestiaAnnouncement](#celestiaannouncement) on) |
| [CelestiaAnnouncement](#celestiaannouncement) | page or message, your choice | A new message in Celestia's announcement channels on Discord (optional) |
| [Watchdog](#watchdog) | heartbeat | Monitoring itself down, Prometheus unable to store what it scrapes, or an alert that could not be delivered (through the dead-man service) |

## Where alerts go

`celestia_mon_receivers`, in the monitor's secrets (`sudo ./install.sh --edit-secrets` there) or its vault, is one list of receivers: any number, several of one type allowed. The type decides what each receiver gets:

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

**One cause, one notification.** While NodeNotAdvancing fires, ValidatorNotSigning, SignaturesMissed, RemoteSignerProblem, FibreNotServing and NodeBehind are held back on every host. While NodePeersLow pages, NodeNotAdvancing, ValidatorNotSigning and SignaturesMissed are held back, and so are that host's NodeBehind and NodePeersLow message: a node without peers stops signing and falls behind, and where no other node advances, no node does. While ValidatorNotSigning fires, SignaturesMissed and FibreNotServing `not_ready` are held back: a validator out of the active set or a signer that does not answer also fails Fibre's own checks. While FibreDown fires, FibreNotServing is held back. While FibreBothActive fires, FibreNotServing `missed_uploads` is held back: only the Fibre the chain names gets uploads. While a host's Fibre unit crash loops, that host's FibreNotServing messages are held back but not a page: a message never holds a page, the crash loop alert outlasts the loop by up to 30 minutes, and a Fibre that has answered NOT_SERVING for 10 minutes is running. While its collector (celestia-mon-otelcol) is down, every reason is held back but `not_ready` and `not_served`, which reach the monitor without it: it asks Fibre directly and reads Tensile. While UpgradeSignalMissing pages, its message is held back, so the chat repeat comes once. While the monitor reaches none of a host's metrics endpoints, [MonitorCannotReach](#monitorcannotreach) names that host once instead of a ServiceDown for each endpoint; where the monitor has a host of its own and reaches no host at all, it pages once, and NodeNotAdvancing and FibreDown's unit check, which read the hosts' metrics, wait for a host that answers. Alertmanager treats a held-back alert that has ended as handled. Prometheus repeats an alert's end for 15 minutes, so an alert already sent still gets its resolved notification when its cause ends within that time. If the cause lasts longer, none is sent: chat shows no RESOLVED, and PagerDuty's incident stays open until you close it or the service's auto-resolve does. Until 4 hours after that alert's last notification, the same alert firing again sends nothing.

**Thresholds** below are the defaults. Change them in `celestia_mon_thresholds` in your inventory (list every key) and run `install.sh` on the monitor again; the dashboard's threshold lines follow. The durations named with each alert are fixed in the rules.

| Key | Default | Used by |
|---|---|---|
| `not_signing_blocks` | 10 | ValidatorNotSigning |
| `signatures_missed_ratio` | 0.05 | SignaturesMissed |
| `node_behind_blocks` | 20 | NodeBehind, SignaturesMissed |
| `peers_message_below` | 2 | NodePeersLow (message) |
| `peers_page_below` | 1 | NodePeersLow (page). Keep it at 1: the install stops on any other value, because the page holds back ValidatorNotSigning, NodeNotAdvancing and SignaturesMissed, which is right only for a node that receives nothing, and at 0 it would never page. The dashboard names the page "No peers" |
| `signer_median_seconds` | 0.010 | RemoteSignerProblem |
| `disk_page_free_ratio` | 0.05 | DiskLow (page) |
| `disk_message_free_ratio` | 0.10 | DiskLow (message) |
| `crashloop_restarts` | 2 | ServiceDown (crash loop) |

To see what is firing now, open Prometheus (`http://127.0.0.1:9912/alerts`) or Alertmanager (`http://127.0.0.1:9913`). Both listen on the monitor's loopback only: use them on the monitor, or through an SSH tunnel from your machine (`ssh -N -L 9912:127.0.0.1:9912 -L 9913:127.0.0.1:9913 <user>@<monitor>`, then the same URLs with `localhost`). Both ask for a login, which your browser prompts for: user `celestia-mon` and the password the install generated on the monitor (`sudo cat /etc/celestia-mon/api-password`). Open the tunnel only while you use it: while it is open and the browser remembers the login, other web pages in that browser can send requests to these ports too.

## Before planned restarts and upgrades

Silence the host on the monitor first, so a planned restart pages nobody. `amtool` comes with Alertmanager, in `/opt/celestia-mon/alertmanager-<version>/`. Run it as root (`sudo -i`): its login to Alertmanager is in a file only root can read.

```sh
amtool=$(ls /opt/celestia-mon/alertmanager-*/amtool)
am="--alertmanager.url=http://127.0.0.1:9913 --http.config.file=/etc/celestia-mon/alertmanager/amtool-http.yml"
$amtool $am silence add host=<host> --duration=30m --comment="planned restart of <host>"
```

It prints the silence's ID. Every alert labelled with that host is held back until the silence ends; the Watchdog keeps running. When you stop a node that a Fibre on another host reads, silence that Fibre host too: its Fibre cannot restart until the node is back. The alerts about the whole install name no host, so a host silence does not cover them. Silence them by name when the planned work stops what they watch everywhere: FibreDown when Fibre stops and no standby starts in its place; NodeNotAdvancing when the only node restarts (one celestia-appd host, no backup node), when tmkms stops for minutes on an install with one node, or when tmkms is pointed at another node, since that node restarts for it and the node tmkms left commits a block only every few minutes; ValidatorNotSigning when the node the signer uses restarts, or tmkms restarts or is pointed at another node; and MonitorCannotReach when the monitor has a host of its own and the work stops every host it reaches (its only remote host, or all of them):

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
| celestia-mon-blackbox on the monitor is unreachable, so nothing reads Fibre's readiness (the alert names the monitor; the dashboard reads "Probe stopped") | 3 min |
| node_exporter answers but its filesystem collector fails, so DiskLow sees no disk on that host (the dashboard reads "No disk data"; `doctor` check 10 gives the fix) | 3 min |
| systemd restarted celestia-appd, Fibre or tmkms more than 2 times in 30 minutes (crash loop; for Fibre, not in the 40 minutes after its host booted) | at once |
| The celestia-appd unit (validator or backup node) or the tmkms unit is not loaded at all | 2 min |

A stopped Fibre unit is no ServiceDown: on a host with a standby Fibre, stopped is the normal state. [FibreDown](#fibredown) pages when no Fibre unit stays active anywhere, a crash loop included (active under a minute at a time), and [FibreBothActive](#fibrebothactive) tells you when more than one is active. tmkms is watched as a unit only when its host has `celestia_mon_kms_scrape: true`; without it nothing runs on the KMS host, and a tmkms that stopped shows on the validator side. With a backup node that follows the chain, [ValidatorNotSigning](#validatornotsigning) pages within about 3 minutes. With one node, or with tmkms signing through every node, the node stalls too: [NodeNotAdvancing](#nodenotadvancing) pages within about 4 minutes, and ValidatorNotSigning, which needs 10 more blocks, comes about an hour later and is held back while NodeNotAdvancing fires. The signer's failures (`timeout`) show on the dashboard. The crash loop rule counts only the restarts systemd makes under `Restart=`. A Fibre restarting in the 40 minutes after its host booted is not reported, because Fibre exits until its node answers; an upgrade that restarts both can still send one message, which clears 30 minutes after the last restart. While that message holds, the Fibre tile reads "Fibre crash". While node_exporter is unreachable, the service and disk alerts for that host cannot fire. When none of a host's endpoints answers (celestia-appd's metrics, node_exporter, the collector), the monitor cannot tell the services from the host or the link: [MonitorCannotReach](#monitorcannotreach) names the host once instead of these unreachable cases.

**Check first.**

1. On the host in the alert, `systemctl status <unit>` and `journalctl -u <unit> -n 100`. After an upgrade or a config change, the last lines usually name the cause: a binary that does not match the network's app version, a config key, a full or read-only disk.
2. For "is not loaded": the unit was stopped and disabled, or renamed after the install, so `celestia_mon_units` names a different unit than the one that runs the service. A stopped, disabled unit has no metrics at all. Each host may set its own `celestia_mon_units` (the whole map), and the alert names the unit of that host. Correct it in that host's entry and run `install.sh` again on that host, where the install stops while a name is not a unit systemd has loaded and names the units that run the service, and on the monitor.
3. For "is unreachable": from the monitor, `curl -s http://<host private address>:<port>/metrics | head`. If that fails while the service runs, check its listen address (for celestia-appd, `prometheus_listen_addr` in `config.toml`), a firewall on the host, and the private network between the hosts. `sudo celestia-mon-doctor` on the host checks the listen addresses and the node's metrics; on the monitor, check 11 names each target that is down and what to fix.

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/cmd/README.md#troubleshooting), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## NodeNotAdvancing

**What it means.** No node of this install, the validator node and the backup nodes, has committed a new block for 3 minutes: the highest height of all nodes did not change in 2 minutes, and that held for 1 more. It also fires when Prometheus has read no node's height for that long, so with one node a celestia-appd that is down pages here (after about 3 minutes). Where the monitor has a host of its own, that case needs a Celestia host that still answers: when the monitor reaches none, [MonitorCannotReach](#monitorcannotreach) pages instead, at the same time. The nodes are stuck (often at an upgrade height), have lost sync or their peers, or the whole network has halted. With a remote signer and no other node that follows the chain, a tmkms that stopped does the same: a node without its signer commits a block only every few minutes. While one node advances, the alert stays quiet: a node that stops alone is [NodeBehind](#nodebehind). The alert shows that nothing advances; it cannot tell whether a node that advances is at the network's head. Compare the height with an explorer or [status.celestia.org](https://status.celestia.org/) for that. It names no host.

**Severity.** page. It resolves 5 minutes after the last stall, so a block every few minutes does not resolve it. While it fires, ValidatorNotSigning, SignaturesMissed, RemoteSignerProblem, FibreNotServing and NodeBehind are held back. It is held back while NodePeersLow pages: when the node the validator signs through has no peers and no other node advances (one node, or the nodes it peers with stopped), that page names the cause.

**Check first.**

1. [status.celestia.org](https://status.celestia.org/) and an explorer. If the whole chain stopped, coordinate with the other validators instead of fixing locally.
2. With a remote signer, if the chain moves: `journalctl -u tmkms -n 100` on the KMS host.
3. `systemctl status celestia-appd` and `journalctl -u celestia-appd -n 200` on each node: a stopped service, an upgrade height that needs a new binary, a consensus failure, an app hash mismatch.
4. Peers and sync: on the dashboard, the Consensus node row of each server's card shows whether the node signs, is in sync, behind (and by how many blocks), stalled, syncing or down, and its peers (with five columns or more, counting a remote signer whose host is not scraped, the Servers now table's Node and Peers columns); `sudo celestia-mon-doctor` (check 3) on each node shows whether its height advances and `catching_up`.

## NodeBehind

**What it means.** One node lags the others of this install: for 10 minutes it has been more than 20 blocks (`node_behind_blocks`) behind the highest node, or it has not advanced while another node did. This is how a backup node that could not take over signing is caught: after a tmkms repoint, a node that lags signs late or not at all. The node tmkms left falls behind too, since it commits a block only every few minutes ([after a tmkms repoint](layouts.md#after-a-tmkms-repoint)). With one node it cannot fire. The alert names the host of the node that lags. A Fibre that reads that node (its `app_grpc_address`, as on `fibre-1` in the [mainnet layout](layouts.md#mainnet-layout)) lags with it: once the node's newest block is older than Fibre's `max_block_age` (2 minutes), Fibre's health check reports `chain_stalled`, and [FibreNotServing](#fibrenotserving) (`not_ready`) follows.

**Severity.** message. It is held back while NodeNotAdvancing fires, and while NodePeersLow pages for its host.

**Check first.**

1. The node's server card on the dashboard: its Consensus node row shows the blocks it is behind the highest node (or stalled, syncing, down) and its peers (with five columns or more, counting a remote signer whose host is not scraped, the Servers now table's Node and Peers columns); the "Block processing time (slowest 1%)" chart shows whether a node is too slow to keep up.
2. On that host, `sudo celestia-mon-doctor` (check 3: height and `catching_up`), then `journalctl -u celestia-appd -n 100`: block sync after a restart, too few peers, a full or slow disk.
3. On the monitor, `sudo celestia-mon-doctor` check 18 lists every node's height and lag, and which one signs.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/), [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/).

## NodePeersLow

**What it means.** A node of this install has too few peers (`cometbft_p2p_peers`, the peers its P2P layer is connected to now). The message: for 5 minutes a node, the validator node or a backup node, has had fewer than 2 peers (`peers_message_below`). Nodes it peers with are down or cannot reach it; while a peer is left the node still follows the chain. The page: for 1 minute the node the validator signs through has had no peers (`peers_page_below` is 1). It receives no blocks or votes, so the validator misses every block until a peer connects. Which node signs is read from the metrics as the dashboard reads it, over 15 minutes instead of 2: with a remote signer, a node that got votes or proposals signed by tmkms in that time; with a local key, a node whose last signed height changed in that time. A node cut off from the network stops signing within a round, so once the page fires it holds until the node has a peer again. There is no page while another node with peers signs (in the last 2 minutes): the validator signs through that node, after a tmkms repoint or with `celestia_mon_signer_multi_node`. A backup node without peers gets the message only: it cannot take over signing until it has peers and has caught up. The alert names the host of the node.

**Severity.** message; page for the node the validator signs through. While the page fires, NodeNotAdvancing, ValidatorNotSigning and SignaturesMissed are held back, and so are the same host's NodeBehind and NodePeersLow message: with one node, or when the nodes it peers with stopped, no node advances either.

**Check first.**

1. The nodes it peers with: whether they run and are in sync (their server cards on the dashboard, or `systemctl status celestia-appd` on their hosts), and whether this host reaches them on their P2P port (26656 by default). On the node's host, `curl -s http://127.0.0.1:26657/net_info` (or the address in `[rpc] laddr` of the node's `config.toml`) lists the peers it has now (`n_peers`, and each peer's `moniker` and `remote_ip`).
2. This host's network: a firewall rule or route changed, or the link between the hosts is down. `journalctl -u celestia-appd -n 200` shows the node's dial errors and the peers it dropped.
3. `persistent_peers` in the `[p2p]` section of the node's `config.toml`: each entry is `<node ID>@<address>:<port>`, and `celestia-appd comet show-node-id` on a peer's host prints its node ID. celestia-appd reads the list at start, so restart it after a change.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/).

## ValidatorNotSigning

**What it means.** The chain moves, but the validator's own key has not signed for more than 10 blocks, for 2 minutes, on any node of this install. Only the node the signer is connected to reports the last signed height, so the alert compares the highest height of all nodes with the highest last signed height, and it follows the signer from the validator node to a backup node. Causes: the remote signer or its link is down, the node the signer uses is down (its metrics go with it), the node runs with the wrong key, the validator is jailed or out of the active set, or the KMS is not compatible after an upgrade. It also fires when the node has not signed once since it started. With one node a stopped signer stalls the node too, and [NodeNotAdvancing](#nodenotadvancing) pages first. When the validator node's host goes down and tmkms is pointed at a backup node, the alert resolves once that node signs. It names no host; with backup nodes, its description names each node Prometheus cannot reach, which may still be signing unseen. Signatures that go missing now and then, while others arrive, are [SignaturesMissed](#signaturesmissed).

**Severity.** page. While NodeNotAdvancing fires or NodePeersLow pages, it is held back. While it fires, SignaturesMissed and FibreNotServing `not_ready` are held back.

**Check first.** Never load the validator key on another machine because of this page alone: a node that Prometheus cannot reach may still be signing, and two signers of one key double-sign. When the description names a node Prometheus cannot reach, check on an explorer whether the validator misses blocks before you act.

1. With a remote signer, `journalctl -u tmkms -n 100` on the KMS host first: whether tmkms runs, which node it connects to, and why it fails. Then the link between that node and the KMS host, and the grey failure bars on the "Signing time" chart. If the node tmkms connects to is down, the way out is your tmkms repoint to a synced backup node ([when a role moves](layouts.md#when-a-role-moves)); the Consensus node rows of the server cards show which backup node is in sync and how many blocks any other is behind.
2. Is the validator jailed or out of the active set? Look it up in an explorer or with `celestia-appd query staking validator <celestiavaloper address>`. If it is jailed, find out why before you unjail.
3. The key: `sudo celestia-mon-doctor` (check 4) on the validator host prints the consensus address the node signs with, from its `/status`. Compare it with your validator's consensus address on chain. With a remote signer, do not use `celestia-appd comet show-address` for this: it reads the local key file, not the signer.

## SignaturesMissed

**What it means.** The validator signs, but not every block: for 10 minutes, more than 5% (`signatures_missed_ratio`) of the blocks a node committed in the last hour carry no signature from the validator. Signatures that time out now and then, from load on the KMS host or loss on the link to it, show here, while ValidatorNotSigning stays quiet because the next signature arrives within 10 blocks. Each node that knows the validator's key counts the blocks committed without its signature, reading them from the chain's commits; the alert takes the highest share of any node, leaving out a node more than 20 blocks (`node_behind_blocks`) behind the highest, so it follows the signer after a repoint and names no host. The count starts at the first miss and starts over when the node restarts; the alert adds up its rise minute by minute and reads a drop as a restart, so the misses on both sides of a restart count and the drop adds none. The dashboard's Missed blocks reads the same expression. The share says how often signatures are missed, not how close the validator is to being jailed.

**Severity.** message. When the signatures stop altogether, ValidatorNotSigning pages and this alert is held back; it is held back while NodeNotAdvancing fires or NodePeersLow pages too.

**Check first.**

1. With a remote signer, `journalctl -u tmkms -n 100` on the KMS host: timeouts and reconnects. The "Signing time" chart shows whether signatures come late (its lines) or fail (its grey bars).
2. The link between the node and the KMS host (packet loss, round trip) and the load on the KMS host; then the node itself: its peers and a slow disk ("Disk latency" panel), since a node that lags signs late.
3. How close to jailing: `celestia-appd query slashing signing-info <celestiavalcons address>` (`doctor` check 4 on the validator host prints the address) shows the blocks missed in the current window; `celestia-appd query slashing params` shows the window and the share it must sign.

**Links.** [Celestia validator metrics and alerts](https://docs.celestia.org/operate/consensus-validators/metrics/), [celestia-app v10 release notes: KMS requirements](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/docs/release-notes/release-notes.md#key-management-systems-kms).

## ValidatorSigningTwice

Only with `celestia_mon_signer: remote`, and not with `celestia_mon_signer_multi_node: true`.

**What it means.** Two or more nodes of this install keep receiving votes or proposals signed with the validator's key from a remote signer; the description names them. Two signers with one key can sign two different votes for one height: a double sign, which the chain slashes and for which it tombstones the validator, so it can never sign again. Typically a second tmkms with the same key was started, for example on a standby KMS host. Each node counts only the signatures it got back from its signer, so a node that tmkms left counts none, although it keeps reporting the validator's signatures from the chain, a block every few minutes at most. A node counts once it has such signatures both in the last 2 minutes and in the 2 minutes before, so a tmkms repoint never matches, and the alert comes about 3 minutes after a second signer started. The alert names no host.

**Severity.** page: at any height where the two nodes vote differently, for a block and for nil for example, the validator double-signs.

**Check first.**

1. On each KMS host, `systemctl status tmkms` and `journalctl -u tmkms -n 50`: which tmkms runs and which node it connects to. Stop the one that should not sign.
2. On the dashboard, the Consensus node rows of the server cards show which nodes get the signatures now, and the Validator signs through lane under History shows since when.
3. If one tmkms signs through several nodes on purpose (several `[[validator]]` entries), set `celestia_mon_signer_multi_node: true` and run `install.sh` on the monitor: this alert is then left out.

**Links.** [tmkms](https://github.com/iqlusioninc/tmkms), [Cosmos SDK x/slashing](https://github.com/celestiaorg/cosmos-sdk/tree/v0.52.12/x/slashing).

## RemoteSignerProblem

Only with `celestia_mon_signer: remote`.

**What it means.** One of two things, named in the summary:

- **Slower than 10 ms:** the median of the last 50 signatures has been above 10 ms for 15 minutes, on a node that got signatures from the remote signer in the last 5 minutes. Celestia requires the median signing latency to stay at or below 10 ms. A node that tmkms left keeps its last median; the alert ends for it within 5 minutes of the repoint.
- **Fails to sign proposals:** proposal signing failed in the last 6 hours for a reason other than a timeout or a lost connection. The KMS still signs votes, so ValidatorNotSigning stays quiet, but it cannot propose. Timeouts and lost connections are left out, because every failed signature is retried up to 50 times and each attempt counts; a connection that closes in the middle of a request counts as `other` and can show. A refusal from the signer (`remote_error`) is not retried: the first one only creates the counter, so the alert fires on the second failed proposal. `unexpected_response` and `other` are retried and count per attempt, so one failed proposal can be enough. With `celestia_mon_signer_multi_node: true` it fires only when no node got a proposal signed in the last 6 hours: tmkms signs each proposal for the first node that asks and refuses the others.

**Severity.** message: slower signing is a performance problem. When it turns into missed signatures, SignaturesMissed sends a message, and ValidatorNotSigning pages once no signature has arrived for more than 10 blocks.

**Check first.**

1. The "Signing time" chart: whether the median this alert watches is above its dashed 10 ms line, how slow the slowest vote signatures are, and whether signatures fail (its grey bars); the remote signer's card reads "Bad proposals" while proposals fail (with five columns or more, counting a remote signer whose host is not scraped, the Servers now table's Signing time column, on the node whose proposals fail).
2. Latency: the network path between the validator and the KMS host (round trip, same datacenter or not) and the load on the KMS host. Celestia recommends keeping the signer close, for example by colocation.
3. Proposal failures: the tmkms log on the KMS host around the failed proposals, and whether the tmkms version and its chain settings match the network's current app version (often the cause after an upgrade).

**Links.** [celestia-app v10 release notes: KMS requirements](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/docs/release-notes/release-notes.md#key-management-systems-kms), [Fibre server guide: signing](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/cmd/README.md#signing), [tmkms](https://github.com/iqlusioninc/tmkms).

## FibreDown

Only with `celestia_mon_fibre_enabled: true`.

**What it means.** No Fibre runs anywhere: for 10 minutes no Fibre host (every host in the `fibre` group, active or standby) has reported the Fibre unit as active for a whole minute. Fibre is stopped or crashed on every host, crash looping (active under a minute at a time), the host of the active Fibre is down (its node_exporter goes with it, and the page comes about a minute later) and no standby was started, or `celestia_mon_units.fibre` names the wrong unit. A stopped standby is normal, and a failover within 9 minutes pages nobody, so a short restart pages nobody either. The alert names no host; its description lists the Fibre hosts. The monitor cannot tell a Fibre host that is down from one it cannot reach: with the link to the active Fibre's host down, this pages as well, next to [MonitorCannotReach](#monitorcannotreach)'s message for that host. When only node_exporter is gone and Fibre on that host still answers its health check, Fibre runs: ServiceDown's message names node_exporter and FibreDown stays quiet. A Fibre older than v10.5.0 gives no such answer, so it pages as the host down. While the monitor reaches no Celestia host at all (a monitor on a host of its own), this unit check waits and MonitorCannotReach pages instead. When the monitor runs on the Fibre host and that host dies, the dead-man service tells you ([Watchdog](#watchdog)).

**From outside.** A running unit is not enough when nobody can reach it. With `celestia_mon_tensile` set on the monitor, FibreDown also pages, after 10 minutes, when:

- the endpoint registered on chain (`x/valaddr`) cannot be reached from outside: [Tensile](https://tensile.huginn.tech), a Fibre observer, dials it from outside your networks and needs TCP and TLS to pass. This catches a closed port or firewall, an inbound outage at the provider, or a routing or DNS fault. Reachable means TCP and TLS: a wrong identity, a certificate your consensus key does not endorse, still reads as reachable, and shows as `missed_uploads` ("Uploads missed") and on the dashboard once blobs come. Only Tensile checks of the last 15 minutes count. Tensile checks about every 5 minutes and calls an endpoint unreachable at the second failed check in a row, so this page comes about 15 to 23 minutes after the fault;
- Fibre runs on one host only, and that host's `celestia_mon_fibre_public_endpoint` is not the endpoint registered on chain: after a failover, `set-host` was not sent, and clients dial the old host. While two Fibres run this waits: [FibreBothActive](#fibrebothactive) sends its message, and the 10 minutes start once one is left. A reading over 9 minutes old does not count, because it cannot tell a failover done meanwhile from a forgotten `set-host`;
- no Fibre host is registered on chain for a validator in the active set. Tensile reads hosts from the chain's list of bonded providers, so out of the active set this check stays quiet and [ValidatorNotSigning](#validatornotsigning) pages.

The `check` label tells the cases apart (none for the unit, `reachability`, `registration` with `reason` `wrong_host` or `not_registered`), and the dashboard names each in one word: "Not running", "Unreachable", "Wrong host", "Not registered".

celestia-mon-tensile on the monitor reads Tensile's verdict for your validator every minute and serves it as metrics; Tensile is used only for this check and for the blobs [FibreNotServing](#fibrenotserving) compares with Fibre's uploads. Set it up in the inventory, on the monitor host: `celestia_mon_tensile: {api: "https://tensile-mocha.huginn.tech/api/v1", validator: "<celestiavalcons1...>"}` (Tensile's API for your network; check 4 of `celestia-mon-doctor` on the validator prints the consensus address), and set `celestia_mon_fibre_public_endpoint` on every Fibre host. [TensileUnreadable](#tensileunreadable) tells you when the outside check stops.

**Severity.** page. While it fires, FibreNotServing is held back.

**Check first.**

1. On the monitor, `sudo celestia-mon-doctor` check 19 shows which Fibre host is active, if any, and check 11 whether each host's node_exporter answers.
2. On the Fibre host that should be active, `systemctl status <fibre unit>` and `journalctl -u <fibre unit> -n 100`. `sudo celestia-mon-doctor` there (check 6) shows the unit and its listening port. If that host is down, start the standby Fibre on another Fibre host and point `x/valaddr` at it with `celestia-appd tx valaddr set-host`, from your workstation or through an ops key with an authz grant, never with a key on a server ([registering Fibre with set-host](layouts.md#registering-fibre-with-set-host)); check 5 on the validator shows the registration. Before you start a standby, check 17 on its host shows whether its signer reaches the validator node.
3. For a planned Fibre stop with no standby started in its place, silence the alert by name first ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)): a host silence does not cover it.

**Links.** [Fibre server troubleshooting](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/cmd/README.md#troubleshooting).

## TensileUnreadable

Only with `celestia_mon_tensile` set and Fibre on.

**What it means.** For 15 minutes, then 10 more, celestia-mon-tensile has not read Tensile's API, Tensile has not checked your Fibre endpoint, or the service is down. The 15 minutes are FibreDown's: it counts only checks of the last 15 minutes, so from then on it cannot see an endpoint that is unreachable from outside. FibreNotServing sees no new blob and no new failed read meanwhile: `missed_uploads` keeps comparing the last blob Tensile reported, so a miss already seen still fires, but a later blob Fibre misses is not reported. The unit check still works. Tensile checks every endpoint about every 5 minutes, so the 15 minutes leave room for a late check.

**Severity.** message.

**Check first.** On the monitor, `journalctl -u celestia-mon-tensile -n 50`: a 404 means the validator address or the API's network is wrong in `celestia_mon_tensile`; a network error means the monitor cannot reach Tensile. If the service reads Tensile but its last check is old, Tensile itself is behind: look at its [status page](https://tensile.huginn.tech).

## FibreBothActive

Only with `celestia_mon_fibre_enabled: true`.

**What it means.** Fibre runs active-passive: one Fibre host serves, the others keep a stopped standby. For 5 minutes the Fibre unit has been active on more than one host, named in the description. Typically a standby was started and the old Fibre came back, or was never stopped.

**Severity.** message. While it fires, FibreNotServing `missed_uploads` is held back, as only the Fibre the chain names gets uploads.

**Check first.**

1. On the validator host, `sudo celestia-mon-doctor` check 5 shows which host `x/valaddr` registers: that one stays active.
2. Stop Fibre on every other host (`sudo systemctl stop <fibre unit>`, and `sudo systemctl disable <fibre unit>` so it stays a standby after a reboot).
3. On the monitor, `sudo celestia-mon-doctor` check 19 confirms that one Fibre host is active.

## FibreNotServing

Only with `celestia_mon_fibre_enabled: true`; `missed_uploads` and `not_served` also need the outside check (`celestia_mon_tensile`), and `not_ready` a Fibre with its health check (celestia-app v10.5.0 or later).

**What it means.** Fibre runs but does not do its job. Only a host whose Fibre unit is active can fire it: a stopped standby sends no Fibre metrics, does not answer the health check and is not expected to. A network that sends no blobs never fires it: when no blob comes, nothing is missed. The `reason` label says how, and the dashboard says it in the words in brackets:

- `missed_uploads` ("Uploads missed"): a blob that assigned rows to your validator settled more than 5 minutes after Fibre last accepted an upload (or after Fibre started, if that is later), and that held for 5 minutes. The reference client sends a blob's rows to every validator the blob assigns rows to: after 2/3 of the voting power has signed, it keeps sending to the slower ones in the background, best effort, each upload within a short timeout (celestia-app `fibre/client_upload.go`), so a working Fibre has usually accepted that upload by the time the blob settles. The protocol does not require it: another client can stop at 2/3, a failed upload is not sent again unless Fibre asks for it (a full budget), and once the blob has settled Fibre refuses its upload. So one message with no other sign of a fault can be a single upload that did not make it; the next accepted upload ends it. [Tensile](https://tensile.huginn.tech) records which blobs assigned rows to your validator, and celestia-mon-tensile reads that every minute; the alert compares the newest with Fibre's own last accepted upload, taking Tensile's reading of a minute before, so that Fibre's report of the same upload (seconds later) has arrived. A blob that settled while no Fibre ran, or before Fibre restarted, does not count. It comes about 6 to 8 minutes after the missed blob settled, when Fibre accepted nothing in the 5 minutes before it. While Tensile is not read, the last blob it reported still counts ([TensileUnreadable](#tensileunreadable) tells you): an old reading can end the alert, never start it. Tensile does not send the time of that blob yet (`signing.last_assigned_at`; celestia-mon-tensile uses it when it does), so celestia-mon-tensile takes the time of the newest block at the read at which the number of blobs assigned to your validator in Tensile's last 24 hours rose. That is up to 2 minutes late, as Tensile's number is up to a minute old. The number can stay flat when as many blobs leave the 24 hours as arrive, so on steady traffic the time can lag by hours and the alert comes that much later; after celestia-mon-tensile restarts the time is gone until the next rise. A read that failed resets the number, so blobs found after it are not taken for new ones; a Tensile whose record catches up after its own outage can still be taken for them. A blob that carries your validator's signature does not fire it, however late its publisher settled it (a promise can settle up to `PaymentPromiseTimeout` after the upload, an hour by default): Fibre took that upload. A blob without your signature fires it as any other: the blob settles once 2/3 of the voting power has signed, so a healthy validator is sometimes left out, and its Fibre then usually gets the upload in the background.
- `uploads_failing` ("Uploads failing"): for 15 minutes Fibre has accepted no upload, and the uploads that passed their checks failed on this side: every signature Fibre tried failed, the storage budget was full, or Fibre could not store them. The failures must keep coming, less than 5 minutes apart for more than 5 minutes: a publisher that sends one blob, or one every 5 minutes or more, does not fire it. `missed_uploads` reports those blobs with the outside check. Fibre signs each upload it accepts through the node at its signer address, so a signer that gives the validator's key but does not sign, as a tmkms that cannot sign raw bytes, fails every upload while Fibre's health check, which only reads that key, still passes. A full budget refuses every upload until old shards are removed: under heavy network load, or when Fibre's clean-ups fail and old shards stay. A store that fails need not be a full disk: [DiskLow](#disklow) reads free space and read-only state, not the inodes that one file per shard uses up, nor the owner and permissions of the store's directories, and Fibre's health check writes one small key, not the shard files, so `not_ready` stays quiet. An upload a client cancels during the store counts as a failed store too. An upload a client gets wrong (an escrow too low, an expired or repeated payment promise) fails at its checks, before all of these, and never fires it: on a quiet network one such client, sending its blob again every few seconds, can be all of Fibre's traffic. A signer Fibre cannot reach fails each upload at its checks too, where Fibre reads the validator's key; Fibre's health check asks that signer every round, so `not_ready` reports it within minutes, blob or no blob. That is the case after tmkms is pointed at another node while Fibre still dials the old one: its unit stays active and its port answers, so FibreDown and an external port check stay quiet. The dashboard's "Fibre signs through" turns amber as soon as Fibre's last signatures went through another node than the validator's. No alert backs that amber: after a repoint on a network that sends no blob, Fibre's last signatures stay on the old node for up to an hour, even when Fibre already dials the right one.
- `not_served` ("Rows not served"): Tensile read rows of a blob your validator signed for, while Fibre must still keep them (the retention window), and Fibre did not return them: a client would not get your share of that blob. Tensile reads each blob once, late in the window, from one place, so this comes hours after the blob, and one failed read alone can be a blip between Tensile and your host: the alert needs a second failed read within Tensile's last 24 hours. It fires at once at the read that makes two or more, and resolves an hour after the last such read. After a failover the new box does not hold the rows the old box stored, and Tensile reads them at the host registered now, so its failed reads rise for 4 hours and Rows served turns amber; the alert waits 5 hours 15 minutes after the registration moved, and a real failed read in that time sends nothing either.
- `not_ready` ("Not ready"): for 3 minutes Fibre's own health check has answered NOT_SERVING. The monitor asks each Fibre host's gRPC health service (service `""`) through the blackbox exporter (job `fibre_health`). Fibre answers SERVING only while six checks pass, which it runs every 10 seconds by default ([celestia-app v10.5.0 `fibre/server_health.go`](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/server_health.go)): `app` (the app node answers on the expected chain, is not syncing, and its newest block is younger than `max_block_age`, 2 minutes by default), `fibre_module` (the app node has the Fibre module), `signer` (the signer answers with the public key Fibre started with), `validator` (in the active set), `registration` (a valid provider host on chain) and `store` (a small write and read). Uploads do not wait for this state, but they need what the checks test, so uploads to this Fibre may fail. Fibre also answers NOT_SERVING from its start until its checks first pass and while it shuts down; the 3 minutes let a restart pass. A Fibre older than v10.5.0 answers Unimplemented and a stopped standby does not answer: neither fires it, and the dashboard's Upload readiness shows them as "Not reported" and "Not checked". After 10 minutes a second rule pages. With celestia-mon-blackbox down this check is off: [ServiceDown](#servicedown) sends its message for the monitor after 3 minutes, and the dashboard reads "Probe stopped".
- `no_telemetry` ("No Fibre data"): the Fibre unit has been active for 30 minutes but no Fibre metrics arrive. Fibre's OTLP endpoint is not set, or the collector is not receiving.

Without the outside check nothing on your hosts tells a network that sends no blobs from a Fibre that receives none, so no alert covers that case: the dashboard's Last accepted upload shows when Fibre last took one, and Tensile shows whether the network carries blobs. Failed signatures and a full budget that come less often than every 5 minutes show only on the dashboard's Uploads failing chart too, and failed stores only in Fibre's log. An active Fibre that does not answer the monitor (a firewall change on its port, another bind address, a wedged process) fires nothing either: a probe with no answer reads like a stopped standby, the dashboard's Upload readiness shows "Not checked", and `doctor` check 24 on the monitor reports it when you run it. Set up `celestia_mon_tensile` to be told.

Possible causes: the host is not registered or not reachable from outside, the validator is not bonded, the node is not synced, or the signer is unreachable or cannot sign raw bytes.

**Severity.** message: not serving Fibre carries no on-chain penalty. FibreDown pages when Fibre itself is down. `not_ready` also pages once it has lasted 10 minutes, as long as FibreDown waits: that long is no restart, and uploads to this Fibre may fail. While FibreDown or NodeNotAdvancing fires, FibreNotServing is held back, and while ValidatorNotSigning fires, `not_ready` is; while FibreBothActive fires, `missed_uploads` is, as only the Fibre the chain names gets uploads. While a host's Fibre unit crash loops, its messages are held back, and the `not_ready` page still goes out.

**Check first.**

1. For `missed_uploads`: Tensile's page for your validator lists the recent blobs. `journalctl -u <fibre unit> -n 100` shows the uploads Fibre received and why any failed; no upload lines at all point at the registration (`doctor` check 5 on the validator host) or at a port that publishers cannot reach.
2. For `not_served`: `journalctl -u <fibre unit>` for "failed to retrieve blob shard" (a store error; a shard Fibre does not hold is logged only at debug level), the Fibre store's disk (read-only, full, I/O errors in `journalctl -k`), and Failed clean-ups on the dashboard.
3. For `no_telemetry`: `sudo celestia-mon-doctor` on the Fibre host (check 7) shows where the unit sends its metrics, and check 13 on the monitor shows whether they arrive. Fibre needs `--otel-endpoint http://127.0.0.1:9915` or `FIBRE_OTEL_ENDPOINT` ([quickstart step 6](quickstart.md#6-your-steps)).
4. Registration and reachability: `sudo celestia-mon-doctor` on the validator host (check 5) shows the registered `host:port`; on the Fibre host, check 6 shows the unit and its listening port. From outside, a TCP connection to the registered `host:port` must succeed; [Tensile](https://tensile.huginn.tech) shows your server from outside. If the port does not answer while the unit is active, read `journalctl -u <fibre unit> -n 100` and restart Fibre at a moment you choose.
5. The validator is bonded and the node is synced (Fibre logs "derived storage budget is 0" when it finds no stake), and the signer supports `SignRawBytes` (`raw_bytes` failures add to the grey failure bars on the "Signing time" chart).
6. For `uploads_failing`: `journalctl -u <fibre unit> | grep 'failed to sign payment promise'` prints each failed signature with its `error`; tmkms's log on the KMS box shows the same requests, and the signer must support `SignRawBytes`. `journalctl -u <fibre unit> | grep 'failed to store upload data'` prints each failed store with its `error`: check the store's inodes (`df -i <store>`), and the owner and permissions of its directories. Without either line the storage budget is full: the dashboard's Storage budget used reads 100%, and Failed clean-ups above 0 means old shards stay on disk (Fibre's log names that error).
7. Uploads refused over their payment promise: `journalctl -u <fibre unit> | grep 'payment promise verification failed'` prints each with its `error` ([celestia-app v10.5.0 `x/fibre/keeper/keeper.go`](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/x/fibre/keeper/keeper.go)). "payment promise height … is too far in the future": the promise names a height more than one block above the newest block of the node at Fibre's `app_grpc_address`, so that node commits blocks later than the publisher's node does; check its peers ([NodePeersLow](#nodepeerslow)), its disk, and the Block processing time chart. "payment promise expired" and "insufficient balance in escrow account" (or "escrow account not found") come from the publisher's side: nothing to fix on your hosts. So does "payment promise has already been processed", which is also what an upload gets that reaches Fibre after its blob settled (`missed_uploads`).
8. For `not_ready`: on the Fibre host in the alert, `journalctl -u <fibre unit> | grep 'health check failed'` prints each failing check, with its `check`, its `reason` and, in `message`, what to do; a `server not ready` line lists every check that fails, and `health check passed` and `server ready` show when they recover. The messages, by check:

| `check` | `message` (`reason`) | What to do |
|---|---|---|
| `app` | "The app node did not answer a valid status request. Check app_grpc_address and that the node is running." (`app_unreachable`) | The node at Fibre's `app_grpc_address` is down or not at that address: `doctor` check 27 on the Fibre host names that node, then `systemctl status <its celestia-appd unit>` there, and `doctor` check 3. |
| `app` | "The app node reports chain ID "<chain>", expected "<chain>"." (`chain_id_mismatch`) | `app_grpc_address` points at a node of another network. |
| `app` | "The app node is still syncing." (`app_syncing`) | Wait until the node has caught up; its server card shows how far behind it is. |
| `app` | "The latest block is <age> old (limit <max_block_age>)." (`chain_stalled`) | The node stalled or the chain halted: [NodeNotAdvancing](#nodenotadvancing) and [NodeBehind](#nodebehind). The limit is `max_block_age` under `[health]` in Fibre's config. |
| `fibre_module` | "The app node has no Fibre module. Check that app_grpc_address is the application gRPC endpoint of a chain with Fibre enabled." (`fibre_module_unavailable`), or "The app node did not answer the Fibre module query." (`app_unreachable`) | `app_grpc_address` names another endpoint than the node's application gRPC, or the node is down. |
| `signer` | "The signer did not answer within the probe timeout. Check signer_grpc_address and the node's priv_validator_grpc_laddr." or "The signer did not return a public key. Check signer_grpc_address and the node's priv_validator_grpc_laddr." (`signer_unreachable`) | If tmkms was pointed at another node, point Fibre's `--signer-grpc-address` (or `signer_grpc_address`) at that node's privval gRPC and restart Fibre ([after a tmkms repoint](layouts.md#after-a-tmkms-repoint)); `sudo celestia-mon-doctor` checks 17 (Fibre's signer) and 20 (where the node's signer ports listen) show both ends. |
| `signer` | "The signer reports a different public key than at startup. Confirm the validator key and restart." (`signer_key_changed`) | Another key signs behind that node: confirm which key tmkms holds before you restart Fibre. |
| `validator` | "Validator <address> is not in the active set." (`validator_not_active`), or "Could not fetch the validator set from the app node." (`app_unreachable`) | Jailed, unbonded or outside the active set: [ValidatorNotSigning](#validatornotsigning) pages for that and holds this back. |
| `registration` | "Register a Fibre provider host for validator <address> with MsgSetFibreProviderInfo." (`provider_not_registered`), "The registered provider host "<host>" is not a valid host:port. Register it again." (`provider_host_invalid`), or "Could not query the Fibre provider registration from the app node." (`app_unreachable`) | `doctor` check 5 on the validator host shows the registration; register the active Fibre host in `x/valaddr` as under [FibreDown](#fibredown). |
| `store` | "The store failed a small write and read. Check free disk space and permissions of the store directory." (`store_failed`) | The Fibre store's disk (full, read-only, I/O errors in `journalctl -k`) and the permissions of the store directory for Fibre's user. |

**Links.** [Fibre server guide: server runs but no uploads arrive](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/cmd/README.md#server-runs-but-no-uploads-arrive), [registration](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/cmd/README.md#registration), [tracing and metrics](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/cmd/README.md#tracing--metrics), [health checks](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/server_health.go).

## DiskLow

**What it means.** A disk you listed in `celestia_mon_data_mounts` (`/` on a box that sets none) or `celestia_mon_fibre_mount` is filling up or turned read-only. A node whose data disk turns read-only stops working, and the validator can be jailed for downtime; a backup node whose disk fills cannot take over signing.

**Severity.** page or message, by disk and condition:

| Disk | Condition | Severity |
|---|---|---|
| Data mounts on the celestia-appd hosts (validator and backup nodes) and the KMS host | read-only, or less than 5% free | page |
| Data mounts on every watched host | less than 10% free for 10 minutes | message |
| Data mounts on a Fibre host that runs no celestia-appd, and on a monitor with a host of its own (the disk of Prometheus's data, `/var/lib/celestia-mon`) | read-only | message |
| Fibre store | read-only | message |
| Fibre store | the space the storage budget still needs is more than the free space, for 30 minutes | message |
| Fibre store, when Fibre reports a budget of 0 (no limit) or no budget | less than 10% free for 10 minutes | message |

The Fibre store has no percentage rule while a budget is set: Fibre keeps its occupancy near the budget at full load, which is normal. A stopped Fibre reports no budget, and neither does one whose collector is down: a store more than 90% full then sends the no-budget message about 15 minutes after the stop, and every 4 hours while it lasts. A standby that once ran can hold such a store. A Fibre store that is also a data mount of a celestia-appd host (a standby Fibre on the validator's disk, for example) follows the data mount rows instead, and keeps the budget row.

**Check first.**

1. Read-only: `journalctl -k` or `dmesg` for I/O or filesystem errors. The disk may be failing; find out why before you remount it read-write.
2. Every mount of a host read-only while `findmnt` shows `rw`: node_exporter cannot read `/proc/1` (hidepid on `/proc`). `sudo celestia-mon-doctor` (check 10) gives the fix. The same cause can stop node_exporter's filesystem collector: DiskLow then sees no disk on that host, and [ServiceDown](#servicedown) sends a message after 3 minutes.
3. Free space: `du -xh --max-depth=2 <mount> | sort -h | tail` shows what grew. On the node, check pruning in `app.toml` and blockstore compaction in `config.toml`.
4. Fibre store: the "Fibre disk and budget" panel shows the free space on the store of the Fibre that runs against what Fibre may still write under its storage budget, the room the budget still needs. Grow the disk or move other data off it. Celestia recommends separate disks for Fibre and celestia-app data.

**Links.** [celestia-app v10 release notes: Fibre](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/docs/release-notes/release-notes.md#fibre), [blockstore compaction](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/docs/release-notes/release-notes.md#blockstore-compaction).

## RaidDegraded

**What it means.** An md software RAID array (mdadm) on a host has run on fewer disks than it needs for 5 minutes: a disk failed or dropped out, the array was started without one, or a new disk is still being rebuilt. node_exporter's mdadm collector reads `/proc/mdstat` on every host it runs on; the `[2/1]` of an array's status line gives the disks it needs and the active ones. The host keeps serving the array's data from the disks left, but one more failure in it loses that data. The monthly check of an array and a resync after an unclean shutdown keep `[2/2]` and fire nothing, and a host without md arrays has nothing to watch. A hardware RAID controller hides its disks from the host: its own tools alert on them.

**Severity.** message. A failed disk can wait days for its replacement, so after the first message it comes again once a day while it holds, not every 4 hours. It keeps firing through the rebuild of a new disk and through a short loss of the host's metrics, so its "resolved" message means the array is whole again. When the monitor reaches none of the host's metrics for more than 10 minutes it resolves as well; [ServiceDown](#servicedown) or [MonitorCannotReach](#monitorcannotreach) names the host then.

**Check first.**

1. `cat /proc/mdstat` on the host: `[U_]` marks the missing disk, `(F)` a failed one, and a `recovery` line a rebuild's progress. `mdadm --detail /dev/<device>` shows each disk's state, and `journalctl -k | grep -i md` when one dropped out. `sudo celestia-mon-doctor` (check 10) prints every array of the host, and fails for one that node_exporter does not export.
2. A failed disk: replace it, partition the new disk like the one left, and add the partition to the array (`mdadm --manage /dev/<device> --add <partition>`); the rebuild starts at once and `/proc/mdstat` shows its progress. When the swap stops the host, silence it first ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)).
3. Right after an install: a disk that once held half of a mirror can start as an array of its own, such as `md127 : active (auto-read-only) raid1 sdb1[1]` with `[2/1]`. If nothing mounts it, stop it (`mdadm --stop /dev/md127`) and clear that partition's RAID superblock (`mdadm --zero-superblock <partition>`), which erases its old RAID metadata.

**Links.** [node_exporter v1.12.1 mdadm collector](https://github.com/prometheus/node_exporter/blob/v1.12.1/collector/mdadm_linux.go), [mdadm(8)](https://man7.org/linux/man-pages/man8/mdadm.8.html).

## ClockNotSynced

**What it means.** For an hour the kernel has reported a host's clock as not synchronised: its maximum error (`node_timex_maxerror_seconds`, from node_exporter's timex collector) has reached 16 seconds, the kernel's limit. That is the test of `timedatectl`'s `NTPSynchronized` and of `doctor` check 9. chrony and systemd-timesyncd set the maximum error at each update, and the kernel adds 0.5 ms a second in between, so it reaches 16 seconds about 9 hours after time sync stops, and at once when chrony has no usable time source. A clock step or a clocksource switch also sets it to 16 seconds until the daemon's next update, which the hour lets pass. Nothing fails yet, but the clock drifts freely from then on: a celestia-appd node whose clock falls about 60 seconds behind rejects every block as too far in the future ([celestia-core v0.42.3 `state/validation.go`](https://github.com/celestiaorg/celestia-core/blob/v0.42.3/state/validation.go)), and a Fibre server whose clock runs about 2 minutes ahead finds the newest block older than `max_block_age` and reports itself not ready ([FibreNotServing](#fibrenotserving) `not_ready`). A clock synchronised to a wrong time source still counts as synchronised: this alert does not catch it.

**Severity.** message: a clock left to drift takes days or longer to reach those limits. It keeps firing through a short loss of the host's metrics, so its "resolved" message means time sync works again. When the monitor reaches none of the host's metrics for more than about 13 minutes it resolves as well; [ServiceDown](#servicedown) or [MonitorCannotReach](#monitorcannotreach) names the host then.

**Check first.**

1. `chronyc tracking` and `chronyc sources` (chrony), or `timedatectl timesync-status` (systemd-timesyncd): whether the daemon runs, which sources it reaches, and the offset it measures. `sudo celestia-mon-doctor` on the host (check 9) shows the same.
2. Outgoing NTP (UDP 123) must be allowed to the time servers. Start the daemon if it stopped (`systemctl start chrony`, or `timedatectl set-ntp true` for systemd-timesyncd); run one of the two, not both.
3. The dashboard's Clock maximum error chart (Servers: disks, restarts and clocks) shows when the line began to climb.

**Links.** [node_exporter v1.12.1 timex collector](https://github.com/prometheus/node_exporter/blob/v1.12.1/collector/timex.go), [Fibre health checks](https://github.com/celestiaorg/celestia-app/blob/v10.5.0/fibre/server_health.go).

## MonitorCannotReach

Only on hosts the monitor reaches over the network: not on the monitor's own host.

**What it means.** For 3 minutes Prometheus on the monitor has reached none of a host's metrics endpoints: celestia-appd's metrics, node_exporter and, on a Fibre host, the OpenTelemetry collector. From the monitor, a host that is down and a link that is down look the same: the host is off or cut from the network, the link between it and the monitor is down (WireGuard, either provider's network), or each of those services stopped. The message names the host and stands in for its ServiceDown messages; its service and disk alerts cannot fire until it answers again. The alerts that follow the roles still page for what that host held: [ValidatorNotSigning](#validatornotsigning) when the validator signed through its node, [FibreDown](#fibredown) when it ran the active Fibre and no standby takes over.

Where the monitor has a host of its own (`mon-1` in the [mainnet layout](layouts.md#mainnet-layout)), it can lose every host at once. When no Celestia host answers for 3 minutes, one page, "Monitor cannot reach any server", stands in for the messages: no alert that reads the hosts' metrics can fire meanwhile, and NodeNotAdvancing and FibreDown's unit check, which would otherwise page for the missing metrics, wait for a host that answers. It names no host; its description lists them. The validator may well be signing: this page says the monitor is blind, not that the validator is down. A monitor that shares a host with a node or Fibre reads that host locally and never loses all of them, so there is no page; the messages for the other hosts remain.

**Severity.** message for one host; page when no host answers.

**Check first.**

1. Whether the validator still signs, from outside the monitor: an explorer shows its recent signed and missed blocks. If it signs, the fault is between the monitor and the hosts, and nothing on the validator needs you now.
2. On the monitor, `sudo wg show`: each peer's `latest handshake`. While the scrapes flow, WireGuard renews the handshake about every 2 minutes, so one older than about 3 minutes, or none, means no packet gets through that tunnel. Check that the host's WireGuard UDP port allows the monitor's public IP, and that the keys, endpoints and `AllowedIPs` on both ends match ([WireGuard-only link](security.md#wireguard-only-link)). `sudo wg show` on the host, if you can reach it another way, shows the other end.
3. The status pages of the monitor's provider and of the host's: a data centre or its network down takes every host there, or the monitor, off the link.
4. If you can still log in to the host: `systemctl status celestia-mon-node-exporter celestia-mon-otelcol <celestia-appd unit>` and `last -x reboot | head -3` for a reboot; all its services stopped at once is rare (a reboot, a full root disk).

## UpgradeSignalMissing

Only with `celestia_mon_signal` set.

**What it means.** Celestia moves to a new app version through x/signal: validators signal the next version, and once 5/6 of the voting power has signalled it, anyone can schedule the upgrade, which then happens a fixed number of blocks later (about a week on mainnet). This alert says that such a round is open: voting power has signalled the next version, and for 10 minutes your validator has not signalled it, or has signalled another version. It is a message from the first signal, and a page 10 minutes after more than 1/3 of the voting power has signalled. Once the upgrade is scheduled, x/signal accepts no more signals: the alert ends, and [UpgradeScheduled](#upgradescheduled) says that your validator did not signal. Your validator's signal is read from chain state, so a signal sent through an authz grant counts too. The alert names no host.

celestia-mon-signal on the monitor reads x/signal every minute through a node's CometBFT RPC and serves it as metrics: the app version, the voting power that signalled the next version against the 5/6 threshold, the scheduled upgrade with its height and the time until it at the measured block time, or 2.6 s per block until one is measured, and the version your validator signalled. It sends no transaction and needs no key. Set it up in the inventory, on the monitor host ([upgrade signalling](reference.md#upgrade-signalling)): `celestia_mon_signal: {chain_id: "celestia", rpc: ["<CometBFT RPC URL>", ...], validator: "<celestiavaloper1...>"}`. The RPCs are tried in order, and one that serves another network than `chain_id`, or whose node is catching up or has made no block for 2 minutes, is skipped. A collector or RPC outage of up to 7 days does not end the alert: the upgrade alerts use the last reading of the past 7 days, and [SignalUnreadable](#signalunreadable) reports the outage.

**Severity.** message, and a page once more than 1/3 of the voting power has signalled; while the page fires, the message is held back, so the chat repeat comes once. Until your signal is on chain, the chat channels get the alert again every 4 hours, and PagerDuty keeps the page's incident open.

**Check first.**

1. Upgrade celestia-appd on every node, the validator and each backup node, to a release that supports the version in the alert, at a moment you choose: Celestia asks validators to upgrade the binary before they signal. The release notes and Celestia's announcements ([CelestiaAnnouncement](#celestiaannouncement)) name the release.
2. Signal with the validator's account key, kept on your workstation or a Ledger as for `set-host` ([registering Fibre with set-host](layouts.md#registering-fibre-with-set-host)): `celestia-appd tx signal signal <version> --from <validator account key> --node <RPC of a synced node>`. An ops key can send it through an authz grant, with the same three commands as for `set-host` there: the grant with `--msg-type=/celestia.signal.v1.MsgSignalVersion`, then `celestia-appd tx signal signal <version> --from <validator account address> --generate-only > signal.json` and `celestia-appd tx authz exec signal.json --from <ops key>`.
3. `celestia-appd query signal tally <version> --node <RPC of a synced node>` shows the voting power that has signalled. The alert resolves a few minutes after your signal is in a block.

x/signal accepts a signal for any version at or above the current one, so a validator can signal the next version before a release exists. The page waits for more than 1/3 of the voting power; until then the message says there is no rush, the dashboard's Not signalled is amber (red once the page fires), and `doctor` check 23 warns.

**Links.** [Celestia network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/), [x/signal](https://github.com/celestiaorg/celestia-app/tree/v10.5.0/x/signal).

## UpgradeScheduled

Only with `celestia_mon_signal` set.

**What it means.** At least 5/6 of the voting power signalled the next app version, and the upgrade is scheduled at a height. The message names the version, the height, the time until it at the block time measured over the last 1000 blocks, or 2.6 s per block until one is measured, and whether your validator signalled. Every node of this install must run a celestia-app release that supports that version before the height, or it stops there. It names no host.

**Severity.** message, sent when the upgrade is scheduled. Its "resolved" message means the chain has switched to the new version. On mainnet an upgrade is scheduled about a week ahead, so a message still firing after 4 days is sent once more, with the time left then.

**Check first.** Plan the binary upgrade of every node before the height, and the restart of each one with a host silence ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)). If the message says your validator did not signal, nothing can be signalled for this version any more; upgrade the nodes all the same. [UpgradeSoon](#upgradesoon) reminds you a day ahead.

## UpgradeSoon

Only with `celestia_mon_signal` set.

**What it means.** The scheduled upgrade is less than 24 hours away at the measured block time, or 2.6 s per block until one is measured. A node that does not run a celestia-app release that supports the new version stops at the upgrade height, and with the validator node the validator stops signing. It names no host.

**Severity.** message, sent once. Once sent, it stays until the chain has switched, also when slower blocks move the time left back above 24 hours; its "resolved" message means the chain has switched to the new version.

**Check first.** On every node of this install, the validator and each backup node, confirm that the celestia-app release it runs supports the target version (`celestia-appd version`, and the release notes). After the height, the Consensus node rows of the server cards and NodeNotAdvancing show whether the nodes moved on.

## GovVoteMissing

Only with `celestia_mon_signal` set, unless it sets `gov: false`.

**What it means.** A governance proposal (x/gov) is in its voting period, less than 2 days are left until its end (on a vote shorter than 4 days, less than half of it), and the validator account has no vote on it. The validator account is `celestia1...` with the same bytes as the operator address `celestiavaloper1...`: the tally counts that account's vote as the validator's. A vote from another wallet, or from a multisig that is not that account, does not count; a vote sent through an authz grant from the validator account does, because the vote is then the validator account's. Nothing is sent when a vote opens, and nothing at all for a validator that votes before these limits. One alert per proposal; it names the proposal, its title and the time left, and no host.

celestia-mon-signal on the monitor reads the proposals with x/signal, every minute through the same RPCs: the proposals in their voting period, and those with a vote from the validator account, both at one height. For each it serves the start and end of the voting period, whether the validator account voted, and the title, which whoever submitted the proposal wrote: at most 80 characters of it on one line, square brackets made round, so no chat channel shows a link under other words. While the RPCs fail the Proposals query, the service keeps serving the proposals it read last, so the alert goes on from that reading: it still comes on time for a proposal already read, but a vote cast meanwhile does not resolve it, and a proposal opened meanwhile is not seen. The rules read each proposal's last value of the past hour, so a short outage of the service or of its scrape neither resolves the alert nor sends it again; after an hour without the service, the alert resolves. [SignalUnreadable](#signalunreadable) reports either outage.

**Severity.** message once less than 2 days are left, and a page once less than 1 day is left, where the page is on: by default with `chain_id: celestia` (mainnet), and not on other networks, where the message is all ([upgrade signalling](reference.md#upgrade-signalling) has `gov_page` to change it). Neither comes before half the vote has passed: a 3-day vote gets the message 36 hours before its end, and a vote of 2 days or less gets both at half its length, a 24-hour vote (an expedited proposal, or a testnet's short voting period) 12 hours before its end; where the page is on, such a vote gets the page alone, and so does a longer one first read with less than a day left (`gov` turned on then, or the readings back after more than an hour). GovVoteMissing has its own route: each notification is sent once and comes again only after 4 days, longer than the alert can last, with a "resolved" one at the end.

It resolves a few minutes after the validator account's vote is in a block, when its proposer cancels it, or when the voting period has ended. An expedited proposal that does not pass by the end of its short voting period becomes a regular one: its voting period then ends a full voting period after it started, and x/gov removes its votes during that tally, so a vote cast during the expedited period must be sent again. The alert resolves when the end moves out, and comes again as the new end nears.

**Check first.**

1. The proposal and its forum post: `celestia-appd query gov proposal <proposal> --node <RPC of a synced node>`.
2. Vote with the validator's account key, kept on your workstation or a Ledger as for `set-host` ([registering Fibre with set-host](layouts.md#registering-fibre-with-set-host)): `celestia-appd tx gov vote <proposal> yes|no|no_with_veto|abstain --from <validator account key> --node <RPC of a synced node>`. With no view on a proposal, vote `abstain` rather than leave the vote empty or silence the alert. An ops key can vote through an authz grant, with the same three commands as for `set-host` there: the grant with `--msg-type=/cosmos.gov.v1.MsgVote`, then `celestia-appd tx gov vote <proposal> <option> --from <validator account address> --generate-only > vote.json` and `celestia-appd tx authz exec vote.json --from <ops key>`.
3. `celestia-appd query gov vote <proposal> <validator account address> --node <RPC of a synced node>` shows the vote on chain. `sudo celestia-mon-doctor` on the monitor (check 23) lists the proposals in their voting period, with their end and whether the validator account voted on each, and for each without its vote when GovVoteMissing comes.

**Links.** [x/gov](https://github.com/celestiaorg/cosmos-sdk/tree/v0.52.12/x/gov) in the Cosmos SDK fork that celestia-app v10.5.0 builds with.

## SignalUnreadable

Only with `celestia_mon_signal` set.

**What it means.** For 15 minutes, then 10 more, celestia-mon-signal has read none of the RPCs in `celestia_mon_signal.rpc`, none has given it the governance proposals (with `gov` on, the default), or the service is down. The upgrade alerts keep their last reading meanwhile: an alert that fires stays, but a new signalling round would not be reported, and a scheduled upgrade would not be announced. The vote alerts keep the proposals read last: GovVoteMissing still comes on time for a proposal already read, but a vote cast meanwhile does not resolve it, and a proposal opened meanwhile is not seen, so a missing vote on it gets no message, nor a page where it pages. While the service is down or not scraped, the vote alerts keep their reading for an hour only, then resolve.

**Severity.** message.

**Check first.** On the monitor, `journalctl -u celestia-mon-signal -n 50`: each line names the RPC that failed and why. An RPC that serves another network than `celestia_mon_signal.chain_id` is skipped (a Mocha RPC in a mainnet setting, or the reverse), and so is one whose node is catching up, has made no block for 2 minutes, or answers `/abci_info` at an older height than `/status` (`behind`: a node that is syncing, stalled, or halted at an upgrade height without the new release, or a stalled node among those behind a provider's URL). A `query` error is an x/signal query the node refused, or a signal store that does not hold the height of the read (behind a provider's URL, a node a block behind the one that gave the height): that read is skipped, never taken for a validator that has not signalled. A network error means the monitor cannot reach the RPC. A `gov` error is a Proposals query of x/gov that the node refused or answered in a form that does not read: the next RPC is asked for the proposals, the ones read last stay, and x/signal goes on being read. A failed measurement of the block time (`block_time`) does not stop the reads: the one measured last stays, or 2.6 s per block until one is measured. A service that does not start names its setting there too. Add a second RPC to the list so one outage does not stop the reads, and run `install.sh` on the monitor again.

## MonitoringBroken

**What it means.** Prometheus failed to evaluate one or more alert rules in the last 10 minutes, so some alerts may not fire. It fires at once and clears 10 minutes after the last failure. The alert names the monitor host.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-prometheus -n 100`: each "Evaluating rule failed" line names the rule and the error.
2. Prometheus's rules page on the monitor (`http://127.0.0.1:9912/rules`, through an SSH tunnel from your machine, as user `celestia-mon` with the password in `/etc/celestia-mon/api-password`) shows the failing rule and its last error.
3. Free space for Prometheus's data (`df -h /var/lib/celestia-mon/prometheus`), and `sudo celestia-mon-doctor` on the monitor: checks 11 and 12 show whether Prometheus answers and which rule metrics have no series. Prometheus needs retention size x 1.5 + 2 GiB on that disk, which every `install.sh` run on the monitor checks.

## NotificationsFailing

**What it means.** Alertmanager could not deliver a page or message notification through one integration (Telegram, Discord, Slack, PagerDuty or a `webhook` receiver) in the last 15 minutes. There is one alert per integration, named in the summary, and it goes to the message receivers, so a broken integration is reported through the others. It fires at once and clears 15 minutes after the last failure. Failed heartbeats to the dead-man service are left out: they are [DeadmanFailing](#deadmanfailing). The Watchdog also stops for an hour after such a failure, so the dead-man service reports it too, also when no other receiver works.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-alertmanager -n 100`: each failed notification names the receiver and the error, for example a rejected bot token, a removed chat, a deleted Discord or Slack webhook, or an invalid PagerDuty routing key. A Telegram send that failed on the network (DNS, a timeout, TLS) is not in the journal: Alertmanager would log it with the request URL, which holds the bot token, so journald drops such lines. For Telegram, read `alertmanager_notifications_failed_total{integration="telegram"}` in Prometheus (its `reason` label) and this alert instead. If a journal of the monitor was ever shared, revoke the bot token in BotFather and put the new one in the monitor's secrets (`sudo ./install.sh --edit-secrets` there) or its vault.
2. Fix the receiver in the monitor's secrets (`sudo ./install.sh --edit-secrets` there) or its vault (`sudo ./install.sh --edit-vault <file>`) and run `install.sh` on the monitor again; it sends a test message to the chat receivers.
3. Send a test alert of each severity ([quickstart step 7](quickstart.md#7-open-grafana-and-test-the-alert-paths)) and check that it arrives everywhere you expect.

## DeadmanFailing

**What it means.** Alertmanager could not deliver the heartbeat to the dead-man service in `celestia_mon_deadman_webhook_url` at least three times in the last 15 minutes: the URL is wrong (a typo, a deleted check), the service rejects it, or the monitor cannot reach the service. One failed heartbeat costs nothing, as the next follows within 2 minutes. It fires at the third failed heartbeat in 15 minutes, about 3 minutes into a steady failure, and clears about 13 minutes after the last failure. The alert names the monitor host. A dead-man check that has never received a heartbeat may never alert, so a URL that was wrong from the start shows only here.

**Severity.** message. Failed heartbeats do not stop the Watchdog.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-alertmanager -n 100`: each failed heartbeat names the `deadman` receiver and the error, for example a 404 for a wrong URL. `sudo celestia-mon-doctor` there (check 22) counts the heartbeats sent and failed in the last 10 minutes.
2. Compare `celestia_mon_deadman_webhook_url` in the monitor's secrets (`sudo ./install.sh --edit-secrets` there) or its vault with the ping URL your dead-man service shows, fix it, and run `install.sh` on the monitor again.
3. Outbound HTTPS from the monitor to the dead-man service.

## CelestiaAnnouncement

**What it means.** Celestia posted a new message in one of its announcement channels on Discord: usually a network upgrade with its height and time, or an incident. In Telegram and Discord the notification is the announcement itself: the channel it came from (for example `Celestia #mainnet-announcements`) as its title, then the message as text, role pings and Discord markup removed (up to 3500 characters). PagerDuty and webhooks also get a link to the original. Each announcement is sent once, as soon as it is read (within a minute). The chat channels get no "resolved" message for it; the PagerDuty incident closes by itself 30 minutes later.

It is optional. celestia-mon-announcements, a small service on the monitor host, reads one or more channels on your own Discord server that follow Celestia's announcement channels ("Follow" on the channel in Celestia's Discord), with a bot of yours, and posts each new message to Alertmanager. Only what a followed channel posted counts: messages that people, bots or webhooks write into that channel are passed over. The first read of a channel only notes its newest message, so nothing older is sent. Set it up on the monitor:

1. On your Discord server, create a channel and make it follow Celestia's mainnet and testnet announcement channels (one channel can follow several). Keep your alert receivers out of it: a `discord` receiver posts into a channel of its own.
2. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications) (or reuse one): turn on **Message Content Intent**, invite it to your server, and give it **View Channel** and **Read Message History** in that channel. Copy its token.
3. In the inventory, on the monitor host: `celestia_mon_announcements: [{channel_id: "<channel ID>", severity: page}]` (Developer Mode, then right-click the channel, Copy Channel ID). `page` reaches PagerDuty and every chat channel, `message` the chat channels only. A channel that follows only testnet announcements is best set to `message`, so routine testnet news does not page.
4. In the monitor's secrets (`sudo ./install.sh --edit-secrets` there) or its vault (`sudo ./install.sh --edit-vault <file>`): `celestia_mon_discord_bot_token: "<token>"`. Then run `install.sh` on the monitor.

**Severity.** The one you set per channel (`page` by default).

**Check first.** Read the announcement: plan the upgrade it names before its height, and silence that host's alerts for the restart ([Before planned restarts and upgrades](#before-planned-restarts-and-upgrades)). Celestia's [network upgrades](https://docs.celestia.org/operate/maintenance/network-upgrades/) page and [status.celestia.org](https://status.celestia.org/) carry the details.

## AnnouncementsUnreadable

**What it means.** celestia-mon-announcements has not read its Discord channel for 15 minutes, or the service is down, for 10 minutes more. New announcements would not reach you. It exists only when `celestia_mon_announcements` is set.

**Severity.** message.

**Check first.**

1. On the monitor, `journalctl -u celestia-mon-announcements -n 50`. Each failed read names its cause and fix: Discord refused the token (401: a reset or wrong token, fix it in the monitor's secrets or vault), the bot may not read the channel (403: give it View Channel and Read Message History there), the channel is unknown (404: the ID, or the bot is not on that server), or Discord is unreachable.
2. `systemctl status celestia-mon-announcements` when the service is down.
3. After a fix in the monitor's secrets or vault, run `install.sh` on the monitor again.

## Watchdog

**What it means.** The Watchdog always fires while monitoring works. Alertmanager sends it every 1 to 2 minutes, only to the dead-man service in `celestia_mon_deadman_webhook_url`, which alerts you when the heartbeats stop. You never receive it from celestia-validator-monitoring itself. The heartbeats stop:

- when the monitor host, Prometheus or Alertmanager is down, or the monitor has lost its network;
- when Prometheus cannot store what it scrapes (a full or read-only disk), 2 minutes after its last stored sample, although its rules still run and its alerts still go out;
- for one hour after a page or message notification failed (Telegram, Discord, Slack, PagerDuty or a `webhook` receiver), so a broken alert channel reaches you through the dead-man service. Failed heartbeats to the dead-man URL itself do not count: [DeadmanFailing](#deadmanfailing) reports them.

Connect the dead-man check to PagerDuty and to email or another channel than your alert chat. A dead monitor host, or a whole data centre gone, then still rings your phone, and when the alert chat is what broke, the dead-man service is the one left to tell you. If the dead-man service alerts you, assume that no other celestia-validator-monitoring alert can reach you until you find the cause.

With the monitor on a host of its own, as `mon-1` in the [mainnet layout](layouts.md#mainnet-layout), a dead-man alert says the monitor is down or cut off, not the validator: check from outside first (an explorer for its signatures, [Tensile](https://tensile.huginn.tech) for the Fibre endpoint). When the monitor shares a host with Fibre or a node, a dead-man alert also means that host is probably down, and nothing else can tell you so: if it ran the active Fibre, start the standby Fibre on another Fibre host and send `set-host` ([FibreDown](#fibredown)).

**Severity.** heartbeat.

**Check first.**

1. Is the monitor host up and reachable? If the monitor shares a host with the validator or Fibre, that may be down too.
2. On the monitor, `systemctl status celestia-mon-prometheus celestia-mon-alertmanager` and `journalctl -u celestia-mon-alertmanager -n 100`; `sudo celestia-mon-doctor` (check 22) shows whether heartbeats went out in the last 10 minutes. A failed notification shows there with its receiver and error, for example a rejected bot token or a removed chat (a Telegram failure on the network does not: see [NotificationsFailing](#notificationsfailing)); so do failed heartbeats to the dead-man URL.
3. Outbound HTTPS from the monitor to the dead-man service and to the receivers, and the URL in `celestia_mon_deadman_webhook_url`.
4. Prometheus's disk: `df -h /var/lib/celestia-mon/prometheus`, and `journalctl -u celestia-mon-prometheus -n 100 | grep 'write to WAL'`.

The heartbeat resumes one hour after the last failed notification. To test the chain end to end, stop Alertmanager on the monitor for longer than the dead-man period, then start it again (see [quickstart step 7](quickstart.md#7-open-grafana-and-test-the-alert-paths)).
