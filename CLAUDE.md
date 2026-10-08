# Working on Taut

Taut is a monitoring package for Celestia consensus validators that run a Fibre server and/or a remote signer. Scope and design live in `docs/` and in the code; keep both in step.

Rules for anyone (human or agent) changing this repo:

1. All builds, tests and linters run in GitHub Actions (`.github/workflows/ci.yml`). Write the change, push, and read the CI result.
2. Taut never edits, restarts or upgrades celestia-appd, Fibre or tmkms, and never enables, resets or reorders a host firewall. Where a validator setting must change for metrics to exist, `tools/doctor.sh` reports it and the operator applies it.
3. Docs describe Taut plainly. They may link the upstream and community resources Taut builds on; they never compare Taut with other tools.
4. Metric names and labels must be confirmed against the pinned upstream source (`versions.yml` and the versions named in `docs/`). If a name cannot be confirmed, stop and ask; do not guess.
5. Keep the scope small: one dashboard, the alerts in `docs/alerts.md`, the collectors and stack that feed them, and `doctor`. New panels or alerts need a concrete failure they catch.
