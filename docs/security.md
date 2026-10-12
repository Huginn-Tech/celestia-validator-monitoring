# Security

What celestia-validator-monitoring exposes, and how to keep the network and the machines around it tight.

## What it exposes

| Service | Listens on | Reached by |
|---|---|---|
| node_exporter (9910) | private address | Prometheus on the monitor |
| OpenTelemetry collector metrics (9911) | private address | Prometheus on the monitor |
| OpenTelemetry collector OTLP (9915) | 127.0.0.1 | Fibre on the same host |
| Prometheus (9912), Alertmanager (9913) | 127.0.0.1 | the monitor itself: Grafana, doctor, an SSH tunnel |
| Grafana (9914) | private address | you, with a login |

The install fails if one of these services listens on a wildcard or public address. Prometheus and Alertmanager have no login, so they stay on loopback. Grafana has anonymous access, external snapshots and public dashboards turned off, and its Prometheus datasource is `http://127.0.0.1:9912`.

The secrets (receiver tokens, the dead-man URL, Grafana's password) sit in one ansible-vault file that only the monitor host gets; the inventory copies on the other hosts hold none. On the monitor, the rendered Alertmanager settings are readable by root and the `celestia-mon-alertmanager` user only.

`install.sh` downloads uv and every release by a version and sha256 pinned in `versions.yml`, builds its Ansible in a temporary directory that it deletes at the end, and installs nothing with apt or pip.

## Tailnet policy

When the hosts share a Tailscale tailnet, the tailnet policy is the firewall between them. In Tailscale's default netfilter mode on Linux, tailscaled puts its own chain first in `INPUT` and accepts everything that arrives on `tailscale0`, so ufw never sees tailnet traffic: neither your rules nor the ones celestia-validator-monitoring adds apply to it. ufw still filters the public interface and other private networks.

Deny by default: a policy that holds only the grants below allows nothing else between the hosts. The policy file is HuJSON (JSON with comments), edited in the Tailscale admin console. Tag each server by its role; with one host in several roles, give it several tags.

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
    // node_exporter and the OpenTelemetry collector's metrics on the Fibre host.
    {"src": ["tag:celestia-mon-monitor"], "dst": ["tag:fibre"], "ip": ["tcp:9910", "tcp:9911"]},
    // node_exporter on the KMS host, only with celestia_mon_kms_scrape: true.
    {"src": ["tag:celestia-mon-monitor"], "dst": ["tag:kms"], "ip": ["tcp:9910"]},
    // tmkms connects to the validator's privval port (priv_validator_laddr) and nothing else.
    {"src": ["tag:kms"], "dst": ["tag:validator"], "ip": ["tcp:26659"]},
    // Admins: SSH to every server, and Grafana on the monitor.
    {"src": ["autogroup:admin"], "dst": ["tag:celestia-mon-monitor", "tag:validator", "tag:fibre", "tag:kms"], "ip": ["tcp:22"]},
    {"src": ["autogroup:admin"], "dst": ["tag:celestia-mon-monitor"], "ip": ["tcp:9914"]}
  ]
}
```

Use your own ports where they differ: the node's metrics port, the port in `priv_validator_laddr`, and `celestia_mon_ports`.

Before you switch from an allow-all policy, list the connections the hosts make over the tailnet today (for example `sudo ss -tnp` on each host, looking for `100.x` addresses) and add a grant for each one you still need: anything not granted stops at the switch.

Tagging a device turns off its key expiry, so the servers stay on the tailnet without re-authenticating. What limits them is then the policy alone; remove a server's tags, or the server, when it leaves your setup.

## The operator's machine

The machine you administer the hosts from can reach all of them. Keep it as tight as the hosts:

- **SSH keys on a FIDO2 security key** (`ssh-keygen -t ed25519-sk`): a copied key file is useless without the device and its touch.
- **No agent forwarding** (`ForwardAgent no`, OpenSSH's default): a host you log in to could use a forwarded agent to reach the others.
- **No public port 22** on the hosts: SSH over the private network or the tailnet only.

## Where to run the monitor

For mainnet, use a monitor host that is neither the validator nor the Fibre host. The monitor holds the alert channels' secrets and has a web login; on a host of its own, neither sits next to the validator's key, and monitoring keeps running and alerting when the validator or Fibre host goes down.
