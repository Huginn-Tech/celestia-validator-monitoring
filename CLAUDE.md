# Working on celestia-validator-monitoring

celestia-validator-monitoring is a monitoring package for Celestia consensus validators that run a Fibre server and/or a remote signer. Scope and design live in `docs/` and in the code; keep both in step.

Rules for anyone (human or agent) changing this repo:

1. All builds, tests and linters run in GitHub Actions (`.github/workflows/ci.yml`). Write the change, push, and read the CI result.
2. celestia-validator-monitoring never edits, restarts or upgrades celestia-appd, Fibre or tmkms, and never enables, resets or reorders a host firewall. Where a validator setting must change for metrics to exist, `tools/doctor.sh` reports it and the operator applies it.
3. Docs describe celestia-validator-monitoring plainly. They may link the upstream and community resources celestia-validator-monitoring builds on; they never compare celestia-validator-monitoring with other tools.
4. Metric names, labels and every other name taken from Celestia must be confirmed against the pinned upstream source: `celestia_app` in `versions.yml`, and the celestia-core and Cosmos SDK fork releases its go.mod builds with (`celestia_core` and `cosmos_sdk` in `monitoring/upstream.yml`, which lists each such name with the file that defines it). If a name cannot be confirmed, stop and ask; do not guess.
5. Keep the scope small: one dashboard, the alerts in `docs/alerts.md`, the collectors and stack that feed them, and `doctor`. New panels or alerts need a concrete failure they catch.

## Celestia release

1. Change `celestia_app.version` in `versions.yml`; the monthly versions report lists new celestia-app releases.
2. Push. The lint step prints a sed for each upstream link and version phrase that no longer matches the pin: check that the sentence still holds at the new release, then run it.
3. The upstream workflow lists each expression of `monitoring/upstream.yml` that no longer matches: find the new name, update the entry and every use of the old name.
4. When celestia-app's go.mod moved to another celestia-core or Cosmos SDK fork release, the upstream workflow says so: update `celestia_core` or `cosmos_sdk` in `monitoring/upstream.yml`, then step 3 again.
5. After upgrading Mocha, run `doctor` on the Mocha monitor: check 12 shows that every metric the rules read has series, and check 21 covers every loaded page alert.
6. Update the release in the "tested on" line of the Mocha layout (`docs/quickstart.md`).
