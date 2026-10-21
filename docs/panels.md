# Panels

The dashboard **Celestia validator**, in the **Celestia validator** folder, reads like a status page for one validator setup. The first screen answers, top to bottom: whose validator and which network, is anything wrong, is each part fine, was it fine over the last day, which server held each role, and how each server is doing now. Below it, the rows **Validator** and **Fibre** (open) hold the charts that explain those answers, **Servers** (open) has a column of hardware charts for each server, and **Servers: disk activity, restarts and clocks**, **Network upgrade** and **All firing alerts** (closed) hold the detail. The install on the monitor builds the dashboard from your inventory: panels that do not apply are left out (no signer panels with a local key, no Fibre panels with Fibre off, no backup panels with one node, no outside check without `celestia_mon_tensile`, no upgrade panels without `celestia_mon_signal`). The default range is 24 hours, refreshed every 30 seconds; times are in UTC.

**Colours mean state, and only where an alert watches.** Every word, bar and cell follows one rule, printed in the key under the role band:

- **Fine**: plain text, or a muted green bar. Only "All online" in the Status cell is green text.
- **Look soon** (amber): the problem an alert watches is true, the alert is pending, or a message alert fires. If it lasts, you get the alert.
- **Urgent alert** (red): a page alert fires.
- **No data or not in use** (grey).

Amber and red appear only where an alert rule in [alerts.md](alerts.md) watches the condition, at the rule's own threshold, so anything amber reaches you if it lasts. Facts no alert watches (CPU, memory, link load, peers, the storage budget, proposals, uptime shares) are drawn in neutral colours whatever their value. The one exception is "Two signers" (below), which is amber without a rule. The Status cell, the answers and the status bars read Prometheus's own alert states, so they always agree with the alerts you received. Silences stop notifications only: a silenced alert still shows here.

**Servers and roles.** A server is every host node_exporter runs on: the validator node and the backup nodes first, then the Fibre hosts, then KMS hosts with `celestia_mon_kms_scrape: true`, in inventory order. Each server keeps one colour (blue, light purple, cyan, light blue) in every line, fill, bar and sparkline it owns and in its name; what belongs to the whole network is drawn in the text colour, and amber and red appear only where an alert's condition holds. Roles move between hosts (a tmkms repoint to a backup node, a Fibre failover), so no panel takes a role from the inventory: the signing node is the node the validator's signatures go through, and the active Fibre is the host whose Fibre unit systemd reports active.

## First screen

### Top strip

One framed band of six cells of equal width, each a small label over a larger value, set from the left, so the labels share one line and the values another; the answers below line up with its columns. A fact that cannot be read says "No data".

