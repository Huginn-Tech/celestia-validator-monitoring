# celestia-validator-monitoring

Ready-made monitoring for Celestia validators running Fibre.

<img width="1883" height="797" alt="image" src="https://github.com/user-attachments/assets/c12e5225-8943-4b55-861d-e00affcbdb4a" />


It watches a consensus validator, its Fibre server and its remote signer (tmkms), on one host or split across validator, KMS, Fibre and monitor hosts, with optional backup nodes and a standby Fibre; when tmkms moves to a backup node or a standby Fibre takes over, the alerts and the dashboard follow. It ships:

- one Grafana dashboard that reads like a status page: what is wrong now in the words of your alerts; one-word answers (is the validator signing and through which node, is Fibre serving and reachable from outside, is a network upgrade coming, is a governance vote missing); each server's state, such as whether a backup node could take over signing; a day of uptime bars per check and per server, read from the alert states; which server held each role; and below them a list of every firing alert, then the charts behind the alerts, with their thresholds drawn;
- twenty-five alerts tied to the failures validators actually hit, routed to PagerDuty, Telegram, Discord, Slack or a webhook, plus a heartbeat to a dead-man service that also stops when an alert could not be delivered;
- optionally, Celestia's announcements from its Discord (upgrades, incidents) through the same receivers, PagerDuty included;
- optionally, network upgrades read from the chain (x/signal): a page while validators signal the next app version and yours has not, and a message when the upgrade is scheduled and when it is a day away; with them, the validator's governance votes (x/gov): a message when a proposal is 2 days from its end without your vote, and on mainnet a page a day before (an expedited 24-hour vote: 12 hours before its end);
- `celestia-mon-doctor`, a read-only check of each host's setup.

## Install

Each box needs Ubuntu 24.04 with root and a private address: WireGuard, a private VLAN or Tailscale; on a single box, `127.0.0.1`. The monitor also needs an alert channel (Telegram, Discord, Slack or a webhook; PagerDuty optional) and the URL of a dead-man service. [Layouts](docs/layouts.md) shows which box gets which role, from one box to Fibre and the node it reads on separate boxes.

On each box except the KMS box, from the unpacked package, run the same two commands:

```sh
sudo ./install.sh --survey   # reads this box and prints its inventory entry and your steps; changes nothing
sudo ./install.sh            # checks the box, shows a dry run, asks, installs, then runs doctor
```

Between the two, put every box's entry into one inventory and copy it to each box. The secrets (your receivers and the dead-man URL) stay on the monitor: `sudo ./install.sh --edit-secrets` there. Nothing is installed on the KMS box.

Each install is pre-checked and reversible. It stops before any change when the box does not match the inventory: its address, unit names, mount points, free ports and disk, or a box that belongs to another install. Then it shows a dry run of every change: services, users, directories, ports and firewall rules. It applies the plan only after you confirm, keeps the whole output in `/var/log/celestia-mon/install.log`, and ends with what is still yours to do on that box. `sudo ./install.sh --uninstall` removes it again: the services, their binaries and the firewall rules it added (`--purge` also deletes its data, settings and users). install.sh brings a pinned Ansible, with the sha256 of every package checked, in a temporary directory it deletes afterwards, and installs nothing with apt or pip.

celestia-validator-monitoring never changes, restarts or upgrades celestia-appd, Fibre or tmkms, and never enables, resets or reorders a firewall. Where ufw is active, it adds only the rules, marked `celestia-mon`, that let the monitor reach the ports it scrapes. What it needs from you, such as the node's metrics address and Fibre's metrics endpoint, it prints as your steps, for you to apply at a moment you choose. The [quickstart](docs/quickstart.md) walks through an install.

## Documentation

- [Quickstart](docs/quickstart.md): what you need, getting the package, the inventory, the secrets, the install, your steps on the Celestia side, Grafana and the alert paths, keeping it running.
- [Layouts](docs/layouts.md): which box gets which role, with the steps of each layout; the mainnet and Mocha layouts; what happens when a role moves; repointing Fibre's signer; registering Fibre with set-host.
- [Reference](docs/reference.md): every setting, the secrets, ports, files and resources, how install.sh works, Grafana access, upload readiness, the optional alerts (upgrade signalling, the outside check of Fibre, Celestia's announcements), upgrades and uninstall.
- [doctor](docs/doctor.md): its statuses, every check, and the survey.
- [Alerts](docs/alerts.md): what each alert means, where it goes and what to check first.
- [Panels](docs/panels.md): the question each dashboard panel answers and how to read it.
- [Security](docs/security.md): what is exposed, a tailnet policy, who connects to the validator's ports, SSH hygiene, where to run the monitor, a WireGuard-only link.

Licence: MIT.
