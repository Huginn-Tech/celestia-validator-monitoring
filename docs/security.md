# Security

What celestia-validator-monitoring exposes, and how to keep the network and the machines around it tight.

## What it exposes

| Service | Listens on | Reached by |
|---|---|---|
| node_exporter (9910) | private address | Prometheus on the monitor |
| OpenTelemetry collector metrics (9911) | private address | Prometheus on the monitor |
| OpenTelemetry collector OTLP (9915) | 127.0.0.1 | Fibre on the same host |
| Prometheus (9912), Alertmanager (9913) | 127.0.0.1 | the monitor itself, with a login: Grafana (Prometheus only), doctor, amtool as root, you through an SSH tunnel |
| Grafana (9914) | 127.0.0.1 by default, or the private address in `celestia_mon_grafana_listen` | you, with a login: through an SSH tunnel, a reverse proxy on the monitor, or that private address from the machines in `celestia_mon_grafana_clients` |
| celestia-mon-announcements (9916), celestia-mon-tensile (9917), celestia-mon-signal (9918), each only when turned on | 127.0.0.1 | Prometheus on the monitor |
| celestia-mon-blackbox (9919), with Fibre on | 127.0.0.1 | Prometheus and doctor on the monitor, with a login |

The install fails if one of these services listens on a wildcard or public address. Loopback is no boundary between the accounts of one host, so Prometheus, Alertmanager and celestia-mon-blackbox also ask for HTTP basic auth on every request, `/metrics`, `/-/ready` and `/-/healthy` included, and no other account on the monitor host can read them, silence an alert or have blackbox connect to a Fibre host: user `celestia-mon`, with a password the install generates once on the monitor in `/etc/celestia-mon/api-password`, readable by root and Prometheus only. Prometheus, doctor and amtool use it, and so does celestia-mon-announcements with Celestia's announcements on: systemd hands it a copy (`LoadCredential`), and it posts each announcement to Alertmanager with it. That login opens all of Alertmanager's API, silences included, so the announcements reader is the one service that both parses answers from outside (Discord's) and could silence an alert; it uses Python's standard library only and checks Discord's TLS certificate. Grafana has a login of its own, user `grafana` with a password in `/etc/celestia-mon/grafana-api-password` (root only; Grafana's datasource file holds a copy), which Prometheus takes and Alertmanager refuses, so a Grafana login that is taken over can read the metrics but cannot silence an alert. Prometheus, Alertmanager and blackbox keep only bcrypt hashes. `doctor` check 21 lists every active silence that holds back a page alert or ends more than 4 hours from now, and check 22 shows whether the dead-man heartbeat goes out. Through an SSH tunnel to 9912 or 9913 your browser asks for it; forward those ports only while you use them, because while the tunnel is open and the browser remembers the login, other web pages in that browser can send requests to them too. Grafana stays on loopback too unless you set `celestia_mon_grafana_listen` to a private address of the monitor (Tailscale, WireGuard, a LAN) and list in `celestia_mon_grafana_clients` the private address of each machine that opens it there; the install stops when that address is not the monitor's or the list is empty, and Grafana never listens on `0.0.0.0`. Open it with `ssh -N -L 9914:127.0.0.1:9914 <user>@<monitor>` and `http://localhost:9914`, or run a reverse proxy on the monitor in front of `127.0.0.1:9914` ([quickstart step 7](quickstart.md#7-open-grafana)). Grafana has anonymous access, snapshots (local and external), public dashboards, the plugin catalog and the news feed turned off, and its Prometheus datasource is `http://127.0.0.1:9912` with the API password.

