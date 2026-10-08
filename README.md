# Taut

Monitoring for Celestia consensus validators that run a Fibre server and/or a remote signer (tmkms), on one host or split across validator, KMS and Fibre hosts.

Taut installs the collectors and a Prometheus, Alertmanager and Grafana stack with Ansible, and ships:

- one Grafana dashboard: is the node at the chain head, is the validator signing, is the remote signer within Celestia's 10 ms requirement, is Fibre serving, how loaded the links and Fibre are, and are the hosts healthy;
- six alerts tied to the failures validators actually hit, plus a heartbeat so you know when monitoring itself is down;
- `tools/doctor.sh`, a read-only check of a host's setup.

Taut never changes, restarts or upgrades celestia-appd, Fibre or tmkms, and never enables, resets or reorders a firewall; when ufw is already active it only adds rules that let the monitor host reach the ports it scrapes.

## Documentation

- [Quickstart](docs/quickstart.md): requirements, inventory, install, Grafana, dead-man service, `doctor`.
- [Alerts](docs/alerts.md): what each alert means and what to check first.
- [Panels](docs/panels.md): the question each dashboard panel answers and how to read it.

Status: under construction.

Licence: MIT.