- **Name and network**: `celestia_mon_validator_name` (or "Validator") under the network, read from the nodes' own metrics (`chain_id`): "Celestia mainnet" or "Mocha testnet (mocha-5)". A Mocha and a mainnet dashboard cannot be confused. A name of up to 13 characters shows at full size; 14 to 17 characters make the strip's values a little smaller. "No node data" while no node answers.
- **Latest block**: the newest block any node of this setup committed. It grows by about 20 a minute; compare it with an explorer or [status.celestia.org](https://status.celestia.org/).
- **Block time**: the average time between blocks over 5 minutes, from the blocks' own timestamps. About 2.9 s is normal today; slower blocks affect every validator. Amber "Stalled" when no block arrived in those 5 minutes.
- **Voting power**: this validator's share of the active set's voting power. "Out of set" while the chain moves but no node records blocks for the validator's key (jailed, unbonded, outside the set); "Stalled" while no block arrives, when the share cannot be read.
- **Signed, 30 d**: the share of the blocks committed over the last 30 days (or the retention, if shorter) that carry the validator's signature. Blocks while it was not signing at all count as unsigned, and so does the jump of a node that catches up after an outage; time without monitoring is left out. Explorers count over the chain's slashing window, so their number can differ.
- **Status**: green "All online", or what is wrong right now, or about to be, in a few words: the worst item, urgent before warning. Red: a page alert fires. Amber: a message alert fires, or an alert's problem is true and it will be sent if it lasts. With several items the label counts them ("Status · 3 alerts"); the closed row **All firing alerts** lists every alert in the words of your notifications, with its server and how long it has been active. Follow-on alerts are grouped into their cause, as Alertmanager's inhibitions do (no "Missed blocks" while "Not signing" shows; a server whose node_exporter does not answer counts once, as "Not reporting"). A scheduled network upgrade is no fault and does not show here: the Network upgrade answer shows it. "All online" needs nothing wrong, the alert rules to have run in the last 2 minutes and Alertmanager to be up and reached.

The Status words and what they stand for (hover the cell's (i) for the list of your install):

| Status | Alert or condition |
|---|---|
| Nodes stalled | NodeNotAdvancing: no new blocks on any node |
| Not signing | ValidatorNotSigning |
| Missed blocks | SignaturesMissed |
| Node behind | NodeBehind |
| Signer slow, Bad proposals | RemoteSignerProblem (remote signer): slow, or proposals fail |
| Fibre down | FibreDown: no Fibre server running |
| Fibre offline, Unregistered | FibreDown: unreachable from outside, or not registered or wrong host (`celestia_mon_tensile`) |
| Two Fibres | FibreBothActive |
| Missed blobs, Unserved rows, Upload errors, No Fibre data | FibreNotServing: blobs sent to the validator missed or rows it signed for not served (`celestia_mon_tensile`), uploads failing, no Fibre metrics |
| Not reporting | ServiceDown: a server's node_exporter does not answer |
| Node silent, No Fibre data | ServiceDown: celestia-appd's or the OpenTelemetry collector's metrics lost |
| Node stopped, Node crashing, Node missing | ServiceDown: celestia-appd stopped, in a crash loop, or its unit missing |
| Signer down, Signer crash, No signer | ServiceDown: tmkms, likewise (`celestia_mon_kms_scrape`) |
| Fibre crash | ServiceDown: Fibre in a crash loop |
| Disk critical, Disk low | DiskLow: page (full or read-only), message (low or read-only) |
| Rules failing, Alerts unsent, No heartbeat | MonitoringBroken, NotificationsFailing, DeadmanFailing |
| Check stopped, Feed stopped, Signal stale | TensileUnreadable, AnnouncementsUnreadable, SignalUnreadable |
| Not signalled, Upgrade soon | UpgradeSignalMissing, UpgradeSoon |
| Rules stopped | no rule run in the last 2 minutes |
| Alerting down | Alertmanager down, alerts not sent |
| Alerts unsent | in the last 3 minutes, alerts failed to reach Alertmanager and none got through (not while Alertmanager is down) |
| No heartbeat | a notification failed in the last hour, so the heartbeat to the dead-man service stops for that hour |
| Two signers | the validator signs through two nodes (remote signer) |
| Announcement | a new Celestia announcement, for 30 minutes (`celestia_mon_announcements`); its text is in your alert channel |

### Answers

One word each, amber while the problem an alert watches is true, red once its page fires, in tiles of equal width under the top strip. Hover the (i) for every word.

- **Validator**: "Signing" while its signature is on one of the last 10 blocks (`not_signing_blocks`). "Missed blocks" (SignaturesMissed's condition), "Not signing" (ValidatorNotSigning's), "Out of set", "Nodes stalled" (no node committed a block for 2 minutes: NodeNotAdvancing's), "Nodes offline", and "Not seen": no signature while a node Prometheus cannot read may still be signing. Check an explorer before you move the key anywhere: two signers of one key double-sign.
- **Signing through**: the server whose node the validator signs through now; with a remote signer, the node whose privval client signed a vote or proposal in the last 2 minutes (a node that tmkms left keeps reading the validator's signatures from the chain, so only signatures made through the node count). "Switching" for up to 2 minutes after a tmkms repoint, while both nodes have recent signatures. "Two signers" in amber when two nodes have signed for 3 minutes: one key signing through two nodes can double-sign. If tmkms is set up to sign through several nodes on purpose, set `celestia_mon_signer_multi_node: true` and it reads "Two nodes" in plain text. "No node signing" while the validator is asked to sign and no node does (check tmkms and the link to it); "Not visible" while a node that may still sign is not reporting.
- **Servers** (only where the answers would be five, so they stay six and line up with the strip; it takes the place of Backup node): the server lines of the status board now, the worst first, in their colours. "All up" while every server's node_exporter answers, no server alert fires and the alert rules ran; red "Disk critical" while DiskLow pages; amber "Not reporting", "Service down", "Disk low", "Node behind" or "Warning" as the lines have them; grey "No data". Which server: its line on the board and the Servers now table.
- **Backup node** (with backup nodes): "Ready" while another node is read, at most 20 blocks (`node_behind_blocks`) behind the highest node and advancing, so it can take over signing; "Not ready" while it lags (NodeBehind's levels), "Down" while no other node is read, "Switching" during a repoint.
- **Fibre** (Fibre on): "Serving" while one Fibre server runs, sends its metrics, accepted an upload in the last hour and, with the outside check, is reachable from outside; "Running" when it runs without those. Problems: "Uploads failing" (most often Fibre still signs through a node that tmkms left), "Storage full" (the storage budget refuses uploads), "No metrics", "Not serving" (FibreNotServing fires), "On 2 servers" (FibreBothActive), "Not running" (no Fibre unit active anywhere: amber during a failover, red once FibreDown pages), "Not seen" (a Fibre host's node_exporter does not answer, so its unit state is unknown).
- **Fibre from outside** (with `celestia_mon_tensile`): [Tensile](https://tensile.huginn.tech)'s check of the Fibre endpoint registered on chain, from outside your network, read every minute. "Reachable"; "Unreachable", "Wrong host" (the chain names another endpoint than the active Fibre host's `celestia_mon_fibre_public_endpoint`; after a failover, send `set-host`) and "Not registered", amber, then red when FibreDown pages; grey "Not checked" while no check is under 15 minutes old, grey "Check pending" until Tensile has checked a new registration, amber "Check stopped" once TensileUnreadable fires.
- **Network upgrade** (with `celestia_mon_signal`): "None pending" almost always. "Signal sent" while a signalling round is open and the validator signalled; "Signal needed" while it has not (red once UpgradeSignalMissing pages); "Scheduled" once the upgrade height is set; amber "Within 24 h" while UpgradeSoon fires; grey "Not checked" and amber "Check stopped" as for the outside check. The dashboard cannot see silences: a round opened before any release exists stays red while you silence its page ([alerts.md](alerts.md#upgradesignalmissing)).

### Status board

One line per check and one per server, one bar per time block: 10 minutes on the default 24 hours (144 bars), 1 hour on 7 days, 6 hours on 30 days. The bars follow the time picker and line up with the charts below. The newest bar on the right is the current block and fills in as its minutes pass; in its first 30 seconds it shows the 2 minutes before it. A bar shows the worst moment of its block, read from the alert states Prometheus evaluated at the time, so each alert colours one line; NodeBehind colours Node in sync and the line of the server that lags. Network upgrades have no line: the Network upgrade answer shows them. A bar is green only while the alert rules ran in its block; grey means no data: before the install, monitoring down, the rules not running, or nothing to check. Hover a bar to see every line at that moment. Each bar is drawn centred on the start of its block, half a block left of the charts below.

| Line | Shown | Amber | Red | Uptime % |
|---|---|---|---|---|
| Node in sync | always | NodeNotAdvancing pending, NodeBehind fired for any node | NodeNotAdvancing fired | minutes in which the nodes advanced and none was behind |
| Validator signing | always | ValidatorNotSigning pending, SignaturesMissed fired | ValidatorNotSigning fired | share of the blocks that carry the validator's signature, counted as in Signed |
| Remote signer | remote signer | RemoteSignerProblem fired; "No signatures" in any minute of the block in which the chain recorded misses and no node signed | | minutes in which it signed without a signer alert, among the minutes it was asked to sign |
| Fibre serving | Fibre on | FibreDown pending, FibreNotServing, FibreBothActive | FibreDown fired (no Fibre unit active) | minutes with a Fibre unit active and uploads not all failing |
| Fibre from outside | `celestia_mon_tensile` | FibreDown's outside branches pending, TensileUnreadable | FibreDown (unreachable, or not registered or wrong host) fired | minutes in which a check under 15 minutes old reached the registered endpoint |
| Monitoring | always | MonitoringBroken, NotificationsFailing, DeadmanFailing, AnnouncementsUnreadable; Alertmanager down, or alerts not handed to it while none got through; the heartbeat stopped (not in the block in which Prometheus started) | | minutes with the heartbeat running, Alertmanager up and reached and no monitoring alert |
| one line per server | always | not reporting, a service down, disk low or read-only, node behind | DiskLow paged (disk full or read-only) | minutes in which all the server's metrics endpoints answered and its disk was not full or read-only |

Green on Remote signer needs real signatures in the block (the median latency freezes when tmkms stops), and green on Fibre from outside needs a reading under 15 minutes old. While the nodes are stalled, Validator signing turns grey ("Nodes stalled"): ValidatorNotSigning is held back then. Every % leaves out the minutes in which Prometheus did not scrape itself throughout, the minute it starts included. Prometheus keeps 30 days (`celestia_mon_prometheus_retention`) or 8 GB; older bars are grey.

### Role band

Which server held each role over the same time, read from the metrics: **Signing through** (the node the validator's signatures went through), with Fibre on **Fibre active on** (the host whose Fibre unit systemd reported active), and with Fibre and a remote signer **Fibre signs through** (the node Fibre sends its signing requests to, from its raw-bytes signatures in the last 15 minutes, else the last hour). A tmkms repoint or a Fibre failover shows as the name changing, and each repoint also puts a marker on the band and the Validator charts. Segments are tinted in the server's colour. Amber: nobody held the role, two servers did ("Two signers", "Two servers"), or Fibre signs through another node than the one the validator signs through (after a repoint, a Fibre that still dials the old node fails every upload: point its signer address at the signing node); not while no node signs at all, which is the signer's outage. Red: the page alert for it fired. Grey "No data" where monitoring had no data, as on the bars; grey "Out of set" only where the validator was out of the set within the step, never in the first 2 minutes after a start. On long ranges each step shows its worst moment, a Fibre outage included. The band carries the time axis of the whole board; the key under it reads "Times in UTC".

### Servers now

One row per server, in inventory order, even when nothing is read from it: "no data" means the server should report a value and does not, "–" that it does not apply.

- **Server**: the name, in the server's colour (the key to every chart).
- **Role**: what the server does now, read from the metrics: "Signing node", "Backup node", amber "Node, not signing" while no node signs; "Fibre active", grey "Fibre standby"; on a KMS host with `celestia_mon_kms_scrape`, "Remote signer", amber "Signer stopped" or "Signer not loaded". A server with several roles names each. Amber "Not reporting" while its node_exporter does not answer. Where the full words would not fit the table on a 1440-pixel screen, the dashboard uses short ones ("Signing", "Backup", "Not signing", "Fibre").
- **Node**: "in sync" within 5 blocks of the highest node, else the blocks it trails ("12 behind", amber above 20, NodeBehind's level); amber "stalled" or "down", plain "syncing" during block sync.
- **Peers**: connected peers. No alert watches them; a node without peers falls behind and NodeBehind follows.
- **Signer time** (remote signer): the median of the last 50 signatures on the node that signs now, amber from 10 ms (`signer_median_seconds`, RemoteSignerProblem). "> 10 ms, stale" on a node that tmkms left: it keeps its last median, and RemoteSignerProblem keeps firing for it until that node restarts. "No signing" while the validator is asked to sign and no node signs; "Proposals fail" while proposal signing failed for another reason than a timeout in 6 hours.
- **CPU**, **Memory**, **Network peak**: the share in use, as grey bars; no alert watches them. Network peak is the busiest minute of the last hour against `celestia_mon_link_mbps` or the NIC speed ("speed unknown" when neither is known: set `celestia_mon_link_mbps`).
- **Disk used**: the fullest disk DiskLow watches on the server, the data disks you listed and the Fibre store, as a grey bar. A rule's live condition replaces the number with a word: amber "Low space" below 10% free (`disk_message_free_ratio`; on a Fibre store only while Fibre reports no storage budget, since with a budget its fill is normal), amber "Too small" while a Fibre store has less free space than its storage budget still needs, amber "Read-only", and red "Full or read-only" only when DiskLow pages, so a Fibre-only server, where DiskLow sends messages, never turns red.
- **Restarts**: automatic restarts of celestia-appd, Fibre and tmkms by systemd in 24 hours; amber "Crash loop" at more than 2 in 30 minutes (`crashloop_restarts`, ServiceDown).

## Validator

One strip of six facts, then the charts behind the Validator and Signing through answers, all one height: two lines of two with a remote signer, one line of three with a local key. Everything the validator signs is drawn in the colour of the server whose node it signs through, so after a repoint the colour changes with it; the whole network is drawn in the text colour.

- **Blocks since signature** (ValidatorNotSigning): blocks since the validator's last signature on any node, so the count carries on after a repoint: 0 or 1 while it signs. ValidatorNotSigning pages above 10 blocks (`not_signing_blocks`). The value stops at 10 times that; right after a node start it reads that until the first signature. The sparkline shows the highest count of each step and tops out at the alert level.
- **Missed blocks, 1 h** (SignaturesMissed): the share of the last hour's blocks committed without the validator's signature; SignaturesMissed sends a message above 5% (`signatures_missed_ratio`), where the sparkline tops out.
- **Signer time** (remote signer, RemoteSignerProblem): the median of the last 50 signatures on the node the validator signs through; RemoteSignerProblem sends a message above 10 ms (`signer_median_seconds`), where the sparkline tops out. "No reading" while no node signs, or before a node's 50th signature since it started. With a local key this place shows **Peers**: the peers of the node the validator signs through, as a plain number.
- **Proposals signed, 24 h**: with a remote signer, the proposals tmkms signed; with a local key (**Proposals built, 24 h**), the proposals the node built. **Expected, 24 h**: the blocks of the same time times the validator's share of voting power; proposers take turns by stake, so the two should be close. **Missed proposals, 24 h**: times it was the validator's turn and no proposal arrived in time. The counts cover the last 24 hours, or the time since monitoring started when that is less, and carry on across node restarts.
- **Missed blocks** (SignaturesMissed, ValidatorNotSigning): bars count the blocks committed without the validator's signature in each bar's time (10 minutes on 24 hours, an hour on 7 days); none while every block is signed. The solid line is the share of the last hour's blocks without its signature, with the dashed 5% line and its zone; the dashed line in the text colour is the whole network, the share of all stake missing from recent blocks. The validator's line high and the network's low points at this setup; both high, at the network.
- **Signer latency** (remote signer, RemoteSignerProblem): on the node the validator signs through, the median of the last 50 signatures (solid, with the dashed 10 ms line and its zone), the slowest 1% of vote signatures (dashed, "votes") and, with Fibre, of Fibre's raw-bytes signatures through its node (dotted, "Fibre"). The axis is logarithmic, 0.1 to 100 ms, so a rare signature of up to a second does not flatten the rest. Grey bars count failed signing requests (`cometbft_privval_signing_failures_total`: timeouts, lost connections and others; every retry counts); none on a healthy setup. A node tmkms left is not drawn; the Servers now table marks it "stale".
- **Proposals per hour**: the proposals signed (or built) through each node per hour as bars, missed proposals stacked on top in grey, against the dashed number the validator's share of voting power should bring. A signer that signs votes but not proposals leaves the bars short.
- **Block handling time**: one line per node, the backup node's too: the slowest 1% of checking a proposed block plus the slowest 1% of applying a committed one, over 10 minutes. A node that is too slow votes late, which shows as missed blocks; all nodes slow points at a heavy block, one node at its CPU or disk.

## Fibre

Only with `celestia_mon_fibre_enabled: true`. Only the active Fibre sends Fibre metrics, so the panels follow it after a failover. One strip of six facts, then four charts, two to a line.

- **Uploads accepted, 1 h**, **Upload success, 1 h**, **Upload time** (95 of 100 accepted uploads, 15 minutes), **Last accepted upload** ("3 minutes ago"; "None since start" while Fibre reports but has accepted no upload since it started), **Storage budget used** and **Failed clean-ups, 24 h**, as plain numbers; the charts below hold their history. The storage budget follows the whole network's Fibre data over the 4-hour retention window: near 100% usually means network load; at 100% Fibre refuses uploads. "No limit": the budget is unlimited, or the validator was out of the active set when Fibre started. Failed clean-ups are failed runs of Fibre's removal of old shards over the last 24 hours (or since monitoring started), across Fibre restarts; 0 on a healthy server. Failed runs fill the store. Fibre pushes its metrics up to a minute apart, so Last accepted upload can be up to a minute late.
- **Since last accepted upload** (FibreNotServing, missed uploads): a sawtooth that drops with each upload is healthy; on a network that sends no blobs the line climbs, which is normal, and no alert follows. The axis stops at 3 hours. With the outside check, FibreNotServing sends a message when a blob assigned to the validator settled more than 5 minutes after the last accepted upload and none follows for 5 minutes.
- **Uploads and downloads** (FibreNotServing, uploads failing): requests per minute by outcome, averaged over 30 minutes, with uploads refused because the budget is full (dashed) apart from the ones that failed.
- **Fibre response time**: 95 of 100 requests over the last hour for the whole upload, the disk write and the signing through the node and its signer. A rising disk write points at the Fibre disk, a rising signing time at the signer or the link to it.
- **Fibre disk and budget** (DiskLow, Fibre store): free space on each Fibre store (solid, the standby's too) against the room the active Fibre's budget may still fill (dashed). The dashed line must stay below the solid one.

## Servers

Open. One column of charts for each server, in inventory order and in the server's colour, each chart titled with the server's name first ("fibre-server · CPU by service"), so a server's charts sit under one another and each line compares the servers. Two servers take half the width each, three a third, four a quarter; a single server's charts sit two to a line. No alert watches CPU, memory or traffic, so only the disk chart has alert levels.

- **CPU by service**: the share of all the server's cores in use, 5-minute averages, stacked by what uses it: **Consensus node** (celestia-appd's own CPU time), **Monitoring** (Prometheus and Alertmanager, on the monitor host), and **Other**, everything else; on a Fibre host it reads **Fibre and other**, because Fibre reports no CPU time of its own. The line on top is all in use; the chart's top is all the cores. On a VM, steal counts as in use.
- **Memory by service**: the same parts in bytes, with **Fibre** (the memory Fibre's Go runtime holds, a little under its real use) on a Fibre host where Fibre runs. The line on top is all in use (installed minus available, so cache that can be freed is left out); the dashed line is the installed memory. Fibre can use about 27 GiB at its default limits ([connection caps and memory](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#connection-caps-and-memory)). An amber marker shows each time the kernel killed a process for lack of memory (`journalctl -k` names it).
- **Network in and out**: bits per second through the server's network ports (virtual interfaces left out), in above zero and out below. On a Fibre host, **Fibre in** and **Fibre out** are the shard data Fibre accepted and served, drawn inside the totals; the rest is consensus traffic, RPC, proofs and TLS. The first screen shows the busiest minute against the link speed.
- **Free disk space** (DiskLow): each data disk you listed ("Data disk /var/lib/celestia") and each Fibre store ("Fibre store /var/lib/fibre"). The amber zone starts at 10% free, where DiskLow sends a message (on a Fibre store only while Fibre reports no storage budget); on servers with celestia-appd or tmkms the red zone starts at 5% free, where DiskLow pages. A server with no listed disk shows its root filesystem, which no alert watches.

## Servers: disk activity, restarts and clocks

Closed. The same columns.

- **Disk latency**: how long one write (above zero) and one read (below) took on average over the server's physical disks; above about 10 ms on NVMe the disk is overloaded or the provider's storage is slow. LVM, RAID, ZFS volumes and CD drives are left out; their physical disks count. Where the monitor shares a disk with the validator or Fibre, `celestia-mon-doctor` check 16 compares it around Prometheus's compactions ([quickstart step 9](quickstart.md#9-run-doctor)).
- **Disk reads and writes**: bytes written (above zero) and read (below) per second.
- **Restarts** (ServiceDown, crash loop): automatic restarts of celestia-appd, Fibre and tmkms by systemd, one bar per 30 minutes; an empty chart means none. The dashed line is the crash-loop level (more than 2 in 30 minutes, `crashloop_restarts`).
- **Clock offset**: how far the server's clock is from its time servers; more than a few tens of milliseconds means time sync is not working.

## Network upgrade

Closed; with `celestia_mon_signal`. One strip of five facts. **App version**: the version the chain runs now. **Our signal**: the version the validator signalled ("None" until it signals). **Support for next version**, as a bar twice as wide: the share of all voting power that signalled the next version (an upgrade can be scheduled at 5/6). **Upgrade height** and **Time to upgrade** at the measured block time, amber under 24 hours, while an upgrade is scheduled. They read the last value of the past 7 days, as the upgrade alerts do.

## All firing alerts

Closed. Every alert Prometheus has firing now, and those whose problem is true but has not lasted long enough to be sent, in the words of your notifications ("Validator not signing", "Disk low or read-only on fibre-1"), with their level ("Urgent", "Urgent, not paged yet", "Warning", "Warning, not sent yet") and how long since the problem started. The heartbeat is left out. With nothing firing it reads "No alerts firing".

## Markers and links

Charts carry grey markers, hidden from the dashboard's controls: "<unit> started on <server>" each time systemd starts celestia-appd, Fibre or tmkms; "Signing through <server>" at each repoint (on the role band too); "Out of memory on <server>" on that server's Memory by service, in amber; with `celestia_mon_signal`, "Network upgrade: new app version"; with `celestia_mon_announcements`, "Celestia announcement" (its text is in your alert channel). A chart of the Servers rows shows the starts of its own server only. Markers can be missed on ranges longer than about a week, where Grafana takes coarser steps.

Each panel's (i) ends with links to the alerts it shows in the [alert reference](alerts.md) and to related pages. The dashboard's links open the alert reference, [Node Exporter Full](https://grafana.com/grafana/dashboards/1860-node-exporter-full/) for everything else about a server, and with Fibre on the official [Fibre dashboard](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/observability/docker/grafana/dashboards/fibre.json) and [Tensile](https://tensile.huginn.tech).