The secrets (receiver tokens, the dead-man URL, Grafana's password) sit in one ansible-vault file that only the monitor host gets, and `install.sh` refuses a `vault.yml` that is not encrypted; the inventory copies on the other hosts hold none. On the monitor, the rendered Alertmanager settings are readable by root and the `celestia-mon-alertmanager` user only, and journald drops any Alertmanager log line that holds a Telegram bot token. `install.sh --uninstall` deletes the settings files that hold secrets, also without `--purge`. With Celestia's announcements on, the Discord bot token sits in `/etc/celestia-mon/announcements/discord-token`, readable by root only; systemd hands a copy to celestia-mon-announcements (`LoadCredential`), which never logs it and reaches only Discord's API and Alertmanager on 127.0.0.1. Give that bot read access to the announcement channel only. With the outside check on, celestia-mon-tensile reads one public page of Tensile's API (no key) and sends it nothing but your validator's address. With upgrade signalling on, celestia-mon-signal makes read-only CometBFT RPC calls (`/status`, `/abci_info`, `/abci_query`, `/blockchain`) to the RPCs in `celestia_mon_signal`; the store query sends them your validator's operator address. It holds no key and sends no transaction; signalling stays with your validator's account key. Its metrics and log name each RPC by its scheme, host and port only, so a provider's key in a URL's path stays in its settings file, `/etc/celestia-mon/signal/config.json`: root and the service alone can read it, the install leaves it out of its diff and its log, and `install.sh --uninstall` deletes it. Keep such a URL in the monitor's vault file.

With Fibre on, celestia-mon-blackbox (blackbox_exporter) on the monitor asks each Fibre host's own port (7980), at its private address, for Fibre's [upload readiness](quickstart.md#upload-readiness): one TLS connection per probe, every 15 seconds, carrying only the standard gRPC health request, with no key and no client certificate. It does not verify Fibre's certificate: Fibre makes its own self-signed certificate at every start, signed by no authority, so there is nothing to verify it against, and this probe reads readiness, not identity. It listens on 127.0.0.1 only and takes only requests with the API password, so only Prometheus and doctor on the monitor can ask it for a probe, and its configuration holds the one module for this probe. Its unit may exchange network traffic only with 127.0.0.1 and the Fibre hosts' private addresses (`IPAddressDeny=any` with `IPAddressAllow`), so a probe cannot reach any other host.

Every `celestia-mon-*` unit runs as its own system user with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, `SystemCallArchitectures=native` and `RestrictAddressFamilies` (Unix and IP sockets; node_exporter also netlink), and with `OOMScoreAdjust=500`, so when the host runs out of memory the kernel ends a monitoring service before celestia-appd, Fibre or tmkms. Each unit also has no capabilities (`CapabilityBoundingSet=`), `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `ProtectHostname`, `RestrictNamespaces`, `RestrictSUIDSGID` and `LockPersonality`, and only the system calls of an ordinary service (`SystemCallFilter=@system-service`; node_exporter also `adjtimex`, which its timex collector reads the clock's state with); any other call fails. Grafana may exchange network traffic only with 127.0.0.1 and, when `celestia_mon_grafana_listen` is a private address, with that address and the machines in `celestia_mon_grafana_clients` (`IPAddressDeny=any` with `IPAddressAllow`; the install refuses a range, loopback or a host of the inventory in that list), so a Grafana login cannot use a datasource to reach other hosts, such as the validator's RPC, privval gRPC or app gRPC ports.

The units also limit whom each service exchanges network traffic with, whatever the firewall (systemd's `IPAddressDeny` and `IPAddressAllow`, which apply in both directions). node_exporter and the OpenTelemetry collector: only 127.0.0.1, their host's own private address, the monitor's, and the host's `celestia_mon_firewall_sources`, so list there the address Prometheus scrapes from when it is not the monitor's `celestia_mon_private_ip`. celestia-mon-announcements, celestia-mon-tensile and celestia-mon-signal never reach the internal ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16, fc00::/7, fe80::/10) but for 127.0.0.1, the DNS resolvers `/etc/resolv.conf` names when the install runs, and, for celestia-mon-signal, the RPC addresses in `celestia_mon_signal`: an answer or a changed DNS record cannot send them, or the Discord bot token, to another host of the private network or to a cloud's metadata service. Name a node of the private network in `celestia_mon_signal.rpc` by its address, not by a host name, and run the install again after a change of DNS resolvers.

`install.sh` downloads uv and every release by a version and sha256 pinned in `versions.yml`, installs ansible-core and every package it needs from `ansible/requirements.lock` (each file checked against its sha256 there, wheels only) into a temporary directory that it deletes at the end, and installs nothing with apt or pip. The vault password from `--ask-vault-pass`, and the decrypted copy `--edit-vault` opens in your editor, stay in memory, in `/run/celestia-mon-install` (root only, deleted at the end), never on the disk; `--edit-vault` refuses a vault file that is a symbolic link. The lock is made in GitHub Actions by the `lock` workflow from the pins in `versions.yml`. Each release is unpacked as root into a directory only root can enter, with every file owned by root (the archives store other owners), and moved or copied from there to `/opt/celestia-mon`, so no other account can change what the services and root run. For node_exporter, the collector, Prometheus, Alertmanager and blackbox_exporter the sha256 of each installed binary is recorded next to it (`SHA256SUMS`; check it by hand with `sha256sum -c SHA256SUMS` in that directory), and a binary that no longer matches is installed again on the next run.

## Tailnet policy

When the hosts share a Tailscale tailnet, the tailnet policy is the firewall between them. In Tailscale's default netfilter mode on Linux, tailscaled puts its own chain first in `INPUT` and accepts everything that arrives on `tailscale0`, so ufw never sees tailnet traffic: neither your rules nor the ones celestia-validator-monitoring adds apply to it. ufw still filters the public interface and other private networks.

Keep the signing path off the tailnet: tmkms to the node's privval port (`priv_validator_laddr`) and Fibre to the node's privval gRPC (`priv_validator_grpc_laddr`, mTLS) and app gRPC (9090) belong on a private VLAN or WireGuard. Bind those three ports to that address, or to 127.0.0.1, never to `0.0.0.0`: on every interface they are reachable over `tailscale0`, which ufw does not filter, and celestia-appd does not authenticate the signer that connects to `priv_validator_laddr`. `doctor` check 20 shows where the two signer ports listen.

Deny by default: a policy that holds only the grants below allows nothing else between the hosts. The policy file is HuJSON (JSON with comments), edited in the Tailscale admin console. Tag each server by its role; with one host in several roles, give it several tags. Tag a backup node `tag:validator` too: the monitor scrapes it like the validator.

```jsonc
{
  "tagOwners": {
    "tag:celestia-mon-monitor": ["autogroup:admin"],
    "tag:validator": ["autogroup:admin"],
    "tag:fibre": ["autogroup:admin"],
    "tag:kms": ["autogroup:admin"]
  },
  "grants": [
    // Prometheus on the monitor scrapes node_exporter and celestia-appd's metrics port.
    {"src": ["tag:celestia-mon-monitor"], "dst": ["tag:validator"], "ip": ["tcp:9910", "tcp:26660"]},
    // node_exporter and the OpenTelemetry collector's metrics on the Fibre host, and Fibre's own port,
    // where the monitor asks for Fibre's upload readiness.
    {"src": ["tag:celestia-mon-monitor"], "dst": ["tag:fibre"], "ip": ["tcp:9910", "tcp:9911", "tcp:7980"]},
    // node_exporter on the KMS host, only with celestia_mon_kms_scrape: true.
    {"src": ["tag:celestia-mon-monitor"], "dst": ["tag:kms"], "ip": ["tcp:9910"]},
    // Only if celestia_mon_signal.rpc lists a node's CometBFT RPC on its tailnet address:
    // {"src": ["tag:celestia-mon-monitor"], "dst": ["tag:validator"], "ip": ["tcp:26657"]},
    // Only if tmkms or Fibre reach the nodes over the tailnet instead of a private VLAN or WireGuard:
    // tmkms to the privval port (priv_validator_laddr), Fibre to the privval gRPC and the app gRPC.
    // {"src": ["tag:kms"], "dst": ["tag:validator"], "ip": ["tcp:26658"]},
    // {"src": ["tag:fibre"], "dst": ["tag:validator"], "ip": ["tcp:26669", "tcp:9090"]},
    // Admins: SSH to every server. Grafana listens on 127.0.0.1 by default and is reached
    // through an SSH tunnel; only if you set celestia_mon_grafana_listen to the monitor's
    // tailnet address, add: {"src": ["autogroup:admin"], "dst": ["tag:celestia-mon-monitor"], "ip": ["tcp:9914"]}
    {"src": ["autogroup:admin"], "dst": ["tag:celestia-mon-monitor", "tag:validator", "tag:fibre", "tag:kms"], "ip": ["tcp:22"]}
  ]
}
```

Use your own ports where they differ: the node's metrics port, the ports in `priv_validator_laddr` and `priv_validator_grpc_laddr`, `celestia_mon_fibre_port` and `celestia_mon_ports`.

Before you switch from an allow-all policy, list the connections the hosts make over the tailnet today (for example `sudo ss -tnp` on each host, looking for `100.x` addresses) and add a grant for each one you still need: anything not granted stops at the switch.

Tagging a device turns off its key expiry, so the servers stay on the tailnet without re-authenticating. What limits them is then the policy alone; remove a server's tags, or the server, when it leaves your setup.

## The operator's machine

The machine you administer the hosts from can reach all of them. Keep it as tight as the hosts:

- **SSH keys on a FIDO2 security key** (`ssh-keygen -t ed25519-sk`): a copied key file is useless without the device and its touch.
- **No agent forwarding** (`ForwardAgent no`, OpenSSH's default): a host you log in to could use a forwarded agent to reach the others.
- **No public port 22** on the hosts: SSH over the private network or the tailnet only.

## Where to run the monitor

The monitor holds the alert channels' secrets and the dead-man URL, and has a web login. On a host of its own, none of that sits next to a signing credential, and monitoring keeps running and alerting when the validator or Fibre host goes down. With a remote signer the consensus key is on the KMS host only, so the monitor may share the Fibre host, as in the [mainnet layout](quickstart.md#mainnet-layout). Know what that host then holds:

- **A signing credential.** Fibre's mTLS client key for the validator node's privval gRPC (26669) can ask the node for raw-bytes signatures with the validator key: payment promises, never votes, because celestia-core prefixes raw bytes before it signs them. Keep the key `0600`, owned by Fibre's user; `doctor` check 17 warns when group or other can read it.
- **Every alert path.** Root on that host can stop or silence every alert and keep pinging the dead-man service, so nothing from celestia-validator-monitoring tells you. Treat the host like a validator host: no other users, SSH as in the previous section. A check that runs elsewhere with its own paging route, such as the external check on Fibre's port, still reaches you then. After a suspected compromise, rotate the receivers' secrets (routing keys, bot tokens, webhook URLs) and the dead-man URL.
- **One point of failure.** When that host goes down, monitoring goes with it, and the dead-man service and the external check on Fibre's port are what tell you.

Keep the monitor off the KMS host.
