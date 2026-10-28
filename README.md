# celestia-validator-monitoring

Ready-made monitoring for Celestia validators running Fibre.

It watches a consensus validator, its Fibre server and its remote signer (tmkms), on one host or split across validator, KMS, Fibre and monitor hosts, with optional backup nodes and a standby Fibre; when tmkms moves to a backup node or a standby Fibre takes over, the alerts and the dashboard follow. It ships:

- one Grafana dashboard that reads like a status page: what is wrong now in the words of your alerts; one-word answers (is the validator signing and through which node, is a backup node ready, is Fibre serving and reachable from outside, is a network upgrade coming, is a governance vote missing); a day of uptime bars per check and per server, read from the alert states; which server held each role; each server's state; and below them the charts behind every alert, with its threshold drawn;
- twenty-five alerts tied to the failures validators actually hit, routed to PagerDuty, Telegram, Discord, Slack or a webhook, plus a heartbeat to a dead-man service that also stops when an alert could not be delivered;
- optionally, Celestia's announcements from its Discord (upgrades, incidents) through the same receivers, PagerDuty included;
- optionally, network upgrades read from the chain (x/signal): a page while validators signal the next app version and yours has not, and a message when the upgrade is scheduled and when it is a day away; with them, the validator's governance votes (x/gov): a message when a proposal is 2 days from its end without your vote, and on mainnet a page a day before (an expedited 24-hour vote: 12 hours before its end);
- `celestia-mon-doctor`, a read-only check of each host's setup.

## How it installs

Copy the repository and one shared inventory to each host, and run on that host:

```sh
sudo ./install.sh --inventory <inventory>/hosts.yml --host <its name in the inventory>
```

Each install is pre-checked and reversible. install.sh first checks the host (Ubuntu 24.04, the address in the inventory, free ports, disk and memory), then shows a dry run of every change: services, users, directories, ports and firewall rules. It applies the plan only after you confirm, with one line per part (the checks, each service, doctor) and what it changed; the whole output goes to `/var/log/celestia-mon/install.log`, and `--verbose` shows it as it runs. Then it runs `celestia-mon-doctor`, and prints the command that removes it again (`sudo ./install.sh --uninstall --host <name>`). That removes the services, their binaries and the firewall rules the install added, and keeps the data, settings and users for a reinstall; `--purge` deletes those too. install.sh brings a pinned Ansible, installed from a lock file with the sha256 of every package, in a temporary directory that it deletes afterwards, and installs nothing with apt or pip.

Every service runs as its own `celestia-mon-*` systemd unit and user, with lower CPU and disk priority than the Celestia services (no CPU quota), a soft memory target (`MemoryHigh`) and a hard limit well above it (`MemoryMax`). Grafana listens on 127.0.0.1 by default; you open it through an SSH tunnel. celestia-validator-monitoring never changes, restarts or upgrades celestia-appd, Fibre or tmkms, and never enables, resets or reorders a firewall; when ufw is already active, it only adds rules, marked `celestia-mon`, that let the monitor host reach the ports it scrapes, and records them so the uninstall deletes only those.

## Documentation

- [Quickstart](docs/quickstart.md): requirements, inventory, install, Grafana, dead-man service, doctor, maintenance, uninstall.
- [Alerts](docs/alerts.md): what each alert means, where it goes and what to check first.
- [Panels](docs/panels.md): the question each dashboard panel answers and how to read it.
- [Security](docs/security.md): what is exposed, a tailnet policy, SSH hygiene.

Status: running on a Mocha testnet validator since 17 October 2026, split across a validator host, a Fibre host that also runs a node of its own and the monitor, and a KMS host.

Licence: MIT.
