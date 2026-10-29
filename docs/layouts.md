# Layouts

Which box gets which role. Put each box in the groups of what it runs, and run the survey and the install on each box except the KMS box: the plan and `doctor` tell you the rest. Which node signs and which Fibre runs are read from the node metrics and from systemd, never from the inventory, so after a tmkms repoint or a Fibre failover the inventory stays as it is ([when a role moves](#when-a-role-moves)).

## The layouts

These settings apply to every layout and are left out of the rows below:

- **On every box you install on:** `celestia_mon_private_ip` and `celestia_mon_data_mounts` (mount points).
- **On every Fibre box:** `celestia_mon_fibre_mount` and `celestia_mon_fibre_public_endpoint`.
- **Shared:** `celestia_mon_signer` (`local` or `remote`) and `celestia_mon_fibre_enabled`.

| Layout | Boxes and their groups | Also set in the inventory | Your steps on the Celestia side |
|---|---|---|---|
| [One box](#one-box) (`example-single`, with Fibre added) | node-1: `validator`, `fibre`, `monitor` | `celestia_mon_private_ip: 127.0.0.1` (or a private address) | Node metrics on `127.0.0.1:26660`; Fibre's `--otel-endpoint`. Fibre reads `127.0.0.1:9090`, its default. |
| [One box, no Fibre](#one-box-no-fibre) (an older network, a DR box; `example-single`) | node-1: `validator`, `monitor` | `celestia_mon_fibre_enabled: false` | Node metrics. |
| [Validator and a Fibre box without a node](#validator-and-a-fibre-box-without-a-node) (`example-split`) | val-1: `validator`; fibre-1: `fibre`; mon-1: `monitor`; kms-1: `kms` | none | val-1: `[grpc] address = "<val-1 private>:9090"` and ufw from fibre-1 to 9090; `priv_validator_grpc_laddr` on val-1's private address with mTLS, ufw from fibre-1 to 26669. fibre-1: `app_grpc_address` and the signer at val-1, with mTLS. |
| [Fibre box with its own node](#mainnet-layout) (`example-mainnet`) | val-1: `validator`, `fibre` (a stopped standby); fibre-1: `fibre`, `backup_node`; mon-1: `monitor`; kms-1: `kms` | none | fibre-1: Fibre reads `127.0.0.1:9090`, and signs through the node tmkms serves (val-1, mTLS). The node's `priv_validator_grpc_laddr` is set ahead of a repoint. Standby: OTLP endpoint set, unit disabled. |
| [The same with the monitor on the Fibre box](#mocha-layout) (the Mocha layout) | val-1: `validator`; fibre-1: `fibre`, `backup_node`, `monitor`; kms-1: `kms` | none | As above. |
| [Fibre and the node it reads on separate boxes](#fibre-and-the-node-it-reads-on-separate-boxes) (`example-fibre-apart`) | val-1: `validator` (+ `fibre` for a standby); fibre-1: `fibre`; node-1: `backup_node`; mon-1: `monitor`; kms-1: `kms` | node-1: its own `celestia_mon_units` if its unit is not `celestia-appd`; `celestia_mon_node_metrics_port` if not 26660 | val-1: `priv_validator_grpc_laddr` on val-1's private address with mTLS, ufw from fibre-1 to 26669. node-1: `[grpc] address = "<node-1 private>:9090"` and ufw from fibre-1 to 9090; `priv_validator_grpc_laddr` with mTLS and ufw from fibre-1 and val-1 to 26669, ahead of a repoint. fibre-1: `app_grpc_address = "<node-1 private>:9090"`; the signer is the node tmkms serves, with mTLS. |
| [More backup nodes or standby Fibres](#more-backup-nodes-or-standby-fibres) | add each box to `backup_node` or `fibre` | none | Each standby Fibre: OTLP endpoint set, unit disabled. |
| [Monitor](#monitor) | its own box (another data centre over WireGuard), or shared with a node or Fibre box; not the KMS box | On a monitor of its own, the disk of `/var/lib/celestia-mon` in its data mounts | none |
| [KMS](#kms) | kms-1: `kms`; nothing is installed. A standby KMS box stays out of the inventory | `celestia_mon_kms_scrape: true` only to watch its disks | none |
| [DR site](#dr-site) | a separate install with its own inventory, in the one-box shape | `celestia_mon_signer: local`; its own receivers and dead-man URL | Prepared ahead of time; installed when you switch. |
| Not supported | Two validators or two monitors in one install; one box in two installs (for example testnet and mainnet) | none | none |

For any other layout: put each box in the groups of what it runs, and run the survey and the install on each box except the KMS box. The plan and `doctor` tell you the rest.

The links that cross boxes stay on a private VLAN or WireGuard: the app gRPC (plaintext) and the privval gRPC (mTLS). [security.md](security.md#ports-on-the-validator-who-connects-to-what) has each setting and ufw rule.

## One box

One box runs the validator, its Fibre and the monitor. Start from `ansible/inventory/example-single` and add Fibre: put `node-1` in a `fibre` group under `children:` too, set `celestia_mon_fibre_enabled: true` in `group_vars/all.yml`, and give `node-1` its `celestia_mon_fibre_mount` and `celestia_mon_fibre_public_endpoint`. With tmkms on a KMS box, also set `celestia_mon_signer: remote` and list that box by its name in a `kms` group. Its `celestia_mon_private_ip` can be `127.0.0.1`. Fibre reads the node beside it on `127.0.0.1:9090`, its default, and signs through the node's privval gRPC on 127.0.0.1 (with mTLS: `doctor` check 17 warns without it). Your steps: the node's metrics on `127.0.0.1:26660` and Fibre's `--otel-endpoint` ([quickstart step 6](quickstart.md#6-your-steps)). When the box dies, nothing on it can alert: the dead-man service tells you.

## One box, no Fibre

`ansible/inventory/example-single`: the validator with a local key and the monitor on one box, `celestia_mon_signer: local` and `celestia_mon_fibre_enabled: false`. Your step: the node's metrics.

## Validator and a Fibre box without a node

`ansible/inventory/example-split`: the validator on `val-1`, tmkms on `kms-1`, Fibre on `fibre-1` and the monitor on `mon-1`. `fibre-1` runs no node: its Fibre reads `val-1`'s node over the private network and signs through it. On the Celestia side:

- on `val-1`, `[grpc] address = "<val-1 private address>:9090"` in `app.toml` and `priv_validator_grpc_laddr = "<val-1 private address>:26669"` in `config.toml` with its three TLS files, each port open to `fibre-1` only;
- on `fibre-1`, `app_grpc_address = "<val-1 private address>:9090"` and `signer_grpc_address = "<val-1 private address>:26669"` with mTLS (or `--app-grpc-address` and `--signer-grpc-address` in its unit).

Fibre starts only while the node it reads answers, so when you restart `val-1`'s node, silence `fibre-1` too. The example sets `celestia_mon_kms_scrape: true` on `kms-1` to watch its disks; remove that line to leave the KMS box untouched.

## Mainnet layout

`ansible/inventory/example-mainnet` describes a validator with a backup node and a standby Fibre, signing through tmkms, and a monitor on a box of its own:

| Box | Groups | Runs |
|---|---|---|
| `val-1` | `validator`, `fibre` | celestia-appd without a key; tmkms on `kms-1` signs through it. A standby Fibre: installed, stopped and disabled, port 7980 closed; it reads `val-1`'s node (its signer: below). |
| `fibre-1` | `fibre`, `backup_node` | The active Fibre, which reads the node beside it; that node is a synced celestia-appd without a key that can also take over signing. |
| `kms-1` | `kms` | tmkms only; nothing is installed there. |
| `mon-1` | `monitor` | Prometheus, Alertmanager and Grafana, and node_exporter for its own disk; no Celestia service and no key. |

Run `install.sh` on `val-1`, on `fibre-1` and on `mon-1`, with the secrets on `mon-1` ([quickstart step 4](quickstart.md#4-add-the-secrets-on-the-monitor)); `kms-1` needs nothing. Turn on both nodes' metrics. Both Fibre boxes get the collector, the standby too; on `val-1`, point the standby's OTLP endpoint at it and leave the unit stopped ([quickstart step 6](quickstart.md#6-your-steps)).

### Monitor box

`mon-1` may sit in another data centre, as long as it reaches the other boxes over WireGuard: its `celestia_mon_private_ip` and theirs are WireGuard addresses. Prometheus on `mon-1` pulls every metric; no Celestia box connects to it, and it reaches on them only the metrics ports and Fibre's port, never a signer port, an RPC or SSH ([security.md](security.md#wireguard-only-link)). Its own disk, CPU and memory are watched like the other servers': list the mount point of `/var/lib/celestia-mon` in its `celestia_mon_data_mounts`. When it loses its link to one box it sends one message for that box, and when it reaches none, one page ([MonitorCannotReach](alerts.md#monitorcannotreach)).

### Backup node

A synced celestia-appd without a key, ready to take the validator role over. tmkms connects to one node at a time; moving it is a change of its `addr` and a restart of tmkms. While the backup node is not the signer's, keep its `priv_validator_laddr` empty: with it set, celestia-appd exits at start when no signer connects (`doctor` check 2 reports it as `INFO`). With it empty, the node signs with its local key file (`config/priv_validator_key.json`), so that file must never be the validator's key: a home copied from the validator still holds it, and the node then double-signs next to tmkms. `doctor` check 2 reads the key's voting power from the node's `/status` and fails when it is above 0. Voting power 0 does not clear the key: a jailed validator's key has none either and signs again after unjail. Check 2 prints the key's address to compare with check 4 on `val-1`; on a backup node that shares the monitor's box, it also fails when Prometheus saw that address as the validator's in the last 30 days. Its metrics are scraped like the validator's: its card in the dashboard's Servers band reads "Backup, in sync · 28 peers" (its peer count) while it can take over (amber "Node behind · 35 blocks" while it lags), NodeBehind sends a message when it lags for 10 minutes, and `doctor` check 18 on the monitor shows its lag.

### Each Fibre reads its own box's node

Fibre reads the chain (its registration, the validator set, the newest block) through the app gRPC at its `app_grpc_address`. In this layout every Fibre box runs a node, and each Fibre reads that one: `app_grpc_address = "127.0.0.1:9090"`, with `[grpc] address = "127.0.0.1:9090"` in the node's `app.toml`, so the app gRPC never leaves the box and no firewall rule is needed for it ([security.md](security.md#ports-on-the-validator-who-connects-to-what)). The node on `fibre-1` therefore serves its Fibre as well as standing by for signing, and Fibre follows it: when it stops, Fibre's health check reports `app_unreachable`, and while it lags, `app_syncing` or `chain_stalled` ([FibreNotServing](alerts.md#fibrenotserving) `not_ready`, a message after 3 minutes and a page after 10); ServiceDown and [NodeBehind](alerts.md#nodebehind) name the node itself. Fibre still signs through the node tmkms serves (below), not through the one it reads. `doctor` check 27 on each Fibre box shows the node it reads.

### Standby Fibre

Every box with a Fibre install is in the `fibre` group, and systemd says which one runs. A stopped Fibre unit is a standby: it raises no alert, reads "Standby, stopped" in its card in the dashboard's Servers band while the other Fibre runs, and is `OK` in `doctor` check 6. Keep the standby unit disabled (`systemctl disable <fibre unit>`), so a reboot does not start it next to the active one; check 6 warns while it is enabled. Its OTLP endpoint must still point at its collector (check 7).

### The standby's signer

Fibre signs through the validator node's privval gRPC, always with mTLS, and celestia-appd has one such listener (`priv_validator_grpc_laddr`). On `val-1` it serves the active Fibre on `fibre-1` on `val-1`'s private address. The standby dials that same address, `<val-1 private address>:26669`, with a client certificate of its own from the same CA (`--signer-grpc-ca-file`, `--signer-grpc-cert-file`, `--signer-grpc-key-file`, or `signer_grpc_*` in its `server_config.toml`), so nothing changes at promotion. A plaintext listener, on 127.0.0.1 too, would let every account on `val-1` ask for signatures with the validator key ([security.md](security.md#ports-on-the-validator-who-connects-to-what)).

`doctor` check 17 on `val-1` warns while the standby's signer does not match the node's listener, or has no client certificate. Keep each Fibre's client key `0600`, owned by Fibre's user (check 17 warns otherwise).

## Mocha layout

The layout celestia-validator-monitoring is developed and tested on, on the Mocha testnet (celestia-app and Fibre v10.5.0, tmkms v0.15 with a soft-signing key): three boxes. As in the [mainnet layout](#mainnet-layout), the Fibre box runs a node of its own, without a key, which its Fibre reads; here it also runs the monitor.

| Box | Groups | Runs |
|---|---|---|
| validator | `validator` | celestia-appd, signing through tmkms over WireGuard. |
| KMS | `kms` | tmkms only; nothing is installed there. |
| Fibre | `fibre`, `backup_node`, `monitor` | Fibre, which reads the node beside it; that node, a celestia-appd without a key; Prometheus, Alertmanager and Grafana. |

Start from `example-split`: remove `mon-1`, put `fibre-1` in the `monitor` and `backup_node` groups with the secrets on it, and remove `celestia_mon_kms_scrape` from `kms-1`. When another service on the Fibre box holds port 26660, give its node another metrics port and set `celestia_mon_node_metrics_port` to it in `fibre-1`'s entry.

Four steps are the operator's on such a setup after the install (celestia-validator-monitoring changes none of them):

1. Point Fibre at the collector on the Fibre box, `--otel-endpoint http://127.0.0.1:9915`, and restart Fibre at a moment you choose ([quickstart step 6](quickstart.md#6-your-steps)). Until then `doctor` check 7 fails.
2. If Fibre sent its metrics to a collector of your own before, for example one on the validator box that Fibre reached over WireGuard on port 4319, remove that collector and its firewall rule once step 1 is done.
3. Turn on both nodes' metrics, each on its box's private address: a `prometheus_listen_addr` on `0.0.0.0:26660` listens on every interface. `doctor` check 1 reports it until it is changed: it fails while the box has a Tailscale interface or no firewall is active, and warns otherwise.
4. Point Fibre at the node on its own box, `--app-grpc-address 127.0.0.1:9090` (or `app_grpc_address` in its `server_config.toml`) with `[grpc] address = "127.0.0.1:9090"` in that node's `app.toml`. Fibre still signs through the validator's node, and that node stays a [backup node](#backup-node) (`priv_validator_laddr` empty).

## Fibre and the node it reads on separate boxes

`ansible/inventory/example-fibre-apart`: the validator node and a standby Fibre on `val-1`, the active Fibre on `fibre-1` with no node beside it, the node that Fibre reads on `node-1` (a backup node, whose unit here is named `celestia.service`), tmkms on `kms-1` and the monitor on `mon-1`. On the Celestia side:

- on `val-1`, `priv_validator_grpc_laddr = "<val-1 private address>:26669"` in its `config.toml` with its three TLS files, port 26669 open to `fibre-1` only; its standby Fibre reads the node beside it on `127.0.0.1:9090` and signs through that same address ([the standby's signer](#the-standbys-signer));
- on `node-1`, `[grpc] address = "<node-1 private address>:9090"` in its `app.toml`, port 9090 open to `fibre-1` only; ahead of a tmkms repoint, `priv_validator_grpc_laddr = "<node-1 private address>:26669"` in its `config.toml` with its three TLS files, and port 26669 open to both Fibre boxes, `fibre-1` and `val-1`, only;
- on `fibre-1`, `app_grpc_address = "<node-1 private address>:9090"`, and `signer_grpc_address` at the node tmkms serves, with mTLS: `val-1`'s, and `node-1`'s after a repoint ([below](#after-a-tmkms-repoint)).

`node-1` sets its own `celestia_mon_units` (`{appd: celestia, fibre: fibre, tmkms: tmkms}`): the alerts and the dashboard watch each box under its own unit names, and the install stops when a name is not a unit systemd has loaded there, naming the units that run celestia-appd. Fibre starts only while the node it reads and the node it signs through both answer. So when you stop `node-1`, silence `fibre-1` too: FibreNotServing (`not_ready`) names `fibre-1`, and Fibre there cannot restart until `node-1` is back. `doctor` check 27 on `fibre-1` names the node it reads; an address that is no node of the inventory warns, since nothing would watch that node.

What each fault sends:

- `node-1` down: after 3 minutes "Monitor cannot reach node-1" (a message) and "Fibre on fibre-1 is not ready for uploads" (a message, the page after 10 minutes).
- `fibre-1` down: "Monitor cannot reach fibre-1" after 3 minutes, and FibreDown ("Fibre is not running on any host", a page) after 10, unless the standby on `val-1` runs by then; nothing about `node-1`.
- tmkms pointed at `node-1` while Fibre still signs through `val-1`: FibreNotServing (`uploads_failing`), "Fibre on fibre-1 fails every upload".

Four servers and the signer's column make five, so the dashboard's first screen shows the [Servers now](panels.md#servers-now-five-columns-or-more) table instead of a card per server.

## More backup nodes or standby Fibres

Add each box to `backup_node` or `fibre` and run `install.sh` on it. Each standby Fibre needs its OTLP endpoint set and its unit disabled, and its signer pointed at the node tmkms serves, as [the standby's signer](#the-standbys-signer) describes.

## Monitor

The monitor runs on a box of its own, which may sit in another data centre as long as it reaches the other boxes over WireGuard ([monitor box](#monitor-box)), or shares a box with a node or Fibre. A monitor of its own keeps alerting when a validator or Fibre box dies; a shared one cannot alert when its own box dies, and the dead-man service then tells you. Never put it on the KMS box. On a monitor of its own, list the mount point of `/var/lib/celestia-mon` in its `celestia_mon_data_mounts`. [security.md](security.md#where-to-run-the-monitor) weighs the choice.

## KMS

The KMS box is in the `kms` group by its name alone, and nothing is installed there. To watch its disks and tmkms's unit, set `celestia_mon_kms_scrape: true`, `celestia_mon_private_ip` and `celestia_mon_data_mounts` for it, get the package there and run `install.sh` on it: it installs node_exporter only. The signer's health is read from the node side either way: signing failures and signer latency (RemoteSignerProblem), missed signatures (SignaturesMissed) and signing that stopped (ValidatorNotSigning).

### KMS standby

A standby KMS box (tmkms installed without the key, disabled) needs nothing from celestia-validator-monitoring; leave it out of the inventory.

## DR site

A disaster-recovery box at another site, which takes over only when the whole primary site is lost (the key loaded there as a key file, Fibre signing through that node's privval gRPC with mTLS), is a separate install: its own inventory in the one-box shape, with the box in the `validator`, `fibre` and `monitor` groups, `celestia_mon_signer: local`, `celestia_mon_fibre_enabled: true`, and its own receivers and dead-man URL. Prepare the inventory and get the package there ahead of time, and run `install.sh` when you switch: until the key is loaded there, its node signs nothing and ValidatorNotSigning would page.

## When a role moves

Neither a Fibre failover nor a tmkms repoint needs an inventory change or a new install. The alerts and the dashboard follow the roles:

| Event | Alerts | Dashboard |
|---|---|---|
| Fibre failover: the active Fibre stops, a standby on another box starts, and `x/valaddr` is pointed at that box | FibreDown pages only when no Fibre unit has been active for 10 minutes, so a failover within that time pages nobody. FibreBothActive sends a message when both run for 5 minutes. FibreNotServing watches whichever box's unit is active. | The role band's "Fibre active on" changes to the new box; in the Servers band, Fibre reads "Active, serving" there and "Standby, stopped" on the old one. During the gap the Fibre tile reads "Not running" in amber (red only if FibreDown pages), and with the outside check "Fibre from outside" reads "Wrong host" until `x/valaddr` names the new box. The Fibre panels show the new box's series. |
| The validator box down, tmkms pointed at a backup node | ValidatorNotSigning pages about 2 minutes after the signatures stop (its first step: `journalctl -u tmkms` on the KMS box) and resolves once the backup node signs. MonitorCannotReach sends one message for the validator box (none of its endpoints answers) in place of its ServiceDown messages. NodeNotAdvancing stays quiet while the backup node advances. Until each Fibre's signer follows ([below](#after-a-tmkms-repoint)), FibreNotServing (`uploads_failing`) sends a message for each active Fibre that still signs through the old node. | Servers band: the validator box's card reads "Not reporting", the backup node's "Backup, in sync · 28 peers" until it signs; the validator box's History line reads "Not reached". The Validator tile reads "Not seen" until the backup node signs; then Signing through and the signer's card name it, the role band marks the move, and Blocks since signature drops back to 0. |
| tmkms pointed at another node while both run | ValidatorNotSigning pages when no node has signed for more than 10 blocks for 2 minutes; silence it by name for a planned repoint ([quickstart](quickstart.md#keep-it-running)). If both nodes keep signing, ValidatorSigningTwice pages about 3 minutes after the second one started. | Signing through reads "Switching" for up to 2 minutes, then names the new node, and the role band marks the move. If both nodes keep signing, it reads "Two signers": amber while ValidatorSigningTwice is pending, red once it pages. |
| The box of the active Fibre down (in the mainnet layout, the backup node with it) | The monitor keeps alerting: MonitorCannotReach sends one message for that box after 3 minutes, and FibreDown pages after 10 unless a standby runs by then (start it and send [set-host](#registering-fibre-with-set-host)). The monitor cannot tell a box that is down from a link that is: a WireGuard fault between the monitor and that box alerts the same way, so check from outside (Tensile, the [external check](#external-check-on-fibres-port)) before you fail over. | That box's card reads "Not reporting" and its History line "Not reached"; the Fibre tile reads "Not seen", red once FibreDown pages. |
| A node that a Fibre on another box reads, down | MonitorCannotReach sends one message for the node's box after 3 minutes, and FibreNotServing (`not_ready`) a message for the Fibre box, then a page after 10 minutes: Fibre there cannot restart until the node is back. | The node's box reads "Not reached" in its History line. |
| A monitor on a box of its own loses its link to every box (its WireGuard, its data centre's network), while it runs | After 3 minutes one page, MonitorCannotReach ("Monitor cannot reach any server"); NodeNotAdvancing and FibreDown's unit check wait for a box that answers, so they do not page for the same cause. The validator may well be signing: check an explorer first. | Every server's line but the monitor's in History reads "Not reached" in red, and the cards read "Not reporting". |
| The monitor down | Nothing can alert from the monitor: the dead-man service pages. The validator and Fibre are not affected; the external check on Fibre's port (below) still watches Fibre. | None until the monitor is back. |

## After a tmkms repoint

Fibre signs through the privval gRPC of the node tmkms serves. When tmkms moves to another node, every Fibre still dials the node tmkms left, and every signature fails while its unit stays active and port 7980 answers (FibreNotServing `uploads_failing`). Point each Fibre's `--signer-grpc-address` (or `signer_grpc_address`) at the mTLS address of the node tmkms now serves, `<that node's private address>:26669`, keep the same client certificate, and restart Fibre; on a standby, leave the unit stopped. Set that node's `priv_validator_grpc_laddr` to that address with its three TLS files (a server certificate that names the address, and the CA of the Fibre client certificates) ahead of time, because celestia-appd reads it only at start; `doctor` check 20 on a backup node says when it is empty, and check 17 on each Fibre box names the node it signs through.

## Registering Fibre with set-host

`celestia-appd tx valaddr set-host` is signed by the validator's account key, which also controls the validator's staking settings. Keep that key on your workstation, on a Ledger, and never import it on a server:

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

## External check on Fibre's port

celestia-validator-monitoring watches Fibre from inside the boxes; it asks Fibre for its readiness on the private address only, never at its public endpoint. Add a TCP check on the registered `host:port` (port 7980 by default) in an external uptime service, wired to PagerDuty and set to alert after 10 minutes of failure, the delay FibreDown uses. It pages when the monitor cannot (the monitor down, or cut off from the Fibre box), and it catches what the boxes cannot see: a closed firewall, a routing fault, a registration that names the wrong box. After a Fibre failover, point the check at the endpoint now registered. [Tensile](https://tensile.huginn.tech) shows your Fibre server from outside as well, and with `celestia_mon_tensile` its verdict reaches your alerts ([reference.md](reference.md#outside-check-of-the-fibre-endpoint)).
