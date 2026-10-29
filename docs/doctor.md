# doctor

`celestia-mon-doctor` checks a host of the validator and tells you what to fix. It changes nothing: it reads the host's units, the Celestia services' settings, the listening sockets and, on the monitor, Prometheus and Alertmanager. The install puts it on every host it installs on and runs it at the end of each install ([quickstart step 6](quickstart.md#6-your-steps) has what it shows right after each host's install).

```sh
sudo celestia-mon-doctor
```

Run it as root, so it can read the node's settings and see which process owns each listening socket. It reads `/etc/celestia-mon/doctor.conf`, which the install writes from the inventory; any setting can also be given as a flag (`celestia-mon-doctor --help` lists them). The settings include the install type: with `signer=local` doctor never expects the remote signer's metrics, and with `fibre_enabled=false` never Fibre's or the collector's.

Before an install, or on a host without one, run it from the package: `sudo bash tools/doctor.sh`. Without a settings file it guesses the host's roles from its systemd units; on a backup node, pass `--roles backup_node`.

## Statuses

Each check prints one line (check 12 one line per group, check 10 one more per md RAID array, check 23 a second one for the governance votes): the status, the check's number and name, and what it found.

| Status | Meaning |
|---|---|
| `OK` | The check passed. |
| `NOT YET` | Metrics that appear only after an event; the line names the event. |
| `FAIL` | The check failed; the line ends with the fix to apply. |
| `WARN` | Worth fixing, but not counted as a failure; the line ends with the fix. |
| `INFO` | A measurement to read, never a failure. |
| `SKIP` | The check does not apply on this host, or cannot run yet. |

The exit code is 0 when no check failed, 1 when one did, and 2 on a usage error. Run doctor again after every celestia-app or Fibre upgrade and every config change.

## Checks

Each check keeps its number, which the docs and the alerts cite.

| # | Name | Host | Check |
|---|---|---|---|
| 1 | node metrics | validator, backup_node | celestia-appd metrics are enabled and served. Warns when they listen on every interface (`prometheus_listen_addr` with an empty host or `0.0.0.0`) or on a public address, and fails then when no firewall is active (ufw, firewalld or nftables) or the host has a Tailscale interface, whose traffic ufw does not filter: whoever reaches the port can read the metrics and hold its few connections (3 by default), which blinds the signing alerts. Warns too on a Tailscale address (100.64.0.0/10), where only the tailnet policy decides who reaches the port, and when a ufw rule lets anyone reach it beyond loopback. The fix binds them to the private address the monitor scrapes (`127.0.0.1` on one box whose `celestia_mon_private_ip` is `127.0.0.1`), never `0.0.0.0`, and names the ufw rule for the monitor. |
| 2 | remote signer | validator, backup_node | The signer setting matches `celestia_mon_signer`; with a remote signer, the median signing latency is at most 10 ms (skipped until 50 signatures after a start). With `priv_validator_laddr` empty, celestia-appd signs with its local key file, so doctor reads that key's voting power from the node's `/status`: above 0 it fails on a backup node, and on a validator set to `signer: remote`, because two signers of one validator double-sign. A jailed or unbonded validator's key also has voting power 0, so on a backup node doctor prints the key's address to compare with check 4 on the validator, and on a backup node that is also the monitor it fails when Prometheus saw that address as the validator's in the last 30 days. Otherwise `INFO` on a backup node: whether `priv_validator_laddr` is set (with it set, celestia-appd exits at start when no signer connects). |
| 3 | node progress | validator, backup_node | Node progress: the height advances and `catching_up=false`. This shows that the node advances, not that it is at the network's head. |
| 4 | validator addr | validator | The validator's consensus address (`celestiavalcons1...`), from the node's `/status`. |
| 5 | fibre registry | validator | With Fibre on: the Fibre host registered in `x/valaddr`, compared with `celestia_mon_fibre_public_endpoint` of every Fibre host (any of them may be the registered one). When the validator host also has a Fibre install, the registration must name this host while its Fibre runs, and must not while it is the standby. |
| 6 | fibre service | fibre | The Fibre unit is active and its port (7980 by default) listens. With two or more Fibre hosts, a stopped unit with a closed port is `OK` (standby); a port that listens while the unit is stopped is a warning, and so is a stopped unit that is enabled, which a reboot would start next to the active Fibre. |
| 7 | fibre otlp | fibre | Fibre's OTLP endpoint points at the local collector, on a standby too, so its metrics flow the moment it starts; the fix for a standby never starts it. |
| 8 | private binds | all | The `celestia-mon-*` services listen on private addresses only (Grafana on 127.0.0.1 or `celestia_mon_grafana_listen`), Prometheus and Alertmanager on loopback only; other software on the host is not checked. Reports whether a firewall is active, and warns when a ufw rule lets anyone reach one of their ports beyond loopback. |
| 9 | time sync | all | Time sync is active (`NTPSynchronized`, the kernel's test that ClockNotSynced also makes) and the clock offset that chrony or systemd-timesyncd measures is small. |
| 10 | disk space | all | Free space on the data mounts (`/` when the inventory lists none); the Fibre mount is not read-only. Then one line per md software RAID array in `/proc/mdstat` (none without md), as RaidDegraded reads it: fails for an array missing a disk, warns while one rebuilds, and fails for an array that the host's node_exporter does not export, which the alert would not see. |
| 11 | monitor stack | monitor | Prometheus and Alertmanager answer on 127.0.0.1, and every scrape target is up. A down target means its collector or exporter is broken; it is listed with its error and the fix for that job. Warns while targets have not been scraped yet, in the first minute. |
| 12 | rule metrics | monitor | Every metric the alert rules read: `OK` when it has series, `NOT YET` with the event that makes it appear (a first signing failure, a first upload, the validator's first own signature, its first missed signature, an md RAID array on a watched host, a governance proposal in its voting period), `WARN` when it is missing although its source is up (most likely a renamed metric), `FAIL` when its source is broken (a target down, or Fibre not pushing). `signer` and `fibre_enabled` decide which metrics are expected at all. |
| 13 | fibre pipeline | monitor | Fibre pipeline, with Fibre on: Prometheus has fresh Fibre series from each Fibre host whose unit is active, a sample under 3 minutes old; a standby needs only its collector up. The collector takes OTLP only on 127.0.0.1 at `celestia_mon_ports.otelcol_otlp`, so this shows that the unit's endpoint matches it end to end, not just that the port is open. |
| 14 | unit memory | all | Unit memory: each `celestia-mon-*` unit's use against its `MemoryHigh` and `MemoryMax`; warns above 80% of `MemoryHigh`. |
| 15 | | | Not used. |
| 16 | | | Not used. |
| 17 | fibre signer | fibre | Fibre's signer address (`--signer-grpc-address` or `signer_grpc_address`) is an IP address and port: Fibre resolves a host name at every dial, and without mTLS it accepts only a loopback IP, not `localhost`. Warns when Fibre signs without mTLS (no `signer_grpc_*` files), on loopback too: the node's privval gRPC then serves signatures with the validator key to every account on its host. On a host with a celestia-appd node, a signer on this host (with mTLS, this host's own address or 127.0.0.1) must match the node's one privval gRPC listener (`priv_validator_grpc_laddr`) in address, port and mode. A mismatch fails for an active Fibre and warns for a standby ([layouts.md](layouts.md#the-standbys-signer)). With mTLS, it warns when group or other can read Fibre's client key. With the inventory's nodes known, the line names the node Fibre signs through, and warns (mTLS) when the address is no node of the inventory: then celestia-validator-monitoring watches no node Fibre signs through, or the address is mistyped. doctor knows each node by its `celestia_mon_private_ip` only, so another private address of the same node warns too, and needs nothing. |
| 18 | node lag | monitor | Node lag: every celestia-appd node's height against the highest one, and which node signs. Warns when a node is more than `node_behind_blocks` (20) behind, or reports no height: a backup node that lags cannot take over signing. |
| 19 | fibre active | monitor | Fibre active, with Fibre on: which Fibre host runs Fibre, read from systemd. `FAIL` when none does (FibreDown pages after 10 minutes) or when no Fibre host's unit state can be read (its node_exporter target is down or missing), `WARN` when two or more do (two Fibre instances must never be live at once). |
| 20 | signer binds | validator, backup_node | Signer binds: where `priv_validator_laddr` (only the KMS host may connect) and `priv_validator_grpc_laddr` (only the Fibre hosts, with mTLS) listen, from `config.toml` and the listening sockets, and which ufw rules let others in; doctor never connects to them. On every interface or a public address it fails when no firewall is active or the host has a Tailscale interface (ufw does not filter `tailscale0`), and warns otherwise; it warns on a Tailscale address (100.64.0.0/10), on a private address with no firewall active, and when a ufw rule lets anyone, or every host of a private range, reach the port. `priv_validator_laddr` on a public address fails: celestia-appd does not authenticate the signer that connects to it. The privval gRPC warns without mTLS, on loopback too, and fails beyond loopback with `priv_validator_grpc_allow_insecure = true`, or with only some of its three TLS files. The fix names the setting and the ufw command ([security.md](security.md#ports-on-the-validator-who-connects-to-what)). On a backup node it notes an empty `priv_validator_grpc_laddr`: after a tmkms repoint, no Fibre can sign through that node until it is set. |
| 21 | silences | monitor | Silences: warns on each active silence in Alertmanager that holds back a page alert (every alert rule with severity page that Prometheus has loaded) or ends more than 4 hours from now, with its matchers, author, end and ID: while it holds an alert back, nothing is sent and the dead-man heartbeat stays green. A host silence counts for its length only, and so does every silence while Prometheus does not answer (check 11). |
| 22 | dead-man | monitor | Dead-man heartbeat: Alertmanager sent the Watchdog to the dead-man service in the last 10 minutes, and none of those sends failed (`FAIL`). `WARN` when none went out (the Watchdog stops for an hour after a failed page or message notification); `NOT YET` in the first 10 minutes after Prometheus or Alertmanager starts. |
| 23 | upgrade signal | monitor | Upgrade signalling, with `celestia_mon_signal` set (`SKIP` without): celestia-mon-signal is up and has read an RPC (`/abci_info`, x/signal and the validator's entry, which needs an operator address that decodes) in the last 15 minutes. Prints the last reading: the RPC, the app version, the share of the voting power that signalled the next version against the 5/6 needed, your validator's signal, and a scheduled upgrade. `WARN` while validators signal the next version and yours has not, and while the reading comes from another RPC than the first one listed; `FAIL` when the service is down or has read no RPC for 15 minutes, with the reasons of its failed reads. A second line, `gov votes`, prints the governance proposals in their voting period, each with its end and whether the validator account voted on it (`SKIP` with `gov: false`): `WARN` while it has not voted on one, with when GovVoteMissing comes for it, `FAIL` when x/signal is read but the proposals have not been for 15 minutes. |
| 24 | fibre ready | monitor | [Upload readiness](reference.md#upload-readiness), with Fibre on: asks each Fibre host's port, through celestia-mon-blackbox, whether Fibre is ready now. `OK` when every Fibre that runs is Ready; a stopped standby does not answer, as expected. `FAIL` when one is Not ready: Fibre reports that at least one of its own checks fails, so uploads to it may fail, and the fix names the `journalctl` line that says which check. `WARN` when one is Not reported (Fibre older than v10.5.0) or when the monitor cannot reach the port of an active Fibre, so its readiness is not checked. |
| 25 | node ports | validator, backup_node | Node ports: the app gRPC (`[grpc] address` in `app.toml`: 127.0.0.1 when only the Fibre on this host reads it, else only the Fibre hosts that read it), the REST API (`[api] address`) and the RPC (`[rpc] laddr`), 127.0.0.1 or one host, and the RPC's gRPC, pprof and trace pull addresses when they are set, judged as in check 20; a key that is not set reads as its default. The fix names the setting and the ufw command. With the inventory's Fibre hosts known, the fix for the app gRPC names the Fibre hosts without a node of their own (one ufw command each), or only the bind to 127.0.0.1 when every Fibre host reads its own node. |
| 26 | service users | validator, backup_node, fibre | Service users: warns when the celestia-appd or Fibre unit runs as root (no `User=`): a flaw in it that lets someone run code or read files gives them the whole host. |
| 27 | fibre app node | fibre | Fibre's chain node: the address Fibre reads the chain from (`--app-grpc-address`, else `app_grpc_address` in `server_config.toml`, else Fibre's default `127.0.0.1:9090`). Fibre reads the chain ID there once when it starts, with no retry, so it exits while that node does not answer. On a host without a celestia-appd node, an address of this host fails (warns when something listens there: put the host in `backup_node`); on a host with one, the node's `[grpc] address` in `app.toml` must take the dial (fails on another address or with `enable = false`). A node on another host is named from the inventory, and an address that is no node of the inventory warns: nothing then watches the node Fibre depends on (another private address of a node of the inventory warns too, and needs nothing). A problem fails for an active Fibre and warns for a standby, a stopped or failed unit with two or more Fibre hosts as check 6 counts it; a unit that systemd keeps restarting is an active Fibre that exits, and fails. doctor never connects to the port. |

Checks 17 and 27 name the nodes, and check 25 the Fibre hosts, from two settings the install writes to `doctor.conf` from the inventory: `node_addresses` and `fibre_addresses`, each a comma-separated list of `NAME=address`. Without them (an older `doctor.conf`, or doctor run by hand) those lines name no host.

## Survey

`sudo ./install.sh --survey` (or `sudo bash tools/doctor.sh --survey`, or `sudo celestia-mon-doctor --survey` once installed) runs no check. It finds the celestia-appd, Fibre and tmkms units of the host by the binary they run, whatever their names (`celestia-appd`, or `cosmovisor` for it, `fibre`, `tmkms`), and prints:

- **Read on this box:** each unit with its state, the node's home, Fibre's home, port and the node it reads and signs through, and the host's addresses.
- **Groups:** the groups the host belongs in. A node is a validator or a backup node as check 2 tells them apart.
- **Entry for hosts.yml:** the host's settings, filled from what it read: `celestia_mon_private_ip` (WireGuard addresses first, Tailscale last; `127.0.0.1` when everything runs on this one box), `celestia_mon_data_mounts` (the mount points of the node's data and `/`, and on a monitor that of `/var/lib/celestia-mon`), and on a Fibre box `celestia_mon_fibre_mount` and a placeholder for `celestia_mon_fibre_public_endpoint`. Unit names, the node's metrics port and Fibre's port appear only where they are not the defaults.
- **Your steps on the Celestia side:** node metrics that are off or on every address, Fibre's OTLP endpoint, the app gRPC Fibre reads (with the exact ufw line where it reads another box), a signer without mTLS.

It writes no file and changes nothing. A box that runs only tmkms gets no entry: the survey says to list it by its name in the `kms` group. Without root, it first says that the node's and Fibre's settings may not be readable.

A sample, on a Fibre box that runs no node and still reads the chain on loopback:

```
celestia-mon-doctor --survey on fibre-1: what this box runs; nothing was changed.

Read on this box:
  celestia-appd  none
  Fibre          fibre.service (active), home /var/lib/fibre (on /var/lib/fibre), port 7980; reads the chain at 127.0.0.1:9090 (this box); signs through 10.10.0.1:26669 (mTLS)
  addresses      10.10.0.3 (wg0, WireGuard), 192.168.1.20 (eth1, private)

Groups: fibre (and monitor, when the monitor runs on this box too)

Entry for hosts.yml, under all: hosts: (name the box as you like):
    fibre-1:
      celestia_mon_private_ip: 10.10.0.3  # wg0, WireGuard; also on this box: 192.168.1.20 (eth1)
      celestia_mon_data_mounts: [/]
      celestia_mon_fibre_mount: /var/lib/fibre
      celestia_mon_fibre_public_endpoint: "<public host or IP>:7980"  # what valaddr set-host registers while this box's Fibre is the active one
      # Fibre reads the chain at 127.0.0.1:9090 (this box) and signs through 10.10.0.1:26669 (mTLS)

Your steps on the Celestia side, at a moment you choose (celestia-mon never makes them):
  1. Fibre sends no metrics yet: add "--otel-endpoint http://127.0.0.1:9915" to ExecStart in fibre.service (or Environment=FIBRE_OTEL_ENDPOINT=http://127.0.0.1:9915), run "systemctl daemon-reload", then restart Fibre at a moment you choose. The collector the install puts on this box takes them there
  2. Fibre reads the chain at 127.0.0.1:9090, but no celestia-appd node runs on this box: set --app-grpc-address in ExecStart (or app_grpc_address in /var/lib/fibre/config/server_config.toml) to "<private address of the node it reads>:9090", run "systemctl daemon-reload" if you changed the unit, then restart Fibre at a moment you choose. On that node, set [grpc] address = "<its private address>:9090" in app.toml, restart celestia-appd there at a moment you choose, and let only this box reach it: "sudo ufw allow from 10.10.0.3 to any port 9090 proto tcp"

Next: put the entry of every box in one hosts.yml in ~/celestia-mon-inventory (start from the example of your layout) and each box in its groups under children:, copy that folder to every box but the KMS box, then run on each: sudo ./install.sh
```
